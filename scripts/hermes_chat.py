"""Private adapter to Hermes's own persistent Bot Chat JSON-RPC interface."""
import atexit
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from uuid import uuid4
from hermes_profile import profile_environment


def voice_reasoning_effort(model, provider):
    if provider in {'openai-codex', 'openrouter'} and re.fullmatch(
            r'(openai/)?(gpt-6-(sol|luna)|gpt-5\.6-(sol|terra|luna))(-900k)?', model):
        return 'none'
    return 'low'


class NativeChat:
    def __init__(self, settings, root):
        self.settings, self.root = settings, root
        self.process = None
        self.pending = {}
        self.events = deque(maxlen=500)
        self.requests = {}
        self.sequence = 0
        self.guard = threading.RLock()
        self.control_guard = threading.RLock()
        self.event_ready = threading.Condition(self.guard)
        self.voice_call = None
        self.voice_turn = None
        self.voice_until = 0
        self.voice_probe_at = 0
        self.voice_review = False
        self.voice_restore = None
        self.voice_model_restore = None
        self.connect_guard = threading.Lock()
        self.sid = None
        self.stored = None
        self.info = {}
        self.lease = 0
        self.actor = None
        self.workspace_grant = None
        self.workspace_actor = None
        self.workspace_turn = None
        self.os_tool_token = secrets.token_urlsafe(32)
        self.os_tool_file = None
        self.epoch = str(uuid4())
        self.disposed = False
        atexit.register(self.close)
        threading.Thread(target=self.watch, daemon=True).start()

    def start(self):
        with self.guard:
            if self.process and self.process.poll() is None:
                return
            self.sid = None
            self.stored = None
            self.info = {}
            self.events.clear()
            self.requests.clear()
            self.epoch = str(uuid4())
            log = open(self.root / '.runtime' / 'hermes-chat.log', 'a', encoding='utf-8')
            self.os_tool_token = secrets.token_urlsafe(32)
            if self.os_tool_file:
                self.os_tool_file.unlink(missing_ok=True)
            self.os_tool_file = self.root / '.runtime' / ('native-os-tool-' + str(uuid4()) + '.token')
            self.os_tool_file.write_text(self.os_tool_token, encoding='utf-8')
            os.chmod(self.os_tool_file, 0o600)
            self.clear_workspace()
            env = profile_environment(self.settings)
            env['HERMES_APPROVAL_MODE'] = 'off'
            env['HERMES_PERMISSION_MODE'] = 'off'
            env['HERMES_SESSION_PLATFORM'] = 'api_server'
            if self.settings.get('HERMES_TEAM_CONTEXT') == 'true':
                env['MICHAEL_TEAM_AUTH_HOME'] = self.settings['HERMES_TEAM_AUTH_HOME']
            env.pop('MICHAEL_RESTRICT_TEAM_TOOLS',None)
            if self.settings.get('HERMES_RESTRICT_TEAM_TOOLS')=='true':env['MICHAEL_RESTRICT_TEAM_TOOLS']='true'
            env['MICHAEL_OS_TOOL_TOKEN_FILE'] = str(self.os_tool_file)
            python_bin = self.settings.get('HERMES_PYTHON') or os.environ.get('HERMES_PYTHON') or sys.executable
            self.process = subprocess.Popen(
                [python_bin, '-u', str(self.root / 'scripts/hermes-chat-entry.py')],
                env=env, cwd=self.root,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                encoding='utf-8', text=True,
                **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}))
            log.close()
            threading.Thread(target=self.read, args=(self.process,), daemon=True).start()

    def read(self, process):
        for line in process.stdout:
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            with self.guard:
                if 'id' in frame and 'method' not in frame:
                    waiting = self.pending.get(str(frame['id']))
                    if waiting:
                        waiting.put(frame)
                elif frame.get('method') == 'event':
                    event = frame.get('params', {})
                    # Do not expose internal reasoning or unrelated sessions.
                    if event.get('session_id') != self.sid:
                        continue
                    kind = event.get('type', '')
                    if kind in {'thinking.delta', 'reasoning.delta'}:
                        continue
                    if kind == 'request.cancel':
                        self.requests.pop(str(event.get('payload', {}).get('request_id', '')), None)
                    if kind == 'session.info':
                        self.info.update(event.get('payload', {}))
                    if kind == 'message.start':
                        self.info['running'] = True
                    if kind in {'turn.complete', 'turn.error', 'turn.interrupted'}:
                        self.info['running'] = False
                        self.clear_workspace()
                    if kind.startswith(('message.', 'tool.', 'turn.', 'notification.')) or kind in {'session.info', 'error', 'request.cancel'}:
                        self.sequence += 1
                        self.events.append({**event, 'seq': self.sequence, 'voice_turn': self.voice_turn})
                        self.event_ready.notify_all()
                elif frame.get('method') and frame.get('id'):
                    params = frame.get('params', {})
                    if params.get('session_id') == self.sid and frame['method'] == 'clarify':
                        self.requests[str(frame['id'])] = frame
                        self.event_ready.notify_all()
                    elif frame['method'] == 'approval':
                        # Automatically approve permission requests so the assistant executes tools directly
                        req_id = frame.get('id')
                        params = frame.get('params', {})
                        app_id = params.get('request_id') or params.get('id') or req_id
                        try:
                            self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': req_id, 'result': {'choice': 'always', 'decision': 'allow'}}) + '\n')
                            self.process.stdin.flush()
                        except Exception:
                            pass
                        if app_id:
                            try:
                                self.rpc('approval.respond', self.scoped(request_id=app_id, choice='always'))
                            except Exception:
                                pass
                    else:
                        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': frame['id'], 'error': {'code': -32601, 'message': 'This host setup operation requires Hermes Desktop.'}}) + '\n')
                        self.process.stdin.flush()
        with self.guard:
            for waiting in self.pending.values():
                waiting.put({'error': {'message': 'Native Hermes chat disconnected. Reconnect to continue.'}})
            self.voice_turn = None
            self.clear_workspace()
            self.event_ready.notify_all()

    def clear_workspace(self):
        cancel=getattr(self,'cancel_specialists',None)
        if cancel:cancel()
        self.workspace_grant = self.workspace_actor = self.workspace_turn = None

    def bind_workspace(self, body, renew=False):
        with self.guard:
            if renew and (not self.workspace_turn or self.workspace_actor != body.get('actor')):
                return
            grant = body.get('workspace_grant')
            self.workspace_grant = grant if isinstance(grant, str) and 0 < len(grant) <= 2048 else None
            self.workspace_actor = body.get('actor') if self.workspace_grant else None
            if not renew:
                self.workspace_turn = str(uuid4()) if self.workspace_grant else None

    def rpc(self, method, params=None, timeout=30):
        self.start()
        rid = str(uuid4())
        waiting = queue.Queue()
        with self.guard:
            self.pending[rid] = waiting
            self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params or {}}) + '\n')
            self.process.stdin.flush()
        try:
            result = waiting.get(timeout=timeout)
            if 'error' in result:
                raise RuntimeError(result['error'].get('message', 'Hermes request failed'))
            return result.get('result', {})
        except queue.Empty:
            raise RuntimeError('Hermes took too long to respond. Reconnect and check the conversation before resending.')
        finally:
            with self.guard:
                self.pending.pop(rid, None)

    def scoped(self, **extra):
        profile = getattr(self, 'active_profile', None) or self.settings['HERMES_PROFILE']
        return {'session_id': self.sid, 'profile': profile, **extra}

    def resume(self, profile, session_id):
        params = {'profile': profile, 'session_id': session_id, 'eager_build': True, 'inline_images': False}
        try:
            return self.rpc('session.resume', params, timeout=60)
        except RuntimeError as exc:
            # 0.21.5 rejects this field before executing the read-only operation.
            message = str(exc)
            if not (message.startswith('invalid params for session.resume: inline_images:') and 'Extra inputs are not permitted' in message):
                raise
            params.pop('inline_images')
            return self.rpc('session.resume', params, timeout=60)

    def connect(self):
        with self.connect_guard:
            self.start()
            if not self.sid:
                profile = self.settings['HERMES_PROFILE']
                rows = self.rpc('session.list', {'profile': profile, 'title': 'Bot Chat', 'limit': 10}).get('sessions', [])
                if rows:
                    session = self.resume(profile, 'Bot Chat')
                else:
                    session = self.rpc('session.create', {'profile': profile, 'title': 'Bot Chat', 'source': 'desktop', 'follow_profile_config': True}, timeout=60)
                self.sid = session['session_id']
                self.stored = session.get('stored_session_id') or session.get('session_key') or self.sid
                self.info = session.get('info', {})
                if not self.restore_voice_effort(recover=True):
                    raise RuntimeError('Previous voice work is still settling. Reconnect shortly.')
                if not self.restore_voice_model(recover=True):
                    raise RuntimeError('Previous voice model is still settling. Reconnect shortly.')
            return self.snapshot(0)

    def snapshot(self, cursor):
        if not self.sid:
            raise RuntimeError('Connect to Hermes first.')
        history = self.rpc('session.history', self.scoped())
        # Activate is a read/attach operation that refreshes live session information.
        live = self.rpc('session.activate', self.scoped(omit_messages=True))
        self.info.update(live.get('info', {}))
        approvals = self.rpc('approval.pending', self.scoped()).get('approvals', [])
        if approvals:
            for app in approvals:
                rid = app.get('request_id') or app.get('id')
                if rid:
                    try:
                        self.rpc('approval.respond', self.scoped(request_id=rid, choice='always'))
                    except Exception:
                        pass
            approvals = []
        title_info = self.rpc('session.title', self.scoped())
        # Native create and resume use different persisted-ID fields. Read the
        # authoritative key again here, including after a compression continuation.
        self.stored = title_info.get('session_key') or self.stored
        title = title_info.get('title')
        with self.guard:
            active_p = getattr(self, 'active_profile', None) or self.settings['HERMES_PROFILE']
            return {'profile': active_p, 'session_id': self.stored,
                    'title': title,
                    'epoch': self.epoch, 'cursor': self.sequence, 'info': {k: self.info.get(k) for k in
                    ['model', 'provider', 'running', 'tools', 'skills', 'usage', 'approval_mode', 'reasoning_effort', 'reasoning_effort_wire']},
                    'messages': [{**{k: m.get(k) for k in ['role', 'name', 'timestamp', 'row_id']},
                                  'text': m.get('text') or (m.get('content') if isinstance(m.get('content'), str) else '')}
                                 for m in history.get('messages', [])], 'approvals': approvals,
                    'requests': list(self.requests.values()),
                    'events': [e for e in self.events if e['seq'] > cursor]}

    def model_options(self):
        raw = self.rpc('model.options', self.scoped(include_unconfigured=False), timeout=60)
        providers = []
        for p in raw.get('providers', []):
            if p.get('authenticated') is False:
                continue
            models = [m for m in p.get('models', []) if m not in p.get('unavailable_models', [])]
            providers.append({'slug': p['slug'], 'name': p['name'], 'models': models,
                              'capabilities': p.get('capabilities', {})})
        return {'providers': providers,
                'effort': self.rpc('config.get', self.scoped(key='reasoning')).get('value', '')}

    def configure(self, body):
        live = self.rpc('session.activate', self.scoped(omit_messages=True)).get('info', {})
        if live.get('running'):
            raise RuntimeError('Wait for the reply to finish before changing model or thinking.')
        options = self.model_options()
        if body.get('model'):
            model, provider = body['model'], body.get('provider', '')
            if not any(p['slug'] == provider and model in p['models'] for p in options['providers']):
                raise RuntimeError('This model is not available in your connected Hermes providers.')
            # The native setter parses flags: reject whitespace/flags in catalog identifiers.
            if not re.fullmatch(r'[\w./:@+-]+', model) or model.startswith('-') or not re.fullmatch(r'[\w-]+', provider):
                raise RuntimeError('This model identifier is not supported by the picker.')
            result = self.rpc('config.set', self.scoped(key='model', value=f'{model} --provider {provider}',
                              scope='session', confirm_expensive_model=body.get('confirm') is True), timeout=60)
        elif body.get('effort') in {'low', 'medium', 'high'}:
            capabilities = next((p['capabilities'].get(live.get('model'), {}) for p in options['providers']
                                 if p['slug'] == live.get('provider')), {})
            if capabilities.get('reasoning') is not True:
                raise RuntimeError('Hermes does not report thinking support for this model.')
            result = self.rpc('config.set', self.scoped(key='reasoning', value=body['effort'], scope='session'))
        else:
            raise RuntimeError('Choose an available model or Low, Medium, or High thinking.')
        return {'setting': result}

    def sessions(self):
        # Listing does not implicitly create a Bot Chat or an agent profile.
        result = self.rpc('session.list', {'profile': self.settings['HERMES_PROFILE'],
                                         'limit': 500, 'include_hidden': True})
        sessions = list(result.get('sessions', []))
        if self.settings.get('HERMES_PROFILE') == 'leo':
            for wa_profile in ('leo-whatsapp-text', 'team-whatsapp-michael-text', 'team-whatsapp-michael-business'):
                try:
                    wa = self.rpc('session.list', {'profile': wa_profile,
                                                 'limit': 200, 'include_hidden': True})
                    for s in wa.get('sessions', []):
                        s_copy = dict(s)
                        s_copy['profile'] = wa_profile
                        if not s_copy.get('source'):
                            s_copy['source'] = 'whatsapp'
                        sessions.append(s_copy)
                except Exception:
                    pass
        return {'profile': self.settings['HERMES_PROFILE'], 'sessions': sessions,
                'current_session_id': self.stored, 'limit': 500, 'protocol': 1}

    def switch_session(self, body):
        if self.voice_call:
            raise RuntimeError('End the active voice call before changing conversations.')
        if self.sid:
            current = self.snapshot(0)
            if current['info'].get('running') or current['approvals'] or current['requests']:
                raise RuntimeError('Finish the current reply and pending questions before changing conversations.')
        profile = self.settings['HERMES_PROFILE']
        if body['action'] == 'session_open':
            target = body.get('session_id')
            available = self.sessions()['sessions']
            target_info = next((s for s in available if s['id'] == target), None)
            if not target_info:
                raise RuntimeError('This conversation is not available in the connected profile.')
            sess_profile = target_info.get('profile') or body.get('profile') or profile
            session = self.resume(sess_profile, target)
        else:
            title = str(body.get('title') or '').strip()
            if not title or len(title) > 100:
                raise RuntimeError('Enter a conversation title up to 100 characters.')
            session = self.rpc('session.create', {'profile': profile, 'title': title,
                               'source': 'desktop', 'follow_profile_config': True}, timeout=60)
            sess_profile = profile
        with self.guard:
            self.sid = session['session_id']
            self.stored = session.get('stored_session_id') or session.get('session_key') or self.sid
            self.info = session.get('info', {})
            self.active_profile = sess_profile
            self.epoch = str(uuid4())
            self.events.clear()
            self.requests.clear()
            self.actor, self.lease = None, 0
        return self.snapshot(0)

    def handle(self, body):
        if str(body.get('action', '')).startswith('voice_'):
            return self.handle_voice(body)
        # A snapshot and a session switch must not interleave histories/identities.
        with self.control_guard:
            return self._handle(body)

    def _handle(self, body):
        action = body.get('action')
        actor = body.get('actor')
        allowed_profiles = {
            self.settings['HERMES_PROFILE'],
            'leo-whatsapp-text',
            'team-whatsapp-michael-text',
            'team-whatsapp-michael-business',
        }
        if body.get('profile') and body['profile'] not in allowed_profiles:
            raise RuntimeError('The connected profile changed. Refresh before continuing.')
        if body.get('expected_session') and body['expected_session'] != self.stored:
            raise RuntimeError('The active conversation changed. Refresh before sending again.')
        if action == 'sessions':
            return self.sessions()
        if action in {'session_open', 'session_new'}:
            return self.switch_session(body)
        if isinstance(action, str) and action.startswith('voice_'):
            return self.handle_voice(body)
        if self.voice_call and action in {'send', 'stop', 'configure'}:
            raise RuntimeError('End the active voice call before changing or sending a text conversation.')
        if not self.voice_call and (self.voice_restore or self.voice_model_restore) and action in {'send', 'configure'}:
            if not self.restore_voice_effort() or not self.restore_voice_model():
                raise RuntimeError('Previous voice work is still settling. Reconnect shortly.')
        if action == 'connect':
            return self.connect()
        if not self.sid:
            raise RuntimeError('Reconnect to Hermes first.')
        if action == 'options':
            return self.model_options()
        if action == 'configure':
            return self.configure(body)
        if action == 'poll':
            if self.actor == actor:
                self.lease = time.monotonic() + 25
                self.bind_workspace(body, renew=True)
            return self.snapshot(int(body.get('cursor', 0)))
        if action == 'stop':
            self.clear_workspace()
            result = self.rpc('session.interrupt', self.scoped())
            self.actor = None
            return result
        if action == 'send':
            text = body.get('text', '').strip()
            if not text or len(text) > 12000:
                raise RuntimeError('Enter a message up to 12,000 characters.')
            if self.info.get('running'):
                raise RuntimeError('Wait for the active reply before sending another message.')
            self.actor, self.lease = actor, time.monotonic() + 25
            self.bind_workspace(body)
            if text.startswith('/'):
                parts = text[1:].split(maxsplit=1)
                name, arg = parts[0], parts[1] if len(parts) > 1 else ''
                if name in {'profile', 'profiles', 'hermes', 'update', 'uninstall', 'reset'}:
                    raise RuntimeError('Manage installation and other profiles in Hermes. This panel is bound to the connected profile.')
                try:
                    result = self.rpc('command.dispatch', self.scoped(name=name, arg=arg), timeout=60)
                except RuntimeError as exc:
                    if 'not a quick/plugin/bundle/skill' not in str(exc):
                        raise
                    result = self.rpc('slash.exec', self.scoped(command=text), timeout=60)
                if result.get('type') in {'send', 'skill'}:
                    return self.rpc('prompt.submit', self.scoped(text=result['message'], surface='app'))
                return {'command': result}
            return self.rpc('prompt.submit', self.scoped(text=text, surface='app'))
        if action == 'approve':
            self.bind_workspace(body, renew=True)
            return self.rpc('approval.respond', self.scoped(request_id=body.get('request_id'), choice=body.get('choice')))
        if action == 'answer':
            self.bind_workspace(body, renew=True)
            rid = str(body.get('request_id', ''))
            with self.guard:
                if rid not in self.requests:
                    raise RuntimeError('This question is no longer pending.')
                self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': rid, 'result': body.get('answer')}) + '\n')
                self.process.stdin.flush()
                self.requests.pop(rid, None)
            return {'answered': True}
        if action == 'catalog':
            return self.rpc('commands.catalog', self.scoped())
        if action == 'attach':
            data = body.get('data_url', '')
            name = Path(body.get('name', 'attachment')).name
            if not isinstance(data, str) or not data.startswith('data:') or len(data) > 12000000:
                raise RuntimeError('Choose a file smaller than 8 MB.')
            if data.startswith('data:image/'):
                return self.rpc('image.attach_bytes', self.scoped(content_base64=data.split(',', 1)[1], filename=name))
            return self.rpc('file.attach', self.scoped(data_url=data, name=name))
        raise RuntimeError('Unsupported chat action.')

    def handle_voice(self, body):
        if body.get('action') in {'voice_events', 'voice_heartbeat'}:
            return self._handle_voice(body)
        with self.control_guard:
            return self._handle_voice(body)

    def prepare_voice_effort(self, live):
        model, provider = live.get('model', ''), live.get('provider', '')
        if not re.fullmatch(r'[\w][\w./:@+-]*', model) or not re.fullmatch(r'[\w][\w-]*', provider):
            return 'low', None
        effort = voice_reasoning_effort(model, provider)
        try:
            previous = self.rpc('config.get', self.scoped(key='reasoning')).get('value')
        except Exception:
            previous = 'low'
        if previous not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}:
            previous = 'low'
        if previous == effort:
            return effort, None
        saved = {'profile': self.settings['HERMES_PROFILE'], 'session': self.stored,
                 'model': model, 'provider': provider, 'effort': previous}
        path = self.root / '.runtime' / 'native-voice-effort.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(saved), encoding='utf-8')
        temporary.replace(path)
        self.voice_restore = saved
        try:
            result = self.rpc('config.set', self.scoped(key='reasoning', value=effort, scope='session'))
            if result.get('value') != effort:
                print(f"[prepare_voice_effort] Could not apply voice reasoning {effort}")
        except Exception as e:
            print(f"[prepare_voice_effort] Error setting reasoning: {e}")
        return effort, saved

    def prepare_voice_model(self, live):
        model = self.settings.get('HERMES_VOICE_MODEL', '').strip()
        provider = (self.settings.get('HERMES_VOICE_PROVIDER', '').strip()
                    or self.settings.get('HERMES_CHAT_PROVIDER', '').strip()
                    or live.get('provider', '').strip()
                    or 'openrouter')
        if not model:
            return
        if not re.fullmatch(r'[\w][\w./:@+-]*', model) or not re.fullmatch(r'[\w][\w-]*', provider):
            print(f"[prepare_voice_model] Invalid voice model format ({model}, {provider}), using live chat model.")
            return
        if live.get('model') == model and live.get('provider') == provider:
            return
        options = {'effort': 'low'}
        try:
            options = self.model_options()
        except Exception as e:
            print(f"[prepare_voice_model] Options check skipped: {e}")
        effort = options.get('effort') or 'low'
        if effort not in {'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}:
            effort = 'low'
        for value, pattern in [(live.get('model', ''), r'[\w][\w./:@+-]*'), (live.get('provider', ''), r'[\w][\w-]*')]:
            if not re.fullmatch(pattern, value):
                print(f"[prepare_voice_model] Live model/provider invalid format ({value}), staying with live model.")
                return
        saved = {'profile': self.settings['HERMES_PROFILE'], 'session': self.stored,
                 'model': live['model'], 'provider': live['provider'], 'effort': effort,
                 'voice_model': model, 'voice_provider': provider}
        try:
            result = self.rpc('config.set', self.scoped(key='model',
                value=f'{model} --provider {provider} --reasoning {effort} --session', scope='session',
                confirm_expensive_model=True))
            if result.get('confirm_required'):
                print(f"[prepare_voice_model] Confirmation required to switch voice model: {result.get('confirm_message')}")
                return
            if result.get('value') != model:
                print(f"[prepare_voice_model] Voice model switch returned {result.get('value')}, continuing with live model.")
                return
            path = self.root / '.runtime' / 'native-voice-model.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(saved), encoding='utf-8')
            temporary.replace(path)
            self.voice_model_restore = saved
        except Exception as e:
            print(f"[prepare_voice_model] Could not switch to voice model {model}: {e}. Staying with live model.")
            self.voice_model_restore = None
            (self.root / '.runtime' / 'native-voice-model.json').unlink(missing_ok=True)
            return

    def restore_voice_model(self, recover=False):
        path = self.root / '.runtime' / 'native-voice-model.json'
        saved = self.voice_model_restore
        if recover and not saved and path.exists():
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                self.voice_model_restore = saved
            except Exception:
                saved = None
        if not saved:
            return True
        try:
            live = self.rpc('session.activate', self.scoped(omit_messages=True)).get('info', {})
            if live.get('running'):
                return False
            if (saved['profile'] == self.settings['HERMES_PROFILE'] and saved['session'] == self.stored
                    and saved['voice_model'] == live.get('model') and saved['voice_provider'] == live.get('provider')):
                result = self.rpc('config.set', self.scoped(key='model',
                    value=f"{saved['model']} --provider {saved['provider']} --reasoning {saved['effort']} --session", scope='session',
                    confirm_expensive_model=True))
                if result.get('confirm_required') or result.get('value') != saved['model']:
                    print(f"[restore_voice_model] Warning: could not restore text model {saved['model']}.")
        except Exception as e:
            print(f"[restore_voice_model] Error restoring model: {e}")
        self.voice_model_restore = None
        path.unlink(missing_ok=True)
        return True

    def restore_voice_effort(self, recover=False):
        path = self.root / '.runtime' / 'native-voice-effort.json'
        saved = self.voice_restore
        if recover and not saved and path.exists():
            try:
                saved = json.loads(path.read_text(encoding='utf-8'))
                self.voice_restore = saved
            except Exception:
                saved = None
        if not saved:
            return True
        try:
            live = self.rpc('session.activate', self.scoped(omit_messages=True)).get('info', {})
            if live.get('running'):
                return False
            if (saved['profile'] == self.settings['HERMES_PROFILE'] and saved['session'] == self.stored
                    and saved['model'] == live.get('model') and saved['provider'] == live.get('provider')):
                result = self.rpc('config.set', self.scoped(key='reasoning', value=saved['effort'], scope='session'))
                if result.get('value') != saved['effort']:
                    print(f"[restore_voice_effort] Warning: could not restore text reasoning {saved['effort']}.")
        except Exception as e:
            print(f"[restore_voice_effort] Error restoring reasoning: {e}")
        self.voice_restore = None
        path.unlink(missing_ok=True)
        return True

    def _handle_voice(self, body):
        action, actor, call_id = body['action'], body.get('actor'), body.get('call_id')
        if not actor or not isinstance(call_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', call_id):
            raise RuntimeError('Invalid voice call.')
        if action == 'voice_start':
            with self.guard:
                if self.voice_call or self.info.get('running'):
                    raise RuntimeError('Leo is already in a call or working. Wait for it to finish.')
                self.voice_call = (actor, call_id)
                self.voice_until = time.monotonic() + 90
            try:
                state = self.connect()
                if state['info'].get('running'):
                    raise RuntimeError('Leo is working. Wait for the current reply to finish.')
                self.prepare_voice_model(state['info'])
                return {'epoch': self.epoch, 'session_id': self.stored, 'call_id': call_id}
            except Exception:
                if self.restore_voice_model():
                    with self.guard:
                        self.voice_call = None
                raise
        with self.guard:
            if self.voice_call != (actor, call_id):
                raise RuntimeError('This voice call has ended or belongs to another connection.')
            self.voice_until = time.monotonic() + 30
        if action == 'voice_heartbeat':
            return {'active': True}
        if action in {'voice_stop', 'voice_end'}:
            if action == 'voice_stop' and body.get('turn_id') and body['turn_id'] != self.voice_turn:
                return {'stopped': False}
            self.clear_workspace()
            result = self.rpc('session.interrupt', self.scoped())
            deadline = time.monotonic() + 5
            while True:
                # None/unchanged reasoning has no restore journal. Still wait for
                # the old native worker to settle before accepting another turn.
                live = self.rpc('session.activate', self.scoped(omit_messages=True)).get('info', {})
                if live.get('running') is False and self.restore_voice_effort() and (
                        action != 'voice_end' or self.restore_voice_model()):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('Hermes is still stopping. End the call again shortly.')
                time.sleep(0.05)
            with self.guard:
                self.voice_turn = None
                self.actor = None
                if action == 'voice_end':
                    self.voice_call = None
                self.event_ready.notify_all()
            return result
        if action == 'voice_submit':
            text = body.get('text', '').strip()
            if not text or len(text) > 12000 or text.startswith('/'):
                raise RuntimeError('Speak a request. Run slash commands in text chat.')
            live = self.rpc('session.activate', self.scoped(omit_messages=True)).get('info', {})
            with self.guard:
                if self.voice_turn or live.get('running'):
                    raise RuntimeError('The previous reply has not stopped yet.')
                turn = body.get('turn_id') or str(uuid4())
                if not re.fullmatch(r'[a-f0-9-]{36}', turn):
                    raise RuntimeError('Invalid voice turn.')
                self.voice_turn = turn
                cursor = self.sequence
                self.actor, self.lease = actor, time.monotonic() + 30
                self.bind_workspace(body)
            restore = None
            try:
                if not self.restore_voice_effort():
                    raise RuntimeError('The previous voice reply is still settling.')
                effort, restore = self.prepare_voice_effort(live)
                self.rpc('prompt.submit', self.scoped(text=text, surface='voice-live'))
                return {'turn_id': turn, 'cursor': cursor, 'epoch': self.epoch, 'effort': effort}
            except Exception:
                if self.voice_restore:
                    self.rpc('session.interrupt', self.scoped())
                    self.restore_voice_effort()
                with self.guard:
                    self.voice_turn = None
                    self.clear_workspace()
                raise
        if action == 'voice_events':
            cursor, turn = int(body.get('cursor', 0)), body.get('turn_id')
            with self.event_ready:
                if turn != self.voice_turn or body.get('epoch') != self.epoch:
                    raise RuntimeError('The voice turn was interrupted or Hermes reconnected.')
                if self.events and cursor < self.events[0]['seq'] - 1:
                    raise RuntimeError('Voice events were missed. Start a new call.')
                self.event_ready.wait_for(lambda: self.sequence > cursor or self.voice_turn != turn, timeout=2)
                if self.voice_turn != turn:
                    raise RuntimeError('The voice turn was interrupted.')
                events = [e for e in self.events if e['seq'] > cursor and e.get('voice_turn') == turn]
                sequence = self.sequence
                self.actor, self.lease = actor, time.monotonic() + 30
                self.bind_workspace(body, renew=True)
            # Refresh settled state and pending approvals without reloading conversation history.
            if time.monotonic() - self.voice_probe_at > 1:
                self.voice_probe_at = time.monotonic()
                live = self.rpc('session.activate', self.scoped(omit_messages=True)).get('info', {})
                approvals = self.rpc('approval.pending', self.scoped()).get('approvals', [])
                with self.guard:
                    self.info.update(live)
                    self.voice_review = bool(approvals)
            return {'events': events, 'cursor': sequence, 'running': bool(self.info.get('running')), 'review': bool(self.voice_review or self.requests)}
        if action == 'voice_finish':
            if self.voice_turn != body.get('turn_id'):
                return {'finished': False}
            self.restore_voice_effort()
            with self.guard:
                if self.voice_turn == body.get('turn_id'):
                    self.voice_turn = None
                    self.actor = None
                    self.clear_workspace()
            return {'finished': True}
        raise RuntimeError('Unsupported voice action.')

    def watch(self):
        while not self.disposed:
            time.sleep(2)
            if not self.disposed:
                self.expire_leases()

    def shutdown(self):
        with self.control_guard:
            self.disposed = True
            self.close()
            atexit.unregister(self.close)

    def expire_leases(self):
        with self.control_guard:
            with self.guard:
                expired_call = self.voice_call if time.monotonic() > self.voice_until else None
            if expired_call:
                actor, call_id = expired_call
                try:
                    self.handle_voice({'action': 'voice_end', 'actor': actor, 'call_id': call_id})
                except Exception:
                    self.close()
                    self.voice_call = self.voice_turn = None
            if self.actor and time.monotonic() > self.lease:
                self.actor = None
                self.clear_workspace()
                try:
                    self.rpc('session.interrupt', self.scoped(), timeout=5)
                except Exception:
                    self.close()

    def close(self):
        self.clear_workspace()
        if self.os_tool_file:
            self.os_tool_file.unlink(missing_ok=True)
        self.os_tool_token = secrets.token_urlsafe(32)
        if self.process and self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'], capture_output=True)
                else:
                    import signal
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
