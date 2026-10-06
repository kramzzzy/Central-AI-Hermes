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
    # Retained standalone fixtures; managed deployments always provision the file.
    import os
    return {'owner':'61423947456','business':'639267200480','group':os.environ.get('LEO_WHATSAPP_GROUP',''),
        'group_profile':'team-whatsapp-social','contacts':[
        {'number':'61423947456','name':'Michael Vazquez','role':'owner','route':'michael','profile':'team-whatsapp-michael-business','text_profile':os.environ.get('LEO_MICHAEL_TEXT_PROFILE','team-whatsapp-michael-business'),'calls':True,'inbound':True,'outbound':True},
        {'number':'639267200480','name':'Mark Tech','role':'business','route':'mark','profile':'leo','text_profile':'leo-whatsapp-text','calls':True,'inbound':True,'outbound':True},
        {'number':'639606637666','name':'May Sambitan','role':'accounts','route':'contact-639606637666','profile':'leo','text_profile':'leo-whatsapp-text','calls':True,'inbound':True,'outbound':True}]}

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
    if number == '639267200480': return base
    if number == '61423947456': return base / 'business-michael'
    return base / ('contact-' + number)
