"""Where cameras sharing a clip are seeked to, and why it has to be measured.

Every camera in the demonstration fleet replays the same file. That is what
lets a vehicle genuinely pass more than one camera, which is what cross-camera
linking needs in order to have anything real to link. But three cameras showing
the *same instant* of the same clip publish the same vehicle at the same moment,
which downstream is indistinguishable from one plate appearing in three places
at once — and is correctly flagged implausible. Spreading them across the clip
is what prevents that.

How far they can be spread is a property of the footage, so `stagger_offset`
measures the clip rather than assuming a window. The rule these tests hold is
**never past the end**: an offset beyond a clip's duration yields no video at
all, which presents as a camera that is registered, publishing and black.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from simulator import publisher
from simulator.publisher import FALLBACK_WINDOW_S, stagger_offset


@pytest.fixture
def clip(monkeypatch: pytest.MonkeyPatch):
    """A clip of a stated duration, without touching ffprobe or the disk."""

    def _clip(duration: float | None) -> Path:
        monkeypatch.setattr(publisher, "probe_duration", lambda _source: duration)
        return Path("/data/videos/anpr_demo.mp4")

    return _clip


class TestSpreadingCamerasAcrossAClip:
    def test_the_first_camera_starts_at_the_beginning(self, clip) -> None:
        assert stagger_offset(clip(60.0), slot=0, sharing=3) == 0.0

    def test_a_camera_with_the_clip_to_itself_is_not_staggered(self, clip) -> None:
        """Nothing to desynchronise from, so seeking only loses footage."""
        assert stagger_offset(clip(60.0), slot=0, sharing=1) == 0.0

    def test_cameras_are_spread_evenly_across_the_clip(self, clip) -> None:
        source = clip(61.0)  # 60 s usable once the tail margin is taken
        offsets = [stagger_offset(source, slot, sharing=3) for slot in range(3)]

        assert offsets == [0.0, 20.0, 40.0]

    def test_a_longer_clip_spreads_cameras_further(self, clip) -> None:
        """The old hardcoded twelve-second window bunched long footage into its
        first seconds no matter how much of it there was."""
        short = [stagger_offset(clip(31.0), slot, 3) for slot in range(3)]
        long = [stagger_offset(clip(301.0), slot, 3) for slot in range(3)]

        assert long[2] > short[2]
        assert long == [0.0, 100.0, 200.0]

    def test_no_offset_ever_lands_past_the_end(self, clip) -> None:
        """The failure this exists to prevent: seeking past the end of a clip
        yields no video, so the camera publishes nothing while looking healthy."""
        for duration in (2.0, 5.0, 13.2, 15.0, 60.0, 600.0):
            source = clip(duration)
            for slot in range(8):
                assert stagger_offset(source, slot, sharing=8) < duration

    def test_two_cameras_never_share_a_phase(self, clip) -> None:
        source = clip(16.0)
        offsets = [stagger_offset(source, slot, sharing=3) for slot in range(3)]

        assert len(set(offsets)) == len(offsets)


class TestWhenTheClipCannotBeMeasured:
    def test_an_unprobeable_clip_falls_back_rather_than_failing(self, clip) -> None:
        """Bunching cameras near the start is recoverable; seeking past the end
        of a clip we could not measure is not."""
        source = clip(None)
        offsets = [stagger_offset(source, slot, sharing=3) for slot in range(3)]

        assert offsets == [0.0, pytest.approx(3.333), pytest.approx(6.667)]
        assert all(offset < FALLBACK_WINDOW_S for offset in offsets)

    def test_a_clip_shorter_than_the_tail_margin_is_not_staggered(self, clip) -> None:
        assert stagger_offset(clip(0.5), slot=1, sharing=3) == 0.0


class TestAnExplicitStep:
    def test_an_explicit_step_is_honoured(self, clip) -> None:
        source = clip(61.0)
        offsets = [stagger_offset(source, slot, sharing=3, step=2.0) for slot in range(3)]

        assert offsets == [0.0, 2.0, 4.0]

    def test_an_explicit_step_is_still_clamped_to_the_clip(self, clip) -> None:
        """Honouring a step that walks off the end would be worse than ignoring
        it: no video at all, rather than a narrower spread."""
        source = clip(16.0)
        offsets = [stagger_offset(source, slot, sharing=3, step=90.0) for slot in range(3)]

        assert offsets == [0.0, 5.0, 10.0]
