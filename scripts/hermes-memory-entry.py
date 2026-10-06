"""Private memory inference through Hermes's native provider/auth implementation.

This never starts an agent, executes tools, or changes the chat/call model.
"""
import contextlib
import json
import sys
import time
import uuid


def wire(value):
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {key: wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    if hasattr(value, '__dict__'):
        return wire(vars(value))
    return value


def complete(body):
    from hermes_profile import load_profile
    load_profile()
    from agent.auxiliary_client import resolve_provider_client, _CodexCompletionsAdapter

    # The native auxiliary adapter handles rotating credentials, tool replay,
    # model vocabulary and bounded Responses streams. Add its missing JSON-format
    # mapping because Hindsight extraction requires structured output.
    class MemoryAdapter(_CodexCompletionsAdapter):
        def _build_responses_kwargs(self, kwargs):
            request, model, timeout = super()._build_responses_kwargs(kwargs)
            fmt = kwargs.get('response_format') or {}
            if fmt.get('type') == 'json_schema':
                schema = fmt['json_schema']
                request['text'] = {'format': {'type': 'json_schema',
                    'name': schema.get('name', 'memory'), 'schema': schema['schema'],
                    'strict': bool(schema.get('strict', False))}}
            elif fmt.get('type') == 'json_object':
                request['text'] = {'format': {'type': 'json_object'}}
            choice = kwargs.get('tool_choice')
            if isinstance(choice, str):
                request['tool_choice'] = choice
            elif isinstance(choice, dict) and choice.get('type') == 'function':
                request['tool_choice'] = {'type': 'function', 'name': choice['function']['name']}
            return request, model, timeout

    model = 'gpt-6-luna'
    client, model = resolve_provider_client(provider='openai-codex', model=model, raw_codex=True)
    try:
        result = MemoryAdapter(client, model).create(model=model,
            messages=body['messages'], tools=body.get('tools'),
            tool_choice=body.get('tool_choice'), response_format=body.get('response_format'),
            timeout=75, no_progress_timeout=30,
            extra_body={'reasoning': {'effort': 'none', 'enabled': False}})
        data = wire(result)
        data.update(id='memory-' + uuid.uuid4().hex, object='chat.completion', created=int(time.time()))
        return data
    finally:
        client.close()


if __name__ == '__main__':
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = complete(json.load(sys.stdin))
    except Exception as exc:
        # Native exceptions can contain provider credentials. Keep only the class.
        print('Memory inference failed: ' + type(exc).__name__, file=sys.stderr)
        result = {'error': {'message': 'Hermes memory model unavailable', 'type': 'server_error'}}
    print(json.dumps(result))
