# Official Hermes v0.21.5 / v2026.9.24, source f97608f178d1ffeca59860195ab7da295f7c8e5f.
# This immutable public base replaces the deleted private voice image.
FROM nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7
USER root
WORKDIR /opt/os-adapter
# Memory runs separately; these are its SDK and the CPU phone dependencies.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache \
    'hindsight-client==0.10.2' 'hindsight-embed==0.10.2' 'cryptography==50.0.0' 'msal==1.39.0' \
    'faster-whisper==1.2.1' 'msgpack==1.1.2' 'livekit==1.1.20' 'onnxruntime==1.30.0' 'piper-tts==1.8.0'
# Pre-install Playwright Chromium for native browser tools
RUN env npm_config_cache=/tmp/.npm PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright npx playwright install chromium \
    && chmod -R a+rX /opt/hermes/.playwright \
    && ln -sf /opt/hermes/.playwright/chromium-*/chrome-linux/chrome /usr/local/bin/chromium \
    && ln -sf /opt/hermes/.playwright/chromium-*/chrome-linux/chrome /usr/local/bin/google-chrome \
    && ln -sf /opt/hermes/.playwright/chromium-*/chrome-linux/chrome /usr/local/bin/chrome

# Immutable CPU models are shared by native Hermes and both call surfaces.
ADD --checksum=sha256:5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f https://huggingface.co/rhasspy/piper-voices/resolve/c10ece1aade47bb51c153c893d14e5bf8e5b7117/en/en_US/lessac/medium/en_US-lessac-medium.onnx /opt/voice-models/en_US-lessac-medium.onnx
ADD https://huggingface.co/rhasspy/piper-voices/resolve/c10ece1aade47bb51c153c893d14e5bf8e5b7117/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json /opt/voice-models/en_US-lessac-medium.onnx.json
ADD https://huggingface.co/rhasspy/piper-voices/raw/c10ece1aade47bb51c153c893d14e5bf8e5b7117/en/en_US/lessac/medium/MODEL_CARD /opt/voice-models/PIPER_MODEL_CARD
RUN /opt/hermes/.venv/bin/python -c "from faster_whisper.utils import download_model; download_model('base.en', revision='3d3d5dee26484f91867d81cb899cfcf72b96be6c', output_dir='/opt/voice-models/whisper-base.en')" \
    && chmod -R a+rX /opt/voice-models
# Only transport/integration code enters this layer. Profiles and credentials are volumes/secrets.
COPY scripts/*.py ./scripts/
COPY hermes_plugins ./hermes_plugins/
# Project skills join Hermes' bundled set; central_ai_service seeds them into each profile.
COPY skills/personal-assistant /opt/hermes/skills/personal-assistant/
ADD --checksum=sha256:3f7ea75f19ce0d3770019b3f748e3d7f026ccfedd82a9d754c695dda3905e67d https://codeload.github.com/vectorize-io/hindsight/tar.gz/176f8c2de1369f569c489b831d143b78128b5535 /tmp/hindsight-plugin.tar.gz
RUN mkdir -p /opt/hindsight-plugin && tar -xzf /tmp/hindsight-plugin.tar.gz --strip-components=3 -C /opt/hindsight-plugin hindsight-176f8c2de1369f569c489b831d143b78128b5535/hindsight-integrations/hermes && rm /tmp/hindsight-plugin.tar.gz && test -f /opt/hindsight-plugin/plugin.yaml
# Both surfaces resolve the maintained modules; no inherited /opt/setup code.
RUN ln -s /opt/os-adapter/scripts /opt/setup \
    && ln -s whatsapp-audio.py scripts/whatsapp_audio.py \
    && ln -s whatsapp-text-supervisor.py scripts/whatsapp_text_supervisor.py
ADD --checksum=sha256:1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3 https://raw.githubusercontent.com/snakers4/silero-vad/1e261b036686cd0017d500ee96acd1c4ba572a9d/src/silero_vad/data/silero_vad.onnx /opt/os-adapter/scripts/silero-vad.onnx
ENV CENTRAL_AI_APP_OS_PLUGIN=1 PYTHONPATH=/opt/hermes:/opt/os-adapter/scripts:/opt/os-adapter
RUN uv pip check --python /opt/hermes/.venv/bin/python && /opt/hermes/.venv/bin/python scripts/check-backend-build.py \
    && mkdir -p .runtime /opt/data/logs && chmod 644 scripts/silero-vad.onnx \
    && chown -R 10000:10000 /opt/os-adapter /opt/data
USER 10000:10000
ENTRYPOINT ["/opt/hermes/.venv/bin/python", "/opt/os-adapter/scripts/central_ai_service.py", "hermes"]
