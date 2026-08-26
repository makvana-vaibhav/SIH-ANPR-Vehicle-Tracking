"""Classical licence-plate proposal — no learned weights required.

This is a genuine detector, not a placeholder: Sobel edge response, morphological
grouping of character strokes into a blob, then geometric and edge-density
filtering. It runs when no plate weights are available and, more usefully, gives
the learned detector something to be measured against. "The YOLO plate model
finds 94% of plates" means little on its own; "94% versus 61% for classical edge
grouping on the same footage" is a decision.

One caveat is recorded honestly in the output: the score this returns is a
geometric plausibility heuristic, NOT a learned probability. It is not
comparable to the YOLO detector's confidence and must never be pooled with it —
which is exactly the confidence-versus-accuracy confusion the lab exists to
prevent.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ailab.config import PlateDetectorConfig
from ailab.plate.base import PlateDetector
from ailab.registry import register
from ailab.types import BBox

# What morphological grouping actually returns is the *row of characters*, not
# the plate rectangle — the plate's margins have no edge response, so they are
# not part of the blob. A text row is markedly wider relative to its height than
# the plate containing it, which is why the ceiling is 10:1 and not the 4.5:1 a
# plate's outline would suggest. Measured on real crops: ~8:1 for a single-row
# Indian plate, ~2:1 for the two-row plates common on motorcycles.
ASPECT_MIN = 1.6
ASPECT_MAX = 10.0
IDEAL_ASPECTS = (2.2, 4.5, 8.0)

# The text row is then grown vertically to approximate the plate, so the crop
# handed to OCR carries the quiet margin recognisers expect.
VERTICAL_PAD = 0.30


@register("plate_detector", "contour")
class ContourPlateDetector(PlateDetector):
    def __init__(self, config: PlateDetectorConfig, weights_path: str | None = None) -> None:
        self.config = config
        # Wide, short kernel: merges the vertical strokes of adjacent characters
        # into one horizontal blob while keeping separate lines of text apart.
        self._close_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 3))
        self._open_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

    def detect(self, image: np.ndarray) -> list[tuple[BBox, float]]:
        if image is None or image.size == 0:
            return []
        height, width = image.shape[:2]
        if height < 16 or width < 32:
            return []

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # Bilateral: suppresses paint texture and road grain without softening
        # the character edges we are about to look for.
        gray = cv2.bilateralFilter(gray, 9, 75, 75)

        # Character strokes are predominantly vertical, so a horizontal
        # gradient responds to text far more strongly than to bodywork.
        sobel = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
        sobel = cv2.convertScaleAbs(sobel)

        _, binary = cv2.threshold(sobel, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, self._close_kernel)
        closed = cv2.morphologyEx(closed, cv2.MORPH_OPEN, self._open_kernel)

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        frame_area = float(height * width)
        candidates: list[tuple[BBox, float]] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w < self.config.min_plate_width or h < 8:
                continue

            aspect = w / h if h else 0.0
            if not (ASPECT_MIN <= aspect <= ASPECT_MAX):
                continue

            area = float(w * h)
            share = area / frame_area
            # Too small to read; too large to be a plate rather than the car.
            if not (0.0008 <= share <= 0.35):
                continue

            # Rectangularity: a plate fills its bounding box; a tree branch or
            # a shadow that happened to group does not.
            fill = float(cv2.contourArea(contour)) / area
            if fill < 0.35:
                continue

            roi = binary[y : y + h, x : x + w]
            edge_density = float(np.count_nonzero(roi)) / area
            # Text has busy but not saturated edges.
            if not (0.10 <= edge_density <= 0.75):
                continue

            pad = h * VERTICAL_PAD
            box = BBox(x, y - pad, x + w, y + h + pad).clip(width, height)
            candidates.append((box, self._score(aspect, fill, edge_density)))

        candidates.sort(key=lambda c: -c[1])
        kept = [c for c in candidates if c[1] >= self.config.confidence][:8]
        return kept

    @staticmethod
    def _score(aspect: float, fill: float, edge_density: float) -> float:
        """Geometric plausibility in 0..1. A heuristic, not a probability."""
        aspect_fit = max(
            0.0, 1.0 - min(abs(aspect - ideal) / ideal for ideal in IDEAL_ASPECTS)
        )
        # Peak edge density for text sits near 0.35; fall off either side.
        density_fit = max(0.0, 1.0 - abs(edge_density - 0.35) / 0.35)
        return round(0.45 * aspect_fit + 0.25 * fill + 0.30 * density_fit, 4)

    def describe(self) -> dict[str, Any]:
        return {
            "engine": "contour",
            "weights": None,
            "method": "sobel-x + morphological grouping + geometric filtering",
            "returns": (
                "character-row regions grown vertically, not plate outlines — "
                "adequate for OCR, not comparable to a plate-detector's box IoU"
            ),
            "score_semantics": (
                "geometric plausibility heuristic, NOT a learned probability; "
                "not comparable with a YOLO confidence"
            ),
            "aspect_range": [ASPECT_MIN, ASPECT_MAX],
        }


def build(config: PlateDetectorConfig, weights_path: str | None = None) -> ContourPlateDetector:
    return ContourPlateDetector(config, weights_path)
