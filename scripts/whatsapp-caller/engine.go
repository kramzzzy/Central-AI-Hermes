package main

import (
    "context"
    "encoding/json"
    "errors"
    "net/http"
    "os"
    "time"
)

func waitEngineEnabled(ctx context.Context, path string) error {
    if path == "" { return nil }
    ticker := time.NewTicker(time.Second)
    defer ticker.Stop()
    for {
        info, err := os.Stat(path)
        if err == nil {
            if !info.Mode().IsRegular() { return errors.New("engine enable marker must be a regular file") }
            return nil
        }
        if !os.IsNotExist(err) { return err }
        select {
        case <-ctx.Done(): return ctx.Err()
        case <-ticker.C:
        }
    }
}

func engineHealthy(url string) bool {
    client := &http.Client{Timeout:3*time.Second}
    response, err := client.Get(url)
    if err != nil { return false }
    defer response.Body.Close()
    if response.StatusCode != http.StatusOK { return false }
    var value struct { Connected bool `json:"connected"` }
    return json.NewDecoder(response.Body).Decode(&value) == nil && value.Connected
}
