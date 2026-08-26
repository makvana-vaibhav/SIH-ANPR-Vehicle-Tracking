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

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import cv2
import numpy as np

from ailab.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class CapturedFrame:
    """A frame plus when it was actually captured, for latency accounting."""

    index: int
    image: np.ndarray
    captured_at: float          # perf_counter, for measuring latency
    wall_time: datetime         # tz-aware UTC, for the event payload
    source_t_s: float           # position within the stream

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
        max_reconnects: int = 0,
    ) -> None:
        self.source = source
        self.realtime = realtime
        self.reconnect = reconnect
        self.reconnect_delay_s = reconnect_delay_s
        self.max_reconnects = max_reconnects

        self.stats = ReaderStats()
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

    def start(self) -> StreamReader:
        if not self._open():
            raise RuntimeError(f"could not open stream: {self.source}")
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
            captured = CapturedFrame(
                index=index,
                image=image,
                captured_at=time.perf_counter(),
                wall_time=datetime.now(UTC),
                source_t_s=index / self._fps,
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
            log.warning("giving up on %s after %d reconnects", self.source, self.stats.reconnects)
            return False

        self.stats.decode_failures += 1
        # A file simply ends; only a live source is worth reconnecting to.
        if not self.source.startswith(("rtsp://", "rtsps://", "http://", "https://")):
            return False

        self.stats.reconnects += 1
        log.warning(
            "stream %s dropped; reconnecting in %.1fs (attempt %d)",
            self.source, self.reconnect_delay_s, self.stats.reconnects,
        )
        if self._stop.wait(self.reconnect_delay_s):
            return False
        return self._open()

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
