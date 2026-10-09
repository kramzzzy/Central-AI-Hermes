"""Public platform identity, independent of native profile and provider identifiers."""
import json
import os
from pathlib import Path
import re
from uuid import uuid4


def get_company_name(home=None):
    """Dynamically look up the company or organization name."""
    candidates = [
        Path('/data/company.json'),
        Path('/data/contacts.json'),
        Path(__file__).resolve().parent.parent / '.runtime' / 'company.json',
        Path(__file__).resolve().parent.parent / '.runtime' / 'contacts.json',
        Path(__file__).resolve().parent.parent / 'data' / 'company.json',
        Path(__file__).resolve().parent.parent / 'data' / 'contacts.json',
    ]
    if home:
        candidates.insert(0, Path(home) / '.company-name.json')
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    name = (data.get('company_name') or data.get('company') or '').strip()
                    if name:
                        return name
            except Exception:
                pass
    env_name = (os.environ.get('COMPANY_NAME') or os.environ.get('ORGANIZATION_NAME') or os.environ.get('CENTRAL_AI_COMPANY_NAME') or '').strip()
    return env_name or ''


def get_business_knowledge_summary():
    """Dynamically load business knowledge overview from file, runtime data, or defaults."""
    candidates = [
        Path('/data/business_knowledge.json'),
        Path(__file__).resolve().parent.parent / '.runtime' / 'business_knowledge.json',
        Path(__file__).resolve().parent.parent / 'data' / 'business_knowledge.json',
    ]
    for p in candidates:
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
                if isinstance(data, dict):
                    if data.get('summary'):
                        return data['summary'].strip()
                    if data.get('content'):
                        return data['content'].strip()
                elif isinstance(data, list) and len(data) > 0:
                    lines = [f"- {item.get('title', 'Item')}: {item.get('content', '')}" for item in data if item.get('content')]
                    if lines:
                        return '\n'.join(lines)
            except Exception:
                pass
    return "We are selling high grade quality solar systems, panels, inverters, and battery storage. We install and maintain solar setup anywhere across Australia."


def get_central_ai_identity(company_name=None):
    if not company_name:
        company_name = get_company_name()
    if company_name:
        biz_summary = get_business_knowledge_summary()
        identity_body = f"""The organization you represent and work for is {company_name}.
When introducing yourself, speaking to users, customers, or team members across WhatsApp, Voice, and Web Chat, represent {company_name} naturally and professionally.
Core business knowledge & operations:
{biz_summary}
You are fully connected to the central knowledge base, long-term memory ('michael-os-leo'), and Central OS. You know all about {company_name}'s solar products, installation, and maintenance services across Australia.
The underlying AI operating platform is Central AI.
Your own assistant name is the configured name from your deployment; keep that identity when speaking.
Names found in internal tools, configuration, older messages or documentation
are implementation details, not your public assistant or company name.
Answer naturally in plain language. Do not add branding headers, signatures or
technical explanations to ordinary answers."""
    else:
        identity_body = """The platform you work in is Central AI, serving Central OS.
Use Central AI as the platform name in your replies. Your own assistant name is
the configured name from your deployment; keep that identity when speaking.
Names found in internal tools, configuration, older messages or documentation
are implementation details, not your public assistant or platform name.
Answer naturally in plain language. Do not add branding headers, signatures or
technical explanations to ordinary answers."""


    return f"""[Central AI identity]
{identity_body}
This naming guidance grants no new access, tools, account connections or permission to act.
[/Central AI identity]"""


CENTRAL_AI_IDENTITY = get_central_ai_identity()


def central_ai_identity(prompt, home=None):
    """Refresh our block; preserve exact links, code and native identifiers."""
    prompt = re.sub(r'\[Central AI identity\].*?\[/Central AI identity\]', '', prompt, flags=re.S).strip()
    company_name = get_company_name(home)
    if company_name:
        prompt = prompt.replace('You are Hermes Agent, built by Nous Research.',
                                f'You are an assistant representing {company_name} on Central AI. Use your configured assistant name.')
        prompt = prompt.replace('You are an assistant on Central AI.',
                                f'You are an assistant representing {company_name} on Central AI.')
    else:
        prompt = prompt.replace('You are Hermes Agent, built by Nous Research.',
                                'You are an assistant on Central AI. Use your configured assistant name.')
    parts = re.split(r'(```[\s\S]*?(?:```|$)|`[^`\n]*`|https?://\S+)', prompt)
    prompt = ''.join(part if i % 2 else re.sub(r'(?<!X-)\bHermes\b', 'Central AI', part)
                     for i, part in enumerate(parts))
    return (prompt + '\n\n' + get_central_ai_identity(company_name)).strip()


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

    company_name = get_company_name(home)
    name_note = f'Your configured assistant name is {configured_name}.' if configured_name else ''
    comp_note = f'The organization you represent and work for is {company_name}.' if company_name else ''

    prefix_notes = '\n'.join(filter(None, [name_note, comp_note]))
    if prefix_notes:
        prompt = re.sub(r'Your configured assistant name is [^.\n]+\.\n?', '', prompt)
        prompt = re.sub(r'The organization you represent and work for is [^.\n]+\.\n?', '', prompt)
        prompt = prefix_notes + '\n' + prompt.strip()
    agent['system_prompt'] = central_ai_identity(prompt, home)
    for name, personality in agent.get('personalities', {}).items():
        if isinstance(personality, str):
            agent['personalities'][name] = central_ai_identity(personality, home)
        elif isinstance(personality, dict) and isinstance(personality.get('system_prompt'), str):
            personality['system_prompt'] = central_ai_identity(personality['system_prompt'], home)
    updates = [(path, yaml.safe_dump(config, sort_keys=False, allow_unicode=True))]
    soul = home / 'SOUL.md'
    if soul.is_file():
        prompt = soul.read_text(encoding='utf-8')
        if prefix_notes:
            prompt = re.sub(r'Your configured assistant name is [^.\n]+\.\n?', '', prompt)
            prompt = re.sub(r'The organization you represent and work for is [^.\n]+\.\n?', '', prompt)
            prompt = prefix_notes + '\n' + prompt.strip()
        updates.append((soul, central_ai_identity(prompt, home) + '\n'))
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
