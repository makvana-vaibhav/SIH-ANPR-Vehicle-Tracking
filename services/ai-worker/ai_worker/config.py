"""AI worker settings.

Parsed once from the environment into a single object, the same way the API
does it, so nothing in the worker reaches for os.getenv on its own.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # ── where events go ──
    redis_url: str = "redis://redis:6379/0"
    event_stream_key: str = "nagarnetra:events:detections"
    # Trim the stream to roughly this many entries. The API consumes it within
    # milliseconds; the cap exists so a stopped consumer cannot fill Redis.
    event_stream_maxlen: int = 100_000

    # ── where video comes from ──
    mediamtx_host: str = "mediamtx"
    mediamtx_rtsp_port: int = 8554
    mediamtx_api_url: str = "http://mediamtx:9997"

    # ── which cameras this worker owns ──
    #
    # Cameras are split across workers by a stable hash of the camera code, so
    # adding a worker rebalances without any coordinator and without two
    # workers ever opening the same stream. `AI_WORKER_COUNT` is the shard
    # count, `AI_WORKER_INDEX` this worker's slot.
    ai_worker_index: int = Field(default=0, ge=0)
    ai_worker_count: int = Field(default=1, ge=1)
    # An explicit comma-separated list overrides discovery entirely.
    ai_worker_cameras: str = ""
    # Where cameras come from:
    #   "registry" the ANPR fleet as the API publishes it — federated grid
    #              cameras and our own alike, and the only source that still
    #              lists a federated camera while its gateway is down
    #   "sandbox"  the organisers' grid, read from its own catalogue
    #   "mediamtx" whatever is publishing to our gateway (simulator, RTSP pulls)
    ai_worker_source: str = "registry"
    # The CDN, for the catalogue and HLS.
    sandbox_base_url: str = "https://cctv.corp8.cloud"
    # Where RTSP and WHEP are served. A CDN cannot proxy either, which is why
    # the organisers publish them on a direct address — and why pointing these
    # at the CDN made port 8554 look closed for weeks.
    sandbox_media_host: str = "103.250.160.189"
    # Force a transport instead of probing. Empty means probe once at startup.
    sandbox_transport: str = ""
    # Concurrent streams per worker. Each is a decode thread plus an inference
    # loop; oversubscribing makes every camera slower rather than covering more
    # of them. Measured at roughly one camera per spare core.
    ai_worker_max_cameras: int = Field(default=4, ge=1)
    # How long a camera holds a slot before yielding it to one that is waiting.
    # Cameras beyond the slot count are rotated rather than dropped, so the
    # fleet is covered partially in time instead of not at all in space.
    # 0 disables rotation: the first N cameras run and the rest never do.
    ai_worker_slice_seconds: float = Field(default=45.0, ge=0.0)
    # Cameras that never yield their slot — the demonstration feed, or a
    # junction under active investigation. Comma-separated codes.
    ai_worker_pinned: str = ""

    # ── pipeline ──
    ai_config: str = "stream"
    ai_models_dir: str = "/models"
    # Rediscover publishing cameras this often.
    discovery_interval_s: float = 20.0
    # Wait this long before reopening a stream that failed.
    restart_delay_s: float = 5.0

    log_level: str = "INFO"


settings = WorkerSettings()
