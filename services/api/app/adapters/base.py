"""The integration layer contract.

This is the seam that makes Model 3 (federation middleware) work. Gujarat's
estate spans multiple vendors speaking different protocols; every one of them
is reduced to this interface, so the rest of the platform — health monitor,
stream gateway, AI pipeline — never knows or cares which vendor a camera
belongs to.

Adding a new vendor means writing one class and registering it. Nothing else
in the platform changes.
"""

from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.models.enums import CameraStatus

log = get_logger("api.adapters")


#: Errors meaning *our integration* failed, not that the camera is faulty.
#: These must not mark a camera offline: if we cannot reach the VMS, we do not
#: know the camera's state, and reporting "offline" would send an engineer to
#: check a camera that is working perfectly.
INTEGRATION_ERRORS = frozenset(
    {
        "vms_unreachable",
        "onvif_unreachable",
        "sandbox_unreachable",
        "gateway_unreachable",
        "unauthorized",
        "adapter_exception",
        "ffprobe_missing",
        "no_stream_url",
        "no_stream_configured",
    }
)

#: The camera is registered but no live feed is currently attached. Under
#: on-demand publishing this is the NORMAL state for most of the estate — a
#: feed is pulled only while someone is watching — so it is not a fault.
NOT_INTEGRATED_ERRORS = frozenset({"not_publishing", "stream_not_ready"})


@dataclass(slots=True)
class HealthProbe:
    """The result of probing one camera.

    Carries enough detail to distinguish *why* a camera is unhealthy, because
    "offline" and "reachable but dropping 40% of frames" need different
    responses from a control room.
    """

    reachable: bool
    latency_ms: int | None = None
    fps_actual: float | None = None
    bitrate_kbps: int | None = None
    frame_drop_pct: float | None = None
    error_code: str | None = None
    detail: str | None = None
    probed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def status(self) -> CameraStatus:
        """Map the probe onto a fleet status.

        Degraded is a first-class state, not a rounding of "online". A camera
        delivering 4 fps when it should deliver 25 will produce poor ANPR
        results, and an operator needs to know that before they trust a
        negative result from it.
        """
        if not self.reachable:
            # Distinguish three genuinely different situations that a single
            # "offline" would collapse into one misleading state.
            if self.error_code in INTEGRATION_ERRORS:
                return CameraStatus.UNKNOWN
            if self.error_code in NOT_INTEGRATED_ERRORS:
                return CameraStatus.UNKNOWN
            return CameraStatus.OFFLINE
        if self.frame_drop_pct is not None and self.frame_drop_pct > 15.0:
            return CameraStatus.DEGRADED
        if self.fps_actual is not None and self.fps_actual < 5.0:
            return CameraStatus.DEGRADED
        if self.latency_ms is not None and self.latency_ms > 4000:
            return CameraStatus.DEGRADED
        return CameraStatus.ONLINE


@dataclass(slots=True)
class DiscoveredCamera:
    """A camera as reported by a federated VMS.

    Deliberately loose: every vendor names things differently, and the adapter's
    job is to normalise into this shape. ``raw`` keeps the original payload so
    nothing is lost when a vendor exposes a field we do not model yet.
    """

    external_id: str
    name: str
    lat: float | None = None
    lon: float | None = None
    stream_url: str | None = None
    sub_stream_url: str | None = None
    resolution: str | None = None
    fps: int | None = None
    codec: str | None = None
    is_live: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StreamEndpoints:
    """Where a camera's video can actually be watched."""

    rtsp: str | None = None
    whep: str | None = None
    hls: str | None = None


class AdapterError(Exception):
    """Raised when an adapter cannot complete an operation."""


class CameraAdapter(abc.ABC):
    """Base class for every integration strategy.

    One instance per VMS instance. Adapters are constructed by the registry
    from a ``vms_instances`` row, so credentials are resolved from the secret
    store the row *points* at — never from the row itself.
    """

    #: Value of ``vms_instances.adapter_type`` this class handles.
    adapter_type: str = ""

    def __init__(
        self,
        *,
        vms_id: uuid.UUID | None = None,
        name: str = "unnamed",
        base_url: str | None = None,
        credentials_ref: str | None = None,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.vms_id = vms_id
        self.name = name
        self.base_url = (base_url or "").rstrip("/")
        self.credentials_ref = credentials_ref
        self.timeout_seconds = timeout_seconds
        self.log = log.bind(adapter=self.adapter_type, vms=name)

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def connect(self) -> bool:
        """Establish or verify a session with the VMS.

        Default implementation reports success for adapters that are
        connectionless (direct RTSP). Adapters holding a token override this.
        """
        return True

    async def close(self) -> None:
        """Release any held resources. Safe to call repeatedly."""
        return

    # ── Required capabilities ─────────────────────────────────────────

    @abc.abstractmethod
    async def probe_health(self, camera: Any) -> HealthProbe:
        """Check whether one camera is delivering usable video."""

    @abc.abstractmethod
    async def get_stream_url(self, camera: Any) -> StreamEndpoints:
        """Resolve where this camera can be watched, right now.

        Resolved on demand rather than stored, because vendor URLs frequently
        carry short-lived session tokens — and because pulling only when
        someone is watching is the whole bandwidth argument.
        """

    async def list_cameras(self) -> list[DiscoveredCamera]:
        """Enumerate cameras this VMS knows about.

        Adapters that cannot enumerate (a bare RTSP URL describes exactly one
        camera) return an empty list rather than raising.
        """
        return []

    async def get_recording(self, camera: Any, start: datetime, end: datetime) -> str | None:
        """Resolve a playback URL for a past time range.

        Recordings stay with the owning department's VMS — this platform is a
        viewing and analytics tier, not a recorder. Returns None when the
        vendor exposes no playback API.
        """
        return None

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"
