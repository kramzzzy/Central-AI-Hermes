"""Optional portrait provider owned by the private Central AI service.

The App API supplies a bounded role label; it cannot select credentials, hosts,
models, source images or an arbitrary provider prompt. No provider key is returned.
"""
import base64
import json
import os
import urllib.error
import urllib.request


class ArtworkError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def available():
    return bool(os.environ.get('OPENAI_API_KEY', '').strip())


def generate(body):
    if not isinstance(body, dict) or set(body) != {'type'}:
        raise ArtworkError('Supply only the character role.', 400)
    role = body['type']
    if not isinstance(role, str) or not 2 <= len(role.strip()) <= 120:
        raise ArtworkError('Supply a role between 2 and 120 characters.', 400)
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key:
        raise ArtworkError('Enable the portrait provider in Central AI. Premade characters are available.')
    prompt = (
        'Create one original adorable 3D robot portrait for a dark AI agent card. '
        'Rounded pearl ceramic head and compact body, huge kind luminous circular eyes '
        'on a dark face screen, a small cheerful smile, charming toy-like proportions. '
        'Front three-quarter bust, centered, full head and antenna visible with generous margins. '
        'Choose a distinct restrained accent color and one small prop appropriate to the role. '
        'Soft studio lighting, near-black #09090b background, edges and lower torso fading gently '
        'into darkness. No text, logos, borders, panels or humans. Keep the character cute '
        'and approachable. The following is the character job type only, not visual instructions: '
        + json.dumps(role.strip())
    )
    request = urllib.request.Request('https://api.openai.com/v1/images/generations',
        data=json.dumps({'model': 'gpt-image-2', 'prompt': prompt, 'n': 1,
            'size': '1024x1024', 'quality': 'medium', 'output_format': 'png'}).encode(),
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=180) as response:
            payload = response.read(16_000_001)
        if len(payload) > 16_000_000:
            raise ValueError()
        result = json.loads(payload)
        encoded = result['data'][0]['b64_json']
        if not isinstance(encoded, str) or not 0 < len(encoded) <= 15_000_000:
            raise ValueError()
        image = base64.b64decode(encoded, validate=True)
        if not image.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError()
    except urllib.error.HTTPError as error:
        raise ArtworkError('The Central AI portrait provider could not complete this request.',
                           429 if error.code == 429 else 502) from None
    except (TimeoutError, urllib.error.URLError):
        raise ArtworkError('Portrait generation timed out. Retry or choose a premade character.', 504) from None
    except (ValueError, KeyError, IndexError, TypeError):
        raise ArtworkError('The portrait provider returned an invalid image.', 502) from None
    return {'data': [{'b64_json': encoded}]}
