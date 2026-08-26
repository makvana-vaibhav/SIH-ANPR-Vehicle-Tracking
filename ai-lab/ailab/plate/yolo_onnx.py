"""Licence-plate detection with a YOLO ONNX model — the default engine.

The weights are a YOLO11n fine-tuned on a licence-plate dataset (single class).
Running it on the vehicle crop rather than the whole frame is both faster and
more accurate: the plate occupies a far larger fraction of a 200x150 vehicle
crop than of a 1920x1080 frame, so the detector sees it at a workable scale.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ailab.config import PlateDetectorConfig
from ailab.detect.onnx_backend import OnnxYoloModel
from ailab.plate.base import PlateDetector
from ailab.registry import register
from ailab.types import BBox


@register("plate_detector", "plate_yolo_onnx")
class PlateYoloOnnxDetector(PlateDetector):
    def __init__(self, config: PlateDetectorConfig, weights_path: str) -> None:
        self.config = config
        self.model = OnnxYoloModel(
            weights_path, imgsz=config.imgsz, intra_threads=config.threads
        )
        self.last_latency_s = 0.0

    def detect(self, image: np.ndarray) -> list[tuple[BBox, float]]:
        if image is None or image.size == 0:
            return []
        height, width = image.shape[:2]
        if height < 8 or width < 8:
            return []

        size = self.model.input_size_for(
            image, self.config.min_imgsz, self.config.imgsz_strategy
        )
        boxes, scores, _ids, latency = self.model.infer(
            image, conf=self.config.confidence, iou=self.config.iou,
            max_detections=16, imgsz=size,
        )
        self.last_latency_s = latency

        out: list[tuple[BBox, float]] = []
        for box, score in zip(boxes, scores, strict=False):
            bbox = BBox(*(float(v) for v in box)).clip(width, height)
            if bbox.width < self.config.min_plate_width:
                continue
            out.append((bbox, float(score)))
        return out

    def describe(self) -> dict[str, Any]:
        return {"engine": "plate_yolo_onnx", **self.model.describe()}


def build(config: PlateDetectorConfig, weights_path: str) -> PlateYoloOnnxDetector:
    return PlateYoloOnnxDetector(config, weights_path)
