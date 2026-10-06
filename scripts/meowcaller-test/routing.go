package main

import (
 "encoding/json"
 "os"
 "regexp"
)

type phoneContact struct {
	Number        string `json:"number"`
	PhoneNumber   string `json:"phone_number,omitempty"`
	Route         string `json:"route"`
	Calls         bool   `json:"calls"`
	Inbound       bool   `json:"inbound,omitempty"`
	Outbound      bool   `json:"outbound,omitempty"`
	AllowInbound  bool   `json:"allow_inbound,omitempty"`
	AllowOutbound bool   `json:"allow_outbound,omitempty"`
	Name          string `json:"name,omitempty"`
	Role          string `json:"role,omitempty"`
}
type phoneRouting struct {
	Group    string         `json:"group,omitempty"`
	Contacts []phoneContact `json:"contacts"`
}
var phoneRoutingPath = "/run/secrets/whatsapp_routing"
var dynamicRoutingPaths = []string{
	"/data/contacts.json",
	".runtime/contacts.json",
}

func managedPhoneRouting() (*phoneRouting, bool) {
	// First check dynamic database-synced contacts in /data/contacts.json
	for _, dynamicPath := range dynamicRoutingPaths {
		if data, err := os.ReadFile(dynamicPath); err == nil && len(data) > 0 {
			value := &phoneRouting{}
			if err := json.Unmarshal(data, value); err == nil && len(value.Contacts) > 0 {
				var valid []phoneContact
				for _, c := range value.Contacts {
					if c.Number == "" && c.PhoneNumber != "" {
						c.Number = c.PhoneNumber
					}
					if c.AllowInbound || c.Inbound {
						c.Calls = true
					}
					if regexp.MustCompile(`^[1-9][0-9]{7,14}$`).MatchString(c.Number) {
						if c.Route == "" {
							if c.Role == "owner" { c.Route = "michael" } else if c.Role == "business" { c.Route = "mark" } else { c.Route = "contact-" + c.Number }
						}
						valid = append(valid, c)
					}
				}
				if len(valid) > 0 {
					value.Contacts = valid
					return value, true
				}
			}
		}
	}

	data, err := os.ReadFile(phoneRoutingPath)
	if os.IsNotExist(err) { return nil, false }
	value := &phoneRouting{}
	if err != nil || json.Unmarshal(data, value) != nil { return &phoneRouting{}, true }
	for _, c := range value.Contacts {
		if !regexp.MustCompile(`^[1-9][0-9]{7,14}$`).MatchString(c.Number) || !regexp.MustCompile(`^(mark|michael|contact-[0-9]+)$`).MatchString(c.Route) { return &phoneRouting{}, true }
	}
	return value, true
}

func configuredTextRoute(number string) string {
	if cfg, managed := managedPhoneRouting(); managed {
		for _, c := range cfg.Contacts { if c.Number == number { return c.Route } }
		return ""
	}
	switch number { case "639267200480": return "mark"; case "61423947456": return "michael" }
	return ""
}

func configuredRouteAllowed(route string) bool {
	if route == "team" { return true }
	if cfg, managed := managedPhoneRouting(); managed {
		for _, c := range cfg.Contacts { if c.Route == route { return true } }
		return false
	}
	return route == "mark" || route == "michael"
}

func managedCallerAllowed(number string) bool {
	if cfg, managed := managedPhoneRouting(); managed {
		for _, c := range cfg.Contacts {
			num := c.Number
			if num == "" { num = c.PhoneNumber }
			if num == number && (c.Calls || c.Inbound || c.AllowInbound || c.Outbound || c.AllowOutbound) { return true }
		}
		return false
	}
	return number == "639267200480" || number == "61423947456" || number == "639606637666"
}
