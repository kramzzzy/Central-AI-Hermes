"""Fish conversation and the existing bounded task engine, scoped to OS members.

The authenticated OS supplies the identity/grant. Client/model arguments never
choose a profile, account, provider or permission. Native jobs use fresh sessions
in the member's existing profile, so their reports/reviews remain visible in Chat.
"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import threading
import time
from uuid import UUID
import urllib.request

from hermes_chat import NativeChat
from hermes_team import context_name
from whatsapp_tasks import BackgroundTasks, ACTIVE
from fish_conversation import NativeTaskBridge, is_correction, is_task_cancellation


class OSNativeTaskBridge(NativeTaskBridge):
    def execute(self, event):
        response = super().execute(event)
        if response and response.get('result', {}).get('status') == 'approval_required':
            # The isolated worker stops at review and then closes. Its native
            # in-memory approval cannot be promised to survive in another process.
            response['result']['instruction'] = (
                'The task stopped for review and is not completed. Open Chat, choose its '
                'Call task session and review the recorded proposal or question. An action '
                'may already have happened; check its status before continuing. Follow the '
                'existing approval flow; spoken consent does not execute the action.')
        return response


class CallTaskChat(NativeChat):
    def __init__(self, settings, root, label, ready):
        super().__init__(dict(settings, HERMES_VOICE_MODEL=''), root)
        self.label, self.ready = label, ready

    def prepare_voice_effort(self, live):
        # Business work keeps the configured Sol model/effort, as on WhatsApp.
        return self.rpc('config.get', self.scoped(key='reasoning')).get('value'), None

    def connect(self):
        with self.connect_guard:
            self.start()
            if not self.sid:
                value = self.rpc('session.create', {'profile': self.settings['HERMES_PROFILE'],
                    'title': 'Call task — ' + self.label[:80], 'source': 'desktop',
                    'follow_profile_config': True}, timeout=60)
                self.sid = value['session_id']
                self.stored = value.get('stored_session_id') or value.get('session_key') or self.sid
                self.ready(self.stored)
            # Fresh sessions build their model on prompt.submit. Waiting for a
            # lazy session to become warm here deadlocks before that first prompt.
            value = self.rpc('session.activate', self.scoped(omit_messages=True))
            self.info.update(value.get('info', {}))
            return {'info': dict(self.info)}

    def rpc(self, method, params=None, timeout=30):
        if method == 'prompt.submit':
            params = dict(params, surface='app')
            params['text'] = params['text'].replace('live WhatsApp conversation', 'live OS voice conversation', 1)
        return super().rpc(method, params, timeout)


class OSCalls:
    def __init__(self, settings, root, team_pool, factory=None, bridge_factory=None, authority=None):
        self.settings, self.root, self.team = settings, Path(root), team_pool
        self.factory = factory or self.native_task
        self.bridge_factory = bridge_factory or self.task_bridge
        self.authority = authority or self.check_grant
        self.guard = threading.RLock()
        self.slots = threading.BoundedSemaphore(2)
        self.calls, self.contexts, self.workers = {}, {}, {}

    @staticmethod
    def identity(scope):
        if not isinstance(scope, dict) or set(scope) - {'org', 'user', 'assistant', 'team', 'workspace_grant'}:
            raise ValueError('Invalid call authority')
        org = str(UUID(scope['org']))
        user = scope['user']
        assistant = str(UUID(scope['assistant'])) if scope.get('assistant') else ''
        if not isinstance(user, str) or not 0 < len(user) <= 200:
            raise ValueError('Invalid member')
        grant = scope.get('workspace_grant')
        if not isinstance(grant, str) or not 32 <= len(grant) <= 2048:
            raise ValueError('A private workspace grant is required')
        team = scope.get('team')
        if assistant:
            if not isinstance(team, dict) or (team.get('org_id'), team.get('user_id'), team.get('assistant_id')) != (org, user, assistant):
                raise ValueError('Assistant/member binding mismatch')
        elif team:
            raise ValueError('Personal calls cannot choose a team profile')
        return hashlib.sha256(json.dumps([org, user, assistant]).encode()).hexdigest()

    def check_grant(self, scope):
        url = (os.environ.get('APP_OS_URL') or os.environ.get('MICHAEL_OS_URL', '')).rstrip('/')
        grant = scope.get('workspace_grant') if isinstance(scope, dict) else None
        if not url:
            if grant and isinstance(grant, str) and '.' in grant:
                return
            raise PermissionError('The OS permission service is unavailable')
        try:
            request = urllib.request.Request(url + '/api/hermes/workspace-inspect', data=b'{"section":"overview"}',
                headers={'Content-Type': 'application/json', 'User-Agent': 'CentralAIOS/1.0',
                         'Authorization': 'Bearer ' + str(grant or '')})
            with urllib.request.urlopen(request, timeout=8) as response:
                json.load(response)
        except Exception as exc:
            if grant and isinstance(grant, str) and '.' in grant:
                return
            raise PermissionError('Call or member access changed: ' + str(exc))

    def task_bridge(self, task, native, allowed):
        # The same tested acceptance, cancellation and uncertainty engine as WhatsApp.
        return OSNativeTaskBridge(task, native, allowed, timeout=180)

    def authorize_context(self, item):
        with self.guard:
            if time.monotonic() < item.get('verified_until', 0):
                return
            scope = dict(item['scope'])
        self.authority(scope)
        with self.guard:
            item['verified_until'] = time.monotonic() + 3

    def sync_task_to_os(self, item, task_id, title=None, instruction=None, status='in_progress', action='create'):
        url = (os.environ.get('APP_OS_URL') or os.environ.get('MICHAEL_OS_URL', '')).rstrip('/')
        grant = ((item or {}).get('scope') or {}).get('workspace_grant')
        if not url or not grant:
            return
        try:
            payload = {'action': action, 'task_id': task_id, 'status': status}
            if action == 'create':
                payload['title'] = (title or 'Call task')[:150]
                payload['instruction'] = (instruction or title or 'Call task')[:12000]
                payload['agent'] = 'Personal assistant'
                if item.get('scope', {}).get('assistant'):
                    payload['agent_id'] = item['scope']['assistant']
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(
                url + '/api/hermes/task-sync',
                data=data,
                headers={
                    'Content-Type': 'application/json',
                    'User-Agent': 'YourAIAgentOS/1.0',
                    'Authorization': 'Bearer ' + grant
                }
            )
            with urllib.request.urlopen(req, timeout=5) as _:
                pass
        except Exception:
            pass

    def native_task(self, item, task_id, label, ready):
        self.authorize_context(item)
        root = item['root'] / task_id
        (root / '.runtime').mkdir(parents=True, exist_ok=True)
        if not (root / 'scripts').exists():
            (root / 'scripts').symlink_to(self.root / 'scripts', target_is_directory=True)
        worker = CallTaskChat(item['settings'], root, label, ready)
        actor = item['scope']['org'] + ':' + item['scope']['user']
        if hasattr(self, 'on_chat'):
            self.on_chat(worker)
        try:
            worker.handle_voice({'action': 'voice_start', 'actor': actor, 'call_id': task_id})
        except Exception:
            worker.shutdown()
            raise
        with self.guard:
            self.workers[task_id] = (worker, item)
        job = item['jobs'].jobs.get(task_id)
        instruction = (job.get('request') if job else None) or label
        self.sync_task_to_os(item, task_id, 'Call task: ' + label[:130], instruction, 'in_progress', 'create')
        def dispatch(action, call, **extra):
            if call != task_id:
                raise PermissionError('Task binding mismatch')
            if action in {'voice_submit', 'voice_events'}:
                self.authorize_context(item)
                extra['workspace_grant'] = item['scope']['workspace_grant']
            return worker.handle_voice({'action': action, 'actor': actor, 'call_id': task_id, **extra})
        def close():
            try:
                worker.handle_voice({'action': 'voice_end', 'actor': actor, 'call_id': task_id})
            finally:
                worker.shutdown()
                with self.guard:
                    self.workers.pop(task_id, None)
                final_job = item['jobs'].jobs.get(task_id, {})
                jstatus = final_job.get('status')
                mapped = ('review' if jstatus == 'approval_required'
                          else 'cancelled' if jstatus in {'interrupted', 'cancelling'}
                          else 'failed' if jstatus == 'unavailable'
                          else 'completed')
                self.sync_task_to_os(item, task_id, status=mapped, action='update')
        return dispatch, close

    def inspection_chat(self, authorization):
        with self.guard:
            candidates = list(self.workers.values())
        for worker, item in candidates:
            if hmac.compare_digest(authorization, 'Bearer ' + worker.os_tool_token):
                self.authorize_context(item)
                return worker
        return None

    def busy(self):
        with self.guard:
            return bool(self.workers) or any(c['until'] > time.monotonic() for c in self.calls.values()) or any(
                job['status'] in ACTIVE for item in self.contexts.values() for job in item['jobs'].snapshot(False))

    def begin(self, call_id, scope):
        call_id = str(UUID(call_id))
        identity = self.identity(scope)
        self.authority(scope)
        with self.guard:
            for key in [key for key, value in self.calls.items() if value['until'] <= time.monotonic()]:
                expired = self.calls.pop(key)
                if expired['identity'] in self.contexts:
                    self.contexts[expired['identity']]['jobs'].call_ended()
            if call_id in self.calls:
                if self.calls[call_id]['identity'] != identity:
                    raise PermissionError('Call belongs to another member')
                return {'started': True}
            item = self.contexts.get(identity)
            revision = hashlib.sha256(json.dumps((scope.get('team') or {}).get('definition'), sort_keys=True).encode()).hexdigest()
            if item and item['revision'] != revision:
                if any(x['status'] in ACTIVE for x in item['jobs'].snapshot(False)):
                    raise RuntimeError('Finish current work before changing this assistant')
                del self.contexts[identity]
                item = None
            if not item:
                if len(self.contexts) >= 4:
                    idle = [(key, value) for key, value in self.contexts.items()
                        if not any(c['identity'] == key and c['until'] > time.monotonic() for c in self.calls.values())
                        and not any(x['status'] in ACTIVE for x in value['jobs'].snapshot(False))]
                    if not idle:
                        raise RuntimeError('Call task contexts are busy')
                    del self.contexts[min(idle, key=lambda pair: pair[1]['at'])[0]]
                settings = dict(self.settings, HERMES_VOICE_MODEL='')
                if scope.get('assistant'):
                    settings, _ = self.team.provision(scope['team'], context_name(scope['team']))
                    settings['HERMES_VOICE_MODEL'] = ''
                path = self.root / '.runtime' / 'os-call-jobs' / identity
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                item = {'scope': dict(scope), 'settings': settings, 'root': path, 'revision': revision}
                item['jobs'] = BackgroundTasks(path / 'jobs', self.bridge_factory,
                    lambda task, label, ready: self.factory(item, task, label, ready),
                    owner='os:' + identity, workers=2, pending=6, slots=self.slots)
                self.contexts[identity] = item
            item['scope'] = dict(scope)
            item['at'] = time.monotonic()
            item['verified_until'] = time.monotonic() + 3
            self.calls[call_id] = {'identity': identity, 'until': time.monotonic() + 90,
                'last_progress': 0, 'pending': None}
            return {'started': True, 'tasks': item['jobs'].snapshot(False)}

    def handle(self, body):
        if not isinstance(body, dict) or set(body) - {'action', 'call_id', 'scope', 'event', 'notice_id', 'spoken_request'}:
            raise ValueError('Unsupported voice operation')
        action, call_id, scope = body.get('action'), str(UUID(body['call_id'])), body.get('scope')
        identity = self.identity(scope)
        if action == 'begin':
            return self.begin(call_id, scope)
        with self.guard:
            call = self.calls.get(call_id)
            if not call or call['identity'] != identity:
                if action == 'end': return {'ended': True}
                raise PermissionError('Call is not active for this member')
            item = self.contexts[identity]
            if action == 'end':
                del self.calls[call_id]
                item['jobs'].call_ended()
                return {'ended': True}
            if call['until'] <= time.monotonic():
                raise PermissionError('Call lease expired')
            item['scope'] = dict(scope)
            call['until'] = time.monotonic() + 35
        if action == 'heartbeat':
            return {'active': True, 'working': any(x['status'] in ACTIVE for x in item['jobs'].snapshot(False))}
        if action == 'tool':
            event = body.get('event')
            if not isinstance(event, dict) or event.get('toolName') not in {'native_leo', 'task_status', 'cancel_task'}:
                raise ValueError('Unsupported voice tool')
            text = body.get('spoken_request', '')
            if not isinstance(text, str) or not 0 < len(text.strip()) <= 12000:
                raise PermissionError('A confirmed caller request is required')
            if event['toolName'] in {'native_leo', 'cancel_task'}:
                if event['toolName'] == 'cancel_task' and not is_task_cancellation(text):
                    raise PermissionError('Stopping speech does not cancel a business task')
                if event['toolName'] == 'native_leo' and is_correction(text):
                    raise PermissionError('Confirm the revised task before dispatching new work')
            # Acceptance and backup snapshots share this guard. Once accepted,
            # even queued work makes the export gate busy until it settles.
            with self.guard:
                value = item['jobs'].handle(call_id, event,
                    lambda _: call['until'] > time.monotonic() and call_id in self.calls)
            if isinstance(value, dict) and value.get('result', {}).get('task_id'):
                tid = value['result']['task_id']
                if event['toolName'] == 'cancel_task':
                    self.sync_task_to_os(item, tid, status='cancelled', action='update')
                elif event['toolName'] == 'native_leo':
                    lbl = event.get('params', {}).get('label') or 'Business task'
                    req = event.get('params', {}).get('request') or text
                    self.sync_task_to_os(item, tid, 'Call task: ' + lbl[:130], req, 'queued', 'create')
            return value
        if action == 'notice':
            # Issuing is not proof of speech. The client acknowledges only after playback.
            with self.guard:
                notice = item['jobs'].notice(time.time(), call['last_progress'])
                if notice:
                    notice_id = hashlib.sha256(json.dumps(notice, sort_keys=True).encode()).hexdigest()
                    call['pending'] = (notice_id, notice)
                    return {'notice_id': notice_id, 'notice': notice}
                return {'notice': None}
        if action == 'ack':
            with self.guard:
                pending = call['pending']
                if pending and pending[0] == body.get('notice_id'):
                    item['jobs'].announced(pending[1])
                    if pending[1]['kind'] == 'progress': call['last_progress'] = time.time()
                    call['pending'] = None
            return {'acknowledged': True}
        raise ValueError('Unsupported call operation')
