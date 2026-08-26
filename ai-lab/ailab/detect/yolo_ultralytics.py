"""YOLO object detection through Ultralytics/PyTorch — optional comparison engine.

Only importable when the torch profile is installed. It exists so that "does
the ONNX export cost us accuracy?" is a question we answer by measurement:
run the same footage through both engines and diff the results.

Ultralytics is AGPL-3.0. It is a development dependency of this lab and never
ships in a deployed Sentinel-GJ artifact — see the licence note in README.md.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ailab.config import DetectorConfig
from ailab.detect.base import ObjectDetector
from ailab.logging import get_logger
from ailab.registry import register
from ailab.types import BBox, Detection

log = get_logger(__name__)


@register("detector", "ultralytics")
class UltralyticsDetector(ObjectDetector):
    def __init__(self, config: DetectorConfig, weights_path: str) -> None:
        from ultralytics import YOLO  # imported lazily: heavy, optional

        self.config = config
        self.model = YOLO(weights_path)
        self.weights_path = weights_path

        names: dict[int, str] = dict(self.model.names)
        by_name = {v.lower(): k for k, v in names.items()}
        wanted = {c.lower() for c in config.classes}
        unknown = wanted - set(by_name)
        if unknown:
            raise ValueError(f"detector classes {sorted(unknown)} are not in this model")
        self.allowed_ids = sorted(by_name[n] for n in wanted) if wanted else None
        self.names = names

        if config.device == "cpu":
            self.device: str | int = "cpu"
        elif config.device == "cuda":
            self.device = 0
        else:
            import torch

            self.device = 0 if torch.cuda.is_available() else "cpu"

    def detect(self, image: np.ndarray, frame_index: int, t_s: float) -> list[Detection]:
        results = self.model.predict(
            image,
            conf=self.config.confidence,
            iou=self.config.iou,
            imgsz=self.config.imgsz,
            classes=self.allowed_ids,
            max_det=self.config.max_detections,
            device=self.device,
            verbose=False,
        )
        height, width = image.shape[:2]
        out: list[Detection] = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            for xyxy, conf, cls in zip(
                boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy(), boxes.cls.cpu().numpy(), strict=False
            ):
                class_id = int(cls)
                out.append(
                    Detection(
                        bbox=BBox(*(float(v) for v in xyxy)).clip(width, height),
                        confidence=float(conf),
                        class_id=class_id,
                        class_name=self.names.get(class_id, f"class_{class_id}"),
                        frame_index=frame_index,
                        t_s=t_s,
                    )
                )
        return out

    def describe(self) -> dict[str, Any]:
        import ultralytics

        return {
            "engine": "ultralytics",
            "weights": self.weights_path,
            "imgsz": self.config.imgsz,
            "device": str(self.device),
            "classes": len(self.names),
            "ultralytics_version": ultralytics.__version__,
            "license": "AGPL-3.0 (development tool, not shipped)",
        }


def build(config: DetectorConfig, weights_path: str) -> UltralyticsDetector:
    return UltralyticsDetector(config, weights_path)
