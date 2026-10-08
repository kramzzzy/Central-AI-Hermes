"""Allowlisted native settings. Provider secrets and arbitrary config never leave Hermes."""
import hashlib
import json
import re
import subprocess
from hermes_profile import profile_environment
from bridge_process import RunProcesses


class SettingsError(RuntimeError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# Deliberately exclude command paths, credentials, endpoints, approval bypasses,
# arbitrary JSON and plugin code. Bounds are OS input guards, not native limits.
FIELDS = {
    'timezone': ('Connection', 'Hermes time zone', 'string', None),
    'agent.reasoning_effort': ('Models', 'Default thinking', 'select', ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']),
    'delegation.reasoning_effort': ('Delegation', 'Subagent thinking', 'select', ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']),
    'delegation.max_concurrent_children': ('Delegation', 'Concurrent subagents', 'number', (1, 100)),
    'delegation.max_iterations': ('Delegation', 'Iterations per subagent', 'number', (1, 10000)),
    'delegation.max_spawn_depth': ('Delegation', 'Delegation depth', 'number', (1, 10)),
    'delegation.child_timeout_seconds': ('Delegation', 'Idle child timeout (seconds; 0 disables)', 'number', (0, 86400)),
    'delegation.inherit_mcp_toolsets': ('Delegation', 'Inherit parent MCP tools', 'boolean', None),
    'delegation.orchestrator_enabled': ('Delegation', 'Enable orchestrator role', 'boolean', None),
    'delegation.independent_completions': ('Delegation', 'Deliver child results individually', 'boolean', None),
    'memory.memory_enabled': ('Memory', 'Agent memory', 'boolean', None),
    'memory.user_profile_enabled': ('Memory', 'User preferences memory', 'boolean', None),
    'memory.write_approval': ('Memory', 'Review memory writes', 'boolean', None),
    'memory.memory_char_limit': ('Memory', 'Agent memory character limit', 'number', (1, 1000000)),
    'memory.user_char_limit': ('Memory', 'User memory character limit', 'number', (1, 1000000)),
    'memory.nudge_interval': ('Memory', 'Memory review interval (turns; 0 disables)', 'number', (0, 10000)),
}


def at(config, key, default=None):
    value = config
    for part in key.split('.'):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def put(config, key, value):
    parts = key.split('.')
    for part in parts[:-1]:
        config = config.setdefault(part, {})
        if not isinstance(config, dict):
            raise SettingsError('Repair the native configuration in Hermes before saving.', 409)
    config[parts[-1]] = value


def revision(home):
    return hashlib.sha256((home / 'config.yaml').read_bytes()).hexdigest()


def validate_changes(changes, available):
    if not isinstance(changes, dict) or not 1 <= len(changes) <= len(FIELDS):
        raise SettingsError('Select settings to change.')
    for key, value in changes.items():
        if key not in FIELDS or key not in available:
            raise SettingsError('This setting is not supported by the connected Hermes.')
        _, _, kind, constraint = FIELDS[key]
        if kind == 'boolean' and type(value) is not bool:
            raise SettingsError('Choose an on or off value.')
        if kind == 'number' and (type(value) is not int or not constraint[0] <= value <= constraint[1]):
            raise SettingsError('Enter a whole number within the displayed range.')
        if kind == 'select' and value not in constraint:
            raise SettingsError('Choose an available setting.')
        if kind == 'string' and (not isinstance(value, str) or len(value) > 80):
            raise SettingsError('Enter a valid time zone.')
        if key == 'timezone' and value:
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError):
                raise SettingsError('Enter an IANA time zone, such as Australia/Brisbane.') from None


def run_settings(settings, root, body):
    if body.get('action') not in {'read', 'save', 'model', 'inherit_model', 'tool', 'plugins'}:
        raise SettingsError('Unsupported settings operation.')
    try:
        env = profile_environment(settings)
        env['HERMES_DISABLE_LAZY_INSTALLS'] = '1'
        python_bin = settings.get('HERMES_PYTHON') or __import__('os').environ.get('HERMES_PYTHON') or __import__('sys').executable
        process = subprocess.Popen(
            [python_bin, str(root / 'scripts/hermes-settings-entry.py')],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
            env=env, cwd=root, **({'creationflags': subprocess.CREATE_NO_WINDOW} if __import__('os').name == 'nt' else {'start_new_session': True}))
        try:
            stdout, _ = process.communicate(json.dumps(body), timeout=60)
        except BaseException:
            RunProcesses._kill(process)
            process.communicate(timeout=10)
            raise
        if process.returncode:
            raise RuntimeError('Native adapter failed')
        payload = json.loads(stdout)
        if 'error' in payload:
            raise SettingsError(payload['error'], payload.get('status', 400))
        if payload.get('profile') != settings['HERMES_PROFILE']:
            raise SettingsError('The connected profile changed. Refresh before continuing.', 409)
        return payload
    except SettingsError:
        raise
    except Exception:
        raise SettingsError('Hermes settings are unavailable. Check the native installation and retry.', 503) from None


def idle(chat):
    if chat.voice_call or chat.requests or chat.voice_restore or chat.voice_model_restore:
        raise SettingsError('Finish the call or pending review before changing Hermes settings.', 409)
    if chat.sid:
        info = chat.rpc('session.activate', chat.scoped(omit_messages=True)).get('info', {})
        approvals = chat.rpc('approval.pending', chat.scoped()).get('approvals', [])
        if info.get('running') or approvals:
            raise SettingsError('Finish the active reply or approval before changing Hermes settings.', 409)


def runtime_settings(chat, body):
    """Explicit RPC allowlist. No raw dispatch, shell, endpoint or credential passthrough."""
    params = {'profile': chat.settings['HERMES_PROFILE']}
    action = body.get('action')
    if action == 'models':
        live_models = []
        model_details = []
        vendors = []
        try:
            from provider_models import get_catalog_models
            live_catalog = get_catalog_models(chat.settings, force_refresh=body.get('refresh') is True)
            live_models = live_catalog.get('models', [])
            model_details = live_catalog.get('model_details', [])
            vendors = live_catalog.get('vendors', [])
        except Exception as e:
            print(f"[hermes_settings] Live catalog fetch failed: {e}")

        raw = chat.rpc('model.options', {**params, 'include_unconfigured': False}, timeout=60)
        providers = []
        for row in raw.get('providers', []):
            if row.get('authenticated') is False or row.get('picker_hints', {}).get('authenticated') is False or row.get('available') is False:
                continue
            slug = row.get('slug')
            models = [m for m in row.get('models', []) if m not in row.get('unavailable_models', [])]
            if slug == 'openrouter' and live_models:
                models = live_models
            providers.append({'slug': slug, 'name': row.get('name'), 'models': models})

        if not any(p['slug'] == 'openrouter' for p in providers) and live_models:
            providers.insert(0, {'slug': 'openrouter', 'name': 'OpenRouter (Live Catalog)', 'models': live_models})

        # Models are identifiers/catalog labels only; never return endpoint or credential metadata.
        for row in providers:
            row['models'] = [str(v) if isinstance(v, str) else str(v.get('id', v.get('name', '')))
                             for v in row.get('models') or []]
        return {'providers': providers, 'model_details': model_details, 'vendors': vendors}
    if action == 'integrations':
        status = chat.rpc('mcp.servers.status', params, timeout=30)
        return {'servers': [{k: row.get(k) for k in ('name', 'transport', 'connected', 'disabled', 'status', 'tools')}
                            for row in status.get('servers', [])]}
    if action == 'schedules':
        raw = chat.rpc('cron.manage', {**params, 'action': 'list', 'include_disabled': True}, timeout=30)
        return {'jobs': [{'id': row.get('job_id', row.get('id')), **{k: row.get(k) for k in ('name', 'schedule', 'schedule_display', 'next_run_at', 'enabled', 'paused')}}
                         for row in raw.get('jobs', [])]}
    if action == 'test':
        idle(chat)
        servers = chat.rpc('mcp.servers.status', params).get('servers', [])
        if body.get('name') not in {row.get('name') for row in servers}:
            raise SettingsError('Choose a configured native connection.')
        result = chat.rpc('mcp.servers.test', {**params, 'name': body['name']}, timeout=60)
        return {'ok': bool(result.get('ok')), 'oauth_needed': bool(result.get('oauth_needed')),
                'tool_count': len(result.get('tools') or []),
                'message': 'Connection verified.' if result.get('ok') else 'Connection failed. Complete setup in Hermes and test again.'}
    if action == 'schedule':
        idle(chat)
        if body.get('operation') not in {'pause', 'resume'}:
            raise SettingsError('Choose pause or resume.')
        rows = chat.rpc('cron.manage', {**params, 'action': 'list', 'include_disabled': True}).get('jobs', [])
        if body.get('id') not in {row.get('job_id', row.get('id')) for row in rows}:
            raise SettingsError('Choose an available native schedule.')
        result = chat.rpc('cron.manage', {**params, 'action': body['operation'], 'name': body['id']})
        if not result.get('success'):
            raise SettingsError('Hermes could not update this schedule.')
        return runtime_settings(chat, {**body, 'action': 'schedules'})
    raise SettingsError('Unsupported settings operation.')
