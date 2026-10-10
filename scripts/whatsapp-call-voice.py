"""Private owner-bound WhatsApp PCM -> recognition -> native Hermes -> Jarvis.

Separate native conversation; never attach to the OS owner's Bot Chat. Audio
exists only in RAM. No outbound message transport or public HTTP listener.
"""
import hmac
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, '/opt/os-adapter/scripts')
from hermes_chat import NativeChat
from dotenv import dotenv_values
import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.utils import download_model
from huggingface_hub.errors import LocalEntryNotFoundError
import msgpack
from websockets.sync.client import connect as connect_speech
sys.path.insert(0, '/opt/setup')
from whatsapp_audio import start_audio_server
from whatsapp_callers import resolve_caller, provision_business_phone
from whatsapp_routing import routing, caller_data
from whatsapp_speech import phone_speech, PREFERRED_LOCALE, FALLBACK_LOCALE

CONVERSATION_MODE = os.environ.get('WHATSAPP_CONVERSATION_MODE', 'stream')
if CONVERSATION_MODE == 'fish':
    CONVERSATION_MODE = 'stream'
if CONVERSATION_MODE not in {'native', 'fish', 'stream'}:
    raise RuntimeError('Invalid phone conversation mode')
fish_service = None
text_supervisor = None

PHONE_ROUTES = routing()
OWNER = PHONE_ROUTES['owner']
OWNER_MEMBER = resolve_caller(OWNER) if OWNER else {'number':'', 'profile':'leo', 'name':'Owner'}
BUSINESS = PHONE_ROUTES['business']
active_caller = OWNER
DATA_BASE = Path('/data/voice')
DATA = caller_data(DATA_BASE, OWNER)
DATA.mkdir(parents=True, exist_ok=True)
ROOT = DATA / 'native'
(ROOT / '.runtime').mkdir(parents=True, exist_ok=True)
if not (ROOT / 'scripts').exists():
    (ROOT / 'scripts').symlink_to('/opt/os-adapter/scripts', target_is_directory=True)
KEYFILE = Path('/data/voice-key')
if not KEYFILE.exists():
    KEYFILE.write_text(secrets.token_urlsafe(32))
try:
    os.chmod(KEYFILE, 0o666)
    os.chown(KEYFILE, 10000, 10000)
except Exception:
    pass
KEY = KEYFILE.read_text().strip()
config = dotenv_values('/opt/data/profiles/leo/.env')
from central_ai_integrations import integration_config
config = integration_config(config)
if os.environ.get('FISH_API_KEY'):
    config['FISH_API_KEY'] = os.environ['FISH_API_KEY'].strip()
if os.environ.get('FISH_VOICE_ID'):
    config['FISH_VOICE_ID'] = os.environ['FISH_VOICE_ID'].strip()
else:
    config.setdefault('FISH_VOICE_ID', '612b878b113047d9a770c069c8b4fdfe')
if os.environ.get('FISH_VOICE_NAME'):
    config['FISH_VOICE_NAME'] = os.environ['FISH_VOICE_NAME'].strip()
else:
    config.setdefault('FISH_VOICE_NAME', 'Jarvis')
if os.environ.get('CENTRAL_AI_CALL_SPEECH'):
    config['CENTRAL_AI_CALL_SPEECH'] = os.environ['CENTRAL_AI_CALL_SPEECH'].strip()
elif config.get('FISH_API_KEY'):
    config['CENTRAL_AI_CALL_SPEECH'] = 'fish'
from native_call_speech import native_enabled
if not native_enabled(config) and (not config.get('FISH_API_KEY') or not config.get('FISH_VOICE_ID')):
    print("[voice] Warning: FISH_API_KEY or FISH_VOICE_ID missing; voice service running in idle waiting for credentials", flush=True)
    config['FISH_API_KEY'] = config.get('FISH_API_KEY') or ''
config = phone_speech(config)
# A separate conversation provides the phone channel's context. It cannot resume
# an OS conversation or inherit its signed workspace grant.
os.environ.pop('MICHAEL_OS_URL', None)
os.environ['HERMES_VOICE_LANGUAGE'] = 'en'
os.environ['HERMES_PHONE_CONVERSATION'] = 'true'


class PhoneChat(NativeChat):
    def prepare_voice_effort(self, live):
        if CONVERSATION_MODE in {'fish', 'stream'}:
            # Fish supplies quick conversation; delegated business work keeps
            # Hermes' own configured model and reasoning quality.
            effort = self.rpc('config.get', self.scoped(key='reasoning')).get('value')
            if effort not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}:
                raise RuntimeError('Invalid native task reasoning setting')
            return effort, None
        return super().prepare_voice_effort(live)

    def connect(self):
        with self.connect_guard:
            self.start()
            if not self.sid:
                saved = self.root.parent / 'session.json'
                profile = self.settings['HERMES_PROFILE']
                session = None
                if saved.exists() and profile == 'leo':
                    try:
                        session = self.resume(profile, json.loads(saved.read_text())['id'])
                    except RuntimeError as exc:
                        # Hermes may not persist a newly created, empty session.
                        # Recover only an explicit missing session, never a timeout
                        # or provider failure. Retain the old pointer for diagnosis.
                        if str(exc) != 'session not found':
                            raise
                        saved.rename(saved.with_name('session-missing-' + str(uuid4()) + '.json'))
                if session is None:
                    session = self.rpc('session.create', {'profile': profile,
                        'title': 'WhatsApp voice — ' + self.settings.get('HERMES_PHONE_NAME', 'Mark Tech'), 'source': 'whatsapp',
                        'follow_profile_config': True}, timeout=60)
                self.sid = session['session_id']
                self.stored = session.get('stored_session_id') or session.get('session_key') or self.sid
                self.info = session.get('info', {})
                if profile == 'leo':
                    saved.write_text(json.dumps({'id': self.stored}))
                    saved.chmod(0o600)
                if not self.restore_voice_effort(recover=True) or not self.restore_voice_model(recover=True):
                    raise RuntimeError('Previous phone turn is still settling')
            # Phone admission needs live status, not another copy of the entire
            # private history. Resume already loads the conversation natively.
            if CONVERSATION_MODE == 'stream':
                live = self.rpc('session.activate', self.scoped(omit_messages=True))
                self.info.update(live.get('info', {}))
                return {'info': dict(self.info)}
            deadline = time.monotonic() + 60
            while True:
                live = self.rpc('session.activate', self.scoped(omit_messages=True))
                if not live.get('info', {}).get('lazy'):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Private phone native worker did not become ready')
                time.sleep(.1)
            self.info.update(live.get('info', {}))
            return {'info': dict(self.info)}


chat = PhoneChat({'HERMES_REPO': '/opt/hermes', 'HERMES_PYTHON': sys.executable,
    'HERMES_PROFILE_ROOT': '/opt/data', 'HERMES_PROFILE': OWNER_MEMBER['profile'],
    'HERMES_VOICE_MODEL': '' if CONVERSATION_MODE in {'fish', 'stream'} else config.get('FISH_LLM_MODEL', 'openai/gpt-4.1-mini'),
    'HERMES_VOICE_PROVIDER': 'openrouter',
    **({'HERMES_TEAM_CONTEXT':'true','HERMES_TEAM_AUTH_HOME':'/opt/data/profiles/leo'} if OWNER_MEMBER['profile'] != 'leo' else {})}, ROOT)


class PhoneTaskChat(PhoneChat):
    """Independent native session per accepted job, retaining Leo's normal settings."""
    def __init__(self, task_id, label, on_session, member=None, conversation=False):
        member = member or resolve_caller(OWNER)
        context_data = caller_data(DATA_BASE, member['number'])
        root = context_data / 'task-workers' / task_id
        (root / '.runtime').mkdir(parents=True, exist_ok=True)
        (root / 'scripts').symlink_to('/opt/os-adapter/scripts', target_is_directory=True)
        self.task_id, self.label, self.on_session = task_id, label, on_session
        self.conversation = conversation
        settings = {**chat.settings, 'HERMES_PROFILE': member['profile'],
            'HERMES_VOICE_MODEL': (os.environ.get('HERMES_VOICE_MODEL') or config.get('FISH_LLM_MODEL', '')) if conversation else ''}
        if member['number'] != OWNER_MEMBER['number']:
            settings.update(HERMES_TEAM_CONTEXT='true', HERMES_TEAM_AUTH_HOME='/opt/data/profiles/leo')
        super().__init__(settings, root)

    def connect(self):
        with self.connect_guard:
            self.start()
            if not self.sid:
                session = self.rpc('session.create', {'profile': self.settings['HERMES_PROFILE'],
                    'title': ('WhatsApp call — ' if self.conversation else 'WhatsApp task — ') + self.label, 'source': 'whatsapp',
                    'follow_profile_config': True}, timeout=60)
                self.sid = session['session_id']
                self.stored = session.get('stored_session_id') or session.get('session_key') or self.sid
                self.info = session.get('info', {})
                self.on_session(self.stored)
            if CONVERSATION_MODE == 'stream':
                live = self.rpc('session.activate', self.scoped(omit_messages=True))
                self.info.update(live.get('info', {}))
                return {'info': dict(self.info)}
            deadline = time.monotonic() + 60
            while True:
                live = self.rpc('session.activate', self.scoped(omit_messages=True))
                if not live.get('info', {}).get('lazy'):
                    self.info.update(live.get('info', {}))
                    return {'info': dict(self.info)}
                if time.monotonic() >= deadline:
                    raise RuntimeError('Background task worker unavailable')
                time.sleep(.1)

    def rpc(self, method, params=None, timeout=30):
        if method == 'prompt.submit' and not self.conversation:
            # Report generation uses full native task output, not spoken-turn limits.
            params = {**params, 'surface': 'app'}
        return super().rpc(method, params, timeout)


def task_native(task_id, label, on_session, member=None, conversation=False):
    member = member or resolve_caller(OWNER)
    if member['number'] == re.sub(r'\D', '', BUSINESS) and business_chat:
        member = {**member, 'profile': business_chat.settings['HERMES_PROFILE']}
    elif member['number'] != OWNER_MEMBER['number'] and member['profile'] == OWNER_MEMBER['profile']:
        raise PermissionError('A separate native profile is required for this caller')
    actor = 'whatsapp:' + member['number']
    worker = PhoneTaskChat(task_id, label, on_session, member, conversation=conversation)
    try:
        worker.handle_voice({'action': 'voice_start', 'actor': actor, 'call_id': task_id})
    except Exception:
        worker.shutdown()
        raise
    def dispatch(action, call, **extra):
        if call != task_id:
            raise RuntimeError('Background task binding mismatch')
        result = worker.handle_voice({'action': action, 'actor': actor, 'call_id': task_id, **extra})
        if action in {'voice_finish', 'voice_stop'}:
            # Create's runtime ID may precede the permanent history key. Native
            # title returns the authoritative persisted key after submission.
            try:
                stored = worker.rpc('session.title', worker.scoped()).get('session_key')
                if stored:
                    worker.stored = stored
                    on_session(stored)
            except Exception:
                pass  # A metadata read cannot invalidate a confirmed task result.
        return result
    def close():
        try:
            worker.handle_voice({'action': 'voice_end', 'actor': actor, 'call_id': task_id})
        finally:
            worker.shutdown()
    return dispatch, close


def native_conversation(call_id, member):
    from native_voice_conversation import NativeVoiceConversation
    configured = resolve_caller(member['number'])
    if not configured or not configured.get('calls') or configured['profile'] != member['profile']:
        raise PermissionError('Caller has no authorized native profile')
    if configured['number'] == re.sub(r'\D', '', BUSINESS) and business_chat:
        configured = {**configured, 'profile': business_chat.settings['HERMES_PROFILE']}
    elif configured['number'] != OWNER_MEMBER['number'] and configured['profile'] == OWNER_MEMBER['profile']:
        raise PermissionError('A separate native profile is required for this caller')
    dispatch, close = task_native(call_id, 'WhatsApp', lambda _: None, configured, conversation=True)
    return NativeVoiceConversation(dispatch, close, call_id, admitted=lambda: allowed_audio_call(call_id))


business_chat = None
if CONVERSATION_MODE in {'fish', 'stream'} and BUSINESS:
    business_profile = resolve_caller(BUSINESS)['profile']
    if business_profile == OWNER_MEMBER['profile']:
        business_profile = provision_business_phone('/opt/data')
    business_root = caller_data(DATA_BASE, BUSINESS) / 'native'
    (business_root / '.runtime').mkdir(parents=True, exist_ok=True)
    if not (business_root / 'scripts').exists():
        (business_root / 'scripts').symlink_to('/opt/os-adapter/scripts', target_is_directory=True)
    business_chat = PhoneChat({**chat.settings, 'HERMES_PROFILE': business_profile,
        'HERMES_PHONE_NAME': resolve_caller(BUSINESS)['name'],
        **({'HERMES_TEAM_CONTEXT': 'true', 'HERMES_TEAM_AUTH_HOME': '/opt/data/profiles/leo'} if business_profile != 'leo' else {'HERMES_TEAM_CONTEXT': 'false', 'HERMES_TEAM_AUTH_HOME': ''})}, business_root)


def current_phone_chat():
    if active_caller == OWNER:
        return chat
    if business_chat and active_caller == BUSINESS:
        return business_chat
    return chat

ASR_DEVICE = os.environ.get('WHATSAPP_ASR_DEVICE', 'cpu')
if ASR_DEVICE not in {'cpu', 'cuda'}:
    raise RuntimeError('Invalid phone recognition device')
ASR_MODEL = 'base.en' if CONVERSATION_MODE == 'stream' else 'small.en'
streaming_asr = None
if CONVERSATION_MODE=='stream' and os.environ.get('WHATSAPP_ASR_MODE') not in {'cloud','deepgram','auto'} and os.environ.get('WHATSAPP_STREAM_ASR_DIR'):
    from phone_stream_asr import StreamingRecognizer
    streaming_asr=StreamingRecognizer(os.environ['WHATSAPP_STREAM_ASR_DIR'])
ASR_REVISION = '3d3d5dee26484f91867d81cb899cfcf72b96be6c' if CONVERSATION_MODE == 'stream' else 'd1d751a5f8271d482d14ca55d9e2deeebbae577f'
model = None
if CONVERSATION_MODE in {'native', 'stream'}:
    if Path('/opt/voice-models/whisper-base.en/model.bin').is_file():
        model_path = '/opt/voice-models/whisper-base.en'
    else:
        try:
            model_path = download_model(ASR_MODEL, revision=ASR_REVISION, cache_dir=str(DATA / 'models'), local_files_only=True)
        except (LocalEntryNotFoundError, Exception):
            model_path = download_model(ASR_MODEL, revision=ASR_REVISION, cache_dir=str(DATA / 'models'))
    model = WhisperModel(model_path, device=ASR_DEVICE,
        compute_type='float16' if ASR_DEVICE == 'cuda' else 'int8',
        cpu_threads=int(os.environ.get('WHATSAPP_ASR_THREADS', '4')))
if model and (ASR_DEVICE == 'cuda' or CONVERSATION_MODE == 'stream'):
    # Initialize CUDA kernels before accepting a call; this synthetic silence is
    # discarded and never sent to Hermes or stored as conversation.
    warm_segments, _ = model.transcribe(np.zeros(16000, dtype=np.float32),
        beam_size=5, temperature=0, language='en', vad_filter=False, condition_on_previous_text=False)
    list(warm_segments)
guard = threading.RLock()
turn_guard = threading.Lock()
active = None
ready = False
turns = 0
last_metrics = {}
last_recognition = {}
turn_records = {}
audio_metrics = {}
failure_counts = {}
snapshot_changes = 0
interruptions = 0


class TurnState:
    def __init__(self):
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.native_started = False
        self.native_complete = False
        self.stream = None
        self.stop_guard = threading.Lock()
        self.stopped = False

    def check(self):
        if self.cancelled.is_set():
            raise InterruptedError('Caller interrupted this reply')

    def stop_native(self, call, turn):
        with self.stop_guard:
            if self.native_started and not self.native_complete and not self.stopped:
                native('voice_stop', call, turn_id=turn)
                self.stopped = True


def incomplete_utterance(text):
    # A conservative pause extension, not a semantic turn model. Only obvious
    # unfinished tails defer submission. No tools run on this draft transcript.
    return bool(re.search(r'\b(and|because|but|if|with|to|the|my|a|an|or|about)\W*$', text, re.I))


def recognize(samples):
    """Use independent English transcripts; withhold uncertain decoding."""
    segments, _ = model.transcribe(samples, language='en', task='transcribe',
        # Go already selects a voiced utterance with pre-roll. A second VAD
        # removed quiet opening words in the synthetic phone-band test.
        beam_size=3 if CONVERSATION_MODE == 'stream' else 5, temperature=0, vad_filter=False,
        condition_on_previous_text=False, initial_prompt='Hello, how are you? What is up. Central AI assistant.',
        log_prob_threshold=-1.0, no_speech_threshold=0.6,
        compression_ratio_threshold=2.4)
    parts = list(segments)
    text = ' '.join(s.text.strip() for s in parts).strip()
    rms = float(np.sqrt(np.mean(samples*samples)))
    quality = {'language': 'en', 'audio_ms': round(len(samples)/16),
               'decision': 'clarify' if rms > 0.004 else 'silence'}
    if not text:
        return '', quality
    quality.update(avg_logprob=round(min(s.avg_logprob for s in parts), 3),
                   no_speech_prob=round(max(s.no_speech_prob for s in parts), 3),
                   compression_ratio=round(max(s.compression_ratio for s in parts), 3))
    # These scores signal decoding failures, not proof of factual correctness.
    # Reject the entire utterance instead of silently dropping uncertain words.
    uncertain = (quality['avg_logprob'] < -1.0 or quality['no_speech_prob'] > 0.6
                 or quality['compression_ratio'] > 2.4)
    quality['decision'] = 'clarify' if uncertain else 'accepted'
    return ('' if uncertain else text), quality


def native(action, call, **extra):
    chat_handler = current_phone_chat() or chat
    if chat_handler is None:
        return None
    try:
        return chat_handler.handle_voice({'action': action, 'actor': 'whatsapp:' + (active_caller or 'unknown'),
            'call_id': call, **extra})
    except Exception as exc:
        print(f"[native voice] handle_voice failed: {exc}", flush=True)
        return None


def speech(text):
    if native_enabled(config):
        from native_call_speech import pcm
        from stream_conversation import TurnControl
        return b''.join(pcm(text[:1800], config, 16000, TurnControl()))
    payload = json.dumps({'text': text[:1800], 'reference_id': config['FISH_VOICE_ID'],
        'format': 'pcm', 'sample_rate': 16000, 'latency': 'balanced'}).encode()
    request = urllib.request.Request('https://api.fish.audio/v1/tts', data=payload,
        headers={'Authorization': 'Bearer ' + config['FISH_API_KEY'],
                 'Content-Type': 'application/json',
                 'model': config.get('FISH_TTS_MODEL') or 's2.1-pro-free'})
    with urllib.request.urlopen(request, timeout=30) as response:
        audio = response.read(16000 * 2 * 45 + 1)
    if len(audio) > 16000 * 2 * 45 or len(audio) % 2:
        raise RuntimeError('Invalid speech audio')
    return audio


GREETING = "Hey Mark! It's Leo. I'm here. What can I help you with?"
RECOVERY = "That reply had a connection problem. I'm still here. What would you like to do next?"
TASK_ACK = "Let me check that."


def fixed_speech(text):
    # Cache only these fixed generated phrases. Never cache microphone audio,
    # private replies or arbitrary text. Includes voice/model in cache identity.
    if text not in {GREETING, RECOVERY, TASK_ACK}:
        raise ValueError('Only fixed phone phrases can be cached')
    speech_mode = 'piper-alan' if native_enabled(config) else ('fish-' + (config.get('FISH_VOICE_ID') or 'jarvis'))
    stamp = hashlib.sha256((speech_mode + '\0' + (config.get('FISH_TTS_MODEL') or 's2.1-pro-free') + '\0' + text).encode()).hexdigest()
    path = DATA / ('fixed-' + stamp + '.pcm')
    if path.exists():
        audio = path.read_bytes()
        if 0 < len(audio) <= 16000*2*45 and len(audio)%2 == 0:
            return audio
    audio = speech(text)
    path.write_bytes(audio)
    path.chmod(0o600)
    return audio


class StreamingSpeech:
    """Connect during recognition; synthesize native text and forward PCM now."""
    def __init__(self, handler):
        self.handler = handler
        self.ready = threading.Event()
        self.done = threading.Event()
        self.cancelled = threading.Event()
        self.socket = None
        self.error = False
        self.completed = False
        self.first_audio = None
        self.bytes = 0
        self.unflushed = ''
        self.pending_text = ''
        self.first_text = None
        self.first_flush = None
        threading.Thread(target=self.receive, daemon=True).start()

    def receive(self):
        try:
            with connect_speech('wss://api.fish.audio/v1/tts/live',
                    additional_headers={'Authorization': 'Bearer ' + config['FISH_API_KEY'],
                        'model': config.get('FISH_TTS_MODEL') or 's2.1-pro-free'},
                    open_timeout=8, close_timeout=2, max_size=4*1024*1024, max_queue=8) as socket:
                self.socket = socket
                socket.send(msgpack.packb({'event': 'start', 'request': {
                    'text': '', 'reference_id': config['FISH_VOICE_ID'], 'format': 'pcm',
                    'sample_rate': 16000, 'latency': 'low', 'chunk_length': 100}}, use_bin_type=True))
                self.ready.set()
                deadline = time.monotonic() + 60
                while not self.cancelled.is_set() and time.monotonic() < deadline:
                    try:
                        raw = socket.recv(timeout=.5)
                    except TimeoutError:
                        continue
                    if not isinstance(raw, bytes):
                        raise RuntimeError('Invalid speech message')
                    event = msgpack.unpackb(raw, raw=False)
                    if event.get('event') == 'audio' and event.get('audio'):
                        audio = event['audio']
                        self.bytes += len(audio)
                        if self.bytes > 16000*2*45:
                            raise RuntimeError('Speech exceeded bounded output')
                        self.first_audio = self.first_audio or time.monotonic()
                        self.handler.audio_chunk(audio)
                    elif event.get('event') == 'finish':
                        self.completed = event.get('reason') == 'stop'
                        if not self.completed:
                            raise RuntimeError('Speech did not finish')
                        return
                if not self.cancelled.is_set():
                    raise RuntimeError('Speech deadline exceeded')
        except Exception:
            self.error = True
        finally:
            self.ready.set()
            self.done.set()

    def send(self, event):
        if not self.ready.wait(9) or not self.socket or self.error or self.cancelled.is_set():
            raise RuntimeError('Speech connection unavailable')
        self.socket.send(msgpack.packb(event, use_bin_type=True))

    def text(self, text):
        if not text:
            return
        # Native deltas can split words and usually prefix their whitespace.
        # Send complete words, then flush the first short phrase immediately;
        # do not wait for an entire long first sentence to finish generating.
        self.pending_text += text
        boundaries = list(re.finditer(r'\s+|[!?;:]$|\.(?<!\d\.)$', self.pending_text))
        if not boundaries:
            return
        cut = boundaries[-1].end()
        phrase, self.pending_text = self.pending_text[:cut], self.pending_text[cut:]
        self.send({'event': 'text', 'text': phrase})
        self.first_text = self.first_text or time.monotonic()
        self.unflushed += phrase
        first_phrase = self.first_flush is None and len(self.unflushed.strip()) >= 24 and len(self.unflushed.split()) >= 5
        if re.search(r'[.!?;:]\s*$', self.unflushed) or first_phrase or len(self.unflushed) >= 100:
            self.send({'event': 'flush'})
            self.first_flush = self.first_flush or time.monotonic()
            self.unflushed = ''

    def finish(self):
        if self.pending_text:
            self.send({'event': 'text', 'text': self.pending_text})
            self.first_text = self.first_text or time.monotonic()
            self.pending_text = ''
        self.send({'event': 'stop'})
        if not self.done.wait(20) or not self.completed or self.error:
            raise RuntimeError('Speech stream did not complete')

    def cancel(self):
        self.cancelled.set()
        if self.socket:
            self.socket.close()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass

    def reply(self, code, body, content_type='application/json', native_complete=False):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.close_connection = True
        if native_complete:
            self.send_header('X-Native-Reply', 'complete')
        self.end_headers()
        self.wfile.write(data)

    def audio_chunk(self, data):
        if not getattr(self, 'audio_started', False):
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Transfer-Encoding', 'chunked')
            self.send_header('Trailer', 'X-Native-Reply')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.audio_started = True
            self.close_connection = True
        self.wfile.write(f'{len(data):x}\r\n'.encode() + data + b'\r\n')
        self.wfile.flush()

    def end_audio(self, complete, review=False):
        if getattr(self, 'audio_started', False):
            state = 'review' if review else ('complete' if complete else 'failed')
            self.wfile.write(f'0\r\nX-Native-Reply: {state}\r\n\r\n'.encode())
            self.wfile.flush()
        else:
            self.reply(200 if complete or review else 503, b'', 'application/octet-stream')

    def do_GET(self):
        if self.path != '/health':
            return self.reply(404, {})
        task_counts = fish_service.task_counts() if fish_service else {}
        return self.reply(200, {'ready': ready, 'text_ready': text_supervisor.ready() if text_supervisor else False,
                              'voice': 'Jarvis (offline)' if native_enabled(config) else (config.get('FISH_VOICE_NAME') or 'Jarvis'),
                              'conversation_mode': CONVERSATION_MODE,
                              'active_call': active is not None, 'native_task_active': chat.voice_turn is not None or
                                  bool(business_chat and business_chat.voice_turn is not None) or
                                  any(task_counts.get(status, 0) for status in ('queued', 'starting', 'running', 'cancelling')),
                              'background_tasks': task_counts,
                              'recognition': 'deepgram/nova-3' if getattr(getattr(fish_service,'streaming_asr',None),'remote',False) else fish_service.cloud_asr.primary if getattr(fish_service,'cloud_asr',None) else 'streaming-zipformer-en' if streaming_asr else 'fish-deepgram-nova-3' if CONVERSATION_MODE=='fish' else 'whisper-' + ASR_MODEL,
                              'recognition_device': 'websocket' if getattr(getattr(fish_service,'streaming_asr',None),'remote',False) else 'api' if getattr(fish_service,'cloud_asr',None) else 'fish' if CONVERSATION_MODE=='fish' else ASR_DEVICE,
                              'recognition_preparation': 'local-English-preview' if getattr(fish_service,'cloud_asr',None) and getattr(fish_service,'streaming_asr',None) and not getattr(fish_service.streaming_asr,'remote',False) else 'live-provider' if getattr(getattr(fish_service,'streaming_asr',None),'remote',False) else 'none',
                              'task_acknowledgement_ready': bool(getattr(fish_service,'task_ack_audio',b'')),
                              'recognition_language': 'en-AU' if getattr(getattr(fish_service,'streaming_asr',None),'remote',False) else 'en', 'reply_language': 'en', 'turns': turns,
                              'preferred_english_locale': PREFERRED_LOCALE, 'secondary_english_locale': FALLBACK_LOCALE,
                              'last_recognition': last_recognition, 'last_metrics': last_metrics,
                              'audio': audio_metrics, 'failures': failure_counts,
                              'snapshot_changes': snapshot_changes, 'interruptions': interruptions,
                              'continuous_listening': True})

    def do_POST(self):
        global active, active_caller, turns, last_metrics, last_recognition, snapshot_changes, interruptions
        if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + KEY):
            return self.reply(401, {})
        call = self.headers.get('X-Call-ID', '')
        if not re.fullmatch(r'[a-f0-9-]{36}', call):
            return self.reply(400, {})
        length = int(self.headers.get('Content-Length', '0'))
        if not 0 <= length <= 16000 * 2 * 60:
            return self.reply(413, {})
        audio = self.rfile.read(length)
        state = None
        try:
            if self.path in {'/callbacks/claim', '/callbacks/finish'}:
                caller = self.headers.get('X-Caller-Number')
                if not fish_service or caller not in fish_service.members:
                    return self.reply(403, {})
                jobs = fish_service.members[caller]['jobs']
                with guard:
                    if self.path == '/callbacks/claim':
                        if not ready or active or fish_service.call:
                            return self.reply(200, {})
                        return self.reply(200, jobs.claim_callback() or {})
                    body = json.loads(audio)
                    jobs.finish_callback(body['callback_id'], body['outcome'])
                    return self.reply(200, {})
            if self.path == '/start':
                callback_id = None
                if audio:
                    callback_id = json.loads(audio).get('callback_id')
                    if not isinstance(callback_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', callback_id):
                        return self.reply(400, {})
                with guard:
                    if active:
                        return self.reply(409, {})
                    try:
                        caller_header = self.headers.get('X-Caller-Number') or OWNER or ''
                        member = resolve_caller(caller_header)
                    except PermissionError:
                        return self.reply(403, {})
                    active_caller = member['number']
                    if fish_service and active_caller not in fish_service.members:
                        data = caller_data(DATA_BASE, active_caller)
                        data.mkdir(parents=True, exist_ok=True)
                        native_fac = (lambda num: lambda task_id, label, ready: task_native(task_id, label, ready, resolve_caller(num)))(active_caller)
                        fish_service.add_member({
                            **member,
                            'data': data,
                            'native_factory': native_fac
                        })
                    if callback_id:
                        # Bind the claimed result before opening a native voice
                        # session; another caller's callback is never attached.
                        jobs=fish_service.members[member['number']]['jobs'] if fish_service else None
                        if not jobs or not jobs.callback_available(callback_id):
                            return self.reply(403, {})
                    if not fish_service:
                        native('voice_start', call)
                    active = call
                    turn_records.clear()
                if fish_service:
                    try:
                        if callback_id:
                            fish_service.start(call, caller=active_caller, callback_id=callback_id)
                        else:
                            fish_service.start(call, caller=active_caller)
                    except Exception:
                        with guard:
                            active = None
                        fish_service.end(call)
                        raise
                return self.reply(200, {})
            with guard:
                if active != call:
                    return self.reply(403, {})
                if self.headers.get('X-Caller-Number', active_caller) != active_caller:
                    return self.reply(403, {})
            if self.path == '/end':
                # Revoke audio admission even if the native lease already
                # expired. Recovery is exact-call scoped, never a new prompt.
                with guard:
                    states = list(turn_records.items())
                    for _, pending in states:
                        pending.cancelled.set()
                    active = None
                if fish_service:
                    # Native cleanup must run even when a provider's session
                    # end endpoint is slow. No expired call can keep a grant.
                    try:
                        fish_service.end(call)
                    except Exception:
                        failure_counts['fish_cleanup'] = failure_counts.get('fish_cleanup', 0) + 1
                for _, pending in states:
                    if pending.stream:
                        pending.stream.cancel()
                if not fish_service and current_phone_chat().voice_call is not None:
                    native('voice_end', call)
                with guard:
                    turn_records.clear()
                return self.reply(200, {})
            if self.path == '/heartbeat':
                if fish_service:
                    return self.reply(200, {'conversation_owner': 'hermes'})
                # Recover an expired idle lease without replaying any action.
                if current_phone_chat().voice_call is None and not current_phone_chat().info.get('running'):
                    native('voice_start', call)
                native('voice_heartbeat', call)
                return self.reply(200, {})
            if self.path == '/interrupt':
                turn = audio.decode('ascii', errors='strict')
                if not re.fullmatch(r'[a-f0-9-]{36}', turn):
                    return self.reply(400, {})
                with guard:
                    state = turn_records.get(turn)
                    if not state:
                        return self.reply(200, {'native_started': True})
                    state.cancelled.set()
                    started = state.native_started
                if state.stream:
                    state.stream.cancel()
                state.stop_native(call, turn)
                if not state.finished.wait(8):
                    return self.reply(503, {'error': 'Reply is still settling'})
                return self.reply(200, {'native_started': started})
            if self.path == '/greeting':
                if fish_service:
                    return self.reply(200, b'', 'application/octet-stream')
                return self.reply(200, fixed_speech(GREETING), 'application/octet-stream')
            if self.path == '/recovery':
                return self.reply(200, fixed_speech(RECOVERY), 'application/octet-stream')
            if self.path != '/turn' or len(audio) < 1920 or len(audio) % 2:
                return self.reply(400, {})
            if fish_service:
                return self.reply(409, {'error': 'Live conversation owns recognition and replies'})
            if not turn_guard.acquire(timeout=8):
                return self.reply(409, {})
            turn = self.headers.get('X-Turn-ID') or str(uuid4())
            if not re.fullmatch(r'[a-f0-9-]{36}', turn):
                turn_guard.release()
                return self.reply(400, {})
            state = TurnState()
            with guard:
                if turn in turn_records:
                    turn_guard.release()
                    return self.reply(409, {})
                turn_records[turn] = state
                while len(turn_records) > 16:
                    oldest = next(iter(turn_records))
                    if not turn_records[oldest].finished.is_set():
                        break
                    turn_records.pop(oldest)
            stage = 'recognition'
            try:
                began = time.monotonic()
                stream = StreamingSpeech(self)
                state.stream = stream
                samples = np.frombuffer(audio, dtype='<i2').astype(np.float32) / 32768
                text, quality = recognize(samples)
                last_recognition = quality
                recognized = time.monotonic()
                state.check()
                if not text:
                    if quality['decision'] == 'clarify':
                        stream.text("I didn't catch that clearly. Could you repeat it?")
                        stream.finish()
                        return self.end_audio(True)
                    stream.cancel()
                    return self.reply(200, b'', 'application/octet-stream')
                if incomplete_utterance(text):
                    if self.headers.get('X-Voice-Final') != 'true':
                        stream.cancel()
                        self.send_response(200)
                        self.send_header('X-Voice-Continue', 'true')
                        self.send_header('Content-Length', '0')
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        self.close_connection = True
                        return
                    stream.text('Could you finish that thought?')
                    stream.finish()
                    return self.end_audio(True)
                stage = 'native_submit'
                with state.stop_guard:
                    with guard:
                        state.check()
                        if active != call:
                            raise InterruptedError('Call ended')
                        # Uncertain submissions cannot be safely replayed.
                        state.native_started = True
                    # Do not hold the audio admission lock across slow RPC.
                    # Interrupt sets cancellation immediately, then serializes
                    # its exact-turn stop behind this submission fence.
                    submitted = native('voice_submit', call, text=text, turn_id=turn)
                submit_done = time.monotonic()
                first_text = None
                cursor = submitted['cursor']
                reply = ''
                completed = False
                sent = ''
                try:
                    deadline = time.monotonic() + 35
                    while time.monotonic() < deadline:
                        state.check()
                        stage = 'native_events'
                        events = native('voice_events', call, turn_id=turn,
                                        epoch=submitted['epoch'], cursor=cursor)
                        cursor = events['cursor']
                        if stream.error:
                            stage = 'speech_stream'
                            raise RuntimeError('Speech stream failed')
                        for event in events.get('events', []):
                            kind, payload = event.get('type'), event.get('payload') or {}
                            if kind == 'message.delta':
                                first_text = first_text or time.monotonic()
                                reply += payload.get('text', '')
                            elif kind == 'message.interim' and not payload.get('already_streamed'):
                                reply = payload.get('text') or reply
                            elif kind == 'message.complete':
                                if payload.get('status') and payload['status'] != 'complete':
                                    raise RuntimeError('Native reply interrupted')
                                reply = payload.get('text') or reply
                                completed = True
                            elif kind in {'error', 'turn.error', 'turn.interrupted'}:
                                raise RuntimeError('Native reply failed')
                            if reply.startswith(sent) and len(reply) > len(sent):
                                stream.text(reply[len(sent):])
                                sent = reply
                            elif sent and reply != sent:
                                # Preserve the spoken prefix when native final
                                # snapshots replace incremental text, as the OS
                                # live stream does. Never replay spoken words.
                                snapshot_changes += 1
                                reply = sent
                            if kind == 'message.interim':
                                stream.text(' ')
                                reply = ''
                                sent = ''
                        if events.get('review'):
                            stream.text(' This needs your approval in Chat before I can continue.')
                            break
                        if completed and not events.get('running'):
                            break
                    if not completed:
                        state.stop_native(call, turn)
                        if not events.get('review'):
                            stream.text(' I could not finish that reply. Please check Chat before retrying an action.')
                    else:
                        native('voice_finish', call, turn_id=turn)
                        state.native_complete = True
                    with guard:
                        if active != call:
                            return self.reply(403, {})
                    turns += 1
                    generated = time.monotonic()
                    stage = 'speech_finish'
                    state.check()
                    stream.finish()
                    last_metrics = {'asr_ms': round((recognized-began)*1000),
                                    'submit_ms': round((submit_done-recognized)*1000),
                                    'native_ms': round((generated-recognized)*1000),
                                    'native_first_ms': round(((first_text or generated)-recognized)*1000),
                                    'speech_done_ms': round((time.monotonic()-began)*1000),
                                    'first_audio_ms': round(((stream.first_audio or time.monotonic())-began)*1000),
                                    'first_speech_text_ms': round(((getattr(stream, 'first_text', None) or generated)-began)*1000),
                                    'first_flush_ms': round(((getattr(stream, 'first_flush', None) or generated)-began)*1000),
                                    'native_complete': completed}
                    print(json.dumps({'phone_voice_timing': last_metrics}), flush=True)
                    return self.end_audio(completed, review=bool(events.get('review')))
                except Exception:
                    stream.cancel()
                    state.stop_native(call, turn)
                    raise
            finally:
                if 'stream' in locals() and not stream.done.is_set():
                    stream.cancel()
                try:
                    state.stop_native(call, turn)
                finally:
                    state.finished.set()
                    turn_guard.release()
        except Exception as e:
            # Never print recognition text, provider credentials or native errors.
            import traceback
            traceback.print_exc()
            interrupted = bool(state and state.cancelled.is_set())
            category = 'interrupted' if interrupted else locals().get('stage', 'control')
            if interrupted:
                interruptions += 1
            else:
                failure_counts[category] = failure_counts.get(category, 0) + 1
            print(json.dumps({'phone_voice_event': category, 'error': str(e)}), flush=True)
            try:
                if getattr(self, 'audio_started', False):
                    self.end_audio(False)
                else:
                    self.reply(503, {'error': 'Phone voice operation unavailable'})
            except (BrokenPipeError, ConnectionResetError):
                pass


def allowed_audio_call(call):
    with guard:
        return active is not None and active == call


# Legacy native audio uses its existing persistent worker. Streamed calls
# admit one private native session in StreamPhoneCall.start, not a second idle
# session selected by the global active-caller variable.
warm_began = time.monotonic()
try:
    if CONVERSATION_MODE != 'stream':
        chat.connect()
        if business_chat:
            business_chat.connect()
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f'Private phone native warmup unavailable: {e}', flush=True)
    raise RuntimeError(f'Private phone native warmup unavailable: {e}')
print(json.dumps({'native_warmup_ms': round((time.monotonic()-warm_began)*1000)}), flush=True)
if CONVERSATION_MODE in {'fish', 'stream'}:
    from whatsapp_fish import FishService
    if CONVERSATION_MODE == 'stream':
        from whatsapp_stream import StreamPhoneCall
    try:
        extra_members = []
        for c in PHONE_ROUTES.get('contacts', []):
            if c.get('calls') and c['number'] != OWNER:
                extra_members.append({
                    **c,
                    'data': caller_data(DATA_BASE, c['number']),
                    'native_factory': (lambda num: lambda task_id, label, ready: task_native(task_id, label, ready, resolve_caller(num)))(c['number'])
                })
        fish_service = FishService(config, DATA, KEY, native, allowed_audio_call, audio_metrics,
            native_factory=task_native,
            **({'call_factory':StreamPhoneCall,'hosted':False} if CONVERSATION_MODE=='stream' else {}),
            members=extra_members)
        fish_service.native_conversation = native_conversation
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise RuntimeError(f'Private Fish conversation setup unavailable: {e}')
    if CONVERSATION_MODE == 'stream':
        def stream_recognize(pcm):
            return recognize(np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768.)
        fish_service.recognize=stream_recognize
        cloud_asr=None
        recognition_mode=os.environ.get('WHATSAPP_ASR_MODE')
        if recognition_mode in {'cloud','deepgram','auto'}:
            from phone_cloud_asr import CloudRecognizer
            cloud_asr=CloudRecognizer(config,
                primary=os.environ.get('WHATSAPP_ASR_MODEL','openai/gpt-4o-mini-transcribe'),
                fallback=os.environ.get('WHATSAPP_ASR_FALLBACK','deepgram/nova-3'))
        fish_service.cloud_asr=cloud_asr
        fish_service.streaming_asr=None if cloud_asr else streaming_asr
        if cloud_asr and os.environ.get('WHATSAPP_STREAM_ASR_DIR'):
            # Local words prepare a reply only; cloud finalization remains the
            # authority. Reuse the offline installed model, with no download.
            try:
                from phone_stream_asr import StreamingRecognizer
                fish_service.streaming_asr=StreamingRecognizer(os.environ['WHATSAPP_STREAM_ASR_DIR'])
            except Exception:
                pass  # An unavailable preview cannot disable accurate ASR.
        direct_key=config.get('DEEPGRAM_API_KEY') or os.environ.get('DEEPGRAM_API_KEY')
        if recognition_mode=='deepgram' or (recognition_mode=='auto' and direct_key):
            from phone_deepgram_asr import StreamingRecognizer
            fish_service.streaming_asr=StreamingRecognizer({**config,'DEEPGRAM_API_KEY':direct_key or ''})
        fish_service.recovery=lambda: fixed_speech(RECOVERY)
        fish_service.task_ack_audio=b''
else:
    audio_server = start_audio_server(KEY, allowed_audio_call, audio_metrics)
for phrase in ((RECOVERY, TASK_ACK) if fish_service and CONVERSATION_MODE=='stream' else (RECOVERY,) if fish_service else (GREETING, RECOVERY)):
    try:
        audio=fixed_speech(phrase)
        if fish_service and phrase==TASK_ACK:fish_service.task_ack_audio=audio
    except Exception:
        failure_counts['fixed_speech_warmup'] = failure_counts.get('fixed_speech_warmup', 0) + 1
ready = True
if os.environ.get('TEXT_CHAT_ENABLED', os.environ.get('LEO_TEXT_CHAT_ENABLED')) == 'true':
    from whatsapp_text_supervisor import TextSupervisor
    text_supervisor = TextSupervisor()
print('Owner-only phone conversation backend ready', flush=True)
ThreadingHTTPServer(('0.0.0.0', 8081), Handler).serve_forever()
