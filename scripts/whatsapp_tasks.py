"""Caller-bound background jobs and explicitly requested, single-attempt callbacks."""
import json
import re
import threading
import time
from pathlib import Path
from uuid import uuid4


ACTIVE = {'queued', 'starting', 'running', 'cancelling'}
TERMINAL = {'complete', 'approval_required', 'interrupted', 'unavailable'}


class BackgroundTasks:
    def __init__(self, data, bridge_factory, native_factory, owner=None,
                 workers=3, pending=8, slots=None, callbacks=False, on_change=None):
        self.path = Path(data) / 'background-tasks.json'
        from whatsapp_routing import routing
        if not owner:
            owner = 'whatsapp:' + (routing().get('owner') or 'default')
        self.owner, self.bridge_factory, self.native_factory = owner, bridge_factory, native_factory
        self.limit, self.pending = workers, pending
        self.lock = threading.RLock()
        self.slots = slots or threading.BoundedSemaphore(workers)
        self.jobs, self.packets, self.requests, self.controls = {}, {}, {}, {}
        self.closed = False
        self.on_change = on_change
        from whatsapp_routing import routing
        self.callback_target = owner.partition(':')[2] if owner in {'whatsapp:'+c['number'] for c in routing()['contacts'] if c['calls']} else None
        self.callbacks = callbacks and self.callback_target is not None
        self.last_call_end = time.time()
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            if saved.get('owner') != owner:
                raise RuntimeError('Background job owner mismatch')
            for job in saved.get('jobs', []):
                callback = job.get('callback', {})
                if callback.get('status') == 'claimed':
                    callback.update(status='unconfirmed', outcome='service_restarted')
                if job['status'] in ACTIVE:
                    # A killed worker may have crossed an action boundary. Never replay it.
                    job.update(status='unavailable', announced=False, finished=time.time(), result={
                        'status': 'unavailable', 'retry_action': False,
                        'message': 'The worker stopped before its outcome was confirmed. Check actual status before trying the action again.'})
                self.jobs[job['task_id']] = job
            self.packets = saved.get('packets', {})
            self.requests = saved.get('requests', {})
            self.save()

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'owner': self.owner, 'jobs': list(self.jobs.values()),
            'packets': self.packets, 'requests': self.requests}), encoding='utf-8')
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def changed(self, task_id):
        if self.on_change:
            try:
                self.on_change(task_id)
            except Exception:
                print('voice_task_sync_failed', flush=True)

    def view(self, job, result=True):
        value = {key: job[key] for key in ('task_id', 'label', 'status')}
        if result and job.get('result'):
            value['result'] = dict(job['result'])
            if len(value['result'].get('message', '')) > 4000:
                value['result']['message'] = value['result']['message'][:4000]
                value['result']['summary_truncated'] = True
        value['retry_action'] = False
        if job.get('callback'):
            value['callback'] = {key: job['callback'][key] for key in ('status', 'outcome') if key in job['callback']}
        return value

    def snapshot(self, result=True):
        with self.lock:
            return [self.view(job, result) for job in self.jobs.values()]

    def counts(self):
        with self.lock:
            return {status: sum(job['status'] == status for job in self.jobs.values())
                    for status in sorted(ACTIVE | TERMINAL)}

    def handle(self, call, event, allowed):
        call_id, name, params = event.get('callId'), event.get('toolName'), event.get('params')
        def response(value, error=False):
            return {'type': 'client_tool.result', 'callId': call_id, 'result': value, **({'isError': True} if error else {})}
        if (not isinstance(call_id, str) or not 1 <= len(call_id) <= 160
                or event.get('expectsResponse') is not True or not isinstance(params, dict)
                or name not in {'native_leo', 'task_status', 'cancel_task', 'task_callback',
                                'add_knowledge', 'search_knowledge', 'get_workspace_overview',
                                'send_whatsapp_message', 'call_whatsapp_contact'}):
            return response({'status': 'invalid_request'}, True)
        with self.lock:
            if self.closed or not allowed(call):
                return response({'status': 'inactive_call'}, True)
            packet = call + ':' + call_id
            previous = self.packets.get(packet)
            if previous:
                return previous
            if name in {'add_knowledge', 'search_knowledge', 'get_workspace_overview', 'send_whatsapp_message', 'call_whatsapp_contact'}:
                bridge = self.bridge_factory(call, None, allowed)
                result = bridge.execute(event)
                self.packets[packet] = result
                self.save()
                return result
            if name in {'task_status', 'cancel_task', 'task_callback'}:
                task_id = params.get('task_id', '')
                if not isinstance(task_id, str):
                    return response({'status': 'invalid_task_id'}, True)
                if name == 'task_status' and not task_id:
                    return response({'tasks': self.snapshot(False)})
                job = self.jobs.get(task_id)
                if not job:
                    return response({'status': 'unknown_task'}, True)
                if name == 'task_callback':
                    mode = params.get('mode')
                    if not self.callbacks or mode not in {'enable', 'disable'}:
                        return response({'status': 'callback_unavailable'}, True)
                    callback = job.get('callback', {})
                    if callback.get('status') == 'claimed':
                        return response({'status': 'callback_in_progress', 'message': 'The call attempt has already started.'}, True)
                    if mode == 'disable':
                        job['callback'] = {'status': 'cancelled'}
                    elif job['announced'] or job['status'] in {'interrupted', 'cancelling'}:
                        return response({'status': 'callback_not_needed', 'message': 'The result was already announced or the task was cancelled; no callback was scheduled.'})
                    elif callback.get('status') != 'requested':
                        job['callback'] = {'status': 'requested', 'requested': time.time(),
                            'expires': time.time() + 86400, 'requested_call': call}
                if name == 'cancel_task' and job['status'] in ACTIVE:
                    self.controls[task_id]['cancel'].set()
                    job['status'] = 'cancelling'
                    if job.get('callback', {}).get('status') == 'requested':
                        job['callback']['status'] = 'cancelled'
                    self.save()
                result = response(self.view(job))
                self.packets[packet] = result
                self.save()
                return result
            request = params.get('request')
            label = params.get('label') or 'Business task'
            if (not isinstance(request, str) or not request.strip() or len(request) > 9000
                    or request.lstrip().startswith('/') or not isinstance(label, str) or len(label) > 100):
                return response({'status': 'invalid_request'}, True)
            normalized = ' '.join(request.casefold().split())
            if normalized in self.requests:
                job = self.jobs[self.requests[normalized]]
                result = response({**self.view(job), 'previously_dispatched': True,
                    'instruction': 'This is the earlier task, not a new execution. Check its actual outcome before any retry.'})
            elif len(self.jobs) >= 128 or sum(job['status'] in ACTIVE for job in self.jobs.values()) >= self.pending:
                result = response({'status': 'queue_full', 'retry_action': False,
                    'message': 'No new task was started. Existing work is continuing.'}, True)
            else:
                task_id = str(uuid4())
                job = {'task_id': task_id, 'label': label.strip() or 'Business task',
                    'request': request.strip(), 'status': 'queued', 'created': time.time(),
                    'announced': False, 'result': None}
                self.jobs[task_id] = job
                self.requests[normalized] = task_id
                self.controls[task_id] = {'cancel': threading.Event(), 'bridge': None, 'thread': None}
                result = response({**self.view(job), 'instruction':
                    'Acknowledge briefly: I will look into that in the background. Keep listening and accept other requests. This is acceptance, not completion.'})
                self.packets[packet] = result
                # Persist acceptance before any worker can perform an action.
                self.save()
                thread = threading.Thread(target=self.run, args=(task_id,), daemon=True)
                self.controls[task_id]['thread'] = thread
                thread.start()
                return result
            self.packets[packet] = result
            self.save()
            return result

    def run(self, task_id):
        control = self.controls[task_id]
        acquired, close = False, None
        result = {'status': 'unavailable', 'retry_action': False,
            'message': 'The task outcome is unconfirmed. Check actual status before trying again.'}
        try:
            while not control['cancel'].is_set():
                if self.slots.acquire(timeout=.2):
                    acquired = True
                    break
            if control['cancel'].is_set():
                result = {'status': 'interrupted', 'retry_action': False,
                          'message': 'Cancelled before the task started.'}
                return
            with self.lock:
                job = self.jobs[task_id]
                job['status'] = 'starting'
                self.save()
            self.changed(task_id)
            def session_ready(session_id):
                with self.lock:
                    job['native_session'] = session_id
                    self.save()
            native, close = self.native_factory(task_id, job['label'], session_ready)
            bridge = self.bridge_factory(task_id, native, lambda _: not control['cancel'].is_set())
            with self.lock:
                control['bridge'] = bridge
                if not control['cancel'].is_set():
                    job['status'] = 'running'
                self.save()
            self.changed(task_id)
            result = bridge.execute({'toolName': 'native_leo', 'callId': task_id,
                'expectsResponse': True, 'params': {'request': job['request']}})['result']
            if result.get('status') not in TERMINAL:
                result = {'status': 'interrupted' if control['cancel'].is_set() else 'unavailable',
                          'retry_action': False, 'message': 'The task did not finish. Check actual status before retrying.'}
        except Exception:
            pass  # Never log request, profile credentials or raw provider failures.
        finally:
            # Cleanup and completion observers must see the actual persisted result.
            with self.lock:
                job = self.jobs[task_id]
                job.update(status=result['status'], result=result, finished=time.time(), announced=False)
                self.save()
            if close:
                try:
                    close()
                except Exception:
                    pass
            if acquired:
                self.slots.release()
            self.changed(task_id)

    def notice(self, now, last_progress, since=None):
        """Called only in a conversational gap. Read state at delivery, not timer creation."""
        with self.lock:
            jobs = [job for job in self.jobs.values() if since is None or job['created'] >= since]
            finished = [job for job in jobs if job['status'] in TERMINAL and not job['announced']]
            if finished:
                return {'kind': 'result', 'tasks': [self.view(job) for job in finished[:2]]}
            active = [job for job in jobs if job['status'] in ACTIVE
                      and job.get('spoken_progress') != job['status']]
            if active and now - min(job['created'] for job in active) >= 12 and now - last_progress >= 25:
                return {'kind': 'progress', 'tasks': [self.view(job, False) for job in active]}
            return None

    def announced(self, notice):
        if notice['kind'] == 'progress':
            with self.lock:
                for item in notice['tasks']:
                    job = self.jobs.get(item['task_id'])
                    if job and job['status'] == item['status']:
                        job['spoken_progress'] = item['status']
                self.save()
        if notice['kind'] == 'result':
            with self.lock:
                for item in notice['tasks']:
                    self.jobs[item['task_id']]['announced'] = True
                    callback = self.jobs[item['task_id']].get('callback', {})
                    if callback.get('status') in {'requested', 'claimed'}:
                        callback.update(status='reported', outcome='spoken_result')
                self.save()

    def call_ended(self):
        self.last_call_end = time.time()

    def claim_callback(self, now=None):
        """Persist consumption before dialing; no callback/action is ever replayed."""
        now = time.time() if now is None else now
        with self.lock:
            if not self.callbacks or self.closed or now - self.last_call_end < 15:
                return None
            eligible, changed = [], False
            for job in self.jobs.values():
                callback = job.get('callback', {})
                if callback.get('status') == 'claimed' and not callback.get('voice_call') and now - callback['claimed'] >= 180:
                    callback.update(status='unconfirmed', outcome='call_attempt_unconfirmed'); changed = True
                if callback.get('status') != 'requested':
                    continue
                if job['announced']:
                    callback.update(status='reported', outcome='spoken_result'); changed = True
                elif callback['expires'] <= now:
                    callback.update(status='expired', outcome='request_expired'); changed = True
                elif job['status'] == 'interrupted':
                    callback['status'] = 'cancelled'; changed = True
                elif job['status'] in TERMINAL and job.get('result') and now - job.get('finished', now) >= 5:
                    eligible.append(job)
            if not eligible:
                if changed:
                    self.save()
                return None
            callback_id = str(uuid4())
            for job in eligible[:2]:
                job['callback'].update(status='claimed', callback_id=callback_id, claimed=now)
            self.save()
            return {'callback_id': callback_id, 'target': self.callback_target}

    def callback_available(self, callback_id):
        with self.lock:
            return self.callbacks and any(job.get('callback',{}).get('callback_id')==callback_id
                and job['callback']['status']=='claimed' and not job['callback'].get('voice_call') for job in self.jobs.values())

    def attach_callback(self, callback_id, call):
        with self.lock:
            jobs = [job for job in self.jobs.values() if job.get('callback', {}).get('callback_id') == callback_id
                    and job['callback']['status'] == 'claimed']
            if not self.callbacks or not jobs or any(job['callback'].get('voice_call') for job in jobs):
                raise PermissionError('Callback is not available')
            for job in jobs:
                job['callback']['voice_call'] = call
            self.save()
            return {'kind': 'result', 'tasks': [self.view(job) for job in jobs]}

    def finish_callback(self, callback_id, outcome):
        if outcome not in {'ended', 'unanswered', 'dial_failed', 'voice_unavailable'}:
            raise ValueError('Invalid callback outcome')
        with self.lock:
            for job in self.jobs.values():
                callback = job.get('callback', {})
                if callback.get('callback_id') == callback_id and callback.get('status') == 'claimed':
                    callback.update(status='not_reported', outcome=outcome)
            self.save()

    def shutdown(self):
        with self.lock:
            self.closed = True
            for control in self.controls.values():
                control['cancel'].set()
        deadline = time.monotonic() + 12
        for control in list(self.controls.values()):
            thread = control['thread']
            if thread:
                thread.join(max(0, deadline - time.monotonic()))
