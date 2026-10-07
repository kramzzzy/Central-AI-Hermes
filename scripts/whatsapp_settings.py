"""Installation-owner contact settings in the fixed Coolify application."""
import json
import re
from pathlib import Path
import coolify_voice as control
from central_ai_integrations import atomic_write
from whatsapp_routing import routing

KEYS = ('WHATSAPP_OWNER','WHATSAPP_BUSINESS_CONTACT','WHATSAPP_GROUP','WHATSAPP_TEAM_CONTACTS')

def validate(value):
    if not isinstance(value,dict) or set(value)!= {'owner','business','group','team'}:
        raise ValueError('Check the WhatsApp contact fields.')
    def phone(value,required=False):
        if not isinstance(value,str): raise ValueError('Use a phone number with country code.')
        number=re.sub(r'[+ ()-]','',value)
        if (required or number) and not re.fullmatch(r'[1-9][0-9]{7,14}',number): raise ValueError('Use an international number with country code, for example +63 900 000 0000.')
        return number
    owner,business=phone(value['owner'],True),phone(value['business'])
    group=value['group'].strip() if isinstance(value['group'],str) else None
    if group is None or (group and not re.fullmatch(r'[0-9]{5,30}(?:-[0-9]{5,20})?@g\.us',group)): raise ValueError('Use a group ID ending in @g.us, not an invite link.')
    if not isinstance(value['team'],list) or len(value['team'])>20: raise ValueError('Add up to 20 private team contacts.')
    team=[]
    for item in value['team']:
        if not isinstance(item,dict) or set(item)!={'name','number'} or not isinstance(item['name'],str) or not 1<=len(item['name'].strip())<=80 or re.search(r'[\r\n\0]',item['name']): raise ValueError('Each contact needs a name and number.')
        team.append({'name':item['name'].strip(),'number':phone(item['number'],True)})
    numbers=[owner]+([business] if business else [])+[c['number'] for c in team]
    if len(numbers)!=len(set(numbers)): raise ValueError('Each number can be assigned only once.')
    return {'owner':owner,'business':business,'group':group,'team':team}

def current():
    value=routing()
    return {'owner':value['owner'],'business':value['business'],'group':value['group'],
        'team':[{'name':c['name'],'number':c['number']} for c in value['contacts'] if c['role']=='team']}

def path(root):
    target=Path(root)/'.whatsapp-deployment.json'
    if target.is_symlink(): raise ValueError('Contact settings need administrator repair.')
    return target

@control.serialized
def status(root):
    target=path(root)
    state=json.loads(target.read_text()) if target.exists() else {}
    result={'managed':control.configured(),'settings':state.get('settings',current()),'deployment':'ready'}
    if state and not state.get('applied'):
        result['deployment']='awaiting_apply'
        if state.get('uuid'):
            try: phase=control.deployment_phase(state['uuid'])
            except control.VoiceDeploymentError:
                result['deployment']='unknown';return result
            result['deployment']='failed' if phase in control.GONE else 'deploying'
            if phase in control.DONE:
                result['deployment']='ready' if current()==state['settings'] else 'failed'
                if result['deployment']=='ready':
                    state['applied']=True;atomic_write(target,json.dumps(state))
    return result

@control.serialized
def apply(root):
    if status(root)['deployment'] in {'deploying','unknown'}: return status(root)
    target=path(root)
    if not target.exists(): raise ValueError('Save contact settings first.')
    state=json.loads(target.read_text())
    result=control.request('POST','/deploy',{'uuid':control.config()['application']})
    jobs=result.get('deployments') or []
    if len(jobs)!=1 or not re.fullmatch(r'[a-zA-Z0-9]{10,64}',jobs[0].get('deployment_uuid','')): raise ValueError('Deployment was not confirmed. Retry applying.')
    state.update(uuid=jobs[0]['deployment_uuid'],applied=False)
    atomic_write(target,json.dumps(state))
    return {'managed':True,'settings':state['settings'],'deployment':'deploying'}

@control.serialized
def save(root,value):
    value=validate(value)
    if status(root)['deployment'] in {'deploying','unknown'}: raise ValueError('Wait for the contact deployment to finish.')
    # A voice deployment uses the same app and must settle before another change.
    voice=control.pending({'HERMES_PROFILE_ROOT':str(root)})
    if voice and not voice.get('applied'):
        if not voice.get('uuid') or control.deployment_phase(voice['uuid']) not in control.DONE: raise ValueError('Finish applying voice settings before changing contacts.')
    app=control.config()['application']
    records=control.request('GET','/applications/'+app+'/envs')
    before={c['key']:c.get('value') or '' for c in records if not c.get('is_preview') and c['key'] in KEYS}
    atomic_write(Path(root)/'.whatsapp-environment-backup.json',json.dumps(before))
    values=dict(zip(KEYS,[value['owner'],value['business'],value['group'],json.dumps(value['team'])]))
    control.request('PATCH','/applications/'+app+'/envs/bulk',{'data':[{'key':k,'value':v,'is_preview':False,'is_literal':True,'is_buildtime':False,'is_runtime':True} for k,v in values.items()]})
    checked={c['key']:c.get('value') or '' for c in control.request('GET','/applications/'+app+'/envs') if not c.get('is_preview')}
    if any(checked.get(k)!=v for k,v in values.items()): raise ValueError('Contact settings were not confirmed. Retry saving.')
    atomic_write(path(root),json.dumps({'settings':value,'uuid':None}))
    return apply(root)
