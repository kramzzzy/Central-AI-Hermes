"""Launch the installed Hermes chat protocol inside the explicitly selected profile."""
import contextlib
import os
import sys
from hermes_profile import load_profile

with contextlib.redirect_stdout(sys.stderr):
    home, _ = load_profile()
    if os.environ.get('HERMES_ROUTINE_ONLY')!='true' and os.environ.get('MICHAEL_OS_URL') and os.environ.get('MICHAEL_OS_TOOL_TOKEN_FILE'):
        from hermes_os import configure_profile, install_process_token
        configure_profile(home)
        install_process_token()
        if os.environ.get('CENTRAL_AI_APP_OS_PLUGIN') == '1':
            # Verify real native discovery before removing the legacy MCP tool.
            from hermes_cli.plugins import discover_plugins
            from tools.registry import registry
            discover_plugins()
            if registry.get_entry('mcp__michael_os__inspect_workspace') is None:
                raise RuntimeError('App OS native plugin failed to register')
    # This process serves our Fish call adapter. Fish handles speech and pacing;
    # Replace the generic delegation/final-only note with direct conversation.
    # Scope the guidance to voice turns in this process, never the profile.
    from tools import voice_live
    from hermes_voice import voice_call_note
    voice_live.VOICE_LIVE_TURN_NOTE = voice_call_note(os.environ.get('HERMES_VOICE_LANGUAGE'))
    if os.environ.get('HERMES_PHONE_CONVERSATION') == 'true':
        from hermes_phone_tools import configure_phone_tools
        configure_phone_tools()
if os.environ.get('HERMES_PHONE_CONVERSATION') == 'true':
    from hermes_phone_tools import configure_business_phone_runtime
    configure_business_phone_runtime(home)
from hermes_specialists import restrict_native_specialist_runtime
restrict_native_specialist_runtime(home,routine_only=os.environ.get('HERMES_ROUTINE_ONLY')=='true')
if os.environ.get('MICHAEL_RESTRICT_TEAM_TOOLS')=='true':
    import tui_gateway.server as native
    if not callable(getattr(native,'_load_enabled_toolsets',None)):
        raise RuntimeError('Native team tool-selection compatibility check failed')
    native._load_enabled_toolsets=lambda platform=None:['memory','todo','michael_os']
from tui_gateway.entry import main
main()
