package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/coder/websocket"
	meow "github.com/purpshell/meowcaller"
)

// The private filter gets caller audio continuously and the exact frames that
// have been rendered to WhatsApp, allowing echo cancellation while listening.
func (v *voiceAudio) connectFilter(ctx context.Context) (*websocket.Conn, error) {
	headers := http.Header{"Authorization": []string{"Bearer " + v.key}, "X-Call-ID": []string{v.id}}
	url := strings.Replace(os.Getenv("LEO_AUDIO_URL"), "http", "ws", 1) + "/audio"
	socket, _, err := websocket.Dial(ctx, url, &websocket.DialOptions{HTTPHeader: headers, CompressionMode: websocket.CompressionDisabled})
	if err == nil {
		socket.SetReadLimit(4096)
	}
	return socket, err
}

func (v *voiceAudio) runFilter(socket *websocket.Conn) {
	for v.ctx.Err() == nil {
		connectionCtx, stop := context.WithCancel(v.ctx)
		failed := make(chan struct{}, 2)
		go func() {
			defer func() { failed <- struct{}{} }()
			for {
				select {
				case <-connectionCtx.Done():
					return
				case message := <-v.capture:
					writeCtx, cancel := context.WithTimeout(connectionCtx, time.Second)
					err := socket.Write(writeCtx, websocket.MessageBinary, message)
					cancel()
					if err != nil {
						return
					}
				}
			}
		}()
		go func() {
			defer func() { failed <- struct{}{} }()
			for {
				kind, data, err := socket.Read(connectionCtx)
				if err != nil {
					return
				}
				if v.fish {
					if kind == websocket.MessageText {
						var state struct { Type string `json:"type"`; State string `json:"state"`; Generation uint64 `json:"generation"` }
						if json.Unmarshal(data, &state) != nil || state.Type != "state" || state.Generation == 0 { return }
						v.mu.Lock()
						interrupted := state.Generation > v.generation
						if interrupted { v.generation = state.Generation }
						v.mu.Unlock()
						current.Lock(); current.VoiceState = state.State; if interrupted { current.Interruptions++ }; current.Unlock()
						continue
					}
					if kind != websocket.MessageBinary || len(data) != 1928 { return }
					generation := binary.BigEndian.Uint64(data[:8])
					v.mu.Lock(); valid := generation == v.generation; v.mu.Unlock()
					if valid { v.enqueueFor(connectionCtx, generation, data[8:]) }
					continue
				}
				if kind != websocket.MessageBinary || len(data) != 1921 || data[0] > 1 {
					return
				}
				frame := make([]float32, meow.FrameSamples)
				for i := range frame {
					frame[i] = float32(int16(binary.LittleEndian.Uint16(data[1+i*2:]))) / 32768
				}
				v.receiveClean(frame, data[0] == 1)
			}
		}()
		<-failed
		stop()
		socket.CloseNow()
		<-failed
		if v.ctx.Err() != nil {
			return
		}
		current.Lock()
		current.VoiceState = "audio_reconnecting"
		current.Unlock()
		if v.fish {
			v.mu.Lock()
			for len(v.playback) > 0 { <-v.playback }
			v.mu.Unlock()
		}
		for v.ctx.Err() == nil {
			select {
			case <-v.ctx.Done():
				return
			case <-time.After(time.Second):
			}
			// Discard stale transport backlog, never replay speech after reconnect.
			for len(v.capture) > 0 {
				<-v.capture
			}
			retryCtx, cancel := context.WithTimeout(v.ctx, 5*time.Second)
			next, err := v.connectFilter(retryCtx)
			cancel()
			if err == nil {
				socket = next
				current.Lock()
				current.VoiceState = "listening"
				current.Unlock()
				break
			}
		}
	}
}

func (v *voiceAudio) captureFrame(kind byte, frame []float32) {
	if v.capture == nil || len(frame) != meow.FrameSamples {
		return
	}
	data := make([]byte, 1+len(frame)*2)
	data[0] = kind
	for i, x := range frame {
		if x > 1 {
			x = 1
		}
		if x < -1 {
			x = -1
		}
		binary.LittleEndian.PutUint16(data[1+i*2:], uint16(int16(x*32767)))
	}
	select {
	case v.capture <- data:
	default:
		current.Lock()
		current.AudioOverruns++
		current.Unlock()
	}
}
