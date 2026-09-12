"""Does one leg of a journey look normal, given what the fleet has already seen?

## What this is, and what it replaces

`correlator.score_legs` already flags six things — `revisit`, `co_located`,
`impossible_simultaneous`, `implausible_speed`, `unobserved_gap`,
`heading_conflict` — but every one of them is a **physics check** run on a
single leg in isolation: could a vehicle have covered this distance in this
time. None of them compares a journey to anything the fleet has actually
observed. `AlertType.ANOMALY` has had zero producers because there was
nothing here to produce one.

This module is the data-driven half: for each newly-observed leg of a
journey, has *any* vehicle made this exact camera-to-camera transition
before, and if so, does this one's timing look like the others'. Two
factors, learned from `vehicle_tracks` — the journey history `route_history`
already persists (P2) — plus the physics filter's own `implausible_speed`
flag promoted to a named factor when it is the newest leg, per
ROADMAP.md's P5 definition ("unexpected camera sequence, travel time vs
baseline, unusual speed, impossible hop").

## Language discipline (CLAUDE.md §1)

Call it a **trajectory anomaly**. Nothing here decides that a vehicle,
plate or driver is suspicious — a rare transition is a fact about the
*route*, not an accusation about who drove it. `Reason.detail` is written to
be read by an operator who will decide what it means; it never says
"suspect", "criminal" or "wanted".

## The same trap as analytics.py, for the same reason

`vehicle_tracks` holds one row per journey *advance*, not per journey. Every
query here starts from `DISTINCT ON (plate_normalised) ... ORDER BY
created_at DESC` for the identical reason `app/routers/analytics.py`
documents: without it, a plate re-persisted 200 times would look like 200
vehicles having made the same trip, and "this transition is rare" would be
false for exactly the transitions where it matters most.

## Why only the newest hops are scored

Re-scoring a whole route on every snapshot would raise the same finding
again each time a journey is re-persisted — one journey producing an alert
storm instead of one alert. `route_history.snapshot` already knows exactly
which hops are new since the last snapshot; this module scores only those.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.correlator import Hop, Route

log = get_logger("api.anomaly")

#: How far back a transition's frequency and typical duration are learned
#: from. Matches the demo's practical history depth; long enough that a
#: transition made once a week still counts as "seen", short enough that a
#: road that changed character six months ago is not still setting the norm.
BASELINE_LOOKBACK = timedelta(days=30)

#: A transition made by fewer distinct vehicles than this, in the lookback,
#: is rare. Three, not one or two: a single prior vehicle could itself have
#: been the anomaly, and two is not enough to call anything a pattern.
RARE_TRANSITION_SAMPLES = 3

#: A transition needs at least this many prior *timed* samples before "how
#: long does this normally take" is a distribution rather than a guess.
#: Matches MIN_BASELINE_SAMPLES in analytics.py, deliberately — the same
#: honesty threshold applies to both.
MIN_DURATION_SAMPLES = 5

#: Current duration at or above this multiple of the historical median is
#: flagged as unusually slow; at or below its reciprocal, unusually fast.
DURATION_RATIO_HIGH = 2.0
DURATION_RATIO_LOW = 0.5


@dataclass(slots=True)
class Reason:
    """One factor behind a trajectory-anomaly alert."""

    factor: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"factor": self.factor, "detail": self.detail}


@dataclass(slots=True)
class TransitionBaseline:
    """What the fleet has observed for one camera-to-camera transition."""

    #: Distinct vehicles seen making this transition in the lookback —
    #: "how many times" would double-count a vehicle re-persisted mid-journey.
    plates: int
    #: Sorted elapsed-seconds of the *timed, plausible* legs behind it.
    durations: list[float]

    @property
    def median_seconds(self) -> float | None:
        if not self.durations:
            return None
        mid = len(self.durations) // 2
        if len(self.durations) % 2:
            return self.durations[mid]
        return (self.durations[mid - 1] + self.durations[mid]) / 2.0


# Same shape as `_LEGS_SQL` in app/routers/analytics.py, and the same reason:
# `lag()` over each journey's ordered hops supplies the *from* camera, which
# a hop only carries relative to the one before it.
_TRANSITION_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (plate_normalised) plate_normalised, hops
    FROM vehicle_tracks
    WHERE created_at >= :since
    ORDER BY plate_normalised, created_at DESC
),
legs AS (
    SELECT
        l.plate_normalised,
        lag(e.h ->> 'camera_code') OVER (
            PARTITION BY l.plate_normalised ORDER BY e.ord
        ) AS from_camera,
        e.h ->> 'camera_code'            AS to_camera,
        (e.h ->> 'elapsed_s')::float     AS elapsed_s,
        (e.h ->> 'plausible')::bool      AS plausible
    FROM latest l,
         jsonb_array_elements(l.hops -> 'hops') WITH ORDINALITY AS e(h, ord)
)
SELECT from_camera, to_camera, plate_normalised, elapsed_s
FROM legs
WHERE from_camera IS NOT NULL AND plausible
"""


async def _load_baselines(
    session: AsyncSession, since: datetime
) -> dict[tuple[str, str], TransitionBaseline]:
    """Every transition the fleet has made, from every plate's latest snapshot."""
    rows = (await session.execute(text(_TRANSITION_SQL), {"since": since})).all()

    plates: dict[tuple[str, str], set[str]] = {}
    durations: dict[tuple[str, str], list[float]] = {}
    for from_camera, to_camera, plate, elapsed_s in rows:
        key = (from_camera, to_camera)
        plates.setdefault(key, set()).add(plate)
        if elapsed_s is not None:
            durations.setdefault(key, []).append(float(elapsed_s))

    return {
        key: TransitionBaseline(plates=len(plates[key]), durations=sorted(durations.get(key, [])))
        for key in plates
    }


def _score_leg(
    from_camera: str, to_camera: str, hop: Hop, baseline: TransitionBaseline | None
) -> list[Reason]:
    label = f"{from_camera} → {to_camera}"

    if not hop.plausible:
        # The physics filter already found this leg impossible — the strongest
        # signal this module has, promoted to a named factor rather than left
        # as a flag only the route view shows.
        return [
            Reason(
                "impossible_hop",
                f"{label}: this leg is not physically plausible "
                f"({', '.join(hop.flags)}) — a cloned plate looks exactly like this",
            )
        ]

    reasons: list[Reason] = []
    seen = baseline.plates if baseline else 0
    if seen < RARE_TRANSITION_SAMPLES:
        reasons.append(
            Reason(
                "rare_transition",
                f"{label} has been made by {seen} vehicle(s) in the last "
                f"{BASELINE_LOOKBACK.days} days — fewer than the "
                f"{RARE_TRANSITION_SAMPLES} needed to call it an established route",
            )
        )

    median = baseline.median_seconds if baseline else None
    if (
        baseline is not None
        and len(baseline.durations) >= MIN_DURATION_SAMPLES
        and median
        and hop.elapsed_s is not None
    ):
        ratio = hop.elapsed_s / median
        if ratio >= DURATION_RATIO_HIGH:
            reasons.append(
                Reason(
                    "slow_transition",
                    f"{label} took {hop.elapsed_s:.0f}s, {ratio:.1f}× the "
                    f"{median:.0f}s median over {len(baseline.durations)} prior journeys",
                )
            )
        elif ratio <= DURATION_RATIO_LOW:
            reasons.append(
                Reason(
                    "fast_transition",
                    f"{label} took {hop.elapsed_s:.0f}s, {ratio:.1f}× the "
                    f"{median:.0f}s median over {len(baseline.durations)} prior journeys",
                )
            )

    return reasons


async def evaluate_new_hops(
    session: AsyncSession, route: Route, new_hops: list[Hop], *, now: datetime | None = None
) -> list[Reason]:
    """Score only the hops added since the journey's last snapshot.

    Must be called *before* the current advance is persisted — the baseline
    query has no way to exclude "this exact advance" from "history", so
    calling it after `persist_route` would let a journey's own newest leg
    inflate the sample count it is being measured against.

    `now` anchors the lookback window and defaults to the real clock;
    injectable so a test can score against a fixed historical baseline
    without racing whatever the live simulator is doing at the actual
    current time — the same reason `correlator.build_route` takes an
    explicit `since`/`until` rather than always meaning "now".
    """
    if not new_hops:
        return []

    since = (now or datetime.now(UTC)) - BASELINE_LOOKBACK
    baselines = await _load_baselines(session, since)

    all_hops = route.hops
    start_index = len(all_hops) - len(new_hops)

    reasons: list[Reason] = []
    for offset, hop in enumerate(new_hops):
        index = start_index + offset
        if index == 0:
            # The first hop in a route has no leg into it — nothing to score.
            continue
        previous_hop = all_hops[index - 1]
        baseline = baselines.get((previous_hop.camera_code, hop.camera_code))
        reasons.extend(_score_leg(previous_hop.camera_code, hop.camera_code, hop, baseline))

    if reasons:
        log.info(
            "anomaly.scored",
            plate=route.plate,
            factors=[r.factor for r in reasons],
        )
    return reasons
