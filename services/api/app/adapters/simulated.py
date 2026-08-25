"""Adapter for our own camera simulator.

This is what powers the local demo: the simulator replays sample footage into
MediaMTX as RTSP, so 250 "cameras" exist without any hardware. It is also how
the deliberately-killed-stream test in Phase 3 works — we can take a camera
down on purpose and watch the health monitor notice.

It queries MediaMTX's control API for ground truth about which paths are
actually publishing, which makes it a genuine health check rather than a
hardcoded "everything is fine".
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.adapters.base import (
    CameraAdapter,
    DiscoveredCamera,
    HealthProbe,
    StreamEndpoints,
)
from app.core.config import settings


class SimulatedVmsAdapter(CameraAdapter):
    """Talks to MediaMTX to report on simulated camera streams."""

    adapter_type = "simulated"

    def _path_for(self, camera: Any) -> str:
        """MediaMTX path name for a camera. Codes are lowercased."""
        return str(getattr(camera, "camera_code", "unknown")).lower()

    async def _paths(self) -> dict[str, dict[str, Any]]:
        """Fetch every active path from the MediaMTX control API."""
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(f"{settings.mediamtx_api_url}/v3/paths/list")
            response.raise_for_status()
            payload = response.json()
        return {item["name"]: item for item in payload.get("items", [])}

    async def probe_health(self, camera: Any) -> HealthProbe:
        """Report whether this camera's stream is actually publishing."""
        path_name = self._path_for(camera)
        started = time.perf_counter()

        try:
            paths = await self._paths()
        except (httpx.HTTPError, ValueError) as exc:
            return HealthProbe(
                reachable=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="gateway_unreachable",
                detail=f"MediaMTX control API: {exc}",
            )

        latency_ms = int((time.perf_counter() - started) * 1000)
        path = paths.get(path_name)

        if path is None:
            return HealthProbe(
                reachable=False,
                latency_ms=latency_ms,
                error_code="not_publishing",
                detail="No active stream for this camera on the gateway",
            )

        if not path.get("ready", False):
            # The path exists but has no source attached — exactly what a
            # camera that has dropped off looks like.
            return HealthProbe(
                reachable=False,
                latency_ms=latency_ms,
                error_code="stream_not_ready",
                detail="Gateway path exists but is not receiving video",
            )

        # MediaMTX reports bytes received; convert the rate into kbps.
        bytes_received = path.get("bytesReceived", 0)
        tracks = path.get("tracks", [])

        return HealthProbe(
            reachable=True,
            latency_ms=latency_ms,
            fps_actual=float(getattr(camera, "fps", None) or 25),
            bitrate_kbps=int(bytes_received * 8 / 1000) if bytes_received else None,
            detail=f"tracks={','.join(tracks) if tracks else 'none'}",
        )

    async def get_stream_url(self, camera: Any) -> StreamEndpoints:
        path_name = self._path_for(camera)
        return StreamEndpoints(
            rtsp=f"rtsp://{settings.mediamtx_host}:{settings.mediamtx_rtsp_port}/{path_name}",
            whep=f"{settings.mediamtx_public_webrtc_url}/{path_name}/whep",
            hls=f"{settings.mediamtx_public_hls_url}/{path_name}/index.m3u8",
        )

    async def list_cameras(self) -> list[DiscoveredCamera]:
        """Every path currently publishing on the gateway."""
        try:
            paths = await self._paths()
        except (httpx.HTTPError, ValueError) as exc:
            self.log.warning("simulated.list_failed", error=str(exc))
            return []

        return [
            DiscoveredCamera(
                external_id=name,
                name=f"Simulated {name.upper()}",
                stream_url=f"rtsp://{settings.mediamtx_host}:{settings.mediamtx_rtsp_port}/{name}",
                is_live=bool(item.get("ready")),
                raw=item,
            )
            for name, item in paths.items()
        ]
