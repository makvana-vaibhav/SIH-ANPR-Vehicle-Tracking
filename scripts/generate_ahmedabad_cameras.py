#!/usr/bin/env python3
"""Generate the Ahmedabad ANPR camera fleet as data/seed/cameras.csv.

    python3 scripts/generate_ahmedabad_cameras.py

Deterministic (fixed seed), so the committed CSV is reproducible and a judge can
diff it. The CSV is the committed artifact; `make seed` loads it through the same
bulk-import path the API exposes, so seeding exercises the real onboarding code
rather than a private shortcut.

## Why cameras are placed on corridors, not scattered

SIH26127 is about reconstructing a vehicle's trajectory across cameras, and a
trajectory is only believable if consecutive cameras are actually connected by a
road. So every camera is placed **on a real vertex of a real named Ahmedabad
arterial**, using the OSM geometry committed by `fetch_ahmedabad_geodata.py`:

* a camera always sits on a real carriageway, at a real coordinate — never in
  the middle of a building or the Sabarmati;
* consecutive cameras on a corridor are joined by that corridor, so the implied
  speed between them is the speed of a vehicle on that road;
* the ring roads cross every radial, which is what lets a vehicle plausibly
  change corridor mid-journey and makes route-anomaly detection meaningful
  rather than arbitrary.

## Why thinning rather than a centreline

The obvious approach — reduce each corridor to one centreline, then walk it at
fixed spacing — breaks on this city. Ahmedabad's dominant arterial is the
**Sardar Patel Ring Road**, and a ring doubles back on itself: projecting it onto
a principal axis and averaging each bin collapses the east and west sides of the
ring together and lands the cameras in the middle of the city, nowhere near the
road. Dual carriageways have the same problem in miniature.

So instead each corridor's own vertices are **thinned to a minimum spacing** and
used directly. That guarantees every camera is on real tarmac with no snapping
step, works identically for rings, radials and dual carriageways, and enforces
spacing by construction. Ordering along the corridor is then recovered with a
nearest-neighbour tour from the corridor's most extreme vertex, which traces a
radial end to end and a ring the way round.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

SEED = 26127  # the problem-statement number, so the fleet is reproducible
ROOT = Path(__file__).resolve().parent.parent
SEED_DIR = ROOT / "data" / "seed"
OUTPUT = SEED_DIR / "cameras.csv"

EARTH_R = 6_371_000.0


@dataclass(frozen=True)
class Corridor:
    """One named arterial the fleet covers."""

    names: tuple[str, ...]  # exact OSM `name`/`ref` values, merged into one corridor
    code: str  # camera-code fragment, e.g. "SPR"
    spacing_m: float
    department: str
    anpr: bool


#: Spacing is a real trade-off. Too tight and a vehicle is seen by every camera,
#: which makes trajectory reconstruction trivial and unconvincing. Too loose and
#: consecutive sightings are far enough apart that several routes are plausible,
#: which turns anomaly detection into noise. ~1.2-3 km roughly matches how far
#: apart real junction ANPR installations sit, wider on the expressways.
#:
#: Departments are the ones the registry already seeds (POLICE, MUNICIPAL,
#: GSRTC). City arterial ANPR is traffic-police work; AMC owns the civic
#: estate; GSRTC owns the bus corridors.
CORRIDORS: tuple[Corridor, ...] = (
    # Outer ring — encircles the city and crosses every radial.
    Corridor(("Sardar Patel Ring Road",), "SPR", 3000, "POLICE", True),
    # Inner rings.
    Corridor(("132 Ft. Ring Road",), "R132", 1800, "MUNICIPAL", True),
    Corridor(("120 Feet Ring Road",), "R120", 1800, "MUNICIPAL", True),
    # North-south spine through the centre.
    Corridor(("Ashram Road",), "ASH", 1500, "POLICE", True),
    Corridor(("Jawaharlal Nehru Road",), "JNR", 1500, "POLICE", True),
    # The western corridor. OSM splits the same road between a short "SG
    # Highway" stretch and the longer "Gandhinagar-Ahmedabad Highway"; they are
    # one corridor to a driver, so they are merged here.
    Corridor(
        ("SG Highway", "Gandhinagar-Ahmedabad Highway"), "SGH", 2000, "POLICE", True
    ),
    # Central commercial streets.
    Corridor(("Chimanlal Girdharlal Road",), "CGR", 1200, "MUNICIPAL", True),
    Corridor(("Drive-in Road",), "DIR", 1500, "MUNICIPAL", False),
    # Eastern and southern radials.
    Corridor(("Naroda Road",), "NRD", 1800, "GSRTC", True),
    Corridor(("Narol Sarkhej Road", "Narol-Sarkhej Road"), "NSR", 1800, "GSRTC", True),
    # Airport / north-east.
    Corridor(("Airport Road",), "APR", 2000, "MUNICIPAL", False),
    # Long-distance approach.
    Corridor(("Ahmadabad Vadodara Expressway",), "AVE", 3000, "GSRTC", True),
)


# ── geometry ──────────────────────────────────────────────────────────
def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Metres between two (lon, lat) points."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_R * math.asin(math.sqrt(h))


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> int:
    """True bearing a->b, degrees clockwise from north."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        dlon
    )
    return round(math.degrees(math.atan2(y, x))) % 360


def point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon."""
    lon, lat = point
    inside = False
    for i in range(len(ring) - 1):
        x1, y1 = ring[i]
        x2, y2 = ring[i + 1]
        if (y1 > lat) != (y2 > lat):
            if lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
    return inside


#: No two cameras in the fleet may be closer than this, whatever corridor they
#: belong to.
#:
#: Per-corridor thinning alone is not enough. Ashram Road, Jawaharlal Nehru Road
#: and the 132 Ft Ring Road all meet near Nehru Bridge, and thinning each
#: corridor independently put three cameras within **13 m** of each other, with
#: another pair at 45 m. That is absurd physically — they would see the same
#: vehicles from the same spot — and actively harmful downstream: the
#: correlator's `MIN_SEPARATION_M` is 50 m, so a hop between two of them is
#: flagged `co_located` and carries no implied speed, polluting exactly the
#: trajectories this fleet exists to produce.
#:
#: 250 m keeps one camera per approach at a real junction, which is how junction
#: ANPR is actually installed, while staying well clear of that 50 m threshold.
GLOBAL_MIN_SEPARATION_M = 250.0


def thin(
    vertices: list[tuple[tuple[float, float], int]], spacing_m: float
) -> list[tuple[tuple[float, float], int]]:
    """Keep vertices at least `spacing_m` apart, in input order.

    A greedy sweep is enough and is stable: the corridor's vertices arrive
    grouped by way, so accepting the first candidate that clears every
    already-accepted point spreads cameras along the road without clustering at
    junctions, where OSM puts many vertices close together.
    """
    kept: list[tuple[tuple[float, float], int]] = []
    for point, heading in vertices:
        if all(haversine_m(point, other) >= spacing_m for other, _ in kept):
            kept.append((point, heading))
    return kept


def order_along_corridor(
    points: list[tuple[tuple[float, float], int]],
) -> list[tuple[tuple[float, float], int]]:
    """Sequence points along their corridor with a nearest-neighbour tour.

    Started from the most extreme vertex (furthest from the corridor's centroid)
    so a radial is traced end to end rather than from its middle outwards. On a
    ring this walks the way round, which is what "the next camera along" means
    to a driver.
    """
    if len(points) < 3:
        return points

    mean_lon = sum(p[0][0] for p in points) / len(points)
    mean_lat = sum(p[0][1] for p in points) / len(points)
    remaining = list(points)
    start = max(remaining, key=lambda p: haversine_m(p[0], (mean_lon, mean_lat)))
    remaining.remove(start)

    tour = [start]
    while remaining:
        last = tour[-1][0]
        nxt = min(remaining, key=lambda p: haversine_m(p[0], last))
        remaining.remove(nxt)
        tour.append(nxt)
    return tour


# ── source data ───────────────────────────────────────────────────────
def load_corridor_vertices() -> dict[str, list[tuple[tuple[float, float], int]]]:
    """Corridor name -> its vertices, each with its road segment's bearing."""
    roads = json.loads((SEED_DIR / "ahmedabad_roads.geojson").read_text())
    by_name: dict[str, list[tuple[tuple[float, float], int]]] = {}
    for feature in roads["features"]:
        name = (
            feature["properties"].get("name") or feature["properties"].get("ref") or ""
        )
        if not name:
            continue
        coords = [(lon, lat) for lon, lat in feature["geometry"]["coordinates"]]
        bucket = by_name.setdefault(name, [])
        for i in range(len(coords) - 1):
            bucket.append((coords[i], bearing_deg(coords[i], coords[i + 1])))
    return by_name


def load_talukas() -> list[tuple[str, list[list[list[float]]]]]:
    talukas = json.loads((SEED_DIR / "ahmedabad_talukas.geojson").read_text())
    return [
        (
            feature["properties"]["taluka"],
            [
                ring
                for polygon in feature["geometry"]["coordinates"]
                for ring in polygon
            ],
        )
        for feature in talukas["features"]
    ]


def load_localities() -> list[tuple[str, tuple[float, float]]]:
    places = json.loads((SEED_DIR / "ahmedabad_localities.geojson").read_text())
    return [
        (
            f["properties"]["name"],
            (f["geometry"]["coordinates"][0], f["geometry"]["coordinates"][1]),
        )
        for f in places["features"]
    ]


def taluka_for(point, talukas) -> str:
    """Smallest matching taluka wins.

    Daskroi is the rural fringe and geometrically contains parts of the city
    talukas, so a plain first-match would file half the fleet under it. The
    city taluka is the more specific and more useful answer.
    """
    matches = [
        (sum(len(r) for r in rings), code)
        for code, rings in talukas
        if any(point_in_ring(point, ring) for ring in rings)
    ]
    if not matches:
        return ""
    return min(matches)[1]


def nearest_locality(point, localities) -> str:
    return min(localities, key=lambda entry: haversine_m(point, entry[1]))[0]


# ── generation ────────────────────────────────────────────────────────
#: Corridor code (lowercased, as it appears in `tags`) → canonical name.
#: Derived from CORRIDORS rather than written out again, so the two cannot drift.
CORRIDOR_BY_SLUG = {corridor.code.lower(): corridor.names[0] for corridor in CORRIDORS}

#: The column order of data/seed/cameras.csv. `scripts/seed.py` reads by name,
#: so this is for humans diffing the file.
CSV_COLUMNS = (
    "camera_code",
    "name",
    "department_code",
    "district",
    "city",
    "junction",
    "corridor",
    "lat",
    "lon",
    "heading_deg",
    "camera_type",
    "protocol",
    "stream_url",
    "resolution",
    "fps",
    "anpr_enabled",
    "tags",
)


def corridor_from_tags(tags: str) -> str | None:
    """Recover a corridor from a legacy row's `tags`.

    Rows written before the `corridor` column existed carry it only as a slug in
    `tags` (`ash|arterial|sabarmati`). Returns None when no tag matches, which
    keeps the camera uncorridored rather than attributing it to a road by guess.
    """
    for tag in tags.split("|"):
        name = CORRIDOR_BY_SLUG.get(tag.strip().lower())
        if name:
            return name
    return None


def load_preserved(path: Path, prefix: str) -> list[dict[str, str]]:
    """Existing rows whose `camera_code` starts with `prefix`, carried over verbatim.

    The demo cameras are hand-tuned — their footage, plate region and placement
    were chosen deliberately and are what the ANPR demo path rests on. A
    generator run that widens the fleet must not silently rewrite them, so they
    are read back and re-emitted unchanged apart from gaining a `corridor`.
    """
    if not prefix or not path.exists():
        return []

    preserved: list[dict[str, str]] = []
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if not row.get("camera_code", "").startswith(prefix):
                continue
            if not (row.get("corridor") or "").strip():
                row["corridor"] = corridor_from_tags(row.get("tags", "")) or ""
            preserved.append(row)
    return preserved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the Ahmedabad camera fleet from OSM road geometry."
    )
    parser.add_argument(
        "--corridors",
        default="",
        help=(
            "Comma-separated corridor codes to place cameras on "
            f"(default: all). Known: {', '.join(c.code for c in CORRIDORS)}"
        ),
    )
    parser.add_argument(
        "--max-per-corridor",
        type=int,
        default=0,
        help="Cap cameras per corridor. 0 means no cap.",
    )
    parser.add_argument(
        "--preserve",
        default="",
        help=(
            "camera_code prefix to carry over unchanged from the existing CSV, "
            "e.g. CAM-DEMO. Preserved cameras also reserve their own space, so "
            "nothing new is placed on top of them."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rng = random.Random(SEED)
    vertices_by_name = load_corridor_vertices()
    talukas = load_talukas()
    localities = load_localities()

    wanted = {code.strip().upper() for code in args.corridors.split(",") if code.strip()}
    unknown = wanted - {corridor.code for corridor in CORRIDORS}
    if unknown:
        raise SystemExit(f"Unknown corridor code(s): {', '.join(sorted(unknown))}")

    preserved = load_preserved(OUTPUT, args.preserve)
    rows: list[dict[str, object]] = [dict(row) for row in preserved]
    missing: list[str] = []
    #: Every camera accepted so far, across all corridors, for the global
    #: separation check. Corridors are processed in CORRIDORS order, so which
    #: corridor keeps the camera at a crossing is deterministic.
    #:
    #: Seeded with the preserved cameras so a widened fleet cannot drop a new
    #: camera on top of a demo one — the separation rule exists because the
    #: correlator treats anything under 50 m as co-located and refuses to imply
    #: a speed across it.
    accepted: list[tuple[float, float]] = [
        (float(row["lon"]), float(row["lat"])) for row in preserved
    ]
    dropped_too_close = 0

    for corridor in CORRIDORS:
        if wanted and corridor.code not in wanted:
            continue
        vertices: list[tuple[tuple[float, float], int]] = []
        for name in corridor.names:
            vertices.extend(vertices_by_name.get(name, []))
        if not vertices:
            missing.append("/".join(corridor.names))
            continue

        placed = order_along_corridor(thin(vertices, corridor.spacing_m))

        index = 0
        for point, heading in placed:
            # The cap counts *placed* cameras, so a corridor whose first
            # candidates fall outside the city or too close to a neighbour still
            # reaches its quota instead of being short-changed by rejections.
            if args.max_per_corridor and index >= args.max_per_corridor:
                break
            taluka = taluka_for(point, talukas)
            # Outside every taluka is outside the city we claim to cover; the
            # ring road and the expressway both run past the boundary.
            if not taluka:
                continue

            if any(
                haversine_m(point, other) < GLOBAL_MIN_SEPARATION_M
                for other in accepted
            ):
                dropped_too_close += 1
                continue
            accepted.append(point)

            index += 1
            lon, lat = point
            locality = nearest_locality(point, localities)
            rows.append(
                {
                    "camera_code": f"CAM-{corridor.code}-{index:02d}",
                    "name": f"{locality} — {corridor.names[0]}",
                    "department_code": corridor.department,
                    "district": taluka,
                    "city": "Ahmedabad",
                    "junction": f"{corridor.names[0]} @ {locality}",
                    # An explicit column, not a slug inside `tags`. Traffic
                    # analytics groups on the corridor, and a grouping key that
                    # has to be parsed out of a tag list or a camera code is one
                    # that silently mis-buckets the moment either is edited.
                    "corridor": corridor.names[0],
                    "lat": f"{lat:.6f}",
                    "lon": f"{lon:.6f}",
                    "heading_deg": heading,
                    "camera_type": "anpr" if corridor.anpr else "fixed",
                    "protocol": "rtsp",
                    # Left blank deliberately, and the seed leaves it NULL.
                    # `gateway.reconcile()` registers a MediaMTX *pull* path for
                    # every camera that has a stream_url and is not federated.
                    # These cameras are *published into* MediaMTX by the
                    # simulator, so giving them a gateway URL would tell
                    # MediaMTX to pull each path from itself — an infinite loop
                    # that silently blocks publishing for the whole fleet. The
                    # simulated adapter derives every URL from camera_code.
                    "stream_url": "",
                    "resolution": "1920x1080",
                    "fps": rng.choice([15, 20, 25]),
                    "anpr_enabled": corridor.anpr,
                    "tags": f"{corridor.code.lower()}|arterial|{taluka.lower()}",
                }
            )

    if missing:
        print(
            f"WARNING: no geometry for {len(missing)} corridor(s): {', '.join(missing)}"
        )
    if dropped_too_close:
        print(
            f"  dropped {dropped_too_close} candidate(s) within "
            f"{GLOBAL_MIN_SEPARATION_M:.0f} m of another camera (corridor crossings)"
        )
    if not rows:
        raise SystemExit("No cameras generated — check the road GeoJSON is present.")

    rows.sort(key=lambda row: str(row["camera_code"]))
    # Explicit, not `rows[0].keys()`: preserved rows and generated rows agree on
    # the column *set* but not its order, so taking it from whichever row sorts
    # first would silently reshuffle the file between runs.
    with OUTPUT.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    anpr = sum(1 for r in rows if r["anpr_enabled"])
    print(
        f"Wrote {OUTPUT.relative_to(ROOT)}: {len(rows)} cameras ({anpr} ANPR-enabled)"
    )

    per_corridor: dict[str, int] = {}
    for row in rows:
        key = str(row["camera_code"]).split("-")[1]
        per_corridor[key] = per_corridor.get(key, 0) + 1
    print(
        "  per corridor:",
        ", ".join(f"{k}={v}" for k, v in sorted(per_corridor.items())),
    )

    per_taluka: dict[str, int] = {}
    for row in rows:
        per_taluka[str(row["district"])] = per_taluka.get(str(row["district"]), 0) + 1
    print(
        "  per taluka:  ", ", ".join(f"{k}={v}" for k, v in sorted(per_taluka.items()))
    )


if __name__ == "__main__":
    main()
