"""Owner-channel context and bounded health probes; no OS session impersonation."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import urllib.request
from central_ai_identity import CENTRAL_AI_IDENTITY

REPLY_STYLE = """[WhatsApp reply style]
Provide only the direct, clean conversational response to the user's message.
Never mention tools, tool names, tool execution, function calls, or internal processes.
Never show tool execution status or say 'using tool'.
Never include introductory boilerplate, command lists, or /help suggestions.
Do not ask to build a profile or configure channels.
Use short natural sentences with commas and periods. Do not use em dashes or en
dashes as sentence punctuation, and avoid dash-led bullet lists. Use numbered
lists when a list helps. Preserve exact links, code, dates and filenames.
Speak as a helpful personal assistant, in plain English by default unless the
user requests another language. Answer capability questions directly, then ask
for the useful details. Do not explain backend architecture, providers, APIs,
skills or tool permissions unless the user explicitly asks for technical help.
Do not search product documentation to answer what you can do, or append
documentation citations to ordinary replies. Share relevant booking, venue or
travel links when they help the user's actual request, or sources when requested.
Describe help you can actually provide. Bring up a missing connection, sign-in
or approval only when it blocks the requested next step, in everyday language.
Never claim that a booking, payment, email or other action has completed without
confirmation from the service. Avoid a standard caveat paragraph or canned script.
[/WhatsApp reply style]""" + '\n' + CENTRAL_AI_IDENTITY

BOOKING_SKILL = 'personal-assistant-booking'


def install_booking_skill(home, source='/opt/setup/personal-assistant-booking.md'):
    """Install the packaged workflow in Leo's persistent native skill directory."""
    content = Path(source).read_bytes()
    destination = Path(home) / 'skills/personal-assistant' / BOOKING_SKILL / 'SKILL.md'
    if destination.exists() and destination.read_bytes() == content:
        return
    if destination.exists():
        backup = Path(home) / 'cache/skill-backups' / (BOOKING_SKILL + '.original.md')
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not backup.exists():
            backup.write_bytes(destination.read_bytes())
            os.chmod(backup, 0o600)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = destination.with_suffix('.tmp')
    temporary.write_bytes(content)
    os.chmod(temporary, 0o600)
    temporary.replace(destination)


def attach_booking_skill(event, home):
    """Load the installed skill for existing as well as new owner conversations."""
    if not re.search(r'\b(book(?:ing)?|reserv(?:e|ation)s?|appointments?|restaurants?|'
                     r'flights?|hotels?|travel|schedule\s+(?:a\s+)?meeting)\b',
                     event.text or '', re.I):
        return
    # Native auto_skill bindings only load on a new session in this release.
    # The per-turn sidecar supplies the actual installed skill without clearing
    # history or changing any other native skill bindings.
    path = Path(home) / 'skills/personal-assistant' / BOOKING_SKILL / 'SKILL.md'
    content = '[Installed booking workflow]\n' + path.read_text(encoding='utf-8') + '\n[/Installed booking workflow]'
    event.channel_prompt = '\n\n'.join(filter(None, [event.channel_prompt, content]))


def attach_assistant_skills(event, home):
    """Bring relevant installed owner workflows into existing chat turns."""
    attach_booking_skill(event, home)
    triggers = {
        'morning-briefing': r'\b(morning briefing|daily brief(?:ing)?|brief me|plan my day|day ahead|today.s priorities)\b',
        'follow-up-tracker': r'\b(follow[ -]?up|unanswered|waiting (?:for|on)|outstanding (?:replies|promises))\b',
        'meeting-preparation': r'\b(meeting (?:prep\w*|agenda|brief)|prepare.{0,40}meeting|talking points)\b',
        'team-coordination': r'\b(team (?:coord\w*|progress|update|status)|project (?:progress|update|status)|overdue (?:tasks|work))\b',
        'receipts-and-expenses': r'\b(receipts?|expenses?|reimburse\w*)\b',
    }
    for name, pattern in triggers.items():
        if not re.search(pattern, event.text or '', re.I):
            continue
        path = Path(home) / 'skills/personal-assistant' / name / 'SKILL.md'
        if path.is_file():
            note = '[Installed assistant workflow]\n' + path.read_text(encoding='utf-8') + '\n[/Installed assistant workflow]'
            event.channel_prompt = '\n\n'.join(filter(None, [event.channel_prompt, note]))


def clean_reply_punctuation(content):
    # Keep exact code/URLs; normalize only long sentence punctuation in prose.
    parts = re.split(r'(```[\s\S]*?(?:```|$)|`[^`\n]*`|https?://\S+)', content)
    return ''.join(part if index % 2 else re.sub(
        r'[ \t]*\u2014[ \t]*|[ \t]+\u2013[ \t]+', ', ', part)
        for index, part in enumerate(parts))

def get_configured_assistant_name(home=None):
    roots = ['/opt/data/profiles/leo', '/opt/data/profiles/leo-whatsapp-text']
    if home:
        roots.insert(0, str(home))
    for r in roots:
        p = Path(r) / '.assistant-name.json'
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding='utf-8'))
                if data.get('name'):
                    return str(data['name']).strip()
            except Exception:
                pass
    return os.environ.get('DEFAULT_ASSISTANT_NAME', 'Assistant')


def get_channel_context(assistant_name=None):
    name = assistant_name or get_configured_assistant_name()
    return f"""[Owner WhatsApp system context]
You are {name}, the same configured native assistant behind Central OS.
The OS is the dashboard; Central AI owns reasoning, tools, skills and native history.
PostgreSQL stores OS accounts, memberships, tasks, knowledge, schedules and attachments.
Native sessions and Hindsight memory are separate from that PostgreSQL database.
Fish provides the configured spoken voice; it is not the reasoning engine.
The dashboard includes tasks, knowledge, assistant chat/calls, team calendars,
approval reviews, routines, notifications, member access and memory controls.
Google Workspace is an optional per-member connection with separately approved
permissions; a feature existing does not mean an account is connected.
This admitted owner WhatsApp channel has its own conversation history. It does not
have a signed-in OS user session. Do not use mcp__michael_os__inspect_workspace or
Google/delegation tools that require an active authenticated OS conversation here.
Persistent Hindsight memory ('michael-os-leo') and Laya decision engine are shared across WhatsApp, voice calls, and App OS. When the user instructs characteristics, voice emotions, speaking tone, or personal preferences, immediately record and save them into memory so they persist across all channels. Actively reflect and embody saved user characteristics and voice emotions (warm, natural, empathetic emotions, clear measured diction, and confident personal assistant demeanor).
When the user instructs you to change rules, adopt new behavior guidelines, or update custom instructions (e.g. 'from now on remember...', 'update your instructions to...', 'change rules to...', 'new rule:...'), confirm that your instructions have been updated and synchronized across WhatsApp, Web Chat, and Voice calls.
For system-health questions use the fresh transport checks supplied below. They
are actual read-only probes, not memories or inferred status. Explain their scope:
an app/database check does not prove every feature works, and a configured provider
does not prove connectivity. Private workspace records, Google permissions, worker
and memory-server health are not checked by this snapshot. Never invent access.
Answer naturally and concisely. Do not include tool names, argument previews,
internal diagnostic chatter, or an automatic name/header in normal replies.
[/Owner WhatsApp system context]""" + '\n' + REPLY_STYLE


CHANNEL_CONTEXT = get_channel_context()


def quiet_whatsapp_display(home):
    """Use native per-platform display options, preserving other channels/settings."""
    import yaml
    path = Path(home) / 'config.yaml'
    original = path.read_text()
    config = yaml.safe_load(original)
    platform = config.setdefault('display', {}).setdefault('platforms', {}).setdefault('whatsapp', {})
    platform.update(tool_progress='off', show_reasoning=False, busy_ack_detail=False)
    updated = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    if updated == original:
        return
    backup = path.with_name('config.before-whatsapp-display.yaml')
    if not backup.exists():
        backup.write_text(original)
        os.chmod(backup, 0o600)
    temporary = path.with_name('config.whatsapp-display.tmp')
    temporary.write_text(updated)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _probe(url, headers=None):
    try:
        request = urllib.request.Request(url, headers=headers or {})
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = response.read(32769)
            if len(payload) > 32768:
                return None
            data = json.loads(payload)
            return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _native_health():
    # Fixed private endpoint and credential file; no model-selected URL or identity.
    try:
        settings = dict(line.split('=', 1) for line in
                        Path('/run/secrets/hermes_bridge_config').read_text().splitlines()
                        if '=' in line and not line.startswith('#'))
        key = settings['HERMES_API_KEY']
        if len(key) < 32:
            return None
        port = settings.get('HERMES_PORT') or os.environ.get('HERMES_PORT', '8642')
        return _probe(f'http://hermes:{port}/health', {'Authorization': 'Bearer ' + key})
    except (OSError, KeyError):
        return None


async def owner_channel_context(text, assistant_name=None):
    # Ordinary conversation needs no extra network wait. Relevant questions get
    # fresh probes in parallel, with independent failures and no cached success.
    base_context = get_channel_context(assistant_name)
    if not re.search(r'\b(system|health|status|database|db|workspace|michael|os|app|'
                     r'connection|connected|working|hermes|laya|gumagana|koneksyon|sistema)\b',
                     text or '', re.I):
        return base_context
    app, native = await asyncio.gather(
        asyncio.to_thread(_probe, 'http://web:3000/api/health'),
        asyncio.to_thread(_native_health))
    snapshot = {
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'source': 'Read-only owner WhatsApp transport probes for this turn',
        'services': {
            'os_app_and_database': 'verified_available' if app and app.get('status') == 'ok'
                                   else 'unavailable_or_unverified',
            'hermes_backend': 'verified_available' if native and native.get('hermes') is True
                              else 'unavailable_or_unverified',
            'laya': ('verified_available' if native.get('laya') is True else 'unavailable')
                    if native else 'unverified',
        },
        'not_checked': ['worker', 'Google account connections', 'Hindsight server',
                        'private workspace records', 'end-to-end voice quality'],
    }
    return base_context + '\n[Fresh system checks]\n' + json.dumps(snapshot) + '\n[/Fresh system checks]'
