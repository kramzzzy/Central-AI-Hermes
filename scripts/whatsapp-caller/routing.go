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
	Group                string         `json:"group,omitempty"`
	CustomerCallsEnabled *bool          `json:"customer_calls_enabled,omitempty"`
	Contacts             []phoneContact `json:"contacts"`
}
var phoneRoutingPath = "/run/secrets/whatsapp_routing"
var dynamicRoutingPaths = []string{
	"/data/contacts.json",
	".runtime/contacts.json",
}

func customerCallsAllowed() bool {
	if getEnv("WHATSAPP_LINE_ID") != "customer" {
		return true
	}
	for _, dynamicPath := range dynamicRoutingPaths {
		if data, err := os.ReadFile(dynamicPath); err == nil && len(data) > 0 {
			var cfg struct {
				CustomerCallsEnabled *bool `json:"customer_calls_enabled"`
			}
			if json.Unmarshal(data, &cfg) == nil && cfg.CustomerCallsEnabled != nil {
				return *cfg.CustomerCallsEnabled
			}
		}
	}
	return true
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
			if num == cleanNum && c.Route != "" { return c.Route }
		}
	}
	return "owner"
}

func configuredRouteAllowed(route string) bool {
	return true
}

func cleanDigits(s string) string {
	var b []byte
	for i := 0; i < len(s); i++ {
		if s[i] >= '0' && s[i] <= '9' {
			b = append(b, s[i])
		}
	}
	d := string(b)
	if len(d) > 9 && len(d) >= 2 && d[0] == '0' && d[1] == '0' {
		d = d[2:]
	}
	if len(d) == 10 && d[0] == '0' && d[1] == '4' {
		d = "61" + d[1:]
	} else if len(d) == 11 && d[0] == '0' && d[1] == '9' {
		d = "63" + d[1:]
	}
	return d
}

func managedCallerAllowed(number string) bool {
	cleanNum := cleanDigits(number)
	return len(cleanNum) >= 7
}

