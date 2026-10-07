"""Phone dialect/voice preferences preserve OS settings and never replay live actions."""
import ast
import asyncio
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parents[1]))
from whatsapp_speech import ENGLISH_KEYTERMS, phone_speech


class SpeechPreferences(unittest.TestCase):
    def test_phone_voice_does_not_replace_shared_os_voice_and_preserves_english_fallback(self):
        original = {'FISH_VOICE_ID': 'existing-english-voice', 'FISH_VOICE_NAME': 'Jarvis'}
        phone = phone_speech(original)
        self.assertEqual(original['FISH_VOICE_ID'], 'existing-english-voice')
        self.assertEqual(phone['FISH_VOICE_ID'], 'existing-english-voice')
        self.assertEqual(phone['FISH_VOICE_NAME'], 'Jarvis')
        self.assertEqual(phone['FISH_ENGLISH_FALLBACK_VOICE_ID'], 'existing-english-voice')
        self.assertEqual(phone_speech(phone)['FISH_ENGLISH_FALLBACK_VOICE_ID'], 'existing-english-voice')

    def test_english_place_hints_preserve_bali_alongside_australian_locations(self):
        self.assertTrue({'Brisbane', 'Melbourne', 'Canberra', 'Cairns', 'Gold Coast', 'Denpasar', 'Bali'} <= set(ENGLISH_KEYTERMS))
        self.assertLessEqual(len(ENGLISH_KEYTERMS), 100)


class SessionSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def prepare(self, rejection):
        source = ast.parse(((Path(__file__).parents[1] / 'whatsapp_fish.py').read_text() + '\n' + (Path(__file__).parents[1] / 'fish_conversation.py').read_text()))
        node = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'FishCall')
        requests = []
        def api(config, path, body):
            requests.append(json.loads(json.dumps(body)))
            if len(requests) == 1 and rejection:
                raise urllib.error.HTTPError('https://synthetic.invalid', rejection, 'fixture rejection', {}, None)
            return {'transport': 'livekit', 'livekit_url': 'wss://synthetic.invalid', 'token': 'fixture'}
        async def nothing(*args): pass
        rtc = SimpleNamespace(LocalAudioTrack=SimpleNamespace(create_audio_track=lambda *args: object()),
            TrackPublishOptions=lambda **kwargs: None, TrackSource=SimpleNamespace(SOURCE_MICROPHONE=1))
        scope = dict(asyncio=asyncio, json=json, urllib=SimpleNamespace(error=urllib.error), fish_api=api, rtc=rtc)
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual English session creation>', 'exec'), scope)
        call = scope['FishCall'].__new__(scope['FishCall'])
        call.service = SimpleNamespace(config=phone_speech({'FISH_VOICE_ID': 'existing-english-voice',
            'FISH_ENGLISH_FALLBACK_VOICE_ID': 'backup-english-voice'}), metrics={}, agent='owned-fixture')
        call.member = {'number': '61423947456', 'name': 'Michael'}
        call.voice_id = call.service.config['FISH_VOICE_ID']
        call.prompt, call.notice_marker, call.callback_notice = 'English fixture', 'private-fixture', None
        call.jobs = SimpleNamespace(snapshot=lambda *args: [])
        call.room = SimpleNamespace(connect=nothing, remote_participants={}, local_participant=SimpleNamespace(publish_track=nothing))
        call.source = object()
        call.task = lambda coroutine: coroutine.close()
        return call, requests

    async def test_configured_voice_keeps_supported_english_wire_language_and_michael_identity(self):
        call, requests = await self.prepare(None)
        await call.start()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['overrides']['language'], 'en')
        self.assertEqual(requests[0]['overrides']['voice_id'], 'existing-english-voice')
        self.assertEqual(requests[0]['end_user_id'], 'whatsapp:61423947456')
        self.assertFalse(requests[0]['record_audio'])

    async def test_rejected_voice_can_fall_back_once_before_live_session_exists(self):
        call, requests = await self.prepare(422)
        await call.start()
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1]['overrides']['voice_id'], 'backup-english-voice')
        self.assertEqual(requests[1]['overrides']['language'], 'en')
        self.assertEqual(call.service.metrics['english_voice_fallbacks'], 1)
        self.assertEqual(requests[0]['end_user_id'], requests[1]['end_user_id'])

    async def test_uncertain_server_failure_does_not_repeat_a_session_or_action(self):
        call, requests = await self.prepare(503)
        with self.assertRaises(urllib.error.HTTPError):
            await call.start()
        self.assertEqual(len(requests), 1)


if __name__ == '__main__':
    unittest.main()
