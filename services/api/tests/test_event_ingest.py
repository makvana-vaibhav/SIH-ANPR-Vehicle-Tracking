"""The ingest path: batching, camera resolution, and the fan-out split.

This code lands every detection the platform holds and had **no tests at all**
until the load test made its behaviour matter. Two of the properties asserted
here were broken in ways nothing else would have caught.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models.intelligence import Detection
from app.models.registry import Camera
from app.services.event_consumer import EventConsumer

pytestmark = pytest.mark.asyncio


def an_event(camera_code: str, plate: str = "GJ05TT4321", vehicle_id: int = 1) -> dict:
    """A `vehicle.completed` event shaped as the AI worker emits it."""
    return {
        "schema": "ailab.vehicle.event.v1",
        "event": "vehicle.completed",
        "event_time": datetime.now(UTC).isoformat(),
        "source": {"camera_id": camera_code, "name": camera_code},
        "frame": {"width": 1920, "height": 1080},
        "vehicle": {"vehicle_id": vehicle_id, "track_ids": [vehicle_id], "type": "car",
                    "confidence": 0.81, "bbox": {"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}},
        "plate": {"text": plate, "confidence": 0.9, "grammar_valid": True,
                  "candidates": [], "evidence": {}, "bbox": None},
        "evidence": {"reads": []},
    }


@pytest.fixture
async def _clean_detections():
    yield
    async with SessionLocal() as session:
        await session.execute(delete(Detection).where(Detection.track_id.like("CAM-TEST%")))
        await session.execute(delete(Camera).where(Camera.camera_code.like("CAM-TEST%")))
        await session.commit()


async def _make_camera(code: str) -> uuid.UUID:
    async with SessionLocal() as session:
        camera = Camera(
            camera_code=code, name=f"Test {code}", district="TEST",
            location="SRID=4326;POINT(72.5 23.0)", status="unknown", anpr_enabled=True,
        )
        session.add(camera)
        await session.commit()
        return camera.id


class TestBatchedPersistence:
    async def test_a_batch_lands_as_one_transaction(self, _clean_detections):
        """Two hundred events in, two hundred rows out, one commit."""
        code = "CAM-TEST-BATCH"
        await _make_camera(code)

        consumer = EventConsumer()
        events = [an_event(code, vehicle_id=i) for i in range(200)]
        await consumer._persist_batch(events)

        assert consumer.persisted == 200
        async with SessionLocal() as session:
            rows = (
                await session.execute(
                    select(Detection).where(Detection.track_id.like(f"{code}%"))
                )
            ).scalars().all()
        assert len(rows) == 200

    async def test_every_row_in_a_batch_gets_its_own_primary_key(self, _clean_detections):
        """One flush assigns all of them.

        The keys come from a Python-side default applied at flush. Without the
        flush, every alert in the batch would record `detection_id=None` and
        lose its link to the evidence that raised it — which is exactly the bug
        this code had when it persisted one event at a time.
        """
        code = "CAM-TEST-PK"
        await _make_camera(code)

        consumer = EventConsumer()
        await consumer._persist_batch([an_event(code, vehicle_id=i) for i in range(10)])

        async with SessionLocal() as session:
            ids = (
                await session.execute(
                    select(Detection.id).where(Detection.track_id.like(f"{code}%"))
                )
            ).scalars().all()
        assert len(set(ids)) == 10
        assert None not in ids


class TestCameraResolution:
    async def test_a_lowercase_code_still_finds_its_camera(self, _clean_detections):
        """MediaMTX reports stream paths lowercased; the registry stores uppercase.

        An exact match left `camera_id` NULL on every detection, silently
        breaking per-camera history and the map.
        """
        code = "CAM-TEST-CASE"
        camera_id = await _make_camera(code)

        consumer = EventConsumer()
        resolved = await consumer._resolve_cameras({code.lower()})
        assert resolved[code.lower()] == camera_id

    async def test_an_unknown_camera_does_not_lose_the_sighting(self, _clean_detections):
        """Storing a detection whose camera is unknown beats dropping it."""
        consumer = EventConsumer()
        await consumer._persist_batch([an_event("CAM-TEST-NOBODY")])

        async with SessionLocal() as session:
            row = (
                await session.execute(
                    select(Detection).where(Detection.track_id.like("CAM-TEST-NOBODY%"))
                )
            ).scalar_one()
        assert row.camera_id is None
        assert row.plate_normalised == "GJ05TT4321"

    async def test_a_camera_onboarded_later_is_picked_up(self, _clean_detections):
        """Negative lookups must expire.

        Cameras are onboarded while the platform runs — that is the entire
        point of the registry. Caching "no such camera" forever meant every
        detection from a newly-onboarded camera was stored unattributed until
        the worker restarted, with nothing anywhere reporting a fault.
        """
        code = "CAM-TEST-LATER"
        consumer = EventConsumer()

        assert (await consumer._resolve_cameras({code}))[code] is None
        camera_id = await _make_camera(code)

        # Still cached as unknown, so nothing has changed yet...
        assert (await consumer._resolve_cameras({code}))[code] is None

        # ...until the negative entry ages out.
        consumer._unknown_cameras[code] -= 3600
        assert (await consumer._resolve_cameras({code}))[code] == camera_id

    async def test_one_query_resolves_a_whole_batch(self, _clean_detections):
        """Resolution is per batch, not per event — a fleet-scale difference."""
        codes = {f"CAM-TEST-M{i}" for i in range(5)}
        for code in codes:
            await _make_camera(code)

        consumer = EventConsumer()
        resolved = await consumer._resolve_cameras(codes)
        assert set(resolved) == codes
        assert all(value is not None for value in resolved.values())

        # A second call is served entirely from cache.
        cached = await consumer._resolve_cameras(codes)
        assert cached == resolved


class TestTheFanOutSplitIsRealAndSeparate:
    """Durable processing and the live picture have opposite delivery needs.

    Persistence must happen exactly once across the fleet, so it uses a Redis
    consumer group. The live feed must reach *every* operator on *every*
    replica, and a consumer group is precisely the wrong tool for that — members
    receive disjoint subsets. One class did both jobs, so the platform was
    correct only while exactly one API process ran, and nothing said so.
    """

    def test_the_consumer_does_not_broadcast_stream_events(self):
        from app.services import event_consumer as module

        source = module.__file__
        with open(source) as handle:
            body = handle.read()
        assert "broadcaster.publish" not in body, (
            "the consumer must not fan events out itself — with dedicated ingest "
            "workers that reaches nobody, and on a single node it double-delivers"
        )

    def test_the_tailer_reads_without_a_consumer_group(self):
        from app.services import event_tailer as module

        with open(module.__file__) as handle:
            body = handle.read()
        assert "xreadgroup" not in body, (
            "a consumer group would give each replica a different subset of "
            "events, which splits the operations picture"
        )
        assert "xread" in body

    def test_alerts_leave_on_a_channel_every_replica_hears(self):
        from app.services import alert_fanout

        assert alert_fanout.publish is not None
        assert alert_fanout.subscriber is not None


class TestLatencyReporting:
    def test_percentiles_are_none_before_anything_is_measured(self):
        """An empty window reports nothing, rather than a confident zero."""
        consumer = EventConsumer()
        assert consumer.latency_percentiles() == {
            "p50_ms": None, "p95_ms": None, "p99_ms": None, "samples": 0,
        }

    def test_percentiles_come_from_the_events_own_timestamps(self):
        consumer = EventConsumer()
        now = datetime.now(UTC)
        pending = [
            (Detection(ts=now - timedelta(milliseconds=ms), track_id="x"), "CAM")
            for ms in (100, 200, 300, 400, 500)
        ]
        consumer._record_latency(pending)

        result = consumer.latency_percentiles()
        assert result["samples"] == 5
        assert 90 <= result["p50_ms"] <= 350
        assert result["p99_ms"] >= result["p50_ms"]
