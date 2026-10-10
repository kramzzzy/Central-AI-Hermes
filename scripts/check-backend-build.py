"""Check native protocol, plugins and CPU audio without provider requests."""
import importlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def main():
    # Exercise the installed native loader, not just Python syntax/imports.
    from hermes_cli.plugins import PluginManager, PluginManifest
    from gateway.platform_registry import platform_registry
    from tools.registry import registry
    with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'HERMES_HOME': temporary}):
        Path(temporary, 'config.yaml').write_text('model: {provider: fixture, default: fixture}\n')
        manager = PluginManager()
        try:
            for name, version, kind, source in (
                ('central-ai-app-os', '1.0.0', 'standalone', '/opt/os-adapter/hermes_plugins/central_ai_app_os'),
                ('central-ai-whatsapp', '1.0.0', 'platform', '/opt/os-adapter/hermes_plugins/central_ai_whatsapp'),
                ('hindsight', '1.0.1', 'standalone', '/opt/hindsight-plugin'),
            ):
                manifest = PluginManifest(name=name, version=version, kind=kind, source='project', path=source)
                with patch('socket.socket.connect', side_effect=AssertionError('Plugin registration must be offline')):
                    manager._load_plugin(manifest)
                loaded = next(item for item in manager._plugins.values() if item.manifest.name == name)
                if not loaded.enabled or loaded.error:
                    raise RuntimeError('Native plugin registration failed: ' + name)
            if registry.get_entry('mcp__michael_os__inspect_workspace', scope=manager.scope_key) is None:
                raise RuntimeError('Native App OS tool was not registered')
            if not platform_registry.is_registered('central_whatsapp'):
                raise RuntimeError('Native WhatsApp platform was not registered')
        finally:
            manager.unload()
    for name in (
        'tools.voice_live', 'tui_gateway.entry', 'tui_gateway.server',
        'hermes_cli.plugins', 'gateway.platform_registry',
        'hermes_chat', 'hermes_voice', 'hermes_plugins.central_ai_app_os',
        'hermes_plugins.central_ai_whatsapp', 'whatsapp_audio',
        'whatsapp_text_supervisor', 'whatsapp_stream', 'os_stream_voice', 'native_voice_conversation',
        'faster_whisper', 'msgpack', 'livekit.rtc', 'onnxruntime',
    ):
        importlib.import_module(name)
    from tools import voice_live
    if not hasattr(voice_live, 'VOICE_LIVE_TURN_NOTE'):
        raise RuntimeError('Native live voice protocol is incompatible')
    from livekit import rtc
    processor = rtc.AudioProcessingModule(noise_suppression=True,
                                         echo_cancellation=True,
                                         high_pass_filter=True)
    processor.process_stream(rtc.AudioFrame(bytearray(320), 16000, 1, 160))
    from whatsapp_audio import speech_model
    speech_model()
    if not Path('/opt/setup/whatsapp-call-voice.py').is_file():
        raise RuntimeError('Maintained phone entry point is missing')
    # Real CPU speech/recognition with baked models, without provider APIs or
    # private audio. Also verify a canceled turn cannot emit another PCM frame.
    import io
    import wave
    from native_call_speech import pcm
    from local_call_asr import transcribe
    from stream_conversation import TurnControl, TurnCancelled
    with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {'HERMES_HOME': temporary}):
        path = Path(temporary) / 'config.yaml'
        voice_model = '/opt/voice-models/en_GB-alan-medium.onnx'
        if not Path(voice_model).exists() and Path('/opt/voice-models/en_US-lessac-medium.onnx').exists():
            voice_model = '/opt/voice-models/en_US-lessac-medium.onnx'
        path.write_text(f'tts:\n  provider: piper\n  piper:\n    voice: {voice_model}\n')
        config = {'CENTRAL_AI_CALL_SPEECH': 'piper', 'CENTRAL_AI_VOICE_CONFIG': str(path)}
        with patch('socket.socket.connect', side_effect=AssertionError('Baked speech must work offline')), \
             patch('tools.tts_tool_local._get_piper_voices_dir', side_effect=AssertionError('Speech cache must follow the selected profile')):
            control = TurnControl()
            frames = pcm('Please tell me the time in Brisbane.', config, 16000, control)
            first = next(frames)
            assert first and len(first) % 2 == 0
            control.cancel()
            try:
                next(frames)
            except TurnCancelled:
                pass
            else:
                raise AssertionError('Canceled speech continued')
            raw = b''.join(pcm('Please tell me the time in Brisbane.', config, 24000, TurnControl()))
            data = io.BytesIO()
            with wave.open(data, 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000)
                output.writeframes(raw)
            if 'brisbane' not in transcribe(data.getvalue()).lower():
                raise RuntimeError('Local speech round trip failed')
    print('PASS: native chat/voice protocol, plugin imports and CPU audio processing')


if __name__ == '__main__':
    main()
