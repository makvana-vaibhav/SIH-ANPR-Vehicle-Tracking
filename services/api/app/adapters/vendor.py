"""Generic vendor-VMS federation over REST.

This is Model 3 in practice. Departmental VMS platforms (Milestone, Genetec,
CP Plus, Hikvision) all expose broadly the same *concepts* — authenticate, list
cameras, resolve a stream, fetch a recording — behind entirely different URL
shapes and payloads.

Rather than one class per vendor, this adapter is driven by a small field map,
so onboarding a new vendor is configuration rather than code. Where a vendor
needs genuinely different behaviour, subclass and override just that method.

Credentials come from the secret store that ``credentials_ref`` points at. The
VMS row itself never contains a password.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from app.adapters.base import (
    AdapterError,
    CameraAdapter,
    DiscoveredCamera,
    HealthProbe,
    StreamEndpoints,
)
from app.core.config import settings
from app.services.secrets import resolve_credentials

#: Candidate key names for each concept, tried in order. Real vendor payloads
#: disagree about almost every field name, and being liberal here is what makes
#: one adapter cover four vendors.
_FIELD_CANDIDATES: dict[str, tuple[str, ...]] = {
    "id": ("id", "camera_id", "cameraId", "guid", "deviceId", "device_id", "uuid"),
    "name": ("name", "displayName", "camera_name", "title", "label", "description"),
    "lat": ("lat", "latitude", "gps_lat", "y"),
    "lon": ("lon", "lng", "longitude", "gps_lon", "x"),
    "stream": ("rtsp_url", "rtspUrl", "stream_url", "streamUrl", "url", "uri", "rtsp"),
    "substream": ("sub_stream_url", "subStreamUrl", "low_url", "secondary_url"),
    "resolution": ("resolution", "video_resolution"),
    "fps": ("fps", "frame_rate", "frameRate"),
    "codec": ("codec", "video_codec", "encoding"),
    "live": ("is_live", "live", "online", "status", "state", "enabled"),
}

#: Where a list of cameras might live inside a response envelope.
_LIST_CANDIDATES = ("cameras", "items", "data", "results", "devices", "records", "list")


def pick(payload: dict[str, Any], concept: str) -> Any:
    """Return the first present value among the candidate keys for a concept."""
    for key in _FIELD_CANDIDATES.get(concept, ()):
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def extract_list(payload: Any) -> list[dict[str, Any]]:
    """Find the camera list inside an arbitrary response envelope.

    Vendors return a bare array, or wrap it in ``data``/``items``/``cameras``,
    sometimes two levels deep. Rather than fail on an unexpected shape, search
    for the first list of objects.
    """
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if isinstance(payload, dict):
        for key in _LIST_CANDIDATES:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        # Fall back to any list-of-dicts value at the top level.
        for value in payload.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def as_bool(value: Any) -> bool | None:
    """Interpret the many ways a vendor says 'this camera is up'."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "online", "up", "active", "ok", "1", "connected", "live"):
            return True
        if lowered in ("false", "offline", "down", "inactive", "0", "disconnected"):
            return False
    return None


class VendorVmsAdapter(CameraAdapter):
    """Federates a departmental VMS exposing a REST API."""

    adapter_type = "vendor_api"

    #: Overridable per vendor.
    auth_path = "/auth/login"
    cameras_path = "/cameras"
    stream_path_template = "/cameras/{external_id}/stream"
    playback_path_template = "/cameras/{external_id}/playback"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    async def connect(self) -> bool:
        """Authenticate and cache a session token."""
        if self._token and time.monotonic() < self._token_expires_at:
            return True
        if not self.base_url:
            raise AdapterError(f"VMS '{self.name}' has no base_url configured")

        credentials = resolve_credentials(self.credentials_ref)
        if credentials is None:
            # No credential available: many departmental VMS in a pilot are
            # open on an internal VLAN. Proceed unauthenticated rather than
            # refusing, and let the request itself fail if auth is required.
            self.log.info("vendor.no_credentials", credentials_ref=self.credentials_ref)
            return True

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.base_url}{self.auth_path}",
                    json={
                        "username": credentials.username,
                        "password": credentials.password,
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise AdapterError(f"VMS '{self.name}' authentication failed: {exc}") from exc
        except ValueError as exc:
            raise AdapterError(f"VMS '{self.name}' returned non-JSON auth response") from exc

        self._token = (
            payload.get("token")
            or payload.get("access_token")
            or payload.get("sessionId")
            or payload.get("session_token")
        )
        if not self._token:
            raise AdapterError(f"VMS '{self.name}' auth response contained no token")

        # Refresh a minute before expiry; default to 15 minutes if unstated.
        ttl = float(payload.get("expires_in", 900))
        self._token_expires_at = time.monotonic() + max(ttl - 60, 60)
        return True

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def list_cameras(self) -> list[DiscoveredCamera]:
        """Sync the camera list from the VMS."""
        await self.connect()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds * 3) as client:
                response = await client.get(
                    f"{self.base_url}{self.cameras_path}", headers=self._headers()
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise AdapterError(f"VMS '{self.name}' camera list failed: {exc}") from exc
        except ValueError as exc:
            raise AdapterError(f"VMS '{self.name}' returned non-JSON camera list") from exc

        return [self.to_discovered(item) for item in extract_list(payload)]

    def to_discovered(self, item: dict[str, Any]) -> DiscoveredCamera:
        """Normalise one vendor record. Override for an awkward vendor."""
        return DiscoveredCamera(
            external_id=str(pick(item, "id") or ""),
            name=str(pick(item, "name") or "Unnamed camera"),
            lat=as_float(pick(item, "lat")),
            lon=as_float(pick(item, "lon")),
            stream_url=pick(item, "stream"),
            sub_stream_url=pick(item, "substream"),
            resolution=pick(item, "resolution"),
            fps=int(as_float(pick(item, "fps")) or 0) or None,
            codec=pick(item, "codec"),
            is_live=as_bool(pick(item, "live")),
            raw=item,
        )

    async def probe_health(self, camera: Any) -> HealthProbe:
        """Ask the VMS about this camera's state.

        Federation means trusting the owning system's own view of its cameras
        where it offers one — it knows things we cannot see from outside, such
        as recording state and disk health.
        """
        external_id = getattr(camera, "camera_code", None)
        started = time.perf_counter()

        try:
            await self.connect()
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    f"{self.base_url}{self.cameras_path}/{external_id}",
                    headers=self._headers(),
                )
        except (AdapterError, httpx.HTTPError) as exc:
            return HealthProbe(
                reachable=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="vms_unreachable",
                detail=str(exc),
            )

        latency_ms = int((time.perf_counter() - started) * 1000)

        if response.status_code == 404:
            # The VMS no longer knows this camera — it was decommissioned or
            # moved. That is a registry problem, not a network problem, and the
            # distinct code makes it visible in the fleet view.
            return HealthProbe(
                reachable=False,
                latency_ms=latency_ms,
                error_code="unknown_to_vms",
                detail="Camera is not present in the VMS camera list",
            )
        if response.status_code in (401, 403):
            return HealthProbe(
                reachable=False,
                latency_ms=latency_ms,
                error_code="unauthorized",
                detail="VMS rejected our credentials",
            )
        if response.status_code >= 400:
            return HealthProbe(
                reachable=False,
                latency_ms=latency_ms,
                error_code="vms_error",
                detail=f"VMS returned HTTP {response.status_code}",
            )

        try:
            payload = response.json()
        except ValueError:
            return HealthProbe(
                reachable=False, latency_ms=latency_ms, error_code="bad_vms_response"
            )

        live = as_bool(pick(payload, "live"))
        return HealthProbe(
            reachable=live if live is not None else True,
            latency_ms=latency_ms,
            fps_actual=as_float(pick(payload, "fps")),
            error_code=None if live is not False else "reported_offline",
            detail=f"Reported by {self.name}",
        )

    async def get_stream_url(self, camera: Any) -> StreamEndpoints:
        """Resolve a viewing URL, preferring the VMS's own answer.

        Vendor stream URLs often carry short-lived session tokens, which is
        exactly why this is resolved per request rather than stored.
        """
        external_id = getattr(camera, "camera_code", "unknown")
        code = str(external_id).lower()
        gateway = StreamEndpoints(
            rtsp=f"rtsp://{settings.mediamtx_host}:{settings.mediamtx_rtsp_port}/{code}",
            whep=f"{settings.mediamtx_public_webrtc_url}/{code}/whep",
            hls=f"{settings.mediamtx_public_hls_url}/{code}/index.m3u8",
        )

        if not self.base_url:
            return gateway

        try:
            await self.connect()
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    f"{self.base_url}{self.stream_path_template.format(external_id=external_id)}",
                    headers=self._headers(),
                )
                response.raise_for_status()
                payload = response.json()
        except (AdapterError, httpx.HTTPError, ValueError) as exc:
            # Fall back to the gateway rather than failing the request: the
            # simulator or a cached path may still serve this camera.
            self.log.info("vendor.stream_resolve_failed", error=str(exc))
            return gateway

        return StreamEndpoints(
            rtsp=pick(payload, "stream") or gateway.rtsp,
            whep=payload.get("whep") or gateway.whep,
            hls=payload.get("hls") or gateway.hls,
        )

    async def get_recording(self, camera: Any, start: datetime, end: datetime) -> str | None:
        """Resolve a playback URL from the owning VMS.

        The department keeps its recordings; we only ask where they are. This
        is the heart of the federation argument — we never copy their video.
        """
        external_id = getattr(camera, "camera_code", "unknown")
        try:
            await self.connect()
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(
                    f"{self.base_url}{self.playback_path_template.format(external_id=external_id)}",
                    params={"start": start.isoformat(), "end": end.isoformat()},
                    headers=self._headers(),
                )
                response.raise_for_status()
                return pick(response.json(), "stream")
        except (AdapterError, httpx.HTTPError, ValueError) as exc:
            self.log.info("vendor.playback_unavailable", error=str(exc))
            return None
