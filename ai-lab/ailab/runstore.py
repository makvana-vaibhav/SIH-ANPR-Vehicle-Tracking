"""Reload a finished run from disk.

`ailab report`, `evaluate`, `mine` and `compare` all operate on runs that have
already happened, possibly weeks ago. Rather than keeping a pickle — which would
rot the moment a dataclass gains a field — a run is reconstructed from the CSVs
it wrote. Those files are the durable format: readable in a spreadsheet,
diffable, and stable across versions of this code.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from ailab.logging import get_logger
from ailab.types import (
    BBox,
    CropQuality,
    PlateCandidate,
    PlateConsensus,
    PlateDetection,
    PlateRead,
    Track,
    TrackObservation,
)

log = get_logger(__name__)


class LoadedRun:
    """A run directory, read back into objects."""

    def __init__(self, path: Path) -> None:
        self.path = path
        manifest_path = path / "run.json"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"{path} is not a run directory (no run.json). "
                "Point at one of the timestamped folders under runs/."
            )
        self.manifest: dict[str, Any] = json.loads(manifest_path.read_text())

        summary_path = path / "summary.json"
        self.summary: dict[str, Any] = (
            json.loads(summary_path.read_text()) if summary_path.exists() else {}
        )
        self.tracks: dict[int, Track] = _load_tracks(path)

    @property
    def name(self) -> str:
        return str(self.manifest.get("run", {}).get("name", self.path.name))

    @property
    def annotated_codec(self) -> str:
        annotated = self.path / "annotated.mp4"
        return "avc1" if annotated.exists() else ""

    def predictions(self) -> list[tuple[int | None, str, float]]:
        """(track_id, plate, confidence) for every track that resolved a plate."""
        return [
            (track.track_id, track.result.text, track.result.confidence)
            for track in sorted(self.tracks.values(), key=lambda t: t.track_id)
            if track.result and track.result.text
        ]

    def hard_case_index(self) -> list[dict[str, Any]]:
        path = self.path / "hard_cases" / "index.json"
        if not path.exists():
            return []
        return list(json.loads(path.read_text()))


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _float(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError:
        return default


def _int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(value)) if value not in (None, "") else default
    except ValueError:
        return default


def _load_tracks(path: Path) -> dict[int, Track]:
    tracks: dict[int, Track] = {}

    for row in _read_csv(path / "vehicles.csv"):
        track_id = _int(row.get("track_id"), -1)
        if track_id < 0:
            continue
        track = Track(
            track_id=track_id,
            class_name=row.get("class_name", ""),
            class_id=0,
            best_crop_path=row.get("best_crop") or None,
        )
        if row.get("plate"):
            track.result = PlateConsensus(
                text=row["plate"],
                confidence=_float(row.get("plate_confidence")),
                method=row.get("method", "unknown"),
                reads_total=_int(row.get("reads_total")),
                reads_agreeing=_int(row.get("reads_agreeing")),
                candidates=_parse_candidates(row.get("candidates", "")),
                grammar_valid=row.get("grammar_valid", "").lower() == "true",
                grammar_note=row.get("grammar_note", ""),
                corrected_from=row.get("corrected_from") or None,
                ambiguous=row.get("ambiguous", "").lower() == "true",
                disagreement=_float(row.get("disagreement")),
            )
        tracks[track_id] = track

    for row in _read_csv(path / "detections.csv"):
        track_id = _int(row.get("track_id"), -1)
        track = tracks.get(track_id)
        if track is None:
            continue
        track.observations.append(
            TrackObservation(
                frame_index=_int(row.get("frame_index")),
                t_s=_float(row.get("t_s")),
                bbox=BBox(
                    _float(row.get("bbox_x1")), _float(row.get("bbox_y1")),
                    _float(row.get("bbox_x2")), _float(row.get("bbox_y2")),
                ),
                confidence=_float(row.get("confidence")),
                detected=True,
            )
        )

    for row in _read_csv(path / "plate_reads.csv"):
        track_id = _int(row.get("track_id"), -1)
        track = tracks.get(track_id)
        if track is None:
            continue
        track.plate_reads.append(
            PlateRead(
                text_raw=row.get("text_raw", ""),
                text=row.get("text", ""),
                ocr_confidence=_float(row.get("ocr_confidence")),
                frame_index=_int(row.get("frame_index")),
                t_s=_float(row.get("t_s")),
                track_id=track_id,
                engine=row.get("engine", ""),
                quality=CropQuality(
                    sharpness=_float(row.get("sharpness")),
                    brightness=128.0,   # not persisted; only used for live weighting
                    contrast=40.0,
                    width=_int(row.get("crop_w")),
                    height=_int(row.get("crop_h")),
                ),
                grammar_valid=row.get("grammar_valid", "").lower() == "true",
                grammar_note=row.get("grammar_note", ""),
                crop_path=row.get("crop_path") or None,
                plate_confidence=_float(row.get("plate_confidence")),
                preprocess_variant=row.get("preprocess_variant", ""),
                latency_ms=_float(row.get("latency_ms")),
            )
        )

    for row in _read_csv(path / "plate_detections.csv"):
        track_id = _int(row.get("track_id"), -1)
        track = tracks.get(track_id)
        if track is None:
            continue
        vbox = None
        if row.get("vbox_x2") not in (None, ""):
            vbox = BBox(
                _float(row.get("vbox_x1")), _float(row.get("vbox_y1")),
                _float(row.get("vbox_x2")), _float(row.get("vbox_y2")),
            )
        track.plate_detections.append(
            PlateDetection(
                bbox=BBox(
                    _float(row.get("bbox_x1")), _float(row.get("bbox_y1")),
                    _float(row.get("bbox_x2")), _float(row.get("bbox_y2")),
                ),
                bbox_in_vehicle=vbox,
                confidence=_float(row.get("plate_confidence")),
                frame_index=_int(row.get("frame_index")),
                t_s=_float(row.get("t_s")),
                track_id=track_id,
                crop_path=row.get("crop_path") or None,
                detector=row.get("detector", ""),
            )
        )

    for track in tracks.values():
        track.observations.sort(key=lambda o: o.frame_index)

    return tracks


def _parse_candidates(text: str) -> list[PlateCandidate]:
    """Parse the `GJ03AB1234:0.82x4 | GJ03AB1284:0.18x1` column back into objects."""
    out: list[PlateCandidate] = []
    for chunk in (c.strip() for c in text.split("|") if c.strip()):
        try:
            plate, _, rest = chunk.partition(":")
            score, _, support = rest.partition("x")
            out.append(
                PlateCandidate(
                    text=plate.strip(),
                    score=float(score),
                    support=int(support),
                    grammar_valid=False,
                )
            )
        except ValueError:
            log.debug("could not parse candidate chunk: %r", chunk)
    return out
