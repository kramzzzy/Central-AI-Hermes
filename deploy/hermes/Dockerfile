# Official Hermes v0.21.5 / v2026.9.24, source f97608f178d1ffeca59860195ab7da295f7c8e5f.
# This immutable public base replaces the deleted private voice image.
FROM nousresearch/hermes-agent:v2026.9.24@sha256:fca358f12efd65bfaaca05884166f15c0e2788375ca30d77061ac1ebc96452b7
USER root
WORKDIR /opt/os-adapter
# Memory runs separately; these are its SDK and the CPU phone dependencies.
RUN uv pip install --python /opt/hermes/.venv/bin/python --no-cache \
    'hindsight-client==0.10.2' 'hindsight-embed==0.10.2' 'cryptography==50.0.0' 'msal==1.39.0' \
    'faster-whisper==1.2.1' 'msgpack==1.1.2' 'livekit==1.1.20' 'onnxruntime==1.30.0' 'piper-tts==1.8.0' 'edge-tts==7.2.7' 'imageio-ffmpeg==0.6.0'

# Immutable CPU models and pinned external plugins stay above COPY layers for fast rebuilds.
ADD --checksum=sha256:0a309668932205e762801f1efc2736cd4b0120329622adf62be09e56339d3330 https://huggingface.co/rhasspy/piper-voices/resolve/c10ece1aade47bb51c153c893d14e5bf8e5b7117/en/en_GB/alan/medium/en_GB-alan-medium.onnx /opt/voice-models/en_GB-alan-medium.onnx
ADD https://huggingface.co/rhasspy/piper-voices/resolve/c10ece1aade47bb51c153c893d14e5bf8e5b7117/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json /opt/voice-models/en_GB-alan-medium.onnx.json
ADD https://huggingface.co/rhasspy/piper-voices/raw/c10ece1aade47bb51c153c893d14e5bf8e5b7117/en/en_GB/alan/medium/MODEL_CARD /opt/voice-models/PIPER_MODEL_CARD
ADD --checksum=sha256:1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3 https://raw.githubusercontent.com/snakers4/silero-vad/1e261b036686cd0017d500ee96acd1c4ba572a9d/src/silero_vad/data/silero_vad.onnx /opt/voice-models/silero-vad.onnx
ADD --checksum=sha256:3f7ea75f19ce0d3770019b3f748e3d7f026ccfedd82a9d754c695dda3905e67d https://codeload.github.com/vectorize-io/hindsight/tar.gz/176f8c2de1369f569c489b831d143b78128b5535 /tmp/hindsight-plugin.tar.gz
RUN ln -sf /opt/voice-models/en_GB-alan-medium.onnx /opt/voice-models/en_US-lessac-medium.onnx \
    && ln -sf /opt/voice-models/en_GB-alan-medium.onnx.json /opt/voice-models/en_US-lessac-medium.onnx.json \
    && /opt/hermes/.venv/bin/python -c "from faster_whisper.utils import download_model; download_model('base.en', revision='3d3d5dee26484f91867d81cb899cfcf72b96be6c', output_dir='/opt/voice-models/whisper-base.en')" \
    && mkdir -p /opt/hindsight-plugin && tar -xzf /tmp/hindsight-plugin.tar.gz --strip-components=3 -C /opt/hindsight-plugin hindsight-176f8c2de1369f569c489b831d143b78128b5535/hindsight-integrations/hermes \
    && rm -rf /tmp/hindsight-plugin.tar.gz /root/.cache \
    && test -f /opt/hindsight-plugin/plugin.yaml \
    && chmod -R a+rX /opt/voice-models /opt/hindsight-plugin
# Only transport/integration code enters this layer. Profiles and credentials are volumes/secrets.
COPY scripts/*.py ./scripts/
COPY hermes_plugins ./hermes_plugins/
# Project skills join Hermes' bundled set; central_ai_service seeds them into each profile.
COPY skills/personal-assistant /opt/hermes/skills/personal-assistant/
# Both surfaces resolve the maintained modules; no inherited /opt/setup code.
RUN ln -s /opt/os-adapter/scripts /opt/setup \
    && ln -s whatsapp-audio.py scripts/whatsapp_audio.py \
    && ln -s whatsapp-text-supervisor.py scripts/whatsapp_text_supervisor.py \
    && ln -sf /opt/voice-models/silero-vad.onnx /opt/os-adapter/scripts/silero-vad.onnx
ENV CENTRAL_AI_APP_OS_PLUGIN=1 PYTHONPATH=/opt/hermes:/opt/os-adapter/scripts:/opt/os-adapter
RUN uv pip check --python /opt/hermes/.venv/bin/python && /opt/hermes/.venv/bin/python scripts/check-backend-build.py \
    && mkdir -p .runtime /opt/data/logs \
    && chown -R 10000:10000 /opt/os-adapter /opt/data \
    && rm -rf /tmp/* /root/.cache
USER 10000:10000
ENTRYPOINT ["/opt/hermes/.venv/bin/python", "/opt/os-adapter/scripts/central_ai_service.py", "hermes"]
