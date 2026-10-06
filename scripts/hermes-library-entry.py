"""Read native profile resources through Hermes; never starts an inference agent.

The gateway passes only allowlisted operations. No arbitrary paths or raw SQL.
Skill preprocessing is deliberately disabled so a preview cannot execute commands.
"""
import contextlib
import json
import os
import sys

with contextlib.redirect_stdout(sys.stderr):
    from hermes_profile import load_profile
    home, _ = load_profile()
    body = json.load(sys.stdin)
    action = body.get('action')
    if action in {'skills', 'skill'}:
        from tools.skills_tool import skills_list, skill_view
        catalog = json.loads(skills_list())
        if not catalog.get('success'):
            raise RuntimeError('Native skills could not be read')
        rows = [{k: s.get(k, '') for k in ('name', 'description', 'category')}
                for s in catalog.get('skills', [])]
        if action == 'skills':
            result = {'skills': rows}
        else:
            name = body.get('name')
            if not isinstance(name, str) or name not in {s['name'] for s in rows}:
                raise RuntimeError('Choose an available native skill')
            detail = json.loads(skill_view(name, preprocess=False))
            if not detail.get('success'):
                raise RuntimeError('Native skill is unavailable')
            result = {'skill': {**next(s for s in rows if s['name'] == name),
                                'content': detail.get('content', '')}}
    elif action == 'memory':
        from tools.memory_tool import load_on_disk_store
        from hermes_cli.config import load_config
        store = load_on_disk_store()
        cfg = load_config() or {}
        provider = (cfg.get('memory') or {}).get('provider')
        result = {'provider': provider or None, 'stores': [
            {'id': target, 'name': label, 'file': filename,
             'enabled': store.target_enabled(target), 'entries': entries,
             'limit': limit}
            for target, label, filename, entries, limit in [
                ('memory', 'Agent memory', 'MEMORY.md', store.memory_entries, store.memory_char_limit),
                ('user', 'User preferences', 'USER.md', store.user_entries, store.user_char_limit)]]}
        if provider == 'hindsight':
            from hermes_memory import read_bank
            external = read_bank(home, include_memories=True)
            result['external'] = external
            if external and external.get('available'):
                rows = (external.pop('memories', {}) or {}).get('items', [])
                result['stores'].insert(0, {'id': 'hindsight', 'name': 'Hindsight long-term memory',
                    'file': external['bank'], 'enabled': True,
                    'entries': [str(row.get('text', ''))[:12000] for row in rows if row.get('text')],
                    'limit': 0})
    elif action == 'skill_create':
        from tools.skill_manager_tool import skill_manage, _skill_gate_bypass
        _skill_gate_bypass.set(True)
        name = str(body.get('name', '')).strip()
        content = str(body.get('content', '')).strip()
        category = str(body.get('category', '')).strip() or None
        description = str(body.get('description', '')).strip()
        if not content.startswith('---'):
            desc = description or f"Skill for {name}."
            if len(desc) > 60:
                desc = desc[:57].rstrip() + "..."
            frontmatter = f"---\nname: {name}\ndescription: {desc}\n---\n\n"
            content = frontmatter + content
        raw_res = skill_manage(action='create', name=name, content=content, category=category)
        res_data = json.loads(raw_res) if isinstance(raw_res, str) else raw_res
        if not res_data.get('success'):
            raise RuntimeError(res_data.get('error') or res_data.get('message') or 'Could not create skill')
        result = {'success': True, 'message': res_data.get('message', f"Skill '{name}' created."), 'name': name}
    elif action == 'skill_edit':
        from tools.skill_manager_tool import skill_manage, _skill_gate_bypass
        _skill_gate_bypass.set(True)
        name = str(body.get('name', '')).strip()
        content = str(body.get('content', '')).strip()
        raw_res = skill_manage(action='edit', name=name, content=content)
        res_data = json.loads(raw_res) if isinstance(raw_res, str) else raw_res
        if not res_data.get('success'):
            raise RuntimeError(res_data.get('error') or res_data.get('message') or 'Could not update skill')
        result = {'success': True, 'message': res_data.get('message', f"Skill '{name}' updated."), 'name': name}
    elif action == 'skill_delete':
        from tools.skill_manager_tool import skill_manage, _skill_gate_bypass
        _skill_gate_bypass.set(True)
        name = str(body.get('name', '')).strip()
        raw_res = skill_manage(action='delete', name=name)
        res_data = json.loads(raw_res) if isinstance(raw_res, str) else raw_res
        if not res_data.get('success'):
            raise RuntimeError(res_data.get('error') or res_data.get('message') or 'Could not delete skill')
        result = {'success': True, 'message': res_data.get('message', f"Skill '{name}' deleted."), 'name': name}
    else:
        raise RuntimeError('Unsupported library operation')
    result.update(profile=os.environ['ORBIT_HERMES_PROFILE'], protocol=1)
print(json.dumps(result))
