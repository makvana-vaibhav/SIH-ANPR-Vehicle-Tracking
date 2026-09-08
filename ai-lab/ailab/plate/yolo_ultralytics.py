"""Licence-plate detection through Ultralytics — optional comparison engine.

Lets a `.pt` plate model be evaluated directly, without exporting to ONNX
first. Requires the torch profile.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ailab.config import PlateDetectorConfig
from ailab.plate.base import PlateDetector
from ailab.registry import register
from ailab.types import BBox


@register("plate_detector", "plate_ultralytics")
class PlateUltralyticsDetector(PlateDetector):
    def __init__(self, config: PlateDetectorConfig, weights_path: str) -> None:
        from ultralytics import YOLO

        self.config = config
        self.weights_path = weights_path
        self.model = YOLO(weights_path)

    def detect(self, image: np.ndarray) -> list[tuple[BBox, float]]:
        if image is None or image.size == 0:
            return []
        height, width = image.shape[:2]
        if height < 8 or width < 8:
            return []

        results = self.model.predict(
            image,
            conf=self.config.confidence,
            iou=self.config.iou,
            imgsz=self.config.imgsz,
            verbose=False,
        )
        out: list[tuple[BBox, float]] = []
        for result in results:
            if result.boxes is None:
                continue
            for xyxy, conf in zip(
                result.boxes.xyxy.cpu().numpy(), result.boxes.conf.cpu().numpy(), strict=False
            ):
                bbox = BBox(*(float(v) for v in xyxy)).clip(width, height)
                if bbox.width < self.config.min_plate_width:
                    continue
                out.append((bbox, float(conf)))
        return out

    def describe(self) -> dict[str, Any]:
        return {"engine": "plate_ultralytics", "weights": self.weights_path}


def build(config: PlateDetectorConfig, weights_path: str) -> PlateUltralyticsDetector:
    return PlateUltralyticsDetector(config, weights_path)
