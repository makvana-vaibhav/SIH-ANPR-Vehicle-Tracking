"""Filter an all-India ADM2 boundary file down to Gujarat's districts.

Called by scripts/fetch_geodata.sh. Kept separate so the transformation is
reviewable and testable on its own.

Matching is by district name against the official list, because the source
data carries no state attribute. Every match is additionally required to have
its centroid inside Gujarat's bounding box, so a same-named district elsewhere
in India (there are several) cannot slip into the state map.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Gujarat's 33 districts, with the spelling variants that appear across
# open datasets mapped onto one canonical form.
DISTRICT_ALIASES: dict[str, str] = {
    # Census spellings differ from common usage: geoBoundaries carries
    # "Ahmadabad" and "Batod".
    "ahmedabad": "Ahmedabad",
    "ahmadabad": "Ahmedabad",
    "amreli": "Amreli",
    "anand": "Anand",
    "aravalli": "Aravalli",
    "arvalli": "Aravalli",
    "aravali": "Aravalli",
    "banaskantha": "Banaskantha",
    "banas kantha": "Banaskantha",
    "bharuch": "Bharuch",
    "bhavnagar": "Bhavnagar",
    "botad": "Botad",
    "batod": "Botad",
    "chhotaudepur": "Chhota Udaipur",
    "chhota udaipur": "Chhota Udaipur",
    "chhotaudaipur": "Chhota Udaipur",
    "chhota udepur": "Chhota Udaipur",
    "dahod": "Dahod",
    "dohad": "Dahod",
    "dang": "Dang",
    "dangs": "Dang",
    "the dangs": "Dang",
    "devbhoomi dwarka": "Devbhoomi Dwarka",
    "devbhumi dwarka": "Devbhoomi Dwarka",
    "dwarka": "Devbhoomi Dwarka",
    "gandhinagar": "Gandhinagar",
    "gir somnath": "Gir Somnath",
    "girsomnath": "Gir Somnath",
    "jamnagar": "Jamnagar",
    "junagadh": "Junagadh",
    "kheda": "Kheda",
    "kachchh": "Kachchh",
    "kutch": "Kachchh",
    "kachchh (kutch)": "Kachchh",
    "mahisagar": "Mahisagar",
    "mehsana": "Mehsana",
    "mahesana": "Mehsana",
    "morbi": "Morbi",
    "narmada": "Narmada",
    "navsari": "Navsari",
    "panchmahal": "Panchmahal",
    "panch mahals": "Panchmahal",
    "panchmahals": "Panchmahal",
    "patan": "Patan",
    "porbandar": "Porbandar",
    "rajkot": "Rajkot",
    "sabarkantha": "Sabarkantha",
    "sabar kantha": "Sabarkantha",
    "surat": "Surat",
    "surendranagar": "Surendranagar",
    "tapi": "Tapi",
    "vadodara": "Vadodara",
    "valsad": "Valsad",
}

# Gujarat's bounding box, padded.
LAT_MIN, LAT_MAX = 20.0, 24.8
LON_MIN, LON_MAX = 68.0, 74.7


def centroid(geometry: dict[str, Any]) -> tuple[float, float]:
    """Rough centroid: the mean of every vertex.

    Good enough to decide which state a district sits in, and avoids a
    shapely dependency in a script that runs on the host.
    """
    points: list[list[float]] = []

    def walk(coords: Any) -> None:
        if coords and isinstance(coords[0], (int, float)):
            points.append(coords)
        else:
            for item in coords:
                walk(item)

    walk(geometry["coordinates"])
    return (
        sum(p[0] for p in points) / len(points),
        sum(p[1] for p in points) / len(points),
    )


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: filter_gujarat_districts.py <input.geojson> <output.geojson>")
        return 2

    source = json.loads(Path(sys.argv[1]).read_text())
    features: list[dict[str, Any]] = []
    seen: set[str] = set()

    for feature in source.get("features", []):
        raw_name = (feature["properties"].get("shapeName") or "").strip()
        canonical = DISTRICT_ALIASES.get(raw_name.lower())
        if canonical is None or canonical in seen:
            continue

        lon, lat = centroid(feature["geometry"])
        if not (LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX):
            continue  # same name, different state

        seen.add(canonical)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "district": canonical,
                    "state": "Gujarat",
                    "centroid": [round(lon, 5), round(lat, 5)],
                },
                "geometry": feature["geometry"],
            }
        )

    features.sort(key=lambda f: f["properties"]["district"])

    collection = {
        "type": "FeatureCollection",
        "properties": {
            "name": "Gujarat district boundaries",
            "source": "geoBoundaries gbOpen IND ADM2 (simplified)",
            "source_url": "https://www.geoboundaries.org",
            "licence": "ODbL 1.0 / CC-BY 4.0",
            "district_count": len(features),
        },
        "features": features,
    }

    out_path = Path(sys.argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Compact separators: this file ships in the repo and is fetched by the
    # browser on every map load.
    out_path.write_text(json.dumps(collection, separators=(",", ":")))

    expected = set(DISTRICT_ALIASES.values())
    missing = sorted(expected - seen)

    print(f"  districts written : {len(features)} / {len(expected)}")
    print(f"  output size       : {out_path.stat().st_size / 1024:.0f} KB")
    if missing:
        print(f"  NOT FOUND in source: {', '.join(missing)}")

    if len(features) < 25:
        print("ERROR: too few districts matched — the source schema may have changed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
