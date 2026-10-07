package main

import (
 "net/http"
 "net/http/httptest"
 "os"
 "path/filepath"
 "strings"
 "testing"
)

func TestPairingNeedsBackendAuthenticationAndRetainsConnectedSession(t *testing.T) {
 dir:=t.TempDir();key:=filepath.Join(dir,"key");marker:=filepath.Join(dir,"marker")
 os.WriteFile(key,[]byte(strings.Repeat("k",40)),0600)
 connected:=false
 handler:=pairingHandler(key,marker,func()bool{return connected})
 call:=func(method,token string)int{r:=httptest.NewRequest(method,"/pairing/start",nil);r.Header.Set("Authorization",token);w:=httptest.NewRecorder();handler(w,r);return w.Code}
 if call(http.MethodGet,"")!=405 || call(http.MethodPost,"")!=401 {t.Fatal("Pairing must reject unauthenticated/GET mutation")}
 if _,err:=os.Stat(marker);!os.IsNotExist(err){t.Fatal("Denied pairing created a marker")}
 if call(http.MethodPost,"Bearer "+strings.Repeat("k",40))!=200 {t.Fatal("Authorized pairing failed")}
 if value,_:=os.ReadFile(marker);string(value)!="enabled\n"{t.Fatal("Missing pairing marker")}
 os.WriteFile(marker,[]byte("retained"),0600);connected=true
 if call(http.MethodPost,"Bearer "+strings.Repeat("k",40))!=200{t.Fatal("Already-connected check failed")}
 if value,_:=os.ReadFile(marker);string(value)!="retained"{t.Fatal("Connected device state changed")}
}

func TestDisconnectNeedsBackendAuthentication(t *testing.T) {
 dir:=t.TempDir();key:=filepath.Join(dir,"key")
 os.WriteFile(key,[]byte(strings.Repeat("k",40)),0600)
 disconnected:=false
 handler:=disconnectHandler(key,func()error{disconnected=true;return nil})
 call:=func(method,token string)int{r:=httptest.NewRequest(method,"/pairing/disconnect",nil);r.Header.Set("Authorization",token);w:=httptest.NewRecorder();handler(w,r);return w.Code}
 if call(http.MethodGet,"")!=405 || call(http.MethodPost,"")!=401 {t.Fatal("Disconnect must reject unauthenticated/GET mutation")}
 if disconnected {t.Fatal("Denied disconnect triggered callback")}
 if call(http.MethodPost,"Bearer "+strings.Repeat("k",40))!=200 {t.Fatal("Authorized disconnect failed")}
 if !disconnected {t.Fatal("Authorized disconnect did not trigger callback")}
}
