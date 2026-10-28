"""Provider settings owned by a managed Central AI deployment.

These settings share provider billing and service authentication, never user
identity, session IDs, tool grants, uploaded files or Hindsight bank IDs.
Unmanaged development profiles keep their existing native configuration.
"""
import os
import json
import re
import tempfile
from pathlib import Path

MANAGED_KEYS = (
    'OPENROUTER_API_KEY', 'FISH_API_KEY', 'LAYA_API_KEY',
    'HINDSIGHT_API_KEY', 'LAYA_URL', 'HINDSIGHT_API_URL',
    'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY',
    'HERMES_API_KEY', 'APP_OS_URL', 'MICHAEL_OS_URL',
)
OPTIONAL_KEYS = {
    'FISH_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY',
    'HERMES_API_KEY', 'APP_OS_URL', 'MICHAEL_OS_URL',
}
PLACEHOLDER_FISH_KEYS = {
    '',
    'sk-fish-your_fish_audio_key_here',
    'sk-fish-your-key-here',
    'your-fish-audio-api-key-here',
}


def valid_fish_key(value):
    cleaned = str(value or '').strip().strip('"').strip("'")
    return '' if cleaned in PLACEHOLDER_FISH_KEYS else cleaned


def discover_fish_credentials(root='/opt/data'):
    """Locate working Fish Audio credentials across shared volumes and profiles."""
    base = Path(root)
    result = {}
    env_key = valid_fish_key(os.environ.get('FISH_API_KEY'))
    if env_key:
        result['FISH_API_KEY'] = env_key
    search_roots = [base]
    for extra_root in (Path('/data'), Path('/opt/os-adapter/.runtime')):
        if extra_root not in search_roots:
            search_roots.append(extra_root)
    for search_dir in search_roots:
        for json_name in ('.runtime-fish-voice.json', '.app-voice.json'):
            path = search_dir / json_name
            try:
                if path.is_file() and not path.is_symlink():
                    data = json.loads(path.read_text(encoding='utf-8'))
                    if isinstance(data, dict):
                        k = valid_fish_key(data.get('FISH_API_KEY'))
                        if k and not result.get('FISH_API_KEY'):
                            result['FISH_API_KEY'] = k
                        for vk in ('FISH_VOICE_ID', 'FISH_VOICE_NAME', 'CENTRAL_AI_CALL_SPEECH'):
                            if data.get(vk) and vk not in result:
                                result[vk] = str(data[vk]).strip()
            except Exception:
                pass
    if not result.get('FISH_API_KEY'):
        candidates = []
        for idx, env_file in enumerate((
            base / '.env',
            Path('/opt/os-adapter/.runtime/hermes.env'),
            Path('/data/.env'),
            Path('/data/voice/.env'),
        )):
            candidates.append((-1, f'{idx:02d}', env_file))
        profiles_dir = base / 'profiles'
        try:
            if profiles_dir.is_dir():
                for env_file in sorted(profiles_dir.glob('*/.env')):
                    name = env_file.parent.name
                    priority = 0 if name.startswith('team-owner-') else (1 if name == 'leo' else 2)
                    candidates.append((priority, str(env_file), env_file))
                candidates.sort(key=lambda item: (item[0], item[1]))
        except Exception:
            pass
        for _, _, env_file in candidates:
            try:
                if not env_file.is_file():
                    continue
                for line in env_file.read_text(encoding='utf-8-sig', errors='ignore').splitlines():
                    if line.strip().startswith('#') or '=' not in line:
                        continue
                    key, val = line.split('=', 1)
                    if key.strip() == 'FISH_API_KEY':
                        k = valid_fish_key(val)
                        if k:
                            result['FISH_API_KEY'] = k
                            break
                if result.get('FISH_API_KEY'):
                    break
            except Exception:
                pass
    if result.get('FISH_VOICE_ID') in {
        '31d3dd937d2944a1b06da3db533d27f9',
        '711cf3ed00ab441a8f54a45058047b7a',
        'bf342d9e9e5b44e0ade1ccfd1abb4815',
    }:
        result['FISH_VOICE_ID'] = os.environ.get('FISH_VOICE_ID') or '612b878b113047d9a770c069c8b4fdfe'
        result['FISH_VOICE_NAME'] = os.environ.get('FISH_VOICE_NAME') or 'Jarvis'
    if result.get('CENTRAL_AI_CALL_SPEECH') == 'piper':
        result['CENTRAL_AI_CALL_SPEECH'] = 'fish'
    return result


def managed_environment(environment=None):
    source = os.environ if environment is None else environment
    if source.get('CENTRAL_AI_MANAGED_INTEGRATIONS') != '1':
        return {}
    values = {key: source.get(key, '').strip() for key in MANAGED_KEYS}
    values['FISH_API_KEY'] = valid_fish_key(values.get('FISH_API_KEY'))
    if any(not value for key, value in values.items() if key not in OPTIONAL_KEYS):
        raise RuntimeError('Central AI integration settings are incomplete')
    if any('\x00' in value or '\n' in value or '\r' in value for value in values.values()):
        raise RuntimeError('Invalid Central AI integration setting')
    # Empty new provider slots preserve credentials owned by native profiles.
    return {key:value for key,value in values.items()
            if value or key not in {'FISH_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY','GEMINI_API_KEY','HERMES_API_KEY','APP_OS_URL','MICHAEL_OS_URL'}}


def integration_config(config, environment=None):
    """Keep persona/voice preferences; deployment settings own provider keys."""
    return {**config, **managed_environment(environment)}


def memory_config(config, environment=None):
    """Resolve service auth without changing the actor's fixed memory bank."""
    values = managed_environment(environment)
    if not values:
        return config
    return {**config, 'api_url': values['HINDSIGHT_API_URL'], 'api_key': values['HINDSIGHT_API_KEY']}


def sync_profile(home, environment=None):
    """Compose managed keys into native secret scopes; keep bank/persona fields.

    Native multiplexed turns deliberately read the profile's secret scope rather
    than arbitrary process credentials. These files are generated compatibility
    bindings, with Coolify's shared settings as their authoritative source.
    """
    values = managed_environment(environment)
    if not values:
        return
    home = Path(home).resolve(strict=True)
    path = home / '.env'
    target = path.resolve()
    allowed = home.parent if home.parent.name == 'profiles' else home
    if not target.is_relative_to(allowed):
        raise RuntimeError('Invalid Central AI profile credential binding')
    text = target.read_text(encoding='utf-8-sig') if target.exists() else ''
    lines = [line for line in text.splitlines() if not re.match(
        r'^\s*(?:export\s+)?(?:' + '|'.join(values) + r')\s*=', line)
        and line != '# Central AI managed integrations; edit Coolify shared production settings.']
    lines += ['# Central AI managed integrations; edit Coolify shared production settings.']
    lines += [key + "='" + value.replace('\\', '\\\\').replace("'", "\\'") + "'" for key, value in values.items()]
    content = '\n'.join(lines) + '\n'
    if content != text:
        atomic_write(target, content)
    # Plugin-owned bank/prefetch/retention configuration remains private. Native
    # get_secret resolves the managed key/URL from this actor's composed scope.
    memory = home / 'hindsight/config.json'
    if memory.is_file() and not memory.is_symlink():
        config = json.loads(memory.read_text(encoding='utf-8'))
        cleaned = {key: value for key, value in config.items() if key not in ('api_key', 'apiKey', 'api_url')}
        if cleaned != config:
            atomic_write(memory, json.dumps(cleaned, indent=2) + '\n')


def atomic_write(path, content):
    previous = path.stat() if path.exists() else None
    descriptor, name = tempfile.mkstemp(prefix='.central-ai-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(content)
        os.chmod(name, 0o600)
        if previous and getattr(os, 'geteuid', lambda: -1)() == 0:
            os.chown(name, previous.st_uid, previous.st_gid)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


if __name__ == '__main__':
    import sys
    home = Path(sys.argv[1])
    sync_profile(home)
    for profile in (home / 'profiles').glob('*/config.yaml'):
        sync_profile(profile.parent)
