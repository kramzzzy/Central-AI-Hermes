"""Native Hermes chat runners using the voice engine's existing WhatsApp device."""
import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0,'/opt/os-adapter/scripts')
sys.path.insert(0,'/opt/os-adapter')
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from hermes_profile import load_profile
from whatsapp_system_context import REPLY_STYLE, quiet_whatsapp_display, attach_assistant_skills, clean_reply_punctuation

async def run_route(route):
    from whatsapp_routing import text_routes, routing
    all_routes = text_routes()
    if route not in all_routes:
        raise RuntimeError(f'Unknown WhatsApp text route: {route}')
    member = dict(all_routes[route])
    home, _ = load_profile()
    if home.name != member['profile']:
        raise RuntimeError(f"Text profile binding mismatch: expected {member['profile']}, got {home.name}")
    quiet_whatsapp_display(home)
    group=routing()['group'] if member['group'] else ''
    owner=member.get('number','')
    os.environ.update(WHATSAPP_MODE='bot',WHATSAPP_ALLOW_ALL_USERS='false',GATEWAY_ALLOW_ALL_USERS='false',
        WHATSAPP_FORWARD_OWNER_MESSAGES='false',WHATSAPP_REPLY_PREFIX='',
        WHATSAPP_DM_POLICY='disabled' if group else 'allowlist',WHATSAPP_ALLOWED_USERS=owner,
        WHATSAPP_GROUP_POLICY='allowlist' if group else 'disabled',WHATSAPP_GROUP_ALLOWED_USERS=group,
        HERMES_KANBAN_DISPATCH_IN_GATEWAY='false')
    from gateway.config import Platform,PlatformConfig,load_gateway_config
    from gateway.run import GatewayRunner

    from hermes_plugins.central_ai_whatsapp.adapter import build_engine_adapter
    EngineAdapter=build_engine_adapter(route,member,home,group)

    class EngineRunner(GatewayRunner):
        def _instantiate_adapter(self,platform,config):
            return EngineAdapter(config) if platform==Platform.WHATSAPP else None

    config=load_gateway_config()
    config.multiplex_profiles=False
    config.group_sessions_per_user=False
    config.platforms={Platform.WHATSAPP:PlatformConfig(enabled=True,gateway_restart_notification=False,
        extra={'dm_policy':'disabled' if group else 'allowlist','allow_from':[] if group else [owner],
            'group_policy':'allowlist' if group else 'disabled','group_allow_from':[group] if group else [],
            'require_mention':True,'mention_patterns':[r'(?i)\bleo\b'],
            'group_sessions_per_user':False,'send_read_receipts':False,'reply_prefix':'','text_batch_delay':0})}
    runner=EngineRunner(config)
    marker=Path('/data/text/ready-'+route)
    marker.parent.mkdir(parents=True,exist_ok=True)
    marker.unlink(missing_ok=True)
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(sig,lambda:asyncio.create_task(runner.stop()))
    try:
        if not await runner.start():raise RuntimeError('Native text runner did not start')
        marker.write_text('ready\n');marker.chmod(0o600)
        print(json.dumps({'leo_text_route_ready':route}),flush=True)
        await runner.wait_for_shutdown()
    finally:
        marker.unlink(missing_ok=True)
        await runner.stop()

if __name__=='__main__':
    logging.basicConfig(level=logging.ERROR,format='%(levelname)s %(name)s: %(message)s')
    asyncio.run(run_route(sys.argv[1]))
