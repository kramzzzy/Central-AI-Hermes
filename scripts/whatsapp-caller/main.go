// Owner-only native voice, plus explicitly requested one-time team call checks.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	meow "github.com/purpshell/meowcaller"
	wa "github.com/polymorfa/hypermeow"
	"github.com/polymorfa/hypermeow/proto/waE2E"
	"github.com/polymorfa/hypermeow/store"
	"github.com/polymorfa/hypermeow/store/sqlstore"
	"github.com/polymorfa/hypermeow/types"
	"github.com/polymorfa/hypermeow/types/events"
	walog "github.com/polymorfa/hypermeow/util/log"
	"github.com/rs/zerolog"
	qr "github.com/skip2/go-qrcode"
	"google.golang.org/protobuf/proto"
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
 LinkedNumber string `json:"linked_number,omitempty"`
 PushName string `json:"push_name,omitempty"`
 code string
 expires time.Time
 busy bool
 activeHangup func() error
}
var current = status{State:"starting", Call:"idle"}
func allowedPeer(peer, resolved string) bool {
	peerClean := cleanDigits(peer)
	resolvedClean := cleanDigits(resolved)
	allowAll := getEnv("WHATSAPP_ALLOW_ALL_INBOUND", "ALLOW_ALL_INBOUND")
	if allowAll == "" || allowAll == "true" || allowAll == "1" {
		return len(peerClean) >= 7 || len(resolvedClean) >= 7
	}
	owner := cleanDigits(getEnv("WHATSAPP_OWNER", "LEO_WHATSAPP_OWNER"))
	if owner != "" && (peerClean == owner || resolvedClean == owner) { return true }
	return managedCallerAllowed(peerClean) || managedCallerAllowed(resolvedClean)
}

func admittedCaller(peer, resolved string) string {
	peerClean := cleanDigits(peer)
	resolvedClean := cleanDigits(resolved)

	if len(resolvedClean) >= 7 { return resolvedClean }
	if len(peerClean) >= 7 { return peerClean }

	owner := cleanDigits(getEnv("WHATSAPP_OWNER", "LEO_WHATSAPP_OWNER"))
	if owner != "" && (peerClean == owner || resolvedClean == owner) {
		return owner
	}
	return ""
}
func wireAudio(ctx context.Context, call *meow.Call, voice *voiceAudio) error {
	callCtx, stop := context.WithCancel(ctx)
	current.Lock()
	current.activeHangup = call.Hangup
	current.Unlock()
	call.Receive(meow.SinkFunc(voice.Receive))
	call.Play(voice)
	call.OnReady(func() {
		voice.markReady()
		current.Lock()
		current.Call = "audio_ready"
		current.Unlock()
		log.Print("owner call media ready")
	})
	call.OnEnd(func(reason string) {
		stop()
		voice.Close()
		current.Lock()
		current.Call = "ended"
		current.EndReason = reason
		current.busy = false
		current.activeHangup = nil
		current.Unlock()
		log.Print("owner call ended")
	})
	// Ringing timeout: if outbound call is not answered within 50 seconds, hang up cleanly
	go func() {
		select {
		case <-callCtx.Done():
			return
		case <-time.After(50 * time.Second):
			current.Lock()
			answered := current.Answered
			busy := current.busy
			current.Unlock()
			if busy && !answered {
				log.Print("outbound call ringing timeout (50s without answer); terminating call")
				_ = call.Hangup()
				voice.Close()
				current.Lock()
				current.Call = "no_answer"
				current.EndReason = "ringing_timeout"
				current.busy = false
				current.activeHangup = nil
				current.Unlock()
			}
		}
	}()
	go func() {
		select {
		case <-time.After(30 * time.Minute):
			call.Hangup()
		case <-voice.ctx.Done():
			call.Hangup()
		case <-callCtx.Done():
		}
	}()
	return nil
}
func main() {
 if len(os.Args)==2 && os.Args[1]=="readycheck" { if serverReady("http://127.0.0.1:8080/ready") {return};os.Exit(1) }
 if len(os.Args)==2 && os.Args[1]=="healthcheck" { if engineHealthy("http://127.0.0.1:8080/health") {return};os.Exit(1) }
 owner:=getEnv("WHATSAPP_OWNER","LEO_WHATSAPP_OWNER")
 routes,managed:=managedPhoneRouting()
 unconfigured:=owner=="" || (managed && len(routes.Contacts)==0)
 if !unconfigured && !managedCallerAllowed(owner) { log.Fatal("configured owner required") }
 ctx,cancel:=signal.NotifyContext(context.Background(),os.Interrupt,syscall.SIGTERM);defer cancel()
 http.HandleFunc("/ready",func(w http.ResponseWriter,r *http.Request){w.Header().Set("Cache-Control","no-store");w.Header().Set("Content-Type","application/json");json.NewEncoder(w).Encode(map[string]bool{"service_running":true})})
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
 var socket *wa.Client
 http.HandleFunc("/status",func(w http.ResponseWriter,r *http.Request){
  stage:=receiveStage()
  current.Lock();defer current.Unlock();w.Header().Set("Content-Type","application/json")
  current.ReceiveStage=stage
  if socket!=nil && socket.Store!=nil && socket.Store.ID!=nil && current.State=="connected" {
   current.LinkedNumber = socket.Store.ID.User
   if socket.Store.PushName!="" { current.PushName = socket.Store.PushName }
  } else if current.State!="connected" {
   current.LinkedNumber = ""
   current.PushName = ""
  }
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
 if device!=nil && device.ID!=nil {
  current.Lock()
  current.LinkedNumber = device.ID.User
  if device.PushName!="" { current.PushName = device.PushName }
  current.Unlock()
 }
 store.DeviceProps.Os=ptr(getEnvDefault("Central AI Assistant","WHATSAPP_DEVICE_NAME","LEO_WHATSAPP_DEVICE_NAME"))
 socket = wa.NewClient(device,walog.Zerolog(logger))
 http.HandleFunc("/setup/groups",groupsHandler(socket))
 http.HandleFunc("/pairing/start",pairingHandler(
  "/run/secrets/whatsapp_setup_key",
  "/data/remote-engine-enabled",
  func()bool{current.Lock();defer current.Unlock();return current.State=="connected"},
  func()error{
   startPairing(ctx,socket)
   return nil
  },
 ))
 http.HandleFunc("/pairing/disconnect",disconnectHandler("/run/secrets/whatsapp_setup_key",func()error{
  current.Lock();current.State="logged_out";current.code="";current.LinkedNumber="";current.PushName="";current.Unlock()
  go func(){
   pairingMu.Lock()
   if pairingCancel != nil {
    pairingCancel()
    pairingCancel = nil
   }
   pairingMu.Unlock()
   if socket.Store!=nil {
    _=socket.Store.Delete(ctx)
    socket.Store.ID=nil
   }
   socket.Disconnect()
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
		cleanTarget := cleanDigits(req.Target)
		if len(cleanTarget) < 7 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "Invalid target phone number: at least 7 digits required."})
			return
		}
		if socket != nil && socket.Store != nil && socket.Store.ID != nil && cleanTarget == cleanDigits(socket.Store.ID.User) {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]any{
				"ok":    false,
				"error": fmt.Sprintf("Target %s is the phone number of the linked WhatsApp account itself (%s). WhatsApp does not permit an account to place a call to its own number.", req.Target, socket.Store.ID.User),
			})
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
		current.Target = cleanTarget
		current.Kind = req.Mode
		if current.Kind == "" { current.Kind = "native" }
		current.Received = 0
		current.Played = 0
		current.VoiceErrors = 0
		current.HeartbeatMisses = 0
		current.Answered = false
		current.AnsweredAt = ""
		current.EndReason = ""
		current.Unlock()

		voice, err := prepareVoiceOutbound(ctx, cleanTarget)
		if err != nil {
			current.Lock(); current.busy = false; current.Call = "dial_failed"; current.EndReason = err.Error(); current.Unlock()
			log.Printf("outbound voice preparation failed for %s: %v", cleanTarget, err)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": fmt.Sprintf("Voice initialization failed: %v", err)})
			return
		}
		current.Lock(); current.Call = "dialing"; current.Unlock()
		call, err := caller.Call(ctx, cleanTarget)
		if err != nil {
			voice.Close()
			current.Lock(); current.busy = false; current.Call = "dial_failed"; current.EndReason = err.Error(); current.Unlock()
			log.Printf("outbound call to %s failed: %v", req.Target, err)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": fmt.Sprintf("Failed to place WhatsApp call to %s: %v", req.Target, err)})
			return
		}
		current.Lock()
		current.activeHangup = call.Hangup
		current.Unlock()
		call.OnPeerAccept(func() {
			current.Lock()
			current.Answered = true
			current.AnsweredAt = time.Now().UTC().Format(time.RFC3339)
			current.Unlock()
			log.Printf("outbound call to %s accepted by peer", req.Target)
		})
		call.OnEnd(func(reason string) {
			voice.Close()
			current.Lock()
			current.Call = "ended"
			current.EndReason = reason
			current.busy = false
			current.Answered = false
			current.activeHangup = nil
			current.Unlock()
			log.Printf("outbound call to %s ended: %s", req.Target, reason)
		})
		go func() {
			if err := wireAudio(ctx, call, voice); err != nil {
				call.Hangup()
				voice.Close()
				current.Lock(); current.busy = false; current.Call = "dial_failed"; current.EndReason = err.Error(); current.activeHangup = nil; current.Unlock()
			}
		}()

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"ok": true, "status": "dialing", "target": req.Target})
	})
	http.HandleFunc("/contacts", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		data, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "Bad request", http.StatusBadRequest)
			return
		}
		for _, p := range []string{"/data/contacts.json", ".runtime/contacts.json"} {
			os.MkdirAll(filepath.Dir(p), 0755)
			os.WriteFile(p, data, 0666)
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"ok": true})
	})
	http.HandleFunc("/call/drop", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost && r.Method != http.MethodDelete {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		current.Lock()
		hangup := current.activeHangup
		target := current.Target
		wasBusy := current.busy
		current.busy = false
		current.Call = "ended"
		current.EndReason = "dropped_by_user"
		current.Answered = false
		current.activeHangup = nil
		current.Unlock()

		if hangup != nil {
			go hangup()
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"ok":      true,
			"dropped": wasBusy,
			"target":  target,
			"message": "Call terminated successfully.",
		})
	})
	http.HandleFunc("/send", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			Target  string `json:"target"`
			Message string `json:"message"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil || req.Target == "" || req.Message == "" {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "Target and message are required."})
			return
		}
		cleanTarget := cleanDigits(req.Target)
		if len(cleanTarget) < 7 {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": "Valid phone number with at least 7 digits is required."})
			return
		}
		targetJID := types.NewJID(cleanTarget, types.DefaultUserServer)
		msg := &waE2E.Message{Conversation: proto.String(req.Message)}
		sendCtx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
		defer cancel()
		resp, err := socket.SendMessage(sendCtx, targetJID, msg)
		if err != nil {
			log.Printf("outbound whatsapp message to %s failed: %v", cleanTarget, err)
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadGateway)
			json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": fmt.Sprintf("Failed to send WhatsApp message: %v", err)})
			return
		}
		log.Printf("outbound whatsapp message to %s sent successfully (id=%s)", cleanTarget, resp.ID)
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"ok": true, "message_id": string(resp.ID), "target": cleanTarget})
	})
	socket.AddEventHandler(func(event any){
  current.Lock();defer current.Unlock()
  switch event.(type) {
  case *events.Connected:
    current.State="connected";current.code=""
    if socket.Store!=nil && socket.Store.ID!=nil {
      current.LinkedNumber = socket.Store.ID.User
      if socket.Store.PushName!="" { current.PushName = socket.Store.PushName }
    }
    go func(){
     defaultPush:=getEnvDefault("Central AI Assistant","WHATSAPP_DEVICE_NAME","LEO_WHATSAPP_DEVICE_NAME")
     if socket.Store.PushName=="" || socket.Store.PushName=="Leo call test" || socket.Store.PushName=="Leo - youraiagent" {socket.Store.PushName=defaultPush}
     if err:=socket.SendPresence(ctx,types.PresenceAvailable);err!=nil {log.Print("call presence announcement failed")}
    }()
  case *events.Disconnected:
    current.State="reconnecting"
    go func() {
      time.Sleep(2 * time.Second)
      if socket.Store != nil && socket.Store.ID != nil && !socket.IsConnected() {
        connectPairedDevice(ctx, socket)
      }
    }()
  case *events.LoggedOut:
    current.State="logged_out";current.code=""
    current.LinkedNumber=""
    current.PushName=""
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
 if marker:=getEnv("ENGINE_ENABLED_FILE","LEO_ENGINE_ENABLED_FILE");marker!="" {
  current.Lock();current.State="standby";current.Unlock()
  if err:=waitEngineEnabled(ctx,marker);err!=nil {log.Print("engine startup cancelled");return}
 }
	go connectPairedDevice(ctx, socket)
	// Proactive WhatsApp Connection Keep-Alive & Self-Healing Watchdog
	go func() {
		ticker := time.NewTicker(30 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if socket == nil || socket.Store == nil || socket.Store.ID == nil {
					continue
				}
				if socket.IsConnected() {
					_ = socket.SendPresence(ctx, types.PresenceAvailable)
					current.Lock()
					if current.State != "connected" {
						current.State = "connected"
					}
					current.Unlock()
				} else {
					log.Printf("Watchdog detected disconnected WhatsApp socket; auto-reconnecting...")
					current.Lock()
					current.State = "reconnecting"
					current.Unlock()
					go connectPairedDevice(ctx, socket)
				}
			}
		}
	}()
 go func(){
  ticker:=time.NewTicker(5*time.Second);defer ticker.Stop()
  for {select{case <-ctx.Done():return;case <-ticker.C:}
   mode := os.Getenv("WHATSAPP_CONVERSATION_MODE")
   if mode != "fish" && mode != "stream" {continue}
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
    if request.Mode=="availability_check" {probe,err=readProbeGreeting("/data/team-call-probe.pcm")} else {voice,err=prepareVoiceOutbound(ctx,request.Target)}
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
const page=`<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><title>Central AI WhatsApp Line</title><style>body{font:18px system-ui;background:#f6f7f9;color:#16202b;max-width:760px;margin:40px auto;padding:20px}img{width:min(100%,520px);display:block;background:white}pre{white-space:pre-wrap;background:white;padding:18px;border-radius:12px}.banner{background:#edf2f7;border-left:4px solid #3182ce;padding:12px;border-radius:6px;margin:16px 0}</style><h1>Central AI WhatsApp Connector</h1><p>On your mobile WhatsApp, open <b>Settings → Linked devices → Link a device</b> and scan this QR code.</p><img id="qr" hidden><p id="state">Starting…</p><div id="linked-box" class="banner" hidden><div id="linked-text" style="font-weight:bold;color:#2b6cb0;"></div></div><details><summary>Connection diagnostics</summary><pre id="details"></pre></details><script>async function poll(){try{let s=await(await fetch('/status',{cache:'no-store'})).json();document.getElementById('state').textContent='Connection: '+s.state;let box=document.getElementById('linked-box');let txt=document.getElementById('linked-text');if(s.linked_number){box.hidden=false;txt.textContent='Connected Line: +'+s.linked_number+(s.push_name?' ('+s.push_name+')':'');}else{box.hidden=true;}let q=document.getElementById('qr');q.hidden=s.state!=='pairing';if(!q.hidden)q.src='/qr.png?t='+Date.now();document.getElementById('details').textContent=JSON.stringify(s,null,2)}catch(e){document.getElementById('state').textContent='Waiting for connector…'}}poll();setInterval(poll,2500)</script>`
