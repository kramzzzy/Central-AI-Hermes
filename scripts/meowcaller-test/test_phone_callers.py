"""Business caller uses a separate native profile, bank and job store."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from whatsapp_callers import resolve_caller, provision_business_phone


class CallerTests(unittest.TestCase):
    def test_business_gateway_is_initialized_outside_stdout_redirection(self):
        import ast
        path = Path(__file__).parents[1] / 'hermes-chat-entry.py'
        tree = ast.parse(path.read_text())
        bootstrap = next(node for node in tree.body if isinstance(node, ast.With))
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'configure_business_phone_runtime']
        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0].lineno, bootstrap.end_lineno)

    def test_only_two_confirmed_identities_can_select_fixed_profiles(self):
        self.assertEqual(resolve_caller('639267200480')['profile'], 'leo')
        self.assertEqual(resolve_caller('61423947456')['profile'], 'team-whatsapp-michael-business')
        for number in ('', '614239474569', 'unknown', None, {'profile': 'leo'}):
            with self.assertRaises(PermissionError):
                resolve_caller(number)

    def test_provision_has_distinct_memory_and_no_private_state_copy(self):
        import yaml
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'profiles' / 'leo'
            (source / 'plugins' / 'hindsight').mkdir(parents=True)
            (source / 'plugins' / 'hindsight' / 'plugin.py').write_text('synthetic plugin code')
            (source / 'hindsight').mkdir()
            (source / 'hindsight' / 'config.json').write_text(json.dumps({'bank_id': 'private-mark-bank', 'url': 'http://memory'}))
            (source / 'config.yaml').write_text(yaml.safe_dump({'model': {'default': 'existing', 'provider': 'configured'}, 'reasoning': 'medium'}))
            (source / 'SOUL.md').write_text('private Mark preferences')
            (source / 'state.db').write_text('private Mark messages')
            (source / 'auth.json').write_text('synthetic private credential fixture')
            (source / 'MEMORY.md').write_text('private Mark facts')
            # Native symlink permission is unavailable on some Windows accounts;
            # stub only this credential reference operation, never data provisioning.
            from unittest.mock import patch
            with patch.object(Path, 'symlink_to'):
                profile = provision_business_phone(root)
            home = source.parent / profile
            config = yaml.safe_load((home / 'config.yaml').read_text())
            self.assertTrue(config['phone_business_context'])
            self.assertEqual(config['model']['default'], 'existing')
            self.assertEqual(config['reasoning'], 'medium')
            self.assertEqual(config['platform_toolsets']['cli'], ['memory', 'todo', 'laya'])
            self.assertEqual(json.loads((home / 'hindsight' / 'config.json').read_text())['bank_id'], 'michael-os-leo')
            for filename in ('state.db', 'MEMORY.md', 'auth.json'):
                self.assertFalse((home / filename).exists())
            self.assertNotIn('private Mark preferences', (home / 'SOUL.md').read_text())
            self.assertEqual(provision_business_phone(root), profile)
            (home / 'phone-channel.json').write_text('{}')
            with self.assertRaisesRegex(RuntimeError, 'ownership mismatch'):
                provision_business_phone(root)

if __name__ == '__main__':
    unittest.main()
