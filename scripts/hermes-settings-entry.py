"""Use the installed Hermes schema, config persistence and model validation."""
import contextlib
import json
import os
import sys
from hermes_settings import FIELDS, SettingsError, at, put, revision, validate_changes


def main(body, home):
    from hermes_cli.config import load_config, require_readable_config_before_write, save_config
    from hermes_cli.web_server_config import CONFIG_SCHEMA
    from hermes_cli import tools_config as tc
    config = load_config()
    if body.get('action') == 'plugins':
        from hermes_cli import plugins_cmd as pc
        enabled, disabled, active = pc._get_enabled_set(), pc._get_disabled_set(), pc._category_active_names()
        return {'profile': os.environ['ORBIT_HERMES_PROFILE'], 'protocol': 1,
                'plugins': [{'name': name, 'key': key, 'version': str(version or ''), 'description': str(desc or ''),
                            'status': pc._plugin_status(name, enabled, disabled, key=key, source=source, dir_path=path, active=active)}
                           for name, version, desc, source, path, key in sorted(pc._discover_all_plugins())]}
    available = {key for key in FIELDS if key in CONFIG_SCHEMA}
    # Supported native config.set reasoning stores this optional key (no seeded default).
    available.add('agent.reasoning_effort')
    action = body.get('action')
    if action != 'read':
        if body.get('revision') != revision(home):
            raise SettingsError('Hermes settings changed elsewhere. Refresh before saving.', 409)
        raw = require_readable_config_before_write()
        if action == 'save':
            validate_changes(body.get('changes'), available)
            for key, value in body['changes'].items():
                put(raw, key, value)
        elif action == 'inherit_model':
            delegation = raw.setdefault('delegation', {})
            for key in ('model', 'provider', 'base_url', 'api_key', 'api_mode', 'key_env'):
                delegation.pop(key, None)
        elif action == 'model':
            from hermes_cli.web_server_config import (_prepare_main_assignment,
                _apply_main_model_assignment, _resolve_assignment_credentials, _provider_entry)
            provider, model = body.get('provider', ''), body.get('model', '')
            import re
            if not isinstance(provider, str) or not isinstance(model, str) or not re.fullmatch(r'[\w][\w:-]*', provider) or not re.fullmatch(r'[\w][\w./:@+-]*', model):
                raise SettingsError('Choose a configured provider and model.')
            slot = body.get('slot', 'main')
            if slot not in {'main', 'delegation'}:
                raise SettingsError('Choose a supported native model slot.')
            validation_config = dict(config)
            old = raw.get('model', {})
            if slot == 'delegation':
                old = dict(raw.get('delegation', {}))
                old['default'] = old.pop('model', '') or at(config, 'model.default', '')
                validation_config['model'] = {**old, 'provider': old.get('provider') or at(config, 'model.provider', '')}
            # Official Hermes picker validation (credentials/catalog/aliases), no client endpoints.
            prepared = _prepare_main_assignment(validation_config, provider, model, '', '')
            _, selection = prepared
            selected = _apply_main_model_assignment(old, selection)
            _resolve_assignment_credentials(selected, selection.target_provider, _provider_entry(config, selection.target_provider))
            if slot == 'delegation':
                selected['model'] = selected.pop('default')
                raw['delegation'] = selected
            else:
                raw['model'] = selected
        elif action == 'tool':
            choices = {key for key, _, _ in tc.CONFIGURABLE_TOOLSETS}
            if body.get('name') not in choices or type(body.get('enabled')) is not bool:
                raise SettingsError('Choose an available native toolset.')
            # Native _apply_toolset_change persists immediately. Defer it until
            # after the final revision check; otherwise our own write looks stale.
        else:
            raise SettingsError('Unsupported settings operation.')
        # Recheck after model validation/probes, which can take time.
        if body.get('revision') != revision(home):
            raise SettingsError('Hermes settings changed elsewhere. Refresh before saving.', 409)
        if action == 'tool':
            tc._apply_toolset_change(raw, 'cli', [body['name']], 'enable' if body['enabled'] else 'disable')
        elif action == 'model' and slot == 'main':
            from hermes_cli.web_server_config import _apply_main_assignment_sync
            _apply_main_assignment_sync(raw, provider, model, '', '', prepared=prepared)
        else:
            save_config(raw)
        config = load_config()
        if action == 'save' and any(at(config, key) != value for key, value in body['changes'].items()):
            raise SettingsError('Hermes did not apply all settings. Refresh to inspect the saved state.', 409)
        if action == 'model' and at(config, 'delegation.model' if slot == 'delegation' else 'model.default') != selection.new_model:
            raise SettingsError('Hermes did not apply the selected model. Refresh and retry.', 409)
        if action == 'inherit_model' and (at(config, 'delegation.model') or at(config, 'delegation.provider')):
            raise SettingsError('Hermes did not restore model inheritance. Refresh and retry.', 409)
    fields = []
    for key, (group, label, kind, constraint) in FIELDS.items():
        if key not in available:
            continue
        value = at(config, key, '')
        if kind == 'select' and value is None:
            value = ''
        if not isinstance(value, (str, int, bool)):
            continue
        fields.append({'key': key, 'group': group, 'label': label, 'type': kind,
            'value': value, 'description': CONFIG_SCHEMA.get(key, {}).get('description', ''),
            **({'options': constraint} if kind == 'select' else {}),
            **({'min': constraint[0], 'max': constraint[1]} if kind == 'number' else {})})
    enabled = tc._get_platform_tools(config, 'cli', include_default_mcp_servers=False)
    tools = [{'name': key, 'label': label, 'description': desc, 'enabled': key in enabled}
             for key, label, desc in tc.CONFIGURABLE_TOOLSETS]
    if action == 'tool' and (body['name'] in enabled) != body['enabled']:
        raise SettingsError('Hermes did not apply the tool selection. Refresh and retry.', 409)
    try:
        from hermes_cli.version_info import get_version_info
        native_version = get_version_info().display_version
    except ModuleNotFoundError:
        from hermes_cli import __version__
        native_version = __version__
    from hermes_memory import read_bank
    memory_status = read_bank(home) if at(config, 'memory.provider', '') == 'hindsight' else None
    return {'profile': os.environ['ORBIT_HERMES_PROFILE'], 'revision': revision(home),
            'version': native_version, 'fields': fields, 'tools': tools,
            'model': {'provider': at(config, 'model.provider', 'auto'),
                      'model': at(config, 'model.default', config.get('model') if isinstance(config.get('model'), str) else '')},
            'delegation_model': {'provider': at(config, 'delegation.provider', ''), 'model': at(config, 'delegation.model', '')},
            'memory_provider': at(config, 'memory.provider', '') or 'Built-in',
            'memory_status': memory_status, 'protocol': 1}


try:
    with contextlib.redirect_stdout(sys.stderr):
        from hermes_profile import load_profile
        home, _ = load_profile()
        result = main(json.load(sys.stdin), home)
except SettingsError as exc:
    result = {'error': str(exc), 'status': exc.status}
except Exception:
    # Native providers may embed credentials in exception messages. Never relay them.
    result = {'error': 'Hermes could not apply or read this setting. Check the native configuration.', 'status': 503}
print(json.dumps(result))
