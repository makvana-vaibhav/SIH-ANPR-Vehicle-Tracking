"""Direct RTSP integration.

The lowest common denominator: almost every IP camera and NVR speaks RTSP, so
this adapter covers cameras that have no VMS in front of them at all.

Health is probed with ``ffprobe``, which negotiates the RTSP session and reads
real stream parameters. That is a genuine end-to-end check — a camera can
answer a TCP connect on port 554 and still deliver no video, and a TCP probe
would call that healthy.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import time
from datetime import datetime
from typing import Any

from app.adapters.base import (
    CameraAdapter,
    DiscoveredCamera,
    HealthProbe,
    StreamEndpoints,
)
from app.core.config import settings

#: RTSP over TCP. UDP loses packets on congested municipal links and ffprobe
#: then reports corrupt streams for cameras that are actually fine.
_FFPROBE_TRANSPORT = "tcp"


class RtspAdapter(CameraAdapter):
    """Talks directly to a camera or NVR over RTSP."""

    adapter_type = "rtsp"

    @staticmethod
    def ffprobe_available() -> bool:
        """Whether ffprobe is on PATH.

        Checked rather than assumed: if the binary is missing the adapter
        reports a specific error code instead of silently marking the whole
        estate offline, which would look identical to a network outage.
        """
        return shutil.which("ffprobe") is not None

    async def probe_health(self, camera: Any) -> HealthProbe:
        """Negotiate an RTSP session and read the stream's real parameters."""
        url = getattr(camera, "sub_stream_url", None) or getattr(camera, "stream_url", None)
        if not url:
            return HealthProbe(
                reachable=False,
                error_code="no_stream_url",
                detail="Camera has no stream URL registered",
            )

        if not self.ffprobe_available():
            return HealthProbe(
                reachable=False,
                error_code="ffprobe_missing",
                detail="ffprobe is not installed in this image",
            )

        return await self._ffprobe(url)

    async def _ffprobe(self, url: str) -> HealthProbe:
        """Run ffprobe and translate its output into a HealthProbe."""
        command = (
            "ffprobe",
            "-v",
            "error",
            "-rtsp_transport",
            _FFPROBE_TRANSPORT,
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,bit_rate,width,height,codec_name",
            "-of",
            "json",
            "-timeout",
            str(int(self.timeout_seconds * 1_000_000)),  # microseconds
            url,
        )

        started = time.perf_counter()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds + 2
            )
        except TimeoutError:
            # Kill the child; an abandoned ffprobe holds an RTSP session open
            # on the camera, and cameras have very few session slots.
            with contextlib.suppress(ProcessLookupError, OSError):
                process.kill()
            return HealthProbe(
                reachable=False,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_code="timeout",
                detail=f"No response within {self.timeout_seconds}s",
            )
        except OSError as exc:
            return HealthProbe(reachable=False, error_code="spawn_failed", detail=str(exc))

        latency_ms = int((time.perf_counter() - started) * 1000)

        if process.returncode != 0:
            message = stderr.decode(errors="replace").strip().splitlines()
            return HealthProbe(
                reachable=False,
                latency_ms=latency_ms,
                error_code=_classify_ffprobe_error(message[-1] if message else ""),
                detail=message[-1] if message else "ffprobe failed",
            )

        try:
            streams = json.loads(stdout.decode()).get("streams", [])
        except (ValueError, UnicodeDecodeError) as exc:
            return HealthProbe(
                reachable=False,
                latency_ms=latency_ms,
                error_code="bad_probe_output",
                detail=str(exc),
            )

        if not streams:
            # Session negotiated but no video track — a real failure mode for
            # misconfigured encoders, and invisible to a TCP check.
            return HealthProbe(
                reachable=False,
                latency_ms=latency_ms,
                error_code="no_video_stream",
                detail="RTSP responded but exposes no video stream",
            )

        video = streams[0]
        return HealthProbe(
            reachable=True,
            latency_ms=latency_ms,
            fps_actual=_parse_frame_rate(video.get("avg_frame_rate")),
            bitrate_kbps=_parse_bitrate(video.get("bit_rate")),
            detail=f"{video.get('codec_name', '?')} "
            f"{video.get('width', '?')}x{video.get('height', '?')}",
        )

    async def get_stream_url(self, camera: Any) -> StreamEndpoints:
        """Direct RTSP cameras are republished through our gateway.

        The browser never receives the camera's own URL: it would be
        unreachable from outside the camera VLAN, and handing it out would
        bypass the audited token flow entirely.
        """
        code = getattr(camera, "camera_code", "unknown").lower()
        return StreamEndpoints(
            rtsp=f"rtsp://{settings.mediamtx_host}:{settings.mediamtx_rtsp_port}/{code}",
            whep=f"{settings.mediamtx_public_webrtc_url}/{code}/whep",
            hls=f"{settings.mediamtx_public_hls_url}/{code}/index.m3u8",
        )

    async def list_cameras(self) -> list[DiscoveredCamera]:
        """A bare RTSP URL describes one camera and cannot be enumerated."""
        return []

    async def get_recording(self, camera: Any, start: datetime, end: datetime) -> str | None:
        """Plain RTSP exposes no playback API — the NVR owns recordings."""
        return None


def _parse_frame_rate(value: str | None) -> float | None:
    """ffprobe reports frame rate as a rational string like '25/1'."""
    if not value or value in ("0/0", "N/A"):
        return None
    try:
        numerator, _, denominator = value.partition("/")
        den = float(denominator) if denominator else 1.0
        return round(float(numerator) / den, 2) if den else None
    except (ValueError, ZeroDivisionError):
        return None


def _parse_bitrate(value: str | None) -> int | None:
    """ffprobe reports bitrate in bits per second; we store kbps."""
    try:
        return int(value) // 1000 if value else None
    except (TypeError, ValueError):
        return None


def _classify_ffprobe_error(message: str) -> str:
    """Turn ffprobe's stderr into a stable, groupable error code.

    The fleet health view groups by error code, so an operator can see "31
    cameras unauthorised" (one expired credential rotated across a department)
    rather than 31 unique error strings.
    """
    lowered = message.lower()
    if "401" in lowered or "unauthorized" in lowered:
        return "unauthorized"
    if "404" in lowered or "not found" in lowered:
        return "stream_not_found"
    if "connection refused" in lowered:
        return "connection_refused"
    if "no route to host" in lowered or "unreachable" in lowered:
        return "unreachable"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "immediate exit" in lowered or "invalid data" in lowered:
        return "invalid_stream"
    return "probe_failed"
