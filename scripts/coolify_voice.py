"""Owner setup's fixed voice-only Coolify operations; credentials stay on the VPS."""
import hashlib
import json
import re
import urllib.error
import urllib.request
import threading
from functools import wraps
from pathlib import Path
from central_ai_integrations import atomic_write

CONFIG = Path('/run/secrets/coolify_voice')
guard = threading.RLock()


def serialized(function):
    @wraps(function)
    def call(*args, **kwargs):
        with guard: return function(*args, **kwargs)
    return call


class VoiceDeploymentError(RuntimeError):
    status = None  # Coolify HTTP status, when it answered


def configured():
    return CONFIG.is_file() and not CONFIG.is_symlink()


def config():
    if not configured():
        raise VoiceDeploymentError('Coolify voice setup is not connected.')
    value = json.loads(CONFIG.read_text())
    if set(value) != {'url', 'token', 'application'} or not (re.fullmatch(r'https://[a-zA-Z0-9.-]+', value['url']) or value['url']=='http://coolify:8080') or not re.fullmatch(r'[a-zA-Z0-9]{10,64}', value['application']):
        raise VoiceDeploymentError('Coolify voice setup needs administrator repair.')
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(method, path, body=None):
    settings = config()
    req = urllib.request.Request(settings['url']+'/api/v1'+path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization':'Bearer '+settings['token'], 'Content-Type':'application/json', 'User-Agent':'CentralAI-Voice-Setup/1.0'}, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=25) as response:
            return json.loads(response.read())
    except Exception as exc:
        error = VoiceDeploymentError('Could not reach Coolify. Your setup is saved; retry applying the voice settings.')
        error.status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
        raise error from None


DONE = {'finished', 'missing'}
GONE = {'failed', 'cancelled', 'canceled', 'missing'}


def deployment_phase(uuid):
    """Coolify's status, or 'missing' when it has no record (it can drop a deploy queued behind another)."""
    try:
        return request('GET', '/deployments/'+uuid).get('status')
    except VoiceDeploymentError as exc:
        if exc.status == 404: return 'missing'
        raise


def pending_path(settings):
    path = Path(settings['HERMES_PROFILE_ROOT'])/'.voice-deployment.json'
    if path.is_symlink():
        raise VoiceDeploymentError('Voice deployment state needs administrator repair.')
    return path


def pending(settings):
    path = pending_path(settings)
    return json.loads(path.read_text()) if path.exists() else None


def fingerprint(value):
    return hashlib.sha256(json.dumps([value.get('CENTRAL_AI_CALL_SPEECH',''),value.get('OPENROUTER_TTS_MODEL',''),value.get('OPENROUTER_TTS_VOICE','')]).encode()).hexdigest()


def wait_for_contacts(settings):
    path=Path(settings['HERMES_PROFILE_ROOT'])/'.whatsapp-deployment.json'
    if not path.exists(): return
    state=json.loads(path.read_text())
    if state.get('applied'): return
    if state.get('uuid') and deployment_phase(state['uuid']) in DONE: return
    raise VoiceDeploymentError('Finish applying WhatsApp contacts before changing voice settings.')


@serialized
def deployment_status(settings, current):
    if not configured(): return {'managed':False}
    state=pending(settings)
    if not state: return {'managed':True,'state':'ready'}
    if state.get('applied'): return {'managed':True,'state':'ready'}
    if not state.get('uuid'): return {'managed':True,'state':'awaiting_apply'}
    if not re.fullmatch(r'[a-zA-Z0-9]{10,64}',state['uuid']):
        raise VoiceDeploymentError('Invalid voice deployment reference.')
    try:
        status=deployment_phase(state['uuid'])
    except VoiceDeploymentError:
        return {'managed':True,'state':'unknown'}
    if status in DONE:
        ready=state['fingerprint']==fingerprint(current)
        if ready:
            state['applied']=True
            atomic_write(pending_path(settings),json.dumps(state))
        return {'managed':True,'state':'ready' if ready else 'failed'}
    return {'managed':True,'state':'failed' if status in {'failed','cancelled','canceled'} else 'deploying'}


@serialized
def save(settings, value, current):
    raise VoiceDeploymentError('Manage OpenRouter speech settings in the Hermes stack environment.')


@serialized
def apply(settings):
    wait_for_contacts(settings)
    state=pending(settings)
    if not state: return {'deploying':False}
    if state.get('uuid'):
        previous=deployment_phase(state['uuid'])
        if previous not in GONE:
            return {'deploying':previous!='finished'}
    result=request('POST','/deploy',{'uuid':config()['application']})
    jobs=result.get('deployments') or []
    if len(jobs)!=1 or not jobs[0].get('deployment_uuid'):
        raise VoiceDeploymentError('Coolify did not confirm a deployment. Retry applying the saved settings.')
    state['uuid']=jobs[0]['deployment_uuid']
    atomic_write(pending_path(settings),json.dumps(state))
    return {'deploying':True}
