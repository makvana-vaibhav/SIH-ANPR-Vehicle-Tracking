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
UA="nagarnetra/0.1 (SIH26127 city ANPR research; +https://github.com/makvana-vaibhav/SIH-ANPR-Vehicle-Tracking)"

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

# The ANPR demonstration clip. The four Wikimedia clips above are real road
# scenes but were shot too far from the traffic for any plate to be legible —
# useful for tracking and health, useless for showing OCR. This one is the
# sample published with the reference ANPR project (Muhammad-Zeerak-Khan,
# Automatic-License-Plate-Recognition-using-YOLOv8): 4K, close to the traffic,
# plates readable. It carries UK plates, which is why `configs/demo.yaml`
# accepts UK grammar. Hosted on Google Drive, so it is fetched separately and
# a failure here is not fatal — the rest of the fleet still works.
ANPR_CLIP_ID="1JbwLyqpFCXmftaJY1oap8Sa6KfjoWJta"
# 15 seconds is enough to see a dozen plates read and keeps the file small
# enough to loop as a camera without hogging the disk cache.
DEMO_SECONDS=15

have_ffmpeg() { command -v ffmpeg >/dev/null 2>&1; }

# Google Drive interposes a virus-scan interstitial on large files; the confirm
# token has to be read back out of it.
fetch_from_drive() {
    local id="$1" dst="$2" cookies="${WORK_DIR}/drive.cookies"
    local confirm
    confirm=$(curl -sL -c "${cookies}" -A "${UA}" \
        "https://drive.usercontent.google.com/download?id=${id}&export=download" \
        | grep -o 'name="confirm" value="[^"]*"' | head -1 | cut -d'"' -f4)
    curl -sL -b "${cookies}" -A "${UA}" --max-time 900 --retry 2 -o "${dst}" \
        "https://drive.usercontent.google.com/download?id=${id}&export=download&confirm=${confirm:-t}"
    rm -f "${cookies}"
    [ -s "${dst}" ] && [ "$(wc -c < "${dst}")" -gt 1000000 ]
}

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

# ── The ANPR demonstration clip ──────────────────────────────────────────────
anpr_sample="${VIDEO_DIR}/anpr_sample.mp4"
anpr_demo="${VIDEO_DIR}/anpr_demo.mp4"

if [ -f "${anpr_demo}" ] && [ -z "${FORCE:-}" ]; then
    printf '  \033[2m%-20s already present\033[0m\n' "anpr_demo"
    skipped=$((skipped + 1))
else
    if [ ! -f "${anpr_sample}" ] || [ -n "${FORCE:-}" ]; then
        printf '  %-20s downloading… ' "anpr_sample"
        if fetch_from_drive "${ANPR_CLIP_ID}" "${anpr_sample}"; then
            printf '\033[32m✓\033[0m %s MB\n' "$(( $(wc -c < "${anpr_sample}") / 1000000 ))"
        else
            rm -f "${anpr_sample}"
            printf '\033[33mfailed — ANPR demo clip unavailable\033[0m\n'
            printf '     \033[2mGet it manually from https://drive.google.com/file/d/%s/view\033[0m\n' "${ANPR_CLIP_ID}"
            printf '     \033[2mand save it as data/videos/anpr_sample.mp4, or use\033[0m\n'
            printf '     \033[2mscripts/generate_synthetic_plates.py for Gujarat plates with ground truth.\033[0m\n'
        fi
    fi

    if [ -f "${anpr_sample}" ]; then
        # The source is 4K/60 — far more than the pipeline needs and slow to
        # loop. Cut a 15 s window at 1080p/15fps, which is what a decent
        # municipal camera actually delivers.
        printf '  %-20s transcoding… ' "anpr_demo"
        if have_ffmpeg; then
            ffmpeg -hide_banner -loglevel error -y -t "${DEMO_SECONDS}" -i "${anpr_sample}" \
                -vf "scale=1920:1080" -r 15 -c:v libx264 -preset veryfast -crf 24 \
                -pix_fmt yuv420p -an "${anpr_demo}" 2>/dev/null
        else
            docker compose exec -T api ffmpeg -hide_banner -loglevel error -y \
                -t "${DEMO_SECONDS}" -i "/data/videos/anpr_sample.mp4" \
                -vf "scale=1920:1080" -r 15 -c:v libx264 -preset veryfast -crf 24 \
                -pix_fmt yuv420p -an "/data/videos/anpr_demo.mp4" 2>/dev/null
        fi
        if [ -s "${anpr_demo}" ]; then
            printf '\033[32m✓\033[0m %s MB\n' "$(( $(wc -c < "${anpr_demo}") / 1000000 ))"
            fetched=$((fetched + 1))
        else
            printf '\033[33mfailed\033[0m\n'
        fi
    fi
fi

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
    printf '| `anpr_sample.mp4` | see source | Sample published with the reference ANPR project |\n'
    printf '| `anpr_demo.mp4` | see source | 15 s 1080p cut of `anpr_sample.mp4`, pinned to CAM-00001 |\n'
    printf '\nAll clips are re-encoded to %sx%s H.264 at %s fps for replay, except\n' \
        "${TARGET_WIDTH}" "${TARGET_HEIGHT}" "${TARGET_FPS}"
    printf 'the ANPR clip, kept at 1080p because plate legibility is its whole point.\n'
    printf '\nNote on plates: the four Wikimedia clips were shot too far from the\n'
    printf 'traffic for any plate to be legible — they exercise detection, tracking\n'
    printf 'and camera health, not OCR. `anpr_demo.mp4` is the clip where plates are\n'
    printf 'actually readable, and they are UK plates, which is why `configs/demo.yaml`\n'
    printf 'accepts UK grammar. Gujarat-format plates with exact ground truth to\n'
    printf 'score against come from `scripts/generate_synthetic_plates.py`.\n'
} > "${VIDEO_DIR}/ATTRIBUTION.md"

printf '\n\033[32m%s fetched, %s already present\033[0m → %s\n' \
    "${fetched}" "${skipped}" "data/videos/"
printf 'Restart the simulator to pick them up:  docker compose restart simulator\n'
