FROM ghcr.io/vectorize-io/hindsight:0.10.2-slim@sha256:35656b02eb8b14a648822fd28fe76129207ce18ece3599947dcd5a0f4b7d89df
USER root
# CPU inference only: no Torch/CUDA stack or additional embedding API key.
RUN uv pip install --python /app/api/.venv/bin/python 'transformers==5.5.4' 'flashrank==0.2.10'
RUN uv pip check --python /app/api/.venv/bin/python \
    && /app/api/.venv/bin/python -c "import onnxruntime,flashrank; from transformers import AutoTokenizer"
COPY scripts/hindsight-start.py /app/hindsight-start.py
COPY scripts/hindsight-start-vps.py /app/hindsight-start-vps.py
COPY scripts/hindsight-backup.py /app/hindsight-backup.py
RUN mkdir -p /home/hindsight/.cache && chown -R 1000:1000 /home/hindsight/.cache
USER hindsight
ENTRYPOINT ["python", "/app/hindsight-start.py"]
