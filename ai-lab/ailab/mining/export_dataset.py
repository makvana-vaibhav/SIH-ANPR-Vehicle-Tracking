"""Training-set export.

Turns a run's output into datasets a model can actually be fine-tuned on. Two
are produced, because the two failure modes need different training data:

  plate_detection/   YOLO-format: vehicle crops with the plate box marked.
                     For when the plate detector is *missing* plates.
  plate_ocr/         recognition format: plate crops with their text.
                     For when plates are found but read wrongly.

The honest part of this module is the third output. A hard case has no reliable
label by definition — if the pipeline could label it correctly it would not be
a hard case. So anything the pipeline was unsure about goes to `to_label/` with
a pre-filled CSV holding the pipeline's best guess, for a human to correct.
Training on the pipeline's own uncertain output would teach the model to repeat
its own mistakes with more conviction, which is worse than not training at all.
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ailab.logging import get_logger
from ailab.types import Track

log = get_logger(__name__)

# Reads at or above this confidence, with a grammar-valid string and agreement
# across frames, are treated as reliable enough to auto-label. Everything else
# goes for human review.
AUTOLABEL_MIN_CONFIDENCE = 0.80
AUTOLABEL_MIN_AGREEMENT = 0.75


class DatasetExporter:
    """Writes a fine-tuning dataset from one or more runs."""

    def __init__(self, output: Path) -> None:
        self.root = output
        self.det_images = output / "plate_detection" / "images"
        self.det_labels = output / "plate_detection" / "labels"
        self.ocr_images = output / "plate_ocr" / "images"
        self.review_images = output / "to_label" / "images"
        for directory in (
            self.det_images, self.det_labels, self.ocr_images, self.review_images
        ):
            directory.mkdir(parents=True, exist_ok=True)

        self.ocr_labels: list[tuple[str, str]] = []
        self.review_rows: list[dict[str, Any]] = []
        self.counts = {"detection": 0, "ocr": 0, "to_label": 0}
        self.sources: list[str] = []

    def add_run(
        self,
        run_path: Path,
        tracks: dict[int, Track],
        hard_case_track_ids: set[int],
    ) -> None:
        """Harvest one run's crops into the dataset."""
        self.sources.append(run_path.name)
        prefix = run_path.name[:32]

        for track in tracks.values():
            result = track.result
            reliable = bool(
                result
                and result.text
                and result.grammar_valid
                and result.confidence >= AUTOLABEL_MIN_CONFIDENCE
                and result.agreement >= AUTOLABEL_MIN_AGREEMENT
                and track.track_id not in hard_case_track_ids
            )

            for index, read in enumerate(track.plate_reads):
                if not read.crop_path:
                    continue
                source = run_path / read.crop_path
                if not source.exists():
                    continue

                name = f"{prefix}__t{track.track_id:04d}_{index:03d}.jpg"
                if reliable and result is not None:
                    shutil.copy2(source, self.ocr_images / name)
                    self.ocr_labels.append((f"images/{name}", result.text))
                    self.counts["ocr"] += 1
                else:
                    shutil.copy2(source, self.review_images / name)
                    self.review_rows.append(
                        {
                            "image": f"images/{name}",
                            "pipeline_guess": read.text,
                            "consensus_guess": result.text if result else "",
                            "confidence": round(read.ocr_confidence, 4),
                            "grammar_valid": read.grammar_valid,
                            "correct_plate": "",     # for a human to fill in
                            "unreadable": "",        # mark 'y' if genuinely illegible
                            "run": run_path.name,
                            "track_id": track.track_id,
                            "frame_index": read.frame_index,
                        }
                    )
                    self.counts["to_label"] += 1

            # Detection training pairs: the vehicle crop plus where the plate
            # sat inside it, in YOLO normalised xywh.
            if not track.best_crop_path:
                continue
            vehicle_source = run_path / track.best_crop_path
            if not vehicle_source.exists():
                continue
            best_detection = next(
                (p for p in track.plate_detections if p.bbox_in_vehicle is not None), None
            )
            if best_detection is None or best_detection.bbox_in_vehicle is None:
                continue

            import cv2

            image = cv2.imread(str(vehicle_source))
            if image is None:
                continue
            height, width = image.shape[:2]
            box = best_detection.bbox_in_vehicle
            if box.width <= 0 or box.height <= 0 or width == 0 or height == 0:
                continue
            # The vehicle crop and the plate-search region were padded
            # differently, so a box can fall outside; drop rather than clamp,
            # because a clamped box is a wrong label.
            if box.x2 > width or box.y2 > height:
                continue

            stem = f"{prefix}__t{track.track_id:04d}_vehicle"
            shutil.copy2(vehicle_source, self.det_images / f"{stem}.jpg")
            (self.det_labels / f"{stem}.txt").write_text(
                f"0 {box.cx / width:.6f} {box.cy / height:.6f} "
                f"{box.width / width:.6f} {box.height / height:.6f}\n",
                encoding="utf-8",
            )
            self.counts["detection"] += 1

    def finalise(self) -> dict[str, Any]:
        """Write label files, the YOLO data.yaml, and a manifest."""
        (self.root / "plate_ocr" / "labels.txt").write_text(
            "\n".join(f"{path}\t{text}" for path, text in self.ocr_labels) + "\n",
            encoding="utf-8",
        )

        (self.root / "plate_detection" / "data.yaml").write_text(
            "# YOLO dataset for fine-tuning the licence-plate detector.\n"
            "# Split into train/val before training — this export is unsplit on\n"
            "# purpose, because a random split would put near-identical frames of\n"
            "# the same vehicle on both sides and report a flattering score.\n"
            f"path: {self.root.resolve() / 'plate_detection'}\n"
            "train: images\n"
            "val: images\n"
            "nc: 1\n"
            "names: ['license_plate']\n",
            encoding="utf-8",
        )

        if self.review_rows:
            review_csv = self.root / "to_label" / "labels_to_fill.csv"
            with review_csv.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(self.review_rows[0].keys()))
                writer.writeheader()
                writer.writerows(self.review_rows)

        manifest = {
            "created_at": datetime.now(UTC).isoformat(),
            "source_runs": self.sources,
            "counts": self.counts,
            "autolabel_thresholds": {
                "min_confidence": AUTOLABEL_MIN_CONFIDENCE,
                "min_agreement": AUTOLABEL_MIN_AGREEMENT,
                "rule": "grammar-valid, above both thresholds, and not flagged as a hard case",
            },
            "layout": {
                "plate_detection": "YOLO format, 1 class. Auto-labelled from the current "
                                   "detector — useful for augmenting, NOT as a clean benchmark.",
                "plate_ocr": "images/ + labels.txt (path<TAB>text). Auto-labelled from "
                             "high-confidence consensus only.",
                "to_label": "images/ + labels_to_fill.csv. The uncertain cases. Fill in "
                            "'correct_plate' by hand before training on these — they are "
                            "the examples that would actually improve the model.",
            },
            "warning": (
                "plate_ocr labels are the pipeline's own output. Training on them reinforces "
                "what the model already does. Real improvement comes from to_label/ once a "
                "human has corrected it."
            ),
        }
        (self.root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        log.info(
            "dataset written to %s — %d detection, %d ocr, %d awaiting labels",
            self.root, self.counts["detection"], self.counts["ocr"], self.counts["to_label"],
        )
        return manifest
