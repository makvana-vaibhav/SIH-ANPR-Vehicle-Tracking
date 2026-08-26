"""Live-stream reading and event emission.

The stream path differs from the batch path in ways that only show up under
load, so these tests exercise the behaviour that matters: dropping frames to
stay current, counting what was dropped, and emitting events a platform can key
on.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from ailab.stream.events import EventSink, SourceIdentity, vehicle_event
from ailab.stream.reader import StreamReader
from ailab.track.merge import Vehicle
from ailab.types import BBox, CropQuality, PlateCandidate, PlateConsensus, PlateDetection, PlateRead


@pytest.fixture(scope="module")
def short_clip(tmp_path_factory) -> Path:
    """A tiny synthetic clip, so reader tests do not depend on sample footage."""
    path = tmp_path_factory.mktemp("stream") / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (320, 240))
    assert writer.isOpened()
    for i in range(60):
        frame = np.full((240, 320, 3), i * 4 % 255, dtype=np.uint8)
        cv2.putText(frame, str(i), (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 3)
        writer.write(frame)
    writer.release()
    return path


class TestStreamReader:
    def test_reads_frames_from_a_file(self, short_clip: Path) -> None:
        with StreamReader(str(short_clip), realtime=False) as reader:
            seen = 0
            while seen < 5:
                frame = reader.read(timeout=2.0)
                if frame is None:
                    break
                seen += 1
            assert seen == 5

    def test_drops_frames_when_the_consumer_is_slow(self, short_clip: Path) -> None:
        """The core streaming property: stay current rather than fall behind.

        A consumer far slower than the source must not accumulate a backlog —
        it must see recent frames and a truthful count of what it missed.
        """
        with StreamReader(str(short_clip), realtime=True) as reader:
            delivered = 0
            deadline = time.perf_counter() + 2.5
            while time.perf_counter() < deadline:
                frame = reader.read(timeout=0.5)
                if frame is None and reader.finished:
                    break
                if frame is not None:
                    delivered += 1
                    time.sleep(0.12)      # a slow consumer

            stats = reader.stats
            assert stats.frames_decoded > delivered, "reader did not run ahead of the consumer"
            assert stats.frames_dropped > 0, "slow consumer should have caused drops"
            assert stats.frames_decoded == stats.frames_delivered + stats.frames_dropped

    def test_frames_carry_capture_time_for_latency(self, short_clip: Path) -> None:
        with StreamReader(str(short_clip), realtime=False) as reader:
            frame = reader.read(timeout=2.0)
            assert frame is not None
            assert frame.wall_time.tzinfo is not None, "event timestamps must be tz-aware"
            assert frame.age_ms >= 0.0

    def test_finishes_at_end_of_file(self, short_clip: Path) -> None:
        with StreamReader(str(short_clip), realtime=False) as reader:
            for _ in range(300):
                if reader.read(timeout=0.5) is None and reader.finished:
                    break
            assert reader.finished

    def test_unopenable_source_fails_loudly(self) -> None:
        with pytest.raises(RuntimeError, match="could not open"):
            StreamReader("/nonexistent/camera.mp4").start()

    def test_stats_are_serialisable(self, short_clip: Path) -> None:
        with StreamReader(str(short_clip), realtime=False) as reader:
            reader.read(timeout=2.0)
            stats = reader.stats.to_dict()
        assert {"frames_decoded", "frames_dropped", "drop_rate", "decode_fps"} <= set(stats)


def make_vehicle(plate: str = "GJ03AB1234") -> Vehicle:
    read = PlateRead(
        text_raw=plate, text=plate, ocr_confidence=0.94, frame_index=10, t_s=0.4,
        track_id=3, engine="test",
        quality=CropQuality(210.0, 130.0, 46.0, 150, 44),
        grammar_valid=True, crop_path="crops/plates/x.jpg",
    )
    return Vehicle(
        vehicle_id=1, track_ids=[3], class_name="car", reads=[read],
        plate_detections=[
            PlateDetection(bbox=BBox(10, 20, 110, 50), confidence=0.88,
                           frame_index=10, t_s=0.4, track_id=3)
        ],
        result=PlateConsensus(
            text=plate, confidence=0.93, method="char_vote", reads_total=4,
            reads_agreeing=4, grammar_valid=True,
            candidates=[PlateCandidate(plate, 1.0, 4, True)],
        ),
        first_seen_s=0.2, last_seen_s=2.6, frames_tracked=40,
        mean_detection_confidence=0.9, best_crop_path="crops/vehicles/v.jpg",
    )


class TestEvents:
    def test_event_carries_what_the_platform_needs(self) -> None:
        source = SourceIdentity(
            camera_id="GJ-RJT-0042", name="Kalawad Road", site="Rajkot",
            location={"lat": 22.29, "lon": 70.79},
        )
        event = vehicle_event(make_vehicle(), source, latency_ms=182.5)

        assert event["source"]["camera_id"] == "GJ-RJT-0042"
        assert event["source"]["location"]["lat"] == 22.29
        assert event["plate"]["text"] == "GJ03AB1234"
        assert event["plate"]["state"] == "GJ"
        assert event["plate"]["bbox"] is not None
        assert event["vehicle"]["type"] == "car"
        assert event["vehicle"]["track_ids"] == [3]
        assert event["latency_ms"] == 182.5
        assert event["evidence"]["plate_crop"] == "crops/plates/x.jpg"
        # Alternatives travel with the event so the platform can widen a
        # watchlist match rather than trusting one string.
        assert event["plate"]["candidates"]

    def test_unreadable_plate_is_explicit_not_missing(self) -> None:
        vehicle = make_vehicle()
        vehicle.result = None
        vehicle.reads = []
        event = vehicle_event(vehicle, SourceIdentity(camera_id="C1"))
        assert event["plate"]["readable"] is False
        assert event["plate"]["text"] == ""

    def test_event_kinds_are_distinguished(self) -> None:
        source = SourceIdentity(camera_id="C1")
        provisional = vehicle_event(make_vehicle(), source, kind="vehicle.observed")
        final = vehicle_event(make_vehicle(), source, kind="vehicle.completed")
        assert provisional["event"] == "vehicle.observed"
        assert final["event"] == "vehicle.completed"

    def test_event_is_json_serialisable(self) -> None:
        event = vehicle_event(make_vehicle(), SourceIdentity(camera_id="C1"))
        assert json.loads(json.dumps(event, default=str))["plate"]["text"] == "GJ03AB1234"


class TestEventSink:
    def test_writes_one_json_object_per_line(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        with EventSink(path=path) as sink:
            for _ in range(3):
                sink.emit(vehicle_event(make_vehicle(), SourceIdentity(camera_id="C1")))
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 3
        assert all(json.loads(line)["plate"]["text"] == "GJ03AB1234" for line in lines)

    def test_counts_by_kind(self, tmp_path: Path) -> None:
        with EventSink(path=tmp_path / "e.jsonl") as sink:
            sink.emit(vehicle_event(make_vehicle(), SourceIdentity(camera_id="C1"),
                                    kind="vehicle.observed"))
            sink.emit(vehicle_event(make_vehicle(), SourceIdentity(camera_id="C1"),
                                    kind="vehicle.completed"))
            assert sink.by_kind == {"vehicle.observed": 1, "vehicle.completed": 1}

    def test_callback_receives_events(self, tmp_path: Path) -> None:
        """A live consumer should be able to react without tailing a file."""
        received: list[dict] = []
        with EventSink(path=None, on_event=received.append) as sink:
            sink.emit(vehicle_event(make_vehicle(), SourceIdentity(camera_id="C1")))
        assert len(received) == 1


def test_event_carries_the_vehicle_bounding_box() -> None:
    """The platform needs to place the vehicle, not just the plate."""
    vehicle = make_vehicle()
    vehicle.bbox = BBox(300, 200, 700, 520)
    event = vehicle_event(vehicle, SourceIdentity(camera_id="C1"))
    assert event["vehicle"]["bbox"] is not None
    assert event["vehicle"]["bbox"]["w"] == 400.0
