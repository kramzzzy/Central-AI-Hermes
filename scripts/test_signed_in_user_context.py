"""Unit tests for ephemeral signed-in App OS user context propagation."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_user_context import (  # noqa: E402
    USER_CONTEXT_END,
    USER_CONTEXT_START,
    clear_user_context_file,
    format_signed_in_user_note,
    install_signed_in_user_context_hook,
    merge_user_context_note,
    read_user_context_note,
    sanitize_signed_in_user,
    strip_user_context_note,
    write_user_context_file,
)
from hermes_chat import NativeChat  # noqa: E402


def test_sanitization_and_formatting():
    assert sanitize_signed_in_user(None) is None
    assert sanitize_signed_in_user('Mark') is None
    assert sanitize_signed_in_user({'id': 'u1', 'name': '   ', 'email': ''}) is None

    cleaned = sanitize_signed_in_user({
        'id': 'user-1',
        'name': 'Mark [Admin]\nAnthony\tEbio',
        'email': 'mark@yourchoicesolar.com.au\r\n',
        'role': 'owner',
        'organization': 'Your Choice Solar [HQ]',
        'timezone': 'Australia/Brisbane',
    })
    assert cleaned == {
        'id': 'user-1',
        'name': 'Mark Admin Anthony Ebio',
        'email': 'mark@yourchoicesolar.com.au',
        'role': 'owner',
        'organization': 'Your Choice Solar HQ',
        'timezone': 'Australia/Brisbane',
    }

    note = format_signed_in_user_note(cleaned)
    assert note.startswith(USER_CONTEXT_START)
    assert note.endswith(USER_CONTEXT_END)
    assert '- Name: Mark Admin Anthony Ebio' in note
    assert '- Role: owner' in note
    assert '- Email: mark@yourchoicesolar.com.au' in note
    assert '- Organisation: Your Choice Solar HQ' in note
    assert '- Timezone: Australia/Brisbane' in note

    # Deduplication and stripping (including stream voice merge with weather context)
    merged_once = merge_user_context_note('Weather: Brisbane 24C', note)
    assert 'Weather: Brisbane 24C' in merged_once
    assert 'Mark Admin Anthony Ebio' in merged_once
    assert merged_once.count(USER_CONTEXT_START) == 1
    merged_twice = merge_user_context_note(merged_once, note)
    assert merged_twice == merged_once
    assert merge_user_context_note('', note) == note
    assert strip_user_context_note(merged_once) == 'Weather: Brisbane 24C'


def test_file_roundtrip_and_hook_ephemeral_attribute():
    with tempfile.TemporaryDirectory() as tmp:
        ctx_file = Path(tmp) / 'user-context.json'
        assert read_user_context_note(ctx_file) == ''

        class FakeAttributeAgent:
            def __init__(self, ephemeral=None):
                self.ephemeral_system_prompt = ephemeral
                self.seen = []

            def run_conversation(self, user_message, system_message=None):
                self.seen.append((user_message, self.ephemeral_system_prompt, system_message))
                if user_message == 'boom':
                    raise RuntimeError('simulated failure')
                return 'ok'

        assert install_signed_in_user_context_hook(context_path=ctx_file, agent_cls=FakeAttributeAgent)
        # Idempotent double-install
        assert install_signed_in_user_context_hook(context_path=ctx_file, agent_cls=FakeAttributeAgent)

        agent = FakeAttributeAgent(ephemeral='Existing voice note')

        # No file yet -> unchanged
        assert agent.run_conversation('hello') == 'ok'
        assert agent.seen[-1] == ('hello', 'Existing voice note', None)

        # Write Mark's signed-in context
        assert write_user_context_file(ctx_file, {
            'id': 'u-mark',
            'name': 'Mark Anthony Ebio',
            'role': 'owner',
            'email': 'mark@example.com',
            'organization': 'Your Choice Solar',
        })
        assert agent.run_conversation('you know me?') == 'ok'
        seen_msg, seen_ephemeral, seen_sys = agent.seen[-1]
        assert seen_msg == 'you know me?'
        assert 'Existing voice note' in seen_ephemeral
        assert 'Mark Anthony Ebio' in seen_ephemeral
        assert seen_sys is None
        # Must restore original attribute after turn
        assert agent.ephemeral_system_prompt == 'Existing voice note'

        # Must restore even when run_conversation raises
        try:
            agent.run_conversation('boom')
        except RuntimeError:
            pass
        assert agent.ephemeral_system_prompt == 'Existing voice note'

        # Switch user to Sarah -> Turn 2 sees Sarah, NOT Mark
        assert write_user_context_file(ctx_file, {'id': 'u-sarah', 'name': 'Sarah Jones', 'role': 'member'})
        assert agent.run_conversation('who am i?') == 'ok'
        _, seen_ephemeral_2, _ = agent.seen[-1]
        assert 'Sarah Jones' in seen_ephemeral_2
        assert 'Mark Anthony Ebio' not in seen_ephemeral_2

        # Clear file -> no user note injected
        clear_user_context_file(ctx_file)
        assert agent.run_conversation('after clear') == 'ok'
        assert agent.seen[-1] == ('after clear', 'Existing voice note', None)


def test_hook_ephemeral_kwarg_and_cached_fallback():
    with tempfile.TemporaryDirectory() as tmp:
        ctx_file = Path(tmp) / 'user-context.json'
        assert write_user_context_file(ctx_file, {'id': 'u1', 'name': 'Mark Anthony Ebio'})

        class FakeKwargAgent:
            def __init__(self):
                self.seen = []

            def run_conversation(self, user_message, ephemeral_system_prompt=None):
                self.seen.append(ephemeral_system_prompt)
                return 'ok'

        assert install_signed_in_user_context_hook(context_path=ctx_file, agent_cls=FakeKwargAgent)
        kw_agent = FakeKwargAgent()
        kw_agent.run_conversation('hi', ephemeral_system_prompt='Base ephemeral')
        assert 'Base ephemeral' in kw_agent.seen[-1]
        assert 'Mark Anthony Ebio' in kw_agent.seen[-1]

        class FakeLegacyCachedAgent:
            def __init__(self):
                self._cached_system_prompt = None
                self.seen = []

            def run_conversation(self, user_message, system_message=None):
                if self._cached_system_prompt is None:
                    self._cached_system_prompt = system_message or 'Base system'
                self.seen.append(self._cached_system_prompt)
                return 'ok'

        assert install_signed_in_user_context_hook(context_path=ctx_file, agent_cls=FakeLegacyCachedAgent)
        legacy = FakeLegacyCachedAgent()
        legacy.run_conversation('turn 1', system_message='You are Leo.')
        assert 'You are Leo.' in legacy.seen[-1]
        assert 'Mark Anthony Ebio' in legacy.seen[-1]
        # Cached prompt was stripped in finally so Mark is not permanently baked in
        assert 'Mark Anthony Ebio' not in legacy._cached_system_prompt
        assert 'You are Leo.' in legacy._cached_system_prompt

        # Turn 2 with a different user injects cleanly and restores cached prompt
        assert write_user_context_file(ctx_file, {'id': 'u2', 'name': 'Alex Smith'})
        legacy.run_conversation('turn 2')
        assert 'Alex Smith' in legacy.seen[-1]
        assert 'Mark Anthony Ebio' not in legacy.seen[-1]
        assert 'Alex Smith' not in legacy._cached_system_prompt


def test_native_chat_binding_and_isolation():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / '.runtime').mkdir(parents=True, exist_ok=True)
        chat = NativeChat({'HERMES_PROFILE': 'leo'}, root)
        try:
            chat.user_context_file = root / '.runtime' / 'native-user-context-test.json'

            # Bind Mark
            chat.bind_workspace({
                'actor': 'org1:mark',
                'workspace_grant': 'grant-1',
                'signed_in_user': {'id': 'mark', 'name': 'Mark Anthony Ebio', 'role': 'owner'},
            })
            assert 'Mark Anthony Ebio' in read_user_context_note(chat.user_context_file)

            # Same actor renew without signed_in_user keeps active turn user context
            chat.bind_workspace({'actor': 'org1:mark', 'workspace_grant': 'grant-1'}, renew=True)
            assert 'Mark Anthony Ebio' in read_user_context_note(chat.user_context_file)

            # Different actor without signed_in_user clears previous user immediately
            chat.bind_workspace({'actor': 'org1:other', 'workspace_grant': 'grant-2'})
            assert read_user_context_note(chat.user_context_file) == ''

            # Re-bind Mark, then send/voice_submit without signed_in_user clears it
            chat.bind_workspace({
                'actor': 'org1:mark',
                'signed_in_user': {'id': 'mark', 'name': 'Mark Anthony Ebio'},
            })
            assert 'Mark Anthony Ebio' in read_user_context_note(chat.user_context_file)
            chat.bind_workspace({'actor': 'org1:mark'}, clear_missing_user=True)
            assert read_user_context_note(chat.user_context_file) == ''

            # Re-bind Mark, then clear_workspace on turn complete removes file
            chat.bind_workspace({
                'actor': 'org1:mark',
                'signed_in_user': {'id': 'mark', 'name': 'Mark Anthony Ebio'},
            })
            assert chat.user_context_file.is_file()
            chat.clear_workspace()
            assert not chat.user_context_file.exists()
        finally:
            chat.shutdown()


if __name__ == '__main__':
    test_sanitization_and_formatting()
    test_file_roundtrip_and_hook_ephemeral_attribute()
    test_hook_ephemeral_kwarg_and_cached_fallback()
    test_native_chat_binding_and_isolation()
    print('PASS: signed-in user context sanitization, ephemeral hook, and NativeChat isolation')
