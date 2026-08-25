"""Domain vocabularies.

These are stored as TEXT in Postgres (matching the specified schema) rather
than native enum types: a statewide platform onboards new departments, vendors
and vehicle classes over its lifetime, and adding a value to a Postgres enum
requires a migration and an exclusive lock. Validation happens at the
application edge through Pydantic instead.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """User roles. The RBAC matrix in ``app/core/rbac.py`` maps these to grants."""

    ADMIN = "admin"
    SUPERVISOR = "supervisor"
    OPERATOR = "operator"
    ANALYST = "analyst"
    AUDITOR = "auditor"
    API_CLIENT = "api_client"


class DepartmentCode(StrEnum):
    """Owning departments across the Gujarat CCTV estate.

    These are the five departments whose live feeds the challenge sandbox
    exposes (sentinel.gujarat.gov.in), plus SCRB, which operates the state
    aggregation tier. Each department keeps its own VMS and remains
    authoritative for its own recordings — the whole point of Model 5.
    """

    HEALTH = "HEALTH"
    POLICE = "POLICE"
    GSRTC = "GSRTC"  # Gujarat State Road Transport Corporation
    PANCHAYAT = "PANCHAYAT"
    MUNICIPAL = "MUNICIPAL"
    SCRB = "SCRB"  # State Crime Records Bureau — state aggregation tier


class VmsVendor(StrEnum):
    """VMS vendors we federate with.

    Each keeps its own recordings; we ingest metadata and pull streams on
    demand. ``SIMULATED`` powers the demo fleet.
    """

    MILESTONE = "milestone"
    GENETEC = "genetec"
    CPPLUS = "cpplus"
    HIKVISION = "hikvision"
    GENERIC_RTSP = "generic_rtsp"
    ONVIF = "onvif"
    SENTINEL_SANDBOX = "sentinel_sandbox"  # the challenge's own camera grid
    SIMULATED = "simulated"


class AdapterType(StrEnum):
    """Integration strategy, resolved by the adapter registry (Phase 3)."""

    RTSP = "rtsp"
    ONVIF = "onvif"
    VENDOR_API = "vendor_api"
    SENTINEL_SANDBOX = "sentinel_sandbox"
    SIMULATED = "simulated"


class CameraStatus(StrEnum):
    """Fleet health, maintained by the health monitor (Phase 3)."""

    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class CameraType(StrEnum):
    FIXED = "fixed"
    PTZ = "ptz"
    ANPR = "anpr"
    DOME = "dome"


class Protocol(StrEnum):
    RTSP = "rtsp"
    ONVIF = "onvif"
    VENDOR_API = "vendor_api"


class VehicleType(StrEnum):
    """Includes classes that matter on Gujarat state highways."""

    CAR = "car"
    TRUCK = "truck"
    BUS = "bus"
    MOTORCYCLE = "motorcycle"
    AUTO = "auto"
    TRACTOR = "tractor"


class WatchlistCategory(StrEnum):
    STOLEN = "stolen"
    WANTED = "wanted"
    SUSPECT = "suspect"
    BOLO = "bolo"  # be on the lookout
    VIP = "vip"
    TEST = "test"


class Priority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AlertType(StrEnum):
    WATCHLIST_HIT = "watchlist_hit"
    POSSIBLE_MATCH = "possible_match"  # fuzzy match, deliberately lower priority
    SPEED = "speed"
    CONVOY = "convoy"
    ANOMALY = "anomaly"
    CAMERA_DOWN = "camera_down"


class AlertStatus(StrEnum):
    """Alert lifecycle. Every transition records who and when."""

    NEW = "new"
    ACKNOWLEDGED = "acknowledged"
    DISPATCHED = "dispatched"
    CLOSED = "closed"
    FALSE_POSITIVE = "false_positive"


class AuditResult(StrEnum):
    SUCCESS = "success"
    DENIED = "denied"
    ERROR = "error"
