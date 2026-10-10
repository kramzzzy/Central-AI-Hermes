"""Read public widget weather from App OS's single data source; no private grant."""
import json
import os
import urllib.request
from urllib.parse import urlencode, urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_weather(arguments):
    try:
        if not isinstance(arguments, dict) or set(arguments) - {'location', 'unit'}:
            raise ValueError('Invalid weather arguments')
        location = arguments.get('location') or 'brisbane'
        unit = arguments.get('unit') or 'C'
        if not isinstance(location, str) or not 1 <= len(location.strip()) <= 80 or unit not in {'C', 'F'}:
            raise ValueError('Invalid location or unit')
        base = (os.environ.get('APP_OS_URL') or os.environ.get('MICHAEL_OS_URL') or '').rstrip('/')
        parsed = urlsplit(base)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError('Configure a valid App OS origin')
        request = urllib.request.Request(base + '/api/weather?' + urlencode({'location': location.strip(), 'unit': unit}))
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=18) as response:
            payload = response.read(32769)
        if len(payload) > 32768:
            raise ValueError('Weather response too large')
        result = json.loads(payload)
        if not isinstance(result, dict) or not isinstance(result.get('data'), dict) or result.get('live') is not True or result.get('kind') != 'forecast' or result.get('unit') != unit:
            raise ValueError('Invalid weather response')
        return result
    except (OSError, ValueError, TypeError):
        return {'error': 'Shared widget weather is unavailable. No current weather or widget reading was verified.'}
