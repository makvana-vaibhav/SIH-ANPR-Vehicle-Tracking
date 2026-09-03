"""Live-stream reading and event emission.

The stream path differs from the batch path in ways that only show up under
load, so these tests exercise the behaviour that matters: dropping frames to
stay current, counting what was dropped, and emitting events a platform can key
on.
"""

from __future__ import annotations

import json
import time
from itertools import pairwise
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


class TestSentinelIntegrationRules:
    """The rules the Sentinel sandbox integration guide is explicit about.

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
