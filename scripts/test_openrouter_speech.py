import os
import unittest
from unittest.mock import patch

import httpx
import openrouter_speech as speech


class SpeechTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config = {'OPENROUTER_API_KEY': 'test-key', 'FISH_API_KEY': 'expired'}

    def client(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return patch('voice_http.client', return_value=client)

    def test_routes_only_to_openrouter_and_resamples_phone_audio(self):
        import json
        def reply(request):
            self.assertEqual(str(request.url), 'https://openrouter.ai/api/v1/audio/speech')
            self.assertEqual(request.headers['authorization'], 'Bearer test-key')
            body = json.loads(request.content)
            self.assertEqual((body['model'], body['voice'], body['response_format']),
                             (speech.MODEL, speech.VOICE, 'pcm'))
            return httpx.Response(200, headers={'content-type': 'audio/pcm;rate=24000;channels=1'},
                                  content=b'\x01\x00' * 2400)
        with self.client(reply):
            pcm = speech.synthesize('Hello there.', self.config, 16000)
        self.assertTrue(3198 <= len(pcm) <= 3200)

    def test_bad_audio_and_http_failures_are_not_silent(self):
        for status, content_type, data in [(200, 'application/json', b'{}'),
                                          (200, 'audio/pcm', b'\x00'),
                                          (200, 'audio/pcm', b''), (402, 'application/json', b'{}')]:
            with self.subTest(status=status, data=data), self.client(lambda _: httpx.Response(
                    status, headers={'content-type': content_type}, content=data)):
                with self.assertRaises((RuntimeError, httpx.HTTPStatusError)):
                    speech.synthesize('Hello', self.config)

    def test_cancellation_prevents_request(self):
        from stream_conversation import TurnControl, TurnCancelled
        control = TurnControl()
        control.cancel()
        with patch('voice_http.client') as client, self.assertRaises(TurnCancelled):
            speech.synthesize('Hello', self.config, control=control)
        client.assert_not_called()

    def test_voice_changes_do_not_reuse_cached_audio(self):
        self.assertNotEqual(speech.voice_cache_key(self.config),
                            speech.voice_cache_key({**self.config, 'OPENROUTER_TTS_VOICE': 'Orus'}))

    def test_reference_voice_uses_cloning_model(self):
        import json
        def reply(request):
            body = json.loads(request.content)
            self.assertEqual(body['model'], 'heygen/heygen-voice-1')
            self.assertNotIn('voice', body)
            self.assertEqual(body['input_references'][0]['type'], 'input_audio')
            return httpx.Response(200, headers={'content-type': 'audio/pcm;rate=24000'}, content=b'\0\0')
        with self.client(reply):
            speech.synthesize('Hello', self.config, references=[{'type': 'input_audio', 'input_audio': {'data': 'sample'}}])

    def test_missing_key_cannot_be_satisfied_by_expired_fish_key(self):
        with self.assertRaisesRegex(RuntimeError, 'OPENROUTER_API_KEY'):
            speech.synthesize('Hello', {'FISH_API_KEY': 'expired'})

    def test_retired_credentials_are_not_rediscovered_or_forwarded(self):
        from central_ai_integrations import discover_fish_credentials, integration_config
        with patch.dict(os.environ, {'FISH_API_KEY': 'expired'}):
            self.assertEqual({}, discover_fish_credentials())
            self.assertEqual({'OPENROUTER_API_KEY': 'test-key'}, integration_config(self.config))

    def test_call_status_requires_openrouter_and_never_exposes_credentials(self):
        from hermes_voice import VoiceProvider
        provider = VoiceProvider.__new__(VoiceProvider)
        provider.config = {'FISH_API_KEY': 'expired'}
        self.assertFalse(provider.status()['configured'])
        provider.config.update(self.config)
        status = provider.status()
        self.assertTrue(status['configured'])
        self.assertEqual((status['provider'], status['conversation']), ('openrouter', 'stream'))
        self.assertNotIn('test-key', repr(status))
        self.assertNotIn('expired', repr(status))

    def test_stream_failure_is_reported_without_replaying_partial_audio(self):
        import stream_conversation as stream
        emitted = []
        def broken(text, config, rate, audio, control):
            audio(b'\0\0')
            raise RuntimeError('Connection dropped')
        with patch.object(stream, 'fish_rest_tts', side_effect=broken) as request, \
                patch.dict(stream._opener_audio, clear=True):
            voice = stream.FreeSpeech(self.config, emitted.append, stream.TurnControl())
            voice.text('Hello there.')
            with self.assertRaises(RuntimeError):
                voice.finish()
        self.assertEqual(emitted, [b'\0\0'])
        self.assertEqual(request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
