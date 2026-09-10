"""What the simulator does to a clip before publishing it.

Two of the seed clips are 3840x2160 at 24 Mbps — `anpr_sample.mp4` at 60 fps
and `test1.mp4` at 30 — and roughly a quarter of the fleet is assigned one.
Published verbatim, a browser tile has to decode 4K60 while the AI worker
decodes every 4K frame only to letterbox it to the detector's fixed 640px
input. Neither gains anything from the extra pixels, and on a laptop already
sharing 10 cores it is enough to make a camera tile hang.

The cap is applied in the prepare pass, which already re-encodes once to disk,
so it costs nothing per stream.

The rule these tests hold is **never up**: a clip already within the cap must
come out untouched, because upscaling invents detail the sensor never captured
and re-encoding 15 fps to 15 fps is pure loss. Getting that backwards would
quietly degrade every plate in the fleet.
"""

from __future__ import annotations

from simulator.publisher import encode_plan


class TestDownscaling:
    def test_4k_is_brought_down_to_the_cap(self) -> None:
        """`anpr_sample.mp4`: 2160p60."""
        plan = encode_plan(2160, 60.0, max_height=1080, max_fps=15.0)

        assert plan.filters == ("scale=-2:1080",)
        assert plan.fps == 15.0
        assert plan.resampled

    def test_the_width_is_forced_even(self) -> None:
        """H.264 cannot encode an odd width, and `-2` is how ffmpeg is told to
        pick one that preserves aspect. `-1` would be allowed to be odd and
        the encode would fail on some sources."""
        plan = encode_plan(2160, 30.0, max_height=1080, max_fps=15.0)

        assert plan.filters == ("scale=-2:1080",)

    def test_a_clip_at_the_cap_is_left_alone(self) -> None:
        plan = encode_plan(1080, 15.0, max_height=1080, max_fps=15.0)

        assert plan.filters == ()
        assert not plan.resampled

    def test_a_smaller_clip_is_never_upscaled(self) -> None:
        """The rule this file exists for.

        Upscaling 720p to 1080p invents detail, costs bitrate, and gives the
        plate recogniser nothing it did not already have.
        """
        plan = encode_plan(720, 15.0, max_height=1080, max_fps=15.0)

        assert plan.filters == ()
        assert plan.fps == 15.0


class TestFrameRate:
    def test_a_fast_source_is_capped(self) -> None:
        assert encode_plan(720, 60.0, max_height=1080, max_fps=15.0).fps == 15.0

    def test_a_slow_source_is_never_sped_up(self) -> None:
        """A 12 fps camera stays 12 fps.

        Resampling up would duplicate frames, which costs bandwidth and gives
        the tracker repeated observations that look like a stationary vehicle.
        """
        plan = encode_plan(720, 12.0, max_height=1080, max_fps=15.0)

        assert plan.fps == 12.0
        assert not plan.resampled

    def test_an_exactly_matching_rate_is_not_resampled(self) -> None:
        """`resampled` drives whether `-r` is passed at all; re-encoding
        15 fps to 15 fps is work for no change."""
        assert not encode_plan(720, 15.0, max_height=1080, max_fps=15.0).resampled


class TestUnreadableProbes:
    def test_an_unknown_height_is_left_alone(self) -> None:
        """None means the probe failed, not that the clip is huge.

        Rescaling on a guess would cost plate legibility on a clip that may be
        perfectly fine.
        """
        plan = encode_plan(None, 30.0, max_height=1080, max_fps=15.0)

        assert plan.filters == ()

    def test_an_unknown_frame_rate_falls_back_without_crashing(self) -> None:
        plan = encode_plan(720, None, max_height=1080, max_fps=15.0)

        assert plan.fps == 15.0
        assert plan.source_fps == 15.0

    def test_both_unknown_is_still_a_usable_plan(self) -> None:
        plan = encode_plan(None, None, max_height=1080, max_fps=15.0)

        assert plan.filters == ()
        assert plan.fps > 0


class TestRealSeedClips:
    """The actual fleet, so a cap change shows its consequences here."""

    def test_every_seed_clip_lands_within_the_cap(self) -> None:
        clips = {
            "anpr_demo.mp4": (1080, 15.0),
            "anpr_sample.mp4": (2160, 60.0),
            "highway_congestion.mp4": (720, 15.0),
            "junction_dashcam.mp4": (720, 15.0),
            "rajkot-bus-stand.mp4": (1080, 25.0),
            "road_cctv.mp4": (720, 15.0),
            "street_crossing.mp4": (720, 15.0),
            "test1.mp4": (2160, 30.0),
        }
        for name, (height, fps) in clips.items():
            plan = encode_plan(height, fps, max_height=1080, max_fps=15.0)
            assert plan.fps <= 15.0, name
            if height > 1080:
                assert plan.filters, f"{name} is {height}p and was not scaled"
            else:
                assert not plan.filters, f"{name} is {height}p and should be untouched"

    def test_only_the_two_4k_clips_are_rescaled(self) -> None:
        """Re-encoding is not free even once, so it must apply to exactly the
        clips that need it."""
        rescaled = [
            name
            for name, height in (
                ("anpr_demo", 1080),
                ("anpr_sample", 2160),
                ("highway_congestion", 720),
                ("junction_dashcam", 720),
                ("rajkot-bus-stand", 1080),
                ("road_cctv", 720),
                ("street_crossing", 720),
                ("test1", 2160),
            )
            if encode_plan(height, 15.0, max_height=1080, max_fps=15.0).filters
        ]

        assert rescaled == ["anpr_sample", "test1"]
