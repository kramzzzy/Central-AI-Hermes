---
name: product
description: Showcase solar panels, home batteries, smart thermostats, and energy monitoring hardware using MeiGen.ai (?category=product) flagship product photography and glassmorphism spec callouts (/product).
version: 2.0.1
author: kramzzzy, Central AI
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [product, hardware, meigen, solar-panels, home-battery, glassmorphism, commercial-photography]
    related_skills: [ads, promotion, marketing]
---

# Flagship Product Showcase (`/product`)

Transform technical solar & smart-home hardware into Apple/Xiaomi/Tesla-grade flagship product posters modeled on **`https://www.meigen.ai/?category=product`**.

## Australian Homes and Roofs — Required

- Every house or residential roof in generated artwork must look authentically Australian: the hero property, neighbouring houses, distant rooftops, reflections, and homes visible through windows. Apply this to every post in a series and every image variation, not just the main subject.
- Specify the Australian location from the brief. If none is supplied, use a believable Australian suburban setting with ordinary local homes: brick veneer, rendered brick, or weatherboard; practical eaves, gutters and downpipes; and locally appropriate gardens. Do not invent a specific customer property or completed installation.
- Describe the roof explicitly: realistic Australian corrugated/profiled metal roofing or concrete/terracotta tiles, with plausible hip or gable geometry and pitch. Keep solar panels aligned to the roof plane with credible mounting and clearance; no floating panels or impossible roof layouts.
- Avoid default American suburban imagery (asphalt-shingle subdivisions, Cape Cod/colonial houses, US-style mailboxes), European alpine cottages, snow-covered roofs, or tropical resort villas standing in for an Australian home. Match any supplied Australian property reference; do not force every Australian region into one architectural style.
- Include this requirement in the actual Visual Brief and image tool's prompt/visual_prompt, not only in the caption: "Authentic Australian residential architecture throughout; all foreground and background homes and roofs are Australian, with locally appropriate metal or tiled roofing, eaves, gutters, gardens and realistic rooftop solar installation."
- Inspect the generated image before submitting it for review. Check every visible house and roof, including the background. If any look inconsistent with the Australian brief, revise the prompt and regenerate before presenting it as finished. For a studio-only product image, do not add a house just to satisfy this rule.

## When to Use

Use when showcasing a specific product, solar panel tier, home battery unit, smart inverter, smart thermostat, energy monitoring app, or package specification, or when the user invokes `/product`.

## The 4 MeiGen.ai Product Poster Archetypes for Solar

Choose or rotate across these 4 commercial product archetypes:

1. **Archetype 1 — Forced-Perspective Hero Subject + Device (Xiaomi 17 Pro Flagship Style)**:
   - Low-angle cinematic portrait of a confident subject holding a sleek smart solar controller, smartphone energy dashboard, or irradiance analyzer toward the camera in strong forced perspective, dominating the right foreground.
   - Top-right brand header (`Your Choice Solar` + `Power. Savings. Mastered.`), 3 thin white/sage pointer lines with glowing nodes, and a bottom-left frosted glassmorphism card with 4 key specs.

2. **Archetype 2 — Architectural Podium Hardware Monolith**:
   - A matte-black 13.5 kWh Home Solar Battery or N-Type 440W All-Black Monocrystalline Solar Module centered on a minimalist travertine/concrete architectural podium.
   - Soft-focus gradient backdrop in muted sage-emerald and warm stone, studio softbox rim lighting, floating HUD pointer lines highlighting hardware engineering, and a frosted glassmorphism spec card.

3. **Archetype 3 — Smart Home Ecosystem In-Action**:
   - Foreground focus on a sleek obsidian-black Smart Solar Thermostat (`22°C ECO · $0/HR GRID DRAW`) or wall tablet inside a sunlit Australian living room, with gleaming rooftop solar panels visible through floor-to-ceiling glass behind it.

4. **Archetype 4 — Precision Engineering & Weather Durability**:
   - Dramatic low-angle macro hero shot of an N-Type TOPCon monocrystalline solar array with micro-textured anti-reflective glass, water droplets, golden-hour Australian sun flare, and glowing HUD callout nodes pointing to cell efficiency and hail/cyclone ratings.

## Required Output Structure for Every Product Post

```markdown
### Post 1: [Product Name & Outcome Title]
- **Attention Hook:** [Bold 5–8 word headline focused on what the hardware does for the homeowner's bill]
- **Tagline:** [3–4 word product slogan, e.g. "Daylight Stored. Night Owned."]
- **Concept:** [Hardware capability translated into homeowner savings]
- **Caption:** "[Direct, no-fluff explanation of how the hardware works in real Australian homes and how much it saves per quarter.]"
- **Callouts:** [Hardware Callout 1] | [Hardware Callout 2] | [Hardware Callout 3]
- **Glass Panel:** [Key Spec 1] | [Key Spec 2] | [Key Spec 3] | [Key Spec 4]
- **Visual Brief:** [Detailed MeiGen.ai ?category=product prompt specifying low-angle forced perspective or architectural podium, hardware finish, screen UI, studio rim lighting, 3 floating UI callout labels, and bottom-left frosted glassmorphism spec card, 8K HDR]
- **CTA:** [Direct action to get a pre-assessment calculation, phone call, or site visit]
- **Hashtags:** #YourChoiceSolar #SolarHardware #HomeBattery #SmartEnergy
```
