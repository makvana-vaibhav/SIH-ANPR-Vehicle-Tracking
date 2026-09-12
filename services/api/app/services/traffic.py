"""Traffic metrics: the arithmetic, with no database in sight.

Every function here is pure. Nothing imports FastAPI, SQLAlchemy or the
settings object; the router gathers rows, calls these, and shapes the
response. That is not tidiness for its own sake — it is what makes the
formulas *runnable*. The API test suite needs a live Postgres with PostGIS
and TimescaleDB, so on a machine without Docker none of it executes and a
congestion threshold could be wrong for weeks without anyone noticing. A
stdlib-only module can be imported and checked anywhere, which is the same
reasoning P10's forecasting used when it moved `_fit_line`/`_backtest` out
of the query path.

## What these metrics are made of

The platform stores **one row per vehicle**, not one per frame — the AI
worker emits `vehicle.completed` when a track retires and that is what
lands in `detections`. Every count here is therefore already a count of
distinct vehicles; there is no frame-level double counting to undo, and no
`DISTINCT` needed to avoid it.

Two columns make the rest possible, and both were being discarded before
this feature existed (migration 0007):

* ``dwell_s`` — how long the vehicle was in view. Density is occupancy over
  these intervals; a queue is several long dwells overlapping.
* ``direction`` / ``motion_px`` — whether it actually went anywhere.
  ``"stationary"`` over a long dwell is what a queue or an obstruction looks
  like from a record with no per-frame positions.

## What they are not

Density is **vehicles in a camera's view**, not vehicles per kilometre: no
camera here is calibrated, so there is no honest way to convert one into the
other. Speed comes from multi-camera timing, where the distance between two
cameras is a surveyed fact rather than a guess — it is never derived from a
single view. Both are labelled estimates everywhere they surface.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Literal

# ── Vocabulary ────────────────────────────────────────────────────────────

#: Ordered from free-flowing to worst. Ordering matters: the UI sorts by it
#: and "top congested roads" means the tail of this scale.
CongestionLevel = Literal["free", "moderate", "heavy", "severe"]

CONGESTION_ORDER: tuple[CongestionLevel, ...] = ("free", "moderate", "heavy", "severe")

#: Mirrors `DataStatus` in `app/schemas/analytics.py`. Repeated as a literal
#: rather than imported so this module stays stdlib-only; the schema module is
#: the one that has to agree, and its own tests assert the values.
DataStatus = Literal["ok", "insufficient_history", "insufficient_data"]

#: The vehicle classes the detector reports and the dashboard breaks down by.
#: Same tuple as `app/routers/detections.py::VEHICLE_CLASSES`.
VEHICLE_CLASSES: tuple[str, ...] = ("car", "motorcycle", "bus", "truck")

#: The worker's image-space direction labels (`ailab.stream.events._motion_block`).
#: "unknown" never reaches the database — the consumer stores NULL for it — so
#: it is absent here deliberately.
STATIONARY = "stationary"


@dataclass(frozen=True, slots=True)
class TrafficThresholds:
    """Every tunable in one place, so none of them hide in a function body.

    The router builds this from `Settings`; tests build it literally. Defaults
    are reasoned, not measured — no calibrated ground truth for Ahmedabad
    traffic exists in this repo, and inventing one would be worse than saying
    so. They are chosen to behave sensibly across the whole range (see
    `tests/test_traffic.py`, which pins the boundaries) and are expected to be
    tuned against a real deployment.
    """

    #: Vehicles dwelling at once before it counts as a queue rather than a
    #: busy moment.
    queue_min_vehicles: int = 4
    #: ...and how long each of them has to have been sitting there.
    queue_dwell_seconds: float = 25.0
    #: Consecutive buckets the condition must hold for. One bucket is a
    #: coincidence; the word "sustained" has to mean something.
    queue_min_sustained_buckets: int = 2

    #: A single vehicle stationary this long is worth a human look. Called a
    #: *possible obstruction* and nothing stronger: the system can see that a
    #: vehicle has not moved, and cannot see why.
    obstruction_dwell_seconds: float = 90.0

    #: Vehicles in view that represents a saturated camera. Occupancy is
    #: normalised against this before it can be combined with speed and travel
    #: time, which are already fractions.
    occupancy_reference: int = 12

    #: Congestion score boundaries. `score < moderate` is free flow.
    congestion_moderate: float = 0.30
    congestion_heavy: float = 0.55
    congestion_severe: float = 0.75

    #: How much each signal counts toward the score. Weights are renormalised
    #: over whichever signals are actually available, so a corridor with no
    #: usable speed figure is still scored on the rest rather than scored
    #: wrongly — or, worse, scored as free-flowing because a number was
    #: missing.
    weight_occupancy: float = 0.40
    weight_speed: float = 0.35
    weight_travel_time: float = 0.25


@dataclass(frozen=True, slots=True)
class Factor:
    """Why a verdict came out the way it did.

    Same shape as `alerts.reasons` (migration 0005): `factor` is a stable slug
    to branch on, `detail` is the sentence an operator reads. CLAUDE.md's
    explainability rule — a level with no reasons is a bug.
    """

    factor: str
    detail: str


@dataclass(frozen=True, slots=True)
class CongestionAssessment:
    level: CongestionLevel | None
    score: float | None
    status: DataStatus
    factors: tuple[Factor, ...] = ()


@dataclass(frozen=True, slots=True)
class QueueAssessment:
    present: bool
    #: Vehicles, not metres. Nothing here knows how long a car is.
    length_vehicles: int
    sustained_buckets: int
    status: DataStatus


# ── Counting and rates ────────────────────────────────────────────────────


def rate_per_minute(count: int, window_seconds: float) -> float | None:
    """Vehicles per minute. None for a window of zero, never a division error."""
    if window_seconds <= 0:
        return None
    return count / (window_seconds / 60.0)


def rate_per_hour(count: int, window_seconds: float) -> float | None:
    if window_seconds <= 0:
        return None
    return count / (window_seconds / 3600.0)


def counts_by_type(types: list[str | None]) -> dict[str, int]:
    """Break a window's vehicles down by class.

    Every known class is present in the result even at zero — a dashboard
    panel that silently drops "bus" when none passed reads as a broken panel
    rather than as quiet traffic. Anything the detector reported that is not a
    known class is counted under `other`, never discarded.
    """
    counts = dict.fromkeys(VEHICLE_CLASSES, 0)
    counts["other"] = 0
    for raw in types:
        key = (raw or "").strip().lower()
        counts[key if key in VEHICLE_CLASSES else "other"] += 1
    return counts


# ── Density ───────────────────────────────────────────────────────────────


def peak_occupancy(intervals: list[tuple[float, float]]) -> int:
    """Most vehicles in view at once, over a list of (entered, left) times.

    A sweep over interval endpoints. Exits are processed before entries at the
    same instant, so a vehicle leaving exactly as another arrives is a handover
    rather than a moment of double occupancy — the alternative inflates every
    figure on a busy camera by one.

    Seconds are whatever clock the caller used, as long as it is consistent;
    the router passes epoch seconds.
    """
    if not intervals:
        return 0

    # (time, delta) with -1 sorting before +1 at equal times.
    points: list[tuple[float, int]] = []
    for start, end in intervals:
        if end < start:
            start, end = end, start
        points.append((start, 1))
        points.append((end, -1))
    points.sort(key=lambda p: (p[0], p[1]))

    current = 0
    peak = 0
    for _at, delta in points:
        current += delta
        peak = max(peak, current)
    return peak


def occupancy_at(intervals: list[tuple[float, float]], at: float) -> int:
    """How many vehicles were in view at one instant."""
    return sum(1 for start, end in intervals if start <= at <= end)


# ── Congestion ────────────────────────────────────────────────────────────


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def congestion_score(
    *,
    peak_vehicles: int | None,
    speed_ratio: float | None,
    travel_time_delta_pct: float | None,
    thresholds: TrafficThresholds,
) -> tuple[float | None, tuple[Factor, ...]]:
    """Combine whichever of the three signals are available into 0..1.

    Each is normalised so that 1.0 means "as congested as this signal can
    say":

        occupancy    peak vehicles in view / `occupancy_reference`
        speed        1 - (current speed / free-flow speed)
        travel time  delta over baseline, where +100% (a doubling) is 1.0

    **Weights are renormalised over the signals actually present.** On this
    demo fleet most corridors have no usable speed figure at all — the
    correlator excludes replayed-clip legs as physically implausible — and
    scoring a missing signal as zero would report free-flowing traffic
    precisely where the platform knows least. Returning a score over two
    signals and saying so is honest; returning a confident wrong one is not.
    """
    components: list[tuple[float, float]] = []  # (normalised, weight)
    factors: list[Factor] = []

    if peak_vehicles is not None:
        occupancy = _clamp01(peak_vehicles / max(1, thresholds.occupancy_reference))
        components.append((occupancy, thresholds.weight_occupancy))
        factors.append(
            Factor(
                "occupancy",
                f"{peak_vehicles} vehicles in view at once "
                f"(saturation reference {thresholds.occupancy_reference})",
            )
        )

    if speed_ratio is not None:
        slowdown = _clamp01(1.0 - speed_ratio)
        components.append((slowdown, thresholds.weight_speed))
        factors.append(
            Factor(
                "speed",
                f"moving at {speed_ratio * 100:.0f}% of the free-flow estimate",
            )
        )

    if travel_time_delta_pct is not None:
        delay = _clamp01(travel_time_delta_pct / 100.0)
        components.append((delay, thresholds.weight_travel_time))
        factors.append(
            Factor(
                "travel_time",
                f"journeys taking {travel_time_delta_pct:+.0f}% against the baseline",
            )
        )

    if not components:
        return None, ()

    total_weight = sum(weight for _value, weight in components)
    score = sum(value * weight for value, weight in components) / total_weight
    return score, tuple(factors)


def classify_congestion(score: float, thresholds: TrafficThresholds) -> CongestionLevel:
    """Turn the score into the word an operator reads."""
    if score >= thresholds.congestion_severe:
        return "severe"
    if score >= thresholds.congestion_heavy:
        return "heavy"
    if score >= thresholds.congestion_moderate:
        return "moderate"
    return "free"


def assess_congestion(
    *,
    peak_vehicles: int | None,
    speed_ratio: float | None,
    travel_time_delta_pct: float | None,
    thresholds: TrafficThresholds,
) -> CongestionAssessment:
    """Score, classify and explain — never a level without its reasons."""
    score, factors = congestion_score(
        peak_vehicles=peak_vehicles,
        speed_ratio=speed_ratio,
        travel_time_delta_pct=travel_time_delta_pct,
        thresholds=thresholds,
    )
    if score is None:
        return CongestionAssessment(
            level=None,
            score=None,
            status="insufficient_data",
            factors=(
                Factor(
                    "no_signal",
                    "No occupancy, speed or travel-time figure for this window",
                ),
            ),
        )
    return CongestionAssessment(
        level=classify_congestion(score, thresholds),
        score=round(score, 4),
        status="ok",
        factors=factors,
    )


# ── Queues and obstructions ───────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class QueueSample:
    """One time bucket's worth of evidence for the queue detector."""

    at: float
    stationary_vehicles: int
    median_dwell_s: float


def detect_queue(
    samples: list[QueueSample], thresholds: TrafficThresholds
) -> QueueAssessment:
    """A queue is accumulation that *persists*.

    Both conditions have to hold in the same bucket — enough vehicles, each
    sitting long enough — and then hold again in consecutive buckets.
    Requiring persistence is what separates a queue from a red light's normal
    rhythm, or from four vehicles that happened to be in view together.

    Two separate maxima, deliberately not conflated: `sustained_buckets` is the
    **longest** qualifying run, `length_vehicles` is the **worst** queue seen
    in any qualifying bucket. A window where a queue formed, cleared and formed
    again reports the length of the bigger one — taking the length from
    whichever run happened to be longest would report the earlier, smaller
    queue whenever the two ran equally long, which is a real case and reads as
    the panel under-reporting.
    """
    if not samples:
        return QueueAssessment(False, 0, 0, "insufficient_data")

    longest_run = 0
    peak_vehicles = 0
    run = 0

    for sample in samples:
        qualifies = (
            sample.stationary_vehicles >= thresholds.queue_min_vehicles
            and sample.median_dwell_s >= thresholds.queue_dwell_seconds
        )
        if qualifies:
            run += 1
            longest_run = max(longest_run, run)
            peak_vehicles = max(peak_vehicles, sample.stationary_vehicles)
        else:
            run = 0

    present = longest_run >= thresholds.queue_min_sustained_buckets
    return QueueAssessment(
        present=present,
        length_vehicles=peak_vehicles if present else 0,
        sustained_buckets=longest_run if present else 0,
        status="ok",
    )


def is_possible_obstruction(
    *, direction: str | None, dwell_s: float | None, thresholds: TrafficThresholds
) -> bool:
    """Has this vehicle sat still long enough to be worth a human look?

    **"Possible obstruction", never "accident" and never "suspicious".** The
    platform can see that a vehicle did not move; it cannot see a breakdown, a
    delivery, a police stop or a driver reading a map, and every one of those
    produces this exact signal. Naming the observation rather than the cause is
    CLAUDE.md §1's language rule, and it is also the difference between an
    operator trusting this panel and learning to ignore it.

    `direction` is NULL for anything the worker could not measure — a
    single-sighting detector flicker — and that is deliberately not treated as
    stationary. Otherwise tracker noise would invent stopped vehicles.
    """
    if direction != STATIONARY or dwell_s is None:
        return False
    return dwell_s >= thresholds.obstruction_dwell_seconds


# ── Speed helpers ─────────────────────────────────────────────────────────


def speed_ratio(current_kmph: float | None, free_flow_kmph: float | None) -> float | None:
    """Current speed as a fraction of free-flow. None when either is unknown.

    Free-flow is the reference this corridor manages when empty, so a ratio
    near 1.0 means unimpeded and 0.3 means crawling. Guarded against a zero or
    negative reference, which would otherwise produce an infinite slowdown out
    of a data error.
    """
    if current_kmph is None or free_flow_kmph is None or free_flow_kmph <= 0:
        return None
    return current_kmph / free_flow_kmph


def median_or_none(values: list[float]) -> float | None:
    """Median of whatever is present, or None for nothing.

    Used rather than a mean because a single implausible leg — the replayed
    demo clips produce plenty — drags a mean somewhere no vehicle went.
    """
    usable = [v for v in values if v is not None]
    return statistics.median(usable) if usable else None
