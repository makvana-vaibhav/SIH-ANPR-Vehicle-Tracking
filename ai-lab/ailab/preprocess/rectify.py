"""Plate-crop conditioning.

A plate photographed from a roadside camera is small, off-axis and often
underexposed. Each named variant here is a different attempt to fix that, and
the pipeline can run several and keep whichever produced the best OCR result.
Which variant won is recorded on every read, so after a few hundred vehicles the
run report answers "does perspective correction actually help our footage?" with
counts instead of intuition.

Variants
--------
raw         the crop exactly as the detector cut it — the control
upscaled    pad, CLAHE, upscale to a comfortable OCR height
rectified   find the plate quadrilateral and warp it flat, then as upscaled
binarised   upscaled, then adaptive threshold — helps engines trained on scans
sharpened   upscaled, then unsharp mask — recovers slightly soft crops
"""

from __future__ import annotations

import cv2
import numpy as np

from ailab.config import PreprocessConfig

VARIANT_NAMES = ("raw", "upscaled", "rectified", "binarised", "sharpened")


def _pad(crop: np.ndarray, ratio: float) -> np.ndarray:
    """Add quiet space around the plate.

    Text recognisers are trained on crops with margin; a glyph flush against the
    image border is frequently dropped or misread as a different character.
    """
    if ratio <= 0:
        return crop
    h, w = crop.shape[:2]
    py, px = max(2, int(h * ratio)), max(2, int(w * ratio))
    return cv2.copyMakeBorder(crop, py, py, px, px, cv2.BORDER_REPLICATE)


def _upscale(image: np.ndarray, target_height: int, max_factor: float) -> np.ndarray:
    """Scale toward a height OCR is comfortable with, without over-magnifying.

    Beyond roughly 4x, interpolation is inventing detail rather than revealing
    it, and the OCR engine reads the invention.
    """
    h, w = image.shape[:2]
    if h == 0:
        return image
    factor = min(max_factor, target_height / h)
    if factor <= 1.01:
        return image
    return cv2.resize(
        image, (int(round(w * factor)), int(round(h * factor))), interpolation=cv2.INTER_CUBIC
    )


def _enhance(image: np.ndarray, config: PreprocessConfig) -> np.ndarray:
    """CLAHE on the luminance channel only, so colour is left alone.

    Plates are frequently backlit or in shade; local histogram equalisation
    recovers character contrast that a global stretch would not.
    """
    clahe = cv2.createCLAHE(
        clipLimit=config.clahe_clip, tileGridSize=(config.clahe_grid, config.clahe_grid)
    )
    if image.ndim == 2:
        return clahe.apply(image)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def find_plate_quad(crop: np.ndarray) -> np.ndarray | None:
    """Locate the plate's four corners inside a crop, if they are findable.

    Uses the largest plausible quadrilateral contour. Returns corners ordered
    top-left, top-right, bottom-right, bottom-left, or None when the plate
    border is not clean enough to trust — in which case the caller should fall
    back to the axis-aligned crop rather than warp to a wrong quad, which would
    be worse than not warping at all.
    """
    if crop is None or crop.size == 0:
        return None
    h, w = crop.shape[:2]
    if h < 12 or w < 24:
        return None

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    gray = cv2.bilateralFilter(gray, 7, 60, 60)
    edges = cv2.Canny(gray, 40, 140)
    edges = cv2.dilate(edges, np.ones((2, 2), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    crop_area = float(h * w)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:6]:
        area = cv2.contourArea(contour)
        # The quad must cover most of the crop — the detector already framed
        # the plate, so a small quad is some feature inside it, not the border.
        if area < 0.30 * crop_area:
            continue
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.03 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        return _order_corners(approx.reshape(4, 2).astype(np.float32))
    return None


def _order_corners(points: np.ndarray) -> np.ndarray:
    """Order four points TL, TR, BR, BL.

    Sum of coordinates is smallest at top-left and largest at bottom-right;
    the difference (y - x) separates the other two. Robust to rotation up to
    the point where "top" stops meaning anything.
    """
    ordered = np.zeros((4, 2), dtype=np.float32)
    s = points.sum(axis=1)
    d = np.diff(points, axis=1).ravel()
    ordered[0] = points[np.argmin(s)]
    ordered[2] = points[np.argmax(s)]
    ordered[1] = points[np.argmin(d)]
    ordered[3] = points[np.argmax(d)]
    return ordered


def four_point_warp(crop: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Warp a quadrilateral to a flat rectangle."""
    tl, tr, br, bl = quad
    width = int(round(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))))
    height = int(round(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))))
    if width < 16 or height < 8:
        return crop

    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
    )
    matrix = cv2.getPerspectiveTransform(quad, destination)
    return cv2.warpPerspective(crop, matrix, (width, height), flags=cv2.INTER_CUBIC)


def build_variants(
    crop: np.ndarray, config: PreprocessConfig
) -> list[tuple[str, np.ndarray]]:
    """Produce every enabled preprocessing variant of one plate crop."""
    if crop is None or crop.size == 0:
        return []

    requested = [v for v in config.variants if v in VARIANT_NAMES]
    unknown = set(config.variants) - set(VARIANT_NAMES)
    if unknown:
        raise ValueError(
            f"unknown preprocess variants {sorted(unknown)}; available: {list(VARIANT_NAMES)}"
        )

    out: list[tuple[str, np.ndarray]] = []
    padded = _pad(crop, config.pad_ratio)

    # `upscaled` is the shared base for the variants that build on it.
    base: np.ndarray | None = None
    if requested and set(requested) - {"raw", "rectified"}:
        base = _upscale(_enhance(padded, config), config.target_height, config.max_upscale)

    for name in requested:
        if name == "raw":
            out.append(("raw", crop))

        elif name == "upscaled":
            assert base is not None
            out.append(("upscaled", base))

        elif name == "rectified":
            quad = find_plate_quad(padded)
            if quad is None:
                # No trustworthy border: fall back to the axis-aligned path
                # rather than warping to a guess. Named distinctly so the
                # report can show how often rectification actually applied.
                fallback = _upscale(
                    _enhance(padded, config), config.target_height, config.max_upscale
                )
                out.append(("rectified_fallback", fallback))
            else:
                warped = four_point_warp(padded, quad)
                out.append(
                    (
                        "rectified",
                        _upscale(
                            _enhance(warped, config), config.target_height, config.max_upscale
                        ),
                    )
                )

        elif name == "binarised":
            assert base is not None
            gray = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY) if base.ndim == 3 else base
            binary = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 25, 12
            )
            out.append(("binarised", cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)))

        elif name == "sharpened":
            assert base is not None
            blurred = cv2.GaussianBlur(base, (0, 0), 3.0)
            out.append(("sharpened", cv2.addWeighted(base, 1.6, blurred, -0.6, 0)))

    return out
