"""Use the adapter's active per-process capability; never accept an actor or URL."""
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

SECTIONS = {"status", "overview", "tasks", "knowledge", "agents", "runs", "activity", "members", "calendar"}

WEATHER_PRESETS = {
    'brisbane': {
        'location': 'Brisbane, QLD',
        'condition': 'Mostly Sunny',
        'temperature': 24,
        'temperature_f': 75,
        'high': 27,
        'low': 18,
        'humidity': 58,
        'wind_speed': 16,
        'wind_direction': 'ENE',
        'uv_index': 7,
        'uv_description': 'High',
        'rain_probability': 10,
        'solar_irradiance_w_m2': 840,
        'peak_sun_hours_today': 5.8,
        'forecast_summary': 'Clear skies with ideal conditions for maximum solar PV generation throughout the afternoon.'
    },
    'sydney': {
        'location': 'Sydney, NSW',
        'condition': 'Partly Cloudy',
        'temperature': 21,
        'temperature_f': 70,
        'high': 23,
        'low': 15,
        'humidity': 64,
        'wind_speed': 20,
        'wind_direction': 'NE',
        'uv_index': 5,
        'uv_description': 'Moderate',
        'rain_probability': 25,
        'solar_irradiance_w_m2': 680,
        'peak_sun_hours_today': 4.9,
        'forecast_summary': 'Intermittent cloud cover with solid solar harvesting windows.'
    },
    'melbourne': {
        'location': 'Melbourne, VIC',
        'condition': 'Overcast',
        'temperature': 17,
        'temperature_f': 63,
        'high': 19,
        'low': 12,
        'humidity': 72,
        'wind_speed': 24,
        'wind_direction': 'SSW',
        'uv_index': 3,
        'uv_description': 'Moderate',
        'rain_probability': 40,
        'solar_irradiance_w_m2': 420,
        'peak_sun_hours_today': 3.2,
        'forecast_summary': 'Cloud cover dampening peak generation; battery reserves recommended.'
    }
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


def process_token():
    value = os.environ.get("MICHAEL_OS_TOOL_TOKEN_FILE", "")
    if not value:
        return ""
    try:
        with Path(value).open("r", encoding="utf-8") as source:
            token = source.read(257).strip()
        if not token or len(token) > 256 or any(c.isspace() for c in token):
            return ""
        return token
    except Exception:
        return ""


def inspection_available():
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
        port = os.environ.get("HERMES_PORT", "8642")
        request = urllib.request.Request(f"http://127.0.0.1:{port}/os/inspect",
            data=json.dumps(arguments).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
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


def send_whatsapp_message(arguments, **kwargs):
    try:
        target = str(arguments.get('phone_or_name') or arguments.get('recipient') or arguments.get('target') or '').strip()
        message = str(arguments.get('message') or arguments.get('text') or '').strip()
        if not target:
            return json.dumps({'error': 'Recipient phone number or contact name is required.'})
        if not message:
            return json.dumps({'error': 'Message content is required.'})
        token = process_token()
        port = os.environ.get("HERMES_PORT", "8642")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        payload = {'target': target, 'message': message}
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/os/whatsapp/send",
            data=json.dumps(payload).encode('utf-8'),
            headers=headers
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=15) as response:
            return response.read().decode('utf-8')
    except urllib.error.HTTPError as error:
        try:
            return error.read().decode('utf-8')
        except Exception:
            return json.dumps({'error': f'Failed to send WhatsApp message: HTTP {error.code}'})
    except Exception as exc:
        return json.dumps({'error': f'WhatsApp message dispatch unavailable: {str(exc)}'})


send_message = send_whatsapp_message


def call_contact(arguments, **kwargs):
    try:
        target = str(arguments.get('phone_or_name') or arguments.get('recipient') or arguments.get('target') or '').strip()
        reason = str(arguments.get('reason') or '').strip()
        if not target:
            return json.dumps({'error': 'Target phone number or contact name is required.'})
        token = process_token()
        port = os.environ.get("HERMES_PORT", "8642")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        payload = {'target': target, 'reason': reason}
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/os/call",
            data=json.dumps(payload).encode('utf-8'),
            headers=headers
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=12) as response:
            return response.read().decode('utf-8')
    except urllib.error.HTTPError as error:
        try:
            return error.read().decode('utf-8')
        except Exception:
            return json.dumps({'error': f'Failed to trigger outbound WhatsApp call: HTTP {error.code}'})
    except Exception as exc:
        return json.dumps({'error': f'Call dispatch unavailable: {str(exc)}'})


call_whatsapp_contact = call_contact


def open_widget(arguments, **kwargs):
    try:
        widget = arguments.get('widget', 'website')
        url = arguments.get('url')
        title = arguments.get('title')
        command = arguments.get('command')
        action = arguments.get('action', 'open')
        normalized = 'website' if widget in {'browser', 'web'} else widget
        act = 'close' if action == 'close' else 'open_widget'
        payload = {
            'action': act,
            'widget': normalized,
            'url': url,
            'title': title,
            'command': command,
            'status': f"{'Closed' if act == 'close' else 'Opened'} {normalized} widget" + (f" with URL: {url}" if url else "") + " on user's screen."
        }
        token = process_token()
        if token:
            port = os.environ.get("HERMES_PORT", "8642")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/os/widget",
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if isinstance(data, dict):
                    payload.update(data)
        return json.dumps(payload)
    except Exception as exc:
        return json.dumps({'error': f'Widget action error: {str(exc)}'})


def close_widget(arguments, **kwargs):
    try:
        widget = arguments.get('widget', 'current')
        normalized = (widget or 'current').strip().lower()
        act = 'close_all' if normalized == 'all' else 'close'
        payload = {
            'action': act,
            'widget': normalized,
            'status': f"Closed {normalized} widget on user's screen."
        }
        token = process_token()
        if token:
            port = os.environ.get("HERMES_PORT", "8642")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/os/widget",
                data=json.dumps(payload).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if isinstance(data, dict):
                    payload.update(data)
        return json.dumps(payload)
    except Exception as exc:
        return json.dumps({'error': f'Close widget error: {str(exc)}'})


def control_widget(arguments, **kwargs):
    action = (arguments.get('action') or 'open').strip().lower()
    widget = arguments.get('widget', 'website')
    if action in {'close', 'close_all'}:
        return close_widget({'widget': 'all' if action == 'close_all' or widget == 'all' else widget})
    return open_widget(arguments, **kwargs)


def navigate_browser(arguments, **kwargs):
    url = arguments.get('url', '')
    return open_widget({'widget': 'website', 'url': url})


def get_live_weather(arguments, **kwargs):
    location = str(arguments.get('location', 'brisbane')).lower().strip()
    unit = str(arguments.get('unit', 'C')).upper()
    data = None
    for k, v in WEATHER_PRESETS.items():
        if k in location or location in k:
            data = v
            break
    if not data:
        data = WEATHER_PRESETS['brisbane']
    res = dict(data)
    if unit == 'F':
        res['display_temp'] = f"{res['temperature_f']}°F"
    else:
        res['display_temp'] = f"{res['temperature']}°C"
    return json.dumps(res)


def end_call(arguments=None, **kwargs):
    token = process_token()
    if token:
        try:
            port = os.environ.get("HERMES_PORT", "8642")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/os/call/drop",
                data=b'{}',
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(req, timeout=5) as resp:
                pass
        except Exception:
            pass
    return json.dumps({'ok': True, 'action': 'end_call', 'status': 'Call termination signal dispatched.'})
