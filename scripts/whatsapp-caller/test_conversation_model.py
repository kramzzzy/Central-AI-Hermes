"""Cheap custom voice generation keeps the existing voice and fails closed on bad config."""
import ast
from pathlib import Path
import unittest

tree = ast.parse(((Path(__file__).parents[1] / 'whatsapp_fish.py').read_text() + '\n' + (Path(__file__).parents[1] / 'fish_conversation.py').read_text()))
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'conversation_llm')
scope = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual conversation configuration>', 'exec'), scope)
configure = scope['conversation_llm']


class ConversationModelTests(unittest.TestCase):
    def test_existing_default_does_not_change(self):
        self.assertEqual(configure({}), {'custom': None})

    def test_fixed_openrouter_binding_preserves_input(self):
        config = {'FISH_LLM_MODEL': 'openai/gpt-4o-mini', 'OPENROUTER_API_KEY': 'sk-or-fixture',
                  'FISH_VOICE_ID': 'existing-jarvis'}
        self.assertEqual(configure(config)['custom'], {
            'base_url': 'https://openrouter.ai/api/v1', 'model': config['FISH_LLM_MODEL'], 'api_key': 'sk-or-fixture'})
        self.assertEqual(config['FISH_VOICE_ID'], 'existing-jarvis')

    def test_missing_key_or_unapproved_model_never_falls_back(self):
        for config in ({'FISH_LLM_MODEL': 'openai/gpt-4o-mini'},
                       {'FISH_LLM_MODEL': 'google/gemini-2.5-flash-lite', 'OPENROUTER_API_KEY': 'sk-or-fixture'},
                       {'FISH_LLM_MODEL': 'expensive-or-unknown', 'OPENROUTER_API_KEY': 'sk-or-fixture'}):
            with self.assertRaises(ValueError):
                configure(config)

    def test_owned_voice_policy_is_allowed_but_arbitrary_presets_are_rejected(self):
        model = '@preset/leo-live-voice-20261003'
        self.assertEqual(configure({'FISH_LLM_MODEL': model, 'OPENROUTER_API_KEY': 'sk-or-fixture'})['custom']['model'], model)
        for model in ['@preset/arbitrary', 'anthropic/claude-opus-4.6', 'openai/gpt-6.1-sol']:
            with self.assertRaises(ValueError):
                configure({'FISH_LLM_MODEL': model, 'OPENROUTER_API_KEY': 'sk-or-fixture'})
