"""Exercise real transcript handling against LiveKit's identity/EOS contract."""
import ast
import asyncio
import collections
import re
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

source=ast.parse(((Path(__file__).parents[1]/'whatsapp_fish.py').read_text() + '\n' + (Path(__file__).parents[1] / 'fish_conversation.py').read_text()))
node=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='FishCall')
scope=dict(asyncio=asyncio,re=re,time=time)
exec(compile(ast.Module(body=[node],type_ignores=[]),'<actual Fish transcript receiver>','exec'),scope)
FishCall=scope['FishCall']

class Reader:
    def __init__(self,chunks):
        self.chunks=iter(chunks)
        self.info=SimpleNamespace(id='stream-id',attributes={'lk.segment_id':'segment-1'})
        self.closed=False
    def __aiter__(self):return self
    async def __anext__(self):
        try:return next(self.chunks)
        except StopIteration:
            self.info.attributes['lk.transcription_final']='true'
            raise StopAsyncIteration
    def close(self):self.closed=True

class TranscriptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.call=FishCall.__new__(FishCall)
        self.call.room=SimpleNamespace(local_participant=SimpleNamespace(identity='owner'))
        self.call.state='speaking';self.call.generation=1
        self.call.last_speaking_end=0.
        self.call.last_interrupted_segment=self.call.last_cancelled_segment=None
        self.call.output=asyncio.Queue()
        self.call.output.put_nowait(b'stale speech')
        self.call.service=SimpleNamespace(metrics={},state_changed=asyncio.Event())
        self.call.recent=collections.OrderedDict()
        self.call.pending_notice=None
        self.call.latest_caller_text=''
        self.call.closed=False;self.call.connected=True
        self.announced=[]
        self.call.jobs=SimpleNamespace(announced=self.announced.append)
        self.cancelled=[];self.sent=[];self.tasks=[]
        self.call.bridge=SimpleNamespace(cancel=lambda:self.cancelled.append(True))
        self.call.task=lambda coro:self.tasks.append(asyncio.create_task(coro))
        async def publish(event):self.sent.append(event)
        self.call.publish=publish
    async def asyncTearDown(self):
        if self.tasks:await asyncio.gather(*self.tasks)
    async def test_owner_identity_string_and_incremental_correction_flush_only_once(self):
        reader=Reader(['Stop,',' I meant',' something else.'])
        await self.call.read_transcript(reader,'owner')
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.call.generation,2)
        self.assertTrue(self.call.output.empty())
        self.assertEqual(self.sent,[{'type':'user.interrupt'}])
        self.assertFalse(self.cancelled)
        self.assertEqual(self.call.recent['segment-1']['text'],'Stop, I meant something else.')
        self.assertEqual(self.call.recent['segment-1']['role'],'user')
        self.assertTrue(reader.closed)
    async def test_backchannel_does_not_flush_or_cancel_task(self):
        await self.call.read_transcript(Reader(['Okay.']),'owner')
        self.assertEqual(self.call.generation,1)
        self.assertFalse(self.call.output.empty())
        self.assertFalse(self.tasks)
    async def test_stop_after_fish_state_changed_still_clears_buffer_without_interrupting_new_thought(self):
        self.call.state='listening'
        self.call.last_speaking_end=time.monotonic()-2
        await self.call.read_transcript(Reader(['Stop. I meant another question.']),'owner')
        await asyncio.gather(*self.tasks)
        self.assertEqual(self.call.generation,2)
        self.assertTrue(self.call.output.empty())
        self.assertFalse(self.sent)
        self.assertFalse(self.cancelled)
    async def test_assistant_quoting_stop_cannot_cancel_owner_task(self):
        await self.call.read_transcript(Reader(['You can say stop.']),'agent')
        self.assertEqual(self.call.generation,1)
        self.assertFalse(self.tasks)
        self.assertEqual(self.call.recent['segment-1']['role'],'assistant')
    async def test_oversized_stream_closes_ffi_reader(self):
        reader=Reader(['a'*12001])
        await self.call.read_transcript(reader,'owner')
        self.assertTrue(reader.closed)
        self.assertFalse(self.call.recent)

    async def test_finished_spoken_update_marks_result_after_transcript_closes(self):
        notice={'kind':'result','tasks':[{'task_id':'report'}]}
        self.call.pending_notice={'notice':notice,'generation':1,'sent':time.monotonic()}
        await self.call.read_transcript(Reader(['The report is ready.']),'agent')
        self.assertFalse(self.announced)
        self.assertTrue(self.call.pending_notice['spoken'])

    async def test_caller_interruption_keeps_result_unannounced(self):
        self.call.pending_notice={'notice':{'kind':'result'},'generation':1,'sent':time.monotonic()}
        await self.call.read_transcript(Reader(['Stop. I have another question.']),'owner')
        await self.call.read_transcript(Reader(['The report']),'agent')
        self.assertFalse(self.announced)
        self.assertIsNone(self.call.pending_notice)

    async def test_stream_interrupted_while_open_does_not_acknowledge_result(self):
        self.call.pending_notice={'notice':{'kind':'result'},'generation':1,'sent':time.monotonic()}
        original=Reader(['The report'])
        async def chunks():
            yield 'The report'
            self.call.flush()
        original.__class__=type('InterruptReader',(Reader,),{'__aiter__':lambda _:chunks()})
        await self.call.read_transcript(original,'agent')
        self.assertFalse(self.announced)

if __name__=='__main__':unittest.main()
