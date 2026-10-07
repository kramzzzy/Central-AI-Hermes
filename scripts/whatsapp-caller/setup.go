package main

import (
 "crypto/hmac"
 "encoding/json"
 "net/http"
 "os"
 "strings"
)

func getEnv(keys ...string) string {
	for _, k := range keys {
		if val := strings.TrimSpace(os.Getenv(k)); val != "" {
			return val
		}
	}
	return ""
}

func getEnvDefault(defaultVal string, keys ...string) string {
	if val := getEnv(keys...); val != "" {
		return val
	}
	return defaultVal
}

func getSetupKey(keyPath string) string {
	if data, err := os.ReadFile(keyPath); err == nil && len(strings.TrimSpace(string(data))) >= 32 {
		return strings.TrimSpace(string(data))
	}
	if data, err := os.ReadFile("/data/whatsapp_setup_key"); err == nil && len(strings.TrimSpace(string(data))) >= 32 {
		return strings.TrimSpace(string(data))
	}
	if envKey := strings.TrimSpace(os.Getenv("WHATSAPP_SETUP_KEY")); len(envKey) >= 32 {
		return envKey
	}
	if envKey := strings.TrimSpace(os.Getenv("HERMES_API_KEY")); len(envKey) >= 32 {
		return envKey
	}
	return ""
}

// Only the authenticated backend may begin pairing. Never clear an existing device.
func pairingHandler(keyPath, marker string, connected func() bool) http.HandlerFunc {
 return func(w http.ResponseWriter,r *http.Request) {
  w.Header().Set("Cache-Control","no-store")
  if r.Method!=http.MethodPost {w.WriteHeader(http.StatusMethodNotAllowed);return}
  key := getSetupKey(keyPath)
  if len(key)<32 {http.Error(w,"Pairing authentication unavailable",503);return}
  expected:="Bearer "+key
  if !hmac.Equal([]byte(r.Header.Get("Authorization")),[]byte(expected)) {http.Error(w,"Unauthorized",401);return}
  if !connected() {
   if err:=os.WriteFile(marker,[]byte("enabled\n"),0600);err!=nil {http.Error(w,"Pairing could not start",503);return}
  }
  w.Header().Set("Content-Type","application/json")
  json.NewEncoder(w).Encode(map[string]bool{"started":true})
 }
}

func disconnectHandler(keyPath string, onDisconnect func() error) http.HandlerFunc {
 return func(w http.ResponseWriter,r *http.Request) {
  w.Header().Set("Cache-Control","no-store")
  if r.Method!=http.MethodPost {w.WriteHeader(http.StatusMethodNotAllowed);return}
  key := getSetupKey(keyPath)
  if len(key)<32 {http.Error(w,"Pairing authentication unavailable",503);return}
  expected:="Bearer "+key
  if !hmac.Equal([]byte(r.Header.Get("Authorization")),[]byte(expected)) {http.Error(w,"Unauthorized",401);return}
  if onDisconnect!=nil {
   if err:=onDisconnect();err!=nil {http.Error(w,"Disconnect failed: "+err.Error(),500);return}
  }
  w.Header().Set("Content-Type","application/json")
  json.NewEncoder(w).Encode(map[string]bool{"disconnected":true})
 }
}

func serverReady(url string) bool {
 client:=&http.Client{Timeout:5_000_000_000}
 response,err:=client.Get(url)
 if err!=nil{return false};defer response.Body.Close()
 return response.StatusCode==http.StatusOK
}
