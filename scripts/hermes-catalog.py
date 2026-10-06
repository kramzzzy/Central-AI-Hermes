"""Inspect installed skill guidance and account configuration without running a model."""
import os, sys, json, re, contextlib
from pathlib import Path
with contextlib.redirect_stdout(sys.stderr):
 from hermes_profile import load_profile
 home, config = load_profile()
 from hermes_cli.runtime_provider import resolve_runtime_provider
 from hermes_runtime_policy import available_tools
 model = config['default']
 provider = config.get('provider', 'auto')
 try:
  runtime = resolve_runtime_provider(target_model=model)
  provider = runtime.get('provider', provider)
  ready = bool(runtime.get('api_key'))
 except Exception:
  ready = False
 skills = []
 root = (home / 'skills').resolve()
 if root.exists():
  for path in sorted(root.rglob('SKILL.md')):
   if not path.resolve().is_relative_to(root) or path.stat().st_size > 24000: continue
   content = path.read_text(encoding='utf-8', errors='replace')
   def field(key, fallback):
    found = re.search(r'^' + key + r':\s*(.+)$', content, re.M)
    return found.group(1).strip().strip('\"\'') if found else fallback
   identity = path.parent.relative_to(root).as_posix()
   skills.append({'id':identity, 'name':field('name',path.parent.name), 'description':field('description','Installed Hermes skill guidance.')[:600], 'content':content})
 try:
  tools = available_tools()
  modes = ['draft-only','tools']
 except Exception:
  tools = []
  modes = ['draft-only']
print(json.dumps({'mode':'profile-aware','modes':modes,'tools':tools, 'auth_ready':ready,'model':model,'provider':provider,'runtime_profile':os.environ['ORBIT_HERMES_PROFILE'],'runtime_host':os.environ.get('HERMES_RUNTIME_HOST','native-windows'),'skills':skills}))
