"""Read service credentials from Docker secrets, then start the official server."""
import os
import subprocess
from pathlib import Path

for variable, filename in {
    'HINDSIGHT_API_LLM_API_KEY': 'hermes_memory_token',
    'HINDSIGHT_API_TENANT_API_KEY': 'hindsight_api_key',
    'HINDSIGHT_CP_DATAPLANE_API_KEY': 'hindsight_api_key',
    'HINDSIGHT_CP_ACCESS_KEY': 'hindsight_ui_key',
}.items():
    value = Path('/run/secrets/' + filename).read_text(encoding='utf-8').strip()
    if len(value) < 32:
        raise RuntimeError('A generated private service key is required')
    os.environ[variable] = value
if os.environ.get('HINDSIGHT_BACKUP_KEY_FILE'):
    # The helper survives this exec and is confined to the private Docker network.
    # It never starts/stops the live embedded database.
    subprocess.Popen(['/app/api/.venv/bin/python','/app/hindsight-backup.py'])
os.execv('/app/start-all.sh', ['/app/start-all.sh'])
