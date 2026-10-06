package meowcaller

import (
 "context"
 "testing"
 "github.com/rs/zerolog"
 waBinary "github.com/polymorfa/hypermeow/binary"
 "github.com/polymorfa/hypermeow/types"
)

func inboundMuteFixture()(*engine,*Call,*waBinary.Node,*[]waBinary.Node) {
 c:=&Client{log:zerolog.Nop()};eng:=newEngine(c);c.eng=eng
 peer:=types.NewADJID("123",0,2)
 creator:=peer.ToNonAD()
 call:=&Call{eng:eng,id:"INBOUND",peer:creator,phase:CallPhaseRinging}
 eng.calls[call.id]=&engineCall{call:call,direction:CallDirectionIncoming,from:peer,creator:creator}
 var sent []waBinary.Node
 eng.sendCallNode=func(_ context.Context,node waBinary.Node)error{sent=append(sent,node);return nil}
 mute:=&waBinary.Node{Tag:"call",Attrs:waBinary.Attrs{"from":peer},Content:[]waBinary.Node{{Tag:"mute_v2",Attrs:waBinary.Attrs{"call-id":call.id,"call-creator":creator,"mute-state":"0"}}}}
 return eng,call,mute,&sent
}
func TestIncomingAcceptAfterMuteBeforeAnswer(t *testing.T) {
 eng,call,mute,sent:=inboundMuteFixture()
 eng.onCallRaw(mute)
 if len(*sent)!=0{t.Fatal("mute auto-answered before admission/Answer")}
 if err:=call.Answer();err!=nil{t.Fatal(err)}
 if len(*sent)!=1 || (*sent)[0].GetChildren()[0].Tag!="accept"{t.Fatal("late Answer lost the earlier mute handshake")}
 if (*sent)[0].AttrGetter().JID("to")!=mute.AttrGetter().JID("from"){t.Fatal("accept sent to wrong peer device")}
 eng.onCallRaw(mute);call.Answer();eng.onCallRaw(mute)
 if len(*sent)!=1{t.Fatal("duplicate Answer/mute sent another accept")}
}
func TestIncomingAcceptAfterAnswerBeforeMute(t *testing.T) {
 eng,call,mute,sent:=inboundMuteFixture()
 if err:=call.Answer();err!=nil{t.Fatal(err)}
 if len(*sent)!=0{t.Fatal("accept sent before caller transport/mute")}
 eng.onCallRaw(mute)
 if len(*sent)!=1{t.Fatal("Answer then mute did not accept")}
 eng.onCallRaw(mute)
 if len(*sent)!=1{t.Fatal("in-call mute repeated the handshake")}
}
func TestIncomingMuteNeverAnswersRejectedOrUnknownCall(t *testing.T) {
 eng,call,mute,sent:=inboundMuteFixture()
 eng.onCallRaw(mute)
 if err:=call.Reject();err!=nil{t.Fatal(err)}
 if err:=call.Answer();err==nil{t.Fatal("ended call accepted")}
 eng.onCallRaw(mute)
 if len(*sent)!=1 || (*sent)[0].GetChildren()[0].Tag!="reject"{t.Fatal("unknown/rejected mute caused an accept")}
}
func TestIncomingMuteTypedAckPrecedesAcceptWithoutDuplicateGenericAck(t *testing.T) {
 eng,call,mute,sent:=inboundMuteFixture()
 mute.Attrs["id"]="MUTE-REQUEST"
 if err:=call.Answer();err!=nil{t.Fatal(err)}
 if !eng.onCallRaw(mute){t.Fatal("typed microphone ack fell through to generic ack")}
 if len(*sent)!=2 || (*sent)[0].Tag!="ack" || (*sent)[1].GetChildren()[0].Tag!="accept"{t.Fatal("microphone acknowledgement did not precede acceptance")}
 attrs:=(*sent)[0].AttrGetter()
 if attrs.String("class")!="call" || attrs.String("type")!="mute_v2" || attrs.String("id")!="MUTE-REQUEST" || attrs.JID("to")!=mute.AttrGetter().JID("from"){t.Fatal("typed microphone acknowledgement identity is incorrect")}
 eng.onCallRaw(mute)
 if len(*sent)!=3 || (*sent)[2].Tag!="ack"{t.Fatal("later microphone control repeated acceptance")}
}

func TestIncomingOfferReceiptUsesOuterIDAndExactCallerBeforeAnswer(t *testing.T) {
 eng,call,node,sent:=inboundMuteFixture()
 node.Attrs["id"]="OFFER-STANZA"
 node.Content=[]waBinary.Node{{Tag:"offer",Attrs:waBinary.Attrs{"call-id":call.id,"call-creator":call.peer}}}
 if eng.onCallRaw(node){t.Fatal("receipt suppressed normal offer dispatch/generic ACK")}
 if len(*sent)!=1 || (*sent)[0].Tag!="receipt"{t.Fatal("missing separate offer receipt")}
 receipt:=(*sent)[0];attrs:=receipt.AttrGetter();child:=receipt.GetChildren()[0]
 if attrs.String("id")!="OFFER-STANZA" || attrs.JID("to")!=node.AttrGetter().JID("from") || child.Tag!="offer" || child.AttrGetter().String("call-id")!=call.id || child.AttrGetter().JID("call-creator")!=call.peer {t.Fatal("offer receipt addressing/IDs do not echo the offer")}
 if call.State()!=CallPhaseRinging || eng.calls[call.id].acceptPending {t.Fatal("delivery receipt answered the call before owner admission")}
}
func TestIncomingOfferReceiptSkipsMissingEnvelopeAndNonOffer(t *testing.T) {
 for _,tag:=range []string{"offer","offer_notice","accept"} {
  eng,call,node,sent:=inboundMuteFixture()
  node.Content=[]waBinary.Node{{Tag:tag,Attrs:waBinary.Attrs{"call-id":call.id,"call-creator":call.peer}}}
  if tag!="offer"{node.Attrs["id"]="REQUEST"}
  eng.onCallRaw(node)
  if len(*sent)!=0{t.Fatal("receipt emitted for a missing stanza ID or non-offer action")}
 }
 eng,call,node,sent:=inboundMuteFixture()
 node.Attrs["id"]="ENDED-OFFER"
 node.Content=[]waBinary.Node{{Tag:"offer",Attrs:waBinary.Attrs{"call-id":call.id,"call-creator":call.peer,"is_call_ended":"1"}}}
 eng.onCallRaw(node)
 if len(*sent)!=0{t.Fatal("already-ended offer engaged the receipt path")}
}
