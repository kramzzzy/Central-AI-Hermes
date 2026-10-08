"""Profile-bound read adapter, reusable by the planned OS plugin transport."""
import json
import os
import subprocess
import sys
from hermes_profile import profile_environment


def read_library(settings, root, body):
    if body.get('action') not in {'skills', 'skill', 'memory', 'skill_create', 'skill_edit', 'skill_delete'}:
        raise RuntimeError('Unsupported library operation')
    try:
        python_bin = settings.get('HERMES_PYTHON') or os.environ.get('HERMES_PYTHON') or sys.executable
        result = subprocess.run(
            [python_bin, str(root / 'scripts/hermes-library-entry.py')],
            input=json.dumps(body),
            capture_output=True, text=True, encoding='utf-8', timeout=45,
            env=profile_environment(settings), cwd=root)
        if result.returncode:
            err = (result.stderr or '').strip()
            # If stderr contains a python traceback with RuntimeError, extract the message
            lines = [line.strip() for line in err.splitlines() if line.strip()]
            clean_msg = lines[-1] if lines else 'Operation failed'
            if clean_msg.startswith('RuntimeError:'):
                clean_msg = clean_msg.replace('RuntimeError:', '').strip()
            raise RuntimeError(clean_msg)
        return json.loads(result.stdout)
    except Exception as exc:
        msg = str(exc)
        if any(w in msg for w in ['already exists', 'cannot be deleted', 'Frontmatter', 'YAML', 'Invalid skill', 'Description is', 'Skill name', 'not found', 'pinned']):
            raise RuntimeError(msg)
        raise RuntimeError(f'Hermes library operation failed: {msg}') from exc
