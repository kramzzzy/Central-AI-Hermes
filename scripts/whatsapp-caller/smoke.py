"""Synthetic phone audio fixture, run inside the private voice container."""
import json
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4
from dotenv import dotenv_values

base = 'http://127.0.0.1:8081'
key = Path('/data/voice-key').read_text().strip()
call = str(uuid4())

def request(path, data=b'', token=key, cid=call):
    req = urllib.request.Request(base + path, data=data, headers={
        'Authorization': 'Bearer ' + token, 'X-Call-ID': cid})
    with urllib.request.urlopen(req, timeout=100) as response:
        if path == '/turn':
            assert response.headers.get('Transfer-Encoding') == 'chunked', 'Reply audio is not streamed'
        return response.read()

try:
    request('/start', token='wrong')
    raise AssertionError('Unauthenticated start admitted')
except urllib.error.HTTPError as error:
    assert error.code == 401
started = False
try:
    request('/start')
    started = True
    try:
        request('/greeting', cid=str(uuid4()))
        raise AssertionError('Foreign call admitted')
    except urllib.error.HTTPError as error:
        assert error.code == 403
    greeting = request('/greeting')
    assert len(greeting) > 32000
    config = dotenv_values('/opt/data/profiles/leo/.env')
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from openrouter_speech import synthesize
    question = synthesize("What's six plus seven?", config, rate=16000)
    reply = request('/turn', question)
    assert len(reply) > 16000
    with urllib.request.urlopen(base+'/health') as response:
        assert json.load(response)['last_metrics']['native_complete'], 'Native reply did not finish'
    print('PASS: unauthorized/foreign calls rejected; Jarvis greeting; synthetic audio -> recognition -> native Hermes -> spoken PCM reply')
finally:
    if started:
        request('/end')
