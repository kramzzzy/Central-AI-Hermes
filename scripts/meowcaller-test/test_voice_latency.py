"""Phone startup and streaming boundaries; no provider keys or native state."""
import ast
import json
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path


def owned_class(name, namespace):
    namespace.setdefault('CONVERSATION_MODE', 'native')
    path = Path('/opt/setup/whatsapp-call-voice.py')
    if not path.exists():
        path = Path(__file__).parents[1] / 'whatsapp-call-voice.py'
    node = next(n for n in ast.parse(path.read_text(encoding='utf-8')).body
                if isinstance(n, ast.ClassDef) and n.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<owned phone class>', 'exec'), namespace)
    return namespace[name]


class NativeWarmupTests(unittest.TestCase):
    def test_unused_business_call_lease_never_resumes_an_unpersisted_runtime_id(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            saved = data / 'session.json'
            saved.write_text(json.dumps({'id': 'unpersisted-runtime-id'}))
            calls = []
            class FakeNative:
                def start(self):pass
                def resume(self, *args):raise AssertionError('Empty business lease resumed as persisted history')
                def restore_voice_effort(self, **kwargs):return True
                def restore_voice_model(self, **kwargs):return True
                def scoped(self, **kwargs):return kwargs
                def rpc(self, method, params, **kwargs):
                    calls.append(method)
                    if method == 'session.create':
                        return {'session_id': 'fresh-runtime', 'info': {}}
                    if method == 'session.activate':return {'info': {'lazy': False, 'running': False}}
                    raise AssertionError('Unexpected call lease operation')
            phone = owned_class('PhoneChat', dict(NativeChat=FakeNative, json=json, time=time))()
            phone.root = data / 'native'
            phone.settings = {'HERMES_PROFILE': 'team-whatsapp-michael-business', 'HERMES_PHONE_NAME': 'Michael'}
            phone.connect_guard = threading.Lock()
            phone.sid = None
            phone.connect()
            self.assertEqual(calls, ['session.create', 'session.activate'])
            self.assertEqual(json.loads(saved.read_text())['id'], 'unpersisted-runtime-id')

    def test_fish_tasks_keep_native_reasoning_without_a_config_write(self):
        calls = []
        class FakeNative:
            def scoped(self, **kwargs):
                return kwargs
            def rpc(self, method, params):
                calls.append((method, params))
                if method != 'config.get':
                    raise AssertionError('Fish business task changed native configuration')
                return {'value': 'medium'}
        phone = owned_class('PhoneChat', dict(NativeChat=FakeNative,
            CONVERSATION_MODE='fish'))()
        self.assertEqual(phone.prepare_voice_effort({'model': 'existing-model'}), ('medium', None))
        self.assertEqual(calls, [('config.get', {'key': 'reasoning'})])

    def test_resume_is_read_only_and_does_not_reload_history(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            (data / 'session.json').write_text(json.dumps({'id': 'private-phone-session'}))
            calls = []

            class FakeNative:
                def start(self):
                    calls.append('start')
                def resume(self, profile, session):
                    calls.append(('resume', profile, session))
                    return {'session_id': 'runtime-session', 'stored_session_id': session,
                            'info': {'model': 'existing-model'}}
                def restore_voice_effort(self, **kwargs):
                    calls.append('restore-effort')
                    return True
                def restore_voice_model(self, **kwargs):
                    calls.append('restore-model')
                    return True
                def scoped(self, **kwargs):
                    return {'session_id': self.sid, **kwargs}
                def rpc(self, method, params):
                    calls.append((method, params))
                    if method != 'session.activate':
                        raise AssertionError('Warmup attempted a prompt or history reload')
                    return {'info': {'running': False}}

            phone = owned_class('PhoneChat', dict(NativeChat=FakeNative, DATA=data, json=json, time=time))()
            phone.connect_guard = threading.Lock()
            phone.root = data / 'native'
            phone.settings = {'HERMES_PROFILE': 'leo'}
            phone.sid = None
            phone.info = {}
            result = phone.connect()
            self.assertEqual(phone.stored, 'private-phone-session')
            self.assertFalse(result['info']['running'])
            self.assertNotIn('messages', result)
            self.assertEqual(calls[-1], ('session.activate',
                {'session_id': 'runtime-session', 'omit_messages': True}))
            calls.clear()
            phone.connect()
            self.assertEqual(len(calls), 2)


class TextBoundaryTests(unittest.TestCase):
    def setUp(self):
        cls = owned_class('StreamingSpeech', dict(threading=threading, re=re, time=time))
        self.stream = cls.__new__(cls)
        self.stream.pending_text = self.stream.unflushed = ''
        self.stream.first_text = self.stream.first_flush = None
        self.stream.completed = True
        self.stream.error = False
        self.stream.done = threading.Event()
        self.stream.done.set()
        self.events = []
        self.stream.send = self.events.append

    def test_first_phrase_flushes_before_long_sentence_finishes(self):
        for delta in ('Please', ' tell', ' me', ' the', ' det', 'ails', ' of'):
            self.stream.text(delta)
        self.assertTrue(any(e['event'] == 'flush' for e in self.events))
        sent = ''.join(e['text'] for e in self.events if e['event'] == 'text')
        self.assertEqual(sent, 'Please tell me the details ')
        self.assertEqual(self.stream.pending_text, 'of')
        self.stream.text(' the team meeting.')
        self.stream.finish()
        self.assertEqual(''.join(e['text'] for e in self.events if e['event'] == 'text'),
                         'Please tell me the details of the team meeting.')

    def test_decimal_and_split_word_are_not_sent_partway(self):
        self.stream.text('The number is 3.')
        self.assertEqual(self.stream.pending_text, '3.')
        self.stream.text('14 and thir')
        self.stream.text('teen')
        self.stream.finish()
        self.assertEqual(''.join(e['text'] for e in self.events if e['event'] == 'text'),
                         'The number is 3.14 and thirteen')
        self.assertFalse(any(e.get('text', '').endswith('thir') for e in self.events))

    def test_short_complete_reply_flushes_immediately(self):
        self.stream.text('Thir')
        self.assertFalse(self.events)
        self.stream.text('teen.')
        self.assertEqual(self.events, [{'event': 'text', 'text': 'Thirteen.'}, {'event': 'flush'}])


if __name__ == '__main__':
    unittest.main()
