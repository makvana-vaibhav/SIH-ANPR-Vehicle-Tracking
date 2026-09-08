"""Hard-case identification.

The runs that matter are the ones that fail. This walks the results and flags
every vehicle the pipeline handled badly, with the reason attached, so that
improving the models becomes a matter of looking at a folder rather than
scrubbing through video hoping to notice something.

Each reason maps to a different fix, and keeping them distinct is the point:

  no_plate_found      the plate detector missed, or the plate is unreadable
                      at this resolution — a detector or camera-siting problem
  ocr_failed          a plate was found but produced no text — an OCR problem
  low_confidence      read, but weakly — a threshold question
  grammar_invalid     read as something that is not a plate — often a fixable
                      confusion, sometimes a genuinely unusual plate
  ambiguous           two candidates too close to separate — needs more frames
                      or better crops
  high_disagreement   frames disagreed badly — usually motion blur
  wrong               contradicted by ground truth — the only reason here that
                      is measured rather than inferred
"""

from __future__ import annotations

from typing import Any

from ailab.config import HardCaseConfig, RunConfig
from ailab.evaluate.metrics import PlateComparison
from ailab.types import Track


def find(
    tracks: dict[int, Track],
    config: RunConfig,
    comparisons: list[PlateComparison] | None = None,
) -> list[dict[str, Any]]:
    """Flag every track worth a human look. One entry per track, worst reason first."""
    rules: HardCaseConfig = config.hard_cases
    if not rules.enabled:
        return []

    plate_bearing = {c.lower() for c in config.detector.plate_bearing_classes}
    wrong_by_track = {
        c.track_id: c
        for c in (comparisons or [])
        if c.status == "wrong" and c.track_id is not None
    }

    cases: list[dict[str, Any]] = []
    for track in sorted(tracks.values(), key=lambda t: t.track_id):
        if track.class_name.lower() not in plate_bearing:
            continue

        result = track.result
        best_crop = max(track.plate_reads, key=lambda r: r.vote_weight, default=None)
        crop_path = (
            best_crop.crop_path if best_crop and best_crop.crop_path else track.best_crop_path
        )

        base = {
            "track_id": track.track_id,
            "class_name": track.class_name,
            "plate": result.text if result else "",
            "confidence": result.confidence if result else 0.0,
            "frames_tracked": track.frame_count,
            "reads": len(track.plate_reads),
            "plate_detections": len(track.plate_detections),
            "first_seen_s": round(track.first_seen_s, 3),
            "crop_path": crop_path,
            "vehicle_crop": track.best_crop_path,
        }

        # Ground truth trumps every inferred signal: if we know it is wrong,
        # that is the reason, whatever the pipeline thought of itself.
        if track.track_id in wrong_by_track:
            comparison = wrong_by_track[track.track_id]
            cases.append({
                **base,
                "reason": "wrong",
                "detail": f"read {comparison.predicted}, truth {comparison.truth} "
                          f"(edit distance {comparison.edit_distance})",
                "truth": comparison.truth,
            })
            continue

        if not track.plate_reads:
            if track.plate_detections:
                cases.append({
                    **base,
                    "reason": "ocr_failed",
                    "detail": f"{len(track.plate_detections)} plate(s) located, "
                              "none produced usable text",
                })
            elif track.frame_count >= rules.min_track_frames_without_plate:
                cases.append({
                    **base,
                    "reason": "no_plate_found",
                    "detail": f"tracked for {track.frame_count} frames "
                              f"({track.duration_s:.1f}s), no plate ever detected",
                })
            continue

        if result is None or not result.text:
            cases.append({
                **base,
                "reason": "ocr_failed",
                "detail": f"{len(track.plate_reads)} read(s) but consensus produced nothing",
            })
            continue

        if rules.flag_ambiguous and result.ambiguous:
            runner_up = result.candidates[1].text if len(result.candidates) > 1 else "?"
            cases.append({
                **base,
                "reason": "ambiguous",
                "detail": f"{result.text} vs {runner_up} — too close to separate",
            })
            continue

        if rules.flag_grammar_invalid and not result.grammar_valid:
            cases.append({
                **base,
                "reason": "grammar_invalid",
                "detail": result.grammar_note or "does not match any Indian plate format",
            })
            continue

        if result.disagreement > rules.flag_disagreement_above:
            cases.append({
                **base,
                "reason": "high_disagreement",
                "detail": f"top candidate held only "
                          f"{(1 - result.disagreement) * 100:.0f}% of the weighted vote "
                          f"across {result.reads_total} reads",
            })
            continue

        if result.confidence < rules.low_ocr_confidence:
            cases.append({
                **base,
                "reason": "low_confidence",
                "detail": f"consensus confidence {result.confidence:.2f} is below "
                          f"{rules.low_ocr_confidence:.2f}",
            })

    return cases


def summarise(cases: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for case in cases:
        counts[case["reason"]] = counts.get(case["reason"], 0) + 1
    return {
        "total": len(cases),
        "by_reason": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
    }
