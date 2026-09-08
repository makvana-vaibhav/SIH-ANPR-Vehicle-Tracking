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

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CameraStream:
    camera_code: str
    rtsp_url: str
    transport: str = "rtsp"
    label: str = ""
    #: National plate formats this camera is expected to see. Applied per
    #: camera rather than per worker, because a fleet can span regions and
    #: accepting a format a camera never sees invites the repair step to
    #: invent one.
    plate_regions: tuple[str, ...] = ("IN",)


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
            len(codes),
            index,
            limit,
            limit,
        )
        codes = codes[:limit]

    return [
        CameraStream(
            camera_code=code, rtsp_url=f"rtsp://{rtsp_host}:{rtsp_port}/{code}"
        )
        for code in codes
    ]


# ─────────────────────────────────────────────────────────────────────
# hosted grid grid
# ─────────────────────────────────────────────────────────────────────
async def sandbox_catalogue(base_url: str, timeout: float = 15.0) -> list[dict]:
    """The organisers' camera catalogue.

    Their integration guide is explicit that "the catalogue is the contract, the
    URL pattern is not", and that camera ids and the set of cameras can change.
    So the worker reads it directly rather than caching a list or deriving URLs
    from a template — and it needs no credentials, no database and no coupling
    to the platform to do so.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/ingest")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("could not read the sandbox catalogue: %s", exc)
        return []

    cameras = payload.get("cameras") if isinstance(payload, dict) else payload
    return [c for c in (cameras or []) if isinstance(c, dict)]


async def reachable_transport(base_url: str, sample: dict, timeout: float = 8.0) -> str:
    """Which transport this network can actually use.

    The guide anticipates exactly this: "If port 8554 is blocked on your network,
    use the HLS endpoint instead." Rather than assume RTSP and fail with thirty
    second timeouts on every camera, probe once and tell the operator which
    path was chosen.
    """
    host = (
        urlparse(base_url if "://" in base_url else f"//{base_url}").hostname
        or base_url
    )

    # RTSP first: lower latency, and what the guide recommends for inference.
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 8554), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        log.info("RTSP port 8554 is reachable; using RTSP over TCP")
        return "rtsp"
    except (OSError, asyncio.TimeoutError):
        log.info("RTSP port 8554 is not reachable from this network")

    hls = sample.get("hls_live_url") or ""
    if hls:
        url = hls if hls.startswith("http") else f"{base_url.rstrip('/')}{hls}"
        try:
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True
            ) as client:
                response = await client.get(url)
            if response.status_code == 200 and "#EXTM3U" in response.text:
                log.info("falling back to HLS over HTTPS, as the guide advises")
                return "hls"
        except httpx.HTTPError:
            pass

    log.warning("neither RTSP nor HLS is reachable; this worker cannot pull video")
    return "none"


async def discover_sandbox(
    base_url: str, index: int, count: int, limit: int = 4, transport: str | None = None
) -> list[CameraStream]:
    """Cameras from the sandbox grid that this worker owns."""
    cameras = await sandbox_catalogue(base_url)
    if not cameras:
        return []

    live = [c for c in cameras if c.get("live")]
    chosen = transport or await reachable_transport(
        base_url, live[0] if live else cameras[0]
    )
    if chosen == "none":
        return []

    streams: list[CameraStream] = []
    for camera in live:
        external_id = str(camera.get("id") or camera.get("number") or "")
        if not external_id:
            continue
        # Registry codes are SBX-00001; the grid uses bare numeric ids. Shard on
        # the code so a worker owns the same cameras whichever path found them.
        code = f"SBX-{external_id.zfill(5)}"
        if not owns(code, index, count):
            continue

        if chosen == "rtsp":
            url = (
                camera.get("rtsp_url")
                or f"rtsp://{urlparse(base_url).hostname}:8554/stream/{external_id}"
            )
        else:
            hls = camera.get("hls_live_url") or f"/live/stream/{external_id}/index.m3u8"
            url = hls if hls.startswith("http") else f"{base_url.rstrip('/')}{hls}"

        label = str(camera.get("location") or camera.get("name") or code).strip()
        streams.append(
            CameraStream(camera_code=code, rtsp_url=url, transport=chosen, label=label)
        )

    streams.sort(key=lambda s: s.camera_code)
    if len(streams) > limit:
        log.warning(
            "%d sandbox cameras assigned to worker %d but the cap is %d; "
            "processing the first %d. Add workers to cover the rest.",
            len(streams),
            index,
            limit,
            limit,
        )
        streams = streams[:limit]
    return streams


# ─────────────────────────────────────────────────────────────────────
# The registry roster
# ─────────────────────────────────────────────────────────────────────
#: Where the API publishes the ANPR fleet. See app/services/fleet_roster.py for
#: why the roster travels through Redis rather than the database or the API.
ROSTER_KEY = "nagarnetra:fleet:anpr"


async def rtsp_reachable(host: str, port: int = 8554, timeout: float = 4.0) -> bool:
    """Can this worker open RTSP to that host at all?

    Probed per host and cached by the caller. The organisers' guide anticipates
    the answer being no — "if port 8554 is blocked on your network, use the HLS
    endpoint instead" — and finding out by opening thirty streams that each
    time out is far more expensive than one connect.
    """
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def discover_registry(
    redis_url: str,
    index: int,
    count: int,
    timeout: float = 5.0,
    rtsp_probe: dict[str, bool] | None = None,
) -> list[CameraStream]:
    """Every ANPR camera this worker owns, from the registry roster.

    Unlike MediaMTX discovery, this includes federated cameras whose video
    lives on somebody else's gateway, and it keeps including them while that
    gateway is down. A camera that cannot be reached is a camera with a
    problem, and reporting that is the platform's job; pretending it left the
    fleet is not.

    No cap is applied here. How many cameras a worker can analyse at once is a
    hardware question, answered by the rotation in `worker.py`, not a question
    of which cameras exist.
    """
    import redis.asyncio as aioredis  # imported lazily: only this path needs it

    client = aioredis.from_url(redis_url, decode_responses=True)
    try:
        raw = await asyncio.wait_for(client.get(ROSTER_KEY), timeout=timeout)
    except (asyncio.TimeoutError, OSError, ValueError) as exc:
        log.warning("could not read the fleet roster: %s", exc)
        return []
    finally:
        await client.aclose()

    if not raw:
        log.warning(
            "the fleet roster is empty or expired — is the API running? "
            "It publishes %s every 30s.",
            ROSTER_KEY,
        )
        return []

    try:
        cameras = json.loads(raw).get("cameras", [])
    except ValueError as exc:
        log.warning("the fleet roster is not valid JSON: %s", exc)
        return []

    probe = rtsp_probe if rtsp_probe is not None else {}
    streams: list[CameraStream] = []
    unusable: list[str] = []

    for entry in cameras:
        code = str(entry.get("camera_code") or "")
        if not code or not owns(code, index, count):
            continue

        rtsp = str(entry.get("rtsp_url") or "")
        hls = str(entry.get("hls_url") or "")

        url, transport = "", "none"
        if rtsp:
            host = urlparse(rtsp).hostname or ""
            if host not in probe:
                probe[host] = await rtsp_reachable(host)
                log.info(
                    "RTSP to %s is %sreachable from this worker",
                    host,
                    "" if probe[host] else "not ",
                )
            if probe[host]:
                url, transport = rtsp, "rtsp"
        if not url and hls:
            url, transport = hls, "hls"

        # A camera with no usable URL is real but cannot be analysed. It is
        # named in the log rather than silently dropped: "we are not watching
        # this camera" is something an operator needs told.
        if not url:
            unusable.append(code)
            continue

        regions = entry.get("plate_regions") or ["IN"]
        streams.append(
            CameraStream(
                camera_code=code,
                rtsp_url=url,
                transport=transport,
                label=str(entry.get("label") or code),
                plate_regions=tuple(str(r) for r in regions),
            )
        )

    if unusable:
        log.warning(
            "%d camera(s) have no reachable stream and are not being analysed: %s",
            len(unusable),
            ", ".join(sorted(unusable)[:10]),
        )

    streams.sort(key=lambda s: s.camera_code)
    return streams
