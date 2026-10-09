"""Use Hermes message handling on the voice engine's single paired device."""
import asyncio
import base64
import contextvars
import hashlib
import json
import logging
import mimetypes
from pathlib import Path
import shutil

binding=contextvars.ContextVar('leo_text_binding',default=None)

def _retain_memory_item(content, sender, route):
    try:
        if not content or not isinstance(content, str) or not content.strip():
            return
        key_file = Path('/run/secrets/hindsight_api_key')
        token = key_file.read_text().strip() if key_file.is_file() else ''
        if not token:
            cfg_file = Path('/opt/data/profiles/leo/hindsight/config.json')
            if cfg_file.is_file():
                token = json.loads(cfg_file.read_text()).get('api_key', '')
        if not token:
            return
        import urllib.request
        payload = json.dumps({
            'items': [{
                'content': f"WhatsApp {sender} ({route}): {content.strip()}",
                'context': 'whatsapp'
            }]
        }).encode('utf-8')
        req = urllib.request.Request(
            'http://hindsight:8888/v1/default/banks/michael-os-leo/memories',
            data=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass


_NONCONVERSATIONAL_PATTERNS = (
    'self-improvement review',
    'user profile updated',
    'memory updated',
    'memory saved',
    'profile updated',
    'skill \'',
    'skill "',
    '[background process',
    'hermes update',
    'gateway restarted',
    '/sethome',
    'home channel is set',
    'no home channel',
    'build a short profile',
    'working —',
    'working -',
)

def _is_non_conversational(content, metadata=None):
    if metadata and isinstance(metadata, dict):
        if any(metadata.get(k) for k in ('non_conversational', 'non_conversational_history', 'is_progress')):
            return True
        if metadata.get('kind') in {'tool_progress', 'thinking', 'status', 'card', 'review', 'diagnostic', 'system'}:
            return True
    if not content or not isinstance(content, str):
        return False
    text = content.strip()
    if '💾' in text:
        return True
    lower = text.lower()
    return any(p in lower for p in _NONCONVERSATIONAL_PATTERNS)


def build_engine_adapter(route,member,home,group):
    import aiohttp
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter
    from gateway.platforms.base import SendResult,get_image_cache_dir,get_audio_cache_dir,get_document_cache_dir
    from whatsapp_system_context import get_whatsapp_reply_style,attach_assistant_skills,clean_reply_punctuation
    owner=member.get('number','')
    class EngineAdapter(WhatsAppAdapter):
        def _bridge_url(self,path):return 'http://caller:8080/text/'+path

        async def connect(self,*,is_reconnect=False):
            self._http_session=aiohttp.ClientSession(headers={
                'Authorization':'Bearer '+Path('/data/voice-key').read_text().strip(),'X-Leo-Route':route})
            self._mark_connected()
            self._poll_task=asyncio.create_task(self._poll_messages())
            return True

        async def disconnect(self):
            self._mark_disconnected()
            if self._poll_task:
                self._poll_task.cancel()
                await asyncio.gather(self._poll_task,return_exceptions=True)
            if self._http_session:await self._http_session.close()
            self._poll_task=self._http_session=None

        async def _bridge_unavailable(self):
            return None if self._running and self._http_session else 'Not connected'

        async def _build_message_event(self,data):
            if bool(data.get('isGroup'))!=member['group']:return None
            if member['group'] and group and data.get('chatId')!=group:return None
            if not member.get('allow_all', True) and not member['group'] and owner and data.get('senderId')!=owner:return None
            event=await super()._build_message_event(data)
            if event is not None:
                event.channel_prompt='\n\n'.join(filter(None,[event.channel_prompt,get_whatsapp_reply_style(),
                    'Use this native profile’s configured memory tools for saved facts; do not invent memories. '
                    'When the user instructs characteristics, voice emotions, speaking tone, or personal preferences, immediately record and save them into memory so they persist across WhatsApp and App OS. '
                    'If an attachment is marked unavailable, ask for its contents or a smaller copy; never claim to have read it.']))
                if route in {'mark', 'owner'}:attach_assistant_skills(event,home)
            return event

        async def _collect_bridge_media(self,data,msg_type):
            urls=[]
            allowed=(Path('/data/text/media')/route).resolve()
            kind=data.get('mediaType')
            target=Path(get_image_cache_dir() if kind=='image' else get_audio_cache_dir() if kind=='audio' else get_document_cache_dir())
            target.mkdir(parents=True,exist_ok=True)
            for name in data.get('mediaUrls',[]):
                path=Path(name)
                if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(allowed):continue
                copied=target/path.name
                await asyncio.to_thread(shutil.copyfile,path,copied)
                copied.chmod(0o600);urls.append(str(copied))
            return urls,[data.get('mimetype') or 'application/octet-stream']*len(urls)

        async def _poll_messages(self):
            while self._running:
                try:
                    async with self._bridge_req('get','messages',10) as response:
                        packets=await response.json() if response.status==200 else []
                    for data in packets:
                        token=binding.set(data)
                        try:
                            event=await self._build_message_event(data)
                            if event:
                                if getattr(event, 'text', None):
                                    asyncio.create_task(asyncio.to_thread(_retain_memory_item, event.text, 'User', route))
                                await self.handle_message(event)
                            else:await self._complete(data,'rejected')
                        finally:binding.reset(token)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logging.error('Leo text route %s temporarily unavailable',route)
                    await asyncio.sleep(3)
                await asyncio.sleep(.3)

        async def _complete(self,data,status):
            async with self._bridge_req('post','complete',5,json={'chatId':data['chatId'],
                'bridgeLease':data['bridgeLease'],'status':status}) as response:
                if response.status!=200:logging.error('Text processing receipt unavailable for %s',route)

        async def _send_typing(self,chat_id,lease=None,presence='composing'):
            if not chat_id or not self._http_session:
                return
            try:
                payload={'chatId':chat_id,'presence':presence}
                if lease:payload['bridgeLease']=lease
                async with self._bridge_req('post','typing',5,json=payload) as resp:
                    pass
            except Exception:
                pass

        async def _typing_loop(self,chat_id,lease,stop_event):
            while not stop_event.is_set():
                await self._send_typing(chat_id,lease,'composing')
                try:
                    await asyncio.wait_for(stop_event.wait(),timeout=6.0)
                except (asyncio.TimeoutError,asyncio.CancelledError):
                    pass

        async def _process_message_background(self,event,session_key):
            token=binding.set(event.raw_message)
            stop_typing=asyncio.Event()
            raw=event.raw_message if isinstance(event.raw_message,dict) else {}
            chat_id=raw.get('chatId')
            lease=raw.get('bridgeLease')
            typing_task=asyncio.create_task(self._typing_loop(chat_id,lease,stop_typing))
            try:
                return await super()._process_message_background(event,session_key)
            finally:
                stop_typing.set()
                typing_task.cancel()
                await self._send_typing(chat_id,lease,'paused')
                binding.reset(token)

        async def _dispatch_inline_reply(self,event,**kwargs):
            token=binding.set(event.raw_message)
            try:return await super()._dispatch_inline_reply(event,**kwargs)
            finally:binding.reset(token)

        async def on_processing_complete(self,event,outcome):
            # Native histories retain uncertain outcomes; a claimed action is never replayed on restart.
            await self._complete(event.raw_message,'complete' if str(getattr(outcome,'value',outcome)).lower()=='success' else 'interrupted')

        async def _post_bridge_message(self,path,payload,*,timeout):
            data=binding.get()
            if not isinstance(data,dict) or payload.get('chatId')!=data.get('chatId'):
                return SendResult(success=False,error='No admitted inbound reply binding')
            payload={**payload,'bridgeLease':data['bridgeLease']}
            digest=json.dumps({'path':path,'payload':payload},sort_keys=True,ensure_ascii=False).encode()
            payload['sendKey']=hashlib.sha256(digest).hexdigest()
            return await super()._post_bridge_message(path,payload,timeout=timeout)

        async def send(self,chat_id,content,reply_to=None,metadata=None):
            if _is_non_conversational(content, metadata):
                return SendResult(success=True)
            cleaned = clean_reply_punctuation(content)
            if not cleaned or not cleaned.strip():
                return SendResult(success=True)
            asyncio.create_task(asyncio.to_thread(_retain_memory_item, cleaned, 'Leo', route))
            return await super().send(chat_id,cleaned,reply_to=None,metadata=metadata)

        async def edit_message(self,chat_id,message_id,content,*,finalize=False):
            if _is_non_conversational(content):
                return SendResult(success=True)
            return await self._post_bridge_message('edit',{'chatId':chat_id,'messageId':message_id,
                'message':clean_reply_punctuation(content)},timeout=30)

        async def _send_media_to_bridge(self,chat_id,file_path,media_type,caption=None,file_name=None):
            path=Path(file_path)
            if not path.is_file() or path.stat().st_size>16*1024*1024:
                return SendResult(success=False,error='Attachment missing or exceeds the 16 MB chat limit')
            data=await asyncio.to_thread(path.read_bytes)
            return await self._post_bridge_message('send-media',{'chatId':chat_id,'message':caption or '',
                'mediaBase64':base64.b64encode(data).decode(),'mediaType':media_type,
                'mimetype':mimetypes.guess_type(file_name or path.name)[0] or 'application/octet-stream',
                'fileName':file_name or path.name},timeout=30)

    return EngineAdapter
