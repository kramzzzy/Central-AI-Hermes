package main

import (
 "context"
 "fmt"
 "sync"
 "testing"
 "time"
 meow "github.com/purpshell/meowcaller"
)

type fakeIncoming struct {
 mu sync.Mutex
 ready func()
 end func(string)
 answered bool
 phase meow.CallPhase
 sink meow.AudioSink
 attached chan *voiceAudio
}
func (c *fakeIncoming)OnReady(fn func()){c.mu.Lock();c.ready=fn;c.mu.Unlock()}
func (c *fakeIncoming)OnEnd(fn func(string)){c.mu.Lock();c.end=fn;c.mu.Unlock()}
func (c *fakeIncoming)State()meow.CallPhase{c.mu.Lock();defer c.mu.Unlock();return c.phase}
func (c *fakeIncoming)Receive(s meow.AudioSink){c.mu.Lock();c.sink=s;c.mu.Unlock()}
func (c *fakeIncoming)Play(s meow.AudioSource)*meow.Player{c.attached<-s.(*voiceAudio);return nil}
func (c *fakeIncoming)Answer()error{
 c.mu.Lock();ready,end:=c.ready,c.end
 if ready==nil || end==nil || c.sink==nil{c.mu.Unlock();return fmt.Errorf("listeners/receiver missing before answer")}
 sink:=c.sink
 c.answered=true;c.phase=meow.CallPhaseActive;c.mu.Unlock()
 // Simulate media becoming ready while voice preparation is still pending.
 sink.WriteFrame(make([]float32,960));ready();return nil
}
func (c *fakeIncoming)Hangup()error{c.mu.Lock();c.phase=meow.CallPhaseEnded;end:=c.end;c.mu.Unlock();if end!=nil{end("hangup")};return nil}

func TestIncomingAnswersBeforeSlowVoiceAndRetainsEarlyReady(t *testing.T) {
 c:=&fakeIncoming{phase:meow.CallPhaseRinging,attached:make(chan *voiceAudio,1)}
 preparing:=make(chan struct{});release:=make(chan struct{});returned:=make(chan struct{})
 recognized:=make(chan []byte,1)
 go func(){answerIncomingWithVoice(context.Background(),c,func(ctx context.Context)(*voiceAudio,error){
  c.mu.Lock();answered:=c.answered;c.mu.Unlock()
  if !answered{return nil,fmt.Errorf("voice prepared before answer")}
  close(preparing);<-release
  child,cancel:=context.WithCancel(ctx)
  v:=&voiceAudio{ctx:child,cancel:cancel,output:make(chan []float32,2),mediaReady:make(chan struct{}),sendTurn:func(data []byte){recognized<-data}}
  v.enqueue([]byte{0,64});return v,nil
 });close(returned)}()
 select{case <-returned:case <-time.After(time.Second):t.Fatal("incoming callback blocked on voice preparation")}
 select{case <-preparing:case <-time.After(time.Second):t.Fatal("voice was not prepared after answer")}
 close(release)
 var v *voiceAudio
 select{case v=<-c.attached:case <-time.After(time.Second):t.Fatal("voice never attached")}
 select{case <-v.mediaReady:default:t.Fatal("readiness received during preparation was lost")}
 frame,err:=v.ReadFrame();if err!=nil || frame[0]!=0.5{t.Fatal("greeting not playable after early readiness")}
 speech:=make([]float32,960);for i:=range speech{speech[i]=0.1}
 c.mu.Lock();sink:=c.sink;c.mu.Unlock()
 sink.WriteFrame(speech);sink.WriteFrame(speech)
 v.flushIfQuiet(v.lastSpeech.Add(680*time.Millisecond))
 select{case <-recognized:case <-time.After(time.Second):t.Fatal("incoming microphone did not reach voice recognition")}
 c.Hangup()
 select{case <-v.ctx.Done():case <-time.After(time.Second):t.Fatal("hangup failed to cancel voice")}
}
func TestIncomingHangupCancelsPendingVoicePreparation(t *testing.T) {
 c:=&fakeIncoming{phase:meow.CallPhaseRinging,attached:make(chan *voiceAudio,1)}
 preparing:=make(chan struct{});cancelled:=make(chan struct{})
 answerIncomingWithVoice(context.Background(),c,func(ctx context.Context)(*voiceAudio,error){
  close(preparing);<-ctx.Done();close(cancelled);return nil,ctx.Err()
 })
 select{case <-preparing:case <-time.After(time.Second):t.Fatal("voice preparation never started")}
 c.Hangup()
 select{case <-cancelled:case <-time.After(time.Second):t.Fatal("pending voice did not receive cancellation")}
 select{case <-c.attached:t.Fatal("ended call got an audio source");default:}
}
