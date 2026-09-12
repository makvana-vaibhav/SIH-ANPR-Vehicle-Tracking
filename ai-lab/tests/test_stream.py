"""Live-stream reading and event emission.

The stream path differs from the batch path in ways that only show up under
load, so these tests exercise the behaviour that matters: dropping frames to
stay current, counting what was dropped, and emitting events a platform can key
on.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import cv2
import numpy as np
import pytest

from ailab.config import RunConfig
from ailab.stream.events import EventSink, SourceIdentity, vehicle_event
from ailab.stream.reader import StreamReader
from ailab.stream.runner import StreamRunner, _LiveTrack
from ailab.track.merge import Vehicle
from ailab.types import (
    BBox,
    CropQuality,
    PlateCandidate,
    PlateConsensus,
    PlateDetection,
    PlateRead,
    StageTimer,
    Track,
    TrackObservation,
)


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
        """And says *why*, not merely that it failed.

        "could not open" sends whoever reads it looking in the wrong place
        when the real answer is a rejected password or a wrong camera id.
        """
        from ailab.stream.reader import StreamUnavailable

        with pytest.raises(StreamUnavailable) as caught:
            StreamReader("/nonexistent/camera.mp4").start()

        assert "/nonexistent/camera.mp4" in str(caught.value)
        assert caught.value.reason

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


class TestMotionOnTheEvent:
    """Which way the vehicle went, and whether it went anywhere at all.

    This is what makes density, queues and stopped vehicles computable from a
    record that holds one row per vehicle rather than one per frame. It is
    image-space only — turning it into a compass direction needs
    `cameras.heading_deg`, which the platform owns.
    """

    FRAME = (1920, 1080)  # stationary below 0.03 * 1080 = 32.4 px

    def _vehicle(self, dx: float | None, dy: float | None) -> Vehicle:
        vehicle = make_vehicle()
        vehicle.motion_dx = dx
        vehicle.motion_dy = dy
        vehicle.motion_px = None if dx is None or dy is None else math.hypot(dx, dy)
        return vehicle

    def _direction(self, dx: float | None, dy: float | None) -> str:
        event = vehicle_event(
            self._vehicle(dx, dy), SourceIdentity(camera_id="C1"), frame_size=self.FRAME
        )
        return str(event["vehicle"]["motion"]["direction"])

    def test_downward_travel_is_approaching(self) -> None:
        assert self._direction(20.0, 400.0) == "approaching"

    def test_upward_travel_is_receding(self) -> None:
        assert self._direction(-10.0, -350.0) == "receding"

    def test_lateral_travel_is_crossing(self) -> None:
        assert self._direction(500.0, 30.0) == "crossing_right"
        assert self._direction(-420.0, -12.0) == "crossing_left"

    def test_a_vehicle_that_barely_moved_is_stationary(self) -> None:
        """Box jitter over a long dwell is what a queue looks like."""
        assert self._direction(8.0, 11.0) == "stationary"

    def test_unmeasurable_motion_is_unknown_not_stationary(self) -> None:
        """A one-sighting detector flicker must not read as a parked car.

        The obstruction detector keys on `stationary`, so collapsing "we could
        not measure" into "it did not move" would invent stopped vehicles out
        of tracker noise.
        """
        assert self._direction(None, None) == "unknown"

    def test_no_frame_size_means_unknown(self) -> None:
        """Without the frame, 'barely moved' has no scale to be judged against.

        This fleet mixes 720p and 1080p, so the threshold is a fraction of the
        frame rather than a pixel count — and with no frame there is no
        honest answer.
        """
        event = vehicle_event(self._vehicle(0.0, 400.0), SourceIdentity(camera_id="C1"))
        assert event["vehicle"]["motion"]["direction"] == "unknown"
        assert event["vehicle"]["motion"]["distance_px"] is None


class TestBoxesAnOverlayCanDraw:
    """What an overlay needs in order to put a box on the vehicle.

    `vehicle.bbox` answers "which frame is the best evidence" and
    `vehicle.live_bbox` answers "where is it now". They are different questions
    and, for a vehicle crossing the frame, different answers: drawing the first
    over live video puts the rectangle wherever the vehicle looked biggest,
    which is a position it left seconds ago.
    """

    def test_live_bbox_is_carried_separately_from_the_best_bbox(self) -> None:
        vehicle = make_vehicle()
        vehicle.bbox = BBox(300, 200, 700, 520)
        event = vehicle_event(
            vehicle,
            SourceIdentity(camera_id="C1"),
            live_bbox=BBox(120, 210, 420, 480),
        )
        assert event["vehicle"]["bbox"]["x1"] == 300.0
        assert event["vehicle"]["live_bbox"]["x1"] == 120.0

    def test_live_bbox_is_null_rather_than_guessed(self) -> None:
        """A consumer must be able to tell "not sent" from "same as best"."""
        event = vehicle_event(make_vehicle(), SourceIdentity(camera_id="C1"))
        assert event["vehicle"]["live_bbox"] is None

    def test_capture_time_is_distinct_from_emit_time(self) -> None:
        """The gap between the two is the pipeline, and it is what an overlay
        has to schedule against. Reusing `event_time` would place every box
        wherever the vehicle had got to by the time the read finished."""
        captured = datetime(2026, 9, 11, 10, 31, 4, 200000, tzinfo=UTC)
        event = vehicle_event(
            make_vehicle(),
            SourceIdentity(camera_id="C1"),
            kind="vehicle.observed",
            latency_ms=820.0,
            captured_at=captured,
        )
        assert event["captured_at"] == captured.isoformat()
        assert event["captured_at"] != event["event_time"]
        assert event["latency_ms"] == 820.0

    def test_capture_time_is_null_when_unknown(self) -> None:
        event = vehicle_event(make_vehicle(), SourceIdentity(camera_id="C1"))
        assert event["captured_at"] is None

    def test_event_with_both_boxes_is_json_serialisable(self) -> None:
        event = vehicle_event(
            make_vehicle(),
            SourceIdentity(camera_id="C1"),
            live_bbox=BBox(1, 2, 3, 4),
            captured_at=datetime.now(UTC),
        )
        assert json.loads(json.dumps(event, default=str))["vehicle"]["live_bbox"]


class TestPositionRefreshesAreLean:
    """A refresh exists to move a rectangle, and carries nothing else.

    The full event carries one entry per frame the plate was read in, each with
    its crop path, plus every candidate string and every per-character
    confidence — and it grows for as long as the vehicle stays in view. A
    refresh is emitted several times a second per vehicle, is never persisted,
    and the only thing any consumer can do with it is redraw a box. Sending the
    full payload made the message whose entire job was to be prompt into the
    largest one on the bus.
    """

    def _refresh(self) -> dict:
        vehicle = make_vehicle()
        # A vehicle in view for a while: the evidence list is what grows, and
        # it is the whole reason this matters.
        vehicle.reads = list(vehicle.reads) * 12
        vehicle.bbox = BBox(300, 200, 700, 520)
        return vehicle_event(
            vehicle,
            SourceIdentity(camera_id="CAM-DEMO-01"),
            kind="vehicle.observed",
            latency_ms=271.4,
            frame_size=(1920, 1080),
            live_bbox=BBox(120, 210, 420, 480),
            captured_at=datetime(2026, 9, 11, 10, 31, 4, 200000, tzinfo=UTC),
            position_refresh=True,
        )

    def test_it_carries_everything_an_overlay_draws_with(self) -> None:
        event = self._refresh()

        # The clock to schedule against, and the pipeline's own latency.
        assert event["captured_at"] == "2026-09-11T10:31:04.200000+00:00"
        assert event["latency_ms"] == 271.4
        # The frame the coordinates are in. Without it they cannot be scaled,
        # and an overlay drops the event rather than guess a resolution.
        assert event["frame"] == {"width": 1920, "height": 1080}
        # Where the vehicle is now, and where it looked best. An overlay draws
        # the first and falls back to the second.
        assert event["vehicle"]["live_bbox"]["x1"] == 120.0
        assert event["vehicle"]["bbox"]["x1"] == 300.0
        assert event["vehicle"]["track_ids"] == [3]
        # Enough of the plate to label the box and colour it.
        assert event["plate"]["text"] == "GJ03AB1234"
        assert event["plate"]["grammar_valid"] is True
        assert event["plate"]["ambiguous"] is False
        assert event["plate"]["bbox"] is not None
        assert event["position_refresh"] is True
        assert event["event"] == "vehicle.observed"

    def test_it_carries_none_of_the_evidence(self) -> None:
        event = self._refresh()

        # Not "empty" — absent. A consumer must not be able to read a refresh
        # as a sighting with no evidence behind it.
        assert "evidence" not in event
        assert "candidates" not in event["plate"]
        assert "char_confidences" not in event["plate"].get("evidence", {})
        assert "motion" not in event["vehicle"]

    def test_it_is_several_times_smaller_than_the_full_event(self) -> None:
        vehicle = make_vehicle()
        vehicle.reads = list(vehicle.reads) * 12
        shared = {
            "source": SourceIdentity(camera_id="CAM-DEMO-01"),
            "kind": "vehicle.observed",
            "latency_ms": 271.4,
            "frame_size": (1920, 1080),
            "live_bbox": BBox(120, 210, 420, 480),
            "captured_at": datetime(2026, 9, 11, 10, 31, 4, tzinfo=UTC),
        }
        full = vehicle_event(vehicle, position_refresh=False, **shared)
        lean = vehicle_event(vehicle, position_refresh=True, **shared)

        full_bytes = len(json.dumps(full, default=str))
        lean_bytes = len(json.dumps(lean, default=str))
        # Measured at 3,787 against 793 for this vehicle. Asserted as a ratio
        # rather than a byte count so adding a field to either payload does not
        # fail the test spuriously — the property is that a refresh stays an
        # order cheaper, not that it is exactly this many bytes.
        assert lean_bytes * 3 < full_bytes

    def test_a_full_event_still_carries_its_evidence(self) -> None:
        """The trimming must apply to refreshes and nothing else."""
        event = vehicle_event(
            make_vehicle(),
            SourceIdentity(camera_id="C1"),
            kind="vehicle.observed",
            position_refresh=False,
        )
        assert event["evidence"]["plate_crop"] == "crops/plates/x.jpg"
        assert event["plate"]["candidates"]
        assert event["vehicle"]["motion"]["direction"]

    def test_it_is_json_serialisable(self) -> None:
        assert json.loads(json.dumps(self._refresh(), default=str))["position_refresh"]


class _StubPipeline:
    """Everything `StreamRunner.__init__` asks of a pipeline, and nothing more.

    The batch logic under test is pure bookkeeping over `self._live` — pacing,
    the cut point, and the one final empty message. Loading five ONNX sessions
    to exercise it would make these tests slow, model-dependent and much worse
    at saying what broke.
    """

    def __init__(self) -> None:
        self.timer = StageTimer()

    def reset_run_state(self) -> None:
        return None


@dataclass
class _FakeCapture:
    """The two fields `_emit_track_batch` reads off a captured frame."""

    wall_time: datetime
    age_ms: float


def _runner(sink: EventSink, batch_s: float = 0.2) -> StreamRunner:
    config = RunConfig()
    config.stream.track_batch_s = batch_s
    runner = StreamRunner(
        config,
        SourceIdentity(camera_id="CAM-DEMO-01"),
        sink,
        pipeline=_StubPipeline(),  # type: ignore[arg-type]
    )
    runner._frame_size = (1920, 1080)
    return runner


def _live_track(
    runner: StreamRunner,
    track_id: int,
    *,
    located: bool = True,
    read: str = "",
) -> Track:
    """Put one vehicle in view, at a chosen stage of being read."""
    track = Track(track_id=track_id, class_name="car", class_id=2)
    track.observe(
        TrackObservation(
            frame_index=10,
            t_s=0.4,
            bbox=BBox(120, 210, 420, 480),
            confidence=0.9,
            detected=True,
        )
    )
    if located:
        track.plate_location = PlateDetection(
            bbox=BBox(300, 400, 390, 422),
            confidence=0.88,
            frame_index=10,
            t_s=0.4,
            track_id=track_id,
        )
        # The vehicle box on the frame the plate was found on. Here it is the
        # same frame as the observation above, so the plate needs no carrying
        # and the batch reports it exactly as measured.
        track.plate_location_vehicle = BBox(120, 210, 420, 480)
    if read:
        track.result = PlateConsensus(
            text=read, confidence=0.93, method="char_vote", reads_total=4,
            reads_agreeing=4, grammar_valid=True,
        )
    runner._live[track_id] = _LiveTrack(track=track, last_frame_seen=10)
    return track


def _capture() -> _FakeCapture:
    return _FakeCapture(
        wall_time=datetime(2026, 9, 12, 10, 31, 4, 200000, tzinfo=UTC),
        age_ms=271.4,
    )


class TestTheLiveBoxesChannel:
    """`camera.tracks`: every drawable vehicle on one camera, in one message.

    This channel exists because an overlay needs a box per vehicle several
    times a second, and sending that as one event per vehicle made the traffic
    whose whole purpose is promptness scale with the number of vehicles — which
    is exactly when promptness matters. It is the picture, not the intelligence:
    never persisted, never counted, never listed as a sighting.
    """

    def test_a_located_plate_is_published_before_anything_has_read_it(self) -> None:
        """The cut point, and the reason the channel exists.

        The plate detector has found a plate and can say where it is. OCR has
        not read it and may never — measured on this fleet, two-thirds of
        tracked vehicles never yield a plate at all. Waiting for text left
        those two-thirds with no box and the rest with a late one.
        """
        events: list[dict] = []
        runner = _runner(EventSink(on_event=events.append))
        _live_track(runner, 7, located=True, read="")

        runner._emit_track_batch(_capture())

        assert len(events) == 1
        event = events[0]
        assert event["event"] == "camera.tracks"
        entry = event["tracks"][0]
        assert entry["track_id"] == 7
        assert entry["plate_bbox"] == [300.0, 400.0, 390.0, 422.0]
        assert entry["plate_detection_confidence"] == 0.88
        # No reading exists, so no reading is claimed. Absence is the signal,
        # not an empty string a consumer might print.
        assert "plate" not in entry
        assert "confidence" not in entry

    def test_a_read_plate_carries_its_text_and_verdicts(self) -> None:
        events: list[dict] = []
        runner = _runner(EventSink(on_event=events.append))
        _live_track(runner, 7, read="GJ03AB1234")

        runner._emit_track_batch(_capture())

        entry = events[0]["tracks"][0]
        assert entry["plate"] == "GJ03AB1234"
        assert entry["confidence"] == 0.93
        # The worker reports the facts; whether they clear a display gate is
        # the overlay's policy, so both verdicts travel rather than a verdict.
        assert entry["grammar_valid"] is True
        assert entry["ambiguous"] is False

    def test_a_vehicle_with_no_located_plate_is_not_published(self) -> None:
        """Tracked is not drawable, under the cut point this channel uses.

        Publishing every tracked vehicle would be earlier still and would cover
        the vehicles that never yield a plate. It was considered and rejected:
        the box is gated on the detector having found a plate, so a rectangle
        never appears over a vehicle the system has no plate evidence for.
        """
        events: list[dict] = []
        runner = _runner(EventSink(on_event=events.append))
        _live_track(runner, 7, located=False)

        runner._emit_track_batch(_capture())

        assert events == []

    def test_the_batch_is_paced_not_emitted_per_frame(self) -> None:
        events: list[dict] = []
        runner = _runner(EventSink(on_event=events.append), batch_s=0.2)
        _live_track(runner, 7)

        runner._emit_track_batch(_capture())
        runner._emit_track_batch(_capture())
        runner._emit_track_batch(_capture())

        # Three frames, one message: a batch is a redraw, and redrawing faster
        # than the cadence says nothing new.
        assert len(events) == 1

    def test_an_emptied_camera_says_so_exactly_once(self) -> None:
        """Absence from the batch is how a consumer learns a vehicle has gone.

        That only works if a message arrives. A camera whose last vehicle has
        left must publish one empty batch — and then stay quiet, because a
        camera watching an empty road has no business publishing at 5 Hz.
        """
        events: list[dict] = []
        runner = _runner(EventSink(on_event=events.append))
        _live_track(runner, 7)
        runner._emit_track_batch(_capture())
        assert events[0]["tracks"]

        runner._live.clear()
        runner._last_batch_s = 0.0  # fast-forward the pacer
        runner._emit_track_batch(_capture())
        assert events[-1]["tracks"] == []
        assert len(events) == 2

        runner._last_batch_s = 0.0
        runner._emit_track_batch(_capture())
        assert len(events) == 2

    def test_it_carries_the_clock_the_boxes_belong_to(self) -> None:
        events: list[dict] = []
        runner = _runner(EventSink(on_event=events.append))
        _live_track(runner, 7)

        runner._emit_track_batch(_capture())

        event = events[0]
        # The frame the boxes were measured on, not the moment the message was
        # built. An overlay places boxes against this or it places them wrong.
        assert event["captured_at"] == "2026-09-12T10:31:04.200000+00:00"
        assert event["captured_at"] != event["event_time"]
        assert event["latency_ms"] == 271.4
        assert event["frame"] == {"width": 1920, "height": 1080}

    def test_boxes_are_four_numbers_not_six(self) -> None:
        """`w` and `h` are `x2 - x1` and `y2 - y1`.

        Every other event sends `BBox.to_dict()` and that is right for them.
        Here the redundancy repeats per box per vehicle per tick, which is the
        one place in this schema where byte count is a design constraint.
        """
        events: list[dict] = []
        runner = _runner(EventSink(on_event=events.append))
        _live_track(runner, 7)

        runner._emit_track_batch(_capture())

        assert events[0]["tracks"][0]["bbox"] == [120.0, 210.0, 420.0, 480.0]

    def test_one_batch_is_cheaper_than_one_event_per_vehicle(self) -> None:
        """The whole argument for the channel, as a number."""
        batched: list[dict] = []
        runner = _runner(EventSink(on_event=batched.append))
        for track_id in range(10):
            _live_track(runner, track_id, read="GJ03AB1234" if track_id % 3 == 0 else "")
        runner._emit_track_batch(_capture())

        vehicle = make_vehicle()
        vehicle.reads = list(vehicle.reads) * 12
        per_vehicle = vehicle_event(
            vehicle,
            SourceIdentity(camera_id="CAM-DEMO-01"),
            kind="vehicle.observed",
            latency_ms=271.4,
            frame_size=(1920, 1080),
            live_bbox=BBox(120, 210, 420, 480),
            captured_at=datetime(2026, 9, 12, 10, 31, 4, tzinfo=UTC),
            position_refresh=True,
        )

        batch_bytes = len(json.dumps(batched[0], default=str))
        refresh_bytes = len(json.dumps(per_vehicle, default=str)) * 10
        # Measured: 1,987 bytes for ten vehicles in one envelope against 7,150
        # for ten envelopes, a factor of 3.6. Asserted as a ratio rather than a
        # byte count so adding a field does not fail this spuriously; the
        # property under test is that the drawing channel stays several times
        # cheaper than sending the same boxes one vehicle at a time, and that
        # its cost grows with the camera count rather than the traffic.
        assert batch_bytes * 3 < refresh_bytes

    def test_it_is_json_serialisable(self) -> None:
        events: list[dict] = []
        runner = _runner(EventSink(on_event=events.append))
        _live_track(runner, 7, read="GJ03AB1234")
        runner._emit_track_batch(_capture())

        assert json.loads(json.dumps(events[0], default=str))["tracks"][0]["plate"]


class TestThePlateBoxTravelsWithTheVehicle:
    """A localised plate must not be left behind by the car it is on.

    The scheduler stops searching a vehicle once its plate has converged, so
    after that the last localisation is all there will ever be — while the
    vehicle carries on across the frame. Reported as the absolute box it was
    measured as, the firmest rectangle on screen becomes the most stale one.
    """

    def test_a_plate_is_carried_onto_the_vehicles_current_box(self) -> None:
        # Plate at the bottom middle of a 300x270 vehicle box, then the vehicle
        # moves 200px right and 30px down without changing size. The plate has
        # to move with it: a number plate does not slide about on a car.
        anchored = StreamRunner._anchored_plate(
            BBox(300, 400, 390, 422),
            BBox(120, 210, 420, 480),
            BBox(320, 240, 620, 510),
        )
        assert anchored.x1 == pytest.approx(500.0)
        assert anchored.y1 == pytest.approx(430.0)
        assert anchored.x2 == pytest.approx(590.0)
        assert anchored.y2 == pytest.approx(452.0)

    def test_it_grows_with_a_vehicle_coming_closer(self) -> None:
        # The vehicle box doubles in size as the car approaches. A plate held at
        # a fixed pixel size on a vehicle twice as large no longer covers the
        # plate, so the fractions scale rather than translate.
        anchored = StreamRunner._anchored_plate(
            BBox(150, 200, 250, 220),
            BBox(100, 100, 300, 300),
            BBox(100, 100, 500, 500),
        )
        assert anchored.x1 == pytest.approx(200.0)
        assert anchored.x2 == pytest.approx(400.0)
        assert anchored.width == pytest.approx(200.0)

    def test_it_reports_the_measured_box_when_there_is_no_anchor(self) -> None:
        """Nothing to anchor against is not licence to invent a position."""
        plate = BBox(300, 400, 390, 422)
        assert StreamRunner._anchored_plate(plate, None, BBox(0, 0, 10, 10)) is plate
        assert (
            StreamRunner._anchored_plate(plate, BBox(5, 5, 5, 5), BBox(0, 0, 10, 10))
            is plate
        )

    def test_the_batch_carries_the_anchored_box(self) -> None:
        events: list[dict] = []
        runner = _runner(EventSink(on_event=events.append))
        track = _live_track(runner, 7)
        # The vehicle has moved on since the plate was localised: a later
        # observation, and no new plate search because the plate converged.
        track.observe(
            TrackObservation(
                frame_index=40,
                t_s=1.6,
                bbox=BBox(320, 240, 620, 510),
                confidence=0.9,
                detected=True,
            )
        )

        runner._emit_track_batch(_capture())

        entry = events[0]["tracks"][0]
        assert entry["bbox"] == [320.0, 240.0, 620.0, 510.0]
        # Carried with the vehicle, not left at [300, 400, 390, 422].
        assert entry["plate_bbox"] == [500.0, 430.0, 590.0, 452.0]


class TestNagarNetraIntegrationRules:
    """The rules the hosted grid integration guide is explicit about.

    Each of these was a real defect in the first implementation, and each fails
    in a way that looks like a model bug rather than a transport bug — which is
    precisely why they are worth pinning down in tests.
    """

    def test_rtsp_is_forced_over_tcp(self) -> None:
        """UDP "fails across NAT and most corporate firewalls", and partial UDP
        delivery produces corrupt frames that look like detector errors."""
        import os

        import ailab.stream.reader  # noqa: F401  (import sets the option)

        assert "rtsp_transport;tcp" in os.environ.get("OPENCV_FFMPEG_CAPTURE_OPTIONS", "")

    def test_timing_comes_from_pts_not_arrival(self, short_clip: Path) -> None:
        """Arrival time is wrong after every connection.

        The gateway replays a buffered GOP at join, so early frames arrive
        faster than real time. Timestamps must track the stream, not the clock.
        """
        with StreamReader(str(short_clip), realtime=False) as reader:
            stamps = []
            for _ in range(8):
                frame = reader.read(timeout=2.0)
                if frame is None:
                    break
                stamps.append(frame.source_t_s)

        assert len(stamps) >= 4
        assert stamps == sorted(stamps), "presentation timestamps must not go backwards"
        # Read as fast as possible, so wall-clock spacing is far smaller than
        # the stream's own 1/30s cadence. If timing came from arrival, the
        # deltas would collapse toward zero.
        deltas = [b - a for a, b in pairwise(stamps)]
        assert max(deltas) > 0.0, "timestamps did not advance with the stream"

    def test_backoff_grows_and_is_capped(self) -> None:
        """~2s rising to ~30s, not a tight reconnect loop."""
        reader = StreamReader("rtsp://example.invalid/stream/1",
                              reconnect_delay_s=2.0, max_reconnect_delay_s=30.0)
        delays = []
        for _ in range(8):
            delays.append(reader._backoff_s)
            reader._backoff_s = min(reader.max_reconnect_delay_s, reader._backoff_s * 2.0)

        assert delays[0] == 2.0
        assert delays[1] == 4.0
        assert delays[-1] == 30.0, "backoff must cap, not grow without bound"
        assert all(b >= a for a, b in pairwise(delays))

    def test_a_backwards_pts_jump_is_reported_as_a_discontinuity(self) -> None:
        """The recording loops; the scene cuts. Long-lived state must rebuild."""

        class FakeCapture:
            def __init__(self, values: list[float]) -> None:
                self._values = values
                self._i = 0

            def get(self, _prop: int) -> float:
                value = self._values[self._i]
                self._i += 1
                return value

        from ailab.stream.reader import ReaderStats

        reader = StreamReader.__new__(StreamReader)
        reader.source = "rtsp://example/stream/1"
        reader.stats = ReaderStats()
        reader._fps = 25.0
        reader._pts_s = 0.0
        reader._last_pts_s = None
        reader._pts_offset_s = 0.0
        reader._pts_available = True

        capture = FakeCapture([1000.0, 2000.0, 3000.0, 100.0, 200.0])
        flags = [reader._read_pts(capture)[1] for _ in range(5)]

        assert flags == [False, False, False, True, False], "loop point not detected"
        assert reader.stats.discontinuities == 1

    def test_timestamps_stay_monotonic_across_a_loop(self) -> None:
        """Events must remain orderable even though the source clock reset."""

        class FakeCapture:
            def __init__(self, values: list[float]) -> None:
                self._values, self._i = values, 0

            def get(self, _prop: int) -> float:
                value = self._values[self._i]
                self._i += 1
                return value

        from ailab.stream.reader import ReaderStats

        reader = StreamReader.__new__(StreamReader)
        reader.source = "s"
        reader.stats = ReaderStats()
        reader._fps = 25.0
        reader._pts_s = 0.0
        reader._last_pts_s = None
        reader._pts_offset_s = 0.0
        reader._pts_available = True

        capture = FakeCapture([1000.0, 2000.0, 3000.0, 100.0, 200.0, 300.0])
        stamps = [reader._read_pts(capture)[0] for _ in range(6)]
        assert stamps == sorted(stamps), f"clock went backwards across the loop: {stamps}"

    def test_a_source_without_pts_falls_back_and_says_so(self) -> None:
        """Some sources report 0 forever. Invent nothing silently."""

        class NoPts:
            def get(self, _prop: int) -> float:
                return 0.0

        from ailab.stream.reader import ReaderStats

        reader = StreamReader.__new__(StreamReader)
        reader.source = "s"
        reader.stats = ReaderStats()
        reader._fps = 25.0
        reader._pts_s = 0.0
        reader._last_pts_s = None
        reader._pts_offset_s = 0.0
        reader._pts_available = True

        capture = NoPts()
        stamps = [reader._read_pts(capture)[0] for _ in range(10)]
        assert stamps == sorted(stamps)
        assert reader.stats.pts_unavailable is True, "fallback must be recorded, not hidden"


class TestBoxesCarryTheirCoordinateSpace:
    """A bbox in pixels is meaningless without the frame it was measured in.

    The command centre draws these boxes over a video element that is almost
    never the source resolution. Without `frame`, the client has to guess the
    scale, and a guess puts the box on the wrong car.
    """

    def _vehicle(self):
        from ailab.track.merge import Vehicle
        from ailab.types import BBox, PlateConsensus

        return Vehicle(
            vehicle_id=1,
            track_ids=[1],
            class_name="car",
            bbox=BBox(100.0, 200.0, 300.0, 400.0),
            result=PlateConsensus(
                text="GJ03AB1234", confidence=0.9, method="char_vote",
                reads_total=3, reads_agreeing=3,
            ),
        )

    def test_frame_size_travels_with_the_event(self) -> None:
        from ailab.stream.events import SourceIdentity, vehicle_event

        event = vehicle_event(
            self._vehicle(),
            SourceIdentity(camera_id="cam-1"),
            frame_size=(1920, 1080),
        )
        assert event["frame"] == {"width": 1920, "height": 1080}

    def test_frame_is_null_rather_than_absent_when_unknown(self) -> None:
        """A consumer must be able to tell "unknown" from "forgot to read it"."""
        from ailab.stream.events import SourceIdentity, vehicle_event

        event = vehicle_event(self._vehicle(), SourceIdentity(camera_id="cam-1"))
        assert "frame" in event
        assert event["frame"] is None

    def test_boxes_lie_inside_the_frame_they_declare(self) -> None:
        from ailab.stream.events import SourceIdentity, vehicle_event

        event = vehicle_event(
            self._vehicle(), SourceIdentity(camera_id="cam-1"), frame_size=(1920, 1080)
        )
        box, frame = event["vehicle"]["bbox"], event["frame"]
        assert 0 <= box["x1"] < box["x2"] <= frame["width"]
        assert 0 <= box["y1"] < box["y2"] <= frame["height"]


class TestCredentialsNeverReachTheLog:
    """RTSP carries credentials in the URL. Every line that prints a source
    is therefore a place a password can escape to disk.

    This is not hypothetical: adding grid credentials leaked them into the
    worker's log on the first attempt, in three separate places, because each
    log site had to be found by hand. These tests are the net.
    """

    URL = "rtsp://officer%40police.gov.in:s3cr3t-token@10.0.0.5:8554/stream/cam01"

    def test_the_password_is_replaced(self) -> None:
        from ailab.stream.reader import redact

        assert "s3cr3t-token" not in redact(self.URL)
        assert "***" in redact(self.URL)

    def test_the_rest_of_the_url_survives(self) -> None:
        """A redacted URL still has to be useful for diagnosing a camera."""
        from ailab.stream.reader import redact

        safe = redact(self.URL)
        assert "10.0.0.5:8554" in safe
        assert "/stream/cam01" in safe
        assert safe.startswith("rtsp://")

    def test_a_url_without_credentials_is_untouched(self) -> None:
        from ailab.stream.reader import redact

        plain = "rtsp://mediamtx:8554/cam-demo"
        assert redact(plain) == plain

    def test_every_log_call_that_prints_a_source_redacts_it(self) -> None:
        """A grep, deliberately.

        The failure mode is a *new* log line added later that prints a raw
        URL, and no unit test of existing behaviour would catch that. This
        reads the source and fails on the pattern.
        """
        import re
        from pathlib import Path

        def calls(text: str):
            """Whole `log.x(...)` calls, matched by balancing parentheses.

            A regex that stops at the first `)` would cut `redact(url)` in
            half and report the redaction as the leak.
            """
            for match in re.finditer(r"log\.\w+\(", text):
                index, depth = match.end(), 1
                while index < len(text) and depth:
                    depth += (text[index] == "(") - (text[index] == ")")
                    index += 1
                yield text[match.start() : index], text[: match.start()].count("\n") + 1

        # Which names hold a *URL* differs by file, and getting this wrong
        # produces a confident false positive: `self.source` is the stream URL
        # in reader.py, but a `SourceIdentity` object in runner.py.
        url_names = {
            "ailab/stream/reader.py": ("self.source",),
            "ailab/stream/runner.py": ("url",),
        }

        offenders: list[str] = []
        for name, names in url_names.items():
            path = Path(name)
            text = path.read_text()
            for call, line in calls(text):
                for holder in names:
                    named = re.search(
                        rf"(?<![\w.]){re.escape(holder)}\b(?!\s*[=.])", call
                    )
                    if named and f"redact({holder}" not in call:
                        offenders.append(f"{path.name}:{line}")
        assert not offenders, (
            "these log calls print a stream source without redacting it, so a "
            "federated grid's password would reach the log: " + ", ".join(offenders)
        )
