from whatsapp_routing import routing
"""Owned phone audio engine; reuses the paired device, noise filter and jobs.

Only synthetic/provider audio is used by operator checks. This module never
initiates a phone call or chooses a recipient.
"""
import asyncio
from collections import deque
import json
import re
import struct
import threading
import time

from stream_conversation import Conversation, FreeSpeech, TurnControl, TurnCancelled, tool_definitions, confirmed_text_key
from whatsapp_fish import (PROMPT, business_connection_limit, is_callback_request)
from fish_conversation import is_correction, is_task_cancellation
from whatsapp_audio import PhoneAudioFilter

try:
    from central_ai_identity import central_ai_identity, get_company_name
except Exception:
    def central_ai_identity(x): return x
    def get_company_name(): return ""



class PhonePCM:
    """Keep provider chunks continuous; pad only the final transport frame."""
    frame_bytes = 1920
    prefill_frames = 3
    single_burst_frames = 10

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
        # Two provider bursts give the sink headroom for uneven synthesis.
        # A single burst holding 600ms is already sufficient. This waits for
        # available audio, not a fixed sleep, and short answers flush on finish.
        if not self.started and not final:
            minimum=len(self.pending)>=self.frame_bytes*self.prefill_frames
            headroom=self.chunks>=2 or len(self.pending)>=self.frame_bytes*self.single_burst_frames
            if not minimum or not headroom:return
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
        self.last_user_activity = self.last_speech = time.monotonic()
        self.playback_until = self.last_speaking_end = 0.
        self.last_notice = self.last_progress = 0.
        self.pending_notice = None
        self.callback_notice = callback_notice
        self.greeting_pending = True
        self.received_input = False
        self.rendered_generation, self.last_rendered = None, 0.
        self.last_recovery = 0.
        self.asr = None
        self.prepared_speech = None
        self.dispatch_context = threading.local()
        self.speculative=None
        self.pending_input=None
        self.spoken_text=''
        self.capture_echo_text=''
        self.capture_rejected=False
        self.early_asr=None
        self.capture_version=0
        self.callback_new_tasks_since = time.time() if callback_notice else None
        try:
            company = get_company_name()
        except Exception:
            company = ""
        company_str = f" representing {company}" if company else ""

        caller_name = self.member.get('name', '')
        caller_digits = self.member.get('number', '')
        is_unknown = (
            not caller_name
            or caller_name.startswith('+')
            or 'Caller' in caller_name
            or (self.member.get('role') == 'contact' and caller_name.replace('+', '').isdigit())
        )

        if is_unknown:
            self.prompt = central_ai_identity(f"""You are Leo, the personal assistant representing {company or 'Central AI'} on WhatsApp.
You are speaking with a caller at +{caller_digits}. This caller may be a customer, client, partner, prospect, or team member.
Greet the caller warmly, politely, and professionally.
Introduce yourself as Leo, representing {company or 'Central AI'}.
Speak in a friendly, conversational manner in clear English (or natural Filipino / Taglish if preferred).
Ask how you can assist them today.
CONVERSATION REALISM:
- Listen like a real human. When the caller speaks, wait patiently until they finish speaking before replying.
- Instant barge-in: If the caller interrupts while you are talking, immediately stop speaking and listen.
- Say Australian place names naturally if mentioned. Keep answers concise, measured, and helpful (1-2 sentences).
""")
        else:
            self.prompt = central_ai_identity(f"""You are Leo, the personal assistant{company_str}, speaking privately with {caller_name} on WhatsApp.
Understand English accents naturally. Keep your responses concise, measured, and helpful.
Start with a brief direct answer. Usually use one or two short sentences.
CONVERSATION REALISM:
- Listen like a real human. When the caller speaks, wait patiently until they finish speaking before replying.
- Instant barge-in: If the caller interrupts while you are talking, immediately stop speaking and listen.
- Say Australian place names naturally if mentioned. Keep answers concise, measured, and helpful (1-2 sentences).
""")
        if self.member.get('role') == 'business':
            biz_name = self.member.get('name', 'Business')
            self.prompt += f'\nThis is {biz_name}\'s separate business channel. Personal memory and tasks are inaccessible. {biz_name} has ONLY their own memory and task list; email, calendar, live flight/web search, company files and ordinary outbound calls/messages are not connected. Explain these limits immediately; do not start work to rediscover a known missing connection. You can draft, calculate and analyse information supplied. When asked about Central AI, confirm that the platform is live, running smoothly, responsive, and performing great.'
        self.prompt += '\nThis call uses Central AI streaming speech. Never mention providers unless asked. Never claim a job succeeded from its acceptance. Interruptions affect speech only. Background updates cannot authorize tools.'
        self.prompt += '\nA request for several report outlines, items or comparisons is one native_leo job containing the full quantity and all restrictions. Never create one job per item. Start separate jobs only for distinct tasks the caller explicitly requests. task_status reads this caller\'s existing background jobs; it cannot check external code updates, files or live systems. A new request to inspect external development progress is one native_leo task, unless an exact existing job already covers it.'
        self.conversation = Conversation(service.config, self.prompt, self.tool,
            names={'native_leo','task_status','cancel_task','task_callback',
                   'add_knowledge','search_knowledge','get_workspace_overview',
                   'send_whatsapp_message','call_whatsapp_contact'})

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
        if self.control: self.control.cancel()
        if self.prepared_speech:
            self.prepared_speech[0].cancel();self.prepared_speech=None
        if self.speculative:self.speculative.cancel();self.speculative=None
        self.pending_notice = None
        self.discard_early_asr()
        if self.pending_input:
            self.close_asr(self.pending_input.get('stream'))
            self.close_asr(self.pending_input.get('prepared_asr'))
        self.pending_input = None
        self.playback_until = 0.
        while not self.output.empty(): self.output.get_nowait()
        self.service.metrics['interruptions']=self.service.metrics.get('interruptions',0)+1
        self.change_state('listening')

    def resume_input(self):
        pending=self.pending_input
        if not getattr(self.service,'cloud_asr',None) or self.state!='thinking' or not pending or pending['generation']!=self.generation:
            return False
        with pending['control'].guard:
            if pending['committed'] or pending['control'].cancelled.is_set():return False
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
        self.capture_echo_text=''
        self.capture_rejected=False
        self.close_asr(self.asr);self.asr=None
        self.change_state('reconnecting')

    def transport_ready(self):
        if not self.greeting_pending:self.change_state('listening')

    @staticmethod
    def close_asr(stream):
        if stream and hasattr(stream,'close'):stream.close()

    def discard_early_asr(self):
        if self.early_asr:
            self.close_asr(self.early_asr['recognizer'])
            self.early_asr=None

    def prepare_recognition(self):
        cloud=getattr(self.service,'cloud_asr',None)
        if self.early_asr or getattr(self.asr,'remote',False) or not hasattr(cloud,'prepare'):return
        generation=self.generation
        control=TurnControl(lambda:not self.closed and self.generation==generation
                            and self.service.allowed(self.call))
        pcm=bytes(self.capture)
        recognizer=cloud.prepare(pcm,control)
        if recognizer:
            self.early_asr={'recognizer':recognizer,'version':self.capture_version,'pcm':pcm}
            self.service.metrics['early_recognitions_started']=self.service.metrics.get('early_recognitions_started',0)+1

    def tool(self, event, caller_text):
        control=getattr(self.dispatch_context,'control',None) or self.control
        if control:control.check()
        name, params = event['toolName'], event['params']
        caller_audio=getattr(self.dispatch_context,'audio',None)
        if caller_audio is not None and name=='native_leo':
            # Fast streaming drafts are adequate for conversation, but a task
            # needs independent confirmation of the caller's actual words.
            cloud=getattr(self.service,'cloud_asr',None)
            source=getattr(self.dispatch_context,'source',None)
            confirmation=getattr(self.dispatch_context,'confirmation',None) if cloud and source else None
            began=time.monotonic()
            prepared=getattr(self.dispatch_context,'prepared_confirmation',None)
            if confirmation:
                verified,quality=confirmation
            elif prepared:
                try:verified,quality=prepared.finish(control)
                except TurnCancelled:raise
                except Exception:
                    control.check()
                    verified,quality='',{'decision':'clarify'}
            else:
                verified,quality=(cloud.recognize(caller_audio,control,confirmation_source=source)
                                  if cloud and source else self.service.recognize(caller_audio))
            self.service.metrics['last_task_confirmation_wait_ms']=round((time.monotonic()-began)*1000)
            if cloud and source:self.dispatch_context.confirmation=(verified,quality)
            if control:control.check()
            # Only equivalent formatting is folded: every word/value, negation,
            # name and numeric separator must still match. No paraphrase gate.
            if quality.get('decision')!='accepted' or confirmed_text_key(verified)!=confirmed_text_key(caller_text):
                self.service.metrics['task_transcript_clarifications']=self.service.metrics.get('task_transcript_clarifications',0)+1
                return {'isError':True,'result':{'status':'clarification_required','task_started':False,
                    'message':'Independent recognition did not confirm the task wording. Ask the caller to repeat the exact request before starting work.'}}
        error = None
        if name=='native_leo' and is_correction(caller_text): error='clarification_required'
        elif name=='cancel_task' and not is_task_cancellation(caller_text): error='explicit_cancellation_required'
        elif name=='task_callback' and (self.member['number'] not in self.service.members or
                not is_callback_request(caller_text,params.get('mode'))): error='explicit_callback_request_required'
        if error: return {'isError':True,'result':{'status':error,'retry_action':False}}
        if name=='native_leo' and (limit:=business_connection_limit(self.member,params.get('request'))):
            return {'result':{'status':'connection_unavailable','message':limit,'task_started':False}}
        if name=='native_leo':
            scoped_request=params.get('request')
            if (not isinstance(scoped_request,str) or not scoped_request.strip()
                    or len(scoped_request)>9000 or scoped_request.lstrip().startswith('/')):
                return {'isError':True,'result':{'status':'clarification_required','task_started':False}}
            # Recognition may be correct while the conversational model drops
            # a number or restriction from its tool summary. Give the worker
            # the exact confirmed words as authoritative details, plus the
            # scoped summary needed to resolve earlier conversation references.
            request=('Task scope from this conversation:\n'+scoped_request+
                     '\nVerbatim independently confirmed caller wording:\n'+caller_text+
                     '\nComplete only the task scope above. The verbatim caller wording controls '
                     'quantities, names, recipients, negation and restrictions if the summary differs. '
                     'Use it to resolve this task, not to start unrelated additional tasks.')
            if len(request)>9000:
                return {'isError':True,'result':{'status':'clarification_required','task_started':False}}
            event={**event,'params':{**params,'request':request}}
        began=time.monotonic()
        result=self.jobs.handle(self.call,event,self.service.allowed)
        self.service.metrics['last_task_admission_ms']=round((time.monotonic()-began)*1000)
        return result

    def prepare_task_confirmation(self):
        control=getattr(self.dispatch_context,'control',None)
        cloud=getattr(self.service,'cloud_asr',None)
        pcm=getattr(self.dispatch_context,'audio',None)
        source=getattr(self.dispatch_context,'source',None)
        if (not control or pcm is None or not source or not hasattr(cloud,'prepare')
                or getattr(self.dispatch_context,'confirmation',None)
                or getattr(self.dispatch_context,'prepared_confirmation',None)):return
        control.check()
        independent=TurnControl(lambda: not control.cancelled.is_set() and control.admitted())
        prepared=cloud.prepare(pcm,independent,confirmation_source=source)
        if prepared:
            control.track(prepared)
            self.dispatch_context.prepared_confirmation=prepared
            self.service.metrics['task_confirmations_prepared']=self.service.metrics.get('task_confirmations_prepared',0)+1

    async def start(self):
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
        if self.prepared_speech and (self.prepared_speech[0].cancelled.is_set() or self.prepared_speech[1].error):
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
                        'control':control,'generation':generation,'committed':False} if audio is not None else None)
        self.pending_input=pending_input
        self.change_state('thinking')
        self.worker=self.task(self.reply(audio,notice,greeting,control,generation,prepared,speculative,pending_input))
        return True

    async def reply(self,audio,notice,greeting,control,generation,prepared=None,speculative=None,pending_input=None):
        loop=asyncio.get_running_loop()
        framer=PhonePCM()
        def output(data,final=False):
            control.check()
            future=asyncio.run_coroutine_threadsafe(self.enqueue(data,generation,control,framer,final),loop)
            try: future.result(timeout=3)
            except Exception: future.cancel();raise TurnCancelled()
        def run():
            nonlocal framer,output,speculative
            began=time.monotonic()
            if prepared:_,speech,framer,output=prepared
            else:speech=FreeSpeech(self.service.config,output,control,rate=16000)
            first=None
            def speak(value):
                nonlocal first
                if first is None:self.spoken_text=''
                first=first or time.monotonic();speech.text(value)
                self.spoken_text=(self.spoken_text+value)[-2400:]
            try:
                if greeting:
                    company = ""
                    try:
                        from central_ai_identity import get_company_name
                        company = get_company_name()
                    except Exception:
                        pass
                    company_str = f" representing {company}" if company else ""
                    caller_name = self.member.get('name', '')
                    is_unknown = (
                        not caller_name
                        or caller_name.startswith('+')
                        or 'Caller' in caller_name
                        or (self.member.get('role') == 'contact' and caller_name.replace('+', '').isdigit())
                    )
                    if is_unknown:
                        speak(f"Hello! I'm Leo, your personal assistant{company_str}. How can I help you today?")
                    elif caller_name.startswith('Michael'):
                        speak(f"Hey Michael, it's Leo{company_str}. The app is live and running great! How can I help you today?")
                    else:
                        speak(f"Hey {caller_name}, it's Leo{company_str}. What can I help you with?")
                elif notice:
                    self.conversation.reply(json.dumps(notice,ensure_ascii=False)[:12000],speak,control,notice=True)
                else:
                    pcm=audio['pcm'] if isinstance(audio,dict) else audio
                    streaming=audio.get('stream') if isinstance(audio,dict) else None
                    cloud=getattr(self.service,'cloud_asr',None)
                    if cloud and streaming and not getattr(streaming,'remote',False):
                        # The local decoder is a text-only head start. It never
                        # supplies final caller words or confirms a task.
                        try:
                            draft,_=streaming.finish()
                            messages=[{'role':'system','content':self.prompt},*self.conversation.history,{'role':'user','content':draft}]
                            if draft and (not speculative or not speculative.matches(messages,tool_definitions(self.conversation.names))):
                                if speculative:speculative.cancel()
                                preview_control=TurnControl(lambda:not control.cancelled.is_set() and not self.closed
                                    and self.generation==generation and self.service.allowed(self.call))
                                speculative=self.conversation.prefetch(draft,preview_control)
                        except Exception:
                            control.check()
                            if speculative:speculative.cancel();speculative=None
                            self.service.metrics['preview_recognition_failures']=self.service.metrics.get('preview_recognition_failures',0)+1
                    if streaming and getattr(streaming,'remote',False):
                        text,quality=streaming.finish(control)
                        if quality.get('fallback_allowed') and cloud:
                            text,quality=cloud.recognize(pcm,control)
                        elif text and cloud and re.match(r'^(?:(?:okay|ok|alright)\W+)?(?:good\s*bye|bye|thanks? for watching|please subscribe)\b',text,re.I):
                            verified,confirmation=cloud.recognize(pcm,control,confirmation_source=quality['source'])
                            normal=lambda value:re.sub(r'[^\w\s]','',value.casefold()).split()
                            if confirmation['decision']!='accepted' or normal(verified)!=normal(text):
                                text='';quality.update(decision='silence',rejected_unconfirmed_farewell=True)
                    elif cloud:
                        early=audio.get('prepared_asr') if isinstance(audio,dict) else None
                        if early:
                            control.track(early)
                            try:
                                text,quality=early.finish(control)
                                self.service.metrics['early_recognitions_used']=self.service.metrics.get('early_recognitions_used',0)+1
                            except TurnCancelled:
                                control.check()
                                text,quality=cloud.recognize(pcm,control)
                            except Exception:
                                control.check()
                                text,quality=cloud.recognize(pcm,control)
                        else:text,quality=cloud.recognize(pcm,control)
                    else:
                        text,quality=streaming.finish() if streaming else self.service.recognize(pcm)
                        if streaming and quality['decision']=='clarify':text,quality=self.service.recognize(pcm)
                    control.check()
                    self.service.metrics['last_asr_ms']=round((time.monotonic()-began)*1000)
                    self.service.metrics['last_recognition']=quality
                    echo_text=audio.get('echo_text','') if isinstance(audio,dict) else ''
                    normalize=lambda value:' '.join(re.sub(r'[^\w\s]','',value.casefold()).split())
                    candidate=normalize(text)
                    if candidate and echo_text and (len(candidate.split())>=3 or len(candidate)>=8) and (' '+candidate+' ') in (' '+normalize(echo_text)+' '):
                        self.service.metrics['rejected_playback_echoes']=self.service.metrics.get('rejected_playback_echoes',0)+1
                        return
                    if not text:
                        if quality.get('decision')=='clarify': speak("I didn't catch that clearly. Could you repeat it?")
                        else: return
                    elif re.search(r'\b(and|because|but|if|with|to|the|my|a|an|or|about)\W*$',text,re.I):
                        # Obvious unfinished clauses get one short opportunity
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
                        self.dispatch_context.audio=pcm if streaming or cloud else None
                        self.dispatch_context.source=quality.get('source') if cloud else None
                        self.dispatch_context.confirmation=None
                        self.dispatch_context.prepared_confirmation=None
                        self.dispatch_context.control=control
                        def pending_tool(name,caller_text,needs_acknowledgement):
                            nonlocal first
                            # Reading this caller's isolated task ledger has no
                            # action effects. Final cloud words are sufficient;
                            # changes/cancellations/callbacks retain independent
                            # confirmation before dispatch.
                            if name!='task_status':self.prepare_task_confirmation()
                            pcm=getattr(self.service,'task_ack_audio',b'')
                            if needs_acknowledgement and pcm:
                                control.check()
                                phrase='Let me check that. '
                                first=first or time.monotonic()
                                self.spoken_text=phrase
                                output(pcm)
                                self.service.metrics['task_acknowledgements']=self.service.metrics.get('task_acknowledgements',0)+1
                                return phrase
                        try:
                            messages=[{'role':'system','content':self.prompt},*self.conversation.history,{'role':'user','content':text}]
                            reused=bool(speculative and speculative.matches(messages,tool_definitions(self.conversation.names)))
                            self.service.metrics['prepared_replies_used']=self.service.metrics.get('prepared_replies_used',0)+int(reused)
                            self.conversation.reply(text,speak,control,speculative=speculative,on_tool_pending=pending_tool)
                        finally:
                            self.service.metrics['last_conversation_timing']=getattr(self.conversation,'last_timing',{})
                            self.dispatch_context.audio=None;self.dispatch_context.control=None;self.dispatch_context.source=None;self.dispatch_context.confirmation=None;self.dispatch_context.prepared_confirmation=None
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
                if pending_input:
                    self.close_asr(pending_input.get('stream'))
                    self.close_asr(pending_input.get('prepared_asr'))
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
                self.asr=None;self.last_speech=time.monotonic()+.7;self.voiced=max(self.voiced,3)
                if getattr(self.service,'streaming_asr',None):
                    self.asr=self.service.streaming_asr.new_turn();await asyncio.to_thread(self.asr.feed,bytes(self.capture))
            elif result=='complete' and notice and self.generation==generation:
                self.pending_notice={'notice':notice,'generation':generation,'after':self.playback_until+.8}
            if greeting and self.callback_notice and self.generation==generation:
                self.pending_notice=None  # Callback result is announced in the next quiet gap.
        except (TurnCancelled,asyncio.CancelledError): pass
        except Exception:
            self.service.metrics['reply_errors']=self.service.metrics.get('reply_errors',0)+1
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
            self.playback_until=max(self.playback_until,time.monotonic())+.06
            self.change_state('speaking')

    async def feed(self):
        while not self.closed:
            frame=await self.input.get()
            reverse=frame[0]==2
            filtered=self.audio_filter.process(frame[1:],reverse=reverse)
            if reverse:
                # The caller's reverse stream is the audio actually rendered by
                # WhatsApp. It feeds echo cancellation, not recognition.
                if any(frame[1:]):
                    self.rendered_generation=self.generation;self.last_rendered=time.monotonic()
                continue
            self.received_input=True
            speaking,pcm=bool(filtered[0]),filtered[1:]
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
                if self.speech_run>=3 and (self.state in {'speaking','thinking'} or self.playback_until>time.monotonic()):
                    if not self.resume_input():self.flush()
            else: self.speech_run=0
            if not self.capture:
                if not speaking: self.pre.append(pcm);continue
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
                self.capture.clear();self.voiced=0
                self.capture_rejected=True;self.pre.clear()
                self.discard_early_asr()
                self.service.metrics['rejected_capture_limit']=self.service.metrics.get('rejected_capture_limit',0)+1

    async def tick(self):
        while not self.closed:
            await asyncio.sleep(.03)
            now=time.monotonic()
            busy=self.worker and not self.worker.done()
            if self.capture_rejected:
                if now-self.last_speech>=.38:self.capture_rejected=False
                continue
            if self.greeting_pending:
                if self.connected and self.received_input and not busy:
                    self.greeting_pending=False;self.start_reply(greeting=True)
                continue
            if self.capture and self.voiced>=3 and now-self.last_speech>=.38 and not busy:
                audio={'pcm':bytes(self.capture),'stream':self.asr,'echo_text':self.capture_echo_text,'speech_ended':self.last_speech};self.asr=None
                if self.early_asr:
                    early=self.early_asr;self.early_asr=None
                    if early['version']==self.capture_version and audio['pcm'].startswith(early['pcm']):
                        audio['prepared_asr']=early['recognizer']
                    else:self.close_asr(early['recognizer'])
                self.capture_echo_text=''
                self.capture.clear();self.voiced=0
                self.start_reply(audio=audio);continue
            if self.capture and self.voiced<3 and now-self.last_speech>=.8:
                self.capture.clear();self.voiced=0;self.close_asr(self.asr);self.asr=None
                self.discard_early_asr()
            if self.capture and self.voiced>=3 and not busy and now-self.last_speech>=.14:
                self.prepare_recognition()
            if self.asr and self.capture and self.voiced>=3 and not busy and not self.speculative and now-self.last_speech>=.18:
                draft=self.asr.partial.strip()
                if draft and not re.search(r'\b(and|because|but|if|with|to|the|my|a|an|or|about)\W*$',draft,re.I):
                    generation=self.generation
                    prepared_control=TurnControl(lambda: not self.closed and self.generation==generation and self.service.allowed(self.call))
                    self.speculative=self.conversation.prefetch(draft,prepared_control)
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
        self.pending_input=None
        if self.control:self.control.cancel()
        if self.prepared_speech:self.prepared_speech[0].cancel();self.prepared_speech=None
        if self.speculative:self.speculative.cancel();self.speculative=None
        for task in list(self.tasks):task.cancel()
        if self.tasks:await asyncio.gather(*list(self.tasks),return_exceptions=True)
        while not self.output.empty():self.output.get_nowait()
