package main

import (
    "context"
    "encoding/binary"
    "fmt"
    "net/http"
    "net/http/httptest"
    "sync/atomic"
    "testing"
    "time"
    "github.com/coder/websocket"
)

func TestFishStreamReceivesContinuousAudioAndFencesInterruptedPlayback(t *testing.T) {
    var mic atomic.Int32
    var references atomic.Int32
    var socket atomic.Pointer[websocket.Conn]
    server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if r.Header.Get("Authorization") != "Bearer fixture-key" || r.Header.Get("X-Call-ID") != "fixture-call" {w.WriteHeader(403);return}
        conn, err := websocket.Accept(w, r, nil); if err != nil {return}; socket.Store(conn)
        defer conn.CloseNow()
        for {
            kind, body, err := conn.Read(r.Context()); if err != nil {return}
            if kind != websocket.MessageBinary || len(body) != 1921 {return}
            if body[0] == 1 {mic.Add(1)} else if body[0] == 2 {references.Add(1)}
        }
    }))
    defer server.Close()
    t.Setenv("LEO_AUDIO_URL", server.URL)
    v := conversationFixture(t); v.fish = true; v.key = "fixture-key"; v.id = "fixture-call"
    conn, err := v.connectFilter(v.ctx); if err != nil {t.Fatal(err)}
    go v.runFilter(conn)
    defer v.cancel()
    v.Receive(voicedFrame()); v.ReadFrame()
    eventually(t, func() bool {return mic.Load()>0 && references.Load()>0})
    send := func(generation uint64, sample int16) {
        packet := make([]byte,1928); binary.BigEndian.PutUint64(packet[:8],generation)
        for i:=8;i<len(packet);i+=2 {binary.LittleEndian.PutUint16(packet[i:],uint16(sample))}
        ctx,cancel:=context.WithTimeout(v.ctx,time.Second);defer cancel()
        if err:=socket.Load().Write(ctx,websocket.MessageBinary,packet);err!=nil {t.Fatal(err)}
    }
    send(1,2000)
    eventually(t,func()bool{return len(v.playback)==1})
    if err:=socket.Load().Write(v.ctx,websocket.MessageText,[]byte(fmt.Sprintf(`{"type":"state","state":"listening","generation":%d}`,2)));err!=nil {t.Fatal(err)}
    eventually(t,func()bool{v.mu.Lock();defer v.mu.Unlock();return v.generation==2})
    send(1,2000) // A late packet from the interrupted reply must be discarded.
    time.Sleep(20*time.Millisecond)
    frame,err:=v.ReadFrame();if err!=nil || frame[0]!=0 {t.Fatal("interrupted speech replayed")}
    send(2,3000)
    eventually(t,func()bool{return len(v.playback)==1})
    frame,err=v.ReadFrame();if err!=nil || frame[0]==0 {t.Fatal("fresh Fish speech missing")}
    if len(v.samples)!=0 {t.Fatal("Fish audio was incorrectly routed through local recognition")}
    if v.ctx.Err()!=nil {t.Fatal("Fish state change ended the WhatsApp call")}
}
