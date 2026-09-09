#!/usr/bin/env bash
#
# The full demonstration path, from a fresh clone to a system a judge can use.
#
# `make up` brings the containers up. This does the rest — the steps that were
# previously typed by hand and therefore forgotten under pressure: migrating and
# seeding, bringing the camera fleet live, and starting the inference worker.
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
ADMIN_PW=${BOOTSTRAP_ADMIN_PASSWORD:-NagarNetra@2026}

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

# ── 1. Dependencies, then schema, then the app ───────────────────────
#
# The order here is load-bearing and was wrong: it used to `docker compose up -d`
# everything and then wait for the API. On a fresh clone that can never work —
# the API queries `cameras` during startup, the table does not exist until
# migrations run, and migrations cannot run through `compose exec` because there
# is no healthy container to exec into. Compose returned non-zero, `set -e` fired,
# and `make demo` died 15 seconds in with "Error 1". Exactly the path a judge
# takes, and it was broken.
#
# So: dependencies first, migrate and seed with `compose run` (which needs no
# healthy app container), and only then start the app tier.
step "Starting dependencies"
# `--wait` blocks on the healthchecks. Without it `up -d` returns as soon as the
# containers are *started*, and migrations then race a Postgres that is still
# initialising a fresh data directory — which failed on one clean run and
# succeeded on the next purely on timing.
docker compose up -d --wait postgres redis minio mediamtx opensearch >/dev/null 2>&1 \
    || { fail "dependencies did not become healthy"; exit 1; }
ok "postgres, redis, minio, mediamtx, opensearch healthy"

step "Schema and seed data"
if docker compose run --rm --no-deps -T -w /app/services/api api \
        alembic upgrade head >/dev/null 2>&1; then
    ok "migrations at head"
else
    fail "migrations failed"; exit 1
fi

if docker compose run --rm --no-deps -T api python -m scripts.seed >/dev/null 2>&1; then
    ok "seed data loaded"
else
    warn "seed reported an error (it is idempotent; continuing)"
fi

step "Bringing the stack up"
docker compose up -d >/dev/null 2>&1 || true
wait_for "$API/health" 180 "API"

# ── 3. The camera fleet ──────────────────────────────────────────────
# The fleet is seeded, not synced: scripts/seed.py loads the Ahmedabad ANPR
# fleet from data/seed/cameras.csv, which places every camera on a real vertex
# of a real named arterial. Step 2 above already did it, so this step verifies
# rather than repeats.
#
# Every camera here is published by the simulator, so it can be opened,
# watched and analysed. That is the distinction from the old 281-camera fleet,
# 251 of which had no video source at all.
step "The camera fleet"

FLEET=$(docker compose exec -T postgres psql -U "${POSTGRES_USER:-nagarnetra}" \
    -d "${POSTGRES_DB:-nagarnetra}" -tAc \
    "select count(*) from cameras" 2>/dev/null | tr -d '[:space:]')
if [ "${FLEET:-0}" -gt 10 ]; then
    ok "$FLEET cameras seeded across the city corridors"
else
    fail "only ${FLEET:-0} cameras — did scripts/generate_ahmedabad_cameras.py run?"
fi

# The organisers' grid from the previous brief. Opt-in, because its host is not
# reachable and SANDBOX_BASE_URL is unset by default: running it unconditionally
# spent time and printed a warning on every single demo for no benefit.
if [ -n "${SANDBOX_BASE_URL:-}" ]; then
    docker compose exec -T api python /app/scripts/sync_sandbox.py 2>/dev/null \
        | grep -E "created|updated|unchanged" | sed 's/^/  /' \
        || warn "grid sync did not report"
    docker compose exec -T api python /app/scripts/retarget_grid.py 2>/dev/null \
        | grep -E "grid cameras retargeted" | sed 's/^/  /' || true
fi

# ── 4. Demonstration footage ─────────────────────────────────────────
step "Demonstration footage"
if [ -f data/videos/anpr_demo.mp4 ]; then
    ok "anpr_demo.mp4 present ($(du -h data/videos/anpr_demo.mp4 | cut -f1))"
else
    warn "anpr_demo.mp4 missing — run 'make videos'."
    warn "Live ANPR will have no camera that reliably reads plates."
fi

docker compose up -d simulator >/dev/null 2>&1

# Poll rather than sleep. On a cold cache the simulator first re-encodes each
# clip to a keyframe-dense copy (publisher.prepare_clip), which takes tens of
# seconds — a fixed `sleep 8` reported "0 streams publishing" on every fresh
# clone even though the fleet came up fine moments later.
printf '  %s…waiting for the fleet to publish (clips are prepared once)%s\n' "$DIM" "$RESET"
STREAMS=0
for _ in $(seq 1 30); do
    STREAMS=$(curl -s "http://localhost:9100/streams" 2>/dev/null \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("count",0))' 2>/dev/null || echo 0)
    [ "${STREAMS:-0}" -gt 5 ] && break
    sleep 5
done
if [ "${STREAMS:-0}" -gt 5 ]; then
    ok "$STREAMS cameras publishing live video"
else
    warn "only ${STREAMS:-0} streams publishing — check 'docker compose logs simulator'"
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

# Threshold is 10, not 100. The old fleet was 281 cameras of which 251 had no
# video source; a count in the hundreds proved only that a CSV had loaded. What
# matters now is that real cameras were onboarded, and there are about 31.
CAMERAS=$(api "/api/v1/cameras/summary" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("total",0))' 2>/dev/null || echo 0)
[ "${CAMERAS:-0}" -gt 10 ] && ok "$CAMERAS cameras in the registry" \
    || fail "only $CAMERAS cameras — the grid sync or the seed failed"

# Asserted, because every camera should now be analysable. A camera in this
# registry without ANPR is a camera whose stream could not be resolved.
ANPR=$(api "/api/v1/cameras/summary" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("anpr_enabled",0))' 2>/dev/null || echo 0)
[ "${ANPR:-0}" -gt 10 ] && ok "$ANPR cameras in the ANPR fleet" \
    || fail "only $ANPR cameras have a resolvable stream"

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
printf '%s  admin / %s · script: docs/DEMO_SCRIPT.md · if it breaks: docs/PANIC.md%s\n\n' \
    "$DIM" "$ADMIN_PW" "$RESET"
