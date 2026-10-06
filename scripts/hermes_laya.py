"""Hermes-owned read-only decision tool; also exposed through native MCP."""
import json
import os
import threading
import time
import urllib.request
from pathlib import Path

CATEGORIES = {
    'Personal assistant': 'Scheduling, planning and administration.',
    'Social media': 'Writing social media posts and marketing content.',
    'Lead generation': 'Prospective customers asking for quotes or pricing.',
    'Customer service': 'Existing customers needing help or repairs.',
}
_health_lock = threading.Lock()
_health_at, _healthy = 0, False


def configured():
    return bool(os.environ.get('LAYA_URL') and (os.environ.get('LAYA_API_KEY') or
        Path(os.environ.get('LAYA_API_KEY_FILE', '/nonexistent')).is_file()))


def api_key():
    return os.environ.get('LAYA_API_KEY', '').strip() or Path(os.environ['LAYA_API_KEY_FILE']).read_text().strip()


def available():
    global _health_at, _healthy
    if not configured():
        return False
    with _health_lock:
        if time.monotonic() - _health_at > 5:
            try:
                key = api_key()
                request = urllib.request.Request(os.environ['LAYA_URL'].rstrip('/') + '/health',
                    headers={'Authorization': 'Bearer ' + key})
                with urllib.request.urlopen(request, timeout=2) as response:
                    health = json.load(response)
                _healthy = health.get('status') == 'ok' and 'english' in health.get('loaded', [])
            except Exception:
                _healthy = False
            _health_at = time.monotonic()
        return _healthy


def classify(text: str) -> dict:
    """Classify a request for review. This tool does not execute actions."""
    if not isinstance(text, str) or not 5 <= len(text.strip()) <= 4000 or not configured():
        raise ValueError('A connected decision tool and bounded request are required')
    payload = {'state': text.strip(), 'max_len': 1024,
               'questions': {'category': {'type': 'choice', 'instructions': 'Which team should review this request?', 'criteria': CATEGORIES}}}
    key = api_key()
    request = urllib.request.Request(os.environ['LAYA_URL'].rstrip('/') + '/v1/systemone',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
    answer = result['answers']['category']
    confidence = answer['answer_confidence']
    truncated = result['usage']['truncated']
    model = result['routing']['model']
    if answer['choice'] not in CATEGORIES or type(confidence) not in (int, float) or not 0 <= confidence <= 1 or type(truncated) is not bool or model not in ('english', 'multilingual'):
        raise ValueError('Invalid decision response')
    return {'category': answer['choice'], 'confidence': confidence, 'model': 'laya-' + model,
            'needs_review': model == 'multilingual' or confidence < 0.8 or truncated}
