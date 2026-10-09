"""Fixed backend entrypoints; provider configuration never enters the OS image."""
import json
import os
from pathlib import Path
import sys
from central_ai_integrations import MANAGED_KEYS, OPTIONAL_KEYS, sync_profile, atomic_write


def load_config(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if set(value) != {'version', 'integrations', 'phone_profiles'} or value['version'] != 1:
        raise RuntimeError('Unsupported private backend configuration')
    settings = value['integrations']
    for key in OPTIONAL_KEYS:
        settings.setdefault(key, '')
    if set(settings) != set(MANAGED_KEYS) or any(not isinstance(v, str) or (not v and k not in OPTIONAL_KEYS) or any(c in v for c in '\n\r\0') for k, v in settings.items()):
        raise RuntimeError('Incomplete private backend integrations')
    return value


def install_whatsapp(home):
    """Install the platform package; retained wrapper remains its only consumer."""
    import shutil
    import uuid
    import yaml
    from app_os_plugin import profile_lock
    with profile_lock(home) as home:
        path = home / 'config.yaml'
        config = yaml.safe_load(path.read_text(encoding='utf-8'))
        plugins = config.setdefault('plugins', {})
        name = 'central-ai-whatsapp'
        if name in plugins.get('disabled', []) or plugins.get('entries', {}).get(name, {}).get('enabled') is False:
            raise RuntimeError('WhatsApp plugin is disabled; enable it explicitly')
        target = home / 'plugins' / name
        if target.is_symlink() or (home / 'plugins').is_symlink():
            raise RuntimeError('Plugin storage must be local')
        target.mkdir(parents=True, mode=0o700, exist_ok=True)
        source = Path(__file__).resolve().parent.parent / 'hermes_plugins' / 'central_ai_whatsapp'
        for filename in ['__init__.py', 'adapter.py', 'plugin.yaml']:
            destination = target / filename
            if destination.is_symlink(): raise RuntimeError('Plugin files must be local')
            content = (source / filename).read_text(encoding='utf-8')
            if not destination.exists() or destination.read_text(encoding='utf-8') != content:
                atomic_write(destination, content)
        enabled = plugins.setdefault('enabled', [])
        if name not in enabled: enabled.append(name)
        content = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
        if path.read_text(encoding='utf-8') != content:
            backup = home / 'central-ai-install-backups'
            backup.mkdir(mode=0o700, exist_ok=True)
            backup_path = backup / (uuid.uuid4().hex + '.yaml')
            shutil.copyfile(path, backup_path); os.chmod(backup_path, 0o600)
            atomic_write(path, content)


def install_memory(home):
    """Ship the official pinned plugin in the image; no old profile export."""
    import shutil
    import yaml
    from app_os_plugin import profile_lock
    with profile_lock(home) as home:
        source = Path('/opt/hindsight-plugin')
        if not (source / 'plugin.yaml').is_file():
            raise RuntimeError('The bundled Hindsight plugin is missing')
        target = home / 'plugins' / 'hindsight'
        if target.is_symlink() or (home / 'plugins').is_symlink():
            raise RuntimeError('Plugin storage must be local')
        shutil.copytree(source, target, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('.git', '__pycache__', 'docs', 'tests'))
        path = home / 'config.yaml'
        if path.is_file():
            try:
                cfg = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
                plugins = cfg.setdefault('plugins', {})
                enabled = plugins.setdefault('enabled', [])
                if 'hindsight' not in enabled:
                    enabled.append('hindsight')
                cfg['memory'] = {'provider': 'hindsight'}
                atomic_write(path, yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True))
            except Exception:
                pass
        hindsight_dir = home / 'hindsight'
        hindsight_dir.mkdir(parents=True, exist_ok=True)
        hindsight_cfg = hindsight_dir / 'config.json'
        if not hindsight_cfg.is_file():
            h_data = {
                "mode": "local_external",
                "bank_id": "michael-os-leo",
                "recall_budget": "mid",
                "timeout": 120
            }
            atomic_write(hindsight_cfg, json.dumps(h_data, indent=2) + '\n')



def get_standalone_config():
    openrouter_key = os.environ.get('OPENROUTER_API_KEY', '') or 'sk-or-v1-standalone-default-key-32chars'
    hermes_key = os.environ.get('HERMES_API_KEY', 'hermes_standalone_secret_token_32chars')
    fish_key = os.environ.get('FISH_API_KEY', '').strip()
    call_speech = os.environ.get('CENTRAL_AI_CALL_SPEECH') or os.environ.get('HERMES_CALL_SPEECH') or ('fish' if fish_key else 'piper')
    voice_name = os.environ.get('FISH_VOICE_NAME', 'Jarvis')
    voice_id = os.environ.get('FISH_VOICE_ID', '612b878b113047d9a770c069c8b4fdfe')
    return {
        'version': 1,
        'integrations': {
            'OPENROUTER_API_KEY': openrouter_key,
            'OPENAI_API_KEY': os.environ.get('OPENAI_API_KEY', ''),
            'ANTHROPIC_API_KEY': os.environ.get('ANTHROPIC_API_KEY', ''),
            'GEMINI_API_KEY': os.environ.get('GEMINI_API_KEY', ''),
            'FISH_API_KEY': fish_key,
            'FISH_VOICE_ID': voice_id,
            'FISH_VOICE_NAME': voice_name,
            'HERMES_API_KEY': hermes_key,
            'HERMES_MEMORY_TOKEN': openrouter_key or 'hindsight-default-secret-key-32chars!!',
            'HINDSIGHT_API_KEY': os.environ.get('HINDSIGHT_API_KEY', 'hindsight-default-secret-key-32chars!!'),
            'HINDSIGHT_UI_KEY': 'hindsight-default-secret-key-32chars!!',
            'HINDSIGHT_API_URL': os.environ.get('HINDSIGHT_API_URL', 'http://hindsight:8888'),
            'LAYA_API_KEY': os.environ.get('LAYA_API_KEY', 'laya-local-key'),
            'LAYA_URL': os.environ.get('LAYA_URL', 'http://laya:8000'),
            'CENTRAL_AI_CALL_SPEECH': call_speech,
            'HERMES_CALL_SPEECH': call_speech,
            'GOOGLE_CLIENT_ID': os.environ.get('GOOGLE_CLIENT_ID', '').strip(),
            'GOOGLE_CLIENT_SECRET': os.environ.get('GOOGLE_CLIENT_SECRET', '').strip(),
            'GOOGLE_REDIRECT_URI': os.environ.get('GOOGLE_REDIRECT_URI', '').strip(),
        },
        'phone_profiles': ['leo', 'sarah', 'leo-whatsapp-text', 'team-whatsapp-social'],
    }


def bootstrap(mode, config):
    os.environ.update(config['integrations'])
    os.environ['CENTRAL_AI_MANAGED_INTEGRATIONS'] = '1'
    if mode == 'hermes':
        from hermes_premade import preserved_profiles
        from app_os_plugin import profile_lock, install_files
        import yaml
        os.environ.setdefault('HERMES_PROFILE', 'leo')
        os.environ.setdefault('HERMES_PROFILE_ROOT', '/opt/data')
        os.environ.setdefault('HERMES_REPO', '/opt/hermes')
        os.environ.setdefault('HERMES_PYTHON', sys.executable)
        if 'HERMES_API_KEY' not in os.environ or len(os.environ['HERMES_API_KEY']) < 32:
            os.environ['HERMES_API_KEY'] = 'hermes_standalone_secret_token_32chars'
        runtime_env = Path('/opt/os-adapter/.runtime/hermes.env')
        runtime_env.parent.mkdir(parents=True, exist_ok=True)
        env_content = '\n'.join(f'{k}={v}' for k, v in os.environ.items() if v and '\n' not in v)
        runtime_env.write_text(env_content, encoding='utf-8')
        os.environ['HERMES_BRIDGE_CONFIG'] = str(runtime_env)
        settings = dict(line.split('=', 1) for line in runtime_env.read_text().splitlines() if '=' in line and not line.startswith('#'))
        try:
            profiles = list(preserved_profiles(settings))
        except Exception as e:
            print(f'Preserved profiles failed: {e}; using fallback leo and sarah', file=sys.stderr)
            profiles = [{'native_profile': 'leo'}, {'native_profile': 'sarah'}]
        root_data = Path('/opt/data')
        root_data.mkdir(parents=True, exist_ok=True)
        root_config = root_data / 'config.yaml'
        if not root_config.is_file():
            root_config.write_text('plugins:\n  enabled: []\n', encoding='utf-8')
        os.environ.setdefault('HERMES_APPROVAL_MODE', 'off')
        os.environ.setdefault('HERMES_PERMISSION_MODE', 'off')
        os.environ.setdefault('HERMES_SESSION_PLATFORM', 'api_server')
        os.environ.setdefault('CALLER_URL', 'http://caller:8080')
        os.environ.setdefault('WHATSAPP_URL', 'http://caller:8080')
        all_homes = [Path('/opt/data/profiles') / item['native_profile'] for item in profiles]
        for home in all_homes:
            home.mkdir(parents=True, exist_ok=True)
            path = home / 'config.yaml'
            if not path.is_file():
                path.write_text('plugins:\n  enabled: []\n', encoding='utf-8')
            install_memory(home)
            install_whatsapp(home)
            with profile_lock(home):
                original = path.read_text(encoding='utf-8')
                native = yaml.safe_load(original) or {}
                # Enforce bypass of approval prompts so agent tools execute immediately
                native['approval_mode'] = 'off'
                native['permission_mode'] = 'off'
                native['unattended'] = True
                install_files(home, native)
                content = yaml.safe_dump(native, sort_keys=False, allow_unicode=True)
                if content != original:
                    from uuid import uuid4
                    backup = home / 'central-ai-install-backups'
                    backup.mkdir(mode=0o700, exist_ok=True)
                    atomic_write(backup / (uuid4().hex + '.yaml'), original)
                    atomic_write(path, content)
            sync_profile(home)
            try:
                from hermes_os import configure_profile
                configure_profile(home)
            except Exception as e:
                print(f'Configure profile failed for {home.name}: {e}', file=sys.stderr)
            import subprocess
            if subprocess.run([sys.executable, '-c', 'from tools.skills_sync import sync_skills; sync_skills(quiet=True)'],
                              env=dict(os.environ, HERMES_HOME=str(home)), cwd='/opt/hermes').returncode:
                print(f'Skill sync failed for {home.name}; continuing without new skills', file=sys.stderr)
        return '/opt/os-adapter/scripts/local-bridge.py'
    if mode == 'voice':
        import re
        if not os.environ.get('FISH_API_KEY') and 'CENTRAL_AI_CALL_SPEECH' not in os.environ:
            os.environ['CENTRAL_AI_CALL_SPEECH'] = 'piper'
        try:
            from whatsapp_routing import routing
            for c in routing().get('contacts', []):
                for p in (c.get('profile'), c.get('text_profile')):
                    if p and p not in config['phone_profiles']:
                        config['phone_profiles'].append(p)
        except Exception:
            pass
        for profile in config['phone_profiles']:
            if not isinstance(profile, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', profile) or profile == 'default':
                continue
            home = Path('/opt/data/profiles') / profile
            home.mkdir(parents=True, exist_ok=True)
            path = home / 'config.yaml'
            if not path.is_file():
                path.write_text('plugins:\n  enabled: []\n', encoding='utf-8')
            install_memory(home); install_whatsapp(home); sync_profile(home)
            try:
                from central_ai_identity import configure_identity
                configure_identity(home)
            except Exception:
                pass
        return '/opt/setup/whatsapp-call-voice.py'
    raise RuntimeError('Choose a supported backend service')


if __name__ == '__main__':
    config_path = os.environ.get('CENTRAL_AI_CONFIG_FILE')
    config = load_config(config_path) if config_path and Path(config_path).is_file() else get_standalone_config()
    script = bootstrap(sys.argv[1], config)
    os.execv(sys.executable, [sys.executable, script])
