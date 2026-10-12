"""Offline regression checks: real sender/native methods, fake transport/providers."""
import ast
import asyncio
import json
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace
import unittest
from uuid import uuid4

SCRIPTS = Path(__file__).resolve().parents[1]


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_is_paced_and_interruption_discards_queued_generation(self):
        tree = ast.parse((SCRIPTS / 'whatsapp_fish.py').read_text())
        service_class = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'FishService')
        socket = next(n for n in service_class.body if n.name == 'socket')
        sender = next(n for n in socket.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'send')
        session = SimpleNamespace(closed=False, generation=1, call='test', output=asyncio.Queue())
        session.state_event = lambda: str(session.generation)
        service = SimpleNamespace(state_changed=asyncio.Event(), allowed=lambda call: True)
        sent = []
        first = asyncio.Event()

        async def send(value):
            sent.append((time.monotonic(), value))
            if isinstance(value, bytes):
                first.set()

        ns = dict(asyncio=asyncio, time=time, struct=struct, session=session, self=service,
                  connection=SimpleNamespace(send=send))
        exec(compile(ast.Module(body=[sender], type_ignores=[]), '<sender>', 'exec'), ns)
        for _ in range(30):
            session.output.put_nowait(struct.pack('>Q', 1) + bytes(1920))
        task = asyncio.create_task(ns['send']())
        try:
            await asyncio.wait_for(first.wait(), 1)
            await asyncio.sleep(.08)
            before = [item for item in sent if isinstance(item[1], bytes)]
            self.assertLessEqual(len(before), 2, 'audio must not burst into socket buffers')
            self.assertGreaterEqual(before[1][0] - before[0][0], .055)
            interrupted_at = time.monotonic()
            session.generation = 2
            service.state_changed.set()
            while not session.output.empty():
                session.output.get_nowait()
            session.output.put_nowait(struct.pack('>Q', 2) + bytes(1920))
            await asyncio.sleep(.15)
            self.assertIn('2', [value for _, value in sent])
            after = [(at, value) for at, value in sent if at > interrupted_at]
            self.assertTrue(all(not isinstance(value, bytes) or struct.unpack('>Q', value[:8])[0] == 2
                                for _, value in after), 'canceled audio escaped after interrupt')
            self.assertTrue(any(isinstance(value, bytes) for _, value in after))
        finally:
            session.closed = True
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class CancellationTests(unittest.TestCase):
    def test_canceled_event_batch_cannot_emit_or_finish_a_reply(self):
        tree = ast.parse((SCRIPTS / 'native_voice_conversation.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
        ns = dict(json=json, threading=threading, time=time, uuid4=uuid4,
                  is_casual_greeting_intent=lambda text: False)
        exec(compile(ast.Module(body=[cls], type_ignores=[]), '<native>', 'exec'), ns)
        class Cancelled(Exception):
            pass
        class Control:
            cancelled = threading.Event()
            def check(self):
                if self.cancelled.is_set():
                    raise Cancelled()
        control = Control()
        actions, spoken = [], []
        def dispatch(action, call, **kwargs):
            actions.append(action)
            if action == 'voice_submit':
                return dict(cursor=0, epoch='test')
            if action == 'voice_events':
                control.cancelled.set()
                # A late response must be ignored even before reading its fields.
                return None
            return {}
        call = ns['NativeVoiceConversation'](dispatch, lambda: None, 'test')
        try:
            with self.assertRaises(Cancelled):
                call.reply('weather please', spoken.append, control)
            self.assertEqual(actions, ['voice_submit', 'voice_events', 'voice_stop'])
            self.assertEqual(spoken, [])
            self.assertFalse(call.guard.locked())
        finally:
            call.close()


if __name__ == '__main__':
    unittest.main()
