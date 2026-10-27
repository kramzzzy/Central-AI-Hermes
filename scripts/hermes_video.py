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
    val = os.environ.get(name, "").strip()
    if val:
        return val
    for env_path in (
        Path(os.environ.get("HERMES_PROFILE_ROOT", "/opt/data")) / "profiles" / os.environ.get("HERMES_PROFILE", "default") / ".env",
        Path("/opt/data/.env"),
        Path(".env.local"),
        Path(".env"),
    ):
        try:
            if env_path.is_file():
                for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == name:
                        cleaned = v.strip().strip('"').strip("'")
                        if cleaned:
                            return cleaned
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
    default_voice_id = _get_env_key("FISH_VOICE_ID")

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


def _try_openrouter_video_api(
    visual_prompt: str,
    duration_sec: int,
    aspect_ratio: str,
    first_frame_data_uri: str | None,
    openrouter_key: str,
) -> bytes | None:
    """Attempt native video generation via OpenRouter /api/v1/videos if available on the key."""
    if not openrouter_key or openrouter_key.startswith("sk-or-v1-standalone-default"):
        return None
    video_model = _get_env_key("OPENROUTER_VIDEO_MODEL", "")
    models_to_try = [m for m in [video_model, "alibaba/wan-2.6", "google/veo-3.1-fast"] if m]

    for model_slug in models_to_try:
        payload: dict = {
            "model": model_slug,
            "prompt": visual_prompt[:1200],
            "duration": max(5, min(10, int(duration_sec))),
            "aspect_ratio": aspect_ratio if aspect_ratio in ("16:9", "9:16", "1:1") else "9:16",
        }
        if first_frame_data_uri:
            payload["frame_images"] = {"first_frame": first_frame_data_uri}

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
            with urllib.request.urlopen(req, timeout=30) as resp:
                job = json.loads(resp.read().decode("utf-8", errors="replace"))
            job_id = str(job.get("id") or "").strip()
            direct_url = str(job.get("url") or job.get("video_url") or "").strip()
            if direct_url.startswith("https://"):
                with urllib.request.urlopen(direct_url, timeout=45) as vresp:
                    data = vresp.read(50_000_000)
                    if len(data) > 4096:
                        return data
            if not job_id:
                continue
            deadline = time.time() + 110
            while time.time() < deadline:
                time.sleep(4)
                poll_req = urllib.request.Request(
                    f"https://openrouter.ai/api/v1/videos/{urllib.parse.quote(job_id)}",
                    headers={"Authorization": f"Bearer {openrouter_key}"},
                )
                with urllib.request.urlopen(poll_req, timeout=20) as presp:
                    status_data = json.loads(presp.read().decode("utf-8", errors="replace"))
                st = str(status_data.get("status") or "").lower()
                if st in ("completed", "succeeded", "done"):
                    v_url = (
                        status_data.get("url")
                        or status_data.get("video_url")
                        or (status_data.get("output") or [None])[0]
                    )
                    if isinstance(v_url, str) and v_url.startswith("https://"):
                        with urllib.request.urlopen(v_url, timeout=45) as dl:
                            v_bytes = dl.read(50_000_000)
                            if len(v_bytes) > 4096:
                                return v_bytes
                    break
                if st in ("failed", "cancelled", "expired", "error"):
                    break
        except Exception:
            continue
    return None


def _extract_last_frame_b64(ffmpeg: str, mp4_path: str) -> str | None:
    out_png = mp4_path + ".last.png"
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
                "2",
                out_png,
            ],
            check=True,
            timeout=20,
            capture_output=True,
        )
        if os.path.isfile(out_png):
            raw = Path(out_png).read_bytes()
            if len(raw) > 512:
                return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    except Exception:
        pass
    finally:
        with_ignore_unlink(out_png)
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
            all_refs.append(r)

    openrouter_key = _get_env_key("OPENROUTER_API_KEY")
    raw_video_bytes = _try_openrouter_video_api(
        visual_prompt,
        duration_sec,
        aspect_ratio,
        all_refs[0] if all_refs else None,
        openrouter_key,
    )

    width, height = (1080, 1920) if aspect_ratio == "9:16" else ((1920, 1080) if aspect_ratio == "16:9" else (1080, 1080))

    with tempfile.TemporaryDirectory(prefix="central-video-scene-") as tmpdir:
        out_mp4 = os.path.join(tmpdir, "scene_out.mp4")
        audio_path = os.path.join(tmpdir, "voice.mp3")
        if voice_mp3_bytes:
            Path(audio_path).write_bytes(voice_mp3_bytes)

        if raw_video_bytes:
            in_mp4 = os.path.join(tmpdir, "raw.mp4")
            Path(in_mp4).write_bytes(raw_video_bytes)
            if voice_mp3_bytes:
                cmd = [
                    ffmpeg,
                    "-y",
                    "-i",
                    in_mp4,
                    "-i",
                    audio_path,
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
                    "veryfast",
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
                    "-i",
                    in_mp4,
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=44100",
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
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-shortest",
                    out_mp4,
                ]
            subprocess.run(cmd, check=True, timeout=90, capture_output=True)
            provider_used = "openrouter-video"
        else:
            # Render high-res scene frame via OpenRouter Image (with reference continuity) + cinematic motion MP4
            img_b64: str | None = None
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
                except Exception as exc:
                    raise VideoStudioError(f"Scene frame generation failed: {exc}", 502) from None

            frame_path = os.path.join(tmpdir, "frame.png")
            Path(frame_path).write_bytes(base64.b64decode(img_b64))
            fps = 25
            total_frames = duration_sec * fps
            vf_filter = (
                f"scale={width * 2}:{height * 2}:force_original_aspect_ratio=increase,"
                f"crop={width * 2}:{height * 2},"
                f"zoompan=z='min(zoom+0.0005,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"d={total_frames}:s={width}x{height}:fps={fps},setsar=1"
            )
            if voice_mp3_bytes:
                cmd = [
                    ffmpeg,
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    frame_path,
                    "-i",
                    audio_path,
                    "-t",
                    str(duration_sec),
                    "-vf",
                    vf_filter,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
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
                    "-loop",
                    "1",
                    "-i",
                    frame_path,
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-t",
                    str(duration_sec),
                    "-vf",
                    vf_filter,
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-shortest",
                    out_mp4,
                ]
            subprocess.run(cmd, check=True, timeout=90, capture_output=True)
            provider_used = "openrouter-cinematic-scene"

        final_bytes = Path(out_mp4).read_bytes()
        last_frame_uri = _extract_last_frame_b64(ffmpeg, out_mp4)

    return {
        "ok": True,
        "video_b64": base64.b64encode(final_bytes).decode("ascii"),
        "mime": "video/mp4",
        "duration_sec": duration_sec,
        "last_frame_b64": last_frame_uri,
        "provider": provider_used,
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
            concat_lines.append(f"file '{clip_path}'")

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
            "veryfast",
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
        subprocess.run(cmd, check=True, timeout=120, capture_output=True)
        master_bytes = Path(out_mp4).read_bytes()

    return {
        "ok": True,
        "video_b64": base64.b64encode(master_bytes).decode("ascii"),
        "mime": "video/mp4",
        "scene_count": len(clips),
    }
