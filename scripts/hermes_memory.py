"""Authenticated, bounded service-only memory completions and read-only status."""
import hmac
import json
import os
import subprocess
import threading
from pathlib import Path
from urllib.request import Request, urlopen
from central_ai_integrations import memory_config

_slots = threading.BoundedSemaphore(1)


def authorized(headers):
    path = os.environ.get('HERMES_MEMORY_TOKEN_FILE', '')
    if not path or headers.get('Origin'):
        return False
    token = Path(path).read_text(encoding='utf-8').strip()
    return len(token) >= 32 and hmac.compare_digest(headers.get('Authorization', ''), 'Bearer ' + token)


def validate(body):
    if not isinstance(body, dict) or body.get('stream', False) is not False:
        raise ValueError('Non-streaming memory completion required')
    messages = body.get('messages')
    if not isinstance(messages, list) or not 1 <= len(messages) <= 150:
        raise ValueError('Invalid memory messages')
    for message in messages:
        if not isinstance(message, dict) or message.get('role') not in {'system', 'developer', 'user', 'assistant', 'tool'}:
            raise ValueError('Invalid message role')
        if message.get('content') is not None and not isinstance(message['content'], str):
            raise ValueError('Memory accepts text only')
    tools = body.get('tools')
    if tools is not None and (not isinstance(tools, list) or len(tools) > 32 or
            any(not isinstance(t, dict) or t.get('type') != 'function' or not isinstance(t.get('function'), dict) for t in tools)):
        raise ValueError('Invalid memory tool schemas')
    fmt = body.get('response_format')
    if fmt is not None and (not isinstance(fmt, dict) or fmt.get('type') not in {'json_object', 'json_schema', 'text'}):
        raise ValueError('Invalid memory output format')
    # Ignore all endpoint, credential, model and generation overrides.
    return {key: body[key] for key in ('messages', 'tools', 'tool_choice', 'response_format') if key in body}


def complete(settings, root, body):
    from hermes_profile import profile_environment
    prepared = validate(body)
    if not _slots.acquire(timeout=5):
        return 429, {'error': {'message': 'Memory processor busy', 'type': 'rate_limit_error'}}
    try:
        python_bin = settings.get('HERMES_PYTHON') or __import__('os').environ.get('HERMES_PYTHON') or __import__('sys').executable
        result = subprocess.run([python_bin, str(root / 'scripts' / 'hermes-memory-entry.py')],
            input=json.dumps(prepared), capture_output=True, text=True, encoding='utf-8',
            timeout=85, env=profile_environment(settings), cwd=root)
        data = json.loads(result.stdout)
        return (503 if result.returncode or 'error' in data else 200), data
    except Exception:
        return 503, {'error': {'message': 'Memory processor unavailable', 'type': 'server_error'}}
    finally:
        _slots.release()


def read_bank(home, include_memories=False):
    """Only the active profile's fixed bank. No browser-selected URL or bank."""
    cfg_path = home / 'hindsight' / 'config.json'
    if not cfg_path.is_file():
        return None
    try:
        cfg = json.loads(cfg_path.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return {'available': False, 'bank': '', 'background_recall': False,
            'error': 'Memory configuration could not be read; existing native files are preserved.'}
    if not isinstance(cfg, dict):
        return {'available': False, 'bank': '', 'background_recall': False}
    cfg = memory_config(cfg)
    bank = cfg.get('bank_id', '')
    result = {'available': False, 'bank': bank, 'background_recall': not cfg.get('recall_sync', True)}
    if cfg.get('mode') != 'local_external' or cfg.get('api_url') != 'http://hindsight:8888' or bank != 'michael-os-leo':
        return result
    token = cfg.get('api_key', '')
    def get(path):
        request = Request(cfg['api_url'] + path, headers={'Authorization': 'Bearer ' + token})
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read(1048576))
    try:
        stats = get('/v1/default/banks/' + bank + '/stats?refresh=true')
        result.update(available=True, memories_count=stats.get('total_nodes', 0),
            documents=stats.get('total_documents', 0), pending=stats.get('pending_operations', 0),
            failed=stats.get('failed_operations', 0), observations=stats.get('total_observations', 0))
        if include_memories:
            result['memories'] = get('/v1/default/banks/' + bank + '/memories/list?limit=50')
    except Exception:
        result['available'] = False
        result['error'] = 'Memory service unavailable; existing native files are preserved.'
    return result
