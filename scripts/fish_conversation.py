"""Shared Fish client task protocol and native bridge; no audio/RTC imports."""
import json
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from uuid import uuid4

TOOL = {
    'tool_type': 'client', 'name': 'native_leo',
    'description': 'Start an independent Central AI background task with normal permissions and approvals. Returns acceptance and task_id immediately, not the task result. Keep conversing; completion and progress arrive separately. Do not retry uncertain actions.',
    'arguments': [{'name': 'request', 'description': 'Complete English request with the caller intent and relevant details. Preserve negation, recipients and corrections. For a previously uncertain action ask to check its status before doing anything again.'},
                  {'name': 'label', 'description': 'Short task name, e.g. weekly report or meeting preparation. Maximum 100 characters.'}],
    'expects_response': True, 'timeout_seconds': 10,
}

ADD_KNOWLEDGE_TOOL = {
    'tool_type': 'client', 'name': 'add_knowledge',
    'description': 'Save, enhance, or add a new entry to the Central AI Knowledge Base in PostgreSQL and long-term memory. Use whenever the caller asks to add information, note something down, remember business rules, or enhance the knowledge base.',
    'arguments': [
        {'name': 'title', 'description': 'Descriptive title for this knowledge base entry.'},
        {'name': 'content', 'description': 'Full knowledge content, facts, notes, or instructions to save.'},
        {'name': 'category', 'description': 'Optional category (e.g. Clients, Operations, Policies, General, Technical).'}
    ],
    'expects_response': True, 'timeout_seconds': 12,
}

SEARCH_KNOWLEDGE_TOOL = {
    'tool_type': 'client', 'name': 'search_knowledge',
    'description': 'Search and retrieve entries from the Central AI Knowledge Base. Use whenever the caller asks about documented procedures, past notes, company information, or what is in the knowledge base.',
    'arguments': [
        {'name': 'query', 'description': 'Keywords or search terms to look up in the knowledge base.'}
    ],
    'expects_response': True, 'timeout_seconds': 10,
}

WORKSPACE_OVERVIEW_TOOL = {
    'tool_type': 'client', 'name': 'get_workspace_overview',
    'description': 'Read live Central AI workspace statistics: number of knowledge base entries, total tasks, and registered contacts.',
    'arguments': [],
    'expects_response': True, 'timeout_seconds': 10,
}

WHATSAPP_SEND_TOOL = {
    'tool_type': 'client', 'name': 'send_whatsapp_message',
    'description': 'Send a WhatsApp message or chat to any phone number or contact name. Use whenever the caller asks you to message, text, or chat someone on WhatsApp.',
    'arguments': [{'name': 'recipient', 'description': 'Phone number with country code (e.g. +61400111222 or +1234567890) or contact name.'},
                  {'name': 'message', 'description': 'The exact message content to send via WhatsApp.'}],
    'expects_response': True, 'timeout_seconds': 15,
}

WHATSAPP_CALL_TOOL = {
    'tool_type': 'client', 'name': 'call_whatsapp_contact',
    'description': 'Place an outbound WhatsApp phone call to a contact or phone number. Use whenever the caller asks you to call or ring someone on WhatsApp.',
    'arguments': [{'name': 'recipient', 'description': 'Phone number or contact name to call on WhatsApp.'},
                  {'name': 'reason', 'description': 'Optional topic or purpose of the call.'}],
    'expects_response': True, 'timeout_seconds': 15,
}

LIVE_WEATHER_TOOL = {
    'tool_type': 'client', 'name': 'get_live_weather',
    'description': 'Read realtime live weather, solar irradiance (W/m²), UV index, and 7-day forecast from the Central AI meteorological ground truth without external searching.',
    'arguments': [
        {'name': 'location', 'description': 'Optional city name (e.g. Brisbane, Gold Coast, Sydney, Melbourne, Perth, London, Tokyo). Defaults to Brisbane HQ.'},
        {'name': 'unit', 'description': 'Temperature unit, C or F (default C).'}
    ],
    'expects_response': True, 'timeout_seconds': 10,
}

CONTROL_WIDGET_TOOL = {
    'tool_type': 'client', 'name': 'control_widget',
    'description': 'Control App OS screen widgets on the user display (weather, website, contacts, report, tasks). Can open, close, close_all, or navigate to a URL.',
    'arguments': [
        {'name': 'action', 'description': 'Action to perform: open, close, close_all, or navigate.'},
        {'name': 'widget', 'description': 'Target widget: weather, website, report, contacts, tasks, images, videos, or all.'},
        {'name': 'url', 'description': 'Optional URL when opening or navigating website.'}
    ],
    'expects_response': True, 'timeout_seconds': 10,
}

END_CALL_TOOL = {
    'tool_type': 'client', 'name': 'end_call',
    'description': 'Immediately hang up / end the current voice call when the user says goodbye or asks to end the call.',
    'arguments': [
        {'name': 'reason', 'description': 'Optional reason, e.g. user_goodbye.'}
    ],
    'expects_response': True, 'timeout_seconds': 10,
}

TASK_TOOLS = [
    TOOL,
    ADD_KNOWLEDGE_TOOL,
    SEARCH_KNOWLEDGE_TOOL,
    WORKSPACE_OVERVIEW_TOOL,
    LIVE_WEATHER_TOOL,
    CONTROL_WIDGET_TOOL,
    END_CALL_TOOL,
    WHATSAPP_SEND_TOOL,
    WHATSAPP_CALL_TOOL,
    {
        'tool_type': 'client', 'name': 'task_status',
        'description': 'Read the status or actual result of background work without starting or repeating it. Use an empty task_id to list tasks.',
        'arguments': [{'name': 'task_id', 'description': 'Exact task_id from acceptance or task list; empty string lists tasks.'}],
        'expects_response': True, 'timeout_seconds': 10,
    },
    {
        'tool_type': 'client', 'name': 'cancel_task',
        'description': 'Cancel one exact background task only when the caller explicitly requests that cancellation. Stopping speech or taking another request does not cancel tasks.',
        'arguments': [{'name': 'task_id', 'description': 'Exact task_id from acceptance or task list. Ask which task if ambiguous.'}],
        'expects_response': True, 'timeout_seconds': 10,
    },
    {
        'tool_type': 'client', 'name': 'task_callback',
        'description': 'Only after the current caller explicitly asks: enable or disable one single-attempt callback for an exact existing task. It calls that caller only, after hangup and an actual result. Mark Tech and Michael have separate tasks and fixed recipients. Never automatic for ordinary tasks.',
        'arguments': [{'name': 'task_id', 'description': 'Exact task_id already accepted for the current caller. Ask which task if unclear.'},
                      {'name': 'mode', 'description': 'enable when the current caller explicitly asks to call them when finished; disable when they explicitly withdraw that callback. Work is unchanged.'}],
        'expects_response': True, 'timeout_seconds': 10,
    }
]

def is_goodbye_intent(text, assistant_name='Leo'):
    if not text:
        return False
    trimmed = text.strip()
    name = (assistant_name or 'Leo').strip()
    escaped_name = re.escape(name)
    if re.search(r'\b(?:end\s*(?:the\s*)?call|hang\s*up|disconnect\s*(?:the\s*)?call|drop\s*(?:the\s*)?call)\b', trimmed, re.I):
        return True
    if re.search(rf'\b(?:goodbye|good\s*bye|bye\s*bye|bye|farewell|see\s*you|talk\s*later)\s+{escaped_name}\b', trimmed, re.I):
        return True
    if re.search(rf'\b{escaped_name}\s+(?:goodbye|good\s*bye|bye|hang\s*up|end\s*(?:the\s*)?call)\b', trimmed, re.I):
        return True
    if re.match(r'^(?:goodbye|good\s*bye|bye\s*bye|bye|end\s*call|hang\s*up)[.!?]*$', trimmed, re.I):
        return True
    return False

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

def call_app_os_mcp(tool_name, arguments, timeout=10):
    url = (os.environ.get('APP_OS_URL') or 'http://web:3000').rstrip('/')
    api_key = os.environ.get('HERMES_API_KEY', '')
    req_body = {
        'jsonrpc': '2.0',
        'id': str(uuid4()),
        'method': 'tools/call',
        'params': {
            'name': tool_name,
            'arguments': arguments
        }
    }
    candidates = [url, 'http://127.0.0.1:3000', 'http://web:3000']
    for base in candidates:
        try:
            req = urllib.request.Request(
                f"{base}/api/mcp",
                data=json.dumps(req_body).encode('utf-8'),
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f"Bearer {api_key}" if api_key else ''
                }
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if 'result' in data:
                    return data['result']
                if 'error' in data:
                    return {'error': data['error'].get('message', 'MCP error')}
        except Exception:
            continue
    return {'error': 'Could not reach Central AI App OS MCP endpoint.'}

def call_whatsapp_action(endpoint, payload, timeout=10):
    caller_urls = [
        os.environ.get('CALLER_URL', 'http://caller:8080'),
        'http://127.0.0.1:8080',
        'http://whatsapp-connector:8080',
        'http://meowcaller:8080'
    ]
    for base in caller_urls:
        try:
            req = urllib.request.Request(
                f"{base}{endpoint}",
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except Exception:
            continue
    return {'error': 'WhatsApp caller connector unreachable on Box 1.'}

def resolve_contact_target(target):
    digits = re.sub(r'[^\d]', '', target)
    if digits and len(digits) >= 7:
        return digits, target
    for cf in ['/data/contacts.json', str(Path.home() / '.runtime' / 'contacts.json'), 'data/contacts.json']:
        if os.path.exists(cf):
            try:
                cdata = json.loads(Path(cf).read_text(encoding='utf-8'))
                for c in cdata.get('contacts', []):
                    if target.lower() in c.get('name', '').lower() or target.lower() in c.get('role', '').lower():
                        c_num = c.get('phone_number', '') or c.get('number', '')
                        if c_num:
                            return c_num, c.get('name', target)
            except Exception:
                pass
    return target, target

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
        name = event.get('toolName')
        params = event.get('params') or {}

        if not isinstance(call_id, str) or not 1 <= len(call_id) <= 160 or event.get('expectsResponse') is not True:
            return {'type': 'client_tool.result', 'callId': call_id, 'isError': True,
                    'result': {'status': 'invalid_request'}}

        if name == 'add_knowledge':
            title = str(params.get('title') or 'Voice Knowledge Entry').strip()
            content = str(params.get('content') or '').strip()
            category = str(params.get('category') or 'General').strip()
            if not content:
                return {'type': 'client_tool.result', 'callId': call_id, 'isError': True,
                        'result': {'status': 'error', 'message': 'Content is required.'}}
            mcp_res = call_app_os_mcp('central_ai_add_knowledge', {'title': title, 'content': content, 'category': category})
            success = mcp_res.get('success', False)
            return {'type': 'client_tool.result', 'callId': call_id, 'isError': not success,
                    'result': mcp_res}

        if name == 'search_knowledge':
            query = str(params.get('query') or '').strip()
            mcp_res = call_app_os_mcp('central_ai_search_knowledge', {'query': query})
            return {'type': 'client_tool.result', 'callId': call_id,
                    'result': mcp_res}

        if name == 'get_workspace_overview':
            mcp_res = call_app_os_mcp('central_ai_get_workspace_overview', {})
            return {'type': 'client_tool.result', 'callId': call_id,
                    'result': mcp_res}

        if name in {'send_whatsapp_message', 'send_message'}:
            recipient = str(params.get('recipient') or params.get('target') or params.get('phone_or_name') or '').strip()
            message = str(params.get('message') or '').strip()
            target_num, target_name = resolve_contact_target(recipient)
            res = call_whatsapp_action('/send', {'target': target_num, 'name': target_name, 'message': message})
            return {'type': 'client_tool.result', 'callId': call_id, 'isError': 'error' in res,
                    'result': res}

        if name in {'call_whatsapp_contact', 'call_contact'}:
            recipient = str(params.get('recipient') or params.get('target') or params.get('phone_or_name') or '').strip()
            reason = str(params.get('reason') or '').strip()
            target_num, target_name = resolve_contact_target(recipient)
            res = call_whatsapp_action('/call', {'target': target_num, 'name': target_name, 'reason': reason})
            return {'type': 'client_tool.result', 'callId': call_id, 'isError': 'error' in res,
                    'result': res}

        if name == 'get_live_weather':
            loc = str(params.get('location') or 'brisbane').strip()
            unit = str(params.get('unit') or 'C').strip()
            mcp_res = call_app_os_mcp('central_ai_get_live_weather', {'location': loc, 'unit': unit})
            return {'type': 'client_tool.result', 'callId': call_id,
                    'result': mcp_res}

        if name == 'control_widget':
            act = str(params.get('action') or 'open').strip()
            widget = str(params.get('widget') or ('current' if act == 'close' else 'website')).strip()
            url = str(params.get('url') or '').strip()
            mcp_res = call_app_os_mcp('central_ai_control_widget', {'action': act, 'widget': widget, 'url': url})
            return {'type': 'client_tool.result', 'callId': call_id,
                    'result': mcp_res}

        if name == 'end_call':
            call_whatsapp_action('/call/drop', {})
            mcp_res = call_app_os_mcp('central_ai_end_call', {'reason': params.get('reason') or 'user_goodbye'})
            return {'type': 'client_tool.result', 'callId': call_id,
                    'result': {'status': 'ended', 'mcp': mcp_res}}

        if name != 'native_leo':
            return {'type': 'client_tool.result', 'callId': call_id, 'isError': True,
                    'result': {'status': 'unsupported_tool'}}

        request = params.get('request') if isinstance(params, dict) else None
        if not isinstance(request, str) or not request.strip() or len(request) > 9000 or request.lstrip().startswith('/'):
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
    model = (config.get('FISH_LLM_MODEL') or os.environ.get('HERMES_VOICE_MODEL') or 'openai/gpt-4.1-mini').strip()
    if not model:
        return {'custom': None}
    openai_key = (config.get('OPENAI_API_KEY') or os.environ.get('OPENAI_API_KEY') or '').strip()
    if openai_key.startswith('sk-') and not openai_key.startswith('sk-or-') and model.startswith('openai/') and model != '@preset/leo-live-voice-20261003':
        openai_model = model.replace('openai/', '')
        return {'custom': {'base_url': 'https://api.openai.com/v1', 'model': openai_model, 'api_key': openai_key}}
    key = (config.get('OPENROUTER_API_KEY') or os.environ.get('OPENROUTER_API_KEY') or '').strip()
    if not key.startswith('sk-or-') and not key and not openai_key:
        raise ValueError('OpenRouter conversation credential is missing')
    return {'custom': {'base_url': 'https://openrouter.ai/api/v1', 'model': model, 'api_key': key or openai_key}}
