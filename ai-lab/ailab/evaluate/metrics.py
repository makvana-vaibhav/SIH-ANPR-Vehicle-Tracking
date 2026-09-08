"""Accuracy metrics.

The distinction this module exists to enforce: a model's confidence is a claim,
and accuracy is a measurement. They are reported separately and never blended.
The calibration table is the bridge between them — it answers "when this
pipeline says it is 90% sure, how often is it actually right?", which is the
only sound basis for choosing an operating threshold.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any


def levenshtein(a: str, b: str) -> int:
    """Edit distance. Small strings, so the simple DP is more than fast enough."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(
                min(
                    previous[j] + 1,          # deletion
                    current[j - 1] + 1,       # insertion
                    previous[j - 1] + (char_a != char_b),  # substitution
                )
            )
        previous = current
    return previous[-1]


def character_error_rate(predicted: str, truth: str) -> float:
    """Edit distance normalised by ground-truth length.

    CER is reported alongside exact-match because they answer different
    questions. Exact match is what an ANPR system is judged on operationally —
    one wrong character is a wrong plate. CER tells you *how* wrong, which is
    what says whether a model is nearly there or hopeless.
    """
    if not truth:
        return 0.0 if not predicted else 1.0
    return levenshtein(predicted, truth) / len(truth)


@dataclass(slots=True)
class PlateComparison:
    """One predicted plate judged against its ground truth."""

    track_id: int | None
    predicted: str
    truth: str
    confidence: float
    exact: bool
    cer: float
    edit_distance: int
    status: str            # "correct" | "wrong" | "missed" | "spurious"
    wrong_positions: list[int] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id if self.track_id is not None else "",
            "predicted": self.predicted,
            "truth": self.truth,
            "status": self.status,
            "exact": self.exact,
            "cer": round(self.cer, 4),
            "edit_distance": self.edit_distance,
            "confidence": round(self.confidence, 4),
            "wrong_positions": " ".join(str(p) for p in self.wrong_positions),
        }


def compare(predicted: str, truth: str, confidence: float, track_id: int | None) -> PlateComparison:
    exact = predicted == truth
    distance = levenshtein(predicted, truth)
    positions = (
        [i for i, (p, t) in enumerate(zip(predicted, truth, strict=False)) if p != t]
        if len(predicted) == len(truth)
        else []
    )
    return PlateComparison(
        track_id=track_id,
        predicted=predicted,
        truth=truth,
        confidence=confidence,
        exact=exact,
        cer=character_error_rate(predicted, truth),
        edit_distance=distance,
        status="correct" if exact else "wrong",
        wrong_positions=positions,
    )


def calibration_table(
    comparisons: list[PlateComparison], buckets: int = 10
) -> list[dict[str, Any]]:
    """Reported confidence versus measured accuracy, bucketed.

    A well-calibrated pipeline produces a table where the 0.8-0.9 bucket is
    right about 85% of the time. A badly calibrated one — the common case for
    OCR — is right 55% of the time in that bucket, and the gap is precisely
    what stops you trusting a raw confidence as a threshold.
    """
    scored = [c for c in comparisons if c.status in ("correct", "wrong")]
    rows: list[dict[str, Any]] = []

    for index in range(buckets):
        low, high = index / buckets, (index + 1) / buckets
        in_bucket = [
            c for c in scored
            if low <= c.confidence < high or (index == buckets - 1 and c.confidence == 1.0)
        ]
        if not in_bucket:
            continue
        correct = sum(1 for c in in_bucket if c.exact)
        accuracy = correct / len(in_bucket)
        mean_confidence = sum(c.confidence for c in in_bucket) / len(in_bucket)
        rows.append(
            {
                "bucket": f"{low:.1f}-{high:.1f}",
                "count": len(in_bucket),
                "mean_confidence": round(mean_confidence, 4),
                "measured_accuracy": round(accuracy, 4),
                # Positive: overconfident. Negative: underconfident.
                "calibration_gap": round(mean_confidence - accuracy, 4),
            }
        )
    return rows


def threshold_sweep(
    comparisons: list[PlateComparison], steps: int = 20
) -> list[dict[str, Any]]:
    """Precision/recall as the confidence cut-off moves.

    This is how an operating threshold gets chosen: a control room that acts on
    every alert needs high precision; an investigator searching history after
    the fact would rather have recall. The table lets that be a decision instead
    of a default.
    """
    scored = [c for c in comparisons if c.status in ("correct", "wrong")]
    total_truth = len([c for c in comparisons if c.truth])
    rows: list[dict[str, Any]] = []

    for step in range(steps + 1):
        threshold = step / steps
        kept = [c for c in scored if c.confidence >= threshold]
        correct = sum(1 for c in kept if c.exact)
        precision = correct / len(kept) if kept else 0.0
        recall = correct / total_truth if total_truth else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        rows.append(
            {
                "threshold": round(threshold, 2),
                "emitted": len(kept),
                "correct": correct,
                "precision": round(precision, 4),
                "recall": round(recall, 4),
                "f1": round(f1, 4),
            }
        )
    return rows


def error_analysis(comparisons: list[PlateComparison]) -> dict[str, Any]:
    """Which characters and which positions the pipeline gets wrong.

    A confusion concentrated on one substitution (8→B, say) is a fixable
    grammar or preprocessing problem. Errors spread evenly across every
    character are a resolution problem, and no amount of post-processing fixes
    those — that is a "fine-tune or move the camera" finding.
    """
    substitutions: Counter[str] = Counter()
    positions: Counter[int] = Counter()
    length_errors = 0

    for comparison in comparisons:
        if comparison.status != "wrong":
            continue
        if len(comparison.predicted) != len(comparison.truth):
            length_errors += 1
            continue
        for index, (predicted, truth) in enumerate(zip(comparison.predicted, comparison.truth, strict=False)):
            if predicted != truth:
                substitutions[f"{truth}->{predicted}"] += 1
                positions[index] += 1

    return {
        "top_substitutions": dict(substitutions.most_common(15)),
        "errors_by_position": {str(k): v for k, v in sorted(positions.items())},
        "wrong_length_count": length_errors,
    }
