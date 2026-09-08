"""Ground-truth loading and matching.

Ground truth arrives in whatever shape whoever labelled the footage found
convenient, so three layouts are accepted:

  plate                      the set of plates that appear in the clip
  track_id,plate             a plate per tracked object (exact join)
  frame,plate / t_s,plate    a plate at a moment in time

Only `track_id` gives an unambiguous join. The other two need matching, and the
matching is deliberately conservative: a predicted plate is paired with the
closest unused ground-truth string only if they are within an edit distance
that could plausibly be OCR noise. Pairing anything with anything would let a
completely wrong read count as "close", which would flatter the CER and hide
exactly the failures we are looking for.
"""

from __future__ import annotations

import contextlib
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ailab.aggregate.grammar import normalise
from ailab.evaluate.metrics import PlateComparison, compare, levenshtein
from ailab.logging import get_logger

log = get_logger(__name__)

# Beyond this many edits two strings are different plates, not one misread one.
MAX_MATCH_DISTANCE = 3


@dataclass(slots=True)
class GroundTruth:
    """Expected plates for one piece of footage."""

    plates: list[str]
    by_track: dict[int, str]
    by_frame: dict[int, str]
    source_path: str

    @property
    def keyed_by_track(self) -> bool:
        return bool(self.by_track)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source_path,
            "plate_count": len(self.plates),
            "keyed_by": "track_id" if self.by_track else ("frame" if self.by_frame else "plate-set"),
            "plates": self.plates,
        }


def load(path: str | Path) -> GroundTruth:
    """Read a ground-truth CSV in any of the accepted layouts."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"ground truth not found: {target}")

    plates: list[str] = []
    by_track: dict[int, str] = {}
    by_frame: dict[int, str] = {}

    with target.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"{target} has no header row")

        columns = {name.strip().lower(): name for name in reader.fieldnames}
        plate_column = next(
            (columns[c] for c in ("plate", "plate_number", "number", "text", "gt") if c in columns),
            None,
        )
        if plate_column is None:
            raise ValueError(
                f"{target} needs a 'plate' column. Found: {list(columns)}"
            )
        track_column = columns.get("track_id") or columns.get("track")
        frame_column = columns.get("frame") or columns.get("frame_index")

        for row in reader:
            plate = normalise(str(row.get(plate_column, "")))
            if not plate:
                continue
            plates.append(plate)
            if track_column and str(row.get(track_column, "")).strip():
                try:
                    by_track[int(float(row[track_column]))] = plate
                except ValueError:
                    log.warning("non-numeric track_id in ground truth: %r", row[track_column])
            if frame_column and str(row.get(frame_column, "")).strip():
                with contextlib.suppress(ValueError):
                    by_frame[int(float(row[frame_column]))] = plate

    if not plates:
        raise ValueError(f"{target} contained no usable plate values")

    log.info(
        "ground truth: %d plates%s from %s",
        len(plates), " (keyed by track_id)" if by_track else "", target.name,
    )
    return GroundTruth(
        plates=plates, by_track=by_track, by_frame=by_frame, source_path=str(target)
    )


def evaluate_predictions(
    predictions: list[tuple[int | None, str, float]], truth: GroundTruth
) -> list[PlateComparison]:
    """Judge predictions against ground truth.

    `predictions` is (track_id, plate, confidence). Returns one comparison per
    prediction plus one 'missed' entry for each ground-truth plate nothing was
    matched to — a plate the pipeline never read at all is the most important
    failure and it would be invisible if only predictions were scored.
    """
    comparisons: list[PlateComparison] = []

    if truth.keyed_by_track:
        unmatched = dict(truth.by_track)
        for track_id, plate, confidence in predictions:
            if track_id is not None and track_id in truth.by_track:
                comparisons.append(compare(plate, truth.by_track[track_id], confidence, track_id))
                unmatched.pop(track_id, None)
            else:
                comparisons.append(
                    PlateComparison(
                        track_id=track_id, predicted=plate, truth="", confidence=confidence,
                        exact=False, cer=1.0, edit_distance=len(plate), status="spurious",
                    )
                )
        for track_id, plate in unmatched.items():
            comparisons.append(
                PlateComparison(
                    track_id=track_id, predicted="", truth=plate, confidence=0.0,
                    exact=False, cer=1.0, edit_distance=len(plate), status="missed",
                )
            )
        return comparisons

    # ── Set matching: greedily pair each prediction with its nearest unused truth ──
    remaining = list(truth.plates)
    # Best (smallest-distance) pairs first, so a confident exact match claims
    # its ground truth before a poor read can steal it.
    ordered = sorted(
        predictions,
        key=lambda p: min((levenshtein(p[1], t) for t in remaining), default=99),
    )

    for track_id, plate, confidence in ordered:
        if not remaining:
            comparisons.append(
                PlateComparison(
                    track_id=track_id, predicted=plate, truth="", confidence=confidence,
                    exact=False, cer=1.0, edit_distance=len(plate), status="spurious",
                )
            )
            continue

        best = min(remaining, key=lambda t: levenshtein(plate, t))
        distance = levenshtein(plate, best)
        if distance <= MAX_MATCH_DISTANCE:
            remaining.remove(best)
            comparisons.append(compare(plate, best, confidence, track_id))
        else:
            comparisons.append(
                PlateComparison(
                    track_id=track_id, predicted=plate, truth="", confidence=confidence,
                    exact=False, cer=1.0, edit_distance=len(plate), status="spurious",
                )
            )

    for plate in remaining:
        comparisons.append(
            PlateComparison(
                track_id=None, predicted="", truth=plate, confidence=0.0,
                exact=False, cer=1.0, edit_distance=len(plate), status="missed",
            )
        )
    return comparisons


def summarise(comparisons: list[PlateComparison]) -> dict[str, Any]:
    """Headline accuracy figures — measured, not claimed."""
    correct = [c for c in comparisons if c.status == "correct"]
    wrong = [c for c in comparisons if c.status == "wrong"]
    missed = [c for c in comparisons if c.status == "missed"]
    spurious = [c for c in comparisons if c.status == "spurious"]

    scored = correct + wrong
    attempted = len(scored)
    total_truth = len(correct) + len(wrong) + len(missed)

    precision = len(correct) / (attempted + len(spurious)) if (attempted + len(spurious)) else 0.0
    recall = len(correct) / total_truth if total_truth else 0.0

    return {
        "ground_truth_plates": total_truth,
        "predictions": attempted + len(spurious),
        "matched": attempted,
        "correct": len(correct),
        "wrong": len(wrong),
        "missed": len(missed),
        "spurious": len(spurious),
        # Of the plates we attempted, how many were exactly right.
        "plate_accuracy": round(len(correct) / attempted, 4) if attempted else 0.0,
        # Of all plates present, how many we got exactly right end to end.
        "end_to_end_accuracy": round(len(correct) / total_truth, 4) if total_truth else 0.0,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4)
        if (precision + recall)
        else 0.0,
        "mean_cer": round(sum(c.cer for c in scored) / attempted, 4) if attempted else 1.0,
        "median_cer": round(sorted(c.cer for c in scored)[attempted // 2], 4) if attempted else 1.0,
        "within_one_character": sum(1 for c in scored if c.edit_distance <= 1),
    }
