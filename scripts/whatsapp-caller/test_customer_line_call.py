"""Customer-line calls use the customer agent's persona and voice, with no tools and no owner session."""
import asyncio
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
sys.modules.setdefault('whatsapp_audio', types.SimpleNamespace(PhoneAudioFilter=lambda vad: None))
import whatsapp_stream  # noqa: E402

ROUTING = {'owner': '61400000001', 'contacts': [], 'company_name': 'Test Solar',
           'customer_agent': 'James', 'customer_agent_role': 'Customer Service',
           'customer_agent_instructions': 'Be friendly with a light sense of humour.',
           'customer_agent_knowledge': [{'title': 'Battery offer', 'category': 'Sales', 'content': 'Batteries store daytime solar.'}]}


class FakeService:
    config = {'FISH_VOICE_ID': 'owner-voice', 'FISH_ENGLISH_FALLBACK_VOICE_ID': 'owner-voice'}
    vad = None
    metrics = {}

    def native_conversation(self, call, member):
        raise AssertionError('customer calls must never open the owner native session')


class CustomerLineCallTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(whatsapp_stream, 'routing', lambda: ROUTING)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {'CUSTOMER_FISH_VOICE_ID': 'james-voice'})
        env.start()
        self.addCleanup(env.stop)

    def call(self, **member):
        return whatsapp_stream.StreamPhoneCall(FakeService(), 'call-1', member={'number': '61400000002', 'jobs': object(), **member})

    def test_customer_call_uses_customer_agent_voice_and_no_tools(self):
        call = self.call(line='customer', role='customer')
        self.assertEqual('james-voice', call.speech_config['FISH_VOICE_ID'])
        self.assertEqual('james-voice', call.speech_config['FISH_ENGLISH_FALLBACK_VOICE_ID'])
        self.assertEqual('owner-voice', FakeService.config['FISH_VOICE_ID'], 'shared config must not change')
        conv = call._get_fallback_conversation()
        self.assertEqual(set(), conv.names)
        self.assertIn('You are James, Customer Service for Test Solar', conv.prompt)
        self.assertIn('light sense of humour', conv.prompt)
        self.assertIn('Batteries store daytime solar.', conv.prompt)
        self.assertNotIn('manage_crm', conv.prompt)
        self.assertNotIn('Current workspace workflow', conv.prompt)
        self.assertEqual('openai/gpt-4o-mini', conv.config['AGENT_MODEL'], 'customer calls use the fast model')
        asyncio.run(call._warm_native_conversation())
        self.assertIsNone(call.conversation)

    def test_owner_line_call_is_unchanged(self):
        call = self.call(name='Owner', role='owner', profile='leo')
        self.assertIs(FakeService.config, call.speech_config)
        self.assertIn('send_whatsapp_message', call._get_fallback_conversation().names)

    def test_customer_without_voice_setting_keeps_default_voice(self):
        with patch.dict(os.environ, {'CUSTOMER_FISH_VOICE_ID': ''}):
            cfg = self.call(line='customer').speech_config
            self.assertEqual('a30d099a643d4173836cfee1d8ae6c13', cfg['FISH_VOICE_ID'])
            self.assertEqual('fish', cfg['CENTRAL_AI_CALL_SPEECH'])

    def test_customer_legacy_or_unset_voice_uses_michael_voice(self):
        for legacy_id in (
            '711cf3ed00ab441a8f54a45058047b7a',
            'bf342d9e9e5b44e0ade1ccfd1abb4815',
            '612b878b113047d9a770c069c8b4fdfe',
            '31d3dd937d2944a1b06da3db533d27f9',
        ):
            with patch.dict(os.environ, {'CUSTOMER_FISH_VOICE_ID': legacy_id}):
                self.assertEqual('a30d099a643d4173836cfee1d8ae6c13', self.call(line='customer').speech_config['FISH_VOICE_ID'])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CUSTOMER_FISH_VOICE_ID', None)
            self.assertEqual('a30d099a643d4173836cfee1d8ae6c13', self.call(line='customer').speech_config['FISH_VOICE_ID'])

    def test_customer_call_injects_proactive_solar_calculation_on_bill_turn(self):
        call = self.call(line='customer', role='customer')
        conv = call._get_fallback_conversation()
        sys_prompt = conv._system_prompt_for_turn('My last electricity bill was $600 a quarter')
        self.assertIn('OFFICIAL YOUR CHOICE SOLAR WEBSITE CALCULATOR', sys_prompt)
        self.assertIn('PROACTIVE VALUE FIRST', sys_prompt)

    def test_customer_call_prepare_speech_and_fish_rest_fallback_emit_pcm(self):
        import stream_conversation
        emitted = []
        async def _check():
            call = self.call(line='customer', role='customer')
            call.prepare_speech()
            control, speech, _framer, _output = call.prepared_speech
            self.assertEqual('a30d099a643d4173836cfee1d8ae6c13', speech.config['FISH_VOICE_ID'])
            control.cancel()
            rest_control = stream_conversation.TurnControl()
            rest_speech = stream_conversation.FishRestSpeech(
                call.speech_config,
                lambda data, final=False: emitted.append((data, final)),
                rest_control,
                rate=16000,
            )
            rest_speech.text('Hi there! James speaking from Test Solar.')
            rest_speech.finish()

        def _fake_proxy_tts(_text, _voice_id, _rate, audio, _control=None):
            if audio:
                audio(b'\x01\x02' * 480)
            return b''

        with patch.dict(os.environ, {'HERMES_PHONE_CONVERSATION': 'true', 'CUSTOMER_FISH_VOICE_ID': ''}), \
             patch.object(stream_conversation, '_hermes_proxy_tts', side_effect=_fake_proxy_tts) as mock_proxy:
            asyncio.run(_check())
            self.assertTrue(mock_proxy.called)
            self.assertTrue(any(chunk == b'\x01\x02' * 480 for chunk, _ in emitted))

    def test_customer_call_prompt_is_honest_when_asked_and_has_no_scripted_openers(self):
        prompt = self.call(line='customer', role='customer')._get_fallback_conversation().prompt
        self.assertIn('sincerely asks whether they are talking to a real person, answer honestly', prompt)
        self.assertNotIn('NEVER say or imply that you are an AI', prompt)
        self.assertNotIn('Oh, no worries at all.', prompt)
        self.assertIn('Be friendly with a light sense of humour.', prompt, 'persona comes from the agent profile')

    def test_spoken_caller_name_from_this_is_but_not_from_ordinary_sentences(self):
        extract = whatsapp_stream._extract_spoken_caller_name
        self.assertEqual('Mark', extract('Hey James, this is Mark. I would like to inquire about solar.'))
        self.assertEqual('', extract('This is for the new system.'))
        self.assertEqual('', extract('this is great, thanks'))
        self.assertEqual('', extract("Hey, what's up James? I am inquiry."))
        self.assertEqual('', extract("I'm inquiring about solar"))
        self.assertEqual('Mark Ebio', extract("Hi, I'm Mark Ebio."))

    def test_short_opener_is_spoken_before_the_rest_of_the_sentence_arrives(self):
        import stream_conversation
        queued = []
        speech = stream_conversation.FishRestSpeech.__new__(stream_conversation.FishRestSpeech)
        speech.pending, speech.first_flush = '', None
        speech.control = stream_conversation.TurnControl()
        speech._enqueue = lambda clause: (queued.append(clause), setattr(speech, 'first_flush', 1))
        speech.text('Got it! Roughly how')
        self.assertEqual(['Got it!'], queued)

    def test_only_detail_turns_wait_for_cloud_transcription(self):
        precise = whatsapp_stream._needs_precise_transcript
        self.assertFalse(precise('Do you install batteries as well', 'Hi there! How can I help?'))
        self.assertFalse(precise("Yeah, that's right.", 'Is that name and address all right?'))
        self.assertTrue(precise('Mark Anthony Ebio', "Lovely, and what's your full name?"))
        self.assertTrue(precise('I live at 12 Smith Street'))
        self.assertTrue(precise('it is mark at gmail dot com'))

    def test_fish_rest_speech_plays_clauses_in_order_while_next_synthesizes(self):
        import time
        import stream_conversation
        started, emitted = [], []

        def fake_tts(text, _config, _rate, audio, control=None):
            started.append(time.monotonic())
            time.sleep(.3 if text.startswith('Right') else .02)
            audio(text.encode())

        with patch.object(stream_conversation, 'fish_rest_tts', side_effect=fake_tts):
            speech = stream_conversation.FishRestSpeech({'FISH_API_KEY': 'k', 'FISH_VOICE_ID': 'v'},
                                                        emitted.append, stream_conversation.TurnControl(), rate=16000)
            speech.text('Right, gotcha. A six kilowatt system would suit a bill that size nicely. ')
            speech.finish()
        self.assertEqual([b'Right, gotcha.', b'A six kilowatt system would suit a bill that size nicely.'], emitted)
        self.assertLess(started[1] - started[0], .2, 'next clause synthesizes while the opener is still running')

    def test_repeated_opener_plays_from_cache_without_waiting_for_fish(self):
        import stream_conversation
        calls, emitted = [], []

        def fake_tts(text, _config, _rate, audio=None, control=None):
            calls.append(text)
            pcm = text.encode().ljust(32, b'\0')[:32]
            if audio is None:
                return pcm
            audio(pcm)

        config = {'FISH_API_KEY': 'k', 'FISH_VOICE_ID': 'cache-test-voice'}
        with patch.object(stream_conversation, 'fish_rest_tts', side_effect=fake_tts), \
             patch.dict(stream_conversation._opener_audio, clear=True):
            for _ in range(2):
                speech = stream_conversation.FishRestSpeech(config, emitted.append,
                                                            stream_conversation.TurnControl(), rate=16000)
                speech.text('Right, gotcha! How big was your last power bill? ')
                speech.finish()
        self.assertEqual(['Right, gotcha!', 'How big was your last power bill?'], calls, 'second reply never waits on Fish')
        self.assertEqual(emitted[:2], emitted[2:], 'second reply reuses the first audio exactly')

    def test_phone_transcription_uses_openrouter_speech_to_text_endpoint(self):
        import base64
        import io
        import json
        import phone_cloud_asr
        from stream_conversation import TurnControl
        sent = []

        class Reply(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(request, timeout):
            sent.append((request.full_url, json.loads(request.data), timeout))
            return Reply(b'{"text": "Hi, this is Mark.", "usage": {"seconds": 1}}')

        recognizer = phone_cloud_asr.CloudRecognizer({'OPENROUTER_API_KEY': 'or-key'})
        self.assertEqual(('openai/whisper-large-v3-turbo', 'openai/gpt-4o-mini-transcribe'),
                         (recognizer.primary, recognizer.fallback))
        with patch.object(phone_cloud_asr, 'open_request', side_effect=fake_open):
            text, quality = recognizer.recognize(b'\x00\x10' * 1600, TurnControl())
        self.assertEqual(('Hi, this is Mark.', 'accepted'), (text, quality['decision']))
        url, body, timeout = sent[0]
        self.assertEqual('https://openrouter.ai/api/v1/audio/transcriptions', url)
        self.assertEqual('openai/whisper-large-v3-turbo', body['model'])
        self.assertEqual('wav', body['input_audio']['format'])
        self.assertTrue(base64.b64decode(body['input_audio']['data']).startswith(b'RIFF'))
        self.assertLessEqual(timeout, 2.5)

    def test_reply_keeps_michael_voice_when_a_later_clause_fails(self):
        import stream_conversation
        emitted, offline = [], []

        def fake_tts(text, _config, _rate, audio=None, control=None):
            if text.startswith('Second'):
                raise RuntimeError('Fish busy')
            audio(b'FISH')

        with patch.object(stream_conversation, 'fish_rest_tts', side_effect=fake_tts), \
             patch.object(stream_conversation.time, 'sleep'), \
             patch.dict(stream_conversation._opener_audio, clear=True), \
             patch('native_call_speech.piper_available', return_value=True), \
             patch('native_call_speech.pcm', side_effect=lambda *a, **k: offline.append(a[0]) or [b'PIPER']):
            speech = stream_conversation.FishRestSpeech({'FISH_API_KEY': 'k', 'FISH_VOICE_ID': 'michael-voice'},
                                                        emitted.append, stream_conversation.TurnControl(), rate=16000)
            speech.lanes = __import__('threading').BoundedSemaphore(1)  # deterministic order for the test
            speech.text('First clause is spoken by Fish here. Second clause fails at Fish twice. ')
            speech.finish()
        self.assertEqual([b'FISH'], emitted, 'no offline voice once Michael has spoken in the reply')
        self.assertEqual([], offline)
        self.assertEqual(1, speech.skipped_clauses)

    def test_fish_400_for_one_text_does_not_switch_the_process_model(self):
        import stream_conversation

        class Resp:
            def __init__(self, code):
                self.status_code = code

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def raise_for_status(self):
                pass

            def iter_bytes(self, chunk_size=1920):
                return iter([b'\x01\x02'])

        models = []

        class Client:
            def stream(self, method, url, content, headers, timeout):
                models.append(headers['model'])
                return Resp(422 if headers['model'] == 's1' and len(models) == 1 else 200)

        with patch('voice_http.client', return_value=Client()), \
             patch.object(stream_conversation, '_fish_failed_models', set()), \
             patch.object(stream_conversation, '_fish_working_model', None):
            for _ in range(2):
                stream_conversation.fish_rest_tts('Hello there.', {'FISH_API_KEY': 'k', 'FISH_VOICE_ID': 'v'},
                                                  16000, lambda pcm: None)
            self.assertNotIn('s1', stream_conversation._fish_failed_models)
        self.assertEqual(['s1', 's2.1-pro-free', 's1'], models, 'the next clause goes back to the configured model')

    def test_discarded_cloud_recognition_frees_its_slot(self):
        import threading
        import phone_cloud_asr
        from stream_conversation import TurnControl
        gate = threading.Event()
        recognizer = phone_cloud_asr.CloudRecognizer(
            {'OPENROUTER_API_KEY': 'k'}, request=lambda model, pcm, control: gate.wait(2) and {'text': 'hello'})
        pcm = b'\x00\x10' * 1600
        stale = [recognizer.prepare(pcm, TurnControl()) for _ in range(4)]
        self.assertIsNone(recognizer.prepare(pcm, TurnControl()))
        for prepared in stale:
            prepared.close()
        fresh = recognizer.prepare(pcm, TurnControl())
        self.assertIsNotNone(fresh, 'mid-pause requests that were discarded must not block the real endpoint')
        gate.set()
        for prepared in (*stale, fresh):
            prepared.done.wait(2)
        self.assertEqual(('hello', 'accepted'), (fresh.result[0], fresh.result[1]['decision']))
        gate.clear()
        held = [recognizer.prepare(pcm, TurnControl()) for _ in range(5)]
        self.assertEqual(4, sum(1 for item in held if item), 'each slot is released exactly once')
        for item in held:
            if item:
                item.close()
        gate.set()


if __name__ == '__main__':
    unittest.main()
