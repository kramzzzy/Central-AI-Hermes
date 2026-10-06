"""Bounded final phone transcription; no audio or transcript persistence."""
import base64
import io
import json
import math
import re
import struct
import threading
import time
import urllib.request
import wave

from stream_conversation import TurnCancelled
from voice_http import open_request

MODELS = ('openai/gpt-4o-mini-transcribe', 'deepgram/nova-3')


class CloudRecognizer:
    def __init__(self, config, primary=MODELS[0], fallback=MODELS[1], request=None):
        if primary not in MODELS or fallback not in MODELS or primary == fallback:
            raise ValueError('Unsupported phone transcription models')
        if not config.get('OPENROUTER_API_KEY'):
            raise ValueError('Private transcription credential is required')
        self.key = config['OPENROUTER_API_KEY']
        self.primary, self.fallback = primary, fallback
        self.request = request or self.transcribe
        self.preparing = threading.BoundedSemaphore(2)

    def prepare(self, pcm, control, *, confirmation_source=None):
        # Start during an existing silence window. No rolling transcriptions
        # while the caller speaks and at most two bounded in-flight requests.
        if not self.preparing.acquire(blocking=False):
            return None
        return PreparedRecognition(self, pcm, control, confirmation_source=confirmation_source)

    def transcribe(self, model, pcm, control):
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(16000)
            file.writeframes(pcm)
        payload = {'model': model,
                   'input_audio': {'data': base64.b64encode(audio.getvalue()).decode(), 'format': 'wav'},
                   'language': 'en', 'temperature': 0, 'response_format': 'json'}
        request = urllib.request.Request('https://openrouter.ai/api/v1/audio/transcriptions',
            data=json.dumps(payload).encode(), headers={
                'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json',
                'X-Title': 'Central AI speech recognition'})
        control.check()
        with open_request(request, timeout=3) as response:
            control.track(response)
            raw = response.read(65537)
            control.check()
            if len(raw) > 65536:
                raise ValueError('Transcription response too large')
            result = json.loads(raw)
        if result.get('error') or not isinstance(result.get('text'), str) or len(result['text']) > 12000:
            raise ValueError('Invalid transcription response')
        return result

    def recognize(self, pcm, control, *, confirmation_source=None):
        control.check()
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > 16000*2*30:
            raise ValueError('Invalid bounded mono PCM16')
        # VAD admits utterances before this boundary; silence cannot be turned
        # into a command even if a provider invents a nonempty transcript.
        samples = struct.unpack('<' + 'h'*(len(pcm)//2), pcm)
        rms = math.sqrt(sum(value*value for value in samples)/len(samples))/32768
        quality = {'language': 'en', 'audio_ms': round(len(pcm)/32), 'source': 'cloud-transcription',
                   'decision': 'silence', 'provider_failures': 0, 'rms': round(rms,5)}
        if rms <= .004:
            return '', quality
        began = time.monotonic()
        models = (self.primary, self.fallback)
        if confirmation_source:
            # Independent confirmation must use a different recognizer. If it
            # is unavailable, the caller repeats rather than starting work.
            if confirmation_source not in MODELS:
                raise ValueError('Unknown transcription source')
            models = tuple(model for model in models if model != confirmation_source)
        for model in models:
            try:
                result = self.request(model, pcm, control)
                control.check()
                text = result['text'].strip()
                if len(text) > 12000:
                    raise ValueError('Transcript too large')
                normal=lambda value:re.sub(r'[^\w\s]','',value.casefold()).split()
                plain=' '.join(normal(text))
                if not confirmation_source and re.match(r'^(?:(?:okay|ok|alright) )?(?:good ?bye|bye|thanks for watching|thank you for watching|please subscribe)\b',plain):
                    verified,other=self.recognize(pcm,control,confirmation_source=model)
                    if other['decision']!='accepted' or normal(verified)!=normal(text):
                        quality.update(source=model,decision='silence',rejected_unconfirmed_farewell=True,
                                       finalization_ms=round((time.monotonic()-began)*1000))
                        return '',quality
                # OpenRouter currently omits word confidence for these routes.
                # Do not fabricate a score or replace an empty result with the
                # small local recognizer that caused the reported mishearing.
                quality.update(source=model, decision='accepted' if text else 'clarify',
                               finalization_ms=round((time.monotonic()-began)*1000))
                return text, quality
            except TurnCancelled:
                raise
            except Exception:
                control.check()
                quality['provider_failures'] += 1
        quality.update(decision='clarify', finalization_ms=round((time.monotonic()-began)*1000))
        return '', quality


class PreparedRecognition:
    """A whole utterance snapshot, reusable only when no speech followed it."""
    def __init__(self, recognizer, pcm, control, *, confirmation_source=None):
        self.pcm, self.control = pcm, control
        self.done = threading.Event()
        self.result = self.error = None
        def run():
            try:
                self.result = recognizer.recognize(pcm, control, confirmation_source=confirmation_source)
            except Exception as error:
                self.error = error
            finally:
                recognizer.preparing.release()
                self.done.set()
        threading.Thread(target=run, daemon=True).start()

    def finish(self, control):
        while not self.done.wait(.02):
            control.check()
        control.check()
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.control.cancel()
