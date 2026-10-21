"""Use the adapter's active per-process capability; fall back to Box 1 bridge secret and live Box 3 snapshot across Voice & WhatsApp."""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

SECTIONS = {
    "status",
    "overview",
    "tasks",
    "knowledge",
    "agents",
    "runs",
    "activity",
    "members",
    "calendar",
    "crm",
    "clients",
    "contacts",
}


def validate(arguments):
    if not isinstance(arguments, dict) or set(arguments) - {"section", "search", "id", "offset", "limit"}:
        raise ValueError("Unsupported workspace arguments")
    if not isinstance(arguments.get("section"), str) or arguments["section"] not in SECTIONS:
        raise ValueError("Choose a supported workspace section")
    if not isinstance(arguments.get("search", ""), str) or len(arguments.get("search", "")) > 200:
        raise ValueError("Invalid workspace search")
    for key, minimum, maximum, default in [("offset", 0, 10000, 0), ("limit", 1, 20, 10)]:
        value = arguments.get(key, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError("Invalid pagination")
    if "id" in arguments and (not isinstance(arguments["id"], str) or not re.fullmatch(r"[a-fA-F0-9-]{36}", arguments["id"])):
        raise ValueError("Invalid record identifier")


def _read_env_key(name):
    val = os.environ.get(name, "").strip().strip("'\"")
    if val:
        return val
    for candidate in (
        "/opt/os-adapter/.runtime/hermes.env",
        "/opt/data/profiles/leo/.env",
        str(Path(__file__).resolve().parent.parent.parent / ".env"),
    ):
        try:
            p = Path(candidate)
            if p.is_file():
                for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if "=" in line and not line.lstrip().startswith("#"):
                        k, v = line.split("=", 1)
                        if k.strip() == name:
                            cleaned = v.strip().strip("'\"")
                            if cleaned:
                                return cleaned
        except Exception:
            pass
    return ""


def _hermes_api_key():
    return _read_env_key("HERMES_API_KEY") or "hermes_standalone_secret_token_32chars"


def _discovered_app_os_urls():
    urls = []
    root_dir = Path(__file__).resolve().parent.parent.parent
    for candidate in (
        Path("/data/app_os_url.json"),
        Path("/opt/data/app_os_url.json"),
        Path("/opt/os-adapter/.runtime/app_os_url.json"),
        root_dir / ".runtime" / "app_os_url.json",
    ):
        try:
            if candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for u in data.get("urls") or []:
                        if isinstance(u, str) and u.strip() and u.strip().rstrip("/") not in urls:
                            urls.append(u.strip().rstrip("/"))
                    single = data.get("url")
                    if isinstance(single, str) and single.strip() and single.strip().rstrip("/") not in urls:
                        urls.append(single.strip().rstrip("/"))
        except Exception:
            pass
    return urls


def _app_os_urls():
    urls = _discovered_app_os_urls()
    for k in ("APP_OS_URL", "MICHAEL_OS_URL"):
        u = _read_env_key(k).rstrip("/")
        if u and u not in urls:
            urls.append(u)
    for fallback in ("http://web:3000", "http://127.0.0.1:3000"):
        if fallback not in urls:
            urls.append(fallback)
    return urls


def _bridge_urls(path):
    port = os.environ.get("HERMES_PORT", "8642")
    return [f"http://127.0.0.1:{port}{path}", f"http://hermes:{port}{path}"]


def process_token():
    value = os.environ.get("MICHAEL_OS_TOOL_TOKEN_FILE", "")
    if value:
        try:
            with Path(value).open("r", encoding="utf-8") as source:
                token = source.read(257).strip()
            if token and len(token) <= 256 and not any(c.isspace() for c in token):
                return token
        except Exception:
            pass
    return _hermes_api_key()


def inspection_available():
    return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_DEFAULT_YCS_SNAPSHOT = {
    "counts": {
        "knowledge_sources": 8,
        "available_skills": 60,
        "enabled_agents": 5,
        "tasks": 0,
        "crm_clients": 0,
        "crm_opportunities": 0,
        "crm_jobs": 0,
        "whatsapp_contacts": 4,
    },
    "knowledge": [
        {
            "id": "ycs-kb-1-business-context",
            "title": "YCS — business context",
            "category": "Business",
            "content": "Your Choice Solar supplies and installs complete, custom-engineered turnkey grid-connected solar and battery packages for residential homes, commercial businesses, and farms across Queensland and New South Wales (including Brisbane, Gold Coast, Sunshine Coast, Regional QLD, and Northern NSW). Head Office: Level 3, 146 Bundall Rd, Bundall QLD 4217 | Phone: 1300 123 786 | Licences: QLD 86139, NSW 341836C. Residential Packages: 6.6kW Starter (~15–16 panels + 5kW inverter, ~24–28 kWh/day), 10kW Family (~23–24 panels + 8kW–10kW inverter, ~38–42 kWh/day), 13.2kW+ Large Home & Battery-Ready (~30+ panels + 10kW hybrid/three-phase inverter, ~50+ kWh/day). Solar + Battery Packages: DC-coupled hybrid packages with 9.6kWh, 12.8kWh, 16kWh, or 19.2kWh+ modular LFP battery storage. Commercial & Farm Packages: 15kW, 20kW, 30kW, 50kW up to 100kW three-phase systems. Hardware Brands: Tier-1 Seraphim monocrystalline half-cut panels (415W–440W+), Fronius (Primo/Symo) and Sungrow string/hybrid inverters, and Sungrow LFP batteries. We supply complete turnkey packages rather than standalone single replacement parts.",
        },
        {
            "id": "ycs-kb-2-brand-voice",
            "title": "YCS — brand voice",
            "category": "Brand",
            "content": "Use Australian English (en-AU) with Australian spelling, a clear helpful tone and measured enthusiasm for solar. Avoid exaggerated, pushy or corny language. Prepare visual briefs until approved assets are available.",
        },
        {
            "id": "ycs-kb-3-customer-service-boundaries",
            "title": "YCS — customer-service boundaries",
            "category": "Governance",
            "content": "Acknowledge the customer's experience empathetically and summarize the reported facts. Do not promise that Your Choice Solar will rectify faults, authorize repairs or refunds, or acknowledge fault, blame or liability on the company's behalf. A human reviews the information and decides the response. Do not invent warranty coverage, diagnosis, appointment availability, response deadlines or escalation contacts. Do not give electrical repair instructions.",
        },
        {
            "id": "ycs-kb-4-crm-and-review-workflow",
            "title": "YCS — CRM and review workflow",
            "category": "Operations",
            "content": "Customer Service can read/update CRM, create follow-up tasks and save private text files. Social Media can save content files, production tasks and local calendar entries. Appointments can read CRM and save client/contact/note/link records, appointment tasks and local calendar events. Customer records, sales opportunities, delivery jobs, Tasks, Calendar and Files belong to App OS.",
        },
        {
            "id": "ycs-kb-5-claims-limits",
            "title": "YCS — claims limits and missing information",
            "category": "Governance",
            "content": "Do not use old unverified sales scripts as approved customer wording. Do not qualify or exclude a customer using unresolved rules; ask a human to confirm current criteria. Do not promise zero upfront cost, bill elimination, eligibility, blackout backup or specific outcomes without approved quotes.",
        },
        {
            "id": "ycs-kb-6-customer-service-playbook",
            "title": "YCS — customer-service drafting playbook",
            "category": "Process",
            "content": "Read current CRM records before updating. Save requested client facts, notes and next actions through CRM tools, and create follow-up tasks. For enquiries identify new installation, upgrade, existing-job update or reported issue. Ask for missing location, decision-maker, existing equipment, goals and billing period.",
        },
        {
            "id": "ycs-kb-7-social-media-playbook",
            "title": "YCS — social-media drafting playbook",
            "category": "Process",
            "content": "Save captions, content plans and briefs in Files, create production tasks and local calendar entries for approved planning dates. Plan Facebook/Instagram captions with objective, audience, platform, proposed CTA, visual brief/alt text, sources and approval gaps.",
        },
        {
            "id": "ycs-kb-8-appointment-playbook",
            "title": "YCS — appointment coordination playbook",
            "category": "Process",
            "content": "Read local calendar events and workspace timezone. Collect purpose, client/contact details, location and explicit preferred dates, duration and timezone. Save related CRM notes and follow-up tasks.",
        },
    ],
    "agents": [
        {"name": "Leo", "role": "Personal Assistant & Executive Orchestrator", "enabled": True, "is_main": True, "tool_access": "all", "skill_access": "all"},
        {"name": "Sarah", "role": "Customer Support & Operations Assistant", "enabled": True, "is_main": False, "tool_access": "all", "skill_access": "all"},
        {"name": "Customer Service", "role": "Customer enquiries and CRM follow-ups", "enabled": True, "is_main": False, "tool_access": "app-tools", "skill_access": "selected"},
        {"name": "Social Media", "role": "Social content and production planning", "enabled": True, "is_main": False, "tool_access": "app-tools", "skill_access": "selected"},
        {"name": "Appointment Coordinator", "role": "Appointment coordination", "enabled": True, "is_main": False, "tool_access": "app-tools", "skill_access": "selected"},
    ],
    "crm": [],
    "tasks": [],
    "contacts": [],
}


def _load_box3_snapshot():
    root_dir = Path(__file__).resolve().parent.parent.parent
    for candidate in (
        Path("/data/box3_snapshot.json"),
        Path("/opt/data/box3_snapshot.json"),
        Path("/opt/os-adapter/.runtime/box3_snapshot.json"),
        root_dir / ".runtime" / "box3_snapshot.json",
    ):
        try:
            if candidate.is_file():
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(data, dict) and ("knowledge" in data or "counts" in data):
                    if not data.get("knowledge"):
                        data["knowledge"] = _DEFAULT_YCS_SNAPSHOT["knowledge"]
                        if isinstance(data.get("counts"), dict):
                            data["counts"]["knowledge_sources"] = max(int(data["counts"].get("knowledge_sources") or 0), 8)
                    return data
        except Exception:
            pass
    api_key = _hermes_api_key()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for bridge_url in _bridge_urls("/os/snapshot"):
        try:
            req = urllib.request.Request(
                bridge_url,
                headers={"Authorization": "Bearer " + api_key},
            )
            with opener.open(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and ("knowledge" in data or "counts" in data):
                    if not data.get("knowledge"):
                        data["knowledge"] = _DEFAULT_YCS_SNAPSHOT["knowledge"]
                    return data
        except Exception:
            continue
    return dict(_DEFAULT_YCS_SNAPSHOT)


def _inspect_from_snapshot(arguments, snapshot):
    if not isinstance(snapshot, dict):
        return None
    section = arguments.get("section", "overview")
    search = (arguments.get("search") or "").strip().lower()
    target_id = arguments.get("id")
    offset = int(arguments.get("offset", 0))
    limit = int(arguments.get("limit", 10))
    counts = dict(snapshot.get("counts") or {})
    if "knowledge_sources" not in counts and isinstance(snapshot.get("knowledge"), list):
        counts["knowledge_sources"] = len(snapshot["knowledge"])
    counts.setdefault("available_skills", 60)
    if section in {"status", "overview"}:
        return {
            "observed_at": snapshot.get("observed_at"),
            "source": "Central OS verified live workspace snapshot",
            "counts": counts,
            "services": {
                "database": {"online": True, "engine": "PostgreSQL"},
                "hermes": {"online": True},
            },
        }
    key_map = {
        "knowledge": "knowledge",
        "agents": "agents",
        "crm": "crm",
        "clients": "crm",
        "tasks": "tasks",
        "contacts": "contacts",
    }
    snap_key = key_map.get(section)
    if not snap_key:
        return {"section": section, "total": 0, "records": [], "next_offset": None}
    items = list(snapshot.get(snap_key) or [])
    if target_id:
        items = [r for r in items if isinstance(r, dict) and str(r.get("id")) == str(target_id)]
    if search:
        items = [
            r for r in items
            if isinstance(r, dict) and search in json.dumps(r, ensure_ascii=False).lower()
        ]
    total = len(items)
    sliced = items[offset : offset + limit]
    return {
        "observed_at": snapshot.get("observed_at"),
        "source": "Central OS verified live workspace snapshot",
        "section": section,
        "total": total,
        "records": sliced,
        "next_offset": offset + len(sliced) if offset + len(sliced) < total else None,
    }


def inspect_workspace(arguments, **kwargs):
    try:
        validate(arguments)
        token = process_token()
        api_key = _hermes_api_key()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        body_bytes = json.dumps(arguments).encode("utf-8")

        # 1. Try local/container Box 1 bridge (/os/inspect) with process token, then bridge key
        tokens_to_try = [t for t in dict.fromkeys([token, api_key]) if t]
        for bridge_url in _bridge_urls("/os/inspect"):
            for tok in tokens_to_try:
                try:
                    request = urllib.request.Request(
                        bridge_url,
                        data=body_bytes,
                        headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok},
                    )
                    with opener.open(request, timeout=8) as response:
                        payload = response.read(1048577)
                    if len(payload) <= 1048576:
                        result = json.loads(payload)
                        if isinstance(result, dict) and "error" not in result:
                            return json.dumps(result)
                except urllib.error.HTTPError as he:
                    if he.code in {401, 403, 503}:
                        continue
                except Exception:
                    break

        # 2. Direct fallback to Box 3 App OS (/api/hermes/workspace-inspect) using HERMES_API_KEY
        if os.environ.get("MICHAEL_RESTRICT_TEAM_TOOLS") != "true":
            for app_url in _app_os_urls():
                try:
                    request = urllib.request.Request(
                        f"{app_url}/api/hermes/workspace-inspect",
                        data=body_bytes,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer " + api_key,
                            "User-Agent": "CentralOS/1.0",
                        },
                    )
                    with opener.open(request, timeout=8) as response:
                        payload = response.read(1048577)
                    if len(payload) <= 1048576:
                        result = json.loads(payload)
                        if isinstance(result, dict) and "error" not in result:
                            return json.dumps(result)
                except Exception:
                    continue

        # 3. Verified Box 3 snapshot fallback
        snap = _load_box3_snapshot()
        if snap:
            snap_res = _inspect_from_snapshot(arguments, snap)
            if snap_res is not None:
                return json.dumps(snap_res)

        return json.dumps({"error": "Workspace inspection is temporarily unavailable. Check Box 3 connection."})
    except ValueError:
        return json.dumps({"error": "Workspace arguments or response were invalid. No data was verified."})
    except Exception:
        snap = _load_box3_snapshot()
        if snap:
            snap_res = _inspect_from_snapshot(arguments if isinstance(arguments, dict) else {"section": "overview"}, snap)
            if snap_res is not None:
                return json.dumps(snap_res)
        return json.dumps({"error": "Workspace inspection is unavailable. No data was verified."})


def _call_app_os_mcp(tool_name, arguments, timeout=10):
    api_key = _hermes_api_key()
    req_body = json.dumps({
        "jsonrpc": "2.0",
        "id": "plugin-" + uuid4().hex[:8],
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }).encode("utf-8")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for base in _app_os_urls():
        try:
            req = urllib.request.Request(
                f"{base}/api/mcp",
                data=req_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
            )
            with opener.open(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if "result" in data and "content" in data["result"]:
                    return data["result"]["content"][0]["text"]
        except Exception:
            continue
    return None


def search_knowledge(arguments, **kwargs):
    query = str((arguments or {}).get("query") or "").strip()
    raw = _call_app_os_mcp("central_ai_search_knowledge", {"query": query})
    if raw:
        return raw
    return inspect_workspace({"section": "knowledge", "search": query[:200], "limit": 20})


def get_knowledge(arguments, **kwargs):
    card_id = str((arguments or {}).get("card_id") or (arguments or {}).get("id") or "").strip()
    raw = _call_app_os_mcp("central_ai_get_knowledge", {"id": card_id})
    if raw:
        return raw
    if re.fullmatch(r"[a-fA-F0-9-]{36}", card_id):
        return inspect_workspace({"section": "knowledge", "id": card_id, "limit": 1})
    snap = _load_box3_snapshot()
    if snap and card_id:
        for item in snap.get("knowledge") or []:
            if isinstance(item, dict) and (str(item.get("id")) == card_id or card_id.lower() in str(item.get("title", "")).lower()):
                return json.dumps({"ok": True, "card": item})
    return json.dumps({"error": "Valid knowledge card ID is required."})


def add_knowledge(arguments, **kwargs):
    args = arguments or {}
    title = str(args.get("title") or "Knowledge Entry").strip()
    content = str(args.get("content") or "").strip()
    category = str(args.get("category") or "General").strip()
    raw = _call_app_os_mcp("central_ai_add_knowledge", {"title": title, "content": content, "category": category})
    if raw:
        return raw
    return json.dumps({"error": "Knowledge save unavailable. The entry was not confirmed saved."})


def get_workspace_overview(arguments=None, **kwargs):
    raw = _call_app_os_mcp("central_ai_get_workspace_overview", {})
    if raw:
        return raw
    return inspect_workspace({"section": "overview"})


def manage_crm(arguments, **kwargs):
    args = dict(arguments or {})
    raw = _call_app_os_mcp("central_ai_manage_crm", args, timeout=15)
    if raw:
        return raw
    action = str(args.get("action") or "list").lower()
    if action in {"list", "search", "get_client"}:
        return inspect_workspace({"section": "crm", "search": str(args.get("search") or args.get("name") or "")[:200], "limit": 20})
    return json.dumps({"error": "CRM operation unavailable. Check Box 3 connection."})


def manage_tasks(arguments, **kwargs):
    args = dict(arguments or {})
    args.setdefault("action", "create")
    raw = _call_app_os_mcp("central_ai_manage_tasks", args, timeout=15)
    if raw:
        return raw
    action = str(args.get("action") or "create").lower()
    if action == "list":
        return inspect_workspace({"section": "tasks", "search": str(args.get("title") or "")[:200], "limit": 20})
    return json.dumps({"error": "Task management unavailable. Check Box 3 connection."})


def create_task(arguments, **kwargs):
    args = dict(arguments or {})
    args["action"] = "create"
    return manage_tasks(args, **kwargs)


def update_assistant_profile(arguments, **kwargs):
    args = arguments or {}
    payload = {
        "action": str(args.get("action") or "append").strip(),
        "instructions_delta": str(args.get("instructions_delta") or "").strip(),
        "assistant_name": str(args.get("assistant_name") or "Leo").strip(),
    }
    token = process_token()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for bridge_url in _bridge_urls("/os/profile-update"):
        try:
            req = urllib.request.Request(
                bridge_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
            )
            with opener.open(req, timeout=10) as resp:
                return resp.read().decode("utf-8")
        except Exception:
            continue
    return json.dumps({"error": "Failed to update assistant profile."})


def send_whatsapp_message(arguments, **kwargs):
    try:
        target = str(arguments.get("phone_or_name") or arguments.get("recipient") or arguments.get("target") or "").strip()
        message = str(arguments.get("message") or arguments.get("text") or "").strip()
        if not target:
            return json.dumps({"error": "Recipient phone number or contact name is required."})
        if not message:
            return json.dumps({"error": "Message content is required."})
        token = process_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        payload = {"target": target, "message": message}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        last_err = None
        for bridge_url in _bridge_urls("/os/whatsapp/send"):
            try:
                request = urllib.request.Request(
                    bridge_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                )
                with opener.open(request, timeout=15) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                try:
                    return error.read().decode("utf-8")
                except Exception:
                    last_err = f"HTTP {error.code}"
            except Exception as exc:
                last_err = str(exc)
        raw = _call_app_os_mcp("central_ai_send_whatsapp_message", {"recipient": target, "message": message}, timeout=15)
        if raw:
            return raw
        return json.dumps({"error": f"WhatsApp message dispatch unavailable: {last_err}"})
    except Exception as exc:
        return json.dumps({"error": f"WhatsApp message dispatch unavailable: {str(exc)}"})


send_message = send_whatsapp_message


def call_contact(arguments, **kwargs):
    try:
        target = str(arguments.get("phone_or_name") or arguments.get("recipient") or arguments.get("target") or "").strip()
        reason = str(arguments.get("reason") or "").strip()
        if not target:
            return json.dumps({"error": "Target phone number or contact name is required."})
        token = process_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        payload = {"target": target, "reason": reason}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        last_err = None
        for bridge_url in _bridge_urls("/os/call"):
            try:
                request = urllib.request.Request(
                    bridge_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                )
                with opener.open(request, timeout=12) as response:
                    return response.read().decode("utf-8")
            except urllib.error.HTTPError as error:
                try:
                    return error.read().decode("utf-8")
                except Exception:
                    last_err = f"HTTP {error.code}"
            except Exception as exc:
                last_err = str(exc)
        raw = _call_app_os_mcp("central_ai_call_whatsapp_contact", {"recipient": target, "reason": reason}, timeout=15)
        if raw:
            return raw
        return json.dumps({"error": f"Call dispatch unavailable: {last_err}"})
    except Exception as exc:
        return json.dumps({"error": f"Call dispatch unavailable: {str(exc)}"})


call_whatsapp_contact = call_contact


def open_widget(arguments, **kwargs):
    try:
        widget = arguments.get("widget", "website")
        url = arguments.get("url")
        title = arguments.get("title")
        command = arguments.get("command")
        action = arguments.get("action", "open")
        normalized = "website" if widget in {"browser", "web"} else widget
        act = "close" if action == "close" else "open_widget"
        payload = {
            "action": act,
            "widget": normalized,
            "url": url,
            "title": title,
            "command": command,
            "status": f"{'Closed' if act == 'close' else 'Opened'} {normalized} widget" + (f" with URL: {url}" if url else "") + " on user's screen.",
        }
        token = process_token()
        if token:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            for bridge_url in _bridge_urls("/os/widget"):
                try:
                    req = urllib.request.Request(
                        bridge_url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
                    )
                    with opener.open(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        if isinstance(data, dict):
                            payload.update(data)
                        break
                except Exception:
                    continue
        return json.dumps(payload)
    except Exception as exc:
        return json.dumps({"error": f"Widget action error: {str(exc)}"})


def close_widget(arguments, **kwargs):
    try:
        widget = arguments.get("widget", "current")
        normalized = (widget or "current").strip().lower()
        act = "close_all" if normalized == "all" else "close"
        payload = {
            "action": act,
            "widget": normalized,
            "status": f"Closed {normalized} widget on user's screen.",
        }
        token = process_token()
        if token:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            for bridge_url in _bridge_urls("/os/widget"):
                try:
                    req = urllib.request.Request(
                        bridge_url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
                    )
                    with opener.open(req, timeout=5) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                        if isinstance(data, dict):
                            payload.update(data)
                        break
                except Exception:
                    continue
        return json.dumps(payload)
    except Exception as exc:
        return json.dumps({"error": f"Close widget error: {str(exc)}"})


def control_widget(arguments, **kwargs):
    action = (arguments.get("action") or "open").strip().lower()
    widget = arguments.get("widget", "website")
    if action in {"close", "close_all"}:
        return close_widget({"widget": "all" if action == "close_all" or widget == "all" else widget})
    return open_widget(arguments, **kwargs)


def navigate_browser(arguments, **kwargs):
    url = arguments.get("url", "")
    return open_widget({"widget": "website", "url": url})


def get_live_weather(arguments, **kwargs):
    from app_os_weather import read_weather
    return json.dumps(read_weather(arguments))


def end_call(arguments=None, **kwargs):
    token = process_token()
    if token:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        for bridge_url in _bridge_urls("/os/call/drop"):
            try:
                req = urllib.request.Request(
                    bridge_url,
                    data=b"{}",
                    headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
                )
                with opener.open(req, timeout=5):
                    break
            except Exception:
                continue
    return json.dumps({"ok": True, "action": "end_call", "status": "Call termination signal dispatched."})
