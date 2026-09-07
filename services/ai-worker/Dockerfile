# ── NagarNetra AI worker ────────────────────────────────────────────
# Runs the ai-lab pipeline against live RTSP cameras and publishes vehicle
# events to the platform's Redis Stream.
#
# Built from the repository root so it can take the pipeline from ai-lab/ — the
# worker deliberately runs the *same* code the lab measures. Vendoring a copy
# would let the two drift, and every accuracy number would quietly become a
# claim about code that is no longer running.
#
#   docker build -f services/ai-worker/Dockerfile .
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# RTSP over TCP, not UDP. OpenCV tries UDP first and falls back after a failed
# SETUP, which wastes a round trip on every reconnect; worse, UDP RTSP drops
# packets silently and hands the decoder corrupt frames, which reach the
# pipeline as smeared plates rather than as an error. TCP is what CCTV
# integrations use, for exactly this reason.
ENV OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp"

# libgomp1 is required by numpy/scipy/onnxruntime threading. No ffmpeg: the
# worker decodes RTSP through OpenCV's bundled FFmpeg and never encodes video.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY services/ai-worker/requirements.txt ./
# --no-deps on the RapidOCR line only: see the note in requirements.txt about
# it pulling in the non-headless OpenCV and displacing the headless build.
RUN grep -v '^rapidocr-onnxruntime' requirements.txt > /tmp/base.txt \
 && pip install -r /tmp/base.txt \
 && pip install --no-deps rapidocr-onnxruntime==1.4.4 \
 && python -c "import cv2, rapidocr_onnxruntime; print('cv2', cv2.__version__, 'headless ok')"

# The pipeline, installed as a library. --no-deps because requirements.txt
# above already pins them, and the lab's own file carries dev extras the
# worker has no business shipping.
COPY ai-lab/pyproject.toml ai-lab/README.md /opt/ailab/
COPY ai-lab/ailab /opt/ailab/ailab
COPY ai-lab/configs /opt/ailab/configs
RUN pip install --no-deps -e /opt/ailab

COPY services/ai-worker/ai_worker ./ai_worker

# Weights are mounted, not baked: they are large, they are fetched by a
# committed script, and they must never enter git or an image layer.
VOLUME ["/models"]

# Healthy means "can reach the bus". A worker with no cameras assigned is idle,
# not broken.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD ["python", "-m", "ai_worker.healthcheck"]

CMD ["python", "-m", "ai_worker"]
