"""Run statistics and structured output.

The brief asked for "a complete result, not only a list of final number plates",
so this computes the counts, the distributions and the failure tallies, and
writes them alongside the raw per-row data that produced them. Every headline
number in `summary.json` can be recomputed from the CSVs next to it — if a
statistic and the rows disagree, the rows win, and the discrepancy is a bug
worth knowing about.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import UTC
from pathlib import Path
from typing import Any

from ailab.aggregate.grammar import REGIONS, describe_plate
from ailab.config import RunConfig
from ailab.pipeline import RunResult
from ailab.stream.events import SourceIdentity, vehicle_event
from ailab.types import Track


def _distribution(values: list[float]) -> dict[str, Any]:
    """Enough of a distribution to spot a bimodal confidence profile.

    The mean alone hides the case that matters: a detector that is either very
    sure or entirely lost, with nothing in between, needs a different threshold
    than one that is uniformly mediocre.
    """
    if not values:
        return {"count": 0}
    ordered = sorted(values)

    def percentile(p: float) -> float:
        if len(ordered) == 1:
            return round(ordered[0], 4)
        index = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
        return round(ordered[index], 4)

    return {
        "count": len(ordered),
        "min": round(ordered[0], 4),
        "p25": percentile(0.25),
        "median": percentile(0.50),
        "mean": round(statistics.fmean(ordered), 4),
        "p75": percentile(0.75),
        "p90": percentile(0.90),
        "max": round(ordered[-1], 4),
        "stdev": round(statistics.pstdev(ordered), 4) if len(ordered) > 1 else 0.0,
    }


def _histogram(values: list[float], buckets: int = 10) -> dict[str, int]:
    counts = Counter()
    for value in values:
        index = min(buckets - 1, max(0, int(value * buckets)))
        counts[index] += 1
    return {
        f"{i / buckets:.1f}-{(i + 1) / buckets:.1f}": counts.get(i, 0) for i in range(buckets)
    }


def vehicle_row(track: Track) -> dict[str, Any]:
    """One line per tracked object: the answer plus how it was reached."""
    result = track.result
    parts = describe_plate(result.text, REGIONS) if result and result.text else {}
    return {
        "track_id": track.track_id,
        "class_name": track.class_name,
        "plate": result.text if result else "",
        "plate_confidence": round(result.confidence, 4) if result else 0.0,
        "grammar_valid": result.grammar_valid if result else False,
        "grammar_note": result.grammar_note if result else "",
        "ambiguous": result.ambiguous if result else False,
        "disagreement": round(result.disagreement, 4) if result else 0.0,
        "method": result.method if result else "none",
        "corrected_from": (result.corrected_from or "") if result else "",
        "reads_total": result.reads_total if result else 0,
        "reads_agreeing": result.reads_agreeing if result else 0,
        "agreement": round(result.agreement, 4) if result else 0.0,
        "candidates": " | ".join(
            f"{c.text}:{c.score:.2f}x{c.support}" for c in (result.candidates if result else [])
        ),
        "state": parts.get("state", ""),
        "rto": parts.get("rto", ""),
        "plate_format": parts.get("format", ""),
        "first_frame": track.first_frame,
        "last_frame": track.last_frame,
        "first_seen_s": round(track.first_seen_s, 3),
        "last_seen_s": round(track.last_seen_s, 3),
        "duration_s": round(track.duration_s, 3),
        "frames_tracked": track.frame_count,
        "travelled_px": round(track.travelled_px(), 1),
        "mean_detection_confidence": round(track.mean_confidence, 4),
        "max_detection_confidence": round(track.max_confidence, 4),
        "plate_detections": len(track.plate_detections),
        "best_crop": track.best_crop_path or "",
    }


def event_payload(track: Track, result: RunResult) -> dict[str, Any]:
    """A vehicle result shaped for the platform's future event contract.

    This is deliberately the same shape the production `detection.event.v1`
    will carry, so integrating the pipeline later is a transport change rather
    than a data-model change. Nothing here is published anywhere — the lab is
    not wired to the platform, by design at this stage.
    """
    consensus = track.result
    best = track.best_observation
    started = result.run_dir.started_at.astimezone(UTC)
    return {
        "schema": "ailab.vehicle.result.v1",
        "run": result.run_dir.path.name,
        "source": result.source.name,
        "track_id": track.track_id,
        "vehicle_type": track.class_name,
        "plate": consensus.text if consensus else "",
        "plate_confidence": round(consensus.confidence, 4) if consensus else 0.0,
        "plate_grammar_valid": consensus.grammar_valid if consensus else False,
        "plate_ambiguous": consensus.ambiguous if consensus else False,
        "plate_candidates": [c.to_dict() for c in consensus.candidates] if consensus else [],
        "consensus": consensus.to_dict() if consensus else None,
        "first_seen_s": round(track.first_seen_s, 3),
        "last_seen_s": round(track.last_seen_s, 3),
        "duration_s": round(track.duration_s, 3),
        "frames_tracked": track.frame_count,
        "detection_confidence": round(track.mean_confidence, 4),
        "bbox": best.bbox.to_dict() if best else None,
        "reads": [
            {
                "frame_index": r.frame_index,
                "t_s": round(r.t_s, 3),
                "text": r.text,
                "ocr_confidence": round(r.ocr_confidence, 4),
                "vote_weight": round(r.vote_weight, 4),
                "grammar_valid": r.grammar_valid,
                "variant": r.preprocess_variant,
                "crop": r.crop_path,
            }
            for r in track.plate_reads
        ],
        "media": {
            "vehicle_crop": track.best_crop_path,
            "plate_crops": [r.crop_path for r in track.plate_reads if r.crop_path],
        },
        "generated_at": started.isoformat(),
        "integration_note": (
            "Produced by the standalone AI lab. Not published to any bus or database; "
            "shaped to match the platform event contract for later integration."
        ),
    }


def _invocations(result: RunResult) -> dict[str, Any]:
    """How often each expensive stage actually ran, per frame.

    The headline cost of a stage is rate x cost, and a profile that only reports
    cost cannot distinguish a slow model from a model called too often. These
    two answer different questions and need different fixes.
    """
    frames = max(1, result.frames_processed)
    counts = result.timer.counts
    totals = result.timer.totals

    rows: dict[str, Any] = {"frames_processed": result.frames_processed}
    for stage in (
        "detect", "plate_detect", "ocr", "preprocess",
        "crop_save_plate", "crop_save_vehicle", "consensus_incremental",
    ):
        calls = counts.get(stage, 0)
        rows[stage] = {
            "calls": calls,
            "per_frame": round(calls / frames, 2),
            "total_s": round(totals.get(stage, 0.0), 2),
            "mean_ms": round(result.timer.mean_ms(stage), 2),
        }
    return rows


def build_summary(result: RunResult, config: RunConfig) -> dict[str, Any]:
    """Every headline number the run produced."""
    tracks = list(result.tracks.values())
    plate_bearing = {c.lower() for c in config.detector.plate_bearing_classes}

    vehicles = [t for t in tracks if t.class_name.lower() in plate_bearing]
    people = [t for t in tracks if t.class_name.lower() == "person"]
    resolved = [t for t in tracks if t.result and t.result.text]
    valid = [t for t in resolved if t.result and t.result.grammar_valid]
    ambiguous = [t for t in resolved if t.result and t.result.ambiguous]
    corrected = [t for t in resolved if t.result and t.result.corrected_from]
    unresolved = [t for t in vehicles if not (t.result and t.result.text)]

    all_reads = [r for t in tracks for r in t.plate_reads] + list(result.orphan_reads)
    low_confidence_reads = [
        r for r in all_reads if r.ocr_confidence < config.hard_cases.low_ocr_confidence
    ]
    invalid_reads = [r for r in all_reads if not r.grammar_valid]

    detection_confidences = [d.confidence for d in result.detections]
    plate_confidences = [p.confidence for p in result.plate_detections]
    ocr_confidences = [r.ocr_confidence for r in all_reads]
    final_confidences = [t.result.confidence for t in resolved if t.result]

    fps = result.frames_processed / result.wall_seconds if result.wall_seconds > 0 else 0.0
    realtime = fps / result.source.fps if result.source.fps else 0.0

    return {
        "processing": {
            "source_total_frames": result.source.total_frames,
            "frames_read": result.frames_read,
            "frames_processed": result.frames_processed,
            "frame_stride": config.source.frame_stride,
            "source_fps": round(result.source.fps, 3),
            "source_duration_s": round(result.source.duration_s, 2),
            "wall_seconds": round(result.wall_seconds, 3),
            "processing_fps": round(fps, 3),
            "realtime_factor": round(realtime, 3),
            "ms_per_frame": round(1000.0 * result.wall_seconds / max(1, result.frames_processed), 2),
            # wall_seconds covers the frame loop; this covers the whole run,
            # including report generation and file writing.
            "total_measured_s": round(sum(result.timer.totals.values()), 2),
            "streams_per_core_estimate": round(realtime, 2),
        },
        "stages": result.timer.to_dict(),
        "invocations": _invocations(result),
        # Why the plate detector ran or did not. Reported so a speedup can be
        # audited rather than taken on trust.
        "plate_schedule": result.schedule_stats,
        # One physical vehicle should be one event. This says how far the raw
        # tracks were from that, and how much merging had to repair.
        "fragmentation": result.fragmentation,
        "plate_ownership": result.plate_ownership,
        # Why a located plate was or was not read. Same audit principle.
        "crop_gate": result.crop_gate_stats,
        "detection": {
            "total_detections": len(result.detections),
            "detections_per_frame": round(
                len(result.detections) / max(1, result.frames_processed), 2
            ),
            "by_class": dict(Counter(d.class_name for d in result.detections).most_common()),
            "confidence": _distribution(detection_confidences),
            "confidence_histogram": _histogram(detection_confidences),
            "low_confidence_count": sum(1 for c in detection_confidences if c < 0.5),
        },
        "tracking": {
            "unique_tracks": len(tracks),
            "vehicle_tracks": len(vehicles),
            "person_tracks": len(people),
            "by_class": dict(Counter(t.class_name for t in tracks).most_common()),
            "track_duration_s": _distribution([t.duration_s for t in tracks]),
            "track_frames": _distribution([float(t.frame_count) for t in tracks]),
            # A track seen once or twice is usually a detector flicker rather
            # than a vehicle; counting them separately keeps "unique vehicles"
            # from being quietly inflated.
            "fragmentary_tracks": sum(1 for t in tracks if t.frame_count <= 2),
            "stationary_tracks": sum(1 for t in tracks if t.travelled_px() < 20.0),
        },
        "plates": {
            "plate_detections": len(result.plate_detections),
            "frames_with_a_plate": len({p.frame_index for p in result.plate_detections}),
            "plate_detection_confidence": _distribution(plate_confidences),
            "ocr_attempts": len(all_reads),
            # Plates read that no tracked vehicle owned: each one is a vehicle
            # the object detector missed, so this is a detector-recall signal.
            "unattributed_reads": len(result.orphan_reads),
            "ocr_confidence": _distribution(ocr_confidences),
            "ocr_confidence_histogram": _histogram(ocr_confidences),
            "reads_below_threshold": len(low_confidence_reads),
            "reads_grammar_invalid": len(invalid_reads),
            "reads_by_variant": dict(
                Counter(r.preprocess_variant for r in all_reads).most_common()
            ),
            "variant_mean_confidence": {
                variant: round(
                    statistics.fmean([r.ocr_confidence for r in all_reads if r.preprocess_variant == variant]), 4
                )
                for variant in {r.preprocess_variant for r in all_reads}
            },
        },
        "results": {
            "vehicles_tracked": len(vehicles),
            "plates_resolved": len(resolved),
            "plates_grammar_valid": len(valid),
            "plates_ambiguous": len(ambiguous),
            "plates_confusion_corrected": len(corrected),
            "vehicles_without_plate": len(unresolved),
            "resolution_rate": round(len(resolved) / len(vehicles), 4) if vehicles else 0.0,
            "grammar_valid_rate": round(len(valid) / len(resolved), 4) if resolved else 0.0,
            "final_confidence": _distribution(final_confidences),
            "consensus_methods": dict(
                Counter(t.result.method for t in resolved if t.result).most_common()
            ),
            "reads_per_resolved_plate": _distribution(
                [float(t.result.reads_total) for t in resolved if t.result]
            ),
            "distinct_plates": sorted({t.result.text for t in resolved if t.result}),
            "state_codes": dict(
                Counter(
                    describe_plate(t.result.text, REGIONS).get("state", "?")
                    for t in valid if t.result
                ).most_common()
            ),
        },
        "caveats": [
            "Confidence is what the models report, not measured accuracy. Run "
            "`ailab evaluate` against ground truth before quoting any accuracy figure.",
            "Timings are for this host and this configuration only; see run.json "
            "for the machine they were measured on.",
        ],
    }


def write_outputs(result: RunResult, config: RunConfig) -> dict[str, Path]:
    """Write every structured artifact for the run."""
    run_dir = result.run_dir
    written: dict[str, Path] = {}

    written["run"] = run_dir.write_manifest(result.source.to_dict(), result.components)

    written["detections"] = run_dir.write_csv(
        "detections.csv",
        [d.to_row() for d in result.detections],
        columns=[
            "frame_index", "t_s", "track_id", "class_name", "class_id", "confidence",
            "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2", "bbox_w", "bbox_h",
        ],
    )

    written["plate_detections"] = run_dir.write_csv(
        "plate_detections.csv",
        [p.to_row() for p in result.plate_detections],
        columns=[
            "frame_index", "t_s", "track_id", "plate_confidence", "detector",
            "bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2", "bbox_w", "bbox_h",
            "vbox_x1", "vbox_y1", "vbox_x2", "vbox_y2", "crop_path",
        ],
    )

    reads = [r.to_row() for t in result.tracks.values() for r in t.plate_reads]
    # Unattributed plates are written with a blank track_id, so the CSV holds
    # every OCR attempt the run made — not only the ones a vehicle claimed.
    reads.extend(r.to_row() for r in result.orphan_reads)
    reads.sort(key=lambda r: (r["track_id"] if r["track_id"] != "" else -1, r["frame_index"]))
    written["plate_reads"] = run_dir.write_csv(
        "plate_reads.csv",
        reads,
        columns=[
            "track_id", "frame_index", "t_s", "text_raw", "text", "ocr_confidence",
            "plate_confidence", "vote_weight", "grammar_valid", "grammar_note",
            "engine", "preprocess_variant", "sharpness", "crop_w", "crop_h",
            "latency_ms", "crop_path",
        ],
    )

    vehicles = [vehicle_row(t) for t in result.tracks.values()]
    vehicles.sort(key=lambda r: r["track_id"])
    written["vehicles"] = run_dir.write_csv("vehicles.csv", vehicles)

    if config.output.events_jsonl:
        # One event per physical vehicle. The platform keys history and
        # cross-camera tracing on this, so a fragmented track must not become
        # two sightings of the same car at the same camera.
        source = SourceIdentity(
            camera_id=config.source.camera_id or result.source.name,
            name=result.source.name,
        )
        written["events"] = run_dir.write_jsonl(
            "events.jsonl",
            (
                vehicle_event(v, source, kind="vehicle.completed",
                              run_id=result.run_dir.path.name,
                              frame_size=(result.source.width, result.source.height))
                for v in result.vehicles
                if v.result and v.result.text
            ),
        )

    # Raw per-track results are kept alongside, because "the tracker split this
    # vehicle" is a finding worth inspecting rather than hiding.
    written["tracks"] = run_dir.write_csv(
        "tracks.csv", [vehicle_row(t) for t in sorted(result.tracks.values(), key=lambda t: t.track_id)]
    )

    summary = build_summary(result, config)
    result.stats = summary
    written["summary"] = run_dir.write_json("summary.json", summary)

    return written
