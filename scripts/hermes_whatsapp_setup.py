"""Owner-only pairing through the fixed private companion, never a call/send tool."""
import base64
import json
from pathlib import Path
import urllib.error
import urllib.request


class SetupError(RuntimeError):
    def __init__(self, message, status=503):
        super().__init__(message); self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SetupError('WhatsApp setup redirected unexpectedly')


class WhatsAppSetup:
    def __init__(self, root, token_file='/run/secrets/whatsapp_setup_key'):
        self.root, self.token_file = Path(root), Path(token_file)
        self.transport = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def fetch(self, path, start=False):
        headers = {}
        if start:
            import os
            key = ''
            if self.token_file.is_file():
                try:
                    key = self.token_file.read_text(encoding='utf-8').strip()
                except Exception:
                    key = ''
            if not key:
                alt = Path('/data/whatsapp_setup_key')
                if alt.is_file():
                    try:
                        key = alt.read_text(encoding='utf-8').strip()
                    except Exception:
                        key = ''
            if not key:
                key = os.environ.get('WHATSAPP_SETUP_KEY', '').strip() or os.environ.get('HERMES_API_KEY', '').strip()
            if len(key) < 32: raise SetupError('WhatsApp setup authentication is unavailable')
            headers['Authorization'] = 'Bearer ' + key
        request = urllib.request.Request('http://caller:8080' + path, data=b'{}' if start else None, headers=headers)
        try:
            with self.transport.open(request, timeout=5) as response:
                content = response.read(150001)
                if len(content) > 150000: raise SetupError('WhatsApp setup response was too large')
                return content
        except urllib.error.HTTPError as error:
            if path == '/qr.png' and error.code == 410: return None
            raise SetupError('WhatsApp connection is unavailable') from None
        except (OSError, ValueError):
            raise SetupError('WhatsApp connection is unavailable') from None

    def handle(self, body):
        if not isinstance(body, dict) or set(body) not in ({'action','org','user'},{'action','org','user','value'}) or body['action'] not in {'status','start','settings','save_settings','apply_settings','groups'}:
            raise SetupError('Invalid WhatsApp setup request', 400)
        if body['action'] in {'settings','save_settings','apply_settings'}:
            import whatsapp_settings as settings
            if body['action']=='settings': return settings.status(self.root)
            state=json.loads(self.fetch('/status'))
            if state.get('call') not in {'idle','ended',None,''}: raise SetupError('Finish the active WhatsApp call before applying contacts.',409)
            if body['action']=='save_settings': return settings.save(self.root,body.get('value'))
            return settings.apply(self.root)
        if body['action']=='groups':
            return json.loads(self.fetch('/setup/groups',start=True))
        if body['action'] == 'start': self.fetch('/pairing/start', start=True)
        state = json.loads(self.fetch('/status'))
        if not isinstance(state, dict): raise SetupError('Invalid WhatsApp status')
        allowed = {'starting', 'standby', 'pairing', 'connected', 'reconnecting', 'qr_timeout', 'logged_out'}
        name = state.get('state')
        if name not in allowed: raise SetupError('Unrecognized WhatsApp connection state')
        result = {'state': name, 'connected': name == 'connected', 'voice_ready': False}
        if name == 'pairing':
            pixels = self.fetch('/qr.png')
            if pixels and pixels.startswith(b'\x89PNG\r\n\x1a\n'):
                result['qr'] = 'data:image/png;base64,' + base64.b64encode(pixels).decode('ascii')
        return result
