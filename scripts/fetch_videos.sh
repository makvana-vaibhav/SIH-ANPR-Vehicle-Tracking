#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════
#  Fetch sample traffic footage for the camera simulator.
#
#  Downloads openly-licensed road and traffic clips, transcodes them to
#  H.264 MP4 at a CCTV-like resolution, and drops them in data/videos/.
#  The simulator then replays them into MediaMTX as RTSP, so the video wall
#  shows real traffic instead of a test pattern.
#
#  Videos are GITIGNORED — this script is the committed artifact, and every
#  clip's licence and attribution is recorded in data/videos/ATTRIBUTION.md.
#
#  Without this, the simulator still publishes generated test patterns and
#  every downstream component keeps working. Real footage makes the demo
#  legible; it is not required for the platform to run.
#
#  Usage:  make videos            (or ./scripts/fetch_videos.sh)
#          FORCE=1 make videos    re-download even if present
# ══════════════════════════════════════════════════════════════════════
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VIDEO_DIR="${REPO_ROOT}/data/videos"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "${WORK_DIR}"' EXIT

# Wikimedia requires a descriptive User-Agent and refuses generic ones.
UA="sentinel-gj/0.1 (Gujarat Police CCTV hackathon; +https://sentinel.gujarat.gov.in)"

# Target encode: 1280x720 is a realistic municipal CCTV resolution and keeps
# ffmpeg cheap enough to run ~24 concurrent streams on a laptop.
TARGET_WIDTH=1280
TARGET_HEIGHT=720
TARGET_FPS=15

# name|licence|attribution|url
CLIPS=(
"street_crossing|CC BY-SA 4.0|Roncesvalles Ave crossing at Galley Ave (Wikimedia Commons)|https://upload.wikimedia.org/wikipedia/commons/0/06/Roncesvalles_Ave_crossing_at_Galley_Ave%2C_PXO_Type_A_lights_flashing%2C_July_2026.webm"
"highway_congestion|CC BY-SA 4.0|Ayalon traffic congestion time lapse (Wikimedia Commons)|https://upload.wikimedia.org/wikipedia/commons/1/1f/Ayalon_trafic_congestion_time_lapse.webm"
"junction_dashcam|CC BY-SA 4.0|Wymuszenie pierwszeństwa (Wikimedia Commons)|https://upload.wikimedia.org/wikipedia/commons/e/e9/Wymuszenie_pierwsze%C5%84stwa.webm"
"road_cctv|Public domain|Jilin Tonghua rear-end collision, road CCTV (Wikimedia Commons)|https://upload.wikimedia.org/wikipedia/commons/3/36/Jilin_Tonghua_car_rear-end_collision_accident%2C_2025-03-11.webm"
)

have_ffmpeg() { command -v ffmpeg >/dev/null 2>&1; }

# ffmpeg lives in the api image; use it when it is not on the host.
transcode() {
    local src="$1" dst="$2"
    local filter="scale=${TARGET_WIDTH}:${TARGET_HEIGHT}:force_original_aspect_ratio=decrease,pad=${TARGET_WIDTH}:${TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2"
    if have_ffmpeg; then
        ffmpeg -hide_banner -loglevel error -y -i "$src" \
            -vf "$filter" -r "${TARGET_FPS}" \
            -c:v libx264 -preset veryfast -crf 26 -pix_fmt yuv420p -an "$dst"
    else
        docker compose exec -T -w /work api ffmpeg -hide_banner -loglevel error -y \
            -i "/work/$(basename "$src")" -vf "$filter" -r "${TARGET_FPS}" \
            -c:v libx264 -preset veryfast -crf 26 -pix_fmt yuv420p -an \
            "/work/$(basename "$dst")"
    fi
}

mkdir -p "${VIDEO_DIR}"
printf '\033[1mFetching sample traffic footage\033[0m\n'

if ! have_ffmpeg && ! docker compose ps api --format '{{.State}}' 2>/dev/null | grep -q running; then
    printf '\033[31m✗ Needs ffmpeg on PATH, or the api container running (make up).\033[0m\n'
    exit 1
fi

fetched=0
skipped=0

for entry in "${CLIPS[@]}"; do
    IFS='|' read -r name licence attribution url <<< "${entry}"
    out="${VIDEO_DIR}/${name}.mp4"

    if [ -f "${out}" ] && [ -z "${FORCE:-}" ]; then
        printf '  \033[2m%-20s already present\033[0m\n' "${name}"
        skipped=$((skipped + 1))
        continue
    fi

    printf '  %-20s downloading… ' "${name}"
    raw="${WORK_DIR}/${name}.src"
    if ! curl -sL --max-time 300 --retry 2 -A "${UA}" -o "${raw}" "${url}"; then
        printf '\033[33mfailed (skipping)\033[0m\n'
        continue
    fi

    size=$(wc -c < "${raw}")
    if [ "${size}" -lt 100000 ]; then
        printf '\033[33mtoo small (%s bytes) — skipping\033[0m\n' "${size}"
        continue
    fi

    printf 'transcoding… '
    # Transcode inside the work dir the container can see.
    if have_ffmpeg; then
        transcode "${raw}" "${out}" 2>/dev/null || { printf '\033[33mtranscode failed\033[0m\n'; continue; }
    else
        cp "${raw}" "${VIDEO_DIR}/.${name}.src"
        docker compose exec -T api ffmpeg -hide_banner -loglevel error -y \
            -i "/data/videos/.${name}.src" \
            -vf "scale=${TARGET_WIDTH}:${TARGET_HEIGHT}:force_original_aspect_ratio=decrease,pad=${TARGET_WIDTH}:${TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2" \
            -r "${TARGET_FPS}" -c:v libx264 -preset veryfast -crf 26 -pix_fmt yuv420p -an \
            "/data/videos/${name}.mp4" 2>/dev/null \
            || { printf '\033[33mtranscode failed\033[0m\n'; rm -f "${VIDEO_DIR}/.${name}.src"; continue; }
        rm -f "${VIDEO_DIR}/.${name}.src"
    fi

    printf '\033[32m✓\033[0m %s MB\n' "$(( $(wc -c < "${out}") / 1000000 ))"
    fetched=$((fetched + 1))
done

# Attribution is a licence obligation for CC BY-SA material, not a nicety.
{
    printf '# Sample footage — sources and licences\n\n'
    printf 'Fetched by `scripts/fetch_videos.sh`. The video files themselves are\n'
    printf 'gitignored; this record is committed.\n\n'
    printf '| Clip | Licence | Source |\n|---|---|---|\n'
    for entry in "${CLIPS[@]}"; do
        IFS='|' read -r name licence attribution url <<< "${entry}"
        printf '| `%s.mp4` | %s | %s |\n' "${name}" "${licence}" "${attribution}"
    done
    printf '\nAll clips are re-encoded to %sx%s H.264 at %s fps for replay.\n' \
        "${TARGET_WIDTH}" "${TARGET_HEIGHT}" "${TARGET_FPS}"
    printf '\nNote: these are real road scenes, but the vehicles carry non-Indian\n'
    printf 'plates. Gujarat-format plates for ANPR accuracy measurement are\n'
    printf 'produced by `scripts/generate_synthetic_plates.py`, which gives exact\n'
    printf 'ground truth to score against.\n'
} > "${VIDEO_DIR}/ATTRIBUTION.md"

printf '\n\033[32m%s fetched, %s already present\033[0m → %s\n' \
    "${fetched}" "${skipped}" "data/videos/"
printf 'Restart the simulator to pick them up:  docker compose restart simulator\n'
