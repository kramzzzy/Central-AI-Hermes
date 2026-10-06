"""Private, operator-selected native bindings. HTTP/model inputs cannot choose paths."""
import json
import re
from pathlib import Path
from uuid import UUID


def preserved_profiles(settings):
    location = settings.get('HERMES_PREMADE_MANIFEST_FILE')
    if not location:
        raise RuntimeError('The installer-generated Leo and Sarah definitions are unavailable')
    path = Path(location)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('The premade binding must be a private regular file')
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
        if set(manifest) != {'version', 'agents'} or manifest['version'] != 1 or len(manifest['agents']) != 2:
            raise ValueError()
        bindings = []
        for agent in manifest['agents']:
            UUID(agent['id'])
            profile = agent['native_profile']
            if agent['name'] not in {'Leo', 'Sarah'} or not isinstance(profile,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*',profile) or profile == 'default':
                raise ValueError()
            bindings.append({key: agent[key] for key in ['id', 'name', 'native_profile']})
        if any(len({a[key] for a in bindings}) != 2 for key in ['id','name','native_profile']):
            raise ValueError()
        profiles = Path(settings['HERMES_PROFILE_ROOT']).resolve(strict=True) / 'profiles'
        for binding in bindings:
            home = profiles / binding['native_profile']
            if home.is_symlink() or home.resolve(strict=True).parent != profiles or not (home / 'config.yaml').is_file():
                raise ValueError()
        return sorted(bindings, key=lambda item:item['name'])
    except (KeyError,TypeError,ValueError,OSError):
        raise RuntimeError('Premade native profiles need private administrator repair') from None
