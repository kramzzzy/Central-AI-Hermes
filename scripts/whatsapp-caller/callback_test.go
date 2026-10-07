package main

import (
	"context"
	"encoding/json"
	"fmt"
	meow "github.com/purpshell/meowcaller"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type fakeCallback struct {
	fakeIncoming
	accept func()
}

func (c *fakeCallback) OnPeerAccept(fn func()) { c.mu.Lock(); c.accept = fn; c.mu.Unlock() }
func (c *fakeCallback) emitReady() {
	c.mu.Lock()
	fn := c.ready
	c.phase = meow.CallPhaseActive
	c.mu.Unlock()
	fn()
}
func (c *fakeCallback) emitAccept() { c.mu.Lock(); fn := c.accept; c.mu.Unlock(); fn() }

func TestCallbackRequiresAuthorizedMarkOrMichaelAndValidClaim(t *testing.T) {
	t.Setenv("LEO_WHATSAPP_OWNER", "639267200480")
	t.Setenv("LEO_WHATSAPP_BUSINESS_CONTACT", "61423947456")
	t.Setenv("WHATSAPP_CONVERSATION_MODE", "fish")
	for _, item := range []struct {
		target, id string
		ok         bool
	}{
		{"61423947456", "11111111-1111-4111-8111-111111111111", true},
		{"639267200480", "11111111-1111-4111-8111-111111111111", true},
		{"639690395476", "11111111-1111-4111-8111-111111111111", false},
		{"61423947456", "", false}, {"614239474569", "11111111-1111-4111-8111-111111111111", false},
	} {
		if err := validateCallback(taskCallback{Target: item.target, ID: item.id}); (err == nil) != item.ok {
			t.Fatal("callback admission mismatch")
		}
	}
}

func TestCallbackClaimsAreBoundToThePolledOwner(t *testing.T) {
	t.Setenv("LEO_WHATSAPP_OWNER", "639267200480")
	t.Setenv("LEO_WHATSAPP_BUSINESS_CONTACT", "61423947456")
	t.Setenv("WHATSAPP_CONVERSATION_MODE", "fish")
	for _, target := range []string{"639267200480", "61423947456"} {
		request,err:=claimTaskCallbackUsing(context.Background(),func(ctx context.Context, owner,path string,body []byte)([]byte,error){
			if path!="/callbacks/claim" { t.Fatal("unexpected callback operation") }
			if owner!=target { return []byte(`{}`),nil }
			return json.Marshal(taskCallback{Target:target,ID:"11111111-1111-4111-8111-111111111111"})
		})
		if err!=nil || request.Target!=target { t.Fatal("authorized self callback not claimed") }
	}
	request,err:=claimTaskCallbackUsing(context.Background(),func(ctx context.Context,owner,path string,body []byte)([]byte,error){
		return json.Marshal(taskCallback{Target:"61423947456",ID:"11111111-1111-4111-8111-111111111111"})
	})
	if err==nil || request.ID!="" { t.Fatal("Mark poll accepted a Michael callback") }
}

func TestCallbackConfigurationCannotRedirectToAnotherNumber(t *testing.T) {
	t.Setenv("LEO_WHATSAPP_OWNER","639690395476")
	t.Setenv("LEO_WHATSAPP_BUSINESS_CONTACT","")
	t.Setenv("WHATSAPP_CONVERSATION_MODE","fish")
	_,err:=claimTaskCallbackUsing(context.Background(),func(context.Context,string,string,[]byte)([]byte,error){
		t.Fatal("unconfigured recipient reached callback backend");return nil,nil
	})
	if err!=nil { t.Fatal(err) }
	if callbackTargetAllowed("639690395476") || callbackTargetAllowed("639267200480") { t.Fatal("redirected callback admitted") }
}

func TestCallbackDoesNotPreparePrivateReportBeforeActualPeerAnswer(t *testing.T) {
	c := &fakeCallback{fakeIncoming: fakeIncoming{phase: meow.CallPhaseRinging, attached: make(chan *voiceAudio, 1)}}
	var prepared atomic.Int32
	finished := make(chan string, 1)
	wireTaskCallback(context.Background(), c, func(ctx context.Context) (*voiceAudio, error) {
		prepared.Add(1)
		vctx, cancel := context.WithCancel(ctx)
		v := &voiceAudio{ctx: vctx, cancel: cancel, mediaReady: make(chan struct{}), output: make(chan []float32, 2)}
		v.enqueue([]byte{0, 64})
		return v, nil
	}, func(result string) { finished <- result })
	c.emitReady()
	time.Sleep(20 * time.Millisecond)
	if prepared.Load() != 0 {
		t.Fatal("media ready prepared report before peer answered")
	}
	c.emitAccept()
	select {
	case v := <-c.attached:
		frame, _ := v.ReadFrame()
		if frame[0] != .5 {
			t.Fatal("callback audio missing after answered and ready")
		}
	case <-time.After(time.Second):
		t.Fatal("answered callback never attached voice")
	}
	c.Hangup()
	select {
	case result := <-finished:
		if result != "ended" {
			t.Fatal("answered callback has wrong outcome")
		}
	case <-time.After(time.Second):
		t.Fatal("callback result not recorded")
	}
	if prepared.Load() != 1 {
		t.Fatal("callback prepared more than once")
	}
}

func TestRejectedCallbackNoNativeSessionAndOneOutcome(t *testing.T) {
	c := &fakeCallback{fakeIncoming: fakeIncoming{phase: meow.CallPhaseRinging, attached: make(chan *voiceAudio, 1)}}
	var prepared atomic.Int32
	var mu sync.Mutex
	outcomes := []string{}
	wireTaskCallback(context.Background(), c, func(ctx context.Context) (*voiceAudio, error) { prepared.Add(1); return nil, fmt.Errorf("unexpected") }, func(s string) { mu.Lock(); outcomes = append(outcomes, s); mu.Unlock() })
	c.Hangup()
	c.Hangup()
	c.emitAccept()
	time.Sleep(20 * time.Millisecond)
	mu.Lock()
	defer mu.Unlock()
	if prepared.Load() != 0 || len(outcomes) != 1 || outcomes[0] != "unanswered" {
		t.Fatal("rejected callback started native or duplicated outcome")
	}
}

func TestHangupDuringCallbackPreparationCannotAttachLateAudio(t *testing.T) {
	c := &fakeCallback{fakeIncoming: fakeIncoming{phase: meow.CallPhaseRinging, attached: make(chan *voiceAudio, 1)}}
	preparing, release := make(chan struct{}), make(chan struct{})
	wireTaskCallback(context.Background(), c, func(ctx context.Context) (*voiceAudio, error) {
		close(preparing)
		<-release
		return nil, fmt.Errorf("cancelled")
	}, func(string) {})
	c.emitAccept()
	<-preparing
	c.Hangup()
	close(release)
	time.Sleep(20 * time.Millisecond)
	select {
	case <-c.attached:
		t.Fatal("late callback report attached after hangup")
	default:
	}
}
