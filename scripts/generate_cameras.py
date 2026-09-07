"""Generate the 250-camera demo fleet as data/seed/cameras.csv.

Deterministic (fixed seed), so the committed CSV is reproducible and a judge
can diff it. The CSV is the committed artifact; the seed loads it through the
same bulk-import path the API exposes, which means `make seed` exercises the
real onboarding code rather than a private shortcut.

Geographic realism matters here. Cameras sit on actual city junctions and along
the real NH-27 and NH-48 alignments, because Judge Moment 4 draws a route
across four cameras — Rajkot → Gondal → Jetpur → Junagadh — and the implied
speeds between them have to look like a car on a highway. Cameras scattered at
random would produce journeys at impossible speeds through empty desert.
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

SEED = 20260910  # the challenge grand finale date
OUTPUT = Path(__file__).resolve().parent.parent / "data" / "seed" / "cameras.csv"
TARGET_COUNT = 250


@dataclass(frozen=True)
class City:
    name: str
    district: str
    lat: float
    lon: float
    weight: int  # relative share of the urban camera budget
    junctions: tuple[str, ...]


# Real coordinates. Gujarat's major urban centres, weighted roughly by
# population and policing load.
CITIES: tuple[City, ...] = (
    City(
        "Ahmedabad",
        "Ahmedabad",
        23.0225,
        72.5714,
        34,
        (
            "Ashram Road Circle",
            "SG Highway Thaltej",
            "CG Road Panchvati",
            "Iskcon Crossroads",
            "Naroda Patiya",
            "Kalupur Station Circle",
            "Sarkhej Circle",
            "Vastrapur Lake Junction",
            "Maninagar Char Rasta",
            "Paldi Crossroads",
            "Gota Circle",
            "Nikol Junction",
        ),
    ),
    City(
        "Surat",
        "Surat",
        21.1702,
        72.8311,
        22,
        (
            "Adajan Patiya",
            "Varachha Char Rasta",
            "Ring Road Junction",
            "Udhna Darwaja",
            "Katargam Junction",
            "Dumas Road Circle",
            "Sarthana Jakatnaka",
            "Piplod Circle",
        ),
    ),
    City(
        "Vadodara",
        "Vadodara",
        22.3072,
        73.1812,
        16,
        (
            "Alkapuri Circle",
            "Fatehgunj Junction",
            "Akota Bridge",
            "Sayajigunj Crossroads",
            "Waghodia Road Junction",
            "Gotri Circle",
        ),
    ),
    City(
        "Rajkot",
        "Rajkot",
        22.3039,
        70.8022,
        20,
        (
            "Kalawad Road Junction",
            "150 Feet Ring Road",
            "Gondal Road Chowk",
            "Trikon Baug",
            "Yagnik Road Circle",
            "Race Course Ring Road",
            "Bhaktinagar Circle",
            "Aji Dam Chokdi",
        ),
    ),
    City(
        "Bhavnagar",
        "Bhavnagar",
        21.7645,
        72.1519,
        8,
        (
            "Ghogha Circle",
            "Waghawadi Road Junction",
            "Kalanala Chowk",
        ),
    ),
    City(
        "Jamnagar",
        "Jamnagar",
        22.4707,
        70.0577,
        8,
        (
            "Bedi Gate Circle",
            "Lal Bungalow Junction",
            "Hapa Road Chowk",
        ),
    ),
    City(
        "Gandhinagar",
        "Gandhinagar",
        23.2156,
        72.6369,
        10,
        (
            "Sector 21 Circle",
            "Ch-0 Junction",
            "Infocity Crossroads",
            "Sachivalaya Gate",
            "Adalaj Circle",
        ),
    ),
    City(
        "Junagadh",
        "Junagadh",
        21.5222,
        70.4579,
        8,
        (
            "Kalwa Chowk",
            "Zanzarda Road Junction",
            "Majevadi Gate",
        ),
    ),
    City(
        "Gondal",
        "Rajkot",
        21.9614,
        70.8027,
        6,
        (
            "Gondal Bus Station Circle",
            "Kailash Baug Junction",
        ),
    ),
    City(
        "Jetpur",
        "Rajkot",
        21.7549,
        70.6236,
        6,
        (
            "Jetpur Highway Chowk",
            "Kagvad Road Junction",
        ),
    ),
)

# National highway alignments as ordered waypoints. Cameras are placed along
# the segments between them, which is what makes a multi-camera route
# geographically coherent.
#
# NH-27 (Saurashtra corridor) carries the demo vehicle's journey.
NH27_CORRIDOR: tuple[tuple[float, float], ...] = (
    (22.4707, 70.0577),  # Jamnagar
    (22.3039, 70.8022),  # Rajkot
    (21.9614, 70.8027),  # Gondal
    (21.7549, 70.6236),  # Jetpur
    (21.5222, 70.4579),  # Junagadh
)

# NH-48 (Delhi–Mumbai golden quadrilateral leg through Gujarat).
NH48_CORRIDOR: tuple[tuple[float, float], ...] = (
    (23.2156, 72.6369),  # Gandhinagar
    (23.0225, 72.5714),  # Ahmedabad
    (22.6916, 72.8634),  # Nadiad
    (22.3072, 73.1812),  # Vadodara
    (21.8090, 73.0169),  # Bharuch
    (21.1702, 72.8311),  # Surat
)

DEPARTMENTS = ("POLICE", "MUNICIPAL", "GSRTC", "PANCHAYAT", "HEALTH")

# Weighted so Police and Municipal dominate the estate, as they do in reality.
DEPARTMENT_WEIGHTS = (46, 26, 14, 8, 6)

VMS_INSTANCES = (
    "Rajkot City Command Centre",  # milestone
    "Ahmedabad Smart City VMS",  # genetec
    "GSRTC Depot Surveillance",  # cpplus
    "Gujarat Highway ANPR Grid",  # hikvision
    "Hosted Camera Grid",  # the challenge's own camera grid
)

CAMERA_TYPES = ("fixed", "anpr", "ptz", "dome")
CAMERA_TYPE_WEIGHTS = (40, 34, 14, 12)

RESOLUTIONS = ("1920x1080", "2560x1440", "3840x2160", "1280x720")
RESOLUTION_WEIGHTS = (52, 22, 12, 14)


def interpolate(
    a: tuple[float, float], b: tuple[float, float], t: float
) -> tuple[float, float]:
    """Point at fraction ``t`` along the great-circle-ish segment a→b.

    Linear interpolation is accurate enough over the tens of kilometres between
    consecutive highway waypoints.
    """
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def bearing(a: tuple[float, float], b: tuple[float, float]) -> int:
    """Initial compass bearing from a to b, in degrees.

    Highway cameras face along the road, which is what makes ``heading_deg``
    meaningful to the correlator's plausibility check.
    """
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        dlon
    )
    return int(round((math.degrees(math.atan2(y, x)) + 360) % 360))


def jitter(rng: random.Random, value: float, metres: float) -> float:
    """Offset a coordinate by up to ``metres`` so cameras are not collinear."""
    return value + rng.uniform(-metres, metres) / 111_320.0


def build_rows() -> list[dict[str, object]]:
    rng = random.Random(SEED)
    rows: list[dict[str, object]] = []
    counter = 0

    def next_code() -> str:
        nonlocal counter
        counter += 1
        return f"CAM-{counter:05d}"

    # ── Urban cameras: ~65% of the fleet, clustered on named junctions ──
    urban_budget = int(TARGET_COUNT * 0.65)
    total_weight = sum(c.weight for c in CITIES)

    for city in CITIES:
        count = max(2, round(urban_budget * city.weight / total_weight))
        for i in range(count):
            junction = city.junctions[i % len(city.junctions)]
            # Spread within roughly 4 km of the centre.
            lat = jitter(rng, city.lat, 4000)
            lon = jitter(rng, city.lon, 4000)
            camera_type = rng.choices(CAMERA_TYPES, weights=CAMERA_TYPE_WEIGHTS)[0]
            rows.append(
                {
                    "camera_code": next_code(),
                    "name": f"{junction} {'ANPR' if camera_type == 'anpr' else 'CCTV'} {i + 1:02d}",
                    "department_code": rng.choices(
                        DEPARTMENTS, weights=DEPARTMENT_WEIGHTS
                    )[0],
                    "vms_name": rng.choice(VMS_INSTANCES),
                    "district": city.district,
                    "city": city.name,
                    "junction": junction,
                    "address": f"{junction}, {city.name}, Gujarat",
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "heading_deg": rng.randrange(0, 360, 15),
                    "camera_type": camera_type,
                    "protocol": "rtsp",
                    "resolution": rng.choices(RESOLUTIONS, weights=RESOLUTION_WEIGHTS)[
                        0
                    ],
                    "fps": rng.choice((15, 20, 25, 25, 30)),
                    "anpr_enabled": camera_type in ("anpr", "fixed"),
                    "tags": "urban|junction",
                }
            )

    # ── Highway cameras: the rest, strung along NH-27 and NH-48 ─────────
    corridors = (("NH-27", NH27_CORRIDOR), ("NH-48", NH48_CORRIDOR))
    remaining = TARGET_COUNT - len(rows)
    per_corridor = remaining // len(corridors)

    for corridor_index, (highway, waypoints) in enumerate(corridors):
        # Give the last corridor any rounding remainder.
        budget = (
            remaining - per_corridor * (len(corridors) - 1)
            if corridor_index == len(corridors) - 1
            else per_corridor
        )
        segments = len(waypoints) - 1
        for i in range(budget):
            # Distribute evenly along the whole corridor.
            position = (i + 0.5) / budget * segments
            segment_index = min(int(position), segments - 1)
            t = position - segment_index
            a, b = waypoints[segment_index], waypoints[segment_index + 1]
            lat, lon = interpolate(a, b, t)
            # Highway cameras sit within ~250 m of the alignment.
            lat, lon = jitter(rng, lat, 250), jitter(rng, lon, 250)

            heading = bearing(a, b)
            # Half the cameras face oncoming traffic.
            if i % 2:
                heading = (heading + 180) % 360

            nearest = min(CITIES, key=lambda c: (c.lat - lat) ** 2 + (c.lon - lon) ** 2)
            km_marker = round(position * 42 + 8)

            rows.append(
                {
                    "camera_code": next_code(),
                    "name": f"{highway} km {km_marker} ANPR Gantry",
                    "department_code": rng.choices(
                        ("POLICE", "GSRTC", "PANCHAYAT"), weights=(64, 24, 12)
                    )[0],
                    "vms_name": rng.choice(
                        ("Gujarat Highway ANPR Grid", "Hosted Camera Grid")
                    ),
                    "district": nearest.district,
                    "city": nearest.name,
                    "junction": f"{highway} km {km_marker}",
                    "address": f"{highway}, near {nearest.name}, Gujarat",
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "heading_deg": heading,
                    "camera_type": "anpr",
                    "protocol": "rtsp",
                    "resolution": rng.choices(
                        ("1920x1080", "2560x1440"), weights=(70, 30)
                    )[0],
                    "fps": rng.choice((25, 30)),
                    "anpr_enabled": True,
                    "tags": f"highway|{highway.lower()}|anpr",
                }
            )

    # Trim or pad to land exactly on the target.
    rows = rows[:TARGET_COUNT]

    # Installation dates spread over the last three years.
    base = date(2026, 8, 1)
    for row in rows:
        row["installed_on"] = (base - timedelta(days=rng.randint(30, 1095))).isoformat()

    return rows


def main() -> int:
    rows = build_rows()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

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

    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    districts = sorted({str(r["district"]) for r in rows})
    anpr = sum(1 for r in rows if r["anpr_enabled"])
    highway = sum(1 for r in rows if "highway" in str(r["tags"]))

    print(
        f"Wrote {len(rows)} cameras to {OUTPUT.relative_to(OUTPUT.parent.parent.parent)}"
    )
    print(f"  districts : {len(districts)} ({', '.join(districts)})")
    print(f"  ANPR      : {anpr}")
    print(f"  highway   : {highway}   urban: {len(rows) - highway}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
