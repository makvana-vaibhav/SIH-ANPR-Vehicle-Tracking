"""Predictive traffic: a short-horizon congestion forecast, computed and checked.

ROADMAP.md's P10 gate has three parts, and this module exists to satisfy all
three honestly rather than the first one impressively:

1. A 15/30-minute forecast per junction/corridor, from inflow trend, speed
   trend and upstream state.
2. The contributing factors shown beside the number (the explainability
   rule).
3. **A backtest against held-out history, with the forecast's own error
   reported.** A prediction with no error bar is decoration.

See `app.schemas.predictions` for what "congestion" is defined to mean here
— a relative measure against each camera's own history, because there is no
road-capacity data anywhere in this system to compute an absolute occupancy
figure from.

## Method, plainly

`current_index_pct` = 100 × (vehicles in the most recent 5-minute bucket) ÷
(this camera's typical vehicles per 5-minute bucket, at this hour of day,
averaged over `BASELINE_DAYS` earlier days). 100 means "normal for this
hour"; 150 means "50% busier than normal for this hour".

The forecast is an ordinary-least-squares line through the last
`TREND_BUCKETS` index values, extrapolated to now+15 and now+30. Nothing
more exotic than that — no external model, nothing this session could not
verify by hand.

The backtest runs the *identical* fitting procedure against buckets already
inside the requested lookback window: fit on an earlier slice, "forecast"
forward into a later slice that has already happened, and measure the
error against what was actually observed there. Same method, same code
path, just checked against the past instead of the future — so the
`mae_pct` reported alongside every live forecast is a measurement of that
forecast's own method, not a borrowed number.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DbSession
from app.core.logging import get_logger
from app.core.rbac import Permission, require_permission
from app.routers.analytics import BASELINE_DAYS, _median, _segment_legs
from app.schemas.analytics import DataStatus, Window
from app.schemas.predictions import (
    BacktestResult,
    CongestionResponse,
    CongestionSeries,
    ContributingFactor,
    ForecastPoint,
)

router = APIRouter(prefix="/api/v1/predictions", tags=["predictions"])
log = get_logger(__name__)

_read = require_permission(Permission.ANALYTICS_READ)

#: Width of one live bucket. Fixed rather than caller-chosen: the baseline,
#: the trend fit and the backtest all assume this width, and letting it vary
#: per request would need every one of those to be re-derived per call.
BUCKET_MINUTES = 5

#: How far back "current" reaches. Must comfortably hold TREND_BUCKETS (the
#: live fit) plus TREND_BUCKETS + BACKTEST_HOLDOUT_BUCKETS again further back
#: (the backtest's train+test split) with room to spare.
LOOKBACK_MINUTES = 90

#: Horizons the PS demo names explicitly (§12: "68% now → 81% in 15 min →
#: 89% in 30 min").
HORIZONS = (15, 30)

#: Recent buckets the live trend line is fit through (30 minutes at 5-minute
#: buckets). Also the training-set size for the backtest, so both trend lines
#: are fit from the same amount of history.
TREND_BUCKETS = 6

#: Held-out buckets the backtest checks its forecast against.
BACKTEST_HOLDOUT_BUCKETS = 6

#: Below this many points, `_fit_line` refuses to fit — two points define a
#: line exactly, which is indistinguishable from noise.
MIN_TREND_SAMPLES = 3

#: Below this many held-out points, a backtest MAE is one or two lucky (or
#: unlucky) buckets, not a measurement.
MIN_BACKTEST_SAMPLES = 2

#: An hourly baseline built from fewer than this many distinct calendar days
#: is a single day's pattern wearing an average's clothes.
MIN_BASELINE_DAYS = 3

#: Slope magnitude (index points/minute) below which a trend is reported as
#: "steady" rather than "rising"/"falling" — keeps sampling noise from
#: reading as a real trend.
SLOPE_EPS = 0.5

#: Fewer upstream legs than this and "trend" is one or two vehicles, not a
#: pattern.
MIN_UPSTREAM_LEGS = 2

#: Fewer plausible legs than this, split across the two halves of the
#: lookback window, and a speed comparison is noise.
MIN_SPEED_LEGS_PER_HALF = 2

#: Speed-change magnitude (%) below which the speed factor is omitted rather
#: than reported as a false leading indicator.
SPEED_CHANGE_THRESHOLD_PCT = 10.0

MAX_ROWS = 200


async def _bucketed_flow(
    session: AsyncSession, group_by: str, start: datetime, end: datetime, corridor: str | None
) -> list[Any]:
    """Vehicle count per fixed-width bucket, by camera or corridor.

    Same shape as `analytics.flow`'s query, fixed to `BUCKET_MINUTES` because
    the trend fit and the backtest both assume a constant bucket width.
    """
    grouping = "c.corridor" if group_by == "corridor" else "c.camera_code"
    sql = f"""
        SELECT
            time_bucket(INTERVAL '{BUCKET_MINUTES} minutes', d.ts) AS bucket,
            {grouping}                                             AS key,
            max(c.name)                                            AS label,
            max(c.corridor)                                        AS corridor,
            count(*)                                               AS vehicles
        FROM detections d
        JOIN cameras c ON c.id = d.camera_id
        WHERE d.ts >= :start AND d.ts < :end
          AND (CAST(:corridor AS text) IS NULL OR c.corridor = :corridor)
          AND {grouping} IS NOT NULL
        GROUP BY bucket, key
        ORDER BY key, bucket
    """
    rows = await session.execute(text(sql), {"start": start, "end": end, "corridor": corridor})
    return list(rows.mappings())


async def _hourly_baseline(
    session: AsyncSession, group_by: str, start: datetime, end: datetime, corridor: str | None
) -> dict[tuple[str, int], tuple[float, int]]:
    """Typical vehicles per bucket, by (key, hour-of-day), over `[start, end)`.

    Same "same time of day, earlier days" shape as `analytics._travel_times`'
    baseline, applied to volume: total vehicles seen in that hour across the
    days observed, divided by (days × buckets-per-hour) to land back in
    per-bucket units comparable to a live bucket's count.
    """
    grouping = "c.corridor" if group_by == "corridor" else "c.camera_code"
    sql = f"""
        SELECT
            {grouping}                              AS key,
            EXTRACT(HOUR FROM d.ts)::int             AS hour,
            count(*)                                 AS vehicles,
            count(DISTINCT date_trunc('day', d.ts))  AS days
        FROM detections d
        JOIN cameras c ON c.id = d.camera_id
        WHERE d.ts >= :start AND d.ts < :end
          AND (CAST(:corridor AS text) IS NULL OR c.corridor = :corridor)
          AND {grouping} IS NOT NULL
        GROUP BY key, hour
    """
    rows = await session.execute(text(sql), {"start": start, "end": end, "corridor": corridor})
    buckets_per_hour = 60 // BUCKET_MINUTES
    baseline: dict[tuple[str, int], tuple[float, int]] = {}
    for row in rows.mappings():
        days = row["days"] or 0
        if days < MIN_BASELINE_DAYS:
            continue
        baseline[(row["key"], row["hour"])] = (row["vehicles"] / (days * buckets_per_hour), days)
    return baseline


def _fit_line(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Ordinary least squares over (minutes-since-origin, index_pct) pairs.

    Returns `(slope, intercept)`, or `None` when there are too few points or
    they share one x value (a vertical "line" has no slope in y = a + bx).
    """
    n = len(points)
    if n < MIN_TREND_SAMPLES:
        return None
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    ss_xx = sum((x - mean_x) ** 2 for x, _ in points)
    if ss_xx == 0:
        return None
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _backtest(indexed: list[tuple[datetime, float, int]]) -> BacktestResult:
    """Fit on the earliest `TREND_BUCKETS` indexed points, "forecast" the
    following points, and score against what was actually observed there.

    `indexed` is chronological. This is exactly what the live forecast does
    at the end of the window, just run against a slice that has already
    happened, so the error is measurable today rather than promised.
    """
    if len(indexed) < TREND_BUCKETS + MIN_BACKTEST_SAMPLES:
        return BacktestResult(status="insufficient_data", samples=0, mae_pct=None)

    train = indexed[:TREND_BUCKETS]
    test = indexed[TREND_BUCKETS : TREND_BUCKETS + BACKTEST_HOLDOUT_BUCKETS]
    if len(test) < MIN_BACKTEST_SAMPLES:
        return BacktestResult(status="insufficient_data", samples=0, mae_pct=None)

    origin = train[0][0]
    fit = _fit_line([((ts - origin).total_seconds() / 60.0, idx) for ts, idx, _ in train])
    if fit is None:
        return BacktestResult(status="insufficient_data", samples=0, mae_pct=None)
    slope, intercept = fit

    errors = []
    for ts, actual, _ in test:
        x = (ts - origin).total_seconds() / 60.0
        predicted = max(0.0, intercept + slope * x)
        errors.append(abs(predicted - actual))

    return BacktestResult(status="ok", samples=len(errors), mae_pct=round(sum(errors) / len(errors), 2))


def _upstream_factor(
    legs: list[Any], lookback_start: datetime, now: datetime
) -> ContributingFactor | None:
    """Is inflow from the cameras that feed this one rising or falling?

    `legs` are observed journeys' arrivals at this camera, from the same
    `_segment_legs` the P4 route-density and speed endpoints already use —
    real adjacency from real journeys, not a modelled road graph.
    """
    if len(legs) < MIN_UPSTREAM_LEGS:
        return None
    midpoint = lookback_start + (now - lookback_start) / 2
    earlier = sum(1 for leg in legs if leg["arrived_at"] < midpoint)
    later = len(legs) - earlier
    upstream_cameras = {leg["from_camera"] for leg in legs}
    span_min = int((now - lookback_start).total_seconds() // 60)
    trend = "rising" if later > earlier else "falling" if later < earlier else "steady"
    return ContributingFactor(
        factor="upstream_inflow",
        detail=(
            f"{len(upstream_cameras)} upstream camera(s) sent {len(legs)} observed "
            f"arrival(s) in the last {span_min} min ({trend}: {earlier} in the "
            f"earlier half, {later} in the later half)."
        ),
    )


def _speed_factor(
    legs: list[Any], lookback_start: datetime, now: datetime
) -> ContributingFactor | None:
    """Is the approach speed into this camera easing — an early sign of
    building congestion before volume itself shows it?

    Frequently absent on the replayed demo fleet, same as `analytics.speed`:
    a replayed clip makes most legs read as physically implausible, so there
    are rarely enough plausible legs to compare. That is documented, expected
    behaviour here for the same reason it is there, not a bug in this module.
    """
    plausible = [leg for leg in legs if leg["plausible"] and leg["implied_kmph"] is not None]
    if len(plausible) < 2 * MIN_SPEED_LEGS_PER_HALF:
        return None
    midpoint = lookback_start + (now - lookback_start) / 2
    earlier = [float(leg["implied_kmph"]) for leg in plausible if leg["arrived_at"] < midpoint]
    later = [float(leg["implied_kmph"]) for leg in plausible if leg["arrived_at"] >= midpoint]
    if len(earlier) < MIN_SPEED_LEGS_PER_HALF or len(later) < MIN_SPEED_LEGS_PER_HALF:
        return None
    earlier_med = _median(earlier)
    later_med = _median(later)
    if not earlier_med:
        return None
    delta_pct = round((later_med - earlier_med) / earlier_med * 100.0, 1)
    if abs(delta_pct) < SPEED_CHANGE_THRESHOLD_PCT:
        return None
    direction = "easing" if delta_pct < 0 else "improving"
    return ContributingFactor(
        factor="speed_trend",
        detail=(
            f"Approach speed {direction} — median {'-' if delta_pct < 0 else '+'}"
            f"{abs(delta_pct):.0f}% between the two halves of the recent window "
            f"({len(plausible)} plausible legs)."
        ),
    )


def _build_series(
    *,
    key: str,
    rows: list[Any],
    baseline: dict[tuple[str, int], tuple[float, int]],
    baseline_keys: set[str],
    legs: list[Any],
    group_by: str,
    lookback_start: datetime,
    now: datetime,
) -> CongestionSeries:
    label = key if group_by == "corridor" else (rows[-1]["label"] or key)
    corridor_val = rows[-1]["corridor"]
    empty_forecasts = [ForecastPoint(horizon_minutes=h, index_pct=None) for h in HORIZONS]
    empty_backtest = BacktestResult(status="insufficient_data", samples=0, mae_pct=None)

    indexed: list[tuple[datetime, float, int]] = []
    for row in rows:
        entry = baseline.get((key, row["bucket"].hour))
        if entry is None:
            continue
        avg_bucket_flow, days = entry
        if avg_bucket_flow <= 0:
            continue
        indexed.append((row["bucket"], 100.0 * row["vehicles"] / avg_bucket_flow, days))

    if not indexed:
        status: DataStatus = "insufficient_history" if key not in baseline_keys else "insufficient_data"
        return CongestionSeries(
            key=key,
            label=label,
            corridor=corridor_val,
            status=status,
            current_index_pct=None,
            current_bucket_vehicles=None,
            trend_samples=0,
            baseline_days=0,
            trend_per_minute_pct=None,
            forecasts=empty_forecasts,
            factors=[],
            backtest=empty_backtest,
        )

    last_ts, last_index, _ = indexed[-1]
    current_vehicles = next((r["vehicles"] for r in rows if r["bucket"] == last_ts), None)

    trend_points = indexed[-TREND_BUCKETS:]
    origin = trend_points[0][0]
    fit = _fit_line([((ts - origin).total_seconds() / 60.0, idx) for ts, idx, _ in trend_points])

    forecasts = empty_forecasts
    trend_per_minute = None
    factors: list[ContributingFactor] = []
    if fit is not None:
        slope, intercept = fit
        trend_per_minute = round(slope, 3)
        now_x = (now - origin).total_seconds() / 60.0
        forecasts = [
            ForecastPoint(
                horizon_minutes=h,
                index_pct=round(max(0.0, intercept + slope * (now_x + h)), 1),
            )
            for h in HORIZONS
        ]
        span_minutes = int((trend_points[-1][0] - trend_points[0][0]).total_seconds() // 60)
        span_minutes = span_minutes or BUCKET_MINUTES
        if slope > SLOPE_EPS:
            detail = f"Rising — volume index up {slope:.1f} pts/min over the last {span_minutes} min."
        elif slope < -SLOPE_EPS:
            detail = f"Falling — volume index down {abs(slope):.1f} pts/min over the last {span_minutes} min."
        else:
            detail = f"Steady — volume index flat over the last {span_minutes} min."
        factors.append(ContributingFactor(factor="inflow_trend", detail=detail))

    if group_by == "camera":
        upstream = _upstream_factor(legs, lookback_start, now)
        if upstream is not None:
            factors.append(upstream)
        speed = _speed_factor(legs, lookback_start, now)
        if speed is not None:
            factors.append(speed)

    return CongestionSeries(
        key=key,
        label=label,
        corridor=corridor_val,
        status="ok" if fit is not None else "insufficient_data",
        current_index_pct=round(last_index, 1),
        current_bucket_vehicles=current_vehicles,
        trend_samples=len(trend_points),
        baseline_days=max((d for _, _, d in indexed), default=0),
        trend_per_minute_pct=trend_per_minute,
        forecasts=forecasts,
        factors=factors,
        backtest=_backtest(indexed),
    )


@router.get(
    "/congestion",
    response_model=CongestionResponse,
    summary="Short-horizon congestion forecast, per camera or corridor",
)
async def congestion(
    session: DbSession,
    _user: Annotated[object, Depends(_read)],
    at: Annotated[
        datetime | None, Query(description="Forecast as of this instant, UTC. Defaults to now.")
    ] = None,
    group_by: Annotated[str, Query(pattern="^(camera|corridor)$")] = "camera",
    corridor: str | None = None,
) -> CongestionResponse:
    now = at or datetime.now(UTC)
    lookback_start = now - timedelta(minutes=LOOKBACK_MINUTES)
    baseline_start = lookback_start - timedelta(days=BASELINE_DAYS)

    live_rows = await _bucketed_flow(session, group_by, lookback_start, now, corridor)
    baseline = await _hourly_baseline(session, group_by, baseline_start, lookback_start, corridor)
    baseline_keys = {k for k, _hour in baseline}

    leg_rows_by_to: dict[str, list[Any]] = {}
    if group_by == "camera":
        for leg in await _segment_legs(session, lookback_start, now):
            if leg["to_camera"]:
                leg_rows_by_to.setdefault(leg["to_camera"], []).append(leg)

    grouped: dict[str, list[Any]] = {}
    for row in live_rows:
        grouped.setdefault(row["key"], []).append(row)

    series = [
        _build_series(
            key=key,
            rows=rows,
            baseline=baseline,
            baseline_keys=baseline_keys,
            legs=leg_rows_by_to.get(key, []),
            group_by=group_by,
            lookback_start=lookback_start,
            now=now,
        )
        for key, rows in grouped.items()
    ]

    return CongestionResponse(
        window=Window(start=lookback_start, end=now, bucket_seconds=BUCKET_MINUTES * 60),
        group_by=group_by,  # type: ignore[arg-type]
        baseline_window_days=BASELINE_DAYS,
        horizons_minutes=list(HORIZONS),
        series=sorted(series, key=lambda s: s.key)[:MAX_ROWS],
    )
