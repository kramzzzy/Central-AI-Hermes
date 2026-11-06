"""Exercise the real fast-path function without importing the running server."""
import ast
import json
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch


class CustomerIsolationTests(unittest.TestCase):
    def test_isolated_request_has_no_enrichment_or_retention(self):
        source = Path(__file__).with_name('local-bridge.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_fast_draft_only_completion')
        scope = dict(os=os, json=json, re=re)
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<bridge>', 'exec'), scope)
        messages = [{'role': 'system', 'content': 'Assigned profile and knowledge only'},
                    {'role': 'user', 'content': 'My bill is $800. Give a quotation.'}]
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({'choices': [{'message': {'content': 'No approved price is available.'}}]}).encode()
        def provider(request, **kwargs):
            self.assertEqual(json.loads(request.data)['messages'], messages)
            self.assertEqual(json.loads(request.data)['model'], expected_model)
            return Response()
        # Recall, classification and retention helpers are deliberately absent.
        for selected in ['deepseek/deepseek-v4.1-flash', 'z-ai/glm-5.2', 'workspace-default', 'hermes', '']:
            expected_model = selected if selected not in {'workspace-default', 'hermes', ''} else 'configured/default'
            with self.subTest(model=selected), patch.dict(os.environ, {}, clear=True), patch('urllib.request.urlopen', provider):
                result = scope['_fast_draft_only_completion']({
                    'model': selected, 'execution_mode': 'draft-only', 'context_isolation': 'provided-only',
                    'memory_keys': ['customer-test'], 'messages': messages,
                }, {'OPENROUTER_API_KEY': 'local-test-only', 'HERMES_CHAT_MODEL': 'configured/default'})
            self.assertEqual(result['context_isolation'], 'provided-only')
            self.assertEqual(result['choices'][0]['message']['content'], 'No approved price is available.')

    def test_job_and_profile_preserve_selected_models(self):
        root = Path(__file__).parent
        job = ast.parse((root / 'hermes-job.py').read_text(encoding='utf-8'))
        body = next(n for n in job.body if isinstance(n, ast.With)).body
        start = next(i for i, n in enumerate(body) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'requested_model')
        end = next(i for i, n in enumerate(body) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'runtime')
        profile = ast.parse((root / 'hermes_profile.py').read_text(encoding='utf-8'))
        profile_body = next(n for n in profile.body if isinstance(n, ast.FunctionDef) and n.name == 'load_profile').body
        default_gate = profile_body[-2]
        for selected in ['deepseek/deepseek-v4.1-flash', 'z-ai/glm-5.2']:
            with self.subTest(model=selected), patch.dict(os.environ, {'HERMES_CHAT_MODEL': selected}, clear=True):
                scope = {'os': os, 'body': {'model': selected}, 'config': {'default': 'another/model'}}
                exec(compile(ast.Module(body=body[start:end], type_ignores=[]), '<job routing>', 'exec'), scope)
                self.assertEqual(scope['model'], selected)
                for config in [{'default': selected}, {}]:
                    scope['config'] = config
                    exec(compile(ast.Module(body=[default_gate], type_ignores=[]), '<profile routing>', 'exec'), scope)
                    self.assertEqual(config['default'], selected)

    def test_bootstrap_preserves_managed_models(self):
        tree = ast.parse(Path(__file__).with_name('central_ai_service.py').read_text(encoding='utf-8'))
        body = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'bootstrap').body
        end = next(i for i, n in enumerate(body) if isinstance(n, ast.If))
        models = {'HERMES_CHAT_MODEL': 'deepseek/deepseek-v4.1-flash',
                  'WHATSAPP_CHAT_MODEL': 'z-ai/glm-5.2', 'HERMES_VOICE_MODEL': 'z-ai/glm-5.2'}
        with patch.dict(os.environ, {}, clear=True):
            exec(compile(ast.Module(body=body[:end], type_ignores=[]), '<bootstrap routing>', 'exec'),
                 {'os': os, 'config': {'integrations': models}})
            self.assertEqual({key: os.environ[key] for key in models}, models)


if __name__ == '__main__':
    unittest.main()
