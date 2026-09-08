"""OCR engine interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(slots=True)
class OcrResult:
    """What one OCR engine made of one image."""

    text: str
    confidence: float
    latency_ms: float = 0.0
    # Per-line output, top-to-bottom. Two-row plates (common on motorcycles and
    # commercial vehicles) come back as two lines and are joined by the caller.
    lines: list[tuple[str, float]] = field(default_factory=list)
    # Per-character confidence where the engine exposes it. Empty otherwise —
    # and the consensus stage checks rather than assuming, because silently
    # treating a line score as a character score would make weak characters
    # look as trustworthy as strong ones.
    char_confidences: list[float] = field(default_factory=list)

    @property
    def has_char_confidence(self) -> bool:
        return len(self.char_confidences) == len(self.text) and bool(self.text)


class OcrEngine(ABC):
    """Reads text from a prepared plate crop."""

    engine_name: str = "unknown"

    @abstractmethod
    def read(self, image: np.ndarray) -> OcrResult: ...

    @abstractmethod
    def describe(self) -> dict[str, Any]: ...

    def close(self) -> None:
        return None
