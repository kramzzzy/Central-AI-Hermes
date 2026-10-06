"""Per-request tool selection; Hermes still enforces readiness and native approvals."""

def tool_options(mode):
    if mode == 'draft-only':
        return {'enabled_toolsets': [], 'disabled_toolsets': []}
    if mode != 'tools':
        raise ValueError('Unsupported execution mode')
    from hermes_cli.config import load_config
    from hermes_cli.plugins import discover_plugins
    from toolsets import get_all_toolsets
    discover_plugins()
    config = load_config()
    # Native MCP registrations must exist before taking the all-toolset snapshot.
    # Otherwise saved-agent tasks omit integrations that Bot Chat already discovers.
    if config.get('mcp_servers'):
        from tools.mcp_tool_discovery import register_mcp_servers
        register_mcp_servers(config['mcp_servers'])
    disabled = config.get('agent', {}).get('disabled_toolsets', [])
    # Explicit operator exclusions and Hermes role/credential gates remain authoritative.
    return {'enabled_toolsets': list(get_all_toolsets()),
            'disabled_toolsets': disabled if isinstance(disabled, list) else []}

def available_tools():
    from model_tools import get_tool_definitions
    return sorted({t['function']['name'] for t in get_tool_definitions(
        **tool_options('tools'), quiet_mode=True, skip_tool_search_assembly=True)})
