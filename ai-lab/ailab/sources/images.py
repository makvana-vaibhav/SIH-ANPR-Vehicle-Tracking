"""Image-directory frame source.

Lets a still dataset run through exactly the same pipeline as video. Tracking
is meaningless across unrelated stills, so each image becomes its own track —
which is the correct behaviour for evaluating a plate dataset image by image.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import cv2

from ailab.config import SourceConfig
from ailab.logging import get_logger
from ailab.sources.base import FrameSource, SourceInfo
from ailab.types import Frame

log = get_logger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


class ImageDirectorySource(FrameSource):
    """Every image in a directory (recursively), in sorted order."""

    # Nominal rate so downstream timestamps stay meaningful and the annotated
    # output is watchable as a contact sheet.
    NOMINAL_FPS = 1.0

    def __init__(self, path: str | Path, config: SourceConfig) -> None:
        root = Path(path)
        if root.is_file():
            self._files = [root]
            root = root.parent
        else:
            self._files = sorted(
                p for p in root.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES
            )
        if not self._files:
            raise RuntimeError(f"no images found under {path}")

        if config.max_frames is not None:
            self._files = self._files[: config.max_frames]

        self._config = config
        first = cv2.imread(str(self._files[0]))
        if first is None:
            raise RuntimeError(f"could not decode image: {self._files[0]}")

        self._info = SourceInfo(
            name=root.name or "images",
            path=str(root),
            kind="images",
            width=int(first.shape[1]),
            height=int(first.shape[0]),
            fps=self.NOMINAL_FPS,
            total_frames=len(self._files),
            duration_s=len(self._files) / self.NOMINAL_FPS,
            codec="still",
        )
        self.file_names: list[str] = [str(p) for p in self._files]

    @property
    def info(self) -> SourceInfo:
        return self._info

    def __iter__(self) -> Iterator[Frame]:
        for index, path in enumerate(self._files):
            image = cv2.imread(str(path))
            if image is None:
                log.warning("skipping undecodable image: %s", path)
                continue
            if self._config.resize_width and image.shape[1] > self._config.resize_width:
                scale = self._config.resize_width / image.shape[1]
                image = cv2.resize(
                    image,
                    (self._config.resize_width, int(round(image.shape[0] * scale))),
                    interpolation=cv2.INTER_AREA,
                )
            yield Frame(
                index=index,
                t_s=index / self.NOMINAL_FPS,
                image=image,
                source_name=path.stem,
            )

    def close(self) -> None:
        return None
