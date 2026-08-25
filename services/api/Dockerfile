# ══════════════════════════════════════════════════════════════════════
#  Sentinel-GJ API
#  Multi-stage: dependencies resolve in a builder, runtime stays slim.
#  Runs as a non-root user — this is police infrastructure.
# ══════════════════════════════════════════════════════════════════════

ARG PYTHON_VERSION=3.11

# ── Builder ───────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# build-essential + libpq-dev are needed to compile asyncpg/psycopg2 wheels
# on arm64 where prebuilt wheels are not always published.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY services/api/requirements.txt services/api/requirements-dev.txt ./

# Install into an isolated prefix we can copy wholesale into the runtime.
ARG INSTALL_DEV=false
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip==24.3.1 setuptools==75.6.0 wheel==0.45.1 \
    && if [ "$INSTALL_DEV" = "true" ]; then \
           /opt/venv/bin/pip install -r requirements-dev.txt; \
       else \
           /opt/venv/bin/pip install -r requirements.txt; \
       fi

# ── Runtime ───────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

LABEL org.opencontainers.image.title="Sentinel-GJ API" \
      org.opencontainers.image.description="Statewide CCTV intelligence platform — API tier" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/services/api:/app/packages/contracts/generated \
    PATH="/opt/venv/bin:$PATH" \
    TZ=UTC

# libpq5 for asyncpg/psycopg2 at runtime; curl for container-level probes.
# ffmpeg supplies ffprobe (RTSP health probing) and the simulator's stream
# publisher. It is the one heavyweight runtime dependency we accept, because
# probing RTSP with a TCP connect would call a dead camera healthy.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1001 sentinel \
    && useradd --uid 1001 --gid 1001 --create-home --shell /bin/bash sentinel

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=sentinel:sentinel services/api /app/services/api
COPY --chown=sentinel:sentinel packages/contracts /app/packages/contracts
# Seed and maintenance scripts run inside this image (`make seed`).
COPY --chown=sentinel:sentinel scripts /app/scripts

USER sentinel

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=12 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--app-dir", "/app/services/api", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
