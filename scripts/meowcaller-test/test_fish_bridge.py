"""Exercise the native action fence without provider calls or real user data."""
import ast
import json
import threading
import time
import unittest
from pathlib import Path
from uuid import uuid4

source = ast.parse(((Path(__file__).parents[1] / 'whatsapp_fish.py').read_text() + '\n' + (Path(__file__).parents[1] / 'fish_conversation.py').read_text()))
node = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'NativeTaskBridge')
scope = dict(threading=threading, time=time, uuid4=uuid4)
exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual native task bridge>', 'exec'), scope)
NativeTaskBridge = scope['NativeTaskBridge']


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.actions = []
        self.admitted = True
        self.submission_gate = None
        self.submitted = threading.Event()
        self.events = [{'cursor': 1, 'running': False, 'events': [
            {'type': 'message.complete', 'payload': {'text': 'Thirteen.', 'status': 'complete'}}]}]
        self.fail_submit = False
        def native(action, call, **extra):
            self.actions.append((action, call, extra))
            if action == 'voice_submit':
                self.submitted.set()
                if self.submission_gate:
                    self.submission_gate.wait(3)
                if self.fail_submit:
                    raise RuntimeError('uncertain fixture submission')
                return {'epoch': 'fixture', 'cursor': 0}
            if action == 'voice_events':
                return self.events.pop(0)
            return {}
        self.bridge = NativeTaskBridge('exact-call', native, lambda call: self.admitted and call == 'exact-call')
        self.event = {'type': 'client_tool.call', 'toolName': 'native_leo', 'callId': 'fixture-tool-1',
                      'expectsResponse': True, 'params': {'request': 'Check a synthetic arithmetic question.'}}

    def count(self, action):
        return sum(a[0] == action for a in self.actions)

    def test_completed_result_reuses_exact_id_without_repeating_native(self):
        first = self.bridge.execute(self.event)
        self.assertEqual(first['result']['status'], 'complete')
        self.assertEqual(first['result']['message'], 'Thirteen.')
        self.assertEqual(self.bridge.execute(self.event), first)
        self.assertEqual(self.count('voice_submit'), 1)
        self.assertEqual(self.count('voice_finish'), 1)
        self.assertEqual(self.count('voice_stop'), 0)
        self.assertTrue(all(a[1] == 'exact-call' for a in self.actions))

    def test_duplicate_during_submission_does_not_start_second_action(self):
        self.submission_gate = threading.Event()
        thread = threading.Thread(target=self.bridge.execute, args=(self.event,))
        thread.start(); self.assertTrue(self.submitted.wait(2))
        self.assertIsNone(self.bridge.execute(self.event))
        self.submission_gate.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.count('voice_submit'), 1)

    def test_same_request_with_new_packet_id_returns_previous_result_without_execution(self):
        self.bridge.execute(self.event)
        repeat = self.bridge.execute({**self.event, 'callId': 'new-packet-id'})
        self.assertEqual(repeat['callId'], 'new-packet-id')
        self.assertTrue(repeat['result']['previously_dispatched'])
        self.assertFalse(repeat['result']['retry_action'])
        self.assertEqual(self.count('voice_submit'), 1)

    def test_hangup_or_inactive_admission_cannot_dispatch(self):
        self.bridge.cancel(close=True)
        self.assertTrue(self.bridge.execute(self.event)['isError'])
        self.assertFalse(self.actions)
        self.setUp(); self.admitted = False
        self.assertTrue(self.bridge.execute(self.event)['isError'])
        self.assertFalse(self.actions)

    def test_cancel_during_uncertain_submission_stops_exact_turn_once(self):
        self.submission_gate = threading.Event()
        results = []
        thread = threading.Thread(target=lambda: results.append(self.bridge.execute(self.event)))
        thread.start(); self.assertTrue(self.submitted.wait(2))
        stop = threading.Thread(target=self.bridge.cancel)
        stop.start()
        deadline = time.monotonic() + 2
        while not self.bridge.records[self.event['callId']]['cancel'].is_set() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.bridge.records[self.event['callId']]['cancel'].is_set())
        self.submission_gate.set(); thread.join(3); stop.join(3)
        self.assertEqual(results[0]['result']['status'], 'interrupted')
        self.assertFalse(results[0]['result']['retry_action'])
        self.assertEqual(self.count('voice_submit'), 1)
        self.assertEqual(self.count('voice_stop'), 1)
        submitted_turn = next(a[2]['turn_id'] for a in self.actions if a[0] == 'voice_submit')
        self.assertEqual(next(a[2]['turn_id'] for a in self.actions if a[0] == 'voice_stop'), submitted_turn)

    def test_busy_result_stays_rejected_after_first_task_finishes(self):
        self.submission_gate = threading.Event()
        thread = threading.Thread(target=self.bridge.execute, args=(self.event,))
        thread.start(); self.assertTrue(self.submitted.wait(2))
        other = {**self.event, 'callId': 'fixture-tool-2', 'params': {'request': 'Check a different synthetic question.'}}
        rejected = self.bridge.execute(other)
        self.assertEqual(rejected['result']['status'], 'previous_task_still_settling')
        self.submission_gate.set(); thread.join(3)
        self.assertEqual(self.bridge.execute(other), rejected)
        self.assertEqual(self.count('voice_submit'), 1)

    def test_native_snapshot_replacement_returns_final_without_fake_prefix(self):
        self.events = [{'cursor': 2, 'running': False, 'events': [
            {'type': 'message.delta', 'payload': {'text': 'Draft phrase'}},
            {'type': 'message.complete', 'payload': {'text': 'Correct verified result.', 'status': 'complete'}}]}]
        self.assertEqual(self.bridge.execute(self.event)['result']['message'], 'Correct verified result.')

    def test_review_is_returned_to_fish_without_any_approval_dispatch(self):
        self.events = [{'cursor': 1, 'running': True, 'review': True, 'events': []}]
        result = self.bridge.execute(self.event)['result']
        self.assertEqual(result['status'], 'approval_required')
        self.assertFalse(result['retry_action'])
        self.assertEqual(self.count('voice_submit'), 1)
        self.assertEqual(self.count('voice_stop'), 1)
        self.assertFalse(any('approval' in a[0] for a in self.actions))

    def test_uncertain_submission_failure_is_never_retried(self):
        self.fail_submit = True
        result = self.bridge.execute(self.event)
        self.assertEqual(result['result']['status'], 'unavailable')
        self.assertEqual(self.bridge.execute(self.event), result)
        self.assertEqual(self.count('voice_submit'), 1)
        self.assertEqual(self.count('voice_stop'), 1)

    def test_invalid_client_payload_does_not_touch_native(self):
        for event in [{**self.event, 'toolName': 'unknown'}, {**self.event, 'expectsResponse': False},
                      {**self.event, 'params': {'request': '/approve'}},
                      {**self.event, 'params': {'request': 'x' * 9001}},
                      {**self.event, 'params': {'request': 7}}]:
            self.assertTrue(self.bridge.execute(event)['isError'])
        self.assertFalse(self.actions)


if __name__ == '__main__':
    unittest.main()
