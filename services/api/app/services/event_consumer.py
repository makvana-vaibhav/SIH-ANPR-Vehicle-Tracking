"""Consumes vehicle events from the AI workers and lands them in the platform.

The workers produce; this is where the platform takes over. Three things happen
to each event, in this order and for this reason:

1. It is **broadcast to connected operators first.** A watchlist hit is worth
   nothing after the vehicle has gone, and a database write is the slowest step
   here. Fanning out before persisting costs milliseconds on the alert path.
2. **Final events are persisted.** `vehicle.completed` means the vehicle has
   left and consensus is settled. Provisional `vehicle.observed` events are
   *not* written: they are the same vehicle mid-read, and storing them would
   put several rows in history for one car — the exact fragmentation the AI
   tier works to avoid.
3. The consumer **acknowledges** only after both. A crash between read and ack
   redelivers the event, which is the right failure: a duplicate detection is
   recoverable, a lost one is not.

A Redis consumer group, not a plain read, so a restarted API resumes from its
offset instead of losing whatever arrived while it was down.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import func, select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.intelligence import Detection
from app.models.registry import Camera
from app.services.event_bus import broadcaster, parse_event

log = get_logger("event_consumer")

CONSUMER_GROUP = "sentinel-api"
# How long to block waiting for events before looping. Long enough that an idle
# stream costs nothing, short enough that shutdown is prompt.
BLOCK_MS = 2000
BATCH_SIZE = 50


class EventConsumer:
    """Reads the detection stream and lands events in the platform."""

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._camera_ids: dict[str, uuid.UUID | None] = {}
        self.consumed = 0
        self.persisted = 0
        self.failed = 0

    # ─────────────────────────────────────────────────────────────────
    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="event-consumer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ─────────────────────────────────────────────────────────────────
    async def _run(self) -> None:
        consumer_name = f"api-{uuid.uuid4().hex[:8]}"
        log.info(
            "event_consumer.starting", stream=settings.event_stream_key, consumer=consumer_name
        )

        while not self._stop.is_set():
            try:
                await self._consume(consumer_name)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Supervisor boundary: the consumer must survive a bus outage
                # or a malformed batch and come back, not die silently and
                # leave the operations picture frozen.
                self.failed += 1
                log.exception("event_consumer.crashed_restarting")
                await asyncio.sleep(3.0)

    async def _client_or_connect(self) -> aioredis.Redis:
        if self._client is None:
            self._client = aioredis.from_url(settings.redis_url, decode_responses=True)
            try:
                await self._client.xgroup_create(
                    settings.event_stream_key, CONSUMER_GROUP, id="0", mkstream=True
                )
                log.info("event_consumer.group_created", group=CONSUMER_GROUP)
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
        return self._client

    async def _consume(self, consumer_name: str) -> None:
        client = await self._client_or_connect()
        while not self._stop.is_set():
            try:
                batches = await client.xreadgroup(
                    CONSUMER_GROUP, consumer_name,
                    {settings.event_stream_key: ">"},
                    count=BATCH_SIZE, block=BLOCK_MS,
                )
            except RedisError:
                self._client = None
                raise

            if not batches:
                continue

            for _stream, entries in batches:
                for entry_id, raw in entries:
                    await self._handle(client, entry_id, raw)

    async def _handle(self, client: aioredis.Redis, entry_id: str, raw: dict[str, Any]) -> None:
        event = parse_event(raw)
        if event is None:
            await client.xack(settings.event_stream_key, CONSUMER_GROUP, entry_id)
            return

        self.consumed += 1

        # Operators first: the database write is the slow step and an alert is
        # worth less with every second it waits.
        await broadcaster.publish(event)

        if event.get("event") == "vehicle.completed":
            try:
                await self._persist(event)
            except Exception:
                self.failed += 1
                log.exception("event_consumer.persist_failed", entry=entry_id)

        await client.xack(settings.event_stream_key, CONSUMER_GROUP, entry_id)

    # ─────────────────────────────────────────────────────────────────
    async def _resolve_camera(self, camera_code: str) -> uuid.UUID | None:
        """Map the worker's camera code to the registry's UUID.

        Cached, because it is looked up on every detection and a camera's code
        does not change. A code with no registry entry resolves to None and the
        detection is still stored: losing a sighting because the registry is
        behind would be worse than storing one whose camera is unknown.
        """
        if camera_code in self._camera_ids:
            return self._camera_ids[camera_code]

        # Case-insensitive: the registry stores canonical uppercase codes
        # (CAM-00042) while MediaMTX reports its stream paths lowercased. An
        # exact match silently left camera_id NULL on every detection, which
        # would have quietly broken per-camera history and the map.
        async with SessionLocal() as session:
            result = await session.execute(
                select(Camera.id).where(
                    func.upper(Camera.camera_code) == camera_code.upper()
                )
            )
            camera_id = result.scalar_one_or_none()

        if camera_id is None:
            log.warning("event_consumer.unknown_camera", camera_code=camera_code)
        self._camera_ids[camera_code] = camera_id
        return camera_id

    async def _persist(self, event: dict[str, Any]) -> None:
        """Write one settled vehicle as a detection row."""
        source = event.get("source", {})
        vehicle = event.get("vehicle", {})
        plate = event.get("plate", {})
        evidence = event.get("evidence", {})

        camera_code = str(source.get("camera_id", ""))
        camera_id = await self._resolve_camera(camera_code) if camera_code else None

        timestamp = _parse_time(event.get("event_time"))

        detection = Detection(
            ts=timestamp,
            camera_id=camera_id,
            # The worker's track id is unique within a camera session; prefixing
            # with the camera keeps it unique across the fleet.
            track_id=f"{camera_code}:{vehicle.get('vehicle_id', 0)}",
            vehicle_type=str(vehicle.get("type") or "")[:16] or None,
            plate_text=(plate.get("text") or None),
            plate_normalised=(plate.get("text") or None),
            plate_confidence=plate.get("confidence"),
            detection_confidence=vehicle.get("confidence"),
            bbox=vehicle.get("bbox"),
            plate_bbox=plate.get("bbox"),
            # Everything behind the consensus, so an operator can see why this
            # plate was chosen and overrule it.
            ocr_raw={
                "candidates": plate.get("candidates", []),
                "evidence": plate.get("evidence", {}),
                "reads": evidence.get("reads", []),
                "ambiguous": plate.get("ambiguous", False),
                "corrected_from": plate.get("corrected_from"),
                "track_ids": vehicle.get("track_ids", []),
                "merged_from_fragments": vehicle.get("merged_from_fragments", 1),
                "latency_ms": event.get("latency_ms"),
            },
            is_validated=bool(plate.get("grammar_valid", False)),
        )

        async with SessionLocal() as session:
            session.add(detection)
            await session.commit()

        self.persisted += 1

    def stats(self) -> dict[str, Any]:
        return {
            "consumed": self.consumed,
            "persisted": self.persisted,
            "failed": self.failed,
            "running": self._task is not None and not self._task.done(),
        }


def _parse_time(value: Any) -> datetime:
    """Event time, always tz-aware UTC. Falls back to now if absent or odd."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


consumer = EventConsumer()
