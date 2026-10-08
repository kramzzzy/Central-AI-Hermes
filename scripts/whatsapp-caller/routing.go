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
					if c.AllowInbound || c.Inbound || c.AllowOutbound || c.Outbound || c.Calls {
						c.Calls = true
					}
					if regexp.MustCompile(`^[1-9][0-9]{7,14}$`).MatchString(c.Number) {
						if c.Route == "" {
							if c.Role == "owner" { c.Route = "owner" } else if c.Role == "business" { c.Route = "business" } else { c.Route = "contact-" + c.Number }
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
		num := cleanDigits(c.Number)
		if num == "" { num = cleanDigits(c.PhoneNumber) }
		if !regexp.MustCompile(`^[1-9][0-9]{7,14}$`).MatchString(num) || !regexp.MustCompile(`^(mark|michael|owner|business|contact-[0-9]+)$`).MatchString(c.Route) { return &phoneRouting{}, true }
	}
	return value, true
}

func configuredTextRoute(number string) string {
	cleanNum := cleanDigits(number)
	if cleanNum == "" { return "" }
	if cfg, managed := managedPhoneRouting(); managed {
		for _, c := range cfg.Contacts {
			num := cleanDigits(c.Number)
			if num == "" { num = cleanDigits(c.PhoneNumber) }
			if num == cleanNum { return c.Route }
		}
		return ""
	}
	return "contact-" + cleanNum
}

func configuredRouteAllowed(route string) bool {
	if route == "team" { return true }
	if cfg, managed := managedPhoneRouting(); managed {
		for _, c := range cfg.Contacts { if c.Route == route { return true } }
		return false
	}
	return route != ""
}

func cleanDigits(s string) string {
	var b []byte
	for i := 0; i < len(s); i++ {
		if s[i] >= '0' && s[i] <= '9' {
			b = append(b, s[i])
		}
	}
	return string(b)
}

func managedCallerAllowed(number string) bool {
	cleanNum := cleanDigits(number)
	if len(cleanNum) < 7 { return false }
	allowAll := getEnv("WHATSAPP_ALLOW_ALL_INBOUND", "ALLOW_ALL_INBOUND")
	if allowAll == "" || allowAll == "true" || allowAll == "1" {
		return true
	}
	if cfg, managed := managedPhoneRouting(); managed {
		for _, c := range cfg.Contacts {
			num := cleanDigits(c.Number)
			if num == "" { num = cleanDigits(c.PhoneNumber) }
			if num == cleanNum && (c.Calls || c.Inbound || c.AllowInbound || c.Outbound || c.AllowOutbound) { return true }
		}
		return false
	}
	return true
}

