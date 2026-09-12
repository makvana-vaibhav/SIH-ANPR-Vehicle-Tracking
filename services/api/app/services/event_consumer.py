"""Consumes vehicle events from the AI workers and lands them in the platform.

The workers produce; this is where the platform takes over. Three things happen
to each event, in this order and for this reason:

1. **Final events are persisted.** `vehicle.completed` means the vehicle has
   left and consensus is settled. Provisional `vehicle.observed` events are
   *not* written: they are the same vehicle mid-read, and storing them would
   put several rows in history for one car — the exact fragmentation the AI
   tier works to avoid.
2. Any **watchlist hit is announced** on the alert channel, after the commit.
3. The consumer **acknowledges** only after both. A crash between read and ack
   redelivers the event, which is the right failure: a duplicate detection is
   recoverable, a lost one is not.

Live fan-out to operators is **not** done here — see `event_tailer.py` for why
that is a different job with the opposite delivery requirement.

A Redis consumer group, not a plain read, so a restarted API resumes from its
offset instead of losing whatever arrived while it was down.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from collections import deque
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
from app.services import alert_fanout, alerts, watchlist
from app.services.event_bus import parse_event

log = get_logger("event_consumer")

CONSUMER_GROUP = "nagarnetra-api"
# How long to block waiting for events before looping. Long enough that an idle
# stream costs nothing, short enough that shutdown is prompt.
BLOCK_MS = 2000
# How many entries one read may return. `xreadgroup` returns as soon as *any*
# entry is available, so a large count costs an idle stream nothing — it only
# takes effect when events are already queued, which is exactly when batching
# is worth having.
BATCH_SIZE = 200
# Rolling window for the latency percentiles reported by `stats()`. Bounded so
# a long-running API does not accumulate samples forever; large enough that a
# p99 over it means something.
LATENCY_SAMPLES = 10_000
# A consumer quiet for this long, holding nothing, is a previous process.
IDLE_CONSUMER_MS = 300_000

#: Each ingest worker publishes its counters here so `/metrics` can report the
#: whole ingest fleet rather than only whichever process happens to serve the
#: scrape. Keyed per worker and expiring, so a worker that dies disappears from
#: the total instead of contributing stale numbers forever.
#: One hash, one field per worker, rather than a key each. Reading a key per
#: worker meant SCAN, and SCAN against a Redis that is simultaneously carrying
#: the event stream at statewide rate is slow enough that the scrape times out
#: — so the platform looked unmeasurable exactly when it was working hardest.
#: Staleness is carried in the value and filtered by the reader, since fields
#: within a hash cannot expire individually.
WORKER_STATS_KEY = "nagarnetra:ingest:workers"
WORKER_STATS_TTL_S = 30
WORKER_STATS_EVERY_S = 5.0

#: How long a *negative* camera lookup is trusted. Positive results are kept
#: until something proves them wrong, because a camera's id does not change.
#: A miss is different: cameras are onboarded while the platform runs — that is
#: the entire point of the registry — and caching "no such camera" forever
#: means every detection from a newly-onboarded camera is stored unattributed
#: until the worker is restarted. Nobody would see a fault; the camera would
#: simply never appear in its own history.
UNKNOWN_CAMERA_TTL_S = 60.0


class EventConsumer:
    """Reads the detection stream and lands events in the platform."""

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._camera_ids: dict[str, uuid.UUID] = {}
        #: Codes the registry did not know, with the time they were looked up.
        self._unknown_cameras: dict[str, float] = {}
        self.consumed = 0
        self.persisted = 0
        self.alerts_raised = 0
        self.failed = 0
        self.batches = 0
        self._stats_published_at = 0.0
        # Capture-to-persisted latency, in milliseconds, over a rolling window.
        self._latency_ms: deque[float] = deque(maxlen=LATENCY_SAMPLES)

    # ─────────────────────────────────────────────────────────────────
    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="event-consumer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            # Cancelling is how this task is meant to end; awaiting it just
            # lets the cancellation land before we drop the reference.
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
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
            await self._forget_departed_consumers(self._client)
        return self._client

    async def _forget_departed_consumers(self, client: aioredis.Redis) -> None:
        """Drop consumer names left behind by previous API processes.

        Each process joins the group under a fresh name, and Redis remembers
        every name until it is told not to — a long-lived deployment restarted
        nightly accumulates one entry per restart forever. This host had 67
        after a day of development.

        Only consumers that are idle *and* hold nothing pending are removed:
        a consumer with unacknowledged entries is either alive or holding
        events that still need reclaiming, and deleting it would discard them.
        """
        try:
            consumers = await client.xinfo_consumers(settings.event_stream_key, CONSUMER_GROUP)
        except ResponseError:
            return

        removed = 0
        for entry in consumers:
            if entry.get("pending", 0) == 0 and entry.get("idle", 0) > IDLE_CONSUMER_MS:
                await client.xgroup_delconsumer(
                    settings.event_stream_key, CONSUMER_GROUP, entry["name"]
                )
                removed += 1
        if removed:
            log.info("event_consumer.forgot_departed", consumers=removed)

    async def _publish_stats(self, client: aioredis.Redis, consumer_name: str) -> None:
        """Report this worker's counters, so the fleet's total is visible."""
        now = time.monotonic()
        if now - self._stats_published_at < WORKER_STATS_EVERY_S:
            return
        self._stats_published_at = now
        try:
            await client.hset(
                WORKER_STATS_KEY,
                consumer_name,
                json.dumps({"worker": consumer_name, "reported_at": time.time(), **self.stats()}),
            )
            # The hash as a whole expires, so a platform shut down overnight
            # does not come back reporting yesterday's ingest fleet.
            await client.expire(WORKER_STATS_KEY, WORKER_STATS_TTL_S * 4)
        except RedisError:
            # Losing a stats heartbeat must never stop the worker ingesting.
            log.warning("event_consumer.stats_publish_failed", exc_info=True)

    async def _consume(self, consumer_name: str) -> None:
        client = await self._client_or_connect()
        while not self._stop.is_set():
            await self._publish_stats(client, consumer_name)
            try:
                batches = await client.xreadgroup(
                    CONSUMER_GROUP,
                    consumer_name,
                    {settings.event_stream_key: ">"},
                    count=BATCH_SIZE,
                    block=BLOCK_MS,
                )
            except RedisError:
                self._client = None
                raise

            if not batches:
                continue

            for _stream, entries in batches:
                await self._handle_batch(client, entries)

    async def _handle_batch(
        self, client: aioredis.Redis, entries: list[tuple[str, dict[str, Any]]]
    ) -> None:
        """Broadcast every event, persist the settled ones, then ack the lot.

        Persisting the batch in **one transaction** rather than one per event
        is what makes the statewide event rate reachable: a commit costs about
        the same whether it carries one row or two hundred, and at 2,667
        events/s the per-event commit was the whole ceiling.

        Ordering is unchanged from the per-event version — broadcast, persist,
        ack — because each step's reason still holds.
        """
        entry_ids: list[str] = []
        settled: list[dict[str, Any]] = []

        for entry_id, raw in entries:
            entry_ids.append(entry_id)
            event = parse_event(raw)
            if event is None:
                continue

            self.consumed += 1

            # Not broadcast here. `EventTailer` fans the stream out to sockets
            # on every replica; doing it here too would double every event on a
            # single-node deployment, and would deliver nothing at all on a
            # deployment where ingest runs in a dedicated worker.
            if event.get("event") == "vehicle.completed":
                settled.append(event)

        if settled:
            try:
                await self._persist_batch(settled)
            except Exception:
                # The batch transaction rolled back, so nothing in it landed.
                # Retry the events one at a time: a single malformed event must
                # not cost the other 199 their sighting, and which one it was
                # is worth knowing.
                log.exception("event_consumer.batch_failed_retrying_singly", size=len(settled))
                # Forget what we thought we knew about these cameras first. The
                # commonest cause of a batch failing is a cached camera id whose
                # row has since been deleted, and retrying with the same stale
                # id just fails 200 times instead of once. Re-resolving finds
                # the camera gone and stores the sighting unattributed, which is
                # what `_resolve_cameras` already says it prefers.
                self._forget_cameras(settled)
                for event in settled:
                    try:
                        await self._persist_batch([event])
                    except Exception:
                        self.failed += 1
                        log.exception(
                            "event_consumer.persist_failed",
                            camera=event.get("source", {}).get("camera_id"),
                        )

        if entry_ids:
            self.batches += 1
            await client.xack(settings.event_stream_key, CONSUMER_GROUP, *entry_ids)

    # ─────────────────────────────────────────────────────────────────
    def _forget_cameras(self, events: list[dict[str, Any]]) -> None:
        """Drop cached camera ids for a batch that failed to write."""
        for event in events:
            code = str(event.get("source", {}).get("camera_id", ""))
            self._camera_ids.pop(code, None)
            self._unknown_cameras.pop(code, None)

    async def _resolve_cameras(self, codes: set[str]) -> dict[str, uuid.UUID | None]:
        """Map the workers' camera codes to registry UUIDs, one query a batch.

        Cached, because a code is looked up on every detection and a camera's
        code does not change. A code with no registry entry caches as None and
        the detection is still stored: losing a sighting because the registry
        is behind would be worse than storing one whose camera is unknown.
        """
        wanted = {code for code in codes if code}

        # Retry anything whose "not found" has expired, in case it has since
        # been onboarded.
        now = time.monotonic()
        stale = [
            code
            for code, looked_up_at in self._unknown_cameras.items()
            if now - looked_up_at > UNKNOWN_CAMERA_TTL_S
        ]
        for code in stale:
            del self._unknown_cameras[code]

        missing = wanted - self._camera_ids.keys() - self._unknown_cameras.keys()

        if missing:
            # Case-insensitive: the registry stores canonical uppercase codes
            # (CAM-00042) while MediaMTX reports its stream paths lowercased.
            # An exact match silently left camera_id NULL on every detection,
            # which would have quietly broken per-camera history and the map.
            upper = {code.upper(): code for code in missing}
            async with SessionLocal() as session:
                rows = await session.execute(
                    select(Camera.camera_code, Camera.id).where(
                        func.upper(Camera.camera_code).in_(upper.keys())
                    )
                )
                found = {code.upper(): camera_id for code, camera_id in rows.all()}

            for upper_code, original in upper.items():
                camera_id = found.get(upper_code)
                if camera_id is None:
                    log.warning("event_consumer.unknown_camera", camera_code=original)
                    self._unknown_cameras[original] = now
                else:
                    self._camera_ids[original] = camera_id

        return {code: self._camera_ids.get(code) for code in wanted}

    def _build_detection(self, event: dict[str, Any], camera_id: uuid.UUID | None) -> Detection:
        """Turn one settled vehicle event into a detection row."""
        vehicle = event.get("vehicle", {})
        plate = event.get("plate", {})
        evidence = event.get("evidence", {})
        camera_code = str(event.get("source", {}).get("camera_id", ""))
        # Absent on events from a worker that predates the motion block; the
        # columns then stay NULL, which is the honest record of "not measured"
        # and is exactly how the traffic engine treats them.
        motion = vehicle.get("motion") or {}

        return Detection(
            ts=_parse_time(event.get("event_time")),
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
            # What makes traffic intelligence possible from a one-row-per-
            # vehicle record. `direction` is image-space, not compass — see
            # the column comments on the model. "unknown" is stored as NULL
            # rather than as a string, so "we could not measure" reads the same
            # here as it does on every row written before this existed.
            direction=(str(motion.get("direction") or "")[:16] or None)
            if motion.get("direction") != "unknown"
            else None,
            motion_px=motion.get("distance_px"),
            # The worker has always computed this and the consumer has always
            # discarded it. Density and queue detection are both occupancy over
            # these intervals.
            dwell_s=vehicle.get("duration_s"),
            # The object key the worker uploaded the crop under. The worker
            # returns the key synchronously and uploads on a background thread,
            # so for a moment after a detection this names an object that does
            # not exist yet — the crop endpoint answers 404 for that, and the UI
            # shows no crop rather than a broken image.
            crop_key=(evidence.get("plate_crop") or evidence.get("vehicle_crop") or None),
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

    async def _persist_batch(self, events: list[dict[str, Any]]) -> None:
        """Write a batch of settled vehicles, and any alerts they raise.

        One transaction for the whole batch. Watchlist matching stays inside it
        for the same reason it always did: an alert that survives without its
        detection, or a detection that silently failed to raise the alert it
        should have, are both worse than the write failing.
        """
        codes = {str(e.get("source", {}).get("camera_id", "")) for e in events}
        camera_ids = await self._resolve_cameras(codes)

        pending: list[tuple[Detection, str]] = []
        async with SessionLocal() as session:
            for event in events:
                code = str(event.get("source", {}).get("camera_id", ""))
                detection = self._build_detection(event, camera_ids.get(code))
                session.add(detection)
                pending.append((detection, code))

            # One flush for the batch. The primary key comes from a Python-side
            # column default applied at flush, so without this every alert below
            # would record detection_id=None and lose its link to the evidence
            # that raised it.
            await session.flush()

            raised: list[dict[str, Any]] = []
            for detection, code in pending:
                if not detection.plate_normalised:
                    continue
                match = await watchlist.index.match(detection.plate_normalised, detection.ts)
                if match is None:
                    continue
                alert = await alerts.raise_for_match(
                    session,
                    match=match,
                    plate=detection.plate_normalised,
                    camera_id=detection.camera_id,
                    camera_code=code,
                    detection_id=detection.id,
                    detection_ts=detection.ts,
                    confidence=detection.plate_confidence,
                )
                if alert is not None:
                    raised.append(alerts.alert_payload(alert, match))

            await session.commit()

        self.persisted += len(pending)
        self._record_latency(pending)

        # Announced after the commit, so an operator never sees an alert that a
        # rolled-back transaction means does not exist. Over pub/sub rather
        # than to the local broadcaster, because the operators who need it are
        # attached to whichever API replica they happen to have reached — which
        # in a dedicated-worker deployment is never this process.
        for payload in raised:
            self.alerts_raised += 1
            await alert_fanout.publish(payload)

    def _record_latency(self, pending: list[tuple[Detection, str]]) -> None:
        """Capture-to-persisted latency for each row just committed.

        Measured from the event's own timestamp, so it spans the bus and the
        write but not the inference that produced it — the AI tier reports its
        own latency separately, and adding them here would double-count.
        """
        now = datetime.now(UTC)
        for detection, _code in pending:
            self._latency_ms.append((now - detection.ts).total_seconds() * 1000.0)

    def latency_percentiles(self) -> dict[str, float | None]:
        """p50 / p95 / p99 over the rolling window, or nulls if it is empty."""
        samples = sorted(self._latency_ms)
        if not samples:
            return {"p50_ms": None, "p95_ms": None, "p99_ms": None, "samples": 0}

        def at(fraction: float) -> float:
            index = min(len(samples) - 1, int(fraction * len(samples)))
            return round(samples[index], 1)

        return {
            "p50_ms": at(0.50),
            "p95_ms": at(0.95),
            "p99_ms": at(0.99),
            "samples": len(samples),
        }

    def stats(self) -> dict[str, Any]:
        return {
            "consumed": self.consumed,
            "persisted": self.persisted,
            "alerts_raised": self.alerts_raised,
            "failed": self.failed,
            "batches": self.batches,
            "mean_batch": round(self.consumed / self.batches, 1) if self.batches else 0.0,
            "latency": self.latency_percentiles(),
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
