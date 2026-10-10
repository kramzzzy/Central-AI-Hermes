"""Audio-channel adapter. Hermes owns history, reasoning and tools."""
import json
import threading
import time
from uuid import uuid4
from fish_conversation import is_casual_greeting_intent


class NativeVoiceConversation:
    def __init__(self, dispatch, close, call, admitted=lambda: True):
        self.dispatch, self.release, self.call = dispatch, close, call
        self.admitted = admitted
        self.guard = threading.Lock()
        self.close_guard = threading.Lock()
        self.released = False
        self.closed = threading.Event()
        self.last_timing = {}
        threading.Thread(target=self.keep_alive, daemon=True).start()

    def keep_alive(self):
        while not self.closed.wait(8):
            try:
                if not self.admitted():
                    self.close()
                    return
                self.dispatch('voice_heartbeat', self.call)
            except Exception:
                self.close()  # Never reconnect/replay an accepted action.
                break

    def close(self):
        self.closed.set()
        with self.close_guard:
            if not self.released:
                self.released = True
                self.release()

    def reply(self, text, speak, control, notice=False, on_event=None):
        control.check()
        if self.closed.is_set() or not self.admitted():
            raise RuntimeError('Native call ended')
        if notice:
            value = json.loads(text)
            parts = []
            for task in value.get('tasks', [])[:2]:
                result = task.get('result') or {}
                message = result.get('message') if isinstance(result, dict) else None
                parts.append('Task status: ' + str(task.get('status', 'unknown')) + '. ' +
                             str(message or 'No completed result was provided.'))
            speak((' '.join(parts) or 'No task result was provided.')[:4000])
            return  # Result data never becomes a tool-capable model prompt.
        if is_casual_greeting_intent(text):
            speak('Hey! What can I help you with?')
            return  # Greetings cannot authorize work.
        if not self.guard.acquire(timeout=8):
            raise RuntimeError('Previous native turn is still settling')
        turn, submitted, finished = str(uuid4()), False, False
        began = time.monotonic()
        try:
            control.check()
            if self.closed.is_set() or not self.admitted():
                raise RuntimeError('Native call ended')
            submitted = True  # Even a failed receipt can have an unknown outcome.
            receipt = self.dispatch('voice_submit', self.call, text=text, turn_id=turn)
            if not isinstance(receipt, dict) or 'cursor' not in receipt or 'epoch' not in receipt:
                raise RuntimeError('Native submission was not acknowledged')
            cursor, reply, sent, complete = receipt['cursor'], '', '', False
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                control.check()
                if self.closed.is_set() or not self.admitted():
                    raise RuntimeError('Native call lease expired')
                state = self.dispatch('voice_events', self.call, turn_id=turn,
                                      epoch=receipt['epoch'], cursor=cursor)
                cursor = state['cursor']
                for event in state.get('events', []):
                    kind, payload = event.get('type'), event.get('payload') or {}
                    if kind == 'message.delta':
                        reply += payload.get('text', '')
                    elif kind in {'message.interim', 'message.complete'}:
                        if kind == 'message.interim' and payload.get('already_streamed'):
                            continue
                        if kind == 'message.complete' and payload.get('status', 'complete') != 'complete':
                            raise RuntimeError('Native reply did not complete')
                        reply = payload.get('text') or reply
                        complete = kind == 'message.complete'
                    elif kind in {'error', 'turn.error', 'turn.interrupted'}:
                        raise RuntimeError('Native reply failed; check status before retrying')
                    elif kind == 'widget.action' and on_event:
                        control.check()
                        on_event(dict(payload, type='widget'))
                    control.check()
                    if reply.startswith(sent) and len(reply) > len(sent):
                        speak(reply[len(sent):])
                        sent = reply
                    elif sent and reply != sent:
                        reply = sent  # Do not replay already spoken snapshots.
                    if kind == 'message.interim':
                        speak(' ')
                        reply = sent = ''
                if state.get('review'):
                    speak(' This requires approval. No completion was confirmed; check Chat before retrying.')
                    return
                if complete and not state.get('running'):
                    self.dispatch('voice_finish', self.call, turn_id=turn)
                    finished = True
                    return
                if not state.get('events'):
                    control.cancelled.wait(.02)
            raise RuntimeError('Native reply timed out; check status before retrying')
        finally:
            try:
                if submitted and not finished:
                    self.dispatch('voice_stop', self.call, turn_id=turn)
            finally:
                self.last_timing = {'native_ms': round((time.monotonic()-began)*1000)}
                self.guard.release()
