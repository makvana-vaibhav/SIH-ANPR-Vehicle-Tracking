"""CRNN + CTC recognition on ONNX Runtime.

This is the engine shape the production NagarNetra worker targets: a single
small recognition network, no text-detection stage, run on a crop that the plate
detector has already isolated. It is the only engine here that exposes genuine
per-character confidence, because CTC gives a probability distribution per time
step — which makes it the most useful engine for character-position consensus
voting.

It needs its own weights and charset; point `ocr.weights` at a `.onnx` file with
a sibling `.charset.txt`. Unlike the other engines there is no bundled model, so
this raises a clear error rather than pretending to work.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort

from ailab.config import OcrConfig
from ailab.detect.onnx_backend import select_providers
from ailab.ocr.base import OcrEngine, OcrResult
from ailab.registry import register

# CTC blank is conventionally index 0 in the exports this loader targets.
BLANK_INDEX = 0


@register("ocr", "crnn_onnx")
class CrnnOnnxEngine(OcrEngine):
    def __init__(self, config: OcrConfig) -> None:
        if not config.weights:
            raise ValueError(
                "ocr.engine='crnn_onnx' requires ocr.weights pointing at a CRNN .onnx file. "
                "No CRNN model ships with the lab — use 'rapidocr' for a working default, "
                "or export a CRNN and set ocr.weights."
            )
        path = Path(config.weights)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent.parent / path
        if not path.exists():
            raise FileNotFoundError(f"CRNN weights not found: {path}")

        charset_path = path.with_suffix(".charset.txt")
        if not charset_path.exists():
            raise FileNotFoundError(
                f"charset file not found: {charset_path}. It must list one character per "
                "line in the model's label order, excluding the CTC blank."
            )
        self.charset = ["<blank>", *charset_path.read_text(encoding="utf-8").splitlines()]

        self.config = config
        self.providers = select_providers("auto")
        self.session = ort.InferenceSession(str(path), providers=self.providers)
        self.weights_path = str(path)

        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.input_dtype = np.float16 if "float16" in inp.type else np.float32
        self.channels = inp.shape[1] if isinstance(inp.shape[1], int) else 3
        self.height = inp.shape[2] if isinstance(inp.shape[2], int) else 48
        self.width = inp.shape[3] if isinstance(inp.shape[3], int) else 320

    def _prepare(self, image: np.ndarray) -> np.ndarray:
        target = cv2.resize(image, (self.width, self.height), interpolation=cv2.INTER_CUBIC)
        if self.channels == 1:
            gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY) if target.ndim == 3 else target
            blob = gray[None, None, :, :]
        else:
            rgb = target[:, :, ::-1] if target.ndim == 3 else cv2.cvtColor(target, cv2.COLOR_GRAY2RGB)
            blob = rgb.transpose(2, 0, 1)[None]
        blob = np.ascontiguousarray(blob, dtype=self.input_dtype) / self.input_dtype(255.0)
        # Standard [-1, 1] normalisation used by PP-OCR/CRNN recognisers.
        return (blob - self.input_dtype(0.5)) / self.input_dtype(0.5)

    def read(self, image: np.ndarray) -> OcrResult:
        if image is None or image.size == 0:
            return OcrResult(text="", confidence=0.0)

        started = time.perf_counter()
        logits = self.session.run(None, {self.input_name: self._prepare(image)})[0]
        latency_ms = (time.perf_counter() - started) * 1000.0

        probs = np.asarray(logits, dtype=np.float32)
        if probs.ndim == 3:
            probs = probs[0]
        # (T, C) expected; transpose a (C, T) export.
        if probs.shape[0] < probs.shape[1] and probs.shape[1] == len(self.charset):
            pass
        elif probs.shape[0] == len(self.charset):
            probs = probs.transpose()
        probs = _softmax(probs) if probs.max() > 1.0 or probs.min() < 0.0 else probs

        text, char_confs = self._ctc_greedy_decode(probs)
        confidence = float(np.mean(char_confs)) if char_confs else 0.0
        return OcrResult(
            text=text,
            confidence=confidence,
            latency_ms=latency_ms,
            lines=[(text, confidence)] if text else [],
            char_confidences=char_confs,
        )

    def _ctc_greedy_decode(self, probs: np.ndarray) -> tuple[str, list[float]]:
        """Collapse repeats and drop blanks, keeping each character's probability."""
        best = probs.argmax(axis=1)
        scores = probs[np.arange(probs.shape[0]), best]

        chars: list[str] = []
        confs: list[float] = []
        previous = -1
        for index, score in zip(best, scores, strict=False):
            index = int(index)
            if index != previous and index != BLANK_INDEX and index < len(self.charset):
                chars.append(self.charset[index])
                confs.append(float(score))
            previous = index

        text = "".join(chars).upper()
        allowed = set(self.config.allowlist)
        filtered = [(c, s) for c, s in zip(text, confs, strict=False) if c in allowed]
        return "".join(c for c, _ in filtered), [s for _, s in filtered]

    def describe(self) -> dict[str, Any]:
        return {
            "engine": "crnn_onnx",
            "backend": "onnxruntime",
            "weights": self.weights_path,
            "input": [self.channels, self.height, self.width],
            "charset_size": len(self.charset),
            "providers": self.providers,
            "char_level_confidence": True,
        }


def _softmax(x: np.ndarray) -> np.ndarray:
    shifted = x - x.max(axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=-1, keepdims=True)


def build(config: OcrConfig) -> CrnnOnnxEngine:
    return CrnnOnnxEngine(config)
