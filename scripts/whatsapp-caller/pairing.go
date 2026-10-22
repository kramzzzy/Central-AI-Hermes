package main

import (
	"context"
	"log"
	"sync"
	"time"

	wa "github.com/polymorfa/hypermeow"
)

var (
	pairingMu        sync.Mutex
	pairingCancel    context.CancelFunc
	pairingDone      chan struct{}
	pairingStartedAt time.Time
)

// startPairing initiates or restarts the WhatsApp QR pairing flow.
// Guarantees at most one active pairing/connection loop on socket at any time.
func startPairing(parentCtx context.Context, socket *wa.Client) {
	if socket == nil {
		return
	}
	pairingMu.Lock()
	defer pairingMu.Unlock()

	// If already connected with an active stored session, ensure connected state
	if socket.Store != nil && socket.Store.ID != nil {
		current.Lock()
		current.LinkedNumber = socket.Store.ID.User
		if socket.Store.PushName != "" {
			current.PushName = socket.Store.PushName
		}
		if socket.IsConnected() {
			current.State = "connected"
			current.Unlock()
			return
		}
		current.Unlock()
	}

	// If a pairing loop is already running and either has a valid QR code or started recently (<12s),
	// do NOT cancel or disconnect it—let it finish emitting/displaying the QR code.
	if pairingDone != nil {
		select {
		case <-pairingDone:
			pairingDone = nil
		default:
			current.Lock()
			st := current.State
			hasValidCode := current.code != "" && time.Now().Before(current.expires)
			current.Unlock()
			if hasValidCode || st == "reconnecting" || (st == "pairing" && time.Since(pairingStartedAt) < 12*time.Second) {
				return
			}
			// Otherwise cancel stale loop and wait for it to exit cleanly
			if pairingCancel != nil {
				pairingCancel()
				pairingCancel = nil
			}
			doneCh := pairingDone
			pairingMu.Unlock()
			select {
			case <-doneCh:
			case <-time.After(2 * time.Second):
			}
			pairingMu.Lock()
			pairingDone = nil
		}
	}

	current.Lock()
	if socket.Store == nil || socket.Store.ID == nil {
		current.State = "pairing"
	}
	current.Unlock()

	pairCtx, cancel := context.WithCancel(parentCtx)
	pairingCancel = cancel
	done := make(chan struct{})
	pairingDone = done
	pairingStartedAt = time.Now()

	go func() {
		defer close(done)
		connectPairedDevice(pairCtx, socket)
	}()
}

// Expired, unscanned codes refresh without deleting a stored paired device.
func connectPairedDevice(ctx context.Context, socket *wa.Client) {
	if socket == nil {
		return
	}
	if socket.Store != nil && socket.Store.ID != nil {
		backoff := 1 * time.Second
		for ctx.Err() == nil {
			if socket.IsConnected() {
				current.Lock()
				current.LinkedNumber = socket.Store.ID.User
				if socket.Store.PushName != "" {
					current.PushName = socket.Store.PushName
				}
				current.State = "connected"
				current.Unlock()
				return
			}
			err := socket.Connect()
			if err == nil {
				current.Lock()
				current.LinkedNumber = socket.Store.ID.User
				if socket.Store.PushName != "" {
					current.PushName = socket.Store.PushName
				}
				current.State = "connected"
				current.Unlock()
				log.Printf("WhatsApp connected successfully for line +%s", socket.Store.ID.User)
				return
			}
			log.Printf("retained WhatsApp connection attempt failed: %v (retrying in %v)", err, backoff)
			current.Lock()
			current.State = "reconnecting"
			current.Unlock()
			select {
			case <-ctx.Done():
				return
			case <-time.After(backoff):
			}
			if backoff < 16*time.Second {
				backoff *= 2
			}
		}
		return
	}

	// Disconnect if already connected so GetQRChannel succeeds cleanly
	if socket.IsConnected() {
		socket.Disconnect()
		time.Sleep(300 * time.Millisecond)
	}

	for ctx.Err() == nil && (socket.Store == nil || socket.Store.ID == nil) {
		channel, err := socket.GetQRChannel(ctx)
		if err != nil {
			log.Printf("pairing channel unavailable: %v", err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
				continue
			}
		}
		if err := socket.Connect(); err != nil {
			log.Printf("WhatsApp pairing connection failed: %v", err)
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
				continue
			}
		}

		success := false
		for event := range channel {
			current.Lock()
			if event.Event == "code" {
				current.State = "pairing"
				current.code = event.Code
				current.expires = time.Now().Add(event.Timeout)
			}
			if event.Event == "success" {
				success = true
				if socket.Store != nil && socket.Store.ID != nil {
					current.LinkedNumber = socket.Store.ID.User
					if socket.Store.PushName != "" {
						current.PushName = socket.Store.PushName
					}
				}
				current.State = "connected"
			}
			if event.Event == "timeout" {
				current.State = "qr_timeout"
				current.code = ""
			}
			current.Unlock()
		}

		if success || (socket.Store != nil && socket.Store.ID != nil) || ctx.Err() != nil {
			if socket.Store != nil && socket.Store.ID != nil {
				current.Lock()
				current.LinkedNumber = socket.Store.ID.User
				if socket.Store.PushName != "" {
					current.PushName = socket.Store.PushName
				}
				current.State = "connected"
				current.Unlock()
			}
			return
		}
		socket.Disconnect()
		select {
		case <-ctx.Done():
			return
		case <-time.After(2 * time.Second):
		}
	}
}
