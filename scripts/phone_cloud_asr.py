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

MODELS = (
    'openai/gpt-4o-mini-transcribe',
    'deepgram/nova-3',
    'google/gemini-2.5-flash',
    'openai/gpt-4o-audio-preview',
)


class CloudRecognizer:
    def __init__(self, config, primary=MODELS[0], fallback=MODELS[1], request=None):
        if primary not in MODELS or fallback not in MODELS or primary == fallback:
            raise ValueError('Unsupported phone transcription models')
        if not config.get('OPENROUTER_API_KEY'):
            raise ValueError('Private transcription credential is required')
        self.key = config['OPENROUTER_API_KEY']
        raw_oa = (config.get('OPENAI_API_KEY') or '').strip()
        self.openai_key = raw_oa if (raw_oa.startswith('sk-') and not raw_oa.startswith('sk-or-')) else ''
        self.primary, self.fallback = primary, fallback
        self.request = request or self.transcribe
        self.preparing = threading.BoundedSemaphore(4)

    def prepare(self, pcm, control, *, confirmation_source=None):
        # Start during an existing silence window. No rolling transcriptions
        # while the caller speaks and at most four bounded in-flight requests.
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
        wav_bytes = audio.getvalue()
        if self.openai_key and model == self.primary:
            try:
                boundary = '----CentralAIVoiceBoundary7MA4YWxkTrZu0gW'
                prompt_hint = (
                    'Live Australian phone call with James from Your Choice Solar. '
                    'Accurately transcribe caller names (e.g. Mark Anthony Ebio), spelled letters, '
                    'Australian street addresses, suburbs (Brisbane, Gold Coast, Sydney, Melbourne, QLD, NSW), '
                    'email addresses, and electricity bill amounts in AUD.'
                )
                body = bytearray()
                for field_name, field_val in (('model', 'gpt-4o-mini-transcribe'), ('language', 'en'), ('response_format', 'json'), ('prompt', prompt_hint)):
                    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{field_name}"\r\n\r\n{field_val}\r\n'.encode('utf-8'))
                body.extend(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="speech.wav"\r\nContent-Type: audio/wav\r\n\r\n'.encode('utf-8')
                )
                body.extend(wav_bytes)
                body.extend(f'\r\n--{boundary}--\r\n'.encode('utf-8'))
                oa_req = urllib.request.Request(
                    'https://api.openai.com/v1/audio/transcriptions',
                    data=bytes(body),
                    headers={
                        'Authorization': 'Bearer ' + self.openai_key,
                        'Content-Type': f'multipart/form-data; boundary={boundary}',
                    },
                )
                control.check()
                with open_request(oa_req, timeout=2.5) as oa_resp:
                    control.track(oa_resp)
                    oa_raw = oa_resp.read(65537)
                    control.check()
                    oa_data = json.loads(oa_raw)
                oa_text = str(oa_data.get('text') or '').strip()
                if oa_text and oa_text not in {'""', "''", '[silence]', '[inaudible]', '[empty]', 'None', 'null'}:
                    return {'text': oa_text}
            except TurnCancelled:
                raise
            except Exception:
                control.check()
        b64_wav = base64.b64encode(wav_bytes).decode()
        # Use OpenRouter multimodal chat completions directly (fast, zero 404 roundtrips)
        chat_model = 'google/gemini-2.5-flash' if model == self.primary else 'openai/gpt-4o-audio-preview'
        chat_payload = {
            'model': chat_model,
            'temperature': 0,
            'max_tokens': 250,
            'reasoning': {'effort': 'none'},
            'provider': {'sort': 'latency', 'allow_fallbacks': True},
            'messages': [{
                'role': 'user',
                'content': [
                    {
                        'type': 'text',
                        'text': (
                            'Transcribe the spoken English phone call audio verbatim. '
                            'Pay close attention to caller names (e.g. Mark Anthony Ebio), spelled-out letters, '
                            'Australian street names and suburbs (e.g. Brisbane, Gold Coast, Sydney, Melbourne), '
                            'phone numbers, email addresses, and power bill amounts. '
                            'Output ONLY the exact words spoken by the caller, with no quotes or commentary. '
                            'If the audio is only silence, breathing, background noise, or unintelligible sound, output an empty string.'
                        )
                    },
                    {'type': 'input_audio', 'input_audio': {'data': b64_wav, 'format': 'wav'}}
                ]
            }]
        }
        chat_request = urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',
            data=json.dumps(chat_payload).encode(), headers={
                'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json',
                'X-Title': 'Central AI speech recognition'})
        control.check()
        with open_request(chat_request, timeout=4) as response:
            control.track(response)
            raw = response.read(65537)
            control.check()
            if len(raw) > 65536:
                raise ValueError('Transcription response too large')
            data = json.loads(raw)
        choices = data.get('choices') or []
        text = ((choices[0].get('message') or {}).get('content') or '').strip() if choices else ''
        if data.get('error') or not isinstance(text, str) or len(text) > 12000:
            raise ValueError('Invalid transcription response')
        # Strip accidental surrounding quotes or empty placeholders from LLM output
        if text in {'""', "''", '[silence]', '[inaudible]', '[empty]', 'None', 'null'}:
            text = ''
        return {'text': text}

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
            if confirmation_source not in MODELS:
                raise ValueError('Unknown transcription source')
            models = tuple(model for model in models if model != confirmation_source)
        for model in models:
            # A second provider after a slow failure only adds dead air; the caller's
            # local Whisper words are the fallback instead.
            if model != models[0] and time.monotonic() - began > 2.0:
                break
            try:
                result = self.request(model, pcm, control)
                control.check()
                text = result['text'].strip()
                if len(text) > 12000:
                    raise ValueError('Transcript too large')
                normal=lambda value:re.sub(r'[^\w\s]','',value.casefold()).split()
                plain=' '.join(normal(text))
                if re.match(r'^(?:thanks for watching|thank you for watching|please subscribe|subtitles by)\b', plain):
                    quality.update(source=model, decision='silence',
                                   finalization_ms=round((time.monotonic()-began)*1000))
                    return '', quality
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
        self._slot = recognizer.preparing
        self._slot_guard = threading.Lock()
        def run():
            try:
                self.result = recognizer.recognize(pcm, control, confirmation_source=confirmation_source)
            except Exception as error:
                self.error = error
            finally:
                self._release_slot()
                self.done.set()
        threading.Thread(target=run, daemon=True).start()

    def _release_slot(self):
        with self._slot_guard:
            if self._slot:
                self._slot.release()
                self._slot = None

    def finish(self, control):
        while not self.done.wait(.02):
            control.check()
        control.check()
        if self.error:
            raise self.error
        return self.result

    def close(self):
        self.control.cancel()
        # A discarded mid-pause request may still be in flight; free its slot now so
        # the caller's real endpoint can still start cloud recognition early.
        self._release_slot()
