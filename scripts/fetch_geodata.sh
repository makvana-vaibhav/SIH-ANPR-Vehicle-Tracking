#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  Fetch and prepare Gujarat map data.
#
#  Downloads Indian ADM2 (district) boundaries from geoBoundaries, filters
#  them to Gujarat's 33 districts, and writes a compact GeoJSON into
#  data/seed/. The RESULT IS COMMITTED, so this script only needs to run
#  when the boundaries change — the demo itself never touches the network.
#
#  Source : geoBoundaries gbOpen (https://www.geoboundaries.org)
#  Licence: ODbL 1.0 / CC-BY 4.0 — attribution recorded in the output file
#           and in docs/INFRASTRUCTURE.md.
#
#  This is what "no tile server, works on a plane" is built on: MapLibre
#  renders these polygons plus the camera GeoJSON directly, with no basemap
#  tiles to download and no external service to call.
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${REPO_ROOT}/data/seed"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

# geoBoundaries stores release data in git-lfs; raw.githubusercontent returns
# a pointer file, so the media.githubusercontent host is required.
SRC_URL="https://media.githubusercontent.com/media/wmgeolab/geoBoundaries/main/releaseData/gbOpen/IND/ADM2/geoBoundaries-IND-ADM2_simplified.geojson"

echo "Fetching Indian district boundaries…"
curl -sSL --max-time 180 --retry 2 -o "${WORK_DIR}/ind_adm2.geojson" "${SRC_URL}"

size=$(wc -c < "${WORK_DIR}/ind_adm2.geojson")
if [ "${size}" -lt 100000 ]; then
    echo "ERROR: download is only ${size} bytes — expected several MB." >&2
    echo "       (A git-lfs pointer or a 404 page, not the dataset.)" >&2
    exit 1
fi
echo "  downloaded $((size / 1024 / 1024)) MB"

mkdir -p "${OUT_DIR}"
python3 "${REPO_ROOT}/scripts/filter_gujarat_districts.py" \
    "${WORK_DIR}/ind_adm2.geojson" \
    "${OUT_DIR}/gujarat_districts.geojson"

echo "Done. data/seed/gujarat_districts.geojson is committed to the repo."
