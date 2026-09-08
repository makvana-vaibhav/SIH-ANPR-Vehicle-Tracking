"""Frame sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from ailab.types import Frame


@dataclass(slots=True)
class SourceInfo:
    """What we know about the input before processing it."""

    name: str
    path: str
    kind: str                   # "video" | "images"
    width: int
    height: int
    fps: float
    total_frames: int           # 0 when the container does not report it
    duration_s: float
    codec: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 3),
            "total_frames": self.total_frames,
            "duration_s": round(self.duration_s, 2),
            "codec": self.codec,
        }


class FrameSource(ABC):
    """Anything that can produce frames with timestamps."""

    @property
    @abstractmethod
    def info(self) -> SourceInfo: ...

    @abstractmethod
    def __iter__(self) -> Iterator[Frame]: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self) -> FrameSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
