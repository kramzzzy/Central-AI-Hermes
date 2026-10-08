package main

import (
	"context"
	"encoding/json"
	"fmt"
	meow "github.com/purpshell/meowcaller"
	"os"
	"regexp"
	"sync"
	"time"
)

type taskCallback struct {
	ID     string `json:"callback_id"`
	Target string `json:"target"`
}

var callbackUUID = regexp.MustCompile(`^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`)

func validateCallback(c taskCallback) error {
	if !callbackTargetAllowed(c.Target) || os.Getenv("WHATSAPP_CONVERSATION_MODE") != "fish" || !callbackUUID.MatchString(c.ID) {
		return fmt.Errorf("invalid task callback")
	}
	return nil
}

func callbackTargetAllowed(target string) bool {
	return target != "" && managedCallerAllowed(target)
}

func callbackControl(ctx context.Context, target, path string, body []byte) ([]byte, error) {
	if !callbackTargetAllowed(target) {
		return nil, fmt.Errorf("unconfigured callback recipient")
	}
	key, err := os.ReadFile("/data/voice-key")
	if err != nil {
		return nil, fmt.Errorf("voice backend not ready")
	}
	bounded, stop := context.WithTimeout(ctx, 3*time.Second)
	defer stop()
	v := &voiceAudio{id: "00000000-0000-4000-8000-000000000000", callerNumber: target, key: string(key)}
	return v.request(bounded, path, body)
}

func claimTaskCallback(ctx context.Context) (taskCallback, error) {
	return claimTaskCallbackUsing(ctx, callbackControl)
}

func claimTaskCallbackUsing(ctx context.Context, control func(context.Context, string, string, []byte) ([]byte, error)) (taskCallback, error) {
	var request taskCallback
	for _, target := range []string{getEnv("WHATSAPP_OWNER", "LEO_WHATSAPP_OWNER"), getEnv("WHATSAPP_BUSINESS_CONTACT", "LEO_WHATSAPP_BUSINESS_CONTACT")} {
		if !callbackTargetAllowed(target) { continue }
		data, err := control(ctx, target, "/callbacks/claim", nil)
		if err != nil { return taskCallback{}, err }
		if json.Unmarshal(data, &request) != nil { return taskCallback{}, fmt.Errorf("invalid callback response") }
		if request.ID == "" && request.Target == "" { continue }
		if request.Target != target { return taskCallback{}, fmt.Errorf("callback owner mismatch") }
		return request, validateCallback(request)
	}
	return taskCallback{}, nil
}

func finishTaskCallback(request taskCallback, outcome string) {
	body, _ := json.Marshal(map[string]string{"callback_id": request.ID, "outcome": outcome})
	if _, err := callbackControl(context.Background(), request.Target, "/callbacks/finish", body); err != nil {
		current.Lock()
		current.LastVoiceFailure = "callback_outcome_unconfirmed"
		current.Unlock()
	}
}

type callbackHandle interface {
	OnReady(func())
	OnEnd(func(string))
	OnPeerAccept(func())
	State() meow.CallPhase
	Hangup() error
	Receive(meow.AudioSink)
	Play(meow.AudioSource) *meow.Player
}

func wireTaskCallback(ctx context.Context, call callbackHandle, prepare func(context.Context) (*voiceAudio, error), finish func(string)) {
	callCtx, stop := context.WithCancel(ctx)
	var link sync.Mutex
	var voice *voiceAudio
	ready, accepted := false, false
	answered := make(chan struct{})
	var acceptOnce, endOnce sync.Once
	outcome := "unanswered"
	call.Receive(meow.SinkFunc(func(frame []float32) {
		link.Lock()
		v := voice
		link.Unlock()
		if v != nil {
			v.Receive(frame)
		}
	}))
	call.OnReady(func() {
		link.Lock()
		ready = true
		if accepted && voice != nil {
			voice.markReady()
		}
		link.Unlock()
	})
	end := func(reason string) {
		endOnce.Do(func() {
			stop()
			link.Lock()
			v, result := voice, outcome
			link.Unlock()
			if v != nil {
				v.Close()
			}
			finish(result)
			current.Lock()
			current.Call = "ended"
			current.EndReason = reason
			current.busy = false
			current.Unlock()
		})
	}
	call.OnEnd(end)
	if call.State() == meow.CallPhaseEnded {
		end("ended_before_callback_attach")
		return
	}
	if call.State() == meow.CallPhaseActive {
		link.Lock()
		ready = true
		link.Unlock()
	}
	call.OnPeerAccept(func() {
		acceptOnce.Do(func() {
			if callCtx.Err() != nil {
				return
			}
			link.Lock()
			accepted = true
			outcome = "ended"
			link.Unlock()
			current.Lock()
			current.Answered = true
			current.AnsweredAt = time.Now().UTC().Format(time.RFC3339)
			current.Call = "preparing_report"
			current.Unlock()
			close(answered)
		})
	})
	go func() {
		timer := time.NewTimer(45 * time.Second)
		defer timer.Stop()
		select {
		case <-callCtx.Done():
			return
		case <-timer.C:
			call.Hangup()
			return
		case <-answered:
		}
		if callCtx.Err() != nil {
			return
		}
		v, err := prepare(callCtx)
		if err != nil {
			if callCtx.Err() == nil {
				link.Lock()
				outcome = "voice_unavailable"
				link.Unlock()
				call.Hangup()
			}
			return
		}
		link.Lock()
		if callCtx.Err() != nil || call.State() == meow.CallPhaseEnded {
			link.Unlock()
			v.Close()
			return
		}
		voice = v
		if ready {
			v.markReady()
		}
		call.Play(v)
		link.Unlock()
		current.Lock()
		current.Call = "audio_ready"
		current.Unlock()
		select {
		case <-callCtx.Done():
			return
		case <-v.ctx.Done():
			call.Hangup()
		}
	}()
	go func() {
		select {
		case <-callCtx.Done():
			return
		case <-ctx.Done():
			call.Hangup()
		case <-time.After(30 * time.Minute):
			call.Hangup()
		}
	}()
}
