# Lightweight Laya Decision Engine powered by EmbeddingGemma 2 ONNX Runtime
# Completely removes PyTorch and HuggingFace cache bloat (drops from ~4GB to ~350MB).
FROM python:3.11-slim-bookworm

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LAYA_MODEL_DIR=/app/models

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 laya && useradd --uid 10001 --gid laya --create-home laya

RUN pip install --no-cache-dir \
    "fastapi>=0.115.0" \
    "uvicorn[standard]>=0.32.0" \
    "onnxruntime>=1.20.0" \
    "tokenizers>=0.21.0" \
    "numpy>=1.26.0" \
    "httpx>=0.28.0"

# Pre-download EmbeddingGemma 2 Quantized ONNX model & tokenizer during build
# Kept in single RUN layer with chown to prevent Docker UnionFS layer duplication
RUN mkdir -p /app/models \
    && curl -sL "https://huggingface.co/onnx-community/embeddinggemma-300m-ONNX/resolve/main/onnx/model_q4.onnx" -o /app/models/model_q4.onnx \
    && curl -sL "https://huggingface.co/onnx-community/embeddinggemma-300m-ONNX/resolve/main/onnx/model_q4.onnx_data" -o /app/models/model_q4.onnx_data \
    && curl -sL "https://huggingface.co/onnx-community/embeddinggemma-300m-ONNX/resolve/main/tokenizer.json" -o /app/models/tokenizer.json \
    && ln -sf /app/models/model_q4.onnx /app/models/model.onnx \
    && ln -sf /app/models/model_q4.onnx_data /app/models/model.onnx_data \
    && test -f /app/models/model_q4.onnx && test -f /app/models/model_q4.onnx_data \
    && chown -R laya:laya /app \
    && rm -rf /root/.cache /tmp/*

COPY --chown=laya:laya scripts/laya_embedding_service.py /app/main.py

USER 10001:10001
EXPOSE 8000

ENTRYPOINT ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

