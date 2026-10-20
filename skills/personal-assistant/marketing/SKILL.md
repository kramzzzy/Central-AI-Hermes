---
name: marketing
description: Plan and write high-converting multi-day social media campaigns, content calendars, and realistic brand storytelling series.
version: 1.0.0
author: kramzzzy, Central AI
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [marketing, campaign, social-media, content-strategy, visual-direction]
    related_skills: [promotion]
---

# Marketing (`/marketing`)

Build structured, attention-retaining social media campaigns and multi-day series where every single day attacks a distinct buyer trigger and features a unique, photorealistic visual scene.

## When to Use

Use when the user asks for a social media campaign, multi-day content series (e.g. 5-day or 7-day series), brand awareness push, lead-generation campaign, or invokes `/marketing`.

## Multi-Angle Attention Framework

Never repeat the same angle or visual scene across a series. Rotate through proven psychological attention triggers:

1. **Day / Angle 1 — The Hard Math (Bill Shock & Tariff Reality)**:
   - Expose where money is actually leaking (e.g., daytime solar export rates vs. 45c/kWh evening peak rates, pre-cooling the home at 2pm using free solar).
2. **Day / Angle 2 — Real Environment & Comfort (Passive + Active Efficiency)**:
   - Show how real home upgrades, shading, insulation, or smart load shifting work together with solar in Australian conditions.
3. **Day / Angle 3 — Hardware in the Wild (Battery & Night Independence)**:
   - Show the physical hardware in a clean garage or exterior wall working after sundown so the homeowner keeps their own power.
4. **Day / Angle 4 — Myth vs. Reality (Cloudy Days, Winter, or Maintenance)**:
   - Bust a common objection with straightforward technical truth (e.g. modern monocrystalline panels generating in overcast daylight).
5. **Day / Angle 5 — Smart Visibility (App & Live Meter Control)**:
   - Show the satisfaction of checking live self-consumption and $0 grid draw on a phone or kitchen display.
6. **Day / Angle 6 — Real Proof / Case Study**:
   - Walk through a realistic household scenario (e.g. 4-bedroom family home cutting quarterly bills from $780 to $95).
7. **Day / Angle 7 — Direct Next Step (No-Pressure Assessment)**:
   - Summarize the week's takeaway and invite a simple, straightforward action.

## Mandatory Format for Campaign Files & Drafts

When saving a campaign to `Files` (`file_save`) or returning a campaign draft for `Approvals`, EVERY day/post MUST include all 6 fields below (never omit `**Visual Brief:**` or `**Attention Hook:**`):

```markdown
### Day 1: [Specific, Straightforward Topic]
- **Attention Hook:** [Bold 5–9 word scroll-stopping headline]
- **Concept:** [Psychological trigger + core message]
- **Caption:** "[Straightforward, conversational copy starting with a strong statement—never 'Did you know'. Specific numbers, practical value, and a clear takeaway.]"
- **Visual Brief:** [Photorealistic commercial photography prompt describing a unique real-world scene, subject, Australian lighting, and 35mm camera composition]
- **CTA:** [Specific low-friction call to action]
- **Hashtags:** #...
```

## Pitfalls to Avoid

1. **Never Duplicate Caption into Visual Brief**: `**Visual Brief:**` must describe what the camera sees (people, home, hardware, lighting), not what the caption says.
2. **Never Use Generic Stock Cliches**: Avoid vague prompts like "a green planet with solar panels". Describe a real Australian home, real roof, real garage, or real person.
3. **No Corporate Fluff**: Strip out words like "revolutionary", "unleash", "elevate", "game-changer", and "delve". Keep it grounded, real, and effective.
