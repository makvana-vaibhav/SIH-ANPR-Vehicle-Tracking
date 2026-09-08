"""Frame sources — video files, RTSP URLs, and image directories."""

from __future__ import annotations

from pathlib import Path

from ailab.config import SourceConfig
from ailab.sources.base import FrameSource, SourceInfo
from ailab.sources.images import IMAGE_SUFFIXES, ImageDirectorySource
from ailab.sources.video import VideoSource

__all__ = [
    "FrameSource",
    "ImageDirectorySource",
    "SourceInfo",
    "VideoSource",
    "open_source",
]

VIDEO_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".mpg", ".mpeg", ".ts"}


def open_source(path: str, config: SourceConfig) -> FrameSource:
    """Pick the right source for whatever the user pointed at."""
    if path.startswith(("rtsp://", "rtsps://", "http://", "https://")):
        return VideoSource(path, config)

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"input not found: {path}")
    if p.is_dir():
        return ImageDirectorySource(p, config)
    suffix = p.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return ImageDirectorySource(p, config)
    if suffix in VIDEO_SUFFIXES:
        return VideoSource(p, config)
    # Unknown extension: let OpenCV decide rather than refusing outright.
    return VideoSource(p, config)
