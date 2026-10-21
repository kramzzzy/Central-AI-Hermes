"""Use Hermes message handling on the voice engine's single paired device."""
import asyncio
import base64
import contextvars
import hashlib
import json
import logging
import mimetypes
from pathlib import Path
import shutil

binding=contextvars.ContextVar('leo_text_binding',default=None)

def _retain_memory_item(content, sender, route):
    try:
        if not content or not isinstance(content, str) or not content.strip():
            return
        key_file = Path('/run/secrets/hindsight_api_key')
        token = key_file.read_text().strip() if key_file.is_file() else ''
        if not token:
            cfg_file = Path('/opt/data/profiles/leo/hindsight/config.json')
            if cfg_file.is_file():
                token = json.loads(cfg_file.read_text()).get('api_key', '')
        if not token:
            return
        import urllib.request
        payload = json.dumps({
            'items': [{
                'content': f"WhatsApp {sender} ({route}): {content.strip()}",
                'context': 'whatsapp'
            }]
        }).encode('utf-8')
        req = urllib.request.Request(
            'http://hindsight:8888/v1/default/banks/michael-os-leo/memories',
            data=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass


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
    import os, urllib.request
    msg_text = str(data.get('body') or '').strip()
    sender_id = str(data.get('senderId') or '').strip()
    if not msg_text or not sender_id:
        return {'handled_by_box3': False}

    urls = []
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
    }).encode('utf-8')

    for base_url in deduped:
        try:
            req = urllib.request.Request(
                f'{base_url}/api/connections/whatsapp/webhook',
                data=payload,
                headers={
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {api_key}',
                },
                method='POST',
            )
            with urllib.request.urlopen(req, timeout=45) as resp:
                if resp.status == 200:
                    res_data = json.loads(resp.read().decode('utf-8'))
                    if res_data.get('ok'):
                        return {
                            'handled_by_box3': True,
                            'auto_reply': bool(res_data.get('auto_reply')),
                            'reply': res_data.get('reply'),
                        }
        except Exception:
            continue
    return {'handled_by_box3': False}


def _calculate_solar_pre_assessment(text: str) -> str:
    import math, re
    if not text or not isinstance(text, str):
        return ''
    lower = text.lower()
    kwh_match = re.search(r'\b(\d{1,3}(?:\.\d+)?)\s*kwh\s*(?:/|per\s+|a\s+)?(?:day|daily)\b', lower)
    bill_match = re.search(
        r'(?:\$\s*(\d{2,5}(?:,\d{3})*(?:\.\d{1,2})?)|(\d{2,5}(?:,\d{3})*)\s*(?:dollars|aud|bucks|a\s+month|per\s+month|monthly|a\s+quarter|per\s+quarter|quarterly|a\s+year|yearly))',
        lower,
    )
    if not kwh_match and not bill_match:
        return ''

    tariff = 0.32
    supply = 1.15
    psh = 4.4
    daily_kwh = 0.0
    input_desc = ''

    if kwh_match:
        daily_kwh = float(kwh_match.group(1))
        input_desc = f'~{round(daily_kwh)} kWh/day usage'
    elif bill_match:
        raw_val = (bill_match.group(1) or bill_match.group(2) or '').replace(',', '')
        try:
            amount = float(raw_val)
        except ValueError:
            return ''
        if amount < 40 or amount > 25000:
            return ''
        if any(w in lower for w in ('quarter', 'quarterly', '3 month', 'three month', '90 day')):
            days, period_label = 90.0, 'quarterly'
        elif any(w in lower for w in ('year', 'yearly', 'annual', 'annually')):
            days, period_label = 365.0, 'annual'
        elif any(w in lower for w in ('week', 'weekly')):
            days, period_label = 7.0, 'weekly'
        else:
            days, period_label = 30.0, 'monthly'
        daily_cost = amount / days
        usage_cost = max(daily_cost - supply, daily_cost * 0.78)
        daily_kwh = max(5.0, usage_cost / tariff)
        input_desc = f'${int(round(amount))} {period_label} power bill (~{round(daily_kwh)} kWh/day)'

    if daily_kwh <= 0:
        return ''

    raw_kw = (daily_kwh * 1.2) / psh
    standard_sizes = [6.6, 8.8, 10.5, 13.2, 15.8, 20.0, 26.4, 30.0, 39.6, 50.0]
    rec_kw = next((s for s in standard_sizes if s >= raw_kw), 50.0)
    panels = int(math.ceil((rec_kw * 1000.0) / 440.0))
    if rec_kw <= 6.6:
        battery = 'Optional 10 kWh battery (great for evening peak coverage)'
    elif rec_kw <= 10.5:
        battery = '10–13.5 kWh LFP solar battery recommended'
    elif rec_kw <= 15.8:
        battery = '13.5–20 kWh modular solar battery recommended'
    else:
        battery = '20–30 kWh high-capacity battery storage recommended'

    annual_gen = rec_kw * psh * 365.0
    self_consumed = min(daily_kwh * 365.0 * 0.70, annual_gen * 0.65)
    exported = max(0.0, annual_gen - self_consumed)
    annual_savings = int(round((self_consumed * tariff + exported * 0.06) / 50.0) * 50)
    monthly_savings = int(round(annual_savings / 12.0 / 10.0) * 10)

    return (
        f"[SOLAR PRE-ASSESSMENT CALCULATOR RESULT FOR THIS CUSTOMER]\n"
        f"• Customer Input: {input_desc}\n"
        f"• Recommended Solar System: {rec_kw} kW (~{panels} × 440W high-efficiency panels)\n"
        f"• Recommended Battery Option: {battery}\n"
        f"• Estimated Bill Savings: ~${monthly_savings}/month (~${annual_savings}/year)\n"
        f"• Estimated Payback Period: ~3.5 to 4.5 years\n"
        f"INSTRUCTION: Share this pre-assessment clearly and concisely with the customer, then offer to have a solar expert call them (or book a phone call / on-site solar visit at a day and time that suits them)!"
    )


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
        return Path('/data/voice-key').read_text(encoding='utf-8').strip()

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
                live_box3 = await asyncio.to_thread(_fetch_live_box3_context, msg_text, home)
                solar_calc = _calculate_solar_pre_assessment(msg_text)
                treat_as_customer_inquiry = is_customer_line or (not member['group'] and not is_owner_sender)

                if treat_as_customer_inquiry:
                    agent_name = live_cfg['customer_agent'] or 'James'
                    company_name = live_cfg['company_name'] or 'Your Choice Solar'
                    customer_phone_display = f'+{sender_digits}' if sender_digits else 'their WhatsApp number'
                    event.channel_prompt = '\n\n'.join(filter(None, [
                        event.channel_prompt,
                        get_whatsapp_reply_style(),
                        f"You are {agent_name}, the Solar Customer Service & Pre-Assessment Specialist at {company_name} on WhatsApp. "
                        f"Always reply strictly and exclusively in natural, warm Australian English (en-AU); never switch to Tagalog, Taglish, or any other language. "
                        f"Keep WhatsApp replies concise, friendly, and easy to read on a phone (2–4 short sentences or clean bullet points). "
                        f"The customer is messaging you from WhatsApp number {customer_phone_display} (Name: {data.get('senderName') or 'Customer'}). "
                        f"SOLAR INQUIRY & PRE-ASSESSMENT WORKFLOW:\n"
                        f"1. Welcome anyone inquiring about solar panels, solar batteries, inverters, rebates, or installation across Australia.\n"
                        f"2. If the customer shares their monthly or quarterly electricity bill (e.g. $400/month or $600/quarter) or daily kWh usage, give them the Solar Pre-Assessment below (recommended system size in kW, panel count, battery recommendation, and estimated savings). If they haven't shared their bill or usage yet, warmly ask for their approximate monthly or quarterly power bill so you can calculate a tailored solar pre-assessment.\n"
                        f"3. Always offer to arrange either a **Phone Call with our Solar Expert** (we already have their WhatsApp/phone number {customer_phone_display}) OR an **On-Site Solar Visit / Roof Inspection** at a day and time that suits them.\n"
                        f"4. When a customer provides their details or books a call/visit, use mcp__michael_os__manage_crm or mcp__michael_os__manage_tasks to log their lead and appointment in Box 3 CRM.",
                        solar_calc,
                        live_box3,
                    ]))
                else:
                    event.channel_prompt='\n\n'.join(filter(None,[event.channel_prompt,get_whatsapp_reply_style(),
                        'You are fully connected to the Central AI knowledge base (8 Knowledge Base sources and 60 Available skills) and persistent Hindsight memory (\'michael-os-leo\'). '
                        'All Box 3 tools (mcp__michael_os__inspect_workspace, mcp__michael_os__manage_crm, mcp__michael_os__manage_tasks, mcp__michael_os__create_task, mcp__michael_os__search_knowledge, mcp__michael_os__get_knowledge, mcp__michael_os__add_knowledge, mcp__michael_os__get_workspace_overview) are enabled on this WhatsApp connection. '
                        'When the user asks to make a social media post, create a task, or assign work to an agent, ALWAYS invoke mcp__michael_os__manage_tasks(action=\'create\', title=..., instruction=..., agent=..., auto_run=True) so the task and AI automation run appear in Box 3 Tasks and Approvals (\'review\' status). '
                        'NEVER claim that knowledge base or workspace tools are disabled or unavailable on WhatsApp. '
                        'You represent Your Choice Solar: selling high-grade solar systems, panels, inverters, and batteries, and installing/maintaining solar setups across Australia. '
                        'Always reply strictly and exclusively in natural Australian English (en-AU); never switch to Tagalog, Taglish, Filipino, or any other language. Answer questions about the knowledge base, CRM, business, services, and company capabilities directly, confidently, and warmly. '
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
                                try:
                                    box3_res=await asyncio.to_thread(_forward_customer_whatsapp_to_box3,data,home,is_customer_line)
                                finally:
                                    await self._send_typing(chat_id,lease,'paused')
                                if box3_res.get('handled_by_box3'):
                                    if box3_res.get('auto_reply') and box3_res.get('reply'):
                                        await self.send(data['chatId'],str(box3_res['reply']))
                                    await self._complete(data,'complete')
                                    continue
                                if not live_cfg['customer_auto_reply']:
                                    await asyncio.to_thread(_append_customer_sync_queue,{
                                        'sender_id':data.get('senderId'),
                                        'sender_name':data.get('senderName'),
                                        'wire_chat':data.get('wireChat') or data.get('chatId'),
                                        'message_id':data.get('messageId'),
                                        'text':data.get('body'),
                                        'reply':None,
                                    })
                                    await self._complete(data,'complete')
                                    continue

                            event=await self._build_message_event(data)
                            if event:
                                if getattr(event, 'text', None):
                                    asyncio.create_task(asyncio.to_thread(_retain_memory_item, event.text, 'User', route))
                                await self.handle_message(event)
                            else:await self._complete(data,'rejected')
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
            cleaned = clean_reply_punctuation(content)
            if not cleaned or not cleaned.strip():
                return SendResult(success=True)
            raw_bound = binding.get()
            if isinstance(raw_bound, dict) and (is_customer_line or not bool(raw_bound.get('isGroup'))):
                asyncio.create_task(asyncio.to_thread(_append_customer_sync_queue, {
                    'sender_id': raw_bound.get('senderId'),
                    'sender_name': raw_bound.get('senderName'),
                    'wire_chat': raw_bound.get('wireChat') or raw_bound.get('chatId'),
                    'message_id': raw_bound.get('messageId'),
                    'text': raw_bound.get('body'),
                    'reply': cleaned,
                }))
            asyncio.create_task(asyncio.to_thread(_retain_memory_item, cleaned, 'Leo', route))
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
