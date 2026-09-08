"""Live event fan-out to connected operators.

The AI workers publish to a Redis Stream; this reads that stream once per API
process and hands each event to every connected WebSocket. One reader, many
sockets — a consumer group per socket would give each operator a *different*
subset of events, which is exactly wrong for a shared operations picture.

Slow clients are dropped rather than allowed to block. An operator whose
connection has stalled must not hold up the alert feed for the rest of the
control room; their browser reconnects and resumes from live.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from app.core.logging import get_logger

log = get_logger("event_bus")

# Per-socket buffer. Deep enough to ride out a slow render, shallow enough that
# a dead connection is noticed in seconds rather than minutes.
SUBSCRIBER_QUEUE_SIZE = 256


class EventBroadcaster:
    """Fans one event stream out to many WebSocket subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        self.dropped_for_slow_clients = 0
        self.published = 0

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(self, event: dict[str, Any]) -> None:
        self.published += 1
        async with self._lock:
            subscribers = list(self._subscribers)

        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # This subscriber is not keeping up. Drop the event for them
                # only; the rest of the control room still sees it.
                self.dropped_for_slow_clients += 1

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def stats(self) -> dict[str, Any]:
        return {
            "subscribers": self.subscriber_count,
            "published": self.published,
            "dropped_for_slow_clients": self.dropped_for_slow_clients,
        }


broadcaster = EventBroadcaster()

#: Alerts are raised by whichever ingest worker happened to persist the
#: detection, but they must reach *every* operator, on every API replica. The
#: detection stream cannot carry them — an alert is created after the commit,
#: not read from the bus — so they travel on their own pub/sub channel, which
#: every replica subscribes to and nobody consumes exclusively.
ALERT_CHANNEL = "nagarnetra:alerts:fanout"


def parse_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Decode one Redis Stream entry into an event dict."""
    payload = raw.get("payload") or raw.get(b"payload")
    if payload is None:
        return None
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", "replace")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        log.warning("event_bus.undecodable_payload", preview=str(payload)[:120])
        return None
    return parsed if isinstance(parsed, dict) else None
