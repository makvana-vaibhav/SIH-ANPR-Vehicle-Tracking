"""Object-detector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from ailab.types import Detection


class ObjectDetector(ABC):
    """Finds vehicles and people in a frame.

    Implementations are registered under a name and selected by config, so
    swapping YOLOv8n for YOLO11m — or for a different runtime entirely — is a
    one-line config change and a directly comparable run.
    """

    engine_name: str = "unknown"

    @abstractmethod
    def detect(self, image: np.ndarray, frame_index: int, t_s: float) -> list[Detection]: ...

    @abstractmethod
    def describe(self) -> dict[str, Any]:
        """Provenance recorded in the run manifest."""

    def close(self) -> None:
        return None
