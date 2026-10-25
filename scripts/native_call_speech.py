"""PCM call adapter for Hermes's bundled, CPU-only Piper provider.

Call authority and interruption remain in TurnControl. Only the synthesis model
is shared; text/audio are transient and are never written to the model cache.
"""
import os
import queue
import re
import threading
import time
from pathlib import Path

_synthesis = threading.BoundedSemaphore(1)
_voice_cache = {}
_voice_cache_lock = threading.Lock()


def _cached_piper_voice(config):
    from tools.tts_tool_local import _load_piper_voice_for_config
    cfg = tts_config(config)
    piper_cfg = cfg.get('piper') or {}
    cache_key = (
        str(piper_cfg.get('voice') or ''),
        bool(piper_cfg.get('use_cuda', False)),
        str(piper_cfg.get('voices_dir') or ''),
    )
    with _voice_cache_lock:
        cached = _voice_cache.get(cache_key)
    if cached is not None:
        return cached
    loaded = _load_piper_voice_for_config(cfg)
    with _voice_cache_lock:
        _voice_cache[cache_key] = loaded
    return loaded


def warm_piper(config=None):
    try:
        _cached_piper_voice(config)
        return True
    except Exception:
        return False


def native_enabled(config):
    cfg = config or {}
    speech = cfg.get('CENTRAL_AI_CALL_SPEECH') or os.environ.get('CENTRAL_AI_CALL_SPEECH') or os.environ.get('HERMES_CALL_SPEECH')
    fish_key = (cfg.get('FISH_API_KEY') or os.environ.get('FISH_API_KEY') or '').strip()
    if speech == 'fish':
        return False
    if fish_key and speech != 'piper':
        return False
    return speech == 'piper' or not fish_key


def tts_config(config=None):
    import yaml
    cfg = config or {}
    conf_path = cfg.get('CENTRAL_AI_VOICE_CONFIG') or '/opt/data/profiles/leo/config.yaml'
    path = Path(conf_path)
    if path.exists():
        value = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
        tts = value.get('tts') or {}
    else:
        tts = {}
    tts.setdefault('provider', 'piper')
    tts.setdefault('piper', {})
    default_voice = '/opt/voice-models/en_GB-alan-medium.onnx'
    if not Path(default_voice).exists() and Path('/opt/voice-models/en_US-lessac-medium.onnx').exists():
        default_voice = '/opt/voice-models/en_US-lessac-medium.onnx'
    existing_voice = tts['piper'].get('voice')
    if existing_voice and not Path(existing_voice).exists():
        tts['piper']['voice'] = default_voice
    elif not existing_voice:
        tts['piper']['voice'] = default_voice
    tts['piper'].setdefault('use_cuda', False)
    tts['piper'].setdefault('voices_dir', str(path.parent / 'cache' / 'piper-voices'))
    return tts


def pcm(text, config, rate, control):
    import audioop
    from piper import SynthesisConfig
    deadline = time.monotonic() + 30
    while not _synthesis.acquire(timeout=.1):
        control.check()
        if time.monotonic() > deadline:
            raise RuntimeError('Local speech is busy')
    try:
        control.check()
        voice, options = _cached_piper_voice(config)
        syn = SynthesisConfig(**{k: options[k] for k in (
            'speaker_id', 'length_scale', 'noise_scale', 'noise_w_scale',
            'volume', 'normalize_audio') if k in options})
        state = None
        for chunk in voice.synthesize(text, syn_config=syn):
            control.check()
            if time.monotonic() > deadline:
                raise RuntimeError('Local speech deadline exceeded')
            data = chunk.audio_int16_bytes
            if chunk.sample_rate != rate:
                data, state = audioop.ratecv(data, 2, 1, chunk.sample_rate, rate, state)
            for start in range(0, len(data), rate // 10 * 2):
                control.check()
                yield data[start:start + rate // 10 * 2]
    finally:
        _synthesis.release()


class NativeSpeech:
    """Synthesize completed sentences while subsequent model tokens arrive."""
    def __init__(self, config, audio, control, rate=16000):
        from tools.tts_streaming import SentenceChunker
        self.config, self.audio, self.control, self.rate = config, audio, control, rate
        self.chunker = SentenceChunker.from_config(tts_config(config))
        self.lead_buffer = ''
        self.queue = queue.Queue(maxsize=32)
        self.done = threading.Event()
        self.error = None
        self.first_audio = self.first_flush = None
        self.bytes = 0
        self.complete = False
        self.deadline = time.monotonic() + 180
        threading.Thread(target=self.receive, daemon=True).start()

    def receive(self):
        try:
            while time.monotonic() < self.deadline:
                self.control.check()
                try:
                    text = self.queue.get(timeout=.04)
                except queue.Empty:
                    continue
                if text is None:
                    self.complete = True
                    return
                for data in pcm(text, self.config, self.rate, self.control):
                    self.bytes += len(data)
                    if self.bytes > self.rate * 2 * 180:
                        raise RuntimeError('Speech size exceeded')
                    self.control.check()
                    self.first_audio = self.first_audio or time.monotonic()
                    self.audio(data)
            raise RuntimeError('Speech deadline exceeded')
        except Exception as exc:
            self.error = type(exc).__name__
        finally:
            self.done.set()

    def text(self, value):
        cleaned = value.replace('**', '').replace('`', '').replace('*', '')
        for sentence in self.chunker.feed(cleaned):
            self.sentence(sentence)

    def sentence(self, text):
        self.control.check()
        if self.error:
            raise RuntimeError('Local speech is unavailable')
        self.queue.put_nowait(text)
        self.first_flush = self.first_flush or time.monotonic()

    def finish(self):
        for sentence in self.chunker.flush():
            self.sentence(sentence)
        self.queue.put_nowait(None)
        while not self.done.wait(.05):
            self.control.check()
            if time.monotonic() > self.deadline:
                raise RuntimeError('Speech did not finish')
        self.control.check()
        if self.error or not self.complete or not self.bytes:
            raise RuntimeError('Speech did not finish')
