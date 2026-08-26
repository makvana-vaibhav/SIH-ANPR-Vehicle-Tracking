"""Box maths — letterboxing, NMS, YOLO decoding.

This is code that fails silently and plausibly, so it is tested against
hand-computed values rather than against itself.
"""

from __future__ import annotations

import numpy as np
import pytest

from ailab.detect.postprocess import (
    batched_nms,
    decode_yolo_output,
    letterbox,
    nms,
    undo_letterbox,
    xywh_to_xyxy,
)


class TestLetterbox:
    def test_pads_to_square_preserving_aspect(self) -> None:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        padded, scale, (left, top) = letterbox(image, 640)
        assert padded.shape == (640, 640, 3)
        assert scale == pytest.approx(1.0)
        assert top == 80 and left == 0

    def test_round_trip_recovers_original_coordinates(self) -> None:
        """A box mapped forward then back must land where it started.

        If this drifts, every detection is subtly misplaced and nothing else
        in the pipeline will tell you.
        """
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        _, scale, pad = letterbox(image, 640)

        original = np.array([[100.0, 200.0, 340.0, 460.0]])
        in_letterbox = original * scale + np.array([pad[0], pad[1], pad[0], pad[1]])
        recovered = undo_letterbox(in_letterbox, scale, pad, 1280, 720)
        np.testing.assert_allclose(recovered, original, atol=1e-6)

    def test_rejects_empty_image(self) -> None:
        with pytest.raises(ValueError):
            letterbox(np.zeros((0, 0, 3), dtype=np.uint8), 640)


class TestNms:
    def test_suppresses_overlapping_boxes(self) -> None:
        boxes = np.array([[0, 0, 100, 100], [5, 5, 105, 105], [500, 500, 600, 600]], float)
        scores = np.array([0.9, 0.8, 0.7])
        keep = nms(boxes, scores, 0.5)
        assert sorted(keep.tolist()) == [0, 2]

    def test_keeps_distinct_boxes(self) -> None:
        boxes = np.array([[0, 0, 50, 50], [200, 200, 250, 250]], float)
        keep = nms(boxes, np.array([0.9, 0.8]), 0.5)
        assert len(keep) == 2

    def test_empty_input(self) -> None:
        assert nms(np.empty((0, 4)), np.empty((0,)), 0.5).size == 0

    def test_class_aware_nms_keeps_overlapping_classes(self) -> None:
        """A motorcycle inside a car's box must not be suppressed by it."""
        boxes = np.array([[0, 0, 100, 100], [2, 2, 98, 98]], float)
        scores = np.array([0.9, 0.85])
        keep = batched_nms(boxes, scores, np.array([2, 3]), 0.5)
        assert len(keep) == 2

        # Same boxes, same class: one must go.
        keep_same = batched_nms(boxes, scores, np.array([2, 2]), 0.5)
        assert len(keep_same) == 1


class TestDecode:
    def test_xywh_conversion(self) -> None:
        result = xywh_to_xyxy(np.array([[50.0, 50.0, 20.0, 10.0]]))
        np.testing.assert_allclose(result, [[40.0, 45.0, 60.0, 55.0]])

    def test_decodes_ultralytics_layout(self) -> None:
        """(1, 4+nc, anchors) with class scores already activated."""
        anchors = 3
        output = np.zeros((1, 84, anchors), dtype=np.float32)
        output[0, :4, 0] = [100.0, 100.0, 40.0, 20.0]
        output[0, 4 + 2, 0] = 0.9      # class 2 = car
        output[0, :4, 1] = [300.0, 300.0, 60.0, 30.0]
        output[0, 4 + 5, 1] = 0.4      # class 5 = bus
        # anchor 2 stays at zero and must be filtered out

        boxes, scores, class_ids = decode_yolo_output(output, conf_threshold=0.3)
        assert len(boxes) == 2
        assert class_ids.tolist() == [2, 5]
        np.testing.assert_allclose(scores, [0.9, 0.4], atol=1e-6)
        np.testing.assert_allclose(boxes[0], [80.0, 90.0, 120.0, 110.0])

    def test_single_class_model(self) -> None:
        """A plate model emits (1, 5, anchors) — one class, no argmax needed."""
        output = np.zeros((1, 5, 2), dtype=np.float32)
        output[0, :4, 0] = [50.0, 25.0, 40.0, 12.0]
        output[0, 4, 0] = 0.77
        boxes, scores, class_ids = decode_yolo_output(output, 0.25, num_classes=1)
        assert len(boxes) == 1
        assert class_ids.tolist() == [0]
        assert scores[0] == pytest.approx(0.77)

    def test_threshold_filters_everything(self) -> None:
        output = np.zeros((1, 84, 5), dtype=np.float32)
        boxes, scores, class_ids = decode_yolo_output(output, 0.5)
        assert boxes.shape == (0, 4)
        assert scores.size == 0 and class_ids.size == 0
