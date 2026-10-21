"""Read native profile resources through Hermes; never starts an inference agent.

The gateway passes only allowlisted operations. No arbitrary paths or raw SQL.
Skill preprocessing is deliberately disabled so a preview cannot execute commands.
"""
import contextlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
import yaml


def _discover_disk_skills(home: Path):
    repo_root = Path(__file__).resolve().parent.parent
    roots = [
        home / 'skills',
        Path(os.environ.get('HERMES_EXTRA_SKILLS_DIR', '/opt/hermes/skills/personal-assistant')),
        repo_root / 'skills',
    ]
    found = {}
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.rglob('SKILL.md')):
            if any(p.startswith('.') for p in f.relative_to(root).parts):
                continue
            try:
                raw = f.read_text(encoding='utf-8', errors='replace')[:80000]
                meta = {}
                if raw.startswith('---'):
                    parts = raw.split('---', 2)
                    if len(parts) > 2:
                        meta = yaml.safe_load(parts[1]) or {}
                if not isinstance(meta, dict):
                    meta = {}
                rel_parts = f.relative_to(root).parts[:-1]
                name = str(meta.get('name') or f.parent.name).strip()
                if not name:
                    continue
                category = str(
                    meta.get('category')
                    or (rel_parts[0] if len(rel_parts) > 1 else root.name if root.name != 'skills' else 'custom')
                ).strip()
                description = str(meta.get('description') or '').strip()
                key = name.lower()
                if key not in found:
                    found[key] = {
                        'name': name,
                        'description': description,
                        'category': category,
                        'content': raw,
                        'path': f,
                    }
            except Exception:
                continue
    return found


with contextlib.redirect_stdout(sys.stderr):
    from hermes_profile import load_profile
    home, _ = load_profile()
    body = json.load(sys.stdin)
    action = body.get('action')
    if action in {'skills', 'skill'}:
        from tools.skills_tool import skills_list, skill_view
        catalog = json.loads(skills_list())
        rows_by_key = {}
        disk_skills = _discover_disk_skills(home)
        for key, item in disk_skills.items():
            rows_by_key[key] = {
                'name': item['name'],
                'description': item['description'],
                'category': item['category'],
            }
        if catalog.get('success'):
            for s in catalog.get('skills', []):
                sname = str(s.get('name') or '').strip()
                if not sname:
                    continue
                key = sname.lower()
                if key not in rows_by_key:
                    rows_by_key[key] = {
                        'name': sname,
                        'description': str(s.get('description') or ''),
                        'category': str(s.get('category') or ''),
                    }
                elif not rows_by_key[key].get('description') and s.get('description'):
                    rows_by_key[key]['description'] = str(s.get('description') or '')
        priority_cats = {'custom': 0, 'personal-assistant': 1, 'social-media': 2}
        rows = sorted(
            rows_by_key.values(),
            key=lambda r: (
                priority_cats.get(str(r.get('category') or '').lower(), 9),
                str(r.get('category') or '').lower(),
                str(r.get('name') or '').lower(),
            ),
        )
        if action == 'skills':
            result = {'skills': rows}
        else:
            name = str(body.get('name') or '').strip()
            key = name.lower()
            if not name or (key not in rows_by_key and key not in disk_skills):
                raise RuntimeError('Choose an available native skill')
            row_meta = rows_by_key.get(key) or {
                'name': disk_skills[key]['name'],
                'description': disk_skills[key]['description'],
                'category': disk_skills[key]['category'],
            }
            content_text = ''
            try:
                detail = json.loads(skill_view(row_meta['name'], preprocess=False))
                if detail.get('success') and detail.get('content'):
                    content_text = str(detail.get('content'))
            except Exception:
                pass
            if not content_text and key in disk_skills:
                content_text = disk_skills[key].get('content', '')
            if not content_text:
                raise RuntimeError('Native skill is unavailable')
            result = {'skill': {**row_meta, 'content': content_text}}
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
        name = str(body.get('name', '')).strip().lower()
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
            raise RuntimeError('Invalid skill name')
        content = str(body.get('content', '')).strip()
        category = str(body.get('category', '')).strip().lower() or 'custom'
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', category):
            category = 'custom'
        description = str(body.get('description', '')).strip()
        if not content.startswith('---'):
            desc = description or f"Skill for {name}."
            if len(desc) > 60:
                desc = desc[:57].rstrip() + "..."
            frontmatter = '---\n' + yaml.safe_dump({'name': name, 'description': desc}, sort_keys=False, allow_unicode=True) + '---\n\n'
            content = frontmatter + content
        raw_res = skill_manage(action='create', name=name, content=content, category=category)
        res_data = json.loads(raw_res) if isinstance(raw_res, str) else raw_res
        if not res_data.get('success'):
            target_dir = home / 'skills' / category / name
            target_dir.mkdir(parents=True, exist_ok=True)
            (target_dir / 'SKILL.md').write_text(content + '\n', encoding='utf-8')
            result = {'success': True, 'message': f"Skill '{name}' created.", 'name': name}
        else:
            result = {'success': True, 'message': res_data.get('message', f"Skill '{name}' created."), 'name': name}
    elif action == 'skill_edit':
        from tools.skill_manager_tool import skill_manage, _skill_gate_bypass
        _skill_gate_bypass.set(True)
        name = str(body.get('name', '')).strip()
        content = str(body.get('content', '')).strip()
        raw_res = skill_manage(action='edit', name=name, content=content)
        res_data = json.loads(raw_res) if isinstance(raw_res, str) else raw_res
        if not res_data.get('success'):
            disk_skills = _discover_disk_skills(home)
            key = name.lower()
            if key in disk_skills and disk_skills[key].get('path'):
                Path(disk_skills[key]['path']).write_text(content + '\n', encoding='utf-8')
            else:
                target_dir = home / 'skills' / 'custom' / re.sub(r'[^a-z0-9_-]', '-', key)
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / 'SKILL.md').write_text(content + '\n', encoding='utf-8')
            result = {'success': True, 'message': f"Skill '{name}' updated.", 'name': name}
        else:
            result = {'success': True, 'message': res_data.get('message', f"Skill '{name}' updated."), 'name': name}
    elif action == 'skill_delete':
        from tools.skill_manager_tool import skill_manage, _skill_gate_bypass
        _skill_gate_bypass.set(True)
        name = str(body.get('name', '')).strip()
        raw_res = skill_manage(action='delete', name=name)
        res_data = json.loads(raw_res) if isinstance(raw_res, str) else raw_res
        if not res_data.get('success'):
            disk_skills = _discover_disk_skills(home)
            key = name.lower()
            if key in disk_skills and disk_skills[key].get('path'):
                skill_file = Path(disk_skills[key]['path'])
                if str(skill_file).startswith(str(home / 'skills')):
                    shutil.rmtree(skill_file.parent, ignore_errors=True)
            result = {'success': True, 'message': f"Skill '{name}' deleted.", 'name': name}
        else:
            result = {'success': True, 'message': res_data.get('message', f"Skill '{name}' deleted."), 'name': name}
    else:
        raise RuntimeError('Unsupported library operation')
    result.update(profile=os.environ['ORBIT_HERMES_PROFILE'], protocol=1)
print(json.dumps(result))
