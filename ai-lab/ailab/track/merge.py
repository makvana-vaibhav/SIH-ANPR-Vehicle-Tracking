"""Merge fragmented tracks into physical vehicles.

A tracker assigns identities to *observations*, and it gets that wrong in ways
no parameter tuning fully removes: a vehicle occluded by a pole comes back as a
new track, two overlapping boxes swap identities, a detector flicker starts a
duplicate. Measured on a 240-frame clip, one car reported as three tracks whose
lifetimes overlapped.

For this platform that matters more than it would elsewhere. Cross-camera route
reconstruction joins sightings by plate and time, so one vehicle arriving as
three tracks at a single camera becomes three hops that never happened.

The plate is the strongest identity signal available, and it is one the tracker
does not use. So after tracking, fragments that resolved the *same plate* and are
compatible in time are merged into one vehicle, their observations pooled, and
consensus recomputed over the union — which usually produces a better-supported
answer than any fragment had alone.

Merging is deliberately conservative. Two tracks are only merged when the plate
agrees exactly and the timing is physically plausible. The raw tracks are never
discarded: the lab keeps both views, because "the tracker fragmented here" is
itself a finding worth seeing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ailab.aggregate.consensus import consensus
from ailab.config import RunConfig
from ailab.evaluate.metrics import levenshtein
from ailab.types import PlateConsensus, PlateDetection, PlateRead, Track


@dataclass(slots=True)
class Vehicle:
    """One physical vehicle: one or more tracks that resolved the same plate."""

    vehicle_id: int
    track_ids: list[int]
    class_name: str
    reads: list[PlateRead] = field(default_factory=list)
    plate_detections: list[PlateDetection] = field(default_factory=list)
    result: PlateConsensus | None = None
    first_seen_s: float = 0.0
    last_seen_s: float = 0.0
    first_frame: int = 0
    last_frame: int = 0
    frames_tracked: int = 0
    mean_detection_confidence: float = 0.0
    best_crop_path: str | None = None
    merged_from_fragments: int = 1
    # The vehicle's box at its best sighting. Carried on the event so the
    # platform can place the vehicle in the frame without re-deriving it.
    bbox: Any = None

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_seen_s - self.first_seen_s)

    @property
    def plate(self) -> str:
        return self.result.text if self.result else ""


def _compatible(a: Track, b: Track, max_gap_s: float) -> bool:
    """Could these two tracks be the same vehicle, in time?

    Overlapping spans are the duplicate-track case; a short gap is the
    occlusion case. A long gap is a different pass of a different vehicle
    carrying, as far as we can tell, the same plate — and merging those would
    invent a single long sighting out of two real ones.
    """
    if a.last_seen_s >= b.first_seen_s and b.last_seen_s >= a.first_seen_s:
        return True
    gap = b.first_seen_s - a.last_seen_s if b.first_seen_s > a.last_seen_s else a.first_seen_s - b.last_seen_s
    return gap <= max_gap_s


def _same_vehicle_plate(a: str, b: str, max_edits: int) -> bool:
    """Are these two readings the same plate, allowing for a misread character?

    Exact equality is too strict for fragments. Measured on a 240-frame clip,
    one car's two fragments resolved `GJ12HH8771` and `GJ12H8771` — the same
    plate, one dropped character — and were reported as two vehicles because the
    strings differed. Allowing a small edit distance merges them; pooling their
    reads then lets consensus pick the better-supported spelling, which is the
    right answer rather than a compromise between them.

    Length is checked first: a short string is not a near-match of a long one
    just because few edits separate them, when those edits are most of it.
    """
    if a == b:
        return True
    if not a or not b or max_edits <= 0:
        return False
    if abs(len(a) - len(b)) > max_edits:
        return False
    if min(len(a), len(b)) < 6:
        return False
    return levenshtein(a, b) <= max_edits


def _attach(
    track: Track,
    plate: str,
    by_plate: dict[str, list[list[Track]]],
    max_edits: int,
    max_gap_s: float,
) -> bool:
    """Add this track to a compatible existing cluster, if one exists."""
    for key, clusters in by_plate.items():
        if not _same_vehicle_plate(key, plate, max_edits):
            continue
        for cluster in clusters:
            if any(_compatible(existing, track, max_gap_s) for existing in cluster):
                cluster.append(track)
                return True
    return False


def merge(tracks: dict[int, Track], config: RunConfig) -> list[Vehicle]:
    """Group tracks into physical vehicles, merging plate-identical fragments."""
    cfg = config.tracker.merge
    ordered = sorted(tracks.values(), key=lambda t: (t.first_seen_s, t.track_id))

    groups: list[list[Track]] = []
    if cfg.enabled:
        # Pass 1: cluster tracks whose plate the grammar accepts. Only these may
        # define a vehicle, because a shared *invalid* string between two tracks
        # is far more likely to be the same OCR failure than the same car.
        by_plate: dict[str, list[list[Track]]] = {}
        unplaced: list[Track] = []

        for track in ordered:
            plate = track.result.text if track.result else ""
            valid = bool(track.result and track.result.grammar_valid) or not cfg.require_grammar
            if not plate:
                groups.append([track])
                continue
            if not valid:
                unplaced.append(track)
                continue
            if not _attach(track, plate, by_plate, cfg.max_plate_edits, cfg.max_gap_s):
                by_plate.setdefault(plate, []).append([track])

        # Pass 2: offer the grammar-invalid fragments to the clusters formed
        # above. This runs second on purpose — measured case: `JO8AJ1643` was
        # `GJ08AJ1643` with the leading G dropped, and its track *started
        # earlier*, so a single ordered pass judged it before the cluster it
        # belonged to existed and reported it as a second vehicle. A string the
        # grammar already rejects is known to be a misread, so it earns one more
        # edit of latitude than two valid readings earn against each other.
        for track in unplaced:
            plate = track.result.text if track.result else ""
            if not _attach(track, plate, by_plate, cfg.max_plate_edits + 1, cfg.max_gap_s):
                groups.append([track])

        for clusters in by_plate.values():
            groups.extend(clusters)
    else:
        groups = [[t] for t in ordered]

    vehicles: list[Vehicle] = []
    for index, group in enumerate(sorted(groups, key=lambda g: min(t.first_seen_s for t in g)), start=1):
        reads = [r for t in group for r in t.plate_reads]
        detections = [d for t in group for d in t.plate_detections]
        observations = [o for t in group for o in t.observations]

        # Recompute consensus over the pooled evidence. Two fragments with four
        # reads each vote as one vehicle with eight, which is a better-supported
        # answer than either had alone.
        result = consensus(reads, config.consensus) if reads else None

        best_track = max(group, key=lambda t: t.frame_count)
        confidences = [o.confidence for o in observations if o.detected]
        best_observation = max(
            (t.best_observation for t in group if t.best_observation is not None),
            key=Track.observation_score, default=None,
        )

        vehicles.append(
            Vehicle(
                vehicle_id=index,
                track_ids=sorted(t.track_id for t in group),
                class_name=best_track.class_name,
                reads=reads,
                plate_detections=detections,
                result=result,
                first_seen_s=min(t.first_seen_s for t in group),
                last_seen_s=max(t.last_seen_s for t in group),
                first_frame=min(t.first_frame for t in group),
                last_frame=max(t.last_frame for t in group),
                frames_tracked=sum(t.frame_count for t in group),
                mean_detection_confidence=(
                    sum(confidences) / len(confidences) if confidences else 0.0
                ),
                best_crop_path=best_track.best_crop_path,
                merged_from_fragments=len(group),
                bbox=best_observation.bbox if best_observation else None,
            )
        )
    return vehicles


def fragmentation_stats(tracks: dict[int, Track], vehicles: list[Vehicle]) -> dict[str, Any]:
    """How badly the tracker split physical vehicles, before and after merging."""
    plate_bearing = [v for v in vehicles if v.plate]
    fragmented = [v for v in vehicles if v.merged_from_fragments > 1]

    return {
        "raw_tracks": len(tracks),
        "merged_vehicles": len(vehicles),
        "vehicles_merged_from_fragments": len(fragmented),
        "fragments_absorbed": sum(v.merged_from_fragments - 1 for v in fragmented),
        "worst_fragmentation": max((v.merged_from_fragments for v in vehicles), default=0),
        "vehicles_with_a_plate": len(plate_bearing),
        # 1.0 means every physical vehicle produced exactly one track.
        "tracks_per_vehicle": round(len(tracks) / len(vehicles), 3) if vehicles else 0.0,
        "detail": [
            {
                "vehicle_id": v.vehicle_id,
                "plate": v.plate,
                "track_ids": v.track_ids,
                "fragments": v.merged_from_fragments,
                "first_seen_s": round(v.first_seen_s, 2),
                "last_seen_s": round(v.last_seen_s, 2),
            }
            for v in fragmented
        ],
    }
