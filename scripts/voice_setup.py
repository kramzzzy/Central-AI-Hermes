"""Private, persistent App OS call settings; never returns provider credentials."""
import json
import re
from pathlib import Path
from central_ai_integrations import atomic_write


def settings_path(settings):
    path = Path(settings['HERMES_PROFILE_ROOT']) / '.app-voice.json'
    if path.is_symlink():
        raise RuntimeError('Voice settings must be a private regular file')
    return path


def load_settings(settings):
    from coolify_voice import configured
    if configured(): return {}
    path = settings_path(settings)
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding='utf-8'))
    allowed = {'CENTRAL_AI_CALL_SPEECH', 'FISH_API_KEY', 'FISH_VOICE_ID', 'FISH_VOICE_NAME', 'FISH_OS_CALL_ENGINE', 'FISH_ASR_ENABLED'}
    if not isinstance(value, dict) or set(value) - allowed or any(not isinstance(v, str) for v in value.values()):
        raise RuntimeError('Invalid private voice settings')
    return value


def validate(value):
    if not isinstance(value, dict) or set(value) - {'provider', 'api_key', 'voice_id'} or value.get('provider') not in {'native', 'fish'}:
        raise ValueError('Choose a voice provider')
    if value['provider'] == 'native':
        if set(value) != {'provider'}:
            raise ValueError('Economy voice does not need a provider key')
        return
    key = value.get('api_key', '')
    if key and (not isinstance(key, str) or len(key) > 4096 or not re.fullmatch(r'[\x21-\x7e]+', key)):
        raise ValueError('Enter a valid Fish API key')
    voice_id = value.get('voice_id')
    if voice_id is not None and (not isinstance(voice_id, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', voice_id)):
        raise ValueError('Enter the 32-character Fish Voice ID')


def configure(settings, value):
    validate(value)
    from hermes_voice import VoiceProvider
    provider = VoiceProvider(settings)
    from coolify_voice import configured, save
    if configured(): return save(settings, value, provider.config)
    saved = load_settings(settings)
    changes = {**saved, 'CENTRAL_AI_CALL_SPEECH': 'piper' if value['provider'] == 'native' else 'fish'}
    if value['provider'] == 'fish':
        key = value.get('api_key') or provider.config.get('FISH_API_KEY', '')
        voice_id = value.get('voice_id') or provider.config.get('FISH_VOICE_ID', '')
        if not key:
            raise ValueError('Fish API key is not configured in backend')
        if not voice_id:
            raise ValueError('Fish Voice ID is not configured in backend')
        changes.update(FISH_API_KEY=key, FISH_VOICE_ID=voice_id,
                       FISH_VOICE_NAME='Jarvis', FISH_OS_CALL_ENGINE='stream', FISH_ASR_ENABLED='false')
    provider.config.update(changes)
    if not provider.status().get('realtime'):
        raise ValueError('This voice provider is not ready. Check its settings or choose Set up later.')
    atomic_write(settings_path(settings), json.dumps(changes))
    return {'voice_saved': True}
