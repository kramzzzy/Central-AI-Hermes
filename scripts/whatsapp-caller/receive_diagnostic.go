package main

import (
 "runtime"
 "strings"
)

// Return only a fixed stage, never a stack, argument, key, audio or transcript.
// The direct receive loop lives in runMedia; its child sender goroutines do not
// have that exact function in their stack and are excluded.
func receiveStage() string {
 count,_:=runtime.GoroutineProfile(nil)
 records:=make([]runtime.StackRecord,count+32)
 count,ok:=runtime.GoroutineProfile(records)
 if !ok{return "snapshot_busy"}
 for _,record:=range records[:count] {
  frames:=runtime.CallersFrames(record.Stack())
  functions:=[]string{}
  receiver:=false
  for {frame,more:=frames.Next();functions=append(functions,frame.Function);if frame.Function=="github.com/purpshell/meowcaller.(*engine).runMedia"{receiver=true};if !more{break}}
  if !receiver{continue}
  for _,name:=range functions {
   if strings.Contains(name,".(*RelayMediaChannel).Recv") || strings.Contains(name,".(*relayFanout).Recv"){return "network_wait"}
   if strings.Contains(name,".DecodeAudio") || strings.Contains(name,"mlow."){return "decoding"}
   if strings.Contains(name,".(*audioPlayoutBuffer).Push") || strings.Contains(name,".SinkFunc.WriteFrame"){return "audio_sink"}
  }
  return "receiving"
 }
 return "inactive"
}
