"""Actual job fences: concurrent work, cancellation, restart uncertainty and honest progress."""
import ast
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
from whatsapp_tasks import BackgroundTasks

source = ast.parse(((Path(__file__).parents[1] / 'whatsapp_fish.py').read_text() + '\n' + (Path(__file__).parents[1] / 'fish_conversation.py').read_text()))
node = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'NativeTaskBridge')
scope = dict(threading=threading, time=time, uuid4=uuid4)
exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual task fence>', 'exec'), scope)


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.workers = {}
        self.gate = threading.RLock()
        def factory(task_id, label, ready):
            worker = {'release': threading.Event(), 'started': threading.Event(), 'actions': [], 'review': False}
            with self.gate:
                self.workers[task_id] = worker
            ready('native-session-' + task_id)
            def native(action, call, **extra):
                self.assertEqual(task_id, call)
                worker['actions'].append((action, extra))
                if action == 'voice_submit':
                    worker['started'].set()
                    return {'epoch': task_id, 'cursor': 0}
                if action == 'voice_events':
                    worker['release'].wait(.02)
                    if worker['review']:
                        return {'cursor': 1, 'running': True, 'review': True, 'events': []}
                    if worker['release'].is_set():
                        return {'cursor': 1, 'running': False, 'events': [{'type': 'message.complete',
                            'payload': {'text': 'Verified result for ' + label, 'status': 'complete'}}]}
                    return {'cursor': 0, 'running': True, 'events': []}
                return {}
            return native, lambda: None
        self.factory = factory
        self.manager = BackgroundTasks(self.directory.name, scope['NativeTaskBridge'], factory)
        self.admitted = True

    def tearDown(self):
        self.manager.shutdown()
        self.directory.cleanup()

    def event(self, name='native_leo', packet=None, **params):
        return {'toolName': name, 'callId': packet or str(uuid4()), 'expectsResponse': True,
                'params': params or {'request': 'Synthetic report check', 'label': 'Report'}}

    def handle(self, event, call='call-one'):
        return self.manager.handle(call, event, lambda _: self.admitted)

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(predicate())

    def start(self, request='Synthetic report check', label='Report'):
        result = self.handle(self.event(request=request, label=label))['result']
        task_id = result['task_id']
        self.wait(lambda: task_id in self.workers and self.workers[task_id]['started'].is_set())
        return task_id

    def test_acceptance_returns_promptly_and_three_distinct_jobs_overlap(self):
        ids = []
        began = time.monotonic()
        for index in range(3):
            ids.append(self.start('Synthetic question ' + str(index), 'Report ' + str(index)))
        self.assertLess(time.monotonic() - began, 1)
        self.assertEqual(self.manager.counts()['running'], 3)
        self.assertEqual(len(set(self.manager.jobs[i]['native_session'] for i in ids)), 3)
        for i in reversed(ids):
            self.workers[i]['release'].set()
            self.wait(lambda: self.manager.jobs[i]['status'] == 'complete')
            self.assertIn(self.manager.jobs[i]['label'], self.manager.jobs[i]['result']['message'])

    def test_reliable_duplicate_and_identical_wording_never_repeat_action(self):
        event = self.event()
        first = self.handle(event)
        self.assertEqual(self.handle(event), first)
        repeat = self.handle({**event, 'callId': 'another-packet'})
        self.assertEqual(first['result']['task_id'], repeat['result']['task_id'])
        self.assertTrue(repeat['result']['previously_dispatched'])
        self.wait(lambda: len(self.workers) == 1)
        self.assertEqual(len(self.manager.jobs), 1)

    def test_cancel_named_task_does_not_stop_other_task(self):
        report = self.start()
        meeting = self.start('Synthetic meeting preparation', 'Meeting')
        self.handle(self.event('cancel_task', task_id=report))
        self.wait(lambda: self.manager.jobs[report]['status'] == 'interrupted')
        self.assertEqual(self.manager.jobs[meeting]['status'], 'running')
        self.assertEqual(sum(action[0] == 'voice_stop' for action in self.workers[report]['actions']), 1)
        self.assertFalse(any(action[0] == 'voice_stop' for action in self.workers[meeting]['actions']))

    def test_hangup_does_not_cancel_and_result_available_in_next_call(self):
        task = self.start()
        self.admitted = False
        self.workers[task]['release'].set()
        self.wait(lambda: self.manager.jobs[task]['status'] == 'complete')
        self.admitted = True
        result = self.handle(self.event('task_status', task_id=task), 'next-call')['result']
        self.assertEqual(result['status'], 'complete')
        self.assertIn('Verified result', result['result']['message'])

    def test_inactive_or_malformed_request_never_dispatches(self):
        self.admitted = False
        self.assertTrue(self.handle(self.event())['isError'])
        self.admitted = True
        for event in (self.event(request='/approve', label='test'), self.event(request='x' * 9001),
                      self.event(name='unknown'), self.event('cancel_task', task_id='unknown')):
            self.assertTrue(self.handle(event)['isError'])
        self.assertFalse(self.workers)

    def test_queue_has_limit_and_queued_cancel_never_submits(self):
        ids = [self.start('Synthetic ' + str(i)) for i in range(3)]
        queued = self.handle(self.event(request='Fourth synthetic report', label='Queued'))['result']['task_id']
        self.assertEqual(self.manager.jobs[queued]['status'], 'queued')
        self.handle(self.event('cancel_task', task_id=queued))
        self.wait(lambda: self.manager.jobs[queued]['status'] == 'interrupted')
        self.assertNotIn(queued, self.workers)
        self.manager.pending = 3
        self.assertEqual(self.handle(self.event(request='No queue room'))['result']['status'], 'queue_full')
        self.assertEqual(len(self.workers), len(ids))

    def test_progress_waits_twelve_seconds_and_repeats_at_most_every_twenty_five(self):
        task = self.start()
        created = self.manager.jobs[task]['created']
        self.assertIsNone(self.manager.notice(created + 11, 0))
        notice = self.manager.notice(created + 13, 0)
        self.assertEqual(notice['kind'], 'progress')
        self.assertEqual(notice['tasks'][0]['status'], 'running')
        self.assertNotIn('result', notice['tasks'][0])
        self.assertIsNone(self.manager.notice(created + 30, created + 13))

    def test_finished_result_replaces_stale_progress_and_announces_once(self):
        task = self.start()
        self.workers[task]['release'].set()
        self.wait(lambda: self.manager.jobs[task]['status'] == 'complete')
        notice = self.manager.notice(time.time() + 30, 0)
        self.assertEqual(notice['kind'], 'result')
        self.manager.announced(notice)
        self.assertIsNone(self.manager.notice(time.time() + 60, 0))

    def test_restart_marks_unsettled_outcome_uncertain_without_replay(self):
        self.start()
        restarted = BackgroundTasks(self.directory.name, scope['NativeTaskBridge'],
            lambda *args: self.fail('Restart must not dispatch any worker'))
        self.assertEqual(restarted.snapshot()[0]['status'], 'unavailable')
        self.assertFalse(restarted.snapshot()[0]['result']['retry_action'])
        restarted.shutdown()

    def test_owner_mismatch_cannot_load_another_private_job_store(self):
        self.start()
        with self.assertRaisesRegex(RuntimeError, 'owner mismatch'):
                BackgroundTasks(self.directory.name, scope['NativeTaskBridge'], self.factory, owner='different-owner')

    def test_business_caller_cannot_query_or_cancel_mark_job_even_with_its_id(self):
        mark_task = self.start()
        business = BackgroundTasks(Path(self.directory.name) / 'business', scope['NativeTaskBridge'],
            self.factory, owner='whatsapp:61423947456')
        for name in ('task_status', 'cancel_task'):
            response = business.handle('business-call', self.event(name, task_id=mark_task), lambda _: True)
            self.assertEqual(response['result']['status'], 'unknown_task')
        self.assertEqual(self.manager.jobs[mark_task]['status'], 'running')
        business.shutdown()

    def test_approval_does_not_auto_approve_or_retry(self):
        task = self.start()
        self.workers[task]['review'] = True
        self.wait(lambda: self.manager.jobs[task]['status'] == 'approval_required')
        actions = [action[0] for action in self.workers[task]['actions']]
        self.assertEqual(actions.count('voice_submit'), 1)
        self.assertNotIn('approval.respond', actions)
        self.assertFalse(self.manager.jobs[task]['result']['retry_action'])

    def test_status_queries_do_not_submit_work(self):
        self.assertEqual(self.handle(self.event('task_status', task_id=''))['result']['tasks'], [])
        self.assertFalse(self.workers)

if __name__ == '__main__':
    unittest.main()
