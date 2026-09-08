"""End-to-end plate reading on synthetic plates.

Needs no downloaded weights: RapidOCR ships its PP-OCRv4 models inside the
wheel, and the classical plate detector needs none. So this exercises the real
detect → condition → OCR → normalise → grammar path with nothing mocked.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from ailab.aggregate.consensus import consensus
from ailab.aggregate.grammar import normalise
from ailab.config import ConsensusConfig, OcrConfig, PlateDetectorConfig, PreprocessConfig
from ailab.plate.contour import ContourPlateDetector
from ailab.preprocess import build_variants, measure
from ailab.registry import create
from ailab.types import CropQuality, PlateRead


def render_plate(text: str = "GJ03AB1234", width: int = 320, height: int = 80) -> np.ndarray:
    """A high-contrast synthetic Indian plate.

    The font is scaled to fit inside the plate with a margin. Getting this wrong
    is easy and instructive: an over-wide font is drawn from a negative x offset
    and the first character is clipped off-canvas, after which OCR reads
    '$J03AB1234' and the test appears to indict the recogniser.
    """
    image = np.full((height, width, 3), 250, dtype=np.uint8)
    cv2.rectangle(image, (3, 3), (width - 4, height - 4), (15, 15, 15), 3)

    margin_x, margin_y = int(width * 0.10), int(height * 0.22)
    available_w, available_h = width - 2 * margin_x, height - 2 * margin_y

    (base_w, base_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, 1.0, 2)
    scale = min(available_w / max(base_w, 1), available_h / max(base_h, 1))
    thickness = max(1, int(round(scale * 1.6)))

    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, thickness)
    cv2.putText(
        image, text, ((width - tw) // 2, (height + th) // 2),
        cv2.FONT_HERSHEY_DUPLEX, scale, (10, 10, 10), thickness, cv2.LINE_AA,
    )
    return image


def scene_with_plate(plate_text: str = "GJ03AB1234") -> np.ndarray:
    """A plate pasted onto a textured background, as a plate detector would meet it."""
    rng = np.random.default_rng(7)
    scene = rng.integers(70, 110, (300, 500, 3), dtype=np.uint8)
    plate = render_plate(plate_text, 240, 60)
    scene[120:180, 130:370] = plate
    return scene


@pytest.fixture(scope="module")
def ocr_engine():
    return create("ocr", "rapidocr", OcrConfig())


class TestOcr:
    def test_reads_a_clean_plate(self, ocr_engine) -> None:
        result = ocr_engine.read(render_plate("GJ03AB1234"))
        assert normalise(result.text) == "GJ03AB1234"
        assert result.confidence > 0.5

    def test_reads_through_the_conditioning_pipeline(self, ocr_engine) -> None:
        """A small plate, upscaled and equalised the way the pipeline does it."""
        small = cv2.resize(render_plate("GJ18CD4567"), (120, 30), interpolation=cv2.INTER_AREA)
        variants = build_variants(small, PreprocessConfig(variants=["upscaled"]))
        assert variants
        result = ocr_engine.read(variants[0][1])
        assert normalise(result.text) == "GJ18CD4567"

    def test_empty_input_is_safe(self, ocr_engine) -> None:
        result = ocr_engine.read(np.empty((0, 0, 3), dtype=np.uint8))
        assert result.text == "" and result.confidence == 0.0

    def test_degenerate_input_does_not_raise(self, ocr_engine) -> None:
        """A 1px crop must be a skipped read, not a crashed run."""
        assert ocr_engine.read(np.zeros((1, 1, 3), dtype=np.uint8)).text == ""


class TestContourDetector:
    def test_locates_a_plate_in_a_scene(self) -> None:
        detector = ContourPlateDetector(PlateDetectorConfig(engine="contour", confidence=0.2))
        found = detector.detect(scene_with_plate())
        assert found, "classical detector found no plate region"

        # The best candidate should overlap where the plate was actually pasted.
        best = max(found, key=lambda f: f[1])[0]
        assert 100 < best.cx < 400
        assert 100 < best.cy < 200

    def test_finds_nothing_in_flat_noise(self) -> None:
        detector = ContourPlateDetector(PlateDetectorConfig(engine="contour", confidence=0.6))
        flat = np.full((200, 300, 3), 120, dtype=np.uint8)
        assert detector.detect(flat) == []


def test_detect_condition_read_consensus(ocr_engine) -> None:
    """The whole plate path, end to end, over several simulated frames.

    Each 'frame' is the same plate at a different quality, exactly as a vehicle
    approaching a camera would look. Consensus must land on the true plate.
    """
    truth = "GJ03AB1234"
    detector = ContourPlateDetector(PlateDetectorConfig(engine="contour", confidence=0.2))
    reads: list[PlateRead] = []

    for index, scale in enumerate((0.5, 0.7, 1.0, 1.3)):
        scene = scene_with_plate(truth)
        if scale != 1.0:
            scene = cv2.resize(scene, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

        found = detector.detect(scene)
        if not found:
            continue
        bbox = max(found, key=lambda f: f[1])[0]
        x1, y1, x2, y2 = bbox.as_int()
        crop = scene[y1:y2, x1:x2]
        if crop.size == 0:
            continue

        quality = measure(crop)
        for _, prepared in build_variants(crop, PreprocessConfig(variants=["upscaled"])):
            result = ocr_engine.read(prepared)
            text = normalise(result.text)
            if len(text) < 6:
                continue
            reads.append(
                PlateRead(
                    text_raw=result.text, text=text, ocr_confidence=result.confidence,
                    frame_index=index, t_s=index / 25.0, track_id=1, engine="rapidocr",
                    quality=CropQuality(
                        sharpness=quality.sharpness, brightness=quality.brightness,
                        contrast=quality.contrast, width=quality.width, height=quality.height,
                    ),
                )
            )

    assert reads, "no plate was read from any simulated frame"
    outcome = consensus(reads, ConsensusConfig())
    assert outcome.text == truth, f"consensus produced {outcome.text!r} from {[r.text for r in reads]}"
    assert outcome.grammar_valid
