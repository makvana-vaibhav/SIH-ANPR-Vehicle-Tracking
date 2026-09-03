"""Threaded frame reader with drop-to-latest semantics.

A file and a live camera differ in one way that changes the whole design: a file
waits for you, a camera does not. If inference takes 300 ms and the camera
produces a frame every 33 ms, something has to give. There are only three
options and only one of them is right for CCTV:

  block the reader     the decoder's buffer fills, then the RTSP connection
                       stalls or the server drops us. Recovery is a reconnect,
                       and we lose everything in between.
  queue everything     memory grows without bound and latency grows with it.
                       After a minute we are confidently reporting where a car
                       was a minute ago, which is worse than useless for alerts.
  drop to latest       decode continuously, keep only the newest frame, let the
                       consumer take whatever is current.

The third is correct here because for ANPR the *current* frame is worth more
than a backlog of old ones. A vehicle is in view for dozens of frames; missing
some of them costs a little consensus evidence, while falling behind costs
alert latency, which is the thing the platform actually sells.

Dropped frames are counted, not hidden. A worker silently discarding 90% of its
input while reporting healthy throughput is a lie the operator cannot see.
"""

from __future__ import annotations

import os

# Set before OpenCV opens any FFmpeg capture. The Sentinel integration guide is
# explicit that UDP "fails across NAT and most corporate firewalls" and that
# partial UDP delivery "produces corrupt frames that look like model bugs" —
# which is the worst failure mode available, because it sends you debugging the
# detector instead of the transport.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import socket
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse, urlunparse

import cv2
import numpy as np

from ailab.logging import get_logger

log = get_logger(__name__)


class StreamUnavailable(RuntimeError):
    """The source could not be opened at all.

    Distinct from a stream that opens and then fails, because for a federated
    camera it is the *expected* state whenever the far end is down. A caller
    supervising many cameras wants to log this as a fact about one camera, not
    as an exception with a traceback — thirty unreachable cameras would
    otherwise bury every real fault in the log.

    Carries `reason`, because "unreachable" and "the far end rejected our
    credentials" call for completely different responses and OpenCV reports
    both as a bare False. Telling an operator to check the network when the
    real answer is a missing password costs hours.
    """

    def __init__(self, message: str, reason: str = "unknown") -> None:
        super().__init__(message)
        self.reason = reason


def redact(url: str) -> str:
    """A stream URL safe to log.

    RTSP credentials travel in the URL (`rtsp://user:pass@host/path`), so any
    code path that logs a source — and there are several: connection, retry,
    failure, the run report — would otherwise write the password to disk and
    to whatever ships those logs onward.
    """
    parsed = urlparse(url)
    if not parsed.password:
        return url
    safe = parsed._replace(
        netloc=f"{parsed.username or ''}:***@{parsed.hostname}"
        + (f":{parsed.port}" if parsed.port else "")
    )
    return urlunparse(safe)


def diagnose(url: str, timeout: float = 6.0) -> str:
    """Ask the far end why it would not open.

    `cv2.VideoCapture.isOpened()` returns False for a refused connection, a
    DNS failure, an authentication rejection and a missing path alike. This
    reproduces the first exchange by hand to recover the distinction:

        no route / refused   the host or port is wrong, or a firewall
        unauthorized         credentials are needed or were rejected
        not found            the host is right and the camera id is not
        timeout              the far end accepted and then went quiet
    """
    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port

    if parsed.scheme in ("http", "https"):
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return "ok" if response.status == 200 else f"http {response.status}"
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return "unauthorized"
            if exc.code == 404:
                return "not found"
            return f"http {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return f"no route ({exc})"

    if parsed.scheme != "rtsp" or not host:
        return "unknown"

    port = port or 554
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            # A DESCRIBE is the first request any RTSP client makes, and it is
            # what returns 401 when the server wants credentials.
            request = (
                f"DESCRIBE {url} RTSP/1.0\r\n"
                "CSeq: 1\r\n"
                "User-Agent: sentinel-gj/diagnose\r\n"
                "Accept: application/sdp\r\n\r\n"
            )
            sock.sendall(request.encode("ascii"))
            status = sock.recv(256).decode("latin-1", "replace").split("\r\n")[0]
    except TimeoutError:
        return "timeout"
    except OSError as exc:
        return f"no route ({exc})"

    if " 401" in status or " 403" in status:
        return "unauthorized"
    if " 404" in status:
        return "not found"
    if " 200" in status:
        # It answers a DESCRIBE but the decoder still could not start: usually
        # a codec the build cannot handle, or a path publishing nothing.
        return "opened but no media"
    return status.strip() or "unknown"


@dataclass(slots=True)
class CapturedFrame:
    """A frame plus when it was actually captured, for latency accounting."""

    index: int
    image: np.ndarray
    captured_at: float          # perf_counter, for measuring latency only
    wall_time: datetime         # tz-aware UTC, for the event payload
    # Presentation timestamp, in seconds, taken from the stream itself. Every
    # motion calculation must use this and never arrival time — see the note on
    # `_read_pts`.
    source_t_s: float
    # True when the stream jumped backwards, i.e. the recording looped or the
    # camera restarted. Long-lived state must be rebuilt rather than carried
    # across the cut.
    discontinuity: bool = False

    @property
    def age_ms(self) -> float:
        return (time.perf_counter() - self.captured_at) * 1000.0


@dataclass
class ReaderStats:
    frames_decoded: int = 0
    frames_delivered: int = 0
    frames_dropped: int = 0
    reconnects: int = 0
    decode_failures: int = 0
    discontinuities: int = 0
    pts_unavailable: bool = False
    started_at: float = field(default_factory=time.perf_counter)

    @property
    def elapsed_s(self) -> float:
        return max(1e-6, time.perf_counter() - self.started_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frames_decoded": self.frames_decoded,
            "frames_delivered": self.frames_delivered,
            "frames_dropped": self.frames_dropped,
            "drop_rate": round(self.frames_dropped / max(1, self.frames_decoded), 4),
            "decode_fps": round(self.frames_decoded / self.elapsed_s, 2),
            "delivered_fps": round(self.frames_delivered / self.elapsed_s, 2),
            "reconnects": self.reconnects,
            "decode_failures": self.decode_failures,
            "discontinuities": self.discontinuities,
            "pts_unavailable": self.pts_unavailable,
            "elapsed_s": round(self.elapsed_s, 2),
        }


class StreamReader:
    """Continuously decodes a source, holding only the most recent frame.

    Works for an RTSP URL or a file. A file is read at its natural pace rather
    than as fast as possible, so that a recorded clip exercises the same
    drop-and-catch-up behaviour a live camera would produce — otherwise the
    streaming path would only ever be tested in a regime it will never meet.
    """

    def __init__(
        self,
        source: str,
        realtime: bool = True,
        reconnect: bool = True,
        reconnect_delay_s: float = 2.0,
        max_reconnect_delay_s: float = 30.0,
        max_reconnects: int = 0,
    ) -> None:
        self.source = source
        self.realtime = realtime
        self.reconnect = reconnect
        self.reconnect_delay_s = reconnect_delay_s
        self.max_reconnect_delay_s = max_reconnect_delay_s
        self.max_reconnects = max_reconnects

        self.stats = ReaderStats()
        self._pts_s = 0.0
        self._last_pts_s: float | None = None
        self._pts_offset_s = 0.0
        self._pts_available = True
        self._backoff_s = reconnect_delay_s
        self._latest: CapturedFrame | None = None
        self._lock = threading.Lock()
        self._new_frame = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._capture: cv2.VideoCapture | None = None
        self._fps = 25.0
        self._finished = False

    # ── lifecycle ──
    def _open(self) -> bool:
        capture = cv2.VideoCapture(self.source)
        if not capture.isOpened():
            capture.release()
            return False
        # A small decoder buffer keeps the driver from handing us stale frames;
        # we do our own dropping and want the freshest one available.
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        self._fps = fps if 0.0 < fps <= 240.0 else 25.0
        self._capture = capture
        return True

    def _read_pts(self, capture: cv2.VideoCapture) -> tuple[float, bool]:
        """Presentation timestamp for the frame just read, and whether it jumped.

        The Sentinel integration guide is emphatic about this and it is not a
        style preference: when a client connects the gateway "replays its
        buffered group-of-pictures so the decoder can start at a keyframe", so
        the first second or two arrives *faster than real time*. A tracker that
        timestamps by arrival computes impossible velocities immediately after
        every connection, and a Kalman filter fed those deltas diverges.

        So timing comes from the stream. Two cases still need handling:

        * **Not every source reports PTS.** Some return 0 forever. That is
          detected once and recorded, after which a nominal cadence is used and
          the run says so rather than silently inventing timestamps.
        * **The recording loops.** PTS jumps backwards at the loop point. The
          offset is carried forward so `source_t_s` stays monotonic for event
          ordering, and the jump is flagged so tracker state can be rebuilt.
        """
        raw_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
        if raw_ms is None or raw_ms != raw_ms or raw_ms <= 0.0:  # NaN-safe
            if self._pts_available:
                # Record it immediately; a stream that dies after three frames
                # still needs its timestamps marked as approximate. Only the
                # warning waits, so a normal startup does not log noise.
                self._pts_available = False
                self.stats.pts_unavailable = True
                if self.stats.frames_decoded > 5:
                    log.warning(
                        "%s reports no usable PTS; falling back to nominal cadence. "
                        "Motion-derived values from this source are approximate.",
                        redact(self.source),
                    )
            self._pts_s += 1.0 / self._fps
            return self._pts_s, False

        raw_s = raw_ms / 1000.0
        discontinuity = False
        if self._last_pts_s is not None and raw_s < self._last_pts_s - 1.0:
            # Backwards jump: the loop point, or the camera restarted. Keep the
            # emitted clock monotonic so events stay orderable, and flag it.
            self._pts_offset_s += self._last_pts_s - raw_s + (1.0 / self._fps)
            discontinuity = True
            self.stats.discontinuities += 1
            log.info(
                "scene discontinuity on %s (PTS %.2fs -> %.2fs); "
                "tracker state will be rebuilt",
                redact(self.source), self._last_pts_s, raw_s,
            )

        self._last_pts_s = raw_s
        self._pts_s = raw_s + self._pts_offset_s
        return self._pts_s, discontinuity

    def start(self) -> StreamReader:
        if not self._open():
            reason = diagnose(self.source)
            raise StreamUnavailable(
                f"{redact(self.source)} — {reason}", reason=reason
            )
        self._thread = threading.Thread(target=self._run, name="stream-reader", daemon=True)
        self._thread.start()
        return self

    def _run(self) -> None:
        index = 0
        frame_interval = 1.0 / self._fps
        next_due = time.perf_counter()

        while not self._stop.is_set():
            capture = self._capture
            if capture is None:
                break

            ok, image = capture.read()
            if not ok:
                if not self._handle_read_failure():
                    break
                continue

            self.stats.frames_decoded += 1
            pts_s, discontinuity = self._read_pts(capture)
            # A frame arrived, so the connection is healthy: reset the backoff
            # so a later drop starts from a short delay rather than inheriting
            # the previous outage's.
            self._backoff_s = self.reconnect_delay_s
            captured = CapturedFrame(
                index=index,
                image=image,
                captured_at=time.perf_counter(),
                wall_time=datetime.now(UTC),
                source_t_s=pts_s,
                discontinuity=discontinuity,
            )
            index += 1

            with self._new_frame:
                # Replacing an unconsumed frame IS the drop. Counting it here is
                # the only place the number is knowable.
                if self._latest is not None:
                    self.stats.frames_dropped += 1
                self._latest = captured
                self._new_frame.notify()

            if self.realtime:
                # Pace a file at its natural rate so it exercises the same
                # drop-and-catch-up path a camera would.
                next_due += frame_interval
                sleep_for = next_due - time.perf_counter()
                if sleep_for > 0:
                    self._stop.wait(sleep_for)
                else:
                    # We are behind; do not try to catch up by racing ahead.
                    next_due = time.perf_counter()

        with self._new_frame:
            self._finished = True
            self._new_frame.notify_all()

    def _handle_read_failure(self) -> bool:
        """A read failure is end-of-file, or a camera that dropped out."""
        if self._capture is not None:
            self._capture.release()
            self._capture = None

        if not self.reconnect:
            return False
        if self.max_reconnects and self.stats.reconnects >= self.max_reconnects:
            log.warning(
                "giving up on %s after %d reconnects",
                redact(self.source), self.stats.reconnects,
            )
            return False

        self.stats.decode_failures += 1
        # A file simply ends; only a live source is worth reconnecting to.
        if not self.source.startswith(("rtsp://", "rtsps://", "http://", "https://")):
            return False

        self.stats.reconnects += 1
        delay = self._backoff_s
        log.warning(
            "stream %s dropped; reconnecting in %.1fs (attempt %d)",
            redact(self.source), delay, self.stats.reconnects,
        )
        # Exponential backoff, capped. Feeds are supervised and restart; a tight
        # reconnect loop against a recovering gateway is useless and rude, and
        # the guide asks for ~2s rising to ~30s.
        self._backoff_s = min(self.max_reconnect_delay_s, self._backoff_s * 2.0)

        if self._stop.wait(delay):
            return False
        if not self._open():
            return True   # stay in the loop and back off again

        # A reconnect replays a buffered GOP, so the frames that follow are not
        # continuous with the ones before it.
        self._last_pts_s = None
        return True

    # ── consumption ──
    def read(self, timeout: float = 1.0) -> CapturedFrame | None:
        """Take the most recent frame, or None when the stream has ended."""
        with self._new_frame:
            if self._latest is None and not self._finished:
                self._new_frame.wait(timeout)
            frame = self._latest
            self._latest = None
        if frame is not None:
            self.stats.frames_delivered += 1
        return frame

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._finished and self._latest is None

    @property
    def fps(self) -> float:
        return self._fps

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        if self._capture is not None:
            self._capture.release()
            self._capture = None

    def __enter__(self) -> StreamReader:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
