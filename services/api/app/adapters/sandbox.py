"""Adapter for the challenge's own camera grid (sentinel.gujarat.gov.in).

The organisers publish a catalogue at ``GET /api/ingest`` and serve every
camera over three paths:

    rtsp://<host>:8554/stream/<id>
    http://<host>:8889/stream/<id>/whep
    http://<host>/live/stream/<id>/index.m3u8

Their integrator guide is explicit that the catalogue is the source of truth and
that "camera ids and the set of available cameras can change", and it does not
publish a fixed field schema. This adapter therefore:

* reads every URL from the catalogue where the catalogue provides one, and only
  falls back to constructing URLs from the documented pattern when it does not;
* tolerates unknown and renamed fields rather than requiring an exact schema;
* re-syncs the whole list instead of assuming ids are stable.

Notably, our MediaMTX gateway already uses the same ports (8554 RTSP, 8889
WHEP), so their grid drops straight in as another federated source.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.adapters.base import (
    AdapterError,
    CameraAdapter,
    DiscoveredCamera,
    HealthProbe,
    StreamEndpoints,
)
from app.adapters.vendor import as_bool, as_float, extract_list, pick

#: Documented catalogue endpoint.
CATALOGUE_PATH = "/api/ingest"

#: Documented stream ports.
RTSP_PORT = 8554
WHEP_PORT = 8889


class SentinelSandboxAdapter(CameraAdapter):
    """Federates the challenge sandbox camera grid."""

    adapter_type = "sentinel_sandbox"

    def _host(self) -> str:
        """Bare hostname of the sandbox, without scheme or port."""
        parsed = urlparse(self.base_url if "://" in self.base_url else f"//{self.base_url}")
        return (
            parsed.hostname
            or self.base_url.replace("https://", "").replace("http://", "").split("/")[0]
        )

    def _endpoints_for(self, external_id: str) -> StreamEndpoints:
        """Construct the documented URL forms for a camera id."""
        host = self._host()
        scheme = "https" if self.base_url.startswith("https") else "http"
        return StreamEndpoints(
            rtsp=f"rtsp://{host}:{RTSP_PORT}/stream/{external_id}",
            whep=f"{scheme}://{host}:{WHEP_PORT}/stream/{external_id}/whep",
            hls=f"{scheme}://{host}/live/stream/{external_id}/index.m3u8",
        )

    async def list_cameras(self) -> list[DiscoveredCamera]:
        """Read the catalogue.

        Parsed defensively: the guide does not publish a field schema, so every
        value is looked up across candidate names and anything unrecognised is
        preserved in ``raw`` rather than dropped.
        """
        url = f"{self.base_url}{CATALOGUE_PATH}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds * 4) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise AdapterError(f"Sandbox catalogue unreachable at {url}: {exc}") from exc
        except ValueError as exc:
            raise AdapterError(f"Sandbox catalogue returned non-JSON: {exc}") from exc

        cameras: list[DiscoveredCamera] = []
        for item in extract_list(payload):
            external_id = str(pick(item, "id") or "").strip()
            if not external_id:
                continue

            constructed = self._endpoints_for(external_id)

            # Prefer URLs the catalogue actually gives us; the documented
            # pattern is the fallback, not the assumption.
            rtsp = pick(item, "stream") or item.get("rtsp") or constructed.rtsp
            hls = item.get("hls") or item.get("hls_url") or constructed.hls
            whep = item.get("whep") or item.get("webrtc") or constructed.whep

            lat, lon = _extract_location(item)

            cameras.append(
                DiscoveredCamera(
                    external_id=external_id,
                    name=_camera_name(item, external_id),
                    lat=lat,
                    lon=lon,
                    stream_url=rtsp,
                    sub_stream_url=None,
                    resolution=_extract_resolution(item),
                    fps=int(as_float(pick(item, "fps")) or 0) or None,
                    codec=pick(item, "codec"),
                    is_live=as_bool(pick(item, "live")),
                    raw={**item, "_hls": hls, "_whep": whep},
                )
            )

        self.log.info("sandbox.catalogue_synced", cameras=len(cameras), url=url)
        return cameras

    async def probe_health(self, camera: Any) -> HealthProbe:
        """Check a camera's liveness via the catalogue.

        The catalogue carries a live status per camera, which is cheaper and
        kinder to the sandbox than opening an RTSP session per camera per
        probe cycle — at 30+ cameras every 30 seconds that would be a
        meaningful load on infrastructure we do not own.
        """
        external_id = _external_id_of(camera)
        started = time.perf_counter()

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(f"{self.base_url}{CATALOGUE_PATH}")
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return HealthProbe(
                reachable=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="sandbox_unreachable",
                detail=str(exc),
            )

        latency_ms = int((time.perf_counter() - started) * 1000)

        for item in extract_list(payload):
            if str(pick(item, "id") or "") != external_id:
                continue
            live = as_bool(pick(item, "live"))
            return HealthProbe(
                reachable=live if live is not None else True,
                latency_ms=latency_ms,
                fps_actual=as_float(pick(item, "fps")),
                error_code=None if live is not False else "reported_offline",
                detail=f"codec={pick(item, 'codec') or 'unknown'}",
            )

        # The guide warns the set of cameras can change under us.
        return HealthProbe(
            reachable=False,
            latency_ms=latency_ms,
            error_code="unknown_to_vms",
            detail="Camera id is no longer present in the sandbox catalogue",
        )

    async def get_stream_url(self, camera: Any) -> StreamEndpoints:
        """Return the sandbox's own stream endpoints for this camera."""
        return self._endpoints_for(_external_id_of(camera))


def _external_id_of(camera: Any) -> str:
    """The sandbox id for a registered camera.

    Our codes look like ``SBX-00007``; the sandbox uses bare numeric ids. The
    numeric suffix is the link between them.
    """
    code = str(getattr(camera, "camera_code", "") or "")
    tail = code.rsplit("-", 1)[-1] if "-" in code else code
    return tail.lstrip("0") or tail or code


def _extract_location(item: dict[str, Any]) -> tuple[float | None, float | None]:
    """Pull coordinates out of whatever shape ``location`` arrives in.

    Seen in the wild: flat lat/lon fields, a nested object, or a GeoJSON-style
    [lon, lat] array. All three are handled.
    """
    lat, lon = as_float(pick(item, "lat")), as_float(pick(item, "lon"))
    if lat is not None and lon is not None:
        return lat, lon

    location = item.get("location") or item.get("geo") or item.get("coordinates")

    if isinstance(location, dict):
        return as_float(pick(location, "lat")), as_float(pick(location, "lon"))

    if isinstance(location, list | tuple) and len(location) >= 2:
        first, second = as_float(location[0]), as_float(location[1])
        if first is not None and second is not None:
            # GeoJSON order is [longitude, latitude]. Latitude cannot exceed
            # 90, so a first element beyond that confirms the standard order;
            # otherwise assume it too, since GeoJSON is what the array implies.
            return second, first
    return None, None


def _camera_name(item: dict[str, Any], external_id: str) -> str:
    """The most useful human label available.

    The grid's ``name`` field is "Camera 1"; its ``location`` field is
    "01 Chiman bhai Bridge". An operator looking at an alert needs the second.
    Both are kept — ``location`` becomes the name and the raw payload preserves
    everything — but the useful one is what gets displayed.
    """
    # Read directly rather than through pick(): "location" is not one of the
    # generic vendor concepts, and this grid's own field name is known.
    location = str(item.get("location") or item.get("site") or "").strip()
    if location:
        # The grid prefixes many locations with their own index ("01 Janpath").
        # Harmless, but the camera code already carries the id.
        cleaned = location.lstrip("0123456789 -_.").strip()
        return cleaned or location
    name = str(pick(item, "name") or "").strip()
    return name or f"Sandbox camera {external_id}"


def _extract_resolution(item: dict[str, Any]) -> str | None:
    """Normalise resolution, which may be a string or width/height fields."""
    direct = pick(item, "resolution")
    if isinstance(direct, str) and "x" in direct:
        return direct

    width = item.get("width") or item.get("video_width")
    height = item.get("height") or item.get("video_height")
    if width and height:
        return f"{int(width)}x{int(height)}"
    return None
