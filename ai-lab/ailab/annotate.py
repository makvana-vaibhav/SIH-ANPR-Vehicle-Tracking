"""Annotated video rendering.

The single most useful debugging output the lab produces. Numbers in a CSV tell
you the pipeline read 41 plates; watching the video tells you that 12 of them
came from the same lorry because the tracker split it into four identities, and
that is a different bug with a different fix.

Design rules, learned by making the opposite mistake first:
* Colour is bound to track ID, so following one vehicle across the clip is
  effortless and an ID switch is visible as a colour change.
* The plate label sits on the plate, the vehicle label on the vehicle. Stacking
  everything in one corner makes a busy junction unreadable.
* Text is always drawn on a filled background. White-on-white plates are the
  single most common CCTV scene and unbacked text vanishes into them.
* The running consensus is shown, not the current frame's read, so you can watch
  a plate converge as evidence accumulates.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ailab.logging import get_logger
from ailab.types import BBox, Detection, PlateDetection

log = get_logger(__name__)

FONT = cv2.FONT_HERSHEY_SIMPLEX

# Distinguishable at a glance even when two vehicles overlap. BGR.
PALETTE: tuple[tuple[int, int, int], ...] = (
    (66, 135, 245), (46, 204, 113), (241, 196, 15), (231, 76, 60),
    (155, 89, 182), (26, 188, 156), (243, 156, 18), (52, 152, 219),
    (211, 84, 0), (39, 174, 96), (192, 57, 43), (142, 68, 173),
)
PLATE_COLOUR = (0, 255, 255)        # yellow — never used for a vehicle
UNCERTAIN_COLOUR = (0, 165, 255)    # orange — ambiguous or grammar-invalid
HUD_BG = (24, 24, 24)


def colour_for(track_id: int) -> tuple[int, int, int]:
    return PALETTE[track_id % len(PALETTE)]


@dataclass(slots=True)
class TrackLabel:
    """What the overlay should currently say about one vehicle."""

    text: str = ""
    confidence: float = 0.0
    reads: int = 0
    uncertain: bool = False


@dataclass(slots=True)
class Annotator:
    """Draws one frame's worth of pipeline state."""

    draw_trails: bool = True
    trail_length: int = 40
    _trails: dict[int, list[tuple[int, int]]] = field(default_factory=dict)

    def reset(self) -> None:
        self._trails.clear()

    # ── primitives ──
    @staticmethod
    def _label(
        image: np.ndarray,
        text: str,
        origin: tuple[int, int],
        colour: tuple[int, int, int],
        scale: float = 0.45,
        thickness: int = 1,
        above: bool = True,
    ) -> None:
        """Text on a filled plate, clamped inside the frame."""
        if not text:
            return
        (tw, th), baseline = cv2.getTextSize(text, FONT, scale, thickness)
        x, y = origin
        pad = 3
        box_h = th + baseline + 2 * pad

        top = y - box_h if above else y
        top = max(0, min(top, image.shape[0] - box_h))
        left = max(0, min(x, image.shape[1] - (tw + 2 * pad)))

        cv2.rectangle(image, (left, top), (left + tw + 2 * pad, top + box_h), colour, -1)
        luminance = 0.114 * colour[0] + 0.587 * colour[1] + 0.299 * colour[2]
        text_colour = (0, 0, 0) if luminance > 140 else (255, 255, 255)
        cv2.putText(
            image, text, (left + pad, top + th + pad), FONT, scale, text_colour, thickness, cv2.LINE_AA
        )

    @staticmethod
    def _box(image: np.ndarray, bbox: BBox, colour: tuple[int, int, int], thickness: int = 2) -> None:
        x1, y1, x2, y2 = bbox.as_int()
        cv2.rectangle(image, (x1, y1), (x2, y2), colour, thickness)

    # ── composition ──
    def draw_frame(
        self,
        frame: np.ndarray,
        tracked: list[Detection],
        plates: list[PlateDetection],
        labels: dict[int, TrackLabel],
        hud: dict[str, str] | None = None,
    ) -> np.ndarray:
        canvas = frame.copy()

        if self.draw_trails:
            self._update_trails(tracked)
            self._draw_trails(canvas)

        for detection in tracked:
            track_id = detection.track_id or 0
            colour = colour_for(track_id)
            self._box(canvas, detection.bbox, colour, 2)

            label = labels.get(track_id, TrackLabel())
            head = f"#{track_id} {detection.class_name} {detection.confidence:.2f}"
            x1, y1, _, _ = detection.bbox.as_int()
            self._label(canvas, head, (x1, y1), colour)

            if label.text:
                plate_colour = UNCERTAIN_COLOUR if label.uncertain else PLATE_COLOUR
                mark = "?" if label.uncertain else ""
                body = f"{label.text}{mark} {label.confidence:.2f} ({label.reads})"
                self._label(canvas, body, (x1, y1 - 18), plate_colour, scale=0.5, thickness=1)

        for plate in plates:
            self._box(canvas, plate.bbox, PLATE_COLOUR, 2)
            x1, _, _, y2 = plate.bbox.as_int()
            self._label(
                canvas, f"plate {plate.confidence:.2f}", (x1, y2 + 2),
                PLATE_COLOUR, scale=0.38, above=False,
            )

        if hud:
            self._draw_hud(canvas, hud)
        return canvas

    def _update_trails(self, tracked: list[Detection]) -> None:
        live = set()
        for detection in tracked:
            track_id = detection.track_id or 0
            live.add(track_id)
            trail = self._trails.setdefault(track_id, [])
            trail.append((int(detection.bbox.cx), int(detection.bbox.cy)))
            if len(trail) > self.trail_length:
                del trail[0]
        # Forget vehicles that have left, or the frame slowly fills with the
        # ghosts of every car that ever passed.
        for track_id in [t for t in self._trails if t not in live]:
            trail = self._trails[track_id]
            del trail[0]
            if not trail:
                del self._trails[track_id]

    def _draw_trails(self, canvas: np.ndarray) -> None:
        for track_id, points in self._trails.items():
            if len(points) < 2:
                continue
            colour = colour_for(track_id)
            for i in range(1, len(points)):
                # Fade toward the tail so direction of travel is obvious.
                alpha = i / len(points)
                faded = tuple(int(c * (0.35 + 0.65 * alpha)) for c in colour)
                cv2.line(canvas, points[i - 1], points[i], faded, max(1, int(1 + 2 * alpha)))

    @staticmethod
    def _draw_hud(canvas: np.ndarray, hud: dict[str, str]) -> None:
        pairs = list(hud.items())
        if not pairs:
            return
        line_height = 20
        height = line_height * len(pairs) + 10
        width = 260

        overlay = canvas.copy()
        cv2.rectangle(overlay, (0, 0), (width, height), HUD_BG, -1)
        cv2.addWeighted(overlay, 0.65, canvas, 0.35, 0, canvas)

        for i, (key, value) in enumerate(pairs):
            y = 22 + i * line_height
            cv2.putText(canvas, f"{key}", (10, y), FONT, 0.42, (170, 170, 170), 1, cv2.LINE_AA)
            cv2.putText(canvas, f"{value}", (130, y), FONT, 0.42, (255, 255, 255), 1, cv2.LINE_AA)


class VideoWriter:
    """MP4 writer that reports what it actually managed to open.

    OpenCV's VideoWriter fails by returning False and then silently writing a
    0-byte file, which is a miserable thing to discover after a long run. This
    tries H.264 first (playable in a browser, so the HTML report can embed it)
    and falls back to MPEG-4 Part 2, recording which codec won.
    """

    def __init__(self, path: Path, fps: float, width: int, height: int) -> None:
        self.path = path
        self.fps = fps if fps > 0 else 25.0
        self.size = (int(width), int(height))
        self.codec = ""
        self._writer: cv2.VideoWriter | None = None

        for codec in ("avc1", "mp4v"):
            writer = cv2.VideoWriter(
                str(path), cv2.VideoWriter_fourcc(*codec), self.fps, self.size
            )
            if writer.isOpened():
                self._writer = writer
                self.codec = codec
                break
            writer.release()

        if self._writer is None:
            raise RuntimeError(
                f"could not open a video writer for {path} at {self.size}. "
                "The OpenCV build has neither an H.264 nor an MPEG-4 encoder."
            )
        if self.codec != "avc1":
            log.info(
                "annotated video uses the %s codec; it plays in VLC/QuickTime but "
                "most browsers will not embed it", self.codec
            )
        self.frames_written = 0

    @property
    def browser_playable(self) -> bool:
        return self.codec == "avc1"

    def write(self, frame: np.ndarray) -> None:
        if self._writer is None:
            return
        if (frame.shape[1], frame.shape[0]) != self.size:
            frame = cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
        self._writer.write(frame)
        self.frames_written += 1

    def close(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None

    def __enter__(self) -> VideoWriter:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def transcode_to_h264(path: Path) -> bool:
    """Re-encode an annotated video to H.264 so a browser can play it inline.

    OpenCV's bundled encoder cannot be relied on for H.264, so the video is
    written with whatever codec worked and converted afterwards if ffmpeg is
    present. Returns True when the file was converted.

    A failure here is cosmetic: the original video is left untouched and still
    plays in VLC. It must never take down a run that has already done the
    expensive work.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None or not path.exists():
        return False

    temporary = path.with_suffix(".h264.mp4")
    command = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", str(path),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",     # required by Safari and most hardware decoders
        "-movflags", "+faststart", # metadata first, so playback starts immediately
        str(temporary),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=1800, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("H.264 transcode failed, keeping the original: %s", exc)
        temporary.unlink(missing_ok=True)
        return False

    if completed.returncode != 0 or not temporary.exists() or temporary.stat().st_size == 0:
        log.warning(
            "H.264 transcode failed (exit %d), keeping the original: %s",
            completed.returncode, completed.stderr.decode("utf-8", "replace")[:400],
        )
        temporary.unlink(missing_ok=True)
        return False

    temporary.replace(path)
    return True
