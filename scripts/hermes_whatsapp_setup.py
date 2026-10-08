"""Owner and customer line pairing through private companions, never a call/send tool."""
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

    def fetch(self, path, start=False, line='executive'):
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
        host = 'customer-caller:8080' if line == 'customer' else 'caller:8080'
        request = urllib.request.Request(f'http://{host}' + path, data=b'{}' if start else None, headers=headers)
        try:
            with self.transport.open(request, timeout=5) as response:
                content = response.read(150001)
                if len(content) > 150000: raise SetupError('WhatsApp setup response was too large')
                return content
        except urllib.error.HTTPError as error:
            if path == '/qr.png' and error.code == 410: return None
            raise SetupError('WhatsApp connection is unavailable') from None
        except (OSError, ValueError):
            if line == 'customer' and path == '/status':
                return json.dumps({'state': 'standby', 'call': 'idle'}).encode('utf-8')
            raise SetupError('WhatsApp connection is unavailable') from None

    def handle(self, body):
        if not isinstance(body, dict) or body.get('action') not in {'status','start','disconnect','settings','save_settings','apply_settings','groups'}:
            raise SetupError('Invalid WhatsApp setup request', 400)
        line = body.get('line', 'executive')
        if line not in {'executive', 'customer'}:
            line = 'executive'
        if body['action'] in {'settings','save_settings','apply_settings'}:
            import whatsapp_settings as settings
            if body['action']=='settings': return settings.status(self.root)
            state=json.loads(self.fetch('/status', line=line))
            if state.get('call') not in {'idle','ended',None,''}: raise SetupError('Finish the active WhatsApp call before applying contacts.',409)
            if body['action']=='save_settings': return settings.save(self.root,body.get('value'))
            return settings.apply(self.root)
        if body['action']=='groups':
            return json.loads(self.fetch('/setup/groups',start=True, line=line))
        if body['action'] == 'start': self.fetch('/pairing/start', start=True, line=line)
        if body['action'] == 'disconnect':
            try:
                self.fetch('/pairing/disconnect', start=True, line=line)
            except Exception:
                pass
            phone_dir = Path('/data')
            for f in [phone_dir / 'remote-engine-enabled', phone_dir / 'call-request.json']:
                if f.is_file():
                    try: f.unlink()
                    except Exception: pass
            return {'state': 'logged_out', 'connected': False, 'voice_ready': False, 'line': line}
        state = json.loads(self.fetch('/status', line=line))
        if not isinstance(state, dict): raise SetupError('Invalid WhatsApp status')
        allowed = {'starting', 'standby', 'pairing', 'connected', 'reconnecting', 'qr_timeout', 'logged_out'}
        name = state.get('state')
        if name not in allowed: raise SetupError('Unrecognized WhatsApp connection state')
        result = {'state': name, 'connected': name == 'connected', 'voice_ready': False, 'line': line}
        if state.get('linked_number'):
            result['linked_number'] = state['linked_number']
        if state.get('push_name'):
            result['push_name'] = state['push_name']
        if name == 'pairing':
            pixels = self.fetch('/qr.png', line=line)
            if pixels and pixels.startswith(b'\x89PNG\r\n\x1a\n'):
                result['qr'] = 'data:image/png;base64,' + base64.b64encode(pixels).decode('ascii')
        return result
