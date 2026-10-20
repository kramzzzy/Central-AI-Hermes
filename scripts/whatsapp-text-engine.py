"""Native Hermes chat runners using the voice engine's existing WhatsApp device."""
import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3
import sys

sys.path.insert(0,'/opt/os-adapter/scripts')
sys.path.insert(0,'/opt/os-adapter')
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from hermes_profile import load_profile
from whatsapp_system_context import REPLY_STYLE, quiet_whatsapp_display, attach_assistant_skills, clean_reply_punctuation

_POISONED_PATTERNS = (
    'no working way to read the knowledge base',
    'aren\'t exposed on this whatsapp connection',
    'aren\'t enabled on this whatsapp connection',
    'can\'t give you that number from here',
    'can\'t check that from here',
    'tools that read the knowledge base',
)


def _clean_poisoned_whatsapp_history(home: Path):
    """Remove stale assistant refusal turns from SQLite/session files so Leo does not repeat old error claims."""
    try:
        for db_path in list(home.glob('*.db')) + list((home / 'sessions').glob('*.db') if (home / 'sessions').is_dir() else []):
            try:
                conn = sqlite3.connect(str(db_path))
                cur = conn.cursor()
                tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                for tbl in tables:
                    cols = [c[1] for c in cur.execute(f"PRAGMA table_info({tbl})").fetchall()]
                    text_col = next((c for c in ('content', 'text', 'message') if c in cols), None)
                    if not text_col:
                        continue
                    for pat in _POISONED_PATTERNS:
                        cur.execute(f"DELETE FROM {tbl} WHERE lower({text_col}) LIKE ?", (f"%{pat}%",))
                conn.commit()
                conn.close()
            except Exception:
                pass
        sessions_dir = home / 'sessions'
        if sessions_dir.is_dir():
            for sf in sessions_dir.rglob('*'):
                if not sf.is_file() or sf.suffix not in {'.json', '.jsonl'}:
                    continue
                try:
                    raw = sf.read_text(encoding='utf-8', errors='ignore')
                    if not any(pat in raw.lower() for pat in _POISONED_PATTERNS):
                        continue
                    if sf.suffix == '.jsonl':
                        lines = [
                            ln for ln in raw.splitlines()
                            if not any(pat in ln.lower() for pat in _POISONED_PATTERNS)
                        ]
                        sf.write_text('\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8')
                    elif sf.suffix == '.json':
                        data = json.loads(raw)
                        if isinstance(data, dict) and isinstance(data.get('messages'), list):
                            data['messages'] = [
                                m for m in data['messages']
                                if not any(pat in json.dumps(m).lower() for pat in _POISONED_PATTERNS)
                            ]
                            sf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                except Exception:
                    pass
    except Exception:
        pass


async def run_route(route):
    from whatsapp_routing import text_routes, routing
    all_routes = text_routes()
    if route not in all_routes:
        raise RuntimeError(f'Unknown WhatsApp text route: {route}')
    member = dict(all_routes[route])
    home, _ = load_profile()
    if home.name != member['profile']:
        raise RuntimeError(f"Text profile binding mismatch: expected {member['profile']}, got {home.name}")
    os.environ.setdefault('APP_OS_URL', os.environ.get('MICHAEL_OS_URL') or 'http://web:3000')
    os.environ.setdefault('CENTRAL_AI_APP_OS_PLUGIN', '1')
    _clean_poisoned_whatsapp_history(home)
    try:
        from hermes_os import configure_profile, install_process_token
        install_process_token()
        configure_profile(home, plugin=True, allow_enable=True)
        from hermes_cli.plugins import discover_plugins
        discover_plugins()
    except Exception as e:
        logging.error('configure_profile failed in text engine: %s', e)
    quiet_whatsapp_display(home)
    group=routing()['group'] if member['group'] else ''
    allow_all=member.get('allow_all', True)
    owner=member.get('number', '')
    os.environ.update(WHATSAPP_MODE='bot',
        WHATSAPP_ALLOW_ALL_USERS='true' if allow_all else 'false',
        GATEWAY_ALLOW_ALL_USERS='true' if allow_all else 'false',
        WHATSAPP_FORWARD_OWNER_MESSAGES='false',WHATSAPP_REPLY_PREFIX='',
        WHATSAPP_DM_POLICY='disabled' if group else ('open' if allow_all else 'allowlist'),
        WHATSAPP_ALLOWED_USERS='' if allow_all else owner,
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
        extra={'dm_policy':'disabled' if group else ('open' if allow_all else 'allowlist'),
            'allow_from':[] if (group or allow_all) else [owner],
            'group_policy':'allowlist' if group else 'disabled','group_allow_from':[group] if group else [],
            'require_mention':True if group else False,'mention_patterns':[r'(?i)\bleo\b'],
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
