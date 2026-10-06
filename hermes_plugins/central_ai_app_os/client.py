"""Use the adapter's active per-process capability; never accept an actor or URL."""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

SECTIONS = {"status", "overview", "tasks", "knowledge", "agents", "runs", "activity", "members", "calendar"}


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


def process_token():
    value = os.environ.get("MICHAEL_OS_TOOL_TOKEN_FILE", "")
    if not value:
        return ""
    with Path(value).open("r", encoding="utf-8") as source:
        token = source.read(257).strip()
    if not token or len(token) > 256 or any(c.isspace() for c in token):
        return ""
    return token


def inspection_available():
    # No identity is persisted in plugin configuration. Tool admission is only
    # useful in a native process launched for an authenticated OS conversation.
    return bool(os.environ.get("MICHAEL_OS_TOOL_TOKEN_FILE"))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def inspect_workspace(arguments, **kwargs):
    try:
        validate(arguments)
        token = process_token()
        if not token:
            return json.dumps({"error": "Open an authenticated App OS conversation first. No workspace data was verified."})
        request = urllib.request.Request("http://127.0.0.1:8643/os/inspect",
            data=json.dumps(arguments).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        # Disable proxy discovery as well as redirects: the private capability
        # may only reach the adapter in this native runtime's network namespace.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=10) as response:
            payload = response.read(1048577)
        if len(payload) > 1048576:
            raise ValueError("Workspace response exceeded its limit")
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError("Invalid workspace response")
        return json.dumps(result)
    except urllib.error.HTTPError as error:
        return json.dumps({"error": "Workspace access expired or was revoked. Reconnect in App OS." if error.code in {401, 403} else "Workspace inspection failed. No healthy status was verified."})
    except ValueError:
        return json.dumps({"error": "Workspace arguments or response were invalid. No data was verified."})
    except (OSError, urllib.error.URLError):
        return json.dumps({"error": "Workspace inspection is unavailable. No data was verified."})
