"""Supervise native chat processes; no second WhatsApp client or login."""
import atexit
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

class TextSupervisor:
    def __init__(self):
        self.processes={}
        self.closed=False
        self.guard=threading.Lock()
        from whatsapp_routing import text_routes
        self.routes = {key: member['profile'] for key, member in text_routes().items()}
        for route in self.routes: self.start(route)
        self.watcher = threading.Thread(target=self.watch, daemon=True)
        self.watcher.start()
        atexit.register(self.close)

    def start(self, route):
        profile = self.routes[route]
        marker = Path('/data/text/ready-' + route)
        marker.unlink(missing_ok=True)
        environment = dict(os.environ, HERMES_HOME='/opt/data/profiles/' + profile, ORBIT_HERMES_PROFILE=profile)
        environment.pop('HERMES_PHONE_CONVERSATION', None)
        environment.pop('HERMES_VOICE_LANGUAGE', None)
        if profile == 'leo-whatsapp-text':
            environment['MICHAEL_TEXT_AUTH_HOME'] = '/opt/data/profiles/leo'
        else:
            environment['MICHAEL_TEAM_AUTH_HOME'] = '/opt/data/profiles/leo'
        self.processes[route] = subprocess.Popen([sys.executable, '/opt/setup/whatsapp-text-engine.py', route], env=environment)

    def ready(self):
        with self.guard:
            return all(process.poll() is None and Path('/data/text/ready-'+route).is_file() for route,process in self.processes.items())

    def watch(self):
        while not self.closed:
            time.sleep(5)
            with self.guard:
                if self.closed:return
                for route,process in list(self.processes.items()):
                    if process.poll() is not None:
                        print(json.dumps({'leo_text_worker_restarting':route}),flush=True)
                        self.start(route)

    def close(self):
        self.closed=True
        for process in list(self.processes.values()):
            if process.poll() is None:process.terminate()
