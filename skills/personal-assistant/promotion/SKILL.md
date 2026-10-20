---
name: promotion
description: Create scroll-stopping, straightforward promotional posts, offers, and ad hooks with photorealistic visual art direction.
version: 1.0.0
author: kramzzzy, Central AI
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [promotion, social-media, direct-response, copywriting, ads]
    related_skills: [marketing]
---

# Promotion (`/promotion`)

Create high-impact, straightforward promotional posts and offers that immediately grab attention in a crowded social feed. Every promotion pairs direct, no-fluff Australian copywriting with an authentic, photorealistic photography brief.

## When to Use

Use when the user asks to promote a service, product, seasonal offer, rebate, solar/battery package, or business outcome on social media, or invokes `/promotion`.

## Core Principles: Straightforward & Realistic

1. **Kill Weak Openers**:
   - NEVER start a post with *"Did you know..."*, *"In today's fast-paced world..."*, *"Are you looking to..."*, or generic AI clichés.
   - Start with a **3-Second Scroll-Stopping Hook**: a concrete dollar figure, a real homeowner frustration, a blunt contrast, or a direct outcome.
   - *Bad*: "Did you know pairing a smart thermostat with solar panels can save energy?"
   - *Good*: "Stop running your AC on 45c/kWh peak grid power when your roof makes free electricity all afternoon."

2. **Straight-Talking Australian Tone**:
   - Write like a trusted local expert speaking to a homeowner or business owner over the fence.
   - Keep sentences short and scannable (1–2 sentences per paragraph).
   - Use concrete numbers (`kW`, `kWh`, `$ saved per quarter`, `payback period`, `peak tariff 3pm–9pm`) instead of vague promises.
   - Limit emojis to at most 1–2 per post. Let the facts and the photograph do the heavy lifting.

3. **Photorealistic Visual Direction (Zero Generic Graphics)**:
   - Every promotional post MUST include a dedicated `**Visual Brief:**` that describes a **real, candid or architectural commercial photograph** — never flat icons, cartoons, or abstract illustrations.
   - Specify: subject, real Australian setting (e.g. modern Colorbond or tile roof, sunlit kitchen, tidy garage with wall-mounted battery, real family or technician), lighting (warm golden hour or crisp midday daylight), and camera feel (`35mm editorial photography, natural lighting, photorealistic`).

## Required Output Structure for Every Post

Always format each promotional post or day using these exact fields so App OS renders the photorealistic image and ad overlay automatically:

```markdown
### Post 1: [Direct, Outcome-Focused Title]
- **Attention Hook:** [5–9 words bold headline that stops the scroll on the image]
- **Concept:** [The specific pain point or value angle being targeted]
- **Caption:** "[Direct, punchy 2–4 sentence copy leading with the hook, concrete proof, and a clear next step.]"
- **Visual Brief:** [Detailed photorealistic photography prompt: real setting, subject, lighting, camera angle, 35mm lens, natural Australian light, no text clutter]
- **CTA:** [Single frictionless action, e.g. "DM 'BILL' for a 2-minute roof & tariff check"]
- **Hashtags:** #SolarAustralia #EnergySavings #CleanEnergy
```

## Verification

- Check that `**Visual Brief:**` is a concrete camera/scene description and NEVER repeats the `**Caption:**`.
- Confirm the opening line of `**Caption:**` and `**Attention Hook:**` contain zero filler questions and lead with a direct hook.
