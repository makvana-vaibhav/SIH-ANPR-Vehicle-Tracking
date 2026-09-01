"""Publishing events to the platform's stream."""

from __future__ import annotations

import json
from typing import Any

import pytest
import redis

from ai_worker.publisher import RedisEventSink


class FakeRedis:
    def __init__(self, fail: bool = False) -> None:
        self.entries: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail
        self.closed = False

    def xadd(self, key: str, fields: dict[str, Any], **kwargs: Any) -> str:
        if self.fail:
            raise redis.ConnectionError("bus down")
        self.entries.append((key, fields))
        return f"{len(self.entries)}-0"

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def sink(monkeypatch) -> RedisEventSink:
    fake = FakeRedis()
    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda *a, **k: fake))
    s = RedisEventSink("redis://x", "sentinel:events:detections")
    s._fake = fake  # type: ignore[attr-defined]
    return s


def event(kind: str = "vehicle.completed", plate: str = "GJ03AB1234") -> dict[str, Any]:
    return {"event": kind, "plate": {"text": plate}, "source": {"camera_id": "CAM-1"}}


class TestRedisEventSink:
    def test_publishes_json(self, sink: RedisEventSink) -> None:
        sink.emit(event())
        key, fields = sink._fake.entries[0]  # type: ignore[attr-defined]
        assert key == "sentinel:events:detections"
        assert json.loads(fields["payload"])["plate"]["text"] == "GJ03AB1234"

    def test_counts_by_kind(self, sink: RedisEventSink) -> None:
        sink.emit(event("vehicle.observed"))
        sink.emit(event("vehicle.completed"))
        sink.emit(event("vehicle.completed"))
        assert sink.by_kind == {"vehicle.observed": 1, "vehicle.completed": 2}
        assert sink.written == 3

    def test_bus_outage_does_not_kill_the_inference_loop(self, monkeypatch) -> None:
        """A camera must keep being watched when the bus is unreachable.

        Losing the events for an outage window is recoverable; a worker that
        dies because Redis blinked leaves the camera dark until someone notices.
        """
        fake = FakeRedis(fail=True)
        monkeypatch.setattr(redis.Redis, "from_url", staticmethod(lambda *a, **k: fake))
        sink = RedisEventSink("redis://x", "k")

        for _ in range(5):
            sink.emit(event())  # must not raise

        assert sink.publish_failures == 5
        assert sink.written == 5, "losses must still be counted, not hidden"

    def test_close_is_safe(self, sink: RedisEventSink) -> None:
        sink.close()
        assert sink._fake.closed  # type: ignore[attr-defined]
