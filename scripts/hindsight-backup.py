"""Private Hindsight snapshots and recovery drills using matching PG binaries.

The only restore target is a disposable Unix-socket cluster. Never the live DB.
"""
import hashlib
import hmac
import json
import os
import pwd
import re
import shutil
import subprocess
import tarfile
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path

MAX_EXPORT=512*1024*1024
guard=threading.Lock()

def context():
    from pg0 import Pg0
    if os.environ.get('HINDSIGHT_API_DATABASE_URL'):
        raise RuntimeError('External memory database needs its own backup configuration.')
    info=Pg0(name='hindsight').info()
    if not info.running or not re.fullmatch(r'[0-9.]{1,24}',info.version):raise RuntimeError('Memory database is unavailable.')
    binaries=Path('/home/hindsight/.pg0/installation')/info.version/'bin'
    url=urllib.parse.urlsplit(info.uri)
    env=dict(os.environ,PGHOST=url.hostname or '127.0.0.1',PGPORT=str(url.port or 5432),PGUSER=urllib.parse.unquote(url.username or info.username),PGPASSWORD=urllib.parse.unquote(url.password or ''),PGDATABASE=urllib.parse.unquote(url.path.lstrip('/')),PGCONNECT_TIMEOUT='10')
    env['LD_LIBRARY_PATH']=str(binaries.parent/'lib')+(':'+env['LD_LIBRARY_PATH'] if env.get('LD_LIBRARY_PATH') else '')
    def command(name,args,variables=env,timeout=300):
        result=subprocess.run([str(binaries/name),*args],env=variables,capture_output=True,text=True,timeout=timeout)
        if result.returncode:raise RuntimeError('Memory dump or isolated recovery check failed.')
        return result.stdout
    return info,env,command

def recovery(dump,command,env):
    with tempfile.TemporaryDirectory(prefix='hindsight-recovery-') as temporary:
        root=Path(temporary);os.chmod(root,0o700);cluster=root/'postgres';socket=root/'socket';socket.mkdir(mode=0o700)
        command('initdb',['-D',str(cluster),'--no-locale','--encoding=UTF8','--auth-local=trust'])
        options="-c listen_addresses='' -c unix_socket_directories="+str(socket)+" -c shared_buffers=16MB -c max_connections=10"
        command('pg_ctl',['-D',str(cluster),'-o',options,'-l',str(cluster/'server.log'),'-w','start'])
        isolated=dict(env,PGHOST=str(socket),PGPORT='5432',PGDATABASE='hindsight_recovery',PGUSER=pwd.getpwuid(os.geteuid()).pw_name,PGPASSWORD='',PGSSLMODE='disable')
        try:
            command('createdb',['hindsight_recovery'],isolated)
            command('pg_restore',['--exit-on-error','--no-owner','--no-acl','--dbname=hindsight_recovery',str(dump)],isolated)
            tables=json.loads(command('psql',['-At','-c',"SELECT coalesce(json_agg(format('%I.%I',schemaname,tablename)),'[]'::json) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')"],isolated))
            if not tables:raise RuntimeError('Restored memory database is empty.')
            for table in tables:command('psql',['-At','-c','SELECT count(*) FROM '+table],isolated)
            return len(tables)
        finally:command('pg_ctl',['-D',str(cluster),'-m','immediate','-w','stop'])

def filehash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def export(destination):
    info,env,command=context()
    with tempfile.TemporaryDirectory(prefix='hindsight-dump-') as temporary:
        root=Path(temporary);os.chmod(root,0o700);dump=root/'hindsight.dump'
        command('pg_dump',['--format=custom','--no-owner','--no-acl','--file='+str(dump)])
        if dump.stat().st_size>MAX_EXPORT:raise RuntimeError('Memory backup exceeds its configured bound.')
        tables=recovery(dump,command,env)
        metadata={'kind':'hindsight','postgres_version':info.version,'verified_restore':True,'tables':tables,'sha256':filehash(dump)}
        manifest=root/'manifest.json';manifest.write_text(json.dumps(metadata));os.chmod(manifest,0o600)
        with tarfile.open(destination,'w:gz') as archive:
            archive.add(dump,arcname='hindsight.dump');archive.add(manifest,arcname='manifest.json')
            for name in ('hindsight_api_key','hindsight_ui_key','hermes_memory_token'):
                secret=Path('/run/secrets')/name
                if name=='hindsight_api_key' and os.environ.get('HINDSIGHT_API_TENANT_API_KEY'):
                    secret=root/name
                    secret.write_text(os.environ['HINDSIGHT_API_TENANT_API_KEY'])
                    os.chmod(secret,0o600)
                if secret.is_file():archive.add(secret,arcname='service-secrets/'+name,recursive=False)
    os.chmod(destination,0o600)

def verify(source):
    info,env,command=context()
    with tempfile.TemporaryDirectory(prefix='hindsight-verify-') as temporary:
        root=Path(temporary);dump=root/'hindsight.dump'
        with tarfile.open(source,'r:gz') as archive:
            members=archive.getmembers()
            if len(members)>10 or len({m.name for m in members})!=len(members) or any(not m.isfile() or m.size>MAX_EXPORT for m in members):raise RuntimeError('Invalid memory snapshot.')
            manifest=archive.getmember('manifest.json')
            if manifest.size>4096:raise RuntimeError('Invalid memory manifest.')
            metadata=json.load(archive.extractfile(manifest))
            if metadata.get('kind')!='hindsight' or metadata.get('postgres_version')!=info.version:raise RuntimeError('Recovery requires matching memory PostgreSQL binaries.')
            member=archive.getmember('hindsight.dump')
            with archive.extractfile(member) as incoming,dump.open('xb') as outgoing:shutil.copyfileobj(incoming,outgoing,1024*1024)
        if filehash(dump)!=metadata.get('sha256'):raise RuntimeError('Memory snapshot checksum differs.')
        return {'verified_restore':True,'postgres_version':info.version,'tables':recovery(dump,command,env)}

def serve():
    key=Path(os.environ['HINDSIGHT_BACKUP_KEY_FILE']).read_text().strip()
    if len(key)<32:raise RuntimeError('Private backup key is missing.')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,data):
            raw=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_POST(self):
            if self.path not in {'/backup/export','/backup/verify'}:return self.reply(404,{'error':'Not found.'})
            if self.headers.get('Origin') or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+key):return self.reply(401,{'error':'Unauthorized.'})
            if not guard.acquire(blocking=False):return self.reply(409,{'error':'Memory backup is already running.'})
            sent=False
            try:
                with tempfile.TemporaryDirectory(prefix='memory-export-') as temporary:
                    path=Path(temporary)/'export.tar.gz'
                    if self.path=='/backup/verify':
                        length=int(self.headers.get('Content-Length','0'))
                        if not 0<length<=MAX_EXPORT:return self.reply(413,{'error':'Invalid memory snapshot length.'})
                        self.connection.settimeout(60)
                        with path.open('xb') as output:
                            remaining=length
                            while remaining:
                                chunk=self.rfile.read(min(remaining,1024*1024))
                                if not chunk:raise RuntimeError('Incomplete memory snapshot.')
                                output.write(chunk);remaining-=len(chunk)
                        return self.reply(200,verify(path))
                    export(path)
                    self.send_response(200);self.send_header('Content-Type','application/gzip');self.send_header('Content-Length',str(path.stat().st_size));self.end_headers();sent=True
                    with path.open('rb') as stream:
                        while chunk:=stream.read(1024*1024):self.wfile.write(chunk)
            except Exception:
                if not sent:self.reply(503,{'error':'Memory snapshot or isolated recovery check failed.'})
            finally:guard.release()
    ThreadingHTTPServer(('0.0.0.0',8645),Handler).serve_forever()

if __name__=='__main__':serve()
