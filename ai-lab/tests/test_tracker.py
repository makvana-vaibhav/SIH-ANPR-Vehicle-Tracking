"""ByteTrack behaviour on synthetic motion."""

from __future__ import annotations

from ailab.config import TrackerConfig
from ailab.track.bytetrack import ByteTracker
from ailab.types import BBox, Detection


def detection(x: float, y: float, confidence: float = 0.9, frame: int = 0) -> Detection:
    return Detection(
        bbox=BBox(x, y, x + 80, y + 60),
        confidence=confidence,
        class_id=2,
        class_name="car",
        frame_index=frame,
        t_s=frame / 25.0,
    )


def test_identity_is_stable_across_frames() -> None:
    tracker = ByteTracker(TrackerConfig(), frame_rate=25.0)
    ids = []
    for frame in range(12):
        tracked = tracker.update([detection(100 + frame * 12, 200, frame=frame)], frame, frame / 25)
        if tracked:
            ids.append(tracked[0].track_id)
    assert len(set(ids)) == 1, f"identity changed mid-track: {ids}"


def test_two_vehicles_get_separate_identities() -> None:
    tracker = ByteTracker(TrackerConfig(), frame_rate=25.0)
    seen: set[int] = set()
    for frame in range(10):
        tracked = tracker.update(
            [detection(100 + frame * 10, 200, frame=frame),
             detection(500 - frame * 10, 300, frame=frame)],
            frame, frame / 25,
        )
        seen.update(d.track_id for d in tracked if d.track_id)
    assert len(seen) == 2


def test_low_confidence_frames_do_not_break_a_track() -> None:
    """The point of ByteTrack: a vehicle dimming to 0.25 keeps its identity.

    A new identity here would mean the same car produces two partial plate
    results instead of one well-supported one.
    """
    tracker = ByteTracker(TrackerConfig(), frame_rate=25.0)
    ids = []
    for frame in range(15):
        # Frames 6-9 simulate an occlusion: still detected, but weakly.
        confidence = 0.25 if 6 <= frame <= 9 else 0.9
        tracked = tracker.update(
            [detection(100 + frame * 12, 200, confidence, frame)], frame, frame / 25
        )
        if tracked:
            ids.append(tracked[0].track_id)
    assert len(set(ids)) == 1, f"low-confidence frames split the track: {ids}"


def test_track_survives_a_short_gap() -> None:
    """A vehicle behind a pole for three frames is the same vehicle after it."""
    tracker = ByteTracker(TrackerConfig(), frame_rate=25.0)
    first = None
    for frame in range(6):
        tracked = tracker.update([detection(100 + frame * 12, 200, frame=frame)], frame, 0)
        if tracked:
            first = tracked[0].track_id

    for frame in range(6, 9):
        tracker.update([], frame, 0)

    last = None
    for frame in range(9, 14):
        tracked = tracker.update([detection(100 + frame * 12, 200, frame=frame)], frame, 0)
        if tracked:
            last = tracked[0].track_id
    assert first is not None and last == first


def test_track_expires_after_a_long_absence() -> None:
    tracker = ByteTracker(TrackerConfig(track_buffer=5), frame_rate=25.0)
    for frame in range(6):
        tracker.update([detection(100, 200, frame=frame)], frame, 0)
    for frame in range(6, 60):
        tracker.update([], frame, 0)

    tracked = tracker.update([detection(100, 200, frame=60)], 60, 0)
    # The original identity is long gone; a returning object is a new one.
    assert not tracked or tracked[0].track_id != 1


def test_empty_input_is_safe() -> None:
    tracker = ByteTracker(TrackerConfig(), frame_rate=25.0)
    assert tracker.update([], 0, 0.0) == []
