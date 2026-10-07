// Owner-only native voice, plus explicitly requested one-time team call checks.
package main

import (
 "context"
 "encoding/json"
 "io"
 "log"
 "net/http"
 "os"
 "os/signal"
 "strings"
 "sync"
 "syscall"
 "time"

 meow "github.com/purpshell/meowcaller"
 wa "github.com/polymorfa/hypermeow"
 "github.com/polymorfa/hypermeow/store"
 "github.com/polymorfa/hypermeow/store/sqlstore"
 "github.com/polymorfa/hypermeow/types"
 "github.com/polymorfa/hypermeow/types/events"
 walog "github.com/polymorfa/hypermeow/util/log"
 "github.com/rs/zerolog"
 qr "github.com/skip2/go-qrcode"
 _ "modernc.org/sqlite"
)

type status struct {
 sync.Mutex
 State string `json:"state"`
 Call string `json:"call"`
 Received int `json:"received_frames"`
 Decoded int `json:"decoded_frames"`
 DecodeSamples int `json:"last_decoded_samples"`
 RTPTime uint32 `json:"last_rtp_timestamp"`
 RTPPackets int `json:"rtp_packets"`
 PayloadType uint8 `json:"last_payload_type"`
 SignallingError string `json:"signalling_error,omitempty"`
 Codec string `json:"codec,omitempty"`
 ReceiveStage string `json:"receive_stage,omitempty"`
 RemoteMuted *bool `json:"remote_muted,omitempty"`
 ConnectedRelays int `json:"connected_relays,omitempty"`
 OfferedRelays int `json:"offered_relays,omitempty"`
 Played int `json:"played_frames"`
 EndReason string `json:"end_reason,omitempty"`
 VoiceState string `json:"voice_state,omitempty"`
 VoiceErrors int `json:"voice_errors"`
 LastVoiceFailure string `json:"last_voice_failure,omitempty"`
 Interruptions int `json:"interruptions"`
 AudioOverruns int `json:"audio_overruns"`
 HeartbeatMisses int `json:"heartbeat_misses"`
 Target string `json:"target,omitempty"`
 Kind string `json:"kind,omitempty"`
 Answered bool `json:"answered"`
 AnsweredAt string `json:"answered_at,omitempty"`
 code string
 expires time.Time
 busy bool
}
var current = status{State:"starting", Call:"idle"}
func allowedPeer(peer, resolved string) bool {
	owner:=os.Getenv("LEO_WHATSAPP_OWNER")
	if owner=="" || !managedCallerAllowed(owner) {return false}
	return peer==owner || resolved==owner || (owner=="61423947456" && (peer=="279374905495566" || resolved=="279374905495566")) || (owner=="639267200480" && (peer=="131568421073069" || resolved=="131568421073069"))
}
func admittedCaller(peer,resolved string) string {

	owner:=os.Getenv("LEO_WHATSAPP_OWNER")
	if owner!="" && managedCallerAllowed(owner) {
		if peer==owner || resolved==owner || (owner=="61423947456" && (peer=="279374905495566" || resolved=="279374905495566")) || (owner=="639267200480" && (peer=="131568421073069" || resolved=="131568421073069")) {return owner}
	}
	business:=os.Getenv("LEO_WHATSAPP_BUSINESS_CONTACT")
	if business!="" && managedCallerAllowed(business) {
		if peer==business || resolved==business || (business=="639267200480" && (peer=="131568421073069" || resolved=="131568421073069")) || (business=="61423947456" && (peer=="279374905495566" || resolved=="279374905495566")) {return business}
	}
	if cfg, managed := managedPhoneRouting(); managed {
		for _, c := range cfg.Contacts {
			if c.Inbound || c.Calls {
				if peer == c.Number || resolved == c.Number {
					return c.Number
				}
			}
		}
	}
	return ""
}
func wireAudio(ctx context.Context, call *meow.Call, voice *voiceAudio) error {
 callCtx,stop:=context.WithCancel(ctx)
 call.Receive(meow.SinkFunc(voice.Receive))
 call.Play(voice)
 call.OnReady(func(){voice.markReady();current.Lock();current.Call="audio_ready";current.Unlock();log.Print("owner call media ready")})
 call.OnEnd(func(reason string){stop();voice.Close();current.Lock();current.Call="ended";current.EndReason=reason;current.busy=false;current.Unlock();log.Print("owner call ended")})
 go func(){select{case <-time.After(30*time.Minute):call.Hangup();case <-voice.ctx.Done():call.Hangup();case <-callCtx.Done():}}()
 return nil
}
func main() {
 if len(os.Args)==2 && os.Args[1]=="readycheck" { if serverReady("http://127.0.0.1:8080/ready") {return};os.Exit(1) }
 if len(os.Args)==2 && os.Args[1]=="healthcheck" { if engineHealthy("http://127.0.0.1:8080/health") {return};os.Exit(1) }
 owner:=os.Getenv("LEO_WHATSAPP_OWNER")
 routes,managed:=managedPhoneRouting()
 unconfigured:=owner=="" || (managed && len(routes.Contacts)==0)
 if !unconfigured && !managedCallerAllowed(owner) { log.Fatal("configured owner required") }
 ctx,cancel:=signal.NotifyContext(context.Background(),os.Interrupt,syscall.SIGTERM);defer cancel()
 http.HandleFunc("/ready",func(w http.ResponseWriter,r *http.Request){w.Header().Set("Cache-Control","no-store");w.Header().Set("Content-Type","application/json");json.NewEncoder(w).Encode(map[string]bool{"service_running":true})})
 http.HandleFunc("/pairing/start",pairingHandler("/run/secrets/whatsapp_setup_key","/data/remote-engine-enabled",func()bool{current.Lock();defer current.Unlock();return current.State=="connected"}))
 http.HandleFunc("/health",func(w http.ResponseWriter,r *http.Request){
  current.Lock();connected:=current.State=="connected";current.Unlock()
  w.Header().Set("Content-Type","application/json");w.Header().Set("Cache-Control","no-store")
  if !connected {w.WriteHeader(http.StatusServiceUnavailable)}
  json.NewEncoder(w).Encode(map[string]bool{"connected":connected})
 })
 http.HandleFunc("/",func(w http.ResponseWriter,r *http.Request){
  if r.URL.Path!="/" { http.NotFound(w,r);return }
  w.Header().Set("Content-Type","text/html; charset=utf-8")
  io.WriteString(w,page)
 })
 http.HandleFunc("/status",func(w http.ResponseWriter,r *http.Request){
  stage:=receiveStage()
  current.Lock();defer current.Unlock();w.Header().Set("Content-Type","application/json")
  current.ReceiveStage=stage
  w.Header().Set("Cache-Control","no-store");json.NewEncoder(w).Encode(&current)
 })
 http.HandleFunc("/qr.png",func(w http.ResponseWriter,r *http.Request){
  current.Lock();code,valid:=current.code,time.Now().Before(current.expires);current.Unlock()
  if code=="" || !valid { http.Error(w,"No current QR",http.StatusGone);return }
  png,err:=qr.Encode(code,qr.Medium,640);if err!=nil {http.Error(w,"QR error",500);return}
  w.Header().Set("Content-Type","image/png");w.Header().Set("Cache-Control","no-store");w.Write(png)
 })
 server:=&http.Server{Addr:":8080",ReadHeaderTimeout:5*time.Second}
 go func(){ if err:=server.ListenAndServe();err!=nil && err!=http.ErrServerClosed { log.Fatal(err) } }()
 logger:=zerolog.New(os.Stderr).Level(zerolog.WarnLevel).With().Timestamp().Logger()
 db,err:=sqlstore.New(ctx,"sqlite","file:/data/calls.db?_pragma=foreign_keys(1)&_pragma=busy_timeout(5000)",walog.Zerolog(logger));if err!=nil {log.Fatal(err)}
 defer db.Close()
 device,err:=db.GetFirstDevice(ctx);if err!=nil {log.Fatal(err)}
 store.DeviceProps.Os=ptr("Leo WhatsApp Voice")
 socket:=wa.NewClient(device,walog.Zerolog(logger))
 http.HandleFunc("/setup/groups",groupsHandler(socket))
 http.HandleFunc("/pairing/disconnect",disconnectHandler("/run/secrets/whatsapp_setup_key",func()error{
  current.Lock();current.State="logged_out";current.code="";current.Unlock()
  go func(){
   if socket.Store!=nil {
    _=socket.Store.Delete(ctx)
    socket.Store.ID=nil
   }
   socket.Disconnect()
   go connectPairedDevice(ctx,socket)
  }()
  return nil
 }))
 if err:=installTextBridge(ctx,socket,"/data/voice-key");err!=nil {log.Fatal(err)}
 callerLogger:=zerolog.New(callProgressWriter{}).Level(zerolog.InfoLevel)
 caller:=meow.NewClient(socket,meow.WithLogger(callerLogger))
 http.HandleFunc("/call", func(w http.ResponseWriter, r *http.Request) {
  if r.Method != http.MethodPost {
   http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
   return
  }
  var req struct {
   Target string `json:"target"`
   Mode   string `json:"mode"`
  }
  if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Target == "" {
   http.Error(w, "Invalid target", http.StatusBadRequest)
   return
  }
  current.Lock()
  if current.State != "connected" || current.busy {
   current.Unlock()
   w.Header().Set("Content-Type", "application/json")
   w.WriteHeader(http.StatusConflict)
   json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "caller is busy or disconnected"})
   return
  }
  current.busy = true
  current.Call = "preparing"
  current.Target = req.Target
  current.Kind = req.Mode
  if current.Kind == "" { current.Kind = "native" }
  current.Unlock()

  go func(target, mode string) {
   voice, err := prepareVoiceFor(ctx, target)
   if err != nil {
    current.Lock(); current.busy = false; current.Call = "dial_failed"; current.EndReason = err.Error(); current.Unlock()
    log.Printf("outbound voice preparation failed for %s: %v", target, err)
    return
   }
   current.Lock(); current.Call = "dialing"; current.Unlock()
   call, err := caller.Call(ctx, target)
   if err != nil {
    voice.Close()
    current.Lock(); current.busy = false; current.Call = "dial_failed"; current.EndReason = err.Error(); current.Unlock()
    log.Printf("outbound call to %s failed: %v", target, err)
    return
   }
   call.OnPeerAccept(func() {
    current.Lock()
    current.Answered = true
    current.AnsweredAt = time.Now().UTC().Format(time.RFC3339)
    current.Unlock()
    log.Printf("outbound call to %s accepted by peer", target)
   })
   if err := wireAudio(ctx, call, voice); err != nil {
    call.Hangup()
    voice.Close()
    current.Lock(); current.busy = false; current.Call = "dial_failed"; current.EndReason = err.Error(); current.Unlock()
   }
  }(req.Target, req.Mode)

  w.Header().Set("Content-Type", "application/json")
  json.NewEncoder(w).Encode(map[string]any{"ok": true, "status": "dialing", "target": req.Target})
 })
 socket.AddEventHandler(func(event any){
  current.Lock();defer current.Unlock()
  switch event.(type) { case *events.Connected:current.State="connected";current.code=""
   go func(){
    if socket.Store.PushName=="" || socket.Store.PushName=="Leo call test" {socket.Store.PushName="Leo - youraiagent"}
    if err:=socket.SendPresence(ctx,types.PresenceAvailable);err!=nil {log.Print("call presence announcement failed")}
   }()
  case *events.Disconnected:current.State="reconnecting"
  case *events.LoggedOut:
   current.State="logged_out";current.code=""
   go func(){
    if socket.Store!=nil {
     _=socket.Store.Delete(ctx)
     socket.Store.ID=nil
    }
    socket.Disconnect()
    go connectPairedDevice(ctx,socket)
   }()
  }
 })
 caller.OnIncomingCall(func(call *meow.Call){
  peer:=call.Peer().ToNonAD();resolved:=peer.User
  if peer.Server=="lid" || strings.HasSuffix(peer.String(),"@lid") {
   pn,e:=socket.Store.LIDs.GetPNForLID(ctx,peer);if e==nil && pn.User!="" {resolved=pn.User}
  }
  current.Lock()
  member:=admittedCaller(peer.User,resolved)
  busy:=current.busy
  isVideo:=call.IsVideo()
  if member=="" || busy || isVideo {
   current.Unlock()
   log.Printf("incoming call rejected: peer=%s user=%s resolved=%s member=%q busy=%t isVideo=%t",peer.String(),peer.User,resolved,member,busy,isVideo)
   call.Reject();return
  }
  current.busy=true;current.Call="answering";current.Received=0;current.Decoded=0;current.DecodeSamples=0;current.RTPTime=0;current.RTPPackets=0;current.PayloadType=0;current.SignallingError="";current.RemoteMuted=nil;current.Played=0;current.EndReason="";current.Target=resolved;current.Kind="native";current.Answered=false;current.AnsweredAt="";current.Unlock()
  log.Printf("incoming call accepted from %s (member=%s), answering...",peer.String(),member)
  call.OnMuteState(func(muted bool){current.Lock();current.RemoteMuted=&muted;current.Unlock();log.Printf("incoming microphone state: muted=%t",muted)})
  answerIncomingWithVoice(ctx,call,func(callCtx context.Context)(*voiceAudio,error){return prepareVoiceFor(callCtx,member)})
 })
 if marker:=os.Getenv("LEO_ENGINE_ENABLED_FILE");marker!="" {
  current.Lock();current.State="standby";current.Unlock()
  if err:=waitEngineEnabled(ctx,marker);err!=nil {log.Print("engine startup cancelled");return}
 }
 go connectPairedDevice(ctx,socket)
 go func(){
  ticker:=time.NewTicker(5*time.Second);defer ticker.Stop()
  for {select{case <-ctx.Done():return;case <-ticker.C:}
   if os.Getenv("WHATSAPP_CONVERSATION_MODE")!="fish" || (!callbackTargetAllowed("639267200480") && !callbackTargetAllowed("61423947456")) {continue}
   current.Lock()
   if current.State!="connected" || current.busy {current.Unlock();continue}
   current.Unlock()
   request,err:=claimTaskCallback(ctx)
   if err!=nil || request.ID=="" {continue}
   current.Lock()
   if current.State!="connected" || current.busy {current.Unlock();continue}
   current.busy=true
   current.Call="dialing";current.Kind="task_callback";current.Target=request.Target;current.Received=0;current.Played=0;current.Answered=false;current.AnsweredAt="";current.EndReason="";current.VoiceErrors=0;current.HeartbeatMisses=0;current.Unlock()
   call,err:=caller.Call(ctx,request.Target)
   if err!=nil {
    finishTaskCallback(request,"dial_failed")
    current.Lock();current.Call="dial_failed";current.EndReason="callback_dial_failed";current.busy=false;current.Unlock();log.Print("requested task callback failed");continue
   }
   wireTaskCallback(ctx,call,func(callCtx context.Context)(*voiceAudio,error){return prepareVoiceWithCallback(callCtx,request.Target,request.ID)},func(outcome string){finishTaskCallback(request,outcome)})
   log.Print("explicitly requested task callback initiated")
  }
 }()
 go func(){
  ticker:=time.NewTicker(time.Second);defer ticker.Stop()
  for {select{case <-ctx.Done():return;case <-ticker.C:}
   if _,err:=os.Stat("/data/call-request.json");err!=nil {continue}
   current.Lock()
   if current.State!="connected" || current.busy {current.Unlock();continue}
   // Consume before dialing so a restart or ambiguous result never repeats it.
   if err:=os.Rename("/data/call-request.json","/data/call-request-consumed.json");err!=nil {current.Unlock();continue}
   current.busy=true;current.Call="preparing";current.Received=0;current.Decoded=0;current.DecodeSamples=0;current.RTPTime=0;current.RTPPackets=0;current.PayloadType=0;current.Played=0;current.EndReason="";current.Target="";current.Kind="";current.Answered=false;current.AnsweredAt="";current.Unlock()
   request,err:=readOutgoingRequest("/data/call-request-consumed.json",time.Now())
   var voice *voiceAudio
   var probe []byte
   if err==nil {
    current.Lock();current.Target=request.Target;current.Kind=request.Mode;current.Unlock()
    if request.Mode=="availability_check" {probe,err=readProbeGreeting("/data/team-call-probe.pcm")} else {voice,err=prepareVoiceFor(ctx,request.Target)}
   }
   var call *meow.Call
   if err==nil {
    current.Lock();current.Call="dialing";current.Unlock()
    call,err=caller.Call(ctx,request.Target)
   }
   if err==nil {
    call.OnPeerAccept(func(){current.Lock();current.Answered=true;current.AnsweredAt=time.Now().UTC().Format(time.RFC3339);current.Unlock();log.Print("outgoing call accepted by peer")})
    if request.Mode=="availability_check" {wireProbe(ctx,call,probe)} else {err=wireAudio(ctx,call,voice)}
   }
   if err!=nil {
    if call!=nil {call.Hangup()}
    if voice!=nil {voice.Close()}
    current.Lock();current.busy=false;current.Call="dial_failed";current.EndReason=err.Error();current.Unlock()
    log.Print("authorized call request failed")
   } else {log.Print("authorized one-time call initiated")}
  }
 }()
 log.Print("Leo WhatsApp voice listening; manual outgoing calls require a one-time owner request")
 <-ctx.Done();socket.Disconnect();shutdown,done:=context.WithTimeout(context.Background(),3*time.Second);defer done();server.Shutdown(shutdown)
}
func ptr(s string)*string{return &s}
const page=`<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><title>Leo WhatsApp Voice</title><style>body{font:18px system-ui;background:#f6f7f9;color:#16202b;max-width:760px;margin:40px auto;padding:20px}img{width:min(100%,520px);display:block;background:white}pre{white-space:pre-wrap;background:white;padding:18px;border-radius:12px}</style><h1>Leo WhatsApp Voice</h1><p>On the phone for Leo's WhatsApp number (+63 968 749 3955), open <b>Settings → Linked devices → Link a device</b> and scan this QR code.</p><img id="qr" hidden><p id="state">Starting…</p><p>Once connected, <b>Mark Tech (+63 926 720 0480)</b> and <b>Michael (+61 423 947 456)</b> can call Leo. Leo uses the Jarvis voice with Australian English guidance and also accepts general English. Speak naturally and pause briefly for an answer. You can interrupt Leo while he speaks. Leo will ask you to repeat unclear speech. Calls have a thirty-minute limit. Mark Tech and Michael can each request a callback to their own number by saying: Call me when that is finished. Leo calls once after the task has a result and the current call ends.</p><details><summary>Connection diagnostics</summary><pre id="details"></pre></details><script>async function poll(){try{let s=await(await fetch('/status',{cache:'no-store'})).json();document.getElementById('state').textContent='Connection: '+s.state;let q=document.getElementById('qr');q.hidden=s.state!=='pairing';if(!q.hidden)q.src='/qr.png?t='+Date.now();document.getElementById('details').textContent=JSON.stringify(s,null,2)}catch(e){document.getElementById('state').textContent='Waiting for Leo…'}}poll();setInterval(poll,2500)</script>`
