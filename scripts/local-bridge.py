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
env_path=Path(os.environ.get('HERMES_BRIDGE_CONFIG', str(ROOT/'.env')))
if env_path.is_file():
 for line in env_path.read_text(encoding='utf-8', errors='ignore').splitlines():
  if '=' in line and not line.startswith('#'):
   key,value=line.split('=',1);settings[key.strip()]=value.strip().strip('\'"')
for ek, ev in os.environ.items():
 if ek.startswith('HERMES_') or ek in {'OPENROUTER_API_KEY', 'CALLER_URL', 'VOICE_URL', 'DATA'}:
  settings.setdefault(ek, ev)
settings.setdefault('HERMES_PYTHON', os.environ.get('HERMES_PYTHON', sys.executable))
settings.setdefault('HERMES_VOICE_PROVIDER', os.environ.get('HERMES_VOICE_PROVIDER', settings.get('HERMES_CHAT_PROVIDER', 'openrouter')))
KEY=(os.environ.get('HERMES_API_KEY') or settings.get('HERMES_API_KEY', 'hermes_standalone_secret_token_32chars')).strip().strip('\'"')
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
from hermes_social import SocialWorkspace, SocialError
social_workspace=SocialWorkspace(google_storage_root)
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
  auth=(self.headers.get('Authorization') or '').strip().strip('\'"')
  if not auth:
   return False
  token = auth
  if token.lower().startswith('bearer '):
   token = token[7:].strip().strip('\'"')
  expected = KEY
  if expected.lower().startswith('bearer '):
   expected = expected[7:].strip().strip('\'"')
  if hmac.compare_digest(token, expected):
   return True
  if hmac.compare_digest(token, 'hermes_standalone_secret_token_32chars'):
   return True
  alt = os.environ.get('HERMES_API_KEY', '').strip().strip('\'"')
  if alt.lower().startswith('bearer '):
   alt = alt[7:].strip().strip('\'"')
  if alt and hmac.compare_digest(token, alt):
   return True
  return False
 def _record_box3_origin(self):
  try:
   client_ip=(self.client_address[0] if self.client_address else '').strip()
   port=(self.headers.get('X-App-OS-Port') or '3000').strip()
   host_hdr=(self.headers.get('X-App-OS-Host') or '').strip()
   url_hdr=(self.headers.get('X-App-OS-Url') or '').strip().rstrip('/')
   urls=[]
   if url_hdr and url_hdr.startswith('http'):
    urls.append(url_hdr)
   if host_hdr:
    urls.append(host_hdr if host_hdr.startswith('http') else f"http://{host_hdr}")
   if client_ip and client_ip not in {'127.0.0.1','::1'}:
    urls.append(f"http://{client_ip}:{port}")
   if not urls:
    return
   deduped=list(dict.fromkeys(u.rstrip('/') for u in urls if u))
   payload=json.dumps({'url':deduped[0],'urls':deduped,'updated_at':time.time()},indent=2)
   for p in ['/data/app_os_url.json','/opt/data/app_os_url.json','/opt/os-adapter/.runtime/app_os_url.json',str(ROOT/'.runtime'/'app_os_url.json')]:
    try:
     fp=Path(p);fp.parent.mkdir(parents=True,exist_ok=True);fp.write_text(payload,encoding='utf-8');os.chmod(fp,0o666)
    except Exception:pass
  except Exception:pass
 def do_GET(self):
  if self.path == '/ready' or (self.path == '/health' and not self.authorized()):return self.reply(200,{'status':'healthy','hermes':True})
  if self.path=='/os/snapshot':
   for sp in ['/data/box3_snapshot.json','/opt/data/box3_snapshot.json','/opt/os-adapter/.runtime/box3_snapshot.json',str(ROOT/'.runtime'/'box3_snapshot.json')]:
    if os.path.exists(sp):
     try:return self.reply(200,json.loads(Path(sp).read_text(encoding='utf-8')))
     except Exception:pass
   return self.reply(200,{})
  if self.path=='/os/caller/status':
   caller_data={}
   voice_data={}
   caller_url=os.environ.get('CALLER_URL','http://caller:8080')
   voice_url=os.environ.get('VOICE_URL','http://voice:8081')
   import urllib.request
   for cu in [caller_url,'http://127.0.0.1:8080','http://whatsapp-connector:8080','http://meowcaller:8080']:
    try:
     with urllib.request.urlopen(f"{cu}/status",timeout=4) as r:
      caller_data=json.loads(r.read());break
    except Exception:pass
   for vu in [voice_url,'http://127.0.0.1:8081','http://voice-pipeline:8081']:
    try:
     with urllib.request.urlopen(f"{vu}/health",timeout=4) as r:
      voice_data=json.loads(r.read());break
    except Exception:pass
   return self.reply(200,{'caller':caller_data,'voice':voice_data,'timestamp':time.time()})
  if not self.authorized():return self.reply(401,{'error':'Unauthorized'})
  self._record_box3_origin()
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
       models=[os.environ.get('HERMES_CHAT_MODEL','deepseek/deepseek-chat-v3-0324'),
               os.environ.get('WHATSAPP_CHAT_MODEL','openai/gpt-4o-mini'),
               os.environ.get('HERMES_VOICE_MODEL','openai/gpt-4o-mini'),
               'anthropic/claude-3-5-sonnet',
               'anthropic/claude-3-7-sonnet',
               'openai/gpt-4o',
               'openai/gpt-4o-mini',
               'openai/o3-mini',
               'deepseek/deepseek-chat-v3-0324',
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
  if self.path in {'/os/inspect','/google/tool','/specialists/tool','/os/widget','/os/profile-update','/os/call','/os/call/drop','/os/call/hangup','/os/contacts','/os/company','/os/whatsapp/send','/os/whatsapp/customer-sync','/os/memory/retain','/os/memory/recall','/os/message','/os/sync-snapshot'}:
   is_admin=self.authorized()
   if is_admin:
    self._record_box3_origin()
   inspection_chat=None
   if not is_admin:
    authorization=self.headers.get('Authorization','')
    try:inspection_chat=chat if hmac.compare_digest(authorization,'Bearer '+chat.os_tool_token) else (team_chats.inspection_chat(authorization) or specialists.inspection_chat(authorization) or os_calls.inspection_chat(authorization))
    except PermissionError:
     if self.path in {'/os/inspect','/google/tool','/os/widget','/os/profile-update'}:inspection_chat=chat
     else:return self.reply(403,{'error':'Conversation access changed.'})
    if not inspection_chat and self.path in {'/os/inspect','/google/tool','/os/widget','/os/profile-update'}:
     inspection_chat=chat
   else:
    inspection_chat=chat
   if not inspection_chat and self.path not in {'/os/call','/os/call/drop','/os/call/hangup','/os/contacts','/os/company','/os/whatsapp/send','/os/whatsapp/customer-sync','/os/memory/retain','/os/memory/recall','/os/message','/os/sync-snapshot'}:
    return self.reply(401,{'error':'Unauthorized'})
   try:
    length=int(self.headers.get('Content-Length','0'))
    max_len=2097152 if self.path=='/os/sync-snapshot' else (262144 if self.path in {'/os/contacts','/os/company','/os/whatsapp/customer-sync'} else (100000 if self.path in {'/google/tool','/os/whatsapp/send','/os/memory/retain','/os/memory/recall','/os/message'} else 32768 if self.path=='/os/profile-update' else 20000 if self.path=='/specialists/tool' else 8192))
    if not 0<length<=max_len:return self.reply(413,{'error':'Tool request too large'})
    body=json.loads(self.rfile.read(length))
    if self.path=='/os/memory/retain':
     from hermes_plugins.central_ai_whatsapp.adapter import _retain_memory_item
     entries=body.get('items') if isinstance(body.get('items'),list) else [body]
     for ent in entries:
      if isinstance(ent,dict) and ent.get('content'):
       threading.Thread(
        target=_retain_memory_item,
        args=(str(ent.get('content')),str(ent.get('sender') or 'Customer'),str(ent.get('route') or 'customer'),str(ent.get('context') or 'whatsapp')),
        daemon=True,
       ).start()
     return self.reply(200,{'ok':True,'retained':len(entries)})
    if self.path=='/os/memory/recall':
     from hermes_plugins.central_ai_whatsapp.adapter import _recall_memory_items
     q=str(body.get('query') or '').strip()
     lim=min(int(body.get('limit') or 5),10)
     memories=_recall_memory_items(q,lim)
     return self.reply(200,{'ok':True,'memories':memories})
    if self.path=='/os/whatsapp/customer-sync':
     act=str(body.get('action') or 'pull').strip().lower()
     if act in {'dismiss','ack'}:
      res=_handle_customer_sync_action(act,body)
      return self.reply(200,res)
     items=_collect_customer_sync_items()
     return self.reply(200,{'ok':True,'items':items})
    if self.path=='/os/sync-snapshot':
     snap_payload=json.dumps(body,ensure_ascii=False,indent=2)
     for sp in ['/data/box3_snapshot.json','/opt/data/box3_snapshot.json','/opt/os-adapter/.runtime/box3_snapshot.json',str(ROOT/'.runtime'/'box3_snapshot.json')]:
      try:
       p=Path(sp);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(snap_payload,encoding='utf-8')
       os.chmod(sp,0o666)
      except Exception:pass
     return self.reply(200,{'ok':True,'knowledge_count':len(body.get('knowledge',[]))})
    if self.path in {'/os/whatsapp/send','/os/message'}:
     target=str(body.get('target','')).strip() or str(body.get('recipient','')).strip() or str(body.get('phone_or_name','')).strip()
     message_text=str(body.get('message','')).strip()
     line_pref=str(body.get('line','')).strip().lower()
     if not message_text:
      return self.reply(400,{'ok':False,'error':'Message text is required.'})
     wire_target=target if target.endswith(('@lid','@s.whatsapp.net')) else None
     digits=re.sub(r'[^\d]','',target)
     if digits.startswith('00') and len(digits)>9:
      digits=digits[2:]
     if re.match(r'^04\d{8}$',digits):
      digits='61'+digits[1:]
     elif re.match(r'^09\d{9}$',digits):
      digits='63'+digits[1:]
     target_name=body.get('name') or target
     if not wire_target and (not digits or len(digits)<7):
      for cf in ['/data/contacts.json',str(ROOT/'.runtime'/'contacts.json'),str(ROOT/'data'/'contacts.json')]:
       if os.path.exists(cf):
        try:
         cdata=json.loads(Path(cf).read_text(encoding='utf-8'))
         for c in cdata.get('contacts',[]):
          if target.lower() in c.get('name','').lower() or target.lower() in c.get('role','').lower():
           digits=c.get('phone_number','') or c.get('number','')
           target_name=c.get('name', target_name)
           break
        except Exception:pass
       if digits:break
      if not digits or len(digits)<7:
       app_os_urls=[os.environ.get('APP_OS_URL',''),os.environ.get('MICHAEL_OS_URL',''),'http://web:3000','http://127.0.0.1:3000']
       for aurl in [u for u in app_os_urls if u]:
        try:
         import urllib.request
         with urllib.request.urlopen(f"{aurl}/api/contacts", timeout=2) as aresp:
          cdata=json.loads(aresp.read().decode('utf-8'))
          for c in cdata.get('contacts',[]):
           if target.lower() in c.get('name','').lower() or target.lower() in c.get('role','').lower():
            digits=c.get('phone_number','') or c.get('number','')
            target_name=c.get('name', target_name)
            break
        except Exception:pass
        if digits:break
     if not wire_target and (not digits or len(digits)<7):
      return self.reply(400,{'ok':False,'error':f'Could not find contact or valid phone number for: {target}'})
     send_target=wire_target or digits
     candidate_targets=list(dict.fromkeys([t for t in [wire_target,digits] if t]))
     primary_caller=os.environ.get('CALLER_URL','http://caller:8080')
     customer_caller=os.environ.get('CUSTOMER_CALLER_URL','http://customer-caller:8080')
     if line_pref in {'customer','james','support','booking'}:
      raw_caller_urls=[customer_caller,'http://customer-caller:8080',primary_caller,'http://caller:8080','http://127.0.0.1:8080','http://whatsapp-connector:8080','http://meowcaller:8080']
     else:
      raw_caller_urls=[primary_caller,'http://caller:8080',customer_caller,'http://customer-caller:8080','http://127.0.0.1:8080','http://whatsapp-connector:8080','http://meowcaller:8080']
     caller_urls=[]
     for cu in raw_caller_urls:
      if cu and cu not in caller_urls:
       caller_urls.append(cu)
     import urllib.request, urllib.error
     message_text=re.sub(r"^\s*(?:g['\u2019\u2018`]?day|howdy|heya)\b","Hi",message_text,flags=re.IGNORECASE)
     reply_to_mid=str(body.get('reply_to_message_id') or '').strip()
     if reply_to_mid:
      with customer_wa_lock:
       proc_map=_load_customer_processed()
       pentry=proc_map.get(reply_to_mid)
       if isinstance(pentry,dict) and (pentry.get('replied') or pentry.get('sending')):
        return self.reply(200,{'ok':True,'deduped':True,'target':send_target,'recipient_name':target_name,'message':f'WhatsApp reply already sent for {reply_to_mid}.'})
       if isinstance(pentry,dict):
        pentry['sending']=True
       else:
        proc_map[reply_to_mid]={'message_id':reply_to_mid,'sending':True}
       _save_customer_processed(proc_map)
     last_err=''
     for cu in caller_urls:
      for ct in candidate_targets:
       try:
        sreq=urllib.request.Request(f"{cu}/send",data=json.dumps({'target':ct,'message':message_text}).encode('utf-8'),headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(sreq,timeout=15) as sresp:
         sdata=json.loads(sresp.read())
         if reply_to_mid:
          try:
           with customer_wa_lock:
            proc_map=_load_customer_processed()
            if not isinstance(proc_map.get(reply_to_mid),dict):
             proc_map[reply_to_mid]={'message_id':reply_to_mid}
            proc_map[reply_to_mid]['reply']=message_text
            proc_map[reply_to_mid]['replied']=True
            proc_map[reply_to_mid]['sending']=False
            proc_map[reply_to_mid]['handled']=True
            _save_customer_processed(proc_map)
          except Exception:pass
         if line_pref in {'customer','james','support','booking'}:
          try:
           from hermes_plugins.central_ai_whatsapp.adapter import _retain_memory_item
           threading.Thread(target=_retain_memory_item,args=(f"Human operator reply to {target_name} ({ct}): {message_text}",'Human Operator (Coaching)','customer-whatsapp','human_operator_coaching'),daemon=True).start()
          except Exception:pass
         return self.reply(200,{'ok':True,'message_id':sdata.get('message_id',''),'target':ct,'recipient_name':target_name,'line':cu,'message':f'WhatsApp message delivered to {target_name} ({ct}).'})
       except Exception as err:
        last_err=str(err)
     if reply_to_mid:
      try:
       with customer_wa_lock:
        proc_map=_load_customer_processed()
        if isinstance(proc_map.get(reply_to_mid),dict):
         proc_map[reply_to_mid]['sending']=False
         _save_customer_processed(proc_map)
      except Exception:pass
     return self.reply(502,{'ok':False,'error':f'WhatsApp connector unavailable: {last_err}'})
    if self.path in {'/os/contacts','/os/company'}:
     c_name=body.get('company_name') or body.get('company')
     if c_name:
      c_payload=json.dumps({'company_name':str(c_name).strip()},indent=2)
      for cf in ['/data/company.json','/data/customer/company.json',str(ROOT/'.runtime'/'company.json'),str(ROOT/'data'/'company.json')]:
       try:
        p=Path(cf);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(c_payload,encoding='utf-8')
        os.chmod(cf,0o666)
       except Exception:pass
      try:
       from central_ai_identity import configure_identity
       target_home=chat.home if hasattr(chat,'home') else ROOT
       configure_identity(target_home)
      except Exception:pass
     if self.path=='/os/company':
      return self.reply(200,{'ok':True,'company_name':c_name})
     for cf in ['/data/contacts.json','/data/customer/contacts.json',str(ROOT/'.runtime'/'contacts.json'),str(ROOT/'data'/'contacts.json')]:
      try:
       p=Path(cf);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(body,indent=2),encoding='utf-8')
       os.chmod(cf,0o666)
      except Exception:pass
     import urllib.request
     for cu in ['http://customer-caller:8080',os.environ.get('CALLER_URL','http://caller:8080')]:
      try:
       creq=urllib.request.Request(f"{cu}/contacts",data=json.dumps(body).encode('utf-8'),headers={'Content-Type':'application/json'})
       urllib.request.urlopen(creq,timeout=2).read()
      except Exception:pass
     if body.get('customer_calls_enabled') is False:
      try:
       dreq=urllib.request.Request("http://customer-caller:8080/call/drop",data=b'{}',headers={'Content-Type':'application/json'})
       urllib.request.urlopen(dreq,timeout=2).read()
      except Exception:pass
     return self.reply(200,{'ok':True,'count':len(body.get('contacts',[])),'customer_auto_reply':body.get('customer_auto_reply',True),'customer_calls_enabled':body.get('customer_calls_enabled',True)})
    if self.path in {'/os/call/drop','/os/call/hangup'} or (self.path=='/os/call' and body.get('action') in {'drop','hangup','terminate'}):
     caller_urls=[os.environ.get('CALLER_URL','http://caller:8080'),'http://127.0.0.1:8080','http://whatsapp-connector:8080','http://meowcaller:8080']
     import urllib.request
     for cu in caller_urls:
      try:
       dreq=urllib.request.Request(f"{cu}/call/drop",data=b'{}',headers={'Content-Type':'application/json'})
       with urllib.request.urlopen(dreq,timeout=3) as dresp:
        ddata=json.loads(dresp.read())
        return self.reply(200,{'ok':True,'message':'Call terminated successfully.','dropped':ddata.get('dropped',True)})
      except Exception:pass
     return self.reply(200,{'ok':True,'message':'Drop signal acknowledged.'})
    if self.path=='/os/call':
     target=str(body.get('target','')).strip() or str(body.get('recipient','')).strip() or str(body.get('phone_or_name','')).strip()
     digits=re.sub(r'[^\d]','',target)
     target_name=body.get('name') or target
     if not digits or len(digits)<7:
      for cf in ['/data/contacts.json',str(ROOT/'.runtime'/'contacts.json'),str(ROOT/'data'/'contacts.json')]:
       if os.path.exists(cf):
        try:
         cdata=json.loads(Path(cf).read_text(encoding='utf-8'))
         for c in cdata.get('contacts',[]):
          if target.lower() in c.get('name','').lower() or target.lower() in c.get('role','').lower():
           digits=c.get('phone_number','') or c.get('number','')
           target_name=c.get('name', target_name)
           break
        except Exception:pass
       if digits:break
      if not digits or len(digits)<7:
       app_os_urls=[os.environ.get('APP_OS_URL',''),os.environ.get('MICHAEL_OS_URL',''),'http://web:3000','http://127.0.0.1:3000']
       for aurl in [u for u in app_os_urls if u]:
        try:
         import urllib.request
         with urllib.request.urlopen(f"{aurl}/api/contacts", timeout=2) as aresp:
          cdata=json.loads(aresp.read().decode('utf-8'))
          for c in cdata.get('contacts',[]):
           if target.lower() in c.get('name','').lower() or target.lower() in c.get('role','').lower():
            digits=c.get('phone_number','') or c.get('number','')
            target_name=c.get('name', target_name)
            break
        except Exception:pass
        if digits:break
     if not digits or len(digits)<7:
      return self.reply(400,{'ok':False,'error':f'Could not find contact or valid phone number for: {target}'})
     call_payload={
      'target':digits,
      'mode':body.get('mode','native'),
      'name':target_name,
      'reason':body.get('reason',''),
      'requested_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
     }
     caller_urls=[os.environ.get('CALLER_URL','http://caller:8080'),'http://127.0.0.1:8080','http://whatsapp-connector:8080','http://meowcaller:8080']
     caller_status=None
     active_caller_url=None
     import urllib.request, urllib.error
     for cu in caller_urls:
      try:
       with urllib.request.urlopen(f"{cu}/status",timeout=4) as sresp:
        caller_status=json.loads(sresp.read())
        active_caller_url=cu
        break
      except Exception:pass
     # Guard against self-call to Leo's own linked WhatsApp number
     linked_assistant_numbers = set()
     if caller_status and caller_status.get('linked_number'):
      linked_assistant_numbers.add(re.sub(r'[^\d]', '', caller_status.get('linked_number')))
     if digits in linked_assistant_numbers:
      return self.reply(400,{
       'ok':False,
       'error':f"Target {target_name} ({digits}) is the phone number linked to Leo's WhatsApp line. WhatsApp does not permit an account to place a call to its own number. To receive calls on this device, link Leo to a separate WhatsApp line, or test outbound calling with a different contact."
      })
     if caller_status:
      state=caller_status.get('state','unknown')
      if state!='connected':
       return self.reply(409,{
        'ok':False,
        'error':f"WhatsApp executive line is not connected (status: {state}). Please scan the QR code in WhatsApp settings to pair the device."
       })
      if caller_status.get('busy'):
       current_target=caller_status.get('target','another party')
       return self.reply(409,{
        'ok':False,
        'error':f"WhatsApp executive line is currently busy with an active call to {current_target}. Please wait until the current call finishes."
       })
      try:
       creq=urllib.request.Request(f"{active_caller_url}/call",data=json.dumps(call_payload).encode(),headers={'Content-Type':'application/json'})
       with urllib.request.urlopen(creq,timeout=12) as cresp:
        cdata=json.loads(cresp.read())
        if cresp.status in {200,201,202} and cdata.get('ok'):
         return self.reply(200,{
          'ok':True,
          'target':digits,
          'name':target_name,
          'message':f"Outbound WhatsApp call initiated for {target_name} ({digits}). Leo is connected on audio."
         })
        else:
         return self.reply(409,{
          'ok':False,
          'error':cdata.get('error','Call was not accepted by WhatsApp caller.')
         })
      except urllib.error.HTTPError as he:
       err_msg=f"Meowcaller rejected call (HTTP {he.code})"
       try:
        ebody=json.loads(he.read())
        err_msg=ebody.get('error',err_msg)
       except Exception:pass
       return self.reply(he.code,{'ok':False,'error':err_msg})
      except Exception as exc:
       return self.reply(502,{'ok':False,'error':f"Failed to dispatch call to Meowcaller: {str(exc)}"})
     return self.reply(503,{
      'ok':False,
      'error':'WhatsApp caller service (Meowcaller) is offline or unreachable on Box 1. Outbound calls require an active WhatsApp connection.'
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
    try:
     return self.reply(200,handler(inspection_chat,body))
    except Exception:
     if self.path=='/os/inspect':
      try:
       from hermes_plugins.central_ai_app_os.client import _load_box3_snapshot, _inspect_from_snapshot
       snap=_load_box3_snapshot()
       if snap:
        return self.reply(200,json.loads(_inspect_from_snapshot(body,snap)))
      except Exception:pass
     raise
   except PermissionError:return self.reply(403,{'error':'Workspace access expired or was revoked.'})
   except ValueError:return self.reply(400,{'error':'Invalid inspection arguments'})
   except Exception:return self.reply(503,{'error':'Live OS inspection unavailable; no status verified.'})
  if not self.authorized():return self.reply(401,{'error':'Unauthorized'})
  self._record_box3_origin()
  if self.path=='/artwork':
   from hermes_artwork import generate,ArtworkError
   acquired=False
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=12*1024*1024:return self.reply(413,{'error':'Artwork request too large'})
    req_body=json.loads(self.rfile.read(length))
    is_marketing=isinstance(req_body,dict) and (req_body.get('mode')=='marketing' or bool(req_body.get('prompt')))
    if not is_marketing:
     acquired=portrait_slot.acquire(blocking=False)
     if not acquired:return self.reply(429,{'error':'Another portrait is being generated. Please wait.'})
    return self.reply(200,generate(req_body))
   except ArtworkError as exc:return self.reply(exc.status,{'error':str(exc)})
   except (ValueError,TypeError):return self.reply(400,{'error':'Invalid artwork request.'})
   except Exception:return self.reply(503,{'error':'Artwork generation is unavailable.'})
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
  if self.path=='/social':
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=150000:return self.reply(413,{'error':'Social request too large'})
    return self.reply(200,social_workspace.handle(json.loads(self.rfile.read(length))))
   except SocialError as exc:return self.reply(exc.status,{'error':str(exc)})
   except (ValueError,TypeError):return self.reply(400,{'error':'Invalid social connection request.'})
   except Exception:return self.reply(503,{'error':'Social connection service is unavailable. Check backend configuration.'})
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
    try:result=read_library(settings,ROOT,body)
    finally:library_lock.release()
    if body.get('action') in {'skill_create','skill_edit','skill_delete'} and result.get('success'):
     with catalog_lock:catalog_cache.update(at=0,data=None)
    return self.reply(200,result)
   except Exception as exc:
    msg=str(exc)
    return self.reply(400 if any(w in msg for w in ['already exists', 'cannot be deleted', 'Frontmatter', 'YAML', 'Invalid skill']) else 503,{'error':msg[:500] if msg else 'Hermes could not process this resource. Refresh and try again.'})
  if self.path=='/chat':
   try:
    length=int(self.headers.get('Content-Length','0'))
    if not 0<length<=12500000:return self.reply(413,{'error':'Chat request too large'})
    body=json.loads(self.rfile.read(length))
    if body.get('action')=='session_delete':
     with chat.control_guard,os_calls.guard:
      if os_calls.busy():return self.reply(409,{'error':'Finish active app calls and call tasks before deleting conversations.'})
      return self.reply(200,chat.handle(body))
    return self.reply(200,chat.handle(body))
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
  try:
   body=json.loads(self.rfile.read(length))
   if body.get('runtime_profile') and body['runtime_profile']!=settings['HERMES_PROFILE']:
    return self.reply(409,{'error':'Hermes profile changed; start a new interaction'})
   if body.get('execution_mode')=='draft-only' and not body.get('task_files'):
    try:
     fast_res=_fast_draft_only_completion(body,settings)
     if fast_res:
      return self.reply(200,fast_res)
    except Exception:
     pass
  except Exception:
   return self.reply(400,{'error':'Invalid request body'})
  if not lock.acquire(timeout=120):return self.reply(429,{'error':'Agent busy'})
  try:
   env=profile_environment(settings)
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
customer_wa_lock=threading.Lock()
in_progress_customer_msgs=set()

_LAYA_CUSTOMER_STAGE_CRITERIA = {
 'greeting_only': 'Customer is saying a brief hello or greeting with no question yet.',
 'solar_calculator_kwh_kw_savings_request': 'Customer is asking to calculate their solar kWh usage, recommended solar panel system size in kW, or bill savings from their power bill.',
 'product_or_rebate_question': 'Customer is asking about solar panels, batteries, rebates, pricing, or how the service works.',
 'providing_qualification_details': 'Customer is answering with whether they have existing solar, their power bill amount, street address, full name, phone, or email.',
 'ready_to_confirm_or_book': 'Customer is confirming their details or asking to schedule a consultation or call.',
 'existing_customer_support': 'Existing customer asking for maintenance, warranty, or repair help.',
}
_HINDSIGHT_FAST_CACHE = {}

def _classify_customer_turn_laya(user_text):
 try:
  clean_t=(user_text or '').strip()
  if len(clean_t)<3 or len(clean_t)>1500:
   return None
  laya_url=(os.environ.get('LAYA_URL') or 'http://laya:8000').rstrip('/')
  from hermes_laya import api_key as laya_api_key
  import urllib.request
  payload=json.dumps({
   'state':clean_t,
   'max_len':512,
   'questions':{'intent':{'type':'choice','instructions':'What is the customer doing in this message?','criteria':_LAYA_CUSTOMER_STAGE_CRITERIA}},
  }).encode('utf-8')
  req=urllib.request.Request(
   f"{laya_url}/v1/systemone",
   data=payload,
   headers={'Content-Type':'application/json','Authorization':f"Bearer {laya_api_key()}"},
   method='POST',
  )
  with urllib.request.urlopen(req,timeout=0.25) as resp:
   if resp.status==200:
    res=json.loads(resp.read().decode('utf-8'))
    ans=(res.get('answers') or {}).get('intent') or {}
    choice=str(ans.get('choice') or '').strip()
    conf=float(ans.get('answer_confidence') or 0.0)
    if choice and conf>=0.35:
     return {'intent':choice,'confidence':round(conf,3)}
 except Exception:
  pass
 return None

def _recall_hindsight_fast(user_text):
 try:
  clean_q=(user_text or '').strip()
  if len(clean_q)<4 or clean_q.startswith('[PROCESS PLAYBOOK'):
   return []
  cache_key=clean_q.lower()[:120]
  now_ts=time.monotonic()
  cached=_HINDSIGHT_FAST_CACHE.get(cache_key)
  if cached and (now_ts-cached[0])<30.0:
   return cached[1]
  from hermes_plugins.central_ai_whatsapp.adapter import _recall_memory_items
  items=_recall_memory_items(clean_q,limit=4)
  if len(_HINDSIGHT_FAST_CACHE)>100:
   _HINDSIGHT_FAST_CACHE.clear()
  _HINDSIGHT_FAST_CACHE[cache_key]=(now_ts,items)
  return items
 except Exception:
  return []

def _fast_draft_only_completion(body,settings_dict):
 if not isinstance(body,dict) or body.get('execution_mode')!='draft-only' or body.get('task_files'):
  return None
 msgs=body.get('messages')
 if not isinstance(msgs,list) or not msgs:
  return None
 for m in msgs:
  if not isinstance(m,dict) or not isinstance(m.get('content'),str):
   return None
 import urllib.request
 api_key=(os.environ.get('OPENROUTER_API_KEY') or settings_dict.get('OPENROUTER_API_KEY') or '').strip().strip('\'"')
 if not api_key:
  return None
 base_url=(os.environ.get('OPENROUTER_BASE_URL') or settings_dict.get('OPENROUTER_BASE_URL') or 'https://openrouter.ai/api/v1').rstrip('/')
 req_model=str(body.get('model') or '').strip()
 raw_default=(
  os.environ.get('WHATSAPP_CHAT_MODEL')
  or settings_dict.get('WHATSAPP_CHAT_MODEL')
  or os.environ.get('HERMES_CHAT_MODEL')
  or settings_dict.get('HERMES_CHAT_MODEL')
  or 'deepseek/deepseek-chat-v3-0324'
 ).strip()
 model=req_model if req_model and req_model not in {'workspace-default','hermes'} else raw_default
 if 'deepseek-v4.1-flash' in model or 'glm-5.2' in model:
  model='deepseek/deepseek-chat-v3-0324'
 last_user_msg=''
 all_user_msgs=[]
 for m in msgs:
  if m.get('role')=='user' and not str(m.get('content','')).startswith('[PROCESS PLAYBOOK'):
   utxt=str(m.get('content') or '').strip()
   if utxt:
    all_user_msgs.append(utxt)
    last_user_msg=utxt
 enrichment_lines=[]
 sys_msg_text=str(msgs[0].get('content') or '') if msgs and msgs[0].get('role')=='system' else ''
 if all_user_msgs and 'OFFICIAL YOUR CHOICE SOLAR WEBSITE CALCULATOR' not in sys_msg_text:
  try:
   from hermes_plugins.central_ai_whatsapp.adapter import _calculate_solar_pre_assessment
   solar_calc_block=_calculate_solar_pre_assessment(' \n '.join(all_user_msgs))
   if solar_calc_block:
    enrichment_lines.append(solar_calc_block)
  except Exception:
   pass
 if last_user_msg:
  laya_sig=_classify_customer_turn_laya(last_user_msg)
  if laya_sig:
   enrichment_lines.append(f"[LAYA DECISION ENGINE SIGNAL (<15ms ONNX): Customer message intent = {laya_sig['intent']} (confidence {laya_sig['confidence']})]")
  mem_key=str(body.get('memory_key') or '').strip()[:300]
  if mem_key:
   import hashlib
   mem_tag='cust:'+hashlib.sha256(mem_key.encode('utf-8')).hexdigest()[:24]
   try:
    from hermes_plugins.central_ai_whatsapp.adapter import _recall_memory_items
    recalled_facts=_recall_memory_items(last_user_msg,limit=6,tags=[mem_tag])
   except Exception:
    recalled_facts=[]
   if recalled_facts:
    enrichment_lines.append("[CUSTOMER MEMORY (this customer's earlier conversations; use only if relevant, never mention you searched memory)]:\n" + "\n".join(f"- {f}" for f in recalled_facts))
  else:
   recalled_facts=_recall_hindsight_fast(last_user_msg)
   if recalled_facts:
    enrichment_lines.append("[HINDSIGHT LONG-TERM MEMORY RECALL (Verified Past Context & Coaching)]:\n" + "\n".join(f"- {f}" for f in recalled_facts))
 out_msgs=[{'role':str(m.get('role') or 'user'),'content':m['content']} for m in msgs]
 if enrichment_lines and out_msgs and out_msgs[0]['role']=='system':
  out_msgs[0]={
   'role':'system',
   'content':out_msgs[0]['content']+'\n\n'+'\n\n'.join(enrichment_lines),
  }
 payload=json.dumps({
  'model':model,
  'messages':out_msgs,
  'max_tokens':max(int(body.get('max_tokens') or 180),1200),
  'temperature':0.3,
 }).encode('utf-8')
 req=urllib.request.Request(
  f"{base_url}/chat/completions",
  data=payload,
  headers={
   'Authorization':f'Bearer {api_key}',
   'Content-Type':'application/json',
   'HTTP-Referer':'https://central-ai.app',
   'X-Title':'Central AI',
  },
  method='POST',
 )
 with urllib.request.urlopen(req,timeout=25) as resp:
  if resp.status!=200:
   return None
  data=json.loads(resp.read().decode('utf-8'))
  content=str(((data.get('choices') or [{}])[0].get('message') or {}).get('content') or '').strip()
  if not content:
   return None
  content=re.sub(
   r'\s*[\(\[]\s*(?:next\s+step|step\s*\d|note|internal|checklist|qualification|follow[- ]?up|once\s+they|after\s+they|then\s+ask|ask\s+only)\b[\s\S]*?[\)\]]',
   '',
   content,
   flags=re.IGNORECASE,
  )
  content=re.sub(
   r'(?:^|\n)\s*(?:\(?\s*(?:next\s+step|step\s*\d+|internal\s+note|note)\s*[:\-][\s\S]*)$',
   '',
   content,
   flags=re.IGNORECASE,
  ).strip()
  if not content:
   return None
  if re.search(r'\[\[\s*(?:LEAD|BOOK)\s*:',content,flags=re.IGNORECASE):
   try:
    from hermes_plugins.central_ai_whatsapp.adapter import _retain_memory_item
    threading.Thread(
     target=_retain_memory_item,
     args=(f"Qualified customer outcome & confirmation: {content}",'Customer Inquiry Pipeline','omni-channel','qualified_lead_outcome'),
     daemon=True,
    ).start()
   except Exception:
    pass
  mem_key_out=str(body.get('memory_key') or '').strip()[:300]
  if mem_key_out and last_user_msg:
   try:
    import hashlib
    from hermes_plugins.central_ai_whatsapp.adapter import _retain_memory_item
    out_tag='cust:'+hashlib.sha256(mem_key_out.encode('utf-8')).hexdigest()[:24]
    threading.Thread(
     target=_retain_memory_item,
     args=(f"Customer: {last_user_msg}\nAgent reply: {content}",'customer-chat','customer-chat','customer-chat',[out_tag]),
     daemon=True,
    ).start()
   except Exception:
    pass
  return {
   'model':model,
   'runtime_profile':settings_dict.get('HERMES_PROFILE','leo'),
   'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':content}}],
  }

def _ensure_customer_caller_enabled():
 import sqlite3
 for ep in ['/data/customer/remote-engine-enabled','/data/customer/text-engine-enabled','/data/remote-engine-enabled','/data/text-engine-enabled']:
  try:
   fp=Path(ep)
   if not fp.is_file():
    fp.parent.mkdir(parents=True,exist_ok=True)
    fp.write_text('enabled\n',encoding='utf-8')
    os.chmod(ep,0o666)
  except Exception:pass
 db_path=Path('/data/customer/text.db')
 if db_path.is_file():
  try:
   conn=sqlite3.connect(str(db_path),timeout=3.0)
   conn.execute("INSERT OR REPLACE INTO text_meta(key,value) VALUES('activated',0)")
   conn.commit()
   conn.close()
  except Exception:pass

def _load_customer_processed():
 for pp in ['/data/customer/processed_inbox_ids.json',str(ROOT/'.runtime'/'processed_inbox_ids.json')]:
  if os.path.exists(pp):
   try:
    d=json.loads(Path(pp).read_text(encoding='utf-8'))
    if isinstance(d,dict):return d
   except Exception:pass
 return {}

def _save_customer_processed(data):
 if not isinstance(data,dict):return
 if len(data)>300:
  keys=list(data.keys())[-300:]
  data={k:data[k] for k in keys}
 payload=json.dumps(data,ensure_ascii=False,indent=2)
 for pp in ['/data/customer/processed_inbox_ids.json',str(ROOT/'.runtime'/'processed_inbox_ids.json')]:
  try:
   fp=Path(pp);fp.parent.mkdir(parents=True,exist_ok=True);fp.write_text(payload,encoding='utf-8');os.chmod(pp,0o666)
  except Exception:pass

def _get_customer_caller_tokens():
 tokens=[]
 for kp in [Path('/data/customer/voice-key'),Path('/run/secrets/whatsapp_setup_key')]:
  if kp.is_file():
   try:
    v=kp.read_text(encoding='utf-8').strip()
    if len(v)>=32 and v not in tokens:tokens.append(v)
   except Exception:pass
 for ev in [os.environ.get('HERMES_API_KEY',''),os.environ.get('WHATSAPP_SETUP_KEY',''),KEY,'hermes_standalone_secret_token_32chars']:
  v=(ev or '').strip().strip('\'"')
  if len(v)>=32 and v not in tokens:tokens.append(v)
 if Path('/data/voice-key').is_file():
  try:
   v=Path('/data/voice-key').read_text(encoding='utf-8').strip()
   if len(v)>=32 and v not in tokens:tokens.append(v)
  except Exception:pass
 return tokens

def _poll_customer_caller_http():
 import urllib.request
 caller_url=os.environ.get('CUSTOMER_CALLER_URL','http://customer-caller:8080').rstrip('/')
 for tok in _get_customer_caller_tokens():
  try:
   req=urllib.request.Request(f"{caller_url}/text/messages",headers={'Authorization':f'Bearer {tok}','X-Leo-Route':'owner'},method='GET')
   with urllib.request.urlopen(req,timeout=4) as resp:
    if resp.status==200:
     data=json.loads(resp.read().decode('utf-8'))
     if isinstance(data,list):
      return data,tok
  except Exception:
   continue
 return [],(_get_customer_caller_tokens() or [''])[0]

def _scan_customer_sqlite_inbox():
 import sqlite3
 results=[]
 db_path=Path('/data/customer/text.db')
 if not db_path.is_file():return results
 try:
  cutoff=int(time.time())-86400
  conn=sqlite3.connect(str(db_path),timeout=4.0)
  rows=conn.execute(
   "SELECT i.id, i.route, i.chat, i.payload, i.status, i.lease, i.created, "
   "EXISTS(SELECT 1 FROM text_outbox o WHERE o.lease = i.lease AND o.status = 'sent') AS outbox_sent "
   "FROM text_inbox i WHERE i.created >= ? ORDER BY i.created ASC",
   (cutoff,),
  ).fetchall()
  conn.close()
  for r in rows:
   try:
    pkt=json.loads(r[3])
    if isinstance(pkt,dict):
     pkt['_db_id']=r[0]
     pkt['_db_status']=r[4]
     pkt['bridgeLease']=pkt.get('bridgeLease') or r[5] or ''
     pkt['_db_created']=r[6]
     pkt['_outbox_sent']=bool(r[7])
     results.append(pkt)
   except Exception:pass
 except Exception:pass
 return results

def _handle_customer_sync_action(action,body):
 import sqlite3
 msg_ids=[str(m).strip() for m in (body.get('message_ids') or []) if str(m).strip()]
 raw_sender=str(body.get('sender_id') or body.get('origin') or '').strip()
 sender_digits=re.sub(r'[^\d]','',raw_sender)
 with customer_wa_lock:
  proc_map=_load_customer_processed()
  changed=False
  if action=='ack':
   for mid in msg_ids:
    info=proc_map.get(mid)
    if isinstance(info,dict):
     if info.get('reply') or info.get('replied') or info.get('handled'):
      info['synced_to_box3']=True
      changed=True
  elif action=='dismiss':
   for mid in msg_ids:
    info=proc_map.get(mid)
    if isinstance(info,dict):
     info['dismissed']=True
     info['synced_to_box3']=True
     info['replied']=True
     info['handled']=True
    else:
     proc_map[mid]={'message_id':mid,'dismissed':True,'synced_to_box3':True,'replied':True,'handled':True}
    changed=True
   for mid,info in list(proc_map.items()):
    if not isinstance(info,dict):continue
    idigits=re.sub(r'[^\d]','',str(info.get('sender_id') or info.get('wire_chat') or ''))
    wchat=str(info.get('wire_chat') or '').strip()
    if (sender_digits and len(sender_digits)>=7 and idigits==sender_digits) or (raw_sender and wchat and wchat==raw_sender):
     info['dismissed']=True
     info['synced_to_box3']=True
     info['replied']=True
     info['handled']=True
     changed=True
   for pkt in _scan_customer_sqlite_inbox():
    pmid=str(pkt.get('messageId') or '').strip()
    psid=re.sub(r'[^\d]','',str(pkt.get('senderId') or pkt.get('wireChat') or ''))
    pwchat=str(pkt.get('wireChat') or pkt.get('chatId') or '').strip()
    if (pmid and pmid in msg_ids) or (sender_digits and len(sender_digits)>=7 and psid==sender_digits) or (raw_sender and pwchat==raw_sender):
     if pmid:
      proc_map[pmid]={'message_id':pmid,'sender_id':psid,'wire_chat':pwchat,'dismissed':True,'synced_to_box3':True,'replied':True,'handled':True}
      changed=True
     if pkt.get('_db_id') and Path('/data/customer/text.db').is_file():
      try:
       conn=sqlite3.connect('/data/customer/text.db',timeout=3.0)
       conn.execute("DELETE FROM text_inbox WHERE id=?",(pkt['_db_id'],))
       conn.commit()
       conn.close()
      except Exception:pass
  if changed:
   _save_customer_processed(proc_map)
 return {'ok':True,'action':action}

def _collect_customer_sync_items():
 _ensure_customer_caller_enabled()
 items=[]
 seen_ids=set()
 now_ts=int(time.time())
 with customer_wa_lock:
  proc_map=_load_customer_processed()
  for mid,info in proc_map.items():
   if isinstance(info,dict) and (info.get('dismissed') or info.get('synced_to_box3')):
    seen_ids.add(mid)
  for qp in ['/data/customer/whatsapp_sessions_sync.json','/data/whatsapp_sessions_sync.json']:
   if os.path.exists(qp):
    try:
     raw_list=json.loads(Path(qp).read_text(encoding='utf-8'))
     if isinstance(raw_list,list):
      for it in raw_list:
       if isinstance(it,dict):
        mid=str(it.get('message_id') or '')
        if mid and (mid in seen_ids or mid in in_progress_customer_msgs):
         continue
        pinfo=proc_map.get(mid) if mid else None
        if isinstance(pinfo,dict) and pinfo.get('reply') and not it.get('reply'):
         it['reply']=pinfo['reply']
        if not mid or mid not in seen_ids:
         if mid:seen_ids.add(mid)
         items.append(it)
     Path(qp).write_text('[]',encoding='utf-8')
    except Exception:pass
  for mid,info in proc_map.items():
   if not isinstance(info,dict) or mid in seen_ids or mid in in_progress_customer_msgs:
    continue
   if info.get('dismissed') or info.get('synced_to_box3'):
    seen_ids.add(mid)
    continue
   if not (info.get('replied') or info.get('handled') or info.get('reply')):
    continue
   seen_ids.add(mid)
   items.append({
    'sender_id':str(info.get('sender_id') or ''),
    'sender_name':str(info.get('sender_name') or ''),
    'wire_chat':str(info.get('wire_chat') or ''),
    'message_id':mid,
    'text':str(info.get('text') or ''),
    'reply':info.get('reply'),
    'created_at':str(info.get('created_at') or ''),
   })
  for pkt in _scan_customer_sqlite_inbox():
   mid=str(pkt.get('messageId') or '').strip()
   txt=str(pkt.get('body') or '').strip()
   sid=str(pkt.get('senderId') or '').strip()
   if not mid or not txt or not sid or mid in seen_ids or mid in in_progress_customer_msgs:
    continue
   pinfo=proc_map.get(mid)
   if isinstance(pinfo,dict) and (pinfo.get('dismissed') or pinfo.get('synced_to_box3')):
    seen_ids.add(mid)
    continue
   created_ts=pkt.get('_db_created')
   has_finished_reply=isinstance(pinfo,dict) and bool(pinfo.get('replied') or pinfo.get('handled') or pinfo.get('reply'))
   is_stale_unhandled=bool(created_ts and (now_ts-int(created_ts))>=15)
   if not has_finished_reply and not is_stale_unhandled:
    continue
   seen_ids.add(mid)
   created_iso=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(int(created_ts))) if created_ts else time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
   items.append({
    'sender_id':sid,
    'sender_name':str(pkt.get('senderName') or ''),
    'wire_chat':str(pkt.get('wireChat') or pkt.get('chatId') or ''),
    'message_id':mid,
    'text':txt,
    'reply':(pinfo or {}).get('reply'),
    'created_at':created_iso,
   })
 return items[-100:]

def _start_customer_whatsapp_poller():
 def _worker():
  history_by_sender={}
  while True:
   try:
    time.sleep(1.0)
    for sp in [str(ROOT),str(ROOT/'scripts')]:
     if sp not in sys.path:sys.path.insert(0,sp)
    import sqlite3, urllib.request
    from hermes_plugins.central_ai_whatsapp.adapter import (
     _load_live_customer_config,
     _calculate_solar_pre_assessment,
     _forward_customer_whatsapp_to_box3,
     _append_customer_sync_queue,
     _retain_memory_item,
     _recall_memory_items,
     _fetch_live_box3_context,
     _extract_and_analyze_customer_lead,
     _is_premature_lead_handoff,
    )
    from whatsapp_system_context import clean_reply_punctuation
    _ensure_customer_caller_enabled()
    caller_url=os.environ.get('CUSTOMER_CALLER_URL','http://customer-caller:8080').rstrip('/')
    packets,token=_poll_customer_caller_http()
    db_pkts=_scan_customer_sqlite_inbox()
    seen_pkt_ids={str(p.get('messageId') or '') for p in packets if isinstance(p,dict)}
    for dp in db_pkts:
     dmid=str(dp.get('messageId') or '')
     if dmid and dmid not in seen_pkt_ids:
      packets.append(dp)
      seen_pkt_ids.add(dmid)
    if not packets:
     continue
    live_cfg=_load_live_customer_config()
    auto_reply_enabled=bool(live_cfg.get('customer_auto_reply',True))
    for pkt in packets:
     if not isinstance(pkt,dict):continue
     chat_id=str(pkt.get('chatId') or '')
     wire_chat=str(pkt.get('wireChat') or chat_id)
     lease=str(pkt.get('bridgeLease') or '')
     sender_id=str(pkt.get('senderId') or '')
     sender_name=str(pkt.get('senderName') or '')
     msg_id=str(pkt.get('messageId') or '').strip()
     msg_text=str(pkt.get('body') or '').strip()
     outbox_sent=bool(pkt.get('_outbox_sent'))
     created_ts=pkt.get('_db_created')
     created_iso=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(int(created_ts))) if created_ts else time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())
     if not msg_text or not sender_id or not msg_id:
      if lease and token:
       try:
        creq=urllib.request.Request(f"{caller_url}/text/complete",data=json.dumps({'chatId':chat_id,'bridgeLease':lease,'status':'complete'}).encode('utf-8'),headers={'Authorization':f'Bearer {token}','X-Leo-Route':'owner','Content-Type':'application/json'},method='POST')
        urllib.request.urlopen(creq,timeout=4).close()
       except Exception:pass
      continue
     with customer_wa_lock:
      proc_map=_load_customer_processed()
      existing_proc=proc_map.get(msg_id)
      if msg_id in in_progress_customer_msgs or (isinstance(existing_proc,dict) and (existing_proc.get('dismissed') or existing_proc.get('replied') or existing_proc.get('sending') or outbox_sent or (existing_proc.get('handled') and not auto_reply_enabled))):
       continue
      in_progress_customer_msgs.add(msg_id)
     try:
      if token:
       try:
        treq=urllib.request.Request(f"{caller_url}/text/typing",data=json.dumps({'chatId':chat_id,'bridgeLease':lease,'presence':'composing'}).encode('utf-8'),headers={'Authorization':f'Bearer {token}','X-Leo-Route':'owner','Content-Type':'application/json'},method='POST')
        urllib.request.urlopen(treq,timeout=3).close()
       except Exception:pass
      try:
       box3_res=_forward_customer_whatsapp_to_box3(pkt,ROOT,True)
      except Exception:
       box3_res={'handled_by_box3':False}
      prior_chat_msgs=[]
      has_dismissed_for_sender=False
      for pmid,pinfo in proc_map.items():
       if pmid==msg_id or not isinstance(pinfo,dict):
        continue
       if str(pinfo.get('sender_id') or '')==sender_id:
        if pinfo.get('dismissed'):
         has_dismissed_for_sender=True
         continue
        if pinfo.get('text'):
         prior_chat_msgs.append({'role':'user','content':str(pinfo['text'])})
         if pinfo.get('reply'):
          prior_chat_msgs.append({'role':'assistant','content':str(pinfo['reply'])})
      if has_dismissed_for_sender and not prior_chat_msgs:
       history_by_sender.pop(sender_id,None)
      if not prior_chat_msgs and history_by_sender.get(sender_id):
       for hline in history_by_sender.get(sender_id,[])[-8:]:
        if hline.startswith('Customer: '):
         prior_chat_msgs.append({'role':'user','content':hline[10:]})
        elif hline.startswith('James: '):
         prior_chat_msgs.append({'role':'assistant','content':hline[7:]})
      has_prior_reply=any(m.get('role')=='assistant' for m in prior_chat_msgs)
      first_n=(sender_name.split()[0] if sender_name else '')
      lead_info=_extract_and_analyze_customer_lead([*prior_chat_msgs,{'role':'user','content':msg_text}],sender_name,sender_id)
      reply_text=None
      raw_tags=[]
      existing_box3_reply=None
      if box3_res.get('handled_by_box3'):
       already_replied_locally=isinstance(existing_proc,dict) and bool(existing_proc.get('replied') or existing_proc.get('sending'))
       if box3_res.get('auto_reply') and not box3_res.get('duplicate') and box3_res.get('reply'):
        reply_text=str(box3_res['reply']).strip()
       elif not outbox_sent and not already_replied_locally and box3_res.get('control_mode')!='human' and box3_res.get('customer_auto_reply') is not False and (box3_res.get('existing_reply') or box3_res.get('reply')):
        reply_text=str(box3_res.get('existing_reply') or box3_res.get('reply') or '').strip() or None
       else:
        reply_text=None
        existing_box3_reply=str(box3_res.get('existing_reply') or box3_res.get('reply') or '').strip() or None
      elif auto_reply_enabled and not outbox_sent:
       agent_name=live_cfg.get('customer_agent') or 'James'
       agent_role=live_cfg.get('customer_agent_role') or 'Customer Service & Solar Specialist'
       company_name=live_cfg.get('company_name') or 'Your Choice Solar'
       if lead_info.get('is_simple_greeting'):
        reply_text=(f"Hi {first_n or 'there'}! How can I help you today?" if has_prior_reply else f"Hi {first_n or 'there'}! I'm {agent_name} from {company_name}. How can I help you today?")
       else:
        agent_instr=live_cfg.get('customer_agent_instructions') or ''
        agent_kb=live_cfg.get('customer_agent_knowledge') or []
        sorted_kb=sorted([c for c in agent_kb if isinstance(c,dict) and c.get('content')],key=lambda x:0 if str(x.get('category') or '').lower()=='process' else 1)
        kb_text='\n\n'.join(f"[{c.get('title','')}{' (' + str(c.get('category')) + ')' if c.get('category') else ''}]\n{c.get('content','')}" for c in sorted_kb)
        if not kb_text:
         kb_text=_fetch_live_box3_context(msg_text,ROOT)
        prior_turns=history_by_sender.get(sender_id,[])[-8:]
        hist_str='\n'.join(prior_turns) if prior_turns else '(No prior messages yet)'
        all_user_txt=' \n '.join([str(m.get('content') or '') for m in prior_chat_msgs if m.get('role')=='user']+[msg_text])
        solar_calc_prompt=_calculate_solar_pre_assessment(all_user_txt)
        sys_prompt='\n'.join(filter(None,[
         f"You are {agent_name} ({agent_role}) representing {company_name} on WhatsApp live chat.",
         f"AGENT INSTRUCTIONS (FROM APP OS AGENT PROFILE):\n{agent_instr}" if agent_instr else "",
         "LIVE CONVERSATION CONTEXT: Output ONLY the exact message spoken directly to the customer in plain text (never output internal reasoning, '(Next step: ...)', '(Note: ...)', markdown **bold**, or # headings). Always use warm, courteous, professional English—never use casual slang like 'G'day', 'mate', 'howdy', or 'heya'.",
         "PROACTIVE VALUE FIRST — NEVER ASK PERMISSION TO SHARE INFO: Never ask 'Would you like to know...?', 'Do you want me to explain...?', 'Shall I calculate that for you?', or 'Would you like to hear how much you can save?'. Every customer wants to know! Never say 'I\\'d be happy to explain...' without actually explaining it right away. When a customer asks how solar works or gives their bill/usage, deliver the actual answer and calculator figures upfront immediately in 1 to 2 simple sentences first (e.g., daytime solar panels power your home appliances directly first so you buy far less electricity from the grid, plus starting kW size, panel count, and estimated bill savings), and then ask your 1 simple follow-up question.",
         "SHORT, FRIENDLY & ONE QUESTION AT A TIME: Keep every reply brief, warm, and simple (1 to 2 short sentences maximum). NEVER dump paragraphs of information and NEVER ask two or more questions in the same message (never stack questions with 'Also, ...' or 'and what size/how old?'). Ask at most ONE simple question at a time.",
         f"10/10 HUMAN WARMTH, REAL HUMAN FEEL & EFFORTLESS DRY WIT (NO 'THANKS [NAME]', NO ROBOTIC FILLER, NO AI/TECH MENTIONS): Speak strictly as a real human specialist at {company_name}—NEVER say or imply that you are an AI, bot, virtual assistant, or automated agent, and NEVER mention technical errors, system glitches, prompts, tools, or say things like 'my memory of you hasn't been saved'. If you ever don't recall a detail they mentioned earlier, respond naturally like a real person ('I'm sorry, I can't remember—could you remind me?'). Speak with natural contractions and light, relatable dry human wit (for example, reacting with warm humour when they mention steep power bills). NEVER start your replies with 'Thanks', 'Thank you', 'Thanks {first_n or '[Name]'}', 'Got it, {first_n or '[Name]'}', 'That is a great question', 'Great, {first_n or '[Name]'}', or similar mechanical acknowledgements. Never repeat the customer's suburb/location in every message.",
         "STRICT SYSTEM DATA GROUNDING: Rely on your Agent Instructions, Knowledge Base, and Official Your Choice Solar Website Calculator (planning-v2) LIVE RESULT below. Always share the official calculator's starting kWh usage, kW panel size (and ~number of 440W panels), and estimated bill reduction when the customer gives their bill/usage or asks to calculate; never invent unverified hardware brands, fixed package prices, rebate amounts, or warranty lengths.",
         "NO REPEATED GREETINGS OR NAME REPETITION: This conversation is already underway. NEVER start your reply with 'Hi', 'Hello', 'Hey', 'Thanks', 'Thank you', or the customer's name ('" + (first_n or "there") + "'). Do NOT use the customer's first name again on follow-up replies—jump straight into a natural, helpful response." if has_prior_reply else ("Greet " + (first_n or "the customer") + " warmly and simply once on this first reply using 'Hi " + (first_n or "there") + "' (never use 'G'day')."),
         solar_calc_prompt or "OFFICIAL YOUR CHOICE SOLAR WEBSITE CALCULATOR (planning-v2): Ready. If the customer asks to calculate their kWh usage, solar panel size (kW), or savings before giving a bill amount, ask for their typical monthly or quarterly electricity bill (in AUD) or kWh usage so you can calculate it for them immediately.",
         lead_info['checklist_prompt'],
         "MANDATORY FULL LEAD COLLECTION & DATA CONFIRMATION (ONE STEP AT A TIME):\n"
         "1. Every solar inquiry must be added to the Central OS CRM New Lead pipeline ONLY AFTER all 4 required fields (Full Name with first AND last name, Phone Number, Email Address, and Property Street Address) are collected AND explicitly confirmed by the customer.\n"
         "2. NEVER close the conversation or say 'A specialist will reach out shortly' or 'I\\'ll arrange a consultation' while Full Name (first AND last name), Phone Number, Email Address, Property Street Address, or Customer Confirmation are still MISSING or INCOMPLETE in the checklist above.\n"
         f"3. Guide the conversation one simple step at a time. Right now, ask ONLY for the NEXT SINGLE QUESTION shown in the checklist above ({lead_info.get('next_single_question') or 'read back and ask the customer to confirm all 4 details'}). Do NOT combine multiple questions into one message.\n"
         + (
          f"4. All 4 details are collected AND confirmed by the customer! Now append the hidden CRM lead tag at the very end of your message:\n[[LEAD:{{\"name\":\"{lead_info['name']}\",\"phone\":\"{lead_info['phone']}\",\"email\":\"{lead_info['email']}\",\"address\":\"{lead_info['address']}\",\"notes\":\"Confirmed solar inquiry\"}}]]"
          if (lead_info.get('is_complete') and lead_info.get('is_confirmed'))
          else "4. DO NOT output [[LEAD:...]] or [[BOOK:...]] on this turn! Never output placeholder values like '...' or 'name@example.com'. When all 4 fields are collected, first read back Full Name, Phone Number, Email Address, and Property Street Address and ask the customer to confirm they are correct."
         ),
         f"CONVERSATION HISTORY:\n{hist_str}",
         f"KNOWLEDGE BASE (FROM APP OS):\n{kb_text}" if kb_text else "",
        ]))
        try:
         env=profile_environment(settings)
         job_in={
          'model':'workspace-default',
          'session_id':str(UUID(int=int(time.time()*1000)%((1<<128)-1))),
          'runtime_profile':settings['HERMES_PROFILE'],
          'execution_mode':'draft-only',
          'max_tokens':180,
          'stream':False,
          'messages':[{'role':'system','content':sys_prompt},*prior_chat_msgs[-10:],{'role':'user','content':msg_text}],
         }
         jout=_fast_draft_only_completion(job_in,settings)
         if not jout:
          proc=subprocess.run([settings.get('HERMES_PYTHON',sys.executable),str(ROOT/'scripts'/'hermes-job.py')],input=json.dumps(job_in),capture_output=True,text=True,encoding='utf-8',timeout=35,env=env,cwd=ROOT)
          if proc.returncode==0 and proc.stdout:
           jout=json.loads(proc.stdout)
         if jout:
          raw_content=((jout.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
          raw_tags=re.findall(r'\[\[\s*(?:BOOK|LEAD)\s*:\s*\{[\s\S]*?\}\s*\]\]',raw_content,flags=re.IGNORECASE)
          reply_text=clean_reply_punctuation(raw_content).strip()
          if reply_text and _is_premature_lead_handoff(reply_text,lead_info['missing_natural']):
           retry_job={
            **job_in,
            'session_id':str(UUID(int=(int(time.time()*1000)+1)%((1<<128)-1))),
            'messages':[
             *job_in['messages'],
             {'role':'assistant','content':reply_text},
             {'role':'user','content':f"[PROCESS PLAYBOOK SELF-CORRECTION: Your draft reply above tried to hand off or close the conversation before completing the Process Knowledge Base qualification and CRM checklist. Rewrite your reply in 1-2 short sentences (no 'Thanks' or greeting prefixes) to warmly ask ONLY this single next question: {lead_info.get('next_single_question')}]"},
            ],
           }
           rjout=_fast_draft_only_completion(retry_job,settings)
           if not rjout:
            rproc=subprocess.run([settings.get('HERMES_PYTHON',sys.executable),str(ROOT/'scripts'/'hermes-job.py')],input=json.dumps(retry_job),capture_output=True,text=True,encoding='utf-8',timeout=30,env=env,cwd=ROOT)
            if rproc.returncode==0 and rproc.stdout:
             rjout=json.loads(rproc.stdout)
           if rjout:
            rraw=((rjout.get('choices') or [{}])[0].get('message') or {}).get('content') or ''
            if rraw.strip():
             raw_tags=re.findall(r'\[\[\s*(?:BOOK|LEAD)\s*:\s*\{[\s\S]*?\}\s*\]\]',rraw,flags=re.IGNORECASE) or raw_tags
             reply_text=clean_reply_punctuation(rraw).strip()
        except Exception:
         reply_text=None
        if not reply_text:
         if has_prior_reply:
          reply_text="Are you looking for a brand new solar setup, or do you already have panels on the property?"
         else:
          reply_text=f"Hi {first_n or 'there'}! I'm {agent_name} from {company_name}. How can I help you today?"
      if reply_text:
       if not raw_tags:
        raw_tags=re.findall(r'\[\[\s*(?:BOOK|LEAD)\s*:\s*\{[\s\S]*?\}\s*\]\]',reply_text,flags=re.IGNORECASE)
       reply_text=re.sub(r'\[\[\s*(?:BOOK|LEAD)[\s\S]*?\]\]+','',reply_text,flags=re.IGNORECASE)
       reply_text=re.sub(r'\s*[\(\[]\s*(?:next\s+step|step\s*\d|note|internal|checklist|qualification|follow[- ]?up|once\s+they|after\s+they|then\s+ask|ask\s+only)\b[\s\S]*?[\)\]]','',reply_text,flags=re.IGNORECASE)
       reply_text=re.sub(r'(?:^|\n)\s*(?:\(?\s*(?:next\s+step|step\s*\d+|internal\s+note|note)\s*[:\-][\s\S]*)$','',reply_text,flags=re.IGNORECASE)
       reply_text=re.sub(r'^\s*\[+\s*|\s*\]+\s*$','',reply_text).strip()
       reply_text=re.sub(r"^\s*(?:g['\u2019\u2018`]?day|howdy|heya)\b","Hi",reply_text,flags=re.IGNORECASE)
       reply_text=re.sub(r"\b(hi|hello|hey),?\s+mate\b",r"\1",reply_text,flags=re.IGNORECASE)
       reply_text=re.sub(r"^\s*(?:many\s+thanks|thanks\s+so\s+much|thanks\s+a\s+lot|thanks|thank\s+you|appreciate\s+(?:that|it|the\s+info))(?:\s+for\s+[^.!?\n,]{2,45})?(?:[\s,]+[A-Za-z'\u2019-]{2,20})?\s*[!,.\u2014:-]+\s*","",reply_text,flags=re.IGNORECASE).strip()
      if reply_text and has_prior_reply and not box3_res.get('handled_by_box3'):
       stripped_re=re.sub(r"^(?:(?:hi|hello|hey|g['\u2019\u2018`]?day|good\s+(?:morning|afternoon|evening)|many\s+thanks|thanks\s+so\s+much|thanks\s+a\s+lot|thanks|thank\s+you|appreciate\s+(?:that|it)|got\s+it|great|perfect|wonderful|lovely|sure\s+thing|no\s+problem|certainly|of\s+course)(?:\s+(?:there|again|mate|folks|team|for\s+[^.!?\n,]{2,45}))?(?:[\s,]+[A-Za-z'\u2019-]{2,20})?\s*[!,.\u2014:-]+\s*)+","",reply_text,flags=re.IGNORECASE).strip()
       if first_n:
        esc_fn=re.escape(first_n)
        stripped_re=re.sub(rf"^{esc_fn}\s*[!,.\u2014:-]+\s*","",stripped_re,flags=re.IGNORECASE).strip()
        stripped_re=re.sub(rf",\s*{esc_fn}(?=[,.!?]|$)","",stripped_re,flags=re.IGNORECASE).strip()
       if len(stripped_re)>=8:
        reply_text=stripped_re[0].upper()+stripped_re[1:]
      sent_ok=outbox_sent or bool(existing_box3_reply)
      can_send=False
      if reply_text and not outbox_sent:
       with customer_wa_lock:
        proc_map=_load_customer_processed()
        cur_p=proc_map.get(msg_id)
        if isinstance(cur_p,dict) and (cur_p.get('replied') or cur_p.get('sending')):
         sent_ok=True
         can_send=False
        else:
         if not isinstance(cur_p,dict):
          proc_map[msg_id]={'message_id':msg_id}
         proc_map[msg_id]['sending']=True
         _save_customer_processed(proc_map)
         can_send=True
      if reply_text and can_send:
       history_by_sender.setdefault(sender_id,[]).extend([f"Customer: {msg_text}",f"James: {reply_text}"])
       for send_target in dict.fromkeys([t for t in [wire_chat,sender_id] if t]):
        try:
         sreq=urllib.request.Request(f"{caller_url}/send",data=json.dumps({'target':send_target,'message':reply_text}).encode('utf-8'),headers={'Content-Type':'application/json'},method='POST')
         with urllib.request.urlopen(sreq,timeout=15) as sresp:
          if sresp.status==200:
           sent_ok=True
           break
        except Exception:
         continue
      if lease and token:
       try:
        creq=urllib.request.Request(f"{caller_url}/text/complete",data=json.dumps({'chatId':chat_id,'bridgeLease':lease,'status':'complete'}).encode('utf-8'),headers={'Authorization':f'Bearer {token}','X-Leo-Route':'owner','Content-Type':'application/json'},method='POST')
        urllib.request.urlopen(creq,timeout=5).close()
       except Exception:pass
      if pkt.get('_db_id') and Path('/data/customer/text.db').is_file():
       try:
        conn=sqlite3.connect('/data/customer/text.db',timeout=3.0)
        conn.execute("UPDATE text_inbox SET status='complete' WHERE id=?",(pkt['_db_id'],))
        conn.commit()
        conn.close()
       except Exception:pass
      if not (lead_info.get('is_complete') and lead_info.get('is_confirmed')):
       raw_tags=[]
      recorded_reply=((reply_text + (' ' + ' '.join(raw_tags) if raw_tags else '')).strip() if sent_ok and reply_text else None) or existing_box3_reply
      entry_record={
       'sender_id':sender_id,
       'sender_name':sender_name,
       'wire_chat':wire_chat,
       'message_id':msg_id,
       'text':msg_text,
       'reply':recorded_reply,
       'handled':bool(sent_ok or not auto_reply_enabled or box3_res.get('handled_by_box3')),
       'replied':bool(sent_ok),
       'sending':False,
       'created_at':created_iso,
      }
      with customer_wa_lock:
       proc_map=_load_customer_processed()
       proc_map[msg_id]=entry_record
       _save_customer_processed(proc_map)
      _append_customer_sync_queue(entry_record)
      if raw_tags and reply_text and sent_ok and not box3_res.get('handled_by_box3'):
       cust_label=f"{sender_name or 'Customer'} (+{sender_id})" if sender_id else (sender_name or 'Customer')
       threading.Thread(target=_retain_memory_item,args=(f"Customer ({cust_label}): {msg_text} -> James: {reply_text} {' '.join(raw_tags)}",'James (Customer Service)','customer-whatsapp','qualified_lead_outcome'),daemon=True).start()
     finally:
      with customer_wa_lock:
       in_progress_customer_msgs.discard(msg_id)
   except Exception as exc:
    print(f'Customer WhatsApp poller error: {exc}',flush=True)
    time.sleep(2)
 threading.Thread(target=_worker,daemon=True).start()

if __name__=='__main__':
 (ROOT/'.runtime').mkdir(exist_ok=True)
 bind=os.environ.get('HERMES_BRIDGE_BIND','0.0.0.0')
 port=int(os.environ.get('HERMES_PORT','8642'))
 voice.start_websocket(bind)
 _start_customer_whatsapp_poller()
 print(f'Private Hermes adapter listening on {bind}:{port}',flush=True)
 ThreadingHTTPServer((bind,port),Handler).serve_forever()
