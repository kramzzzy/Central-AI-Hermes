"""Member-scoped Hindsight controls. Only the private OS bridge may call this module.

No caller-provided bank, file path, provider URL, key or model is accepted. This
controls long-term Hindsight facts/sources; chat histories and backups are separate.
"""
import contextlib
import hashlib
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import UUID
from hermes_team import context_name
from hermes_settings import idle, SettingsError
from central_ai_integrations import memory_config


class MemoryError(RuntimeError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def revision(item):
    return hashlib.sha256(json.dumps({k:item.get(k) for k in ('text','state')}, sort_keys=True).encode()).hexdigest()


class MemoryControls:
    def __init__(self, settings, root, chat, team_pool, request=None):
        self.settings, self.root, self.chat, self.team_pool = settings, root, chat, team_pool
        self.lock = threading.RLock()
        self.policy_path = root / '.runtime' / 'memory-controls.json'
        self.request_override = request

    def resolve(self, body):
        org = str(UUID(body.get('org','')))
        user = body.get('user')
        if not isinstance(user,str) or not 0 < len(user) <= 200:
            raise MemoryError('Invalid member identity.')
        assistant = body.get('assistant')
        if assistant == 'leo':
            # Exact immutable owner authorization is enforced in the OS API.
            name = self.settings['HERMES_PROFILE']
            chat = self.chat
            item = None
        else:
            assistant = str(UUID(assistant or ''))
            name = context_name({'org_id':org,'assistant_id':assistant,'user_id':user})
            item = self.team_pool.contexts.get(name)
            chat = item['chat'] if item else None
            if item and item['users']:
                raise MemoryError('Finish the current assistant request before managing memories.',409)
        home = Path(self.settings['HERMES_PROFILE_ROOT']).resolve() / 'profiles' / name
        if home.is_symlink() or not home.is_dir():
            raise MemoryError('Start a conversation with this assistant before managing its memories.',409)
        cfg_path = home / 'hindsight' / 'config.json'
        if cfg_path.is_symlink() or not cfg_path.is_file():
            raise MemoryError('Long-term memory is not configured for this assistant.',503)
        cfg = memory_config(json.loads(cfg_path.read_text(encoding='utf-8')))
        expected = 'michael-os-'+name
        if cfg.get('mode') != 'local_external' or cfg.get('api_url') != 'http://hindsight:8888' or cfg.get('bank_id') != expected:
            raise MemoryError('The private memory configuration needs attention.',503)
        if not isinstance(cfg.get('api_key'),str) or len(cfg['api_key']) < 32:
            raise MemoryError('The memory service credential is unavailable.',503)
        return {'org':org,'user':user,'assistant':assistant,'name':name,'home':home,'chat':chat,'cfg':cfg,'bank':expected}

    def call(self, target, method, suffix, data=None):
        path = '/v1/default/banks/'+quote(target['bank'],safe='')+suffix
        if self.request_override:
            return self.request_override(target['bank'],method,path,data)
        request = Request('http://hindsight:8888'+path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Authorization':'Bearer '+target['cfg']['api_key'],'Content-Type':'application/json'},method=method)
        try:
            with urlopen(request,timeout=25) as response:
                raw=response.read(4*1024*1024+1)
                if len(raw)>4*1024*1024:
                    raise MemoryError('This memory page is too large. Use a smaller page.',413)
                return json.loads(raw) if raw else {'success':True}
        except HTTPError as exc:
            if exc.code == 404:
                raise MemoryError('This memory or source was not found in your assistant.',404) from None
            raise MemoryError('The memory service could not complete this action.',503) from None
        except (TimeoutError,OSError):
            raise MemoryError('The memory service is temporarily unavailable.',503) from None

    def policies(self):
        if not self.policy_path.exists():return {}
        if self.policy_path.is_symlink():raise MemoryError('Memory policy storage is invalid.',503)
        value=json.loads(self.policy_path.read_text(encoding='utf-8'))
        if not isinstance(value,dict):raise MemoryError('Memory policy storage is invalid.',503)
        return value

    def save_policies(self,value):
        self.policy_path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.policy_path.with_suffix('.tmp')
        temp.write_text(json.dumps(value),encoding='utf-8');os.chmod(temp,0o600);temp.replace(self.policy_path)

    def policy(self,target):
        value=self.policies().get(target['name'],{})
        return {k:value.get(k) for k in ('retention_days','last_purge','last_error')}

    def quiet(self,target):
        if target['chat']:
            try:idle(target['chat'])
            except SettingsError as exc:raise MemoryError(str(exc),409) from None
        stats=self.call(target,'GET','/stats?refresh=true')
        if stats.get('pending_operations',0):
            raise MemoryError('Memory processing is still settling. Wait and retry before changing it.',409)

    @staticmethod
    def facts(data):
        keys=('id','text','context','fact_type','document_id','date','mentioned_at','state','updated_at','edited_at')
        return [{**{k:row.get(k) for k in keys},'revision':revision(row)} for row in data.get('items',[])]

    def handle(self,body):
        if not isinstance(body,dict) or set(body)-{'action','org','user','assistant','offset','q','id','text','revision','retention_days','confirm'}:
            raise MemoryError('Unsupported memory arguments.')
        action=body.get('action')
        if action not in {'list','documents','source','export','correct','forget','delete_source','clear','retention'}:
            raise MemoryError('Unsupported memory operation.')
        with self.lock,self.team_pool.guard:
            target=self.resolve(body)
            control=target['chat'].control_guard if target['chat'] else contextlib.nullcontext()
            with control:
                if action in {'list','documents','export'}:
                    offset=body.get('offset',0)
                    if type(offset) is not int or not 0<=offset<=100000:
                        raise MemoryError('Choose a valid page.')
                    q=body.get('q','')
                    if not isinstance(q,str) or len(q)>200:raise MemoryError('Search text is too long.')
                    source=action=='documents'
                    path='/documents' if source else '/memories/list'
                    result=self.call(target,'GET',path+'?'+urlencode({'limit':50,'offset':offset,'q':q}))
                    items=[{k:r.get(k) for k in ('id','created_at','updated_at','memory_unit_count','text_length')} for r in result.get('items',[])] if source else self.facts(result)
                    data={'items':items,'total':result.get('total',0),'offset':offset,'limit':50,'policy':self.policy(target),
                        'scope':'This assistant’s private long-term memory. Chat histories, attachments and backups are separate.'}
                    if action=='export':data.update(exported_at=datetime.now(timezone.utc).isoformat(),partial=offset+len(items)<result.get('total',0))
                    return data
                if action=='source':
                    identifier=self.identifier(body.get('id'))
                    result=self.call(target,'GET','/documents/'+quote(identifier,safe=''))
                    return {k:result.get(k) for k in ('id','original_text','created_at','updated_at','memory_unit_count')}
                self.quiet(target)
                if action=='retention':
                    days=body.get('retention_days')
                    if days not in {None,30,90,180,365} or isinstance(days,bool):raise MemoryError('Choose a supported retention period.')
                    policies=self.policies()
                    old=policies.get(target['name'],{})
                    policies[target['name']]={**old,'org':target['org'],'user':target['user'],'assistant':target['assistant'],'retention_days':days}
                    self.save_policies(policies)
                    return {'success':True,'policy':self.policy(target)}
                if action=='clear':
                    if body.get('confirm')!='CLEAR':raise MemoryError('Type CLEAR to remove these long-term memories.')
                    self.reset_context(target)
                    self.quiet(target)
                    result=self.call(target,'DELETE','/memories')
                    return {'success':True,'scope':'Long-term memories cleared. Existing chats, attachments and backups are unchanged. Reopening old chats may recreate facts.'}
                identifier=self.identifier(body.get('id'))
                if action=='delete_source':
                    if body.get('confirm')!='DELETE':raise MemoryError('Type DELETE to remove the source and its linked memories.')
                    # Bank-scoped source lookup and delete. Never a global document lookup.
                    self.call(target,'GET','/documents/'+quote(identifier,safe=''))
                    self.reset_context(target)
                    self.quiet(target)
                    self.call(target,'DELETE','/documents/'+quote(identifier,safe=''))
                    return {'success':True,'scope':'Source and its linked long-term memories removed. Chat histories and backups are separate.'}
                identifier=str(UUID(identifier))
                item=self.call(target,'GET','/memories/'+identifier)
                if item.get('fact_type',item.get('type')) not in {'world','experience'}:
                    raise MemoryError('This is a derived memory. Edit or remove its source facts instead.',409)
                if body.get('revision')!=revision(item):raise MemoryError('This memory changed. Refresh before editing.',409)
                if action=='correct':
                    text=body.get('text')
                    if not isinstance(text,str) or not 1<=len(text.strip())<=8000:raise MemoryError('Enter a correction within 8,000 characters.')
                    update={'text':text.strip()}
                else:update={'state':'invalidated','reason':'Member requested forgetting in workspace settings.'}
                self.reset_context(target)
                self.quiet(target)
                self.call(target,'PATCH','/memories/'+identifier,update)
                return {'success':True,'scope':'Fact updated.' if action=='correct' else 'Fact excluded from recall; retained in the memory service’s audit archive. Delete its source for permanent source removal.'}

    @staticmethod
    def identifier(value):
        if not isinstance(value,str) or not 1<=len(value)<=400 or any(ord(c)<32 for c in value):
            raise MemoryError('Choose a valid memory or source.')
        return value

    def reset_context(self,target):
        # Discard already-recalled in-process context so subsequent turns reload.
        # Existing native sessions are deliberately preserved, not called erased.
        if target['chat']:
            target['chat'].close()
            target['chat'].sid=None
            target['chat'].stored=None
            target['chat'].info={}

    def maintenance(self):
        purged=0;skipped=0
        with self.lock,self.team_pool.guard:
            policies=self.policies()
            for name,policy in list(policies.items())[:100]:
                days=policy.get('retention_days')
                if days not in {30,90,180,365}:continue
                try:
                    target=self.resolve(policy)
                    if target['name']!=name:raise MemoryError('Policy identity changed.',409)
                    control=target['chat'].control_guard if target['chat'] else contextlib.nullcontext()
                    with control:
                        self.quiet(target)
                        cutoff=(datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
                        # Bound each sweep; delete from offset zero so pagination cannot skip.
                        result=self.call(target,'GET','/documents?'+urlencode({'limit':50,'offset':0,'time_field':'updated_at','end_date':cutoff}))
                        if result.get('items'):
                            self.reset_context(target)
                            self.quiet(target)
                        for item in result.get('items',[]):
                            self.call(target,'DELETE','/documents/'+quote(self.identifier(item.get('id')),safe=''));purged+=1
                        policy['last_purge']=datetime.now(timezone.utc).isoformat();policy['last_error']=None
                except Exception:
                    skipped+=1;policy['last_error']='Retention deferred: assistant busy or memory unavailable.'
            self.save_policies(policies)
        return {'purged_sources':purged,'deferred':skipped}
