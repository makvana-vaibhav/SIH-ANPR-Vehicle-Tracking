"""City traffic analytics: how the roads are moving, computed from observed rows.

This is the module where a plausible invented number would do the most damage,
so three rules govern everything below.

**Every figure is computed.** No constants, no smoothing toward a nice-looking
value, no fallback that quietly substitutes an average when a query returns
nothing. If the data cannot support a figure, the response says so in
`status` and the figure is null.

**Every figure carries its evidence.** `samples` travels with each number,
because a median over four legs and a median over four thousand are different
claims and a chart will otherwise draw them identically.

**Speed carries its provenance.** See `_segment_legs` and the speed endpoint.

## Two data sources, and why

*Counts* come from `detections`, a TimescaleDB hypertable bucketed with
`time_bucket`. That is the cheap, exact source for "how many vehicles passed
this camera".

*Everything about movement between cameras* — segment speed, route density,
travel time — comes from `vehicle_tracks`, the journeys the correlator already
reconstructed and `route_history` persisted. Recomputing routes per request
would mean re-running the correlator over the whole window on every page load.

### The trap in vehicle_tracks

`route_history.snapshot` **inserts a new row every time a journey advances**,
each row holding the *entire* journey so far. Measured on this database: 2,659
rows for 106 distinct plates, with one plate holding 212 snapshots of a 55-hop
journey. Exploding that table directly counts every leg once per snapshot —
inflating route density roughly two-hundredfold, with no symptom other than
impressively large numbers.

So every query here starts from `DISTINCT ON (plate_normalised) ... ORDER BY
created_at DESC`: the latest snapshot per plate, which holds that plate's
complete journey exactly once.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.core.rbac import Permission, require_permission
from app.schemas.analytics import (
    CorridorSpeed,
    DirectionCounts,
    FlowPoint,
    FlowResponse,
    FlowSeries,
    HeatmapFeature,
    HeatmapFeatureProperties,
    HeatmapGeometry,
    HeatmapResponse,
    Hotspot,
    HotspotResponse,
    PossibleObstruction,
    QueueState,
    RouteDensityResponse,
    RoutePair,
    SegmentSpeed,
    SpeedProvenance,
    SpeedResponse,
    TrafficFactor,
    TrafficHistoryPoint,
    TrafficHistoryResponse,
    TrafficResponse,
    TrafficZone,
    TravelTime,
    TravelTimeResponse,
    VehicleTypeCounts,
    Window,
)
from app.services import traffic

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])
log = get_logger(__name__)

_read = require_permission(Permission.ANALYTICS_READ)

#: Default reporting window. Detections is an unbounded hypertable, so "recent"
#: has to mean something specific or the first query after a long run scans
#: every chunk. Matches the detections router for the same reason.
DEFAULT_WINDOW = timedelta(hours=6)

#: Bucket widths a caller may ask for, as Postgres intervals. A closed set
#: because the value is interpolated into `time_bucket` — an open string here
#: would be an injection point, and an arbitrary width is not useful anyway.
BUCKETS: dict[str, tuple[str, int]] = {
    "1m": ("1 minute", 60),
    "5m": ("5 minutes", 300),
    "15m": ("15 minutes", 900),
    "1h": ("1 hour", 3600),
    "1d": ("1 day", 86400),
}

#: How far back a baseline may look for "what is normal at this time of day".
BASELINE_DAYS = 7

#: A median over fewer legs than this is noise, not a baseline. Reported as
#: `insufficient_history` rather than published.
MIN_BASELINE_SAMPLES = 5

#: Below this many plausible legs, a segment reports `insufficient_data`
#: instead of a speed. One vehicle is an anecdote.
MIN_SPEED_SAMPLES = 3

#: Ceiling on rows returned by the list-shaped endpoints.
MAX_ROWS = 200


def _window(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime]:
    """Resolve the reporting window, always timezone-aware UTC."""
    end = until or datetime.now(UTC)
    start = since or (end - DEFAULT_WINDOW)
    if start >= end:
        start = end - DEFAULT_WINDOW
    return start, end


#: The legs of every journey, deduplicated to one snapshot per plate.
#:
#: `lag` over the hop ordinality supplies the *from* camera: each hop already
#: carries distance, elapsed time and implied speed relative to the hop before
#: it, but names only the camera it arrived at.
_LEGS_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (plate_normalised) plate_normalised, hops
    FROM vehicle_tracks
    WHERE created_at >= :snapshot_since
    ORDER BY plate_normalised, created_at DESC
),
legs AS (
    SELECT
        l.plate_normalised,
        lag(e.h ->> 'camera_code') OVER (
            PARTITION BY l.plate_normalised ORDER BY e.ord
        ) AS from_camera,
        e.h ->> 'camera_code'                   AS to_camera,
        (e.h ->> 'distance_m')::float           AS distance_m,
        (e.h ->> 'elapsed_s')::float            AS elapsed_s,
        (e.h ->> 'implied_kmph')::float         AS implied_kmph,
        (e.h ->> 'plausible')::bool             AS plausible,
        (e.h ->> 'arrived_at')::timestamptz     AS arrived_at
    FROM latest l,
         jsonb_array_elements(l.hops -> 'hops') WITH ORDINALITY AS e(h, ord)
)
SELECT
    legs.*,
    cf.corridor AS from_corridor,
    ct.corridor AS to_corridor
FROM legs
LEFT JOIN cameras cf ON cf.camera_code = legs.from_camera
LEFT JOIN cameras ct ON ct.camera_code = legs.to_camera
WHERE legs.from_camera IS NOT NULL
  AND legs.arrived_at >= :start
  AND legs.arrived_at <  :end
"""


async def _segment_legs(
    session: AsyncSession, start: datetime, end: datetime
) -> list[Any]:
    """Every observed camera-to-camera movement in the window.

    Snapshots are read from a window wider than the report itself, because a
    journey that began before `start` is still the row that holds the legs
    inside it.
    """
    rows = await session.execute(
        text(_LEGS_SQL),
        {
            "snapshot_since": start - timedelta(days=BASELINE_DAYS + 1),
            "start": start,
            "end": end,
        },
    )
    return list(rows.mappings())


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


# ── flow ──────────────────────────────────────────────────────────────
@router.get(
    "/flow",
    response_model=FlowResponse,
    summary="Vehicle count per time bucket, by camera or corridor",
)
async def flow(
    session: DbSession,
    _user: Annotated[object, Depends(_read)],
    since: datetime | None = None,
    until: datetime | None = None,
    bucket: str = Query(default="15m", description=f"One of {', '.join(BUCKETS)}"),
    group_by: str = Query(default="camera", pattern="^(camera|corridor)$"),
    corridor: str | None = None,
) -> FlowResponse:
    start, end = _window(since, until)
    interval, seconds = BUCKETS.get(bucket, BUCKETS["15m"])

    # `interval` is interpolated, never the caller's string: BUCKETS is a closed
    # set and the lookup falls back to a default, so no caller input reaches SQL.
    #
    # Two things about the SQL below are worth knowing before editing it.
    #
    # The corridor filter is written `CAST(:corridor AS text)` rather than
    # `:corridor::text`, because SQLAlchemy's `text()` scans the string for
    # `:word` bind parameters itself and reads the `::` cast immediately after
    # one as the start of another. That surfaces as a Postgres syntax error
    # pointing at a colon, which says nothing about the cause.
    #
    # And **never write a SQL comment here containing a colon-prefixed word**.
    # The same scanner does not skip comments, so an explanatory `-- like
    # :this` becomes a required bind parameter and every call fails with
    # "a value is required for bind parameter". Explanations belong in Python
    # comments like this one.
    grouping = "c.corridor" if group_by == "corridor" else "c.camera_code"
    sql = f"""
        SELECT
            time_bucket(INTERVAL '{interval}', d.ts) AS bucket,
            {grouping}                               AS key,
            max(c.name)                              AS label,
            max(c.corridor)                          AS corridor,
            count(*)                                 AS vehicles,
            count(d.plate_normalised)                AS with_plate
        FROM detections d
        JOIN cameras c ON c.id = d.camera_id
        WHERE d.ts >= :start AND d.ts < :end
          AND (CAST(:corridor AS text) IS NULL OR c.corridor = :corridor)
          AND {grouping} IS NOT NULL
        GROUP BY bucket, key
        ORDER BY key, bucket
    """
    rows = (
        await session.execute(
            text(sql), {"start": start, "end": end, "corridor": corridor}
        )
    ).mappings()

    series: dict[str, FlowSeries] = {}
    total = 0
    for row in rows:
        key = row["key"]
        entry = series.get(key)
        if entry is None:
            entry = FlowSeries(
                key=key,
                # Grouped by corridor the key *is* the label; grouped by camera
                # the human name is more useful than the code.
                label=key if group_by == "corridor" else (row["label"] or key),
                corridor=row["corridor"],
                points=[],
                total=0,
            )
            series[key] = entry
        entry.points.append(
            FlowPoint(
                ts=row["bucket"],
                vehicles=row["vehicles"],
                with_plate=row["with_plate"],
            )
        )
        entry.total += row["vehicles"]
        total += row["vehicles"]

    return FlowResponse(
        window=Window(start=start, end=end, bucket_seconds=seconds),
        group_by=group_by,  # type: ignore[arg-type]
        series=sorted(series.values(), key=lambda s: -s.total)[:MAX_ROWS],
        total_vehicles=total,
    )


# ── speed ─────────────────────────────────────────────────────────────
@router.get(
    "/speed",
    response_model=SpeedResponse,
    summary="Average speed per corridor, from observed journeys",
)
async def speed(
    session: DbSession,
    _user: Annotated[object, Depends(_read)],
    since: datetime | None = None,
    until: datetime | None = None,
    corridor: str | None = None,
) -> SpeedResponse:
    """Speed along each corridor, from legs the correlator believes.

    **Only physically plausible legs count.** The correlator flags any leg
    implying more than 150 km/h, and those are excluded here rather than
    averaged in. That matters because the demo fleet replays recorded clips: a
    clip repeats every P seconds, so the largest gap between two cameras seeing
    the same vehicle is P, and with cameras kilometres apart every implied speed
    lands in the hundreds or thousands of km/h. Averaging those would publish a
    confident figure describing the footage rather than the traffic.

    So on replayed footage this endpoint usually reports `insufficient_data`
    with every leg excluded — which is the truthful answer — and begins
    producing real figures unchanged the moment the pipeline runs on footage
    where inter-camera timing is real.
    """
    start, end = _window(since, until)
    legs = await _segment_legs(session, start, end)

    considered = 0
    excluded = 0
    by_segment: dict[tuple[str, str], dict[str, Any]] = {}

    for leg in legs:
        if leg["implied_kmph"] is None or leg["distance_m"] is None:
            continue
        if corridor and leg["to_corridor"] != corridor:
            continue
        considered += 1
        if not leg["plausible"]:
            excluded += 1
            continue

        key = (leg["from_camera"], leg["to_camera"])
        entry = by_segment.setdefault(
            key,
            {
                "corridor": leg["to_corridor"],
                "distance_m": leg["distance_m"],
                "kmph": [],
                "seconds": [],
            },
        )
        entry["kmph"].append(float(leg["implied_kmph"]))
        if leg["elapsed_s"] is not None:
            entry["seconds"].append(float(leg["elapsed_s"]))

    segments: list[SegmentSpeed] = []
    for (from_cam, to_cam), entry in by_segment.items():
        samples = len(entry["kmph"])
        enough = samples >= MIN_SPEED_SAMPLES
        segments.append(
            SegmentSpeed(
                from_camera=from_cam,
                to_camera=to_cam,
                corridor=entry["corridor"],
                distance_km=round(entry["distance_m"] / 1000.0, 3),
                status="ok" if enough else "insufficient_data",
                samples=samples,
                median_kmph=round(_median(entry["kmph"]) or 0.0, 1) if enough else None,
                p85_kmph=(
                    round(_percentile(entry["kmph"], 0.85) or 0.0, 1) if enough else None
                ),
                median_seconds=(
                    round(_median(entry["seconds"]) or 0.0, 1)
                    if enough and entry["seconds"]
                    else None
                ),
            )
        )

    corridors: list[CorridorSpeed] = []
    grouped: dict[str, list[SegmentSpeed]] = {}
    for segment in segments:
        grouped.setdefault(segment.corridor or "(no corridor)", []).append(segment)

    for name, members in sorted(grouped.items()):
        usable = [s for s in members if s.status == "ok" and s.median_kmph is not None]
        total_samples = sum(s.samples for s in members)
        corridors.append(
            CorridorSpeed(
                corridor=name,
                status="ok" if usable else "insufficient_data",
                samples=total_samples,
                median_kmph=(
                    round(_median([s.median_kmph for s in usable]) or 0.0, 1)  # type: ignore[misc]
                    if usable
                    else None
                ),
                segments=sorted(members, key=lambda s: s.from_camera)[:MAX_ROWS],
            )
        )

    if considered and excluded == considered:
        note = (
            f"All {considered} observed legs implied a speed above the "
            "correlator's 150 km/h ceiling and were excluded. The demo fleet "
            "replays recorded clips, so the gap between two cameras seeing the "
            "same vehicle is set by clip length rather than by travel — no "
            "speed can be measured from it."
        )
    elif not considered:
        note = "No journeys with a measurable leg were observed in this window."
    else:
        note = (
            f"{considered - excluded} of {considered} observed legs were "
            "physically plausible and contributed to these figures."
        )

    return SpeedResponse(
        window=Window(start=start, end=end, bucket_seconds=0),
        provenance=SpeedProvenance(
            legs_considered=considered,
            legs_excluded_implausible=excluded,
            note=note,
        ),
        corridors=corridors,
    )


# ── route density ─────────────────────────────────────────────────────
@router.get(
    "/routes",
    response_model=RouteDensityResponse,
    summary="Most-travelled camera-to-camera movements",
)
async def routes(
    session: DbSession,
    _user: Annotated[object, Depends(_read)],
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=25, ge=1, le=MAX_ROWS),
) -> RouteDensityResponse:
    """Which movements traffic actually makes, ranked by how often.

    Counted from the latest snapshot per plate, so one vehicle travelling
    A→B contributes exactly one journey however many times its route was
    re-persisted while it was still moving.
    """
    start, end = _window(since, until)
    legs = await _segment_legs(session, start, end)

    pairs: dict[tuple[str, str], dict[str, Any]] = {}
    for leg in legs:
        key = (leg["from_camera"], leg["to_camera"])
        entry = pairs.setdefault(
            key,
            {
                "from_corridor": leg["from_corridor"],
                "to_corridor": leg["to_corridor"],
                "distance_m": leg["distance_m"] or 0.0,
                "gaps": [],
                "plates": set(),
            },
        )
        entry["plates"].add(leg["plate_normalised"])
        if leg["elapsed_s"] is not None:
            entry["gaps"].append(float(leg["elapsed_s"]))

    ranked = sorted(pairs.items(), key=lambda kv: -len(kv[1]["plates"]))[:limit]
    result = [
        RoutePair(
            from_camera=from_cam,
            to_camera=to_cam,
            from_corridor=entry["from_corridor"],
            to_corridor=entry["to_corridor"],
            journeys=len(entry["plates"]),
            distance_km=round((entry["distance_m"] or 0.0) / 1000.0, 3),
            median_gap_seconds=round(_median(entry["gaps"]) or 0.0, 1),
            same_corridor=(
                entry["from_corridor"] is not None
                and entry["from_corridor"] == entry["to_corridor"]
            ),
        )
        for (from_cam, to_cam), entry in ranked
    ]

    return RouteDensityResponse(
        window=Window(start=start, end=end, bucket_seconds=0),
        pairs=result,
        total_journeys=sum(p.journeys for p in result),
    )


# ── travel time ───────────────────────────────────────────────────────
async def _travel_times(
    session: AsyncSession, start: datetime, end: datetime
) -> list[TravelTime]:
    """Current segment durations against the same time of day on earlier days."""
    current_legs = await _segment_legs(session, start, end)

    # The baseline covers the same clock hours on the preceding days, which is
    # what makes it a fair comparison: 09:00 traffic is compared with 09:00
    # traffic, not with the daily average that includes the small hours.
    baseline_start = start - timedelta(days=BASELINE_DAYS)
    baseline_legs = await _segment_legs(session, baseline_start, start)
    hours = {(start + timedelta(hours=offset)).hour for offset in range(0, 24)}
    window_hours = {
        (start + timedelta(seconds=s)).hour
        for s in range(0, int((end - start).total_seconds()) + 1, 3600)
    } or hours

    current: dict[tuple[str, str], list[float]] = {}
    corridors: dict[tuple[str, str], str | None] = {}
    for leg in current_legs:
        if leg["elapsed_s"] is None or not leg["plausible"]:
            continue
        key = (leg["from_camera"], leg["to_camera"])
        current.setdefault(key, []).append(float(leg["elapsed_s"]))
        corridors[key] = leg["to_corridor"]

    baseline: dict[tuple[str, str], list[float]] = {}
    baseline_days: dict[tuple[str, str], set] = {}
    for leg in baseline_legs:
        if leg["elapsed_s"] is None or not leg["plausible"]:
            continue
        if leg["arrived_at"].hour not in window_hours:
            continue
        key = (leg["from_camera"], leg["to_camera"])
        baseline.setdefault(key, []).append(float(leg["elapsed_s"]))
        baseline_days.setdefault(key, set()).add(leg["arrived_at"].date())
        corridors.setdefault(key, leg["to_corridor"])

    out: list[TravelTime] = []
    for key in sorted(set(current) | set(baseline)):
        now_values = current.get(key, [])
        base_values = baseline.get(key, [])
        now_median = _median(now_values)
        base_median = (
            _median(base_values) if len(base_values) >= MIN_BASELINE_SAMPLES else None
        )

        if now_median is None:
            status = "insufficient_data"
        elif base_median is None:
            status = "insufficient_history"
        else:
            status = "ok"

        delta = None
        if now_median is not None and base_median:
            delta = round((now_median - base_median) / base_median * 100.0, 1)

        out.append(
            TravelTime(
                from_camera=key[0],
                to_camera=key[1],
                corridor=corridors.get(key),
                status=status,  # type: ignore[arg-type]
                current_seconds=round(now_median, 1) if now_median is not None else None,
                current_samples=len(now_values),
                baseline_seconds=(
                    round(base_median, 1) if base_median is not None else None
                ),
                baseline_samples=len(base_values),
                baseline_days=len(baseline_days.get(key, ())),
                delta_pct=delta,
            )
        )
    return out


@router.get(
    "/travel-time",
    response_model=TravelTimeResponse,
    summary="Segment travel time now, against its own baseline",
)
async def travel_time(
    session: DbSession,
    _user: Annotated[object, Depends(_read)],
    since: datetime | None = None,
    until: datetime | None = None,
) -> TravelTimeResponse:
    """Current duration per segment versus the same time of day on earlier days.

    A young database has no earlier days, so segments report
    `insufficient_history` and every baseline figure is null. That is the
    expected state for a fresh `make demo`, and the UI must render it as
    "insufficient history" — never as a zero delta, which would read as
    "traffic is exactly normal".
    """
    start, end = _window(since, until)
    return TravelTimeResponse(
        window=Window(start=start, end=end, bucket_seconds=0),
        segments=(await _travel_times(session, start, end))[:MAX_ROWS],
        baseline_window_days=BASELINE_DAYS,
    )


# ── hotspots ──────────────────────────────────────────────────────────
async def _volume_by_camera(
    session: AsyncSession, start: datetime, end: datetime, limit: int
) -> list[Any]:
    sql = """
        SELECT c.camera_code, c.name, c.corridor,
               ST_Y(c.location::geometry) AS lat,
               ST_X(c.location::geometry) AS lon,
               count(*) AS vehicles
        FROM detections d
        JOIN cameras c ON c.id = d.camera_id
        WHERE d.ts >= :start AND d.ts < :end
        GROUP BY c.camera_code, c.name, c.corridor, c.location
        ORDER BY vehicles DESC
        LIMIT :limit
    """
    rows = await session.execute(
        text(sql), {"start": start, "end": end, "limit": limit}
    )
    return list(rows.mappings())


@router.get(
    "/hotspots",
    response_model=HotspotResponse,
    summary="Busiest cameras, and the most slowed-down segments",
)
async def hotspots(
    session: DbSession,
    _user: Annotated[object, Depends(_read)],
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=10, ge=1, le=MAX_ROWS),
) -> HotspotResponse:
    """Two rankings, deliberately kept apart.

    Volume answers "where are the most vehicles". Slowdown answers "where is
    traffic worse than normal". They are different questions, and presenting a
    volume ranking as congestion — which is the easy mistake, because volume is
    always available and slowdown often is not — would be exactly the invented
    figure this module exists to avoid. When there is no baseline, the slowdown
    list is empty and says why.
    """
    start, end = _window(since, until)
    rows = await _volume_by_camera(session, start, end, limit)
    busiest = max((row["vehicles"] for row in rows), default=0)

    by_volume = [
        Hotspot(
            camera_code=row["camera_code"],
            camera_name=row["name"],
            corridor=row["corridor"],
            lat=row["lat"],
            lon=row["lon"],
            vehicles=row["vehicles"],
            intensity=round(row["vehicles"] / busiest, 3) if busiest else 0.0,
        )
        for row in rows
    ]

    slowdowns = [t for t in await _travel_times(session, start, end) if t.status == "ok"]
    slowdowns.sort(key=lambda t: -(t.delta_pct or 0.0))

    if slowdowns:
        status = "ok"
        note = "Slowdown is measured against each segment's own time-of-day baseline."
    else:
        status = "insufficient_history"
        note = (
            "No segment has enough history for a baseline, so no slowdown "
            "ranking is shown. Volume is ranked below and is not a congestion "
            "measure: a busy road may be flowing freely."
        )

    return HotspotResponse(
        window=Window(start=start, end=end, bucket_seconds=0),
        by_volume=by_volume,
        by_slowdown=slowdowns[:limit],
        slowdown_status=status,  # type: ignore[arg-type]
        note=note,
    )


# ── heatmap ───────────────────────────────────────────────────────────
@router.get(
    "/heatmap",
    response_model=HeatmapResponse,
    summary="Detection density as GeoJSON, for the map",
)
async def heatmap(
    session: DbSession,
    _user: Annotated[object, Depends(_read)],
    since: datetime | None = None,
    until: datetime | None = None,
) -> HeatmapResponse:
    """GeoJSON points weighted by detection count.

    Returned in GeoJSON so MapLibre can take it as a source directly, matching
    the camera layer the map already renders — the heatmap is a different
    weighting of the same points, not a different dataset.
    """
    start, end = _window(since, until)
    rows = await _volume_by_camera(session, start, end, MAX_ROWS)
    busiest = max((row["vehicles"] for row in rows), default=0)

    return HeatmapResponse(
        features=[
            HeatmapFeature(
                geometry=HeatmapGeometry(coordinates=[row["lon"], row["lat"]]),
                properties=HeatmapFeatureProperties(
                    camera_code=row["camera_code"],
                    camera_name=row["name"],
                    corridor=row["corridor"],
                    vehicles=row["vehicles"],
                    intensity=round(row["vehicles"] / busiest, 3) if busiest else 0.0,
                ),
            )
            for row in rows
        ],
        window=Window(start=start, end=end, bucket_seconds=0),
        max_vehicles=busiest,
    )


# ── traffic intelligence ──────────────────────────────────────────────
#
# One endpoint behind the whole dashboard. The panels all describe the same
# window over the same rows, so computing them together is what stops them
# disagreeing on screen while each is individually correct.

#: Hard cap on detection rows pulled for occupancy and queue analysis.
#:
#: Occupancy is a sweep over per-vehicle dwell intervals, and the sweep runs
#: in `app/services/traffic.py` rather than in SQL so the tested function is
#: the one actually serving traffic. That means the rows come to Python, so
#: the window has to be bounded. At the demo fleet's scale (58 cameras, a few
#: thousand vehicles in six hours) this is never reached; a city deployment
#: would move the sweep into SQL, which is a known and deliberate limit
#: rather than an oversight.
MAX_OBSERVATION_ROWS = 20_000

#: Bucket width the queue detector runs over. Fixed rather than caller-chosen:
#: `traffic_queue_dwell_seconds` and `traffic_queue_min_sustained_buckets` are
#: both calibrated against it, so letting a caller change the bucket would
#: silently change what "sustained" means.
QUEUE_BUCKET = "5 minutes"
QUEUE_BUCKET_SECONDS = 300

#: Per-vehicle observations in the window: what was seen, for how long, and
#: whether it moved. One row per vehicle — `detections` holds one row per
#: retired track, so there is no per-frame inflation to undo here.
_OBSERVATIONS_SQL = """
    SELECT c.camera_code, c.name AS camera_name, c.corridor,
           ST_Y(c.location::geometry) AS lat,
           ST_X(c.location::geometry) AS lon,
           d.ts, d.track_id, d.vehicle_type, d.direction,
           d.dwell_s, d.plate_normalised
    FROM detections d
    JOIN cameras c ON c.id = d.camera_id
    WHERE d.ts >= :start AND d.ts < :end
      AND (CAST(:corridor AS text) IS NULL OR c.corridor = :corridor)
    ORDER BY d.ts
    LIMIT :limit
"""


def _rounded(value: float | None, places: int = 2) -> float | None:
    """Round for presentation, keeping None as None.

    A missing figure must stay missing all the way to the client — rounding
    None to 0.0 is exactly how "we could not measure this" turns into "the
    road is empty".
    """
    return None if value is None else round(float(value), places)


def _thresholds() -> traffic.TrafficThresholds:
    """Settings → the engine's threshold object.

    The engine takes these as an argument rather than importing settings,
    which is what keeps it stdlib-only and therefore runnable without a
    database. This function is the single place the two meet.
    """
    return traffic.TrafficThresholds(
        queue_min_vehicles=settings.traffic_queue_min_vehicles,
        queue_dwell_seconds=settings.traffic_queue_dwell_seconds,
        queue_min_sustained_buckets=settings.traffic_queue_min_sustained_buckets,
        obstruction_dwell_seconds=settings.traffic_obstruction_dwell_seconds,
        occupancy_reference=settings.traffic_occupancy_reference,
        congestion_moderate=settings.traffic_congestion_moderate,
        congestion_heavy=settings.traffic_congestion_heavy,
        congestion_severe=settings.traffic_congestion_severe,
    )


#: The direction labels the worker emits, and the only strings this router will
#: act on. An explicit set rather than `hasattr` on the response model: the
#: value arrives from an event, and `hasattr(counts, "model_dump")` is true, so
#: attribute-name dispatch would let a malformed event overwrite a method
#: instead of incrementing a counter.
_DIRECTIONS = frozenset(
    {"approaching", "receding", "crossing_left", "crossing_right", "stationary"}
)


def _direction_counts(directions: list[str | None]) -> DirectionCounts:
    """Tally the worker's image-space direction labels.

    NULL means the worker could not measure the movement — a vehicle it saw
    exactly once. Counted as `unmeasured` rather than folded into
    `stationary`, because the obstruction detector keys on stationary and
    conflating the two would invent stopped vehicles out of tracker noise.
    Anything unrecognised lands there too, rather than being dropped.
    """
    tally = dict.fromkeys(_DIRECTIONS, 0)
    unmeasured = 0
    for raw in directions:
        if raw in tally:
            tally[raw] += 1
        else:
            unmeasured += 1
    return DirectionCounts(**tally, unmeasured=unmeasured)


def _queue_samples(rows: list[Any], bucket_seconds: int) -> list[traffic.QueueSample]:
    """Bucket a zone's vehicles into the evidence the queue detector needs.

    A bucket's `median_dwell_s` is taken over the vehicles that were actually
    stationary in it, not over all of them: a queue of four cars beside a lane
    of free-flowing traffic is still a queue, and averaging the two away is
    how it would be missed.
    """
    buckets: dict[int, list[float]] = {}
    for row in rows:
        if row["direction"] != traffic.STATIONARY or row["dwell_s"] is None:
            continue
        slot = int(row["ts"].timestamp() // bucket_seconds)
        buckets.setdefault(slot, []).append(float(row["dwell_s"]))

    return [
        traffic.QueueSample(
            at=float(slot * bucket_seconds),
            stationary_vehicles=len(dwells),
            median_dwell_s=traffic.median_or_none(dwells) or 0.0,
        )
        for slot, dwells in sorted(buckets.items())
    ]


def _dwell_intervals(rows: list[Any]) -> list[tuple[float, float]]:
    """(entered, left) per vehicle, in epoch seconds.

    `ts` is when the track retired — when the vehicle left the view — so the
    interval runs back from it by the dwell. Vehicles with no dwell recorded
    (anything written before migration 0007, or by a worker that predates the
    motion block) are skipped rather than assumed instantaneous, which would
    quietly report an empty road.
    """
    intervals: list[tuple[float, float]] = []
    for row in rows:
        if row["dwell_s"] is None:
            continue
        left = row["ts"].timestamp()
        intervals.append((left - float(row["dwell_s"]), left))
    return intervals


async def _corridor_speeds(
    session: AsyncSession, start: datetime, end: datetime
) -> dict[str, float]:
    """Median plausible speed per corridor, over the legs the speed endpoint uses.

    Usually empty on the demo fleet: replayed clips make every implied speed
    implausible and the correlator excludes them, which is correct and is why
    congestion renormalises over whichever signals survive.
    """
    legs = await _segment_legs(session, start, end)
    by_corridor: dict[str, list[float]] = {}
    for leg in legs:
        if not leg["plausible"] or leg["implied_kmph"] is None:
            continue
        corridor = leg["to_corridor"]
        if corridor:
            by_corridor.setdefault(corridor, []).append(float(leg["implied_kmph"]))

    out: dict[str, float] = {}
    for corridor, values in by_corridor.items():
        if len(values) < MIN_SPEED_SAMPLES:
            continue
        median = _median(values)
        if median is not None:
            out[corridor] = median
    return out


@router.get(
    "/traffic",
    response_model=TrafficResponse,
    summary="Traffic state per camera or corridor: counts, density, congestion, queues",
)
async def traffic_state(
    session: DbSession,
    _user: Annotated[object, Depends(_read)],
    since: datetime | None = None,
    until: datetime | None = None,
    group_by: Annotated[str, Query(pattern="^(camera|corridor)$")] = "camera",
    corridor: str | None = None,
) -> TrafficResponse:
    """Everything the traffic dashboard shows, for one window.

    Counts are of **unique vehicles**: the AI worker emits one event when a
    track retires and the consumer writes one row for it, so a vehicle in view
    for 300 frames is one row, not 300. No `DISTINCT` is needed and none is
    used — adding one here would hide it if that ever stopped being true.

    Every figure that cannot be computed says so rather than defaulting to
    zero. On a fresh `make demo` expect speed and travel time to report
    `insufficient_data` on most corridors; congestion is then scored on
    occupancy alone and labelled accordingly, which is the honest answer
    rather than "free flowing".
    """
    start, end = _window(since, until)
    window_seconds = (end - start).total_seconds()
    thresholds = _thresholds()

    result = await session.execute(
        text(_OBSERVATIONS_SQL),
        {
            "start": start,
            "end": end,
            "corridor": corridor,
            "limit": MAX_OBSERVATION_ROWS,
        },
    )
    rows = list(result.mappings())

    fleet = await session.execute(text("SELECT count(*) FROM cameras WHERE anpr_enabled"))
    fleet_cameras = int(fleet.scalar() or 0)

    speeds = await _corridor_speeds(session, start, end)

    # Travel-time delta per corridor, from the existing segment comparison.
    # Keyed on the arriving camera's corridor, which is how `_travel_times`
    # already attributes a leg.
    deltas: dict[str, list[float]] = {}
    for segment in await _travel_times(session, start, end):
        if segment.corridor and segment.delta_pct is not None:
            deltas.setdefault(segment.corridor, []).append(segment.delta_pct)

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        key = (
            (row["corridor"] or "unassigned")
            if group_by == "corridor"
            else row["camera_code"]
        )
        grouped.setdefault(key, []).append(row)

    zones: list[TrafficZone] = []
    for key, zone_rows in sorted(grouped.items()):
        first = zone_rows[0]
        zone_corridor = first["corridor"]

        # Speed and travel time are corridor-level facts — both come from the
        # distance between two cameras, which a single camera cannot know. A
        # camera inherits its corridor's figure rather than inventing one.
        median_speed = speeds.get(zone_corridor) if zone_corridor else None
        corridor_deltas = deltas.get(zone_corridor or "", [])
        travel_delta = _median(corridor_deltas) if corridor_deltas else None

        intervals = _dwell_intervals(zone_rows)
        peak = traffic.peak_occupancy(intervals)

        assessment = traffic.assess_congestion(
            peak_vehicles=peak if intervals else None,
            # No free-flow reference is measured anywhere in this system yet,
            # so a speed *ratio* cannot be computed honestly. The median speed
            # is still reported beside it; it simply does not vote on the
            # congestion score rather than voting on an invented baseline.
            speed_ratio=None,
            travel_time_delta_pct=travel_delta,
            thresholds=thresholds,
        )
        queue = traffic.detect_queue(
            _queue_samples(zone_rows, QUEUE_BUCKET_SECONDS), thresholds
        )

        zones.append(
            TrafficZone(
                key=key,
                label=(
                    (zone_corridor or key)
                    if group_by == "corridor"
                    else first["camera_name"]
                ),
                corridor=zone_corridor,
                lat=first["lat"] if group_by == "camera" else None,
                lon=first["lon"] if group_by == "camera" else None,
                vehicles=len(zone_rows),
                by_type=VehicleTypeCounts(
                    **traffic.counts_by_type([r["vehicle_type"] for r in zone_rows])
                ),
                by_direction=_direction_counts([r["direction"] for r in zone_rows]),
                vehicles_per_minute=_rounded(
                    traffic.rate_per_minute(len(zone_rows), window_seconds)
                ),
                vehicles_per_hour=_rounded(
                    traffic.rate_per_hour(len(zone_rows), window_seconds)
                ),
                peak_occupancy=peak,
                density_status="ok" if intervals else "insufficient_data",
                median_speed_kmph=_rounded(median_speed),
                speed_status="ok" if median_speed is not None else "insufficient_data",
                travel_time_delta_pct=_rounded(travel_delta),
                travel_time_status=(
                    "ok" if travel_delta is not None else "insufficient_history"
                ),
                congestion=assessment.level,
                congestion_score=assessment.score,
                congestion_status=assessment.status,
                factors=[
                    TrafficFactor(factor=f.factor, detail=f.detail)
                    for f in assessment.factors
                ],
                queue=QueueState(
                    present=queue.present,
                    length_vehicles=queue.length_vehicles,
                    sustained_buckets=queue.sustained_buckets,
                    status=queue.status,
                ),
            )
        )

    obstructions = [
        PossibleObstruction(
            camera_code=row["camera_code"],
            camera_name=row["camera_name"],
            corridor=row["corridor"],
            lat=row["lat"],
            lon=row["lon"],
            track_id=row["track_id"],
            plate=row["plate_normalised"],
            vehicle_type=row["vehicle_type"],
            stationary_seconds=round(float(row["dwell_s"]), 1),
            last_seen=row["ts"],
        )
        for row in rows
        if traffic.is_possible_obstruction(
            direction=row["direction"], dwell_s=row["dwell_s"], thresholds=thresholds
        )
    ]
    obstructions.sort(key=lambda o: o.stationary_seconds, reverse=True)

    ranked = sorted(
        (z for z in zones if z.congestion_score is not None),
        key=lambda z: z.congestion_score or 0.0,
        reverse=True,
    )

    return TrafficResponse(
        window=Window(start=start, end=end, bucket_seconds=QUEUE_BUCKET_SECONDS),
        group_by=group_by,  # type: ignore[arg-type]
        zones=zones,
        total_vehicles=len(rows),
        totals_by_type=VehicleTypeCounts(
            **traffic.counts_by_type([r["vehicle_type"] for r in rows])
        ),
        active_cameras=len({r["camera_code"] for r in rows}),
        fleet_cameras=fleet_cameras,
        possible_obstructions=obstructions[:MAX_ROWS],
        most_congested=[z.key for z in ranked[:10]],
        note=(
            "Density is vehicles in a camera's view, not vehicles per kilometre "
            "— no camera here is calibrated. Speed is estimated from "
            "multi-camera timing and is absent where no plausible leg exists."
        ),
    )


@router.get(
    "/traffic/history",
    response_model=TrafficHistoryResponse,
    summary="Bucketed traffic volume and dwell, for the trend chart",
)
async def traffic_history(
    session: DbSession,
    _user: Annotated[object, Depends(_read)],
    since: datetime | None = None,
    until: datetime | None = None,
    group_by: Annotated[str, Query(pattern="^(camera|corridor)$")] = "corridor",
    key: str | None = None,
) -> TrafficHistoryResponse:
    """Volume and stationary-vehicle counts per bucket over the window.

    Deliberately the same buckets the queue detector runs over, so the chart
    an operator reads and the verdict shown beside it cannot disagree.
    """
    start, end = _window(since, until)
    # Interpolated, not bound: a column name cannot be a bind parameter. Safe
    # because `group_by` is constrained to two literals by the route's own
    # `pattern`, so nothing caller-supplied reaches the SQL text.
    column = "c.corridor" if group_by == "corridor" else "c.camera_code"

    # `CAST(:key AS text)` rather than `:key::text`, and the key comparison
    # written the long way: see the note above the flow endpoint's SQL for
    # both traps. Bare `:key IS NULL` leaves the parameter untyped, which
    # asyncpg rejects outright rather than treating as NULL.
    sql = f"""
        SELECT time_bucket(INTERVAL '{QUEUE_BUCKET}', d.ts) AS bucket,
               count(*) AS vehicles,
               count(*) FILTER (WHERE d.direction = 'stationary') AS stationary,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY d.dwell_s
               ) FILTER (WHERE d.direction = 'stationary') AS median_dwell
        FROM detections d
        JOIN cameras c ON c.id = d.camera_id
        WHERE d.ts >= :start AND d.ts < :end
          AND (CAST(:key AS text) IS NULL OR {column} = :key)
        GROUP BY bucket
        ORDER BY bucket
    """  # `column` is one of two literals chosen above, never caller input
    rows = await session.execute(text(sql), {"start": start, "end": end, "key": key})

    return TrafficHistoryResponse(
        window=Window(start=start, end=end, bucket_seconds=QUEUE_BUCKET_SECONDS),
        key=key,
        group_by=group_by,  # type: ignore[arg-type]
        points=[
            TrafficHistoryPoint(
                bucket=row["bucket"],
                vehicles=int(row["vehicles"]),
                stationary_vehicles=int(row["stationary"] or 0),
                median_dwell_s=_rounded(row["median_dwell"]),
            )
            for row in rows.mappings()
        ],
    )
