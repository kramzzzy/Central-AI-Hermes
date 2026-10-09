package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/binary"
	"encoding/json"
	"fmt"
	meow "github.com/purpshell/meowcaller"
	"io"
	"math"
	"net/http"
	"os"
	"sync"
	"time"
)

type voiceAudio struct {
	ctx              context.Context
	cancel           context.CancelFunc
	id               string
	key              string
	callerNumber     string
	output           chan []float32
	mu               sync.Mutex
	busy             bool
	samples          []float32
	voiced           int
	silent           int
	pre              [][]float32
	once             sync.Once
	lastSpeech       time.Time
	mediaReady       chan struct{}
	readyOnce        sync.Once
	sendTurn         func([]byte)
	capture          chan []byte
	playback         chan speechFrame
	generation       uint64
	turnID           string
	turnCancel       context.CancelFunc
	lastInput        []float32
	interruptPending bool
	extended         bool
	speechRun        int
	fish             bool
}

type speechFrame struct {
	generation uint64
	samples    []float32
}

var voiceHTTPClient = &http.Client{
	Transport: &http.Transport{
		MaxIdleConns:        64,
		MaxIdleConnsPerHost: 32,
		IdleConnTimeout:     90 * time.Second,
		DisableCompression:  true,
	},
}

func (v *voiceAudio) request(ctx context.Context, path string, body []byte) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, "POST", getEnv("VOICE_URL", "LEO_VOICE_URL")+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+v.key)
	req.Header.Set("X-Call-ID", v.id)
	if v.callerNumber != "" { req.Header.Set("X-Caller-Number", v.callerNumber) }
	resp, err := voiceHTTPClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("voice operation %s failed (%d)", path, resp.StatusCode)
	}
	data, err := io.ReadAll(io.LimitReader(resp.Body, 16000*2*45+1))
	if len(data) > 16000*2*45 {
		return nil, fmt.Errorf("voice audio too large")
	}
	return data, err
}

func prepareVoice(ctx context.Context) (*voiceAudio, error) {
	return prepareVoiceFor(ctx, getEnv("WHATSAPP_OWNER", "LEO_WHATSAPP_OWNER"))
}

func prepareVoiceFor(ctx context.Context, callerNumber string) (*voiceAudio, error) {
    return prepareVoiceWithCallback(ctx, callerNumber, "")
}

func prepareVoiceOutbound(ctx context.Context, target string) (*voiceAudio, error) {
    return prepareVoiceInternal(ctx, target, "", true)
}

func prepareVoiceWithCallback(ctx context.Context, callerNumber, callbackID string) (*voiceAudio, error) {
    return prepareVoiceInternal(ctx, callerNumber, callbackID, callbackID != "")
}

func prepareVoiceInternal(ctx context.Context, callerNumber, callbackID string, isOutbound bool) (*voiceAudio, error) {
	cleanCaller := cleanDigits(callerNumber)
	if cleanCaller == "" {
		return nil, fmt.Errorf("valid phone number required")
	}
	key, err := os.ReadFile("/data/voice-key")
	if err != nil {
		return nil, fmt.Errorf("voice backend is not ready")
	}
	var id [16]byte
	rand.Read(id[:])
	id[6] = (id[6] & 15) | 64
	id[8] = (id[8] & 63) | 128
	callCtx, cancel := context.WithCancel(ctx)
	mode := os.Getenv("WHATSAPP_CONVERSATION_MODE")
	v := &voiceAudio{ctx: callCtx, cancel: cancel, key: string(key), id: fmt.Sprintf("%x-%x-%x-%x-%x", id[0:4], id[4:6], id[6:8], id[8:10], id[10:]), output: make(chan []float32, 8), playback: make(chan speechFrame, 8), capture: make(chan []byte, 64), generation: 1, mediaReady: make(chan struct{}), fish: mode == "fish" || mode == "stream"}
	v.callerNumber = callerNumber
	startCtx, stop := context.WithTimeout(ctx, 80*time.Second)
	defer stop()
    var body []byte
    if callbackID != "" {body, _ = json.Marshal(map[string]string{"callback_id": callbackID})}
	if _, err := v.request(startCtx, "/start", body); err != nil {
		cancel()
		return nil, err
	}
	socket, err := v.connectFilter(startCtx)
	if err != nil {
		v.Close()
		return nil, fmt.Errorf("continuous audio is not ready")
	}
	go v.runFilter(socket)
	audio, err := v.request(startCtx, "/greeting", nil)
	if err != nil {
		v.Close()
		return nil, err
	}
	go v.enqueue(audio)
	go func() {
		if v.fish { return }
		tick := time.NewTicker(30 * time.Millisecond)
		defer tick.Stop()
		for {
			select {
			case <-v.ctx.Done():
				return
			case now := <-tick.C:
				v.flushIfQuiet(now)
			}
		}
	}()
	go func() {
		ticker := time.NewTicker(8 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-v.ctx.Done():
				return
			case <-ticker.C:
				pulse, done := context.WithTimeout(v.ctx, 5*time.Second)
				_, err := v.request(pulse, "/heartbeat", nil)
				done()
				current.Lock()
				if err != nil {
					current.HeartbeatMisses++
					current.VoiceState = "backend_recovering"
				} else {
					current.HeartbeatMisses = 0
				}
				current.Unlock()
			}
		}
	}()
	return v, nil
}

func (v *voiceAudio) enqueue(data []byte) {
	v.mu.Lock()
	generation := v.generation
	v.mu.Unlock()
	v.enqueueFor(v.ctx, generation, data)
}
func (v *voiceAudio) enqueueFor(ctx context.Context, generation uint64, data []byte) {
	for offset := 0; offset < len(data); offset += meow.FrameSamples * 2 {
		frame := make([]float32, meow.FrameSamples)
		for i := 0; i < meow.FrameSamples && offset+i*2+1 < len(data); i++ {
			frame[i] = float32(int16(binary.LittleEndian.Uint16(data[offset+i*2:]))) / 32768
		}
		if v.playback != nil {
			v.mu.Lock()
			currentGen := v.generation
			v.mu.Unlock()
			if generation != currentGen {
				return
			}
			select {
			case v.playback <- speechFrame{generation, frame}:
			case <-ctx.Done():
				return
			case <-v.ctx.Done():
				return
			}
		} else {
			select {
			case v.output <- frame:
			case <-ctx.Done():
				return
			case <-v.ctx.Done():
				return
			}
		}
	}
}

func (v *voiceAudio) ReadFrame() ([]float32, error) {
	select {
	case <-v.ctx.Done():
		return nil, io.EOF
	default:
	}
	if v.mediaReady != nil {
		select {
		case <-v.mediaReady:
		default:
			return make([]float32, meow.FrameSamples), nil
		}
	}
	frame := make([]float32, meow.FrameSamples)
	if v.playback != nil {
		v.mu.Lock()
		for len(v.playback) > 0 {
			entry := <-v.playback
			if entry.generation == v.generation {
				frame = entry.samples
				break
			}
		}
		v.mu.Unlock()
	} else {
		select {
		case queued := <-v.output:
			frame = queued
		default:
		}
	}
	audible := false
	for _, x := range frame {
		if x != 0 {
			audible = true
			break
		}
	}
	if audible {
		current.Lock()
		current.Played++
		current.Unlock()
	}
	v.captureFrame(2, frame)
	return frame, nil
}

func (v *voiceAudio) Receive(frame []float32) {
	current.Lock()
	current.Received++
	current.Unlock()
	if v.capture != nil {
		v.captureFrame(1, frame)
		return
	}
	energy := float64(0)
	for _, x := range frame {
		energy += float64(x * x)
	}
	v.receiveClean(frame, len(frame) > 0 && math.Sqrt(energy/float64(len(frame))) > 0.012)
}
func (v *voiceAudio) receiveClean(frame []float32, speaking bool) {
	v.mu.Lock()
	defer v.mu.Unlock()
	if speaking {
		v.speechRun++
	} else {
		v.speechRun = 0
	}
	if len(v.samples) == 0 && !speaking {
		v.pre = append(v.pre, append([]float32(nil), frame...))
		if len(v.pre) > 4 {
			v.pre = v.pre[1:]
		}
		return
	}
	if len(v.samples) == 0 {
		for _, p := range v.pre {
			v.samples = append(v.samples, p...)
		}
		v.pre = nil
	}
	v.samples = append(v.samples, frame...)
	if speaking {
		v.voiced++
		v.silent = 0
		v.lastSpeech = time.Now()
	} else {
		v.silent++
	}
	// 180ms of neural-VAD speech interrupts output. Preserve pre-roll and all
	// new input; a generation fence prevents canceled audio from returning.
	if v.capture != nil && v.speechRun >= 3 && (v.busy || len(v.playback) > 0) && !v.interruptPending {
		v.interruptLocked()
	}
	if len(v.samples) < 16000*59 {
		return
	}
	if !v.busy && !v.interruptPending {
		v.flushLocked()
	}
}

func (v *voiceAudio) markReady() {
	v.readyOnce.Do(func() {
		if v.mediaReady != nil {
			close(v.mediaReady)
		}
	})
}
func (v *voiceAudio) flushIfQuiet(now time.Time) {
	v.mu.Lock()
	defer v.mu.Unlock()
	wait := 1100 * time.Millisecond
	if v.extended {
		wait = 2200 * time.Millisecond
	}
	if !v.busy && !v.interruptPending && len(v.samples) > 0 && !v.lastSpeech.IsZero() && now.Sub(v.lastSpeech) >= wait {
		v.flushLocked()
	}
}
func (v *voiceAudio) interruptLocked() {
	oldID := v.turnID
	oldInput := append([]float32(nil), v.lastInput...)
	if v.turnCancel != nil {
		v.turnCancel()
	}
	v.generation++
	v.busy = false
	v.turnID = ""
	v.turnCancel = nil
	v.lastInput = nil
	v.extended = false
	current.Lock()
	current.Interruptions++
	current.VoiceState = "listening"
	current.Unlock()
	if oldID == "" {
		return
	}
	v.interruptPending = true
	go func() {
		ctx, stop := context.WithTimeout(v.ctx, 12*time.Second)
		defer stop()
		body, err := v.request(ctx, "/interrupt", []byte(oldID))
		// The server acknowledges whether native submission had started. Only
		// unsubmitted audio may be combined with a continuation, never actions.
		var ack struct {
			NativeStarted *bool `json:"native_started"`
		}
		merge := err == nil && json.Unmarshal(body, &ack) == nil && ack.NativeStarted != nil && !*ack.NativeStarted
		v.mu.Lock()
		defer v.mu.Unlock()
		if merge && len(oldInput) > 0 {
			v.samples = append(oldInput, v.samples...)
			if len(v.samples) > 16000*59 {
				v.samples = v.samples[len(v.samples)-16000*59:]
			}
		}
		v.interruptPending = false
	}()
}
func (v *voiceAudio) flushLocked() {
	if v.voiced < 2 {
		v.samples = nil
		v.voiced = 0
		v.silent = 0
		return
	}
	data := make([]byte, len(v.samples)*2)
	for i, x := range v.samples {
		if x > 1 {
			x = 1
		}
		if x < -1 {
			x = -1
		}
		binary.LittleEndian.PutUint16(data[i*2:], uint16(int16(x*32767)))
	}
	original := append([]float32(nil), v.samples...)
	final := v.extended
	v.extended = false
	v.samples = nil
	v.voiced = 0
	v.silent = 0
	v.busy = true
	v.speechRun = 0
	if v.sendTurn != nil {
		go v.sendTurn(data)
		return
	}
	v.generation++
	generation := v.generation
	var id [16]byte
	rand.Read(id[:])
	id[6] = (id[6] & 15) | 64
	id[8] = (id[8] & 63) | 128
	turn := fmt.Sprintf("%x-%x-%x-%x-%x", id[0:4], id[4:6], id[6:8], id[8:10], id[10:])
	turnCtx, done := context.WithTimeout(v.ctx, 80*time.Second)
	v.turnID = turn
	v.turnCancel = done
	v.lastInput = original
	current.Lock()
	current.VoiceState = "thinking"
	current.Unlock()
	go func() {
		defer done()
		more, err := v.streamTurnFor(turnCtx, data, turn, generation, final)
		v.mu.Lock()
		if generation != v.generation {
			v.mu.Unlock()
			return
		}
		v.busy = false
		v.turnID = ""
		v.turnCancel = nil
		v.lastInput = nil
		if more {
			v.samples = append(original, v.samples...)
			v.voiced += 2
			v.extended = true
		}
		v.mu.Unlock()
		current.Lock()
		current.VoiceState = "listening"
		if err != nil && v.ctx.Err() == nil {
			current.VoiceErrors++
			current.LastVoiceFailure = "reply_failed"
		}
		current.Unlock()
		// A failed reply is not a failed WhatsApp connection. Never replay the
		// submitted utterance; a fixed recovery phrase contains no model/action.
		if err != nil && turnCtx.Err() == nil {
			recovery, stop := context.WithTimeout(v.ctx, 12*time.Second)
			defer stop()
			audio, e := v.request(recovery, "/recovery", nil)
			if e == nil {
				v.enqueueFor(recovery, generation, audio)
			}
		}
	}()
}

func (v *voiceAudio) streamTurn(ctx context.Context, data []byte) error {
	_, err := v.streamTurnFor(ctx, data, "", v.generation, false)
	return err
}
func (v *voiceAudio) streamTurnFor(ctx context.Context, data []byte, turn string, generation uint64, final bool) (bool, error) {
	req, err := http.NewRequestWithContext(ctx, "POST", getEnv("VOICE_URL", "LEO_VOICE_URL")+"/turn", bytes.NewReader(data))
	if err != nil {
		return false, err
	}
	req.Header.Set("Authorization", "Bearer "+v.key)
	req.Header.Set("X-Call-ID", v.id)
	if turn != "" {
		req.Header.Set("X-Turn-ID", turn)
	}
	if final {
		req.Header.Set("X-Voice-Final", "true")
	}
	resp, err := voiceHTTPClient.Do(req)
	if err != nil {
		return false, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return false, fmt.Errorf("voice stream rejected (%d)", resp.StatusCode)
	}
	if resp.Header.Get("X-Voice-Continue") == "true" {
		return true, nil
	}
	total := 0
	buf := make([]byte, meow.FrameSamples*2)
	for {
		n, readErr := io.ReadFull(resp.Body, buf)
		total += n
		if total > 16000*2*45 || n%2 != 0 {
			return false, fmt.Errorf("invalid PCM stream")
		}
		if n > 0 {
			v.enqueueFor(ctx, generation, buf[:n])
		}
		if readErr == io.EOF || readErr == io.ErrUnexpectedEOF {
			break
		}
		if readErr != nil {
			return false, readErr
		}
	}
	state := resp.Header.Get("X-Native-Reply")
	if state == "" {
		state = resp.Trailer.Get("X-Native-Reply")
	}
	if total > 0 && state != "complete" && state != "review" {
		return false, fmt.Errorf("unfinished native stream")
	}
	return false, nil
}

func (v *voiceAudio) Close() error {
	v.once.Do(func() {
		v.cancel()
		go func() {
			cleanup, stop := context.WithTimeout(context.Background(), 15*time.Second)
			defer stop()
			v.request(cleanup, "/end", nil)
		}()
	})
	return nil
}
