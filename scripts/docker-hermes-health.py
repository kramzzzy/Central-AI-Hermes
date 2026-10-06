import json
import os
import urllib.request
from pathlib import Path
settings = dict(line.split('=', 1) for line in Path(os.environ['HERMES_BRIDGE_CONFIG']).read_text().splitlines() if '=' in line and not line.startswith('#'))
from hermes_installation import restore_installation
restore_installation(settings,Path(__file__).resolve().parent.parent)
request = urllib.request.Request('http://127.0.0.1:8643/health', headers={'Authorization': 'Bearer ' + settings['HERMES_API_KEY']})
with urllib.request.urlopen(request, timeout=3) as response:
    result = json.load(response)
assert result['hermes'] and result['runtime_host'] == 'docker' and result['runtime_profile'] == settings['HERMES_PROFILE']
