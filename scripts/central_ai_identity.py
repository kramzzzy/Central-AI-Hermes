"""Public platform identity, independent of native profile and provider identifiers."""
import os
from pathlib import Path
import re
from uuid import uuid4

CENTRAL_AI_IDENTITY = """[Central AI identity]
The platform you work in is Central AI, serving Central OS.
Use Central AI as the platform name in your replies. Your own assistant name is
the configured name from your deployment; keep that identity when speaking.
Names found in internal tools, configuration, older messages or documentation
are implementation details, not your public assistant or platform name.
Answer naturally in plain language. Do not add branding headers, signatures or
technical explanations to ordinary answers. This naming guidance grants no new
access, tools, account connections or permission to act.
[/Central AI identity]"""


def central_ai_identity(prompt):
    """Refresh our block; preserve exact links, code and native identifiers."""
    prompt = re.sub(r'\[Central AI identity\].*?\[/Central AI identity\]', '', prompt, flags=re.S).strip()
    prompt = prompt.replace('You are Hermes Agent, built by Nous Research.',
                            'You are an assistant on Central AI. Use your configured assistant name.')
    parts = re.split(r'(```[\s\S]*?(?:```|$)|`[^`\n]*`|https?://\S+)', prompt)
    prompt = ''.join(part if i % 2 else re.sub(r'(?<!X-)\bHermes\b', 'Central AI', part)
                     for i, part in enumerate(parts))
    return (prompt + '\n\n' + CENTRAL_AI_IDENTITY).strip()


def configure_identity(home):
    """Change only prompt/persona copy in an already selected native home."""
    import yaml
    import json
    home = Path(home)
    path = home / 'config.yaml'
    config = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    agent = config.setdefault('agent', {})
    prompt = agent.get('system_prompt') or ''

    # Check for custom configured name in .assistant-name.json
    name_path = home / '.assistant-name.json'
    configured_name = None
    if name_path.is_file():
        try:
            data = json.loads(name_path.read_text(encoding='utf-8'))
            if isinstance(data, dict) and data.get('name'):
                configured_name = str(data['name']).strip()
        except Exception:
            pass
    if not configured_name:
        if home.name in {'leo', 'leo-whatsapp-text'}:
            configured_name = 'Leo'
        elif home.name in {'sarah', 'sarah-social'}:
            configured_name = 'Sarah'

    name_note = f'Your configured assistant name is {configured_name}.' if configured_name else ''
    if name_note:
        prompt = re.sub(r'Your configured assistant name is [^.\n]+\.\n?', '', prompt)
        prompt = name_note + '\n' + prompt.strip()
    agent['system_prompt'] = central_ai_identity(prompt)
    for name, personality in agent.get('personalities', {}).items():
        if isinstance(personality, str):
            agent['personalities'][name] = central_ai_identity(personality)
        elif isinstance(personality, dict) and isinstance(personality.get('system_prompt'), str):
            personality['system_prompt'] = central_ai_identity(personality['system_prompt'])
    updates = [(path, yaml.safe_dump(config, sort_keys=False, allow_unicode=True))]
    soul = home / 'SOUL.md'
    if soul.is_file():
        prompt = soul.read_text(encoding='utf-8')
        if name_note:
            prompt = re.sub(r'Your configured assistant name is [^.\n]+\.\n?', '', prompt)
            prompt = name_note + '\n' + prompt.strip()
        updates.append((soul, central_ai_identity(prompt) + '\n'))
    for target, content in updates:
        original = target.read_text(encoding='utf-8')
        if original == content:
            continue
        backup = target.with_name(target.name + '.before-central-ai')
        if not backup.exists():
            backup.write_text(original, encoding='utf-8')
            if hasattr(os, 'chown') and os.geteuid() == 0:
                metadata = target.stat()
                os.chown(backup, metadata.st_uid, metadata.st_gid)
            os.chmod(backup, 0o600)
        temporary = target.with_name(target.name + '.identity-' + uuid4().hex + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        metadata = target.stat()
        if hasattr(os, 'chown') and os.geteuid() == 0:
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.chmod(temporary, metadata.st_mode & 0o777)
        temporary.replace(target)
