"""Synthetic camera fleet: replays video files into MediaMTX as RTSP.

This is how 250 "cameras" exist on a laptop with no hardware. It is also
exactly what the challenge organisers do — their FAQ (Q39-40) describes
converting ~12 hours of real footage into simulated live streams via Python
middleware, precisely so teams can integrate without touching production
infrastructure.

Each stream is a separate ffmpeg process publishing to
``rtsp://mediamtx:8554/<camera_code>``, looping its source clip. That makes
them real RTSP endpoints: the health monitor probes them the same way it would
probe a camera in Rajkot, and the AI pipeline reads them with the same code
that would read a real feed.

When no video files are present, a synthetic test pattern is generated with
ffmpeg's ``testsrc`` so the platform still has live streams to work with — the
demo degrades rather than dying.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger

log = get_logger("simulator.publisher")

#: Video extensions we will replay.
VIDEO_SUFFIXES = (".mp4", ".mkv", ".mov", ".avi", ".ts")


@dataclass
class StreamSpec:
    """One simulated camera stream."""

    camera_code: str
    source: Path | None          # None → generated test pattern
    label: str = ""
    fps: int = 15
    width: int = 1280
    height: int = 720


@dataclass
class StreamProcess:
    """A running ffmpeg publisher."""

    spec: StreamSpec
    process: asyncio.subprocess.Process
    restarts: int = 0
    stderr_tail: list[str] = field(default_factory=list)

    @property
    def is_running(self) -> bool:
        return self.process.returncode is None


def find_videos(directory: Path) -> list[Path]:
    """Every replayable clip in a directory, sorted for determinism."""
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    )


def build_command(spec: StreamSpec, rtsp_base: str) -> list[str]:
    """ffmpeg arguments for one stream.

    Two shapes: replay a file on an endless loop, or synthesise a test pattern.
    Both publish H.264 over RTSP/TCP.
    """
    target = f"{rtsp_base}/{spec.camera_code.lower()}"

    if spec.source is not None:
        return [
            "ffmpeg",
            "-hide_banner", "-loglevel", "error",
            # -re paces output at the source frame rate. Without it ffmpeg
            # pushes the whole file as fast as it can and the "live" stream is
            # over in seconds.
            "-re",
            "-stream_loop", "-1",
            "-i", str(spec.source),
            "-an",                       # audio is irrelevant to ANPR
            "-c:v", "libx264",
            "-preset", "ultrafast",      # CPU-only host; latency over quality
            "-tune", "zerolatency",
            "-g", str(spec.fps * 2),     # keyframe every 2s, for fast WebRTC join
            "-pix_fmt", "yuv420p",
            "-f", "rtsp",
            "-rtsp_transport", "tcp",
            target,
        ]

    # No source file: synthesise a moving test pattern with a timestamp, so the
    # stream is visibly live rather than a frozen frame.
    return [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-re",
        "-f", "lavfi",
        "-i", f"testsrc=size={spec.width}x{spec.height}:rate={spec.fps}",
        "-vf", (
            f"drawtext=text='{spec.label or spec.camera_code}':"
            "fontcolor=white:fontsize=28:x=20:y=20:box=1:boxcolor=black@0.5"
        ),
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-g", str(spec.fps * 2),
        "-pix_fmt", "yuv420p",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        target,
    ]


class StreamPublisher:
    """Supervises a pool of ffmpeg publishers.

    Restarts a stream that dies, with a bounded restart count so a permanently
    broken source does not spin forever burning CPU during a demo.
    """

    def __init__(self, rtsp_base: str, *, max_restarts: int = 5) -> None:
        self.rtsp_base = rtsp_base.rstrip("/")
        self.max_restarts = max_restarts
        self._streams: dict[str, StreamProcess] = {}
        self._stopping = False

    @staticmethod
    def ffmpeg_available() -> bool:
        return shutil.which("ffmpeg") is not None

    async def start(self, spec: StreamSpec) -> bool:
        """Launch one publisher."""
        command = build_command(spec, self.rtsp_base)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            log.error(
                "simulator.spawn_failed",
                camera_code=spec.camera_code, error=str(exc), exc_info=True,
            )
            return False

        self._streams[spec.camera_code] = StreamProcess(spec=spec, process=process)
        log.info(
            "simulator.stream_started",
            camera_code=spec.camera_code,
            source=str(spec.source) if spec.source else "testsrc",
        )
        return True

    async def start_all(self, specs: list[StreamSpec], *, stagger_ms: int = 120) -> int:
        """Launch every stream, staggered.

        Starting dozens of ffmpeg processes simultaneously spikes CPU hard
        enough to make the first seconds of every stream stutter — which is
        exactly when a judge is looking at them.
        """
        started = 0
        for spec in specs:
            if await self.start(spec):
                started += 1
            await asyncio.sleep(stagger_ms / 1000)
        log.info("simulator.fleet_started", streams=started, requested=len(specs))
        return started

    async def stop(self, camera_code: str) -> bool:
        """Stop one stream.

        This is what the Phase 3 verification uses to take a camera down on
        purpose and confirm the health monitor notices.
        """
        stream = self._streams.pop(camera_code, None)
        if stream is None:
            return False

        if stream.is_running:
            stream.process.terminate()
            try:
                await asyncio.wait_for(stream.process.wait(), timeout=5)
            except TimeoutError:
                stream.process.kill()
                await stream.process.wait()

        log.info("simulator.stream_stopped", camera_code=camera_code)
        return True

    async def stop_all(self) -> None:
        """Stop every stream."""
        self._stopping = True
        await asyncio.gather(
            *(self.stop(code) for code in list(self._streams)), return_exceptions=True
        )
        log.info("simulator.fleet_stopped")

    def running(self) -> list[str]:
        """Camera codes currently publishing."""
        return [code for code, s in self._streams.items() if s.is_running]

    async def supervise(self, poll_seconds: float = 5.0) -> None:
        """Restart streams that die, until stopped."""
        while not self._stopping:
            await asyncio.sleep(poll_seconds)
            for code, stream in list(self._streams.items()):
                if stream.is_running:
                    continue

                stderr = b""
                if stream.process.stderr is not None:
                    try:
                        stderr = await asyncio.wait_for(
                            stream.process.stderr.read(2000), timeout=1
                        )
                    except (TimeoutError, ValueError):
                        stderr = b""

                if stream.restarts >= self.max_restarts:
                    log.error(
                        "simulator.stream_abandoned",
                        camera_code=code,
                        restarts=stream.restarts,
                        detail=stderr.decode(errors="replace")[-300:],
                    )
                    self._streams.pop(code, None)
                    continue

                log.warning(
                    "simulator.stream_restarting",
                    camera_code=code,
                    exit_code=stream.process.returncode,
                    restarts=stream.restarts + 1,
                    detail=stderr.decode(errors="replace")[-300:],
                )
                restarts = stream.restarts + 1
                await self.start(stream.spec)
                if code in self._streams:
                    self._streams[code].restarts = restarts
