"""Server-generated phone bindings; a number always keeps its own private profile."""
import json
from pathlib import Path

ROUTING_FILE = Path('/run/secrets/whatsapp_routing')
DYNAMIC_FILES = [
    Path('/data/contacts.json'),
    Path(__file__).resolve().parent.parent / '.runtime' / 'contacts.json',
]

def routing():
    for f in DYNAMIC_FILES:
        if f.is_file():
            try:
                data = json.loads(f.read_text(encoding='utf-8'))
                if data.get('contacts') and len(data['contacts']) > 0:
                    return data
            except Exception:
                pass
    if ROUTING_FILE.is_file():
        return json.loads(ROUTING_FILE.read_text())
    # Clean default routing; dynamic contacts are configured by owner
    import os
    owner = os.environ.get('WHATSAPP_OWNER', os.environ.get('LEO_WHATSAPP_OWNER', ''))
    business = os.environ.get('WHATSAPP_BUSINESS_CONTACT', os.environ.get('LEO_WHATSAPP_BUSINESS_CONTACT', ''))
    group = os.environ.get('WHATSAPP_GROUP', os.environ.get('LEO_WHATSAPP_GROUP', ''))
    contacts = []
    if owner:
        contacts.append({
            'number': owner, 'name': 'Owner', 'role': 'owner', 'route': 'owner',
            'profile': 'leo', 'text_profile': 'leo-whatsapp-text',
            'calls': True, 'inbound': True, 'outbound': True
        })
    if business:
        contacts.append({
            'number': business, 'name': 'Business contact', 'role': 'business', 'route': 'business',
            'profile': 'leo', 'text_profile': 'leo-whatsapp-text',
            'calls': True, 'inbound': True, 'outbound': True
        })
    return {
        'owner': owner,
        'business': business,
        'group': group,
        'group_profile': 'team-whatsapp-social',
        'contacts': contacts
    }

def text_routes():
    value = routing()
    routes = {c['route']:{**c,'profile':c['text_profile'],'group':False} for c in value['contacts'] if c.get('text', c.get('role') in {'owner', 'business'})}
    if value.get('group'): routes['team']={'profile':value['group_profile'],'group':True}
    return routes

def caller(number):
    for c in routing()['contacts']:
        clean = c.get('number') or c.get('phone_number')
        if clean == number and (c.get('calls') or c.get('inbound') or c.get('allow_inbound') or c.get('outbound') or c.get('allow_outbound')):
            return {
                **c,
                'number': clean,
                'calls': True,
                'profile': c.get('profile') or 'leo',
                'name': c.get('name') or clean
            }
    raise PermissionError('Caller is not admitted')

def caller_data(base, number):
    return base / ('contact-' + str(number))
