"""Update display identity only; native profile IDs, tools and history stay bound."""
import json
import re
from pathlib import Path
from uuid import uuid4
import yaml
from app_os_plugin import profile_lock
from central_ai_integrations import atomic_write

PREFIX = 'Configured assistant name: '


def identity_text(text, previous, name):
    if text.startswith(PREFIX):
        text = text.split('\n', 1)[1] if '\n' in text else ''
    # Replace only the identity opening, never names in memory or other prose.
    text = re.sub(r'^You are ' + re.escape(previous) + r'(?=[,.])',
                  lambda _: 'You are ' + name, text, count=1)
    return PREFIX + json.dumps(name, ensure_ascii=False) + '. Use this name when identifying yourself.\n' + text


def rename_profile(home, name, template_name):
    if not isinstance(name, str) or not name.strip() or len(name) > 80 or re.search(r'[\x00-\x1f\x7f]', name):
        raise ValueError('Enter an assistant name of 1 to 80 characters')
    name = name.strip()
    with profile_lock(Path(home)) as home:
        config_path, soul_path, name_path = home/'config.yaml', home/'SOUL.md', home/'.assistant-name.json'
        for path in (config_path, soul_path, name_path):
            if path.is_symlink(): raise RuntimeError('Assistant identity files must be local')
        previous = json.loads(name_path.read_text())['name'] if name_path.exists() else template_name
        config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        agent = config.setdefault('agent', {})
        prompt = agent.get('system_prompt') or ''
        agent['system_prompt'] = identity_text(prompt, previous, name)
        soul = soul_path.read_text(encoding='utf-8') if soul_path.exists() else prompt
        changes = [(config_path, yaml.safe_dump(config, sort_keys=False, allow_unicode=True)),
                   (soul_path, identity_text(soul, previous, name)),
                   (name_path, json.dumps({'name': name}, ensure_ascii=False))]
        for path, content in changes:
            original = path.read_text(encoding='utf-8') if path.exists() else None
            if original == content: continue
            if original is not None:
                backup = home/'central-ai-install-backups'
                if backup.is_symlink(): raise RuntimeError('Identity backups must be local')
                backup.mkdir(mode=0o700, exist_ok=True)
                atomic_write(backup/(uuid4().hex+'-'+path.name), original)
            atomic_write(path, content)
