"""Call-local English recognition while audio arrives, with no transcript storage."""
from pathlib import Path
import threading
import time

import numpy as np


class StreamingRecognizer:
    def __init__(self, directory, recognizer=None):
        if recognizer is None:
            import sherpa_onnx
            directory = Path(directory)
            recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(directory/'tokens.txt'),
                encoder=str(directory/'encoder-epoch-99-avg-1.int8.onnx'),
                decoder=str(directory/'decoder-epoch-99-avg-1.onnx'),
                joiner=str(directory/'joiner-epoch-99-avg-1.int8.onnx'),
                num_threads=2, sample_rate=16000, feature_dim=80,
                decoding_method='greedy_search', enable_endpoint_detection=False)
        self.recognizer = recognizer

    def new_turn(self):
        return StreamingTurn(self.recognizer)


class StreamingTurn:
    def __init__(self, recognizer):
        self.recognizer = recognizer
        self.stream = recognizer.create_stream()
        self.lock = threading.RLock()
        self.finished = False
        self.samples = 0
        self.energy = 0.
        self.partial = ''
        self.partial_changes = 0
        self.total_ms = self.max_ms = 0.

    def decode(self):
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        partial = self.recognizer.get_result(self.stream).strip()
        if partial != self.partial:
            self.partial = partial
            self.partial_changes += 1

    def feed(self, pcm):
        with self.lock:
            if self.finished:
                return  # The endpoint already owns this finalized turn.
            began = time.monotonic()
            samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32)/32768.
            self.samples += len(samples)
            self.energy += float(np.dot(samples, samples))
            self.stream.accept_waveform(16000, samples)
            self.decode()
            elapsed = (time.monotonic()-began)*1000
            self.total_ms += elapsed
            self.max_ms = max(self.max_ms, elapsed)

    def finish(self):
        with self.lock:
            if self.finished:
                raise ValueError('Recognition turn already consumed')
            self.finished = True
            began = time.monotonic()
            # This is model look-ahead, decoded immediately. It is not a wall
            # clock pause and is never sent to the handset or the assistant.
            self.stream.accept_waveform(16000, np.zeros(6400, dtype=np.float32))
            self.stream.input_finished()
            self.decode()
            rms = (self.energy/max(1,self.samples))**.5
            quality = {'language':'en', 'source':'streaming-zipformer-en',
                'audio_ms':round(self.samples/16), 'partial_changes':self.partial_changes,
                'finalization_ms':round((time.monotonic()-began)*1000),
                'decision':'accepted' if self.partial and rms>.004 else 'clarify' if rms>.004 else 'silence'}
            return (self.partial if quality['decision']=='accepted' else ''), quality

    def close(self):
        with self.lock:self.finished=True
