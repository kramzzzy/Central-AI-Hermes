"""Explicit native profile binding; never fall back to the user's default profile."""
import os
import re
from pathlib import Path
from central_ai_integrations import managed_environment, sync_profile


def profile_environment(settings, inherited=None):
    name = settings.get('HERMES_PROFILE', '')
    root = settings.get('HERMES_PROFILE_ROOT', '')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name) or name == 'default' or not root:
        raise RuntimeError('Configure an existing named Hermes profile and native profile root')
    root = Path(root).resolve(strict=True)
    home = (root / 'profiles' / name).resolve(strict=True)
    if not home.is_relative_to(root / 'profiles') or not (home / 'config.yaml').is_file():
        raise RuntimeError('Configured Hermes profile is unavailable')
    env = dict(os.environ if inherited is None else inherited)
    managed = managed_environment(env)
    # Ordinary shell credentials never bleed into profiles. Only the explicitly
    # managed backend's provider allowlist may be shared between private actors.
    for key in list(env):
        if key.endswith(('_API_KEY', '_AUTH_TOKEN', '_ACCESS_TOKEN')) or key in {
            'PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'ORBIT_HERMES_MODEL',
            'OPENAI_BASE_URL', 'OPENAI_API_BASE', 'HERMES_INFERENCE_PROVIDER',
        }:
            env.pop(key, None)
    env.update(managed)
    env.update(HERMES_HOME=str(home), HERMES_REPO=settings['HERMES_REPO'],
               ORBIT_HERMES_PROFILE=name, PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    # A service-owned routine context cannot inherit a shell's background flag.
    env.pop('HERMES_ROUTINE_ONLY',None)
    if settings.get('HERMES_ROUTINE_ONLY') == 'true':
        env['HERMES_ROUTINE_ONLY']='true'
    return env


def load_profile():
    """Call before importing Hermes or dotenv. Returns home and normalized model config."""
    managed = managed_environment()
    name = os.environ.get('ORBIT_HERMES_PROFILE', '')
    home = Path(os.environ.get('HERMES_HOME', '')).resolve()
    if not name or name == 'default' or home.name != name or home.parent.name != 'profiles' or not (home / 'config.yaml').is_file():
        raise RuntimeError('An explicit existing native Hermes profile is required')
    sync_profile(home)
    import sys
    sys.path.insert(0, os.environ['HERMES_REPO'])
    import hermes_bootstrap  # Official PM dependency activation for this installed version.
    from central_ai_identity import configure_identity
    configure_identity(home)
    shared_auth = os.environ.get('MICHAEL_TEAM_AUTH_HOME') or os.environ.get('MICHAEL_TEXT_AUTH_HOME')
    if shared_auth:
        # Pinned native auth supports source-aware, locked global fallback refresh.
        # Route only its credential lookup to the existing store: no copied refresh
        # token, profile discovery override, private context, or global config change.
        source = Path(shared_auth).resolve(strict=True)
        owner_text = (name == 'leo-whatsapp-text' and bool(os.environ.get('MICHAEL_TEXT_AUTH_HOME'))
                      and source.name == 'leo' and not os.environ.get('MICHAEL_TEAM_AUTH_HOME'))
        if (not name.startswith('team-') and not owner_text) or source.parent != home.parent or source == home:
            raise RuntimeError('Invalid backend credential binding')
        from hermes_cli import auth
        if not hasattr(auth, '_load_provider_state_with_source') or not hasattr(auth, '_provider_state_transaction'):
            raise RuntimeError('Native credential-sharing compatibility check failed')
        auth._global_auth_file_path = lambda: source / 'auth.json'
    # Fresh installations can sign in once through the browser wizard. Keep a
    # single native store across app/phone processes; native source-aware locks
    # write refreshes back to it instead of cloning single-use refresh tokens.
    provider_auth = Path('/run/provider-auth/auth.json')
    if provider_auth.is_file():
        from hermes_cli import auth
        auth._global_auth_file_path = lambda: provider_auth
    if (shared_auth and not owner_text) or os.environ.get('MICHAEL_RESTRICT_TEAM_TOOLS')=='true':
        # Native @ references can read host files without a file tool. Team turns
        # may expand uploaded files only, never profile configuration, other homes,
        # git commands, folders, URLs or plugin reference providers.
        from agent import context_references
        original_expand = context_references.preprocess_context_references
        def uploaded_references(message, **kwargs):
            refs = context_references.parse_context_references(message)
            if any(ref.kind != 'file' for ref in refs):
                return context_references.ContextReferenceResult(message=message, original_message=message,
                    blocked=True, warnings=['Team chat supports uploaded file references only.'])
            kwargs['cwd'] = home / 'attachments'
            kwargs['allowed_root'] = home / 'attachments'
            return original_expand(message, **kwargs)
        context_references.preprocess_context_references = uploaded_references
    from dotenv import load_dotenv
    load_dotenv(home / '.env', override=True)
    os.environ.update(managed)
    from hermes_cli.runtime_provider import _get_model_config
    config = _get_model_config()
    if not config.get('default'):
        config['default'] = os.environ.get('HERMES_CHAT_MODEL', 'openai/gpt-4.1-mini')
        config['provider'] = os.environ.get('HERMES_CHAT_PROVIDER', 'openrouter')
    return home, config
