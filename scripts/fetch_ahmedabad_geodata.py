#!/usr/bin/env python3
"""Fetch and prepare Ahmedabad map data from OpenStreetMap.

    python3 scripts/fetch_ahmedabad_geodata.py            # fetch what is missing
    python3 scripts/fetch_ahmedabad_geodata.py --force    # refetch everything

Writes three compact GeoJSON files into ``data/seed/``:

    ahmedabad_talukas.geojson     the city's administrative talukas (polygons)
    ahmedabad_roads.geojson       the arterial road network (lines)
    ahmedabad_localities.geojson  suburbs and neighbourhoods (points)

**The results are committed**, so this script only runs when the source data
changes — the demo itself never touches the network. That is what "no tile
server, works on a plane" rests on: MapLibre renders these directly, with no
basemap tiles to download and no external service to call (CLAUDE.md §6).

Source : OpenStreetMap via the Overpass API
Licence: ODbL 1.0 — recorded in each output file's ``licence`` member and
         surfaced in the map's attribution control.

## Why talukas rather than wards

Ahmedabad Municipal Corporation runs 48 numbered wards, and **none of them is
mapped in OSM** — `admin_level` 8, 9 and 10 all come back empty inside the city
bbox. What *is* mapped is `admin_level=6`: the city talukas (Asarva, Ghatlodiya,
Maninagar, Sabarmati, Vatva, Vejalpur, plus rural Daskroi on the fringe). Those
are real administrative divisions with real boundaries, so they are what the
registry files cameras under and what gap analysis reports coverage against.

A happy side effect: taluka names contain no slash, unlike Mumbai's "K/W" style
ward codes, which cannot go in a URL path segment.

## Why the mirror list

Overpass is a free shared service. It rate-limits, sheds load with 504s, and
returns an HTML error page rather than JSON when it is unhappy — all three
happened while building this. So every query tries each mirror with backoff, and
each dataset is fetched only when missing, which makes a partial run safe to
simply repeat.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.jp/api/interpreter",
)
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"

#: Ahmedabad city and its immediate ring. Wide enough to reach Gandhinagar's
#: southern edge along SG Highway and the Narol junction in the south, tight
#: enough that Overpass answers in one call.
BBOX = (22.90, 72.44, 23.13, 72.72)

#: 5 decimal places is ~1.1 m at this latitude — far finer than a camera
#: position needs, and roughly halves the file size against OSM's 7.
PRECISION = 5

LICENCE = (
    "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)"
)

#: The arterial network. Residential streets are excluded deliberately: they
#: would multiply the file size and add nothing an operator reads at city zoom.
ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary")

#: Talukas that make up Ahmedabad city plus its fringe. Gandhinagar's talukas
#: fall inside the bbox but belong to a different city, so they are excluded by
#: name rather than by geometry.
CITY_TALUKAS = frozenset(
    {"Asarva", "Ghatlodiya", "Maninagar", "Sabarmati", "Vatva", "Vejalpur", "Daskroi"}
)


def overpass(query: str, attempts_per_mirror: int = 2) -> dict[str, Any]:
    """POST one Overpass QL query, trying each mirror with backoff."""
    body = urllib.parse.urlencode({"data": query}).encode()
    failures: list[str] = []

    for mirror in OVERPASS_MIRRORS:
        for attempt in range(attempts_per_mirror):
            if attempt:
                time.sleep(20 * attempt)
            request = urllib.request.Request(
                mirror,
                data=body,
                headers={"User-Agent": "nagarnetra/0.1 (SIH26127 city ANPR research)"},
            )
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                failures.append(f"{mirror} -> HTTP {exc.code}")
            except (urllib.error.URLError, TimeoutError) as exc:
                failures.append(f"{mirror} -> {exc}")
            except json.JSONDecodeError:
                # Overpass answers 200 with an HTML error page when overloaded.
                failures.append(f"{mirror} -> HTML error page, not JSON (overloaded)")

    detail = "\n  ".join(failures)
    sys.exit(f"Every Overpass mirror refused the query:\n  {detail}")


def round_line(coords: list[list[float]]) -> list[list[float]]:
    """Round to PRECISION and drop duplicates left behind by rounding."""
    out: list[list[float]] = []
    for lon, lat in coords:
        point = [round(lon, PRECISION), round(lat, PRECISION)]
        if not out or out[-1] != point:
            out.append(point)
    return out


def stitch_rings(ways: list[list[list[float]]]) -> list[list[list[float]]]:
    """Join relation member ways into closed rings.

    Overpass returns a boundary relation as an unordered bag of ways whose
    directions do not agree. Joining them by matching endpoints — flipping a way
    when that is what closes the chain — is what turns them into polygons.
    """
    pool = [list(way) for way in ways if len(way) >= 2]
    rings: list[list[list[float]]] = []

    while pool:
        chain = pool.pop(0)
        extended = True
        while extended and chain[0] != chain[-1]:
            extended = False
            for index, way in enumerate(pool):
                if way[0] == chain[-1]:
                    chain.extend(way[1:])
                elif way[-1] == chain[-1]:
                    chain.extend(way[-2::-1])
                elif way[-1] == chain[0]:
                    chain = way[:-1] + chain
                elif way[0] == chain[0]:
                    chain = way[:0:-1] + chain
                else:
                    continue
                pool.pop(index)
                extended = True
                break

        # An unclosed administrative boundary is a gap in OSM, not a reason to
        # lose the taluka, so open chains are closed rather than dropped.
        if len(chain) >= 3:
            if chain[0] != chain[-1]:
                chain.append(chain[0])
            if len(chain) >= 4:
                rings.append(chain)

    return rings


def taluka_name(raw: str) -> str:
    """ "Ghatlodiya Taluka" -> "Ghatlodiya"; also fixes "SabarmatiTaluka"."""
    name = raw.strip()
    if name.endswith("Taluka"):
        name = name[: -len("Taluka")]
    return name.strip()


def fetch_talukas() -> dict[str, Any]:
    """Ahmedabad's city talukas as a polygon FeatureCollection."""
    south, west, north, east = BBOX
    data = overpass(
        f"[out:json][timeout:280];"
        f'relation["admin_level"="6"]["boundary"="administrative"]'
        f"({south},{west},{north},{east});"
        f"out geom;"
    )

    features = []
    for element in data.get("elements", []):
        raw = (element.get("tags", {}).get("name") or "").strip()
        if not raw:
            continue
        name = taluka_name(raw)
        if name not in CITY_TALUKAS:
            continue

        outer = [
            [[point["lon"], point["lat"]] for point in member.get("geometry") or []]
            for member in element.get("members", [])
            if member.get("type") == "way"
            and member.get("role") in ("outer", "")
            and member.get("geometry")
        ]
        rings = [round_line(ring) for ring in stitch_rings(outer)]
        rings = [ring for ring in rings if len(ring) >= 4]
        if not rings:
            continue

        features.append(
            {
                "type": "Feature",
                "id": name,
                "properties": {"taluka": name, "name": f"{name} Taluka"},
                # MultiPolygon unconditionally: one geometry type keeps the
                # map layer simple, and some talukas are genuinely multi-part.
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [[r] for r in rings],
                },
            }
        )

    features.sort(key=lambda feature: feature["properties"]["taluka"])
    return {
        "type": "FeatureCollection",
        "name": "ahmedabad_talukas",
        "licence": LICENCE,
        "features": features,
    }


def fetch_roads() -> dict[str, Any]:
    """The arterial road network as a line FeatureCollection."""
    south, west, north, east = BBOX
    classes = "|".join(ROAD_CLASSES)
    data = overpass(
        f"[out:json][timeout:280];"
        f'way["highway"~"^({classes})$"]'
        f"({south},{west},{north},{east});"
        f"out geom;"
    )

    features = []
    for element in data.get("elements", []):
        line = round_line(
            [[point["lon"], point["lat"]] for point in element.get("geometry") or []]
        )
        if len(line) < 2:
            continue
        tags = element.get("tags", {})
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "highway": tags.get("highway", ""),
                    "name": tags.get("name", ""),
                    # `ref` carries NH/SH numbers, which is how some of
                    # Ahmedabad's arterials are signed rather than named.
                    "ref": tags.get("ref", ""),
                },
                "geometry": {"type": "LineString", "coordinates": line},
            }
        )

    return {
        "type": "FeatureCollection",
        "name": "ahmedabad_roads",
        "licence": LICENCE,
        "features": features,
    }


def fetch_localities() -> dict[str, Any]:
    """Suburbs and neighbourhoods as points.

    Used to name cameras after the place they actually stand in, so a
    trajectory reads "Bodakdev → Vastrapur → Navrangpura" rather than
    "CAM-17 → CAM-18 → CAM-19".
    """
    south, west, north, east = BBOX
    data = overpass(
        f"[out:json][timeout:280];"
        f'node["place"~"^(suburb|neighbourhood|town|quarter|village)$"]["name"]'
        f"({south},{west},{north},{east});"
        f"out tags center;"
    )

    features = []
    for element in data.get("elements", []):
        name = (element.get("tags", {}).get("name") or "").strip()
        lon, lat = element.get("lon"), element.get("lat")
        if not name or lon is None or lat is None:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"name": name, "place": element["tags"].get("place", "")},
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(lon, PRECISION), round(lat, PRECISION)],
                },
            }
        )

    features.sort(key=lambda feature: feature["properties"]["name"])
    return {
        "type": "FeatureCollection",
        "name": "ahmedabad_localities",
        "licence": LICENCE,
        "features": features,
    }


def write(path: Path, collection: dict[str, Any]) -> None:
    # No spaces in separators: this file is committed and never hand-edited,
    # so bytes matter more than readability.
    path.write_text(json.dumps(collection, separators=(",", ":")))
    print(
        f"  {path.name}: {len(collection['features'])} features, "
        f"{path.stat().st_size / 1024:.0f} KB"
    )


#: filename -> (fetcher, minimum plausible feature count, label)
DATASETS = {
    "ahmedabad_talukas.geojson": (fetch_talukas, 5, "city talukas"),
    "ahmedabad_roads.geojson": (fetch_roads, 500, "arterial roads"),
    "ahmedabad_localities.geojson": (fetch_localities, 40, "localities"),
}


def main() -> None:
    force = "--force" in sys.argv[1:]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for filename, (fetcher, minimum, label) in DATASETS.items():
        path = OUT_DIR / filename
        if path.exists() and not force:
            existing = json.loads(path.read_text())
            print(
                f"  {filename}: present ({len(existing['features'])} features), skipping"
            )
            continue

        print(f"Fetching Ahmedabad {label}…")
        collection = fetcher()
        count = len(collection["features"])
        if count < minimum:
            sys.exit(
                f"Expected at least {minimum} {label}, got {count}. Refusing to write."
            )
        write(path, collection)

    print("\nDone. These files are committed; the demo never refetches them.")


if __name__ == "__main__":
    main()
