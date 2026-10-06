package main

import (
 "context"
 "crypto/hmac"
 "encoding/json"
 "net/http"
 "os"
 "time"
 wa "github.com/polymorfa/hypermeow"
)

func groupsHandler(socket *wa.Client) http.HandlerFunc {
 return func(w http.ResponseWriter,r *http.Request) {
  key,err:=os.ReadFile("/run/secrets/whatsapp_setup_key")
  if r.Method!="POST" || err!=nil || len(key)<32 || !hmac.Equal([]byte(r.Header.Get("Authorization")),append([]byte("Bearer "),key...)) {http.Error(w,"Unauthorized",401);return}
  ctx,cancel:=context.WithTimeout(r.Context(),4*time.Second);defer cancel()
  groups,err:=socket.GetJoinedGroups(ctx)
  if err!=nil {http.Error(w,"Connect WhatsApp before loading groups",503);return}
  result:=[]map[string]string{}
  for _,group:=range groups {if len(result)>=1000 {break};result=append(result,map[string]string{"id":group.JID.String(),"name":group.Name})}
  w.Header().Set("Content-Type","application/json");w.Header().Set("Cache-Control","no-store")
  json.NewEncoder(w).Encode(map[string]any{"groups":result})
 }
}
