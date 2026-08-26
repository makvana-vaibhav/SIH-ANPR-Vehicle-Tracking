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
    event_stream_key: str = "sentinel:events:detections"
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
    # Hard cap on concurrent streams per worker. Each stream is a decode thread
    # plus an inference loop; oversubscribing makes every camera slower rather
    # than covering more of them.
    ai_worker_max_cameras: int = Field(default=4, ge=1)

    # ── pipeline ──
    ai_config: str = "stream"
    ai_models_dir: str = "/models"
    # Rediscover publishing cameras this often.
    discovery_interval_s: float = 20.0
    # Wait this long before reopening a stream that failed.
    restart_delay_s: float = 5.0

    log_level: str = "INFO"


settings = WorkerSettings()
