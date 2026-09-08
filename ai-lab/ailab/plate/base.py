"""Licence-plate detector interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from ailab.types import BBox


class PlateDetector(ABC):
    """Locates plate regions in an image.

    Returns boxes in the coordinate space of the image handed in — the caller
    is responsible for mapping a vehicle-crop result back to the full frame.
    """

    engine_name: str = "unknown"

    @abstractmethod
    def detect(self, image: np.ndarray) -> list[tuple[BBox, float]]: ...

    @abstractmethod
    def describe(self) -> dict[str, Any]: ...
