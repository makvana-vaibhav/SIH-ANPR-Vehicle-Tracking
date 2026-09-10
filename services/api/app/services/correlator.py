"""Cross-camera route reconstruction.

Turns "this plate was seen here, then here, then here" into a journey a police
officer can act on, and — just as importantly — says which parts of that
journey it does not believe.

## What a hop is

A **sighting** is one detection. A **hop** is one camera in the journey: every
sighting of that plate at that camera inside a dwell window collapses into one
hop, because a vehicle queueing at a junction produces dozens of detections and
a route that lists them all is unreadable.

## The distance argument, stated once

Distance between two cameras is **great-circle**, not road distance. Roads are
never shorter than the straight line between their endpoints, so:

* implied speed = straight-line distance / elapsed time is a **lower bound** on
  the speed actually driven.

That asymmetry decides how the results may be read. If the lower bound already
exceeds what a vehicle can do, the leg is impossible and something is wrong —
a cloned plate, or a misread. But a leg that looks plausible has only passed a
weak test: the real route may have been much longer and much faster. So
`implausible` is a finding, and `plausible` is merely the absence of one. The
two are not symmetric and the API does not present them as though they were.

## Nothing is silently dropped

A leg that fails plausibility stays in the route, flagged. A system that
quietly removes the inconvenient parts of a journey is not one a court should
trust, and the flagged legs are often the interesting ones — a cloned plate
shows up precisely as an impossible hop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from math import asin, atan2, cos, degrees, radians, sin, sqrt
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.intelligence import Detection
from app.models.registry import Camera

log = get_logger(__name__)

#: Sightings of one plate at one camera closer together than this are the same
#: visit. A vehicle at a signal produces a detection every few seconds; without
#: this a two-minute wait becomes forty hops.
DWELL_WINDOW = timedelta(minutes=5)

#: Above this implied speed the leg is impossible. 150 km/h is generous for
#: Indian highways — the point is not to flag fast driving but to catch the
#: same plate being in two places it cannot have travelled between, which is
#: what a cloned plate or a misread looks like.
MAX_PLAUSIBLE_KMPH = 150.0

#: Below this distance two cameras are effectively the same place, and implied
#: speed is meaningless — a metre of GPS error over four seconds is 900 km/h.
MIN_SEPARATION_M = 50.0

#: A gap longer than this between consecutive sightings means the vehicle went
#: somewhere unwatched. Not implausible — most of Gujarat has no camera — but
#: it bounds what the route can claim, so it is marked.
UNOBSERVED_GAP = timedelta(hours=1)

#: How far a camera's heading may differ from the direction of travel before it
#: is contradicted. Cameras see a cone, not a line, and a junction camera may
#: legitimately catch a vehicle at a wide angle.
HEADING_TOLERANCE_DEG = 100.0

EARTH_RADIUS_M = 6_371_008.8


# ─────────────────────────────────────────────────────────────────────
# Geometry
# ─────────────────────────────────────────────────────────────────────
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(min(1.0, sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from the first point to the second, 0–360."""
    p1, p2 = radians(lat1), radians(lat2)
    dl = radians(lon2 - lon1)
    x = sin(dl) * cos(p2)
    y = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
    return (degrees(atan2(x, y)) + 360.0) % 360.0


def angular_gap(a: float, b: float) -> float:
    """Smallest angle between two bearings, 0–180."""
    diff = abs((a - b) % 360.0)
    return min(diff, 360.0 - diff)


# ─────────────────────────────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Sighting:
    """One detection, flattened with the camera it came from."""

    detection_id: uuid.UUID
    ts: datetime
    camera_id: uuid.UUID
    camera_code: str
    camera_name: str
    city: str | None
    district: str | None
    lat: float
    lon: float
    heading_deg: float | None
    plate_confidence: float


@dataclass(slots=True)
class Hop:
    """One camera in the journey, and the leg that led to it."""

    camera_id: uuid.UUID
    camera_code: str
    camera_name: str
    city: str | None
    district: str | None
    lat: float
    lon: float
    arrived_at: datetime
    departed_at: datetime
    sightings: int
    best_confidence: float

    # ── the leg from the previous hop; all None for the first ──
    distance_m: float | None = None
    elapsed_s: float | None = None
    implied_kmph: float | None = None
    bearing: float | None = None
    #: Why this leg is not believed. Empty means nothing was found wrong, which
    #: is weaker than "this leg is correct" — see the module docstring.
    flags: list[str] = field(default_factory=list)

    @property
    def dwell_s(self) -> float:
        return (self.departed_at - self.arrived_at).total_seconds()

    @property
    def plausible(self) -> bool:
        return "implausible_speed" not in self.flags

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": str(self.camera_id),
            "camera_code": self.camera_code,
            "camera_name": self.camera_name,
            "city": self.city,
            "district": self.district,
            "lat": round(self.lat, 6),
            "lon": round(self.lon, 6),
            "arrived_at": self.arrived_at.isoformat(),
            "departed_at": self.departed_at.isoformat(),
            "dwell_s": round(self.dwell_s, 1),
            "sightings": self.sightings,
            "plate_confidence": round(self.best_confidence, 4),
            "distance_m": round(self.distance_m, 1) if self.distance_m is not None else None,
            "distance_km": (
                round(self.distance_m / 1000.0, 2) if self.distance_m is not None else None
            ),
            "elapsed_s": round(self.elapsed_s, 1) if self.elapsed_s is not None else None,
            "implied_kmph": (
                round(self.implied_kmph, 1) if self.implied_kmph is not None else None
            ),
            "bearing_deg": round(self.bearing, 1) if self.bearing is not None else None,
            "flags": list(self.flags),
            "plausible": self.plausible,
        }


@dataclass(slots=True)
class Route:
    """A reconstructed journey."""

    plate: str
    hops: list[Hop]
    window_from: datetime | None
    window_to: datetime | None

    @property
    def camera_count(self) -> int:
        return len({hop.camera_id for hop in self.hops})

    @property
    def first_seen(self) -> datetime | None:
        return self.hops[0].arrived_at if self.hops else None

    @property
    def last_seen(self) -> datetime | None:
        return self.hops[-1].departed_at if self.hops else None

    @property
    def distance_m(self) -> float:
        """Straight-line total. A floor on the distance actually driven."""
        return sum(h.distance_m or 0.0 for h in self.hops)

    @property
    def duration_s(self) -> float:
        if self.first_seen is None or self.last_seen is None:
            return 0.0
        return (self.last_seen - self.first_seen).total_seconds()

    @property
    def moving_s(self) -> float:
        """Time on legs that actually covered ground.

        Legs of zero length are excluded, and that exclusion is the whole
        point. A vehicle seen at one camera, then seen at that same camera 90
        minutes later, produces a `revisit` leg: 90 minutes of elapsed time
        across 0 metres. That is time parked, not time driving.

        A first version of this summed every leg's elapsed time, which made
        `moving_kmph` come out identical to `average_kmph` on exactly the
        journey the two figures exist to tell apart — a test caught it.
        """
        return sum(hop.elapsed_s or 0.0 for hop in self.hops if (hop.distance_m or 0.0) > 0.0)

    @property
    def average_kmph(self) -> float | None:
        """Journey average speed — a lower bound, for two compounding reasons.

        `distance_m` is great-circle between cameras rather than the road
        driven, so it is already a floor. `duration_s` runs from the first
        sighting to the last, so it *includes* time the vehicle spent
        stationary at a junction. Both push this figure down, which means the
        speed actually driven is at least this.

        None rather than 0.0 when there is nothing to divide: a single sighting
        has no speed, and "0 km/h" is a claim we would not be able to defend.
        """
        if self.duration_s <= 0 or self.distance_m <= 0:
            return None
        return round(self.distance_m / self.duration_s * 3.6, 1)

    @property
    def moving_kmph(self) -> float | None:
        """Average speed while moving, ignoring dwell.

        Reported alongside `average_kmph` because they answer different
        questions: a vehicle that covered 8 km in 40 minutes with 25 of them
        parked was not driving at 12 km/h, and an operator judging whether a
        journey looks normal needs to be able to tell those apart.
        """
        if self.moving_s <= 0 or self.distance_m <= 0:
            return None
        return round(self.distance_m / self.moving_s * 3.6, 1)

    @property
    def flagged_hops(self) -> list[Hop]:
        return [h for h in self.hops if h.flags]

    @property
    def is_plausible(self) -> bool:
        """False when any leg is outright impossible."""
        return all(h.plausible for h in self.hops)

    @property
    def confidence(self) -> float:
        """How much to trust this route as a whole.

        The weakest plate read in it, penalised for every leg that could not
        be believed. A route is only as good as its worst link: one shaky
        reading in the middle can attach a different vehicle's journey to
        this one.
        """
        if not self.hops:
            return 0.0
        weakest = min(h.best_confidence for h in self.hops)
        impossible = sum(1 for h in self.hops if not h.plausible)
        return round(max(0.0, weakest - 0.25 * impossible), 4)

    def to_geojson(self) -> dict[str, Any]:
        """The route as GeoJSON: a line for the path, a point per hop.

        The line is straight segments between cameras and is labelled as such
        in its properties. Drawing it as though it followed the road would be
        inventing a path we have no evidence for.
        """
        features: list[dict[str, Any]] = []

        if len(self.hops) > 1:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[h.lon, h.lat] for h in self.hops],
                    },
                    "properties": {
                        "kind": "route",
                        "plate": self.plate,
                        "geometry_note": (
                            "Straight lines between cameras, not the roads driven. "
                            "Distances are therefore a lower bound."
                        ),
                        "hop_count": len(self.hops),
                        "camera_count": self.camera_count,
                        "distance_km": round(self.distance_m / 1000.0, 2),
                        "duration_s": round(self.duration_s, 1),
                        "is_plausible": self.is_plausible,
                        "confidence": self.confidence,
                    },
                }
            )

        for index, hop in enumerate(self.hops):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [hop.lon, hop.lat]},
                    "properties": {"kind": "hop", "sequence": index, **hop.to_dict()},
                }
            )

        return {"type": "FeatureCollection", "features": features}

    def to_dict(self) -> dict[str, Any]:
        return {
            "plate": self.plate,
            "window": {
                "from": self.window_from.isoformat() if self.window_from else None,
                "to": self.window_to.isoformat() if self.window_to else None,
            },
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "hop_count": len(self.hops),
            "camera_count": self.camera_count,
            "distance_km": round(self.distance_m / 1000.0, 2),
            "duration_s": round(self.duration_s, 1),
            "moving_s": round(self.moving_s, 1),
            "average_kmph": self.average_kmph,
            "moving_kmph": self.moving_kmph,
            "is_plausible": self.is_plausible,
            "confidence": self.confidence,
            "flagged_hop_count": len(self.flagged_hops),
            "hops": [hop.to_dict() for hop in self.hops],
            "geometry_note": (
                "Distances are great-circle between cameras, not road distances. "
                "Implied speeds are therefore a lower bound on the speed driven. "
                "average_kmph spans first to last sighting and so includes dwell; "
                "moving_kmph excludes it."
            ),
        }


# ─────────────────────────────────────────────────────────────────────
# Building a route
# ─────────────────────────────────────────────────────────────────────
def cluster_into_hops(sightings: list[Sighting]) -> list[Hop]:
    """Collapse consecutive same-camera sightings into one hop each.

    Consecutive is deliberate. A vehicle that passes camera A, goes to B, and
    comes back to A has genuinely visited A twice, and merging those into one
    hop would erase the return leg — which for a route that matters is often
    the whole point.
    """
    hops: list[Hop] = []
    for sighting in sorted(sightings, key=lambda s: s.ts):
        current = hops[-1] if hops else None
        if (
            current is not None
            and current.camera_id == sighting.camera_id
            and sighting.ts - current.departed_at <= DWELL_WINDOW
        ):
            current.departed_at = sighting.ts
            current.sightings += 1
            current.best_confidence = max(current.best_confidence, sighting.plate_confidence)
            continue

        hops.append(
            Hop(
                camera_id=sighting.camera_id,
                camera_code=sighting.camera_code,
                camera_name=sighting.camera_name,
                city=sighting.city,
                district=sighting.district,
                lat=sighting.lat,
                lon=sighting.lon,
                arrived_at=sighting.ts,
                departed_at=sighting.ts,
                sightings=1,
                best_confidence=sighting.plate_confidence,
            )
        )
    return hops


def score_legs(hops: list[Hop], headings: dict[uuid.UUID, float | None]) -> None:
    """Fill in distance, speed and flags for each leg. Mutates in place."""
    for previous, hop in pairwise(hops):
        distance = haversine_m(previous.lat, previous.lon, hop.lat, hop.lon)
        # Time from leaving the previous camera to arriving at this one. Using
        # first-sighting to first-sighting would charge the vehicle for the
        # time it sat at the previous junction and understate its speed.
        elapsed = (hop.arrived_at - previous.departed_at).total_seconds()

        hop.distance_m = distance
        hop.elapsed_s = elapsed
        hop.bearing = (
            bearing_deg(previous.lat, previous.lon, hop.lat, hop.lon)
            if distance >= MIN_SEPARATION_M
            else None
        )

        if previous.camera_id == hop.camera_id:
            # The vehicle came back to a camera it had already passed. Distinct
            # from two cameras that happen to sit close together: this is a
            # fact about the journey — it doubled back, or waited nearby and
            # returned — not an artefact of where the cameras were installed.
            hop.flags.append("revisit")
            continue

        if distance < MIN_SEPARATION_M:
            # Two different cameras on the same gantry. A speed here would be
            # an artefact of position error, not a measurement.
            hop.flags.append("co_located")
            continue

        if elapsed <= 0:
            # The same plate at two separated cameras at the same instant. One
            # vehicle cannot do that: it is a cloned plate or a misread, and it
            # is the single most useful thing this function can surface.
            hop.implied_kmph = None
            hop.flags.append("impossible_simultaneous")
            hop.flags.append("implausible_speed")
            continue

        hop.implied_kmph = (distance / elapsed) * 3.6
        if hop.implied_kmph > MAX_PLAUSIBLE_KMPH:
            hop.flags.append("implausible_speed")

        if timedelta(seconds=elapsed) > UNOBSERVED_GAP:
            # Not a fault — most roads have no camera. It bounds what the route
            # may be read as claiming, so it is stated.
            hop.flags.append("unobserved_gap")

        heading = headings.get(previous.camera_id)
        if (
            heading is not None
            and hop.bearing is not None
            and angular_gap(heading, hop.bearing) > HEADING_TOLERANCE_DEG
        ):
            hop.flags.append("heading_conflict")


async def sightings_for(
    session: AsyncSession,
    plate: str,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 5000,
) -> list[Sighting]:
    """Every sighting of one plate, with its camera's position."""
    location = Camera.location.cast(Geometry)
    query = (
        select(
            Detection.id,
            Detection.ts,
            Detection.plate_confidence,
            Camera.id,
            Camera.camera_code,
            Camera.name,
            Camera.city,
            Camera.district,
            Camera.heading_deg,
            func.ST_Y(location),
            func.ST_X(location),
        )
        .join(Camera, Camera.id == Detection.camera_id)
        .where(Detection.plate_normalised == plate)
        .order_by(Detection.ts.asc())
        .limit(limit)
    )
    if since is not None:
        query = query.where(Detection.ts >= since)
    if until is not None:
        query = query.where(Detection.ts <= until)

    rows = (await session.execute(query)).all()
    return [
        Sighting(
            detection_id=row[0],
            ts=row[1],
            plate_confidence=row[2] or 0.0,
            camera_id=row[3],
            camera_code=row[4],
            camera_name=row[5],
            city=row[6],
            district=row[7],
            heading_deg=row[8],
            lat=row[9],
            lon=row[10],
        )
        # A camera with no position cannot be placed on a route. Including it
        # would put the vehicle at the origin of the coordinate system.
        for row in rows
        if row[9] is not None and row[10] is not None
    ]


async def build_route(
    session: AsyncSession,
    plate: str,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Route:
    """Reconstruct one plate's journey."""
    sightings = await sightings_for(session, plate, since, until)
    hops = cluster_into_hops(sightings)
    headings = {s.camera_id: s.heading_deg for s in sightings}
    score_legs(hops, headings)
    return Route(plate=plate, hops=hops, window_from=since, window_to=until)


# ─────────────────────────────────────────────────────────────────────
# Convoys
# ─────────────────────────────────────────────────────────────────────
def _within_one_edit(a: str, b: str) -> bool:
    """True when one substitution, insertion or deletion turns a into b.

    Shared with the watchlist matcher's notion of a near miss, and used here
    for the opposite purpose: there it widens a search, here it identifies a
    "convoy partner" that is really the same vehicle read twice.
    """
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(1 for x, y in zip(a, b, strict=True) if x != y) <= 1

    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    return any(shorter == longer[:index] + longer[index + 1 :] for index in range(len(longer)))


@dataclass(slots=True)
class Convoy:
    """Two plates travelling together."""

    plate: str
    with_plate: str
    shared_cameras: int
    first_together: datetime
    last_together: datetime
    median_gap_s: float
    #: True when the partner plate is within one character of the subject.
    #: Almost always one vehicle read two ways rather than two vehicles: OCR
    #: producing GJ03AB1234 and GJ03A81234 from the same car will co-occur at
    #: every camera perfectly, which is exactly what a convoy looks like.
    likely_same_vehicle: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "plate": self.plate,
            "with_plate": self.with_plate,
            "shared_cameras": self.shared_cameras,
            "first_together": self.first_together.isoformat(),
            "last_together": self.last_together.isoformat(),
            "median_gap_s": round(self.median_gap_s, 1),
            "likely_same_vehicle": self.likely_same_vehicle,
        }


async def find_convoys(
    session: AsyncSession,
    plate: str,
    since: datetime | None = None,
    until: datetime | None = None,
    window_s: float = 120.0,
    min_shared_cameras: int = 3,
) -> list[Convoy]:
    """Plates that keep appearing alongside this one.

    Two vehicles sharing one camera within two minutes is ordinary traffic.
    Sharing three or more, each time within the window, is a pattern — vehicles
    travelling together. The threshold is a parameter because what counts as a
    convoy is an operational judgement, not a fact about the data.
    """
    subject = await sightings_for(session, plate, since, until)
    if not subject:
        return []

    lower = min(s.ts for s in subject) - timedelta(seconds=window_s)
    upper = max(s.ts for s in subject) + timedelta(seconds=window_s)
    cameras = {s.camera_id for s in subject}

    others = (
        await session.execute(
            select(Detection.plate_normalised, Detection.ts, Detection.camera_id)
            .where(
                Detection.camera_id.in_(cameras),
                Detection.ts >= lower,
                Detection.ts <= upper,
                Detection.plate_normalised.isnot(None),
                Detection.plate_normalised != "",
                Detection.plate_normalised != plate,
            )
            .limit(50_000)
        )
    ).all()

    # camera → the times this plate was there, so a candidate only has to be
    # compared against the sightings that could possibly match.
    by_camera: dict[uuid.UUID, list[datetime]] = {}
    for s in subject:
        by_camera.setdefault(s.camera_id, []).append(s.ts)

    # Gaps and the times they happened are recorded together. Recovering the
    # times afterwards by re-scanning for the plate would pick up sightings at
    # a shared camera that were *not* within the window — so a convoy could
    # report a "first together" at which the two were hours apart.
    together: dict[str, dict[uuid.UUID, list[tuple[float, datetime]]]] = {}
    for other_plate, ts, camera_id in others:
        for mine in by_camera.get(camera_id, ()):
            gap = abs((ts - mine).total_seconds())
            if gap <= window_s:
                together.setdefault(other_plate, {}).setdefault(camera_id, []).append((gap, ts))
                break

    convoys: list[Convoy] = []
    for other_plate, per_camera in together.items():
        if len(per_camera) < min_shared_cameras:
            continue
        matched = [pair for pairs in per_camera.values() for pair in pairs]
        gaps = sorted(gap for gap, _ts in matched)
        times = [ts for _gap, ts in matched]
        convoys.append(
            Convoy(
                plate=plate,
                with_plate=other_plate,
                shared_cameras=len(per_camera),
                first_together=min(times),
                last_together=max(times),
                median_gap_s=gaps[len(gaps) // 2],
                likely_same_vehicle=_within_one_edit(plate, other_plate),
            )
        )

    # Genuine convoys first. A near-identical plate co-occurring perfectly is
    # the pipeline reading one car two ways, and leaving those at the top would
    # bury the real associations under an artefact of OCR.
    convoys.sort(key=lambda c: (c.likely_same_vehicle, -c.shared_cameras, c.median_gap_s))
    return convoys


# ─────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────
async def persist_route(session: AsyncSession, route: Route) -> uuid.UUID | None:
    """Store a reconstructed route. Returns None for a route with no path."""
    from app.models.intelligence import VehicleTrack

    if len(route.hops) < 2:
        return None

    # Built as WKT rather than with ST_MakeLine over a list of ST_MakePoint
    # calls. That was the original form and it never worked: SQLAlchemy renders
    # a Python list as an untyped array, PostGIS has several ST_MakeLine
    # overloads, and Postgres rejected the call outright with
    # "function st_makeline(unknown) is not unique". It went unnoticed because
    # persist_route had no caller until journey snapshots started using it.
    #
    # The coordinates are floats read out of PostGIS, and the WKT travels as a
    # bound parameter, so there is nothing here to interpolate unsafely.
    wkt = "LINESTRING({})".format(", ".join(f"{hop.lon:.7f} {hop.lat:.7f}" for hop in route.hops))
    line = func.ST_SetSRID(func.ST_GeomFromText(wkt), 4326)

    track = VehicleTrack(
        plate_normalised=route.plate,
        first_seen=route.first_seen,
        last_seen=route.last_seen,
        camera_count=route.camera_count,
        hop_count=len(route.hops),
        path=line,
        hops={"hops": [hop.to_dict() for hop in route.hops]},
        confidence=route.confidence,
        is_plausible=route.is_plausible,
    )
    session.add(track)
    await session.flush()
    log.info(
        "correlator.route_persisted",
        plate=route.plate,
        hops=len(route.hops),
        cameras=route.camera_count,
        plausible=route.is_plausible,
    )
    return track.id


async def recent_plates(
    session: AsyncSession, since: datetime | None = None, min_cameras: int = 2, limit: int = 50
) -> list[tuple[str, int]]:
    """Plates seen on several cameras — the ones with a route worth drawing."""
    window = since or (datetime.now(UTC) - timedelta(days=1))
    rows = (
        await session.execute(
            select(
                Detection.plate_normalised,
                func.count(func.distinct(Detection.camera_id)).label("cameras"),
            )
            .where(
                Detection.ts >= window,
                Detection.plate_normalised.isnot(None),
                Detection.plate_normalised != "",
            )
            .group_by(Detection.plate_normalised)
            .having(func.count(func.distinct(Detection.camera_id)) >= min_cameras)
            .order_by(func.count(func.distinct(Detection.camera_id)).desc())
            .limit(limit)
        )
    ).all()
    return [(row[0], row[1]) for row in rows]
