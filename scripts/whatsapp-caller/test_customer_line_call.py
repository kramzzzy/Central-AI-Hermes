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
        self.assertEqual('google/gemini-2.5-flash-lite', conv.config['AGENT_MODEL'], 'customer calls use the fast model')
        asyncio.run(call._warm_native_conversation())
        self.assertIsNone(call.conversation)

    def test_owner_line_call_is_unchanged(self):
        call = self.call(name='Owner', role='owner', profile='leo')
        self.assertIs(FakeService.config, call.speech_config)
        self.assertIn('send_whatsapp_message', call._get_fallback_conversation().names)

    def test_customer_without_voice_setting_keeps_default_voice(self):
        with patch.dict(os.environ, {'CUSTOMER_FISH_VOICE_ID': ''}):
            self.assertEqual('owner-voice', self.call(line='customer').speech_config['FISH_VOICE_ID'])

    def test_customer_legacy_or_unset_voice_uses_michael_voice(self):
        with patch.dict(os.environ, {'CUSTOMER_FISH_VOICE_ID': '711cf3ed00ab441a8f54a45058047b7a'}):
            self.assertEqual('a30d099a643d4173836cfee1d8ae6c13', self.call(line='customer').speech_config['FISH_VOICE_ID'])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('CUSTOMER_FISH_VOICE_ID', None)
            self.assertEqual('a30d099a643d4173836cfee1d8ae6c13', self.call(line='customer').speech_config['FISH_VOICE_ID'])


if __name__ == '__main__':
    unittest.main()
