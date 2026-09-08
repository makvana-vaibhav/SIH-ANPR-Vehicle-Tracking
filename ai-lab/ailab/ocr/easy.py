"""EasyOCR engine — optional comparison engine.

This is what the widely-copied YOLOv8 ANPR tutorials use, which makes it the
obvious baseline to measure our default against. Requires the torch profile,
and downloads its detection and recognition weights from the internet on first
use — worth knowing before relying on it at a venue with no network.
"""

from __future__ import annotations

import re
import time
from typing import Any

import numpy as np

from ailab.config import OcrConfig
from ailab.logging import get_logger
from ailab.ocr.base import OcrEngine, OcrResult
from ailab.registry import register

log = get_logger(__name__)


@register("ocr", "easyocr")
class EasyOcrEngine(OcrEngine):
    def __init__(self, config: OcrConfig) -> None:
        import easyocr
        import torch

        self.config = config
        self._gpu = torch.cuda.is_available()
        self._reader = easyocr.Reader(config.languages, gpu=self._gpu, verbose=False)
        self._allow = re.compile(f"[^{re.escape(config.allowlist)}]")

    def read(self, image: np.ndarray) -> OcrResult:
        if image is None or image.size == 0:
            return OcrResult(text="", confidence=0.0)

        started = time.perf_counter()
        try:
            detections = self._reader.readtext(
                image, allowlist=self.config.allowlist, detail=1, paragraph=False
            )
        except (ValueError, RuntimeError) as exc:
            log.debug("easyocr failed on a crop: %s", exc)
            detections = []
        latency_ms = (time.perf_counter() - started) * 1000.0

        entries = []
        for box, text, score in detections:
            cleaned = self._allow.sub("", str(text).upper())
            if not cleaned:
                continue
            points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
            entries.append((float(points[:, 1].min()), float(points[:, 0].min()), cleaned, float(score)))

        if not entries:
            return OcrResult(text="", confidence=0.0, latency_ms=latency_ms)

        entries.sort(key=lambda e: (round(e[0] / 12.0), e[1]))
        lines = [(t, s) for _, _, t, s in entries]
        text = "".join(t for t, _ in lines)
        total_chars = sum(len(t) for t, _ in lines) or 1
        confidence = sum(s * len(t) for t, s in lines) / total_chars

        return OcrResult(
            text=text, confidence=float(confidence), latency_ms=latency_ms, lines=lines
        )

    def describe(self) -> dict[str, Any]:
        import easyocr

        return {
            "engine": "easyocr",
            "backend": "pytorch",
            "gpu": self._gpu,
            "languages": self.config.languages,
            "version": getattr(easyocr, "__version__", "unknown"),
            "char_level_confidence": False,
            "note": "downloads weights on first use; needs network once",
        }


def build(config: OcrConfig) -> EasyOcrEngine:
    return EasyOcrEngine(config)
