"""Predictive traffic — the response contract.

## What "congestion" means here, precisely

There is no road-capacity data in this system — no lane count, no
free-flow-speed rating, no signal timing. An absolute occupancy percentage
("this road is at 81% of capacity") cannot be computed honestly from what we
have, so this module never claims one.

What *can* be computed honestly: how a camera's current traffic volume
compares to **its own typical volume at this time of day**, from its own
observed history. `current_index_pct` is exactly that ratio, expressed as a
percentage where 100 means "matches the usual volume for this hour" and 150
means "50% busier than usual for this hour". It is a relative, self-baselined
measure, same spirit as `TravelTime.delta_pct` in `app.schemas.analytics` —
just applied to volume instead of duration, and it is documented as such
everywhere it is shown so nobody mistakes it for a capacity occupancy figure.

## The forecast

A short-horizon forecast is an ordinary-least-squares line fit through the
most recent index values, extrapolated forward. It is exactly as
sophisticated as that sentence says — no external traffic model, nothing
borrowed from a paper this session could not verify. What makes it honest is
that the same fitting procedure is used to backtest itself: the same method,
run against buckets already observed within the requested window, held out
the way the live forecast holds out the future. `BacktestResult.mae_pct` is
that method's own error, computed fresh on every response rather than quoted
from an offline run — see `app.routers.predictions` for the mechanics.

A prediction with no error bar is decoration (ROADMAP.md's P10 gate says
so explicitly). `backtest` travels with every forecast for that reason.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.analytics import DataStatus, Window

HorizonMinutes = Literal[15, 30]


class ForecastPoint(BaseModel):
    horizon_minutes: HorizonMinutes
    index_pct: float | None = Field(
        default=None,
        description="Forecast relative-volume index at now + horizon_minutes. Null if the trend could not be fit.",
    )


class ContributingFactor(BaseModel):
    """One reason behind a forecast — the explainability rule, applied here.

    Mirrors the shape of `app.services.anomaly.Reason`: `factor` is a stable
    machine-readable slug a UI can key off, `detail` is the human sentence.
    """

    factor: str
    detail: str


class BacktestResult(BaseModel):
    """How well this exact forecasting method did against buckets already
    observed inside the requested window — not a claim about the live
    forecast above, a measurement of the method that produced it."""

    status: DataStatus
    samples: int = Field(description="Held-out buckets the forecast was checked against.")
    mae_pct: float | None = Field(
        default=None,
        description="Mean absolute error, in index percentage points, between predicted and actual.",
    )


class CongestionSeries(BaseModel):
    """One camera's or corridor's current state, forecast and backtest."""

    key: str = Field(description="Camera code, or corridor name when grouped by corridor.")
    label: str
    corridor: str | None = None
    status: DataStatus
    current_index_pct: float | None = None
    current_bucket_vehicles: int | None = None
    trend_samples: int = Field(
        description="Recent buckets with a valid index, used to fit the live trend line."
    )
    baseline_days: int = Field(description="Distinct historical days behind this key's baseline.")
    trend_per_minute_pct: float | None = Field(
        default=None, description="Slope of the fitted line: index points per minute."
    )
    forecasts: list[ForecastPoint]
    factors: list[ContributingFactor]
    backtest: BacktestResult


class CongestionResponse(BaseModel):
    window: Window
    group_by: Literal["camera", "corridor"]
    baseline_window_days: int
    horizons_minutes: list[HorizonMinutes]
    series: list[CongestionSeries]
