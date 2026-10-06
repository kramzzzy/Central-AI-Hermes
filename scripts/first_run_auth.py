"""First-install device sign-in, using the pinned Hermes implementation.

No generic RPC, shell, credentials import or provider URL is exposed. The native
store is shared by the production backends so refresh tokens are never cloned.
"""
import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
sys.path.insert(0, '/opt/hermes')

guard = threading.Lock()
flow = {'state': 'idle'}


def connected():
    try:
        store = json.loads((Path(os.environ['HERMES_HOME']) / 'auth.json').read_text())
        tokens = store['providers']['openai-codex']['tokens']
        return bool(tokens.get('access_token') and tokens.get('refresh_token'))
    except (OSError, KeyError, ValueError):
        return False


def finish(device):
    global flow
    try:
        from hermes_cli.auth_codex import _codex_poll_authorization_code, _codex_exchange_authorization_code, _save_codex_tokens
        from hermes_cli.auth_constants import CODEX_OAUTH_CLIENT_ID
        result = _codex_poll_authorization_code('https://auth.openai.com',
            device_auth_id=device['device_auth_id'], user_code=device['user_code'], poll_interval=device['interval'])
        tokens = _codex_exchange_authorization_code('https://auth.openai.com', CODEX_OAUTH_CLIENT_ID, result)
        if not tokens.get('access_token') or not tokens.get('refresh_token'):
            raise RuntimeError('Incomplete authentication')
        _save_codex_tokens({k: tokens[k] for k in ('access_token', 'refresh_token')})
        with guard: flow = {'state': 'connected'}
    except Exception:
        with guard: flow = {'state': 'error', 'message': 'Sign-in did not complete. Check account access and start again.'}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_POST(self):
        global flow
        token = os.environ.get('SERVICE_PASSWORD_SETUP', '')
        if len(token) < 32 or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
            self.send_error(401); return
        if self.path not in ('/start', '/status'):
            self.send_error(404); return
        try:
            with guard:
                if self.path == '/start' and flow['state'] != 'pending' and not connected():
                    from hermes_cli.auth_codex import _codex_request_device_code
                    from hermes_cli.auth_constants import CODEX_OAUTH_CLIENT_ID
                    device = _codex_request_device_code('https://auth.openai.com', CODEX_OAUTH_CLIENT_ID)
                    flow = {'state': 'pending', 'code': device['user_code'],
                            'url': 'https://auth.openai.com/codex/device', 'expiresAt': int(time.time()) + 900}
                    threading.Thread(target=finish, args=(device,), daemon=True).start()
                value = {'state': 'connected'} if connected() else dict(flow)
        except Exception:
            value = {'state': 'error', 'message': 'ChatGPT sign-in is unavailable. Retry later or use an API key.'}
        data = json.dumps(value).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)


if __name__ == '__main__':
    home = Path(os.environ['HERMES_HOME'])
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(home, 10000, 10000)
    os.environ['HOME'] = str(home)
    if os.getuid() == 0:
        os.setgid(10000); os.setuid(10000)
    ThreadingHTTPServer(('0.0.0.0', 3001), Handler).serve_forever()
