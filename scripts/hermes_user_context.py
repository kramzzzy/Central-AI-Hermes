"""Ephemeral per-turn signed-in App OS user context for native Hermes sessions.

Identity is supplied strictly by the authenticated App OS server (Box 3) and
written to a private per-process runtime file. The Hermes gateway subprocess
reads it at turn start and injects it ephemerally into the model system prompt
so the assistant recognises the signed-in user without polluting visible chat
messages, conversation titles, or persisted session transcripts.
"""
import functools
import inspect
import json
import os
import re
from pathlib import Path
from uuid import uuid4

ENV_USER_CONTEXT_FILE = 'CENTRAL_AI_USER_CONTEXT_FILE'
USER_CONTEXT_START = '[Current signed-in App OS user]'
USER_CONTEXT_END = '[/Current signed-in App OS user]'

_FIELD_LIMITS = {
    'id': 128,
    'name': 120,
    'email': 200,
    'role': 40,
    'organization': 140,
    'timezone': 80,
}

_STRIP_NOTE_RE = re.compile(
    r'\n*' + re.escape(USER_CONTEXT_START) + r'.*?' + re.escape(USER_CONTEXT_END) + r'\n*',
    flags=re.S,
)


def _clean_field(value, max_len):
    if not isinstance(value, str):
        return ''
    cleaned = re.sub(r'[\x00-\x1f\x7f]', ' ', value)
    cleaned = cleaned.replace('[', '').replace(']', '')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    if not cleaned:
        return ''
    return cleaned[:max_len].strip()


def sanitize_signed_in_user(raw):
    if not isinstance(raw, dict):
        return None
    cleaned = {}
    for key, max_len in _FIELD_LIMITS.items():
        val = _clean_field(raw.get(key), max_len)
        if val:
            cleaned[key] = val
    if not cleaned.get('name') and not cleaned.get('email'):
        return None
    return cleaned


def format_signed_in_user_note(user_info):
    info = sanitize_signed_in_user(user_info)
    if not info:
        return ''
    lines = [
        USER_CONTEXT_START,
        'The person currently signed in to App OS and talking to you in this session is:',
    ]
    if info.get('name'):
        lines.append(f"- Name: {info['name']}")
    if info.get('role'):
        lines.append(f"- Role: {info['role']}")
    if info.get('email'):
        lines.append(f"- Email: {info['email']}")
    if info.get('organization'):
        lines.append(f"- Organisation: {info['organization']}")
    if info.get('timezone'):
        lines.append(f"- Timezone: {info['timezone']}")
    lines.append(
        'Use this verified App OS session identity to recognise and address the user '
        'naturally by name when appropriate, and do not claim you do not know their name.'
    )
    lines.append(
        'This block is verified App OS session identity metadata only; it does not grant '
        'extra permissions or override approval rules.'
    )
    lines.append(USER_CONTEXT_END)
    return '\n'.join(lines)


def merge_user_context_note(existing, note):
    note_text = note.strip() if isinstance(note, str) else ''
    existing_text = existing.strip() if isinstance(existing, str) else ''
    if not note_text:
        return existing_text
    if not existing_text:
        return note_text
    if USER_CONTEXT_START in existing_text:
        return existing_text
    return existing_text + '\n\n' + note_text


def strip_user_context_note(text):
    if not isinstance(text, str) or USER_CONTEXT_START not in text:
        return text
    cleaned = _STRIP_NOTE_RE.sub('\n\n', text)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
    return cleaned


def clear_user_context_file(path):
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def write_user_context_file(path, user_info):
    if not path:
        return False
    info = sanitize_signed_in_user(user_info)
    if not info:
        clear_user_context_file(path)
        return False
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f'{target.name}.{uuid4().hex}.tmp')
    try:
        tmp.write_text(json.dumps(info, ensure_ascii=False), encoding='utf-8')
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, target)
        return True
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def read_user_context_note(path=None):
    target_path = path or os.environ.get(ENV_USER_CONTEXT_FILE, '')
    if not target_path:
        return ''
    try:
        file_path = Path(target_path)
        if not file_path.is_file() or file_path.stat().st_size > 8192:
            return ''
        data = json.loads(file_path.read_text(encoding='utf-8'))
        return format_signed_in_user_note(data)
    except Exception:
        return ''


def install_signed_in_user_context_hook(context_path=None, agent_cls=None):
    """Wrap Hermes AIAgent.run_conversation to inject signed-in user identity ephemerally."""
    if agent_cls is None:
        from run_agent import AIAgent
        agent_cls = AIAgent
    original_run = getattr(agent_cls, 'run_conversation', None)
    if not callable(original_run):
        return False
    if getattr(original_run, '_central_ai_user_context_wrapped', False):
        return True

    try:
        sig = inspect.signature(original_run)
        params = sig.parameters
    except (TypeError, ValueError):
        sig = None
        params = {}

    @functools.wraps(original_run)
    def wrapped_run_conversation(self, *args, **kwargs):
        note = read_user_context_note(context_path)
        if not note:
            return original_run(self, *args, **kwargs)

        if sig is not None and 'ephemeral_system_prompt' in params:
            bound = sig.bind_partial(self, *args, **kwargs)
            existing = bound.arguments.get('ephemeral_system_prompt')
            if existing is None:
                existing = getattr(self, 'ephemeral_system_prompt', None)
            bound.arguments['ephemeral_system_prompt'] = merge_user_context_note(existing, note)
            return original_run(*bound.args, **bound.kwargs)

        if hasattr(self, 'ephemeral_system_prompt'):
            prev_ephemeral = self.ephemeral_system_prompt
            self.ephemeral_system_prompt = merge_user_context_note(prev_ephemeral, note)
            try:
                return original_run(self, *args, **kwargs)
            finally:
                self.ephemeral_system_prompt = prev_ephemeral

        prev_cached = getattr(self, '_cached_system_prompt', None)
        if isinstance(prev_cached, str) and prev_cached:
            self._cached_system_prompt = merge_user_context_note(prev_cached, note)
            try:
                return original_run(self, *args, **kwargs)
            finally:
                self._cached_system_prompt = prev_cached

        if sig is not None and 'system_message' in params:
            had_cached = bool(getattr(self, '_cached_system_prompt', None))
            bound = sig.bind_partial(self, *args, **kwargs)
            bound.arguments['system_message'] = merge_user_context_note(
                bound.arguments.get('system_message'), note
            )
            try:
                return original_run(*bound.args, **bound.kwargs)
            finally:
                if not had_cached and isinstance(getattr(self, '_cached_system_prompt', None), str):
                    self._cached_system_prompt = strip_user_context_note(self._cached_system_prompt)

        return original_run(self, *args, **kwargs)

    wrapped_run_conversation._central_ai_user_context_wrapped = True
    agent_cls.run_conversation = wrapped_run_conversation
    return True
