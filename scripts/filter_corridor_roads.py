#!/usr/bin/env python3
"""Cut the Ahmedabad road network down to the twelve corridors we model.

`data/seed/ahmedabad_roads.geojson` is the full arterial network from OSM —
2,319 line features, 808 KB. The traffic map draws only the roads a camera
actually sits on, and there are twelve of them. Shipping the rest means the
browser parses ~2,000 features it will never draw, on a demo laptop that is
already sharing its cores with the AI worker.

So this filters to the corridors named in migration 0004, writing
`web/public/data/ahmedabad_corridors.geojson` for the map to fetch.

The corridor names are OSM `name` values, which is why the join works at all:
`scripts/generate_ahmedabad_cameras.py` placed every camera on a real OSM road
vertex and named the corridor after the road. Verified against the source data
— all twelve match linework.

Same shape as `filter_gujarat_districts.py`: a committed script producing a
committed artifact, so the derived file can always be regenerated and never has
to be trusted on faith.

    python3 scripts/filter_corridor_roads.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Transcribed from `services/api/alembic/versions/0004_camera_corridor.py`.
#: Deliberately a copy rather than an import: a migration describes the
#: database at one revision and must not change meaning when this script does.
#: If the two ever disagree, the migration is right and this is stale.
CORRIDORS = (
    "Sardar Patel Ring Road",
    "132 Ft. Ring Road",
    "120 Feet Ring Road",
    "Ashram Road",
    "Jawaharlal Nehru Road",
    "SG Highway",
    "Chimanlal Girdharlal Road",
    "Drive-in Road",
    "Naroda Road",
    "Narol Sarkhej Road",
    "Airport Road",
    "Ahmadabad Vadodara Expressway",
)


def main() -> int:
    source = REPO_ROOT / "data" / "seed" / "ahmedabad_roads.geojson"
    target = REPO_ROOT / "web" / "public" / "data" / "ahmedabad_corridors.geojson"

    if not source.exists():
        print(f"missing {source} — run scripts/fetch_ahmedabad_geodata.py first")
        return 1

    data = json.loads(source.read_text(encoding="utf-8"))
    wanted = set(CORRIDORS)

    features = [
        {
            "type": "Feature",
            "geometry": feature["geometry"],
            # Only the corridor name survives. The map joins on it and draws
            # nothing else, and every extra property is bytes the browser
            # parses for no reason.
            "properties": {"corridor": feature["properties"]["name"]},
        }
        for feature in data["features"]
        if feature["properties"].get("name") in wanted
        and feature["geometry"]["type"] == "LineString"
    ]

    found = {f["properties"]["corridor"] for f in features}
    for corridor in CORRIDORS:
        if corridor not in found:
            # Loud, not silent: a corridor with no linework is a road that
            # will never be drawn, and a map quietly missing a road is worse
            # than one that fails to build.
            print(f"  WARNING: no linework for {corridor!r}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}),
        encoding="utf-8",
    )

    size_kb = target.stat().st_size / 1024
    # ASCII only: a Windows console defaults to cp1252 and raises on an arrow,
    # which would crash this script after it had already written its output.
    print(
        f"{len(features)} features across {len(found)}/{len(CORRIDORS)} corridors "
        f"-> {target.relative_to(REPO_ROOT)} ({size_kb:.0f} KB, "
        f"from {len(data['features'])} source features)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
