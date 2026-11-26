"""Regression tests for NativeChat idle lease handling and automatic reconnect."""
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hermes_chat import NativeChat  # noqa: E402


class FakeProcess:
    def __init__(self, alive=True):
        self._alive = alive
        self.stdin = MagicMock()
        self.pid = 99999

    def poll(self):
        return None if self._alive else 0

    def wait(self, timeout=None):
        self._alive = False
        return 0


def make_chat(root, sessions=None):
    (root / '.runtime').mkdir(parents=True, exist_ok=True)
    chat = NativeChat({'HERMES_PROFILE': 'leo'}, root)
    chat.disposed = True
    chat.process = FakeProcess(alive=True)
    chat.start = lambda: setattr(chat, 'process', FakeProcess(alive=True)) if (not chat.process or chat.process.poll() is not None) else None
    rpc_calls = []
    session_list = sessions if sessions is not None else [
        {'id': 'sess-custom', 'title': 'Solar Strategy', 'profile': 'leo'},
        {'id': 'sess-bot', 'title': 'Bot Chat', 'profile': 'leo'},
    ]

    def fake_rpc(method, params=None, timeout=30):
        rpc_calls.append((method, params or {}))
        if method == 'session.list':
            return {'sessions': session_list}
        if method == 'session.resume':
            sid = (params or {}).get('session_id')
            return {'session_id': f'live-{sid}', 'stored_session_id': sid, 'info': {'model': 'openai/gpt-5', 'provider': 'openrouter', 'running': False}}
        if method == 'session.create':
            return {'session_id': 'live-new', 'stored_session_id': 'sess-bot', 'info': {'model': 'openai/gpt-5', 'provider': 'openrouter', 'running': False}}
        if method == 'session.history':
            return {'messages': []}
        if method == 'session.activate':
            return {'info': {'model': 'openai/gpt-5', 'provider': 'openrouter', 'running': bool(chat.info.get('running'))}}
        if method == 'approval.pending':
            return {'approvals': []}
        if method == 'session.title':
            return {'session_key': chat.stored, 'title': 'Bot Chat'}
        if method == 'prompt.submit':
            return {'submitted': True, 'session_id': (params or {}).get('session_id')}
        if method == 'session.interrupt':
            chat.info['running'] = False
            return {'interrupted': True}
        return {}

    chat.rpc = fake_rpc
    return chat, rpc_calls


def test_idle_lease_does_not_interrupt_or_close():
    with tempfile.TemporaryDirectory() as tmp:
        chat, calls = make_chat(Path(tmp))
        chat.sid = 'live-sess-bot'
        chat.stored = 'sess-bot'
        chat.info = {'running': False}
        chat.actor = 'org:user'
        chat.lease = time.monotonic() - 10
        chat.expire_leases()
        assert chat.actor is None
        assert chat.sid == 'live-sess-bot', 'Idle lease expiry must keep live session intact'
        assert not any(m == 'session.interrupt' for m, _ in calls), 'Must not interrupt idle session'


def test_running_lease_still_interrupts():
    with tempfile.TemporaryDirectory() as tmp:
        chat, calls = make_chat(Path(tmp))
        chat.sid = 'live-sess-bot'
        chat.stored = 'sess-bot'
        chat.info = {'running': True}
        chat.actor = 'org:user'
        chat.lease = time.monotonic() - 10
        chat.expire_leases()
        assert any(m == 'session.interrupt' for m, _ in calls), 'Running turn past lease must interrupt'


def test_poll_and_send_auto_reconnect_after_close_or_restart():
    with tempfile.TemporaryDirectory() as tmp:
        chat, calls = make_chat(Path(tmp))
        # Simulate Box 1 restart / closed process (sid=None)
        chat.close()
        assert chat.sid is None and chat.stored is None

        # Poll with active session_id seamlessly resumes that exact session
        polled = chat.handle({'action': 'poll', 'cursor': 42, 'actor': 'org:user', 'session_id': 'sess-custom'})
        assert polled['session_id'] == 'sess-custom'
        assert chat.sid == 'live-sess-custom'

        # Simulate subprocess dying mid-idle before a user sends a message
        chat.process._alive = False
        sent = chat.handle({
            'action': 'send',
            'actor': 'org:user',
            'expected_session': 'sess-custom',
            'text': 'Hello after idle',
        })
        assert sent['submitted'] is True
        assert chat.stored == 'sess-custom'

        # While alive on sess-custom, sending with a different expected_session still fails safely
        try:
            chat.handle({
                'action': 'send',
                'actor': 'org:user',
                'expected_session': 'sess-other',
                'text': 'Wrong session',
            })
            raise AssertionError('Expected mismatch error')
        except RuntimeError as exc:
            assert 'active conversation changed' in str(exc).lower()


if __name__ == '__main__':
    test_idle_lease_does_not_interrupt_or_close()
    test_running_lease_still_interrupts()
    test_poll_and_send_auto_reconnect_after_close_or_restart()
    print('PASS: idle lease preservation and auto-reconnect on poll/send')
