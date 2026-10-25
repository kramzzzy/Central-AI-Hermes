package main

import (
 "context"
 "testing"
 "time"
 meow "github.com/purpshell/meowcaller"
)

func TestAnswerDelayRingsFirstAndSkipsCallsThatEnd(t *testing.T) {
 t.Setenv("WHATSAPP_ANSWER_DELAY_SECONDS","1")
 c:=&fakeIncoming{phase:meow.CallPhaseRinging}
 start:=time.Now()
 if !waitBeforeAnswer(context.Background(),c) || time.Since(start)<time.Second {t.Fatal("must ring for the configured delay before answering")}
 go func(){time.Sleep(300*time.Millisecond);c.mu.Lock();c.phase=meow.CallPhaseEnded;c.mu.Unlock()}()
 if waitBeforeAnswer(context.Background(),c) {t.Fatal("must not answer a call that ended while ringing")}
 t.Setenv("WHATSAPP_ANSWER_DELAY_SECONDS","")
 c.mu.Lock();c.phase=meow.CallPhaseRinging;c.mu.Unlock()
 if !waitBeforeAnswer(context.Background(),c) {t.Fatal("without a delay the call is answered immediately")}
}
