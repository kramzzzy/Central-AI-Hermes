package main

import (
    "context"
    "net/http"
    "net/http/httptest"
    "os"
    "path/filepath"
    "testing"
    "time"
)

func TestEngineCannotConnectBeforeExplicitEnableMarker(t *testing.T) {
    marker := filepath.Join(t.TempDir(), "enabled")
    ctx, cancel := context.WithCancel(context.Background())
    defer cancel()
    done := make(chan error,1)
    go func(){ done <- waitEngineEnabled(ctx,marker) }()
    select {case <-done:t.Fatal("missing marker allowed connection");case <-time.After(20*time.Millisecond):}
    cancel()
    if err := <-done; err != context.Canceled { t.Fatalf("expected cancellation, got %v",err) }
    if err := os.WriteFile(marker,[]byte("vps\n"),0600); err != nil {t.Fatal(err)}
    if err := waitEngineEnabled(context.Background(),marker); err != nil { t.Fatal(err) }
    if err := waitEngineEnabled(context.Background(),filepath.Dir(marker)); err == nil { t.Fatal("directory accepted as enable marker") }
}

func TestEngineHealthRequiresActualWhatsAppConnection(t *testing.T) {
    connected := false
    server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
        if connected {w.Write([]byte(`{"connected":true}`))} else {w.WriteHeader(503);w.Write([]byte(`{"connected":false}`))}
    }))
    defer server.Close()
    if engineHealthy(server.URL) { t.Fatal("unpaired service reported healthy") }
    connected = true
    if !engineHealthy(server.URL) { t.Fatal("connected service reported unhealthy") }
}
