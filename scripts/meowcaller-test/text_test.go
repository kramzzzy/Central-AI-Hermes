package main

import (
 "bytes"
 "context"
 "crypto/sha256"
 "encoding/hex"
 "encoding/json"
 "errors"
 "net/http/httptest"
 "path/filepath"
 "sync/atomic"
 "testing"
 "time"

 "github.com/polymorfa/hypermeow/proto/waE2E"
 "github.com/polymorfa/hypermeow/types"
)

func textFixture(t *testing.T,send func(context.Context,types.JID,*waE2E.Message)(string,error))*textBridge{
 t.Helper();bridge,err:=newTextBridge(filepath.Join(t.TempDir(),"text.db"),"private-key","123@g.us","",send);if err!=nil{t.Fatal(err)};t.Cleanup(func(){bridge.db.Close()});bridge.ready();return bridge
}

func textRequest(b *textBridge,route,action string,value any,auth bool)*httptest.ResponseRecorder{
 method:="GET";var body bytes.Buffer;if value!=nil{method="POST";json.NewEncoder(&body).Encode(value)}
 r:=httptest.NewRequest(method,"/text/"+action,&body);r.Header.Set("X-Leo-Route",route);if auth{r.Header.Set("Authorization","Bearer private-key")}
 w:=httptest.NewRecorder();b.serve(w,r);return w
}

func TestTextAdmissionAndRouting(t *testing.T){
 bridge:=textFixture(t,nil)
 mark:=textPacket{ChatID:"639267200480@s.whatsapp.net",WireChat:"639267200480@s.whatsapp.net",SenderID:"639267200480",MessageID:"one",Body:"hello"}
 if bridge.queue(mark,time.Now().Add(-time.Hour)){t.Fatal("historical message admitted")}
 if !bridge.queue(mark,time.Now()) || bridge.queue(mark,time.Now()){t.Fatal("message replay not fenced")}
 stranger:=mark;stranger.SenderID="639000000000";stranger.MessageID="stranger";if bridge.queue(stranger,time.Now()){t.Fatal("stranger admitted")}
 foreign:=mark;foreign.ChatID="61423947456@s.whatsapp.net";foreign.MessageID="foreign";if bridge.queue(foreign,time.Now()){t.Fatal("sender/chat mismatch admitted")}
 group:=textPacket{ChatID:"123@g.us",WireChat:"123@g.us",SenderID:"639000000000",MessageID:"group",Body:"ordinary conversation",IsGroup:true,BotIDs:[]string{"leo@lid"}}
 if bridge.queue(group,time.Now()){t.Fatal("ordinary group traffic admitted")}
 group.Body="Hi Leo";if !bridge.queue(group,time.Now()){t.Fatal("selected group trigger rejected")}
 group.ChatID="999@g.us";group.MessageID="other";if bridge.queue(group,time.Now()){t.Fatal("other group admitted")}
 if claimedCount(bridge,"michael",t)!=0{t.Fatal("Mark message exposed to Michael")}
 if claimedCount(bridge,"team",t)!=1{t.Fatal("group routing failed")}
 if claimedCount(bridge,"mark",t)!=1{t.Fatal("Mark routing failed")}
}

func claimedCount(bridge *textBridge,route string,t *testing.T)int{t.Helper();items,err:=bridge.claim(route);if err!=nil{t.Fatal(err)};return len(items)}

func TestTextReplyBindingAndSingleDelivery(t *testing.T){
 var attempts atomic.Int32
 bridge:=textFixture(t,func(ctx context.Context,jid types.JID,msg *waE2E.Message)(string,error){attempts.Add(1);return "sent-message",nil})
 packet:=textPacket{ChatID:"639267200480@s.whatsapp.net",WireChat:"639267200480@s.whatsapp.net",SenderID:"639267200480",MessageID:"one",Body:"hello"}
 bridge.queue(packet,time.Now());items,err:=bridge.claim("mark");if err!=nil{t.Fatal(err)}
 digest:=sha256.Sum256([]byte("known reply"))
 body:=map[string]string{"chatId":packet.ChatID,"bridgeLease":items[0].Lease,"message":"Hi Mark","sendKey":hex.EncodeToString(digest[:])}
 if textRequest(bridge,"mark","messages",nil,false).Code!=401{t.Fatal("unauthenticated poll admitted")}
 if textRequest(bridge,"michael","send",body,true).Code!=403{t.Fatal("cross-profile send admitted")}
 changed:=map[string]string{};for k,v:=range body{changed[k]=v};changed["chatId"]="61423947456@s.whatsapp.net"
 if textRequest(bridge,"mark","send",changed,true).Code!=403{t.Fatal("reply redirected to Michael")}
 for i:=0;i<2;i++{if w:=textRequest(bridge,"mark","send",body,true);w.Code!=200{t.Fatal(w.Code,w.Body.String())}}
 if attempts.Load()!=1{t.Fatal("same reply sent more than once")}
 body["messageId"]="someone-else-message"
 if textRequest(bridge,"mark","edit",body,true).Code!=403{t.Fatal("unowned edit admitted")}
}

func TestTextUncertainDeliveryCannotRetryAndClaimCannotReplay(t *testing.T){
 var attempts atomic.Int32
 sender:=func(context.Context,types.JID,*waE2E.Message)(string,error){attempts.Add(1);return "",errors.New("unknown delivery")}
 path:=filepath.Join(t.TempDir(),"text.db")
 bridge,err:=newTextBridge(path,"private-key","","",sender);if err!=nil{t.Fatal(err)}
 packet:=textPacket{ChatID:"639267200480@s.whatsapp.net",WireChat:"639267200480@s.whatsapp.net",SenderID:"639267200480",MessageID:"one",Body:"do work"}
 bridge.queue(packet,time.Now());items,_:=bridge.claim("mark")
 digest:=sha256.Sum256([]byte("reply"));body:=map[string]string{"chatId":packet.ChatID,"bridgeLease":items[0].Lease,"message":"result","sendKey":hex.EncodeToString(digest[:])}
 for i:=0;i<2;i++{if textRequest(bridge,"mark","send",body,true).Code!=409{t.Fatal("uncertain delivery retried")}}
 bridge.db.Close()
 restored,err:=newTextBridge(path,"private-key","","",sender);if err!=nil{t.Fatal(err)};defer restored.db.Close()
 packets,err:=restored.claim("mark");if err!=nil || len(packets)!=0{t.Fatal("claimed native work replayed after restart")}
 if attempts.Load()!=1{t.Fatal("transport retried uncertain send")}
}
