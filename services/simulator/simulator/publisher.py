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
import functools
import os
import shutil
import subprocess
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
    source: Path | None  # None → generated test pattern
    label: str = ""
    fps: int = 15
    width: int = 1280
    height: int = 720
    #: Seconds to seek into the clip before streaming.
    #:
    #: Cameras that share a clip would otherwise show the *same* vehicle at the
    #: *same* wall-clock moment, and the correlator would rightly read that as
    #: one plate at two places at once — `impossible_simultaneous`. Offsetting
    #: each camera into a different part of the clip removes that artefact.
    #:
    #: It does NOT manufacture a plausible journey. The only clip with legible
    #: plates is 15 s long, and the fleet's median camera spacing is 1.7 km, so
    #: any offset that fits inside the clip implies ~415 km/h. Plausible
    #: multi-camera journeys need longer footage or a scheduled replay; see
    #: docs/ROADMAP.md.
    start_offset_s: float = 0.0


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


@functools.lru_cache(maxsize=64)
def is_playable(source: Path) -> bool:
    """True when ffprobe can read a positive duration from `source`.

    `data/videos/` is populated by a fetch script over the network, so a
    truncated download lands there looking like a normal clip. One did:
    `test2.mp4` is exactly 8 MiB with an unreadable header ("contradictionary
    STSC and STCO"), and because clips are handed out round-robin it silently
    killed the two cameras it was assigned to — they restarted five times and
    were abandoned.

    Screening the clip once here means a corrupt file costs nothing instead of
    costing cameras.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    ok = False
    if result.returncode == 0:
        try:
            ok = float(result.stdout.strip()) > 0.0
        except ValueError:
            ok = False
    if not ok:
        # Inside the cached call, so this is logged once per file rather than
        # on every health check that enumerates the directory.
        log.warning(
            "simulator.clip_unplayable",
            source=str(source),
            detail="ffprobe could not read a duration; excluded from replay",
        )
    return ok


def find_videos(directory: Path) -> list[Path]:
    """Every *playable* clip in a directory, sorted for determinism."""
    if not directory.is_dir():
        return []
    candidates = sorted(
        p
        for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES
    )
    return [p for p in candidates if is_playable(p)]


@functools.lru_cache(maxsize=64)
def is_h264(source: Path) -> bool:
    """True when `source` already carries an H.264 video stream.

    Cached because the supervisor restarts publishers, and re-probing the same
    file on every restart is pure waste. A probe failure returns False, so an
    unreadable or exotic file falls back to re-encoding rather than producing a
    stream MediaMTX cannot accept.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=nw=1:nk=1",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "h264"


#: Where keyframe-dense copies of the seed clips are cached. Mounted writable
#: because data/videos is read-only — the source clips must not be touched.
PREPARED_DIR = Path(os.environ.get("SIM_PREPARED_DIR", "/data/prepared"))

#: Target keyframe interval, in seconds, for a prepared clip.
KEYFRAME_INTERVAL_S = 1.0

#: Ceiling on a published stream's height and frame rate.
#:
#: Two of the seed clips are **3840x2160 at 24 Mbps** — `anpr_sample.mp4` at
#: 60 fps and `test1.mp4` at 30 — and roughly a quarter of the fleet is
#: assigned one of them. Publishing that verbatim costs on both sides of the
#: wire: a browser tile has to decode 4K60, and the AI worker decodes every 4K
#: frame only to letterbox it to the detector's fixed 640px input. Neither
#: gains anything from the extra pixels at that stage.
#:
#: 1080p15 is also simply more faithful to the problem. City ANPR cameras are
#: 1080p and run at 12-15 fps; a 4K60 feed is not what a municipal deployment
#: looks like, and `scripts/capacity_model.py` sizes against the realistic
#: figure. Capping here makes the demo cheaper *and* more representative.
#:
#: Applied only in the prepare pass, which already re-encodes once to disk, so
#: it adds no per-stream cost. Clips at or below the cap are untouched —
#: scaling up would invent detail and cost plate legibility for nothing.
MAX_HEIGHT = int(os.environ.get("SIM_MAX_HEIGHT", "1080"))
MAX_FPS = float(os.environ.get("SIM_MAX_FPS", "15"))


@dataclass(frozen=True)
class EncodePlan:
    """What the prepare pass should do to one clip."""

    #: Frame rate to publish at — never above the source's own.
    fps: float
    #: The source's rate, so the caller can tell "capped" from "unchanged".
    source_fps: float
    #: ffmpeg `-vf` filters, empty when the clip is already within the cap.
    filters: tuple[str, ...]

    @property
    def resampled(self) -> bool:
        return self.fps < self.source_fps


def encode_plan(
    source_height: int | None,
    source_fps: float | None,
    max_height: int | None = None,
    max_fps: float | None = None,
) -> EncodePlan:
    """Decide how to bring one clip within the publishing cap.

    Pure, so the policy can be tested without ffmpeg — which the shared test
    image does not carry.

    The rule that matters is **never up**. A 720p15 clip is left exactly as it
    is: upscaling invents detail the sensor never captured, and resampling
    15 fps to 15 fps would re-encode for nothing. An unreadable probe is
    likewise treated as "leave it alone", because rescaling on a guess costs
    plate legibility and a clip that cannot be probed may be perfectly fine.
    """
    ceiling_h = MAX_HEIGHT if max_height is None else max_height
    ceiling_fps = MAX_FPS if max_fps is None else max_fps

    known_fps = source_fps or 15.0
    fps = min(known_fps, ceiling_fps)

    # `-2` keeps the width even — H.264 requires it — while preserving aspect.
    downscale = source_height is not None and source_height > ceiling_h
    filters = (f"scale=-2:{ceiling_h}",) if downscale else ()

    return EncodePlan(fps=fps, source_fps=known_fps, filters=filters)


@functools.lru_cache(maxsize=64)
def prepare_clip(source: Path) -> Path:
    """Return a keyframe-dense copy of `source`, building it once if needed.

    ## Why this exists

    Streaming with `-c:v copy` is what makes a 50-camera fleet affordable on one
    laptop: remuxing costs almost nothing, where re-encoding costs roughly a
    tenth of a core per stream. But copy-mode inherits the source's keyframe
    layout, and the seed clips are *pathologically* sparse — `anpr_demo.mp4` has
    **exactly one keyframe in 15 seconds**.

    That broke two things at once. Seeking (`-ss`) had nothing to land on, so
    stream offsets either did nothing or started mid-GOP with no reference
    frame. And every consumer that joins mid-GOP — which is every AI worker,
    since it connects at an arbitrary moment — decoded garbage until the next
    keyframe, up to 15 s later. Measured: 501 decoder errors in 200 s, and
    plate yield fell from 43% of detections to 17%.

    Re-encoding at stream time fixes the decoding and loses the CPU win.
    Re-encoding *once, to disk* fixes the decoding and keeps it: the expensive
    pass happens a single time per clip and is cached across restarts, and every
    stream afterwards is a cheap remux of a clip that has a keyframe every
    second.

    A failure here is not fatal — the original clip is returned and
    `build_command` will re-encode it live instead.
    """
    try:
        PREPARED_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("simulator.prepare_dir_failed", error=str(exc))
        return source

    plan = encode_plan(probe_height(source), probe_fps(source))
    fps, source_fps, filters = plan.fps, plan.source_fps, plan.filters

    # The cap is part of the cache identity, so changing MAX_HEIGHT or MAX_FPS
    # invalidates prepared clips instead of silently serving ones built to the
    # old ceiling.
    target = PREPARED_DIR / f"{source.stem}_{MAX_HEIGHT}p{fps:g}_bl.mp4"
    # Rebuild when missing or older than the source, so replacing a clip in
    # data/videos does not leave a stale prepared copy behind.
    try:
        if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
            return target
    except OSError:
        pass

    gop = max(1, round(fps * KEYFRAME_INTERVAL_S))
    log.info(
        "simulator.preparing_clip",
        source=str(source),
        gop=gop,
        fps=fps,
        source_fps=source_fps,
        filters=filters or None,
    )
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-an",
                *(["-vf", ",".join(filters)] if filters else []),
                # Capped, never raised — `fps` is min(source, MAX_FPS), so a
                # 15 fps clip is not resampled to 15 fps for no reason.
                *(["-r", f"{fps:g}"] if fps < source_fps else []),
                "-c:v",
                "libx264",
                # Constrained Baseline, not libx264's default High profile.
                #
                # This is what makes WebRTC work. Chrome negotiates H.264 for
                # WHEP but its decoder only reliably handles Constrained
                # Baseline; offered a High-profile stream it completes the
                # handshake and then renders a **black frame** — the overlay
                # draws boxes over nothing, and the player falls back to HLS,
                # which buffers several seconds. That fallback was the video
                # delay, and it looked like a pipeline problem rather than a
                # codec one.
                #
                # Baseline forbids B-frames and CABAC, so the file is somewhat
                # larger for the same quality. That is a trade worth making:
                # sub-second WebRTC is the difference between a live camera and
                # a recording, and this is replayed demo footage either way.
                "-profile:v",
                "baseline",
                "-level",
                "3.1",
                "-preset",
                "veryfast",
                "-g",
                str(gop),
                # Forbid extra keyframes on scene change so the interval is
                # actually uniform, which is what seeking relies on.
                "-sc_threshold",
                "0",
                "-pix_fmt",
                "yuv420p",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("simulator.prepare_failed", source=str(source), error=str(exc))
        return source

    if result.returncode != 0 or not target.exists():
        log.warning(
            "simulator.prepare_failed",
            source=str(source),
            detail=result.stderr[-300:] if result.stderr else "",
        )
        return source

    log.info("simulator.clip_prepared", source=str(source), prepared=str(target))
    return target


@functools.lru_cache(maxsize=64)
def probe_fps(source: Path) -> float | None:
    """Source frame rate, or None when it cannot be read."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=nw=1:nk=1",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = result.stdout.strip()
    if result.returncode != 0 or "/" not in raw:
        return None
    try:
        num, den = raw.split("/", 1)
        return float(num) / float(den) if float(den) else None
    except (ValueError, ZeroDivisionError):
        return None


@functools.lru_cache(maxsize=64)
def probe_duration(source: Path) -> float | None:
    """Clip length in seconds, or None when it cannot be read.

    The simulator staggers cameras that share a clip by seeking to different
    points in it, and how far apart it can space them is bounded by how long the
    clip actually is. That used to be a hardcoded constant, which silently
    became wrong the moment anyone replaced the footage: offsets past the end
    yield no video at all, and offsets bunched into the first few seconds put
    two cameras at the same phase, which reads downstream as one vehicle in two
    places at once.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return seconds if seconds > 0 else None


#: Never seek this close to the end of a clip. Landing in its last moment gives
#: a publisher a fraction of a second of video before it loops, which reads
#: downstream as a camera that keeps restarting.
OFFSET_TAIL_S = 1.0

#: Used only when a clip's duration cannot be probed. Deliberately short: it is
#: better to bunch cameras near the start of a clip than to seek past its end,
#: because seeking past the end yields no video at all.
FALLBACK_WINDOW_S = 10.0


def stagger_offset(source: Path, slot: int, sharing: int, step: float = 0.0) -> float:
    """Where in `source` the `slot`-th of `sharing` cameras should start.

    Two cameras showing the same instant of the same clip publish the same
    vehicle at the same moment, which downstream is indistinguishable from one
    plate genuinely appearing in two places at once — and is correctly flagged
    implausible. Spreading them across the clip is what prevents that.

    How far they *can* be spread is a property of the footage, so it is measured
    rather than assumed. This used to be a hardcoded twelve-second window with a
    1.8-second step, which was only ever right for the clips in the repository
    at the time: longer footage was needlessly bunched into its first seconds,
    and anything shorter than twelve seconds got offsets past its end, which
    yields no video at all.

    `step` of 0 spreads the group evenly across the clip, which is the widest
    spacing the footage allows. An explicit step is honoured but still clamped
    to that, because a step that walks off the end is worse than no stagger.
    """
    if sharing <= 1 or slot <= 0:
        return 0.0

    duration = probe_duration(source)
    window = (duration - OFFSET_TAIL_S) if duration else FALLBACK_WINDOW_S
    if window <= 0.0:
        return 0.0

    even = window / sharing
    chosen = min(step, even) if step > 0.0 else even
    return round((slot * chosen) % window, 3)


@functools.lru_cache(maxsize=64)
def probe_height(source: Path) -> int | None:
    """Source frame height, or None when it cannot be read.

    None means "leave it alone": an unreadable probe must not be taken as a
    reason to re-encode, since the clip may be fine and rescaling on a guess
    would cost plate legibility.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=height",
                "-of",
                "default=nw=1:nk=1",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = result.stdout.strip()
    if result.returncode != 0 or not raw.isdigit():
        return None
    return int(raw)


def build_command(spec: StreamSpec, rtsp_base: str) -> list[str]:
    """ffmpeg arguments for one stream.

    Two shapes: replay a file on an endless loop, or synthesise a test pattern.
    Both publish H.264 over RTSP/TCP.
    """
    target = f"{rtsp_base}/{spec.camera_code.lower()}"

    if spec.source is not None:
        # Remux when the clip is already H.264, which every seed clip is.
        #
        # This matters for the one-laptop constraint rather than for tidiness.
        # Re-encoding costs roughly a tenth of a core per 1080p stream, which
        # caps the live fleet at a handful before ffmpeg starts competing with
        # ONNX inference for the same cores. Copying the existing bitstream
        # costs almost nothing, so the whole city fleet can be live at once and
        # the CPU stays available to the AI pipeline — which is the part that
        # actually has to keep up.
        #
        # The trade is keyframe interval: with `copy` we inherit the source's,
        # so a WebRTC viewer may wait longer for the first frame. MediaMTX
        # serves from its own buffer, so in practice this is not visible.
        codec_args = (
            ["-c:v", "copy"]
            if is_h264(spec.source)
            else [
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",  # CPU-only host; latency over quality
                "-tune",
                "zerolatency",
                "-g",
                str(spec.fps * 2),  # keyframe every 2s, for fast WebRTC join
                "-pix_fmt",
                "yuv420p",
            ]
        )
        return [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            # -re paces output at the source frame rate. Without it ffmpeg
            # pushes the whole file as fast as it can and the "live" stream is
            # over in seconds.
            "-re",
            "-stream_loop",
            "-1",
            # Before -i, so the seek is applied to the input and costs nothing.
            *(["-ss", f"{spec.start_offset_s:.2f}"] if spec.start_offset_s > 0 else []),
            "-i",
            str(spec.source),
            "-an",  # audio is irrelevant to ANPR
            *codec_args,
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            target,
        ]

    # No source file: synthesise a moving test pattern with a timestamp, so the
    # stream is visibly live rather than a frozen frame.
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-re",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=size={spec.width}x{spec.height}:rate={spec.fps}",
        "-vf",
        (
            f"drawtext=text='{spec.label or spec.camera_code}':"
            "fontcolor=white:fontsize=28:x=20:y=20:box=1:boxcolor=black@0.5"
        ),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-g",
        str(spec.fps * 2),
        "-pix_fmt",
        "yuv420p",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
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
                camera_code=spec.camera_code,
                error=str(exc),
                exc_info=True,
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
