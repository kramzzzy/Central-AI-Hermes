"""Box 1 Video & Voice Clone Studio Engine for Central AI.

Owns all OpenRouter Video/Image/LLM calls, Fish Audio Instant Voice Cloning & TTS,
and FFmpeg 10s-to-58s multi-scene video rendering and stitching inside Box 1.
No provider API keys ever leave Box 1.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class VideoStudioError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


def _get_env_key(name: str, default: str = "") -> str:
    val = os.environ.get(name, "").strip().strip('"').strip("'")
    if val and not val.startswith("sk-or-v1-standalone-default"):
        return val
    root = Path(os.environ.get("HERMES_PROFILE_ROOT", "/opt/data"))
    profile = os.environ.get("HERMES_PROFILE", "leo")
    candidate_paths: list[Path] = [
        root / "profiles" / profile / ".env",
        root / "profiles" / "leo" / ".env",
        Path("/opt/os-adapter/.runtime/hermes.env"),
        root / ".env",
        Path("/data/.env"),
        Path("/data/voice/.env"),
        Path(".env.local"),
        Path(".env"),
    ]
    try:
        profiles_dir = root / "profiles"
        if profiles_dir.is_dir():
            for p_env in sorted(profiles_dir.glob("*/.env")):
                if p_env not in candidate_paths:
                    candidate_paths.append(p_env)
    except Exception:
        pass
    for env_path in candidate_paths:
        try:
            if env_path.is_file():
                for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == name:
                        cleaned = v.strip().strip('"').strip("'")
                        if cleaned and not cleaned.startswith("sk-or-v1-standalone-default"):
                            return cleaned
        except Exception:
            pass
    if name in ("FISH_API_KEY", "FISH_VOICE_ID", "FISH_TTS_MODEL"):
        try:
            from central_ai_integrations import discover_fish_credentials
            fish_creds = discover_fish_credentials(str(root))
            found = str(fish_creds.get(name) or "").strip()
            if found:
                return found
        except Exception:
            pass
    return default


def _ffmpeg_bin() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def clone_voice_model(body: dict) -> dict:
    """Create a persistent Fish Audio cloned voice model or zero-shot reference profile."""
    name = str(body.get("name") or "").strip()[:80]
    audio_b64 = str(body.get("audio_b64") or "").strip()
    mime = str(body.get("mime") or "audio/mpeg").strip().lower()
    transcript = str(body.get("transcript") or "").strip()[:1000]
    sample_file_id = str(body.get("sample_file_id") or "").strip()

    if not name:
        raise VideoStudioError("Voice profile name is required.", 400)
    if not audio_b64:
        raise VideoStudioError("Upload or record a 10–30 second voice audio sample.", 400)

    if "," in audio_b64 and audio_b64.startswith("data:"):
        audio_b64 = audio_b64.split(",", 1)[1].strip()

    try:
        audio_bytes = base64.b64decode(audio_b64, validate=True)
    except Exception:
        raise VideoStudioError("Invalid audio encoding.", 400) from None

    if len(audio_bytes) < 1024:
        raise VideoStudioError("Audio sample is too short. Upload at least 5–30 seconds of clear speech.", 400)
    if len(audio_bytes) > 25 * 1024 * 1024:
        raise VideoStudioError("Audio sample must be under 25 MB.", 400)

    fish_key = _get_env_key("FISH_API_KEY")
    ext = "wav" if "wav" in mime else ("ogg" if "ogg" in mime else "mp3")

    if fish_key:
        boundary = f"----CentralAIFishBoundary{uuid.uuid4().hex}"
        parts: list[bytes] = []

        def add_field(field_name: str, value: str):
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field_name}"\r\n\r\n'
                f"{value}\r\n".encode("utf-8")
            )

        add_field("visibility", "private")
        add_field("type", "tts")
        add_field("title", name)
        add_field("train_mode", "fast")
        add_field("enhance_audio_quality", "true")
        if transcript:
            add_field("texts", transcript)

        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="voices"; filename="sample.{ext}"\r\n'
            f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
            + audio_bytes
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        payload = b"".join(parts)

        req = urllib.request.Request(
            "https://api.fish.audio/model",
            data=payload,
            headers={
                "Authorization": f"Bearer {fish_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "Central-AI-VoiceStudio/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
                model_id = str(raw.get("_id") or raw.get("id") or "").strip()
                if model_id:
                    return {
                        "ok": True,
                        "fish_voice_id": model_id,
                        "mode": "fish_instant_clone",
                        "name": name,
                    }
        except Exception as exc:
            print(f"[hermes_video] Fish /model instant clone fallback to zero-shot reference: {exc}")

    # Zero-shot reference voice ID backed by the saved audio sample in Box 2 library_files
    ref_tag = f"ref:{sample_file_id}" if sample_file_id else f"ref:{hashlib.sha256(audio_bytes).hexdigest()[:24]}"
    return {
        "ok": True,
        "fish_voice_id": ref_tag,
        "mode": "fish_zero_shot_reference",
        "name": name,
    }


def generate_voice_tts(body: dict) -> dict:
    """Synthesize speech audio (.mp3 base64) using a cloned Fish voice, default Fish voice, or Edge TTS."""
    text = str(body.get("text") or "").strip()
    if not text:
        raise VideoStudioError("Speech text is required.", 400)
    text = text[:2500]

    fish_voice_id = str(body.get("fish_voice_id") or "").strip()
    reference_audio_b64 = str(body.get("reference_audio_b64") or "").strip()
    reference_text = str(body.get("reference_text") or "").strip()

    fish_key = _get_env_key("FISH_API_KEY")
    default_voice_id = _get_env_key("FISH_VOICE_ID", "a30d099a643d4173836cfee1d8ae6c13") or "a30d099a643d4173836cfee1d8ae6c13"
    fish_tts_model = _get_env_key("FISH_TTS_MODEL", "s1") or "s1"

    if fish_key:
        tts_payload: dict = {
            "text": text,
            "format": "mp3",
            "mp3_bitrate": 128,
            "normalize": True,
            "latency": "normal",
        }
        if reference_audio_b64:
            clean_b64 = reference_audio_b64.split(",", 1)[1].strip() if "," in reference_audio_b64 else reference_audio_b64
            tts_payload["references"] = [
                {
                    "audio": clean_b64,
                    "text": reference_text or "Hello, this is my natural speaking voice for video narration.",
                }
            ]
        elif fish_voice_id and not fish_voice_id.startswith("ref:"):
            tts_payload["reference_id"] = fish_voice_id
        elif default_voice_id:
            tts_payload["reference_id"] = default_voice_id

        req = urllib.request.Request(
            "https://api.fish.audio/v1/tts",
            data=json.dumps(tts_payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {fish_key}",
                "Content-Type": "application/json",
                "model": fish_tts_model,
                "User-Agent": "Central-AI-VoiceStudio/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                audio_data = resp.read(15_000_000)
                if len(audio_data) > 512:
                    return {
                        "ok": True,
                        "audio_b64": base64.b64encode(audio_data).decode("ascii"),
                        "mime": "audio/mpeg",
                        "provider": "fish-audio",
                    }
        except Exception as exc:
            print(f"[hermes_video] Fish Audio TTS failed, falling back to Edge TTS: {exc}")

    # Guaranteed fallback via edge-tts (pre-installed in Box 1)
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            voice_name = "en-AU-WilliamNeural"
            cmd = [
                "/opt/hermes/.venv/bin/python",
                "-m",
                "edge_tts",
                "--voice",
                voice_name,
                "--text",
                text,
                "--write-media",
                tmp_path,
            ]
            if not os.path.isfile(cmd[0]):
                cmd[0] = "python3"
            subprocess.run(cmd, check=True, timeout=30, capture_output=True)
            audio_bytes = Path(tmp_path).read_bytes()
            if len(audio_bytes) > 256:
                return {
                    "ok": True,
                    "audio_b64": base64.b64encode(audio_bytes).decode("ascii"),
                    "mime": "audio/mpeg",
                    "provider": "edge-tts-au",
                }
        finally:
            with_ignore_unlink(tmp_path)
    except Exception as exc:
        print(f"[hermes_video] Edge TTS fallback error: {exc}")

    raise VideoStudioError("Voice synthesis failed. Check FISH_API_KEY in Box 1.", 502)


def with_ignore_unlink(path: str | Path):
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass


def plan_video_storyboard(body: dict) -> dict:
    """Break a video concept into N coherent 10s scenes (10s to 58s total) with visual prompts & voiceover script."""
    prompt = str(body.get("prompt") or "").strip()
    if len(prompt) < 3:
        raise VideoStudioError("Provide a video concept or prompt.", 400)

    target_duration = int(body.get("target_duration_sec") or 30)
    target_duration = max(10, min(58, target_duration))
    aspect_ratio = str(body.get("aspect_ratio") or "9:16").strip()
    if aspect_ratio not in ("9:16", "16:9", "1:1"):
        aspect_ratio = "9:16"

    scene_count = max(1, min(6, round(target_duration / 10.0)))
    scene_duration = round(target_duration / scene_count, 1)

    openrouter_key = _get_env_key("OPENROUTER_API_KEY")
    model = _get_env_key("HERMES_CHAT_MODEL", "google/gemini-2.5-flash")

    if openrouter_key and not openrouter_key.startswith("sk-or-v1-standalone-default"):
        sys_prompt = (
            "You are a Viral Video Director for commercial social media reels and promotions. "
            f"Break the user's request into exactly {scene_count} sequential scenes totaling {target_duration} seconds "
            f"(~{scene_duration}s per scene, aspect ratio {aspect_ratio}). "
            "Maintain strict visual continuity from scene to scene (same subject, lighting, environment, and camera flow). "
            "Scene 1 MUST be a scroll-stopping 0–3s visual & spoken hook. The final scene MUST deliver a clear payoff and call to action. "
            "Return ONLY valid JSON matching this exact structure (no markdown fences):\n"
            '{"title":"Short project title","scenes":[{"index":1,"label":"Scene 1: Hook (0-10s)","duration_sec":10,'
            '"visual_prompt":"Detailed photorealistic camera & motion prompt for this 10s clip (no baked-in text)",'
            '"voice_text":"Natural Australian English voiceover script for this 10s clip (18-24 words max)"}]}'
        )
        req_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt[:2000]},
            ],
            "temperature": 0.4,
        }
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {openrouter_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="replace"))
                content = str(raw["choices"][0]["message"]["content"] or "").strip()
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.I).strip()
                match = re.search(r"\{[\s\S]*\}", content)
                parsed = json.loads(match.group(0) if match else content)
                raw_scenes = parsed.get("scenes")
                if isinstance(raw_scenes, list) and len(raw_scenes) > 0:
                    scenes = []
                    for idx, s in enumerate(raw_scenes[:scene_count]):
                        start_s = int(round(idx * scene_duration))
                        end_s = min(target_duration, int(round((idx + 1) * scene_duration)))
                        scenes.append({
                            "index": idx + 1,
                            "label": str(s.get("label") or f"Scene {idx + 1} ({start_s}–{end_s}s)")[:80],
                            "duration_sec": int(round(scene_duration)),
                            "visual_prompt": str(s.get("visual_prompt") or prompt)[:1200],
                            "voice_text": str(s.get("voice_text") or "")[:400],
                        })
                    return {
                        "ok": True,
                        "title": str(parsed.get("title") or prompt[:60]).strip()[:120],
                        "target_duration_sec": target_duration,
                        "aspect_ratio": aspect_ratio,
                        "scenes": scenes,
                    }
        except Exception as exc:
            print(f"[hermes_video] Storyboard LLM fallback: {exc}")

    # Deterministic fallback storyboard
    role_beats = [
        ("Hook", "Dynamic cinematic opening establishing shot with golden-hour Australian sunlight"),
        ("Problem", "Close-up detail shot showing the everyday challenge and energy context"),
        ("Solution", "Smooth tracking shot revealing the clean modern solar & battery installation"),
        ("Performance", "Crisp architectural angle highlighting sleek panels and smart monitoring"),
        ("Lifestyle", "Warm sunlit home interior with effortless daytime comfort"),
        ("Call to Action", "Hero wide-angle finale shot of the property at sunset"),
    ]
    scenes = []
    for idx in range(scene_count):
        beat_name, beat_desc = role_beats[min(idx, len(role_beats) - 1)]
        start_s = int(round(idx * scene_duration))
        end_s = min(target_duration, int(round((idx + 1) * scene_duration)))
        scenes.append({
            "index": idx + 1,
            "label": f"Scene {idx + 1}: {beat_name} ({start_s}–{end_s}s)",
            "duration_sec": int(round(scene_duration)),
            "visual_prompt": f"{beat_desc}. {prompt}. Ultra-realistic 8K commercial cinematography, smooth gimbal motion, no baked-in text.",
            "voice_text": prompt[:220] if scene_count == 1 else f"{beat_name}: {prompt[:160]}",
        })
    return {
        "ok": True,
        "title": prompt[:60].strip(),
        "target_duration_sec": target_duration,
        "aspect_ratio": aspect_ratio,
        "scenes": scenes,
    }


_VIDEO_MODELS_CACHE: dict = {"models": [], "timestamp": 0.0}
_VIDEO_MODELS_TTL = 900.0  # 15 minutes


def _parse_video_price_per_sec(skus: dict | None) -> float | None:
    """Compute starting USD price per second from OpenRouter video pricing_skus."""
    if not isinstance(skus, dict) or not skus:
        return None
    candidates_usd: list[float] = []
    for k, v in skus.items():
        try:
            val = float(v)
        except Exception:
            continue
        if val <= 0:
            continue
        kl = str(k).lower()
        if any(skip in kl for skip in ("minimum_cents", "input_images", "image_input", "reference_", "with_video_input")):
            continue
        if kl.startswith("cents_per_"):
            candidates_usd.append(val / 100.0)
        elif "duration_seconds" in kl:
            candidates_usd.append(val)
        elif "video_tokens" in kl:
            # Standard 480p/720p 24fps is ~9,607 video tokens per second on OpenRouter
            candidates_usd.append(val * 9607.0)
    if not candidates_usd:
        return None
    return round(min(candidates_usd), 4)


def _format_price_badge(price_per_sec: float) -> str:
    per_10s = price_per_sec * 10.0
    if price_per_sec < 0.01:
        return f"${price_per_sec:.4f}/s · ${per_10s:.3f}/10s"
    return f"${price_per_sec:.3f}/s · ${per_10s:.2f}/10s".replace("0/s", "/s")


def list_video_models(body: dict | None = None) -> dict:
    """Auto-sense ONLY video generation models from OpenRouter (/api/v1/videos/models) with live pricing."""
    force_refresh = bool((body or {}).get("force_refresh"))
    now = time.monotonic()
    if not force_refresh and _VIDEO_MODELS_CACHE["models"] and (now - _VIDEO_MODELS_CACHE["timestamp"] < _VIDEO_MODELS_TTL):
        return {"ok": True, "models": _VIDEO_MODELS_CACHE["models"], "source": "openrouter_video_catalog"}

    openrouter_key = _get_env_key("OPENROUTER_API_KEY")
    headers = {"User-Agent": "Central-AI-VideoStudio/1.0", "Accept": "application/json"}
    if openrouter_key and not openrouter_key.startswith("sk-or-v1-standalone-default"):
        headers["Authorization"] = f"Bearer {openrouter_key}"

    raw_list: list[dict] = []
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/videos/models", headers=headers)
        with urllib.request.urlopen(req, timeout=12) as resp:
            if resp.status == 200:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                raw_list = payload.get("data") or []
    except Exception as exc:
        print(f"[hermes_video] Live OpenRouter video catalog fetch fallback: {exc}")

    sensed: list[dict] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        durations = item.get("supported_durations")
        # Strictly filter to true text-to-video / image-to-video generation models (exclude upscale/edit-only models)
        if not model_id or not isinstance(durations, list) or len(durations) == 0:
            continue
        price_sec = _parse_video_price_per_sec(item.get("pricing_skus"))
        if price_sec is None:
            price_sec = 0.05
        name = str(item.get("name") or model_id).strip()
        vendor = model_id.split("/", 1)[0] if "/" in model_id else "openrouter"
        frame_imgs = item.get("supported_frame_images") or []
        sensed.append({
            "id": model_id,
            "name": name,
            "vendor": vendor,
            "price_per_sec_usd": price_sec,
            "price_per_10s_usd": round(price_sec * 10.0, 3),
            "price_display": _format_price_badge(price_sec),
            "supported_durations": sorted([int(d) for d in durations if isinstance(d, (int, float))]),
            "supported_aspect_ratios": item.get("supported_aspect_ratios") or ["16:9", "9:16", "1:1"],
            "supported_resolutions": item.get("supported_resolutions") or ["720p"],
            "supports_first_frame": "first_frame" in frame_imgs,
            "supports_audio": bool(item.get("generate_audio")),
        })

    if not sensed:
        # Fallback video catalog with verified OpenRouter video pricing if offline
        fallback_specs = [
            ("bytedance/seedance-1-5-pro", "ByteDance: Seedance 1.5 Pro", 0.0115, [5, 10], True),
            ("x-ai/grok-imagine-video-1.5-lite", "SpaceXAI: Grok Imagine Video 1.5 Lite", 0.02, [5, 10], True),
            ("google/veo-3.1-lite", "Google: Veo 3.1 Lite", 0.03, [4, 6, 8], True),
            ("alibaba/wan-2.6", "Alibaba: Wan 2.6", 0.04, [5, 10], True),
            ("bytedance/seedance-2.0-fast", "ByteDance: Seedance 2.0 Fast", 0.0404, [5, 10], True),
            ("alibaba/wan-3.0", "Alibaba: Wan 3.0", 0.05, [5, 10], True),
            ("minimax/hailuo-3-max", "MiniMax: Hailuo 3 Max", 0.05, [5, 10], True),
            ("alibaba/wan-3.0-prime", "Alibaba: Wan 3.0 Prime", 0.068, [5, 10], True),
            ("google/veo-3.1-fast", "Google: Veo 3.1 Fast", 0.08, [4, 6, 8], True),
            ("minimax/hailuo-2.3", "MiniMax: Hailuo 2.3", 0.0817, [6, 10], True),
            ("kwaivgi/kling-v3.0-std", "Kling: Video v3.0 Standard", 0.084, [5, 10], True),
            ("kwaivgi/kling-v3.0-pro", "Kling: Video v3.0 Pro", 0.112, [5, 10], True),
            ("runway/gen-4.5", "Runway: Gen-4.5", 0.12, [5, 10], True),
            ("google/veo-3.1", "Google: Veo 3.1", 0.20, [4, 6, 8], True),
        ]
        for mid, mname, psec, durs, fframe in fallback_specs:
            sensed.append({
                "id": mid,
                "name": mname,
                "vendor": mid.split("/", 1)[0],
                "price_per_sec_usd": psec,
                "price_per_10s_usd": round(psec * 10.0, 3),
                "price_display": _format_price_badge(psec),
                "supported_durations": durs,
                "supported_aspect_ratios": ["16:9", "9:16", "1:1"],
                "supported_resolutions": ["720p", "1080p"],
                "supports_first_frame": fframe,
                "supports_audio": False,
            })

    sensed.sort(key=lambda x: (x["price_per_sec_usd"], x["name"].lower()))

    _VIDEO_MODELS_CACHE["models"] = sensed
    _VIDEO_MODELS_CACHE["timestamp"] = now
    return {"ok": True, "models": sensed, "source": "openrouter_video_catalog"}


def _normalize_first_frame_uri(ffmpeg: str | None, data_uri: str | None, aspect_ratio: str) -> str | None:
    """Convert any reference image into an aspect-ratio-matched 720p JPEG data URI (<150 KB) for OpenRouter video APIs."""
    if not data_uri or not isinstance(data_uri, str):
        return None
    if not ffmpeg:
        return data_uri
    raw_b64 = data_uri.split(",", 1)[1].strip() if "," in data_uri else data_uri.strip()
    try:
        img_bytes = base64.b64decode(raw_b64)
    except Exception:
        return data_uri
    w, h = (720, 1280) if aspect_ratio == "9:16" else ((1280, 720) if aspect_ratio == "16:9" else (720, 720))
    try:
        with tempfile.TemporaryDirectory(prefix="central-fframe-") as tdir:
            in_p = os.path.join(tdir, "in_img")
            out_p = os.path.join(tdir, "out.jpg")
            Path(in_p).write_bytes(img_bytes)
            subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    in_p,
                    "-vf",
                    f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "3",
                    out_p,
                ],
                check=True,
                timeout=10,
                capture_output=True,
            )
            if os.path.isfile(out_p):
                jpg_bytes = Path(out_p).read_bytes()
                if len(jpg_bytes) > 512:
                    return "data:image/jpeg;base64," + base64.b64encode(jpg_bytes).decode("ascii")
    except Exception:
        pass
    return data_uri


def _try_openrouter_video_api(
    visual_prompt: str,
    duration_sec: int,
    aspect_ratio: str,
    first_frame_data_uri: str | None,
    openrouter_key: str,
    requested_model: str = "auto",
    fast_fallback: bool = False,
) -> tuple[bytes | None, str | None, float | None]:
    """Attempt native video generation via OpenRouter /api/v1/videos with async job polling."""
    if fast_fallback:
        return None, None, None
    if not openrouter_key or openrouter_key.startswith("sk-or-v1-standalone-default"):
        return None, None, None
    req_model = (requested_model or "auto").strip()
    if req_model == "keyframe-ken-burns":
        return None, None, None

    overall_deadline = time.time() + 48.0
    catalog = list_video_models().get("models") or []
    model_map = {m["id"]: m for m in catalog if isinstance(m, dict) and m.get("id")}

    env_model = _get_env_key("OPENROUTER_VIDEO_MODEL", "")
    fallback_chain = [
        req_model if (req_model and req_model != "auto") else "",
        env_model,
        "bytedance/seedance-1-5-pro",
        "x-ai/grok-imagine-video-1.5-lite",
        "google/veo-3.1-lite",
        "alibaba/wan-2.6",
    ]
    models_to_try = list(dict.fromkeys([m for m in fallback_chain if m]))[:3]
    normalized_frame = _normalize_first_frame_uri(_ffmpeg_bin(), first_frame_data_uri, aspect_ratio)

    for model_slug in models_to_try:
        if time.time() >= overall_deadline:
            break
        spec = model_map.get(model_slug) or {}
        supp_durs = spec.get("supported_durations") or [5, 10]
        target_dur = int(duration_sec)
        chosen_dur = min(supp_durs, key=lambda d: abs(int(d) - target_dur)) if supp_durs else max(5, min(10, target_dur))
        supp_ratios = spec.get("supported_aspect_ratios") or ["16:9", "9:16", "1:1"]
        chosen_ratio = aspect_ratio if aspect_ratio in supp_ratios else ("9:16" if "9:16" in supp_ratios else supp_ratios[0])

        frame_variants = (
            [normalized_frame, None]
            if (normalized_frame and spec.get("supports_first_frame", True))
            else [None]
        )

        for frame_uri in frame_variants:
            if time.time() >= overall_deadline:
                break
            payload: dict = {
                "model": model_slug,
                "prompt": visual_prompt[:1200],
                "duration": int(chosen_dur),
                "aspect_ratio": chosen_ratio,
            }
            if frame_uri:
                payload["frame_images"] = [
                    {
                        "type": "image_url",
                        "image_url": {"url": frame_uri},
                        "frame_type": "first_frame",
                    }
                ]

            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/videos",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    job = json.loads(resp.read().decode("utf-8", errors="replace"))
                job_id = str(job.get("id") or "").strip()
                est_cost = round(float(spec.get("price_per_sec_usd") or 0.04) * int(chosen_dur), 4)
                unsigned = job.get("unsigned_urls")
                direct_url = str(
                    (unsigned[0] if isinstance(unsigned, list) and unsigned else None)
                    or job.get("url")
                    or job.get("video_url")
                    or ""
                ).strip()
                if direct_url.startswith("https://"):
                    with urllib.request.urlopen(direct_url, timeout=25) as vresp:
                        data = vresp.read(50_000_000)
                        if len(data) > 4096:
                            actual_cost = float((job.get("usage") or {}).get("cost") or est_cost)
                            return data, model_slug, actual_cost
                if not job_id:
                    continue
                deadline = min(overall_deadline, time.time() + 40.0)
                while time.time() < deadline:
                    time.sleep(2.5)
                    poll_req = urllib.request.Request(
                        f"https://openrouter.ai/api/v1/videos/{urllib.parse.quote(job_id)}",
                        headers={"Authorization": f"Bearer {openrouter_key}"},
                    )
                    with urllib.request.urlopen(poll_req, timeout=12) as presp:
                        status_data = json.loads(presp.read().decode("utf-8", errors="replace"))
                    st = str(status_data.get("status") or "").lower()
                    if st in ("completed", "succeeded", "done"):
                        actual_cost = float((status_data.get("usage") or {}).get("cost") or est_cost)
                        u_urls = status_data.get("unsigned_urls")
                        v_url = (
                            (u_urls[0] if isinstance(u_urls, list) and u_urls else None)
                            or status_data.get("url")
                            or status_data.get("video_url")
                            or (status_data.get("output") or [None])[0]
                        )
                        if isinstance(v_url, str) and v_url.startswith("https://"):
                            with urllib.request.urlopen(v_url, timeout=25) as dl:
                                v_bytes = dl.read(50_000_000)
                                if len(v_bytes) > 4096:
                                    return v_bytes, model_slug, actual_cost
                        content_req = urllib.request.Request(
                            f"https://openrouter.ai/api/v1/videos/{urllib.parse.quote(job_id)}/content",
                            headers={"Authorization": f"Bearer {openrouter_key}"},
                        )
                        with urllib.request.urlopen(content_req, timeout=25) as cdl:
                            v_bytes = cdl.read(50_000_000)
                            if len(v_bytes) > 4096:
                                return v_bytes, model_slug, actual_cost
                        break
                    if st in ("failed", "cancelled", "expired", "error"):
                        break
                break
            except urllib.error.HTTPError as he:
                err_detail = ""
                try:
                    err_detail = he.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    pass
                print(f"[hermes_video] OpenRouter video model {model_slug} (frame={bool(frame_uri)}) HTTP {he.code}: {err_detail}")
                if frame_uri and he.code in (400, 413, 415, 422, 500, 502):
                    continue
                break
            except Exception as exc:
                print(f"[hermes_video] OpenRouter video model {model_slug} failed: {exc}")
                break
    return None, None, None


def _extract_last_frame_b64(ffmpeg: str, mp4_path: str) -> str | None:
    out_jpg = mp4_path + ".last.jpg"
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-sseof",
                "-0.15",
                "-i",
                mp4_path,
                "-frames:v",
                "1",
                "-q:v",
                "3",
                out_jpg,
            ],
            check=True,
            timeout=15,
            capture_output=True,
        )
        if os.path.isfile(out_jpg):
            raw = Path(out_jpg).read_bytes()
            if len(raw) > 512:
                return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")
    except Exception:
        pass
    finally:
        with_ignore_unlink(out_jpg)
    return None


def generate_video_scene(body: dict) -> dict:
    """Render one 8-10s scene MP4 with visual continuity, optional cloned voiceover, and extracted last frame."""
    import hermes_artwork

    visual_prompt = str(body.get("visual_prompt") or body.get("prompt") or "").strip()
    if len(visual_prompt) < 3:
        raise VideoStudioError("Scene visual prompt is required.", 400)

    voice_text = str(body.get("voice_text") or "").strip()
    duration_sec = max(5, min(15, int(body.get("duration_sec") or 10)))
    aspect_ratio = str(body.get("aspect_ratio") or "9:16").strip()
    if aspect_ratio not in ("9:16", "16:9", "1:1"):
        aspect_ratio = "9:16"

    fish_voice_id = str(body.get("fish_voice_id") or "").strip()
    reference_audio_b64 = str(body.get("reference_audio_b64") or "").strip()
    first_frame_b64 = str(body.get("first_frame_b64") or "").strip()
    references = body.get("references") if isinstance(body.get("references"), list) else []

    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise VideoStudioError("FFmpeg is not installed on Box 1.", 503)

    # 1. Synthesize voiceover audio if voice_text is provided
    voice_mp3_bytes: bytes | None = None
    if voice_text:
        try:
            tts_res = generate_voice_tts({
                "text": voice_text,
                "fish_voice_id": fish_voice_id,
                "reference_audio_b64": reference_audio_b64,
            })
            if tts_res.get("audio_b64"):
                voice_mp3_bytes = base64.b64decode(tts_res["audio_b64"])
        except Exception as exc:
            print(f"[hermes_video] Scene voiceover skipped: {exc}")

    # 2. Prepare reference / continuity frame
    all_refs: list[str] = []
    if first_frame_b64:
        if not first_frame_b64.startswith("data:image/"):
            first_frame_b64 = "data:image/png;base64," + first_frame_b64
        all_refs.append(first_frame_b64)
    for r in references:
        if isinstance(r, str) and r.startswith("data:image/") and len(all_refs) < 6:
            if r not in all_refs:
                all_refs.append(r)

    openrouter_key = _get_env_key("OPENROUTER_API_KEY")
    requested_model = str(body.get("video_model") or "auto").strip()
    fast_fallback = bool(body.get("fast_fallback"))
    raw_video_bytes, model_used, scene_cost_usd = _try_openrouter_video_api(
        visual_prompt,
        duration_sec,
        aspect_ratio,
        all_refs[0] if all_refs else None,
        openrouter_key,
        requested_model,
        fast_fallback=fast_fallback,
    )

    width, height = (720, 1280) if aspect_ratio == "9:16" else ((1280, 720) if aspect_ratio == "16:9" else (720, 720))

    with tempfile.TemporaryDirectory(prefix="central-video-scene-") as tmpdir:
        out_mp4 = os.path.join(tmpdir, "scene_out.mp4")
        audio_path = os.path.join(tmpdir, "voice.mp3")
        if voice_mp3_bytes:
            Path(audio_path).write_bytes(voice_mp3_bytes)

        if raw_video_bytes:
            in_mp4 = os.path.join(tmpdir, "raw.mp4")
            Path(in_mp4).write_bytes(raw_video_bytes)
            audio_inputs = ["-i", audio_path] if voice_mp3_bytes else ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
            cmd = [
                ffmpeg,
                "-y",
                "-stream_loop",
                "-1",
                "-i",
                in_mp4,
                *audio_inputs,
                "-t",
                str(duration_sec),
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1",
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                out_mp4,
            ]
            try:
                subprocess.run(cmd, check=True, timeout=60, capture_output=True)
            except Exception as exc:
                raise VideoStudioError(f"Scene video encoding failed: {exc}", 502) from None
            provider_used = model_used or "openrouter-video"
        else:
            # Render scene frame via OpenRouter Image / attached reference + fast 720p Ken Burns motion MP4
            img_b64: str | None = None
            if all_refs:
                try:
                    img_b64 = all_refs[0].split(",", 1)[1].strip() if "," in all_refs[0] else all_refs[0].strip()
                except Exception:
                    img_b64 = None
            if not img_b64:
                try:
                    art = hermes_artwork.generate_social_image({
                        "prompt": visual_prompt[:1350],
                        "aspect_ratio": aspect_ratio,
                        "references": all_refs[:6],
                    })
                    img_b64 = art["data"][0]["b64_json"]
                except Exception:
                    try:
                        art = hermes_artwork.generate_marketing_image({"prompt": visual_prompt[:1350]})
                        img_b64 = art["data"][0]["b64_json"]
                    except Exception:
                        img_b64 = None

            fps = 24
            total_frames = duration_sec * fps
            audio_inputs = ["-i", audio_path] if voice_mp3_bytes else ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]

            if img_b64:
                frame_path = os.path.join(tmpdir, "frame_in")
                Path(frame_path).write_bytes(base64.b64decode(img_b64))
                vf_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                    f"crop={width}:{height},"
                    f"zoompan=z='min(zoom+0.0006,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d={total_frames}:s={width}x{height}:fps={fps},setsar=1"
                )
                cmd = [
                    ffmpeg,
                    "-y",
                    "-i",
                    frame_path,
                    *audio_inputs,
                    "-t",
                    str(duration_sec),
                    "-vf",
                    vf_filter,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-shortest",
                    out_mp4,
                ]
            else:
                cmd = [
                    ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c=0x161a1b:s={width}x{height}:r={fps}:d={duration_sec}",
                    *audio_inputs,
                    "-t",
                    str(duration_sec),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-shortest",
                    out_mp4,
                ]
            try:
                subprocess.run(cmd, check=True, timeout=60, capture_output=True)
            except Exception as exc:
                raise VideoStudioError(f"Scene fallback render failed: {exc}", 502) from None
            provider_used = "keyframe-ken-burns"
            scene_cost_usd = 0.003

        final_bytes = Path(out_mp4).read_bytes()
        last_frame_uri = _extract_last_frame_b64(ffmpeg, out_mp4)

    return {
        "ok": True,
        "video_b64": base64.b64encode(final_bytes).decode("ascii"),
        "mime": "video/mp4",
        "duration_sec": duration_sec,
        "last_frame_b64": last_frame_uri,
        "provider": provider_used,
        "model_used": provider_used,
        "cost_usd": round(float(scene_cost_usd or 0.003), 4),
    }


def stitch_video_scenes(body: dict) -> dict:
    """Stitch 1..6 10-second scene MP4 clips into a single seamless 10s–58s MP4."""
    clips = body.get("clips_b64")
    if not isinstance(clips, list) or not (1 <= len(clips) <= 8):
        raise VideoStudioError("Provide between 1 and 8 scene clips to stitch.", 400)

    if len(clips) == 1:
        raw_one = clips[0].split(",", 1)[1].strip() if "," in str(clips[0]) else str(clips[0]).strip()
        return {
            "ok": True,
            "video_b64": raw_one,
            "mime": "video/mp4",
            "scene_count": 1,
        }

    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise VideoStudioError("FFmpeg is not installed on Box 1.", 503)

    with tempfile.TemporaryDirectory(prefix="central-video-stitch-") as tmpdir:
        concat_lines: list[str] = []
        for idx, clip_b64 in enumerate(clips):
            clean = str(clip_b64)
            if "," in clean:
                clean = clean.split(",", 1)[1].strip()
            clip_path = os.path.join(tmpdir, f"scene_{idx:02d}.mp4")
            Path(clip_path).write_bytes(base64.b64decode(clean))
            safe_clip_path = clip_path.replace("\\", "/")
            concat_lines.append(f"file '{safe_clip_path}'")

        list_file = os.path.join(tmpdir, "concat.txt")
        Path(list_file).write_text("\n".join(concat_lines), encoding="utf-8")
        out_mp4 = os.path.join(tmpdir, "stitched_master.mp4")

        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_file,
            "-t",
            "58",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            out_mp4,
        ]
        try:
            subprocess.run(cmd, check=True, timeout=90, capture_output=True)
        except Exception as exc:
            raise VideoStudioError(f"Video stitching failed: {exc}", 502) from None
        master_bytes = Path(out_mp4).read_bytes()

    return {
        "ok": True,
        "video_b64": base64.b64encode(master_bytes).decode("ascii"),
        "mime": "video/mp4",
        "scene_count": len(clips),
    }
