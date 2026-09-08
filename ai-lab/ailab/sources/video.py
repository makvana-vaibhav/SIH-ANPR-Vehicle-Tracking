"""Video file / RTSP frame source."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import cv2

from ailab.config import SourceConfig
from ailab.logging import get_logger
from ailab.sources.base import FrameSource, SourceInfo
from ailab.types import Frame

log = get_logger(__name__)


def _fourcc_to_str(value: float) -> str:
    code = int(value)
    if code <= 0:
        return ""
    return "".join(chr((code >> (8 * i)) & 0xFF) for i in range(4)).strip()


class VideoSource(FrameSource):
    """Decodes a video with OpenCV.

    Reads sequentially and drops frames in software rather than seeking per
    frame: `CAP_PROP_POS_FRAMES` seeking on a long-GOP H.264 file forces a
    keyframe re-decode each time and is dramatically slower than decoding
    everything and discarding what we don't want.
    """

    def __init__(self, path: str | Path, config: SourceConfig) -> None:
        self._path = str(path)
        self._config = config
        self._cap = cv2.VideoCapture(self._path)
        if not self._cap.isOpened():
            raise RuntimeError(
                f"could not open video: {self._path} "
                "(unsupported codec, or the file is not a video)"
            )

        fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if fps <= 0.0 or fps > 240.0:
            # Some CCTV exports report 0 or nonsense. 25 is the PAL-region
            # default these cameras almost always use; timestamps stay usable.
            log.warning("source reports fps=%.3f, assuming 25.0", fps)
            fps = 25.0
        total = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        self._scale = 1.0
        if config.resize_width and width > config.resize_width:
            self._scale = config.resize_width / width
            width = config.resize_width
            height = int(round(height * self._scale))

        self._info = SourceInfo(
            name=Path(self._path).stem,
            path=self._path,
            kind="video",
            width=width,
            height=height,
            fps=fps,
            total_frames=total,
            duration_s=(total / fps) if total else 0.0,
            codec=_fourcc_to_str(self._cap.get(cv2.CAP_PROP_FOURCC)),
        )

    @property
    def info(self) -> SourceInfo:
        return self._info

    def __iter__(self) -> Iterator[Frame]:
        cfg = self._config
        fps = self._info.fps
        index = -1
        emitted = 0
        while True:
            ok, image = self._cap.read()
            if not ok:
                break
            index += 1
            t_s = index / fps

            if t_s < cfg.start_s:
                continue
            if cfg.end_s is not None and t_s > cfg.end_s:
                break
            if index % cfg.frame_stride != 0:
                continue
            if cfg.max_frames is not None and emitted >= cfg.max_frames:
                break

            if self._scale != 1.0:
                image = cv2.resize(
                    image, (self._info.width, self._info.height), interpolation=cv2.INTER_AREA
                )
            emitted += 1
            yield Frame(index=index, t_s=t_s, image=image, source_name=self._info.name)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
