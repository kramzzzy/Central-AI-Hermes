"""Shared Fish client task protocol and native bridge; no audio/RTC imports."""
import json
import re
import threading
import time
import urllib.request
from uuid import uuid4

TOOL = {
    'tool_type': 'client', 'name': 'native_leo',
    'description': 'Start an independent Central AI background task with normal permissions and approvals. Returns acceptance and task_id immediately, not the task result. Keep conversing; completion and progress arrive separately. Do not retry uncertain actions.',
    'arguments': [{'name': 'request', 'description': 'Complete English request with the caller intent and relevant details. Preserve negation, recipients and corrections. For a previously uncertain action ask to check its status before doing anything again.'},
                  {'name': 'label', 'description': 'Short task name, e.g. weekly report or meeting preparation. Maximum 100 characters.'}],
    'expects_response': True, 'timeout_seconds': 10,
}

TASK_TOOLS = [TOOL, {
    'tool_type': 'client', 'name': 'task_status',
    'description': 'Read the status or actual result of background work without starting or repeating it. Use an empty task_id to list tasks.',
    'arguments': [{'name': 'task_id', 'description': 'Exact task_id from acceptance or task list; empty string lists tasks.'}],
    'expects_response': True, 'timeout_seconds': 10,
}, {
    'tool_type': 'client', 'name': 'cancel_task',
    'description': 'Cancel one exact background task only when the caller explicitly requests that cancellation. Stopping speech or taking another request does not cancel tasks.',
    'arguments': [{'name': 'task_id', 'description': 'Exact task_id from acceptance or task list. Ask which task if ambiguous.'}],
    'expects_response': True, 'timeout_seconds': 10,
}, {
    'tool_type': 'client', 'name': 'task_callback',
    'description': 'Only after the current caller explicitly asks: enable or disable one single-attempt callback for an exact existing task. It calls that caller only, after hangup and an actual result. Mark Tech and Michael have separate tasks and fixed recipients. Never automatic for ordinary tasks.',
    'arguments': [{'name': 'task_id', 'description': 'Exact task_id already accepted for the current caller. Ask which task if unclear.'},
                  {'name': 'mode', 'description': 'enable when the current caller explicitly asks to call them when finished; disable when they explicitly withdraw that callback. Work is unchanged.'}],
    'expects_response': True, 'timeout_seconds': 10,
}]

def is_correction(text):
    if re.match(r'^(no problem|no worries|not a problem)\b', text.strip(), re.I):
        return False
    return bool(re.search(r"^(?:no\b|not\b|stop\b|wait\b|hold on\b|i meant\b)|"
        r"\b(?:that(?:'s| is)|this is|you(?:'re| are)) (?:incorrect|wrong)|"
        r"\b(?:misheard|mishearing|not what i (?:said|meant))\b", text.strip(), re.I))

def is_task_cancellation(text):
    if re.search(r"\b(?:do not|don't|dont|never|not to)\s+(?:\w+\s+){0,2}(?:cancel|stop|abandon|halt)\b", text, re.I):
        return False
    if re.search(r'\bcallback\b', text, re.I) and not re.search(
            r'\bcancel (?:the |that |this |my )?(?:task|job|report|work)\b', text, re.I):
        return False
    return bool(re.search(r'\b(cancel|abandon)\b|\bstop (?:working on|processing|running)\b|'
        r'\b(?:stop|halt) (?:the|that|this|my) (?:task|job|report|work)\b', text, re.I))

class NativeTaskBridge:
    """One submission per Fish callId; cancellation fences a pending submission."""
    def __init__(self, call, native, allowed, timeout=100):
        self.call, self.native, self.allowed = call, native, allowed
        self.timeout = timeout
        self.lock = threading.RLock()
        self.records = {}
        self.requests = {}
        self.closed = False
        self.completed = self.failed = self.interrupted = 0

    def cancel(self, close=False):
        with self.lock:
            self.closed |= close
            records = list(self.records.values())
            for record in records:
                if not record['done'].is_set():
                    record['cancel'].set()
        for record in records:
            with record['fence']:
                if record['started'] and not record['settled'] and not record['stopped']:
                    record['stopped'] = True
                    try:
                        self.native('voice_stop', self.call, turn_id=record['turn'])
                    except Exception:
                        pass  # No resubmission after an uncertain stop.

    def execute(self, event):
        call_id = event.get('callId')
        params = event.get('params')
        request = params.get('request') if isinstance(params, dict) else None
        if (event.get('toolName') != 'native_leo' or event.get('expectsResponse') is not True
                or not isinstance(call_id, str) or not 1 <= len(call_id) <= 160
                or not isinstance(request, str) or not request.strip() or len(request) > 9000
                or request.lstrip().startswith('/')):
            return {'type': 'client_tool.result', 'callId': call_id,
                    'isError': True, 'result': {'status': 'invalid_request'}}
        with self.lock:
            previous = self.records.get(call_id)
            if previous:
                # Reliable delivery can duplicate a packet. Never execute it again.
                return previous.get('result')
            if self.closed or not self.allowed(self.call) or len(self.records) >= 128:
                return {'type': 'client_tool.result', 'callId': call_id, 'isError': True,
                        'result': {'status': 'inactive_or_task_limit'}}
            normalized = ' '.join(request.casefold().split())
            previous = self.requests.get(normalized)
            if previous:
                response = previous.get('result')
                if response:
                    return {**response, 'callId': call_id, 'result': {**response['result'],
                        'previously_dispatched': True,
                        'instruction': 'This is the earlier request result, not a new execution. Do not repeat an uncertain action. An explicitly requested fresh check must first check actual status.'}}
                return {'type': 'client_tool.result', 'callId': call_id, 'isError': True,
                        'result': {'status': 'same_request_already_dispatched', 'retry_action': False}}
            if any(not r['done'].is_set() for r in self.records.values()):
                response = {'type': 'client_tool.result', 'callId': call_id, 'isError': True,
                            'result': {'status': 'previous_task_still_settling', 'retry_action': False}}
                settled = threading.Event(); settled.set()
                self.records[call_id] = {'done': settled, 'started': False, 'settled': True,
                    'stopped': False, 'fence': threading.Lock(), 'result': response}
                return response
            record = {'turn': str(uuid4()), 'cancel': threading.Event(), 'done': threading.Event(),
                      'fence': threading.Lock(), 'started': False, 'settled': False, 'stopped': False}
            self.records[call_id] = record
            self.requests[normalized] = record
        result = {'status': 'unavailable', 'retry_action': False,
                  'message': 'The task did not finish. Check its status before attempting the action again.'}
        try:
            with record['fence']:
                with self.lock:
                    if self.closed or record['cancel'].is_set() or not self.allowed(self.call):
                        raise InterruptedError()
                    # Mark uncertainty before crossing the RPC boundary.
                    record['started'] = True
                submitted = self.native('voice_submit', self.call, turn_id=record['turn'],
                    text='[Task delegated from the live WhatsApp conversation. Complete the requested work in English. '
                         'Create the full requested report or artifact when needed; include a useful result summary. '
                         'Use the existing permissions and approval flow. Do not automatically repeat an uncertain action.]\n'
                         + request.strip())
            cursor, reply, completed, review = submitted['cursor'], '', False, False
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if record['cancel'].is_set() or self.closed or not self.allowed(self.call):
                    raise InterruptedError()
                events = self.native('voice_events', self.call, turn_id=record['turn'],
                    epoch=submitted['epoch'], cursor=cursor)
                cursor = events['cursor']
                for item in events.get('events', []):
                    kind, payload = item.get('type'), item.get('payload') or {}
                    if kind == 'message.delta':
                        reply += payload.get('text', '')
                    elif kind == 'message.complete':
                        if payload.get('status') and payload['status'] != 'complete':
                            raise RuntimeError('Task interrupted')
                        reply = payload.get('text') or reply
                        completed = True
                    elif kind in {'error', 'turn.error', 'turn.interrupted'}:
                        raise RuntimeError('Task failed')
                review = bool(events.get('review'))
                if review or (completed and not events.get('running')):
                    break
            with record['fence']:
                if record['cancel'].is_set():
                    raise InterruptedError()
                if completed and not review:
                    self.native('voice_finish', self.call, turn_id=record['turn'])
                    record['settled'] = True
                    self.completed += 1
                    result = {'status': 'complete', 'message': reply[:12000], 'retry_action': False}
                elif review:
                    # Leave actual approval resolution to the native channel.
                    self.native('voice_stop', self.call, turn_id=record['turn'])
                    record['stopped'] = True
                    result = {'status': 'approval_required', 'message': reply[:8000],
                              'instruction': 'This requires approval in the private native phone conversation. Do not claim it is approved or completed.',
                              'retry_action': False}
                else:
                    self.failed += 1
        except InterruptedError:
            self.interrupted += 1
            result = {'status': 'interrupted', 'message': 'The request was interrupted; an action may already have happened. Check status before a new attempt.', 'retry_action': False}
        except Exception:
            self.failed += 1
        finally:
            with record['fence']:
                if record['started'] and not record['settled'] and not record['stopped']:
                    record['stopped'] = True
                    try:
                        self.native('voice_stop', self.call, turn_id=record['turn'])
                    except Exception:
                        pass
            response = {'type': 'client_tool.result', 'callId': call_id, 'result': result}
            with self.lock:
                record['result'] = response
                record['done'].set()
        return response

def fish_api(config, path, body=None, method=None):
    request = urllib.request.Request('https://api.fish.audio/v1/agent/' + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Authorization': 'Bearer ' + config['FISH_API_KEY'], 'Content-Type': 'application/json'},
        method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response) if response.status != 204 else None

def conversation_llm(config):
    """Explicit low-cost voice routing; reject arbitrary models and expensive fallbacks."""
    model = config.get('FISH_LLM_MODEL', '').strip()
    if not model:
        return {'custom': None}
    if model not in {'openai/gpt-4o-mini', 'openai/gpt-4.1-mini', '@preset/leo-live-voice-20261003'}:
        raise ValueError('Unsupported WhatsApp conversation model')
    openai_key = config.get('OPENAI_API_KEY', '').strip()
    if openai_key.startswith('sk-') and not openai_key.startswith('sk-or-') and model != '@preset/leo-live-voice-20261003':
        openai_model = model.replace('openai/', '')
        return {'custom': {'base_url': 'https://api.openai.com/v1', 'model': openai_model, 'api_key': openai_key}}
    key = config.get('OPENROUTER_API_KEY', '').strip()
    if not key.startswith('sk-or-'):
        raise ValueError('OpenRouter conversation credential is missing')
    return {'custom': {'base_url': 'https://openrouter.ai/api/v1', 'model': model, 'api_key': key}}
