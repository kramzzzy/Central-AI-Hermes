package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
)

func TestContinuousFilterWireAndReconnectKeepCallAlive(t *testing.T) {
	var connections atomic.Int32
	var references atomic.Int32
	var active atomic.Pointer[websocket.Conn]
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/audio" || r.Header.Get("Authorization") != "Bearer fixture-key" || r.Header.Get("X-Call-ID") != "fixture-call" {
			w.WriteHeader(403)
			return
		}
		socket, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		connections.Add(1)
		active.Store(socket)
		defer socket.CloseNow()
		for {
			kind, body, err := socket.Read(r.Context())
			if err != nil {
				return
			}
			if kind != websocket.MessageBinary || len(body) != 1921 {
				return
			}
			if body[0] == 2 {
				references.Add(1)
				continue
			}
			body[0] = 1
			if err := socket.Write(r.Context(), websocket.MessageBinary, body); err != nil {
				return
			}
		}
	}))
	defer server.Close()
	t.Setenv("LEO_AUDIO_URL", server.URL)
	v := conversationFixture(t)
	v.key = "fixture-key"
	v.id = "fixture-call"
	socket, err := v.connectFilter(v.ctx)
	if err != nil {
		t.Fatal(err)
	}
	go v.runFilter(socket)
	v.ReadFrame()
	v.Receive(voicedFrame())
	eventually(t, func() bool { v.mu.Lock(); defer v.mu.Unlock(); return len(v.samples) == 960 })
	eventually(t, func() bool { return references.Load() > 0 })
	active.Load().CloseNow()
	deadline := time.Now().Add(4 * time.Second)
	for connections.Load() < 2 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if connections.Load() < 2 || v.ctx.Err() != nil {
		t.Fatal("audio disconnect ended call or failed to reconnect")
	}
	v.Receive(voicedFrame())
	eventually(t, func() bool { v.mu.Lock(); defer v.mu.Unlock(); return len(v.samples) == 1920 })
	v.cancel()
	active.Load().CloseNow()
}

func TestFilterRequiresExactCallCredentials(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(403) }))
	defer server.Close()
	t.Setenv("LEO_AUDIO_URL", server.URL)
	v := conversationFixture(t)
	ctx, cancel := context.WithTimeout(v.ctx, time.Second)
	defer cancel()
	if _, err := v.connectFilter(ctx); err == nil {
		t.Fatal("unauthorized audio connection accepted")
	}
}
