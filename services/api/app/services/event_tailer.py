"""Tails the event stream for the live operations picture.

Separate from `EventConsumer`, and the distinction is the thing that lets the
platform scale horizontally at all.

**Durable processing is exactly-once across the fleet.** A detection must be
written to Postgres by one worker, not by every worker, so persistence reads
through a Redis *consumer group*: Redis hands each entry to exactly one member.

**The live picture is all-of-it, everywhere.** Every operator watching any API
replica must see every event. That is the opposite requirement, and a consumer
group is exactly the wrong tool for it — members receive disjoint subsets, so
two API replicas in one group would each show their operators half the state's
traffic, with nothing anywhere reporting a fault.

Until this existed, one class did both jobs, and the platform was correct only
while exactly one API process ran. That is not a property anybody had written
down, and it would have failed the first time a second replica was added — in
production, silently, as missing events rather than as an error.

So: persistence uses the group, this uses a plain `XREAD` from `$`, and the
two scale independently.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.services.event_bus import broadcaster, parse_event

log = get_logger("event_tailer")

BLOCK_MS = 2000
BATCH_SIZE = 500


class EventTailer:
    """Reads the stream from now, and fans every event out to local sockets."""

    def __init__(self) -> None:
        self._client: aioredis.Redis | None = None
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.tailed = 0

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="event-tailer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _run(self) -> None:
        # "$" means from now on. A replica that restarts resumes at live rather
        # than replaying history into the control room: an operator does not
        # want twenty minutes of old vehicles arriving as though they were
        # happening. The durable record is the consumer group's job.
        last_id = "$"
        while not self._stop.is_set():
            try:
                last_id = await self._tail(last_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Supervisor boundary: the live feed must survive a bus blip
                # and come back, not die and leave the picture frozen with no
                # indication that it has stopped moving.
                log.exception("event_tailer.crashed_restarting")
                self._client = None
                await asyncio.sleep(3.0)
        return

    async def _tail(self, last_id: str) -> str:
        if self._client is None:
            self._client = aioredis.from_url(settings.redis_url, decode_responses=True)
            log.info("event_tailer.starting", stream=settings.event_stream_key)

        while not self._stop.is_set():
            try:
                batches = await self._client.xread(
                    {settings.event_stream_key: last_id}, count=BATCH_SIZE, block=BLOCK_MS
                )
            except RedisError:
                self._client = None
                raise

            for _stream, entries in batches or []:
                for entry_id, raw in entries:
                    last_id = entry_id
                    event = parse_event(raw)
                    if event is not None:
                        self.tailed += 1
                        await broadcaster.publish(event)

        return last_id

    def stats(self) -> dict[str, Any]:
        return {
            "tailed": self.tailed,
            "running": self._task is not None and not self._task.done(),
        }


tailer = EventTailer()
