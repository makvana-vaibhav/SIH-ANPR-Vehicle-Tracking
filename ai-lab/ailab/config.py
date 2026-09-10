"""Run configuration.

One YAML file describes a complete experiment: which models, which thresholds,
which preprocessing. Two runs that differ only in `ocr.engine` are directly
comparable, which is the whole point — we want to choose models by measurement,
not by reputation.

Every config is copied verbatim into the run directory, so a result is always
reproducible from its own output.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

REPO_ROOT = Path(__file__).resolve().parent.parent


class SourceConfig(BaseModel):
    """How frames are pulled off the input."""

    # Analyse every Nth frame. CCTV at 25 fps rarely needs 25 analyses/s —
    # a vehicle crossing frame is visible for dozens of frames either way.
    frame_stride: int = Field(default=1, ge=1)
    max_frames: int | None = Field(default=None, ge=1)
    start_s: float = Field(default=0.0, ge=0.0)
    end_s: float | None = None
    resize_width: int | None = Field(default=None, ge=64)
    # Identifies the camera in every emitted event. The platform keys vehicle
    # history and cross-camera tracing on this, so it is worth setting
    # explicitly rather than defaulting to a filename.
    camera_id: str = ""


class DetectorConfig(BaseModel):
    """Vehicle / person detector."""

    engine: str = "yolo_onnx"
    weights: str = "models/yolov8n.onnx"
    confidence: float = Field(default=0.30, ge=0.0, le=1.0)
    iou: float = Field(default=0.45, ge=0.0, le=1.0)
    imgsz: int = Field(default=640, ge=64)
    max_detections: int = Field(default=300, ge=1)
    # COCO classes we care about. Persons are included because the challenge
    # asks for them later; they are tracked but never plate-searched.
    classes: list[str] = Field(
        default_factory=lambda: ["car", "motorcycle", "bus", "truck", "bicycle", "person"]
    )
    # Which of those classes can own a licence plate.
    plate_bearing_classes: list[str] = Field(
        default_factory=lambda: ["car", "motorcycle", "bus", "truck"]
    )
    # "auto" takes the best provider actually installed. "cuda" and "coreml"
    # raise at startup when their provider is absent rather than falling back
    # to CPU, because a GPU deployment that silently runs on CPU is a capacity
    # plan wrong by an order of magnitude that reports nothing. Inside Docker
    # on Apple Silicon only "cpu" exists — see docs/GPU.md.
    device: Literal["auto", "cpu", "cuda", "coreml"] = "auto"
    # ONNX Runtime intra-op threads. 0 picks a measured default; the library's
    # own default (one per core) is markedly slower on small models.
    threads: int = Field(default=0, ge=0)


class TrackMergeConfig(BaseModel):
    """Merging tracker fragments into physical vehicles.

    The tracker identifies observations; this identifies vehicles. Kept separate
    because the raw tracks are diagnostic evidence — "the tracker fragmented
    here" is a finding, not noise to be hidden.
    """

    enabled: bool = True
    # Two fragments carrying the same plate this far apart in time are two
    # separate passes, not one vehicle. Beyond it, merging would invent a single
    # long sighting out of two real ones.
    max_gap_s: float = Field(default=3.0, ge=0.0)
    # Only merge on a plate the grammar accepts. A shared invalid string is far
    # more likely to be the same OCR failure mode than the same vehicle.
    require_grammar: bool = True
    # Fragments whose plates differ by at most this many characters are treated
    # as the same vehicle; consensus over the pooled reads then decides the
    # spelling. 0 requires exact equality.
    max_plate_edits: int = Field(default=1, ge=0)


class TrackerConfig(BaseModel):
    engine: str = "bytetrack"
    track_high_thresh: float = 0.50
    track_low_thresh: float = 0.10
    new_track_thresh: float = 0.55
    match_thresh: float = 0.80
    track_buffer: int = Field(default=30, ge=1)   # frames a lost track survives
    min_box_area: float = 100.0
    merge: TrackMergeConfig = Field(default_factory=TrackMergeConfig)


class PlateScheduleConfig(BaseModel):
    """When a tracked vehicle is worth searching for a plate.

    Defaults are deliberately conservative: they cut redundant searches without
    reducing the number of *distinct* looks a vehicle gets, which is what
    multi-frame consensus actually needs. Sweep them rather than trusting them.
    """

    enabled: bool = True
    # Never search the same vehicle more often than this many frames apart.
    # Consecutive frames at 30fps cannot show a materially different plate.
    min_interval: int = Field(default=3, ge=1)
    # Look at least this often even when nothing appears to have changed, so a
    # slow-moving or stationary vehicle is not abandoned.
    max_interval: int = Field(default=12, ge=1)
    # Fractional change in box area that counts as a genuinely new view.
    min_scale_change: float = Field(default=0.18, ge=0.0)
    # Centre movement, as a fraction of the box's short side, that counts.
    min_move_fraction: float = Field(default=0.30, ge=0.0)
    # A plate is roughly a fifth of a vehicle's width; below this the plate
    # cannot be legible and searching is guaranteed waste.
    min_vehicle_width: int = Field(default=90, ge=1)
    # Back off on vehicles that never yield a plate (facing away, obscured).
    miss_backoff: bool = True
    backoff_factor: int = Field(default=4, ge=1)
    # Until a vehicle has yielded a plate, give it this many attempts before any
    # cooldown or backoff applies. Short tracks are the ones that need it: a
    # sprite visible for four frames gets one look under a blanket cooldown and
    # is lost, which is exactly the "plate was previously unreadable, try again"
    # case. After these attempts the backoff takes over and bounds the cost.
    min_attempts_before_cooldown: int = Field(default=4, ge=1)
    # 0 = unlimited. A cap bounds worst-case cost on very long tracks.
    max_searches_per_track: int = Field(default=0, ge=0)


class CropGateConfig(BaseModel):
    """Whether a plate crop is new evidence, or something we have already read.

    The plate scheduler decides whether to *look*; this decides whether what was
    found is worth reading. A vehicle can move enough to justify a fresh search
    and still produce a crop effectively identical to one read four frames ago.
    """

    enabled: bool = True
    # Fractional improvement in sharpness or width that makes a crop worth
    # re-reading — a clearer or closer look is genuinely new evidence.
    min_sharpness_gain: float = Field(default=0.25, ge=0.0)
    min_width_gain: float = Field(default=0.20, ge=0.0)
    # Fraction of perceptual-hash bits that must differ for the view to count
    # as different. Around 0.15 separates "same plate, next frame" from "same
    # plate, meaningfully different angle" on the footage tested.
    min_signature_distance: float = Field(default=0.15, ge=0.0, le=1.0)
    # While the best read for a vehicle is this weak, keep reading regardless:
    # a poor result is exactly the case more evidence might fix.
    low_confidence: float = Field(default=0.75, ge=0.0, le=1.0)


class PlateDetectorConfig(BaseModel):
    engine: str = "plate_yolo_onnx"
    weights: str = "models/plate_yolo11n.onnx"
    confidence: float = Field(default=0.25, ge=0.0, le=1.0)
    iou: float = 0.45
    # 320, not 640: see imgsz_strategy below. Measured to cost a quarter as much
    # per search with no loss of plate recall on the footage tested.
    imgsz: int = 320
    # Search inside the vehicle box (fast, fewer false positives) or across the
    # whole frame (catches plates the vehicle detector missed).
    mode: Literal["per_vehicle", "full_frame"] = "per_vehicle"
    # Look for a plate every Nth analysed frame per vehicle. A car is in view
    # for dozens of frames and plate detection is not free; 1 gives the most
    # evidence for consensus, 2-3 roughly halves pipeline cost for a small loss.
    every_n_frames: int = Field(default=1, ge=1)
    # Pad the vehicle crop before searching: plates sit at the very edge of a
    # tight box and get clipped otherwise.
    vehicle_pad: float = Field(default=0.05, ge=0.0, le=0.5)
    min_plate_width: int = Field(default=20, ge=1)
    # How to size the network input for a per-vehicle plate search.
    #
    # "fixed" feeds every vehicle crop at `imgsz` regardless of the crop's own
    # size. That is the right default, and the reasoning is worth stating: a
    # plate occupies a roughly constant *fraction* of a vehicle (about a fifth
    # of its width), so letterboxing any vehicle crop to 320px puts the plate at
    # roughly 70px whether the crop came from a 4K frame or a 720p one. Scaling
    # the input with the crop — the obvious-seeming approach — makes cost grow
    # with source resolution while buying no extra plate detail.
    #
    # "proportional" scales the input with the crop, capped at `imgsz`. Kept so
    # the two can be compared rather than argued about.
    imgsz_strategy: Literal["fixed", "proportional"] = "fixed"
    # Two plate regions overlapping by at least this much are the same physical
    # plate found via two overlapping vehicle crops; one is kept.
    duplicate_iou: float = Field(default=0.55, ge=0.0, le=1.0)
    min_imgsz: int = Field(default=192, ge=64)
    threads: int = Field(default=0, ge=0)
    schedule: PlateScheduleConfig = Field(default_factory=PlateScheduleConfig)


class PreprocessConfig(BaseModel):
    """Plate-crop conditioning before OCR."""

    # Each named variant is a different image-processing recipe. When more than
    # one is enabled the best-scoring OCR result wins, and the variant that won
    # is recorded — that record is how we learn which recipe actually helps.
    variants: list[str] = Field(default_factory=lambda: ["rectified", "upscaled"])
    target_height: int = Field(default=64, ge=16)
    max_upscale: float = Field(default=4.0, ge=1.0)
    clahe_clip: float = 2.0
    clahe_grid: int = 8
    pad_ratio: float = Field(default=0.08, ge=0.0, le=0.5)
    # Stop trying further variants once one yields a grammar-valid read above
    # this confidence. Selective inference again: the second recipe exists to
    # rescue crops the first failed on, so running it after a clean success is
    # spent for nothing. Set False in diagnostic mode to compare all variants
    # on every crop.
    stop_on_good_read: bool = True
    good_read_confidence: float = Field(default=0.80, ge=0.0, le=1.0)


class OcrConfig(BaseModel):
    engine: str = "rapidocr"
    min_confidence: float = Field(default=0.20, ge=0.0, le=1.0)
    # Below this many characters the read is discarded as noise.
    min_chars: int = Field(default=6, ge=1)
    max_chars: int = Field(default=13, ge=1)
    weights: str | None = None
    languages: list[str] = Field(default_factory=lambda: ["en"])
    allowlist: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    # OCR dominates frame cost. Past a few dozen reads a track's consensus has
    # long since converged, so this caps the spend without changing the answer.
    max_reads_per_track: int = Field(default=40, ge=1)
    # Stop reading a vehicle once its plate has clearly converged. OCR dominates
    # frame cost, and the twentieth identical read of a plate already agreed on
    # by twelve frames changes nothing. Set `stop_when_confident: false` to keep
    # reading every frame when studying how a plate converges.
    stop_when_confident: bool = True
    stop_min_agreeing: int = Field(default=6, ge=1)
    stop_min_confidence: float = Field(default=0.85, ge=0.0, le=1.0)
    # Skip OCR on crops too small to hold legible characters. A 10-character
    # plate 40px wide gives 4px per glyph, which no recogniser reads reliably —
    # attempting it costs time and produces noise that consensus must then
    # outvote. This is a cost/recall trade: sweep it rather than trusting it.
    min_crop_width: int = Field(default=52, ge=8)
    # PP-OCR's text-detection stage costs ~18x recognition alone and exists
    # only to rescue crops recognition cannot read on its own — chiefly two-row
    # plates. Measured on real footage, a permissive gate meant it fired on
    # every unreadable distant plate: 974 ms average OCR for zero successful
    # reads. It is now allowed only where it could genuinely help, and can be
    # switched off entirely to measure what it is worth.
    detection_fallback: bool = True
    # A single-row plate is about 4.5:1; a two-row plate about 2:1. Above this
    # the crop is a single row and detection cannot add anything.
    detection_fallback_max_aspect: float = Field(default=2.6, ge=1.0)
    # Two rows need enough pixels to actually resolve two rows.
    detection_fallback_min_height: int = Field(default=36, ge=8)
    crop_gate: CropGateConfig = Field(default_factory=CropGateConfig)


class ConsensusConfig(BaseModel):
    """Multi-frame aggregation."""

    min_reads: int = Field(default=1, ge=1)
    # Reads whose weight is below this fraction of the best read are ignored;
    # they are still stored and reported, just not voted with.
    relative_weight_floor: float = Field(default=0.15, ge=0.0, le=1.0)
    # Runner-up within this margin of the winner ⇒ flagged ambiguous rather
    # than silently reported as fact.
    ambiguity_margin: float = Field(default=0.15, ge=0.0, le=1.0)
    # Which national plate formats count as valid. The platform deploys with
    # ("IN",); evaluation footage from elsewhere needs its own format accepted,
    # otherwise a perfectly correct read is graded "invalid" and — worse — the
    # repair step tries to bend it into an Indian plate.
    plate_regions: tuple[str, ...] = ("IN",)
    apply_confusion_correction: bool = True
    require_grammar: bool = False   # when True, invalid strings can't win
    emit_min_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class StreamConfig(BaseModel):
    """Live-stream behaviour.

    These govern what a worker does while a camera keeps producing frames, which
    is a different problem from processing a file: events must be emitted before
    the vehicle leaves, and memory must not grow with uptime.
    """

    # Retire a track this long after it was last seen, then emit its final
    # event and release its evidence.
    retire_after_s: float = Field(default=1.5, ge=0.1)
    # Re-emit a provisional event only when confidence improves by at least
    # this much. Without it an unchanged reading would be resent every frame.
    observed_confidence_delta: float = Field(default=0.10, ge=0.0, le=1.0)
    # Bound on provisional events per vehicle, so one long-dwelling vehicle
    # cannot dominate the event stream.
    max_observed_per_track: int = Field(default=4, ge=0)


class OutputConfig(BaseModel):
    annotated_video: bool = True
    annotated_fps: float | None = None       # defaults to source fps
    save_plate_crops: bool = True
    save_vehicle_crops: bool = True
    save_all_read_crops: bool = True         # every OCR attempt, not just winners
    max_crops_per_track: int = Field(default=25, ge=1)
    html_report: bool = True
    events_jsonl: bool = True
    keyframes: bool = False
    draw_trails: bool = True


class HardCaseConfig(BaseModel):
    """What counts as a case worth looking at by hand / training on."""

    enabled: bool = True
    low_ocr_confidence: float = 0.55
    min_track_frames_without_plate: int = 8   # long look at a car, no plate found
    flag_grammar_invalid: bool = True
    flag_ambiguous: bool = True
    flag_disagreement_above: float = 0.35


class RunConfig(BaseModel):
    """A complete, reproducible experiment definition."""

    name: str = "default"
    description: str = ""
    source: SourceConfig = Field(default_factory=SourceConfig)
    detector: DetectorConfig = Field(default_factory=DetectorConfig)
    tracker: TrackerConfig = Field(default_factory=TrackerConfig)
    plate: PlateDetectorConfig = Field(default_factory=PlateDetectorConfig)
    preprocess: PreprocessConfig = Field(default_factory=PreprocessConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)
    consensus: ConsensusConfig = Field(default_factory=ConsensusConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    hard_cases: HardCaseConfig = Field(default_factory=HardCaseConfig)

    @model_validator(mode="after")
    def _check_plate_classes(self) -> RunConfig:
        unknown = set(self.detector.plate_bearing_classes) - set(self.detector.classes)
        if unknown:
            raise ValueError(
                f"plate_bearing_classes {sorted(unknown)} are not in detector.classes; "
                "they would never be detected, so no plate could ever be found on them"
            )
        return self

    # ── loading ──
    @classmethod
    def load(cls, path: str | Path | None) -> RunConfig:
        if path is None:
            return cls()
        p = Path(path)
        if not p.exists():
            # Allow bare names and subdirectory names:
            #   --config fast                     → configs/fast.yaml
            #   --config experiments/no_consensus → configs/experiments/no_consensus.yaml
            candidates = [
                REPO_ROOT / "configs" / f"{p}.yaml",
                REPO_ROOT / "configs" / f"{p.name}.yaml",
            ]
            found = next((c for c in candidates if c.exists()), None)
            if found is None:
                available = sorted(
                    str(c.relative_to(REPO_ROOT / "configs")).removesuffix(".yaml")
                    for c in (REPO_ROOT / "configs").rglob("*.yaml")
                )
                raise FileNotFoundError(
                    f"config not found: {path}. Available: {available}"
                )
            p = found
        data = yaml.safe_load(p.read_text()) or {}
        base = data.pop("extends", None)
        if base:
            parent = cls.load((p.parent / base).resolve())
            data = _deep_merge(parent.model_dump(), data)
        cfg = cls.model_validate(data)
        if cfg.name == "default":
            cfg.name = p.stem
        return cfg

    def with_overrides(self, overrides: dict[str, Any]) -> RunConfig:
        """Apply `--set detector.confidence=0.4` style overrides."""
        data = self.model_dump()
        for dotted, value in overrides.items():
            node: Any = data
            parts = dotted.split(".")
            for key in parts[:-1]:
                if key not in node:
                    raise KeyError(f"unknown config section: {dotted}")
                node = node[key]
            if parts[-1] not in node:
                raise KeyError(f"unknown config key: {dotted}")
            node[parts[-1]] = value
        return RunConfig.model_validate(data)

    def resolve_path(self, value: str) -> Path:
        """Model paths are relative to the lab root unless absolute."""
        p = Path(value)
        return p if p.is_absolute() else (REPO_ROOT / p)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def parse_override(text: str) -> tuple[str, Any]:
    """`detector.confidence=0.4` → ("detector.confidence", 0.4)."""
    if "=" not in text:
        raise ValueError(f"override must be key=value, got: {text}")
    key, _, raw = text.partition("=")
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError:
        value = raw
    return key.strip(), value
