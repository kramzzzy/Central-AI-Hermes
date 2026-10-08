"""Local-only authenticated adapter for the installed Hermes and Laya runtimes.

This Windows development bridge is not a public production gateway.
"""
import os,json,hmac,subprocess,threading,time,re,contextlib,tempfile,shutil,sys,importlib
from uuid import UUID
from bridge_process import RunProcesses
from hermes_profile import profile_environment
from hermes_chat import NativeChat
from hermes_library import read_library
from hermes_settings import run_settings, runtime_settings, idle, SettingsError
from hermes_task_files import task_file_context
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
settings={}
for line in Path(os.environ.get('HERMES_BRIDGE_CONFIG', str(ROOT/'.env'))).read_text().splitlines():
 if '=' in line and not line.startswith('#'):
  key,value=line.split('=',1);settings[key]=value
settings.setdefault('HERMES_PYTHON', os.environ.get('HERMES_PYTHON', sys.executable))
settings.setdefault('HERMES_VOICE_PROVIDER', os.environ.get('HERMES_VOICE_PROVIDER', settings.get('HERMES_CHAT_PROVIDER', 'openrouter')))
KEY=settings['HERMES_API_KEY']
if len(KEY)<32:raise RuntimeError('A generated bridge secret is required')
from hermes_installation import restore_installation, Installation
restore_installation(settings,ROOT)
profile_environment(settings)  # Fail startup rather than silently selecting default.
lock=threading.BoundedSemaphore(1)
catalog_lock=threading.Lock()
portrait_slot=threading.BoundedSemaphore(1)
library_lock=threading.BoundedSemaphore(2)
settings_lock=threading.Lock()
routine_tick_slot=threading.BoundedSemaphore(1)
catalog_cache={'at':0,'data':None}
processes=RunProcesses()
chat=NativeChat(settings,ROOT)
from hermes_team import TeamPool
team_chats=TeamPool(settings,ROOT)
from hermes_google import GoogleWorkspace, GoogleError
google_storage_root=Path(os.environ.get('HERMES_PROFILE_ROOT','/opt/data')) if Path(os.environ.get('HERMES_PROFILE_ROOT','/opt/data')).is_dir() else (ROOT/'.runtime')
google_workspace=GoogleWorkspace(google_storage_root)
from hermes_voice import VoiceProvider
voice=VoiceProvider(settings)
team_voice=VoiceProvider(dict(settings,HERMES_PROFILE='os-team-voice'),defaults=voice.config)
from hermes_specialists import SpecialistPool
specialists=SpecialistPool(settings,ROOT)
from hermes_os_calls import OSCalls
os_calls=OSCalls(settings,ROOT,team_chats)
from os_stream_voice import OSStreamVoice
stream_voice=OSStreamVoice(os_calls,voice,team_voice)
os_calls.on_chat=lambda item: setattr(item,'cancel_specialists',lambda: specialists.cancel_parent(item))
chat.cancel_specialists=lambda: specialists.cancel_parent(chat)
team_chats.on_chat=lambda item: setattr(item,'cancel_specialists',lambda: specialists.cancel_parent(item))
from hermes_routines import Routines,RoutineError
routines=Routines(ROOT,settings,google_workspace)
from hermes_memory_manage import MemoryControls,MemoryError
memory_controls=MemoryControls(settings,ROOT,chat,team_chats)
installation=Installation(settings,ROOT,chat)
from hermes_whatsapp_setup import WhatsAppSetup
whatsapp_setup=WhatsAppSetup(ROOT)

@contextlib.contextmanager
def backup_snapshot_window():
 """Reserve bridge writers without waiting on calls or reversing existing lock order."""
 from hermes_backup_export import ExportError
 acquired=[]
 def reserve(guard):
  if not guard.acquire(blocking=False):
   raise ExportError('Finish active calls and work before exporting a backup.',409)
  acquired.append(guard)
 try:
  # Reserve both library readers: one acquired permit does not exclude the other.
  for guard in [lock,settings_lock,catalog_lock,library_lock,library_lock,
                routine_tick_slot,google_workspace.lock,memory_controls.lock,
                routines.tick_lock,routines.lock,routines.brief_slot,
                team_chats.guard,specialists.guard,processes.lock,chat.control_guard,os_calls.guard]:
   reserve(guard)
  contexts=list(team_chats.contexts.values())
  native_chats=[chat]
  for item in contexts:
   if item.get('users'):
    raise ExportError('Finish active team work before exporting a backup.',409)
   if item['chat'] not in native_chats:
    reserve(item['chat'].control_guard);native_chats.append(item['chat'])
  if specialists.active or processes.active:
   raise ExportError('Finish active tasks before exporting a backup.',409)
  if os_calls.busy():
   raise ExportError('Finish active calls and call tasks before exporting a backup.',409)
  for native in native_chats:
   if native.info.get('running') or native.voice_call or native.requests:
    raise ExportError('Finish active calls, replies and reviews before exporting a backup.',409)
   try:idle(native)
   except SettingsError as exc:
    raise ExportError('Finish pending work before exporting a backup.' if exc.status==409 else
                      'Native session readiness could not be verified.',exc.status) from None
  yield
 finally:
  for guard in reversed(acquired):guard.release()

def start_routine_tick():
 """At most one bridge tick thread, including long reminder authorization loops."""
 if not routine_tick_slot.acquire(blocking=False):return {'accepted':False,'busy':True}
 def tick():
  try:routines.tick()
  except Exception:pass
  finally:routine_tick_slot.release()
 try:threading.Thread(target=tick,name='private-routine-tick',daemon=True).start()
 except Exception:
  routine_tick_slot.release();raise
 return {'accepted':True}

class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def reply(self,status,data):
  payload=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
 def authorized(self):
  return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+KEY) and not self.headers.get('Origin')
 def do_GET(self):
  if self.path == '/ready' or (self.path == '/health' and not self.authorized()):return self.reply(200,{'status':'healthy','hermes':True})
  if not self.authorized():return self.reply(401,{'error':'Unauthorized'})
  if self.path=='/voice/status':return self.reply(200,voice.status())
  if self.path=='/voice/team/status':return self.reply(200,team_voice.status())
  if self.path=='/health':
   from hermes_laya import available
   with processes.lock:background_runs=len(processes.active)
   return self.reply(200,{'mode':'profile-aware','modes':['draft-only','tools'],'tools':[],'hermes':True,'cancellation':True,'runtime_profile':settings['HERMES_PROFILE'],'runtime_host':os.environ.get('HERMES_RUNTIME_HOST','native-windows'),'library_protocol':1,'settings_protocol':1,'google_protocol':1,'task_attachments':1,'laya':available(),'chat_connected':bool(chat.sid),'voice_call':bool(chat.voice_call),'background_runs':background_runs,'call_tasks_busy':os_calls.busy(),'conversation':voice.status()['conversation']})
  if self.path == '/models' or self.path.startswith('/models?'):
   from provider_models import get_catalog_models
   force = 'refresh=1' in self.path or 'refresh=true' in self.path
   catalog = get_catalog_models(settings, force_refresh=force)
   return self.reply(200, catalog)
  if self.path=='/catalog':
   with catalog_lock:
    if time.monotonic()-catalog_cache['at']<30:return self.reply(200,catalog_cache['data'])
    try:
     env=profile_environment(settings)
     result=subprocess.run([settings.get('HERMES_PYTHON',sys.executable),str(ROOT/'scripts'/'hermes-catalog.py')],capture_output=True,text=True,encoding='utf-8',timeout=22,env=env,cwd=ROOT)
     if result.returncode:return self.reply(503,{'error':'Hermes inventory unavailable'})
     from hermes_artwork import available as artwork_available
     data=json.loads(result.stdout);data['image_generation']=artwork_available()
     from provider_models import get_catalog_models
     live_catalog = get_catalog_models(settings)
     live_models = live_catalog.get('models', [])
     if live_models:
      data['available_models'] = live_models
      data['provider_models'] = live_catalog.get('model_details', [])
      data['vendors'] = live_catalog.get('vendors', [])
     else:
      models=[]
      try:
       raw=chat.rpc('model.options',{'profile':settings['HERMES_PROFILE'],'include_unconfigured':False},timeout=5)
       for row in raw.get('providers',[]):
        if row.get('available') is not False and row.get('authenticated') is not False:
         for m in row.get('models',[]):
          val=str(m) if isinstance(m,str) else str(m.get('id',m.get('name','')))
          if val and val not in models:models.append(val)
      except Exception:pass
      if not models:
       models=[os.environ.get('HERMES_CHAT_MODEL','nousresearch/hermes-4-405b'),
               os.environ.get('WHATSAPP_CHAT_MODEL','deepseek/deepseek-v4.1-flash'),
               os.environ.get('HERMES_VOICE_MODEL','meta-llama/llama-3.3-70b-instruct'),
               'anthropic/claude-3-5-sonnet',
               'anthropic/claude-3-7-sonnet',
               'openai/gpt-4o',
               'openai/gpt-4o-mini',
               'openai/o3-mini',
               'deepseek/deepseek-chat-v3.1',
               'meta-llama/llama-3.3-70b-instruct',
               'google/gemini-2.5-pro',
               'google/gemini-2.5-flash']
      data['available_models']=list(dict.fromkeys(m for m in models if m))
     catalog_cache.update(at=time.monotonic(),data=data)
     return self.reply(200,data)
    except Exception:return self.reply(503,{'error':'Hermes inventory unavailable'})
  self.reply(404,{'error':'Not found'})
 def do_POST(self):
  if self.path=='/backup/export':
   from hermes_backup_export import export_authorized,native_export,ExportError
   if not export_authorized(self.headers):return self.reply(401,{'error':'Unauthorized'})
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<=length<=4096:return self.reply(413,{'error':'Backup request too large'})
    if length and json.loads(self.rfile.read(length))!={}:return self.reply(400,{'error':'Backup export takes no arguments.'})
    # Credential-bearing native archive stays in a private tempfile until streamed.
    # Hindsight and asynchronous memory capture have independent snapshot boundaries.
    with tempfile.TemporaryDirectory(prefix='private-native-export-') as directory:
     os.chmod(directory,0o700);destination=Path(directory)/'native.tar.gz'
     with backup_snapshot_window():native_export(settings,ROOT,destination)
     self.send_response(200)
     self.send_header('Content-Type','application/gzip')
     self.send_header('Content-Length',str(destination.stat().st_size))
     self.send_header('Cache-Control','no-store')
     self.send_header('X-Content-Type-Options','nosniff')
     self.end_headers()
     with destination.open('rb') as stream:shutil.copyfileobj(stream,self.wfile,length=65536)
    return
   except ExportError as exc:return self.reply(exc.status,{'error':str(exc)})
   except (ValueError,TypeError):return self.reply(400,{'error':'Invalid backup export request.'})
   except (BrokenPipeError,ConnectionResetError):return
   except Exception:return self.reply(503,{'error':'Native backup export failed; no snapshot was verified.'})
  if self.path=='/memory/v1/chat/completions':
   from hermes_memory import authorized, complete
   if not authorized(self.headers):return self.reply(401,{'error':{'message':'Unauthorized','type':'authentication_error'}})
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=262144:return self.reply(413,{'error':{'message':'Memory request too large'}})
    status,data=complete(settings,ROOT,json.loads(self.rfile.read(length)))
    return self.reply(status,data)
   except ValueError:return self.reply(400,{'error':{'message':'Invalid memory request'}})
   except Exception:return self.reply(503,{'error':{'message':'Memory processor unavailable'}})
  if self.path in {'/os/inspect','/google/tool','/specialists/tool','/os/widget','/os/profile-update','/os/call'}:
   # A separate per-process MCP credential cannot choose an actor or receive the grant.
   authorization=self.headers.get('Authorization','')
   try:inspection_chat=chat if hmac.compare_digest(authorization,'Bearer '+chat.os_tool_token) else (team_chats.inspection_chat(authorization) or specialists.inspection_chat(authorization) or os_calls.inspection_chat(authorization))
   except PermissionError:return self.reply(403,{'error':'Conversation access changed.'})
   if self.headers.get('Origin') or not inspection_chat:
    return self.reply(401,{'error':'Unauthorized'})
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=(100000 if self.path=='/google/tool' else 32768 if self.path=='/os/profile-update' else 20000 if self.path=='/specialists/tool' else 8192):return self.reply(413,{'error':'Tool request too large'})
    body=json.loads(self.rfile.read(length))
    if self.path=='/os/call':
     target=str(body.get('target','')).strip()
     digits=re.sub(r'[^\d]','',target)
     target_name=target
     if not digits or len(digits)<7:
      for cf in ['/data/contacts.json',str(ROOT/'.runtime'/'contacts.json'),str(ROOT/'data'/'contacts.json')]:
       if os.path.exists(cf):
        try:
         cdata=json.loads(Path(cf).read_text(encoding='utf-8'))
         for c in cdata.get('contacts',[]):
          if target.lower() in c.get('name','').lower() or target.lower() in c.get('role','').lower():
           digits=c.get('phone_number','')
           target_name=c.get('name')
           break
        except Exception:pass
       if digits:break
     if not digits:
      return self.reply(400,{'error':f'Could not find contact or valid phone number for: {target}'})
     call_payload={
      'target':digits,
      'mode':'native',
      'name':target_name,
      'reason':body.get('reason',''),
      'requested_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
     }
     called=False
     for caller_url in [os.environ.get('CALLER_URL','http://caller:8080'),'http://127.0.0.1:8080']:
      try:
       import urllib.request
       creq=urllib.request.Request(f"{caller_url}/call",data=json.dumps(call_payload).encode(),headers={'Content-Type':'application/json'})
       with urllib.request.urlopen(creq,timeout=3) as cresp:
        if cresp.status in {200,201,202}:
         called=True;break
      except Exception:pass
     if not called:
      for req_path in ['/data/call-request.json',str(ROOT/'.runtime'/'call-request.json')]:
       try:
        Path(req_path).write_text(json.dumps(call_payload,indent=2),encoding='utf-8')
        called=True;break
       except Exception:pass
     return self.reply(200,{
      'ok':True,
      'target':digits,
      'name':target_name,
      'message':f"Outbound WhatsApp call initiated for {target_name} ({digits}). Leo is connected on audio."
     })
    if self.path=='/os/widget':
     with inspection_chat.guard:
      inspection_chat.sequence += 1
      inspection_chat.events.append({
       'type': 'widget.action',
       'seq': inspection_chat.sequence,
       'voice_turn': getattr(inspection_chat, 'voice_turn', None),
       'payload': {
        'action': body.get('action', 'open_widget'),
        'widget': body.get('widget', 'website'),
        'url': body.get('url'),
        'title': body.get('title'),
        'command': body.get('command')
       }
      })
      inspection_chat.event_ready.notify_all()
     return self.reply(200, {'received': True, 'widget': body.get('widget'), 'url': body.get('url')})
    if self.path=='/os/profile-update':
     from assistant_instructions import update_profile_instructions
     return self.reply(200, update_profile_instructions(settings, ROOT, body))
    if self.path=='/specialists/tool':return self.reply(200,specialists.tool(inspection_chat,body))
    specialists.validate_tool(inspection_chat,self.path,body)
    from hermes_os import inspect_from_chat, google_from_chat
    handler=google_from_chat if self.path=='/google/tool' else inspect_from_chat
    return self.reply(200,handler(inspection_chat,body))
   except PermissionError:return self.reply(403,{'error':'Workspace access expired or was revoked.'})
   except ValueError:return self.reply(400,{'error':'Invalid inspection arguments'})
   except Exception:return self.reply(503,{'error':'Live OS inspection unavailable; no status verified.'})
  if not self.authorized():return self.reply(401,{'error':'Unauthorized'})
  if self.path=='/artwork':
   from hermes_artwork import generate,ArtworkError
   acquired=False
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=1024:return self.reply(413,{'error':'Portrait request too large'})
    acquired=portrait_slot.acquire(blocking=False)
    if not acquired:return self.reply(429,{'error':'Another portrait is being generated. Please wait.'})
    return self.reply(200,generate(json.loads(self.rfile.read(length))))
   except ArtworkError as exc:return self.reply(exc.status,{'error':str(exc)})
   except (ValueError,TypeError):return self.reply(400,{'error':'Invalid portrait role.'})
   except Exception:return self.reply(503,{'error':'Portrait generation is unavailable.'})
   finally:
    if acquired:portrait_slot.release()
  if self.path=='/voice/conversation':
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=40000:return self.reply(413,{'error':'Voice request too large'})
    return stream_voice.handle(self,json.loads(self.rfile.read(length)))
   except PermissionError:return self.reply(403,{'error':'Call or member access changed.'})
   except (ValueError,KeyError,TypeError):return self.reply(400,{'error':'Invalid voice call request.'})
   except Exception:return self.reply(503,{'error':'Voice is temporarily unavailable. You can try another question.'})
  if self.path=='/voice/tasks':
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=262144:return self.reply(413,{'error':'Call request too large'})
    return self.reply(200,os_calls.handle(json.loads(self.rfile.read(length))))
   except PermissionError:return self.reply(403,{'error':'Call or member access changed.'})
   except (ValueError,KeyError,TypeError):return self.reply(400,{'error':'Invalid call task request.'})
   except Exception:return self.reply(503,{'error':'The call task service is unavailable; no outcome confirmed.'})
  if self.path=='/backups':
   from hermes_backups import handle_backup,BackupError
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=4096:return self.reply(413,{'error':'Backup request too large'})
    return self.reply(200,handle_backup(json.loads(self.rfile.read(length))))
   except BackupError as exc:return self.reply(exc.status,{'error':str(exc)})
   except (ValueError,TypeError):return self.reply(400,{'error':'Check backup settings.'})
  if self.path=='/whatsapp/setup':
   from hermes_whatsapp_setup import SetupError
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=16000:return self.reply(413,{'error':'Setup request too large'})
    body=json.loads(self.rfile.read(length))
    if body.get('action') in {'save_settings','apply_settings'} and os_calls.busy():return self.reply(409,{'error':'Finish active app calls before applying contacts.'})
    return self.reply(200,whatsapp_setup.handle(body))
   except __import__('coolify_voice').VoiceDeploymentError as exc:return self.reply(503,{'error':str(exc)})
   except SetupError as exc:return self.reply(exc.status,{'error':str(exc)})
   except (ValueError,TypeError) as exc:return self.reply(400,{'error':str(exc) or 'Check WhatsApp contact fields.'})
   except Exception:return self.reply(503,{'error':'WhatsApp setup is unavailable'})
  if self.path=='/installation':
   from coolify_voice import VoiceDeploymentError
   acquired=False
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=16000:return self.reply(413,{'error':'Setup request too large'})
    acquired=lock.acquire(blocking=False)
    if not acquired:return self.reply(409,{'error':'Finish the active task before setup.'})
    if os_calls.busy():return self.reply(409,{'error':'Finish active calls before changing setup.'})
    body=json.loads(self.rfile.read(length))
    if body.get('action') in {'voice','apply_voice'}:
     from coolify_voice import configured
     if configured():
      import urllib.request
      with urllib.request.urlopen('http://voice:8081/health',timeout=5) as response:
       if json.load(response).get('active_call'):return self.reply(409,{'error':'Finish the WhatsApp call before applying voice settings.'})
    result=installation.handle(body)
    global voice,team_voice
    voice=VoiceProvider(settings)
    team_voice=VoiceProvider(dict(settings,HERMES_PROFILE='os-team-voice'),defaults=voice.config)
    stream_voice.voice,stream_voice.team_voice=voice,team_voice
    current_voice=voice.status()
    local=current_voice.get('transcription')=='local' and current_voice.get('conversation')=='stream'
    result.update(fish_realtime=bool(current_voice.get('realtime') and not local),
      native_voice=bool(current_voice.get('realtime') and local),
      native_voice_detail='Local English speech and transcription on the VPS; model replies use OpenRouter credits.' if local else 'Local calls are not configured.',
      voice_name=current_voice.get('voice'))
    return self.reply(200,result)
   except VoiceDeploymentError as exc:return self.reply(503,{'error':str(exc)})
   except PermissionError:return self.reply(403,{'error':'This backend belongs to another installation.'})
   except (ValueError,TypeError):return self.reply(400,{'error':'Check assistant setup fields.'})
   except Exception:return self.reply(503,{'error':'Native assistant setup failed. Check backend model sign-in and retry.'})
   finally:
    if acquired:lock.release()
  if self.path in {'/specialists','/routines','/memory/manage','/memory/maintenance'}:
   acquired=False
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=20000:return self.reply(413,{'error':'Request too large'})
    body=json.loads(self.rfile.read(length))
    if self.path=='/specialists':result=specialists.handle(body)
    elif self.path=='/routines':
     if body.get('action')=='tick':
      result=start_routine_tick()
     else:result=routines.handle(body)
    else:
     mutation=self.path=='/memory/maintenance' or body.get('action') not in {'list','documents','source','export'}
     if mutation:
      acquired=lock.acquire(blocking=False)
      if not acquired:return self.reply(409,{'error':'Finish the active task before changing memory.'})
     result=memory_controls.maintenance() if self.path=='/memory/maintenance' else memory_controls.handle(body)
    return self.reply(200,result)
   except (RoutineError,MemoryError) as exc:return self.reply(exc.status,{'error':str(exc)})
   except PermissionError:return self.reply(403,{'error':'Assistant access changed.'})
   except (ValueError,TypeError,KeyError):return self.reply(400,{'error':'Invalid request.'})
   except Exception:return self.reply(503,{'error':'The backend service is unavailable. Retry shortly.'})
   finally:
    if acquired:lock.release()
  if self.path=='/google':
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=150000:return self.reply(413,{'error':'Google request too large'})
    return self.reply(200,google_workspace.handle(json.loads(self.rfile.read(length))))
   except GoogleError as exc:return self.reply(exc.status,{'error':str(exc)})
   except (ValueError,TypeError):return self.reply(400,{'error':'Invalid Google request.'})
   except Exception:return self.reply(503,{'error':'Google connection is unavailable. Check backend configuration.'})
  if self.path.startswith('/voice/'):
   try:
    if self.path.startswith('/voice/team/'):
     return team_voice.proxy(self)
    if self.path=='/voice/agent/authorize':
     length=int(self.headers.get('Content-Length','0'))
     if not 0<length<=1024:return self.reply(413,{'error':'Invalid authorization'})
     token=json.loads(self.rfile.read(length)).get('authorization','')
     allowed=any(p.callback_authorized(token) for p in [voice,team_voice])
     return self.reply(200,{'allowed':allowed})
    return voice.proxy(self)
   except (BrokenPipeError,ConnectionResetError):return
   except Exception:return self.reply(503,{'error':'The Hermes voice provider is unavailable.'})
  if self.path=='/decisions/triage':
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=20000:return self.reply(413,{'error':'Request too large'})
    body=json.loads(self.rfile.read(length))
    if body.get('profile')!=settings['HERMES_PROFILE']:return self.reply(409,{'error':'Hermes profile changed'})
    from hermes_laya import classify
    return self.reply(200,classify(body.get('text')))
   except Exception:return self.reply(503,{'error':'Hermes decision tool is unavailable. Keep this request for manual review.'})
  if self.path=='/settings':
   acquired=False
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=16000:return self.reply(413,{'error':'Settings request too large'})
    body=json.loads(self.rfile.read(length))
    if body.get('profile')!=settings['HERMES_PROFILE']:
     return self.reply(409,{'error':'The connected profile changed. Refresh before continuing.'})
    mutation=body.get('action') in {'save','model','inherit_model','tool','test','schedule'}
    if not settings_lock.acquire(timeout=0 if mutation else 5):return self.reply(429,{'error':'Another settings request is running. Retry shortly.'})
    acquired=True
    with chat.control_guard:
     task_acquired=False
     try:
      if mutation:
       task_acquired=lock.acquire(blocking=False)
       if not task_acquired:raise SettingsError('Finish the active task before changing Hermes settings.',409)
       idle(chat)
      result=(run_settings(settings,ROOT,body) if body.get('action') in {'read','save','model','inherit_model','tool','plugins'} else runtime_settings(chat,body))
      result.update(profile=settings['HERMES_PROFILE'],protocol=1)
      return self.reply(200,result)
     finally:
      if task_acquired:lock.release()
   except SettingsError as exc:return self.reply(exc.status,{'error':str(exc)})
   except Exception:return self.reply(503,{'error':'Hermes settings are unavailable. Check the native connection and retry.'})
   finally:
    if acquired:settings_lock.release()
  if self.path=='/library':
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=75000:return self.reply(413,{'error':'Library request too large'})
    body=json.loads(self.rfile.read(length))
    if body.get('profile')!=settings['HERMES_PROFILE']:
     return self.reply(409,{'error':'The connected profile changed. Refresh before continuing.'})
    if not library_lock.acquire(blocking=False):
     return self.reply(429,{'error':'Hermes is reading another resource. Please retry shortly.'})
    try:return self.reply(200,read_library(settings,ROOT,body))
    finally:library_lock.release()
   except Exception as exc:
    msg=str(exc)
    return self.reply(400 if any(w in msg for w in ['already exists', 'cannot be deleted', 'Frontmatter', 'YAML', 'Invalid skill']) else 503,{'error':msg[:500] if msg else 'Hermes could not process this resource. Refresh and try again.'})
  if self.path=='/chat':
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=12500000:return self.reply(413,{'error':'Chat request too large'})
    return self.reply(200,chat.handle(json.loads(self.rfile.read(length))))
   except Exception as exc:
    return self.reply(400,{'error':str(exc)[:500]})
  if self.path=='/browser':
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=16384:return self.reply(413,{'error':'Browser request too large'})
    body=json.loads(self.rfile.read(length))
    url=body.get('url','').strip()
    if not url:return self.reply(400,{'error':'Missing url parameter'})
    if not url.startswith(('http://','https://')):
     url='https://'+url
    action=body.get('action','navigate')
    try:
     bt=importlib.import_module('tools.browser_tool')
    except ImportError:
     hermes_repo=os.environ.get('HERMES_REPO','/opt/hermes')
     if hermes_repo not in sys.path and os.path.exists(hermes_repo):
      sys.path.insert(0,hermes_repo)
     try:
      bt=importlib.import_module('tools.browser_tool')
     except ImportError:
      return self.reply(503,{'error':'Hermes browser tool is not installed or available in this runtime.'})
    if action=='navigate':
     raw=bt.browser_navigate(url)
    elif action=='snapshot':
     raw=bt.browser_snapshot()
    elif action=='click':
     raw=bt.browser_click(body.get('ref',''))
    elif action=='type':
     raw=bt.browser_type(body.get('ref',''), body.get('text',''))
    elif action=='scroll':
     raw=bt.browser_scroll(body.get('direction','down'))
    elif action=='back':
     raw=bt.browser_back()
    elif action=='press':
     raw=bt.browser_press(body.get('key','Enter'))
    else:
     return self.reply(400,{'error':f'Unsupported browser action: {action}'})
    try:
     data=json.loads(raw)
    except Exception:
     data={'result':raw}
    return self.reply(200,data)
   except Exception as exc:
    return self.reply(500,{'error':f'Browser tool execution failed: {str(exc)}'})
  if self.path in {'/team/chat','/team/release'}:
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=12700000:return self.reply(413,{'error':'Chat request too large'})
    body=json.loads(self.rfile.read(length))
    return self.reply(200,team_chats.release(body['team']) if self.path=='/team/release' else team_chats.handle(body))
   except Exception as exc:
    return self.reply(409,{'error':str(exc)[:500]})
  cancel=re.fullmatch(r'/v1/runs/([a-f0-9-]{36})/cancel',self.path)
  if cancel:
   try:
    run_id=str(UUID(cancel.group(1)));processes.cancel(run_id)
    return self.reply(200,{'cancel_requested':True})
   except Exception:return self.reply(503,{'error':'Cancellation could not be confirmed'})
  if self.path!='/v1/chat/completions':return self.reply(404,{'error':'Not found'})
  try:length=int(self.headers.get('Content-Length','0'))
  except ValueError:return self.reply(400,{'error':'Invalid length'})
  if not 0<length<=23068672:return self.reply(413,{'error':'Request too large'})
  if not lock.acquire(blocking=False):return self.reply(429,{'error':'Agent busy'})
  try:
   body=json.loads(self.rfile.read(length));env=profile_environment(settings)
   if body.get('runtime_profile') and body['runtime_profile']!=settings['HERMES_PROFILE']:
    return self.reply(409,{'error':'Hermes profile changed; start a new interaction'})
   run_id=str(UUID(body.get('session_id','')))
   with task_file_context(body, ROOT/'.runtime') as prepared:
    process=processes.run(run_id,[settings.get('HERMES_PYTHON',sys.executable),str(ROOT/'scripts'/'hermes-job.py')],json.dumps(prepared),env=env,cwd=ROOT)
   if process.returncode:
    # Local diagnostics only; never return provider internals or credentials to clients.
    (ROOT/'.runtime'/'hermes-last-error.log').write_text(process.stderr,encoding='utf-8')
    return self.reply(502,{'error':'Hermes run failed; check local bridge diagnostics'})
   self.reply(200,json.loads(process.stdout))
  except subprocess.TimeoutExpired:self.reply(504,{'error':'Hermes timed out'})
  except Exception:self.reply(500,{'error':'Bridge failed'})
  finally:lock.release()
if __name__=='__main__':
 (ROOT/'.runtime').mkdir(exist_ok=True)
 bind=os.environ.get('HERMES_BRIDGE_BIND','0.0.0.0')
 port=int(os.environ.get('HERMES_PORT','8642'))
 voice.start_websocket(bind)
 print(f'Private Hermes adapter listening on {bind}:{port}',flush=True)
 ThreadingHTTPServer((bind,port),Handler).serve_forever()
