"""Accuracy metrics and calibration."""

from __future__ import annotations

import pytest

from ailab.evaluate.groundtruth import GroundTruth, evaluate_predictions, summarise
from ailab.evaluate.metrics import (
    calibration_table,
    character_error_rate,
    compare,
    error_analysis,
    levenshtein,
    threshold_sweep,
)


class TestEditDistance:
    @pytest.mark.parametrize(
        "a,b,expected",
        [
            ("GJ03AB1234", "GJ03AB1234", 0),
            ("GJ03AB1234", "GJ03AB1284", 1),
            ("GJ03AB1234", "GJ03AB123", 1),
            ("", "GJ03", 4),
            ("ABC", "", 3),
        ],
    )
    def test_levenshtein(self, a: str, b: str, expected: int) -> None:
        assert levenshtein(a, b) == expected

    def test_cer_is_normalised_by_truth_length(self) -> None:
        assert character_error_rate("GJ03AB1284", "GJ03AB1234") == pytest.approx(0.1)
        assert character_error_rate("", "GJ03AB1234") == pytest.approx(1.0)


class TestCalibration:
    def test_detects_overconfidence(self) -> None:
        """A pipeline that is 90% sure and 50% right must show a positive gap.

        This is the whole reason the table exists — the gap is what stops a
        raw confidence being used as a threshold.
        """
        comparisons = [
            compare("GJ03AB1234", "GJ03AB1234", 0.92, i) for i in range(5)
        ] + [
            compare("GJ03AB1284", "GJ03AB1234", 0.91, i) for i in range(5, 10)
        ]
        rows = [r for r in calibration_table(comparisons) if r["count"]]
        bucket = next(r for r in rows if r["bucket"] == "0.9-1.0")
        assert bucket["count"] == 10
        assert bucket["measured_accuracy"] == pytest.approx(0.5)
        assert bucket["calibration_gap"] > 0.35

    def test_well_calibrated_shows_no_gap(self) -> None:
        comparisons = [compare("GJ03AB1234", "GJ03AB1234", 0.95, i) for i in range(10)]
        bucket = next(r for r in calibration_table(comparisons) if r["bucket"] == "0.9-1.0")
        assert abs(bucket["calibration_gap"]) < 0.1


def test_threshold_sweep_trades_precision_for_recall() -> None:
    comparisons = [compare("GJ03AB1234", "GJ03AB1234", 0.95, 1),
                   compare("GJ03AB1284", "GJ03AB1234", 0.30, 2)]
    rows = threshold_sweep(comparisons, steps=10)
    low = next(r for r in rows if r["threshold"] == 0.0)
    high = next(r for r in rows if r["threshold"] == 0.9)
    assert low["emitted"] == 2 and low["precision"] == pytest.approx(0.5)
    assert high["emitted"] == 1 and high["precision"] == pytest.approx(1.0)


def test_error_analysis_finds_the_dominant_substitution() -> None:
    comparisons = [compare("GJ03AB1284", "GJ03AB1234", 0.8, i) for i in range(4)]
    analysis = error_analysis(comparisons)
    assert analysis["top_substitutions"]["3->8"] == 4
    assert analysis["errors_by_position"]["8"] == 4


class TestMatching:
    def test_track_keyed_ground_truth_joins_exactly(self) -> None:
        truth = GroundTruth(
            plates=["GJ03AB1234", "MH12DE1433"],
            by_track={1: "GJ03AB1234", 2: "MH12DE1433"},
            by_frame={}, source_path="test",
        )
        comparisons = evaluate_predictions(
            [(1, "GJ03AB1234", 0.9), (2, "MH12DE1499", 0.7)], truth
        )
        summary = summarise(comparisons)
        assert summary["correct"] == 1
        assert summary["wrong"] == 1
        assert summary["missed"] == 0

    def test_missed_plates_are_counted(self) -> None:
        """A plate the pipeline never read is the most important failure."""
        truth = GroundTruth(
            plates=["GJ03AB1234", "MH12DE1433"],
            by_track={}, by_frame={}, source_path="test",
        )
        comparisons = evaluate_predictions([(1, "GJ03AB1234", 0.9)], truth)
        summary = summarise(comparisons)
        assert summary["correct"] == 1
        assert summary["missed"] == 1
        assert summary["recall"] == pytest.approx(0.5)

    def test_unrelated_prediction_is_spurious_not_close(self) -> None:
        """Matching must refuse to pair strings that are simply different.

        Pairing them would let a hallucinated plate score a low CER and hide
        the failure.
        """
        truth = GroundTruth(
            plates=["GJ03AB1234"], by_track={}, by_frame={}, source_path="test"
        )
        comparisons = evaluate_predictions([(1, "MH99ZZ0000", 0.9)], truth)
        summary = summarise(comparisons)
        assert summary["spurious"] == 1
        assert summary["missed"] == 1
        assert summary["correct"] == 0
