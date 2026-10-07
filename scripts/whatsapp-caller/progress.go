package main

import (
 "encoding/json"
 "log"
 "regexp"
)

// Retain only fixed lifecycle messages and numeric audio counters. Never emit
// upstream protocol fields, keys, relay credentials, audio or transcripts.
type callProgressWriter struct{}
func (callProgressWriter)Write(data []byte)(int,error) {
 var event struct {
  Level string `json:"level"`
  Message string `json:"message"`
  Frames int `json:"frames"`
  Samples int `json:"samples"`
  Timestamp uint32 `json:"timestamp"`
  Packets int `json:"packets"`
  Payload uint8 `json:"payload_type"`
  Code string `json:"error_code"`
  Codec string `json:"codec"`
  Connected int `json:"connected"`
  Offered int `json:"offered"`
 }
 if json.Unmarshal(data,&event)!=nil{return len(data),nil}
 if event.Message=="relay fanout established" {
  current.Lock();current.ConnectedRelays=event.Connected;current.OfferedRelays=event.Offered;current.Unlock()
  log.Printf("WhatsApp relay connections: connected=%d offered=%d",event.Connected,event.Offered)
 } else if event.Message=="call rejected by server" && regexp.MustCompile(`^[0-9]{3}$`).MatchString(event.Code) {
  current.Lock();current.SignallingError=event.Code;current.Unlock()
  log.Print("WhatsApp signalling rejected: ",event.Code)
 } else if event.Message=="selected audio codec from voip_settings" && (event.Codec=="mlow" || event.Codec=="opus") {
  current.Lock();current.Codec=event.Codec;current.Unlock()
  log.Print("WhatsApp negotiated codec: ",event.Codec)
 } else if event.Message=="received RTP progress" {
  current.Lock();current.RTPPackets=event.Packets;current.PayloadType=event.Payload;current.Unlock()
  if event.Packets<4 || event.Packets%25==0 {log.Printf("RTP received: packets=%d payload=%d",event.Packets,event.Payload)}
 } else if event.Message=="incoming audio progress" {
  current.Lock();current.Decoded=event.Frames;current.DecodeSamples=event.Samples;current.RTPTime=event.Timestamp;current.Unlock()
  if event.Frames<4 || event.Frames%25==0 {log.Printf("audio decoded: frames=%d samples=%d timestamp=%d",event.Frames,event.Samples,event.Timestamp)}
 } else {
  switch event.Message {
  case "sent incoming offer receipt", "incoming offer receipt failed", "sent typed microphone-state acknowledgement", "microphone-state acknowledgement failed", "relay endpoint changed after media start", "relay peer changed after media start":
   log.Print("WhatsApp call: ",event.Message)
  case "first mute_v2 received; sending deferred accept", "accepted (after mute_v2)", "starting media", "first RTP decoded from relay, inbound audio flowing", "started timestamp-aligned inbound audio playout", "audio RTP did not match an authenticated active participant", "media ended", "send accept failed", "failed to write timestamp-aligned WhatsApp audio", "relay silent after allocate, no bytes back yet (allocate undelivered or rejected)":
   log.Print("WhatsApp call: ",event.Message)
  default:
   if event.Level=="warn" || event.Level=="error" {
    log.Printf("WhatsApp [%s]: %s (code=%s)", event.Level, event.Message, event.Code)
   }
  }
 }
 return len(data),nil
}
