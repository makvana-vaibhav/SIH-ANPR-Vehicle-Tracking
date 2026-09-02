"""Adapter for the challenge's own camera grid (sentinel.gujarat.gov.in).

## Two hosts, not one

The single most important thing about this grid is that **media and metadata
live on different hosts**, and the integrator guide is explicit about why: HLS
is served through a CDN, while "RTSP & WebRTC/WHEP carry media over TCP/UDP
that a CDN cannot proxy", so those are published on a direct static IP.

    catalogue   https://cctv.corp8.cloud/cameras.json     (CDN, password)
    HLS         https://cctv.corp8.cloud/<id>/index.m3u8  (CDN, password)
    RTSP        rtsp://103.250.160.189:8554/stream/<id>   (direct)
    WHEP        http://103.250.160.189:8889/stream/<id>/whep (direct)

An earlier version of this adapter derived all four from one hostname. That
produced `rtsp://<cdn-host>:8554/...`, which cannot work and did not — the port
appeared closed and the grid was written off as RTSP-blocked for weeks. The two
hosts are therefore modelled separately and deliberately.

## Everything else is treated as unstable

The guide says the catalogue is the source of truth and that "camera ids and
the set of available cameras can change", and publishes no field schema. So
this adapter:

* reads every URL from the catalogue where the catalogue provides one, and only
  falls back to constructing URLs from the documented pattern when it does not;
* tolerates unknown and renamed fields rather than requiring an exact schema;
* re-syncs the whole list instead of assuming ids are stable.

Notably, our MediaMTX gateway uses the same ports (8554 RTSP, 8889 WHEP), so
their grid drops straight in as another federated source.
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
from app.core.config import settings

#: Documented catalogue endpoint. `/api/ingest` was the earlier form and is
#: still tried, because the guide has changed once already and a grid that has
#: moved is better than a grid we cannot see.
CATALOGUE_PATHS = ("/cameras.json", "/api/ingest")

#: Documented stream ports, on the media host rather than the CDN.
RTSP_PORT = 8554
WHEP_PORT = 8889

#: Where RTSP and WHEP are actually served. Overridable because a static IP is
#: exactly the kind of thing that changes, and hard-coding it into the adapter
#: would mean a code change to follow it.
DEFAULT_MEDIA_HOST = "103.250.160.189"


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

    async def _fetch_catalogue(self, http_timeout: float) -> tuple[Any | None, str, str]:
        """Read the catalogue, trying each documented path in turn.

        The path has changed once already (`/api/ingest` → `/cameras.json`).
        Trying both costs one extra request on a grid that has moved and
        nothing at all on one that has not, which is a better trade than
        needing a redeploy to follow a URL change mid-event.

        A redirect to a login page is reported as what it is. It arrives as a
        200 carrying HTML, and a caller told "the catalogue is unreachable"
        would go looking for a network fault instead of a password.
        """
        last_error = "no catalogue path configured"
        attempted = ""
        for path in CATALOGUE_PATHS:
            attempted = f"{self.base_url.rstrip('/')}{path}"
            try:
                async with httpx.AsyncClient(timeout=http_timeout, follow_redirects=True) as client:
                    response = await client.get(attempted)
                    response.raise_for_status()
                    if "json" not in response.headers.get("content-type", ""):
                        last_error = (
                            "the catalogue returned a page rather than JSON — the "
                            "grid is behind a login and this deployment has no "
                            "session for it"
                        )
                        continue
                    return response.json(), attempted, ""
            except (httpx.HTTPError, ValueError) as exc:
                last_error = str(exc)
        return None, attempted, last_error

    def _media_host(self) -> str:
        """Where RTSP and WHEP are served — not the CDN.

        A CDN terminates HTTP; it cannot carry an RTSP session or a WebRTC
        media flow. Pointing these at the catalogue host yields a port that
        looks closed, which is a very convincing way to conclude a working
        grid is unreachable.
        """
        return settings.sandbox_media_host or DEFAULT_MEDIA_HOST

    def _endpoints_for(self, external_id: str) -> StreamEndpoints:
        """Construct the documented URL forms for a camera id."""
        cdn = self._host()
        media = self._media_host()
        scheme = "https" if self.base_url.startswith("https") else "http"
        return StreamEndpoints(
            rtsp=f"rtsp://{media}:{RTSP_PORT}/stream/{external_id}",
            # WHEP is plain HTTP on the direct host: it is not behind the CDN,
            # so there is no certificate for it to present.
            whep=f"http://{media}:{WHEP_PORT}/stream/{external_id}/whep",
            hls=f"{scheme}://{cdn}/{external_id}/index.m3u8",
        )

    async def list_cameras(self) -> list[DiscoveredCamera]:
        """Read the catalogue.

        Parsed defensively: the guide does not publish a field schema, so every
        value is looked up across candidate names and anything unrecognised is
        preserved in ``raw`` rather than dropped.
        """
        payload, url, error = await self._fetch_catalogue(self.timeout_seconds * 4)
        if payload is None:
            raise AdapterError(f"Sandbox catalogue unreachable at {url}: {error}")

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

        payload, _url, error = await self._fetch_catalogue(self.timeout_seconds)
        if payload is None:
            return HealthProbe(
                reachable=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="sandbox_unreachable",
                detail=error,
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


#: Tag carrying the grid's own id for a camera, written at sync time.
GRID_ID_TAG = "grid-id:"


def _external_id_of(camera: Any) -> str:
    """The grid's id for a registered camera.

    Read from a `grid-id:` tag written when the camera was synced, because the
    grid's id format is the grid's business and has already changed once —
    from bare numbers (`7`) to prefixed ones (`cam07`). The integrator guide
    says as much: "start from the catalogue rather than hard-coding".

    The numeric fallback exists only for rows synced before the tag was
    introduced. It reproduces the *old* format, which is wrong against the
    current grid, so it is a way to fail visibly rather than a way to work.
    """
    for tag in getattr(camera, "tags", None) or ():
        if isinstance(tag, str) and tag.startswith(GRID_ID_TAG):
            stored = tag[len(GRID_ID_TAG) :].strip()
            if stored:
                return stored

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
