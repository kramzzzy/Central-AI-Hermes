---
name: ads
description: Generate high-converting MeiGen.ai-style flagship commercial advertisement posters with forced-perspective hero product shots, futuristic UI callout nodes, frosted glassmorphism spec panels, and scroll-stopping direct copy (/ads).
version: 2.0.0
author: kramzzzy, Central AI
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [ads, meigen, commercial-poster, product-ads, social-media, solar-ads, glassmorphism]
    related_skills: [product, promotion, marketing]
---

# Flagship Commercial Ads (`/ads`)

Create ultra-realistic flagship commercial advertisement posters and social ad creatives modeled on **MeiGen.ai (`?category=product`)** high-conversion product ad aesthetics, tailored for **Your Choice Solar** and clean-energy solutions.

## When to Use

Use when the user asks for an ad, social media poster, commercial creative, product advertisement, paid social ad, or invokes `/ads`.

## The MeiGen.ai 9-Layer Commercial Ad Prompt Architecture

Every `/ads` creative MUST include a `**Visual Brief:**` built using this exact 9-layer commercial poster structure so OpenRouter (`gemini-2.5-flash-image-preview` / `flux.2-pro` / `seedream-4.5`) and the App OS MeiGen HUD Compositor render a flagship 4:5 commercial poster:

1. **Ad Format & Subject Pose**:
   - *"Ultra-realistic premium clean-energy commercial advertisement for Your Choice Solar, featuring a confident Australian homeowner/specialist in a dynamic low-angle cinematic pose, slightly twisting their torso while holding/presenting [Hero Solar Product/Device] toward the camera in a bold hero shot with strong forced perspective, the hardware dominating the right foreground."*
2. **Hero Product & UI Details**:
   - Describe the physical finish (matte metallic titanium/sage, obsidian aluminum bezel, N-Type monocrystalline black glass) and the live telemetry UI on the screen (e.g., `"22°C Solar Eco — $0/hr Grid Draw"`, `"6.6kW Array · 98% Self-Consumption"`, `"13.5 kWh Stored Solar"`).
3. **Outfit & Styling**:
   - Modern tech-fashion or architectural lifestyle aesthetic matching the colour palette (sage-olive, charcoal, warm stone, eucalyptus green).
4. **Background & Giant Depth Typography**:
   - Clean minimal architectural gradient or sunlit Australian residence with subtle blurred large-scale background typography (`"SOLAR PRO"`, `"ZERO BILL"`, `"13.5 kWh"`, `"ECO 22°C"`) for 3D depth.
5. **Studio & Golden-Hour Rim Lighting**:
   - Soft studio lighting + natural Australian golden-hour rim light, smooth skin illumination, controlled metallic edge highlights, glossy glass reflections, minimal shadows.
6. **Composition**:
   - Vertical 4:5 flagship poster framing, low-angle shot for power, subject slightly left, hero product dominating right foreground, clean negative space at top-right and bottom-left for HUD overlays.
7. **Futuristic UI Callout Nodes (`Callouts`)**:
   - 3 thin minimal white/sage leader lines and glowing nodes pointing to hardware features with floating labels (3–4 words each).
8. **Frosted Glassmorphism Spec Panel (`Glass Panel`)**:
   - Bottom-left frosted translucent glass card listing 4 flagship product benefits (3–5 words each).
9. **Quality & Style Tags**:
   - *"Style: high-end flagship solar & smart-energy advertisement, futuristic, minimal, elegant. Quality: 8K, ultra-detailed, sharp focus, HDR, cinematic commercial photography, realistic textures."*

## Required Output Structure for Every Ad

Always output every ad using these exact markdown fields so App OS automatically renders the MeiGen.ai HUD callouts, frosted glassmorphism spec panel, and 4:5 poster:

```markdown
### Post 1: [Direct Outcome-Focused Ad Title]
- **Attention Hook:** [Bold 5–8 word scroll-stopping headline]
- **Tagline:** [3-word flagship sub-headline, e.g. "Power. Savings. Mastered."]
- **Concept:** [Specific buyer trigger: Bill Shock, Daytime Free AC, Night Battery Independence, or Smart Tariff Control]
- **Caption:** "[Straightforward 2–4 sentence Australian ad copy. Never start with 'Did you know'. Lead with concrete dollar/kWh math and a blunt takeaway.]"
- **Callouts:** [Feature Node 1] | [Feature Node 2] | [Feature Node 3]
- **Glass Panel:** [Flagship Spec 1] | [Flagship Spec 2] | [Flagship Spec 3] | [Flagship Spec 4]
- **Visual Brief:** [Full 9-layer MeiGen.ai commercial advertisement prompt with low-angle forced perspective hero product, studio rim lighting, futuristic UI overlays, and bottom-left glassmorphism panel]
- **CTA:** [Single frictionless action, e.g. "Book a 2-minute solar pre-assessment call or site visit"]
- **Hashtags:** #YourChoiceSolar #SolarAustralia #CleanEnergyTech #ZeroBill
```
