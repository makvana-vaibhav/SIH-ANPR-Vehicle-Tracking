#!/usr/bin/env python3
"""Fetch Ahmedabad's arterial road geometry from OpenStreetMap.

## Why this exists

P1 places a camera fleet along Ahmedabad's real arterials, and every camera has
to sit on an **actual road vertex** rather than at an interpolated point that
may land inside a building, a lake or the Sabarmati. That needs real geometry,
which means OSM.

This script does the network half. It writes `data/seed/ahmedabad_roads.geojson`,
which is **committed**, so `generate_ahmedabad_fleet.py` is deterministic and
works offline: a judge cloning the repo never needs a network to reproduce the
fleet. Same split as `fetch_geodata.sh` → the committed district boundaries.

## Corridors are selected by ref where OSM has one

Selecting arterials by name is unreliable here. SG Highway carries the name
"SG Highway" on only 9 of its ways; the rest of the corridor is named for the
structures along it — "ISKCON Flyover", "Pakwan Flyover", "Thaltej Underpass",
"Sanand Chokdi Flyover". What every one of them shares is `ref=NH147`. The same
holds for Sardar Patel Ring Road (`NH47`/`NH48`) and Narol–Naroda (`SH2`).

So a corridor matches on `ref` **or** on a name pattern, whichever OSM actually
carries. Note the asymmetry: a `ref` may cover far *more* than the corridor —
`SH41` runs from Ahmedabad to Himatnagar, so using it for Ashram Road produced
a 13.4 km "Ashram Road" instead of the real ~3 km. Refs are used only where the
ref and the corridor are the same road.

## Vertices are ordered, not chained

The obvious approach — chain the ways end to end — does not work on this data
and was tried first. OSM splits each arterial wherever a differently-named
structure carries it across a junction, and those structures are excluded by
the name filter, so consecutive segments do not share endpoints: CG Road came
back as 20 fragments with only 5 shared endpoints, chaining to 0.86 km of a
2.5 km road.

What works is ordering every vertex along the corridor and thinning:

* **linear** corridors are ordered by projection onto the first principal axis
  of their own vertex cloud;
* **ring** corridors have no principal axis, so they are ordered by bearing
  around their centroid.

Both give a monotone "distance along the corridor", which is what camera
spacing needs, and every retained point is still a real OSM vertex. Measured
against reality this lands well: Sardar Patel Ring Road computes to a 74.4 km
circumference against a published ~76 km, SG Highway to 22.0 km against ~22 km,
and 132 Ft Ring Road to 18.5 km against ~18 km.

Usage:

    python scripts/fetch_ahmedabad_osm.py             # fetch and write
    python scripts/fetch_ahmedabad_osm.py --dry-run   # report, write nothing
    python scripts/fetch_ahmedabad_osm.py --raw f.json  # re-partition a saved response
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# Corridor names carry en-dashes and the report draws arrows. A Windows console
# defaults to cp1252 and raises UnicodeEncodeError on both, killing the script
# after the network fetch has already succeeded — the most annoying possible
# place to fail. Ask for UTF-8 explicitly; where it is already UTF-8 this is a
# no-op.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SEED = Path(__file__).resolve().parent.parent / "data" / "seed"
OUTPUT = _SEED / "ahmedabad_roads.geojson"
#: Localities give a camera a name an operator recognises. "SG Highway at
#: Thaltej" is a place; "SG Highway km 12.4" is a measurement.
LOCALITIES_OUTPUT = _SEED / "ahmedabad_localities.geojson"

#: Ahmedabad and its ring. Wide enough to hold the whole of Sardar Patel Ring
#: Road, which runs well outside the municipal boundary.
BBOX = (22.88, 72.40, 23.16, 72.78)  # south, west, north, east

#: Tried in order. The public Overpass instances are free, heavily used, and
#: return 504 often enough that a single-endpoint fetch fails perhaps one run
#: in three — which looks like "OSM has no Ahmedabad" to anybody running this
#: for the first time.
OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
)
USER_AGENT = "NagarNetra/0.1 (SIH26127; camera fleet geometry; contact via repo)"
TIMEOUT_S = 240
ATTEMPTS_PER_MIRROR = 2

#: Spacing of the thinned centreline. Fine enough that the fleet generator can
#: place a camera close to any target position, coarse enough to keep the
#: committed file small.
THIN_METRES = 150.0

#: A corridor shorter than this is a mis-match, not an arterial.
MIN_CORRIDOR_KM = 2.0

#: A "linear" corridor flatter than this is several roads caught by one
#: pattern. Real arterials here score 4-40; the five-roads-in-one blob scored
#: under 2.
MIN_LINEARITY = 3.0

EARTH_RADIUS_KM = 6371.0088
#: Degrees → kilometres near Ahmedabad (23°N). Used only to build a local
#: planar frame for ordering; every distance reported is great-circle.
KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON = 111.320


@dataclass(frozen=True)
class Corridor:
    """One arterial, and how to recognise its ways in OSM."""

    key: str
    name: str
    #: What the corridor is for, in journey terms. Carried into the GeoJSON so
    #: the fleet generator can say why a camera is where it is.
    role: str
    #: "linear" for a radial or through route, "ring" for an orbital. This
    #: picks the ordering, and a ring ordered as a line comes out as nonsense.
    shape: str = "linear"
    refs: tuple[str, ...] = ()
    name_pattern: str | None = None


CORRIDORS: tuple[Corridor, ...] = (
    Corridor(
        key="SG-HIGHWAY",
        name="Sarkhej–Gandhinagar Highway",
        role="north–south arterial, west city",
        shape="linear",
        refs=("NH147",),
        name_pattern=r"\bS\.?\s?G\.?\s+Highway\b",
    ),
    Corridor(
        key="SP-RING",
        name="Sardar Patel Ring Road",
        role="orbital, links every radial corridor",
        shape="ring",
        refs=("NH47", "NH48", "NH48;NH47", "NH47;NH48"),
        name_pattern=r"Sardar Patel Ring Road",
    ),
    Corridor(
        key="ASHRAM-ROAD",
        name="Ashram Road",
        role="north–south spine, city centre",
        shape="linear",
        # Deliberately no ref. SH41 continues to Himatnagar and pulled the
        # corridor out to 13.4 km — four times the real road.
        name_pattern=r"^Ashram Road$",
    ),
    Corridor(
        key="CG-ROAD",
        name="Chimanlal Girdharlal Road",
        role="central commercial street",
        shape="linear",
        name_pattern=r"Chimanlal Girdharlal Road|^C\.?\s?G\.? Road$",
    ),
    Corridor(
        key="132FT-RING",
        name="132 Ft. Ring Road",
        role="inner ring, east–west link",
        shape="ring",
        name_pattern=r"132\s*(Ft\.?|Feet)\s*Ring Road",
    ),
    Corridor(
        key="120FT-RING",
        name="120 Feet Ring Road",
        role="inner ring east, east–west link",
        shape="ring",
        name_pattern=r"120\s*(Ft\.?|Feet)\s*(Ring|Circular) Road",
    ),
    # There is no single "Narol–Naroda Road" in OSM, and asking for one by
    # pattern fused five unrelated roads into a blob: 28 cameras spread over
    # 77 km of a corridor 18.9 km long, because the principal axis of a blob
    # orders nothing. These are the three coherent corridors that pattern was
    # reaching for, and having them separate also gives the fleet the
    # east–west links a cross-city journey needs.
    Corridor(
        key="SH2-EAST",
        name="Rakhial–Odhav Road",
        role="east–west corridor, east city",
        shape="linear",
        refs=("SH2",),
        name_pattern=r"^(Rakhial Road|Odhav Road)$",
    ),
    Corridor(
        key="NARODA-ROAD",
        name="Naroda Road",
        role="north-east radial",
        shape="linear",
        name_pattern=r"^Naroda Road$",
    ),
    Corridor(
        key="NAROL-SARKHEJ",
        name="Narol–Sarkhej Road",
        role="east–west corridor, south city",
        shape="linear",
        name_pattern=r"^Narol.?Sarkhej Road$",
    ),
    Corridor(
        key="AHM-VAD-EXPWY",
        name="Ahmedabad–Vadodara Expressway",
        role="south-east intercity approach",
        shape="linear",
        refs=("NE1",),
        name_pattern=r"Ahmadabad Vadodara Expressway|Ahmedabad.Vadodara Expressway",
    ),
)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points, in kilometres."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def polyline_km(points: list[tuple[float, float]]) -> float:
    """Length of a polyline in kilometres."""
    return sum(haversine_km(points[i], points[i + 1]) for i in range(len(points) - 1))


def overpass_query() -> str:
    """Every named arterial way in the bounding box, with geometry.

    One query rather than eight. Overpass allows two concurrent slots and asks
    to be used gently; fetching the arterial network once and partitioning it
    locally is both faster and better behaved than a query per corridor.
    """
    south, west, north, east = BBOX
    return f"""
[out:json][timeout:{TIMEOUT_S}];
way["highway"~"^(motorway|trunk|primary|secondary)$"]["name"]
   ({south},{west},{north},{east});
out geom;
""".strip()


def localities_query() -> str:
    """Named neighbourhoods and suburbs, so cameras get recognisable names."""
    south, west, north, east = BBOX
    return f"""
[out:json][timeout:{TIMEOUT_S}];
(
  node["place"~"^(suburb|neighbourhood|quarter|village|town)$"]["name"]
      ({south},{west},{north},{east});
);
out body;
""".strip()


def _request(url: str, body: bytes) -> bytes:
    """POST the query to one Overpass endpoint."""
    request = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return response.read()


def fetch(raw_path: Path | None = None, save_raw: Path | None = None) -> list[dict]:
    """Run the Overpass query and return its way elements.

    ``raw_path`` re-reads a saved response instead of going to the network,
    which makes the partitioning reproducible offline and keeps development off
    a shared public service.
    """
    raw = (
        raw_path.read_bytes()
        if raw_path is not None
        else run_query(overpass_query(), min_bytes=10_000)
    )
    if raw_path is not None:
        print(f"  cached response: {len(raw):,} bytes from {raw_path}")

    if save_raw is not None:
        save_raw.write_bytes(raw)
        print(f"  saved raw response to {save_raw}")

    elements = json.loads(raw).get("elements", [])
    print(f"  Overpass: {len(raw):,} bytes, {len(elements)} named arterial ways")
    return elements


def run_query(query: str, *, min_bytes: int) -> bytes:
    """Send one Overpass query, trying each mirror in turn."""
    body = urllib.parse.urlencode({"data": query}).encode()
    failures: list[str] = []
    raw = b""

    for url in OVERPASS_MIRRORS:
        host = urllib.parse.urlparse(url).netloc
        for attempt in range(1, ATTEMPTS_PER_MIRROR + 1):
            try:
                candidate = _request(url, body)
            except urllib.error.HTTPError as exc:
                # 429 and 504 mean "busy", not "no data". Treating them as
                # fatal makes a working script look broken.
                failures.append(f"{host} HTTP {exc.code}")
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                failures.append(f"{host} {exc}")
            else:
                # A short body is retried, not accepted. Overpass under load
                # answers 200 with an empty element list, and taking that at
                # face value is how a corrupt file gets committed — the exact
                # failure `fetch_geodata.sh` was hardened against. Observed
                # once already: a 272-byte "success" for a query that returns
                # 21 KB when the server is healthy.
                if len(candidate) >= min_bytes:
                    raw = candidate
                    break
                failures.append(f"{host} returned {len(candidate)} bytes, expected ≥{min_bytes:,}")
            if attempt < ATTEMPTS_PER_MIRROR:
                time.sleep(5 * attempt)
        if raw:
            break

    if not raw:
        raise SystemExit(
            "No Overpass mirror returned a usable response:\n    "
            + "\n    ".join(failures)
            + "\n  These are free public services that rate-limit; wait a minute and retry."
        )

    return raw


def fetch_localities() -> list[dict]:
    """Named localities in the bounding box, as GeoJSON point features."""
    raw = run_query(localities_query(), min_bytes=2_000)
    elements = json.loads(raw).get("elements", [])

    features = []
    for element in elements:
        name = (element.get("tags", {}).get("name") or "").strip()
        if not name or element.get("lat") is None:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": name,
                    "place": element["tags"].get("place"),
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(element["lon"], 6), round(element["lat"], 6)],
                },
            }
        )

    print(f"  Overpass: {len(raw):,} bytes, {len(features)} named localities")
    return features


def matches(corridor: Corridor, tags: dict[str, str]) -> bool:
    """Whether an OSM way belongs to this corridor."""
    ref = (tags.get("ref") or "").strip()
    if ref and ref in corridor.refs:
        return True
    name = (tags.get("name") or "").strip()
    return bool(corridor.name_pattern and name and re.search(corridor.name_pattern, name))


def corridor_vertices(corridor: Corridor, elements: list[dict]) -> list[tuple[float, float]]:
    """Every distinct OSM vertex belonging to this corridor."""
    seen: set[tuple[float, float]] = set()
    for element in elements:
        if not matches(corridor, element.get("tags", {})):
            continue
        for point in element.get("geometry") or []:
            seen.add((round(point["lat"], 7), round(point["lon"], 7)))
    return sorted(seen)


def _local_frame(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Centroid and the longitude scale factor at that latitude."""
    lat0 = sum(p[0] for p in points) / len(points)
    lon0 = sum(p[1] for p in points) / len(points)
    return lat0, lon0, math.cos(math.radians(lat0))


def linearity(points: list[tuple[float, float]]) -> float:
    """How line-like a vertex cloud is: the ratio of its principal axes.

    A corridor is a line, so its vertices should be long in one direction and
    thin in the other. A value near 1 means a blob, and ordering a blob along
    "the" principal axis produces nonsense — which is exactly what a
    five-roads-in-one name pattern produced once already, placing 28 cameras
    over 77 km of an 18.9 km corridor.
    """
    lat0, lon0, klon = _local_frame(points)
    planar = [
        ((p[1] - lon0) * KM_PER_DEG_LON * klon, (p[0] - lat0) * KM_PER_DEG_LAT) for p in points
    ]
    sxx = sum(x * x for x, _ in planar)
    syy = sum(y * y for _, y in planar)
    sxy = sum(x * y for x, y in planar)
    trace, det = sxx + syy, sxx * syy - sxy * sxy
    root = math.sqrt(max(trace * trace / 4 - det, 0.0))
    major, minor = trace / 2 + root, trace / 2 - root
    return math.sqrt(major / minor) if minor > 1e-9 else float("inf")


def order_linear(
    points: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], list[float]]:
    """Order vertices along a corridor's principal axis.

    The first principal component of the vertex cloud is the direction the road
    runs, so projecting onto it gives a monotone distance along the corridor —
    which survives the fragmentation that defeats endpoint chaining.
    """
    lat0, lon0, klon = _local_frame(points)
    planar = [
        ((p[1] - lon0) * KM_PER_DEG_LON * klon, (p[0] - lat0) * KM_PER_DEG_LAT) for p in points
    ]

    # Principal eigenvector of the 2x2 covariance, in closed form.
    sxx = sum(x * x for x, _ in planar)
    syy = sum(y * y for _, y in planar)
    sxy = sum(x * y for x, y in planar)
    trace, det = sxx + syy, sxx * syy - sxy * sxy
    eigenvalue = trace / 2 + math.sqrt(max(trace * trace / 4 - det, 0.0))
    if abs(sxy) > 1e-12:
        vx, vy = sxy, eigenvalue - sxx
    else:
        # Axis-aligned cloud: the wider axis wins.
        vx, vy = (1.0, 0.0) if sxx >= syy else (0.0, 1.0)
    norm = math.hypot(vx, vy) or 1.0
    vx, vy = vx / norm, vy / norm

    distances = [x * vx + y * vy for x, y in planar]
    order = sorted(range(len(points)), key=lambda i: distances[i])
    return [points[i] for i in order], [distances[i] for i in order]


def order_ring(
    points: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], list[float]]:
    """Order vertices by bearing around the corridor's centroid.

    A ring road has no principal axis — its vertex cloud is isotropic, and
    projecting it onto one axis interleaves the near and far sides of the ring.
    Angle around the centroid is the ordering that means anything, and angle
    times the mean radius converts it into a distance along the ring.
    """
    lat0, lon0, klon = _local_frame(points)
    angles: list[float] = []
    radii: list[float] = []
    for lat, lon in points:
        x = (lon - lon0) * KM_PER_DEG_LON * klon
        y = (lat - lat0) * KM_PER_DEG_LAT
        angles.append(math.atan2(y, x))
        radii.append(math.hypot(x, y))

    mean_radius = sum(radii) / len(radii)
    order = sorted(range(len(points)), key=lambda i: angles[i])
    return [points[i] for i in order], [angles[i] * mean_radius for i in order]


def thin(
    points: list[tuple[float, float]], distances: list[float], metres: float
) -> list[tuple[float, float]]:
    """Keep one vertex per ``metres`` of along-corridor distance.

    Thinning on the along-corridor distance rather than on point-to-point
    distance matters: a dual carriageway interleaves two lines about 25 m
    apart, and point-to-point thinning would zig-zag between them.
    """
    if not points:
        return []
    step = metres / 1000.0
    kept = [points[0]]
    last = distances[0]
    for point, distance in zip(points[1:], distances[1:], strict=True):
        if distance - last >= step:
            kept.append(point)
            last = distance
    return kept


@dataclass
class CorridorResult:
    corridor: Corridor
    centreline: list[tuple[float, float]] = field(default_factory=list)
    way_vertices: int = 0
    span_km: float = 0.0
    linearity: float = 0.0


def partition(elements: list[dict]) -> list[CorridorResult]:
    """Group ways into corridors and reduce each to an ordered centreline."""
    results: list[CorridorResult] = []

    for corridor in CORRIDORS:
        vertices = corridor_vertices(corridor, elements)
        if len(vertices) < 2:
            results.append(CorridorResult(corridor=corridor))
            continue

        order = order_ring if corridor.shape == "ring" else order_linear
        ordered, distances = order(vertices)
        span = distances[-1] - distances[0]
        results.append(
            CorridorResult(
                corridor=corridor,
                centreline=thin(ordered, distances, THIN_METRES),
                way_vertices=len(vertices),
                span_km=span,
                linearity=linearity(vertices),
            )
        )

    return results


def to_geojson(results: list[CorridorResult]) -> dict:
    """A FeatureCollection of corridor centrelines, ordered along the road."""
    features = []
    for result in results:
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "corridor": result.corridor.key,
                    "name": result.corridor.name,
                    "role": result.corridor.role,
                    "shape": result.corridor.shape,
                    "length_km": round(result.span_km, 3),
                    "vertices": len(result.centreline),
                    "source_vertices": result.way_vertices,
                },
                "geometry": {
                    "type": "LineString",
                    # GeoJSON is lon, lat — the opposite of everything else in
                    # this file. Six decimals is ~0.1 m: small enough to keep
                    # the committed file lean, precise enough that a camera
                    # does not move off its vertex.
                    "coordinates": [
                        [round(lon, 6), round(lat, 6)] for lat, lon in result.centreline
                    ],
                },
            }
        )

    return {
        "type": "FeatureCollection",
        "properties": {
            "source": "OpenStreetMap contributors, ODbL 1.0",
            "generator": "scripts/fetch_ahmedabad_osm.py",
            "bbox": list(BBOX),
            "thin_metres": THIN_METRES,
        },
        "features": features,
    }


def write_localities() -> int:
    """Fetch named localities and write the committed locality file."""
    print("\nFetching named localities")
    document = {
        "type": "FeatureCollection",
        "properties": {
            "source": "OpenStreetMap contributors, ODbL 1.0",
            "generator": "scripts/fetch_ahmedabad_osm.py",
        },
        "features": fetch_localities(),
    }
    LOCALITIES_OUTPUT.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    print(
        f"  wrote {LOCALITIES_OUTPUT.name}: {len(document['features'])} localities, "
        f"{LOCALITIES_OUTPUT.stat().st_size / 1024:.0f} KB"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    parser.add_argument(
        "--raw", type=Path, default=None, help="partition a saved Overpass response"
    )
    parser.add_argument(
        "--save-raw", type=Path, default=None, help="write the raw Overpass response here"
    )
    parser.add_argument(
        "--localities-only",
        action="store_true",
        help="refresh only the locality file, leaving the road geometry alone",
    )
    args = parser.parse_args()

    if args.localities_only:
        return write_localities()

    print("Fetching Ahmedabad arterial geometry from OpenStreetMap")
    results = partition(fetch(args.raw, args.save_raw))

    print()
    total_km = 0.0
    suspect = []
    for result in results:
        total_km += result.span_km
        print(
            f"  {result.corridor.key:<15} {result.corridor.shape:<6} "
            f"{result.way_vertices:>5} vertices → {len(result.centreline):>4} centreline pts, "
            f"{result.span_km:6.1f} km   linearity {result.linearity:5.1f}"
        )
        if result.span_km < MIN_CORRIDOR_KM:
            suspect.append(f"{result.corridor.key} ({result.span_km:.1f} km)")
        elif result.corridor.shape == "linear" and result.linearity < MIN_LINEARITY:
            suspect.append(
                f"{result.corridor.key} (linearity {result.linearity:.1f} — a blob, not a line)"
            )

    print(f"\n  total corridor length: {total_km:.1f} km")

    if suspect:
        # Loud. A corridor that quietly collapsed would surface much later as a
        # hole in the fleet, with no obvious cause.
        print(f"\n  ✗ implausibly short corridors: {', '.join(suspect)}", file=sys.stderr)
        print("    OSM tags change; check the ref/name patterns in CORRIDORS.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    document = to_geojson(results)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    print(
        f"\n  wrote {OUTPUT.name}: {len(document['features'])} corridors, "
        f"{OUTPUT.stat().st_size / 1024:.0f} KB"
    )

    if args.raw is None:
        write_localities()
    else:
        print("\n  --raw given: skipping the locality fetch (it has no cache)")

    print("\n  © OpenStreetMap contributors, ODbL 1.0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
