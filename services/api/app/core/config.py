"""Application configuration.

Environment is parsed exactly once, here, into a frozen settings object.
Per CLAUDE.md there is no ``os.getenv`` scattered through the codebase — if a
value comes from the environment, it appears in this file with a type and a
default, or the application refuses to start.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
EventBusBackend = Literal["redis", "kafka"]


class Settings(BaseSettings):
    """Runtime configuration for the NagarNetra API tier."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # the .env is shared across services; ignore foreign keys
    )

    # ── Core ──────────────────────────────────────────────────────────
    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    app_name: str = "NagarNetra"
    api_v1_prefix: str = "/api/v1"

    # Storage and transport are UTC everywhere. This is the *display* zone,
    # applied only at the presentation edge.
    display_timezone: str = Field(default="Asia/Kolkata", alias="TZ")

    # ── Database ──────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://nagarnetra:nagarnetra_dev_pw@postgres:5432/nagarnetra"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: int = 30
    db_echo: bool = False

    # ── Redis / event bus ─────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"
    event_bus_backend: EventBusBackend = "redis"
    event_stream_key: str = "nagarnetra:events:detections"
    event_consumer_group: str = "nagarnetra-processors"
    #: Whether this process persists events as well as serving operators.
    #: True on a single-node deployment, which is the demo and the default.
    #: Set false on the API replicas of a deployment that runs dedicated
    #: ingest workers, so serving capacity and ingest capacity scale
    #: independently instead of every added API replica also adding a
    #: consumer nobody asked for.
    ingest_enabled: bool = True
    kafka_bootstrap_servers: str = "redpanda:9092"
    kafka_topic_detections: str = "nagarnetra.detections"

    # ── Object store ──────────────────────────────────────────────────
    minio_endpoint: str = "minio:9000"
    minio_root_user: str = "nagarnetra"
    # Required, never defaulted. A credential with a default in source is a
    # credential that ships to production when someone forgets to set it.
    minio_root_password: str = Field(..., min_length=8)
    minio_bucket: str = "nagarnetra-media"
    minio_secure: bool = False
    minio_public_endpoint: str = "http://localhost:9000"

    # ── Search ────────────────────────────────────────────────────────
    opensearch_url: str = "http://opensearch:9200"
    opensearch_index_detections: str = "detections"
    # When OpenSearch is unreachable, search degrades to Postgres pg_trgm
    # rather than failing. The demo must never die on a container.
    search_fallback_enabled: bool = True

    # ── Stream gateway ────────────────────────────────────────────────
    mediamtx_api_url: str = "http://mediamtx:9997"
    mediamtx_host: str = "mediamtx"
    mediamtx_rtsp_port: int = 8554
    mediamtx_public_webrtc_url: str = "http://localhost:8889"
    mediamtx_public_hls_url: str = "http://localhost:8888"

    # ── Auth ──────────────────────────────────────────────────────────
    # No default: the application refuses to start rather than sign tokens
    # with a key an attacker could read in the repository.
    jwt_secret_key: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    stream_token_expire_seconds: int = 120
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = Field(..., min_length=8)
    cors_origins: str = "http://localhost:5173,http://localhost:8080"

    # ── Detection / alerting thresholds ───────────────────────────────
    detection_emit_confidence: float = 0.55
    alert_auto_confidence: float = 0.80
    duplicate_suppression_seconds: int = 60

    # ── Correlator ────────────────────────────────────────────────────
    route_max_plausible_kmph: float = 150.0
    route_min_plausible_kmph: float = 2.0
    route_dwell_merge_seconds: int = 120
    convoy_min_shared_cameras: int = 3
    convoy_time_window_seconds: int = 90

    # ── Traffic intelligence ──────────────────────────────────────────
    # Thresholds for congestion, queues and stopped vehicles. Reasoned
    # defaults, not measurements: no calibrated ground truth for Ahmedabad
    # traffic exists in this repo, and inventing one would be worse than
    # saying so. They are expected to be tuned against a real deployment,
    # which is why they are settings rather than constants in a function.
    # The maths they feed lives in `app/services/traffic.py`, and
    # `tests/test_traffic.py` pins where each boundary sits.
    #: Where recorded clips live, as this container sees them. Same directory
    #: the simulator mounts as `SIM_VIDEO_DIR`, which is what makes a filename
    #: chosen here resolvable there. Listing it is the only thing the API does
    #: with it — it never opens a clip.
    video_dir: Path = Path("/data/videos")

    traffic_queue_min_vehicles: int = 4
    traffic_queue_dwell_seconds: float = 25.0
    traffic_queue_min_sustained_buckets: int = 2
    #: A single vehicle stationary this long is worth a human look. Reported as
    #: a *possible obstruction* — the system sees that it stopped, never why.
    traffic_obstruction_dwell_seconds: float = 90.0
    #: Vehicles in view that represents a saturated camera, used to normalise
    #: occupancy before it is combined with speed and travel time.
    traffic_occupancy_reference: int = 12
    traffic_congestion_moderate: float = 0.30
    traffic_congestion_heavy: float = 0.55
    traffic_congestion_severe: float = 0.75

    # ── Health monitor ────────────────────────────────────────────────
    health_probe_interval_seconds: int = 30
    health_probe_timeout_seconds: int = 5
    health_offline_after_failures: int = 2

    # ── Retention (enforced by app/services/retention.py) ─────────────
    # Under the DPDP Act the stated period is the lawful basis for holding the
    # data at all, so these are not advisory. See docs/SECURITY.md.
    retention_detections_days: int = 365
    retention_media_days: int = 90
    retention_camera_health_days: int = 90
    retention_audit_days: int = 1825
    # Off only for a deployment that has an external purge process. When false
    # the sweep still runs and reports what it *would* delete, so the gap
    # between policy and practice is visible rather than silent.
    retention_enforce: bool = True

    # ── The challenge's camera grid ───────────────────────────────────
    # Two hosts, deliberately. The catalogue and HLS are behind a CDN; RTSP and
    # WebRTC are not, because a CDN cannot proxy either — so the organisers
    # publish those on a direct address. Deriving all four from one hostname is
    # what made port 8554 appear closed for weeks.
    sandbox_base_url: str = "https://cctv.corp8.cloud"
    sandbox_media_host: str = "103.250.160.189"
    # The grid began requiring RTSP/WHEP credentials on 3 Sep 2026; it was open
    # the day before. Empty means anonymous, which is what the original guide
    # described — so an empty value is a valid configuration, not a missing one.
    #
    # These live in `.env` (gitignored) and are redacted wherever a stream URL
    # is logged. They are a real secret: anyone holding them can pull video
    # from a government camera network.
    sandbox_rtsp_username: str = ""
    sandbox_rtsp_password: str = ""

    # ── Rate limiting ─────────────────────────────────────────────────
    # Counted in Redis, so the limit is per-deployment rather than per-replica.
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 300
    rate_limit_window_seconds: int = 60
    # Authentication is limited far harder: it is the endpoint worth guessing
    # against, and a legitimate operator signs in once a shift.
    rate_limit_auth_requests: int = 10
    rate_limit_auth_window_seconds: int = 300

    # ── Validators ────────────────────────────────────────────────────
    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, v: str) -> str:
        """Catch a sync driver early rather than at first query.

        Everything in this codebase is async; a ``postgresql://`` URL would
        blow up deep inside SQLAlchemy with a far less obvious message.
        """
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL must use the asyncpg driver "
                f"(postgresql+asyncpg://...), got: {v.split('://')[0]}://"
            )
        return v

    @field_validator("jwt_secret_key")
    @classmethod
    def _reject_dev_secret_in_prod(cls, v: str, info: ValidationInfo) -> str:
        """Refuse to boot production with the example development secret.

        The value is required (no default), but .env.example ships a sample so
        the demo works out of the box. That sample must never reach production.
        """
        if info.data.get("environment") == "production" and "dev_only" in v:
            raise ValueError(
                "JWT_SECRET_KEY is still the development sample from .env.example. "
                "Generate one with `openssl rand -hex 32` before deploying."
            )
        return v

    # ── Derived ───────────────────────────────────────────────────────
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def minio_url(self) -> str:
        scheme = "https" if self.minio_secure else "http"
        return f"{scheme}://{self.minio_endpoint}"

    def sanitised(self) -> dict[str, str]:
        """Configuration safe to log or expose — secrets redacted.

        Used at startup and by the admin diagnostics endpoint. Anything whose
        name suggests a credential is replaced, never truncated (a truncated
        secret is still a leaked secret).
        """
        secret_markers = ("password", "secret", "key", "token", "credentials")
        out: dict[str, str] = {}
        for name, value in self.model_dump().items():
            if any(marker in name for marker in secret_markers):
                out[name] = "***redacted***"
            else:
                out[name] = str(value)
        return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()
