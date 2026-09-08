"""Crop quality measurement.

Two reads of the same vehicle can disagree, and when they do we need a reason to
prefer one. OCR confidence alone is not that reason — recognition engines are
routinely confident about hallucinated text on a 14-pixel-tall blur. Measuring
the crop itself gives an independent signal, and the product of the two is what
weights a read's vote in consensus.
"""

from __future__ import annotations

import cv2
import numpy as np

from ailab.types import CropQuality


def measure(crop: np.ndarray) -> CropQuality:
    """Sharpness, exposure and size of a plate crop."""
    if crop is None or crop.size == 0:
        return CropQuality(sharpness=0.0, brightness=0.0, contrast=0.0, width=0, height=0)

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop

    # Variance of the Laplacian: the standard focus measure. High variance means
    # strong second derivatives, i.e. crisp edges.
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    return CropQuality(
        sharpness=sharpness,
        brightness=float(gray.mean()),
        contrast=float(gray.std()),
        width=int(gray.shape[1]),
        height=int(gray.shape[0]),
    )


def measure_exposure(image: np.ndarray, width: int, height: int, sharpness: float) -> CropQuality:
    """Brightness and contrast only, reusing a sharpness measured elsewhere.

    The per-variant path needs exposure statistics but deliberately keeps the
    *original* crop's sharpness, because interpolation inflates a Laplacian and
    would let an upscaled blur vote as though it were sharp. Computing that
    Laplacian only to throw it away is pure cost.
    """
    if image is None or image.size == 0:
        return CropQuality(sharpness=sharpness, brightness=0.0, contrast=0.0,
                           width=width, height=height)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    mean, stddev = cv2.meanStdDev(gray)
    return CropQuality(
        sharpness=sharpness,
        brightness=float(mean[0][0]),
        contrast=float(stddev[0][0]),
        width=width,
        height=height,
    )


def is_legible(quality: CropQuality, min_width: int = 25, min_sharpness: float = 8.0) -> bool:
    """Cheap gate before spending OCR time on a hopeless crop.

    Deliberately permissive: it is better to run OCR and record a bad read
    (which becomes a training example) than to silently discard a crop and
    leave no evidence that the pipeline saw anything at all.
    """
    return quality.width >= min_width and quality.sharpness >= min_sharpness
