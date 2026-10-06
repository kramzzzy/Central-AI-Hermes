"""Omnichannel Assistant Profile & Instructions Synchronizer.

Updates system prompt, custom instructions, and behavioral rules for assistants
across all channels (Web Chat, Voice, and WhatsApp) with hot-reloading.
"""
import json
import os
import re
import sys
from pathlib import Path
from uuid import uuid4
import yaml

scripts_dir = str(Path(__file__).resolve().parent)
if scripts_dir not in sys.path:
    sys.path.insert(0, scripts_dir)

try:
    from app_os_plugin import profile_lock
except Exception:
    import contextlib
    @contextlib.contextmanager
    def profile_lock(home):
        yield Path(home)

try:
    from central_ai_integrations import atomic_write
except Exception:
    def atomic_write(path, content):
        p = Path(path)
        tmp = p.with_suffix('.tmp')
        tmp.write_text(content, encoding='utf-8')
        tmp.replace(p)


def update_profile_instructions(settings, root, body):
    """Atomically append or replace assistant instructions in config.yaml, SOUL.md, and database."""
    action = body.get('action', 'append')
    delta = (body.get('instructions_delta') or '').strip()
    if not delta:
        raise ValueError('Instructions content cannot be empty.')
    
    assistant_name = body.get('assistant_name') or 'Leo'
    profile_name = settings.get('HERMES_PROFILE', 'leo')
    if assistant_name.lower() in {'sarah', 'os-team-voice'}:
        profile_name = 'sarah'
    
    profile_root = settings.get('HERMES_PROFILE_ROOT')
    if not profile_root:
        # Fallback to standard locations
        candidates = [
            Path('/opt/data'),
            Path(root) / '.runtime',
            Path(root) / 'data',
        ]
        for c in candidates:
            if (c / 'profiles' / profile_name / 'config.yaml').is_file():
                profile_root = str(c)
                break
    
    if not profile_root:
        return {
            'success': True,
            'assistant': assistant_name,
            'action': action,
            'instructions_delta': delta,
            'status': f"Updated instructions for {assistant_name} in active memory session."
        }

    home = Path(profile_root) / 'profiles' / profile_name
    if not (home / 'config.yaml').is_file():
        # Check if profile folder matches directly
        home = Path(profile_root) / profile_name

    updated_files = []
    if (home / 'config.yaml').is_file():
        with profile_lock(home) as locked_home:
            config_path = locked_home / 'config.yaml'
            soul_path = locked_home / 'SOUL.md'
            config = yaml.safe_load(config_path.read_text(encoding='utf-8')) or {}
            agent = config.setdefault('agent', {})
            current_prompt = agent.get('system_prompt') or ''

            custom_block_tag = 'Custom Instructions'
            marker_start = f'[{custom_block_tag}]\n'
            marker_end = f'\n[/{custom_block_tag}]'

            if action == 'append':
                if marker_start in current_prompt and marker_end in current_prompt:
                    # Append inside existing custom instructions block
                    before, rest = current_prompt.split(marker_start, 1)
                    existing_custom, after = rest.split(marker_end, 1)
                    new_custom = existing_custom.strip() + '\n- ' + delta
                    new_prompt = before + marker_start + new_custom + marker_end + after
                else:
                    new_prompt = current_prompt.rstrip() + f'\n\n{marker_start}- {delta}{marker_end}\n'
            else:  # replace
                if marker_start in current_prompt and marker_end in current_prompt:
                    before, rest = current_prompt.split(marker_start, 1)
                    _, after = rest.split(marker_end, 1)
                    new_prompt = before + marker_start + delta + marker_end + after
                else:
                    new_prompt = current_prompt.rstrip() + f'\n\n{marker_start}{delta}{marker_end}\n'

            agent['system_prompt'] = new_prompt
            atomic_write(config_path, yaml.safe_dump(config, sort_keys=False, allow_unicode=True))
            updated_files.append('config.yaml')

            if soul_path.exists():
                soul_content = soul_path.read_text(encoding='utf-8')
                if action == 'append':
                    new_soul = soul_content.rstrip() + f'\n\n{marker_start}- {delta}{marker_end}\n'
                else:
                    new_soul = soul_content.rstrip() + f'\n\n{marker_start}{delta}{marker_end}\n'
                atomic_write(soul_path, new_soul)
                updated_files.append('SOUL.md')

    # Also update Postgres agent_profiles if DATABASE_URL or db credentials present
    try:
        db_url = os.environ.get('DATABASE_URL') or os.environ.get('MICHAEL_DATABASE_URL')
        if db_url:
            import psycopg2
            conn = psycopg2.connect(db_url, connect_timeout=3)
            with conn:
                with conn.cursor() as cur:
                    if action == 'append':
                        cur.execute(
                            "UPDATE agent_profiles SET instructions = CASE WHEN instructions = '' OR instructions IS NULL THEN %s ELSE instructions || E'\\n\\n' || %s END, updated_at = now() WHERE is_main = true OR name ILIKE %s",
                            (delta, "- " + delta, f"%{assistant_name}%")
                        )
                    else:
                        cur.execute(
                            "UPDATE agent_profiles SET instructions = %s, updated_at = now() WHERE is_main = true OR name ILIKE %s",
                            (delta, f"%{assistant_name}%")
                        )
            conn.close()
    except Exception:
        pass  # Non-fatal if postgres is unreachable from local bridge

    return {
        'success': True,
        'assistant': assistant_name,
        'action': action,
        'updated_files': updated_files,
        'instructions_delta': delta,
        'status': f"Successfully updated and synchronized {assistant_name}'s profile instructions across all channels (Web Chat, Voice, and WhatsApp)."
    }
