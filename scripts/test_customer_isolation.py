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
            return Response()
        # Recall, classification and retention helpers are deliberately absent.
        with patch('urllib.request.urlopen', provider):
            result = scope['_fast_draft_only_completion']({
                'execution_mode': 'draft-only', 'context_isolation': 'provided-only',
                'memory_keys': ['customer-test'], 'messages': messages,
            }, {'OPENROUTER_API_KEY': 'local-test-only'})
        self.assertEqual(result['context_isolation'], 'provided-only')
        self.assertEqual(result['choices'][0]['message']['content'], 'No approved price is available.')


if __name__ == '__main__':
    unittest.main()
