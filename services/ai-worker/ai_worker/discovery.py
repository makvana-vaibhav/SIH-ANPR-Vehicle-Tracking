"""Which cameras this worker is responsible for.

Cameras are discovered from MediaMTX rather than from the database, and that is
a deliberate choice: MediaMTX knows which paths are *actually publishing right
now*, which is precisely the set worth spending inference on. A camera the
registry believes is online but which is not sending video would otherwise have
a worker sitting on it, opening a stream that never delivers a frame.

Ownership is decided by a stable hash of the camera code, so N workers split the
fleet with no coordinator, no assignment service, and no possibility of two
workers opening the same stream. Adding a worker rebalances; losing one leaves
its cameras unclaimed until it returns, which is visible rather than silent.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CameraStream:
    camera_code: str
    rtsp_url: str


def shard_of(camera_code: str, shards: int) -> int:
    """Stable shard for a camera code.

    A hash of the code, not a position in a list: positional assignment
    reshuffles every camera whenever one is added or removed, which would tear
    down and rebuild working streams for no reason.
    """
    if shards <= 1:
        return 0
    digest = hashlib.blake2b(camera_code.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % shards


def owns(camera_code: str, index: int, count: int) -> bool:
    return shard_of(camera_code, count) == index


async def publishing_paths(api_url: str, timeout: float = 5.0) -> list[str]:
    """Camera codes currently publishing to MediaMTX."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{api_url}/v3/paths/list")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("could not list MediaMTX paths: %s", exc)
        return []

    names: list[str] = []
    for item in payload.get("items", []):
        # `ready` means a publisher is connected and sending. A path can exist
        # without one, and opening that stream would block until it does.
        if item.get("ready") and item.get("name"):
            names.append(str(item["name"]))
    return names


async def discover(
    api_url: str,
    rtsp_host: str,
    rtsp_port: int,
    index: int,
    count: int,
    explicit: str = "",
    limit: int = 4,
) -> list[CameraStream]:
    """The streams this worker should be processing right now."""
    if explicit.strip():
        codes = [c.strip() for c in explicit.split(",") if c.strip()]
    else:
        codes = sorted(await publishing_paths(api_url))
        codes = [c for c in codes if owns(c, index, count)]

    if len(codes) > limit:
        # Taking the first N by sorted order is stable across rediscovery, so a
        # camera does not get picked up and dropped as the publishing set moves.
        log.warning(
            "%d cameras assigned to worker %d but the cap is %d; "
            "processing the first %d. Add workers to cover the rest.",
            len(codes), index, limit, limit,
        )
        codes = codes[:limit]

    return [
        CameraStream(camera_code=code, rtsp_url=f"rtsp://{rtsp_host}:{rtsp_port}/{code}")
        for code in codes
    ]
