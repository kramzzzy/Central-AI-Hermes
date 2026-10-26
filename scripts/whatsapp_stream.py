"""Owned phone audio engine; reuses the paired device, noise filter and jobs.

Only synthetic/provider audio is used by operator checks. This module never
initiates a phone call or chooses a recipient.
"""
import asyncio
from whatsapp_routing import routing
from collections import deque
import json
import os
import re
import struct
import time

from stream_conversation import Conversation, FreeSpeech, TurnControl, TurnCancelled, confirmed_text_key, _is_valid_openrouter_voice_model
from fish_conversation import filter_playback_input, normalize_voice_greeting
from whatsapp_audio import PhoneAudioFilter

_NON_NAME_WORDS = {
    'interested', 'calling', 'looking', 'good', 'fine', 'great', 'okay', 'ok', 'sure',
    'here', 'asking', 'wondering', 'just', 'from', 'in', 'at', 'not', 'a', 'an', 'the',
    'happy', 'ready', 'sorry', 'yes', 'yeah', 'yep', 'no', 'nah', 'nope', 'solar',
    'panels', 'panel', 'battery', 'batteries', 'home', 'house', 'business', 'system',
    'actually', 'still', 'also', 'well', 'right', 'now', 'today', 'speaking', 'calling',
    'there', 'back', 'again', 'doing', 'going', 'having', 'trying', 'checking', 'about',
    'you', 'me', 'my', 'your', 'remember', 'forgot', 'told', 'already', 'called',
    'before', 'earlier', 'last', 'time', 'name', 'is', 'was', 'am', 'are', 'with',
    'all', 'correct', 'perfect', 'number', 'first', 'second', 'third', 'pronounced',
    'everything', 'true', 'accurate', 'that', 'this', 'it', 'monday', 'tuesday',
    'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'morning', 'afternoon',
    'evening', 'poor', 'correction', 'full', 'phone', 'call', 'maybe', 'brisbane',
    'street', 'road', 'avenue', 'drive', 'email', 'address', 'property', 'book',
    'mad', 'mate', 'mates', 'man', 'bro', 'boss', 'sir', 'connect', 'agent', 'agents',
    'available', 'hi', 'hello', 'hey',
}


def _cleanup_customer_call_audio(max_age_days=7, max_total_mb=500):
    """Auto-delete customer call audio recordings older than 1 week (7 days) and cap total storage."""
    from pathlib import Path
    now_ts = time.time()
    cutoff = now_ts - (max_age_days * 86400)
    max_bytes = max_total_mb * 1024 * 1024
    for base_dir in (Path('/data/customer/call_audio'), Path('/data/call_audio')):
        try:
            if not base_dir.is_dir():
                continue
            kept = []
            for f in base_dir.iterdir():
                if not f.is_file() or f.suffix.lower() not in {'.wav', '.pcm', '.mp3', '.ogg'}:
                    continue
                try:
                    st = f.stat()
                    if st.st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                    else:
                        kept.append((st.st_mtime, st.st_size, f))
                except Exception:
                    pass
            total_bytes = sum(sz for _, sz, _ in kept)
            if total_bytes > max_bytes:
                kept.sort(key=lambda item: item[0])
                for _, sz, f in kept:
                    if total_bytes <= max_bytes:
                        break
                    try:
                        f.unlink(missing_ok=True)
                        total_bytes -= sz
                    except Exception:
                        pass
        except Exception:
            pass


def _extract_spoken_caller_name(caller_text, last_assistant_text=''):
    """Extract caller's name when they introduce themselves or answer a name prompt."""
    if not caller_text or not isinstance(caller_text, str):
        return ''
    cleaned = re.sub(r'[^\w\s\'-]', ' ', caller_text).strip()
    if not cleaned:
        return ''

    def _valid_name_tokens(raw_candidate):
        tokens = [t.strip("'-") for t in raw_candidate.split() if t.strip("'-")]
        valid = []
        for t in tokens[:3]:
            low = t.lower()
            if len(t) < 2 or len(t) > 20 or not t.isalpha() or low in _NON_NAME_WORDS:
                break
            valid.append(t.capitalize())
        return ' '.join(valid)

    if last_assistant_text:
        for rb_pat in (
            r"(?:^|[\n•*-]\s*)(?:full\s+)?name\s*:\s*([A-Za-z][a-zA-Z'-]{1,18}(?:\s+[A-Za-z][a-zA-Z'-]{1,18}){1,2})",
            r"\bso,\s+([A-Za-z][a-zA-Z'-]{1,18}(?:\s+[A-Za-z][a-zA-Z'-]{1,18}){1,2})\s+it\s+is\b",
        ):
            rb_m = re.search(rb_pat, last_assistant_text, flags=re.I)
            if rb_m:
                rb_cand = _valid_name_tokens(rb_m.group(1))
                if len(rb_cand.split()) >= 2:
                    return rb_cand

    for pat in (
        r"\b(?:my\s+(?:full\s+|first\s+|last\s+|poor\s+)?name\s+is|my\s+name's|call\s+me|i\s+am|i'm)\s+([A-Za-z][a-zA-Z']{1,18}(?:\s+[A-Za-z][a-zA-Z']{1,18}){0,2})\b",
        r"\b([A-Za-z][a-z']{1,18})\s+(?:speaking|here|calling)\b",
    ):
        for m in re.finditer(pat, cleaned, flags=re.I):
            cand = _valid_name_tokens(m.group(1))
            if cand:
                return cand

    if last_assistant_text and re.search(
        r"\b(?:your\s+(?:full\s+)?name|who\s+am\s+i\s+speaking|who's\s+calling|may\s+i\s+ask\s+who|what\s+should\s+i\s+call\s+you|who\s+i'm\s+speaking\s+with)\b",
        last_assistant_text,
        flags=re.I,
    ):
        stripped = re.sub(r"^(?:yes|yeah|yep|sure|hi|hello|hey|oh|uh|um|it's|its|i'm|im|this\s+is|my\s+(?:poor\s+|full\s+)?name\s+is)\b[\s,]*", '', cleaned, flags=re.I).strip()
        words = stripped.split()
        if 1 <= len(words) <= 3:
            cand = _valid_name_tokens(stripped)
            if cand:
                return cand
    return ''


def _load_customer_caller_memory(caller_digits, snap=None):
    """Load persistent per-number customer caller profile and conversation history."""
    from pathlib import Path
    digits = re.sub(r'\D', '', str(caller_digits or ''))
    mem = {'number': digits, 'name': '', 'history': [], 'calls': [], 'updated_at': ''}
    if not digits:
        return mem
    for p in (Path(f'/data/customer/callers/{digits}.json'), Path(f'/data/callers/{digits}.json')):
        try:
            if p.is_file():
                loaded = json.loads(p.read_text(encoding='utf-8'))
                if isinstance(loaded, dict):
                    mem.update(loaded)
                    break
        except Exception:
            pass
    cached_name = str(mem.get('name') or '').strip()
    if cached_name:
        name_words = [w.strip("'-").lower() for w in cached_name.split() if w.strip("'-")]
        if not name_words or any(w in _NON_NAME_WORDS or not w.isalpha() for w in name_words):
            mem['name'] = ''
    # Authoritative Box 3 CRM / Contacts name always wins over local ASR caller memory
    if isinstance(snap, dict):
        for section in ('crm', 'contacts'):
            found_snap_name = ''
            for row in (snap.get(section) or []):
                if not isinstance(row, dict):
                    continue
                rphone = re.sub(r'\D', '', str(row.get('phone') or row.get('number') or ''))
                rname = str(row.get('name') or '').strip()
                if rphone and rphone == digits and rname and not re.match(r'^(?:Caller|Visitor|Customer)\b', rname, re.I):
                    rwords = [w.strip("'-").lower() for w in rname.split() if w.strip("'-")]
                    if rwords and not any(w in _NON_NAME_WORDS for w in rwords):
                        found_snap_name = rname
                        break
            if found_snap_name:
                if mem.get('name') != found_snap_name:
                    mem['name'] = found_snap_name
                    _save_customer_caller_memory(digits, mem)
                break
    if not mem.get('name') and isinstance(mem.get('history'), list):
        for h in reversed(mem['history']):
            if isinstance(h, dict) and h.get('role') == 'assistant':
                cand = _extract_spoken_caller_name('yes', str(h.get('content') or ''))
                if cand:
                    mem['name'] = cand
                    break
    if not mem.get('name'):
        # Check customer WhatsApp processed_inbox_ids.json / processed_messages.json or box3_snapshot for a known name on this number
        for proc_path in (
            Path('/data/customer/processed_inbox_ids.json'),
            Path('/data/customer/processed_messages.json'),
            Path('/data/processed_messages.json'),
        ):
            try:
                if proc_path.is_file():
                    pmap = json.loads(proc_path.read_text(encoding='utf-8'))
                    if isinstance(pmap, dict):
                        for info in reversed(list(pmap.values())):
                            if not isinstance(info, dict):
                                continue
                            sid = re.sub(r'\D', '', str(info.get('sender_id') or ''))
                            sname = str(info.get('sender_name') or '').strip()
                            if sid == digits and sname and not re.match(r'^(?:Caller|Visitor|Customer)\b', sname, re.I) and not sname.replace('+', '').isdigit():
                                mem['name'] = sname
                                break
                if mem.get('name'):
                    break
            except Exception:
                pass
    if not mem.get('name') and isinstance(snap, dict):
        for section in ('crm', 'contacts'):
            for row in (snap.get(section) or []):
                if not isinstance(row, dict):
                    continue
                rphone = re.sub(r'\D', '', str(row.get('phone') or row.get('number') or ''))
                rname = str(row.get('name') or '').strip()
                if rphone and rphone == digits and rname and not re.match(r'^(?:Caller|Visitor|Customer)\b', rname, re.I):
                    mem['name'] = rname
                    break
            if mem.get('name'):
                break
    return mem


def _save_customer_caller_memory(caller_digits, mem):
    """Persist per-number customer caller profile and conversation history."""
    from pathlib import Path
    digits = re.sub(r'\D', '', str(caller_digits or ''))
    if not digits or not isinstance(mem, dict):
        return
    mem['number'] = digits
    mem['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    if isinstance(mem.get('history'), list):
        mem['history'] = mem['history'][-24:]
    if isinstance(mem.get('calls'), list):
        mem['calls'] = mem['calls'][-20:]
    for base in (Path('/data/customer/callers'), Path('/data/callers')):
        try:
            base.mkdir(parents=True, exist_ok=True)
            target = base / f'{digits}.json'
            target.write_text(json.dumps(mem, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception:
            pass


def _save_customer_call_wav(call_id, pcm_bytes):
    """Write 16kHz 16-bit mono WAV recording for customer call and prune recordings older than 7 days."""
    if not call_id or not pcm_bytes:
        return ''
    import wave
    from pathlib import Path
    _cleanup_customer_call_audio(max_age_days=7, max_total_mb=500)
    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '', str(call_id)) or str(int(time.time()))
    for base in (Path('/data/customer/call_audio'), Path('/data/call_audio')):
        try:
            base.mkdir(parents=True, exist_ok=True)
            wav_path = base / f'{safe_id}.wav'
            with wave.open(str(wav_path), 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(bytes(pcm_bytes[:16000 * 2 * 1800]))
            return str(wav_path)
        except Exception:
            continue
    return ''


def _sync_customer_call_turn_to_box3(caller_digits, caller_name, call_id, turn_idx, caller_utt, assistant_utt, created_at):
    """Sync customer service voice call turn to App OS Communications and trigger CRM lead / appointment booking."""
    digits = re.sub(r'\D', '', str(caller_digits or ''))
    if not digits or (not caller_utt and not assistant_utt):
        return
    msg_id = f'call-{call_id}-turn-{turn_idx}'
    raw_asst = str(assistant_utt or '').strip()
    raw_tags = re.findall(r'\[\[\s*(?:BOOK|LEAD)\s*:\s*\{[\s\S]*?\}\s*\]\]', raw_asst, flags=re.I)
    clean_asst = re.sub(r'\[\[\s*(?:BOOK|LEAD)[\s\S]*?\]\]+', '', raw_asst, flags=re.I).strip()
    user_text = f'📞 [Voice Call] {caller_utt}' if caller_utt and caller_utt != '[Call Answered]' else '📞 [Incoming WhatsApp Voice Call]'
    visible_reply = f'📞 [Voice Call] {clean_asst}' if clean_asst else ''
    reply_with_tags = (visible_reply + (' ' + ' '.join(raw_tags) if raw_tags else '')).strip()
    item = {
        'sender_id': digits,
        'sender_name': str(caller_name or '').strip(),
        'wire_chat': f'{digits}@s.whatsapp.net',
        'message_id': msg_id,
        'text': user_text,
        'reply': reply_with_tags,
        'created_at': created_at,
    }
    try:
        from hermes_plugins.central_ai_whatsapp.adapter import _append_customer_sync_queue
        _append_customer_sync_queue(item)
    except Exception:
        pass
    try:
        import urllib.request
        from pathlib import Path
        urls = []
        for p in ('/data/app_os_url.json', '/opt/data/app_os_url.json', '/opt/os-adapter/.runtime/app_os_url.json'):
            f = Path(p)
            if f.is_file():
                try:
                    j = json.loads(f.read_text(encoding='utf-8'))
                    if isinstance(j.get('urls'), list):
                        urls.extend(str(u).rstrip('/') for u in j['urls'] if u)
                    elif j.get('url'):
                        urls.append(str(j['url']).rstrip('/'))
                except Exception:
                    pass
        for env_k in ('APP_OS_URL', 'MICHAEL_OS_URL'):
            v = os.environ.get(env_k, '').strip().rstrip('/')
            if v:
                urls.append(v)
        urls.extend(['http://web:3000', 'http://127.0.0.1:3000'])
        api_key = (os.environ.get('HERMES_API_KEY') or 'hermes_standalone_secret_token_32chars').strip().strip('"\'')
        payload = json.dumps({
            **item,
            'line': 'customer',
            'precomputed_reply': reply_with_tags,
        }).encode('utf-8')
        for base_url in dict.fromkeys(u for u in urls if u.startswith('http')):
            try:
                req = urllib.request.Request(
                    f'{base_url}/api/connections/whatsapp/webhook',
                    data=payload,
                    headers={
                        'Content-Type': 'application/json',
                        'Authorization': f'Bearer {api_key}',
                        'User-Agent': 'CentralAI-Hermes/1.0',
                    },
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                continue
    except Exception:
        pass

class PhonePCM:
    """Keep provider chunks continuous; pad only the final transport frame."""
    frame_bytes = 1920
    prefill_frames = 1
    single_burst_frames = 4

    def __init__(self):
        self.pending = bytearray()
        self.started = False
        self.chunks = self.previous_padding = self.padding = 0
        self.first_output = None
        self.source_end = None

    def frames(self, data, final=False):
        if len(data) % 2:
            raise ValueError('PCM must contain whole samples')
        if data:
            self.chunks += 1
            self.previous_padding += (-len(data)) % self.frame_bytes
            self.pending.extend(data)
        # Prefill 3 frames (180ms) so network jitter never causes mid-word dropouts
        if not self.started and not final:
            if len(self.pending) < self.frame_bytes * self.prefill_frames:
                return
        self.started = True
        while len(self.pending) >= self.frame_bytes:
            frame = bytes(self.pending[:self.frame_bytes])
            del self.pending[:self.frame_bytes]
            yield frame
        if final and self.pending:
            self.padding = self.frame_bytes - len(self.pending)
            yield bytes(self.pending) + bytes(self.padding)
            self.pending.clear()


class StreamPhoneCall:
    def __init__(self, service, call, member=None, callback_notice=None):
        self.service, self.call = service, call
        self.member = member or service.members[routing()['owner']]
        self.jobs = self.member['jobs']
        # Customer-line calls: customer agent persona and voice, no tools, never the owner's native session.
        self.customer = self.member.get('line') == 'customer'
        use_customer_voice = (
            self.customer
            or str(self.member.get('role') or '').lower() == 'customer'
            or 'james' in str(self.member.get('assigned_assistant') or self.member.get('profile') or '').lower()
        )
        base_cfg = dict(service.config)
        try:
            from central_ai_integrations import discover_fish_credentials, valid_fish_key
            base_cfg['FISH_API_KEY'] = valid_fish_key(base_cfg.get('FISH_API_KEY'))
            if not base_cfg.get('FISH_API_KEY'):
                for k, v in discover_fish_credentials('/opt/data').items():
                    if v and not base_cfg.get(k):
                        base_cfg[k] = v
        except Exception:
            pass
        try:
            from voice_setup import load_settings
            saved_voice = load_settings({'HERMES_PROFILE_ROOT': '/opt/data'})
            if isinstance(saved_voice, dict):
                for k, v in saved_voice.items():
                    if v:
                        base_cfg[k] = v
        except Exception:
            pass
        if base_cfg.get('FISH_API_KEY') or use_customer_voice:
            base_cfg['CENTRAL_AI_CALL_SPEECH'] = 'fish'
        customer_voice = os.environ.get('CUSTOMER_FISH_VOICE_ID', 'a30d099a643d4173836cfee1d8ae6c13').strip()
        if not customer_voice or customer_voice in {
            '612b878b113047d9a770c069c8b4fdfe',
            '31d3dd937d2944a1b06da3db533d27f9',
            '711cf3ed00ab441a8f54a45058047b7a',
            'bf342d9e9e5b44e0ade1ccfd1abb4815',
        }:
            customer_voice = 'a30d099a643d4173836cfee1d8ae6c13'
        self.speech_config = ({**base_cfg, 'FISH_VOICE_ID': customer_voice, 'FISH_ENGLISH_FALLBACK_VOICE_ID': customer_voice, 'FISH_VOICE_NAME': 'Michael', 'CENTRAL_AI_CALL_SPEECH': 'fish'}
                              if use_customer_voice else service.config)
        self.input, self.output = asyncio.Queue(maxsize=32), asyncio.Queue(maxsize=512)
        self.closed = self.connected = False
        self.state, self.generation = 'initializing', 1
        self.tasks, self.worker = set(), None
        self.control = None
        self.audio_filter = PhoneAudioFilter(service.vad)
        self.pre = deque(maxlen=4)
        self.capture = bytearray()
        self.voiced = self.speech_run = 0
        self.silence_frames = 0
        self.capture_extended = False
        self.last_user_activity = self.last_speech = time.monotonic()
        self.playback_until = self.last_speaking_end = 0.
        self.last_notice = self.last_progress = 0.
        self.pending_notice = None
        self.callback_notice = callback_notice
        self.greeting_pending = True
        self.received_input = False
        self.connected_at = 0.
        self.rendered_generation, self.last_rendered = None, 0.
        self.last_recovery = 0.
        self.last_clarify_at = 0.
        self.asr = None
        self.prepared_speech = None
        self.speculative=None
        self.pending_input=None
        self.spoken_text=''
        self.capture_echo_text=''
        self.capture_rejected=False
        self.early_asr=None
        self.capture_version=0
        self.callback_new_tasks_since = time.time() if callback_notice else None
        self.conversation = None
        self.fallback_conversation = None
        self._agent_conv_mtime = None
        self.caller_digits = re.sub(r'\D', '', str(self.member.get('number') or ''))
        self.call_audio_pcm = bytearray()
        if self.customer:
            _cleanup_customer_call_audio(max_age_days=7, max_total_mb=500)
            self.caller_memory = _load_customer_caller_memory(self.caller_digits)
            self.caller_name = str(self.caller_memory.get('name') or '').strip()
        else:
            self.caller_memory = {}
            self.caller_name = ''
        self.call_turns = []
        self.call_started_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        self.service.metrics['last_call_transcript'] = {
            'call_id': self.call,
            'caller': self.member.get('number'),
            'caller_name': self.caller_name,
            'line': 'customer' if self.customer else 'owner',
            'started_at': self.call_started_at,
            'turns': self.call_turns,
        }

    def _get_fallback_conversation(self):
        from pathlib import Path
        snap_paths = (
            Path('/data/box3_snapshot.json'),
            Path('/opt/data/box3_snapshot.json'),
            Path('/opt/os-adapter/.runtime/box3_snapshot.json'),
        )
        profile_slug = str(self.member.get('profile') or 'leo').strip()
        if profile_slug in {'owner-profile', 'assistant', ''}:
            profile_slug = 'leo'
        profile_dir = Path('/opt/data/profiles') / profile_slug
        if not profile_dir.is_dir():
            profile_dir = Path('/opt/data/profiles/leo')
        soul_path = profile_dir / 'SOUL.md'
        cfg_path = profile_dir / 'config.yaml'

        mtimes = []
        for p in (*snap_paths, Path('/data/contacts.json'), Path('/data/customer/contacts.json'), soul_path, cfg_path):
            try:
                if p.is_file():
                    mtimes.append((str(p), p.stat().st_mtime))
            except Exception:
                pass
        mtime_key = tuple(mtimes)
        if self.fallback_conversation is not None and self._agent_conv_mtime == mtime_key:
            return self.fallback_conversation

        try:
            from central_ai_identity import central_ai_identity, get_company_name
            company = get_company_name()
        except Exception:
            def central_ai_identity(x): return x
            company = ""

        snap = None
        for candidate in snap_paths:
            try:
                if candidate.is_file():
                    data = json.loads(candidate.read_text(encoding='utf-8'))
                    if isinstance(data, dict) and ('knowledge' in data or 'counts' in data or 'agents' in data):
                        snap = data
                        break
            except Exception:
                pass
        try:
            from hermes_plugins.central_ai_app_os.client import _DEFAULT_YCS_SNAPSHOT
            if not snap:
                snap = dict(_DEFAULT_YCS_SNAPSHOT)
        except Exception:
            if not snap:
                snap = {'counts': {}, 'knowledge': [], 'agents': []}

        # Resolve the assigned Agent Profile dynamically from Box 2 / Box 3 (agent_profiles)
        agents = snap.get('agents') if isinstance(snap.get('agents'), list) else []
        target_pref = str(self.member.get('assigned_assistant') or self.member.get('profile') or '').strip().lower()
        assigned_agent = None
        if target_pref and target_pref not in {'owner-profile', 'assistant', 'leo'}:
            for a in agents:
                if isinstance(a, dict) and (
                    str(a.get('id') or '').lower() == target_pref
                    or str(a.get('name') or '').strip().lower() == target_pref
                    or str(a.get('app_role') or '').strip().lower() == target_pref
                ):
                    assigned_agent = a
                    break
        if not assigned_agent:
            for a in agents:
                if isinstance(a, dict) and a.get('is_main'):
                    assigned_agent = a
                    break
        if not assigned_agent and agents:
            assigned_agent = agents[0] if isinstance(agents[0], dict) else None

        agent_name = str((assigned_agent or {}).get('name') or '').strip()
        if not agent_name:
            try:
                name_file = profile_dir / '.assistant-name.json'
                if name_file.is_file():
                    agent_name = str(json.loads(name_file.read_text(encoding='utf-8')).get('name') or '').strip()
            except Exception:
                pass
        agent_role = str((assigned_agent or {}).get('role') or '').strip()
        agent_desc = str((assigned_agent or {}).get('description') or '').strip()
        agent_instructions = str((assigned_agent or {}).get('instructions') or '').strip()
        agent_model = str((assigned_agent or {}).get('model') or '').strip()

        soul_text = ''
        cfg_prompt = ''
        cfg_model = ''
        try:
            if soul_path.is_file():
                soul_text = soul_path.read_text(encoding='utf-8').strip()
        except Exception:
            pass
        try:
            if cfg_path.is_file():
                import yaml
                cfg_data = yaml.safe_load(cfg_path.read_text(encoding='utf-8')) or {}
                raw_sp = str((cfg_data.get('agent') or {}).get('system_prompt') or '').strip()
                if raw_sp:
                    for label in ('Central OS integration', 'Michael OS integration', 'Central AI identity'):
                        raw_sp = re.sub(r'\s*\[' + re.escape(label) + r'\].*?\[/' + re.escape(label) + r'\]\s*', '\n\n', raw_sp, flags=re.S)
                    cfg_prompt = raw_sp.strip()
                m_cfg = cfg_data.get('model')
                if isinstance(m_cfg, dict) and m_cfg.get('default'):
                    cfg_model = str(m_cfg.get('default')).strip()
        except Exception:
            pass

        caller_name = self.member.get('name', '') or 'the caller'
        company_str = f" for {company}" if company else ""
        identity_parts = []
        if agent_name or agent_role:
            role_part = f", {agent_role}" if agent_role else ""
            identity_parts.append(f"You are {agent_name or 'the configured assistant'}{role_part}{company_str}, speaking with {caller_name} on a live WhatsApp voice call.")
        if agent_desc:
            identity_parts.append(agent_desc)
        if soul_text:
            identity_parts.append(soul_text)
        elif cfg_prompt:
            identity_parts.append(cfg_prompt)
        if agent_instructions and agent_instructions not in soul_text and agent_instructions not in cfg_prompt:
            identity_parts.append(f"[Agent Profile Instructions]\n{agent_instructions}\n[/Agent Profile Instructions]")

        try:
            from hermes_voice import voice_call_note
            identity_parts.append(voice_call_note('en'))
        except Exception:
            pass

        # Attach assigned Knowledge Base cards (full content, not just titles)
        cards = snap.get('knowledge') if isinstance(snap.get('knowledge'), list) else []
        assigned_kb_ids = set(str(x) for x in ((assigned_agent or {}).get('knowledge_ids') or []) if x)
        if assigned_kb_ids and not (assigned_agent or {}).get('is_main', True):
            selected_cards = [c for c in cards if isinstance(c, dict) and str(c.get('id') or '') in assigned_kb_ids]
            if not selected_cards:
                selected_cards = [c for c in cards if isinstance(c, dict)]
        else:
            selected_cards = [c for c in cards if isinstance(c, dict)]

        if selected_cards:
            kb_lines = []
            for c in selected_cards[:15]:
                title = str(c.get('title') or '').strip()
                cat = str(c.get('category') or 'General').strip()
                content = str(c.get('content') or c.get('summary') or '').strip()[:1200]
                if title or content:
                    kb_lines.append(f"- [{cat}] {title}: {content}")
            if kb_lines:
                identity_parts.append("[Assigned Knowledge Base]\n" + "\n".join(kb_lines) + "\n[/Assigned Knowledge Base]")

        counts = snap.get('counts') if isinstance(snap.get('counts'), dict) else {}
        identity_parts.append(
            f"[Live Box 3 Workspace Summary: {counts.get('knowledge_sources', len(selected_cards))} knowledge sources, "
            f"{counts.get('available_skills', 60)} skills, {counts.get('tasks', len(snap.get('tasks') or []))} tasks, "
            f"{counts.get('crm_clients', len(snap.get('crm') or []))} CRM clients, and "
            f"{counts.get('whatsapp_contacts', len(snap.get('contacts') or []))} WhatsApp contacts. "
            "All live tools (inspect_workspace, manage_crm, manage_tasks, search_knowledge, get_knowledge, "
            "add_knowledge, get_workspace_overview, get_live_weather, send_whatsapp_message, call_whatsapp_contact, "
            "control_widget, end_call) are active.]"
        )

        prompt = central_ai_identity("\n\n".join(p for p in identity_parts if p))
        if self.customer:
            if not self.caller_name:
                refreshed_mem = _load_customer_caller_memory(self.caller_digits, snap)
                if refreshed_mem.get('name'):
                    self.caller_name = str(refreshed_mem['name']).strip()
                    self.caller_memory['name'] = self.caller_name
            prompt = self._customer_prompt(company, snap)
        conv_config = dict(self.service.config or {})
        if not conv_config.get('OPENROUTER_API_KEY') and os.environ.get('OPENROUTER_API_KEY'):
            conv_config['OPENROUTER_API_KEY'] = os.environ['OPENROUTER_API_KEY'].strip()
        if not conv_config.get('OPENAI_API_KEY') and os.environ.get('OPENAI_API_KEY'):
            conv_config['OPENAI_API_KEY'] = os.environ['OPENAI_API_KEY'].strip()
        resolved_model = ''
        for candidate_model in (
            os.environ.get('HERMES_VOICE_MODEL', ''),
            conv_config.get('HERMES_VOICE_MODEL', ''),
            os.environ.get('WHATSAPP_CHAT_MODEL', ''),
            agent_model if _is_valid_openrouter_voice_model(agent_model) else '',
            cfg_model if _is_valid_openrouter_voice_model(cfg_model) else '',
            os.environ.get('HERMES_CHAT_MODEL', '') if _is_valid_openrouter_voice_model(os.environ.get('HERMES_CHAT_MODEL', '')) else '',
        ):
            m = str(candidate_model or '').strip()
            if _is_valid_openrouter_voice_model(m):
                resolved_model = m
                break
        if self.customer:
            # Customer calls use OpenAI's fastest voice-turn model (openai/gpt-4o-mini) for sub-300ms first token.
            fast = os.environ.get('CUSTOMER_VOICE_MODEL', '').strip() or 'openai/gpt-4o-mini'
            if fast == 'google/gemini-2.5-flash-lite':
                fast = 'openai/gpt-4o-mini'
            if _is_valid_openrouter_voice_model(fast):
                resolved_model = fast
        if resolved_model:
            conv_config['AGENT_MODEL'] = resolved_model

        def dispatch_tool(event, caller_text):
            return self.jobs.handle(self.call, event, self.service.allowed)

        if self.fallback_conversation is not None:
            self.fallback_conversation.prompt = prompt
            self.fallback_conversation.config = conv_config
            self.fallback_conversation.caller_name = self.caller_name
            self.fallback_conversation.caller_digits = self.caller_digits
        else:
            self.fallback_conversation = Conversation(conv_config, prompt, dispatch_tool, names=set() if self.customer else None)
            self.fallback_conversation.caller_name = self.caller_name
            self.fallback_conversation.caller_digits = self.caller_digits
            if self.customer and isinstance(self.caller_memory.get('history'), list):
                for h_item in self.caller_memory['history'][-12:]:
                    if isinstance(h_item, dict) and h_item.get('role') in {'user', 'assistant'} and h_item.get('content'):
                        self.fallback_conversation.history.append({
                            'role': h_item['role'],
                            'content': str(h_item['content'])[:600],
                        })
        self._agent_conv_mtime = mtime_key
        return self.fallback_conversation

    def _customer_prompt(self, company, snap=None):
        # Persona and knowledge come from the customer agent App OS syncs into contacts.json / box3_snapshot.json; nothing is fixed here.
        r = routing()
        if not snap or not isinstance(snap, dict):
            from pathlib import Path
            for sp_p in (Path('/data/box3_snapshot.json'), Path('/opt/data/box3_snapshot.json'), Path('/opt/os-adapter/.runtime/box3_snapshot.json')):
                try:
                    if sp_p.is_file():
                        snap = json.loads(sp_p.read_text(encoding='utf-8'))
                        break
                except Exception:
                    pass
            if not isinstance(snap, dict):
                snap = {}
        company = company or str(r.get('company_name') or snap.get('company_name') or '').strip()
        agent_name = str(r.get('customer_agent') or '').strip()
        role = str(r.get('customer_agent_role') or '').strip()
        instructions = str(r.get('customer_agent_instructions') or '').strip()
        raw_kb = r.get('customer_agent_knowledge') if isinstance(r.get('customer_agent_knowledge'), list) else []

        if snap and isinstance(snap, dict):
            snap_agents = [a for a in (snap.get('agents') or []) if isinstance(a, dict)]
            cust_agent = None
            if agent_name:
                cust_agent = next((a for a in snap_agents if str(a.get('name') or '').strip().lower() == agent_name.lower()), None)
            if not cust_agent:
                cust_agent = next((a for a in snap_agents if str(a.get('app_role') or '').strip() == 'customer_service'), None)
            if not cust_agent:
                cust_agent = next((a for a in snap_agents if not a.get('is_main') and a.get('enabled', True)), None)
            if cust_agent:
                agent_name = agent_name or str(cust_agent.get('name') or '').strip()
                role = role or str(cust_agent.get('role') or '').strip()
                instructions = instructions or str(cust_agent.get('instructions') or '').strip()
            if not raw_kb and isinstance(snap.get('knowledge'), list):
                raw_kb = snap['knowledge']

        cards = [f"- [{c.get('category') or 'General'}] {c.get('title') or ''}: {str(c.get('content') or '')[:1200]}"
                 for c in raw_kb[:15] if isinstance(c, dict)]
        caller_mem_lines = []
        if self.caller_name:
            first_n = self.caller_name.split()[0]
            caller_mem_lines.append(
                f"- Caller's Name: {self.caller_name} (Phone: +{self.caller_digits}). You ALREADY know their name from your conversation—address them warmly as {first_n} when natural and NEVER ask for their name again."
            )
        prior_hist = self.caller_memory.get('history') if isinstance(self.caller_memory.get('history'), list) else []
        if prior_hist:
            recent_lines = []
            for h in prior_hist[-8:]:
                if isinstance(h, dict) and h.get('content'):
                    who = (self.caller_name or 'Caller') if h.get('role') == 'user' else (agent_name or 'James')
                    recent_lines.append(f"  {who}: {str(h['content'])[:240]}")
            if recent_lines:
                caller_mem_lines.append("- Previous Call Conversation with this Caller:\n" + "\n".join(recent_lines))
        caller_mem_block = (
            "[Returning Caller Memory]\n" + "\n".join(caller_mem_lines) + "\n[/Returning Caller Memory]"
            if caller_mem_lines else ""
        )

        avail_block = ''
        try:
            import datetime
            now_dt = datetime.datetime.now()
            avail_lines = [f"- Current Date & Time: {now_dt.strftime('%A, %Y-%m-%d %H:%M')}"]
            avail_lines.append("- Business Hours: Monday to Friday, 09:00 to 16:00 (Phone Consultation, On-Site Visit, or Roof Inspection).")
            upcoming_days = []
            for d_off in range(0, 10):
                dt_cand = now_dt + datetime.timedelta(days=d_off)
                if dt_cand.weekday() < 5:
                    upcoming_days.append(f"{dt_cand.strftime('%a %b %d')} ({dt_cand.strftime('%Y-%m-%d')})")
                if len(upcoming_days) >= 5:
                    break
            if upcoming_days:
                avail_lines.append("- Next Open Weekdays: " + ", ".join(upcoming_days))
            avail_block = "[Live Appointment Availability]\n" + "\n".join(avail_lines) + "\n[/Live Appointment Availability]"
        except Exception:
            pass

        parts = [f"You are {agent_name or 'the customer service assistant'}"
                 f"{', ' + role if role else ''}{' for ' + company if company else ''}, speaking as a real human specialist with a customer on a live WhatsApp phone call.",
                 caller_mem_block,
                 instructions,
                 '[Assigned Knowledge Base]\n' + '\n'.join(cards) + '\n[/Assigned Knowledge Base]' if cards else '',
                 avail_block,
                 # The shared voice note carries owner tool guidance, so customer calls get delivery + lead/booking rules.
                 '[Live call communication, lead qualification & appointment booking rules:\n'
                 '- 100% REAL HUMAN FEEL (NEVER MENTION AI, MEMORY SYSTEMS, OR TECHNICAL ERRORS): Speak strictly as a real human specialist at the company. NEVER say or imply that you are an AI, bot, virtual assistant, language model, or automated agent, and NEVER mention technical errors, system glitches, prompts, tools, databases, or say things like "my memory of you hasn\'t been saved" or "I don\'t store memory between calls". If you ever don\'t recall a detail the caller mentioned earlier, respond naturally like a real human: "I\'m sorry, I can\'t remember—could you remind me?" or "Sorry, that slipped my mind—what was your name again?".\n'
                 '- STRICT 1-TO-2 SHORT SPOKEN SENTENCES (ZERO BULLET POINTS OR MONOLOGUES): You are speaking on a live phone call, NOT writing a chat message. NEVER use bullet points (`- Name: ...`), numbered lists, colons, or multi-paragraph summaries. Keep EVERY turn to 1 or 2 short, natural spoken sentences (15–30 words maximum) with everyday contractions. Begin with a natural 2-to-4-word human reaction ("Oh, no worries at all.", "Yeah, that makes sense.", "Right, gotcha.") integrated smoothly into your first sentence.\n'
                 '- PROACTIVE VALUE FIRST (NEVER ASK PERMISSION TO SHARE INFO): Never ask "Would you like to know...?", "Do you want me to explain...?", or "Shall I calculate that?". Deliver the recommended solar size (kW and panels) and estimated savings concisely in one short spoken sentence, then ask one simple follow-up question.\n'
                 f'- NATURAL LEAD QUALIFICATION & APPOINTMENT BOOKING ON THE CALL: Gently qualify the caller one simple question at a time: 1) existing solar check, 2) last power bill amount, 3) property street address so the team can check their roof on satellite imagery, and 4) full name, confirming if this WhatsApp number (+{self.caller_digits}) is best to reach them on, and their email address (if they say they don\'t have an email address, accept that warmly and continue without an email using `"email":""`). When the details are collected, read them back in one natural spoken sentence (never a bullet list!) and ask the caller to confirm they are right. You can also book a Phone Consultation ("kind":"call"), On-Site Visit ("kind":"visit"), or Roof Inspection ("kind":"inspection") on any open weekday between 9 AM and 4 PM.\n'
                 '- SILENT CRM & BOOKING TAG AT THE END OF YOUR REPLY: ONLY in the turn where the caller explicitly confirms their details (or confirms their appointment time + details), append the exact machine tag at the very end of your text after your final spoken sentence (the voice engine automatically strips `[[...]]` so it is NEVER spoken aloud, and uses it to save the New Lead or Calendar Booking in App OS):\n'
                 '  - For a confirmed New Lead (no specific appointment slot chosen yet):\n'
                 '    [[LEAD:{"name":"Full Name","phone":"Phone","email":"","address":"Street Address","notes":"Bill, solar kW recommendation, roof notes"}]]\n'
                 '  - For a confirmed Appointment Booking (creates both the Calendar Event and CRM Lead):\n'
                 '    [[BOOK:{"kind":"call","day":"YYYY-MM-DD","time":"HH:MM","name":"Full Name","phone":"Phone","email":"","address":"Street Address","notes":"Bill, solar kW recommendation, roof notes"}]]\n'
                 '- 10/10 HUMAN WARMTH & EFFORTLESS DRY WIT: Sound like a sharp, warm, down-to-earth Aussie solar specialist having a real phone chat, never a stiff call-center script or robot. React with natural, relatable human warmth or dry wit when appropriate, and never use robotic filler like "Thank you for sharing that", "That is a great question", "Certainly", or "I would be happy to assist".]']
        return "\n\n".join(p for p in parts if p)

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        def finished(value):
            self.tasks.discard(value)
            if not value.cancelled() and value.exception():
                self.service.metrics['stream_task_errors'] = self.service.metrics.get('stream_task_errors',0)+1
        task.add_done_callback(finished)
        return task

    def state_event(self):
        return json.dumps({'type':'state','state':self.state,'generation':self.generation})

    def change_state(self, state):
        if not self.closed:
            if self.state=='speaking' and state!='speaking':
                self.last_speaking_end=time.monotonic()
                if state=='listening' and self.capture and (time.monotonic() - self.last_speech > 0.9):
                    self.capture.clear();self.voiced=0;self.silence_frames=0;self.capture_extended=False
                    self.close_asr(self.asr);self.asr=None
                    self.discard_early_asr()
            self.state=state;self.service.metrics['state']=state;self.service.state_changed.set()

    def flush(self):
        self.generation += 1
        self.capture_extended = False
        if self.control: self.control.cancel()
        if self.prepared_speech:
            self.prepared_speech[0].cancel();self.prepared_speech=None
        if self.speculative:self.speculative.cancel();self.speculative=None
        self.pending_notice = None
        self.discard_early_asr()
        if self.pending_input:
            self.close_asr(self.pending_input.get('stream'))
            self.close_asr(self.pending_input.get('prepared_asr'))
            self.close_asr(self.pending_input.get('confirmation_asr'))
        self.pending_input = None
        self.playback_until = 0.
        while not self.output.empty(): self.output.get_nowait()
        self.service.metrics['interruptions']=self.service.metrics.get('interruptions',0)+1
        self.change_state('listening')

    def resume_input(self):
        pending=self.pending_input
        if self.state!='thinking' or not pending or pending['generation']!=self.generation:
            return False
        with pending['control'].guard:
            if pending.get('speaking_started') or pending['control'].cancelled.is_set():return False
            previous=pending['pcm']
            self.flush()
            self.capture=bytearray(previous)+self.capture
            if getattr(self.service,'streaming_asr',None):
                self.close_asr(self.asr)
                self.asr=self.service.streaming_asr.new_turn()
                self.asr.feed(bytes(self.capture))
            self.service.metrics['resumed_utterances']=self.service.metrics.get('resumed_utterances',0)+1
            return True

    def transport_lost(self):
        self.flush()
        self.capture.clear();self.pre.clear();self.voiced=self.speech_run=0
        self.silence_frames=0
        self.capture_extended=False
        self.capture_echo_text=''
        self.capture_rejected=False
        self.close_asr(self.asr);self.asr=None
        self.change_state('reconnecting')

    def transport_ready(self):
        self.connected_at=time.monotonic()
        if not self.greeting_pending:self.change_state('listening')

    @staticmethod
    def close_asr(stream):
        if stream and hasattr(stream,'close'):stream.close()

    def discard_early_asr(self):
        if self.early_asr:
            self.close_asr(self.early_asr.get('recognizer'))
            self.close_asr(self.early_asr.get('confirmation'))
            if self.early_asr.get('speculative'):
                try:
                    self.early_asr['speculative'].cancel()
                except Exception:
                    pass
            if self.early_asr.get('control'):
                self.early_asr['control'].cancel()
            self.early_asr=None

    def prepare_recognition(self):
        if not self.prepared_speech and (not self.worker or self.worker.done()):
            self.prepare_speech()
        if self.early_asr:return
        cloud=getattr(self.service,'cloud_asr',None)
        local_fn=getattr(self.service,'recognize',None)
        if not hasattr(cloud,'prepare') and not local_fn:return
        generation=self.generation
        version=self.capture_version
        control=TurnControl(lambda:not self.closed and self.generation==generation
                            and self.service.allowed(self.call))
        pcm=bytes(self.capture)
        remote=getattr(self.asr,'remote',False)
        source=self.asr.recognizer.source if remote else (cloud.primary if cloud else 'local')
        # Start cloud ASR in parallel during the pause so names, addresses, and numbers
        # are transcribed by gpt-4o-mini-transcribe / gemini-2.5-flash with zero extra wait.
        recognizer=None if (remote or not hasattr(cloud,'prepare')) else cloud.prepare(pcm,control)
        early_entry={'recognizer':recognizer,'confirmation':None,
                     'confirmation_source':source,'version':version,'pcm':pcm,
                     'control':control,'local_result':None,'local_done':None,'speculative':None}
        if local_fn and not remote:
            import threading
            done_evt=threading.Event()
            early_entry['local_done']=done_evt
            def _run_early_local():
                try:
                    if control.cancelled.is_set() or self.generation != generation:
                        return
                    res=local_fn(pcm)
                    early_entry['local_result']=res
                    l_text,l_qual=res
                    if (l_text and l_qual.get('decision')=='accepted'
                            and not control.cancelled.is_set()
                            and self.generation==generation
                            and self.capture_version==version):
                        conv=self._get_fallback_conversation()
                        if conv and not self.speculative:
                            spec=conv.prefetch(l_text,control)
                            early_entry['speculative']=spec
                            self.speculative=spec
                    elif (not early_entry.get('recognizer') and hasattr(cloud,'prepare')
                            and not control.cancelled.is_set()
                            and self.generation==generation):
                        early_entry['recognizer']=cloud.prepare(pcm,control)
                except Exception:
                    pass
                finally:
                    done_evt.set()
            threading.Thread(target=_run_early_local,daemon=True).start()
        self.early_asr=early_entry
        self.service.metrics['early_recognitions_started']=self.service.metrics.get('early_recognitions_started',0)+1

    async def _warm_native_conversation(self):
        try:
            await asyncio.to_thread(self._get_fallback_conversation)
        except Exception:
            pass
        if self.customer or not getattr(self.service, 'native_conversation', None):
            return
        try:
            conv = await asyncio.to_thread(self.service.native_conversation, self.call, self.member)
            if self.closed:
                await asyncio.to_thread(conv.close)
            else:
                self.conversation = conv
        except Exception as exc:
            print(f"[StreamPhoneCall] native_conversation init fallback to fast stream: {exc}", flush=True)

    async def start(self):
        self.task(self._warm_native_conversation())
        self.task(self.feed());self.task(self.tick())

    def prepare_speech(self):
        if self.prepared_speech:return
        loop=asyncio.get_running_loop();generation=self.generation
        control=TurnControl(lambda: not self.closed and self.service.allowed(self.call)
                            and self.generation==generation)
        framer=PhonePCM()
        def output(data,final=False):
            control.check()
            future=asyncio.run_coroutine_threadsafe(self.enqueue(data,generation,control,framer,final),loop)
            try:future.result(timeout=30)
            except Exception:future.cancel();raise TurnCancelled()
        speech=FreeSpeech(self.speech_config,output,control,rate=16000)
        self.prepared_speech=(control,speech,framer,output)

    def start_reply(self, audio=None, notice=None, greeting=False):
        if self.worker and not self.worker.done(): return False
        generation = self.generation
        if self.prepared_speech and (
                self.prepared_speech[0].cancelled.is_set()
                or self.prepared_speech[1].error
                or self.prepared_speech[1].done.is_set()):
            self.prepared_speech[0].cancel();self.prepared_speech=None
        self.prepare_speech()
        prepared,self.prepared_speech=self.prepared_speech,None
        speculative,self.speculative=self.speculative,None
        if speculative and audio is None:speculative.cancel();speculative=None
        control=prepared[0]
        prepared[2].source_end=audio.get('speech_ended') if isinstance(audio,dict) else None
        self.control=control
        pending_input=({'pcm':audio['pcm'] if isinstance(audio,dict) else audio,
                        'stream':audio.get('stream') if isinstance(audio,dict) else None,
                        'prepared_asr':audio.get('prepared_asr') if isinstance(audio,dict) else None,
                        'confirmation_asr':audio.get('confirmation_asr') if isinstance(audio,dict) else None,
                        'control':control,'generation':generation,'committed':False} if audio is not None else None)
        self.pending_input=pending_input
        self.change_state('thinking')
        self.worker=self.task(self.reply(audio,notice,greeting,control,generation,prepared,speculative,pending_input))
        return True

    async def reply(self,audio,notice,greeting,control,generation,prepared=None,speculative=None,pending_input=None):
        loop=asyncio.get_running_loop()
        stage='speech_setup'
        framer=PhonePCM()
        spoke_any=False
        def output(data,final=False):
            control.check()
            future=asyncio.run_coroutine_threadsafe(self.enqueue(data,generation,control,framer,final),loop)
            try: future.result(timeout=30)
            except Exception: future.cancel();raise TurnCancelled()
        def run():
            nonlocal framer,output,speculative,stage,spoke_any
            began=time.monotonic()
            if prepared:_,speech,framer,output=prepared
            else:speech=FreeSpeech(self.speech_config,output,control,rate=16000)
            first=None
            hermes_started=None
            timing={}
            self.spoken_text=''
            raw_spoken_text=''
            tag_mode=False
            pending_bracket=''
            def speak(value):
                nonlocal first,stage,spoke_any,raw_spoken_text,tag_mode,pending_bracket
                if first is None:self.spoken_text=''
                raw_spoken_text=(raw_spoken_text+str(value or ''))[-3600:]
                if tag_mode:
                    return
                chunk=pending_bracket+str(value or '')
                pending_bracket=''
                if '[[' in chunk:
                    before_tag,_=chunk.split('[[',1)
                    tag_mode=True
                    chunk=before_tag
                elif chunk.endswith('['):
                    pending_bracket='['
                    chunk=chunk[:-1]
                if not chunk:
                    return
                previous=stage;stage='speech'
                first=first or time.monotonic();spoke_any=True;speech.text(chunk)
                stage=previous
                self.spoken_text=(self.spoken_text+chunk)[-2400:]
            try:
                if greeting:
                    if self.customer:
                        r = routing()
                        c_agent = str(r.get('customer_agent') or '').strip()
                        c_comp = str(r.get('company_name') or '').strip()
                        first_n = self.caller_name.split()[0] if self.caller_name else ''
                        if first_n and c_agent and c_comp:
                            speak(f"Hi {first_n}! Good to hear from you again, it's {c_agent} from {c_comp}. How can I help you out today?")
                        elif first_n and c_agent:
                            speak(f"Hi {first_n}! Good to hear from you again, it's {c_agent}. How can I help you out today?")
                        elif c_agent and c_comp:
                            speak(f"Hi there! {c_agent} speaking from {c_comp}. How can I help you out today?")
                        elif c_agent:
                            speak(f"Hi there! {c_agent} speaking. How can I help you out today?")
                        else:
                            speak('Hello! What can I help you with?')
                    else:
                        speak('Hello! What can I help you with?')
                elif notice:
                    conv = self.conversation or self._get_fallback_conversation()
                    conv.reply(json.dumps(notice,ensure_ascii=False)[:12000],speak,control,notice=True)
                else:
                    stage='recognition'
                    pcm=audio['pcm'] if isinstance(audio,dict) else audio
                    streaming=audio.get('stream') if isinstance(audio,dict) else None
                    cloud=getattr(self.service,'cloud_asr',None)
                    confirmation_asr=audio.get('confirmation_asr') if isinstance(audio,dict) else None
                    if confirmation_asr:control.track(confirmation_asr)
                    used_local = False
                    text, quality = '', {'decision': 'clarify', 'source': 'local'}
                    early = audio.get('prepared_asr') if isinstance(audio, dict) else None
                    if streaming and getattr(streaming,'remote',False):
                        text,quality=streaming.finish(control)
                        if (not text or quality.get('fallback_allowed')) and cloud:
                            text,quality=cloud.recognize(pcm,control)
                    elif getattr(self.service, 'recognize', None):
                        # Fast path (~100ms, or 0ms if completed during early_quiet pause):
                        early_local_done = audio.get('early_local_done') if isinstance(audio, dict) else None
                        early_entry = audio.get('early_entry') if isinstance(audio, dict) else None
                        if early_local_done is not None and early_entry is not None:
                            early_local_done.wait(timeout=1.5)
                        if early_entry and early_entry.get('local_result') is not None:
                            local_text, local_quality = early_entry['local_result']
                        else:
                            local_text, local_quality = self.service.recognize(pcm)
                        if not speculative and early_entry and early_entry.get('speculative'):
                            speculative = early_entry.get('speculative')
                            if self.speculative is speculative:
                                self.speculative = None
                        if not early and early_entry and early_entry.get('recognizer'):
                            early = early_entry.get('recognizer')
                        used_local = True
                        simple_ack_only = bool(
                            local_text
                            and len(local_text.split()) <= 3
                            and re.fullmatch(
                                r"[\s,!.?]*(?:yes|yeah|yep|yup|no|nah|nope|correct|that's\s+right|all\s+correct|everything\s+is\s+correct|sure|okay|ok|thanks|thank\s+you|goodbye|bye)[\s,!.?]*",
                                local_text,
                                flags=re.I,
                            )
                        )
                        confident_local = (
                            bool(local_text)
                            and local_quality.get('decision') == 'accepted'
                            and (
                                not cloud
                                or (
                                    simple_ack_only
                                    and float(local_quality.get('avg_logprob') or -2.0) >= -0.45
                                    and float(local_quality.get('no_speech_prob') or 1.0) <= 0.30
                                )
                            )
                        )
                        if confident_local or not cloud:
                            text, quality = local_text, local_quality
                            if early:
                                self.close_asr(early)
                        else:
                            if speculative:
                                speculative.cancel()
                                speculative = None
                            if early:
                                control.track(early)
                                try:
                                    text, quality = early.finish(control)
                                    self.service.metrics['early_recognitions_used'] = self.service.metrics.get('early_recognitions_used', 0) + 1
                                except Exception:
                                    control.check()
                                    text, quality = cloud.recognize(pcm, control)
                            else:
                                text, quality = cloud.recognize(pcm, control)
                            if (not text or quality.get('decision') != 'accepted') and local_text:
                                text, quality = local_text, local_quality
                    elif cloud:
                        if early:
                            control.track(early)
                            try:
                                text,quality=early.finish(control)
                                self.service.metrics['early_recognitions_used']=self.service.metrics.get('early_recognitions_used',0)+1
                            except Exception:
                                control.check()
                                text,quality=cloud.recognize(pcm,control)
                        else:
                            text,quality=cloud.recognize(pcm,control)
                    control.check()
                    self.service.metrics['last_asr_ms']=round((time.monotonic()-began)*1000)
                    timing['transcription_ms']=self.service.metrics['last_asr_ms']
                    timing['recognition_source']=quality.get('source','local')
                    timing['recognition_decision']=quality.get('decision')
                    self.service.metrics['last_recognition']=quality
                    echo_text=audio.get('echo_text','') if isinstance(audio,dict) else ''
                    normalize=lambda value:' '.join(re.sub(r'[^\w\s]','',value.casefold()).split())
                    candidate=normalize(text)
                    if candidate and echo_text and (len(candidate.split())>=3 or len(candidate)>=8) and (' '+candidate+' ') in (' '+normalize(echo_text)+' '):
                        self.service.metrics['rejected_playback_echoes']=self.service.metrics.get('rejected_playback_echoes',0)+1
                        return
                    filtered = filter_playback_input(text, [echo_text] if echo_text else [])
                    if filtered is None:
                        speak(
                            "Sorry, the line got a bit noisy there. Could you say that again?"
                            if self.customer
                            else 'Speaker audio mixed with your words. Please repeat your request; no new work was started.'
                        )
                        speech.finish(); output(b'', final=True)
                        return 'complete'
                    text = normalize_voice_greeting(filtered)
                    already_extended = isinstance(audio, dict) and bool(audio.get('extended'))
                    if not text:
                        now_mono = time.monotonic()
                        if (quality.get('decision')=='clarify'
                                and len(pcm) >= 16000 * 2 * 2.2
                                and now_mono - self.last_speaking_end >= 2.5
                                and now_mono - self.last_clarify_at >= 18.0):
                            self.last_clarify_at = now_mono
                            speak("Sorry, I missed that. Could you say that again?")
                        else:
                            return
                    elif (not already_extended and len(pcm) < 16000 * 2 * 3.2 and not text.rstrip().endswith(('?', '!'))
                            and re.search(r'\b(um|uh|er|hmm|was|is|are|in|on|at|to|for|with|from|of|my|your|the|a|an|and|or|but|so|because|around|about|like)\W*$',text,re.I)):
                        # Unfinished clause or mid-sentence hesitation gets an opportunity
                        # to continue, rather than dispatching a premature partial turn.
                        return 'continue'
                    from fish_conversation import is_goodbye_intent
                    if is_goodbye_intent(text):
                        speak("Goodbye! Have a great day.")
                        speech.finish()
                        output(b'', final=True)
                        return 'goodbye'
                    else:
                        with control.guard:
                            control.check()
                            if pending_input:pending_input['committed']=True
                        self.service.metrics['user_turns']=self.service.metrics.get('user_turns',0)+1
                        stage='hermes'
                        hermes_started=time.monotonic()
                        if self.customer:
                            prev_asst = str(self.call_turns[-1].get('assistant') or '') if self.call_turns else ''
                            extracted_name = _extract_spoken_caller_name(text, prev_asst)
                            if extracted_name and extracted_name != self.caller_name:
                                is_explicit_correction = bool(re.search(r'\b(correction|actually|not\s+\w+|spelled)\b', text, re.I))
                                if len(extracted_name.split()) >= 2 or len(self.caller_name.split()) < 2 or is_explicit_correction:
                                    self.caller_name = extracted_name
                                    self.caller_memory['name'] = extracted_name
                                    _save_customer_caller_memory(self.caller_digits, self.caller_memory)
                                    if self.fallback_conversation is not None:
                                        self.fallback_conversation.caller_name = extracted_name
                                        self.fallback_conversation.prompt = self._customer_prompt('', None)
                        # Use ultra-low-latency direct streaming Conversation (~220ms first token)
                        # as primary for live phone turn-taking, falling back to native_conversation if needed.
                        conv = self._get_fallback_conversation() or self.conversation
                        def _on_tool_pending(tool_name, caller_text, need_ack):
                            if need_ack and first is None:
                                ack = "All right, let me check that for you now. "
                                speak(ack)
                                return ack
                            return None
                        try:
                            if isinstance(conv, Conversation):
                                conv.reply(text,speak,control,speculative=speculative,on_tool_pending=_on_tool_pending)
                            else:
                                conv.reply(text,speak,control)
                        except TurnCancelled:
                            raise
                        except Exception as conv_exc:
                            control.check()
                            if first is None:
                                if not self.customer and not self.conversation and getattr(self.service, 'native_conversation', None):
                                    try:
                                        self.conversation = self.service.native_conversation(self.call, self.member)
                                    except Exception:
                                        pass
                                if self.conversation and self.conversation is not conv:
                                    self.conversation.reply(text,speak,control)
                                    conv = self.conversation
                                else:
                                    raise conv_exc
                            else:
                                raise conv_exc
                        finally:
                            self.service.metrics['last_conversation_timing']=getattr(conv,'last_timing',{})
                stage='speech'
                speech.finish()
                output(b'',final=True)
                self.service.metrics['last_tts_chunks']=framer.chunks
                self.service.metrics['last_audio_padding_ms']=round(framer.padding/32,2)
                self.service.metrics['last_audio_padding_avoided_ms']=round((framer.previous_padding-framer.padding)/32,2)
                self.service.metrics['spoken_replies']=self.service.metrics.get('spoken_replies',0)+1
                self.service.metrics['last_reply_first_text_ms']=round(((first or began)-began)*1000)
                self.service.metrics['last_reply_first_audio_ms']=round(((speech.first_audio or began)-began)*1000)
                return 'complete'
            finally:
                if audio is not None:
                    end=audio.get('speech_ended') if isinstance(audio,dict) else None
                    timing.update(
                        endpoint_ms=round((began-end)*1000) if end is not None else None,
                        hermes_first_text_ms=round((first-hermes_started)*1000) if first is not None and hermes_started is not None else None,
                        speech_first_audio_ms=round((speech.first_audio-first)*1000) if speech.first_audio is not None and first is not None else None,
                        total_first_audio_ms=round((speech.first_audio-end)*1000) if speech.first_audio is not None and end is not None else None,
                        stage=stage, cancelled=control.cancelled.is_set())
                    self.service.metrics['last_turn_timing']=timing
                    print(json.dumps({'phone_voice_timing':timing}),flush=True)
                    if self.customer:
                        turn_pcm = audio['pcm'] if isinstance(audio, dict) else audio
                        if isinstance(turn_pcm, (bytes, bytearray)) and len(self.call_audio_pcm) < 16000 * 2 * 1800:
                            self.call_audio_pcm.extend(turn_pcm)
                            self.call_audio_pcm.extend(b'\x00' * (16000 * 2 * 15 // 100))
                if greeting or (audio is not None and spoke_any and not control.cancelled.is_set()):
                    caller_utt = '[Call Answered]' if greeting else str(locals().get('text') or '').strip()
                    assistant_utt = self.spoken_text.strip()
                    if caller_utt or assistant_utt:
                        turn_at = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                        self.call_turns.append({
                            'at': turn_at,
                            'caller': caller_utt,
                            'assistant': assistant_utt,
                            'timing': dict(timing),
                        })
                        transcript_snap = {
                            'call_id': self.call,
                            'caller': self.member.get('number'),
                            'caller_name': self.caller_name,
                            'line': 'customer' if self.customer else 'owner',
                            'started_at': self.call_started_at,
                            'turns': list(self.call_turns),
                        }
                        self.service.metrics['last_call_transcript'] = transcript_snap
                        try:
                            with open('/data/last_call_transcript.json', 'w', encoding='utf-8') as tf:
                                json.dump(transcript_snap, tf, ensure_ascii=False, indent=2)
                        except Exception:
                            pass
                        if self.customer:
                            hist = self.caller_memory.setdefault('history', [])
                            if not greeting and caller_utt:
                                hist.append({'role': 'user', 'content': caller_utt, 'at': turn_at})
                            if assistant_utt:
                                hist.append({'role': 'assistant', 'content': assistant_utt, 'at': turn_at})
                            _save_customer_caller_memory(self.caller_digits, self.caller_memory)
                            import threading
                            sync_asst = raw_spoken_text.strip() or assistant_utt
                            threading.Thread(
                                target=_sync_customer_call_turn_to_box3,
                                args=(self.caller_digits, self.caller_name, self.call, len(self.call_turns), caller_utt, sync_asst, turn_at),
                                daemon=True,
                            ).start()
                if pending_input:
                    self.close_asr(pending_input.get('stream'))
                    self.close_asr(pending_input.get('prepared_asr'))
                    self.close_asr(pending_input.get('confirmation_asr'))
                control.cancel()
                if speculative:speculative.cancel()
        try:
            result=await asyncio.to_thread(run)
            if result=='goodbye':
                await asyncio.sleep(1.2)
                await self.close()
                return
            if result=='continue' and not self.closed and self.generation==generation:
                self.capture=bytearray(audio['pcm'] if isinstance(audio,dict) else audio)+self.capture
                self.capture_extended=True
                self.asr=None;self.last_speech=time.monotonic()+.5;self.voiced=max(self.voiced,2)
                if getattr(self.service,'streaming_asr',None):
                    self.asr=self.service.streaming_asr.new_turn();await asyncio.to_thread(self.asr.feed,bytes(self.capture))
            elif result=='complete' and notice and self.generation==generation:
                self.pending_notice={'notice':notice,'generation':generation,'after':self.playback_until+.8}
            if greeting and self.callback_notice and self.generation==generation:
                self.pending_notice=None  # Callback result is announced in the next quiet gap.
        except (TurnCancelled,asyncio.CancelledError): pass
        except Exception as exc:
            self.service.metrics['reply_errors']=self.service.metrics.get('reply_errors',0)+1
            # Log failure location, never transcripts, tokens or provider bodies.
            import traceback
            failure={'stage':stage,'exception':type(exc).__name__,'message':str(exc)[:240],
                     'frames':[{'function':frame.name,'line':frame.lineno}
                               for frame in traceback.extract_tb(exc.__traceback__)[-6:]]}
            self.service.metrics['last_reply_failure']=failure
            print(json.dumps({'phone_voice_reply_failure':failure}),flush=True)
            # Fail only this reply. Never disconnect/replay an accepted action, and never
            # play recovery audio over a reply that already spoke to the caller.
            if (not spoke_any and not self.closed and self.generation==generation and self.connected
                    and self.service.allowed(self.call) and time.monotonic()-self.last_recovery>10
                    and getattr(self.service,'recovery',None)):
                self.last_recovery=time.monotonic()
                recovery=TurnControl(lambda: not self.closed and self.generation==generation
                                     and self.service.allowed(self.call))
                self.control=recovery
                try:
                    pcm=await asyncio.to_thread(self.service.recovery)
                    await self.enqueue(pcm,generation,recovery)
                except Exception:pass
                finally:
                    recovery.cancel()
                    if self.control is recovery:self.control=None
        finally:
            if self.pending_input is pending_input:self.pending_input=None
            if self.control is control: self.control=None
            if not self.closed and self.generation==generation and time.monotonic()>=self.playback_until:
                self.change_state('listening')

    async def enqueue(self,data,generation,control,framer=None,final=True):
        control.check()
        if data and self.pending_input and self.pending_input.get('generation') == generation:
            self.pending_input['speaking_started'] = True
        if self.customer and data and len(self.call_audio_pcm) < 16000 * 2 * 1800:
            self.call_audio_pcm.extend(data)
        framer=framer or PhonePCM()
        for pcm in framer.frames(data,final=final):
            control.check()
            await self.output.put(struct.pack('>Q',generation)+pcm)
            if framer.first_output is None:
                framer.first_output=time.monotonic()
                if framer.source_end:
                    self.service.metrics['last_reply_first_transport_ms_from_speech_end']=round((framer.first_output-framer.source_end)*1000)
            self.playback_until=max(self.playback_until,time.monotonic())+(.45 if final else .06)
            self.change_state('speaking')

    async def feed(self):
        while not self.closed:
            frame=await self.input.get()
            self.received_input=True
            reverse=frame[0]==2
            filtered=self.audio_filter.process(frame[1:],reverse=reverse)
            if reverse:
                # The caller's reverse stream is the audio actually rendered by
                # WhatsApp. It feeds echo cancellation, not recognition.
                if any(frame[1:]):
                    self.rendered_generation=self.generation;self.last_rendered=time.monotonic()
                continue
            speaking,pcm=bool(filtered[0]),filtered[1:]
            now_mono=time.monotonic()
            is_playing=(self.state=='speaking' or self.playback_until>now_mono or (now_mono-self.last_rendered)<.6)
            if speaking and len(pcm) == 1920:
                samples = struct.unpack('<960h', pcm)
                rms = (sum(s * s for s in samples) / 960) ** 0.5 / 32768.0
                if (is_playing or self.state == 'thinking') and rms < 0.032:
                    speaking = False
                elif rms < 0.007 or (self.silence_frames >= 1 and self.speech_run == 0 and rms < 0.011):
                    speaking = False
            self.silence_frames=0 if speaking else min(self.silence_frames+1,20)
            self.service.metrics['processed_frames']=self.service.metrics.get('processed_frames',0)+1
            if self.capture_rejected:
                if speaking:self.last_speech=time.monotonic()
                continue
            if speaking:
                self.speech_run+=1;self.last_user_activity=self.last_speech=time.monotonic();self.voiced+=1
                if self.speech_run>=3 or not self.early_asr:
                    self.capture_version+=1
                    self.discard_early_asr()
                    if self.speculative:self.speculative.cancel();self.speculative=None
                # If the caller continues speaking before audio playback actually starts, resume and merge
                # into the current turn; if audio is already playing, allow clean barge-in.
                if self.speech_run>=4 and self.state=='thinking' and self.pending_input:
                    if not self.pending_input.get('speaking_started'):
                        self.resume_input()
                    elif self.speech_run>=6:
                        self.flush()
                if is_playing and self.speech_run>=6:
                    self.flush()
            else: self.speech_run=0
            if not self.capture:
                if not speaking: self.pre.append(pcm);continue
                if (is_playing or now_mono-self.last_speaking_end<.45) and self.speech_run<4:
                    self.pre.append(pcm);continue
                prefix=b''.join(self.pre);self.capture.extend(prefix);self.pre.clear()
                self.capture_echo_text=self.spoken_text if self.state=='speaking' or time.monotonic()-self.last_rendered<.8 else ''
                if getattr(self.service,'streaming_asr',None):
                    self.asr=self.service.streaming_asr.new_turn()
                    if prefix:await asyncio.to_thread(self.asr.feed,prefix)
            self.capture.extend(pcm)
            if self.asr:
                try:await asyncio.to_thread(self.asr.feed,pcm)
                except Exception:
                    self.close_asr(self.asr);self.asr=None;self.service.metrics['streaming_asr_errors']=self.service.metrics.get('streaming_asr_errors',0)+1
            if len(self.capture)>16000*2*30:
                # Never dispatch a truncated turn that could lose its initial
                # negation. The caller gets a fresh turn without any action.
                self.close_asr(self.asr);self.asr=None
                self.capture.clear();self.voiced=0;self.capture_extended=False
                self.capture_rejected=True;self.pre.clear()
                self.discard_early_asr()
                self.service.metrics['rejected_capture_limit']=self.service.metrics.get('rejected_capture_limit',0)+1

    async def tick(self):
        while not self.closed:
            await asyncio.sleep(.03)
            try:
                now=time.monotonic()
                busy=self.worker and not self.worker.done()
                if self.capture_rejected:
                    if (self.silence_frames>=8 or now-self.last_speech>=.85) and now-self.last_speech>=.65:
                        self.capture_rejected=False
                    continue
                if self.greeting_pending:
                    if self.connected and (self.received_input or (self.connected_at and now - self.connected_at >= .15)) and not busy:
                        self.greeting_pending=False;self.start_reply(greeting=True)
                    continue
                # Start background local recognition + LLM prefetch early (~180ms into a pause)
                # so both ASR and speculative tokens are already in flight at the endpoint.
                early_quiet = (self.silence_frames >= 3 and now - self.last_speech >= .18) or (now - self.last_speech >= .26)
                if self.capture and self.voiced >= 2 and early_quiet and not self.early_asr and not busy:
                    self.prepare_recognition()
                # Adaptive endpointing: give natural mid-sentence pauses (~0.85s) when the draft
                # transcript looks unfinished or very short, and ~0.52s for complete thoughts.
                draft_text = ''
                if self.early_asr and isinstance(self.early_asr.get('local_result'), tuple):
                    draft_text = str(self.early_asr['local_result'][0] or '').strip()
                looks_unfinished = (
                    self.voiced <= 5
                    or bool(
                        draft_text
                        and not draft_text.rstrip().endswith('?')
                        and (
                            len(draft_text.split()) <= 2
                            or re.search(
                                r'\b(was|is|are|am|in|on|at|to|for|with|from|of|by|my|your|our|their|his|her|the|a|an|and|or|but|so|because|if|when|while|around|about|like|live\s+at|located\s+at|name\s+is|number\s+is|address\s+is|uh|um|er|hmm|\d+)\W*$',
                                draft_text,
                                re.I,
                            )
                        )
                    )
                )
                min_quiet = 0.85 if looks_unfinished else 0.52
                min_frames = 12 if looks_unfinished else 8
                quiet_enough = (self.silence_frames >= min_frames and now - self.last_speech >= min_quiet) or (now - self.last_speech >= min_quiet + 0.10)
                if (self.capture and self.voiced>=2 and quiet_enough and self.input.empty() and not busy):
                    if not self.early_asr:
                        self.prepare_recognition()
                    audio={
                        'pcm':bytes(self.capture),
                        'stream':self.asr,
                        'echo_text':self.capture_echo_text,
                        'speech_ended':self.last_speech,
                        'extended':self.capture_extended,
                    }
                    self.asr=None
                    self.capture_extended=False
                    if self.early_asr:
                        early=self.early_asr;self.early_asr=None
                        if early['version']==self.capture_version and audio['pcm']==early['pcm']:
                            audio['prepared_asr']=early.get('recognizer')
                            audio['confirmation_asr']=early.get('confirmation')
                            audio['confirmation_source']=early.get('confirmation_source')
                            audio['early_local_done']=early.get('local_done')
                            audio['early_entry']=early
                        elif early['version']==self.capture_version and audio['pcm'].startswith(early['pcm']):
                            audio['prepared_asr']=early.get('recognizer')
                            audio['confirmation_asr']=early.get('confirmation')
                            audio['confirmation_source']=early.get('confirmation_source')
                            audio['early_local_done']=early.get('local_done')
                            audio['early_entry']=early
                        else:
                            self.close_asr(early.get('recognizer'))
                            self.close_asr(early.get('confirmation'))
                            if early.get('control'):
                                early['control'].cancel()
                    self.capture_echo_text=''
                    self.capture.clear();self.voiced=0
                    self.silence_frames=0
                    self.start_reply(audio=audio);continue
                if (self.capture and self.voiced<2 and now-self.last_speech>=.85 and self.input.empty()):
                    self.capture.clear();self.voiced=0;self.capture_extended=False;self.close_asr(self.asr);self.asr=None
                    self.discard_early_asr()
                if not busy and now>=self.playback_until and self.state not in {'listening','idle'}:
                    self.change_state('listening')
                if (self.pending_notice and not busy and not self.capture and now>=self.pending_notice['after']
                        and self.rendered_generation==self.generation and now-self.last_rendered>=.4):
                    pending=self.pending_notice
                    if pending['generation']==self.generation and self.connected:
                        self.jobs.announced(pending['notice'])
                        if pending['notice']['kind']=='progress':self.last_progress=time.time()
                        if self.callback_notice==pending['notice']:self.callback_notice=None
                        self.service.metrics['background_updates_spoken']=self.service.metrics.get('background_updates_spoken',0)+1
                    self.pending_notice=None
                if busy or self.capture or not self.connected or self.pending_notice or self.state!='listening':continue
                if now-self.last_user_activity<1.5 or now-self.last_speaking_end<.8 or now-self.last_notice<3:continue
                notice=self.callback_notice or self.jobs.notice(time.time(),self.last_progress,
                    **({'since':self.callback_new_tasks_since} if self.callback_new_tasks_since else {}))
                if notice:
                    self.last_notice=now;self.start_reply(notice=notice)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.service.metrics['tick_errors']=self.service.metrics.get('tick_errors',0)+1
                self.service.metrics['last_tick_error']=f"{type(exc).__name__}:{exc}"

    async def close(self):
        self.closed=True
        if self.customer:
            try:
                wav_file = await asyncio.to_thread(_save_customer_call_wav, self.call, self.call_audio_pcm)
                self.caller_memory.setdefault('calls', []).append({
                    'call_id': self.call,
                    'started_at': self.call_started_at,
                    'ended_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    'turns_count': len(self.call_turns),
                    'audio_wav': wav_file,
                    'turns': list(self.call_turns[-20:]),
                })
                await asyncio.to_thread(_save_customer_caller_memory, self.caller_digits, self.caller_memory)
            except Exception:
                pass
        self.close_asr(self.asr);self.asr=None
        self.discard_early_asr()
        if self.pending_input:
            self.close_asr(self.pending_input.get('stream'))
            self.close_asr(self.pending_input.get('prepared_asr'))
            self.close_asr(self.pending_input.get('confirmation_asr'))
        self.pending_input=None
        if self.control:self.control.cancel()
        if self.conversation:
            await asyncio.to_thread(self.conversation.close)
        if self.prepared_speech:self.prepared_speech[0].cancel();self.prepared_speech=None
        if self.speculative:self.speculative.cancel();self.speculative=None
        others = [task for task in self.tasks if task is not asyncio.current_task()]
        for task in others:task.cancel()
        if others:await asyncio.gather(*others,return_exceptions=True)
        while not self.output.empty():self.output.get_nowait()
