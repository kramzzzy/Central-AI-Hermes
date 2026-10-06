"""Private encrypted PostgreSQL snapshots with real isolated restore drills.

Independent app, native-state and memory snapshots are packaged together.
Component coverage is recorded honestly; no endpoint restores live databases.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import tempfile
import tarfile
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from uuid import UUID,uuid4
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

MAGIC=b'AIOSBKP1'
CHUNK=4*1024*1024
MAX_COMPONENT=512*1024*1024

def filehash(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def archive_manifest(path,kind):
    """Check bounded archive bytes/paths/checksums without trusting tar extraction."""
    with tarfile.open(path,'r:gz') as archive:
        members=archive.getmembers()
        if len(members)>50000 or len({m.name for m in members})!=len(members) or sum(m.size for m in members)>MAX_COMPONENT:
            raise BackupError('The component archive exceeds its bounds.')
        if any(not m.isfile() or m.name.startswith('/') or '..' in Path(m.name).parts or m.size>MAX_COMPONENT for m in members):
            raise BackupError('Invalid component archive entries.')
        manifest=archive.getmember('manifest.json')
        if manifest.size>10*1024*1024:raise BackupError('Invalid component manifest.')
        metadata=json.load(archive.extractfile(manifest))
        if metadata.get('kind')!=kind:raise BackupError('Unexpected component kind.')
        if kind=='native-backend':
            entries=metadata.get('entries')
            if not isinstance(entries,list) or {m.name for m in members}!={'manifest.json',*[e['path'] for e in entries]}:
                raise BackupError('Incomplete native-state manifest.')
            for entry in entries:
                member=archive.getmember(entry['path'])
                if member.size!=entry['bytes']:raise BackupError('Native-state file size differs.')
                with archive.extractfile(member) as stream:
                    if hashlib.file_digest(stream,'sha256').hexdigest()!=entry['sha256']:raise BackupError('Native-state checksum differs.')
                if entry.get('sqlite'):
                    with tempfile.TemporaryDirectory(prefix='native-sqlite-drill-') as folder:
                        copy=Path(folder)/'state.sqlite'
                        with archive.extractfile(member) as incoming,copy.open('xb') as outgoing:shutil.copyfileobj(incoming,outgoing,1024*1024)
                        connection=sqlite3.connect('file:'+urllib.parse.quote(str(copy))+'?mode=ro',uri=True)
                        try:
                            if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise BackupError('Native database recovery check failed.')
                        finally:connection.close()
        elif kind=='hindsight':
            if not metadata.get('verified_restore') or not re.fullmatch(r'[0-9.]{1,24}',metadata.get('postgres_version','')):
                raise BackupError('Memory snapshot lacks a matching restore check.')
            with archive.extractfile('hindsight.dump') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=metadata.get('sha256'):raise BackupError('Memory checksum differs.')
        return metadata

def encrypt(source,destination,key):
    salt=os.urandom(32);header=MAGIC+salt
    cipher=AESGCM(HKDF(algorithm=hashes.SHA256(),length=32,salt=salt,info=MAGIC).derive(key))
    with open(source,'rb') as src,open(destination,'xb') as dst:
        dst.write(header);index=0
        while True:
            chunk=src.read(CHUNK);nonce=index.to_bytes(12,'big')
            final=not chunk
            payload=cipher.encrypt(nonce,chunk,header+nonce+bytes([final]))
            dst.write(struct.pack('>I',len(payload))+bytes([final])+payload)
            index+=1
            if final:break
    os.chmod(destination,0o600)

def decrypt(source,destination,key):
    with open(source,'rb') as src,open(destination,'xb') as dst:
        header=src.read(40)
        if len(header)!=40 or header[:8]!=MAGIC:raise ValueError('Invalid encrypted backup.')
        cipher=AESGCM(HKDF(algorithm=hashes.SHA256(),length=32,salt=header[8:],info=MAGIC).derive(key));index=0
        while True:
            metadata=src.read(5)
            if len(metadata)!=5:raise ValueError('Truncated encrypted backup.')
            length=struct.unpack('>I',metadata[:4])[0];final=metadata[4]
            if final not in {0,1} or not 16<=length<=CHUNK+16:raise ValueError('Invalid encrypted backup chunk.')
            payload=src.read(length)
            if len(payload)!=length:raise ValueError('Truncated encrypted backup.')
            nonce=index.to_bytes(12,'big')
            chunk=cipher.decrypt(nonce,payload,header+nonce+bytes([final]))
            if final:
                if chunk or src.read(1):raise ValueError('Invalid encrypted backup end.')
                break
            dst.write(chunk);index+=1
    os.chmod(destination,0o600)


class BackupError(Exception):pass

class Backups:
    def __init__(self,root,key,env=None,clock=time.time,runner=None):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.key=key;self.env=dict(env or os.environ);self.clock=clock;self.guard=threading.Lock();self.runner=runner or self.snapshot
        self.schema=self.env.get('DATABASE_SCHEMA','public')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}',self.schema) or self.schema.startswith('pg_'):raise BackupError('Invalid application schema.')
        self.path=self.root/'state.sqlite'
        with self.database() as db:
            db.executescript("CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY,enabled INTEGER,interval_hours INTEGER,retain INTEGER,next_at REAL); INSERT OR IGNORE INTO config VALUES(1,0,24,7,NULL); CREATE TABLE IF NOT EXISTS backups(id TEXT PRIMARY KEY,created REAL,state TEXT,bytes INTEGER,verified REAL,message TEXT);")
            if 'coverage' not in {row[1] for row in db.execute('PRAGMA table_info(backups)')}:
                db.execute("ALTER TABLE backups ADD COLUMN coverage TEXT")
            db.execute("UPDATE backups SET state='failed',message='Backup service was interrupted. The last successful snapshot is retained.' WHERE state IN ('running','verifying')")
        os.chmod(self.path,0o600)

    @contextmanager
    def database(self):
        connection=sqlite3.connect(self.path,timeout=10);connection.row_factory=sqlite3.Row
        try:
            with connection:yield connection
        finally:connection.close()

    def status(self):
        with self.database() as db:
            config=dict(db.execute('SELECT * FROM config WHERE id=1').fetchone());rows=[dict(r) for r in db.execute('SELECT * FROM backups ORDER BY created DESC LIMIT 50')]
        for row in rows:row['coverage']=json.loads(row['coverage']) if row.get('coverage') else {'application':{'state':'unknown'}}
        return {'configured':True,'coverage':'independent application, backend state and memory snapshots; per-snapshot coverage below','encrypted':True,'busy':self.guard.locked(),'config':config,'backups':rows,'offsite':False,'protocol':2}

    def handle(self,body):
        if not isinstance(body,dict):raise BackupError('Invalid backup request.')
        action=body.get('action')
        if action=='status' and set(body)=={'action'}:return self.status()
        if action=='configure' and set(body)=={'action','enabled','interval_hours','retain'}:
            if type(body['enabled']) is not bool or type(body['interval_hours']) is not int or not 6<=body['interval_hours']<=168 or type(body['retain']) is not int or not 2<=body['retain']<=30:raise BackupError('Choose 6–168 hours and 2–30 retained snapshots.')
            with self.database() as db:db.execute('UPDATE config SET enabled=?,interval_hours=?,retain=?,next_at=? WHERE id=1',(body['enabled'],body['interval_hours'],body['retain'],self.clock()+body['interval_hours']*3600 if body['enabled'] else None))
            return self.status()
        if action=='run' and set(body)=={'action'}:return self.start()
        if action=='verify' and set(body)=={'action','id'}:
            try:identifier=str(UUID(body['id']))
            except Exception:raise BackupError('Choose an existing backup.') from None
            with self.database() as db:row=db.execute("SELECT * FROM backups WHERE id=? AND state='completed'",(identifier,)).fetchone()
            if not row:raise BackupError('Choose a completed backup.')
            return self.start(identifier)
        raise BackupError('Unsupported backup request.')

    def start(self,verify_id=None,asynchronous=True):
        if not self.guard.acquire(blocking=False):raise BackupError('Another backup or restore drill is running.')
        identifier=verify_id or str(uuid4())
        with self.database() as db:
            if verify_id:db.execute("UPDATE backups SET state='verifying' WHERE id=?",(identifier,))
            else:db.execute("INSERT INTO backups(id,created,state,bytes,verified,message) VALUES(?,?,'running',0,NULL,'Creating an encrypted snapshot and checking recovery.')",(identifier,self.clock()))
        def work():
            try:
                size=self.runner(identifier,bool(verify_id))
                with self.database() as db:
                    row=db.execute('SELECT coverage FROM backups WHERE id=?',(identifier,)).fetchone()
                    coverage=json.loads(row['coverage']) if row and row['coverage'] else {}
                    partial=any(value.get('state')!='verified' for value in coverage.values())
                    message='Application recovery checked. Some components are unavailable; review coverage.' if partial else 'All included components passed isolated recovery checks.'
                    db.execute("UPDATE backups SET state='completed',bytes=?,verified=?,message=? WHERE id=?",(size,self.clock(),message,identifier))
                self.prune()
            except Exception:
                with self.database() as db:
                    db.execute("UPDATE backups SET state=?,verified=NULL,message='Backup or restore drill failed. Earlier snapshots were retained. Check service logs privately.' WHERE id=?",('completed' if verify_id else 'failed',identifier))
                # No command output (which may contain record text or credentials) in UI/logs.
                print('backup_or_restore_drill_failed',flush=True)
            finally:self.guard.release()
        if asynchronous:threading.Thread(target=work,daemon=True).start()
        else:work()
        return {'accepted':True,'id':identifier}

    def tick(self):
        with self.database() as db:config=db.execute('SELECT * FROM config WHERE id=1').fetchone()
        if not config['enabled'] or not config['next_at'] or config['next_at']>self.clock() or self.guard.locked():return
        # Persist the future slot first: failed jobs never cause a retry storm.
        with self.database() as db:db.execute('UPDATE config SET next_at=? WHERE id=1',(self.clock()+config['interval_hours']*3600,))
        self.start()

    def prune(self):
        with self.database() as db:
            retain=db.execute('SELECT retain FROM config WHERE id=1').fetchone()[0]
            # A partial app-only snapshot must not evict the last recoverable
            # native profile or memory archive. Preserve the newest checked
            # snapshot for each component beyond the ordinary retention count.
            protected=set();components=set()
            for candidate in db.execute("SELECT id,coverage,verified FROM backups WHERE state='completed' ORDER BY created DESC"):
                coverage=json.loads(candidate['coverage']) if candidate['coverage'] else {'application':{'state':'verified' if candidate['verified'] else 'unknown'}}
                for component,state in coverage.items():
                    if component not in components and state.get('state')=='verified':
                        protected.add(candidate['id']);components.add(component)
            rows=db.execute("SELECT id FROM backups WHERE state='completed' ORDER BY created DESC LIMIT -1 OFFSET ?",(retain,)).fetchall()
            for row in rows:
                if row['id'] in protected:continue
                path=self.root/(str(UUID(row['id']))+'.dump.enc')
                if path.parent.resolve()!=self.root.resolve() or path.is_symlink():raise BackupError('Invalid backup path.')
                path.unlink(missing_ok=True);db.execute('DELETE FROM backups WHERE id=?',(row['id'],))
            db.execute("DELETE FROM backups WHERE state='failed' AND created<?",(self.clock()-30*86400,))

    def connection(self):
        url=self.env.get('BACKUP_DATABASE_URL') or self.env.get('DATABASE_URL')
        try:
            parsed=urllib.parse.urlsplit(url)
            if parsed.scheme not in {'postgres','postgresql'} or not parsed.hostname or not parsed.path.strip('/'):raise ValueError()
            env=dict(self.env,PGHOST=parsed.hostname,PGPORT=str(parsed.port or 5432),PGUSER=urllib.parse.unquote(parsed.username or ''),PGPASSWORD=urllib.parse.unquote(parsed.password or ''),PGDATABASE=urllib.parse.unquote(parsed.path[1:]),PGCONNECT_TIMEOUT='10')
            ssl=urllib.parse.parse_qs(parsed.query).get('sslmode')
            if ssl:env['PGSSLMODE']=ssl[0]
            return env
        except Exception:raise BackupError('Configure a private database backup connection.') from None

    def command(self,args,env=None,timeout=180):
        result=subprocess.run(args,env=env or self.env,capture_output=True,text=True,timeout=timeout)
        if result.returncode:raise BackupError('Database backup verification command failed.')
        return result.stdout

    def restore_application(self,dump,work,expected_schema=None):
            # Restore to a separate local cluster with no TCP listener, never the live host.
            shutil.chown(dump,group='postgres');os.chmod(dump,0o640)
            postgres=work/'postgres';socket=work/'socket';postgres.mkdir();socket.mkdir();shutil.chown(postgres,user='postgres',group='postgres');shutil.chown(socket,user='postgres',group='postgres')
            self.command(['gosu','postgres','initdb','-D',str(postgres),'--no-locale','--encoding=UTF8','--auth-local=trust'])
            options="-c listen_addresses='' -c unix_socket_directories="+str(socket)+" -c max_connections=10 -c shared_buffers=16MB"
            self.command(['gosu','postgres','pg_ctl','-D',str(postgres),'-o',options,'-l',str(postgres/'server.log'),'-w','start'])
            isolated=dict(self.env,PGHOST=str(socket),PGPORT='5432',PGUSER='postgres',PGDATABASE='aios_restore',PGPASSWORD='',PGSSLMODE='disable')
            try:
                self.command(['createdb','aios_restore'],isolated)
                # Schema-selected dumps explicitly CREATE SCHEMA public. A new
                # database already has it, so remove only this disposable copy.
                self.command(['psql','-v','ON_ERROR_STOP=1','-c','DROP SCHEMA public CASCADE'],isolated)
                self.command(['pg_restore','--exit-on-error','--no-owner','--no-acl','--dbname=aios_restore',str(dump)],isolated,timeout=300)
                # Existing encrypted snapshots can predate a public -> aios
                # migration. Discover their schema only inside the disposable
                # restored cluster; never modify the live database/search path.
                schemas=json.loads(self.command(['psql','-At','-c',"SELECT coalesce(json_agg(schemaname),'[]'::json) FROM (SELECT schemaname FROM pg_tables WHERE tablename IN ('user','organizations') GROUP BY schemaname HAVING count(DISTINCT tablename)=2) s"],isolated))
                if len(schemas)!=1 or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}',schemas[0]) or schemas[0].startswith('pg_'):
                    raise BackupError('The restored application schema is ambiguous.')
                schema=schemas[0]
                if expected_schema is not None and schema!=expected_schema:
                    raise BackupError('The restored application schema differs from the snapshot metadata.')
                # Actually read every restored application table. Binary attachments/auth
                # are included by pg_dump; constraints are recreated by pg_restore.
                tables=json.loads(self.command(['psql','-At','-c',"SELECT coalesce(json_agg(tablename),'[]'::json) FROM pg_tables WHERE schemaname='"+schema+"'"],isolated))
                if not tables:raise BackupError('The restored application schema is empty.')
                counts={}
                for name in tables:
                    qualified='"'+schema+'"."'+name.replace('"','""')+'"'
                    counts[name]=int(self.command(['psql','-At','-c','SELECT count(*) FROM '+qualified],isolated))
                if 'organizations' not in counts or 'user' not in counts:raise BackupError('Required application tables are missing from the restored snapshot.')
            finally:
                self.command(['gosu','postgres','pg_ctl','-D',str(postgres),'-m','immediate','-w','stop'])
            return counts

    def component_request(self,url,destination=None,source=None):
        """URLs are operator configuration only; private credentials never reach UI."""
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='http' or parsed.hostname not in {'hermes','os-adapter','hindsight'} or parsed.username or parsed.password:
            raise BackupError('Invalid private component endpoint.')
        key_path=self.env.get('BACKUP_API_KEY_FILE','/run/secrets/backup_api_key')
        key=Path(key_path).read_text().strip()
        if len(key)<32:raise BackupError('Private backup service key is unavailable.')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args):raise BackupError('Unexpected component redirect.')
        opener=urllib.request.build_opener(NoRedirect())
        incoming=source.open('rb') if source else None
        try:
            request=urllib.request.Request(url,data=incoming if incoming else b'{}',headers={'Authorization':'Bearer '+key,'Content-Type':'application/gzip' if incoming else 'application/json','Content-Length':str(source.stat().st_size) if source else '2'},method='POST')
            with opener.open(request,timeout=900) as response:
                if destination:
                    length=int(response.headers.get('Content-Length','0'))
                    if not 0<length<=MAX_COMPONENT:raise BackupError('Invalid component response length.')
                    with destination.open('xb') as output:
                        remaining=length
                        while remaining:
                            chunk=response.read(min(remaining,1024*1024))
                            if not chunk:raise BackupError('Incomplete component response.')
                            output.write(chunk);remaining-=len(chunk)
                    os.chmod(destination,0o600);return None
                result=json.loads(response.read(4097))
                if not result.get('verified_restore'):raise BackupError('Memory restore was not verified.')
                return result
        finally:
            if incoming:incoming.close()

    def snapshot(self,identifier,verify=False):
        destination=self.root/(identifier+'.dump.enc')
        if destination.is_symlink():raise BackupError('Invalid backup path.')
        coverage={};snapshot_schema=None if verify else self.schema
        with tempfile.TemporaryDirectory(prefix='aios-restore-') as folder:
            work=Path(folder);shutil.chown(work,group='postgres');os.chmod(work,0o750);dump=work/'database.dump';bundle=work/'bundle.tar';components={}
            if verify:
                decrypt(destination,bundle,self.key)
                with bundle.open('rb') as stream:legacy=stream.read(5)==b'PGDMP'
                if legacy:
                    bundle.rename(dump)
                    coverage={'application':{'state':'verified'},'native':{'state':'not_in_snapshot'},'memory':{'state':'not_in_snapshot'}}
                else:
                    with tarfile.open(bundle,'r:') as archive:
                        members=archive.getmembers()
                        if len(members)>4 or len({m.name for m in members})!=len(members) or any(not m.isfile() or m.name not in {'manifest.json','application.dump','native.tar.gz','memory.tar.gz'} or m.size>MAX_COMPONENT for m in members):raise BackupError('Invalid backup bundle.')
                        manifest=archive.getmember('manifest.json')
                        if manifest.size>65536:raise BackupError('Invalid backup manifest.')
                        metadata=json.load(archive.extractfile(manifest));coverage=metadata['coverage']
                        if metadata.get('kind')!='os-backup-bundle':raise BackupError('Invalid backup bundle kind.')
                        snapshot_schema=metadata.get('application_schema')
                        for member in members:
                            if member.name=='manifest.json':continue
                            target=dump if member.name=='application.dump' else work/member.name
                            with archive.extractfile(member) as incoming,target.open('xb') as output:shutil.copyfileobj(incoming,output,1024*1024)
                            if filehash(target)!=metadata['sha256'].get(member.name):raise BackupError('Backup component checksum differs.')
                            if member.name!='application.dump':components[member.name]=target
            else:
                self.command(['pg_dump','--format=custom','--no-owner','--no-acl','--schema='+self.schema,'--file='+str(dump)],self.connection(),timeout=300)
            if dump.stat().st_size>MAX_COMPONENT:raise BackupError('Application snapshot exceeds its configured bound.')
            self.restore_application(dump,work,snapshot_schema)
            coverage['application']={'state':'verified','checked':self.clock()}
            for kind,key,env_name in [('native','native-backend','BACKUP_NATIVE_URL'),('memory','hindsight','BACKUP_MEMORY_URL')]:
                source=components.get(kind+'.tar.gz');url=self.env.get(env_name)
                if verify and not source:continue
                if not url:
                    coverage[kind]={'state':'not_configured' if not verify else 'recovery_unavailable'}
                    continue
                try:
                    if not verify:
                        source=work/(kind+'.tar.gz');self.component_request(url,destination=source)
                    archive_manifest(source,key)
                    if kind=='memory' and verify:self.component_request(url.rsplit('/',1)[0]+'/verify',source=source)
                    components[kind+'.tar.gz']=source
                    coverage[kind]={'state':'verified','checked':self.clock()}
                except urllib.error.HTTPError as exc:
                    coverage[kind]={'state':'busy' if exc.code==409 else 'unavailable'}
                    exc.close()
                except Exception:
                    coverage[kind]={'state':'recovery_failed' if verify else 'unavailable'}
                    if not verify:components.pop(kind+'.tar.gz',None)
            if not verify:
                files={'application.dump':dump,**components}
                metadata={'kind':'os-backup-bundle','application_schema':self.schema,'independent_snapshots':True,'coverage':coverage,'sha256':{name:filehash(path) for name,path in files.items()}}
                manifest=work/'manifest.json';manifest.write_text(json.dumps(metadata));os.chmod(manifest,0o600)
                with tarfile.open(bundle,'w:') as archive:
                    archive.add(manifest,arcname='manifest.json')
                    for name,path in files.items():archive.add(path,arcname=name,recursive=False)
                partial=self.root/(identifier+'.partial')
                try:encrypt(bundle,partial,self.key);partial.replace(destination)
                finally:partial.unlink(missing_ok=True)
            with self.database() as db:db.execute('UPDATE backups SET coverage=? WHERE id=?',(json.dumps(coverage),identifier))
            return destination.stat().st_size


def serve():
    api=Path('/run/secrets/backup_api_key').read_text().strip()
    encoded=Path('/run/secrets/backup_encryption_key').read_text().strip()
    key=base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4))
    if len(api)<32 or len(key)!=32:raise BackupError('Generated private backup keys are required.')
    backups=Backups(os.environ.get('BACKUP_ROOT','/var/lib/os-backups'),key)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,status,data):
            raw=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if self.path=='/health':return self.reply(200,{'healthy':True})
            self.reply(404,{'error':'Not found.'})
        def do_POST(self):
            if self.path!='/backups':return self.reply(404,{'error':'Not found.'})
            if self.headers.get('Origin') or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+api):return self.reply(401,{'error':'Unauthorized.'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=4096:return self.reply(413,{'error':'Request too large.'})
                self.reply(200,backups.handle(json.loads(self.rfile.read(length))))
            except BackupError as exc:self.reply(400,{'error':str(exc)})
            except Exception:self.reply(503,{'error':'Backup service is unavailable.'})
    def scheduler():
        while True:
            try:backups.tick()
            except Exception:print('backup_schedule_failed',flush=True)
            time.sleep(30)
    threading.Thread(target=scheduler,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',8644),Handler).serve_forever()

if __name__=='__main__':serve()
