"""Hermes reads the widget source, never falls back to a separate forecast."""
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
from app_os_weather import read_weather


class WidgetWeatherTests(unittest.TestCase):
    @patch.dict(os.environ, {'APP_OS_URL': 'https://os.example.test'})
    @patch('app_os_weather.urllib.request.build_opener')
    def test_shared_read_has_fixed_path_and_no_private_credentials(self, build):
        snapshot = {'data': {'tempC': 20}, 'unit': 'F', 'live': True, 'kind': 'forecast'}
        build.return_value.open.return_value = io.BytesIO(json.dumps(snapshot).encode())
        self.assertEqual(read_weather({'location': 'Brisbane', 'unit': 'F'}), snapshot)
        request = build.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://os.example.test/api/weather?location=Brisbane&unit=F')
        self.assertEqual(request.header_items(), [])
        self.assertEqual(build.return_value.open.call_args.kwargs, {'timeout': 18})

    @patch.dict(os.environ, {'APP_OS_URL': 'https://os.example.test'})
    @patch('app_os_weather.urllib.request.build_opener')
    def test_bad_arguments_and_offline_source_fail_closed(self, build):
        for value in [{'unit': 'K'}, {'actor': 'another-account'}, {'location': 'x' * 81}]:
            self.assertIn('error', read_weather(value))
        build.assert_not_called()
        build.return_value.open.side_effect = OSError('offline')
        self.assertIn('error', read_weather({}))
        with patch.dict(os.environ, {'APP_OS_URL': 'https://user:secret@os.example.test'}):
            self.assertIn('error', read_weather({}))

    @patch.dict(os.environ, {'APP_OS_URL': 'https://os.example.test'})
    @patch('app_os_weather.urllib.request.build_opener')
    def test_old_sample_deployment_is_not_treated_as_current_weather(self, build):
        build.return_value.open.return_value = io.BytesIO(json.dumps({'data': {'tempC': 26}, 'unit': 'C', 'live': False}).encode())
        self.assertIn('error', read_weather({}))


if __name__ == '__main__':
    unittest.main()
