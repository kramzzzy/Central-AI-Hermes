"""Private consistent native-state export. SQLite uses its online backup API.

Caller must hold the bridge task/settings/Google/routine/TeamPool locks and check
all active native conversations are idle. Never copy live SQLite WAL or PG files.
"""
import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

class ExportError(Exception):
    def __init__(self,message,status=503):super().__init__(message);self.status=status

def export_authorized(headers):
    path=os.environ.get('BACKUP_API_KEY_FILE')
    if not path or headers.get('Origin'):return False
    try:key=Path(path).read_text().strip()
    except OSError:return False
    return len(key)>=32 and hmac.compare_digest(headers.get('Authorization',''),'Bearer '+key)

def native_export(settings,root,destination):
    """Write an export tar to a supplied private tempfile, never a user-selected path."""
    profile_root=Path(settings['HERMES_PROFILE_ROOT']).resolve()
    root=Path(root).resolve()
    entries=[];total=0
    excluded={'.cache','.git','__pycache__','node_modules','lazy-packages','logs','log','installation'}
    roots=[('profile-root',profile_root),('adapter',root/'.runtime')]
    with tempfile.TemporaryDirectory(prefix='native-backup-') as temporary:
        staging=Path(temporary);os.chmod(staging,0o700)
        for prefix,folder in roots:
            if not folder.is_dir():raise ExportError('A required native state directory is unavailable.')
            for source in folder.rglob('*'):
                relative=source.relative_to(folder)
                if any(part in excluded for part in relative.parts) or not source.is_file():continue
                # Browser installs are recoverable binary caches. Preserve the
                # adjacent authentication/session state in .agent-browser.
                if prefix=='profile-root' and relative.parts[:2]==('.agent-browser','browsers'):continue
                if source.is_symlink():
                    # Flatten configuration symlinks only within the backend-owned
                    # profile tree. Recoverable contents, no host-path traversal.
                    resolved=source.resolve()
                    if prefix!='profile-root' or profile_root not in resolved.parents:continue
                if source.name.endswith(('.log','-wal','-shm','.tmp','.token','.lock','.pid')):continue
                if source.name.startswith(('native-os-tool-','native-google-tool-')):continue
                size=source.stat().st_size
                if size>128*1024*1024:raise ExportError('A native state file exceeds the bounded backup size.')
                total+=size
                if total>512*1024*1024:raise ExportError('Native state exceeds the bounded backup size.')
                name=prefix+'/'+relative.as_posix();target=staging/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with source.open('rb') as stream:sqlite=stream.read(16)==b'SQLite format 3\x00'
                if sqlite:
                    original=sqlite3.connect('file:'+quote(str(source))+'?mode=ro',uri=True,timeout=10)
                    copied=sqlite3.connect(target)
                    try:
                        deadline=time.monotonic()+30
                        def progress(*_):
                            if time.monotonic()>deadline:raise ExportError('Native database snapshot timed out; retry after current work finishes.',409)
                        original.backup(copied,pages=256,progress=progress)
                        if copied.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ExportError('A native database recovery check failed.')
                    finally:copied.close();original.close()
                else:shutil.copyfile(source,target)
                if target.stat().st_size>128*1024*1024:raise ExportError('A native database snapshot exceeds its bounded size.')
                os.chmod(target,0o600)
                with target.open('rb') as stream:checksum=hashlib.file_digest(stream,'sha256').hexdigest()
                entries.append({'path':name,'bytes':target.stat().st_size,'sha256':checksum,'sqlite':sqlite})
        for name in ('hermes_bridge_config','laya_api_key','hermes_memory_token','hindsight_api_key','backup_api_key'):
            source=Path('/run/secrets')/name
            if source.is_file():
                target=staging/'service-secrets'/name;target.parent.mkdir(exist_ok=True,mode=0o700);shutil.copyfile(source,target);os.chmod(target,0o600)
                entries.append({'path':'service-secrets/'+name,'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'sqlite':False})
        metadata={'kind':'native-backend','profiles':True,'profile_root_configuration':True,'sqlite_online_backup':True,'history_and_configuration':True,'excluded':['caches','browser binaries','logs','live-process tokens and locks','external symlinks','Hindsight PostgreSQL'],'entries':entries}
        manifest=staging/'manifest.json';manifest.write_text(json.dumps(metadata));os.chmod(manifest,0o600)
        with tarfile.open(destination,'w:gz') as archive:
            archive.add(manifest,arcname='manifest.json')
            for entry in entries:archive.add(staging/entry['path'],arcname=entry['path'],recursive=False)
    os.chmod(destination,0o600)
    return {'files':len(entries),'bytes':Path(destination).stat().st_size}
