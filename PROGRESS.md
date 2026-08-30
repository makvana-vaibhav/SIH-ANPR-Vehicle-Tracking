# Sentinel-GJ — progress & next steps

**Read this first.** One page: what works, what doesn't, what to do next.

*Updated 30 Aug 2026 · 7 of 14 phases done · **11 days to the event** (10–11 Sep,
registration closes 7 Sep)*

Detail lives in [BUILD_STATE.md](BUILD_STATE.md) (per-phase gates and every bug
fixed), [docs/STATUS.md](docs/STATUS.md) (real vs simulated, explained), and
[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) (challenge compliance map).

---

## Where we are

The **platform** was built first; the **intelligence** now works too. Plates are
read off live camera streams, watchlist hits raise alerts by themselves, and an
alert reaches a connected operator in **24 ms**. What is still missing is the
part that ties sightings together across cameras — the correlator — and that is
what the organisers actually score (FAQ Q26–28).

```
DONE      Phase 0 ──▶ 1 ──▶ 2 ──▶ 3 ──▶ 4 ──▶ 5 ──▶ 6   (platform + ANPR + alerts)
NEXT      Phase 7 ──▶ 8                                  (routes → search)
THEN      Phase 9 ──▶ 10 ──▶ 11 ──▶ 12                   (UI, scale proof, docs, hardening)
LATER     Phase 13 ──▶ 14                                (face/person, government DBs)
```

By the numbers: 11 containers · 48 API operations · 25,000 lines Python ·
2,800 lines TypeScript · 267 API + 161 AI-lab + 10 worker + 11 frontend tests ·
16 commits.

**One thing is blocked and it is not on us.** The organisers' camera grid at
`live.corp8.cloud` returns Cloudflare 502 — their origin is down. Everything
needed to consume it is built and waiting: see *Blocked* below.

---

## ✅ Done

### Phase 0 — Foundation
`docker compose up` brings 9 services healthy in ~20s from cold. Every image
pinned and arm64-native. Structured JSON logging with request correlation.
Liveness vs readiness properly separated.

### Phase 1 — Auth, RBAC, audit
Six roles with genuinely different access, enforced by one declarative matrix.
argon2id, JWT access+refresh, refresh-token rotation, revocation that fails
closed. Every mutating request and every search writes an `audit_log` row —
including failed logins and permission denials.

### Phase 2 — Registry + GIS
250 cameras across 5 departments and 5 VMS vendors, on real Gujarat junction and
highway coordinates. PostGIS radius search, district queries, GeoJSON for the
map. CSV bulk onboarding with row-level errors and dry-run. 33 district
boundaries committed (330 KB) — **no tile server, works offline**.

### Phase 3 — Integration + health monitoring
Five real adapters behind one interface: RTSP (ffprobe), ONVIF (SOAP,
defusedxml), vendor REST (covers Milestone/Genetec/CP Plus/Hikvision), the
challenge's own sandbox grid, and the simulator. Health monitor probes on a
staggered 15s cycle with bounded concurrency. **Gap-analysis reports** —
a mandatory Model 1 deliverable.

### Phase 4 — Stream gateway + portal
Live video in the browser over WebRTC/WHEP, playing real traffic footage.
Access is a short-lived camera-scoped token, and every stream open is audited.
Portal screens: Login · GIS Map (layered filters) · Camera detail with live
video and uptime history · Fleet Health · Integration.

**Verified:** killed stream detected offline in **19s** (target <30s) with a
high-priority alert; 250 GeoJSON features; nearby search ordered correctly.

### Phase 5 — AI pipeline (ANPR)
Built as [`ai-lab/`](ai-lab/README.md), a standalone measurable environment, then
deployed as the `ai-worker` service. Vehicle detection → ByteTrack → plate
detection → crop conditioning → OCR → **multi-frame consensus** → track merge →
structured event. Torch-free: ONNX Runtime throughout.

Consensus votes per character position weighted by OCR confidence × crop
quality, because a recogniser handed an unreadable crop returns a confident
wrong answer rather than an error. Position-aware confusion correction fits the
string to an Indian plate template first, so `8`→`B` is applied in a letter slot
and never in a digit slot.

**Selective inference** is what makes it viable live: a vehicle is searched when
its view has materially changed, and a crop is read when it is new evidence.
Plate detection fell from 13.3 calls per frame to 1.45, OCR from 14.2 to 0.7.

**Measured** (generated footage with known plates): 100% exact plate match, CER
0.000, 0 missed, 0 duplicate vehicles, precision 0.83. 4K throughput went from
7665 ms/frame to 1151 ms (360 ms with diagnostics off) — profiled, not guessed.
Live path: median capture-to-event latency **385 ms** while dropping 70% of
frames, so latency stays bounded instead of growing.

Also compliant with the organisers' streaming contract: RTSP forced over TCP,
timing driven by PTS rather than arrival, exponential reconnect backoff, and
scene-discontinuity recovery at the loop point.

### Phase 6 — Events, watchlist, alerts
Redis Streams consumer → detection persisted → watchlist matched → alert raised
→ pushed to connected operators. **Gate passed at 24 ms** against a 2-second
budget, carrying the case reference so no follow-up lookup is needed.

Exact matches raise `watchlist_hit` at the entry's own priority; one-character
matches raise `possible_match` capped at medium, because raising a maybe as
critical teaches operators to distrust critical. Matching uses a deletion index,
so lookup is proportional to plate length rather than watchlist size.

Deduplication is per (plate, camera) over 90s — a car stopped at a junction must
not raise an alert per detection — but the same plate at a *different* camera is
deliberately a new alert, because that is the vehicle moving. Lifecycle is
new → acknowledged → dispatched → closed / false_positive, every transition
recording who and when, with `false_positive` a first-class outcome rather than
a delete.

**Verified live:** exact hit → critical; one character off → medium possible
match; expired BOLO → nothing; unlisted plate → nothing; repeat inside the
window → folded into the original.

---

## 🚧 Blocked

**The organisers' camera grid is down.** `https://live.corp8.cloud` returns
Cloudflare **502** at both `/` and `/api/ingest` — their origin, not our network.
The host is not published publicly; it sits behind the login at
`sentinel.gujarat.gov.in/login`.

Everything to consume it exists and is waiting:

* `SentinelSandboxAdapter` reads the catalogue and extracts coordinates in all
  three shapes the field uses (flat `lat`/`lon`, nested object, GeoJSON pair).
* `scripts/sync_sandbox.py --base-url https://live.corp8.cloud --dry-run` prints
  every camera with its coordinates, codec and resolution; without `--dry-run`
  it loads them into the registry and onto the map.
* The stream reader already obeys their integration guide (§ Phase 5).

**Camera locations come from their catalogue and nowhere else.** Entries without
coordinates are registered and watchable but kept off the map — placing a camera
at a guessed location is worse than admitting we do not know where it is.

---

## ⏳ Pending

| Phase | Delivers | Why it matters |
|---|---|---|
| **7 · Correlator** | Cross-camera route reconstruction, plausibility scoring, convoy detection | **Judge Moment 4** — and the organisers' scored live test case (FAQ Q26–28) |
| **8 · Search** | OpenSearch partial/fuzzy plate search, pg_trgm fallback | Partial plate → ranked results in <300ms |
| **9 · Remaining UI** | Dashboard, video wall, alerts triage, vehicle profile with animated route, watchlist manager, admin, architecture page | Where the five judge moments are actually performed |
| **10 · Scale proof** | Redpanda, 3 AI workers, 80k-camera load test, k6, Grafana | **Judge Moment 5** — measured numbers, not claims |
| **11 · Documentation** | HLD, INFRASTRUCTURE, SECURITY, API, DEMO_SCRIPT | A required submission artifact |
| **12 · Demo hardening** | `make demo` full path, 500k synthetic detections, e2e test, PANIC.md | Protects the live demo |
| **13 · Person & face** | Person detection, crowd counting, face detection + matching | Required (FAQ Q22). Ships with its own permission and audit path |
| **14 · Government DBs** | VAHAN / SARTHI / eGujCop adapters | Required (FAQ Q22). Real interface, labelled local dataset |

---

## 🔧 Known debt

Real, verified, and not to be mistaken for done.

| Issue | Severity | Note |
|---|---|---|
| **Accuracy measured on generated plates** | **High** | None of the sample footage has a legible plate, so ANPR accuracy comes from rendered plates and **is optimistic by construction**. Real labelled Gujarat footage is the single most valuable thing that could be added |
| **Sandbox grid unreachable** | **High** | `live.corp8.cloud` → Cloudflare 502. Consumption path is built; nothing has run against a real feed yet |
| **mypy fails — 17 errors, 8 files** | Medium | `make lint` never ran it. CLAUDE.md §5 claims "Python passes mypy" — **currently false**. Fix or amend the claim |
| Plate detector weights are AGPL-3.0 | Medium | Ultralytics export. Fine for evaluation, must be replaced before production — see ai-lab/README.md |
| One OCR error survives consensus | Medium | `GJ35K5714` → `GJ35X5714`. Both letters in a letter slot, so grammar cannot repair it and every frame agreed. Needs a better recogniser or a fine-tune |
| Vendor adapters never met a real VMS | Medium | Code is real and unit-tested; no Milestone/Genetec server has been on the other end |
| Evidence crops not in MinIO | Medium | The worker records crop keys; upload to object storage is not wired. `Detection.crop_key` expects a MinIO key |
| GPU path never executed | Medium | Provider selection is one function and the CUDA branch is written, but this machine has no CUDA. **No GPU figure is claimed anywhere** |
| ~1 camera per CPU worker | Medium | 4.4 fps at 720p, 2.8 at 4K. Reaching many cameras is a GPU and node-count question this hardware cannot answer |
| HLS fallback opens a raw `.m3u8` | Low | Non-Safari browsers download instead of playing. Needs hls.js or an embedded page |
| No API rate limiting | Low | Required before anything is exposed beyond localhost |
| Health debounce counters in-memory | Low | Monitor restart resets the failure count. Deliberate, but know it |
| Alembic `downgrade()` never run | Low | Written, untested |
| Portrait clips pillarboxed | Cosmetic | `fetch_videos.sh` pads rather than crops |

---

## ▶ Next steps, in order

### 1. Phase 7 — the correlator ← **start here**
The organisers' scored live test case (FAQ Q26–28): a designated vehicle tracked
across cameras with a complete timestamped route. Everything it needs now
exists — detections carry plate, camera and timestamp, and one physical vehicle
produces one event.

- Fetch sightings by plate and window, cluster into hops (merging same-camera
  sightings inside a dwell window)
- Great-circle distance and elapsed time per consecutive pair → implied speed
- Plausibility scoring: implausible above ~150 km/h, or below ~2 km/h across a
  long gap, or when `heading_deg` contradicts the direction of travel
- **Mark low-confidence hops rather than dropping them.** Judges respect a
  system that shows what it is unsure of
- `GET /vehicles/{plate}/route?from&to` → GeoJSON plus an ordered hop table,
  straight-line segments labelled as such
- Convoy detection: two plates co-occurring across ≥3 cameras in a tight window

*Gate:* a seeded plate yields Rajkot → Gondal → Jetpur → Junagadh with sane
implied speeds, as valid GeoJSON.

### 2. Phase 8 — search
OpenSearch edge-ngram + fuzzy for partial plates, `pg_trgm` fallback chosen at
runtime so the demo survives a dead container. Every search audited.
*Gate:* partial-plate search under 300 ms over 500k detections.

### 3. Phase 9 — the remaining UI
Alerts triage, vehicle profile with animated route playback, watchlist manager,
video wall, dashboard, architecture page. This is where the judge moments are
actually performed — the backend for moments 3 and 4 now exists but has no
screen.

### 4. Then
Phase 10 load test → Phase 11 docs → Phase 12 hardening.

### The moment the sandbox comes back
1. `scripts/sync_sandbox.py --base-url https://live.corp8.cloud --dry-run`
2. Drop `--dry-run` to load real cameras and coordinates
3. Point the worker at them and confirm plates off a real feed

That last step is the one that turns "measured on generated plates" into a real
accuracy number, so do it the same day the grid is reachable.

### Do before submission (not urgent, but not optional)
- Clear the mypy debt and add it to `make lint`
- Replace the AGPL plate weights
- Wire evidence crops into MinIO
- Raise `SIM_STREAM_COUNT` toward 50 and confirm the laptop holds
- `docs/DEMO_SCRIPT.md` — the 8-minute walkthrough with fallbacks

---

## Running it

```bash
make demo        # up + migrate + seed + open browser
make videos      # fetch real traffic footage (one time)
make status      # dependency readiness
make test        # 267 API + 11 frontend tests
make logs S=api  # tail one service

cd ai-lab
make doctor      # AI models and engines
make test        # 161 AI-lab tests
make validate    # end-to-end ANPR against known plates
```

Watch a plate become an alert:

```bash
# 1. put a plate on the watchlist (or use the seeded GJ03AB1234)
# 2. connect to the live feed
wscat -c "ws://localhost:8000/ws/events?token=$TOKEN"
# 3. the ai-worker reads plates off the camera streams; a watchlist hit
#    arrives as alert.raised within ~25 ms of the detection landing
```

| Surface | URL |
|---|---|
| Command centre | http://localhost:8080 — `admin` / `Sentinel@2026` |
| API docs | http://localhost:8000/docs |
| Simulator control | http://localhost:9100/streams |

Watch health monitoring react live:

```bash
curl -X POST http://localhost:9100/streams/CAM-00086/stop
# ~19s later the marker turns red and a high-priority alert is raised
curl -X POST http://localhost:9100/streams/CAM-00086/start
```

---

## The three things that must stay true

1. **Model 1 is compulsory** (FAQ Q12) and is complete — registry, GIS map with
   layered filters, health monitoring, gap analysis, audit trails.
2. **The central tier carries events, not video.** 320 Gbps centralised versus
   43 Mbps of metadata — a ~7,000× reduction. Every architecture decision
   follows from this.
3. **Never mock, never stub.** If a phase is marked done, it runs. Where
   something is simulated, it is labelled — see [docs/STATUS.md](docs/STATUS.md) §2.
