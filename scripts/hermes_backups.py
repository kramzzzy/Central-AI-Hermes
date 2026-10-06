"""Native private relay to the dedicated maintenance service; no key goes to OS."""
import json
import os
import urllib.request
from pathlib import Path

class BackupError(Exception):
    def __init__(self,message,status=503):super().__init__(message);self.status=status

def handle_backup(body):
    if not os.environ.get('BACKUP_BASE_URL') or not os.environ.get('BACKUP_API_KEY_FILE'):
        raise BackupError('Private backup service is not configured. Enable the backup Compose overlay first.')
    key=Path(os.environ['BACKUP_API_KEY_FILE']).read_text().strip()
    if len(key)<32:raise BackupError('Backup service authorization is unavailable.')
    request=urllib.request.Request(os.environ['BACKUP_BASE_URL'].rstrip('/')+'/backups',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
    try:
        with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)
    except urllib.error.HTTPError as exc:
        raise BackupError('The backup service rejected this request. Refresh and check its configuration.',400 if exc.code==400 else 503) from None
    except Exception:raise BackupError('The private backup service is unavailable.') from None
