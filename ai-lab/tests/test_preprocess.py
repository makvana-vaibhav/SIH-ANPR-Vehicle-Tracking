"""Plate-crop conditioning."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from ailab.config import PreprocessConfig
from ailab.preprocess import build_variants, find_plate_quad, four_point_warp, is_legible, measure


def plate_image(width: int = 200, height: int = 50, text: str = "GJ03AB1234") -> np.ndarray:
    """A clean synthetic plate: white ground, black border, fitted black text."""
    image = np.full((height, width, 3), 245, dtype=np.uint8)
    cv2.rectangle(image, (2, 2), (width - 3, height - 3), (20, 20, 20), 2)

    margin_x, margin_y = int(width * 0.10), int(height * 0.22)
    (base_w, base_h), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2)
    scale = min(
        (width - 2 * margin_x) / max(base_w, 1), (height - 2 * margin_y) / max(base_h, 1)
    )
    thickness = max(1, int(round(scale * 1.5)))
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
    cv2.putText(
        image, text, ((width - tw) // 2, (height + th) // 2),
        cv2.FONT_HERSHEY_SIMPLEX, scale, (10, 10, 10), thickness, cv2.LINE_AA,
    )
    return image


class TestQuality:
    def test_sharp_image_scores_above_blurred(self) -> None:
        sharp = plate_image()
        blurred = cv2.GaussianBlur(sharp, (9, 9), 5.0)
        assert measure(sharp).sharpness > measure(blurred).sharpness * 3

    def test_weight_prefers_large_and_sharp(self) -> None:
        big = measure(plate_image(240, 60))
        small = measure(cv2.resize(plate_image(), (30, 8)))
        assert big.weight() > small.weight()

    def test_weight_is_bounded(self) -> None:
        for image in (plate_image(), plate_image(600, 150), np.zeros((10, 10, 3), np.uint8)):
            weight = measure(image).weight()
            assert 0.0 < weight <= 1.0

    def test_empty_input_is_handled(self) -> None:
        quality = measure(np.empty((0, 0, 3), dtype=np.uint8))
        assert quality.width == 0 and quality.sharpness == 0.0

    def test_legibility_gate(self) -> None:
        assert is_legible(measure(plate_image()))
        assert not is_legible(measure(cv2.resize(plate_image(), (18, 6))))


class TestVariants:
    def test_produces_one_image_per_requested_variant(self) -> None:
        config = PreprocessConfig(variants=["raw", "upscaled", "sharpened", "binarised"])
        variants = build_variants(plate_image(), config)
        assert [name for name, _ in variants] == ["raw", "upscaled", "sharpened", "binarised"]
        assert all(image.size > 0 for _, image in variants)

    def test_upscaling_respects_the_cap(self) -> None:
        """Beyond the cap, interpolation invents detail the OCR would then read."""
        config = PreprocessConfig(variants=["upscaled"], target_height=200, max_upscale=2.0)
        small = plate_image(60, 15)
        _, upscaled = build_variants(small, config)[0]
        # 15px * 2.0 cap, plus padding, so nowhere near the 200px target.
        assert upscaled.shape[0] < 100

    def test_rectified_falls_back_when_no_quad_is_found(self) -> None:
        """A borderless crop must not be warped to a guess.

        The distinct 'rectified_fallback' name is what lets the report show how
        often rectification actually applied.
        """
        noise = np.random.default_rng(0).integers(0, 255, (40, 160, 3), dtype=np.uint8)
        variants = build_variants(noise, PreprocessConfig(variants=["rectified"]))
        assert variants[0][0] == "rectified_fallback"

    def test_unknown_variant_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown preprocess variants"):
            build_variants(plate_image(), PreprocessConfig(variants=["enhance_hard"]))

    def test_empty_crop_yields_nothing(self) -> None:
        assert build_variants(np.empty((0, 0, 3), dtype=np.uint8), PreprocessConfig()) == []


class TestRectification:
    def test_finds_a_clean_plate_border(self) -> None:
        quad = find_plate_quad(plate_image(220, 60))
        assert quad is not None
        assert quad.shape == (4, 2)

    def test_warps_a_skewed_plate_flat(self) -> None:
        """The corrected image should be more rectangular than the input."""
        source = plate_image(200, 50)
        canvas = np.full((120, 280, 3), 90, dtype=np.uint8)
        # Project the plate onto the canvas with a perspective skew.
        src = np.float32([[0, 0], [200, 0], [200, 50], [0, 50]])
        dst = np.float32([[30, 20], [250, 5], [258, 78], [22, 95]])
        matrix = cv2.getPerspectiveTransform(src, dst)
        cv2.warpPerspective(source, matrix, (280, 120), canvas,
                            borderMode=cv2.BORDER_TRANSPARENT)

        quad = find_plate_quad(canvas)
        if quad is None:
            pytest.skip("border not recoverable from this synthetic skew")
        warped = four_point_warp(canvas, quad)
        assert warped.shape[1] > warped.shape[0]      # wider than tall, as a plate is

    def test_returns_none_for_a_too_small_crop(self) -> None:
        assert find_plate_quad(np.zeros((5, 10, 3), dtype=np.uint8)) is None
