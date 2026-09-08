"""YOLO object detection on ONNX Runtime — the default engine.

Torch-free, which is why it is the default: it is also what the production
ai-worker will run, so numbers measured here transfer to production instead of
having to be re-measured after an export.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ailab.config import DetectorConfig
from ailab.detect.base import ObjectDetector
from ailab.detect.onnx_backend import OnnxYoloModel
from ailab.logging import get_logger
from ailab.registry import register
from ailab.types import BBox, Detection

log = get_logger(__name__)


@register("detector", "yolo_onnx")
class YoloOnnxDetector(ObjectDetector):
    def __init__(self, config: DetectorConfig, weights_path: str) -> None:
        self.config = config
        self.model = OnnxYoloModel(
            weights_path, imgsz=config.imgsz, device=config.device,
            intra_threads=config.threads,
        )

        # Resolve the configured class names against what the model actually
        # knows. A typo like "motorbike" (COCO says "motorcycle") would
        # otherwise silently filter out every motorcycle in the footage.
        wanted = {c.lower() for c in config.classes}
        by_name = {name.lower(): cid for cid, name in self.model.names.items()}
        unknown = wanted - set(by_name)
        if unknown:
            raise ValueError(
                f"detector classes {sorted(unknown)} are not in this model. "
                f"Available: {sorted(by_name)[:20]}{'...' if len(by_name) > 20 else ''}"
            )
        self.allowed_ids = {by_name[name] for name in wanted} if wanted else None
        self.last_latency_s = 0.0

    def detect(self, image: np.ndarray, frame_index: int, t_s: float) -> list[Detection]:
        boxes, scores, class_ids, latency = self.model.infer(
            image,
            conf=self.config.confidence,
            iou=self.config.iou,
            max_detections=self.config.max_detections,
            allowed_class_ids=self.allowed_ids,
        )
        self.last_latency_s = latency

        height, width = image.shape[:2]
        out: list[Detection] = []
        for box, score, class_id in zip(boxes, scores, class_ids, strict=False):
            out.append(
                Detection(
                    bbox=BBox(*(float(v) for v in box)).clip(width, height),
                    confidence=float(score),
                    class_id=int(class_id),
                    class_name=self.model.class_name(int(class_id)),
                    frame_index=frame_index,
                    t_s=t_s,
                )
            )
        return out

    def describe(self) -> dict[str, Any]:
        return {"engine": "yolo_onnx", **self.model.describe()}


def build(config: DetectorConfig, weights_path: str) -> YoloOnnxDetector:
    return YoloOnnxDetector(config, weights_path)
