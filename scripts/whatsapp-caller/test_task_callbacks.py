"""Task callbacks require caller intent and survive delivery uncertainty without redial."""
import ast
import asyncio
import json
import re
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parents[1]))
from whatsapp_tasks import BackgroundTasks

source = ast.parse(((Path(__file__).parents[1] / 'whatsapp_fish.py').read_text() + '\n' + (Path(__file__).parents[1] / 'fish_conversation.py').read_text()))
nodes = [n for n in source.body if (isinstance(n, ast.ClassDef) and n.name == 'FishCall') or
         (isinstance(n, ast.FunctionDef) and n.name in {'is_callback_request', 'is_correction', 'is_task_cancellation', 'business_connection_limit'})]
scope = dict(asyncio=asyncio, time=time, re=re, json=json)
from whatsapp_routing import routing
scope['routing']=routing
exec(compile(ast.Module(body=nodes, type_ignores=[]), '<actual callback dispatch>', 'exec'), scope)


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.release = threading.Event()
        self.executions = 0
        fixture = self
        class Bridge:
            def __init__(self, *args): pass
            def execute(self, event):
                fixture.executions += 1
                fixture.release.wait(2)
                return {'result': {'status': 'complete', 'message': 'Verified synthetic result violet.'}}
        self.manager = BackgroundTasks(self.directory.name, Bridge, lambda *args: (None, lambda: None),
            owner='whatsapp:61423947456', callbacks=True)

    def tearDown(self):
        self.release.set()
        self.manager.shutdown()
        self.directory.cleanup()

    def event(self, name, **params):
        return {'toolName': name, 'callId': str(uuid4()), 'params': params, 'expectsResponse': True}

    def handle(self, event):
        return self.manager.handle('michael-call', event, lambda _: True)

    def task(self):
        return self.handle(self.event('native_leo', request='Synthetic report', label='Report'))['result']['task_id']

    def complete(self, task):
        self.release.set()
        deadline = time.monotonic() + 3
        while self.manager.jobs[task]['status'] != 'complete' and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.manager.jobs[task]['status'], 'complete')
        return self.manager.jobs[task]['finished'] + 20

    def enable(self, task):
        return self.handle(self.event('task_callback', task_id=task, mode='enable'))

    def test_ordinary_completed_task_and_existing_history_never_dial(self):
        task = self.task()
        now = self.complete(task)
        self.assertIsNone(self.manager.claim_callback(now))
        restarted = BackgroundTasks(self.directory.name, None, None, owner=self.manager.owner, callbacks=True)
        self.assertIsNone(restarted.claim_callback(now + 30))
        restarted.shutdown()

    def test_request_waits_for_result_and_hangup_gap_then_claims_once(self):
        task = self.task()
        self.enable(task)
        self.assertIsNone(self.manager.claim_callback(time.time() + 20))
        now = self.complete(task)
        self.manager.last_call_end = now - 10
        self.assertIsNone(self.manager.claim_callback(now))
        self.manager.last_call_end = now - 20
        request = self.manager.claim_callback(now)
        self.assertEqual(request['target'], '61423947456')
        self.assertIsNone(self.manager.claim_callback(now + 30))
        self.assertEqual(self.executions, 1)
        restarted = BackgroundTasks(self.directory.name, None, None, owner=self.manager.owner, callbacks=True)
        self.assertEqual(restarted.jobs[task]['callback']['status'], 'unconfirmed')
        self.assertIsNone(restarted.claim_callback(now + 60))
        restarted.shutdown()

    def test_answer_context_is_one_use_and_unanswered_does_not_announce_or_redial(self):
        task = self.task()
        self.enable(task)
        now = self.complete(task)
        request = self.manager.claim_callback(now)
        self.manager.finish_callback(request['callback_id'], 'unanswered')
        self.assertFalse(self.manager.jobs[task]['announced'])
        self.assertEqual(self.manager.view(self.manager.jobs[task])['callback']['outcome'], 'unanswered')
        self.assertIsNone(self.manager.claim_callback(now + 30))
        with self.assertRaises(PermissionError):
            self.manager.attach_callback(request['callback_id'], 'new-call')

    def test_spoken_result_suppresses_callback_before_or_after_claim(self):
        task = self.task()
        self.enable(task)
        now = self.complete(task)
        notice = self.manager.notice(now, 0)
        self.manager.announced(notice)
        self.assertIsNone(self.manager.claim_callback(now + 30))
        self.assertEqual(self.manager.jobs[task]['callback']['status'], 'reported')

    def test_callback_attaches_exact_owned_result_only_once(self):
        task = self.task()
        self.enable(task)
        now = self.complete(task)
        request = self.manager.claim_callback(now)
        with self.assertRaises(PermissionError):
            self.manager.attach_callback(str(uuid4()), 'foreign-call')
        notice = self.manager.attach_callback(request['callback_id'], 'exact-callback-call')
        self.assertEqual(notice['tasks'][0]['result']['message'], 'Verified synthetic result violet.')
        with self.assertRaises(PermissionError):
            self.manager.attach_callback(request['callback_id'], 'replayed-call')
        self.manager.announced(notice)
        self.manager.finish_callback(request['callback_id'], 'ended')
        self.assertEqual(self.manager.jobs[task]['callback']['status'], 'reported')

    def test_cancel_callback_preserves_work_and_expired_callback_never_dials(self):
        task = self.task()
        self.enable(task)
        self.handle(self.event('task_callback', task_id=task, mode='disable'))
        self.assertIn(self.manager.jobs[task]['status'], {'queued', 'starting', 'running'})
        now = self.complete(task)
        self.assertIsNone(self.manager.claim_callback(now))
        self.enable(task)
        self.manager.jobs[task]['callback']['expires'] = now - 1
        self.assertIsNone(self.manager.claim_callback(now))
        self.assertEqual(self.manager.jobs[task]['callback']['status'], 'expired')

    def test_callback_does_not_proactively_repeat_old_unrequested_reports(self):
        task = self.task()
        now = self.complete(task)
        self.assertIsNone(self.manager.notice(now, 0, since=now - 1))
        self.assertIsNotNone(self.manager.notice(now, 0))

    def test_mark_cannot_request_or_claim_michael_callback(self):
        mark = BackgroundTasks(Path(self.directory.name) / 'mark', None, None, callbacks=True)
        task = self.task()
        response = mark.handle('mark-call', self.event('task_callback', task_id=task, mode='enable'), lambda _: True)
        self.assertEqual(response['result']['status'], 'unknown_task')
        self.assertTrue(mark.callbacks)
        self.assertIsNone(mark.claim_callback(time.time() + 100))
        mark.shutdown()

    def test_mark_can_request_a_callback_to_his_own_number_only(self):
        mark = BackgroundTasks(Path(self.directory.name) / 'mark', None, None, callbacks=True)
        task=str(uuid4());now=time.time()
        mark.jobs[task]={'task_id':task,'label':'Code update status','status':'complete','announced':False,
            'created':now-60,'finished':now-10,'result':{'status':'complete','message':'Verified code update result.'}}
        response=mark.handle('mark-call',self.event('task_callback',task_id=task,mode='enable',target='61423947456'),lambda _:True)
        self.assertEqual(response['result']['callback']['status'],'requested')
        mark.last_call_end=now-20
        claim=mark.claim_callback(now)
        self.assertEqual(claim['target'],'639267200480')
        self.assertTrue(mark.callback_available(claim['callback_id']))
        self.assertFalse(self.manager.callback_available(claim['callback_id']))
        notice=mark.attach_callback(claim['callback_id'],'mark-callback-call')
        self.assertEqual(notice['tasks'][0]['task_id'],task)
        self.assertFalse(mark.callback_available(claim['callback_id']))
        mark.finish_callback(claim['callback_id'],'unanswered')
        self.assertIsNone(mark.claim_callback(now+100));mark.shutdown()

    def test_an_unconfigured_owner_cannot_enable_outbound_callbacks(self):
        other=BackgroundTasks(Path(self.directory.name)/'other',None,None,owner='whatsapp:639690395476',callbacks=True)
        self.assertFalse(other.callbacks);self.assertIsNone(other.callback_target)
        self.assertIsNone(other.claim_callback(time.time()+100));other.shutdown()

    def test_duplicate_callback_packet_does_not_replace_or_repeat_request(self):
        task = self.task()
        event = self.event('task_callback', task_id=task, mode='enable')
        first = self.handle(event)
        self.assertEqual(self.handle(event), first)
        self.assertEqual(self.handle(self.event('task_callback', task_id=task, mode='enable'))['result']['callback']['status'], 'requested')
        now = self.complete(task)
        self.assertIsNotNone(self.manager.claim_callback(now))
        self.assertIsNone(self.manager.claim_callback(now + 50))

    def test_lost_claim_response_becomes_uncertain_without_repeating_call(self):
        task = self.task()
        self.enable(task)
        now = self.complete(task)
        request = self.manager.claim_callback(now)
        self.assertIsNone(self.manager.claim_callback(now + 181))
        self.assertEqual(self.manager.jobs[task]['callback']['status'], 'unconfirmed')
        with self.assertRaises(PermissionError):
            self.manager.attach_callback(request['callback_id'], 'late-call')


class CallbackIntentTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_self_callback_only_and_backend_updates_blocked(self):
        call = scope['FishCall'].__new__(scope['FishCall'])
        handled, published = [], []
        call.call, call.closed, call.last_input = 'exact-call', False, 'caller'
        call.member = {'number': '61423947456'}
        call.service = SimpleNamespace(metrics={}, allowed=lambda _: True, members={"639267200480":{},"61423947456":{}})
        call.jobs = SimpleNamespace(handle=lambda *args: handled.append(args) or {'result': {'status': 'requested'}})
        async def publish(event): published.append(event)
        call.publish = publish
        event = {'toolName': 'task_callback', 'callId': 'packet', 'params': {'task_id': 'task', 'mode': 'enable'}}
        for text in ('Please check the report.', "Don't call me when it finishes.", 'Could Leo call me when done?',
                     'Maybe call me later when done.', 'Call Mark when it is done.'):
            call.latest_caller_text = text
            await call.run_tool(event)
            self.assertEqual(published[-1]['result']['status'], 'explicit_callback_request_required')
        self.assertFalse(handled)
        call.latest_caller_text = 'Can you call me when that report is finished?'
        await call.run_tool(event)
        self.assertEqual(len(handled), 1)
        call.last_input = 'background'
        await call.run_tool(event)
        self.assertEqual(published[-1]['result']['status'], 'caller_request_required')
        call.last_input = 'caller'
        call.member = {'number': '639267200480'}
        await call.run_tool(event)
        self.assertEqual(len(handled), 2)
        call.member={'number':'unconfigured'}
        await call.run_tool(event)
        self.assertEqual(len(handled),2)

    async def test_withdrawal_requires_explicit_cancel_callback_intent(self):
        helper = scope['is_callback_request']
        self.assertTrue(helper('Do not call me after the report.', 'disable'))
        self.assertTrue(helper('Cancel the callback for that task.', 'disable'))
        self.assertFalse(helper('Stop talking.', 'disable'))
        self.assertFalse(helper('Cancel the report.', 'disable'))
        self.assertTrue(helper('Give me a call once the report is ready.', 'enable'))
        self.assertFalse(scope['is_task_cancellation']('Cancel the callback for that report.'))


class CallbackHTTPTests(unittest.TestCase):
    def setUp(self):
        from test_voice_stream import StreamTests
        self.fixture = StreamTests()
        self.fixture.setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.jobs = BackgroundTasks(self.directory.name, None, None, owner='whatsapp:61423947456', callbacks=True)
        self.mark_jobs=BackgroundTasks(Path(self.directory.name)/'mark',None,None,callbacks=True)
        self.mark_jobs.last_call_end=time.time()-30
        self.jobs.last_call_end = time.time() - 30
        self.task = str(uuid4())
        self.jobs.jobs[self.task] = {'task_id': self.task, 'label': 'Synthetic report', 'status': 'complete',
            'created': time.time() - 60, 'finished': time.time() - 20, 'announced': False,
            'result': {'status': 'complete', 'message': 'Verified synthetic report.'},
            'callback': {'status': 'requested', 'expires': time.time() + 100}}
        self.fixture.ns.update(ready=True, active=None, OWNER='639267200480',
            resolve_caller=lambda number: {'number': number},
            fish_service=SimpleNamespace(call=None, members={'61423947456': {'jobs': self.jobs},'639267200480':{'jobs':self.mark_jobs}}))

    def tearDown(self):
        self.fixture.tearDown()
        self.jobs.shutdown()
        self.mark_jobs.shutdown()
        self.directory.cleanup()

    def post(self, path, number='61423947456', body=b'', key='fixture-key'):
        request = urllib.request.Request(f'http://127.0.0.1:{self.fixture.server.server_port}' + path, data=body,
            headers={'Authorization': 'Bearer ' + key, 'X-Call-ID': str(uuid4()), 'X-Caller-Number': number})
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_claim_http_requires_private_key_and_an_authorized_self_identity(self):
        self.assertEqual(self.post('/callbacks/claim', key='wrong')[0], 401)
        self.assertEqual(self.post('/callbacks/claim', number='639267200480'),(200,{}))
        self.assertEqual(self.post('/callbacks/claim',number='639690395476')[0],403)
        self.assertEqual(self.jobs.jobs[self.task]['callback']['status'], 'requested')
        code, result = self.post('/callbacks/claim')
        self.assertEqual(code, 200)
        self.assertEqual(result['target'], '61423947456')
        self.assertNotIn('result', result)
        self.assertEqual(self.post('/callbacks/claim'), (200, {}))

    def test_mark_http_claim_and_finish_are_bound_to_marks_own_ledger(self):
        task=str(uuid4());self.mark_jobs.jobs[task]={**self.jobs.jobs[self.task],
            'task_id':task,'callback':{'status':'requested','expires':time.time()+100}}
        code,claim=self.post('/callbacks/claim',number='639267200480')
        self.assertEqual(code,200);self.assertEqual(claim['target'],'639267200480')
        self.assertEqual(self.jobs.jobs[self.task]['callback']['status'],'requested')
        body=json.dumps({'callback_id':claim['callback_id'],'outcome':'unanswered'}).encode()
        self.post('/callbacks/finish',body=body)
        self.assertEqual(self.mark_jobs.jobs[task]['callback']['status'],'claimed')
        self.assertEqual(self.post('/callbacks/finish',number='639267200480',body=body),(200,{}))
        self.assertEqual(self.mark_jobs.jobs[task]['callback']['status'],'not_reported')
        self.assertEqual(self.post('/callbacks/claim',number='639267200480'),(200,{}))

    def test_active_native_or_fish_call_defers_claim(self):
        self.fixture.ns['active'] = 'active-call'
        self.assertEqual(self.post('/callbacks/claim'), (200, {}))
        self.fixture.ns['active'] = None
        self.fixture.ns['fish_service'].call = 'closing-fish-call'
        self.assertEqual(self.post('/callbacks/claim'), (200, {}))
        self.assertEqual(self.jobs.jobs[self.task]['callback']['status'], 'requested')

    def test_unanswered_outcome_retains_report_and_does_not_redial(self):
        _, request = self.post('/callbacks/claim')
        body = json.dumps({'callback_id': request['callback_id'], 'outcome': 'unanswered'}).encode()
        self.assertEqual(self.post('/callbacks/finish', body=body), (200, {}))
        self.assertEqual(self.jobs.jobs[self.task]['callback']['status'], 'not_reported')
        self.assertFalse(self.jobs.jobs[self.task]['announced'])
        self.assertEqual(self.post('/callbacks/claim'), (200, {}))

    def test_mark_start_cannot_attach_michael_callback(self):
        _, request = self.post('/callbacks/claim')
        body = json.dumps({'callback_id': request['callback_id']}).encode()
        self.assertEqual(self.post('/start', number='639267200480', body=body)[0], 403)
        self.assertIsNone(self.fixture.ns['active'])


if __name__ == '__main__':
    unittest.main()
