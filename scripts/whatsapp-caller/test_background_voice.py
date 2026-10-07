"""Voice remains independent of worker completion and never treats runtime data as intent."""
import ast
import asyncio
import json
import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

source = ast.parse(((Path(__file__).parents[1] / 'whatsapp_fish.py').read_text() + '\n' + (Path(__file__).parents[1] / 'fish_conversation.py').read_text()))
node = next(n for n in source.body
            if isinstance(n, ast.ClassDef) and n.name == 'FishCall')
scope = dict(asyncio=asyncio, json=json, time=time, re=re)
helpers = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in {'business_connection_limit', 'is_correction', 'is_task_cancellation'}]
from whatsapp_routing import routing
scope['routing']=routing
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
        self.call.member = {'number': '61423947456'}
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

if __name__ == '__main__':
    unittest.main()
