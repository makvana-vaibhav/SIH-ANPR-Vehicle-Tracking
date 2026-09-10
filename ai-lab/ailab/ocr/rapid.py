"""RapidOCR — PP-OCRv4 detection + recognition as ONNX. The default engine.

Chosen for three reasons that matter to this project specifically:

* No PyTorch. The models ship inside the wheel as ONNX and run on the same
  onnxruntime the rest of the pipeline uses, which keeps the lab's findings
  transferable to the torch-free production worker.
* No download at first use. EasyOCR fetches weights from the internet the first
  time it runs; for a system that must work offline at a demo venue, a model
  that is already on disk is worth a lot.
* PP-OCR handles two-row plates, because detection runs before recognition and
  each row comes back as its own line.

Recognition runs FIRST, on the whole crop, and text detection is the fallback.
That ordering is the opposite of PP-OCR's normal usage and it matters: measured
on this host, the detection stage costs 1334 ms against 74 ms for recognition
alone — 18x — and returns the identical string. The plate detector has already
localised the plate, so asking a text detector to find it again is pure waste.

Detection still earns its place as the fallback. A two-row plate recognised as
one line comes back as nonsense, and text that does not fill the crop confuses
the recogniser; in both cases the result fails the plausibility check below and
the full detect-then-recognise path runs.
"""

from __future__ import annotations

import re
import time
from typing import Any

import numpy as np

from ailab import runtime
from ailab.config import OcrConfig
from ailab.logging import get_logger
from ailab.ocr.base import OcrEngine, OcrResult
from ailab.registry import register

log = get_logger(__name__)


@register("ocr", "rapidocr")
class RapidOcrEngine(OcrEngine):
    def __init__(self, config: OcrConfig) -> None:
        from rapidocr_onnxruntime import RapidOCR

        self.config = config
        # RapidOCR builds three ONNX sessions (detection, classification,
        # recognition) and its shipped config leaves all three at
        # `intra_op_num_threads: -1`, which is ONNX Runtime's "one thread per
        # core". Unset, that is three thread pools of ten *per camera*, and the
        # worker runs one pipeline per camera: measured 168 OS threads on a
        # 10-core host, 866% CPU, load average 18.8, and a UI that hung because
        # nothing was left to render it.
        #
        # It does not even buy latency. Measured here on one plate crop:
        # 132.6 ms of CPU per call at the default for a 14.3 ms answer, against
        # 23.6 ms of CPU for an 11.8 ms answer at two threads — 5.6x the CPU to
        # be slower. These are small models and they stop scaling early.
        #
        # RapidOCR propagates these two Global keys down to all three stages.
        threads = runtime.threads_per_model()
        self._engine = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1)
        self._threads = threads
        self._allow = re.compile(f"[^{re.escape(config.allowlist)}]")

    def _clean(self, text: str) -> str:
        return self._allow.sub("", text.upper())

    def read(self, image: np.ndarray) -> OcrResult:
        if image is None or image.size == 0:
            return OcrResult(text="", confidence=0.0)

        # Recognition-only first — see the module docstring for why.
        quick = self._recognise_whole(image)
        if self._is_plate_shaped(quick):
            return quick

        if not self._detection_could_help(image):
            return quick

        # Fall back to detection + recognition for two-row plates and crops
        # where the text does not sit where the recogniser expects it.
        thorough = self._detect_and_recognise(image)
        thorough.latency_ms += quick.latency_ms
        if thorough.text and (
            self._is_plate_shaped(thorough) or len(thorough.text) > len(quick.text)
        ):
            return thorough
        return quick

    def _detection_could_help(self, image: np.ndarray) -> bool:
        """Is the expensive detection pass worth attempting on this crop?

        Only when it could plausibly change the answer. A wide crop holds a
        single row of text; if recognition alone could not read it, locating
        that same row first and handing the same pixels back to the same
        recogniser will not rescue it — it will only cost 18x as long. A squarer
        crop may be a two-row plate, and there detection genuinely helps.

        Without this gate the fallback fires on every distant, unreadable
        vehicle in the frame, which is precisely where it has least to offer and
        where there are most of them. Measured on 1080p bus-stand footage before
        the gate was tightened: 974 ms mean OCR across 186 plate regions, and
        not one successful read to show for it.
        """
        if not self.config.detection_fallback:
            return False
        if image is None or image.size == 0:
            return False
        height, width = image.shape[:2]
        if height < self.config.detection_fallback_min_height or width < 48:
            return False
        return (width / height) < self.config.detection_fallback_max_aspect

    def _is_plate_shaped(self, result: OcrResult) -> bool:
        """Is this result good enough to skip the expensive detection pass?"""
        return (
            self.config.min_chars <= len(result.text) <= self.config.max_chars
            and result.confidence >= max(0.40, self.config.min_confidence)
        )

    def _detect_and_recognise(self, image: np.ndarray) -> OcrResult:
        """Full PP-OCR: locate text lines, then read each one."""
        started = time.perf_counter()
        try:
            raw, _elapse = self._engine(image)
        except (ValueError, RuntimeError, IndexError) as exc:
            # Degenerate crops (1-pixel dimensions, all-black) make the
            # detector's post-processing throw. That is a skipped read, not a
            # failed run — record it and move on.
            log.debug("rapidocr detection failed on a crop: %s", exc)
            raw = None
        latency_ms = (time.perf_counter() - started) * 1000.0

        if not raw:
            return OcrResult(text="", confidence=0.0, latency_ms=latency_ms)

        # raw entries are [box_points, text, score]; order top-to-bottom then
        # left-to-right so a two-row plate reads in the human order.
        entries = []
        for item in raw:
            if len(item) < 3:
                continue
            box, text, score = item[0], str(item[1]), float(item[2])
            points = np.asarray(box, dtype=np.float32).reshape(-1, 2)
            entries.append((float(points[:, 1].min()), float(points[:, 0].min()), text, score))
        entries.sort(key=lambda e: (round(e[0] / 12.0), e[1]))

        lines = [(self._clean(t), s) for _, _, t, s in entries]
        lines = [(t, s) for t, s in lines if t]
        if not lines:
            return OcrResult(text="", confidence=0.0, latency_ms=latency_ms)

        text = "".join(t for t, _ in lines)
        # Weight each line's contribution by its length: a stray two-character
        # fragment should not drag down the score of a full plate row.
        total_chars = sum(len(t) for t, _ in lines) or 1
        confidence = sum(s * len(t) for t, s in lines) / total_chars

        return OcrResult(
            text=text, confidence=float(confidence), latency_ms=latency_ms, lines=lines
        )

    def _recognise_whole(self, image: np.ndarray) -> OcrResult:
        """Recognition-only pass: treat the entire crop as one text line."""
        started = time.perf_counter()
        try:
            raw, _elapse = self._engine(image, use_det=False, use_cls=False, use_rec=True)
        except (ValueError, RuntimeError, IndexError) as exc:
            log.debug("rapidocr recognition failed on a crop: %s", exc)
            raw = None
        latency_ms = (time.perf_counter() - started) * 1000.0

        if not raw:
            return OcrResult(text="", confidence=0.0, latency_ms=latency_ms)

        item = raw[0]
        text = self._clean(str(item[0] if len(item) == 2 else item[1]))
        score = float(item[1] if len(item) == 2 else item[2])
        return OcrResult(
            text=text,
            confidence=score if text else 0.0,
            latency_ms=latency_ms,
            lines=[(text, score)] if text else [],
        )

    def describe(self) -> dict[str, Any]:
        import rapidocr_onnxruntime

        return {
            "engine": "rapidocr",
            "backend": "onnxruntime",
            "models": "PP-OCRv4 det + rec (bundled with the wheel, no download)",
            "version": getattr(rapidocr_onnxruntime, "__version__", "unknown"),
            "char_level_confidence": False,
            "allowlist": self.config.allowlist,
            "intra_op_threads": self._threads,
            "strategy": (
                "recognition-first; text detection only as a fallback "
                "(detection measured at 18x the cost for the same result)"
            ),
        }


def build(config: OcrConfig) -> RapidOcrEngine:
    return RapidOcrEngine(config)
