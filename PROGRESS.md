# Sentinel-GJ — progress & next steps

**Read this first.** One page: what works, what doesn't, what to do next.

*Updated 31 Aug 2026 · 7 of 14 phases done · **10 days to the event** (10–11 Sep,
registration closes 7 Sep)*

Detail lives in [BUILD_STATE.md](BUILD_STATE.md) (per-phase gates and every bug
fixed), [docs/STATUS.md](docs/STATUS.md) (real vs simulated, explained), and
[docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) (challenge compliance map).

---

## Where we are

The **platform** was built first; the **intelligence** now works too. Plates are
read off live camera streams, watchlist hits raise alerts by themselves, and an
alert reaches a connected operator in **24 ms**. The whole chain has now been
run end to end with nothing staged — see *The ANPR demonstration* below.

Two things are missing. The **correlator** ties sightings together across
cameras, which is what the organisers actually score (FAQ Q26–28). And the
**frontend stops at the map**: alerts and the watchlist are complete, tested
APIs with no screen in front of them, so today the demonstration runs in a
terminal rather than in the product.

```
DONE      Phase 0 ──▶ 1 ──▶ 2 ──▶ 3 ──▶ 4 ──▶ 5 ──▶ 6   (platform + ANPR + alerts)
NEXT      Phase 9                                        (operator frontend)
THEN      Phase 7 ──▶ 8 ──▶ 10 ──▶ 11 ──▶ 12             (routes, search, scale, docs, hardening)
LATER     Phase 13 ──▶ 14                                (face/person, government DBs)
```

Phase 9 comes before 7 because the alerting backend is finished and invisible.
Building the correlator first would add a second capability with no screen.

By the numbers: 11 containers · 48 API operations · 25,000 lines Python ·
2,800 lines TypeScript · 276 API + 172 AI-lab + 10 worker + 11 frontend tests ·
22 commits.

**The organisers' grid is live and we are running on it** — 30 real cameras,
real junction names, real video. Vehicles are detected and tracked; **no plates
have been read from it**, and the reason is the cameras, not the pipeline. See
*The live grid, as measured* below. Plates *are* read, at 0.95–1.00 confidence,
from footage shot close enough to the traffic to resolve them.

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

## 📡 The live grid, as measured

`https://live.corp8.cloud` is up and the pipeline runs on it. What that actually
looks like, from probing rather than from the catalogue's own claims:

| | |
|---|---|
| cameras published | 30, all reporting `live: true` |
| registered with a location | 25 at city precision, 5 tagged `placement:unknown` |
| RTSP (8554) / WebRTC (8889) | **blocked** from this network; only 80/443 open |
| transport actually used | **HLS over 443**, the fallback their guide prescribes |
| HLS streams that opened | **3 of 8 sampled** — 6, 14, 15, 17, 22 timed out |
| codecs | H.264 and H.265 mixed; HEVC will not play in Chrome or Firefox |
| resolutions | 1280x720 · 1280x960 · 1920x1080 · 2560x1440 |
| vehicles detected | yes — 131 detections, tracked across frames |
| **plates read** | **none** |

The cameras are night-time junction overview and red-light-violation units. On
the two that opened and had traffic, vehicles measured 192-315px at their widest,
which puts a plate at roughly 40-60px in heavy glare. The single plate-shaped
result recorded, `MANAEC` at 0.62 confidence, is a false read the grammar
correctly rejected.

So: detection, tracking, events, persistence and the alert path all work on real
video. Plate recognition on these particular cameras does not. Daytime footage,
or a camera pointed down a lane rather than across a junction, is what would
change that — the pipeline is the same either way.

Their guide also warns that "each connected client receives its own copy of the
stream" and to open only what you are processing. Some of the timeouts above may
be self-inflicted by surveying while the worker held sessions open; worth
re-testing one camera at a time before concluding a feed is down.

---

## 🎬 The ANPR demonstration

`scripts/demo_anpr.py` runs the production chain end to end with nothing staged:

```
simulator → RTSP into MediaMTX → ai-worker decodes → YOLO → ByteTrack →
plate detect → OCR → consensus → Redis Stream → API consumer →
watchlist match → alert → /ws/events
```

The script's only privileges are reading the database to report what happened
and putting a plate on the watchlist. It does not publish detections, does not
write alerts, and is never told which plates are in the footage — it reads back
what the live pipeline wrote. If the pipeline cannot read the plate, no alert
appears and the demo fails, which is the point of running it.

```bash
make videos                              # fetches and cuts anpr_demo.mp4, once
docker compose --profile ai run -d --rm \
    -e AI_WORKER_SOURCE=mediamtx -e AI_WORKER_CAMERAS=cam-00001 \
    -e AI_CONFIG=stream_demo --name sentinel-ai-demo ai-worker
docker compose exec -e SENTINEL_API_URL=http://api:8000 api \
    python /app/scripts/demo_anpr.py --plate NA13NRU
```

**Measured on this machine:**

| | |
|---|---|
| plates read from the 15s clip | **13**, of which 12 resolve to a valid format |
| best read | `NA13NRU` at **0.95–1.00** |
| repairs the grammar made | `GX150GJ`→`GX15OGJ`, `EYG1NBG`→`EY61NBG`, `KHD5ZZK`→`KH05ZZK`, `MY5IVSU`→`MY51VSU` |
| alert after arming the watchlist | **33–99 s** — how long until the car came round again, *not* pipeline latency |
| detection → alert | 24 ms, measured separately in the Phase 6 gate |

The annotated video draws the magnified plate crop and its transcription above
each vehicle, so the reading is visible frame by frame rather than only in a
log.

**The demonstration footage carries UK plates.** It is the only clip available
with legible plates; the organisers' cameras cannot resolve one (above). Plate
grammar is therefore region-aware: `configs/demo.yaml` and `stream_demo.yaml`
accept UK formats, and a Gujarat deployment runs `stream`, which is
Indian-only. Grading a correct read as invalid because it is not a Gujarat
plate would be the tool being wrong about right output — but **do not ship a
config that accepts GB.**

### Three bugs this surfaced

All three were invisible to a suite of 267 passing tests, because nothing
exercised the endpoints over HTTP.

| Bug | Effect |
|---|---|
| Handlers annotated the user as `CurrentUser`, the bare model, instead of the `Annotated[…, Depends(…)]` alias | FastAPI expected the user in the request body, so **every mutating watchlist and alert endpoint returned 422**. The entire Phase 6 write surface was unusable |
| `watchlist` and `alerts` mounted at the root, every other router under `/api/v1` | Both **unreachable through the web container's nginx** |
| `detection_id` read before flush, where a Python-side column default assigns it | **Every watchlist alert recorded `detection_id=None`** and lost its link to the evidence that raised it |

`services/api/tests/test_watchlist_api.py` now covers the HTTP surface and
asserts the prefix; both were verified to fail against the reverted bugs.

---

## ⏳ Pending

| Phase | Delivers | Why it matters |
|---|---|---|
| **9 · Operator frontend** ← next | Live ANPR overlay on the grid feeds, alerts triage, watchlist manager, dashboard, video wall | **Judge Moment 3.** The APIs are done; without screens they are performed in a terminal |
| **7 · Correlator** | Cross-camera route reconstruction, plausibility scoring, convoy detection | **Judge Moment 4** — and the organisers' scored live test case (FAQ Q26–28) |
| **8 · Search** | OpenSearch partial/fuzzy plate search, pg_trgm fallback | Partial plate → ranked results in <300ms |
| **9b · Remaining UI** | Vehicle profile with animated route, admin, architecture page | Follows Phase 7 and Phase 10, whose output they display |
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
| **No screen for alerts or the watchlist** | **High** | Both APIs are complete and tested; `web/src/pages/` has only Map, Fleet Health, Integration and Login. The judge moments for alerting happen in a terminal today. **This is the next phase of work** |
| **Accuracy measured on generated plates** | **High** | The *measured* accuracy figure still comes from rendered plates and is optimistic by construction. The pipeline now also runs on real footage with legible plates (12 of 13 resolving to a valid format), but that clip has no ground truth, so those are model-confidence numbers. Real labelled **Gujarat** footage is still the single most valuable thing that could be added |
| **The grid's cameras cannot resolve a plate** | **High** | The grid is reachable and detection, tracking, health and events all work on it. But its cameras are night-time junction overviews — plates 40–60 px in glare — and **zero plates have been read from any of them**. Needs a camera pointed down a lane |
| Demonstration footage carries UK plates | Medium | The only clip available with legible plates. Handled by region-aware grammar; `stream` stays Indian-only. Do not let a GB-accepting config reach a deployment |
| Live reads converge less than offline ones | Medium | Streaming emits `vehicle.observed` incrementally, so an early event can carry a partial read (`FJ4ZHY` before `FJ14ZHY`). The final `vehicle.completed` is correct; consumers acting on the first event see the rougher answer |
| **mypy fails — 17 errors, 8 files** | Medium | `make lint` never ran it. CLAUDE.md §5 claims "Python passes mypy" — **currently false**. Fix or amend the claim |
| Plate detector weights are AGPL-3.0 | Medium | Ultralytics export. Fine for evaluation, must be replaced before production — see ai-lab/README.md |
| One OCR error survives consensus | Medium | `GJ35K5714` → `GJ35X5714`. Both letters in a letter slot, so grammar cannot repair it and every frame agreed. Needs a better recogniser or a fine-tune |
| Vendor adapters never met a real VMS | Medium | Code is real and unit-tested; no Milestone/Genetec server has been on the other end |
| Evidence crops not in MinIO | Medium | The worker records crop keys; upload to object storage is not wired. `Detection.crop_key` expects a MinIO key |
| GPU path never executed | Medium | Provider selection is one function and the CUDA branch is written, but this machine has no CUDA. **No GPU figure is claimed anywhere** |
| ~1 camera per CPU worker | Medium | 4.4 fps at 720p, 2.8 at 4K. Reaching many cameras is a GPU and node-count question this hardware cannot answer |
| No API rate limiting | Low | Required before anything is exposed beyond localhost |
| Health debounce counters in-memory | Low | Monitor restart resets the failure count. Deliberate, but know it |
| Alembic `downgrade()` never run | Low | Written, untested |
| Portrait clips pillarboxed | Cosmetic | `fetch_videos.sh` pads rather than crops |

---

## ▶ Next steps, in order

### 1. Phase 9 (brought forward) — the operator frontend ← **start here**
Everything below already works over the API and has no screen. Until it does,
three of the five judge moments can only be performed in a terminal.

- **Live ANPR overlay on the camera feeds.** Multiple real cameras from the
  organisers' grid, plates and vehicle boxes drawn over the video as the worker
  reads them, driven off `/ws/events` rather than re-running inference in the
  browser
- **Alerts screen.** Priority-sorted, red critical banner with the plate crop,
  camera, time and confidence; acknowledge / dispatch / close / false-positive
  with keyboard shortcuts for triage
- **Watchlist manager.** Add, amend, retire; case reference and validity window;
  the audit trail visible
- **Dashboard.** KPI strip, event ticker, alert feed, mini map
- **Video wall.** 2×2 / 3×3 / 4×4, drag to place

The APIs are done and tested — `/api/v1/watchlist`, `/api/v1/alerts` with the
full lifecycle, `/ws/events` for the live push. This is frontend work against a
finished contract.

*Gate:* a plate typed into the watchlist in the browser, a vehicle passing on a
live feed, and the alert appearing on screen with its evidence — the terminal
demo, performed in the product.

### 2. Phase 7 — the correlator
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

### 3. Phase 8 — search
OpenSearch edge-ngram + fuzzy for partial plates, `pg_trgm` fallback chosen at
runtime so the demo survives a dead container. Every search audited.
*Gate:* partial-plate search under 300 ms over 500k detections.

### 4. Then
Phase 10 load test → Phase 11 docs → Phase 12 hardening. The vehicle profile
with animated route playback belongs with Phase 7's output, and the architecture
page with Phase 10's numbers.

### Getting a real accuracy number
The grid's cameras cannot resolve a plate, so the "measured on generated plates"
debt cannot be cleared with them. What would clear it: **daytime footage from a
camera pointed down a lane**, with plates transcribed by hand as ground truth.
Even 200 labelled frames from one Gujarat camera would turn a confidence number
into a measured one. Ask the organisers whether an ANPR-class feed exists on the
grid that was not in the published catalogue.

### Do before submission (not urgent, but not optional)
- Clear the mypy debt and add it to `make lint`
- Replace the AGPL plate weights
- Wire evidence crops into MinIO — the alerts screen wants the plate crop
- Raise `SIM_STREAM_COUNT` toward 50 and confirm the laptop holds
- `docs/DEMO_SCRIPT.md` — the 8-minute walkthrough with fallbacks

---

## Running it

```bash
make demo        # up + migrate + seed + open browser
make videos      # fetch real traffic footage (one time)
make status      # dependency readiness
make test        # 276 API + 11 frontend tests
make logs S=api  # tail one service

cd ai-lab
make doctor      # AI models and engines
make test        # 172 AI-lab tests
make validate    # end-to-end ANPR against known plates
```

Watch a plate become an alert — the whole chain, nothing staged:

```bash
# One camera carrying the ANPR footage, one worker reading it
docker compose --profile ai run -d --rm \
    -e AI_WORKER_SOURCE=mediamtx -e AI_WORKER_CAMERAS=cam-00001 \
    -e AI_CONFIG=stream_demo --name sentinel-ai-demo ai-worker

docker compose exec -e SENTINEL_API_URL=http://api:8000 api \
    python /app/scripts/demo_anpr.py --plate NA13NRU
```

Or watch the events go past directly:

```bash
wscat -c "ws://localhost:8000/ws/events?token=$TOKEN"
# a watchlist hit arrives as alert.raised within ~25 ms of the detection landing
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
