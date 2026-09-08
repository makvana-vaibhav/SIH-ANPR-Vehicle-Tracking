"""Run output directory.

Every run writes a self-contained, timestamped directory. Self-contained is the
requirement that matters: the config that produced a result sits next to the
result, so a run from three weeks ago can be re-run or argued with without
anyone having to remember what the flags were.

Layout
------
runs/2026-08-25T18-30-00__road_cctv__default/
  run.json            config, environment, model provenance, versions
  summary.json        every statistic the run produced
  detections.csv      one row per object per frame
  plate_reads.csv     one row per OCR attempt — including the rejected ones
  vehicles.csv        one row per track: the final answer
  events.jsonl        integration-shaped events, one per vehicle
  annotated.mp4       the video with everything drawn on it
  report.html         self-contained, opens offline
  crops/plates/       every plate crop that was read
  crops/vehicles/     the best frame of each tracked vehicle
  hard_cases/         what the pipeline struggled with, with an index
"""

from __future__ import annotations

import csv
import json
import platform
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ailab.config import REPO_ROOT, RunConfig
from ailab.logging import get_logger

log = get_logger(__name__)


class RunDirectory:
    """Creates and owns one run's output tree."""

    def __init__(self, root: Path, config: RunConfig, source_name: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
        self.path = root / f"{stamp}__{_slug(source_name)}__{_slug(config.name)}"
        self.path.mkdir(parents=True, exist_ok=False)

        self.plates_dir = self.path / "crops" / "plates"
        self.vehicles_dir = self.path / "crops" / "vehicles"
        self.hard_cases_dir = self.path / "hard_cases"
        self.keyframes_dir = self.path / "keyframes"
        for directory in (self.plates_dir, self.vehicles_dir, self.hard_cases_dir):
            directory.mkdir(parents=True, exist_ok=True)

        self.config = config
        self.started_at = datetime.now(UTC)

    # ── paths ──
    @property
    def annotated_video(self) -> Path:
        return self.path / "annotated.mp4"

    @property
    def report(self) -> Path:
        return self.path / "report.html"

    def relative(self, path: Path | str) -> str:
        """Path relative to the run root, for portable references in CSV/JSON."""
        try:
            return str(Path(path).relative_to(self.path))
        except ValueError:
            return str(path)

    # ── writers ──
    def save_crop(self, image: np.ndarray, directory: Path, name: str) -> str | None:
        """Write a crop as JPEG; returns the run-relative path."""
        if image is None or image.size == 0:
            return None
        target = directory / f"{name}.jpg"
        # Quality 95: these crops are evidence and may become training data,
        # so compression artefacts would be baked into a future fine-tune.
        ok = cv2.imwrite(str(target), image, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        if not ok:
            log.warning("failed to write crop: %s", target)
            return None
        return self.relative(target)

    def save_plate_crop(self, image: np.ndarray, name: str) -> str | None:
        return self.save_crop(image, self.plates_dir, name)

    def save_vehicle_crop(self, image: np.ndarray, name: str) -> str | None:
        return self.save_crop(image, self.vehicles_dir, name)

    def write_json(self, name: str, payload: dict[str, Any]) -> Path:
        target = self.path / name
        target.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")
        return target

    def write_jsonl(self, name: str, rows: Iterable[dict[str, Any]]) -> Path:
        target = self.path / name
        with target.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, default=_json_default) + "\n")
        return target

    def write_csv(self, name: str, rows: list[dict[str, Any]], columns: list[str] | None = None) -> Path:
        target = self.path / name
        if not rows:
            # An empty CSV with headers is far more useful than a missing file:
            # downstream tooling and the report can still read the schema.
            target.write_text(",".join(columns or []) + "\n", encoding="utf-8")
            return target

        # Union of every row's keys, in first-appearance order. Taking the
        # first row's keys alone would silently drop a column that only some
        # rows carry — hard cases, for instance, gain a 'truth' field only when
        # ground truth was supplied.
        fieldnames = columns
        if fieldnames is None:
            fieldnames = []
            for row in rows:
                for key in row:
                    if key not in fieldnames:
                        fieldnames.append(key)
        with target.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return target

    def write_manifest(self, source: dict[str, Any], components: dict[str, Any]) -> Path:
        """Everything needed to reproduce or dispute this run."""
        return self.write_json(
            "run.json",
            {
                "run": {
                    "name": self.config.name,
                    "description": self.config.description,
                    "directory": self.path.name,
                    "started_at": self.started_at.isoformat(),
                },
                "source": source,
                "config": self.config.model_dump(),
                "components": components,
                "environment": environment_info(),
            },
        )


def environment_info() -> dict[str, Any]:
    """Host and library versions — the run record has to say what it ran on.

    Timings are meaningless without this. A throughput number from a 10-core
    laptop is not a claim about a GPU server, and recording the machine is what
    keeps the distinction visible later.
    """
    info: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": _cpu_count(),
    }
    try:
        import onnxruntime

        info["onnxruntime"] = onnxruntime.__version__
        info["onnxruntime_providers"] = list(onnxruntime.get_available_providers())
    except ImportError:
        pass
    info["opencv"] = cv2.__version__
    info["numpy"] = np.__version__
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
    except ImportError:
        info["torch"] = None
    return info


def _cpu_count() -> int:
    import os

    # The cgroup-aware count: inside a container this is what we actually get,
    # and os.cpu_count() would report the host's cores and flatter the numbers.
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def _slug(text: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in text]
    return "".join(keep).strip("-")[:60] or "run"


def _json_default(value: Any) -> Any:
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def runs_root() -> Path:
    root = REPO_ROOT / "runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def latest_run(root: Path | None = None) -> Path | None:
    """Most recent run directory — lets `ailab report` default to 'the last one'."""
    base = root or runs_root()
    candidates = [p for p in base.iterdir() if p.is_dir() and (p / "run.json").exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
