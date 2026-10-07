package main
import (
 "context"
 "encoding/binary"
 "io"
 "net/http"
 "net/http/httptest"
 "os"
 "path/filepath"
 "testing"
 "time"
)
func TestTeamProbeCannotOpenPrivateNativeCall(t *testing.T) {
 t.Setenv("LEO_WHATSAPP_OWNER","639267200480")
 now:=time.Date(2026,10,3,3,0,0,0,time.UTC)
 path:=filepath.Join(t.TempDir(),"request.json")
 for _,c:=range []struct{body string;allowed bool}{
  {`{"target":"639267200480"}`,true},
  {`{"target":"639690395476","mode":"native"}`,false},
  {`{"target":"639690395476","mode":"availability_check","expires_at":"2026-10-03T03:04:00Z"}`,true},
  {`{"target":"639690395476","mode":"availability_check","expires_at":"2026-10-03T02:59:59Z"}`,false},
  {`{"target":"639690395476","mode":"availability_check","expires_at":"2026-10-03T03:06:00Z"}`,false},
  {`{"target":"61423947456","mode":"availability_check","expires_at":"2026-10-03T03:04:00Z"}`,false},
  {`{"target":"639690395476","mode":"availability_check"}`,false},
  {`{}`,false},
 } {
  if err:=os.WriteFile(path,[]byte(c.body),0600);err!=nil{t.Fatal(err)}
  _,err:=readOutgoingRequest(path,now)
  if (err==nil)!=c.allowed {t.Fatalf("unexpected request admission: %s",c.body)}
 }
}
func TestProbeOnlyPlaysGreetingAfterMediaReady(t *testing.T) {
 v:=probeAudio(context.Background(),[]byte{0,64});defer v.cancel()
 if v.id!="" || v.key!="" || v.sendTurn!=nil {t.Fatal("probe obtained private native turn access")}
 deadline:=time.Now().Add(time.Second)
 for len(v.output)==0 && time.Now().Before(deadline){time.Sleep(time.Millisecond)}
 frame,_:=v.ReadFrame();if frame[0]!=0 || len(v.output)!=1{t.Fatal("probe greeting played before answer")}
 v.markReady();frame,_=v.ReadFrame();if frame[0]!=0.5{t.Fatal("probe greeting missing")}
 v.cancel();if _,err:=v.ReadFrame();err!=io.EOF{t.Fatal("ended probe still played speech")}
}
func TestOnlyConfirmedOwnerCanReachAudio(t *testing.T) {
 t.Setenv("LEO_WHATSAPP_OWNER","639267200480")
 for _,c:=range []struct{peer,resolved string;want bool}{
  {"639267200480","",true}, {"228028504395792","639267200480",true},
  {"61423947456","61423947456",false}, {"","",false}, {"6392672004809","",false},
 } { if got:=allowedPeer(c.peer,c.resolved);got!=c.want {t.Fatalf("unexpected admission: %q %q",c.peer,c.resolved)} }
 t.Setenv("LEO_WHATSAPP_OWNER","")
 if allowedPeer("","") { t.Fatal("empty configuration admitted a caller") }
}
func TestMichaelHasDistinctIncomingIdentityAndNoOutboundPermission(t *testing.T) {
 t.Setenv("LEO_WHATSAPP_OWNER","639267200480")
 t.Setenv("LEO_WHATSAPP_BUSINESS_CONTACT","61423947456")
 t.Setenv("WHATSAPP_CONVERSATION_MODE","fish")
 for _,c:=range []struct{peer,resolved,want string}{
  {"61423947456","","61423947456"}, {"different-lid","61423947456","61423947456"},
  {"639267200480","","639267200480"}, {"614239474569","",""}, {"unknown","unknown",""},
 } {if got:=admittedCaller(c.peer,c.resolved);got!=c.want {t.Fatalf("wrong caller binding: %q",got)}}
 path:=filepath.Join(t.TempDir(),"request.json")
 if err:=os.WriteFile(path,[]byte(`{"target":"61423947456","mode":"native"}`),0600);err!=nil{t.Fatal(err)}
 if _,err:=readOutgoingRequest(path,time.Now());err==nil {t.Fatal("business incoming admission enabled outbound dialing")}
}
func TestBackendCallerHeaderPreservesBusinessIdentity(t *testing.T) {
 server:=httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
  if r.Header.Get("X-Caller-Number")!="61423947456" || r.Header.Get("X-Call-ID")!="bound-call" {t.Error("caller binding missing")}
  w.Write([]byte(`{}`))
 }));defer server.Close()
 t.Setenv("LEO_VOICE_URL",server.URL)
 v:=&voiceAudio{callerNumber:"61423947456",id:"bound-call",key:"fixture"}
 if _,err:=v.request(context.Background(),"/heartbeat",nil);err!=nil{t.Fatal(err)}
}
func TestIdleAudioIsSilentAndCancelStopsPlayback(t *testing.T) {
 ctx,cancel:=context.WithCancel(context.Background())
 v:=&voiceAudio{ctx:ctx,output:make(chan []float32,4)}
 frame,err:=v.ReadFrame();if err!=nil || len(frame)!=960 {t.Fatal("invalid idle frame")}
 for _,x:=range frame {if x!=0{t.Fatal("idle playback is not silent")}}
 pcm:=make([]byte,4);binary.LittleEndian.PutUint16(pcm,16384);binary.LittleEndian.PutUint16(pcm[2:],49152)
 v.enqueue(pcm);frame,err=v.ReadFrame()
 if err!=nil || frame[0]!=0.5 || frame[1]!= -0.5 || frame[959]!=0 {t.Fatal("PCM conversion/padding failed")}
 cancel();if _,err=v.ReadFrame();err!=io.EOF {t.Fatal("canceled playback continued")}
}
func TestSilenceDoesNotAccumulateOrSubmitSpeech(t *testing.T) {
 v:=&voiceAudio{output:make(chan []float32,4)}
 for i:=0;i<1000;i++ {v.Receive(make([]float32,960))}
 if len(v.pre)>4 || len(v.samples)!=0 || v.busy {t.Fatal("silence exceeded pre-roll or started a turn")}
}
func TestPacketSilenceGapSubmitsWithoutFurtherAudio(t *testing.T) {
 sent:=make(chan []byte,2)
 v:=&voiceAudio{output:make(chan []float32,4),sendTurn:func(data []byte){sent<-data}}
 frame:=make([]float32,960);for i:=range frame{frame[i]=0.1}
 for i:=0;i<3;i++{v.Receive(frame)}
 v.flushIfQuiet(v.lastSpeech.Add(500*time.Millisecond))
 select{case <-sent:t.Fatal("submitted in a short speaking pause");default:}
 v.flushIfQuiet(v.lastSpeech.Add(680*time.Millisecond))
 select{case data:=<-sent:if len(data)!=3*960*2{t.Fatal("missing voice frames")};case <-time.After(time.Second):t.Fatal("DTX gap never submitted the utterance")}
 v.flushIfQuiet(v.lastSpeech.Add(time.Second))
 select{case <-sent:t.Fatal("utterance submitted twice");default:}
}
func TestShortConfirmationIsSubmitted(t *testing.T) {
 sent:=make(chan []byte,1)
 v:=&voiceAudio{output:make(chan []float32,4),sendTurn:func(data []byte){sent<-data}}
 frame:=make([]float32,960);for i:=range frame{frame[i]=0.1}
 v.Receive(frame);v.Receive(frame)
 v.flushIfQuiet(v.lastSpeech.Add(680*time.Millisecond))
 select{case <-sent:case <-time.After(time.Second):t.Fatal("short confirmation was discarded")}
}
func TestGreetingWaitsForCallMedia(t *testing.T) {
 ctx,cancel:=context.WithCancel(context.Background());defer cancel()
 v:=&voiceAudio{ctx:ctx,output:make(chan []float32,2),mediaReady:make(chan struct{})}
 v.enqueue([]byte{0,64})
 frame,_:=v.ReadFrame();if frame[0]!=0 || len(v.output)!=1{t.Fatal("greeting consumed before call ready")}
 v.markReady();frame,_=v.ReadFrame();if frame[0]!=0.5{t.Fatal("greeting missing after ready")}
}
func TestAudioReachesPlaybackBeforeWholeResponseArrives(t *testing.T) {
 finish:=make(chan struct{})
 server:=httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
  w.Header().Set("Trailer","X-Native-Reply")
  w.Write(make([]byte,1920));w.(http.Flusher).Flush()
  <-finish
  w.Write(make([]byte,1920));w.Header().Set("X-Native-Reply","complete")
 }));defer func(){close(finish);server.Close()}()
 t.Setenv("LEO_VOICE_URL",server.URL)
 ctx,cancel:=context.WithCancel(context.Background());defer cancel()
 v:=&voiceAudio{ctx:ctx,output:make(chan []float32,4)}
 done:=make(chan error,1);go func(){done<-v.streamTurn(ctx,[]byte{0,0})}()
 select{case <-v.output:case <-time.After(time.Second):t.Fatal("audio buffered until full HTTP response")}
 select{case <-done:t.Fatal("response unexpectedly completed");default:}
 // Cancel after proving early playback; server cleanup is released below.
 cancel()
 select{case <-done:case <-time.After(time.Second):t.Fatal("cancel did not stop streamed reply")}
}
func TestTruncatedNativeStreamIsRejected(t *testing.T) {
 server:=httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
  w.Header().Set("Trailer","X-Native-Reply");w.Write(make([]byte,1920))
 }));defer server.Close()
 t.Setenv("LEO_VOICE_URL",server.URL)
 ctx,cancel:=context.WithCancel(context.Background());defer cancel()
 v:=&voiceAudio{ctx:ctx,output:make(chan []float32,4)}
 if err:=v.streamTurn(ctx,[]byte{0,0});err==nil{t.Fatal("missing native completion accepted")}
}
func TestFragmentedPCMAndCompletionTrailer(t *testing.T) {
 server:=httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
  w.Header().Set("Trailer","X-Native-Reply")
  w.Write([]byte{0});w.(http.Flusher).Flush()
  w.Write([]byte{64,0,192});w.Header().Set("X-Native-Reply","complete")
 }));defer server.Close()
 t.Setenv("LEO_VOICE_URL",server.URL)
 ctx,cancel:=context.WithCancel(context.Background());defer cancel()
 v:=&voiceAudio{ctx:ctx,output:make(chan []float32,4)}
 if err:=v.streamTurn(ctx,[]byte{0,0});err!=nil{t.Fatal(err)}
 frame:=<-v.output
 if frame[0]!=0.5 || frame[1]!= -0.5 || frame[959]!=0{t.Fatal("network fragmentation corrupted PCM")}
}
