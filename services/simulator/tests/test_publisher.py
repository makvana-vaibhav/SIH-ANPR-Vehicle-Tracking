"""The simulator's ffmpeg command construction and camera offsets.

These are the pieces P1 added to make a city-sized fleet publishable on one
laptop, and none of the simulator had tests before. They are pure functions, so
they need no ffmpeg, no MediaMTX and no database — which is the point: the
things most likely to be got wrong here are argument *order* and argument
*placement*, and both are checkable without running anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from simulator.publisher import (
    STREAM_OFFSET_WRAP_S,
    StreamSpec,
    build_command,
    corridor_offset,
)

RTSP = "rtsp://mediamtx:8554"
CLIP = Path("/data/videos/anpr_demo.mp4")


def spec(**overrides) -> StreamSpec:
    base = {"camera_code": "CAM-SGH-03", "source": CLIP, "fps": 15}
    return StreamSpec(**{**base, "label": "", **overrides})


class TestCopyMode:
    """Remuxing instead of re-encoding is what makes 69 live cameras fit."""

    def test_copy_mode_does_not_invoke_an_encoder(self) -> None:
        command = build_command(spec(), RTSP, copy_video=True)

        assert "-c:v" in command
        assert command[command.index("-c:v") + 1] == "copy"
        assert "libx264" not in command

    def test_encode_mode_is_still_available(self) -> None:
        """A clip that is not already H.264 must be re-encoded, not remuxed."""
        command = build_command(spec(), RTSP, copy_video=False)

        assert command[command.index("-c:v") + 1] == "libx264"
        assert "copy" not in command

    def test_encoding_is_the_default(self) -> None:
        """Copy-mode is opt-in. Remuxing a non-H.264 clip yields a stream
        MediaMTX cannot serve, and the failure is silent until a judge clicks
        the camera, so the safe behaviour is the default one."""
        assert build_command(spec(), RTSP) == build_command(spec(), RTSP, copy_video=False)

    def test_genpts_is_an_input_flag(self) -> None:
        """Placement, not presence, is what matters here.

        Looping with `-c:v copy` replays the source timestamps from zero on
        every lap, so DTS goes backwards at the loop point and the RTSP muxer
        drops the connection. `-fflags +genpts` fixes that only as an *input*
        option; after `-i` it applies to the output and does nothing.
        """
        command = build_command(spec(), RTSP, copy_video=True)

        assert "+genpts" in command
        assert command.index("-fflags") < command.index("-i")

    def test_no_genpts_when_encoding(self) -> None:
        """The encoder regenerates timestamps anyway."""
        assert "+genpts" not in build_command(spec(), RTSP, copy_video=False)


class TestStartOffset:
    """Offsets are what turn one looping clip into a journey across a fleet."""

    def test_seek_precedes_the_input(self) -> None:
        """`-ss` before `-i` seeks by index; after `-i` it decodes and discards
        every frame up to the offset, which for 69 streams is the whole saving
        given away."""
        command = build_command(spec(start_offset_s=12.5), RTSP, copy_video=True)

        assert command.index("-ss") < command.index("-i")
        assert command[command.index("-ss") + 1] == "12.50"

    def test_zero_offset_is_omitted_entirely(self) -> None:
        assert "-ss" not in build_command(spec(start_offset_s=0.0), RTSP)

    def test_test_pattern_ignores_offsets(self) -> None:
        """There is nothing to seek in a generated pattern."""
        command = build_command(spec(source=None, start_offset_s=9.0), RTSP)

        assert "-ss" not in command
        assert "lavfi" in command


class TestCommandShape:
    """Properties every stream must have, whichever mode it runs in."""

    @pytest.mark.parametrize("copy_video", [True, False])
    def test_publishes_over_rtsp_tcp_to_the_lowercased_code(self, copy_video: bool) -> None:
        """MediaMTX lowercases path names; the registry stores codes uppercase."""
        command = build_command(spec(), RTSP, copy_video=copy_video)

        assert command[-1] == f"{RTSP}/cam-sgh-03"
        assert command[command.index("-rtsp_transport") + 1] == "tcp"

    @pytest.mark.parametrize("copy_video", [True, False])
    def test_paces_the_stream_and_loops_forever(self, copy_video: bool) -> None:
        """Without -re ffmpeg pushes the file as fast as it can and the "live"
        stream is over in seconds."""
        command = build_command(spec(), RTSP, copy_video=copy_video)

        assert "-re" in command
        assert command[command.index("-stream_loop") + 1] == "-1"

    @pytest.mark.parametrize("copy_video", [True, False])
    def test_audio_is_dropped(self, copy_video: bool) -> None:
        assert "-an" in build_command(spec(), RTSP, copy_video=copy_video)


class TestCorridorOffset:
    """The offset comes from the camera's place in its corridor."""

    def test_offsets_advance_along_a_corridor(self) -> None:
        """A vehicle should reach 01, then 02, then 03 — a journey down the
        road, not a random scatter the correlator would reject as impossible."""
        offsets = [corridor_offset(f"CAM-SGH-{n:02d}") for n in (1, 2, 3, 4)]

        assert offsets == sorted(offsets)
        assert offsets[0] == 0.0
        assert len(set(offsets)) == len(offsets)

    def test_is_stable_across_restarts(self) -> None:
        """`/streams/{code}/start` rebuilds one spec from the code alone, so a
        restarted camera must resume the offset it had."""
        assert corridor_offset("CAM-SPR-07") == corridor_offset("CAM-SPR-07")

    def test_wraps_so_offsets_stay_inside_a_short_clip(self) -> None:
        assert all(
            0.0 <= corridor_offset(f"CAM-SPR-{n:02d}") < STREAM_OFFSET_WRAP_S
            for n in range(1, 40)
        )

    def test_a_code_with_no_sequence_gets_no_offset(self) -> None:
        """CAM-DEMO is pinned footage; shifting it would change what a scripted
        demo sees on that camera."""
        assert corridor_offset("CAM-DEMO") == 0.0
