"""Opt-in native Hermes platform plugin; it never creates a second WhatsApp device.

The companion media engine still owns WhatsApp authentication and live calls.
Registering this plugin advertises chat transport, not verified call readiness.
"""
import os
import sys
from pathlib import Path

ROUTES={
    'mark':{'profile':'leo-whatsapp-text','number':'639267200480','group':False},
    'michael':{'profile':'team-whatsapp-michael-business','number':'61423947456','group':False},
    'team':{'profile':'team-whatsapp-social','group':True},
}


def check_dependencies():
    import importlib.util
    return importlib.util.find_spec('aiohttp') is not None


def route_binding(config):
    extra=config.extra
    route=extra.get('route')
    if route not in ROUTES:raise ValueError('Select an admitted Leo WhatsApp route')
    member=dict(ROUTES[route])
    if route=='michael':
        member['profile']=os.environ.get('CENTRAL_AI_TEXT_PROFILE',os.environ.get('LEO_MICHAEL_TEXT_PROFILE',member['profile']))
        if member['profile'] not in {'team-whatsapp-michael-business','team-whatsapp-michael-text'}:raise ValueError('Invalid Michael profile binding')
    home=Path(os.environ.get('HERMES_HOME','')).resolve(strict=True)
    if home.name!=member['profile'] or home.parent.name!='profiles' or not (home/'config.yaml').is_file():raise ValueError('WhatsApp route does not match this native profile')
    group=os.environ.get('WHATSAPP_GROUP',os.environ.get('LEO_WHATSAPP_GROUP','')) if member['group'] else ''
    if member['group'] and not group.endswith('@g.us'):raise ValueError('Configure the admitted team group in Hermes')
    return route,member,home,group


def validate_config(config):
    route_binding(config)
    return True


def create_adapter(config):
    adapter_root=Path('/opt/os-adapter/scripts')
    if str(adapter_root) not in sys.path:sys.path.insert(0,str(adapter_root))
    from .adapter import build_engine_adapter
    from gateway.config import Platform
    route,member,home,group=route_binding(config)
    # Fixed allowlists override caller-supplied adapter options. The paired
    # engine independently checks the route and reply lease on each send.
    config.extra.update(dm_policy='disabled' if member['group'] else 'allowlist',
        allow_from=[] if member['group'] else [member['number']],
        group_policy='allowlist' if member['group'] else 'disabled',
        group_allow_from=[group] if group else [],require_mention=True,
        mention_patterns=[r'(?i)\bleo\b'],reply_prefix='',send_read_receipts=False,text_batch_delay=0)
    adapter=build_engine_adapter(route,member,home,group)(config)
    adapter.platform=Platform('central_whatsapp')
    return adapter


def register(ctx):
    ctx.register_platform(name='central_whatsapp',label='Leo WhatsApp',
        adapter_factory=create_adapter,check_fn=check_dependencies,
        validate_config=validate_config,
        install_hint='Enable the retained paired media engine and select this profile’s admitted route.',
        emoji='💬',allow_update_command=False,
        platform_hint='Replies use the existing WhatsApp connection. Live calls are handled by the companion media engine.')
