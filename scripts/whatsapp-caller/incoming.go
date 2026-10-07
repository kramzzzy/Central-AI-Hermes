package main

import (
 "context"
 "log"
 "sync"
 "time"
 meow "github.com/purpshell/meowcaller"
)

// Complete WhatsApp's answer handshake before slow native/TTS preparation.
// Register lifecycle listeners first, retaining readiness if it arrives early.
type incomingHandle interface {
 OnReady(func())
 OnEnd(func(string))
 Answer() error
 Hangup() error
 State() meow.CallPhase
 Receive(meow.AudioSink)
 Play(meow.AudioSource) *meow.Player
}
func answerIncoming(ctx context.Context,call *meow.Call) {answerIncomingWithVoice(ctx,call,prepareVoice)}
func answerIncomingWithVoice(ctx context.Context,call incomingHandle,prepare func(context.Context)(*voiceAudio,error)) {
 callCtx,stop:=context.WithCancel(ctx)
 var link sync.Mutex
 var voice *voiceAudio
 mediaReady:=false
 // A receiver must be installed before Answer starts media; attaching it
 // after greeting preparation discards the caller's initial decoded audio.
 call.Receive(meow.SinkFunc(func(frame []float32){
  link.Lock();v:=voice;link.Unlock()
  if v!=nil {v.Receive(frame)} else {current.Lock();current.Received++;current.Unlock()}
 }))
 call.OnReady(func(){
  link.Lock();mediaReady=true;if voice!=nil{voice.markReady()};link.Unlock()
  current.Lock();current.Call="audio_ready";current.Answered=true;current.AnsweredAt=time.Now().UTC().Format(time.RFC3339);current.Unlock()
  log.Print("incoming owner call media ready")
 })
 call.OnEnd(func(reason string){
  stop();link.Lock();v:=voice;link.Unlock();if v!=nil{v.Close()}
  if reason==""{reason="remote_end"}
  current.Lock();current.Call="ended";current.EndReason=reason;current.busy=false;current.Unlock()
  log.Print("incoming owner call ended")
 })
 if err:=call.Answer();err!=nil {
  call.Hangup();stop();current.Lock();current.Call="answer_failed";current.EndReason="incoming_handshake_failed";current.busy=false;current.Unlock();log.Print("incoming answer failed");return
 }
 current.Lock();if current.Call=="answering"{current.Call="connecting"};current.Unlock()
 log.Print("incoming owner answer requested before voice preparation")
 go func(){
  v,err:=prepare(callCtx)
  if err!=nil {
   if callCtx.Err()==nil{current.Lock();current.EndReason="incoming_voice_unavailable";current.busy=false;current.Unlock();call.Hangup();log.Printf("incoming voice preparation failed: %v",err)}
   return
  }
  link.Lock()
  if callCtx.Err()!=nil || call.State()==meow.CallPhaseEnded {link.Unlock();v.Close();return}
  voice=v
  if mediaReady{v.markReady()}
  call.Play(v)
  link.Unlock()
  log.Print("incoming owner voice attached")
  select {case <-callCtx.Done():return;case <-v.ctx.Done():call.Hangup()}
 }()
 go func(){select{case <-callCtx.Done():return;case <-time.After(30*time.Minute):call.Hangup()}}()
}
