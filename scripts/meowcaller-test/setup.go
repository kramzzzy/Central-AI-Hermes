package main

import (
 "crypto/hmac"
 "encoding/json"
 "net/http"
 "os"
 "strings"
)

// Only the authenticated backend may begin pairing. Never clear an existing device.
func pairingHandler(keyPath, marker string, connected func() bool) http.HandlerFunc {
 return func(w http.ResponseWriter,r *http.Request) {
  w.Header().Set("Cache-Control","no-store")
  if r.Method!=http.MethodPost {w.WriteHeader(http.StatusMethodNotAllowed);return}
  key,err:=os.ReadFile(keyPath)
  if err!=nil || len(strings.TrimSpace(string(key)))<32 {http.Error(w,"Pairing authentication unavailable",503);return}
  expected:="Bearer "+strings.TrimSpace(string(key))
  if !hmac.Equal([]byte(r.Header.Get("Authorization")),[]byte(expected)) {http.Error(w,"Unauthorized",401);return}
  if !connected() {
   if err:=os.WriteFile(marker,[]byte("enabled\n"),0600);err!=nil {http.Error(w,"Pairing could not start",503);return}
  }
  w.Header().Set("Content-Type","application/json")
  json.NewEncoder(w).Encode(map[string]bool{"started":true})
 }
}

func serverReady(url string) bool {
 client:=&http.Client{Timeout:5_000_000_000}
 response,err:=client.Get(url)
 if err!=nil{return false};defer response.Body.Close()
 return response.StatusCode==http.StatusOK
}
