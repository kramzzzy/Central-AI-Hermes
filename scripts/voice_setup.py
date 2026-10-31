"""Private, persistent App OS call settings; never returns provider credentials."""
import json
import os
import re
from pathlib import Path
from central_ai_integrations import atomic_write


def settings_path(settings):
    path = Path(settings['HERMES_PROFILE_ROOT']) / '.app-voice.json'
    if path.is_symlink():
        raise RuntimeError('Voice settings must be a private regular file')
    return path


def load_settings(settings):
    return {'CENTRAL_AI_CALL_SPEECH': 'openrouter'}


def validate(value):
    if not isinstance(value, dict) or set(value) != {'provider'} or value.get('provider') != 'openrouter':
        raise ValueError('Choose OpenRouter speech; manage its existing API key in the Hermes stack')


def configure(settings, value):
    validate(value)
    from hermes_voice import VoiceProvider
    provider = VoiceProvider(settings)
    if not provider.status().get('realtime'):
        raise ValueError('Configure OPENROUTER_API_KEY in the Hermes stack before enabling voice')
    atomic_write(settings_path(settings), json.dumps({'CENTRAL_AI_CALL_SPEECH': 'openrouter'}))
    return {'voice_saved': True}
