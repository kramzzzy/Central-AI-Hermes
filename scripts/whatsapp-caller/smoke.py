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
    payload = json.dumps({'text': "What's six plus seven?", 'reference_id': config['FISH_VOICE_ID'],
        'format': 'pcm', 'sample_rate': 16000, 'latency': 'balanced'}).encode()
    req = urllib.request.Request('https://api.fish.audio/v1/tts', data=payload,
        headers={'Authorization': 'Bearer ' + config['FISH_API_KEY'], 'Content-Type': 'application/json',
                 'model': config.get('FISH_TTS_MODEL') or 's2.1-pro-free'})
    with urllib.request.urlopen(req, timeout=40) as response:
        question = response.read()
    reply = request('/turn', question)
    assert len(reply) > 16000
    with urllib.request.urlopen(base+'/health') as response:
        assert json.load(response)['last_metrics']['native_complete'], 'Native reply did not finish'
    print('PASS: unauthorized/foreign calls rejected; Jarvis greeting; synthetic audio -> recognition -> native Hermes -> spoken PCM reply')
finally:
    if started:
        request('/end')
