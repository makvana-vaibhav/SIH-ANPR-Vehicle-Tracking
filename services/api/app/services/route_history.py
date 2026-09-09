"""Snapshots reconstructed journeys into `vehicle_tracks`.

## Why this exists

`correlator.persist_route` was written in Phase 7 and **never called from
anywhere**, so `vehicle_tracks` — a table with its own composite index and a
GiST index on the route line — was never written to. Two things followed:

* there was no journey history, so nothing could learn what a *normal* route
  between two cameras looks like. Trajectory anomaly detection (P5) needs
  exactly that baseline;
* every route was recomputed from raw `detections` on each request, which is
  fine for one operator and wrong for a report over months.

## Why it runs here rather than on the ingest path

The obvious home is the event consumer, where new detections arrive. That is
the wrong place: the 80,000-camera load test already showed p95
capture-to-persisted at 5.7 s against a 3 s target, and the gap is queue wait.
Adding route reconstruction to that path would make the one measured latency
problem worse.

So it runs in the health-monitor daemon instead — off the hot path, on its own
slower cadence, and independent of whether any operator happens to search.

## Why snapshots rather than one row per route

A journey grows: the same plate seen at a fourth camera is the same journey with
another hop, not a new one. Writing a row every cycle would fill the table with
near-duplicates of the same trip. So a plate is only re-persisted when its route
has actually changed — more hops, or a later last-seen — which keeps one row per
meaningful state of a journey.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.intelligence import VehicleTrack
from app.services import correlator

log = get_logger("api.route_history")

#: How far back to look for plates worth snapshotting.
LOOKBACK = timedelta(hours=6)

#: Cameras a plate must have been seen on before it has a route at all.
MIN_CAMERAS = 2

#: Plates per cycle. Bounded so one busy period cannot make a cycle unbounded:
#: each plate costs a `sightings_for` query, and the daemon has a health sweep
#: to get back to.
MAX_PLATES_PER_CYCLE = 40


async def _latest_track(session: AsyncSession, plate: str) -> VehicleTrack | None:
    """The most recently stored snapshot for a plate, if any."""
    return (
        await session.execute(
            select(VehicleTrack)
            .where(VehicleTrack.plate_normalised == plate)
            .order_by(VehicleTrack.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _has_advanced(route: correlator.Route, previous: VehicleTrack | None) -> bool:
    """True when this route says something the stored snapshot does not.

    Growth is measured on hop count and last-seen rather than on the geometry,
    because those are the two ways a journey can genuinely extend: the vehicle
    reached another camera, or it was seen again at the one it was already at.
    """
    if previous is None:
        return True
    if (previous.hop_count or 0) != len(route.hops):
        return True
    return bool(route.last_seen and previous.last_seen and route.last_seen > previous.last_seen)


async def snapshot(session: AsyncSession) -> dict[str, Any]:
    """Persist journeys that have changed since their last snapshot."""
    since = datetime.now(UTC) - LOOKBACK
    candidates = await correlator.recent_plates(
        session, since=since, min_cameras=MIN_CAMERAS, limit=MAX_PLATES_PER_CYCLE
    )

    persisted = unchanged = skipped = 0
    for plate, _cameras in candidates:
        route = await correlator.build_route(session, plate, since=since)
        # A route needs two hops to be a path; persist_route agrees and would
        # return None, but checking here keeps the counters honest.
        if len(route.hops) < 2:
            skipped += 1
            continue

        previous = await _latest_track(session, plate)
        if not _has_advanced(route, previous):
            unchanged += 1
            continue

        if await correlator.persist_route(session, route) is not None:
            persisted += 1

    if persisted:
        await session.commit()

    return {
        "candidates": len(candidates),
        "persisted": persisted,
        "unchanged": unchanged,
        "skipped": skipped,
    }
