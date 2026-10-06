package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func conversationFixture(t *testing.T) *voiceAudio {
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	return &voiceAudio{ctx: ctx, cancel: cancel, output: make(chan []float32, 8), playback: make(chan speechFrame, 8), capture: make(chan []byte, 64), generation: 1}
}
func voicedFrame() []float32 {
	frame := make([]float32, 960)
	for i := range frame {
		frame[i] = .1
	}
	return frame
}
func eventually(t *testing.T, predicate func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !predicate() {
		if time.Now().After(deadline) {
			t.Fatal("condition did not settle")
		}
		time.Sleep(time.Millisecond)
	}
}
func TestBargeInDropsOldAudioAndKeepsOpeningWords(t *testing.T) {
	v := conversationFixture(t)
	v.playback <- speechFrame{1, voicedFrame()}
	for i := 0; i < 3; i++ {
		v.receiveClean(voicedFrame(), true)
	}
	if v.generation != 2 || len(v.samples) != 3*960 {
		t.Fatal("interruption lost speech or failed to fence playback")
	}
	// Audio already enqueued by an old worker must not return after barge-in.
	v.playback <- speechFrame{1, voicedFrame()}
	frame, err := v.ReadFrame()
	if err != nil {
		t.Fatal(err)
	}
	for _, x := range frame {
		if x != 0 {
			t.Fatal("canceled speech reached playback")
		}
	}
}
func TestFailureKeepsCallAliveAndNextTurnWorksWithoutReplay(t *testing.T) {
	var submitted atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/turn":
			n := submitted.Add(1)
			w.Header().Set("Trailer", "X-Native-Reply")
			w.Write(make([]byte, 1920))
			if n == 1 {
				w.Header().Set("X-Native-Reply", "failed")
			} else {
				w.Header().Set("X-Native-Reply", "complete")
			}
		case "/recovery":
			w.Write(make([]byte, 1920))
		default:
			w.WriteHeader(404)
		}
	}))
	defer server.Close()
	t.Setenv("LEO_VOICE_URL", server.URL)
	v := conversationFixture(t)
	for i := 0; i < 3; i++ {
		v.receiveClean(voicedFrame(), true)
	}
	v.flushIfQuiet(v.lastSpeech.Add(time.Second))
	eventually(t, func() bool { v.mu.Lock(); defer v.mu.Unlock(); return !v.busy })
	if v.ctx.Err() != nil {
		t.Fatal("reply failure ended the call")
	}
	for i := 0; i < 3; i++ {
		v.receiveClean(voicedFrame(), true)
	}
	v.flushIfQuiet(v.lastSpeech.Add(time.Second))
	eventually(t, func() bool { return submitted.Load() == 2 })
	eventually(t, func() bool { v.mu.Lock(); defer v.mu.Unlock(); return !v.busy })
	if submitted.Load() != 2 || v.ctx.Err() != nil {
		t.Fatal("action replayed or second reply ended the call")
	}
}
func TestInterruptMergesOnlyUnsubmittedUtterances(t *testing.T) {
	for _, started := range []bool{false, true} {
		t.Run(map[bool]string{false: "draft", true: "submitted"}[started], func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/interrupt" {
					t.Fatal("unexpected automatic action")
				}
				if started {
					w.Write([]byte(`{"native_started": true}`))
				} else {
					w.Write([]byte(`{"native_started": false}`))
				}
			}))
			defer server.Close()
			t.Setenv("LEO_VOICE_URL", server.URL)
			v := conversationFixture(t)
			v.busy = true
			v.turnID = "draft-fixture"
			v.lastInput = voicedFrame()
			for i := 0; i < 3; i++ {
				v.receiveClean(voicedFrame(), true)
			}
			eventually(t, func() bool { v.mu.Lock(); defer v.mu.Unlock(); return !v.interruptPending })
			expected := 3 * 960
			if !started {
				expected += 960
			}
			v.mu.Lock()
			defer v.mu.Unlock()
			if len(v.samples) != expected {
				t.Fatal("continuation was lost or a submitted request was replayed")
			}
		})
	}
}
func TestUnfinishedDraftRetainsAudioAndExtendsPause(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Header().Set("X-Voice-Continue", "true") }))
	defer server.Close()
	t.Setenv("LEO_VOICE_URL", server.URL)
	v := conversationFixture(t)
	for i := 0; i < 3; i++ {
		v.receiveClean(voicedFrame(), true)
	}
	at := v.lastSpeech
	v.flushIfQuiet(at.Add(700 * time.Millisecond))
	eventually(t, func() bool { v.mu.Lock(); defer v.mu.Unlock(); return !v.busy })
	if !v.extended || len(v.samples) != 3*960 {
		t.Fatal("unfinished draft discarded caller speech")
	}
	v.flushIfQuiet(at.Add(time.Second))
	if v.busy {
		t.Fatal("extended pause ignored")
	}
}
func TestContinuousReceiveCarriesAudioWhileReplyIsBusy(t *testing.T) {
	v := conversationFixture(t)
	v.busy = true
	v.Receive(voicedFrame())
	data := <-v.capture
	if len(data) != 1921 || data[0] != 1 {
		t.Fatal("microphone suppressed during reply")
	}
}
