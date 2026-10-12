"""Keep ordinary phone clarifications in speech, scoped to this subprocess.

Business approvals remain enforced by native tool dispatch. The UI-only clarify
tool would wait on a form the phone caller cannot see; the voice prompt instead
asks one question aloud and consumes the caller's next spoken turn normally.
"""
from functools import wraps

_public_phone_only = False

def configure_business_phone_runtime(home):
    global _public_phone_only
    # Import the gateway AFTER bootstrap stdout redirection ends. Importing it
    # inside that context binds its protocol writer to the diagnostic stream.
    import yaml
    if not yaml.safe_load((home / 'config.yaml').read_text()).get('phone_business_context'):
        return
    _public_phone_only = True
    import tui_gateway.server as native
    if not callable(getattr(native, '_load_enabled_toolsets', None)):
        raise RuntimeError('Business phone tool selection is unavailable')
    # Public forecast needs no workspace grant. Do not expose private App OS
    # operations merely to enable weather for an isolated phone caller.
    native._load_enabled_toolsets = lambda platform=None: ['memory', 'todo', 'laya', 'michael_os']


def configure_phone_tools():
    import model_tools
    original = model_tools.get_tool_definitions
    if getattr(original, '_phone_conversation', False):
        return

    @wraps(original)
    def definitions(*args, **kwargs):
        tools = []
        for tool in original(*args, **kwargs):
            name = (tool.get('function') or tool).get('name', '')
            if name == 'clarify':
                continue
            if (_public_phone_only and name.startswith('mcp__michael_os__')
                    and name != 'mcp__michael_os__get_live_weather'):
                continue
            tools.append(tool)
        return tools

    definitions._phone_conversation = True
    model_tools.get_tool_definitions = definitions
