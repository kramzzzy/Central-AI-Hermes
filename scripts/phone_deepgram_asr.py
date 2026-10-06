"""Direct call-local Nova recognition. Audio and words stay in bounded RAM.

The owner still decides when a turn ends. CloseStream flushes all final segments;
interim words are never a completed command. A private direct key is required.
"""
import json
import math
import queue
import struct
import threading
import time
from urllib.parse import urlencode

class StreamingRecognizer:
    remote = True
    source = 'deepgram/nova-3'

    def __init__(self, config, connect=None):
        key = config.get('DEEPGRAM_API_KEY', '').strip()
        if not key or any(char.isspace() for char in key):
            raise ValueError('Private direct recognition credential is required')
        self.key = key
        if connect is None:
            from websockets.sync.client import connect
        self.connect = connect
        params = [('model', 'nova-3'), ('language', 'en-AU'),
                  ('encoding', 'linear16'), ('sample_rate', '16000'), ('channels', '1'),
                  ('interim_results', 'true'), ('endpointing', 'false'),
                  ('punctuate', 'true'), ('smart_format', 'false')]
        params += [('keyterm', name) for name in ('Leo', 'Mark Tech', 'Michael Vazquez')]
        self.url = 'wss://api.deepgram.com/v1/listen?' + urlencode(params)

    def new_turn(self):
        return StreamingTurn(self)


class StreamingTurn:
    remote = True
    max_bytes = 16000 * 2 * 30

    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.lock = threading.RLock()
        self.audio = queue.Queue(maxsize=64)
        self.stopped, self.done = threading.Event(), threading.Event()
        self.socket = self.thread = None
        self.ending = self.consumed = self.final = False
        self.failure = None
        self.partial = ''
        self.partial_changes = self.samples = 0
        self.energy = 0.
        self.segments = {}
        self.metadata_duration = None

    def feed(self, pcm):
        if not isinstance(pcm, bytes) or len(pcm) % 2 or len(pcm) > self.max_bytes:
            raise ValueError('Invalid bounded PCM16')
        with self.lock:
            if self.stopped.is_set() or self.ending or self.done.is_set():
                return
            if (self.samples * 2 + len(pcm)) > self.max_bytes:
                self.failure = 'capture_limit'
                self.stopped.set()
                return
            samples = struct.unpack('<' + 'h' * (len(pcm)//2), pcm)
            self.samples += len(samples)
            self.energy += sum(value*value for value in samples)
            # The microphone thread never waits for a network operation. The
            # finite queue can absorb a connection handshake and a resumed turn.
            for start in range(0, len(pcm), 32000):
                try:
                    self.audio.put_nowait(pcm[start:start+32000])
                except queue.Full:
                    self.failure = 'audio_backpressure'
                    self.stopped.set()
                    return
            if pcm and self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()

    def receive(self, raw):
        if not isinstance(raw, str) or len(raw) > 65536:
            raise ValueError('Invalid recognition envelope')
        event = json.loads(raw)
        if event.get('type') == 'Error':
            raise ValueError('Recognition unavailable')
        if event.get('type') != 'Results':
            if event.get('type') == 'Metadata':self.metadata_duration=event.get('duration')
            return event.get('type')
        alternative = event['channel']['alternatives'][0]
        text = alternative['transcript'].strip()
        if not isinstance(text, str) or len(text) > 12000:
            raise ValueError('Invalid bounded transcript')
        start, duration = float(event['start']), float(event['duration'])
        if not math.isfinite(start+duration) or start < 0 or duration < 0:
            raise ValueError('Invalid recognition timing')
        with self.lock:
            if event.get('is_final') and text:
                key = (round(start, 6), round(duration, 6))
                self.segments[key] = alternative
            prefix = ' '.join(segment['transcript'].strip()
                              for _, segment in sorted(self.segments.items()))
            partial = prefix if event.get('is_final') else (prefix + ' ' + text).strip()
            if len(partial) > 12000 or len(self.segments) > 256:
                raise ValueError('Recognition turn too large')
            if partial != self.partial:
                self.partial = partial
                self.partial_changes += 1
        return 'Results'

    def run(self):
        close_sent = False
        opened = time.monotonic()
        try:
            with self.recognizer.connect(self.recognizer.url,
                    additional_headers={'Authorization': 'Token ' + self.recognizer.key},
                    open_timeout=3, close_timeout=.2, max_size=65536, max_queue=16) as socket:
                self.socket = socket
                while not self.stopped.is_set():
                    for _ in range(16):
                        try:
                            pcm = self.audio.get_nowait()
                        except queue.Empty:
                            break
                        socket.send(pcm)
                    with self.lock:
                        if self.ending and self.audio.empty() and not close_sent:
                            # CloseStream guarantees buffered final results and
                            # trailing Metadata, even without from_finalize.
                            socket.send(json.dumps({'type': 'CloseStream'}))
                            close_sent = True
                    try:
                        kind = self.receive(socket.recv(timeout=.02))
                    except TimeoutError:
                        if time.monotonic()-opened > 35:
                            raise TimeoutError('Recognition turn deadline')
                        continue
                    if kind == 'Metadata' and close_sent:
                        duration=self.metadata_duration
                        if not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration < self.samples/16000-.1:
                            raise ValueError('Incomplete recognition receipt')
                        self.final = True
                        break
        except Exception:
            # No provider payload, words, audio or credentials enter logs.
            if not self.stopped.is_set():
                self.failure = 'transport'
        finally:
            self.socket = None
            self.done.set()

    def finish(self, control=None):
        began = time.monotonic()
        with self.lock:
            if self.consumed:
                raise ValueError('Recognition turn already consumed')
            self.consumed = self.ending = True
        try:
            while self.thread and not self.done.wait(.02):
                if control:
                    control.check()
                if time.monotonic()-began >= 2:
                    self.failure = 'transport'
                    break
            if control:
                control.check()
            rms = math.sqrt(self.energy/max(1, self.samples))/32768
            quality = {'source': self.recognizer.source, 'transport': 'websocket',
                       'language': 'en-AU', 'audio_ms': round(self.samples/16),
                       'partial_changes': self.partial_changes, 'rms': round(rms, 5),
                       'finalization_ms': round((time.monotonic()-began)*1000),
                       'decision': 'silence', 'fallback_allowed': False}
            if rms <= .004:
                return '', quality
            if self.failure or not self.final:
                quality.update(decision='clarify', failure=self.failure or 'transport',
                               fallback_allowed=self.failure in {None, 'transport'})
                return '', quality
            ordered = [value for _, value in sorted(self.segments.items())]
            text = ' '.join(value['transcript'].strip() for value in ordered)
            if not text:
                return '', quality
            confidences, minimum = [], 1.
            for segment in ordered:
                score = segment.get('confidence')
                words = segment.get('words')
                if not self.valid_score(score) or not words:
                    quality.update(decision='clarify', reason='confidence_missing')
                    return '', quality
                for word in words:
                    value = word.get('confidence')
                    if not self.valid_score(value):
                        quality.update(decision='clarify', reason='confidence_missing')
                        return '', quality
                    minimum = min(minimum, value)
                    confidences.append(value)
                if score < .8:
                    quality.update(decision='clarify', reason='low_confidence')
                    return '', quality
            confidence = sum(confidences)/len(confidences)
            quality.update(confidence=round(confidence, 4), min_word_confidence=round(minimum, 4))
            if confidence < .82 or minimum < .45:
                quality.update(decision='clarify', reason='low_confidence')
                return '', quality
            quality['decision'] = 'accepted'
            return text, quality
        finally:
            self.close()

    @staticmethod
    def valid_score(value):
        return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1

    def close(self):
        self.stopped.set()
        socket = self.socket
        if socket:
            # Closing is bounded by the configured .2s websocket close timeout.
            try:socket.close()
            except Exception:pass
        while not self.audio.empty():
            try:
                self.audio.get_nowait()
            except queue.Empty:
                break
