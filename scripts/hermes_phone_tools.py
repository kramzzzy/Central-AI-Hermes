"""Keep ordinary phone clarifications in speech, scoped to this subprocess.

Business approvals remain enforced by native tool dispatch. The UI-only clarify
tool would wait on a form the phone caller cannot see; the voice prompt instead
asks one question aloud and consumes the caller's next spoken turn normally.
"""
from functools import wraps


def configure_business_phone_runtime(home):
    # Import the gateway AFTER bootstrap stdout redirection ends. Importing it
    # inside that context binds its protocol writer to the diagnostic stream.
    import yaml
    if not yaml.safe_load((home / 'config.yaml').read_text()).get('phone_business_context'):
        return
    import tui_gateway.server as native
    if not callable(getattr(native, '_load_enabled_toolsets', None)):
        raise RuntimeError('Business phone tool selection is unavailable')
    native._load_enabled_toolsets = lambda platform=None: ['memory', 'todo']


def configure_phone_tools():
    import model_tools
    original = model_tools.get_tool_definitions
    if getattr(original, '_phone_conversation', False):
        return

    @wraps(original)
    def definitions(*args, **kwargs):
        return [tool for tool in original(*args, **kwargs)
                if (tool.get('function') or tool).get('name') != 'clarify']

    definitions._phone_conversation = True
    model_tools.get_tool_definitions = definitions
