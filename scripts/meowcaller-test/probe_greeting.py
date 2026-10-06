"""Generate the explicitly requested team's brief call-check introduction.

Reads native speech credentials privately; no native chat/session is created.
"""
import ast
import json
import urllib.request
from pathlib import Path
from dotenv import dotenv_values

source = ast.parse(Path('/opt/setup/whatsapp-call-voice.py').read_text())
speech_function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'speech')
scope = {'json': json, 'urllib': urllib, 'config': dotenv_values('/opt/data/profiles/leo/.env')}
exec(compile(ast.Module(body=[speech_function], type_ignores=[]), '<native-speech-function>', 'exec'), scope)
audio = scope['speech']("Hi, this is Leo, the AI assistant for the team. Mark asked me to check if you can receive this WhatsApp call. This is a quick connection test. Thank you.")
target = Path('/data/team-call-probe.pcm')
target.write_bytes(audio)
target.chmod(0o600)
print(json.dumps({'greeting_ready': True, 'duration_seconds': round(len(audio) / 32000, 2)}))
