"""Delivers alerts raised anywhere to operators connected everywhere.

An alert is not on the event stream. It is created *after* a detection is
committed, by whichever ingest worker happened to handle that detection, and
it has to reach every operator regardless of which API replica their browser
is attached to.

Redis pub/sub, not a stream, and deliberately: pub/sub is fan-out to all live
subscribers with no durability, which is exactly right here. The durable record
of an alert is the `alerts` table — a control room that was disconnected when
one fired sees it in the alert list on reconnect, not by replaying a queue.
Making this durable would mean an operator coming on shift is ambushed by
every alert of the previous eight hours arriving as though it were now.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.logging import get_logger
from app.services.event_bus import ALERT_CHANNEL, broadcaster

log = get_logger("alert_fanout")


async def publish(payload: dict[str, Any]) -> None:
    """Announce an alert to every API replica.

    A short-lived client per call: this runs on the ingest path, which may be
    a worker process with no other Redis use, and an alert is rare enough that
    a connection per alert costs nothing next to the database write that
    preceded it.
    """
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.publish(ALERT_CHANNEL, json.dumps(payload, default=str))
    except RedisError:
        # The alert is already committed and will appear in the alert list on
        # the next poll. Losing the push is a degraded live feed, not a lost
        # alert, and it must not roll back the transaction that raised it.
        log.warning("alert_fanout.publish_failed", exc_info=True)
    finally:
        await client.aclose()


class AlertSubscriber:
    """Subscribes this replica to alerts raised by any worker."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self.received = 0

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="alert-fanout")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            client = aioredis.from_url(settings.redis_url, decode_responses=True)
            try:
                pubsub = client.pubsub()
                await pubsub.subscribe(ALERT_CHANNEL)
                log.info("alert_fanout.subscribed", channel=ALERT_CHANNEL)
                async for message in pubsub.listen():
                    if self._stop.is_set():
                        break
                    if message.get("type") != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                    except (ValueError, TypeError):
                        log.warning("alert_fanout.undecodable")
                        continue
                    self.received += 1
                    await broadcaster.publish(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("alert_fanout.crashed_restarting")
                await asyncio.sleep(3.0)
            finally:
                await client.aclose()

    def stats(self) -> dict[str, Any]:
        return {
            "received": self.received,
            "running": self._task is not None and not self._task.done(),
        }


subscriber = AlertSubscriber()
