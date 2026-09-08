"""Selective inference — when a vehicle is searched and when a crop is read.

These gates exist to remove redundant work without removing evidence, so the
tests check both halves: that repeated identical views are skipped, and that
genuinely new or better views still get through.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from ailab.config import CropGateConfig, PlateScheduleConfig
from ailab.plate.scheduler import CropGate, PlateScheduler, signature, signature_distance
from ailab.types import BBox, CropQuality


def box(x: float, y: float, w: float = 200, h: float = 150) -> BBox:
    return BBox(x, y, x + w, y + h)


def quality(sharpness: float = 200.0, width: int = 140) -> CropQuality:
    return CropQuality(
        sharpness=sharpness, brightness=128.0, contrast=45.0, width=width, height=40
    )


class TestPlateScheduler:
    def test_first_look_always_searches(self) -> None:
        scheduler = PlateScheduler(PlateScheduleConfig())
        assert scheduler.should_search(1, box(100, 100), 0, converged=False)

    def test_consecutive_frames_are_skipped(self) -> None:
        """Two adjacent frames cannot show a materially different plate."""
        scheduler = PlateScheduler(PlateScheduleConfig(min_interval=3))
        assert scheduler.should_search(1, box(100, 100), 0, converged=False)
        scheduler.record(1, box(100, 100), 0, found=True)
        assert not scheduler.should_search(1, box(102, 100), 1, converged=False)
        assert not scheduler.should_search(1, box(104, 100), 2, converged=False)

    def test_a_materially_closer_vehicle_is_searched_again(self) -> None:
        scheduler = PlateScheduler(PlateScheduleConfig(min_interval=2, min_scale_change=0.18))
        scheduler.should_search(1, box(100, 100, 200, 150), 0, converged=False)
        scheduler.record(1, box(100, 100, 200, 150), 0, found=True)
        # 40% larger: a genuinely better look.
        assert scheduler.should_search(1, box(100, 100, 240, 178), 5, converged=False)
        assert scheduler.reasons["search_changed_view"] == 1

    def test_a_vehicle_that_moved_across_frame_is_searched_again(self) -> None:
        scheduler = PlateScheduler(PlateScheduleConfig(min_interval=2, min_move_fraction=0.30))
        scheduler.should_search(1, box(100, 100), 0, converged=False)
        scheduler.record(1, box(100, 100), 0, found=True)
        assert scheduler.should_search(1, box(300, 100), 5, converged=False)

    def test_a_stationary_vehicle_is_still_revisited(self) -> None:
        """Nothing changed, but abandoning a parked vehicle entirely is wrong."""
        config = PlateScheduleConfig(min_interval=2, max_interval=10)
        scheduler = PlateScheduler(config)
        scheduler.should_search(1, box(100, 100), 0, converged=False)
        scheduler.record(1, box(100, 100), 0, found=True)
        for frame in range(1, 10):
            scheduler.should_search(1, box(100, 100), frame, converged=False)
        assert scheduler.should_search(1, box(100, 100), 10, converged=False)
        assert scheduler.reasons["search_periodic"] >= 1

    def test_converged_vehicles_stop_being_searched(self) -> None:
        scheduler = PlateScheduler(PlateScheduleConfig())
        assert not scheduler.should_search(1, box(100, 100), 50, converged=True)

    def test_vehicles_too_small_to_carry_a_plate_are_skipped(self) -> None:
        scheduler = PlateScheduler(PlateScheduleConfig(min_vehicle_width=90))
        assert not scheduler.should_search(1, box(0, 0, 40, 30), 0, converged=False)
        assert scheduler.reasons["skip_too_small"] == 1

    def test_repeated_failures_back_off(self) -> None:
        """A vehicle facing away should not keep competing with readable ones."""
        config = PlateScheduleConfig(min_interval=2, max_interval=6, miss_backoff=True)
        scheduler = PlateScheduler(config)
        for frame in range(0, 40, 4):
            if scheduler.should_search(1, box(100, 100), frame, converged=False):
                scheduler.record(1, box(100, 100), frame, found=False)
        searches = scheduler.states[1].searches
        # Without backoff a static vehicle would be searched every max_interval.
        assert searches < 6, f"backoff did not reduce searches (got {searches})"

    def test_disabled_scheduler_always_searches(self) -> None:
        scheduler = PlateScheduler(PlateScheduleConfig(enabled=False))
        for frame in range(5):
            assert scheduler.should_search(1, box(100, 100), frame, converged=False)

    def test_stats_account_for_every_decision(self) -> None:
        scheduler = PlateScheduler(PlateScheduleConfig(min_interval=3))
        for frame in range(6):
            if scheduler.should_search(1, box(100, 100), frame, converged=False):
                scheduler.record(1, box(100, 100), frame, found=True)
        stats = scheduler.stats()
        assert stats["considered"] == stats["searched"] + stats["skipped"] == 6


def plate_crop(text: str = "GJ03AB1234", blur: float = 0.0) -> np.ndarray:
    image = np.full((48, 200, 3), 240, dtype=np.uint8)
    cv2.putText(image, text, (8, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (10, 10, 10), 2, cv2.LINE_AA)
    if blur:
        image = cv2.GaussianBlur(image, (0, 0), blur)
    return image


class TestSignature:
    def test_identical_crops_have_zero_distance(self) -> None:
        crop = plate_crop()
        assert signature_distance(signature(crop), signature(crop.copy())) == 0.0

    def test_different_plates_differ(self) -> None:
        a, b = signature(plate_crop("GJ03AB1234")), signature(plate_crop("MH12ZZ9999"))
        assert signature_distance(a, b) > 0.15

    def test_recompression_is_not_a_new_view(self) -> None:
        """The hash must ignore encoding noise, or it would defeat its purpose."""
        crop = plate_crop()
        _, buffer = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        recompressed = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
        assert signature_distance(signature(crop), signature(recompressed)) < 0.10


class TestCropGate:
    def test_first_crop_is_always_read(self) -> None:
        gate = CropGate(CropGateConfig())
        allowed, _ = gate.should_read(1, plate_crop(), quality())
        assert allowed

    def test_identical_repeat_is_skipped(self) -> None:
        gate = CropGate(CropGateConfig())
        crop = plate_crop()
        _, sig = gate.should_read(1, crop, quality())
        gate.record(1, quality(), confidence=0.95, readable=True, crop_signature=sig)
        allowed, _ = gate.should_read(1, crop.copy(), quality())
        assert not allowed
        assert gate.reasons["skip_duplicate_view"] == 1

    def test_a_sharper_crop_gets_read(self) -> None:
        gate = CropGate(CropGateConfig(min_sharpness_gain=0.25))
        crop = plate_crop()
        _, sig = gate.should_read(1, crop, quality(sharpness=100.0))
        gate.record(1, quality(sharpness=100.0), 0.95, readable=True, crop_signature=sig)
        allowed, _ = gate.should_read(1, crop.copy(), quality(sharpness=200.0))
        assert allowed
        assert gate.reasons["read_sharper"] == 1

    def test_a_larger_crop_gets_read(self) -> None:
        gate = CropGate(CropGateConfig(min_width_gain=0.20))
        crop = plate_crop()
        _, sig = gate.should_read(1, crop, quality(width=100))
        gate.record(1, quality(width=100), 0.95, readable=True, crop_signature=sig)
        allowed, _ = gate.should_read(1, crop.copy(), quality(width=140))
        assert allowed

    def test_weak_results_keep_being_retried(self) -> None:
        """A poor read is exactly the case more evidence might fix."""
        gate = CropGate(CropGateConfig(low_confidence=0.75))
        crop = plate_crop()
        _, sig = gate.should_read(1, crop, quality())
        gate.record(1, quality(), confidence=0.40, readable=True, crop_signature=sig)
        allowed, _ = gate.should_read(1, crop.copy(), quality())
        assert allowed
        assert gate.reasons["read_low_confidence"] == 1

    def test_an_unreadable_vehicle_keeps_getting_attempts(self) -> None:
        gate = CropGate(CropGateConfig())
        crop = plate_crop()
        _, sig = gate.should_read(1, crop, quality())
        gate.record(1, quality(), confidence=0.0, readable=False, crop_signature=sig)
        allowed, _ = gate.should_read(1, crop.copy(), quality())
        assert allowed

    def test_a_different_view_gets_read(self) -> None:
        gate = CropGate(CropGateConfig())
        _, sig = gate.should_read(1, plate_crop("GJ03AB1234"), quality())
        gate.record(1, quality(), 0.95, readable=True, crop_signature=sig)
        allowed, _ = gate.should_read(1, plate_crop("MH12ZZ9999"), quality())
        assert allowed

    def test_tracks_do_not_interfere(self) -> None:
        gate = CropGate(CropGateConfig())
        crop = plate_crop()
        _, sig = gate.should_read(1, crop, quality())
        gate.record(1, quality(), 0.95, readable=True, crop_signature=sig)
        allowed, _ = gate.should_read(2, crop.copy(), quality())
        assert allowed, "one vehicle's history must not gate another's"

    def test_disabled_gate_reads_everything(self) -> None:
        gate = CropGate(CropGateConfig(enabled=False))
        crop = plate_crop()
        for _ in range(4):
            allowed, _ = gate.should_read(1, crop, quality())
            assert allowed


@pytest.mark.parametrize("enabled", [True, False])
def test_gate_stats_balance(enabled: bool) -> None:
    gate = CropGate(CropGateConfig(enabled=enabled))
    crop = plate_crop()
    for _ in range(5):
        allowed, sig = gate.should_read(1, crop, quality())
        if allowed:
            gate.record(1, quality(), 0.95, readable=True, crop_signature=sig)
    stats = gate.stats()
    if enabled:
        assert stats["considered"] == stats["read"] + stats["skipped"]
