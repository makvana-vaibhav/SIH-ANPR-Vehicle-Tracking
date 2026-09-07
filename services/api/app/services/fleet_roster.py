"""Publishes the ANPR fleet to Redis, for the AI workers to read.

## Why this exists

The workers need to know which cameras to analyse and where to pull each one
from. Three ways to tell them were possible, and two are worse:

* **Read the database directly.** The worker image is deliberately minimal —
  ONNX Runtime and OpenCV, no ORM, no Postgres driver (see CLAUDE.md §9 note 3).
  Adding SQLAlchemy to it to answer one question would undo that.
* **Call the API.** That means a service credential, a new authentication path,
  and a worker that cannot start until the API is up.

So the API — which already owns the registry — writes the roster to Redis,
which both sides already depend on. No new dependency, no new auth surface, and
a worker that keeps running off the last roster if the API restarts.

## Why not discover from MediaMTX alone

The original design discovered cameras from MediaMTX, on the reasoning that it
knows which paths are *actually publishing*. That is true and useful for
cameras we host. It cannot see a federated camera at all: the organisers' grid
publishes to their gateway, not ours. A worker discovering only from MediaMTX
is therefore structurally blind to exactly the cameras this platform exists to
federate.

The registry knows about both, and — importantly — keeps knowing about a
federated camera while its grid is returning 502. A camera that is unreachable
is a camera with a problem, not a camera that stopped existing, and the
difference belongs in health monitoring rather than in whether we try at all.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.adapters import adapter_for
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.registry import Camera

log = get_logger(__name__)

#: Where the roster lives. A plain key, not a stream: this is current state,
#: not a history, and a worker joining late wants the roster as it is now.
ROSTER_KEY = "nagarnetra:fleet:anpr"

#: Republish this often. Fast enough that a newly onboarded camera is picked up
#: within a discovery cycle or two, slow enough to be irrelevant to load.
PUBLISH_INTERVAL_S = 30.0

#: The roster outlives a few missed publishes, so a brief API outage does not
#: idle every worker, but not so long that a dead API leaves workers chasing a
#: fleet that has since changed.
ROSTER_TTL_S = 300


def plate_regions(camera: Camera) -> list[str]:
    """Which national plate formats this camera is expected to see.

    Indian always, plus anything the camera declares with a `plate-region:XX`
    tag. Accepting a format the camera will never see is not free: the repair
    step will bend a genuine misread into a plausible plate of that format, so
    the default stays narrow and widening it is a deliberate, per-camera act.
    """
    extra = [
        tag.split(":", 1)[1].upper()
        for tag in (camera.tags or [])
        if tag.lower().startswith("plate-region:") and ":" in tag
    ]
    return ["IN", *[r for r in extra if r != "IN"]]


async def _entry_for(camera: Camera) -> dict[str, Any] | None:
    """One camera as the worker needs it: where to pull, and how."""
    adapter = adapter_for(camera.vms)
    try:
        endpoints = await adapter.get_stream_url(camera)
    except Exception as exc:
        # A camera whose URL cannot be resolved right now is still a camera.
        # It is published without a URL so the worker can report it as
        # unreachable rather than silently omitting it from the fleet.
        log.warning(
            "roster.resolve_failed",
            camera_code=camera.camera_code,
            error=str(exc),
        )
        return {
            "camera_code": camera.camera_code,
            "label": camera.name,
            "rtsp_url": "",
            "hls_url": "",
            "plate_regions": plate_regions(camera),
            "federated": True,
            "detail": str(exc),
        }
    finally:
        await adapter.close()

    # Both URLs travel, and the worker chooses. Which transport works is a
    # property of the *worker's* network, not of the registry: the organisers'
    # guide says as much ("if port 8554 is blocked, use the HLS endpoint"), and
    # picking here would bake one worker's firewall into every worker's roster.
    return {
        "camera_code": camera.camera_code,
        "label": camera.name,
        "rtsp_url": endpoints.rtsp or "",
        "hls_url": endpoints.hls or "",
        "plate_regions": plate_regions(camera),
        # Whether the video comes from somebody else's gateway. The worker uses
        # this only for logging; it matters to an operator reading the logs.
        "federated": (camera.vms.adapter_type != "simulated") if camera.vms else False,
    }


async def build_roster() -> list[dict[str, Any]]:
    """Every ANPR-enabled camera, with a resolved stream URL where possible."""
    async with SessionLocal() as session:
        cameras = (
            (
                await session.execute(
                    select(Camera)
                    .options(selectinload(Camera.vms))
                    .where(Camera.anpr_enabled.is_(True))
                    .order_by(Camera.camera_code)
                )
            )
            .scalars()
            .all()
        )

    # Resolving is I/O per camera; doing it serially across a large fleet would
    # take longer than the publish interval.
    entries = await asyncio.gather(*(_entry_for(camera) for camera in cameras))
    return [entry for entry in entries if entry is not None]


class RosterPublisher:
    """Keeps the Redis roster in step with the registry."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.published = 0
        self.last_size = 0

    async def publish_once(self) -> int:
        roster = await build_roster()
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            await client.set(
                ROSTER_KEY,
                json.dumps({"cameras": roster}),
                ex=ROSTER_TTL_S,
            )
        finally:
            await client.aclose()
        self.published += 1
        self.last_size = len(roster)
        return len(roster)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                count = await self.publish_once()
                log.debug("roster.published", cameras=count)
            except Exception:
                # A supervisor boundary. The roster has a TTL longer than the
                # interval, so a failed publish costs nothing until several in
                # a row fail — but it must be visible when it happens.
                log.warning("roster.publish_failed", exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=PUBLISH_INTERVAL_S)

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop(), name="fleet-roster")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


publisher = RosterPublisher()
