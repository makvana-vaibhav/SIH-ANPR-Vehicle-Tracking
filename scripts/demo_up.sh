#!/usr/bin/env bash
#
# The full demonstration path, from a fresh clone to a system a judge can use.
#
# `make up` brings the containers up. This does the rest — the steps that were
# previously typed by hand and therefore forgotten under pressure: shaping the
# ANPR fleet, pointing the grid cameras at the organisers' current endpoints,
# and starting the inference worker.
#
# Every step is verified rather than assumed. A demo that reports success and
# then shows an empty screen is worse than one that fails loudly here, where
# there is still time to fix it.
set -euo pipefail

cd "$(dirname "$0")/.."

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; RED=$'\033[31m'
YELLOW=$'\033[33m'; RESET=$'\033[0m'

step()  { printf '\n%s▸ %s%s\n' "$BOLD" "$1" "$RESET"; }
ok()    { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn()  { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
fail()  { printf '  %s✗%s %s\n' "$RED" "$RESET" "$1"; }

API=${API_URL:-http://localhost:8000}
WEB=${WEB_URL:-http://localhost:8080}
ADMIN_PW=${BOOTSTRAP_ADMIN_PASSWORD:-Sentinel@2026}

wait_for() {  # wait_for <url> <seconds> <label>
    local url=$1 limit=$2 label=$3 waited=0
    until curl -sf "$url" >/dev/null 2>&1; do
        sleep 2; waited=$((waited + 2))
        if [ "$waited" -ge "$limit" ]; then fail "$label did not come up in ${limit}s"; return 1; fi
    done
    ok "$label ready (${waited}s)"
}

token() {
    curl -s -X POST "$API/api/v1/auth/login" -H 'Content-Type: application/json' \
        -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PW\"}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("access_token",""))' 2>/dev/null
}

api() { curl -s -H "Authorization: Bearer $TOKEN" "$API$1"; }

# ── 1. Containers ────────────────────────────────────────────────────
step "Bringing the stack up"
docker compose up -d >/dev/null 2>&1
wait_for "$API/health" 180 "API"

# ── 2. Schema and seed data ──────────────────────────────────────────
step "Schema and seed data"
docker compose exec -T -w /app/services/api api alembic upgrade head >/dev/null 2>&1 \
    && ok "migrations at head" || { fail "migrations failed"; exit 1; }

if docker compose exec -T api python /app/scripts/seed.py >/dev/null 2>&1; then
    ok "seed data loaded"
else
    warn "seed reported an error (it is idempotent; continuing)"
fi

# ── 3. The camera fleet ──────────────────────────────────────────────
# Without these two the ANPR fleet is 239 synthetic cameras with no video,
# and the grid cameras point at endpoints the organisers retired.
step "Shaping the camera fleet"
docker compose exec -T api python /app/scripts/shape_fleet.py 2>/dev/null \
    | grep -E "demonstration camera|ANPR fleet|federated grid" | sed 's/^/  /' \
    || warn "shape_fleet did not report"

docker compose exec -T api python /app/scripts/retarget_grid.py 2>/dev/null \
    | grep -E "grid cameras retargeted" | sed 's/^/  /' \
    || warn "retarget_grid did not report"

# ── 4. Demonstration footage ─────────────────────────────────────────
step "Demonstration footage"
if [ -f data/videos/anpr_demo.mp4 ]; then
    ok "anpr_demo.mp4 present ($(du -h data/videos/anpr_demo.mp4 | cut -f1))"
else
    warn "anpr_demo.mp4 missing — run 'make videos'."
    warn "Live ANPR will have no camera that reliably reads plates."
fi

docker compose up -d simulator >/dev/null 2>&1
sleep 8
if curl -s "http://localhost:9100/streams" 2>/dev/null | grep -q CAM-DEMO; then
    ok "CAM-DEMO publishing"
else
    warn "CAM-DEMO is not publishing yet — it may need another few seconds"
fi

# ── 5. Inference ─────────────────────────────────────────────────────
step "Starting the AI worker"
if [ -f ai-lab/models/yolov8n.onnx ]; then
    docker compose --profile ai up -d ai-worker >/dev/null 2>&1
    ok "ai-worker started"
else
    fail "model weights missing — run 'make models' first"
    fail "Without them nothing reads a plate."
fi

# ── 6. Verify what a judge will actually see ─────────────────────────
step "Verifying the judge moments"
TOKEN=$(token)
if [ -z "$TOKEN" ]; then fail "cannot sign in as admin"; exit 1; fi
ok "admin can sign in"

CAMERAS=$(api "/api/v1/cameras/summary" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("total",0))' 2>/dev/null || echo 0)
[ "${CAMERAS:-0}" -gt 100 ] && ok "$CAMERAS cameras on the map" || fail "only $CAMERAS cameras — seed may have failed"

ANPR=$(api "/api/v1/cameras/summary" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("anpr_enabled",0))' 2>/dev/null || echo 0)
ok "$ANPR cameras in the ANPR fleet"

printf '  %s…waiting up to 90s for the first plate read%s\n' "$DIM" "$RESET"
PLATES=0
for _ in $(seq 1 18); do
    # %Y-%m-%dT%H:%M:%SZ, not isoformat(): isoformat emits "+00:00" and an
    # unencoded "+" in a query string decodes to a space, so the API rejects
    # the whole request and this check silently reports zero plate reads.
    SINCE=$(python3 -c 'import datetime;print((datetime.datetime.now(datetime.UTC)-datetime.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"))')
    PLATES=$(api "/api/v1/detections?limit=1&since=$SINCE" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("total",0))' 2>/dev/null || echo 0)
    [ "${PLATES:-0}" -gt 0 ] && break
    sleep 5
done
[ "${PLATES:-0}" -gt 0 ] && ok "$PLATES plate reads in the last 10 minutes" \
    || warn "no plates yet — the worker may still be starting. Check: docker compose logs ai-worker"

# Asserted, not printed. This reported "0 plates on the watchlist" as a
# success for as long as it existed, while judge moments 3 and 4 had nothing
# to fire on — the seed had never loaded data/seed/watchlist.csv at all.
WL=$(api "/api/v1/watchlist" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))' 2>/dev/null || echo 0)
[ "${WL:-0}" -gt 0 ] && ok "$WL plates on the watchlist" \
    || fail "watchlist is empty — the alert and route moments cannot fire"

curl -sf "$WEB" >/dev/null 2>&1 && ok "command centre reachable" || fail "web is not responding"

# ── Done ─────────────────────────────────────────────────────────────
printf '\n%s%sDemo ready%s → %s\n' "$BOLD" "$GREEN" "$RESET" "$WEB"
printf '%s  admin / %s · script: docs/DEMO_SCRIPT.md · if it breaks: PANIC.md%s\n\n' \
    "$DIM" "$ADMIN_PW" "$RESET"
