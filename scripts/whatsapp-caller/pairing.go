package main

import (
	"context"
	"log"
	"sync"
	"time"

	wa "github.com/polymorfa/hypermeow"
)

var (
	pairingMu     sync.Mutex
	pairingCancel context.CancelFunc
)

// startPairing initiates or restarts the WhatsApp QR pairing flow.
func startPairing(parentCtx context.Context, socket *wa.Client) {
	if socket == nil {
		return
	}
	pairingMu.Lock()
	defer pairingMu.Unlock()

	// If already connected with an active stored session, don't restart pairing
	if socket.Store != nil && socket.Store.ID != nil {
		current.Lock()
		current.LinkedNumber = socket.Store.ID.User
		if socket.Store.PushName != "" {
			current.PushName = socket.Store.PushName
		}
		current.State = "connected"
		current.Unlock()
		return
	}

	// Cancel previous pairing attempt if one was running
	if pairingCancel != nil {
		pairingCancel()
		pairingCancel = nil
	}

	current.Lock()
	current.State = "pairing"
	current.code = ""
	current.Unlock()

	pairCtx, cancel := context.WithCancel(parentCtx)
	pairingCancel = cancel

	go func() {
		defer func() {
			pairingMu.Lock()
			pairingCancel = nil
			pairingMu.Unlock()
		}()
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
