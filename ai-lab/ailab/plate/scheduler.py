"""When to look for a plate on a tracked vehicle.

The naive pipeline searches every tracked vehicle on every frame. Profiling on
4K footage showed that is where the time goes, and most of it buys nothing: a
car crossing frame over 90 frames is searched 90 times, and frames 41 and 42
show the same vehicle at the same scale in the same light. The second search
cannot produce a materially different observation, so it is spent for nothing.

But simply searching less often is the wrong fix. Multi-frame consensus depends
on having several genuinely *different* looks at a plate, and dropping to every
tenth frame indiscriminately would take the good frames away along with the
redundant ones. What we want is selective inference: search when the view has
changed enough to be new evidence, and skip when it has not.

The rules, in order of application:

  converged      the plate is already settled — stop looking
  too_small      the vehicle is too small to carry a legible plate
  cooldown       searched very recently; nothing can have changed yet
  first_look     never searched — always look
  changed_view   the vehicle grew, shrank or moved materially
  periodic       nothing changed, but it has been a while — look anyway
  backoff        repeated searches found nothing; look less often
  unchanged      skip

Every skip is counted by reason and reported, so the decision to spend less time
is visible and auditable rather than a silent quality change.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from ailab.config import CropGateConfig, PlateScheduleConfig
from ailab.types import BBox, CropQuality


@dataclass(slots=True)
class TrackPlateState:
    """What we know about plate-searching one vehicle."""

    last_search_frame: int | None = None
    last_bbox: BBox | None = None
    searches: int = 0
    hits: int = 0
    consecutive_misses: int = 0

    @property
    def ever_found(self) -> bool:
        return self.hits > 0


@dataclass(slots=True)
class PlateScheduler:
    """Decides which tracked vehicles get a plate search on this frame."""

    config: PlateScheduleConfig
    states: dict[int, TrackPlateState] = field(default_factory=dict)
    reasons: Counter[str] = field(default_factory=Counter)

    def should_search(
        self, track_id: int, bbox: BBox, frame_index: int, converged: bool
    ) -> bool:
        """True when this vehicle deserves a plate search on this frame."""
        if not self.config.enabled:
            self.reasons["scheduler_disabled"] += 1
            return True

        state = self.states.setdefault(track_id, TrackPlateState())

        if converged:
            self.reasons["skip_converged"] += 1
            return False

        if bbox.width < self.config.min_vehicle_width:
            self.reasons["skip_too_small"] += 1
            return False

        if self.config.max_searches_per_track and state.searches >= self.config.max_searches_per_track:
            self.reasons["skip_search_budget"] += 1
            return False

        if state.last_search_frame is None:
            self.reasons["search_first_look"] += 1
            return True

        elapsed = frame_index - state.last_search_frame

        # A vehicle we have never read is not a vehicle we can afford to skip.
        # Short-lived tracks would otherwise get exactly one look before the
        # cooldown silences them for the rest of their brief life.
        if (
            not state.ever_found
            and state.searches < self.config.min_attempts_before_cooldown
        ):
            self.reasons["search_not_yet_read"] += 1
            return True

        if elapsed < self.config.min_interval:
            self.reasons["skip_cooldown"] += 1
            return False

        # A vehicle we have looked at several times without ever finding a plate
        # is probably facing away, or its plate is obscured. Keep checking, but
        # progressively less often, so it stops competing with vehicles whose
        # plates are actually visible.
        interval = self.config.max_interval
        if self.config.miss_backoff and not state.ever_found:
            interval = min(
                self.config.max_interval * self.config.backoff_factor,
                self.config.max_interval * (1 + state.consecutive_misses),
            )

        if self._view_changed(state.last_bbox, bbox):
            self.reasons["search_changed_view"] += 1
            return True

        if elapsed >= interval:
            self.reasons["search_periodic"] += 1
            return True

        self.reasons["skip_unchanged"] += 1
        return False

    def _view_changed(self, previous: BBox | None, current: BBox) -> bool:
        """Has the vehicle changed enough to give a genuinely new look at it?

        Two things make a view new: the vehicle got closer or further (its box
        changed scale), or it moved across the frame far enough that the plate
        is seen at a different angle. Either yields a crop the OCR has not
        effectively already read.
        """
        if previous is None:
            return True

        previous_area = max(1.0, previous.area)
        scale_change = abs(current.area - previous_area) / previous_area
        if scale_change >= self.config.min_scale_change:
            return True

        reference = max(1.0, min(current.width, current.height))
        moved = math.hypot(current.cx - previous.cx, current.cy - previous.cy)
        return (moved / reference) >= self.config.min_move_fraction

    def record(self, track_id: int, bbox: BBox, frame_index: int, found: bool) -> None:
        """Note the outcome of a search, so the next decision can use it."""
        state = self.states.setdefault(track_id, TrackPlateState())
        state.last_search_frame = frame_index
        state.last_bbox = bbox
        state.searches += 1
        if found:
            state.hits += 1
            state.consecutive_misses = 0
        else:
            state.consecutive_misses += 1

    def stats(self) -> dict[str, Any]:
        """Why searches happened or did not — the audit trail for the speedup."""
        searched = sum(v for k, v in self.reasons.items() if k.startswith("search"))
        skipped = sum(v for k, v in self.reasons.items() if k.startswith("skip"))
        considered = searched + skipped
        return {
            "considered": considered,
            "searched": searched,
            "skipped": skipped,
            "search_rate": round(searched / considered, 4) if considered else 0.0,
            "by_reason": dict(self.reasons.most_common()),
            "tracks_tracked": len(self.states),
            "tracks_with_a_plate": sum(1 for s in self.states.values() if s.ever_found),
        }


# ─────────────────────────────────────────────────────────────────────
# Crop-level gate
# ─────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class TrackCropState:
    """What we have already read for one vehicle, and how good it was."""

    reads: int = 0
    best_confidence: float = 0.0
    best_sharpness: float = 0.0
    best_width: int = 0
    last_signature: np.ndarray | None = None
    ever_readable: bool = False


def signature(crop: np.ndarray, side: int = 12) -> np.ndarray:
    """A tiny perceptual fingerprint of a plate crop.

    A difference hash: downsample hard, then record whether each pixel is
    brighter than the one to its right. That throws away absolute brightness
    and scale — which is what we want, because the question is "is this a
    different view of the plate", not "is this a different JPEG". Comparing two
    of these costs microseconds against the ~75 ms an OCR call costs, so the
    gate pays for itself if it rejects even one read in a thousand.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    small = cv2.resize(gray, (side + 1, side), interpolation=cv2.INTER_AREA)
    return (small[:, 1:] > small[:, :-1]).flatten()


def signature_distance(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """Fraction of bits that differ. 0 = identical view, 1 = unrelated."""
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    return float(np.count_nonzero(a != b)) / float(a.size)


@dataclass(slots=True)
class CropGate:
    """Decides whether a plate crop is worth spending OCR on.

    The plate scheduler decides whether to *look* for a plate; this decides
    whether the plate it found is new information. They are separate questions:
    a vehicle can move enough to justify a fresh search and still yield a crop
    that is pixel-for-pixel what we read four frames ago.

    A crop earns an OCR call when it is genuinely new evidence:

      first_read          nothing read for this vehicle yet
      sharper             materially sharper than the best crop so far
      larger              materially bigger than the best crop so far
      different_view      perceptually different from the last crop read
      low_confidence      what we have is weak; keep trying
      unreadable_so_far   nothing legible yet; every crop is worth a go
      periodic            enough reads apart to be worth a refresh

    and is skipped as `duplicate_view` otherwise. Skips are counted by reason,
    so the saving is auditable and nothing disappears silently.
    """

    config: CropGateConfig
    states: dict[int, TrackCropState] = field(default_factory=dict)
    reasons: Counter[str] = field(default_factory=Counter)

    def should_read(
        self, track_id: int, crop: np.ndarray, quality: CropQuality
    ) -> tuple[bool, np.ndarray | None]:
        """True when this crop is worth an OCR call. Returns its signature too."""
        if not self.config.enabled:
            self.reasons["gate_disabled"] += 1
            return True, None

        state = self.states.setdefault(track_id, TrackCropState())

        if state.reads == 0:
            self.reasons["read_first"] += 1
            return True, signature(crop)

        # A vehicle whose plate has never been legible deserves every attempt:
        # the whole point is that the next frame may be the one that works.
        if not state.ever_readable:
            self.reasons["read_unreadable_so_far"] += 1
            return True, signature(crop)

        if quality.sharpness > state.best_sharpness * (1.0 + self.config.min_sharpness_gain):
            self.reasons["read_sharper"] += 1
            return True, signature(crop)

        if quality.width > state.best_width * (1.0 + self.config.min_width_gain):
            self.reasons["read_larger"] += 1
            return True, signature(crop)

        if state.best_confidence < self.config.low_confidence:
            self.reasons["read_low_confidence"] += 1
            return True, signature(crop)

        current = signature(crop)
        if signature_distance(state.last_signature, current) >= self.config.min_signature_distance:
            self.reasons["read_different_view"] += 1
            return True, current

        self.reasons["skip_duplicate_view"] += 1
        return False, current

    def record(
        self,
        track_id: int,
        quality: CropQuality,
        confidence: float,
        readable: bool,
        crop_signature: np.ndarray | None,
    ) -> None:
        """Note what an OCR call produced, so the next decision can use it."""
        state = self.states.setdefault(track_id, TrackCropState())
        state.reads += 1
        state.best_confidence = max(state.best_confidence, confidence)
        state.best_sharpness = max(state.best_sharpness, quality.sharpness)
        state.best_width = max(state.best_width, quality.width)
        if crop_signature is not None:
            state.last_signature = crop_signature
        if readable:
            state.ever_readable = True

    def stats(self) -> dict[str, Any]:
        read = sum(v for k, v in self.reasons.items() if k.startswith("read"))
        skipped = sum(v for k, v in self.reasons.items() if k.startswith("skip"))
        considered = read + skipped
        return {
            "considered": considered,
            "read": read,
            "skipped": skipped,
            "read_rate": round(read / considered, 4) if considered else 0.0,
            "by_reason": dict(self.reasons.most_common()),
        }
