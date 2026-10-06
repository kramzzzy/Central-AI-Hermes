"""Scoped native specialist sessions; the OS owns definitions, Hermes owns execution.

No model arguments choose an actor, profile path, provider or credential. Each child
gets a distinct native profile/session and only the parent's authenticated OS tools.
"""
import hashlib
from contextlib import contextmanager
import hmac
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from uuid import UUID, uuid4
from hermes_chat import NativeChat
from hermes_team import TeamPool
from hermes_os import authenticated_exchange

TERMINAL = {'completed', 'failed', 'cancelled', 'timed_out'}


def restrict_native_specialist_runtime(home, routine_only=False):
    """Pin this subprocess's native tool selection, including desktop surface fold-ins.

    Installed gateway folds desktop_ui/project in after configuration suppression.
    Specialist subprocesses have no client surface and must never inherit those tools.
    """
    import yaml
    config=yaml.safe_load((Path(home)/'config.yaml').read_text())
    if not routine_only and not config.get('os_specialist'): return
    import tui_gateway.server as native
    if not callable(getattr(native,'_load_enabled_toolsets',None)) or not callable(getattr(native,'_cfg_max_turns',None)):
        raise RuntimeError('Native bounded context tool-selection compatibility check failed')
    native._load_enabled_toolsets=lambda platform=None:[] if routine_only else ['michael_os']
    native._cfg_max_turns=lambda cfg,default=12:12


def validate_tasks(body):
    if not isinstance(body, dict) or set(body)-{'action','tasks'}:
        raise ValueError('Unsupported specialist arguments')
    if body.get('action') == 'list' and set(body) == {'action'}:
        return []
    tasks = body.get('tasks')
    if body.get('action') != 'run' or not isinstance(tasks,list) or not 1 <= len(tasks) <= 2:
        raise ValueError('Delegate one or two tasks at a time')
    for task in tasks:
        if not isinstance(task,dict) or set(task) != {'specialist_id','goal'}:
            raise ValueError('Specify a specialist and goal only')
        UUID(task['specialist_id'])
        if not isinstance(task['goal'],str) or not 0 < len(task['goal'].strip()) <= 8000:
            raise ValueError('Specialist goal must be at most 8000 characters')
    return tasks


class SpecialistPool:
    def __init__(self, settings, root, factory=NativeChat, authority=None, deadline=120):
        self.settings, self.root, self.factory = settings, Path(root), factory
        self.authority = authority or (lambda parent, ids: authenticated_exchange(parent,
            {'ids':ids} if ids else {}, '/api/hermes/specialists-inspect'))
        self.deadline = deadline
        self.guard = threading.RLock()
        self.active = {}
        self.state = self.root / '.runtime' / 'specialists.sqlite'
        self.state.parent.mkdir(parents=True,exist_ok=True)
        with self.database() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, actor TEXT NOT NULL,
                assistant TEXT NOT NULL, specialist TEXT NOT NULL, name TEXT NOT NULL, goal TEXT NOT NULL,
                status TEXT NOT NULL, output TEXT NOT NULL DEFAULT '', progress TEXT NOT NULL DEFAULT '',
                session TEXT, created REAL NOT NULL, finished REAL)''')
            connection.execute("UPDATE runs SET status='failed', progress='Backend restarted before completion.', finished=? WHERE status='running'",(time.time(),))
        os.chmod(self.state,0o600)

    @contextmanager
    def database(self):
        connection=sqlite3.connect(self.state,timeout=10)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update(self, id, **values):
        allowed={'status','output','progress','session','finished'}
        if set(values)-allowed: raise ValueError('Invalid run update')
        with self.database() as connection:
            connection.execute('UPDATE runs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',[*values.values(),id])

    def binding(self, parent):
        with parent.guard:
            if not parent.workspace_grant or parent.actor != parent.workspace_actor or time.monotonic() >= parent.lease:
                raise PermissionError('An active authenticated conversation is required')
            return (parent.actor,parent.workspace_turn,parent.epoch)

    def synchronize(self, item, validate=False):
        parent, child = item['parent'], item.get('chat')
        if item['cancel'].is_set() or self.binding(parent) != item['binding']:
            raise PermissionError('Specialist access was cancelled')
        if time.monotonic() > item['until']:
            raise TimeoutError('Specialist time limit reached')
        if validate and (validate=='force' or time.monotonic()>=item.get('verified_until',0)):
            data=self.authority(parent,[item['definition']['id']])
            if not data.get('definitions') or data['definitions'][0]['revision'] != item['definition']['revision']:
                raise PermissionError('Specialist settings changed')
            item['verified_until']=time.monotonic()+3
        if child:
            with parent.guard, child.guard:
                child.actor=parent.actor
                child.lease=min(parent.lease,item['until'])
                child.workspace_actor=parent.workspace_actor
                child.workspace_grant=parent.workspace_grant
                # This turn binding never changes, even when the grant token is renewed.
                child.workspace_turn=item['binding'][1]

    def inspection_chat(self, authorization):
        with self.guard:
            candidates=list(self.active.values())
        for item in candidates:
            child=item.get('chat')
            if child and hmac.compare_digest(authorization,'Bearer '+child.os_tool_token):
                self.synchronize(item,validate='force')
                return child
        return None

    def validate_tool(self, child, path, body):
        with self.guard:
            item=next((x for x in self.active.values() if x.get('chat') is child),None)
        if item is None: return
        self.synchronize(item,validate='force')
        if path == '/specialists/tool':
            raise PermissionError('Specialists cannot create nested specialists')
        if path == '/google/tool':
            if not item['definition']['allow_google']:
                raise PermissionError('Google tools are disabled for this specialist')
            reads={'status','gmail_search','gmail_read','calendar_list','calendar_events',
                'team_calendar_events','drive_list','drive_read','sheets_read'}
            if not isinstance(body,dict) or body.get('operation') not in reads:
                raise PermissionError('Specialists may only read permitted Google records')

    def provision(self,item,data):
        # Unique child identity is fixed by service-side IDs; task text never picks paths.
        name='team-specialist-'+hashlib.sha256(item['id'].encode()).hexdigest()[:32]
        team={'org_id':data['org_id'],'user_id':data['user_id'],
            'assistant_id':item['id'],'definition':{'name':item['definition']['name'],
            'instructions': ('You are a bounded specialist working for the requesting member. '
                'Return your findings to their main assistant; do not claim pending approvals are executed. '
                'No nested delegation or direct external actions. Purpose: '+item['definition']['purpose']+
                '\n'+item['definition']['instructions']), 'knowledge':data['knowledge']}}
        native, state_root=TeamPool(self.settings,self.root).provision(team,name)
        import yaml
        home=Path(native['HERMES_PROFILE_ROOT'])/'profiles'/name
        config=yaml.safe_load((home/'config.yaml').read_text())
        # Specialists neither read nor write long-term memory. Main assistant owns capture.
        config['plugins']={'enabled':[],'disabled':['hindsight']}
        config['memory']={'enabled':False}
        config['platform_toolsets']={'cli':[]}
        config['delegation']={'max_spawn_depth':0}
        config['agent']['max_turns']=12
        config['agent']['disabled_toolsets']=['terminal','file','browser','web','memory','todo','delegation','cronjob','skills','clarify']
        # configure_profile enforces this explicit allowlist at MCP startup too.
        config['os_specialist']={'google':item['definition']['allow_google']}
        (home/'config.yaml').write_text(yaml.safe_dump(config,sort_keys=False),encoding='utf-8')
        os.chmod(home/'config.yaml',0o600)
        return self.factory(native,state_root)

    def tool(self,parent,body):
        tasks=validate_tasks(body)
        if getattr(parent,'specialist_context',False):
            raise PermissionError('Specialists cannot delegate')
        binding=self.binding(parent)
        data=self.authority(parent,[t['specialist_id'] for t in tasks] if tasks else None)
        if not tasks:
            return {'specialists':[{k:d[k] for k in ['id','name','purpose','allow_google']} for d in data['definitions']]}
        if binding[0] != data['org_id']+':'+data['user_id']:
            raise PermissionError('Specialist identity mismatch')
        definition_by_id={d['id']:d for d in data['definitions']}
        assistant=data.get('assistant_id') or ''
        items=[]
        with self.guard:
            if len(self.active)+len(tasks)>4 or sum(x['binding'][0]==binding[0] for x in self.active.values())+len(tasks)>2:
                raise RuntimeError('Specialists are busy. At most two tasks per member can run concurrently.')
            for task in tasks:
                id=str(uuid4()); definition=definition_by_id[task['specialist_id']]
                item={'id':id,'definition':definition,'parent':parent,'binding':binding,
                    'assistant':assistant,'goal':task['goal'].strip(),'cancel':threading.Event(),
                    'done':threading.Event(),'until':time.monotonic()+self.deadline}
                self.active[id]=item; items.append(item)
                with self.database() as connection:
                    connection.execute('INSERT INTO runs(id,actor,assistant,specialist,name,goal,status,created) VALUES(?,?,?,?,?,?,?,?)',
                        (id,binding[0],assistant,definition['id'],definition['name'],item['goal'],'running',time.time()))
        for item in items:
            threading.Thread(target=self.run,args=(item,data),daemon=True).start()
            threading.Thread(target=self.watch,args=(item,),daemon=True).start()
        # Main model receives verified summaries in this tool result; no scripted filler.
        for item in items:
            if not item['done'].wait(self.deadline+15):
                item['cancel'].set()
                raise RuntimeError('Specialist cancellation is still settling; check progress before retrying.')
        if self.binding(parent)!=binding:
            raise PermissionError('The conversation changed before specialist results were returned')
        self.authority(parent,None)
        return {'results':[self.result(item['id'],binding[0],assistant) for item in items],
            'policy':'Results are specialist reports, not verified external-action receipts. Pending previews still need member approval.'}

    def watch(self,item):
        # Interrupt the subprocess even if native connect/history RPC is blocked.
        while not item['done'].wait(.2):
            expired=time.monotonic()>item['until']
            try: revoked=self.binding(item['parent'])!=item['binding']
            except PermissionError: revoked=True
            if item['cancel'].is_set() or expired or revoked:
                item['cancel'].set()
                child=item.get('chat')
                if child:
                    try: child.close()
                    except Exception: pass
                return

    def run(self,item,data):
        child=None
        try:
            self.synchronize(item,validate=True)
            child=self.provision(item,data)
            child.specialist_context=True
            item['chat']=child
            child.handle({'action':'connect'})
            # The actual native session is separate from the parent and every sibling.
            self.update(item['id'],session=child.stored,progress='Preparing task')
            self.synchronize(item,validate=True)
            child.handle({'action':'send','text':item['goal'],'actor':item['binding'][0],
                'workspace_grant':item['parent'].workspace_grant})
            while True:
                self.synchronize(item,validate=True)
                snapshot=child.handle({'action':'poll','actor':item['binding'][0],
                    'workspace_grant':item['parent'].workspace_grant,'cursor':0})
                events=snapshot.get('events',[])
                terminal=next((e for e in reversed(events) if e.get('type') in {'turn.complete','turn.error','turn.interrupted'}),None)
                latest=next((e for e in reversed(events) if str(e.get('type','')).startswith('tool.')),None)
                if latest:
                    # Do not leak tool arguments, mail/doc content or internal reasoning in progress.
                    self.update(item['id'],progress='Using an authorized tool')
                if snapshot.get('approvals') or snapshot.get('requests'):
                    raise RuntimeError('Specialist needs an interactive answer; main assistant must handle it.')
                if terminal:
                    if terminal['type']!='turn.complete': raise RuntimeError('Native specialist did not complete')
                    output=next((m.get('text','') for m in reversed(snapshot.get('messages',[])) if m.get('role')=='assistant' and m.get('text')),'')
                    if not output.strip(): raise RuntimeError('Specialist returned no answer')
                    self.synchronize(item,validate='force')
                    self.update(item['id'],status='completed',output=output[:30000],progress='Completed',finished=time.time())
                    break
                item['cancel'].wait(.5)
        except (PermissionError,TimeoutError) as exc:
            status='timed_out' if time.monotonic()>item['until'] or isinstance(exc,TimeoutError) else 'cancelled'
            self.update(item['id'],status=status,progress='Time limit reached' if status=='timed_out' else 'Access ended or task cancelled',finished=time.time())
        except Exception:
            status='timed_out' if time.monotonic()>item['until'] else 'cancelled' if item['cancel'].is_set() else 'failed'
            self.update(item['id'],status=status,progress='Specialist failed or interrupted. Main assistant can continue without this result.',finished=time.time())
        finally:
            if child:
                # Complete subprocess tree termination before releasing a concurrency slot.
                try: child.shutdown()
                except Exception: pass
            with self.guard: self.active.pop(item['id'],None)
            item['done'].set()

    def result(self,id,actor,assistant):
        with self.database() as connection:
            connection.row_factory=sqlite3.Row
            row=connection.execute('SELECT id,name,goal,status,output,progress,session,created,finished FROM runs WHERE id=? AND actor=? AND assistant=?',
                (id,actor,assistant)).fetchone()
        if not row: raise PermissionError('Specialist result unavailable')
        return dict(row)

    def handle(self,body):
        if not isinstance(body,dict) or set(body)-{'action','actor','assistant_id','run_id'}:
            raise ValueError('Unsupported specialist operation')
        actor=body.get('actor',''); assistant=body.get('assistant_id') or ''
        if not isinstance(actor,str) or not 0<len(actor)<=250: raise ValueError('Invalid actor')
        if body.get('action')=='cancel':
            id=str(UUID(body.get('run_id','')))
            self.result(id,actor,assistant)
            with self.guard:
                item=self.active.get(id)
                if item: item['cancel'].set()
            return {'cancel_requested':True}
        if body.get('action')!='list': raise ValueError('Unsupported specialist operation')
        with self.database() as connection:
            ids=[r[0] for r in connection.execute('SELECT id FROM runs WHERE actor=? AND assistant=? ORDER BY created DESC LIMIT 30',(actor,assistant))]
        return {'runs':[self.result(id,actor,assistant) for id in ids]}

    def cancel_parent(self,parent):
        with self.guard:
            for item in self.active.values():
                if item['parent'] is parent: item['cancel'].set()
