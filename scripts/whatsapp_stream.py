"""Owned phone audio engine; reuses the paired device, noise filter and jobs.

Only synthetic/provider audio is used by operator checks. This module never
initiates a phone call or chooses a recipient.
"""
import asyncio
from whatsapp_routing import routing
from collections import deque
import json
import re
import struct
import time

from stream_conversation import Conversation, FreeSpeech, TurnControl, TurnCancelled, confirmed_text_key
from fish_conversation import filter_playback_input
from whatsapp_audio import PhoneAudioFilter

class PhonePCM:
    """Keep provider chunks continuous; pad only the final transport frame."""
    frame_bytes = 1920
    prefill_frames = 1
    single_burst_frames = 4

    def __init__(self):
        self.pending = bytearray()
        self.started = False
        self.chunks = self.previous_padding = self.padding = 0
        self.first_output = None
        self.source_end = None

    def frames(self, data, final=False):
        if len(data) % 2:
            raise ValueError('PCM must contain whole samples')
        if data:
            self.chunks += 1
            self.previous_padding += (-len(data)) % self.frame_bytes
            self.pending.extend(data)
        # Prefill 2 frames (120ms) for low-latency streaming without holding speech back
        if not self.started and not final:
            if len(self.pending) < self.frame_bytes * self.prefill_frames:
                return
        self.started = True
        while len(self.pending) >= self.frame_bytes:
            frame = bytes(self.pending[:self.frame_bytes])
            del self.pending[:self.frame_bytes]
            yield frame
        if final and self.pending:
            self.padding = self.frame_bytes - len(self.pending)
            yield bytes(self.pending) + bytes(self.padding)
            self.pending.clear()


class StreamPhoneCall:
    def __init__(self, service, call, member=None, callback_notice=None):
        self.service, self.call = service, call
        self.member = member or service.members[routing()['owner']]
        self.jobs = self.member['jobs']
        self.input, self.output = asyncio.Queue(maxsize=32), asyncio.Queue(maxsize=16)
        self.closed = self.connected = False
        self.state, self.generation = 'initializing', 1
        self.tasks, self.worker = set(), None
        self.control = None
        self.audio_filter = PhoneAudioFilter(service.vad)
        self.pre = deque(maxlen=4)
        self.capture = bytearray()
        self.voiced = self.speech_run = 0
        self.silence_frames = 0
        self.capture_extended = False
        self.last_user_activity = self.last_speech = time.monotonic()
        self.playback_until = self.last_speaking_end = 0.
        self.last_notice = self.last_progress = 0.
        self.pending_notice = None
        self.callback_notice = callback_notice
        self.greeting_pending = True
        self.received_input = False
        self.connected_at = 0.
        self.rendered_generation, self.last_rendered = None, 0.
        self.last_recovery = 0.
        self.last_clarify_at = 0.
        self.asr = None
        self.prepared_speech = None
        self.speculative=None
        self.pending_input=None
        self.spoken_text=''
        self.capture_echo_text=''
        self.capture_rejected=False
        self.early_asr=None
        self.capture_version=0
        self.callback_new_tasks_since = time.time() if callback_notice else None
        self.conversation = None
        self.fallback_conversation = None

    def _get_fallback_conversation(self):
        if self.fallback_conversation is not None:
            return self.fallback_conversation
        try:
            from central_ai_identity import central_ai_identity, get_company_name
            company = get_company_name()
        except Exception:
            def central_ai_identity(x): return x
            company = ""
        company_str = f" representing {company}" if company else ""
        caller_name = self.member.get('name', '') or 'the caller'
        prompt = central_ai_identity(
            f"You are Leo, the personal assistant{company_str}, speaking with {caller_name} on a live WhatsApp voice call.\n"
            "Speak naturally, warmly, and concisely in 1 to 2 short sentences, like an attentive human on a phone call. No markdown or bullet lists.\n"
            "Use brief, natural conversational acknowledgments ('Got it', 'No worries at all', 'Right, that makes sense') when appropriate, "
            "and before running a tool, give a quick natural heads-up ('All right, let me check that for you now')."
        )
        try:
            from pathlib import Path
            from hermes_plugins.central_ai_app_os.client import _DEFAULT_YCS_SNAPSHOT
            snap = None
            for candidate in (
                Path('/data/box3_snapshot.json'),
                Path('/opt/data/box3_snapshot.json'),
                Path('/opt/os-adapter/.runtime/box3_snapshot.json'),
            ):
                if candidate.is_file():
                    data = json.loads(candidate.read_text(encoding='utf-8'))
                    if isinstance(data, dict) and ('knowledge' in data or 'counts' in data):
                        snap = data
                        break
            if not snap:
                snap = dict(_DEFAULT_YCS_SNAPSHOT)
            counts = snap.get('counts') or _DEFAULT_YCS_SNAPSHOT['counts']
            cards = snap.get('knowledge') or _DEFAULT_YCS_SNAPSHOT['knowledge']
            titles = [str(c.get('title') or '') for c in cards[:12] if isinstance(c, dict) and c.get('title')]
            prompt += (
                f"\n\n[Live Workspace Summary: {counts.get('knowledge_sources', len(titles))} knowledge cards "
                f"({', '.join(titles[:8])}), {counts.get('available_skills', 60)} skills, "
                "and full Box 3 tools (inspect_workspace, manage_crm, manage_tasks, search_knowledge, "
                "get_knowledge, add_knowledge, get_workspace_overview, get_live_weather, send_whatsapp_message, "
                "call_whatsapp_contact) are active. Call tools only when specific workspace data or actions are needed.]"
            )
        except Exception:
            pass
        def dispatch_tool(event, caller_text):
            return self.jobs.handle(self.call, event, self.service.allowed)
        self.fallback_conversation = Conversation(self.service.config, prompt, dispatch_tool)
        return self.fallback_conversation

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        def finished(value):
            self.tasks.discard(value)
            if not value.cancelled() and value.exception():
                self.service.metrics['stream_task_errors'] = self.service.metrics.get('stream_task_errors',0)+1
        task.add_done_callback(finished)
        return task

    def state_event(self):
        return json.dumps({'type':'state','state':self.state,'generation':self.generation})

    def change_state(self, state):
        if not self.closed:
            if self.state=='speaking' and state!='speaking': self.last_speaking_end=time.monotonic()
            self.state=state;self.service.metrics['state']=state;self.service.state_changed.set()

    def flush(self):
        self.generation += 1
        self.capture_extended = False
        if self.control: self.control.cancel()
        if self.prepared_speech:
            self.prepared_speech[0].cancel();self.prepared_speech=None
        if self.speculative:self.speculative.cancel();self.speculative=None
        self.pending_notice = None
        self.discard_early_asr()
        if self.pending_input:
            self.close_asr(self.pending_input.get('stream'))
            self.close_asr(self.pending_input.get('prepared_asr'))
            self.close_asr(self.pending_input.get('confirmation_asr'))
        self.pending_input = None
        self.playback_until = 0.
        while not self.output.empty(): self.output.get_nowait()
        self.service.metrics['interruptions']=self.service.metrics.get('interruptions',0)+1
        self.change_state('listening')

    def resume_input(self):
        pending=self.pending_input
        if self.state!='thinking' or not pending or pending['generation']!=self.generation:
            return False
        with pending['control'].guard:
            if pending.get('speaking_started') or pending['control'].cancelled.is_set():return False
            previous=pending['pcm']
            self.flush()
            self.capture=bytearray(previous)+self.capture
            if getattr(self.service,'streaming_asr',None):
                self.close_asr(self.asr)
                self.asr=self.service.streaming_asr.new_turn()
                self.asr.feed(bytes(self.capture))
            self.service.metrics['resumed_utterances']=self.service.metrics.get('resumed_utterances',0)+1
            return True

    def transport_lost(self):
        self.flush()
        self.capture.clear();self.pre.clear();self.voiced=self.speech_run=0
        self.silence_frames=0
        self.capture_extended=False
        self.capture_echo_text=''
        self.capture_rejected=False
        self.close_asr(self.asr);self.asr=None
        self.change_state('reconnecting')

    def transport_ready(self):
        self.connected_at=time.monotonic()
        if not self.greeting_pending:self.change_state('listening')

    @staticmethod
    def close_asr(stream):
        if stream and hasattr(stream,'close'):stream.close()

    def discard_early_asr(self):
        if self.early_asr:
            self.close_asr(self.early_asr.get('recognizer'))
            self.close_asr(self.early_asr.get('confirmation'))
            if self.early_asr.get('control'):
                self.early_asr['control'].cancel()
            self.early_asr=None

    def prepare_recognition(self):
        if self.early_asr:return
        cloud=getattr(self.service,'cloud_asr',None)
        local_fn=getattr(self.service,'recognize',None)
        if not hasattr(cloud,'prepare') and not local_fn:return
        generation=self.generation
        version=self.capture_version
        control=TurnControl(lambda:not self.closed and self.generation==generation
                            and self.service.allowed(self.call))
        pcm=bytes(self.capture)
        remote=getattr(self.asr,'remote',False)
        source=self.asr.recognizer.source if remote else (cloud.primary if cloud else 'local')
        recognizer=None if (remote or not hasattr(cloud,'prepare')) else cloud.prepare(pcm,control)
        local_done=asyncio.Event()
        early_entry={'recognizer':recognizer,'confirmation':None,
                     'confirmation_source':source,'version':version,'pcm':pcm,
                     'control':control,'local_result':None,'local_done':None}
        if local_fn and not remote:
            import threading
            done_evt=threading.Event()
            early_entry['local_done']=done_evt
            def _run_early_local():
                try:
                    res=local_fn(pcm)
                    early_entry['local_result']=res
                    l_text,l_qual=res
                    if (l_text and l_qual.get('decision')=='accepted'
                            and not control.cancelled.is_set()
                            and self.generation==generation
                            and self.capture_version==version):
                        conv=self._get_fallback_conversation()
                        if conv and not self.speculative:
                            self.speculative=conv.prefetch(l_text,control)
                except Exception:
                    pass
                finally:
                    done_evt.set()
            threading.Thread(target=_run_early_local,daemon=True).start()
        self.early_asr=early_entry
        self.service.metrics['early_recognitions_started']=self.service.metrics.get('early_recognitions_started',0)+1

    async def _warm_native_conversation(self):
        try:
            await asyncio.to_thread(self._get_fallback_conversation)
        except Exception:
            pass
        if not getattr(self.service, 'native_conversation', None):
            return
        try:
            conv = await asyncio.to_thread(self.service.native_conversation, self.call, self.member)
            if self.closed:
                await asyncio.to_thread(conv.close)
            else:
                self.conversation = conv
        except Exception as exc:
            print(f"[StreamPhoneCall] native_conversation init fallback to fast stream: {exc}", flush=True)

    async def start(self):
        self.task(self._warm_native_conversation())
        self.task(self.feed());self.task(self.tick())
        # A paired-device socket can connect while the handset is still
        # ringing. Wait for real media before producing the greeting.
        self.prepare_speech()

    def prepare_speech(self):
        if self.prepared_speech:return
        loop=asyncio.get_running_loop();generation=self.generation
        control=TurnControl(lambda: not self.closed and self.service.allowed(self.call)
                            and self.generation==generation)
        framer=PhonePCM()
        def output(data,final=False):
            control.check()
            future=asyncio.run_coroutine_threadsafe(self.enqueue(data,generation,control,framer,final),loop)
            try:future.result(timeout=3)
            except Exception:future.cancel();raise TurnCancelled()
        speech=FreeSpeech(self.service.config,output,control,rate=16000)
        self.prepared_speech=(control,speech,framer,output)

    def start_reply(self, audio=None, notice=None, greeting=False):
        if self.worker and not self.worker.done(): return False
        generation = self.generation
        if self.prepared_speech and (
                self.prepared_speech[0].cancelled.is_set()
                or self.prepared_speech[1].error
                or self.prepared_speech[1].done.is_set()):
            self.prepared_speech[0].cancel();self.prepared_speech=None
        self.prepare_speech()
        prepared,self.prepared_speech=self.prepared_speech,None
        speculative,self.speculative=self.speculative,None
        if speculative and audio is None:speculative.cancel();speculative=None
        control=prepared[0]
        prepared[2].source_end=audio.get('speech_ended') if isinstance(audio,dict) else None
        self.control=control
        pending_input=({'pcm':audio['pcm'] if isinstance(audio,dict) else audio,
                        'stream':audio.get('stream') if isinstance(audio,dict) else None,
                        'prepared_asr':audio.get('prepared_asr') if isinstance(audio,dict) else None,
                        'confirmation_asr':audio.get('confirmation_asr') if isinstance(audio,dict) else None,
                        'control':control,'generation':generation,'committed':False} if audio is not None else None)
        self.pending_input=pending_input
        self.change_state('thinking')
        self.worker=self.task(self.reply(audio,notice,greeting,control,generation,prepared,speculative,pending_input))
        return True

    async def reply(self,audio,notice,greeting,control,generation,prepared=None,speculative=None,pending_input=None):
        loop=asyncio.get_running_loop()
        stage='speech_setup'
        framer=PhonePCM()
        def output(data,final=False):
            control.check()
            future=asyncio.run_coroutine_threadsafe(self.enqueue(data,generation,control,framer,final),loop)
            try: future.result(timeout=3)
            except Exception: future.cancel();raise TurnCancelled()
        def run():
            nonlocal framer,output,speculative,stage
            began=time.monotonic()
            if prepared:_,speech,framer,output=prepared
            else:speech=FreeSpeech(self.service.config,output,control,rate=16000)
            first=None
            hermes_started=None
            timing={}
            def speak(value):
                nonlocal first,stage
                if pending_input:
                    pending_input['speaking_started']=True
                if first is None:self.spoken_text=''
                previous=stage;stage='speech'
                first=first or time.monotonic();speech.text(value)
                stage=previous
                self.spoken_text=(self.spoken_text+value)[-2400:]
            try:
                if greeting:
                    speak('Hello! What can I help you with?')
                elif notice:
                    conv = self.conversation or self._get_fallback_conversation()
                    conv.reply(json.dumps(notice,ensure_ascii=False)[:12000],speak,control,notice=True)
                else:
                    stage='recognition'
                    pcm=audio['pcm'] if isinstance(audio,dict) else audio
                    streaming=audio.get('stream') if isinstance(audio,dict) else None
                    cloud=getattr(self.service,'cloud_asr',None)
                    confirmation_asr=audio.get('confirmation_asr') if isinstance(audio,dict) else None
                    if confirmation_asr:control.track(confirmation_asr)
                    used_local = False
                    text, quality = '', {'decision': 'clarify', 'source': 'local'}
                    early = audio.get('prepared_asr') if isinstance(audio, dict) else None
                    if streaming and getattr(streaming,'remote',False):
                        text,quality=streaming.finish(control)
                        if (not text or quality.get('fallback_allowed')) and cloud:
                            text,quality=cloud.recognize(pcm,control)
                    elif getattr(self.service, 'recognize', None):
                        # Fast path (~100ms, or 0ms if completed during early_quiet pause):
                        early_local_done = audio.get('early_local_done') if isinstance(audio, dict) else None
                        early_entry = audio.get('early_entry') if isinstance(audio, dict) else None
                        if early_local_done is not None and early_entry is not None:
                            early_local_done.wait(timeout=0.35)
                        if early_entry and early_entry.get('local_result') is not None:
                            local_text, local_quality = early_entry['local_result']
                        else:
                            local_text, local_quality = self.service.recognize(pcm)
                        used_local = True
                        confident_local = (
                            bool(local_text)
                            and local_quality.get('decision') == 'accepted'
                            and local_quality.get('avg_logprob', -2.0) >= -0.85
                            and local_quality.get('no_speech_prob', 1.0) <= 0.45
                        )
                        if confident_local or not cloud:
                            text, quality = local_text, local_quality
                            if early:
                                self.close_asr(early)
                        else:
                            if early:
                                control.track(early)
                                try:
                                    text, quality = early.finish(control)
                                    self.service.metrics['early_recognitions_used'] = self.service.metrics.get('early_recognitions_used', 0) + 1
                                except Exception:
                                    control.check()
                                    text, quality = cloud.recognize(pcm, control)
                            else:
                                text, quality = cloud.recognize(pcm, control)
                            if (not text or quality.get('decision') != 'accepted') and local_text:
                                text, quality = local_text, local_quality
                    elif cloud:
                        if early:
                            control.track(early)
                            try:
                                text,quality=early.finish(control)
                                self.service.metrics['early_recognitions_used']=self.service.metrics.get('early_recognitions_used',0)+1
                            except Exception:
                                control.check()
                                text,quality=cloud.recognize(pcm,control)
                        else:
                            text,quality=cloud.recognize(pcm,control)
                    control.check()
                    self.service.metrics['last_asr_ms']=round((time.monotonic()-began)*1000)
                    timing['transcription_ms']=self.service.metrics['last_asr_ms']
                    timing['recognition_source']=quality.get('source','local')
                    timing['recognition_decision']=quality.get('decision')
                    self.service.metrics['last_recognition']=quality
                    echo_text=audio.get('echo_text','') if isinstance(audio,dict) else ''
                    normalize=lambda value:' '.join(re.sub(r'[^\w\s]','',value.casefold()).split())
                    candidate=normalize(text)
                    if candidate and echo_text and (len(candidate.split())>=3 or len(candidate)>=8) and (' '+candidate+' ') in (' '+normalize(echo_text)+' '):
                        self.service.metrics['rejected_playback_echoes']=self.service.metrics.get('rejected_playback_echoes',0)+1
                        return
                    filtered = filter_playback_input(text, [echo_text] if echo_text else [])
                    if filtered is None:
                        speak('Speaker audio mixed with your words. Please repeat your request; no new work was started.')
                        speech.finish(); output(b'', final=True)
                        return 'complete'
                    text = filtered
                    already_extended = isinstance(audio, dict) and bool(audio.get('extended'))
                    if not text:
                        now_mono = time.monotonic()
                        if (quality.get('decision')=='clarify'
                                and len(pcm) >= 16000 * 2 * 2.2
                                and now_mono - self.last_speaking_end >= 2.5
                                and now_mono - self.last_clarify_at >= 18.0):
                            self.last_clarify_at = now_mono
                            speak("Sorry, I missed that. Could you say that again?")
                        else:
                            return
                    elif (not already_extended and not text.rstrip().endswith('?')
                            and re.search(r'\b(and|because|but|if|with|to|the|my|a|an|or|about|so|well|like|um|uh|er|hmm|thinking)\W*$',text,re.I)):
                        # Obvious unfinished clauses or hesitations get one short opportunity
                        # to continue, rather than dispatching a draft request.
                        return 'continue'
                    from fish_conversation import is_goodbye_intent
                    if is_goodbye_intent(text):
                        speak("Goodbye! Have a great day.")
                        speech.finish()
                        output(b'', final=True)
                        return 'goodbye'
                    else:
                        with control.guard:
                            control.check()
                            if pending_input:pending_input['committed']=True
                        self.service.metrics['user_turns']=self.service.metrics.get('user_turns',0)+1
                        stage='hermes'
                        hermes_started=time.monotonic()
                        # Use ultra-low-latency direct streaming Conversation (~220ms first token)
                        # as primary for live phone turn-taking, falling back to native_conversation if needed.
                        conv = self._get_fallback_conversation() or self.conversation
                        def _on_tool_pending(tool_name, caller_text, need_ack):
                            if need_ack and first is None:
                                ack = "All right, let me check that for you now. "
                                speak(ack)
                                return ack
                            return None
                        try:
                            if isinstance(conv, Conversation):
                                conv.reply(text,speak,control,speculative=speculative,on_tool_pending=_on_tool_pending)
                            else:
                                conv.reply(text,speak,control)
                        except TurnCancelled:
                            raise
                        except Exception as conv_exc:
                            control.check()
                            if first is None and self.conversation and self.conversation is not conv:
                                self.conversation.reply(text,speak,control)
                                conv = self.conversation
                            else:
                                raise conv_exc
                        finally:
                            self.service.metrics['last_conversation_timing']=getattr(conv,'last_timing',{})
                stage='speech'
                speech.finish()
                output(b'',final=True)
                self.service.metrics['last_tts_chunks']=framer.chunks
                self.service.metrics['last_audio_padding_ms']=round(framer.padding/32,2)
                self.service.metrics['last_audio_padding_avoided_ms']=round((framer.previous_padding-framer.padding)/32,2)
                self.service.metrics['spoken_replies']=self.service.metrics.get('spoken_replies',0)+1
                self.service.metrics['last_reply_first_text_ms']=round(((first or began)-began)*1000)
                self.service.metrics['last_reply_first_audio_ms']=round(((speech.first_audio or began)-began)*1000)
                return 'complete'
            finally:
                if audio is not None:
                    end=audio.get('speech_ended') if isinstance(audio,dict) else None
                    timing.update(
                        endpoint_ms=round((began-end)*1000) if end is not None else None,
                        hermes_first_text_ms=round((first-hermes_started)*1000) if first is not None and hermes_started is not None else None,
                        speech_first_audio_ms=round((speech.first_audio-first)*1000) if speech.first_audio is not None and first is not None else None,
                        total_first_audio_ms=round((speech.first_audio-end)*1000) if speech.first_audio is not None and end is not None else None,
                        stage=stage, cancelled=control.cancelled.is_set())
                    self.service.metrics['last_turn_timing']=timing
                    print(json.dumps({'phone_voice_timing':timing}),flush=True)
                if pending_input:
                    self.close_asr(pending_input.get('stream'))
                    self.close_asr(pending_input.get('prepared_asr'))
                    self.close_asr(pending_input.get('confirmation_asr'))
                control.cancel()
                if speculative:speculative.cancel()
        try:
            result=await asyncio.to_thread(run)
            if result=='goodbye':
                await asyncio.sleep(1.2)
                await self.close()
                return
            if result=='continue' and not self.closed and self.generation==generation:
                self.capture=bytearray(audio['pcm'] if isinstance(audio,dict) else audio)+self.capture
                self.capture_extended=True
                self.asr=None;self.last_speech=time.monotonic()+.5;self.voiced=max(self.voiced,2)
                if getattr(self.service,'streaming_asr',None):
                    self.asr=self.service.streaming_asr.new_turn();await asyncio.to_thread(self.asr.feed,bytes(self.capture))
            elif result=='complete' and notice and self.generation==generation:
                self.pending_notice={'notice':notice,'generation':generation,'after':self.playback_until+.8}
            if greeting and self.callback_notice and self.generation==generation:
                self.pending_notice=None  # Callback result is announced in the next quiet gap.
        except (TurnCancelled,asyncio.CancelledError): pass
        except Exception as exc:
            self.service.metrics['reply_errors']=self.service.metrics.get('reply_errors',0)+1
            # Log failure location, never transcripts, tokens or provider bodies.
            import traceback
            failure={'stage':stage,'exception':type(exc).__name__,
                     'frames':[{'function':frame.name,'line':frame.lineno}
                               for frame in traceback.extract_tb(exc.__traceback__)[-6:]]}
            self.service.metrics['last_reply_failure']=failure
            print(json.dumps({'phone_voice_reply_failure':failure}),flush=True)
            # Fail only this reply. Never disconnect/replay an accepted action.
            if (not self.closed and self.generation==generation and self.connected
                    and self.service.allowed(self.call) and time.monotonic()-self.last_recovery>10
                    and getattr(self.service,'recovery',None)):
                self.last_recovery=time.monotonic()
                recovery=TurnControl(lambda: not self.closed and self.generation==generation
                                     and self.service.allowed(self.call))
                self.control=recovery
                try:
                    pcm=await asyncio.to_thread(self.service.recovery)
                    await self.enqueue(pcm,generation,recovery)
                except Exception:pass
                finally:
                    recovery.cancel()
                    if self.control is recovery:self.control=None
        finally:
            if self.pending_input is pending_input:self.pending_input=None
            if self.control is control: self.control=None
            if not self.closed and self.generation==generation and time.monotonic()>=self.playback_until:
                self.change_state('listening')

    async def enqueue(self,data,generation,control,framer=None,final=True):
        control.check()
        framer=framer or PhonePCM()
        for pcm in framer.frames(data,final=final):
            control.check()
            await self.output.put(struct.pack('>Q',generation)+pcm)
            if framer.first_output is None:
                framer.first_output=time.monotonic()
                if framer.source_end:
                    self.service.metrics['last_reply_first_transport_ms_from_speech_end']=round((framer.first_output-framer.source_end)*1000)
            self.playback_until=max(self.playback_until,time.monotonic())+(.45 if final else .06)
            self.change_state('speaking')

    async def feed(self):
        while not self.closed:
            frame=await self.input.get()
            self.received_input=True
            reverse=frame[0]==2
            filtered=self.audio_filter.process(frame[1:],reverse=reverse)
            if reverse:
                # The caller's reverse stream is the audio actually rendered by
                # WhatsApp. It feeds echo cancellation, not recognition.
                if any(frame[1:]):
                    self.rendered_generation=self.generation;self.last_rendered=time.monotonic()
                continue
            speaking,pcm=bool(filtered[0]),filtered[1:]
            if speaking and self.capture and self.silence_frames >= 2 and self.speech_run == 0 and len(pcm) == 1920:
                samples = struct.unpack('<960h', pcm)
                rms = (sum(s * s for s in samples) / 960) ** 0.5 / 32768.0
                if rms < 0.008:
                    speaking = False
            self.silence_frames=0 if speaking else min(self.silence_frames+1,20)
            self.service.metrics['processed_frames']=self.service.metrics.get('processed_frames',0)+1
            if self.capture_rejected:
                if speaking:self.last_speech=time.monotonic()
                continue
            if speaking:
                self.capture_version+=1
                self.discard_early_asr()
                if self.speculative:self.speculative.cancel();self.speculative=None
                self.speech_run+=1;self.last_user_activity=self.last_speech=time.monotonic();self.voiced+=1
                # Resume before committing a prepared final turn, even if the
                # caller has only just resumed. The three-frame threshold still
                # applies to interrupting speech that was already committed.
                if self.state=='thinking' and self.pending_input:self.resume_input()
                if self.speech_run>=2 and (self.state in {'speaking','thinking'} or self.playback_until>time.monotonic()):
                    if not self.resume_input():self.flush()
            else: self.speech_run=0
            if not self.capture:
                if not speaking: self.pre.append(pcm);continue
                if (self.state=='speaking' or time.monotonic()-self.last_rendered<.55 or time.monotonic()-self.last_speaking_end<.45) and self.speech_run<3:
                    self.pre.append(pcm);continue
                prefix=b''.join(self.pre);self.capture.extend(prefix);self.pre.clear()
                self.capture_echo_text=self.spoken_text if self.state=='speaking' or time.monotonic()-self.last_rendered<.8 else ''
                if getattr(self.service,'streaming_asr',None):
                    self.asr=self.service.streaming_asr.new_turn()
                    if prefix:await asyncio.to_thread(self.asr.feed,prefix)
            self.capture.extend(pcm)
            if self.speech_run>=3 and not self.prepared_speech and (not self.worker or self.worker.done()):self.prepare_speech()
            if self.asr:
                try:await asyncio.to_thread(self.asr.feed,pcm)
                except Exception:
                    self.close_asr(self.asr);self.asr=None;self.service.metrics['streaming_asr_errors']=self.service.metrics.get('streaming_asr_errors',0)+1
            if len(self.capture)>16000*2*30:
                # Never dispatch a truncated turn that could lose its initial
                # negation. The caller gets a fresh turn without any action.
                self.close_asr(self.asr);self.asr=None
                self.capture.clear();self.voiced=0;self.capture_extended=False
                self.capture_rejected=True;self.pre.clear()
                self.discard_early_asr()
                self.service.metrics['rejected_capture_limit']=self.service.metrics.get('rejected_capture_limit',0)+1

    async def tick(self):
        while not self.closed:
            await asyncio.sleep(.03)
            now=time.monotonic()
            busy=self.worker and not self.worker.done()
            if self.capture_rejected:
                if (self.silence_frames>=8 or now-self.last_speech>=.85) and now-self.last_speech>=.65:
                    self.capture_rejected=False
                continue
            if self.greeting_pending:
                if self.connected and (self.received_input or (self.connected_at and now - self.connected_at >= .45)) and not busy:
                    self.greeting_pending=False;self.start_reply(greeting=True)
                continue
            # Start background cloud recognition early (~180ms into a pause) so it is
            # already in flight if local Whisper needs fallback at the endpoint.
            early_quiet = (self.silence_frames >= 3 and now - self.last_speech >= .18) or (now - self.last_speech >= .28)
            if self.capture and self.voiced >= 2 and early_quiet and not self.early_asr and not busy:
                self.prepare_recognition()
            # Support both continuous silent RTP frames and WhatsApp Opus DTX
            # silence suppression (where RTP packets stop arriving during quiet gaps).
            quiet_enough = (self.silence_frames >= 6 and now - self.last_speech >= .36) or (now - self.last_speech >= .50)
            if (self.capture and self.voiced>=2 and quiet_enough and self.input.empty() and not busy):
                self.prepare_recognition()
                audio={
                    'pcm':bytes(self.capture),
                    'stream':self.asr,
                    'echo_text':self.capture_echo_text,
                    'speech_ended':self.last_speech,
                    'extended':self.capture_extended,
                }
                self.asr=None
                self.capture_extended=False
                if self.early_asr:
                    early=self.early_asr;self.early_asr=None
                    if early['version']==self.capture_version and audio['pcm']==early['pcm']:
                        audio['prepared_asr']=early.get('recognizer')
                        audio['confirmation_asr']=early.get('confirmation')
                        audio['confirmation_source']=early.get('confirmation_source')
                        audio['early_local_done']=early.get('local_done')
                        audio['early_entry']=early
                    elif early['version']==self.capture_version and audio['pcm'].startswith(early['pcm']):
                        audio['prepared_asr']=early.get('recognizer')
                        audio['confirmation_asr']=early.get('confirmation')
                        audio['confirmation_source']=early.get('confirmation_source')
                        audio['early_local_done']=early.get('local_done')
                        audio['early_entry']=early
                    else:
                        self.close_asr(early.get('recognizer'))
                        self.close_asr(early.get('confirmation'))
                        if early.get('control'):
                            early['control'].cancel()
                self.capture_echo_text=''
                self.capture.clear();self.voiced=0
                self.silence_frames=0
                self.start_reply(audio=audio);continue
            if (self.capture and self.voiced<2 and now-self.last_speech>=.85 and self.input.empty()):
                self.capture.clear();self.voiced=0;self.capture_extended=False;self.close_asr(self.asr);self.asr=None
                self.discard_early_asr()
            if not busy and now>=self.playback_until and self.state not in {'listening','idle'}:
                self.change_state('listening')
            if (self.pending_notice and not busy and not self.capture and now>=self.pending_notice['after']
                    and self.rendered_generation==self.generation and now-self.last_rendered>=.4):
                pending=self.pending_notice
                if pending['generation']==self.generation and self.connected:
                    self.jobs.announced(pending['notice'])
                    if pending['notice']['kind']=='progress':self.last_progress=time.time()
                    if self.callback_notice==pending['notice']:self.callback_notice=None
                    self.service.metrics['background_updates_spoken']=self.service.metrics.get('background_updates_spoken',0)+1
                self.pending_notice=None
            if busy or self.capture or not self.connected or self.pending_notice or self.state!='listening':continue
            if now-self.last_user_activity<1.5 or now-self.last_speaking_end<.8 or now-self.last_notice<3:continue
            notice=self.callback_notice or self.jobs.notice(time.time(),self.last_progress,
                **({'since':self.callback_new_tasks_since} if self.callback_new_tasks_since else {}))
            if notice:
                self.last_notice=now;self.start_reply(notice=notice)

    async def close(self):
        self.closed=True
        self.close_asr(self.asr);self.asr=None
        self.discard_early_asr()
        if self.pending_input:
            self.close_asr(self.pending_input.get('stream'))
            self.close_asr(self.pending_input.get('prepared_asr'))
            self.close_asr(self.pending_input.get('confirmation_asr'))
        self.pending_input=None
        if self.control:self.control.cancel()
        if self.conversation:
            await asyncio.to_thread(self.conversation.close)
        if self.prepared_speech:self.prepared_speech[0].cancel();self.prepared_speech=None
        if self.speculative:self.speculative.cancel();self.speculative=None
        others = [task for task in self.tasks if task is not asyncio.current_task()]
        for task in others:task.cancel()
        if others:await asyncio.gather(*others,return_exceptions=True)
        while not self.output.empty():self.output.get_nowait()
