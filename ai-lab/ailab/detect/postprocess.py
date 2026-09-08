"""Letterboxing, NMS and coordinate mapping for YOLO-family models.

Kept separate from the inference wrapper so it can be unit-tested without a
model file — box maths is exactly the kind of code that fails silently and
produces plausible-looking garbage.
"""

from __future__ import annotations

import cv2
import numpy as np


def letterbox(
    image: np.ndarray, size: int, color: tuple[int, int, int] = (114, 114, 114)
) -> tuple[np.ndarray, float, tuple[float, float]]:
    """Resize preserving aspect ratio and pad to a square.

    Returns the padded image, the scale factor applied, and the (left, top)
    padding — everything needed to map a detection back to source pixels.
    """
    h, w = image.shape[:2]
    if h == 0 or w == 0:
        raise ValueError("cannot letterbox an empty image")

    scale = min(size / h, size / w)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    # An upscaling interpolation for growth, area-averaging for shrink: area
    # is markedly better at preserving small distant plates.
    interp = cv2.INTER_LINEAR if scale > 1 else cv2.INTER_AREA
    resized = cv2.resize(image, (new_w, new_h), interpolation=interp)

    pad_w, pad_h = size - new_w, size - new_h
    left, top = pad_w // 2, pad_h // 2
    out = cv2.copyMakeBorder(
        resized, top, pad_h - top, left, pad_w - left, cv2.BORDER_CONSTANT, value=color
    )
    return out, scale, (float(left), float(top))


def undo_letterbox(
    boxes: np.ndarray, scale: float, pad: tuple[float, float], width: int, height: int
) -> np.ndarray:
    """Map xyxy boxes from letterboxed space back to source pixels."""
    if boxes.size == 0:
        return boxes
    out = boxes.copy()
    out[:, [0, 2]] -= pad[0]
    out[:, [1, 3]] -= pad[1]
    out /= scale
    np.clip(out[:, [0, 2]], 0, width, out=out[:, [0, 2]])
    np.clip(out[:, [1, 3]], 0, height, out=out[:, [1, 3]])
    return out


def xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    """(cx, cy, w, h) → (x1, y1, x2, y2)."""
    out = np.empty_like(boxes)
    half_w = boxes[:, 2] / 2.0
    half_h = boxes[:, 3] / 2.0
    out[:, 0] = boxes[:, 0] - half_w
    out[:, 1] = boxes[:, 1] - half_h
    out[:, 2] = boxes[:, 0] + half_w
    out[:, 3] = boxes[:, 1] + half_h
    return out


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> np.ndarray:
    """Greedy non-maximum suppression. Returns kept indices, best first."""
    if boxes.size == 0:
        return np.empty((0,), dtype=np.int64)

    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]

    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        union = areas[i] + areas[rest] - inter
        iou = np.where(union > 0, inter / union, 0.0)
        order = rest[iou <= iou_threshold]

    return np.asarray(keep, dtype=np.int64)


def batched_nms(
    boxes: np.ndarray, scores: np.ndarray, class_ids: np.ndarray, iou_threshold: float
) -> np.ndarray:
    """Class-aware NMS.

    A motorcycle overlapping a car should not suppress it. Implemented by
    offsetting each class into its own coordinate region so a single NMS pass
    can never compare boxes across classes.
    """
    if boxes.size == 0:
        return np.empty((0,), dtype=np.int64)
    max_coord = float(boxes.max()) if boxes.size else 0.0
    offsets = class_ids.astype(np.float64) * (max_coord + 1.0)
    shifted = boxes + offsets[:, None]
    return nms(shifted, scores, iou_threshold)


def decode_yolo_output(
    output: np.ndarray,
    conf_threshold: float,
    num_classes: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode an Ultralytics export of shape (1, 4+nc, anchors).

    Ultralytics exports without NMS emit raw box predictions in letterboxed
    pixel coordinates with per-class scores already activated. Returns
    (boxes_xyxy, scores, class_ids) above the confidence threshold.
    """
    arr = np.asarray(output)
    if arr.ndim == 3:
        arr = arr[0]

    # Ultralytics emits (4+nc, anchors); we want (anchors, 4+nc). Decide by
    # matching the known feature count rather than by assuming anchors always
    # outnumber features — that assumption holds for a 640px frame with 8400
    # anchors and quietly inverts the whole tensor for a small input.
    if num_classes is not None:
        expected = 4 + num_classes
        if arr.shape[0] == expected:
            arr = arr.transpose()
        elif arr.shape[1] != expected:
            raise ValueError(
                f"output shape {arr.shape} matches neither (anchors, {expected}) "
                f"nor ({expected}, anchors) for a {num_classes}-class model"
            )
    else:
        # No declared class count: the feature axis is the smaller one, since a
        # real export has far more anchors than features. Guard the degenerate
        # case where that reading would leave fewer than one class.
        if (arr.shape[0] < arr.shape[1] and arr.shape[0] >= 5) or arr.shape[1] < 5 <= arr.shape[0]:
            arr = arr.transpose()

    arr = arr.astype(np.float32, copy=False)
    nc = num_classes if num_classes is not None else arr.shape[1] - 4
    if nc < 1:
        raise ValueError(
            f"decoded {nc} classes from output shape {arr.shape}; the tensor "
            "orientation could not be determined"
        )
    boxes_xywh = arr[:, :4]
    class_scores = arr[:, 4 : 4 + nc]

    if nc == 1:
        scores = class_scores[:, 0]
        class_ids = np.zeros(scores.shape, dtype=np.int64)
    else:
        class_ids = class_scores.argmax(axis=1)
        scores = class_scores[np.arange(class_scores.shape[0]), class_ids]

    mask = scores >= conf_threshold
    if not np.any(mask):
        return (
            np.empty((0, 4), dtype=np.float32),
            np.empty((0,), dtype=np.float32),
            np.empty((0,), dtype=np.int64),
        )
    return xywh_to_xyxy(boxes_xywh[mask]), scores[mask], class_ids[mask].astype(np.int64)
