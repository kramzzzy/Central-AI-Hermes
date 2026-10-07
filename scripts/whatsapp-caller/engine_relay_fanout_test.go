package meowcaller

import (
 "bytes"
 "errors"
 "sync"
 "sync/atomic"
 "testing"
 "time"
 waBinary "github.com/polymorfa/hypermeow/binary"
 "github.com/polymorfa/hypermeow/types"
 "github.com/polymorfa/hypermeow/types/events"
)

type fakeRelay struct {
 incoming chan []byte
 ended chan struct{}
 once sync.Once
 closes atomic.Int32
 mu sync.Mutex
 sent [][]byte
}
func newFakeRelay()*fakeRelay{return &fakeRelay{incoming:make(chan []byte,4),ended:make(chan struct{})}}
func(r *fakeRelay)Recv(buf []byte)(int,error){select{case packet:=<-r.incoming:return copy(buf,packet),nil;case<-r.ended:return 0,errFanoutClosed}}
func(r *fakeRelay)Send(packet []byte)(int,error){select{case<-r.ended:return 0,errFanoutClosed;default:};r.mu.Lock();r.sent=append(r.sent,bytes.Clone(packet));r.mu.Unlock();return len(packet),nil}
func(r *fakeRelay)Close()error{r.once.Do(func(){r.closes.Add(1);close(r.ended)});return nil}

func TestRelayFanoutMigratesReceivesAndUnblocksOnClose(t *testing.T){
 a,b:=newFakeRelay(),newFakeRelay()
 f:=newRelayFanout([]relayMediaConnection{a,b},[][]byte{{10},{20}},[]string{"a","b"})
 defer f.Close()
 buf:=make([]byte,10)
 a.incoming<-[]byte{1,2};n,err:=f.Recv(buf)
 if err!=nil || !bytes.Equal(buf[:n],[]byte{1,2}){t.Fatal("primary audio did not arrive")}
 a.Close()
 b.incoming<-[]byte{3,4};n,err=f.Recv(buf)
 if err!=nil || !bytes.Equal(buf[:n],[]byte{3,4}){t.Fatal("audio failed after the caller moved to a surviving relay")}
 blocked:=make(chan error,1);go func(){_,err:=f.Recv(make([]byte,10));blocked<-err}()
 f.Close();f.Close()
 select{case err:=<-blocked:if !errors.Is(err,errFanoutClosed){t.Fatal("blocked receive did not report closure")};case<-time.After(time.Second):t.Fatal("hangup left merged receive blocked")}
 if a.closes.Load()!=1 || b.closes.Load()!=1{t.Fatal("transport ownership did not close every relay exactly once")}
}
func TestRelayFanoutRefreshesOnlyMatchingAllocateAndBroadcastsMedia(t *testing.T){
 a,b:=newFakeRelay(),newFakeRelay();f:=newRelayFanout([]relayMediaConnection{a,b},[][]byte{{10},{20}},[]string{"a","b"});defer f.Close()
 if err:=f.ResendAllocates();err!=nil{t.Fatal(err)}
 if _,err:=f.Send([]byte{42});err!=nil{t.Fatal(err)}
 a.mu.Lock();sentA:=append([][]byte(nil),a.sent...);a.mu.Unlock();b.mu.Lock();sentB:=append([][]byte(nil),b.sent...);b.mu.Unlock()
 if len(sentA)!=2 || len(sentB)!=2 || !bytes.Equal(sentA[0],[]byte{10}) || !bytes.Equal(sentB[0],[]byte{20}) || !bytes.Equal(sentA[1],[]byte{42}) || !bytes.Equal(sentB[1],[]byte{42}){t.Fatal("relay-specific credentials crossed connections or media was not broadcast")}
 a.Close();if _,err:=f.Send([]byte{43});err!=nil{t.Fatal("one failed relay stopped media on the surviving relay")}
}
func TestRtpReplayAcrossRelaysPreservesAudioPlayout(t *testing.T){
 filter:=newRtpReplayFilter();playout:=newAudioPlayoutBuffer();written:=0
 sink:=SinkFunc(func(frame []float32){written+=len(frame)})
 frame:=make([]float32,FrameSamples)
 for seq:=uint16(1);seq<=3;seq++{for route:=0;route<2;route++{if !filter.Duplicate(123,seq){if _,err:=playout.Push(uint32(seq)*FrameSamples,frame,sink);err!=nil{t.Fatal(err)}}}}
 if err:=playout.Flush(sink);err!=nil{t.Fatal(err)}
 if written!=3*FrameSamples{t.Fatalf("duplicate relays reset/lost playout: samples=%d",written)}
 if filter.Duplicate(456,2){t.Fatal("different participant stream was treated as a duplicate")}
 for _,seq:=range []uint16{65535,0,1,1025}{if filter.Duplicate(789,seq){t.Fatal("sequence wrap/ring reuse lost a new packet")}}
 if !filter.Duplicate(789,1025){t.Fatal("same relay packet replay was accepted")}
}
func TestIncomingRelayProbeDoesNotEndorseUnavailableEndpoint(t *testing.T){
 eng,call,node,sent:=inboundMuteFixture()
 eng.calls[call.id].relay=&relayData{endpoints:[]relayEndpoint{{relayName:"offered"}}}
 data:=&waBinary.Node{Tag:"relaylatency",Attrs:waBinary.Attrs{"call-id":call.id},Content:[]waBinary.Node{
  {Tag:"te",Attrs:waBinary.Attrs{"relay_name":"unavailable","latency":"33554433"},Content:[]byte{1}},
  {Tag:"te",Attrs:waBinary.Attrs{"relay_name":"offered","latency":"33554434"},Content:[]byte{2}},
 }}
 ev:=&events.CallRelayLatency{BasicCallMeta:types.BasicCallMeta{CallID:call.id,From:node.AttrGetter().JID("from"),CallCreator:call.peer},Data:data}
 eng.onRelayLatency(ev)
 if len(*sent)!=1 || (*sent)[0].GetChildren()[0].GetChildren()[0].AttrGetter().String("relay_name")!="offered"{t.Fatal("election endorsed a relay absent from the offered credentials")}
}
