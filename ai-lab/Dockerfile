# ── Sentinel-GJ AI Lab ───────────────────────────────────────────────
# Deliberately a separate image from the platform services. The lab is a
# development tool: it may carry heavier, AGPL-licensed, or experiment-only
# dependencies that must never reach a deployed Sentinel-GJ artifact.
#
# The default build is torch-free — the whole pipeline runs on ONNX Runtime.
# Build with `--build-arg PROFILE=torch` to add Ultralytics/PyTorch/EasyOCR for
# ONNX export, fine-tuning and cross-engine comparison.
FROM python:3.11-slim-bookworm

ARG PROFILE=core

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=4

# ffmpeg: OpenCV's bundled encoder cannot reliably produce H.264, and an
# annotated video that will not play in a browser is a worse debugging tool.
# libgomp1 is required by numpy/scipy/onnxruntime threading.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      libgomp1 \
      curl \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /lab

COPY requirements.txt requirements-torch.txt requirements-dev.txt ./
RUN pip install -r requirements.txt \
 && if [ "$PROFILE" = "torch" ]; then \
      pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1 torchvision==0.20.1 \
      && pip install ultralytics==8.3.155 easyocr==1.7.2 ; \
    fi \
 && pip install -r requirements-dev.txt

COPY ailab/ ./ailab/
COPY configs/ ./configs/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
COPY pyproject.toml README.md ./

RUN pip install --no-deps -e .

# Model weights and run outputs are bind-mounted, never baked in.
VOLUME ["/lab/models", "/lab/runs", "/lab/datasets"]

ENTRYPOINT ["ailab"]
CMD ["doctor"]
