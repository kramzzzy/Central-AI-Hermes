"""Private OS call transport bound to the existing authenticated task context."""
import base64
import json
import threading
import time
from uuid import UUID

from hermes_voice import call_greeting
from fish_conversation import filter_playback_input
from stream_conversation import FreeSpeech, TurnControl, TurnCancelled, fish_rest_tts


class OSStreamVoice:
    def __init__(self, calls, voice, team_voice):
        self.calls, self.voice, self.team_voice = calls, voice, team_voice
        self.guard = threading.RLock()
        self.contexts = {}
        self.slots = threading.BoundedSemaphore(2)

    def handle(self, handler, body):
        if not isinstance(body, dict) or set(body) - {'action', 'call_id', 'scope', 'prompt', 'display_name', 'turn_id', 'text', 'voice', 'voice_id', 'voice_context'}:
            raise ValueError('Invalid voice request')
        voice_context = body.get('voice_context', '')
        if not isinstance(voice_context, str) or len(voice_context) > 6000:
            raise ValueError('Invalid widget context')
        call = str(UUID(body['call_id']))
        scope, action = body['scope'], body['action']
        identity = self.calls.identity(scope)
        with self.guard:
            for key in [k for k,v in self.contexts.items() if v['until'] < time.monotonic()]:
                stale = self.contexts.pop(key)
                if stale['control']: stale['control'].cancel()
                threading.Thread(target=stale['conversation'].close, daemon=True).start()
            item = self.contexts.get(call)
            if item and item['identity'] != identity: raise PermissionError('Call binding mismatch')
            if action == 'end':
                if item:
                    self.contexts.pop(call)
                    if item['control']: item['control'].cancel()
                    threading.Thread(target=item['conversation'].close, daemon=True).start()
                return handler.reply(200, {'ended': True})
        # Also checks the exact existing call/member/assistant lease. The grant
        # is supplied by the OS, never by microphone text or model arguments.
        self.calls.handle({'action': 'heartbeat', 'call_id': call, 'scope': scope})
        if action == 'start':
            prompt = body.get('prompt')
            if not isinstance(prompt, str) or not 0 < len(prompt) <= 10000: raise ValueError('Invalid voice identity')
            with self.guard:
                if call in self.contexts: raise ValueError('Call already started')
                if len(self.contexts) >= 4: raise RuntimeError('Voice calls are busy')
                config = dict((self.team_voice if scope.get('assistant') else self.voice).config)
                voice_id = body.get('voice_id')
                if not voice_id and body.get('voice') == 'michael':
                    voice_id = 'a30d099a643d4173836cfee1d8ae6c13'
                elif not voice_id and body.get('voice') == 'jarvis':
                    voice_id = '612b878b113047d9a770c069c8b4fdfe'
                if voice_id:
                    config['FISH_VOICE_ID'] = voice_id
                self.contexts[call] = {'identity': identity, 'scope': dict(scope), 'config': config,
                    'conversation': self.calls.conversation(call, scope), 'control': None, 'turn': None,
                    'until': time.monotonic()+180, 'greeting': call_greeting(body.get('display_name', '')),
                    'playback': []}
            return handler.reply(200, {'started': True})
        with self.guard:
            if not item: raise PermissionError('Voice call is inactive')
            item['scope'] = dict(scope)
            item['until'] = time.monotonic()+180
            if action == 'heartbeat': return handler.reply(200, {'active': True})
            if action == 'stop':
                if item['turn'] == body.get('turn_id') and item['control']: item['control'].cancel()
                return handler.reply(200, {'stopped': True})
            if action not in {'turn', 'greeting', 'notice'}: raise ValueError('Unsupported voice action')
            turn_voice_id = body.get('voice_id')
            if not turn_voice_id and body.get('voice') == 'michael':
                turn_voice_id = 'a30d099a643d4173836cfee1d8ae6c13'
            elif not turn_voice_id and body.get('voice') == 'jarvis':
                turn_voice_id = '612b878b113047d9a770c069c8b4fdfe'
            if turn_voice_id:
                item['config']['FISH_VOICE_ID'] = turn_voice_id
            turn = str(UUID(body['turn_id']))
            prior_playback = [entry['text'] for entry in item['playback']
                              if time.monotonic() - entry['at'] < 30]
            if item['control']: item['control'].cancel()
            control = TurnControl(lambda: self.contexts.get(call) is item and item['until'] > time.monotonic())
            item['control'], item['turn'] = control, turn
        if not self.slots.acquire(blocking=False):
            with self.guard: item['control'] = None
            raise RuntimeError('Voice speech is busy')
        output_guard = threading.RLock()
        started, closed, reply = False, False, ''
        def emit(frame):
            nonlocal started
            control.check()
            with output_guard:
                if closed: raise TurnCancelled()
                if not started:
                    handler.send_response(200)
                    handler.send_header('Content-Type', 'application/x-ndjson')
                    handler.send_header('Transfer-Encoding', 'chunked')
                    handler.send_header('Cache-Control', 'no-store')
                    handler.send_header('Connection', 'close')
                    handler.end_headers(); handler.close_connection = True; started = True
                data = (json.dumps(dict(frame, turn_id=turn))+'\n').encode()
                handler.wfile.write(f'{len(data):x}\r\n'.encode()+data+b'\r\n'); handler.wfile.flush()
                if frame.get('type') == 'audio' and reply:
                    with self.guard:
                        entry = next((x for x in item['playback'] if x['turn'] == turn), None)
                        if entry:
                            entry.update(text=reply, at=time.monotonic())
                        else:
                            item['playback'] = [*item['playback'][-7:],
                                                {'turn': turn, 'text': reply, 'at': time.monotonic()}]
        speech = None
        try:
            emit({'type': 'accepted'})
            notice = None
            if action == 'notice':
                notice = self.calls.handle({'action':'notice','call_id':call,'scope':scope})
                if not notice.get('notice'):
                    emit({'type':'done'}); return
                emit({'type':'status','notice_id':notice['notice_id'],'state':'Preparing update…'})
            speech = FreeSpeech(item['config'], lambda pcm: emit({'type': 'audio',
                'audio': base64.b64encode(pcm).decode()}), control, rate=24000)
            began, first, reply = time.monotonic(), None, ''
            def text(value):
                nonlocal first, reply
                first = first or time.monotonic(); reply += value
                emit({'type':'text','text':reply}); speech.text(value)
            if action == 'greeting':
                try:
                    text(item['greeting'])
                except Exception:
                    import traceback; traceback.print_exc()
            else:
                value = json.dumps(notice['notice'], ensure_ascii=False)[:12000] if notice else body.get('text')
                if not notice:
                    if not isinstance(value, str) or not 0 < len(value.strip()) <= 12000:
                        raise ValueError('Invalid confirmed speech')
                    value = filter_playback_input(value, prior_playback)
                if value is None:
                    text('Speaker audio mixed with your words. Please repeat your request; no new work was started.')
                elif value.strip():
                    item['conversation'].reply(value, text, control, notice=bool(notice), on_event=emit,
                                               voice_context=voice_context)
            try:
                speech.finish()
            except Exception:
                if action != 'greeting':
                    raise
                import traceback; traceback.print_exc()
            if action == 'greeting' and (not speech or not getattr(speech, 'bytes', 0)):
                try:
                    greeting_pcm = fish_rest_tts(item['greeting'], item['config'], rate=24000)
                    if greeting_pcm:
                        emit({'type': 'audio', 'audio': base64.b64encode(greeting_pcm).decode()})
                except Exception:
                    import traceback; traceback.print_exc()
            emit({'type':'done','timings':{'first_text_ms':round(((first or began)-began)*1000),
                'first_audio_ms':round(((getattr(speech, 'first_audio', None) or began)-began)*1000)}})
        except TurnCancelled:
            pass
        except (BrokenPipeError,ConnectionResetError):
            control.cancel()
        except Exception as exc:
            if control.cancelled.is_set() or isinstance(exc, TurnCancelled):
                pass
            else:
                import traceback; traceback.print_exc()
                try: emit({'type':'error','text':"I couldn't complete that reply. Check task status before repeating an action."})
                except Exception: pass
        finally:
            # Stop socket readers before closing the response, and release the
            # exact generation only. End/stop cannot cancel native business jobs.
            control.cancel()
            with output_guard:
                closed = True
                if started:
                    try: handler.wfile.write(b'0\r\n\r\n'); handler.wfile.flush()
                    except Exception: pass
            with self.guard:
                if item.get('control') is control: item['control'], item['turn'] = None, None
            self.slots.release()
