"""Compare response timings using an identical synthetic PCM question, no phone call."""
import json
import os
import time
import urllib.request
from pathlib import Path
from uuid import uuid4
from dotenv import dotenv_values

base = 'http://127.0.0.1:8081'
key = Path('/data/voice-key').read_text().strip()
call = str(uuid4())
fixture = Path('/tmp/voice-speed-fixture.pcm')
if not fixture.exists():
    c = dotenv_values('/opt/data/profiles/leo/.env')
    body = json.dumps({'text': 'Briefly explain how you can help me plan my work tomorrow.',
        'reference_id': c['FISH_VOICE_ID'], 'format': 'pcm', 'sample_rate': 16000,
        'latency': 'balanced'}).encode()
    req = urllib.request.Request('https://api.fish.audio/v1/tts', data=body,
        headers={'Authorization': 'Bearer ' + c['FISH_API_KEY'],
        'Content-Type': 'application/json', 'model': c.get('FISH_TTS_MODEL') or 's2.1-pro-free'})
    with urllib.request.urlopen(req, timeout=40) as response:
        fixture.write_bytes(response.read())

def request(path, data=b''):
    req = urllib.request.Request(base+path, data=data, headers={
        'Authorization': 'Bearer '+key, 'X-Call-ID': call})
    return urllib.request.urlopen(req, timeout=100)

started = False
results = []
try:
    with request('/start') as response:
        response.read()
    started = True
    for _ in range(int(os.environ.get('SPEED_RUNS', '2'))):
        began = time.monotonic()
        with request('/turn', fixture.read_bytes()) as response:
            first = response.read(1920)
            first_at = time.monotonic()
            assert first, 'No speech audio'
            rest = response.read()
            complete = time.monotonic()
        with urllib.request.urlopen(base+'/health') as response:
            health = json.load(response)
        if 'native_complete' in health.get('last_metrics', {}):
            assert health['last_metrics']['native_complete'], 'Native reply did not finish'
        result = {**health.get('last_metrics', {}),
            'client_first_audio_ms': round((first_at-began)*1000),
            'client_total_ms': round((complete-began)*1000),
            'audio_bytes': len(first)+len(rest)}
        results.append(result)
        print(json.dumps(result), flush=True)
        time.sleep(.3)
finally:
    if started:
        with request('/end') as response:
            response.read()
