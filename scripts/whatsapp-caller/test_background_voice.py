"""Voice remains independent of worker completion and never treats runtime data as intent."""
import ast
import asyncio
import json
import re
import time
import unittest
import hashlib
import hmac
import io
import sys
import tempfile
import threading
import urllib.request
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from whatsapp_tasks import BackgroundTasks, ACTIVE
from test_background_tasks import fixture_routing

source = ast.parse(((Path(__file__).parents[1] / 'whatsapp_fish.py').read_text() + '\n' + (Path(__file__).parents[1] / 'fish_conversation.py').read_text()))
node = next(n for n in source.body
            if isinstance(n, ast.ClassDef) and n.name == 'FishCall')
scope = dict(asyncio=asyncio, json=json, time=time, re=re)
helpers = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in {'business_connection_limit', 'is_correction', 'is_task_cancellation'}]
scope['routing'] = fixture_routing
exec(compile(ast.Module(body=helpers + [node], type_ignores=[]), '<actual background voice dispatch>', 'exec'), scope)


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.call = scope['FishCall'].__new__(scope['FishCall'])
        self.call.call = 'exact-call'
        self.call.closed = False
        self.call.connected = True
        self.call.recovering = False
        self.call.generation = 1
        self.call.state = 'listening'
        self.call.last_user_activity = 0
        self.call.last_speaking_end = 0
        self.call.last_notice = self.call.last_progress = 0
        self.call.last_input = 'caller'
        self.call.latest_caller_text = 'Please check the report.'
        self.call.member = {'number': '639267200480'}
        self.call.pending_notice = None
        self.call.notice_marker = 'private-test-marker'
        self.sent, self.handled, self.announced = [], [], []
        self.notice = {'kind': 'progress', 'tasks': [{'task_id': 'job-1', 'status': 'running', 'label': 'Report'}]}
        def handle(call, event, allowed):
            self.handled.append(event)
            return {'type': 'client_tool.result', 'callId': event['callId'], 'result': {'status': 'queued'}}
        self.call.service = SimpleNamespace(metrics={}, allowed=lambda call: call == 'exact-call',
            jobs=SimpleNamespace(notice=lambda *args: self.notice, announced=self.announced.append, handle=handle))
        self.call.jobs = self.call.service.jobs
        async def publish(event):
            self.sent.append(event)
            self.call.closed = True
        self.call.publish = publish

    async def test_progress_speaks_in_gap_without_starting_another_task(self):
        await asyncio.wait_for(self.call.notices(), 2)
        self.assertEqual(self.sent[0]['type'], 'user.message')
        self.assertTrue(self.sent[0]['audio'])
        self.assertIn('private-test-marker', self.sent[0]['text'])
        self.assertIn('"status": "running"', self.sent[0]['text'])
        self.assertFalse(self.handled)
        self.assertFalse(self.announced)
        self.assertEqual(self.call.pending_notice['notice'], self.notice)
        self.assertEqual(self.call.last_input, 'background')

    async def test_callback_result_waits_until_the_greeting_has_spoken(self):
        self.call.callback_notice = {'kind': 'result', 'tasks': []}
        self.call.first_spoken = False
        async def greet():
            await asyncio.sleep(.65)
            self.assertFalse(self.sent)
            self.call.first_spoken = True
        await asyncio.gather(self.call.notices(), greet())
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.announced)

    async def test_updates_wait_during_speech_thinking_mic_activity_or_disconnect(self):
        async def stop():
            await asyncio.sleep(.65)
            self.call.closed = True
        for setting in ({'state': 'speaking'}, {'state': 'thinking'}, {'last_user_activity': time.monotonic()}, {'connected': False}):
            self.call.closed = False
            old = {key: getattr(self.call, key) for key in setting}
            for key, value in setting.items():
                if key == 'last_user_activity':
                    value = time.monotonic()
                setattr(self.call, key, value)
            await asyncio.gather(self.call.notices(), stop())
            self.assertFalse(self.sent)
            for key, value in old.items():
                setattr(self.call, key, value)

    async def test_new_caller_speech_while_update_is_selected_defers_notification(self):
        def notice(*args):
            self.call.last_user_activity = time.monotonic()
            return self.notice
        self.call.service.jobs.notice = notice
        async def stop():
            await asyncio.sleep(.65)
            self.call.closed = True
        await asyncio.gather(self.call.notices(), stop())
        self.assertFalse(self.sent)
        self.assertFalse(self.announced)

    async def test_runtime_update_cannot_dispatch_or_cancel_work(self):
        self.call.last_input = 'background'
        for name in ('native_leo', 'cancel_task'):
            self.call.closed = False
            await self.call.run_tool({'toolName': name, 'callId': name})
            self.assertEqual(self.sent[-1]['result']['status'], 'caller_request_required')
        self.assertFalse(self.handled)

    async def test_caller_can_start_another_job_while_first_report_runs(self):
        await self.call.run_tool({'toolName': 'native_leo', 'callId': 'second-job'})
        self.assertEqual(len(self.handled), 1)
        self.assertEqual(self.sent[-1]['result']['status'], 'queued')

    async def test_correction_does_not_start_a_second_job_but_new_request_can(self):
        for correction in ('Not Denver. Denpasar.', "That's incorrect. I said Bali.", 'Stop, I meant the other report.'):
            self.call.latest_caller_text = correction
            self.call.closed = False
            await self.call.run_tool({'toolName': 'native_leo', 'callId': 'correction'})
            self.assertEqual(self.sent[-1]['result']['status'], 'clarification_required')
        self.assertFalse(self.handled)
        self.call.closed = False
        self.call.latest_caller_text = 'Yes, please start the revised report.'
        await self.call.run_tool({'toolName': 'native_leo', 'callId': 'confirmed'})
        self.assertEqual(len(self.handled), 1)

    async def test_known_business_connections_return_without_starting_work(self):
        self.call.member = {'number': '61423947456', 'role': 'business'}
        for request in ("Check if Michael's email is accessible and connected.", 'Search flights from Brisbane to Denpasar. Find prices.'):
            self.call.closed = False
            await self.call.run_tool({'toolName': 'native_leo', 'callId': request, 'params': {'request': request}})
            self.assertEqual(self.sent[-1]['result']['status'], 'connection_unavailable')
            self.assertFalse(self.sent[-1]['result']['task_started'])
        self.assertFalse(self.handled)
        self.call.closed = False
        await self.call.run_tool({'toolName': 'native_leo', 'callId': 'draft', 'params': {
            'request': 'Draft an email using the supplied notes.'}})
        self.assertEqual(len(self.handled), 1)
        self.call.closed = False
        await self.call.run_tool({'toolName': 'native_leo', 'callId': 'compare', 'params': {
            'request': 'Compare flight prices and baggage using the options supplied by Michael.'}})
        self.assertEqual(len(self.handled), 2)

    async def test_idle_gap_delivers_finished_result_without_progress_cooldown(self):
        self.call.state = 'idle'
        self.notice['kind'] = 'result'
        self.call.last_notice = time.monotonic() - 4
        await asyncio.wait_for(self.call.notices(), 2)
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.announced)

    async def test_pending_result_is_not_resent_while_waiting_for_speech(self):
        self.call.pending_notice = {'notice': self.notice, 'generation': 1, 'sent': time.monotonic()}
        async def stop():
            await asyncio.sleep(.65)
            self.call.closed = True
        await asyncio.gather(self.call.notices(), stop())
        self.assertFalse(self.sent)

    async def test_result_not_delivered_by_provider_is_retried_in_a_gap(self):
        self.call.pending_notice = {'notice': self.notice, 'generation': 1, 'sent': time.monotonic() - 21}
        self.call.generation = 2
        await asyncio.wait_for(self.call.notices(), 2)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.call.pending_notice['generation'], 2)

    async def test_result_is_acknowledged_only_after_all_speech_finishes(self):
        self.call.pending_notice = {'notice': self.notice, 'generation': 1, 'sent': time.monotonic(), 'spoken': True}
        self.call.state = 'speaking'
        async def finish():
            await asyncio.sleep(.6)
            self.assertFalse(self.announced)
            self.call.state = 'listening'
            self.call.jobs.notice = lambda *args: None
            await asyncio.sleep(.6)
            self.call.closed = True
        await asyncio.gather(self.call.notices(), finish())
        self.assertEqual(self.announced, [self.notice])
        self.assertIsNone(self.call.pending_notice)

    async def test_interruption_after_first_spoken_segment_preserves_result(self):
        self.call.pending_notice = {'notice': self.notice, 'generation': 0, 'sent': time.monotonic(), 'spoken': True}
        async def stop():
            await asyncio.sleep(.65)
            self.call.closed = True
        await asyncio.gather(self.call.notices(), stop())
        self.assertFalse(self.announced)

    async def test_no_worries_is_not_a_correction_fence(self):
        self.call.latest_caller_text = 'No worries. Please check the second report.'
        await self.call.run_tool({'toolName': 'native_leo', 'callId': 'second'})
        self.assertEqual(len(self.handled), 1)

    async def test_correction_or_negated_cancellation_cannot_stop_an_existing_job(self):
        for text in ("That's incorrect. I meant a different report. Do not start it yet.",
                     'Stop talking.', "Don't cancel the report.", 'Do not stop working on the report.',
                     'Wait, I meant something else.'):
            self.call.latest_caller_text = text
            self.call.closed = False
            await self.call.run_tool({'toolName': 'cancel_task', 'callId': text})
            self.assertEqual(self.sent[-1]['result']['status'], 'explicit_cancellation_required')
        self.assertFalse(self.handled)
        for text in ('Cancel the weekly report.', 'Stop working on that report.'):
            self.call.latest_caller_text = text
            self.call.closed = False
            await self.call.run_tool({'toolName': 'cancel_task', 'callId': text})
        self.assertEqual(len(self.handled), 2)

class OSReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.routing_patch = patch('whatsapp_routing.routing', fixture_routing)
        self.routing_patch.start()
        self.addCleanup(self.routing_patch.stop)
        self.clock = SimpleNamespace(now=100.0)
        self.services, self.items, self.requests, self.executions = [], [], [], []
        self.release = threading.Event()
        self.completion_wins = False
        self.transport = lambda payload, request: {'synced': True, 'cancel_requested': False}
        self.authority = lambda scope: None
        fixture = self

        class Bridge:
            def __init__(self, task, native, allowed):
                self.task, self.allowed = task, allowed
            def execute(self, event):
                fixture.executions.append(self.task)
                while not fixture.release.wait(.01):
                    if not self.allowed(None) and not fixture.completion_wins:
                        return {'result': {'status': 'interrupted', 'message': 'Cancellation acknowledged'}}
                return {'result': {'status': 'complete', 'message': 'Actual completed result'}}

        def factory(item, task, label, ready):
            ready('session-' + task)
            return None, lambda: None

        def urlopen(request, timeout):
            self.assertEqual(timeout, 5)
            for service in self.services:
                self.assertFalse(service.guard._is_owned(), 'HTTP held the context lock')
            for item in self.items:
                self.assertFalse(item['jobs'].lock._is_owned(), 'HTTP held the jobs lock')
            payload = json.loads(request.data)
            self.requests.append((payload, request.get_header('Authorization')))
            return io.BytesIO(json.dumps(self.transport(payload, request)).encode())

        module = ast.parse((Path(__file__).parents[1] / 'hermes_os_calls.py').read_text(encoding='utf-8'))
        cls = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == 'OSCalls')
        self.namespace = dict(hashlib=hashlib, hmac=hmac, json=json, Path=Path, UUID=UUID,
            threading=threading, time=SimpleNamespace(time=time.time, monotonic=lambda: self.clock.now),
            os=SimpleNamespace(environ={'APP_OS_URL': 'https://fixture.invalid'}),
            urllib=SimpleNamespace(request=SimpleNamespace(Request=urllib.request.Request, urlopen=urlopen),
                error=urllib.error), BackgroundTasks=BackgroundTasks, ACTIVE=ACTIVE,
            context_name=lambda team: 'fixture-context', is_casual_greeting_intent=lambda text: False,
            is_task_cancellation=lambda text: True, is_correction=lambda text: False)
        exec(compile(ast.Module(body=[cls], type_ignores=[]), '<actual OSCalls>', 'exec'), self.namespace)
        self.factory, self.bridge = factory, Bridge
        self.service = self.new_service()
        self.scope = {'org': str(uuid4()), 'user': 'fixture-owner', 'workspace_grant': 'fixture-grant-' + 'x' * 40}
        self.call, self.item = self.begin(self.scope)

    def tearDown(self):
        for item in self.items:
            item['retired'] = True
            item['sync_wake'].set()
        self.release.set()
        for item in self.items:
            item['jobs'].shutdown()
            if item.get('sync_thread'):
                item['sync_thread'].join(3)
                self.assertFalse(item['sync_thread'].is_alive())

    def new_service(self):
        service = self.namespace['OSCalls']({}, self.directory.name,
            SimpleNamespace(revision=lambda team: 'revision', provision=lambda *args: ({}, None)),
            factory=self.factory, bridge_factory=self.bridge, authority=lambda scope: self.authority(scope))
        self.services.append(service)
        return service

    def begin(self, scope, service=None):
        service = service or self.service
        call = str(uuid4())
        service.begin(call, scope)
        item = service.contexts[service.identity(scope)]
        if not any(existing is item for existing in self.items):
            self.items.append(item)
        return call, item

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(predicate())

    def advance(self, seconds):
        self.clock.now += seconds
        for item in self.items:
            item['sync_wake'].set()

    def start(self):
        value = self.service.handle({'action': 'tool', 'call_id': self.call, 'scope': self.scope,
            'spoken_request': 'Please prepare this synthetic report.', 'event': {
                'toolName': 'native_leo', 'callId': str(uuid4()), 'expectsResponse': True,
                'params': {'request': 'Synthetic report', 'label': 'Report'}}})
        return value['result']['task_id']

    def terminal(self, item=None, status='complete'):
        item = item or self.item
        task = str(uuid4())
        with item['jobs'].lock:
            item['jobs'].jobs[task] = {'task_id': task, 'request': 'Persisted synthetic task',
                'label': 'Persisted report', 'status': status, 'created': time.time() - 30,
                'finished': time.time(), 'announced': False,
                'result': {'status': status, 'message': 'Persisted actual result'}}
            item['jobs'].save()
        return task

    def http_error(self, code):
        raise urllib.error.HTTPError('https://fixture.invalid', code, 'fixture failure', {}, None)

    def test_last_terminal_failure_recovers_after_hangup_without_another_call(self):
        failed = threading.Event()
        def transport(payload, request):
            if payload['worker_status'] == 'complete' and not failed.is_set():
                failed.set()
                raise TimeoutError('synthetic timeout')
            return {'synced': True, 'cancel_requested': False}
        self.transport = transport
        task = self.start()
        self.wait(lambda: self.item['jobs'].jobs[task].get('os_sync_created'))
        self.service.handle({'action': 'end', 'call_id': self.call, 'scope': self.scope})
        self.release.set()
        self.wait(lambda: self.item['jobs'].jobs[task]['status'] == 'complete')
        self.advance(2)
        self.wait(failed.is_set)
        self.wait(lambda: self.item['sync_retry'] == 4)
        self.assertTrue(self.service.context_pinned(self.item))
        self.assertFalse(self.service.calls)
        self.advance(2)
        self.wait(lambda: not self.service.sync_pending(self.item))
        self.wait(lambda: not self.item['syncing'])
        self.assertEqual(self.executions, [task])
        self.assertEqual(self.requests[-1][0]['action'], 'update')
        self.assertFalse(self.service.context_pinned(self.item))

    def test_unchanged_running_snapshot_fetches_cancellation_and_final_outcome(self):
        cancel = False
        self.transport = lambda payload, request: {'synced': True, 'cancel_requested': cancel}
        task = self.start()
        self.wait(lambda: self.item['jobs'].jobs[task]['status'] == 'running')
        self.wait(lambda: self.item['jobs'].jobs[task].get('os_sync_created'))
        self.advance(2)
        self.wait(lambda: self.requests[-1][0]['worker_status'] == 'running')
        self.wait(lambda: self.item['sync_next'] == self.clock.now + 2)
        prior = {key: value for key, value in self.requests[-1][0].items() if key != 'action'}
        count = len(self.requests)
        cancel = True
        self.advance(2)
        self.wait(lambda: len(self.requests) > count)
        self.assertEqual({key: value for key, value in self.requests[-1][0].items() if key != 'action'}, prior)
        self.wait(lambda: self.item['jobs'].jobs[task]['status'] == 'interrupted')
        self.assertIn('cancel_requested_at', self.item['jobs'].jobs[task])
        self.advance(2)
        self.wait(lambda: not self.service.sync_pending(self.item))
        self.assertEqual(self.requests[-1][0]['worker_status'], 'interrupted')
        self.assertEqual(self.requests[-1][0]['status'], 'cancelled')

    def test_timeout_backoff_is_two_four_eight_sixteen_thirty_and_resets(self):
        self.terminal()
        def unavailable(payload, request):
            raise TimeoutError('synthetic timeout')
        self.transport = unavailable
        self.service.reconcile_tasks(self.item)
        self.wait(lambda: self.item['sync_retry'] == 4)
        for delay in (2, 4, 8, 16, 30, 30):
            self.assertEqual(self.item['sync_next'] - self.clock.now, delay)
            count = len(self.requests)
            self.advance(delay)
            self.wait(lambda: len(self.requests) > count)
            self.wait(lambda: self.item['sync_next'] > self.clock.now)
        self.transport = lambda payload, request: {'synced': True, 'cancel_requested': False}
        self.advance(30)
        self.wait(lambda: not self.item['syncing'])
        self.assertEqual(self.item['sync_retry'], 2)
        self.assertFalse(self.service.sync_pending(self.item))

    def test_network_retryable_terminal_contexts_are_pinned_against_eviction(self):
        def unavailable(payload, request):
            raise TimeoutError('synthetic timeout')
        self.transport = unavailable
        for index in range(4):
            scope = dict(self.scope, user='pin-' + str(index)) if index else self.scope
            call, item = self.begin(scope) if index else (self.call, self.item)
            self.terminal(item)
            self.service.reconcile_tasks(item)
            self.service.handle({'action': 'end', 'call_id': call, 'scope': scope})
        with self.assertRaisesRegex(RuntimeError, 'contexts are busy'):
            self.begin(dict(self.scope, user='fifth'))
        self.assertEqual(len(self.service.contexts), 4)

    def test_auth_failures_cancel_locally_and_terminal_context_can_be_evicted(self):
        for code in (401, 403):
            with self.subTest(code=code):
                if code == 403:
                    self.service = self.new_service()
                    self.scope = dict(self.scope, user='revoked-second')
                    self.call, self.item = self.begin(self.scope)
                self.transport = lambda payload, request: self.http_error(code)
                task = self.start()
                self.wait(lambda: self.item['auth_blocked'])
                self.wait(lambda: self.item['jobs'].jobs[task]['status'] == 'interrupted')
                self.assertTrue(self.service.sync_pending(self.item))
                self.assertFalse(self.service.context_pinned(self.item))
                count = len(self.requests)
                self.advance(100)
                self.service.reconcile_tasks(self.item)
                self.assertEqual(len(self.requests), count)
                identity, path = self.service.identity(self.scope), self.item['jobs'].path
                for index in range(4):
                    self.begin(dict(self.scope, user='replacement-' + str(index)))
                self.assertNotIn(identity, self.service.contexts)
                self.assertTrue(path.exists())
                saved = json.loads(path.read_text())['jobs'][0]
                self.assertEqual(saved['status'], 'interrupted')
                self.assertIn('cancel_requested_at', saved)

    def test_restart_requires_authorized_same_context_and_never_replays_work(self):
        task = self.terminal(status='running')
        self.item['jobs'].jobs[task]['os_sync_created'] = True
        self.item['jobs'].save()
        with self.service.guard:
            self.service.retire_context(self.service.identity(self.scope))
        self.service = self.new_service()
        self.authority = lambda scope: (_ for _ in ()).throw(PermissionError('expired grant'))
        with self.assertRaises(PermissionError):
            self.begin(self.scope)
        self.assertFalse(self.requests)
        self.authority = lambda scope: None
        other = dict(self.scope, assistant=str(uuid4()))
        other['team'] = {'org_id': other['org'], 'user_id': other['user'], 'assistant_id': other['assistant']}
        _, other_item = self.begin(other)
        self.assertFalse(other_item['jobs'].jobs)
        renewed = dict(self.scope, workspace_grant='renewed-fixture-' + 'y' * 40)
        self.call, self.item = self.begin(renewed)
        self.wait(lambda: not self.service.sync_pending(self.item))
        self.assertEqual(self.requests[-1][0]['task_id'], task)
        self.assertEqual(self.requests[-1][0]['action'], 'update')
        self.assertEqual(self.requests[-1][0]['worker_status'], 'unavailable')
        self.assertFalse(self.executions)
        stored = self.item['jobs'].path.read_text()
        self.assertNotIn('workspace_grant', stored)
        self.assertNotIn(renewed['workspace_grant'], stored)

    def test_missing_and_deleted_mirrors_end_retries_without_recreation(self):
        for code in (404, 410):
            with self.subTest(code=code):
                task = self.terminal()
                self.item['jobs'].jobs[task]['os_sync_created'] = code == 404
                self.transport = lambda payload, request: self.http_error(code)
                self.service.reconcile_tasks(self.item)
                self.wait(lambda: self.item['jobs'].jobs[task].get('os_sync_gone'))
                self.wait(lambda: not self.item['syncing'])
                self.assertEqual(self.requests[-1][0]['action'], 'update' if code == 404 else 'create')
                count = len(self.requests)
                self.service.reconcile_tasks(self.item)
                self.advance(30)
                self.assertEqual(len(self.requests), count)
        with self.service.guard:
            self.service.retire_context(self.service.identity(self.scope))
        self.service = self.new_service()
        self.call, self.item = self.begin(self.scope)
        self.assertEqual(len(self.item['jobs'].jobs), 2)
        self.assertFalse(self.service.sync_pending(self.item))
        self.assertFalse(self.executions)

    def test_completion_wins_cancellation_race_without_false_interrupted_result(self):
        self.completion_wins = True
        cancel = False
        self.transport = lambda payload, request: {'synced': True, 'cancel_requested': cancel}
        task = self.start()
        self.wait(lambda: task in self.executions)
        self.wait(lambda: self.item['jobs'].jobs[task].get('os_sync_created'))
        self.wait(lambda: self.item['sync_next'] > self.clock.now)
        cancel = True
        self.advance(2)
        self.wait(lambda: self.item['jobs'].jobs[task]['status'] == 'cancelling')
        self.wait(lambda: self.item['sync_next'] > self.clock.now)
        self.advance(2)
        self.wait(lambda: self.requests[-1][0]['worker_status'] == 'cancelling')
        self.assertEqual(self.requests[-1][0]['status'], 'in_progress')
        self.release.set()
        self.wait(lambda: self.item['jobs'].jobs[task]['status'] == 'complete')
        self.advance(2)
        self.wait(lambda: not self.service.sync_pending(self.item))
        self.assertEqual(self.requests[-1][0]['worker_status'], 'complete')
        self.assertIn('cancel_requested_at', self.item['jobs'].jobs[task])

    def test_one_sweep_and_renewed_authorization_survives_late_old_grant_failure(self):
        task = self.terminal()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def transport(payload, request):
            if request.get_header('Authorization').endswith(self.scope['workspace_grant']):
                entered.set()
                release.wait(2)
                self.http_error(403)
            return {'synced': True, 'cancel_requested': False}
        self.transport = transport
        self.service.reconcile_tasks(self.item)
        self.wait(entered.is_set)
        for _ in range(4):
            self.service.reconcile_tasks(self.item)
        self.assertEqual(len(self.requests), 1)
        renewed = dict(self.scope, workspace_grant='new-grant-' + 'z' * 40)
        self.begin(renewed)
        release.set()
        self.wait(lambda: self.item['sync_next'] > self.clock.now)
        self.assertFalse(self.item['auth_blocked'])
        self.advance(2)
        self.wait(lambda: not self.service.sync_pending(self.item))
        self.assertTrue(self.item['jobs'].jobs[task]['os_sync_created'])
        self.assertTrue(self.requests[-1][1].endswith(renewed['workspace_grant']))

    def task_control_body(self, action='stop'):
        return {'action': 'tool', 'call_id': self.call, 'scope': self.scope,
            'spoken_request': 'Stop the exact task I selected.', 'event': {
                'toolName': 'manage_tasks', 'callId': str(uuid4()), 'expectsResponse': True,
                'params': {'action': action, 'task_id': str(uuid4())}}}

    def test_owner_task_control_uses_exact_scoped_grant_and_single_attempt_receipt(self):
        body = self.task_control_body()
        def transport(payload, request):
            self.assertTrue(request.full_url.endswith('/api/hermes/task-control'))
            self.assertEqual(payload, body['event']['params'])
            return {'ok': True, 'task_id': payload['task_id'], 'status': 'in_progress', 'cancel_requested': True}
        self.transport = transport
        response = self.service.handle(body)
        self.assertTrue(response['result']['cancel_requested'])
        self.assertEqual(response['result']['status'], 'in_progress')
        self.assertEqual(self.service.handle(body), response)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.requests[0][1], 'Bearer ' + self.scope['workspace_grant'])
        self.assertFalse(self.executions)
        self.assertNotIn(self.scope['workspace_grant'], self.item['jobs'].path.read_text())

    def test_task_control_denial_and_uncertain_delivery_never_fall_back_or_retry(self):
        body = self.task_control_body()
        foreign = dict(body, scope=dict(self.scope, user='other-member'))
        with self.assertRaises(PermissionError):
            self.service.handle(foreign)
        self.assertFalse(self.requests)
        for code in (401, 403):
            if code == 403:
                self.scope = dict(self.scope, user='customer-context', workspace_grant='customer-grant-' + 'c' * 40)
                self.call, self.item = self.begin(self.scope)
            self.transport = lambda payload, request: self.http_error(code)
            body = self.task_control_body()
            response = self.service.handle(body)
            self.assertTrue(response['isError'])
            self.assertEqual(response['result']['http_status'], code)
            self.assertEqual(self.requests[-1][1], 'Bearer ' + self.scope['workspace_grant'])
            count = len(self.requests)
            self.assertEqual(self.service.handle(body), response)
            self.assertEqual(len(self.requests), count)
        def timeout(payload, request):
            raise TimeoutError('delivery unknown')
        self.transport = timeout
        body = self.task_control_body()
        response = self.service.handle(body)
        self.assertEqual(response['result']['status'], 'unconfirmed')
        self.assertFalse(response['result']['retry_action'])
        count = len(self.requests)
        self.assertEqual(self.service.handle(body), response)
        self.assertEqual(len(self.requests), count)
        self.assertFalse(self.executions)


if __name__ == '__main__':
    unittest.main()
