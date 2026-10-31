"""Shared bounded PCM synthesis for app calls, phone calls and narration."""
import os
import re
import time

MODEL = 'google/gemini-3.8-flash-lite-tts'
VOICE = 'Charon'
INSTRUCTIONS = ('Speak in a calm, composed male voice with clear Australian English, '
                'measured conversational pacing and restrained warmth. Read only the supplied text.')


def settings(config=None):
    config = config or {}
    return {name: str(config.get(name) or os.environ.get(name) or default).strip()
            for name, default in [('OPENROUTER_API_KEY', ''), ('OPENROUTER_TTS_MODEL', MODEL),
                                  ('OPENROUTER_TTS_VOICE', VOICE),
                                  ('OPENROUTER_TTS_INSTRUCTIONS', INSTRUCTIONS)]}


def voice_cache_key(config=None):
    value = settings(config)
    return tuple(value[name] for name in ('OPENROUTER_TTS_MODEL', 'OPENROUTER_TTS_VOICE',
                                         'OPENROUTER_TTS_INSTRUCTIONS'))


def synthesize(text, config=None, rate=24000, audio=None, control=None, references=None):
    import audioop
    from voice_http import client
    if not isinstance(text, str) or not 0 < len(text.strip()) <= 12000:
        raise ValueError('Speech text must contain 1–12000 characters')
    if rate not in (16000, 24000):
        raise ValueError('Unsupported speech sample rate')
    cfg = settings(config)
    if not cfg['OPENROUTER_API_KEY']:
        raise RuntimeError('OPENROUTER_API_KEY is not configured')
    text = re.sub(r'\[(?:happy|laughing|curious|empathetic|calm|sigh|laugh|chuckle|pause)\]\s*', '', text, flags=re.I).strip()
    if not text:
        return b''
    payload = {'model': cfg['OPENROUTER_TTS_MODEL'], 'input': text, 'response_format': 'pcm',
               'voice': cfg['OPENROUTER_TTS_VOICE'], 'instructions': cfg['OPENROUTER_TTS_INSTRUCTIONS']}
    if references:
        # Saved narration samples need a cloning-capable model, not a preset voice ID.
        payload.update(model='heygen/heygen-voice-1', input_references=references)
        payload.pop('voice')
    if control:
        control.check()
    chunks, size, carry, state = [], 0, b'', None
    deadline = time.monotonic() + 180
    with client().stream('POST', 'https://openrouter.ai/api/v1/audio/speech', json=payload,
                         headers={'Authorization': 'Bearer ' + cfg['OPENROUTER_API_KEY']},
                         timeout=30.0) as response:
        response.raise_for_status()
        content_type = response.headers.get('content-type', '').lower()
        if not any(kind in content_type for kind in ('audio/pcm', 'audio/l16', 'application/octet-stream')):
            raise RuntimeError('Speech provider returned an unexpected audio format')
        match = re.search(r'(?:rate|samplerate|sample_rate)\s*=\s*"?(\d+)', content_type)
        source_rate = int(match.group(1)) if match else 24000
        if not 8000 <= source_rate <= 96000:
            raise RuntimeError('Invalid speech sample rate')
        channels = re.search(r'channels\s*=\s*"?(\d+)', content_type)
        if channels and channels.group(1) != '1':
            raise RuntimeError('Speech must be mono')
        if control:
            control.track(response)
        for chunk in response.iter_bytes(chunk_size=1920):
            if control:
                control.check()
            size += len(chunk)
            if size > source_rate * 2 * 180 or time.monotonic() > deadline:
                raise RuntimeError('Speech exceeded its output limit')
            data = carry + chunk
            cut = len(data) - len(data) % 2
            carry, data = data[cut:], data[:cut]
            if not data:
                continue
            if source_rate != rate:
                data, state = audioop.ratecv(data, 2, 1, source_rate, rate, state)
            if audio:
                audio(data)
            else:
                chunks.append(data)
        if carry or not size:
            raise RuntimeError('Speech provider returned incomplete audio')
    return b''.join(chunks)
