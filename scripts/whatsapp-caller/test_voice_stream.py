"""Real HTTP handler tests with fake model/native/speech, no keys or native state."""
import ast
import hmac
import json
import re
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
import numpy as np


class StreamTests(unittest.TestCase):
    def setUp(self):
        source_path = Path('/opt/setup/whatsapp-call-voice.py')
        if not source_path.exists():
            source_path = Path(__file__).parents[1] / 'whatsapp-call-voice.py'
        source = ast.parse(source_path.read_text())
        handler = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'Handler')
        recognition = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'recognize')
        state = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == 'TurnState')
        incomplete = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'incomplete_utterance')
        self.spoken = []
        self.actions = []
        self.batches = []
        self.submitted = []
        self.recognition_options = []
        self.finish_fails = False
        self.transcribe_gate = None
        self.transcribing = threading.Event()
        self.segments = [SimpleNamespace(text='Test a reply', avg_logprob=-0.2,
                                        no_speech_prob=0.01, compression_ratio=1.2)]
        parent = self
        class FakeSpeech:
            def __init__(self, handler):
                self.handler = handler; self.done = threading.Event(); self.error = False; self.first_audio = None
            def text(self, text):
                parent.spoken.append(text); self.first_audio = self.first_audio or time.monotonic()
                self.handler.audio_chunk(b'\0' * 1920)
            def finish(self):
                if parent.finish_fails:
                    raise RuntimeError('fixture speech failure')
                self.done.set()
            def cancel(self):
                self.done.set()
        def native(action, call, **extra):
            self.actions.append(action)
            if action == 'voice_submit':
                self.submitted.append(extra['text'])
                return {'cursor': 0, 'epoch': 'fixture'}
            if action == 'voice_events':
                return self.batches.pop(0)
            return {}
        def transcribe(samples, **options):
            self.recognition_options.append(options)
            self.transcribing.set()
            if self.transcribe_gate:
                self.transcribe_gate.wait(3)
            return self.segments, SimpleNamespace(duration_after_vad=len(samples)/16000)
        model = SimpleNamespace(transcribe=transcribe)
        self.call = str(uuid4())
        self.ns = dict(BaseHTTPRequestHandler=BaseHTTPRequestHandler, hmac=hmac, json=json, re=re,
            threading=threading, time=time, np=np, uuid4=uuid4, KEY='fixture-key',
            native=native, model=model, StreamingSpeech=FakeSpeech, active=self.call,
            guard=threading.RLock(), turn_guard=threading.Lock(), turns=0, last_metrics={}, last_recognition={},
            turn_records={}, audio_metrics={}, failure_counts={}, snapshot_changes=0, interruptions=0,
            active_caller='639267200480',
            chat=SimpleNamespace(voice_call='fixture', info={}), fish_service=None, CONVERSATION_MODE='native')
        exec(compile(ast.Module(body=[state, incomplete, recognition, handler], type_ignores=[]), '<owned HTTP handler>', 'exec'), self.ns)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), self.ns['Handler'])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown(); self.server.server_close()

    def turn(self, data=b'\0'*6400, streamed=True, turn_id=None, final=False):
        req = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/turn', data=data,
            headers={'Authorization': 'Bearer fixture-key', 'X-Call-ID': self.call,
                     'X-Turn-ID': turn_id or str(uuid4()), 'X-Voice-Final': 'true' if final else 'false'})
        with urllib.request.urlopen(req, timeout=5) as response:
            if streamed:
                self.assertEqual(response.headers['Transfer-Encoding'], 'chunked')
            return response.read()

    def test_english_request_uses_explicit_language_and_verbatim_transcript(self):
        self.segments[0].text = 'What is six plus seven?'
        self.batches = [{'cursor': 1, 'running': False, 'events': [
            {'type': 'message.complete', 'payload': {'text': 'Thirteen.', 'status': 'complete'}}]}]
        self.turn()
        self.assertEqual(self.submitted, ['What is six plus seven?'])
        options = self.recognition_options[0]
        self.assertEqual(options['language'], 'en')
        self.assertEqual(options['task'], 'transcribe')
        self.assertEqual(options['temperature'], 0)
        self.assertEqual(options['beam_size'], 5)
        self.assertFalse(options['vad_filter'])
        self.assertFalse(options['condition_on_previous_text'])
        self.assertNotIn('Tagalog', options['initial_prompt'])

    def test_uncertain_segment_cannot_submit_partial_or_guessed_actions(self):
        for field, score in [('avg_logprob', -1.1), ('no_speech_prob', 0.7), ('compression_ratio', 2.5)]:
            with self.subTest(field=field):
                uncertain = SimpleNamespace(text='Send the message now.', avg_logprob=-0.2,
                                            no_speech_prob=0.01, compression_ratio=1.2)
                setattr(uncertain, field, score)
                self.segments = [self.segments[0], uncertain]
                before = len(self.spoken)
                self.assertTrue(self.turn())
                self.assertIn('Could you repeat it?', ''.join(self.spoken[before:]))
                self.assertEqual(self.ns['last_recognition']['decision'], 'clarify')
        self.assertFalse(self.actions)
        self.assertFalse(self.submitted)
        self.assertEqual(self.ns['turns'], 0)

    def test_empty_recognition_with_audio_requests_repeat_but_silence_stays_quiet(self):
        self.segments = []
        speech = np.full(3200, 2000, dtype='<i2').tobytes()
        self.assertTrue(self.turn(speech))
        self.assertIn('Could you repeat it?', ''.join(self.spoken))
        self.assertEqual(self.ns['last_recognition']['decision'], 'clarify')
        self.spoken.clear()
        self.assertEqual(self.turn(streamed=False), b'')
        self.assertEqual(self.ns['last_recognition']['decision'], 'silence')
        self.assertFalse(self.spoken)
        self.assertFalse(self.actions)

    def test_streamed_interim_is_not_repeated_and_final_follows(self):
        self.batches = [
            {'cursor': 2, 'running': True, 'events': [
                {'type': 'message.delta', 'payload': {'text': 'Checking tasks.'}},
                {'type': 'message.interim', 'payload': {'text': 'Checking tasks.', 'already_streamed': True}}]},
            {'cursor': 3, 'running': False, 'events': [
                {'type': 'thinking.delta', 'payload': {'text': 'PRIVATE REASONING'}},
                {'type': 'message.complete', 'payload': {'text': 'Three tasks are due.', 'status': 'complete'}}]},
        ]
        self.assertTrue(self.turn())
        self.assertEqual(''.join(self.spoken), 'Checking tasks. Three tasks are due.')
        self.assertEqual(self.actions.count('voice_submit'), 1)
        self.assertIn('voice_finish', self.actions)
        self.assertTrue(self.ns['last_metrics']['native_complete'])

    def test_partial_native_failure_stops_without_resubmitting(self):
        self.batches = [{'cursor': 2, 'running': False, 'events': [
            {'type': 'message.delta', 'payload': {'text': 'Started checking.'}},
            {'type': 'turn.error', 'payload': {}}]}]
        self.assertTrue(self.turn())
        self.assertEqual(self.actions.count('voice_submit'), 1)
        self.assertIn('voice_stop', self.actions)
        self.assertNotIn('voice_finish', self.actions)

    def test_changed_final_snapshot_keeps_spoken_words_and_completes(self):
        self.batches = [{'cursor': 2, 'running': False, 'events': [
            {'type': 'message.delta', 'payload': {'text': 'The answer is thirteen.'}},
            {'type': 'message.complete', 'payload': {'text': 'Thirteen.', 'status': 'complete'}}]}]
        self.turn()
        self.assertEqual(''.join(self.spoken), 'The answer is thirteen.')
        self.assertEqual(self.ns['snapshot_changes'], 1)
        self.assertTrue(self.ns['last_metrics']['native_complete'])
        self.assertNotIn('voice_stop', self.actions)

    def test_speech_failure_allows_next_turn_on_same_call_without_replay(self):
        self.finish_fails = True
        self.batches = [{'cursor': 1, 'running': False, 'events': [
            {'type': 'message.complete', 'payload': {'text': 'First answer.', 'status': 'complete'}}]}]
        self.turn()
        self.assertEqual(self.ns['active'], self.call)
        self.assertEqual(self.ns['failure_counts']['speech_finish'], 1)
        self.finish_fails = False
        self.batches = [{'cursor': 2, 'running': False, 'events': [
            {'type': 'message.complete', 'payload': {'text': 'Second answer.', 'status': 'complete'}}]}]
        self.turn()
        self.assertEqual(self.actions.count('voice_submit'), 2)
        self.assertNotIn('voice_end', self.actions)
        self.assertIn('Second answer.', ''.join(self.spoken))

    def test_unfinished_thought_waits_and_requests_completion_without_action(self):
        self.segments[0].text = 'Please send a message to'
        self.assertEqual(self.turn(streamed=False), b'')
        self.assertFalse(self.submitted)
        self.turn(final=True)
        self.assertIn('finish that thought', ''.join(self.spoken))
        self.assertFalse(self.submitted)

    def test_interrupt_during_recognition_fences_native_submission(self):
        self.transcribe_gate = threading.Event()
        turn = str(uuid4())
        errors = []
        def pending():
            try:
                self.turn(turn_id=turn, streamed=False)
            except urllib.error.HTTPError as error:
                errors.append(error.code)
        worker = threading.Thread(target=pending)
        worker.start()
        self.assertTrue(self.transcribing.wait(1))
        req = urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}/interrupt', data=turn.encode(),
            headers={'Authorization': 'Bearer fixture-key', 'X-Call-ID': self.call})
        result = []
        stop = threading.Thread(target=lambda: result.append(json.load(urllib.request.urlopen(req, timeout=5))))
        stop.start()
        deadline = time.monotonic()+1
        while not self.ns['turn_records'][turn].cancelled.is_set() and time.monotonic()<deadline:
            time.sleep(.005)
        self.transcribe_gate.set()
        worker.join(2); stop.join(2)
        self.assertEqual(result, [{'native_started': False}])
        self.assertEqual(errors, [503])
        self.assertFalse(self.submitted)
        self.assertNotIn('voice_end', self.actions)

    def test_same_turn_id_is_never_submitted_twice(self):
        turn = str(uuid4())
        self.batches = [{'cursor': 1, 'running': False, 'events': [
            {'type': 'message.complete', 'payload': {'text': 'Done.', 'status': 'complete'}}]}]
        self.turn(turn_id=turn)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.turn(turn_id=turn)
        self.assertEqual(caught.exception.code, 409)
        self.assertEqual(self.actions.count('voice_submit'), 1)

    def test_real_approval_wait_is_spoken_once_without_connection_failure(self):
        self.batches = [{'cursor': 1, 'running': True, 'review': True, 'events': []}]
        self.turn()
        self.assertIn('needs your approval', ''.join(self.spoken))
        self.assertEqual(self.actions.count('voice_submit'), 1)
        self.assertEqual(self.actions.count('voice_stop'), 1)
        self.assertFalse(self.ns['last_metrics']['native_complete'])
        self.assertFalse(self.ns['failure_counts'])


if __name__ == '__main__':
    unittest.main()
