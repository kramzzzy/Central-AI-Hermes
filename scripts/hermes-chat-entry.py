"""Launch the installed Hermes chat protocol inside the explicitly selected profile."""
import contextlib
import os
import sys
from hermes_profile import load_profile

with contextlib.redirect_stdout(sys.stderr):
    home, _ = load_profile()
    os.environ.setdefault('APP_OS_URL', os.environ.get('MICHAEL_OS_URL') or 'http://web:3000')
    os.environ.setdefault('CENTRAL_AI_APP_OS_PLUGIN', '1')
    try:
        from pathlib import Path
        import importlib.util
        spec = importlib.util.spec_from_file_location('wa_clean', str(Path(__file__).resolve().parent / 'whatsapp-text-engine.py'))
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod._clean_poisoned_whatsapp_history(home)
    except Exception:
        pass
    if os.environ.get('HERMES_ROUTINE_ONLY') != 'true':
        from hermes_os import configure_profile, install_process_token
        try:
            configure_profile(home)
            install_process_token()
            if os.environ.get('CENTRAL_AI_APP_OS_PLUGIN') == '1':
                # Verify real native discovery before removing the legacy MCP tool.
                from hermes_cli.plugins import discover_plugins
                from tools.registry import registry
                discover_plugins()
                if registry.get_entry('mcp__michael_os__inspect_workspace') is None:
                    raise RuntimeError('App OS native plugin failed to register')
        except Exception as exc:
            sys.stderr.write(f"[hermes-chat-entry] configure_profile warning: {exc}\n")
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
elif os.environ.get('HERMES_ROUTINE_ONLY') != 'true':
    # Installed gateway folds desktop_ui in even when disabled in config.yaml, which injects
    # Hermes Desktop ::preview{file=...} scratch-HTML rules that break App OS web chat images.
    try:
        import tui_gateway.server as native
        _orig_load_toolsets = getattr(native, '_load_enabled_toolsets', None)
        if callable(_orig_load_toolsets):
            def _app_os_toolsets(platform=None, _orig=_orig_load_toolsets):
                loaded = _orig(platform)
                return [t for t in loaded if t != 'desktop_ui'] if isinstance(loaded, list) else loaded
            native._load_enabled_toolsets = _app_os_toolsets
    except Exception:
        pass
from tui_gateway.entry import main
main()
