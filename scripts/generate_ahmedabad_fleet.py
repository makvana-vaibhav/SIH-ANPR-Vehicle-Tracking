#!/usr/bin/env python3
"""Generate the Ahmedabad camera fleet as data/seed/ahmedabad_cameras.csv.

## Why this replaces generate_cameras.py

`generate_cameras.py` built a 250-camera *statewide* fleet by interpolating
along four hand-typed highway waypoints and jittering coordinates around city
centres. Two things were wrong with it for SIH26127. It is the wrong geography
— the brief is one city, not a state. And its cameras were not on roads: an
interpolated point 4 km from a city centre lands wherever it lands, which for
Ahmedabad means inside the Sabarmati about as often as on a carriageway.

This generator places every camera on a **real OSM road vertex** taken from
`data/seed/ahmedabad_roads.geojson`, so a camera is never in a building, a lake
or a field. It is deterministic (fixed seed, committed input), so the CSV it
writes is reproducible and diffable, and it needs no network.

## Every camera here can actually produce a detection

This is the rule commit `4d0346f` was written to enforce, and the reason it
deleted 251 cameras. A camera with no video source sits permanently at status
`unknown`, can never be opened, can never produce a detection, and makes every
count on every screen fiction.

These cameras get their video from the simulator, which publishes each one into
MediaMTX under its own code. They therefore carry **no `stream_url`**: the
`SimulatedVmsAdapter` derives `rtsp://mediamtx:8554/<code>` from the code
itself, and `probe_health` asks MediaMTX which paths are actually live.

⚠️ Do **not** give these rows a `stream_url` pointing at the gateway.
`gateway.reconcile()` registers a MediaMTX *pull* path from `camera.stream_url`,
so MediaMTX would pull a path from itself, loop, and silently block publishing.
Leaving the column empty is not an omission — it is what keeps the camera on
the adapter path. See CLAUDE.md's "three traps".

## Spacing

Spacing is chosen per corridor from its measured length so the fleet lands in
the 60-80 band with journeys that make sense: close enough on city streets that
a vehicle is seen several times, wide enough on the ring road that consecutive
sightings are minutes rather than seconds apart. The correlator's plausibility
scoring reads these gaps as implied speeds, so they have to be real distances.

Usage:

    python scripts/generate_ahmedabad_fleet.py
    python scripts/generate_ahmedabad_fleet.py --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SEED = 20260910  # the challenge grand finale date, as in the old generator

_SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"
ROADS = _SEED_DIR / "ahmedabad_roads.geojson"
LOCALITIES = _SEED_DIR / "ahmedabad_localities.geojson"
OUTPUT = _SEED_DIR / "ahmedabad_cameras.csv"

#: Must match a VMS seeded with `adapter_type = simulated`, or the simulator
#: will decline to publish these cameras and the health monitor will probe them
#: against the wrong adapter. `scripts/seed.py` creates it.
VMS_NAME = "Ahmedabad City Surveillance"

DISTRICT = CITY = "Ahmedabad"

#: A locality further away than this tells the operator nothing useful, so the
#: camera is named by its position along the corridor instead.
MAX_LOCALITY_KM = 2.5

#: Two cameras closer than this are one camera. Dual carriageways interleave in
#: the corridor ordering, so without this the ring road would get pairs of
#: cameras 20 m apart on opposite sides of the same junction.
MIN_SEPARATION_KM = 0.4

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Placement:
    """How densely to cover one corridor, and what to call its cameras."""

    corridor: str
    #: Short code used in the camera code, e.g. CAM-SGH-04.
    code: str
    display: str
    spacing_km: float
    #: Weighted department mix. Arterials are policed; inner streets are
    #: mostly municipal traffic cameras.
    departments: tuple[tuple[str, int], ...]


PLACEMENTS: tuple[Placement, ...] = (
    Placement(
        "SG-HIGHWAY", "SGH", "SG Highway", 1.8,
        (("POLICE", 60), ("MUNICIPAL", 25), ("GSRTC", 15)),
    ),
    Placement(
        "SP-RING", "SPR", "SP Ring Road", 5.2,
        (("POLICE", 70), ("GSRTC", 20), ("PANCHAYAT", 10)),
    ),
    Placement(
        "ASHRAM-ROAD", "ASH", "Ashram Road", 1.5,
        (("MUNICIPAL", 55), ("POLICE", 45)),
    ),
    Placement(
        "CG-ROAD", "CGR", "CG Road", 1.1,
        (("MUNICIPAL", 65), ("POLICE", 35)),
    ),
    Placement(
        "132FT-RING", "R132", "132 Ft Ring Road", 2.2,
        (("MUNICIPAL", 50), ("POLICE", 50)),
    ),
    Placement(
        "120FT-RING", "R120", "120 Ft Ring Road", 2.1,
        (("MUNICIPAL", 55), ("POLICE", 45)),
    ),
    Placement(
        "SH2-EAST", "SH2", "Rakhial-Odhav Road", 1.5,
        (("POLICE", 55), ("MUNICIPAL", 30), ("GSRTC", 15)),
    ),
    Placement(
        "NARODA-ROAD", "NRD", "Naroda Road", 1.4,
        (("POLICE", 50), ("MUNICIPAL", 35), ("GSRTC", 15)),
    ),
    Placement(
        "NAROL-SARKHEJ", "NSK", "Narol-Sarkhej Road", 1.5,
        (("POLICE", 60), ("MUNICIPAL", 25), ("PANCHAYAT", 15)),
    ),
    Placement(
        "AHM-VAD-EXPWY", "AVE", "Ahmedabad-Vadodara Expressway", 3.6,
        (("POLICE", 65), ("GSRTC", 35)),
    ),
)

RESOLUTIONS = (("1920x1080", 55), ("2560x1440", 25), ("3840x2160", 12), ("1280x720", 8))
FPS_CHOICES = (15, 20, 25, 25, 30)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two (lat, lon) points, in kilometres."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def bearing_deg(a: tuple[float, float], b: tuple[float, float]) -> int:
    """Initial compass bearing from a to b, in whole degrees.

    A camera faces along its road, which is what makes `heading_deg` mean
    anything to the correlator's heading-conflict check.
    """
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return round((math.degrees(math.atan2(y, x)) + 360) % 360) % 360


def weighted(rng: random.Random, pairs: tuple[tuple[str, int], ...]) -> str:
    """Pick one option by weight."""
    options = [p[0] for p in pairs]
    weights = [p[1] for p in pairs]
    return rng.choices(options, weights=weights)[0]


def load_corridors() -> dict[str, dict]:
    """Corridor centrelines, keyed by corridor code."""
    if not ROADS.exists():
        raise SystemExit(
            f"{ROADS} is missing. Run:\n    python scripts/fetch_ahmedabad_osm.py"
        )
    document = json.loads(ROADS.read_text(encoding="utf-8"))
    corridors = {}
    for feature in document["features"]:
        props = feature["properties"]
        # GeoJSON is lon, lat; everything else here is lat, lon.
        points = [(lat, lon) for lon, lat in feature["geometry"]["coordinates"]]
        corridors[props["corridor"]] = {"props": props, "points": points}
    return corridors


def load_localities() -> list[tuple[str, tuple[float, float]]]:
    """Named localities, for camera names an operator recognises."""
    if not LOCALITIES.exists():
        print(f"  ! {LOCALITIES.name} missing — cameras will be named by position")
        return []
    document = json.loads(LOCALITIES.read_text(encoding="utf-8"))
    return [
        (f["properties"]["name"], (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0]))
        for f in document["features"]
    ]


def nearest_locality(
    point: tuple[float, float], localities: list[tuple[str, tuple[float, float]]]
) -> str | None:
    """Closest named locality within MAX_LOCALITY_KM, if any."""
    best_name, best_km = None, MAX_LOCALITY_KM
    for name, position in localities:
        km = haversine_km(point, position)
        if km < best_km:
            best_name, best_km = name, km
    return best_name


def place_on_corridor(points: list[tuple[float, float]], spacing_km: float) -> list[int]:
    """Indices of the centreline vertices that get a camera.

    Walks the ordered centreline accumulating real great-circle distance and
    drops a camera every ``spacing_km``. Every chosen point is an untouched OSM
    vertex — nothing is interpolated, so no camera can land off the road.
    """
    if len(points) < 2:
        return []

    chosen = [0]
    travelled = 0.0
    since_last = 0.0

    for index in range(1, len(points)):
        step = haversine_km(points[index - 1], points[index])
        travelled += step
        since_last += step
        # The second condition rejects a vertex that is spatially on top of
        # the previous camera. On a dual carriageway the ordering alternates
        # sides, so the along-corridor distance can advance while the ground
        # distance barely moves.
        if (
            since_last >= spacing_km
            and haversine_km(points[chosen[-1]], points[index]) >= MIN_SEPARATION_KM
        ):
            chosen.append(index)
            since_last = 0.0

    return chosen


def build_rows() -> tuple[list[dict[str, object]], dict[str, dict]]:
    """Every camera in the fleet, in corridor order, with the corridors used."""
    rng = random.Random(SEED)
    corridors = load_corridors()
    localities = load_localities()
    rows: list[dict[str, object]] = []

    missing = [p.corridor for p in PLACEMENTS if p.corridor not in corridors]
    if missing:
        raise SystemExit(
            f"{ROADS.name} has no geometry for: {', '.join(missing)}. "
            "Re-run scripts/fetch_ahmedabad_osm.py."
        )

    for placement in PLACEMENTS:
        corridor = corridors[placement.corridor]
        points = corridor["points"]
        indices = place_on_corridor(points, placement.spacing_km)
        shape = corridor["props"]["shape"]

        for sequence, index in enumerate(indices, start=1):
            lat, lon = points[index]

            # Local tangent: the direction the road runs here. Cameras
            # alternate facing, as they do on a real carriageway pair.
            before = points[max(index - 1, 0)]
            after = points[min(index + 1, len(points) - 1)]
            heading = bearing_deg(before, after)
            if sequence % 2 == 0:
                heading = (heading + 180) % 360

            locality = nearest_locality((lat, lon), localities)
            where = locality or f"km {sequence * placement.spacing_km:.1f}"
            junction = f"{placement.display} at {where}"

            # Ring and expressway cameras are gantry ANPR; city streets carry a
            # mix. Both are anpr_enabled — every camera here has a resolvable
            # stream, which is the whole point of the fleet.
            camera_type = "anpr" if shape == "ring" or rng.random() < 0.75 else "fixed"

            rows.append(
                {
                    "camera_code": f"CAM-{placement.code}-{sequence:02d}",
                    "name": f"{junction} {'ANPR' if camera_type == 'anpr' else 'CCTV'} {sequence:02d}",
                    "department_code": weighted(rng, placement.departments),
                    "vms_name": VMS_NAME,
                    "district": DISTRICT,
                    "city": CITY,
                    "junction": junction,
                    "address": f"{placement.display}, {where}, {CITY}, Gujarat",
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "heading_deg": heading,
                    "camera_type": camera_type,
                    "protocol": "rtsp",
                    "resolution": weighted(rng, RESOLUTIONS),
                    "fps": rng.choice(FPS_CHOICES),
                    "anpr_enabled": True,
                    "tags": f"ahmedabad|corridor:{placement.corridor}|{shape}|anpr",
                }
            )

    base = date(2026, 8, 1)
    for row in rows:
        row["installed_on"] = (base - timedelta(days=rng.randint(30, 1095))).isoformat()

    return rows, corridors


def report(rows: list[dict[str, object]], corridors: dict[str, dict]) -> int:
    """What the fleet looks like, in the terms the P1 gate is written in.

    Returns the number of corridors that failed a sanity check.
    """
    by_corridor: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        key = str(row["tags"]).split("corridor:")[1].split("|")[0]
        by_corridor.setdefault(key, []).append(row)

    print(f"\n  {len(rows)} cameras across {len(by_corridor)} corridors\n")
    all_gaps: list[float] = []
    problems = 0

    for placement in PLACEMENTS:
        corridor_rows = by_corridor.get(placement.corridor, [])
        gaps = [
            haversine_km(
                (float(corridor_rows[i]["lat"]), float(corridor_rows[i]["lon"])),
                (float(corridor_rows[i + 1]["lat"]), float(corridor_rows[i + 1]["lon"])),
            )
            for i in range(len(corridor_rows) - 1)
        ]
        all_gaps.extend(gaps)
        span = sum(gaps)
        detail = (
            f"gaps {min(gaps):.1f}–{max(gaps):.1f} km, median {sorted(gaps)[len(gaps) // 2]:.1f}"
            if gaps
            else "single camera"
        )
        print(
            f"  {placement.corridor:<15} {len(corridor_rows):>3} cameras  "
            f"{span:6.1f} km  {detail}"
        )

        # Walking a corridor should cover roughly its own length. Covering far
        # more means the ordering is jumping back and forth, so consecutive
        # cameras are not consecutive on the ground — and the correlator would
        # read those gaps as impossible speeds. This check is here because it
        # happened: a mis-specified corridor walked 77 km of an 18.9 km road.
        length_km = float(corridors[placement.corridor]["props"]["length_km"])
        if length_km > 0 and span > length_km * 1.6:
            print(
                f"      ✗ walked {span:.1f} km of a {length_km:.1f} km corridor — "
                "the vertex ordering is not monotone on the ground",
                file=sys.stderr,
            )
            problems += 1

    if all_gaps:
        ordered = sorted(all_gaps)
        print(
            f"\n  consecutive spacing: min {ordered[0]:.2f} km · "
            f"median {ordered[len(ordered) // 2]:.2f} km · max {ordered[-1]:.2f} km"
        )

    # Corridor changes are what make a multi-camera journey interesting rather
    # than a straight line. Count the places a vehicle could plausibly switch.
    transfers = 0
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            if a["tags"] == b["tags"]:
                continue
            if str(a["tags"]).split("corridor:")[1] == str(b["tags"]).split("corridor:")[1]:
                continue
            if (
                haversine_km(
                    (float(a["lat"]), float(a["lon"])), (float(b["lat"]), float(b["lon"]))
                )
                <= 1.5
            ):
                transfers += 1
    print(f"  corridor interchange pairs within 1.5 km: {transfers}")

    departments: dict[str, int] = {}
    for row in rows:
        departments[str(row["department_code"])] = departments.get(str(row["department_code"]), 0) + 1
    print("  departments: " + " · ".join(f"{k} {v}" for k, v in sorted(departments.items())))

    named = sum(1 for r in rows if " at km " not in str(r["junction"]))
    print(f"  cameras named after a real locality: {named}/{len(rows)}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    print("Generating the Ahmedabad camera fleet")
    rows, corridors = build_rows()
    if report(rows, corridors):
        return 1

    if args.dry_run:
        print("\n  --dry-run: nothing written")
        return 0

    fieldnames = [
        "camera_code",
        "name",
        "department_code",
        "vms_name",
        "district",
        "city",
        "junction",
        "address",
        "lat",
        "lon",
        "heading_deg",
        "camera_type",
        "protocol",
        "resolution",
        "fps",
        "anpr_enabled",
        "installed_on",
        "tags",
    ]
    # No `stream_url` column, deliberately. See the module docstring.
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  wrote {OUTPUT.name}: {len(rows)} cameras, {OUTPUT.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
