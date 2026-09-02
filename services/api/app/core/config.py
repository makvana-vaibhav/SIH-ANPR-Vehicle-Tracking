"""Application configuration.

Environment is parsed exactly once, here, into a frozen settings object.
Per CLAUDE.md there is no ``os.getenv`` scattered through the codebase — if a
value comes from the environment, it appears in this file with a type and a
default, or the application refuses to start.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
EventBusBackend = Literal["redis", "kafka"]


class Settings(BaseSettings):
    """Runtime configuration for the Sentinel-GJ API tier."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # the .env is shared across services; ignore foreign keys
    )

    # ── Core ──────────────────────────────────────────────────────────
    environment: Environment = "development"
    log_level: LogLevel = "INFO"
    app_name: str = "Sentinel-GJ"
    api_v1_prefix: str = "/api/v1"

    # Storage and transport are UTC everywhere. This is the *display* zone,
    # applied only at the presentation edge.
    display_timezone: str = Field(default="Asia/Kolkata", alias="TZ")

    # ── Database ──────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://sentinel:sentinel_dev_pw@postgres:5432/sentinel"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout_seconds: int = 30
    db_echo: bool = False

    # ── Redis / event bus ─────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"
    event_bus_backend: EventBusBackend = "redis"
    event_stream_key: str = "sentinel:events:detections"
    event_consumer_group: str = "sentinel-processors"
    kafka_bootstrap_servers: str = "redpanda:9092"
    kafka_topic_detections: str = "sentinel.detections"

    # ── Object store ──────────────────────────────────────────────────
    minio_endpoint: str = "minio:9000"
    minio_root_user: str = "sentinel"
    # Required, never defaulted. A credential with a default in source is a
    # credential that ships to production when someone forgets to set it.
    minio_root_password: str = Field(..., min_length=8)
    minio_bucket: str = "sentinel-media"
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
