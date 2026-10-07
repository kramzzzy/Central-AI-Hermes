"""Noise is not sent to ASR; beginnings, quiet voice and audio timing survive."""
import ast
import asyncio
import collections
import unittest
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).parents[1]
gate = next(n for n in ast.parse((root / 'whatsapp-audio.py').read_text()).body
            if isinstance(n, ast.ClassDef) and n.name == 'SpeechGate')
scope = {'deque': collections.deque}
exec(compile(ast.Module(body=[gate], type_ignores=[]), '<actual mic gate>', 'exec'), scope)
SpeechGate = scope['SpeechGate']


class GateTests(unittest.TestCase):
    def test_non_speech_noise_is_replaced_with_silence_without_changing_the_clock(self):
        gate = SpeechGate()
        noise = b'\xff\x03' * 960
        outputs = [gate.process(noise, False) for _ in range(60)]
        self.assertEqual(len(outputs), 60)
        self.assertTrue(all(pcm == bytes(1920) for pcm in outputs))
        self.assertLessEqual(len(gate.frames), 3)

    def test_late_speech_detection_retains_three_onset_frames_and_word_ending(self):
        gate = SpeechGate()
        frames = [bytes([index + 1]) * 1920 for index in range(16)]
        outputs = [gate.process(pcm, index in (5, 6, 7)) for index, pcm in enumerate(frames)]
        # Detector fires after the first consonants (frames 2..4), which must survive.
        self.assertEqual(outputs[5:15], frames[2:12])
        self.assertEqual(outputs[15], bytes(1920))

    def test_quiet_detected_speech_is_not_rejected_by_a_volume_threshold(self):
        gate = SpeechGate()
        quiet = b'\x01\x00' * 960
        outputs = [gate.process(quiet, True) for _ in range(8)]
        self.assertEqual(outputs[3:], [quiet] * 5)

    def test_new_call_cannot_inherit_pre_roll_or_open_state(self):
        old, new = SpeechGate(), SpeechGate()
        for _ in range(8):
            old.process(b'\x01\x00' * 960, True)
        self.assertEqual(new.process(b'\x02\x00' * 960, False), bytes(1920))
        self.assertFalse(new.forwarded)

    def test_bad_audio_size_is_rejected(self):
        with self.assertRaises(ValueError):
            SpeechGate().process(bytes(1919), True)


class FeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_fish_receives_silence_for_non_speech_noise(self):
        node = next(n for n in ast.parse(((root / 'whatsapp_fish.py').read_text() + '\n' + (Path(__file__).parents[1] / 'fish_conversation.py').read_text())).body
                    if isinstance(n, ast.ClassDef) and n.name == 'FishCall')
        captured = []
        scope = {'rtc': SimpleNamespace(AudioFrame=lambda data, *args: bytes(data)), 'asyncio': asyncio}
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<actual gated Fish mic feed>', 'exec'), scope)
        call = scope['FishCall'].__new__(scope['FishCall'])
        call.closed = False
        call.input = asyncio.Queue()
        call.input.put_nowait(b'\x01' + b'\xff\x03' * 960)
        call.audio_filter = SimpleNamespace(process=lambda *args, **kwargs: b'\x00' + b'\xff\x03' * 960)
        call.mic_gate = SpeechGate()
        call.service = SimpleNamespace(metrics={})
        async def capture(pcm):
            captured.append(pcm)
            call.closed = True
        call.source = SimpleNamespace(capture_frame=capture)
        await call.feed()
        self.assertEqual(b''.join(captured), bytes(1920))
        self.assertEqual(call.service.metrics['mic_frames_suppressed'], 1)


if __name__ == '__main__':
    unittest.main()
