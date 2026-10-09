"""Authenticated continuous phone audio filter, no audio/transcript storage.

WebRTC APM runs in 10ms blocks. Silero ONNX uses its documented 512-sample
window and 64-sample context at 16kHz; state belongs to one connected call.
"""
import hmac
import json
import threading
from collections import deque
from functools import lru_cache
from pathlib import Path

import numpy as np
import onnxruntime as ort
from livekit import rtc
from websockets.sync.server import serve
from websockets.exceptions import ConnectionClosed


@lru_cache(maxsize=1)
def speech_model():
    options = ort.SessionOptions()
    options.inter_op_num_threads = options.intra_op_num_threads = 1
    return ort.InferenceSession('/opt/setup/silero-vad.onnx',
        providers=['CPUExecutionProvider'], sess_options=options)


class SpeechGate:
    """Clock-preserving mic gate with 180ms onset padding and 240ms tail.

    Decisions come from neural VAD, never a loudness/proximity threshold. Kept
    audio stays verbatim; suppressed frames become silence, not missing time.
    """
    def __init__(self):
        self.frames = deque()
        self.index = -1
        self.open_from = 0
        self.open_until = -1
        self.forwarded = False

    def process(self, pcm, speech):
        if len(pcm) != 1920:
            raise ValueError('Expected 60ms mono PCM16')
        self.index += 1
        self.frames.append((self.index, pcm))
        if speech:
            if self.index > self.open_until:
                self.open_from = max(0, self.index - 3)
            self.open_until = self.index + 4
        if len(self.frames) <= 3:
            self.forwarded = False
            return bytes(1920)
        index, output = self.frames.popleft()
        self.forwarded = self.open_from <= index <= self.open_until
        return output if self.forwarded else bytes(1920)


class PhoneAudioFilter:
    def __init__(self, session):
        self.session = session
        self.apm = rtc.AudioProcessingModule(echo_cancellation=True,
            noise_suppression=True, high_pass_filter=True, auto_gain_control=False)
        # The handset/network delay is not directly measurable at this bridge.
        # AEC3 also estimates echo delay from the actual rendered reference.
        self.apm.set_stream_delay_ms(120)
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)
        self.pending = np.empty(0, dtype=np.float32)
        self.probability = 0.

    def process(self, pcm, reverse=False):
        if len(pcm) != 1920:
            raise ValueError('Expected 60ms mono PCM16')
        result = bytearray()
        for start in range(0, len(pcm), 320):
            frame = rtc.AudioFrame(bytearray(pcm[start:start+320]), 16000, 1, 160)
            if reverse:
                self.apm.process_reverse_stream(frame)
            else:
                self.apm.process_stream(frame)
            result.extend(frame.data.cast('B'))
        if reverse:
            return b''
        audio = np.frombuffer(result, dtype='<i2').astype(np.float32) / 32768.
        self.pending = np.concatenate((self.pending, audio))
        probabilities = []
        while len(self.pending) >= 512:
            chunk, self.pending = self.pending[:512], self.pending[512:]
            data = np.concatenate((self.context, chunk[None, :]), axis=1)
            probability, self.state = self.session.run(None, {
                'input': data, 'state': self.state, 'sr': np.array(16000, dtype=np.int64)})
            self.context = data[:, -64:]
            probabilities.append(float(probability[0, 0]))
        if probabilities:
            self.probability = max(probabilities)
        # The caller consumes a speech decision followed by cleaned PCM16.
        # Threshold 0.35 enables sensitive, instant barge-in during call playback with AEC.
        return bytes([int(self.probability >= .35)]) + bytes(result)


def start_audio_server(key, allowed_call, metrics):
    model = speech_model()
    connected = set()
    lock = threading.Lock()

    def authorize(connection, request):
        if request.path != '/audio' or not hmac.compare_digest(
                request.headers.get('Authorization', ''), 'Bearer ' + key):
            return connection.respond(401, 'Unauthorized')
        call = request.headers.get('X-Call-ID', '')
        if not allowed_call(call):
            return connection.respond(403, 'Inactive call')

    def handle(connection):
        call = connection.request.headers['X-Call-ID']
        with lock:
            if call in connected:
                connection.close(1008, 'Audio connection exists')
                return
            connected.add(call)
        try:
            audio = PhoneAudioFilter(model)
            for message in connection:
                if not allowed_call(call):
                    break
                if not isinstance(message, bytes) or len(message) != 1921 or message[0] not in (1, 2):
                    connection.close(1008, 'Invalid audio frame')
                    break
                output = audio.process(message[1:], reverse=message[0] == 2)
                if output:
                    connection.send(output)
                    metrics['processed_frames'] = metrics.get('processed_frames', 0) + 1
        except ConnectionClosed:
            # A phone hangup also closes its private audio socket. Count only
            # failures while that exact call is still admitted.
            if allowed_call(call):
                metrics['filter_errors'] = metrics.get('filter_errors', 0) + 1
        except Exception:
            # Only a fixed category reaches diagnostics, never payloads/errors.
            metrics['filter_errors'] = metrics.get('filter_errors', 0) + 1
        finally:
            with lock:
                connected.discard(call)
            connection.close()

    server = serve(handle, '0.0.0.0', 8082, process_request=authorize,
        max_size=1921, max_queue=32, compression=None, open_timeout=5,
        ping_interval=10, ping_timeout=10, close_timeout=1)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
