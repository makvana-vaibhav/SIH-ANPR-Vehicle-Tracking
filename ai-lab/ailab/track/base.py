"""Tracker interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ailab.types import Detection


class Tracker(ABC):
    """Assigns stable identities to detections across frames.

    `update` returns the detections that were successfully associated, each
    carrying a `track_id`. Detections the tracker chose not to promote to a
    track are not returned — a one-frame flicker should not become a vehicle.
    """

    engine_name: str = "unknown"

    @abstractmethod
    def update(self, detections: list[Detection], frame_index: int, t_s: float) -> list[Detection]: ...

    @abstractmethod
    def describe(self) -> dict[str, Any]: ...

    def reset(self) -> None:
        return None
