"""Shared fixtures."""

from __future__ import annotations

import numpy as np
import pytest

from ailab.types import CropQuality, PlateRead


@pytest.fixture
def make_read():
    """Build a PlateRead with sensible defaults, overriding what a test cares about."""

    def _make(
        text: str,
        ocr_confidence: float = 0.9,
        sharpness: float = 200.0,
        width: int = 140,
        height: int = 40,
        frame_index: int = 0,
        track_id: int = 1,
        grammar_valid: bool = True,
    ) -> PlateRead:
        return PlateRead(
            text_raw=text,
            text=text,
            ocr_confidence=ocr_confidence,
            frame_index=frame_index,
            t_s=frame_index / 25.0,
            track_id=track_id,
            engine="test",
            quality=CropQuality(
                sharpness=sharpness, brightness=128.0, contrast=45.0,
                width=width, height=height,
            ),
            grammar_valid=grammar_valid,
        )

    return _make


@pytest.fixture
def blank_frame() -> np.ndarray:
    return np.full((480, 640, 3), 60, dtype=np.uint8)
