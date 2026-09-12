"""City traffic analytics — the response contract.

## The rule every shape here enforces

Analytics is where invented numbers do the most damage. A congestion
percentage or an average speed looks authoritative, survives the demo, and
destroys the claim when someone asks where it came from. So every figure in
this module is computed from observed rows, and **every figure carries the
evidence it was computed from**.

That is why nearly every model has a `samples` field. A median speed over four
legs and a median over four thousand are not the same claim, and a UI that
cannot tell them apart will present them identically. Where there is not enough
data, the models say so explicitly — `status="insufficient_history"` with a
null figure — rather than returning a plausible number.

## Provenance

`SpeedProvenance` exists because the demo fleet replays recorded clips. A
replayed clip repeats every P seconds, so the largest possible gap between two
cameras seeing the same vehicle is P — and with cameras 2-6 km apart on real
Ahmedabad arterials, every implied speed lands in the hundreds or thousands of
km/h. That is a property of replay, not a bug, and not something a caller can
infer from a number alone.

So the speed endpoint reports only legs the correlator considers physically
plausible (≤150 km/h), states how many it excluded, and labels where the
figures came from. On replayed footage that usually means *no* legs survive and
the corridor reports `insufficient_data` — which is the honest answer, and
which starts producing real figures the moment the pipeline runs on real
footage, with no change to this contract.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: Why a figure is absent. Kept as a closed set so a UI can branch on it
#: exhaustively instead of pattern-matching prose.
DataStatus = Literal["ok", "insufficient_history", "insufficient_data"]


class Window(BaseModel):
    """The interval a response covers, echoed back so a chart can label itself."""

    start: datetime
    end: datetime
    bucket_seconds: int = Field(
        description="Width of one time bucket. 0 when the response is not bucketed."
    )


# ── flow: how many vehicles, where, when ──────────────────────────────
class FlowPoint(BaseModel):
    """One time bucket at one place."""

    ts: datetime = Field(description="Start of the bucket, UTC.")
    vehicles: int = Field(description="Detections in this bucket.")
    with_plate: int = Field(
        description=(
            "Detections that produced a readable plate. Always ≤ vehicles: the "
            "pipeline declines to read plates it cannot resolve, and the gap "
            "between these two is a real measure of read rate, not a loss."
        )
    )


class FlowSeries(BaseModel):
    """One camera's or corridor's series through the window."""

    key: str = Field(description="Camera code, or corridor name when grouped by corridor.")
    label: str
    corridor: str | None = None
    camera_id: uuid.UUID | None = None
    points: list[FlowPoint]
    total: int


class FlowResponse(BaseModel):
    window: Window
    group_by: Literal["camera", "corridor"]
    series: list[FlowSeries]
    total_vehicles: int


# ── speed: how fast the road is moving ────────────────────────────────
class SpeedProvenance(BaseModel):
    """Where a speed figure came from, and what was thrown away reaching it.

    Reported alongside every speed so the number is never presented bare.
    """

    source: Literal["observed_journeys"] = "observed_journeys"
    legs_considered: int
    legs_excluded_implausible: int = Field(
        description=(
            "Legs discarded for implying a speed above the correlator's "
            "physical ceiling. On replayed footage this is usually all of them."
        )
    )
    note: str


class SegmentSpeed(BaseModel):
    """Speed along one camera-to-camera segment."""

    from_camera: str
    to_camera: str
    corridor: str | None
    distance_km: float = Field(
        description="Great-circle between the two cameras — a lower bound on road distance."
    )
    status: DataStatus
    samples: int = Field(description="Plausible legs behind the figures below.")
    median_kmph: float | None = None
    p85_kmph: float | None = None
    median_seconds: float | None = None


class CorridorSpeed(BaseModel):
    corridor: str
    status: DataStatus
    samples: int
    median_kmph: float | None = None
    segments: list[SegmentSpeed]


class SpeedResponse(BaseModel):
    window: Window
    provenance: SpeedProvenance
    corridors: list[CorridorSpeed]


# ── route density: which way traffic actually goes ────────────────────
class RoutePair(BaseModel):
    """An ordered camera-to-camera movement, and how often it was observed."""

    from_camera: str
    to_camera: str
    from_corridor: str | None
    to_corridor: str | None
    journeys: int
    distance_km: float
    median_gap_seconds: float
    #: True when both cameras are on the same corridor. A movement between
    #: corridors is a real turn; one within a corridor is a vehicle continuing
    #: along the same road, and they answer different planning questions.
    same_corridor: bool


class RouteDensityResponse(BaseModel):
    window: Window
    pairs: list[RoutePair]
    total_journeys: int


# ── travel time against a baseline ────────────────────────────────────
class TravelTime(BaseModel):
    """Now versus normal, for one segment.

    `baseline_seconds` is the median of the same segment in the same
    time-of-day bucket across earlier days. With a young database there are no
    earlier days, so `status` is `insufficient_history` and every figure below
    it is null. That is the state the demo will usually be in, and it must
    render as "insufficient history" rather than as zero.
    """

    from_camera: str
    to_camera: str
    corridor: str | None
    status: DataStatus
    current_seconds: float | None = None
    current_samples: int = 0
    baseline_seconds: float | None = None
    baseline_samples: int = 0
    baseline_days: int = 0
    delta_pct: float | None = Field(
        default=None,
        description="Positive means slower than baseline. Null unless both figures exist.",
    )


class TravelTimeResponse(BaseModel):
    window: Window
    segments: list[TravelTime]
    #: How many days of history the baseline was allowed to draw on.
    baseline_window_days: int


# ── hotspots ──────────────────────────────────────────────────────────
class Hotspot(BaseModel):
    camera_code: str
    camera_name: str
    corridor: str | None
    lat: float
    lon: float
    vehicles: int
    #: Share of the busiest camera's count, 0-1. Lets a map size a marker
    #: without the client needing the whole list to find the maximum.
    intensity: float


class HotspotResponse(BaseModel):
    window: Window
    by_volume: list[Hotspot]
    #: Ranked by slowdown against baseline. Empty with a note when no baseline
    #: exists — never a volume ranking relabelled as congestion.
    by_slowdown: list[TravelTime]
    slowdown_status: DataStatus
    note: str


# ── heatmap ───────────────────────────────────────────────────────────
class HeatmapFeatureProperties(BaseModel):
    camera_code: str
    camera_name: str
    corridor: str | None
    vehicles: int
    intensity: float


class HeatmapGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: list[float] = Field(description="[lon, lat], GeoJSON order.")


class HeatmapFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: HeatmapGeometry
    properties: HeatmapFeatureProperties


class HeatmapResponse(BaseModel):
    """GeoJSON, so MapLibre can consume it as a source without translation.

    Shaped like the camera GeoJSON the map already renders, because the heatmap
    is a second weighting of the same points rather than a different dataset.
    """

    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[HeatmapFeature]
    window: Window
    max_vehicles: int
