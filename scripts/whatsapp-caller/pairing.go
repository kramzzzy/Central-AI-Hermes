package main

import (
 "context"
 "log"
 "time"
 wa "github.com/polymorfa/hypermeow"
)

// Expired, unscanned codes refresh without deleting a stored paired device.
func connectPairedDevice(ctx context.Context,socket *wa.Client) {
 if socket.Store.ID!=nil {if err:=socket.Connect();err!=nil{log.Print("retained WhatsApp connection failed")};return}
 for ctx.Err()==nil && socket.Store.ID==nil {
  channel,err:=socket.GetQRChannel(ctx)
  if err!=nil{log.Print("pairing channel unavailable");return}
  if err:=socket.Connect();err!=nil{log.Print("WhatsApp pairing connection failed");return}
  success:=false
  for event:=range channel {
   current.Lock()
   if event.Event=="code"{current.State="pairing";current.code=event.Code;current.expires=time.Now().Add(event.Timeout)}
   if event.Event=="success"{success=true}
   if event.Event=="timeout"{current.State="qr_timeout";current.code=""}
   current.Unlock()
  }
  if success || socket.Store.ID!=nil || ctx.Err()!=nil{return}
  socket.Disconnect()
  select{case <-ctx.Done():return;case <-time.After(3*time.Second):}
 }
}
