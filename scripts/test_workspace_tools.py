"""Local tool parity and owner boundary regression checks; no network or provider calls."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from hermes_plugins.central_ai_app_os import TOOLS, client
from hermes_os import SECTIONS, WORKSPACE_TOOLS_NOTE, _without_app_blocks
from hermes_voice import VOICE_CALL_NOTE


class WorkspaceToolsTests(unittest.TestCase):
    def test_new_sections_validate_and_do_not_fabricate_empty_snapshot(self):
        sections = {'communications', 'comments', 'submissions', 'social_posts', 'files', 'capabilities'}
        schema = TOOLS['mcp__michael_os__inspect_workspace']['schema']['parameters']['properties']['section']['enum']
        for section in sections:
            client.validate({'section': section})
            self.assertIn(section, SECTIONS)
            self.assertIn(section, schema)
            self.assertIsNone(client._inspect_from_snapshot({'section': section}, {}))
        submissions_tool = TOOLS['mcp__michael_os__manage_submissions']['schema']['parameters']
        self.assertEqual(['action', 'id'], submissions_tool['required'])
        social_tool = TOOLS['mcp__michael_os__manage_social']['schema']['parameters']
        self.assertEqual(['action'], social_tool['required'])
        self.assertEqual(['create_invite'], social_tool['properties']['action']['enum'])

    def test_owner_tools_shared_and_restricted_context_denied(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory) / 'owner'
            home.mkdir(); (home/'config.yaml').write_text('{}')
            with patch.dict(os.environ, {'HERMES_HOME': str(home), 'MICHAEL_RESTRICT_TEAM_TOOLS': 'false'}), patch.object(client, '_call_app_os_mcp', return_value='{"ok":true}') as call:
                self.assertTrue(client.owner_tools_available())
                self.assertTrue(json.loads(client.generate_image({'prompt':'solar poster','aspect_ratio':'3:4'}))['ok'])
                self.assertEqual(call.call_args.args[0], 'central_ai_generate_image')
                self.assertFalse(call.call_args.kwargs['retry'])
                for config in ['phone_business_context: true', 'os_specialist: {id: fixture}']:
                    (home/'config.yaml').write_text(config)
                    call.reset_mock()
                    for tool in (client.generate_image, client.manage_video, client.manage_communications, client.manage_tasks, client.manage_social):
                        self.assertIn('error', json.loads(tool({})))
                    self.assertIn('error', json.loads(client.inspect_workspace({'section':'communications'})))
                    call.assert_not_called()
                (home/'config.yaml').write_text('{}')
                with patch.dict(os.environ, {'MICHAEL_RESTRICT_TEAM_TOOLS':'true'}):
                    self.assertFalse(client.owner_tools_available())

    def test_voice_and_prompt_refresh_use_same_workflow_once(self):
        self.assertIn(WORKSPACE_TOOLS_NOTE, VOICE_CALL_NOTE)
        refreshed = _without_app_blocks('Configured identity\n'+WORKSPACE_TOOLS_NOTE)+'\n'+WORKSPACE_TOOLS_NOTE
        self.assertEqual(refreshed.count('[Current workspace workflow]'),1)
        self.assertIn('Configured identity',refreshed)
        for tool in ('manage_tasks','manage_communications','generate_image','manage_video'):
            self.assertIn('mcp__michael_os__'+tool, TOOLS)

    def test_mcp_child_inherits_active_profile_and_team_restriction(self):
        import types
        from hermes_os import install_process_token
        config = types.SimpleNamespace(_build_safe_env=lambda supplied: dict(supplied))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'owner'
            home.mkdir(); (home / 'config.yaml').write_text('{}')
            with patch.dict(sys.modules, {'tools': types.SimpleNamespace(mcp_tool_config=config)}), patch.dict(os.environ, {'HERMES_HOME': str(home), 'MICHAEL_RESTRICT_TEAM_TOOLS': 'true'}):
                install_process_token()
                child = config._build_safe_env({'MICHAEL_OS_INHERIT_TOOL_TOKEN':'true', 'HERMES_HOME':'wrong', 'MICHAEL_RESTRICT_TEAM_TOOLS':'false'})
                self.assertEqual(child['HERMES_HOME'], str(home))
                self.assertEqual(child['MICHAEL_RESTRICT_TEAM_TOOLS'], 'true')
                with patch.dict(os.environ, child, clear=True):
                    self.assertFalse(client.owner_tools_available())
                os.environ.pop('MICHAEL_RESTRICT_TEAM_TOOLS')
                child = config._build_safe_env({'MICHAEL_OS_INHERIT_TOOL_TOKEN':'true'})
                with patch.dict(os.environ, child, clear=True):
                    self.assertTrue(client.owner_tools_available())
                unrelated = config._build_safe_env({})
                self.assertNotIn('HERMES_HOME', unrelated)

    def test_mcp_tool_errors_remain_machine_readable(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'result': {'isError':True, 'content':[{'type':'text','text':'An assigned skill is no longer installed.'}]}}).encode()
        opener = MagicMock(); opener.open.return_value = response
        with patch.object(client, '_hermes_api_key',return_value='fixture'), patch.object(client,'_app_os_urls',return_value=['http://fixture.invalid']), patch.object(client.urllib.request,'build_opener',return_value=opener):
            result = json.loads(client._call_app_os_mcp('central_ai_manage_tasks', {}, retry=False))
            self.assertEqual(result['error'], 'An assigned skill is no longer installed.')
            self.assertEqual(opener.open.call_count,1)

    def test_task_fallback_only_when_request_could_not_connect(self):
        import socket
        import urllib.error
        from unittest.mock import MagicMock
        for reason, expected_calls in [(ConnectionRefusedError(), 2), (socket.gaierror(), 2), (TimeoutError(), 1), (ConnectionResetError(), 1)]:
            with self.subTest(reason=type(reason).__name__):
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps({'result':{'content':[{'text':'{"task_id":"fixture-task"}'}]}}).encode()
                opener = MagicMock(); opener.open.side_effect = [urllib.error.URLError(reason), response]
                with patch.object(client, '_hermes_api_key',return_value='fixture'), patch.object(client,'_app_os_urls',return_value=['http://old.invalid','http://current.invalid']), patch.object(client.urllib.request,'build_opener',return_value=opener):
                    result=json.loads(client._call_app_os_mcp('central_ai_manage_tasks', {}, retry=False))
                    self.assertEqual(opener.open.call_count, expected_calls)
                    self.assertIn('task_id' if expected_calls == 2 else 'error', result)

    def test_public_call_excludes_private_tools(self):
        import types
        import hermes_phone_tools as phone
        names=['clarify','mcp__michael_os__get_live_weather','mcp__michael_os__inspect_workspace','mcp__michael_os__manage_tasks','mcp__michael_os__generate_image']
        module=types.SimpleNamespace(get_tool_definitions=lambda:[{'function':{'name':n}} for n in names])
        with patch.dict(sys.modules, {'model_tools':module}), patch.object(phone, '_public_phone_only',True):
            phone.configure_phone_tools()
            self.assertEqual([t['function']['name'] for t in module.get_tool_definitions()],['mcp__michael_os__get_live_weather'])

if __name__ == '__main__':
    unittest.main()
