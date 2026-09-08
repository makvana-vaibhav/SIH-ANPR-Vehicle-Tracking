"""Publishes vehicle events to the platform's Redis Stream.

The AI tier's responsibility ends here. It does not write to Postgres, does not
consult the watchlist, and does not raise alerts — those belong to the platform,
and keeping the line sharp is what lets the worker be scaled and restarted
without coordinating with anything.

A Redis Stream rather than pub/sub, because a stream is durable: if the API
restarts, it resumes from its consumer-group offset instead of losing whatever
was in flight. The stream is capped so that a stopped consumer degrades into
losing the oldest events rather than exhausting Redis.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis

log = logging.getLogger(__name__)


class RedisEventSink:
    """Duck-types the lab's EventSink so StreamRunner can write to the bus.

    Deliberately not async: it is called from the per-camera inference thread,
    where an event loop would be an extra moving part for a single XADD.
    """

    def __init__(self, url: str, stream_key: str, maxlen: int = 100_000) -> None:
        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._stream_key = stream_key
        self._maxlen = maxlen
        self.written = 0
        self.by_kind: dict[str, int] = {}
        self.publish_failures = 0

    def emit(self, event: dict[str, Any]) -> None:
        kind = str(event.get("event", "unknown"))
        self.by_kind[kind] = self.by_kind.get(kind, 0) + 1
        self.written += 1

        try:
            self._client.xadd(
                self._stream_key,
                {"payload": json.dumps(event, default=str)},
                # approximate trimming: Redis trims on segment boundaries,
                # which is far cheaper than exact and just as safe here.
                maxlen=self._maxlen,
                approximate=True,
            )
        except redis.RedisError as exc:
            # A bus outage must not kill the inference loop. The camera keeps
            # being watched, the events for the outage window are lost, and the
            # loss is counted rather than hidden.
            self.publish_failures += 1
            if (
                self.publish_failures in (1, 10, 100)
                or self.publish_failures % 1000 == 0
            ):
                log.warning(
                    "failed to publish event to %s (%d failures so far): %s",
                    self._stream_key,
                    self.publish_failures,
                    exc,
                )

    def close(self) -> None:
        try:
            self._client.close()
        except redis.RedisError:
            pass

    def __enter__(self) -> RedisEventSink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
