"""Optional portrait provider owned by the private Central AI service.

Supports OpenRouter image generation (with models like seedream-4.5 / flux-1-schnell / recraft-v3),
OpenAI DALL-E (dall-e-3 / dall-e-2), and high-aesthetic procedural 3D SVG avatars as fallback.
"""
import base64
import hashlib
import json
import os
import urllib.error
import urllib.request


class ArtworkError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def available():
    """Returns True: character generation is always enabled (via OpenRouter/OpenAI API or built-in generator)."""
    return True



def _generate_procedural_avatar(role_text):
    """Generates an aesthetic SVG robot portrait as a guaranteed fallback."""
    seed = int(hashlib.md5(role_text.lower().encode()).hexdigest()[:8], 16)
    palettes = [
        {"accent": "#c6dab9", "accent_glow": "#9ec48b", "panel": "#1b241e", "name": "Sage"},
        {"accent": "#78c9e8", "accent_glow": "#45b3dd", "panel": "#14222b", "name": "Cyan"},
        {"accent": "#bd9be9", "accent_glow": "#9e6fe0", "panel": "#22192e", "name": "Purple"},
        {"accent": "#e6ad65", "accent_glow": "#db9437", "panel": "#2b2114", "name": "Gold"},
        {"accent": "#74cfb9", "accent_glow": "#43ba9e", "panel": "#152622", "name": "Teal"},
    ]
    p = palettes[seed % len(palettes)]
    acc = p["accent"]
    glow = p["accent_glow"]
    pan = p["panel"]

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <defs>
    <radialGradient id="bg-grad" cx="50%" cy="40%" r="60%">
      <stop offset="0%" stop-color="#181c1f"/>
      <stop offset="100%" stop-color="#0a0c0e"/>
    </radialGradient>
    <linearGradient id="ceramic" x1="20%" y1="0%" x2="80%" y2="100%">
      <stop offset="0%" stop-color="#f2f5f7"/>
      <stop offset="60%" stop-color="#d3d9de"/>
      <stop offset="100%" stop-color="#9da6ad"/>
    </linearGradient>
    <linearGradient id="faceplate" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#1a1e22"/>
      <stop offset="100%" stop-color="#090b0d"/>
    </linearGradient>
    <filter id="eye-glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="6" result="blur"/>
      <feMerge>
        <feMergeNode in="blur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
  </defs>
  <rect width="512" height="512" rx="28" fill="url(#bg-grad)"/>
  <!-- Torso & Shoulders -->
  <path d="M 176 390 C 176 350, 336 350, 336 390 L 376 512 L 136 512 Z" fill="{pan}" stroke="#2a3036" stroke-width="3"/>
  <path d="M 216 380 L 296 380 L 286 450 L 226 450 Z" fill="url(#ceramic)"/>
  <!-- Antenna -->
  <line x1="256" y1="90" x2="256" y2="40" stroke="#7a858f" stroke-width="8" stroke-linecap="round"/>
  <circle cx="256" cy="34" r="16" fill="{acc}" filter="url(#eye-glow)"/>
  <!-- Ears / Headphone Rings -->
  <rect x="96" y="170" width="34" height="90" rx="17" fill="{pan}" stroke="#2a3036" stroke-width="3"/>
  <circle cx="113" cy="215" r="14" fill="none" stroke="{acc}" stroke-width="4" filter="url(#eye-glow)"/>
  <rect x="382" y="170" width="34" height="90" rx="17" fill="{pan}" stroke="#2a3036" stroke-width="3"/>
  <circle cx="399" cy="215" r="14" fill="none" stroke="{acc}" stroke-width="4" filter="url(#eye-glow)"/>
  <!-- Ceramic Helmet -->
  <ellipse cx="256" cy="215" rx="150" ry="140" fill="url(#ceramic)"/>
  <!-- Visor / Face Screen -->
  <rect x="156" y="125" width="200" height="155" rx="55" fill="url(#faceplate)" stroke="#22282e" stroke-width="4"/>
  <!-- Glowing Luminous Eyes -->
  <circle cx="205" cy="190" r="26" fill="none" stroke="{acc}" stroke-width="8" filter="url(#eye-glow)"/>
  <circle cx="205" cy="190" r="10" fill="{acc}"/>
  <circle cx="307" cy="190" r="26" fill="none" stroke="{acc}" stroke-width="8" filter="url(#eye-glow)"/>
  <circle cx="307" cy="190" r="10" fill="{acc}"/>
  <!-- Cheerful Smile -->
  <path d="M 230 242 Q 256 262 282 242" fill="none" stroke="{glow}" stroke-width="6" stroke-linecap="round" filter="url(#eye-glow)"/>
</svg>"""
    return base64.b64encode(svg.encode("utf-8")).decode("ascii")


def _download_image_to_b64(url):
    """Fetches an image from an HTTP/HTTPS URL and converts it to base64."""
    req = urllib.request.Request(url, headers={'User-Agent': 'Central-AI-Portrait/1.0'})
    with urllib.request.build_opener(_NoRedirect()).open(req, timeout=30) as resp:
        data = resp.read(16_000_001)
        if len(data) > 16_000_000:
            raise ValueError("Image exceeded 16MB")
        return base64.b64encode(data).decode('ascii')


def generate(body):
    if not isinstance(body, dict) or not body.get('type'):
        raise ArtworkError('Supply a valid character role or description.', 400)
    role = str(body['type']).strip()
    if not 2 <= len(role) <= 200:
        raise ArtworkError('Supply a role between 2 and 200 characters.', 400)

    prompt = (
        'Create one original adorable 3D robot portrait for an AI agent avatar card. '
        'Rounded pearl ceramic head and compact body, huge kind luminous circular eyes '
        'on a dark face screen, a small cheerful smile, charming toy-like proportions. '
        'Front three-quarter bust, centered, full head and antenna visible with comfortable margins. '
        f'Role and character theme: {role}. '
        'Soft studio lighting, dark #09090b background, edges and lower torso fading gently '
        'into darkness. No text, logos, borders, panels or humans. Keep the character cute and approachable.'
    )

    openrouter_key = os.environ.get('OPENROUTER_API_KEY', '').strip()
    openai_key = os.environ.get('OPENAI_API_KEY', '').strip()

    # 1. Try OpenRouter image generation if configured
    if openrouter_key:
        models_to_try = [
            os.environ.get('IMAGE_GENERATION_MODEL', 'bytedance-seed/seedream-4.5'),
            'black-forest-labs/flux-1-schnell',
            'recraft/recraft-v3',
        ]
        for model in models_to_try:
            try:
                payload = json.dumps({
                    'model': model,
                    'prompt': prompt,
                    'n': 1,
                    'output_format': 'png',
                    'resolution': '1K'
                }).encode('utf-8')
                req = urllib.request.Request(
                    'https://openrouter.ai/api/v1/images',
                    data=payload,
                    headers={
                        'Authorization': f'Bearer {openrouter_key}',
                        'Content-Type': 'application/json',
                        'HTTP-Referer': 'https://central-ai.internal',
                        'X-Title': 'Central AI Agent Portrait Generator'
                    },
                    method='POST'
                )
                with urllib.request.build_opener(_NoRedirect()).open(req, timeout=45) as resp:
                    resp_data = json.loads(resp.read(16_000_001))
                    img_data = resp_data.get('data', [])[0]
                    if 'b64_json' in img_data and img_data['b64_json']:
                        return {'data': [{'b64_json': img_data['b64_json']}]}
                    if 'url' in img_data and img_data['url']:
                        b64 = _download_image_to_b64(img_data['url'])
                        return {'data': [{'b64_json': b64}]}
            except Exception as e:
                print(f"[hermes_artwork] OpenRouter generation with {model} failed: {e}")
                continue

    # 2. Try OpenAI Images API if configured
    if openai_key:
        for model in ['dall-e-3', 'dall-e-2']:
            try:
                payload = json.dumps({
                    'model': model,
                    'prompt': prompt,
                    'n': 1,
                    'size': '1024x1024',
                    'response_format': 'b64_json'
                }).encode('utf-8')
                req = urllib.request.Request(
                    'https://api.openai.com/v1/images/generations',
                    data=payload,
                    headers={
                        'Authorization': f'Bearer {openai_key}',
                        'Content-Type': 'application/json'
                    },
                    method='POST'
                )
                with urllib.request.build_opener(_NoRedirect()).open(req, timeout=60) as resp:
                    resp_data = json.loads(resp.read(16_000_001))
                    img_data = resp_data.get('data', [])[0]
                    if 'b64_json' in img_data and img_data['b64_json']:
                        return {'data': [{'b64_json': img_data['b64_json']}]}
                    if 'url' in img_data and img_data['url']:
                        b64 = _download_image_to_b64(img_data['url'])
                        return {'data': [{'b64_json': b64}]}
            except Exception as e:
                print(f"[hermes_artwork] OpenAI generation with {model} failed: {e}")
                continue

    # 3. Graceful fallback: generate procedural SVG avatar
    try:
        b64_svg = _generate_procedural_avatar(role)
        return {'data': [{'b64_json': b64_svg}]}
    except Exception as e:
        raise ArtworkError(f'Character generation failed: {e}', 502) from None
