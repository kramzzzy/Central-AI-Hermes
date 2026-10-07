import os

AUSTRALIAN_VOICE_ID = '31d3dd937d2944a1b06da3db533d27f9'
PREFERRED_LOCALE = 'en-AU'
FALLBACK_LOCALE = 'en'
ENGLISH_KEYTERMS = ['Central OS', 'Central AI',
    'Brisbane', 'Melbourne', 'Sydney', 'Canberra', 'Adelaide', 'Perth', 'Hobart',
    'Darwin', 'Cairns', 'Toowoomba', 'Gold Coast', 'Sunshine Coast', 'Queensland',
    'New South Wales', 'Denpasar', 'Bali', 'DPS', 'BNE', 'OOL', 'Australian dollars']


def phone_speech(config):
    """Retain the configured Jarvis voice independently of dialect preferences."""
    value = dict(config)
    value['FISH_ENGLISH_FALLBACK_VOICE_ID'] = config.get('FISH_ENGLISH_FALLBACK_VOICE_ID') or config.get('FISH_VOICE_ID') or os.environ.get('FISH_VOICE_ID', '612b878b113047d9a770c069c8b4fdfe')
    value['FISH_ENGLISH_FALLBACK_VOICE_NAME'] = config.get('FISH_ENGLISH_FALLBACK_VOICE_NAME') or config.get('FISH_VOICE_NAME', 'English')
    return value
