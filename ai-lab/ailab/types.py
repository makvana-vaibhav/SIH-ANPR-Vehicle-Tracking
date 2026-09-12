"""Core data types for the AI lab.

Everything the pipeline learns about a frame, an object, a plate or a vehicle
lands in one of these. They are deliberately verbose: the point of this module
is that a human can inspect *why* the pipeline reached a conclusion, so no
intermediate value is thrown away just because the final answer doesn't need it.

Coordinates are absolute pixels in the source frame unless a field name says
otherwise. Times are seconds from the start of the media (`t_s`) plus, where a
wall-clock anchor is known, timezone-aware UTC.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ─────────────────────────────────────────────────────────────────────
# Geometry
# ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class BBox:
    """Axis-aligned box in absolute source-frame pixels."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height > 0 else 0.0

    def as_xyxy(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    def as_int(self) -> tuple[int, int, int, int]:
        return (int(round(self.x1)), int(round(self.y1)), int(round(self.x2)), int(round(self.y2)))

    def clip(self, width: int, height: int) -> BBox:
        return BBox(
            max(0.0, min(self.x1, width - 1.0)),
            max(0.0, min(self.y1, height - 1.0)),
            max(0.0, min(self.x2, float(width))),
            max(0.0, min(self.y2, float(height))),
        )

    def expand(self, ratio: float, width: int, height: int) -> BBox:
        """Grow the box by `ratio` on every side, clipped to the frame.

        Plate detectors crop tightly; OCR wants a little quiet space around the
        glyphs, and the perspective rectifier needs the plate border visible to
        find its corners.
        """
        dx = self.width * ratio
        dy = self.height * ratio
        return BBox(self.x1 - dx, self.y1 - dy, self.x2 + dx, self.y2 + dy).clip(width, height)

    def iou(self, other: BBox) -> float:
        ix1, iy1 = max(self.x1, other.x1), max(self.y1, other.y1)
        ix2, iy2 = min(self.x2, other.x2), min(self.y2, other.y2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    def contains_point(self, x: float, y: float) -> bool:
        return self.x1 <= x <= self.x2 and self.y1 <= y <= self.y2

    def shifted(self, dx: float, dy: float) -> BBox:
        return BBox(self.x1 + dx, self.y1 + dy, self.x2 + dx, self.y2 + dy)

    def to_dict(self) -> dict[str, float]:
        return {
            "x1": round(self.x1, 2), "y1": round(self.y1, 2),
            "x2": round(self.x2, 2), "y2": round(self.y2, 2),
            "w": round(self.width, 2), "h": round(self.height, 2),
        }


# ─────────────────────────────────────────────────────────────────────
# Frames
# ─────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Frame:
    """One decoded image plus where it came from in time."""

    index: int              # index in the source, counting every frame
    t_s: float              # seconds from media start
    image: Any              # np.ndarray BGR (H, W, 3)
    source_name: str
    wall_time: datetime | None = None   # tz-aware UTC when an anchor is known

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    @property
    def width(self) -> int:
        return int(self.image.shape[1])


# ─────────────────────────────────────────────────────────────────────
# Detection / tracking
# ─────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Detection:
    """One object found by the object detector in one frame."""

    bbox: BBox
    confidence: float
    class_id: int
    class_name: str
    frame_index: int
    t_s: float
    track_id: int | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "frame_index": self.frame_index,
            "t_s": round(self.t_s, 3),
            "track_id": self.track_id if self.track_id is not None else "",
            "class_name": self.class_name,
            "class_id": self.class_id,
            "confidence": round(self.confidence, 4),
            **{f"bbox_{k}": v for k, v in self.bbox.to_dict().items()},
        }


@dataclass(slots=True)
class TrackObservation:
    """A single frame's worth of a track's life."""

    frame_index: int
    t_s: float
    bbox: BBox
    confidence: float
    detected: bool          # False when the box came from Kalman prediction only


@dataclass(slots=True)
class Track:
    """One object followed across frames."""

    track_id: int
    class_name: str
    class_id: int
    observations: list[TrackObservation] = field(default_factory=list)
    plate_reads: list[PlateRead] = field(default_factory=list)
    #: Plate detections that went on to produce an accepted OCR read.
    #:
    #: **Not every plate the detector found.** A localised plate is discarded
    #: here if the crop was illegible, the crop gate judged it redundant,
    #: conditioning failed, or no OCR variant cleared the confidence and length
    #: floors. That is deliberate and load-bearing: `report/stats.py` counts
    #: this list as `plate_detections` and derives `frames_with_a_plate` and the
    #: plate-confidence distribution from it, and the accuracy harness reads
    #: those. Widening it to mean "located" would silently change what every
    #: one of those numbers says.
    plate_detections: list[PlateDetection] = field(default_factory=list)
    #: The most recent plate the detector **localised** on this vehicle, read or
    #: not, in full-frame pixels.
    #:
    #: This is the earliest moment at which anything can honestly be drawn over
    #: a plate: the detector has found one and says where it is, and OCR has not
    #: been attempted or has not yet agreed with itself. Only the latest is kept
    #: — an overlay needs somewhere current to draw, not a history — so this is
    #: O(1) per vehicle however long it dwells, and it is counted by nothing.
    plate_location: PlateDetection | None = None
    #: The vehicle box on the frame `plate_location` was found on.
    #:
    #: Kept because the plate box alone goes stale. The scheduler stops
    #: searching a vehicle once its plate has converged (`skip_converged`), so
    #: after that the last localisation is all there will ever be — and the
    #: vehicle carries on moving. Drawn as an absolute position it ends up
    #: behind the car, worst precisely at the point where the reading is
    #: settled and the box is drawn most firmly. With the vehicle box it was
    #: measured against, the plate's position *on the vehicle* is known, and
    #: that travels with the car.
    plate_location_vehicle: BBox | None = None
    result: PlateConsensus | None = None
    best_crop_path: str | None = None
    # Best sighting, maintained incrementally. Recomputing it by scanning every
    # observation each frame is O(n) per vehicle per frame — quadratic over a
    # long track, and invisible in a profile that only times model inference.
    _best_obs: TrackObservation | None = None
    _best_score: float = -1.0

    # ── lifetime ──
    @property
    def first_frame(self) -> int:
        return self.observations[0].frame_index if self.observations else -1

    @property
    def last_frame(self) -> int:
        return self.observations[-1].frame_index if self.observations else -1

    @property
    def first_seen_s(self) -> float:
        return self.observations[0].t_s if self.observations else 0.0

    @property
    def last_seen_s(self) -> float:
        return self.observations[-1].t_s if self.observations else 0.0

    @property
    def duration_s(self) -> float:
        return max(0.0, self.last_seen_s - self.first_seen_s)

    @property
    def frame_count(self) -> int:
        return len(self.observations)

    @property
    def detected_frame_count(self) -> int:
        return sum(1 for o in self.observations if o.detected)

    @property
    def mean_confidence(self) -> float:
        vals = [o.confidence for o in self.observations if o.detected]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def max_confidence(self) -> float:
        vals = [o.confidence for o in self.observations if o.detected]
        return max(vals) if vals else 0.0

    @staticmethod
    def observation_score(observation: TrackObservation) -> float:
        """How good a look at the vehicle this frame gives us."""
        return observation.bbox.area * (0.5 + 0.5 * observation.confidence)

    def observe(self, observation: TrackObservation) -> bool:
        """Record a sighting. Returns True when it is the best one so far."""
        self.observations.append(observation)
        if not observation.detected:
            return False
        score = self.observation_score(observation)
        if score > self._best_score:
            self._best_score = score
            self._best_obs = observation
            return True
        return False

    @property
    def best_observation(self) -> TrackObservation | None:
        """Largest, most confident sighting — the one worth saving as a crop."""
        return self._best_obs

    def travelled_px(self) -> float:
        """Total centre-point path length, in pixels.

        A stationary parked car and a car driving through frame produce very
        different values, which is how we tell "tracked for 12 s" apart from
        "sat in the corner of the frame for 12 s".
        """
        total = 0.0
        for a, b in zip(self.observations, self.observations[1:], strict=False):
            total += math.hypot(b.bbox.cx - a.bbox.cx, b.bbox.cy - a.bbox.cy)
        return total

    def net_motion(self) -> tuple[float, float, float] | None:
        """Straight-line displacement from first sighting to last: (dx, dy, distance).

        **None when it cannot be measured** — fewer than two detected sightings.
        Deliberately not `(0, 0, 0)`: a track the detector flickered on once is
        not the same fact as a vehicle watched for forty seconds that never
        moved, and the second is what a queue or an obstruction looks like.
        Collapsing them would let every one-frame detector artefact read as a
        stopped vehicle.

        Deliberately *net*, not `travelled_px` above. The two answer different
        questions and the difference is the whole point for traffic work: a
        vehicle stopped at a light still accumulates travelled_px from box
        jitter frame after frame, while its net displacement stays near zero.
        Only the second distinguishes "queued here for 40 s" from "drove
        through".

        Image coordinates, so `dy > 0` is *downward* in the frame. What that
        means in compass terms depends on where the camera points, which the
        worker has no idea about — the platform owns that, and converts using
        `cameras.heading_deg`. Nothing here pretends to know north.
        """
        detected = [o for o in self.observations if o.detected]
        if len(detected) < 2:
            return None
        first, last = detected[0].bbox, detected[-1].bbox
        dx = last.cx - first.cx
        dy = last.cy - first.cy
        return dx, dy, math.hypot(dx, dy)


# ─────────────────────────────────────────────────────────────────────
# Plates
# ─────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class PlateDetection:
    """A plate region located inside a vehicle box (or the whole frame)."""

    bbox: BBox                  # absolute frame coordinates
    confidence: float
    frame_index: int
    t_s: float
    track_id: int | None
    bbox_in_vehicle: BBox | None = None
    crop_path: str | None = None
    detector: str = ""

    def to_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "frame_index": self.frame_index,
            "t_s": round(self.t_s, 3),
            "track_id": self.track_id if self.track_id is not None else "",
            "plate_confidence": round(self.confidence, 4),
            "detector": self.detector,
            **{f"bbox_{k}": v for k, v in self.bbox.to_dict().items()},
            "crop_path": self.crop_path or "",
        }
        # The plate's position *within the vehicle crop* is what a YOLO
        # fine-tuning label needs. Losing it on reload would make `ailab mine`
        # export an empty detection dataset without saying why.
        vbox = self.bbox_in_vehicle.to_dict() if self.bbox_in_vehicle else {}
        for key in ("x1", "y1", "x2", "y2"):
            row[f"vbox_{key}"] = vbox.get(key, "")
        return row


@dataclass(slots=True)
class CropQuality:
    """Measured properties of a plate crop, used to weight its OCR vote.

    A large, sharp, well-exposed crop is worth more than a tiny blurry one even
    when the OCR engine claims equal confidence — engines are famously
    overconfident on garbage input.
    """

    sharpness: float        # variance of Laplacian
    brightness: float       # mean luminance 0-255
    contrast: float         # std of luminance
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height

    def weight(self) -> float:
        """Combine into a single 0..1 multiplier for consensus voting."""
        # Sharpness saturates: past ~300 variance the crop is "sharp enough".
        sharp = min(1.0, self.sharpness / 300.0)
        # Plates below ~40 px wide rarely survive OCR; 200 px is comfortable.
        size = min(1.0, max(0.0, (self.width - 25.0) / 175.0))
        # Penalise blown-out or crushed exposure.
        exposure = 1.0 - min(1.0, abs(self.brightness - 128.0) / 128.0) * 0.5
        contrast = min(1.0, self.contrast / 50.0)
        return max(0.05, 0.40 * sharp + 0.30 * size + 0.15 * exposure + 0.15 * contrast)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sharpness": round(self.sharpness, 1),
            "brightness": round(self.brightness, 1),
            "contrast": round(self.contrast, 1),
            "width": self.width,
            "height": self.height,
            "weight": round(self.weight(), 4),
        }


@dataclass(slots=True)
class PlateRead:
    """One OCR attempt on one plate crop in one frame.

    Every read is kept, including the wrong ones. `GJ03AB1284 @ 0.61` sitting
    next to three readings of `GJ03AB1234` is exactly the evidence needed to
    judge whether consensus is working.
    """

    text_raw: str
    text: str                       # normalised: uppercase, separators stripped
    ocr_confidence: float
    frame_index: int
    t_s: float
    track_id: int | None
    engine: str
    quality: CropQuality
    char_confidences: list[float] = field(default_factory=list)
    grammar_valid: bool = False
    grammar_note: str = ""
    crop_path: str | None = None
    plate_confidence: float = 0.0   # from the plate detector
    preprocess_variant: str = ""    # which rectification produced this read
    latency_ms: float = 0.0

    @property
    def vote_weight(self) -> float:
        """How much this read counts in consensus."""
        return self.ocr_confidence * self.quality.weight()

    def to_row(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id if self.track_id is not None else "",
            "frame_index": self.frame_index,
            "t_s": round(self.t_s, 3),
            "text_raw": self.text_raw,
            "text": self.text,
            "ocr_confidence": round(self.ocr_confidence, 4),
            "plate_confidence": round(self.plate_confidence, 4),
            "vote_weight": round(self.vote_weight, 4),
            "grammar_valid": self.grammar_valid,
            "grammar_note": self.grammar_note,
            "engine": self.engine,
            "preprocess_variant": self.preprocess_variant,
            "sharpness": round(self.quality.sharpness, 1),
            "crop_w": self.quality.width,
            "crop_h": self.quality.height,
            "latency_ms": round(self.latency_ms, 2),
            "crop_path": self.crop_path or "",
        }


@dataclass(slots=True)
class PlateCandidate:
    """One competing interpretation of a vehicle's plate."""

    text: str
    score: float            # normalised 0..1 share of the weighted vote
    support: int            # how many reads produced exactly this string
    grammar_valid: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "score": round(self.score, 4),
            "support": self.support,
            "grammar_valid": self.grammar_valid,
        }


@dataclass(slots=True)
class PlateConsensus:
    """The pipeline's final answer for one vehicle, plus its reasoning."""

    text: str
    confidence: float
    method: str                     # "char_vote" | "single_read" | "none"
    reads_total: int
    reads_agreeing: int
    candidates: list[PlateCandidate] = field(default_factory=list)
    char_confidences: list[float] = field(default_factory=list)
    grammar_valid: bool = False
    grammar_note: str = ""
    # Which format matched, decided once by consensus against the configured
    # regions. Consumers read it rather than re-validating, so a run cannot
    # report grammar_valid=true and format="invalid" in the same breath.
    grammar_format: str = "invalid"
    corrected_from: str | None = None   # pre-confusion-correction string
    ambiguous: bool = False             # runner-up too close to call
    disagreement: float = 0.0           # 0 = unanimous, 1 = total disagreement

    @property
    def agreement(self) -> float:
        return self.reads_agreeing / self.reads_total if self.reads_total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 4),
            "method": self.method,
            "reads_total": self.reads_total,
            "reads_agreeing": self.reads_agreeing,
            "agreement": round(self.agreement, 4),
            "grammar_valid": self.grammar_valid,
            "grammar_note": self.grammar_note,
            "grammar_format": self.grammar_format,
            "corrected_from": self.corrected_from,
            "ambiguous": self.ambiguous,
            "disagreement": round(self.disagreement, 4),
            "char_confidences": [round(c, 3) for c in self.char_confidences],
            "candidates": [c.to_dict() for c in self.candidates],
        }


# ─────────────────────────────────────────────────────────────────────
# Stage timing
# ─────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class StageTimer:
    """Wall-clock cost of each pipeline stage, accumulated across the run.

    This is what turns "the pipeline feels slow" into "OCR is 71% of frame
    time", which is the only useful input to an optimisation decision.
    """

    totals: dict[str, float] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, stage: str, seconds: float) -> None:
        self.totals[stage] = self.totals.get(stage, 0.0) + seconds
        self.counts[stage] = self.counts.get(stage, 0) + 1

    def mean_ms(self, stage: str) -> float:
        n = self.counts.get(stage, 0)
        return (self.totals.get(stage, 0.0) / n * 1000.0) if n else 0.0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        grand = sum(self.totals.values()) or 1.0
        for stage, total in sorted(self.totals.items(), key=lambda kv: -kv[1]):
            out[stage] = {
                "total_s": round(total, 3),
                "calls": self.counts[stage],
                "mean_ms": round(self.mean_ms(stage), 3),
                "share_pct": round(100.0 * total / grand, 1),
            }
        return out
