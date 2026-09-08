"""Camera simulator service.

Publishes a subset of the registered fleet as real RTSP streams into MediaMTX,
and exposes a small control API so a demo (or a test) can take a camera down on
purpose and watch the health monitor react.

Which cameras go live: ANPR-capable cameras on the demo route are preferred, so
the streams that exist are the ones the AI pipeline and the route demo actually
need. Everything else stays registered-but-not-streaming, which is realistic —
no control room watches 80,000 feeds at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, status
from sqlalchemy import select, true

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal, dispose_engine
from app.models.enums import AdapterType
from app.models.registry import Camera, VmsInstance
from simulator.publisher import StreamPublisher, StreamSpec, find_videos

configure_logging(service="simulator")
log = get_logger("simulator")

VIDEO_DIR = Path(os.environ.get("SIM_VIDEO_DIR", "/data/videos"))
# Pin particular footage to particular cameras: "CAM-00001=anpr_demo.mp4,...".
# Without this, clips are dealt round-robin and which camera shows what changes
# with the fleet size — fine for load, useless for a demonstration that has to
# say *which* junction a vehicle passed.
PINNED_VIDEOS = os.environ.get("SIM_CAMERA_VIDEOS", "")
STREAM_COUNT = int(os.environ.get("SIM_STREAM_COUNT", "6"))
RTSP_BASE = f"rtsp://{settings.mediamtx_host}:{settings.mediamtx_rtsp_port}"

#: Cities along the demo route (Judge Moment 4). Cameras here are preferred for
#: live streaming so the ANPR pipeline and the route reconstruction have real
#: video to work with.
DEMO_ROUTE_CITIES = ("Rajkot", "Gondal", "Jetpur", "Junagadh")

publisher = StreamPublisher(RTSP_BASE)
_supervisor_task: asyncio.Task[None] | None = None


async def select_cameras(limit: int) -> list[Camera]:
    """Choose which registered cameras become live streams.

    By default this is *only* the cameras with footage explicitly pinned to
    them — in practice the one demonstration camera.

    That default changed deliberately. The simulator used to replay clips into
    24 registered cameras, which made a synthetic feed indistinguishable from a
    federated one: a judge clicking "Kalawad Road Junction ANPR 01" saw a
    Wikimedia clip of a road in Israel. The platform federates real cameras;
    inventing video for the ones it cannot reach misrepresents exactly the
    capability being demonstrated.

    An unreachable camera should look unreachable. Set `SIM_STREAM_COUNT`
    above zero to restore fleet-wide replay for load testing, where synthetic
    video is the point rather than a pretence.
    """
    # Only stream cameras whose VMS is served by the simulator. A camera
    # belonging to the real sandbox grid must be probed against the real
    # sandbox — publishing a local test pattern for it would make the health
    # view lie about an integration we do not actually have.
    simulated_vms = (
        select(VmsInstance.id)
        .where(VmsInstance.adapter_type == AdapterType.SIMULATED.value)
        .scalar_subquery()
    )

    async with SessionLocal() as session:
        # A camera with footage pinned to it is published first, whatever the
        # limit. Pinning a clip to a camera the simulator then declines to
        # stream would be a silent no-op.
        pinned_codes = list(pinned_videos())
        pinned_cameras = (
            list(
                (
                    await session.scalars(
                        select(Camera)
                        .where(
                            Camera.camera_code.in_(pinned_codes),
                            Camera.vms_id.in_(simulated_vms),
                        )
                        .order_by(Camera.camera_code)
                    )
                ).all()
            )
            if pinned_codes
            else []
        )

        preferred = list(pinned_cameras)
        if limit <= 0:
            # The honest default: only cameras with real footage attached.
            return preferred

        preferred += list(
            (
                await session.scalars(
                    select(Camera)
                    .where(
                        Camera.anpr_enabled.is_(True),
                        Camera.vms_id.in_(simulated_vms),
                        Camera.city.in_(DEMO_ROUTE_CITIES),
                        Camera.id.not_in([c.id for c in pinned_cameras])
                        if pinned_cameras
                        else true(),
                    )
                    .order_by(Camera.camera_code)
                    .limit(limit)
                )
            ).all()
        )

        if len(preferred) >= limit:
            return preferred[:limit]

        # Top up with other ANPR cameras.
        remaining = limit - len(preferred)
        chosen_ids = [c.id for c in preferred]
        stmt = select(Camera).where(
            Camera.anpr_enabled.is_(True), Camera.vms_id.in_(simulated_vms)
        )
        if chosen_ids:
            stmt = stmt.where(Camera.id.not_in(chosen_ids))
        extra = list(
            (
                await session.scalars(
                    stmt.order_by(Camera.camera_code).limit(remaining)
                )
            ).all()
        )

    return preferred + extra


def pinned_videos() -> dict[str, Path]:
    """Camera code → clip, from SIM_CAMERA_VIDEOS. Unreadable entries are skipped."""
    pinned: dict[str, Path] = {}
    for pair in PINNED_VIDEOS.split(","):
        code, _, filename = pair.partition("=")
        if not code.strip() or not filename.strip():
            continue
        path = VIDEO_DIR / filename.strip()
        if not path.is_file():
            log.warning(
                "simulator.pinned_video_missing", camera=code.strip(), path=str(path)
            )
            continue
        pinned[code.strip().upper()] = path
    return pinned


def build_specs(cameras: list[Camera], videos: list[Path]) -> list[StreamSpec]:
    """Pair cameras with source clips, cycling if there are fewer clips."""
    pinned = pinned_videos()
    specs: list[StreamSpec] = []
    for index, camera in enumerate(cameras):
        source = pinned.get(camera.camera_code)
        if source is None:
            source = videos[index % len(videos)] if videos else None
        specs.append(
            StreamSpec(
                camera_code=camera.camera_code,
                source=source,
                label=f"{camera.camera_code} {camera.city or ''}".strip(),
                fps=camera.fps or 15,
            )
        )
    return specs


async def startup() -> None:
    """Bring the simulated fleet online."""
    global _supervisor_task

    if not StreamPublisher.ffmpeg_available():
        log.error(
            "simulator.ffmpeg_missing",
            detail="ffmpeg is not installed; no streams can be published",
        )
        return

    videos = find_videos(VIDEO_DIR)
    if videos:
        log.info("simulator.videos_found", count=len(videos), directory=str(VIDEO_DIR))
    else:
        # Not a failure: generated test patterns keep every downstream
        # component exercised until real clips are fetched with `make videos`.
        log.warning(
            "simulator.no_videos",
            directory=str(VIDEO_DIR),
            detail="Publishing generated test patterns instead; run `make videos` for real footage",
        )

    cameras = await select_cameras(STREAM_COUNT)
    if not cameras:
        log.warning(
            "simulator.no_cameras",
            detail="No cameras registered yet — run `make seed` then restart",
        )
        return

    specs = build_specs(cameras, videos)
    await publisher.start_all(specs)
    _supervisor_task = asyncio.create_task(publisher.supervise())


async def shutdown() -> None:
    """Stop every stream and release resources."""
    if _supervisor_task is not None:
        _supervisor_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _supervisor_task
    await publisher.stop_all()
    await dispose_engine()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):  # noqa: ANN201
    await startup()
    yield
    await shutdown()


app = FastAPI(
    title="NagarNetra Simulator",
    description=(
        "Synthetic camera fleet. Replays footage into MediaMTX as RTSP so the "
        "platform has live streams without hardware — the same technique the "
        "challenge sandbox uses."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", tags=["health"])
async def health() -> dict[str, Any]:
    """Liveness, plus which streams are currently publishing."""
    running = publisher.running()
    return {
        "status": "alive",
        "streams_running": len(running),
        "cameras": sorted(running),
        "ffmpeg": StreamPublisher.ffmpeg_available(),
        "video_dir": str(VIDEO_DIR),
        "videos_available": len(find_videos(VIDEO_DIR)),
    }


@app.get("/streams", tags=["streams"])
async def list_streams() -> dict[str, Any]:
    """Every stream this simulator is publishing."""
    running = publisher.running()
    return {
        "count": len(running),
        "rtsp_base": RTSP_BASE,
        "streams": [
            {
                "camera_code": code,
                "rtsp": f"{RTSP_BASE}/{code.lower()}",
                "whep": f"{settings.mediamtx_public_webrtc_url}/{code.lower()}/whep",
                "hls": f"{settings.mediamtx_public_hls_url}/{code.lower()}/index.m3u8",
            }
            for code in sorted(running)
        ],
    }


@app.post("/streams/{camera_code}/stop", tags=["streams"])
async def stop_stream(camera_code: str) -> dict[str, Any]:
    """Kill one stream on purpose.

    This is the Phase 3 verification hook: take a camera down and confirm the
    health monitor marks it offline and raises an alert. Also useful mid-demo
    to show failure handling on request.
    """
    stopped = await publisher.stop(camera_code.upper())
    if not stopped:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{camera_code} is not currently publishing",
        )
    return {
        "camera_code": camera_code.upper(),
        "stopped": True,
        "detail": "Stream killed. The health monitor should mark it offline "
        "within two probe cycles and raise a camera_down alert.",
    }


@app.post("/streams/{camera_code}/start", tags=["streams"])
async def start_stream(camera_code: str) -> dict[str, Any]:
    """Bring a stopped stream back up."""
    code = camera_code.upper()
    if code in publisher.running():
        return {"camera_code": code, "started": False, "detail": "Already publishing"}

    async with SessionLocal() as session:
        camera = await session.scalar(select(Camera).where(Camera.camera_code == code))
    if camera is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"{code} is not registered"
        )

    videos = find_videos(VIDEO_DIR)
    specs = build_specs([camera], videos)
    started = await publisher.start(specs[0])
    return {"camera_code": code, "started": started}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9100)  # noqa: S104
