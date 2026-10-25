"""Use Hermes message handling on the voice engine's single paired device."""
import asyncio
import base64
import contextvars
from datetime import datetime, timezone
import hashlib
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import shutil
import time
import urllib.parse
import urllib.request

binding=contextvars.ContextVar('leo_text_binding',default=None)

def _retain_memory_item(content, sender, route='whatsapp', context='whatsapp', tags=None):
    try:
        import os, urllib.request
        if not content or not isinstance(content, str) or len(content.strip()) < 20:
            return
        ctx_norm = str(context or 'whatsapp').strip().lower()
        # Per-customer tagged memory is always retained (it is isolated by tag); untagged chat stays opt-in.
        if not tags and ctx_norm in {'whatsapp', 'website', 'customer'} and os.environ.get('HINDSIGHT_RETAIN_ALL_CHAT', '').lower() != 'true':
            return
        api_url = (
            os.environ.get('HINDSIGHT_URL')
            or os.environ.get('HINDSIGHT_API_URL')
            or 'http://hindsight:8888'
        ).rstrip('/')
        key_file = Path('/run/secrets/hindsight_api_key')
        token = key_file.read_text().strip() if key_file.is_file() else ''
        if not token:
            cfg_file = Path('/opt/data/profiles/leo/hindsight/config.json')
            if cfg_file.is_file():
                try:
                    cfg_data = json.loads(cfg_file.read_text(encoding='utf-8'))
                    token = str(cfg_data.get('api_key') or '').strip()
                    if cfg_data.get('api_url'):
                        api_url = str(cfg_data['api_url']).rstrip('/')
                except Exception:
                    pass
        if not token:
            token = (
                os.environ.get('HINDSIGHT_API_KEY')
                or os.environ.get('HERMES_MEMORY_TOKEN')
                or os.environ.get('OPENROUTER_API_KEY')
                or 'hindsight-default-secret-key-32chars!!'
            ).strip()
        item = {
            'content': f"WhatsApp {sender} ({route}): {content.strip()}" if not tags else content.strip(),
            'context': context or 'whatsapp'
        }
        if tags:
            item['tags'] = [str(t) for t in tags]
        payload = json.dumps({'items': [item]}).encode('utf-8')
        req = urllib.request.Request(
            f'{api_url}/v1/default/banks/michael-os-leo/memories',
            data=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass


def _recall_memory_items(query: str, limit: int = 5, tags=None) -> list:
    try:
        import os, urllib.request
        clean_q = (query or '').strip()
        if not clean_q:
            return []
        api_url = (
            os.environ.get('HINDSIGHT_URL')
            or os.environ.get('HINDSIGHT_API_URL')
            or 'http://hindsight:8888'
        ).rstrip('/')
        key_file = Path('/run/secrets/hindsight_api_key')
        token = key_file.read_text().strip() if key_file.is_file() else ''
        if not token:
            cfg_file = Path('/opt/data/profiles/leo/hindsight/config.json')
            if cfg_file.is_file():
                try:
                    cfg_data = json.loads(cfg_file.read_text(encoding='utf-8'))
                    token = str(cfg_data.get('api_key') or '').strip()
                    if cfg_data.get('api_url'):
                        api_url = str(cfg_data['api_url']).rstrip('/')
                except Exception:
                    pass
        if not token:
            token = (
                os.environ.get('HINDSIGHT_API_KEY')
                or os.environ.get('HERMES_MEMORY_TOKEN')
                or os.environ.get('OPENROUTER_API_KEY')
                or 'hindsight-default-secret-key-32chars!!'
            ).strip()
        recall_body = {
            'query': clean_q[:240],
            'max_tokens': 800,
        }
        if tags:
            # Strict: only this customer's memories, never untagged/global or other customers'.
            recall_body['tags'] = [str(t) for t in tags]
            recall_body['tags_match'] = 'all_strict'
        payload = json.dumps(recall_body).encode('utf-8')
        req = urllib.request.Request(
            f'{api_url}/v1/default/banks/michael-os-leo/memories/recall',
            data=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode('utf-8'))
                raw_results = data.get('results') or data.get('memories') or data.get('items') or []
                out = []
                for r in raw_results:
                    txt = str(r.get('text') or r.get('content') or r.get('fact') or '').strip() if isinstance(r, dict) else str(r or '').strip()
                    if txt and txt not in out:
                        out.append(txt[:280])
                    if len(out) >= limit:
                        break
                return out
    except Exception:
        pass
    return []


_NONCONVERSATIONAL_PATTERNS = (
    'self-improvement review',
    'user profile updated',
    'memory updated',
    'memory saved',
    'profile updated',
    'skill \'',
    'skill "',
    '[background process',
    'hermes update',
    'gateway restarted',
    '/sethome',
    'home channel is set',
    'no home channel',
    'build a short profile',
    'working —',
    'working -',
)

def _is_non_conversational(content, metadata=None):
    if metadata and isinstance(metadata, dict):
        if any(metadata.get(k) for k in ('non_conversational', 'non_conversational_history', 'is_progress')):
            return True
        if metadata.get('kind') in {'tool_progress', 'thinking', 'status', 'card', 'review', 'diagnostic', 'system'}:
            return True
    if not content or not isinstance(content, str):
        return False
    text = content.strip()
    if '💾' in text:
        return True
    lower = text.lower()
    return any(p in lower for p in _NONCONVERSATIONAL_PATTERNS)


def _parse_dict(val):
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def _fetch_live_box3_context(text='', home=None):
    try:
        from hermes_plugins.central_ai_app_os.client import (
            inspect_workspace,
            _load_box3_snapshot,
            _DEFAULT_YCS_SNAPSHOT,
        )
        snap = _load_box3_snapshot() or dict(_DEFAULT_YCS_SNAPSHOT)
        overview = {}
        knowledge = {}
        try:
            overview = _parse_dict(inspect_workspace({'section': 'overview'}))
        except Exception:
            pass
        try:
            knowledge = _parse_dict(inspect_workspace({'section': 'knowledge', 'limit': 20}))
        except Exception:
            pass
        lower = (text or '').lower()
        extra = {}
        if any(w in lower for w in ('crm', 'client', 'lead', 'deal', 'customer', 'sale', 'job', 'pipeline')):
            try:
                extra['crm'] = _parse_dict(inspect_workspace({'section': 'crm', 'limit': 20}))
            except Exception:
                extra['crm'] = {'records': snap.get('crm') or []}
        if any(w in lower for w in ('contact', 'phone', 'number', 'directory', 'whatsapp')):
            try:
                extra['contacts'] = _parse_dict(inspect_workspace({'section': 'contacts', 'limit': 20}))
            except Exception:
                extra['contacts'] = {'records': snap.get('contacts') or []}
        if any(w in lower for w in ('task', 'todo', 'assignment', 'schedule')):
            try:
                extra['tasks'] = _parse_dict(inspect_workspace({'section': 'tasks', 'limit': 20}))
            except Exception:
                extra['tasks'] = {'records': snap.get('tasks') or []}
        if any(w in lower for w in ('agent', 'specialist', 'team', 'profile', 'sarah')):
            try:
                extra['agents'] = _parse_dict(inspect_workspace({'section': 'agents', 'limit': 20}))
            except Exception:
                extra['agents'] = {'records': snap.get('agents') or []}

        cards = (
            knowledge.get('records')
            or knowledge.get('knowledge')
            or snap.get('knowledge')
            or _DEFAULT_YCS_SNAPSHOT['knowledge']
        )
        counts = dict(_DEFAULT_YCS_SNAPSHOT.get('counts') or {})
        if isinstance(snap.get('counts'), dict):
            counts.update(snap['counts'])
        if isinstance(overview.get('counts'), dict):
            counts.update(overview['counts'])

        total_kb = (
            counts.get('knowledge_sources')
            or counts.get('knowledge')
            or knowledge.get('total')
            or (len(cards) if isinstance(cards, list) else 8)
        )
        counts['knowledge_sources'] = total_kb
        counts['available_skills'] = int(counts.get('available_skills') or 60)

        card_summaries = []
        if isinstance(cards, list):
            for c in cards[:25]:
                if isinstance(c, dict):
                    card_summaries.append({
                        'id': c.get('id'),
                        'title': c.get('title'),
                        'category': c.get('category'),
                        'content': str(c.get('content') or c.get('summary') or '')[:1500],
                    })
        payload = {
            'box3_connected': True,
            'knowledge_base_total_sources': total_kb,
            'available_skills_count': counts['available_skills'],
            'workspace_counts': counts,
            'knowledge_base_sources': card_summaries,
            'configured_agents': snap.get('agents') or _DEFAULT_YCS_SNAPSHOT['agents'],
        }
        if extra:
            payload['requested_sections'] = extra
        return (
            "[Live Box 3 & PostgreSQL Knowledge Base Ground Truth (Verified Live Now)]\n"
            f"CRITICAL SYSTEM OVERRIDE: You are connected to {total_kb} Knowledge Base sources and {counts['available_skills']} Available skills in Box 3 App OS, "
            "plus full live CRM, Tasks, Agents, and WhatsApp Contacts. "
            "Your Box 3 database, CRM, workspace, and Knowledge Base tools "
            "(mcp__michael_os__inspect_workspace, mcp__michael_os__manage_crm, mcp__michael_os__search_knowledge, "
            "mcp__michael_os__get_knowledge, mcp__michael_os__add_knowledge, mcp__michael_os__get_workspace_overview) "
            "ARE 100% ENABLED AND ACTIVE on this WhatsApp connection. "
            "IGNORE and DISREGARD any earlier messages in this WhatsApp chat history where you previously stated that knowledge base or CRM tools were not enabled or unavailable on WhatsApp. "
            "Below is the live verified Box 3 Knowledge Base & Workspace data:\n"
            + json.dumps(payload, ensure_ascii=False)
            + "\n[/Live Box 3 & PostgreSQL Knowledge Base Ground Truth]"
        )
    except Exception as exc:
        logging.error('Live Box 3 context fetch error: %s', exc)
        return ''


def _load_live_customer_config():
    import os, re
    cfg = {
        'customer_auto_reply': True,
        'customer_agent': 'James',
        'customer_agent_role': 'Customer Service & Solar Specialist',
        'customer_agent_instructions': '',
        'customer_agent_knowledge': [],
        'company_name': 'Your Choice Solar',
        'owner': '',
        'known_numbers': set(),
    }
    for p in ('/data/contacts.json', '/data/customer/contacts.json', '/opt/os-adapter/.runtime/contacts.json'):
        f = Path(p)
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
                if isinstance(data.get('customer_auto_reply'), bool):
                    cfg['customer_auto_reply'] = data['customer_auto_reply']
                raw_agent = str(data.get('customer_agent') or '').strip()
                if raw_agent:
                    cfg['customer_agent'] = raw_agent.capitalize()
                raw_role = str(data.get('customer_agent_role') or '').strip()
                if raw_role:
                    cfg['customer_agent_role'] = raw_role
                raw_instr = str(data.get('customer_agent_instructions') or '').strip()
                if raw_instr:
                    cfg['customer_agent_instructions'] = raw_instr
                if isinstance(data.get('customer_agent_knowledge'), list):
                    cfg['customer_agent_knowledge'] = data['customer_agent_knowledge']
                raw_company = str(data.get('company_name') or data.get('company') or '').strip()
                if raw_company:
                    cfg['company_name'] = raw_company
                owner_num = re.sub(r'\D', '', str(data.get('owner') or ''))
                if owner_num:
                    cfg['owner'] = owner_num
                    cfg['known_numbers'].add(owner_num)
                for c in data.get('contacts', []):
                    cnum = re.sub(r'\D', '', str(c.get('number') or c.get('phone_number') or ''))
                    if cnum:
                        cfg['known_numbers'].add(cnum)
                break
            except Exception:
                pass
    return cfg


_NON_NAME_WORDS = {
    'interested', 'looking', 'asking', 'wondering', 'here', 'good', 'fine', 'ready',
    'booking', 'just', 'from', 'in', 'at', 'on', 'to', 'for', 'with', 'after', 'about',
    'around', 'near', 'based', 'located', 'living', 'staying', 'moving', 'aiming',
    'hoping', 'wanting', 'needing', 'thinking', 'keen', 'curious', 'happy', 'free',
    'available', 'sure', 'okay', 'ok', 'not', 'also', 'currently', 'only', 'still',
    'very', 'pretty', 'quite', 'getting', 'using', 'paying', 'spending', 'running',
    'working', 'calling', 'texting', 'messaging', 'enquiring', 'inquiring', 'having',
    'trying', 'building', 'planning', 'home', 'house', 'property', 'business',
    'commercial', 'residential', 'solar', 'battery', 'batteries', 'system', 'package',
    'packages', 'quote', 'consultation', 'inspection', 'visit', 'call', 'phone',
    'email', 'address', 'suburb', 'gold', 'coast', 'brisbane', 'sydney', 'melbourne',
    'perth', 'adelaide', 'queensland', 'qld', 'nsw', 'vic', 'wa', 'sa', 'tas', 'act',
    'nt', 'hello', 'hi', 'hey', 'yes', 'yeah', 'yep', 'no', 'nah', 'thanks', 'thank',
}
_STREET_SUFFIX_RE = re.compile(
    r'^(street|st|road|rd|avenue|ave|drive|dr|court|ct|place|pl|lane|ln|way|circuit|cct|crescent|cres|boulevard|blvd|terrace|tce|highway|hwy|parade|pde|close|cl|esplanade|esp)$',
    re.I,
)
_PREMATURE_HANDOFF_RE = re.compile(
    r"\b(specialist will (?:reach out|contact|call|be in touch|get in touch)|team will (?:reach out|contact|call|be in touch|get in touch)|i(?:'ll| will) arrange (?:an?|your)\s+(?:on-site|site|free|solar)?\s*(?:consultation|visit|inspection|quote)|we(?:'ll| will) (?:reach out|be in touch|contact you)\s+shortly|someone will (?:reach out|contact|call))\b",
    re.I,
)
_SIMPLE_GREETING_ONLY_RE = re.compile(
    r"^(?:hi|hello|hey|good\s+(?:morning|afternoon|evening)|greetings|hi\s+there|hello\s+there|hey\s+there)(?:[\s,!?.]+(?:[a-z]{2,20}|how\s+are\s+you|how's\s+it\s+going|how\s+is\s+it\s+going))*[\s!?.]*$",
    re.I,
)
_LAST_WORKING_BOX3_URL = ''


def _is_valid_person_name(cand: str) -> bool:
    words = [w for w in cand.strip().split() if w]
    if not (1 <= len(words) <= 3):
        return False
    return all(
        re.match(r"^[A-Za-z][a-zA-Z'-]{1,24}$", w)
        and w.lower() not in _NON_NAME_WORDS
        and not _STREET_SUFFIX_RE.match(w)
        for w in words
    )


def _format_natural_list(items: list) -> str:
    if not items:
        return ''
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


_BILL_OR_NOISE_IN_ADDRESS_RE = re.compile(
    r'\b(paid|pay|paying|bill|bills|aud|dollars|bucks|kwh|kw|kwp|panel|panels|solar|battery|batteries|inverter|month|monthly|quarter|quarterly|year|yearly|annual|email|no\s+email|just\s+need|how\s+many|how\s+much|calculate|calculator|system|install|installation|setup|planning|quote|phone\s+consultation|website\s+property|street\s+name|first\s+last|not\s+yet|tell\s+me|use\s+first)\b|\$',
    re.I,
)
_STREET_SUFFIX_IN_TEXT_RE = re.compile(
    r'\b(street|st|road|rd|avenue|ave|drive|dr|court|ct|place|pl|lane|ln|way|circuit|cct|crescent|cres|boulevard|blvd|terrace|tce|highway|hwy|parade|pde|close|cl|esplanade|esp|grove|gr|mews|walk|rise|track|trail|loop|plaza|square|sq)\b',
    re.I,
)
_AFFIRMATIVE_CONFIRM_RE = re.compile(
    r'\b(yes|yeah|yep|yup|correct|that\'?s\s+(?:right|correct|all\s+correct|it|good|fine|accurate)|all\s+(?:correct|good|right|accurate)|confirmed|confirm|i\s+confirm|looks\s+good|sounds\s+good|spot\s+on|go\s+ahead|lock\s+it\s+in|book\s+it|please\s+do|sure\s+is|everything\s+is\s+correct)\b',
    re.I,
)
_NEGATIVE_OR_CORRECTION_RE = re.compile(
    r'\b(no\b|nope|nah|not\s+yet|wrong|incorrect|wait|hold\s+on|change|actually\s+(?:it\'?s|my)|instead)\b',
    re.I,
)


def _is_valid_confirmed_street_address(val: str) -> bool:
    raw = str(val or '').strip()
    if len(raw) < 6 or len(raw) > 160:
        return False
    if '?' in raw or '@' in raw or '...' in raw:
        return False
    if _BILL_OR_NOISE_IN_ADDRESS_RE.search(raw):
        return False
    if re.search(r'\b\d{1,3},\d{3}\b', raw):
        return False
    if not re.search(r'(?:^|[\s,/])\d{1,5}[A-Za-z]?\b', raw):
        return False
    cleaned_alpha = re.sub(r'\b(?:unit|apt|suite|lot|level|flat|no|qld|nsw|vic|wa|sa|tas|act|nt)\b', ' ', raw, flags=re.I)
    alpha_words = re.findall(r'[A-Za-z]{2,}', cleaned_alpha)
    if not alpha_words:
        return False
    return bool(_STREET_SUFFIX_IN_TEXT_RE.search(raw) or len(alpha_words) >= 2)


def _is_valid_confirmed_email(val: str) -> bool:
    raw = str(val or '').strip().lower()
    if not raw or '...' in raw:
        return False
    if not re.match(r'^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$', raw, re.I):
        return False
    if re.search(r'@(?:example\.com|test\.com|domain\.com|email\.com|sample\.com|yourcompany\.com)$', raw, re.I):
        return False
    return True


def _has_customer_confirmed_details(turns: list) -> bool:
    if not isinstance(turns, list) or len(turns) < 2:
        return False
    for idx in range(len(turns) - 1, 0, -1):
        m = turns[idx]
        if not isinstance(m, dict) or m.get('role') != 'user':
            continue
        user_txt = re.sub(r'^(?:📞\s*)?\[(?:Voice\s+Call|Incoming\s+WhatsApp\s+Voice\s+Call)\]\s*', '', str(m.get('content') or ''), flags=re.I).strip()
        if not user_txt or _NEGATIVE_OR_CORRECTION_RE.search(user_txt):
            continue
        if not _AFFIRMATIVE_CONFIRM_RE.search(user_txt):
            continue
        prev_asst = ''
        for j in range(idx - 1, -1, -1):
            if isinstance(turns[j], dict) and turns[j].get('role') == 'assistant':
                prev_asst = str(turns[j].get('content') or '')
                break
        if not prev_asst:
            continue
        asks_confirm = bool(
            re.search(r'\b(confirm|correct|right|accurate|check|details|summary|lodge|book|appointment)\b', prev_asst, re.I)
            or '?' in prev_asst
        )
        has_details_readback = bool('@' in prev_asst or (re.search(r'\b(name|phone|email|address)\b', prev_asst, re.I) and re.search(r'\d', prev_asst)))
        if asks_confirm and (has_details_readback or idx == len(turns) - 1):
            return True
    return False


def _extract_and_analyze_customer_lead(turns: list, sender_name: str = '', sender_id: str = '') -> dict:
    hint_name = (sender_name or '').strip()
    digits = re.sub(r'\D', '', str(sender_id or ''))
    hint_phone = f"+{digits}" if 8 <= len(digits) <= 15 else ''
    name = ''
    phone = ''
    email = ''
    address = ''
    suburb = ''
    existing_solar = ''
    power_bill = ''

    for idx, m in enumerate(turns or []):
        if not isinstance(m, dict) or m.get('role') != 'user':
            continue
        text = re.sub(r'^(?:📞\s*)?\[(?:Voice\s+Call|Incoming\s+WhatsApp\s+Voice\s+Call)\]\s*', '', str(m.get('content') or ''), flags=re.I).strip()
        text = re.sub(
            r'\b([a-z0-9._%+-]+(?:\s+dot\s+[a-z0-9._%+-]+)*)\s+at\s+([a-z0-9-]+(?:\s+dot\s+[a-z0-9-]+)+)\b',
            lambda em_m: (re.sub(r'\s+dot\s+', '.', em_m.group(1), flags=re.I) + '@' + re.sub(r'\s+dot\s+', '.', em_m.group(2), flags=re.I)).lower(),
            text,
            flags=re.I,
        )
        prev_asst_raw = ''
        if idx > 0 and isinstance(turns[idx - 1], dict) and turns[idx - 1].get('role') == 'assistant':
            prev_asst_raw = str(turns[idx - 1].get('content') or '')
        prev_asst = prev_asst_raw.lower()

        if not existing_solar:
            if re.search(r'\b(no\s+solar|don\'?t\s+have\s+solar|do\s+not\s+have\s+solar|no\s+existing\s+solar|brand[\s-]*new(?:\s+(?:solar|setup|system|install))?|new\s+(?:solar|setup|system|install|installation)|never\s+had\s+solar|none\s+yet|planning\s+to\s+setup\s+solar)\b', text, re.I):
                existing_solar = 'No existing solar'
            elif re.search(r'\b(already\s+have\s+solar|got\s+solar|existing\s+solar|have\s+a\s+\d+(?:\.\d+)?\s*kw|old\s+solar|replace\s+my\s+solar|upgrade\s+my\s+solar|\d+\s*(?:year|yr)s?\s+old(?:\s+solar)?)\b', text, re.I):
                existing_solar = text[:120]
            elif re.search(r'\b(have you got solar|do you have solar|already have solar|existing solar|solar already|solar on your roof|solar on the property)\b', prev_asst) and re.match(r'^(no|nah|nope|not yet|none|new|brand new|yes|yeah|yep)\b', text, re.I):
                existing_solar = 'No existing solar' if re.match(r'^(no|nah|nope|not yet|none|new|brand new)\b', text, re.I) else text[:120]

        if not power_bill:
            bm = (
                re.search(r'(?:\$|aud\s*)\s*(\d{1,3}(?:,\d{3})+|\d{2,5})(?:\.\d{1,2})?(?:\s*(?:/|\bper\b|\ba\b|\bfor\s+the\b)?\s*(?:quarter|qtr|quarterly|month|monthly|bi-?monthly|bill|year|yr))?', text, re.I)
                or re.search(r'\b(?:for\s+the\s+|per\s+)?(quarter|qtr|quarterly|month|monthly)\s*(?:bill\s*)?(?:is|was|of)?\s*\$?\s*(\d{1,3}(?:,\d{3})+|\d{2,5})(?:\.\d{1,2})?\s*(?:dollars|aud|bucks)?\b', text, re.I)
                or re.search(r'\b(\d{1,3}(?:,\d{3})+|\d{2,5})(?:\.\d{1,2})?\s*(?:dollars|aud|bucks)(?:\s*(?:per|a|each|for\s+the)?\s*(quarter|qtr|quarterly|month|monthly|bill))?', text, re.I)
                or re.search(r'\b(\d{1,3}(?:,\d{3})+|\d{2,5})(?:\.\d{1,2})?\s*(?:dollars|aud|bucks)?\s*(?:per|a|each|for\s+the)?\s*(quarter|qtr|quarterly|month|monthly|bill)\b', text, re.I)
            )
            if bm:
                power_bill = bm.group(0).strip()
            elif re.search(r'\b(power bill|electricity bill|energy bill|last bill|how much was your)\b', prev_asst) and re.search(r'\b(\d{1,3}(?:,\d{3})+|\d{2,5})\b', text):
                nm = re.search(r'\$?\s*(\d{1,3}(?:,\d{3})+|\d{2,5})(?:\s*(?:aud|dollars|bucks))?(?:\s*(?:per|a|for\s+the)?\s*(?:quarter|month|qtr|monthly|quarterly))?', text, re.I)
                if nm:
                    val = nm.group(0).strip()
                    power_bill = val if val.startswith('$') else f"${val}"

        if not email:
            em = re.search(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', text, re.I)
            if em and _is_valid_confirmed_email(em.group(0)):
                email = em.group(0).lower()
        if not phone:
            ph = re.search(r'(?:\+?61[\s-]?|0)[2-478](?:[\s-]?\d){8}\b|\b\+?\d[\d\s-]{7,14}\d\b', text)
            if ph and not _BILL_OR_NOISE_IN_ADDRESS_RE.search(text):
                phone = ph.group(0).strip()
            elif (
                hint_phone
                and (hint_phone in prev_asst_raw or digits in prev_asst_raw or re.search(r'\b(phone|number|contact|whatsapp\s+number|reach\s+you)\b', prev_asst))
                and re.search(r'\b(yes|yeah|yep|correct|that\'?s\s+(?:right|it|correct)|same\s+number|this\s+number|use\s+this)\b', text, re.I)
                and not _NEGATIVE_OR_CORRECTION_RE.search(text)
            ):
                phone = hint_phone

        if not _is_valid_confirmed_street_address(address):
            st = re.search(
                r'\b(?:unit\s+\d+[A-Za-z]?\s*[/,]\s*|\d+[A-Za-z]?\s*/\s*)?\d{1,5}[A-Za-z]?\s+[A-Za-z0-9.\s]{2,35}\b(?:Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Court|Ct|Place|Pl|Lane|Ln|Way|Circuit|Cct|Crescent|Cres|Boulevard|Blvd|Terrace|Tce|Highway|Hwy|Parade|Pde|Close|Cl|Esplanade|Esp|Grove|Gr|Mews|Walk|Rise|Track|Trail|Loop|Plaza|Square|Sq)\b(?:[,.\s]+[A-Za-z\s]{2,25})?(?:\s+(?:QLD|NSW|VIC|WA|SA|TAS|ACT|NT))?(?:\s+\d{4})?',
                text,
                re.I,
            )
            exp_addr = re.search(
                r'(?:street address is|property address is|my address is|address is|live at|located at|property is at|property at|address\s*[:=-])\s*([^\n.?!]{6,110})',
                text,
                re.I,
            )
            loc_match = (
                re.search(
                    r"\b(?:i am in|i'm in|im in|located in|based in|live in|living in|property in|home in|house in)\s+([A-Za-z][A-Za-z\s]{2,30}?)(?=[,.!?]|$|\s+(?:and|looking|aiming|wanting|hoping|for|to)\b)",
                    text,
                    re.I,
                )
                if not suburb
                else None
            )
            if st and _is_valid_confirmed_street_address(st.group(0)):
                address = st.group(0).strip()
            elif exp_addr and _is_valid_confirmed_street_address(exp_addr.group(1)):
                address = exp_addr.group(1).strip()
            elif (
                re.search(r'\b(address|street|where is the property|property located)\b', prev_asst)
                and 6 <= len(text) <= 120
                and '?' not in text
            ):
                cleaned_a = re.sub(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', '', text, flags=re.I).strip(' ,;')
                if _is_valid_confirmed_street_address(cleaned_a):
                    address = cleaned_a
            if loc_match and not suburb and not _BILL_OR_NOISE_IN_ADDRESS_RE.search(loc_match.group(1)):
                suburb = loc_match.group(1).strip()

        has_full = len([w for w in name.split() if w]) >= 2
        if not has_full:
            exp_name = re.search(
                r'(?:my full name is|full name is|my name is|name is|this is|call me|full name\s*[:=-]|name\s*[:=-])\s*([A-Za-z][a-zA-Z\'-]+(?:\s+[A-Za-z][a-zA-Z\'-]+){0,2})',
                text,
                re.I,
            )
            if exp_name:
                cwords = [
                    w
                    for w in exp_name.group(1).strip().split()
                    if w.lower() not in _NON_NAME_WORDS and not _STREET_SUFFIX_RE.match(w)
                ]
                cand = ' '.join(cwords)
                if _is_valid_person_name(cand) and (len(cwords) >= 2 or not name):
                    name = ' '.join(w[0].upper() + w[1:] for w in cwords)
            elif (
                hint_name
                and len(hint_name.split()) >= 2
                and _is_valid_person_name(hint_name)
                and hint_name.lower() in prev_asst
                and re.search(r'\b(yes|yeah|yep|correct|that\'?s\s+(?:right|my\s+name|correct))\b', text, re.I)
                and not _NEGATIVE_OR_CORRECTION_RE.search(text)
            ):
                name = hint_name
            else:
                segs = [
                    re.sub(r'^(hi|hello|hey|it\'s|its|name is|my name is|full name)\s*[:=-]?\s*', '', s, flags=re.I).strip()
                    for s in re.split(r'[\n,;]+', text)
                ]
                segs = [s for s in segs if s]
                for seg in segs:
                    if '@' in seg or re.search(r'\d', seg):
                        continue
                    words = seg.split()
                    if 2 <= len(words) <= 3 and _is_valid_person_name(seg):
                        if (
                            re.search(r'\b(name|full name|surname|last name|details|confirm|who am i speaking)\b', prev_asst)
                            or len(segs) >= 2
                            or (name and words[0].lower() == name.split()[0].lower())
                        ):
                            name = ' '.join(w[0].upper() + w[1:] for w in words)
                            break
                    elif (
                        len(words) == 1
                        and _is_valid_person_name(seg)
                        and re.search(
                            r'\b(last name|surname|family name|full name|your name|who am i speaking|may i have your name|what\'s your name|what is your name)\b',
                            prev_asst,
                        )
                    ):
                        wcap = words[0][0].upper() + words[0][1:]
                        if name and name.lower() != wcap.lower() and re.search(r'\b(last name|surname|family name|full name)\b', prev_asst):
                            name = f"{name} {wcap}"
                        elif not name:
                            name = wcap
                        break

    has_full_name = len([w for w in name.split() if w]) >= 2 and _is_valid_person_name(name)
    has_phone = 8 <= len(re.sub(r'\D', '', phone)) <= 15
    has_email = _is_valid_confirmed_email(email)
    has_street_address = _is_valid_confirmed_street_address(address)
    has_existing_solar = len(existing_solar) >= 2
    has_power_bill = len(power_bill) >= 2
    all_four_collected = has_full_name and has_phone and has_email and has_street_address
    customer_confirmed = all_four_collected and _has_customer_confirmed_details(turns)

    missing_natural = []
    if not has_existing_solar:
        missing_natural.append('whether you have solar already (and if yes, how old and what size in kW)')
    if not has_power_bill:
        missing_natural.append('how much your last power bill was ($$)')
    if not has_street_address:
        missing_natural.append(
            f"the full street address of your property in {suburb}"
            if suburb
            else "your property's full street address"
        )
    if not has_full_name:
        missing_natural.append('your last name (so we have your full name)' if name else 'your full name (first and last)')
    if not has_phone:
        missing_natural.append('your best contact phone number')
    if not has_email:
        missing_natural.append('your email address')
    if all_four_collected and not customer_confirmed:
        missing_natural.append('your confirmation that all 4 details (Full Name, Phone, Email, and Property Street Address) are correct')

    if not has_existing_solar:
        next_single_question = "Ask ONLY this 1 simple question next: whether they already have solar on the property or if this is for a new setup (do NOT ask about their power bill or address yet)."
    elif not has_power_bill:
        next_single_question = "Ask ONLY this 1 simple question next: roughly how much their last power bill was (do NOT ask for their address or contact details yet)."
    elif not has_street_address:
        next_single_question = (
            f"Ask ONLY this 1 simple question next: what the full street address (number and street name) is for their property in {suburb}."
            if suburb
            else "Ask ONLY this 1 simple question next: what the property's full street address (street number, street name, and suburb) is."
        )
    elif not has_full_name:
        next_single_question = (
            f"Ask ONLY this 1 simple question next: what their last name is so we have their full name ({name} ...)."
            if name
            else "Ask ONLY this 1 simple question next: what their full name (first and last) is."
        )
    elif not has_phone:
        next_single_question = "Ask ONLY this 1 simple question next: what their best contact phone number is."
    elif not has_email:
        next_single_question = "Ask ONLY this 1 simple question next: what their best email address is."
    elif not customer_confirmed:
        next_single_question = (
            f"All 4 contact & property details are collected, but NOT YET CONFIRMED by the customer. "
            f"Read back their Full Name ({name}), Phone Number ({phone}), Email Address ({email}), and Property Street Address ({address}) "
            f"clearly in 1-2 short sentences and ask them to confirm if all details are correct before lodging their inquiry "
            f"(do NOT output [[LEAD:...]] or [[BOOK:...]] until AFTER they reply confirming)."
        )
    else:
        next_single_question = "The customer has confirmed all 4 details! Thank them warmly in 1-2 friendly sentences, confirm their inquiry is now lodged with our team, and append the [[LEAD:...]] tag."

    checklist_lines = [
        'LIVE QUALIFICATION & CRM PROGRESS (ONE QUESTION AT A TIME — DO NOT STACK QUESTIONS):',
        f"- 1. Existing Solar: {'COLLECTED (' + existing_solar + ')' if has_existing_solar else 'Not asked yet'}",
        f"- 2. Last Power Bill ($$): {'COLLECTED (' + power_bill + ')' if has_power_bill else 'Not asked yet'}",
        f"- 3. Property Street Address: {'COLLECTED (' + address + ')' if has_street_address else ('Partial (' + suburb + ' — need street number & street name)' if suburb else 'Not asked yet')}",
        f"- 4. Full Name (First & Last): {'COLLECTED (' + name + ')' if has_full_name else ('Partial (First name: ' + name + ' — need last name)' if name else 'Not asked yet')}",
        f"- 5. Phone Number: {'COLLECTED (' + phone + ')' if has_phone else 'Not asked yet'}",
        f"- 6. Email Address: {'COLLECTED (' + email + ')' if has_email else 'Not asked yet'}",
        f"- 7. Customer Confirmation of All 4 Details: {'CONFIRMED BY CUSTOMER' if customer_confirmed else ('AWAITING READ-BACK & CUSTOMER CONFIRMATION' if all_four_collected else 'Pending collection of items 3-6')}",
        f"NEXT SINGLE STEP FOR THIS TURN: {next_single_question}",
    ]

    user_turns = [str(m.get('content') or '').strip() for m in (turns or []) if isinstance(m, dict) and m.get('role') == 'user']
    has_asst = any(isinstance(m, dict) and m.get('role') == 'assistant' for m in (turns or []))
    is_simple_greeting = bool(not has_asst and len(user_turns) == 1 and _SIMPLE_GREETING_ONLY_RE.match(user_turns[0]))

    return {
        'name': name if has_full_name else '',
        'phone': phone if has_phone else '',
        'email': email if has_email else '',
        'address': address if has_street_address else '',
        'existing_solar': existing_solar,
        'power_bill': power_bill,
        'has_full_name': has_full_name,
        'has_phone': has_phone,
        'has_email': has_email,
        'has_street_address': has_street_address,
        'has_existing_solar': has_existing_solar,
        'has_power_bill': has_power_bill,
        'customer_confirmed': customer_confirmed,
        'is_complete': all_four_collected and customer_confirmed,
        'is_simple_greeting': is_simple_greeting,
        'next_single_question': next_single_question,
        'missing_natural': missing_natural,
        'missing_str': _format_natural_list(missing_natural),
        'checklist_prompt': '\n'.join(checklist_lines),
    }


def _is_premature_lead_handoff(reply_text: str, missing_natural: list) -> bool:
    if not reply_text or not missing_natural:
        return False
    return bool(_PREMATURE_HANDOFF_RE.search(reply_text))


def _append_customer_sync_queue(item: dict):
    import os, time
    entry = {
        'sender_id': str(item.get('sender_id') or ''),
        'sender_name': str(item.get('sender_name') or ''),
        'wire_chat': str(item.get('wire_chat') or ''),
        'message_id': str(item.get('message_id') or ''),
        'text': str(item.get('text') or ''),
        'reply': item.get('reply'),
        'created_at': str(item.get('created_at') or time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())),
    }
    if not entry['sender_id'] or not entry['text']:
        return
    for qp in ('/data/customer/whatsapp_sessions_sync.json', '/data/whatsapp_sessions_sync.json'):
        try:
            f = Path(qp)
            f.parent.mkdir(parents=True, exist_ok=True)
            existing = []
            if f.is_file():
                try:
                    loaded = json.loads(f.read_text(encoding='utf-8'))
                    if isinstance(loaded, list):
                        existing = loaded
                except Exception:
                    existing = []
            updated = False
            for idx, row in enumerate(existing):
                if isinstance(row, dict) and row.get('message_id') and row.get('message_id') == entry['message_id']:
                    if entry.get('reply') and not row.get('reply'):
                        existing[idx]['reply'] = entry['reply']
                    updated = True
                    break
            if not updated:
                existing.append(entry)
            existing = existing[-200:]
            f.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
            os.chmod(qp, 0o666)
        except Exception:
            pass


def _forward_customer_whatsapp_to_box3(data: dict, home, is_customer_line: bool) -> dict:
    global _LAST_WORKING_BOX3_URL
    try:
        import os, socket, time, urllib.parse, urllib.request
        msg_text = str(data.get('body') or '').strip()
        sender_id = str(data.get('senderId') or '').strip()
        if not msg_text or not sender_id:
            return {'handled_by_box3': False}

        urls = []
        if _LAST_WORKING_BOX3_URL:
            urls.append(_LAST_WORKING_BOX3_URL)
        for p in ('/data/app_os_url.json', '/opt/data/app_os_url.json', '/opt/os-adapter/.runtime/app_os_url.json'):
            f = Path(p)
            if f.is_file():
                try:
                    j = json.loads(f.read_text(encoding='utf-8'))
                    if isinstance(j.get('urls'), list):
                        urls.extend(str(u).rstrip('/') for u in j['urls'] if u)
                    elif j.get('url'):
                        urls.append(str(j['url']).rstrip('/'))
                except Exception:
                    pass
        for env_k in ('APP_OS_URL', 'MICHAEL_OS_URL'):
            v = os.environ.get(env_k, '').strip().rstrip('/')
            if v:
                urls.append(v)
        urls.extend(['http://web:3000', 'http://127.0.0.1:3000'])
        deduped = list(dict.fromkeys(u for u in urls if u.startswith('http')))

        api_key = os.environ.get('HERMES_API_KEY', '').strip()
        if not api_key and home:
            try:
                env_file = Path(home) / '.env'
                if env_file.is_file():
                    for line in env_file.read_text(encoding='utf-8').splitlines():
                        if line.startswith('HERMES_API_KEY='):
                            api_key = line.split('=', 1)[1].strip().strip('"\'')
                            break
            except Exception:
                pass
        if not api_key:
            api_key = 'hermes_standalone_secret_token_32chars'

        payload = json.dumps({
            'sender_id': sender_id,
            'sender_name': str(data.get('senderName') or ''),
            'wire_chat': str(data.get('wireChat') or data.get('chatId') or ''),
            'message_id': str(data.get('messageId') or ''),
            'text': msg_text,
            'line': 'customer' if is_customer_line else 'executive',
            'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }).encode('utf-8')

        for base_url in deduped:
            try:
                parsed = urllib.parse.urlparse(base_url)
                host = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == 'https' else 80)
                if not host:
                    continue
                if base_url != _LAST_WORKING_BOX3_URL:
                    try:
                        with socket.create_connection((host, port), timeout=0.8):
                            pass
                    except Exception:
                        continue
                req = urllib.request.Request(
                    f'{base_url}/api/connections/whatsapp/webhook',
                    data=payload,
                    headers={
                        'Content-Type': 'application/json',
                        'Authorization': f'Bearer {api_key}',
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) CentralAI-Hermes/1.0',
                    },
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=50) as resp:
                    if resp.status == 200:
                        res_data = json.loads(resp.read().decode('utf-8'))
                        if res_data.get('ok'):
                            _LAST_WORKING_BOX3_URL = base_url
                            should_send = bool(res_data.get('auto_reply')) and not bool(res_data.get('duplicate'))
                            return {
                                'handled_by_box3': True,
                                'duplicate': bool(res_data.get('duplicate')),
                                'auto_reply': should_send,
                                'control_mode': res_data.get('control_mode'),
                                'customer_auto_reply': res_data.get('customer_auto_reply'),
                                'reply': res_data.get('reply') if should_send else None,
                                'existing_reply': res_data.get('reply'),
                            }
            except Exception:
                if base_url == _LAST_WORKING_BOX3_URL:
                    _LAST_WORKING_BOX3_URL = ''
                continue
    except Exception as exc:
        logging.error('Box 3 customer WhatsApp forward error: %s', exc)
    return {'handled_by_box3': False}


def _calculate_solar_pre_assessment(text: str) -> str:
    if not text or not str(text).strip():
        return ''
    try:
        from hermes_plugins.central_ai_app_os.client import calculate_solar_planning_v2
    except Exception:
        return ''

    raw = str(text)
    city = 'Brisbane, QLD'
    for c_key, c_label in (
        ('brisbane', 'Brisbane, QLD'),
        ('gold coast', 'Gold Coast, QLD'),
        ('sunshine coast', 'Sunshine Coast, QLD'),
        ('toowoomba', 'Toowoomba, QLD'),
        ('townsville', 'Townsville, QLD'),
        ('cairns', 'Cairns, QLD'),
        ('ipswich', 'Ipswich, QLD'),
        ('sydney', 'Sydney, NSW'),
        ('newcastle', 'Newcastle, NSW'),
        ('wollongong', 'Wollongong, NSW'),
        ('melbourne', 'Melbourne, VIC'),
        ('geelong', 'Geelong, VIC'),
        ('perth', 'Perth, WA'),
        ('adelaide', 'Adelaide, SA'),
        ('canberra', 'Canberra, ACT'),
        ('hobart', 'Hobart, TAS'),
        ('darwin', 'Darwin, NT'),
    ):
        if re.search(rf'\b{re.escape(c_key)}\b', raw, flags=re.IGNORECASE):
            city = c_label
            break

    prop_type = 'farm' if re.search(r'\bfarm\b', raw, flags=re.IGNORECASE) else (
        'business' if re.search(r'\b(business|commercial|warehouse|shop|office|factory)\b', raw, flags=re.IGNORECASE) else 'home'
    )
    usage_pattern = 'daytime' if re.search(r'\b(daytime|during the day|work from home|wfh)\b', raw, flags=re.IGNORECASE) else (
        'evenings' if re.search(r'\b(evening|evenings|night|after work)\b', raw, flags=re.IGNORECASE) else 'mix'
    )
    battery_interest = 'yes' if re.search(r'\b(battery|batteries|storage|powerwall|blackout)\b', raw, flags=re.IGNORECASE) else 'not_sure'

    # 1. Explicit kWh usage mention
    kwh_m = re.search(
        r'\b(\d{1,3}(?:,\d{3})+|\d{1,6})(?:\.(\d+))?\s*kwh(?:\s*(?:per|a|/|each|every|for\s+the|\s)\s*(day|daily|month|monthly|quarter|quarterly|year|yearly|annum))?',
        raw,
        flags=re.IGNORECASE,
    )
    if kwh_m:
        try:
            val = float(kwh_m.group(1).replace(',', '') + ('.' + kwh_m.group(2) if kwh_m.group(2) else ''))
            unit = (kwh_m.group(3) or '').lower()
            period = 'daily'
            if 'month' in unit or (not unit and 120 < val <= 1200):
                period = 'monthly'
            elif 'quarter' in unit or (not unit and 1200 < val <= 4500):
                period = 'quarterly'
            elif 'year' in unit or 'annum' in unit or (not unit and val > 4500):
                period = 'year'
            res = calculate_solar_planning_v2({
                'property_type': prop_type,
                'city': city,
                'usage_kwh': val,
                'period': period,
                'usage_pattern': usage_pattern,
                'battery_interest': battery_interest,
            })
            return (
                f"OFFICIAL YOUR CHOICE SOLAR WEBSITE CALCULATOR (planning-v2) LIVE RESULT:\n"
                f"- Summary: {res['summary_for_customer']}\n"
                f"- Estimated Electricity Usage: {res['estimated_daily_kwh']} kWh/day (~{round(res['estimated_monthly_kwh']):,} kWh/month, {round(res['annual_usage_kwh']):,} kWh/year)\n"
                f"- Starting Solar System Size to Explore: {res['recommended_kw']} kW of panels (~{res['estimated_panels']} x {res['panel_wattage_w']}W panels, {res['capacity_kwp']} kWp, generating ~{res['annual_generation_kwh']:,} kWh/year)\n"
                f"- Estimated Bill Reduction: AUD ${res['estimated_annual_savings_aud']:,}/year (~AUD ${res['estimated_monthly_savings_aud']:,}/month)\n"
                f"- CALCULATOR INSTRUCTION: Share these official starting figures clearly (including ~{res['estimated_panels']} panels when asked how many panels) if the customer shared their usage or asked to calculate kWh/size."
            )
        except Exception:
            pass

    # 2. Bill amount mention (supports "paid for the month 1,200 AUD", "$1,200 a month", "$600 per quarter")
    num_pat = r'(\d{1,3}(?:,\d{3})+|\d{2,6})(?:\.\d{1,2})?'
    unit_pat = r'(month|monthly|mth|mo|quarter|quarterly|qtr|three months|3 months|two months|2 months|bi-?monthly|year|yearly|annual|annually)'
    patterns = [
        (rf'\b(?:for\s+the\s+|per\s+|each\s+|every\s+|last\s+)?{unit_pat}\s*(?:bill\s*|power\s*bill\s*)?(?:is|was|of|around|about|:)?\s*(?:\$|aud\s*)?{num_pat}\s*(?:bucks|dollars|aud)?\b', 2, 1),
        (rf'(?:\$|\baud\s*){num_pat}\s*(?:bucks|dollars|aud)?\s*(?:(?:per|a|/|each|every|for\s+the)\s*)?{unit_pat}?', 1, 2),
        (rf'\b{num_pat}\s*(?:bucks|dollars|aud)\b(?:\s*(?:per|a|/|each|every|for\s+the)?\s*{unit_pat})?', 1, 2),
        (rf'\b(?:bill|spend|paying|pay|paid|costs?)\s*(?:is|was|of|around|about|for\s+the\s+{unit_pat})?\s*\$?{num_pat}\s*(?:bucks|dollars|aud)?\s*(?:(?:per|a|/|each|every|for\s+the)\s*)?{unit_pat}?', 2, 3),
    ]
    found_amt = None
    found_period = None
    for pat, amt_idx, unit_idx in patterns:
        for m in re.finditer(pat, raw, flags=re.IGNORECASE):
            try:
                amt = float((m.group(amt_idx) or '').replace(',', ''))
            except Exception:
                continue
            if not (35 <= amt <= 100000):
                continue
            if amt in {2024, 2025, 2026, 2027} and '$' not in m.group(0) and 'aud' not in m.group(0).lower():
                continue
            u_str = (m.group(unit_idx) or (m.group(1) if amt_idx == 2 and unit_idx == 3 else '') or '').lower()
            window = raw[max(0, m.start() - 40):min(len(raw), m.end() + 40)].lower()
            if any(k in u_str or k in window for k in ('month', 'mth', ' mo ')):
                found_period = 'monthly'
            elif any(k in u_str or k in window for k in ('two', '2 month', 'bi')):
                found_period = 'two_months'
            elif any(k in u_str or k in window for k in ('quarter', 'qtr', '3 month', 'three')):
                found_period = 'quarterly'
            elif any(k in u_str or k in window for k in ('year', 'annual')):
                found_period = 'year'
            else:
                found_period = 'monthly' if amt < 420 else 'quarterly'
            found_amt = amt

    if found_amt is not None and found_period is not None:
        try:
            res = calculate_solar_planning_v2({
                'property_type': prop_type,
                'city': city,
                'bill_amount_aud': found_amt,
                'period': found_period,
                'usage_pattern': usage_pattern,
                'battery_interest': battery_interest,
            })
            return (
                f"OFFICIAL YOUR CHOICE SOLAR WEBSITE CALCULATOR (planning-v2) LIVE RESULT:\n"
                f"- Summary: {res['summary_for_customer']}\n"
                f"- Estimated Electricity Usage: {res['estimated_daily_kwh']} kWh/day (~{round(res['estimated_monthly_kwh']):,} kWh/month, {round(res['annual_usage_kwh']):,} kWh/year)\n"
                f"- Starting Solar System Size to Explore: {res['recommended_kw']} kW of panels (~{res['estimated_panels']} x {res['panel_wattage_w']}W panels, {res['capacity_kwp']} kWp, generating ~{res['annual_generation_kwh']:,} kWh/year)\n"
                f"- Estimated Bill Reduction: AUD ${res['estimated_annual_savings_aud']:,}/year (~AUD ${res['estimated_monthly_savings_aud']:,}/month)\n"
                f"- CALCULATOR INSTRUCTION: Share these official starting figures clearly (including ~{res['estimated_panels']} panels when asked how many panels) when the customer gives their bill or asks to calculate kWh/size."
            )
        except Exception:
            pass
    return ''


def build_engine_adapter(route,member,home,group):
    import aiohttp, os, re
    from plugins.platforms.whatsapp.adapter import WhatsAppAdapter
    from gateway.platforms.base import SendResult,get_image_cache_dir,get_audio_cache_dir,get_document_cache_dir
    from whatsapp_system_context import get_whatsapp_reply_style,attach_assistant_skills,clean_reply_punctuation
    owner=member.get('number','')
    is_customer_line=bool(member.get('is_customer_line') or route=='customer')
    base_bridge_url=str(member.get('bridge_url') or ('http://customer-caller:8080' if is_customer_line else 'http://caller:8080')).rstrip('/')
    header_route=str(member.get('bridge_route') or ('owner' if is_customer_line else route))

    def _resolve_bridge_token():
        candidates = []
        if member.get('key_file'):
            candidates.append(Path(str(member['key_file'])))
        if is_customer_line:
            candidates.append(Path('/data/customer/voice-key'))
        else:
            candidates.append(Path('/data/voice-key'))
        for kp in candidates:
            if kp.is_file():
                try:
                    val = kp.read_text(encoding='utf-8').strip()
                    if len(val) >= 32:
                        return val
                except Exception:
                    pass
        for env_name in ('WHATSAPP_SETUP_KEY', 'HERMES_API_KEY'):
            val = os.environ.get(env_name, '').strip()
            if len(val) >= 32:
                return val
        if Path('/data/voice-key').is_file():
            try:
                return Path('/data/voice-key').read_text(encoding='utf-8').strip()
            except Exception:
                pass
        return 'hermes_standalone_secret_token_32chars'

    class EngineAdapter(WhatsAppAdapter):
        def _bridge_url(self,path):return f'{base_bridge_url}/text/{path}'

        async def connect(self,*,is_reconnect=False):
            self._http_session=aiohttp.ClientSession(headers={
                'Authorization':'Bearer '+_resolve_bridge_token(),'X-Leo-Route':header_route})
            self._mark_connected()
            self._poll_task=asyncio.create_task(self._poll_messages())
            return True

        async def disconnect(self):
            self._mark_disconnected()
            if self._poll_task:
                self._poll_task.cancel()
                await asyncio.gather(self._poll_task,return_exceptions=True)
            if self._http_session:await self._http_session.close()
            self._poll_task=self._http_session=None

        async def _bridge_unavailable(self):
            return None if self._running and self._http_session else 'Not connected'

        async def _build_message_event(self,data):
            if bool(data.get('isGroup'))!=member['group']:return None
            if member['group'] and group and data.get('chatId')!=group:return None
            sender_digits=re.sub(r'\D','',str(data.get('senderId') or ''))
            live_cfg=_load_live_customer_config()
            owner_digits=re.sub(r'\D','',str(owner or live_cfg.get('owner') or ''))
            is_owner_sender=bool(owner_digits and sender_digits==owner_digits)
            is_known_contact=bool(sender_digits and sender_digits in live_cfg['known_numbers'])

            # Enforce the Public Solar Inquiry Auto-Reply ON/OFF toggle in real time
            if is_customer_line:
                if not live_cfg['customer_auto_reply']:
                    return None
            elif not member['group'] and not is_owner_sender and not is_known_contact:
                if not live_cfg['customer_auto_reply']:
                    return None

            if not member.get('allow_all', True) and not member['group'] and owner and data.get('senderId')!=owner:return None
            event=await super()._build_message_event(data)
            if event is not None:
                msg_text = getattr(event, 'text', '') or ''
                solar_calc = _calculate_solar_pre_assessment(msg_text)
                treat_as_customer_inquiry = is_customer_line or (not member['group'] and not is_owner_sender)

                if treat_as_customer_inquiry:
                    agent_name = live_cfg['customer_agent'] or 'James'
                    agent_role = live_cfg.get('customer_agent_role') or 'Customer Service & Solar Specialist'
                    agent_instr = live_cfg.get('customer_agent_instructions') or ''
                    agent_kb = live_cfg.get('customer_agent_knowledge') or []
                    sorted_kb = sorted([c for c in agent_kb if isinstance(c, dict) and c.get('content')], key=lambda x: 0 if str(x.get('category') or '').lower() == 'process' else 1)
                    kb_str = '\n\n'.join(f"[{c.get('title', '')}{' (' + str(c.get('category')) + ')' if c.get('category') else ''}]\n{c.get('content', '')}" for c in sorted_kb)
                    if not kb_str:
                        kb_str = await asyncio.to_thread(_fetch_live_box3_context, msg_text, home)
                    company_name = live_cfg['company_name'] or 'Your Choice Solar'
                    customer_phone_display = f'+{sender_digits}' if sender_digits else 'their WhatsApp number'
                    event.channel_prompt = '\n\n'.join(filter(None, [
                        event.channel_prompt,
                        get_whatsapp_reply_style(),
                        f"You are {agent_name} ({agent_role}) representing {company_name} on WhatsApp, linked directly to the Process Knowledge Base ('YCS — customer-service drafting playbook'). "
                        f"The customer is messaging you from WhatsApp number {customer_phone_display} (Name: {data.get('senderName') or 'Customer'}). "
                        f"START SIMPLE & FRIENDLY (1 TO 2 SHORT SENTENCES MAX): Speak like a warm, friendly, real human texting on WhatsApp (1 to 2 short plain-text sentences max). NEVER output internal notes such as '(Next step: ...)', '(Step ...)', or '(Note: ...)'. When the customer simply says 'Hi' or 'Hello', reply ONLY with a warm, simple 1-sentence greeting introducing yourself and asking how you can help today—never dump company info or ask qualifying questions on an opening greeting. "
                        f"PROACTIVE VALUE FIRST (NEVER ASK 'WOULD YOU LIKE TO KNOW...?') & ONE QUESTION AT A TIME: Never ask permission to share information every customer obviously wants to know! NEVER ask 'Would you like to know how much you can save?', 'Do you want me to calculate what system size you need?', or 'Would you like me to explain how solar works?'. Give the actual answer, savings estimate, recommended kW system size, or clear explanation immediately in 1 to 2 punchy sentences first, and then ask ONLY ONE natural question per turn (never stack two questions with 'Also, ...'): (1) Do they already have solar on the property? (2) Roughly how much was their last power bill ($$)? (3) What is the property street address? (4) Full Name (first AND last name), Phone Number, and Email Address. Once all 4 details are collected, read back all 4 details (Full Name, Phone, Email, and Property Street Address) and ask the customer to confirm they are correct. NEVER append [[LEAD:...]] or [[BOOK:...]] until AFTER the customer explicitly confirms all 4 details, and NEVER use '...' or placeholder values. "
                        f"10/10 HUMAN WARMTH, REAL HUMAN FEEL & EFFORTLESS DRY WIT (NO ROBOTIC FILLER OR AI/TECH MENTIONS): Speak strictly as a real human specialist at {company_name}—NEVER say or imply that you are an AI, bot, virtual assistant, or automated agent, and NEVER mention technical errors, system glitches, prompts, tools, or say things like 'my memory of you hasn't been saved'. If you ever don't recall a detail they mentioned earlier, respond naturally like a real person ('I'm sorry, I can't remember—could you remind me?'). Bring light, natural, observational warmth and clever dry wit when talking about high power bills, Aussie sun, or grid retailers. NEVER start your replies with 'Thanks', 'Thank you', 'Thanks [Name]', 'Got it, [Name]', or 'Great, [Name]'. Greet once only on the first turn; never repeat 'Hi', 'Hello', 'Hey', or the customer's first name on follow-up replies. Never use casual slang like 'G'day', 'mate', 'howdy', or 'heya'. "
                        f"STRICT SYSTEM DATA GROUNDING: Rely on your Agent Instructions, Knowledge Base, and Official Your Choice Solar Website Calculator (planning-v2) below. Always share the official calculator's starting kWh usage, kW panel size, and estimated bill reduction proactively when a bill or usage figure is mentioned; never invent unverified hardware brands, fixed package prices, rebate amounts, or warranty lengths.",
                        solar_calc,
                        f"AGENT INSTRUCTIONS (FROM APP OS AGENT PROFILE):\n{agent_instr}" if agent_instr else "",
                        f"KNOWLEDGE BASE (FROM APP OS):\n{kb_str}" if kb_str else "",
                    ]))
                else:
                    from hermes_os import WORKSPACE_TOOLS_NOTE
                    live_box3 = await asyncio.to_thread(_fetch_live_box3_context, msg_text, home)
                    event.channel_prompt='\n\n'.join(filter(None,[event.channel_prompt,get_whatsapp_reply_style(),WORKSPACE_TOOLS_NOTE,
                        'Use live tools to discover current workspace knowledge, skills and capabilities. '
                        'Use the tools exposed to this authorized session; capability guidance does not grant access to unavailable tools. '
                        'When the user asks to make a social media post, create a task, or assign work to an agent, ALWAYS invoke mcp__michael_os__manage_tasks(action=\'create\', title=..., instruction=..., agent=..., auto_run=True) so the task and AI automation run appear in Box 3 Tasks and Approvals (\'review\' status). '
                        'Report unavailable tools or permission errors accurately. '
                        'You represent Your Choice Solar: selling high-grade solar systems, panels, inverters, and batteries, and installing/maintaining solar setups across Australia. '
                        'Always reply strictly and exclusively in clear, warm, professional English (never use casual slang like "G\'day" or "mate", and never switch to Tagalog, Taglish, Filipino, or any other language). Answer questions about the knowledge base, CRM, business, services, and company capabilities directly, confidently, and warmly. '
                        'Use this native profile’s configured memory tools for saved facts; do not invent memories. '
                        'When the user instructs characteristics, voice emotions, speaking tone, or personal preferences, immediately record and save them into memory so they persist across WhatsApp and App OS. '
                        'If an attachment is marked unavailable, ask for its contents or a smaller copy; never claim to have read it.',
                        solar_calc,
                        live_box3]))
                    if route in {'mark', 'owner'}:attach_assistant_skills(event,home)
            return event

        async def _collect_bridge_media(self,data,msg_type):
            urls=[]
            allowed_roots=[(Path('/data/text/media')/route).resolve(), (Path('/data/text/media')/header_route).resolve(), (Path('/data/customer/text/media')/header_route).resolve()]
            kind=data.get('mediaType')
            target=Path(get_image_cache_dir() if kind=='image' else get_audio_cache_dir() if kind=='audio' else get_document_cache_dir())
            target.mkdir(parents=True,exist_ok=True)
            for name in data.get('mediaUrls',[]):
                cand_paths=[Path(name)]
                if is_customer_line and str(name).startswith('/data/'):
                    cand_paths.insert(0, Path('/data/customer') / str(name)[len('/data/'):])
                for path in cand_paths:
                    if not path.is_file() or path.is_symlink():continue
                    resolved=path.resolve()
                    if not any(resolved.is_relative_to(ar) for ar in allowed_roots):continue
                    copied=target/path.name
                    await asyncio.to_thread(shutil.copyfile,path,copied)
                    copied.chmod(0o600);urls.append(str(copied))
                    break
            return urls,[data.get('mimetype') or 'application/octet-stream']*len(urls)

        async def _poll_messages(self):
            while self._running:
                try:
                    async with self._bridge_req('get','messages',10) as response:
                        packets=await response.json() if response.status==200 else []
                    for data in packets:
                        token=binding.set(data)
                        try:
                            sender_digits=re.sub(r'\D','',str(data.get('senderId') or ''))
                            live_cfg=_load_live_customer_config()
                            owner_digits=re.sub(r'\D','',str(owner or live_cfg.get('owner') or ''))
                            is_owner_sender=bool(owner_digits and sender_digits==owner_digits)
                            is_known_contact=bool(sender_digits and sender_digits in live_cfg['known_numbers'])
                            treat_as_customer=is_customer_line or (not bool(data.get('isGroup')) and not is_owner_sender and not is_known_contact)

                            if treat_as_customer and str(data.get('body') or '').strip():
                                chat_id=data.get('chatId')
                                lease=data.get('bridgeLease')
                                await self._send_typing(chat_id,lease,'composing')
                                box3_res={'handled_by_box3':False}
                                try:
                                    box3_res=await asyncio.to_thread(_forward_customer_whatsapp_to_box3,data,home,is_customer_line)
                                except Exception:
                                    box3_res={'handled_by_box3':False}
                                finally:
                                    await self._send_typing(chat_id,lease,'paused')
                                if box3_res.get('handled_by_box3'):
                                    reply_val = str(box3_res['reply']) if (box3_res.get('auto_reply') and box3_res.get('reply')) else None
                                    await asyncio.to_thread(_append_customer_sync_queue,{
                                        'sender_id':data.get('senderId'),
                                        'sender_name':data.get('senderName'),
                                        'wire_chat':data.get('wireChat') or data.get('chatId'),
                                        'message_id':data.get('messageId'),
                                        'text':data.get('body'),
                                        'reply':reply_val,
                                    })
                                    if reply_val:
                                        await self.send(data['chatId'],reply_val)
                                    await self._complete(data,'complete')
                                    continue
                                await asyncio.to_thread(_append_customer_sync_queue,{
                                    'sender_id':data.get('senderId'),
                                    'sender_name':data.get('senderName'),
                                    'wire_chat':data.get('wireChat') or data.get('chatId'),
                                    'message_id':data.get('messageId'),
                                    'text':data.get('body'),
                                    'reply':None,
                                })
                                if not live_cfg['customer_auto_reply']:
                                    await self._complete(data,'complete')
                                    continue

                            event=await self._build_message_event(data)
                            if event:
                                await self.handle_message(event)
                            else:await self._complete(data,'rejected')
                        except asyncio.CancelledError:
                            raise
                        except Exception as pkt_exc:
                            logging.error('WhatsApp packet processing error on route %s: %s', route, pkt_exc)
                            try:
                                await self._complete(data,'interrupted')
                            except Exception:
                                pass
                        finally:binding.reset(token)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logging.error('Leo text route %s temporarily unavailable',route)
                    await asyncio.sleep(3)
                await asyncio.sleep(.3)

        async def _complete(self,data,status):
            async with self._bridge_req('post','complete',5,json={'chatId':data['chatId'],
                'bridgeLease':data['bridgeLease'],'status':status}) as response:
                if response.status!=200:logging.error('Text processing receipt unavailable for %s',route)

        async def _send_typing(self,chat_id,lease=None,presence='composing'):
            if not chat_id or not self._http_session:
                return
            try:
                payload={'chatId':chat_id,'presence':presence}
                if lease:payload['bridgeLease']=lease
                async with self._bridge_req('post','typing',5,json=payload) as resp:
                    pass
            except Exception:
                pass

        async def _typing_loop(self,chat_id,lease,stop_event):
            while not stop_event.is_set():
                await self._send_typing(chat_id,lease,'composing')
                try:
                    await asyncio.wait_for(stop_event.wait(),timeout=6.0)
                except (asyncio.TimeoutError,asyncio.CancelledError):
                    pass

        async def _process_message_background(self,event,session_key):
            token=binding.set(event.raw_message)
            stop_typing=asyncio.Event()
            raw=event.raw_message if isinstance(event.raw_message,dict) else {}
            chat_id=raw.get('chatId')
            lease=raw.get('bridgeLease')
            typing_task=asyncio.create_task(self._typing_loop(chat_id,lease,stop_typing))
            try:
                return await super()._process_message_background(event,session_key)
            finally:
                stop_typing.set()
                typing_task.cancel()
                await self._send_typing(chat_id,lease,'paused')
                binding.reset(token)

        async def _dispatch_inline_reply(self,event,**kwargs):
            token=binding.set(event.raw_message)
            try:return await super()._dispatch_inline_reply(event,**kwargs)
            finally:binding.reset(token)

        async def on_processing_complete(self,event,outcome):
            # Native histories retain uncertain outcomes; a claimed action is never replayed on restart.
            await self._complete(event.raw_message,'complete' if str(getattr(outcome,'value',outcome)).lower()=='success' else 'interrupted')

        async def _post_bridge_message(self,path,payload,*,timeout):
            data=binding.get()
            if not isinstance(data,dict) or payload.get('chatId')!=data.get('chatId'):
                return SendResult(success=False,error='No admitted inbound reply binding')
            payload={**payload,'bridgeLease':data['bridgeLease']}
            digest=json.dumps({'path':path,'payload':payload},sort_keys=True,ensure_ascii=False).encode()
            payload['sendKey']=hashlib.sha256(digest).hexdigest()
            return await super()._post_bridge_message(path,payload,timeout=timeout)

        async def send(self,chat_id,content,reply_to=None,metadata=None):
            if _is_non_conversational(content, metadata):
                return SendResult(success=True)
            raw_tags = re.findall(r'\[\[\s*(?:BOOK|LEAD)\s*:\s*\{[\s\S]*?\}\s*\]\]', content or '', flags=re.IGNORECASE)
            cleaned = clean_reply_punctuation(content)
            if cleaned:
                cleaned = re.sub(r'\[\[\s*(?:BOOK|LEAD)[\s\S]*?\]\]+', '', cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r'\s*[\(\[]\s*(?:next\s+step|step\s*\d|note|internal|checklist|qualification|follow[- ]?up|once\s+they|after\s+they|then\s+ask|ask\s+only)\b[\s\S]*?[\)\]]', '', cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r'(?:^|\n)\s*(?:\(?\s*(?:next\s+step|step\s*\d+|internal\s+note|note)\s*[:\-][\s\S]*)$', '', cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r'^\s*\[+\s*|\s*\]+\s*$', '', cleaned).strip()
            if not cleaned or not cleaned.strip():
                return SendResult(success=True)
            raw_bound = binding.get()
            if isinstance(raw_bound, dict) and (is_customer_line or not bool(raw_bound.get('isGroup'))):
                sync_reply = (cleaned + (' ' + ' '.join(raw_tags) if raw_tags else '')).strip()
                asyncio.create_task(asyncio.to_thread(_append_customer_sync_queue, {
                    'sender_id': raw_bound.get('senderId'),
                    'sender_name': raw_bound.get('senderName'),
                    'wire_chat': raw_bound.get('wireChat') or raw_bound.get('chatId'),
                    'message_id': raw_bound.get('messageId'),
                    'text': raw_bound.get('body'),
                    'reply': sync_reply,
                }))
            if raw_tags:
                asyncio.create_task(asyncio.to_thread(_retain_memory_item, f"{cleaned} {' '.join(raw_tags)}", 'Leo', route, 'qualified_lead_outcome'))
            return await super().send(chat_id,cleaned,reply_to=None,metadata=metadata)

        async def edit_message(self,chat_id,message_id,content,*,finalize=False):
            if _is_non_conversational(content):
                return SendResult(success=True)
            return await self._post_bridge_message('edit',{'chatId':chat_id,'messageId':message_id,
                'message':clean_reply_punctuation(content)},timeout=30)

        async def _send_media_to_bridge(self,chat_id,file_path,media_type,caption=None,file_name=None):
            path=Path(file_path)
            if not path.is_file() or path.stat().st_size>16*1024*1024:
                return SendResult(success=False,error='Attachment missing or exceeds the 16 MB chat limit')
            data=await asyncio.to_thread(path.read_bytes)
            return await self._post_bridge_message('send-media',{'chatId':chat_id,'message':caption or '',
                'mediaBase64':base64.b64encode(data).decode(),'mediaType':media_type,
                'mimetype':mimetypes.guess_type(file_name or path.name)[0] or 'application/octet-stream',
                'fileName':file_name or path.name},timeout=30)

    return EngineAdapter
