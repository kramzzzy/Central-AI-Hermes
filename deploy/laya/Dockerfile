# Pinned public Laya source with CPU-only inference.
FROM python:3.11-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python -m venv --upgrade-deps /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN git init /src && cd /src \
    && git remote add origin https://github.com/NandhaKishorM/laya.git \
    && git fetch --depth 1 origin 6d942c92081fbc139e736bbd9ac0023223c29b7f \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = 6d942c92081fbc139e736bbd9ac0023223c29b7f
RUN pip install 'torch==2.14.0' --index-url https://download.pytorch.org/whl/cpu \
    && pip install 'transformers==5.5.4' '/src[serve]' && pip check \
    && python -c "import torch,laya; assert torch.version.cuda is None"

FROM python:3.11-slim-bookworm@sha256:2333bd330d12de02514770b3585cad313644316047cdee24a7acfdece6de6efb
ENV PATH=/opt/venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    TORCH_DISABLE_NATIVE_JIT=1 USE_TF=0 USE_TORCH=1 TOKENIZERS_PARALLELISM=false \
    HF_HOME=/home/laya/.cache/huggingface LAYA_DEVICE=cpu LAYA_REVISION=reviewed
RUN groupadd --gid 10001 laya && useradd --uid 10001 --gid laya --create-home laya \
    && mkdir -p /home/laya/.cache/huggingface /opt/setup \
    && chown -R laya:laya /home/laya/.cache
COPY --from=build /opt/venv /opt/venv
COPY --from=build /src/LICENSE /usr/share/doc/laya/LICENSE
COPY scripts/laya-router-service.py /opt/setup/laya-router-service.py
USER 10001:10001
WORKDIR /home/laya
ENTRYPOINT ["python", "/opt/setup/laya-router-service.py"]
