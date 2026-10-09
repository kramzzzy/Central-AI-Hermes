"""Owner-bound first assistant setup inside the private native backend.

The configured backend profile is a provider/configuration source, not a seeded OS
personality. Setup never copies its SOUL, conversations or remembered user facts.
"""
import hashlib
import json
import os
import re
import shutil
import threading
from pathlib import Path
from uuid import UUID
from hermes_chat import NativeChat
from hermes_profile import profile_environment
from hermes_settings import idle
from central_ai_identity import central_ai_identity
from hermes_premade import preserved_profiles


def read_marker(root):
    path=Path(root)/'.runtime'/'os-installation.json'
    if not path.exists(): return None
    if path.is_symlink(): raise RuntimeError('Installation binding must be a private regular file')
    try:
        marker=json.loads(path.read_text(encoding='utf-8'))
        UUID(marker['org']); profile=marker['profile']; source=marker['source_profile']
        user=marker.get('user')
        if not isinstance(user,str) or not 0<len(user)<=200 or type(marker.get('verified')) is not bool:
            raise ValueError()
        expected='team-owner-'+hashlib.sha256(json.dumps([marker['org'],user]).encode()).hexdigest()[:32]
        if marker.get('mode')=='premade':
            agents=marker['agents']
            if len(agents)!=2 or {a['name'] for a in agents}!={'Leo','Sarah'} or profile!=next(a['native_profile'] for a in agents if a['name']=='Leo') or source!=profile: raise ValueError()
        elif profile!=expected: raise ValueError()
        if not isinstance(source,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',source) or source=='default': raise ValueError()
        return marker
    except (ValueError,KeyError,TypeError):
        raise RuntimeError('Installation binding needs private administrator repair') from None


def restore_installation(settings,root):
    """Call before profile_environment and NativeChat creation on adapter startup."""
    marker=read_marker(root)
    if marker and marker.get('verified') is True:
        profiles=Path(settings['HERMES_PROFILE_ROOT']).resolve()/'profiles'
        if marker.get('mode')=='premade':
            if marker['agents']!=preserved_profiles(settings): raise RuntimeError('Preserved installation bindings changed')
            settings.update(HERMES_PROFILE=marker['profile'],HERMES_TEAM_CONTEXT='false',HERMES_TEAM_AUTH_HOME='')
            return marker
        settings.update(HERMES_PROFILE=marker['profile'],HERMES_TEAM_CONTEXT='true',
            HERMES_TEAM_AUTH_HOME=str(profiles/marker['source_profile']))
    return marker


class Installation:
    def __init__(self,settings,root,chat):
        self.settings,self.root,self.chat=settings,Path(root),chat
        self.guard=threading.RLock()
        self.marker=self.root/'.runtime'/'os-installation.json'
        marker=read_marker(self.root)
        self.source=marker['source_profile'] if marker else settings['HERMES_PROFILE']

    def save_marker(self,marker):
        self.marker.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        temporary=self.marker.with_suffix('.tmp')
        temporary.write_text(json.dumps(marker),encoding='utf-8');os.chmod(temporary,0o600)
        temporary.replace(self.marker)

    def validate(self,body):
        if not isinstance(body,dict) or set(body)-{'action','org','user','name','purpose','instructions','names','voice'}:
            raise ValueError('Unsupported installation arguments')
        if body.get('action') not in {'verify','assistant','premade','voice','apply_voice'}: raise ValueError('Unsupported installation operation')
        if body.get('action')=='apply_voice' and set(body)!={'action','org','user'}: raise ValueError('Invalid apply arguments')
        if body.get('action')=='voice':
            if set(body)!={'action','org','user','voice'}: raise ValueError('Invalid voice setup arguments')
            from voice_setup import validate
            validate(body['voice'])
        elif 'voice' in body: raise ValueError('Invalid voice setup arguments')
        if body.get('action')=='premade' and set(body)-{'action','org','user','names'}: raise ValueError('Premade bindings are selected privately by the operator')
        if 'names' in body:
            if body['action']!='premade' or not isinstance(body['names'],list) or len(body['names'])!=2:
                raise ValueError('Choose a name for each assistant')
            for item in body['names']:
                if not isinstance(item,dict) or set(item)!={'id','name'}: raise ValueError('Only assistant names can be changed')
                UUID(item['id'])
                name=item['name']
                if not isinstance(name,str) or not name.strip() or len(name)>80 or re.search(r'[\x00-\x1f\x7f]',name):raise ValueError('Invalid assistant name')
            if len({item['id'] for item in body['names']})!=2:raise ValueError('Duplicate assistant identity')
        org=str(UUID(body.get('org',''))); user=body.get('user')
        if not isinstance(user,str) or not 0<len(user)<=200: raise ValueError('Owner identity is required')
        if body['action']=='assistant':
            for key,limit,required in [('name',80,True),('purpose',500,True),('instructions',8000,False)]:
                value=body.get(key,'')
                if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):
                    raise ValueError('Enter a valid assistant '+key)
        return org,user

    def verify(self,native):
        profile_environment(native)
        temporary=NativeChat(dict(native),self.root)
        try:
            snapshot=temporary.handle({'action':'connect'})
            info=snapshot.get('info',{})
            model,provider=info.get('model'),info.get('provider')
            if not isinstance(model,str) or not model or not isinstance(provider,str) or not provider:
                raise RuntimeError('Choose and authenticate a model in the backend before setup')
            options=temporary.model_options()
            if not any(p.get('slug')==provider and model in p.get('models',[]) for p in options.get('providers',[])):
                raise RuntimeError('The selected model is not available from the backend sign-in')
            idle(temporary)
            return {'verified':True,'runtime_profile':native['HERMES_PROFILE'],
                'model':model,'provider':provider,'native_voice':False,
                'native_voice_detail':'This adapter has no verified native browser-call transport. Existing backend voice configuration alone does not enable a browser call.'}
        finally:
            temporary.shutdown()

    def provision(self,marker,body):
        import yaml
        profiles=Path(self.settings['HERMES_PROFILE_ROOT']).resolve()/'profiles'
        source=profiles/self.source
        if source.is_symlink() or not source.is_dir(): raise RuntimeError('Configured backend source profile is unavailable')
        config=yaml.safe_load((source/'config.yaml').read_text(encoding='utf-8'))
        if not isinstance(config,dict) or not isinstance(config.get('model'),dict):
            raise RuntimeError('Configure the backend model first')
        home=profiles/marker['profile']
        if home.is_symlink(): raise RuntimeError('Unsafe installation profile binding')
        home.mkdir(mode=0o700,exist_ok=True)
        identity=(f"You are {body['name'].strip()}, the owner's main personal assistant inside Central OS. "
            "Your user identity and tools are fixed by authenticated workspace access. "
            "Use live workspace tools for current facts. Keep personal conversations and memory private. "
            "Do not claim an external action completed from a draft or pending approval. "
            "Configured purpose: "+body['purpose'].strip()+"\nOwner instructions:\n"+body.get('instructions','').strip())
        identity = central_ai_identity(identity)
        configured={'model':config['model'],'agent':{'system_prompt':identity,
            'reasoning_effort':config.get('agent',{}).get('reasoning_effort','low')},
            'reasoning':config.get('reasoning','none'),'timezone':config.get('timezone','UTC'),
            'platform_toolsets':{'cli':['memory','todo']},'terminal':{'backend':'local'},
            'mcp_servers':{k:v for k,v in config.get('mcp_servers',{}).items() if k=='laya'}}
        plugin=source/'plugins'/'hindsight'; memory=source/'hindsight'/'config.json'
        if plugin.is_dir() and memory.is_file():
            destination=home/'plugins'/'hindsight'
            if not destination.exists(): shutil.copytree(plugin,destination,ignore=shutil.ignore_patterns('__pycache__','.git'))
            options=json.loads(memory.read_text(encoding='utf-8'))
            options['bank_id']='michael-os-'+marker['profile']
            options.pop('bank_id_template',None)
            (home/'hindsight').mkdir(exist_ok=True)
            memory_target=home/'hindsight'/'config.json'
            memory_target.write_text(json.dumps(options),encoding='utf-8');os.chmod(memory_target,0o600)
            configured.update(memory={'provider':'hindsight'},plugins={'enabled':['hindsight'],'disabled':[]})
        # Credentials remain in the selected backend source. Linking its private environment
        # supports configured API-key providers without copying secrets into the OS or marker.
        if (source/'.env').is_file():
            environment=home/'.env'
            if environment.exists() and (not environment.is_symlink() or environment.resolve()!=(source/'.env').resolve()):
                raise RuntimeError('Existing owner credential configuration requires administrator review')
            if not environment.exists(): environment.symlink_to(source/'.env')
        for path,content in [(home/'config.yaml',yaml.safe_dump(configured,sort_keys=False,allow_unicode=True)),(home/'SOUL.md',identity)]:
            temporary=path.with_suffix('.setup.tmp'); temporary.write_text(content,encoding='utf-8');os.chmod(temporary,0o600);temporary.replace(path)
        import subprocess, sys
        skills_synced = False
        if Path('/opt/hermes').is_dir():
            try:
                res = subprocess.run(
                    [sys.executable, '-c', 'from tools.skills_sync import sync_skills; sync_skills(quiet=True)'],
                    env=dict(os.environ, HERMES_HOME=str(home)), cwd='/opt/hermes', timeout=30
                )
                if res.returncode == 0:
                    skills_synced = True
            except Exception:
                pass
        if not skills_synced and (source / 'skills').is_dir():
            dest_skills = home / 'skills'
            if not dest_skills.exists():
                shutil.copytree(source / 'skills', dest_skills, ignore=shutil.ignore_patterns('__pycache__', '.git'))
        return dict(self.settings,HERMES_PROFILE=marker['profile'],HERMES_TEAM_CONTEXT='true',HERMES_TEAM_AUTH_HOME=str(source))

    def handle(self,body):
        org,user=self.validate(body)
        with self.guard,self.chat.control_guard:
            marker=read_marker(self.root)
            if marker and (marker['org']!=org or marker['user']!=user):
                raise PermissionError('This backend already belongs to another installation owner')
            idle(self.chat)
            if body['action']=='apply_voice':
                if not marker or marker.get('verified') is not True: raise PermissionError('Complete assistant setup first')
                from coolify_voice import apply
                return apply(self.settings)
            if body['action']=='voice':
                if not marker or marker.get('verified') is not True:
                    raise PermissionError('Complete assistant setup first')
                from voice_setup import configure
                result=self.verify(dict(self.settings))
                return {**result,**configure(self.settings,body['voice'])}
            if body['action']=='verify':
                # Connection/auth/catalog verification makes no model completion request.
                result=self.verify(dict(self.settings))
                return {**result,'org':org,'user':user,'assistant_created':bool(marker and marker.get('verified'))}
            if body['action']=='premade':
                agents=preserved_profiles(self.settings)
                names={item['id']:item['name'].strip() for item in body.get('names',[])}
                if names and set(names)!={agent['id'] for agent in agents}:raise ValueError('Assistant bindings cannot be changed')
                profile=next(a['native_profile'] for a in agents if a['name']=='Leo')
                revision=hashlib.sha256(json.dumps(agents,sort_keys=True).encode()).hexdigest()
                if marker and (marker.get('mode')!='premade' or marker.get('revision')!=revision):
                    raise RuntimeError('The existing installation binding must be preserved; use a fresh adapter state volume')
                pending={'org':org,'user':user,'profile':profile,'source_profile':profile,'revision':revision,'mode':'premade','agents':agents,'verified':False}
                self.save_marker(pending)
                for agent in agents:
                    native=dict(self.settings,HERMES_PROFILE=agent['native_profile'],HERMES_TEAM_CONTEXT='false',HERMES_TEAM_AUTH_HOME='')
                    verified=self.verify(native)
                    if agent['name']=='Leo': leo_native,leo_result=native,verified
                if names:
                    from assistant_identity import rename_profile
                    for agent in agents:
                        rename_profile(Path(self.settings['HERMES_PROFILE_ROOT'])/'profiles'/agent['native_profile'],names[agent['id']],agent['name'])
                self.chat.close();self.chat.sid=self.chat.stored=None
                self.chat.info={};self.chat.events.clear();self.chat.requests.clear()
                self.settings.update(leo_native);self.chat.settings.update(leo_native)
                self.save_marker({**pending,'verified':True})
                return {**leo_result,'org':org,'user':user,'assistant_created':True,'premade_agents':[names.get(a['id'],a['name']) for a in agents]}
            if marker and marker.get('mode')=='premade':raise RuntimeError('Edit preserved assistants in their normal settings')
            profile='team-owner-'+hashlib.sha256(json.dumps([org,user]).encode()).hexdigest()[:32]
            definition={key:body.get(key,'').strip() for key in ['name','purpose','instructions']}
            revision=hashlib.sha256(json.dumps(definition,sort_keys=True).encode()).hexdigest()
            if marker and marker.get('verified'):
                if marker.get('revision')!=revision:
                    raise RuntimeError('Setup is complete. Edit the assistant in its normal settings.')
                return {**self.verify(dict(self.settings)),'org':org,'user':user,'assistant_created':True}
            pending={'org':org,'user':user,'profile':profile,'source_profile':self.source,'revision':revision,'verified':False}
            self.save_marker(pending)  # Reserve owner first; partial retries cannot switch owners.
            native=self.provision(pending,body)
            verified=self.verify(native)
            # Parent object is retained for adapter closures; only its idle native binding changes.
            self.chat.close()
            self.chat.sid=self.chat.stored=None
            self.chat.info={};self.chat.events.clear();self.chat.requests.clear()
            self.settings.update(native);self.chat.settings.update(native)
            self.save_marker({**pending,'verified':True})
            return {**verified,'org':org,'user':user,'assistant_created':True}
