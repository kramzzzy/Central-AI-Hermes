"""Bounded, transient WAV recognition on the VPS; no cloud fallback."""
import io
import threading
import wave

_guard = threading.Lock()
_model = None


def transcribe(data):
    global _model
    import numpy as np
    import audioop
    with wave.open(io.BytesIO(data), 'rb') as source:
        rate = source.getframerate()
        if source.getsampwidth() != 2 or source.getnchannels() != 1 or rate not in (16000, 22050, 24000, 44100, 48000) or source.getnframes() > rate * 45:
            raise ValueError('Send at most 45 seconds of mono PCM WAV')
        raw = source.readframes(source.getnframes())
        if rate != 16000:
            raw, _ = audioop.ratecv(raw, 2, 1, rate, 16000, None)
        samples = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768
    if not len(samples) or float(np.sqrt(np.mean(samples * samples))) < .004:
        return ''
    if not _guard.acquire(timeout=2):
        raise RuntimeError('Local recognition is busy')
    try:
        if _model is None:
            from faster_whisper import WhisperModel
            _model = WhisperModel('/opt/voice-models/whisper-base.en', device='cpu', compute_type='int8', cpu_threads=2)
        segments, _ = _model.transcribe(samples, language='en', beam_size=3,
            temperature=0, vad_filter=True, condition_on_previous_text=False)
        parts = list(segments)
        if any(s.avg_logprob < -1 or s.no_speech_prob > .6 or s.compression_ratio > 2.4 for s in parts):
            return ''
        return ' '.join(s.text.strip() for s in parts).strip()
    finally:
        _guard.release()
