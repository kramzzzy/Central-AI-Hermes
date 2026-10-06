package main

import (
 "context"
 "encoding/json"
 "fmt"
 "os"
 "time"
 meow "github.com/purpshell/meowcaller"
)

// This number was explicitly authorized for a single availability check.
// Incoming admission and native/private conversations remain owner-only.
const teamProbeTarget = "639690395476"

type outgoingRequest struct {
 Target string `json:"target"`
 Mode string `json:"mode"`
 ExpiresAt time.Time `json:"expires_at"`
}

func readOutgoingRequest(path string,now time.Time)(outgoingRequest,error) {
 var r outgoingRequest
 data,err:=os.ReadFile(path)
 if err!=nil || len(data)>8192 {return r,fmt.Errorf("invalid call request file")}
 if json.Unmarshal(data,&r)!=nil {return r,fmt.Errorf("invalid call request JSON")}
 if r.Mode=="" {r.Mode="native"}
 if r.Mode=="native" && (r.Target==os.Getenv("LEO_WHATSAPP_OWNER") || (r.Target=="639267200480" && os.Getenv("LEO_WHATSAPP_OWNER")=="61423947456") || (os.Getenv("LEO_WHATSAPP_OWNER")=="61423947456" && managedCallerAllowed(r.Target))) && r.Target!="" {return r,nil}
 if r.Mode=="availability_check" && r.Target==teamProbeTarget && r.ExpiresAt.After(now) && !r.ExpiresAt.After(now.Add(5*time.Minute)) {return r,nil}
 return r,fmt.Errorf("call request is unauthorized or expired")
}

func readProbeGreeting(path string)([]byte,error) {
 data,err:=os.ReadFile(path)
 if err!=nil || len(data)==0 || len(data)%2!=0 || len(data)>16000*2*30 {return nil,fmt.Errorf("team check greeting unavailable")}
 return data,nil
}

func probeAudio(ctx context.Context,data []byte)*voiceAudio {
 callCtx,cancel:=context.WithCancel(ctx)
 // Playback only: no recognition, native session, private memory or tools.
 v:=&voiceAudio{ctx:callCtx,cancel:cancel,output:make(chan []float32,64),mediaReady:make(chan struct{})}
 go v.enqueue(data)
 return v
}

func wireProbe(ctx context.Context,call *meow.Call,data []byte) {
 v:=probeAudio(ctx,data)
 call.Receive(meow.SinkFunc(func(frame []float32){current.Lock();current.Received++;current.Unlock()}))
 call.Play(v)
 call.OnReady(func(){v.markReady();current.Lock();current.Call="audio_ready";current.Unlock()})
 call.OnEnd(func(reason string){v.cancel();current.Lock();current.Call="ended";current.EndReason=reason;current.busy=false;current.Unlock()})
 go func(){
  ringing:=time.NewTimer(45*time.Second);defer ringing.Stop()
  select {
  case <-v.ctx.Done():return
  case <-ringing.C:call.Hangup();return
  case <-v.mediaReady:
  }
  // Allow the brief introduction to play, then end this availability check.
  finished:=time.NewTimer(20*time.Second);defer finished.Stop()
  select {case <-v.ctx.Done():return;case <-finished.C:call.Hangup()}
 }()
}
