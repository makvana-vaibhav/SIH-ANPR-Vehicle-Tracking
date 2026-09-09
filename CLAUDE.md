# CLAUDE.md — NagarNetra

Read this before writing any code in this repository. It is the contract between sessions.

> ## Start here
>
> **You are working on SIH26127 — a city-wide vehicle intelligence platform.**
>
> | Read | For |
> |---|---|
> | [PROGRESS.md](docs/PROGRESS.md) | one page: what works, what doesn't, **the next task** |
> | [docs/ROADMAP.md](docs/ROADMAP.md) | **the plan of record** — phase definitions and gates (P1–P12) |
> | this file | the rules you must follow |
> | [BUILD_STATE.md](docs/BUILD_STATE.md) | what was already built, the evidence, and the audited gap list |
>
> **The current task is P1** (the Ahmedabad fleet) unless docs/PROGRESS.md says otherwise.
> Do not start a phase whose gate you cannot run.
>
> **Three traps that have each cost real time already — do not rediscover them:**
> 1. Never set a simulator-published camera's `stream_url` to the gateway's own URL. `gateway.py`
>    registers a MediaMTX *pull* path from `stream_url`, so MediaMTX pulls a path from itself, loops
>    forever, and silently blocks publishing.
> 2. Never seed a camera without a real video source. Commit `4d0346f` deleted 251 such cameras
>    precisely because they made every count on every screen meaningless.
> 3. `make ai` is required for any ANPR work — the worker sits behind the `ai` compose profile, so
>    plain `make up` starts no AI at all and the pipeline looks broken when it is merely absent.

> **Lineage.** This codebase began as *Sentinel-GJ*, a **statewide** Gujarat Police CCTV
> federation platform. It was renamed and re-aimed at **SIH26127**, a **city-wide** vehicle
> intelligence problem. Most of the platform carries over; the framing does not.
> Anything still arguing "statewide", "80,000 cameras", "Model 1/3/5", "Gujarat Police /
> Home Department", "SCRB" or "FAQ Q12/Q15/Q26" is **residue from the old brief** — treat it as
> wrong and fix it when you touch that file. See §10 for the migration ledger.

---

## 1. What this product is

**NagarNetra is a city-wide vehicle intelligence platform.** It takes the disconnected
observations of hundreds of city CCTV/ANPR cameras and turns them into **searchable vehicle
journeys, traffic intelligence, and real-time actionable alerts.**

The one-line explanation:

> Cameras tell us what they see. NagarNetra connects those observations to understand how
> vehicles move across the entire city.

**ANPR is one capability inside the platform, not the product.** If you find yourself building
"a number plate reader", you have lost the plot. A basic team ships
`Camera → YOLO → OCR → Dashboard`. The value we add is everything *after* identification.

```
OBSERVE → IDENTIFY → LINK → UNDERSTAND → PREDICT → ALERT
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                     this is the project
```

The system, end to end:

```
MULTIPLE CITY CAMERAS (multi-vendor)
        │
   Integration Layer  ── adapters: RTSP / ONVIF / Vendor VMS API / Simulated
        │
   Stream Gateway     ── RTSP → WebRTC/HLS for browsers
        │
   ┌────┴────┐
 Video     Events
   │          │
AI Pipeline   Event Engine
   │  (vehicle detect → track → plate detect → OCR → consensus → event)
   └────┬─────┘
        │
 Trajectory Engine ── multi-camera linking, journey reconstruction, re-identification
        │
   ┌────┼────────────────┬─────────────────┐
   ▼    ▼                ▼                 ▼
Vehicle  Traffic      Alerts &         Attribute
Search   Analytics    Anomalies        Search
   └────┴────────────────┴─────────────────┘
        │
 GIS + City Intelligence Dashboard
```

### The question that defines the product

Not *"which vehicle did this camera see?"* but:

> *"Where has this vehicle been, where did it travel, how long did it take, what is happening
> on the roads, and is anything unusual happening?"*

### Hard requirements from the PS

`docs/REQUIREMENTS.md` maps every stated requirement to where it is implemented.
The ones that constrain the architecture most:

* **>90% plate recognition accuracy under real-world conditions.** This is a stated numeric
  target, not an aspiration. It is measured in `ai-lab/`, on held-out footage, and the number
  we publish is the number the harness printed. Motion blur, night, angle, occlusion and
  damaged plates are part of "real-world" — accuracy claimed on clean frames is not a claim.
* **Trajectory reconstruction across the city camera network.** Multi-camera linking is the
  core module, not a feature. A sighting that cannot be linked must be visibly unlinked, not
  silently dropped.
* **Real-time alerts** for blacklisted vehicles and route anomalies.
* **City-wide traffic analytics** — density, average speed, route density, travel time.
* **GIS/map-based visualisation** of all of the above.

### The demo moments

Everything in this repo exists to serve these. If a change does not serve one of them,
question it. This is the sequence from the PS's own demo flow (§15 of the brief):

1. **Live multi-camera detection** — 4–6 simulated CCTV feeds, vehicles detected automatically.
2. **ANPR** — a plate resolves with confidence, camera and timestamp: `GJ03AB1234 · 96.4% · CAM-01 · 10:31:04`.
3. **The link** — the same vehicle appears on another camera and the system *connects the two
   sightings by itself*. This is the moment that separates us from an OCR demo.
4. **Vehicle journey** — click the vehicle: 6 cameras, 14.8 km, 32 km/h average, 31 minutes,
   full trajectory drawn and **animated** on the city map.
5. **Search** — type a plate, get every historical sighting and the complete route.
6. **Blacklist alert** — a blacklisted vehicle enters a stream and fires a red alert
   automatically, with plate crop, camera, time, confidence, and its location on the map.
7. **Trajectory anomaly** — a vehicle takes an unusual route; the system flags it **and
   explains why**.
8. **City analytics** — heatmap, congestion, average speed, vehicle count, route density,
   travel time, hotspots.

### Language discipline

Call it a **trajectory anomaly**. Never call a vehicle, plate or driver
"suspicious", "criminal" or "wanted" on the basis of a route deviation. The system reports
movement that differs from the norm; a human decides what it means. This is not
squeamishness — an operator who is shown "SUSPECT" for a lane change stops trusting the tool.

---

## 2. Architecture decision

Cameras are multi-vendor and already deployed. We **federate** rather than replace: existing
recorders stay authoritative for their own video, we ingest metadata centrally, pull streams on
demand, and run AI **at the edge**, so that

> **the central tier carries events, not video.**

That sentence is the scaling argument, and it survives the change of brief intact. For a city
of ~1,000 cameras: 1,000 × 4 Mbps ≈ 4 Gbps of centralised video versus ~35 events/s × ~2 KB
≈ 0.6 Mbps of metadata. The ratio is what matters and it is ~7,000×. It is what lets a city
deployment run on commodity hardware and a metro deployment run at all.

Scale numbers must be labelled. Anything not produced by a load test we actually ran is
**extrapolated**, never *measured*. See §6.

---

## 3. Non-negotiable constraints

| Constraint | Meaning in practice |
|---|---|
| **One laptop, `docker compose up`** | A judge with no setup knowledge gets a working demo in < 5 min. Everything else is secondary. |
| **Zero paid APIs, zero cloud, offline-capable** | No Google Maps, no OpenAI, no hosted inference. Local models, local DB, local map data. |
| **CPU must work** | GPU is an accelerator, never a requirement. Auto-detect, fall back cleanly. |
| **Everything seeded** | Fresh clone → `make demo` → populated DB, cameras on map, video replaying, alerts firing. Zero manual data entry. |
| **The whole flow must survive the demo** | A reliable end-to-end path beats an impressive broken one. This is the single most important rule for the internal hackathon (§9). |
| **No secrets in git** | `.env.example` committed, `.env` gitignored. `credentials_ref` stores a *pointer* to a secret, never a secret. |
| **Pin every version** | Python 3.11, Node 20, every image tag, every dependency. |

---

## 4. Stack (fixed — do not substitute without recording it in §11)

| Layer | Choice |
|---|---|
| API backend | FastAPI (Python 3.11), SQLAlchemy 2.0 async, Alembic, Pydantic v2 |
| DB | PostgreSQL 16 + PostGIS 3.4 + TimescaleDB (`timescale/timescaledb-ha`) |
| Search | OpenSearch 2.19 (Apache-2.0), Postgres `pg_trgm` fallback at runtime |
| Cache / bus (dev) | Redis 7 Streams with consumer groups |
| Bus (scale profile) | Redpanda (Kafka API) behind the same `EventBus` interface |
| Object store | MinIO (S3 API) — plate crops, snapshots, clips |
| Media gateway | MediaMTX — RTSP in → WebRTC (WHEP) / HLS out |
| AI | YOLO (vehicle + plate) exported to ONNX, ByteTrack (pure NumPy), ONNX CRNN OCR |
| Inference runtime | ONNX Runtime, GPU optional |
| Frontend | React 18 + Vite + TypeScript + TailwindCSS + shadcn/ui |
| Map | MapLibre GL JS, **no tile server** — local GeoJSON basemap (see §6) |
| Charts | Recharts |
| Realtime | WebSocket `/ws/events`, SSE fallback |
| Auth | JWT access + refresh, argon2, RBAC |
| Orchestration | Docker Compose profiles: `base`, `ai`, `scale` + Makefile |
| Tests | pytest + httpx, vitest + RTL, k6 |

---

## 5. Coding conventions

**The prime directive: never mock, never stub.**
No `TODO: implement`, no placeholder functions, no hardcoded fake return values, no
"you can add this later". If a phase is declared complete, every line of it runs. If a dependency
will not install, swap it and record the swap in §11 — do not fake around it.

This applies with special force to analytics and prediction. A congestion percentage,
a predicted delay or a risk score must be **computed from observed data**. A plausible-looking
invented number is the worst thing in this repository: it survives the demo and destroys the
claim. If there is not enough data to compute a figure, the UI says so.

- **Async everywhere.** `async def` for all I/O. SQLAlchemy 2.0 async sessions, `httpx.AsyncClient`,
  `redis.asyncio`. Never block the event loop; CPU-bound work goes to a thread/process pool.
- **No bare `except`.** Catch the specific exception. `except Exception` is permitted only at a
  supervisor/task boundary and must log with `exc_info=True` and re-raise or record the failure.
- **structlog, JSON output.** Every log line carries context (`camera_id`, `track_id`, `event_id`,
  `request_id`). No `print()` outside `scripts/`.
- **Time: UTC internally, IST for display.** Every timestamp stored and transported is timezone-aware
  UTC. Conversion to `Asia/Kolkata` happens at the presentation edge only. Never store naive datetimes.
- **Contracts — ⚠️ ASPIRATIONAL, NOT TRUE TODAY (P12).** The intent is that event shapes live in
  `packages/contracts/schemas` as JSON Schema with Pydantic and TS generated from them. In reality
  `packages/contracts/` is a single empty `.gitkeep`, the event is a hand-rolled dict in
  `ai-lab/ailab/stream/events.py`, it is duplicated by hand in `simulator/load_mode.py`, and there is
  no validation on either side of the bus. Do not cite this rule as satisfied. If you change the
  event shape, change **both** hand-rolled copies.
- **Typed.** Frontend passes `tsc --noEmit` (enforced by `make lint`). Public functions get type
  hints. **`mypy` does NOT currently pass** — 17 errors across 8 files, and `make lint` does not run
  it (P12). Do not claim it passes.
- **Config via env, parsed once** into a Pydantic `Settings` object in `app/core/config.py`.
  No `os.getenv` scattered through the codebase.
- **Migrations, never `create_all`.** Schema changes are Alembic revisions.
- **Every DB query that can return many rows is paginated.** No unbounded `SELECT *`.

### Explainability rule (⚠️ not yet enforceable — P5)

**Every alert and every score must carry its reasons.** `Risk: 87%` alone is a bug;
`Risk: HIGH — unusual route, unusual travel time, unexpected camera sequence` is the feature.
Anomaly detection that cannot say *why* does not ship.

**Today the `alerts` table has no reasons/factors column**, so this cannot be honoured and no test
enforces it. The only explanation ever stored is free-text prose in `notes` for near matches. P5
adds a `reasons` JSONB column, which is what makes this rule real. Until then, do not describe it as
enforced.
8
### Audit rule (enforced, tested)

**Every plate search, every camera stream open, and every watchlist/blacklist mutation writes an
`audit_log` row.** This platform tracks the movement of private vehicles; traceability of who
looked at what is both a feature we can point at and the right thing to build in. Middleware
covers mutating requests; the three named actions also get explicit calls. There is a test
asserting the rows appear.

---

## 6. Environment facts that shape the code

Developed on **macOS arm64 (Apple Silicon)**, 10 cores / 16 GB RAM. These are not preferences,
they are constraints discovered by probing:

- **No CUDA.** Apple Silicon does not pass a GPU to Linux containers. The AI pipeline runs CPU-only
  here. GPU code paths exist and are unit-tested, but any GPU number in the docs is labelled
  *extrapolated*, never *measured*.
- **`postgis/postgis` has no arm64 build.** We use `timescale/timescaledb-ha:*-all`, which ships
  PostGIS **and** TimescaleDB in one arm64 image. Migration 0001 asserts both extensions exist.
- **PaddlePaddle publishes no aarch64 Linux wheel.** OCR is ONNX CRNN behind an `OcrEngine`
  interface; a `PaddleOcrEngine` is selectable by env var for amd64 deployments.
- **Runtime images are torch-free.** Ultralytics (AGPL-3.0) + PyTorch is ~2.5 GB and is used *only*
  at model-fetch time in a throwaway tools image to export ONNX. The shipped `ai-worker` carries
  `onnxruntime` + OpenCV only. AGPL code never ships in the deployed artifact.
- **Map: satellite when online, GeoJSON when not.** The map offers two basemaps. *Satellite* uses
  Esri World Imagery (no API key) and is the only external dependency in the product; it is
  optional, probed before use, and falls back automatically. *Offline* renders committed GeoJSON
  from `data/seed/` — no tile server, no external request, identical on a plane or at a venue with
  hostile wifi. **The offline path must keep working**; satellite is an enhancement, never a
  requirement.
- **No MapLibre symbol/text layers.** They require a `glyphs` font endpoint, which would be another
  network dependency. Every map label is HTML.
- **Geography: Ahmedabad city, Gujarat.** The city road/junction network is the working canvas;
  the Gujarat district GeoJSON is retained as regional context and as the offline basemap.
  GJ-series plates stay valid — the PS uses them in every example.

---

## 7. Repository layout

```
services/api/         FastAPI: registry, events, blacklist, alerts, search, auth, GIS,
                      trajectory engine (app/services/correlator.py), analytics
services/api/app/ingest/   adapters (RTSP/ONVIF/VendorVMS/Simulated) + stream supervisor
                      + health monitor — runs from the API image, separate container (§11.5)
services/ai-worker/   the vision pipeline (detect → track → plate → OCR → consensus)
services/simulator/   synthetic camera fleet + video replay → RTSP + load mode
ai-lab/               the vision pipeline AND its evaluation harness. `services/ai-worker/`
                      IMPORTS this as a library (ailab.config, ailab.stream) — it is a thin
                      supervisor around `ailab.stream.StreamRunner`, so every field in every
                      detection event originates here. Older wording called the lab "not wired
                      into the platform"; that was wrong. It is also where accuracy is measured
                      and where the >90% claim is earned.
web/                  React command centre
packages/contracts/   JSON Schema + generated TS types + Python models (single source of truth)
infra/                mediamtx, opensearch, postgres/init, grafana, nginx, prometheus config
data/seed/            cameras.csv, blacklist.csv, city + district GeoJSON
data/models/          model weights (gitignored, fetch script committed)
data/videos/          sample clips (gitignored, fetch script committed)
scripts/              seed.py, generate_cameras.py, fetch_videos.sh, fetch_geodata.sh,
                      capacity_model.py (fleet sizing + cost), demo_up.sh, prune_registry.py
                      (model weights are fetched by ai-lab/scripts/fetch_models.sh)
deploy/               EC2 staging/main deployment (not on the demo path)
tests/e2e, tests/load/k6
docs/                 ROADMAP (the plan), PROGRESS (current state), BUILD_STATE (history
                      + gap list), PANIC (demo triage), HLD, INFRASTRUCTURE, SECURITY,
                      API, DEMO_SCRIPT, submission/
```

---

## 8. Working rules

1. **One phase per commit**, conventional messages: `feat(ai): multi-frame plate consensus`.
2. **A phase is complete when its gate command passes**, not when the code exists. Show real output.
3. **Update `docs/BUILD_STATE.md` after every phase** — what was built, how it was verified, what the next
   phase needs. Write it for a session that remembers nothing.
4. **Tests belong to the phase that creates the code**, never deferred to a later phase.
5. **Never commit** `.env`, model weights, videos, or MinIO data.
6. **If you must guess at a requirement, guess toward "more demonstrable".**
7. **Show uncertainty rather than hiding it.** Low-confidence trajectory hops are marked, not
   dropped. Judges respect a system that knows what it does not know.
8. **Fix old-brief residue when you touch a file.** Do not leave a file half-migrated, and do not
   go on a repo-wide crusade mid-feature either. See §10.

---

## 9. Two milestones, one product

**Phase definitions and gates live in [docs/ROADMAP.md](docs/ROADMAP.md) (P1–P12), not here.**
This section is the *why*; the roadmap is the *what and in what order*.

**Internal hackathon (v1) — make the core pipeline work reliably.**
Multiple feeds → detection → ANPR → tracking → multi-camera linking → trajectory → map →
search → basic alerts. The only thing that matters is that the whole flow actually works
live.

**SIH (v2) — improve the same product, do not restart.**
Better ANPR, vehicle re-identification, historical trajectory analysis, traffic prediction,
anomaly detection, explainable alerts, advanced analytics, scalable architecture.

v1 is the prototype v2 is built on. Nothing in v1 should be throwaway.

### Differentiators (v2), in priority order

1. **Complete vehicle journey** — not "detected at Camera 7" but "travelled A→B→C→D, 46 minutes,
   18.4 km, with a route deviation". Every vehicle gets a journey profile.
2. **Predictive traffic** — not "this road is congested" but "Junction 17: 68% now → 81% in 15
   min → 89% in 30 min", *with the contributing factors shown*. Turns a monitoring dashboard
   into a decision-support system.
3. **Explainable alerts** — see the explainability rule in §5.
4. **Search beyond number plates** — ANPR fails on blur, darkness, angle, occlusion and damaged
   plates. Attribute search (white / SUV / last seen CAM-17 / 10:30–11:00) returns candidates
   from appearance + time + camera adjacency. This is what makes it more than an OCR system.

---

## 10. Migration ledger

Audited against the code on **9 Sep 2026**. The rename is complete: no identifier, container,
database, Redis key, hostname or path says "sentinel". **The re-framing is not.**

### Carries over, works, keep it

Auth · RBAC · audit trail · camera registry · bulk CSV onboarding · GIS map with offline basemap ·
fleet health and gap analysis · ANPR pipeline (detect → track → plate → OCR → consensus) ·
stream gateway (WHEP/HLS, scoped tokens) · watchlist → alert → WebSocket · trajectory engine core ·
`ai-lab` evaluation harness · the 2,774 events/s scale proof · 546 tests.

### Not built — these are the phases

| Area | State | Phase |
|---|---|---|
| Multi-camera fleet | Registry holds **one** camera (`CAM-DEMO`) | P1 |
| Traffic analytics | **Nothing.** No router, no page, no heatmap, no time-bucket queries. `recharts` imported zero times | P4 |
| Route-level average speed | Does not exist; only per-leg `implied_kmph` | P2 |
| Journey animation | No timeline, scrubber or moving marker anywhere | P2 |
| Trajectory anomaly detection | The six hop flags are a **physics filter**, not a detector. `AlertType.ANOMALY` has zero producers | P5 |
| Alert reasons | `alerts` has **no** reasons/factors column | P5 |
| Evidence crops | **No MinIO client exists**; the worker discards crops via `_NullRunDir`; `crop_key` always NULL | P3 |
| Fuzzy search | The `pg_trgm` index exists and **nothing queries it**. OpenSearch runs and indexes nothing | P8 |
| Attribute search | `vehicle_colour` never computed | P9 |
| Predictive traffic | Nothing | P10 |
| Re-identification | Nothing — tracking is motion-only, cross-camera linking is plate equality | P11 |

### Wrong or dead in the current code

| Item | Detail | Phase |
|---|---|---|
| `persist_route` | **Dead code, never called.** `vehicle_tracks` is never written, so there is no journey history to baseline anomalies against | P2 |
| `packages/contracts/` | One empty `.gitkeep`. Event is a hand-rolled dict, duplicated by hand in `simulator/load_mode.py`, no validation either side | P12 |
| Five `detections` columns | `vehicle_colour`, `crop_key`, `frame_key`, `direction`, `speed_kmph` — permanently NULL, no producer anywhere | P3, P9 |
| Six real bugs | Listed in [BUILD_STATE.md](docs/BUILD_STATE.md) — two demo-visible, one silently loses data | P6 |
| Sizing docs | `docs/INFRASTRUCTURE.md` §2 is **~4× optimistic** (treats a 4-thread worker as one core). `scripts/capacity_model.py` supersedes it | P12 |
| `mypy` | 17 errors; `make lint` does not run it | P12 |
| Old-brief prose | `docs/*` (incl. `BUILD_STATE.md` history), `docs/submission/*` still argue statewide / 80,000 cameras / Model 1-3-5 / FAQ Q12-Q15-Q26 / Gujarat Police / SCRB | P12 |
| `HOSTED_GRID` adapter | Federates the old challenge's grid at `sentinel.gujarat.gov.in`, which has no standing in SIH26127. **Removal candidate** | P12 |
| `docs/REQUIREMENTS.md` | Maps the **old** challenge's requirements | P12 |
| `docs/STATUS.md` | Stale — written 25 Aug after Phase 4 | P12 |

### Accuracy — the claim we cannot yet make

The PS demands **>90% under real-world conditions**. There is **no real-world measurement**: only
synthetic footage, at **62.5–87.5% end-to-end** and 100% exact-match on plates attempted.
`ai-lab/FINDINGS.md` says plainly these are optimistic and must not be quoted as system accuracy.
**The bottleneck is recall, not OCR** — when the pipeline commits it is right; it declines to read
3 of 8 plates. See P7.

## 11. Recorded stack deviations

Anything that differs from the original brief goes here, with the reason.

| # | Brief said | We did | Why |
|---|---|---|---|
| 1 | PaddleOCR **or** ONNX CRNN | ONNX CRNN | `paddlepaddle` ships linux **x86_64-only** wheels; cannot install on arm64. Interface allows swapping back on amd64. |
| 2 | PostgreSQL 16 + PostGIS 3.4 + TimescaleDB | `timescale/timescaledb-ha:pg16.14-ts2.29.2-all` | `postgis/postgis:16-3.4` is amd64-only. This image is arm64 and contains both extensions. Same capability, one container. |
| 3 | Ultralytics YOLO at runtime | Ultralytics at **build** time only; ONNX Runtime at runtime | Cuts the AI image from ~2.5 GB to ~400 MB (protects the 5-minute demo) and keeps AGPL-3.0 code out of the shipped artifact. |
| 4 | Self-hosted MBTiles via tileserver-gl | GeoJSON basemap, no tile server | Avoids a ~500 MB download and a container on the demo path. Chosen deliberately; MBTiles path documented for production. |
| 5 | `services/ingest/` as a separate codebase | Ingest runs from the **API image** with a different command | The health monitor shares the ORM models and adapters. One image build instead of two protects the 5-minute demo; it is still a separate container, so probing the fleet never competes with serving operators. |
| 6 | Simulator package named `app` | Named `simulator` | Two services sharing a PYTHONPATH cannot both own the root package `app` — `app.core` resolved to the wrong package. |
| 7 | Command centre UI built in Phase 9 | Map, fleet health and integration screens pulled forward to Phase 3/4 | The GIS map is a mandatory, scored feature, and the work needs to be reviewable in a browser as it lands. |
| 8 | Zero external requests, always | Esri World Imagery as an **optional** satellite basemap | Aerial imagery makes a junction recognisable as itself, which matters for an operations map. It needs no API key, is probed before use, and falls back to the offline GeoJSON basemap automatically. The offline path remains fully functional and is what the "works on a plane" claim rests on. |
| 9 | — | Renamed Sentinel-GJ → NagarNetra; `SENTINEL_SANDBOX` → `HOSTED_GRID` | Re-aimed from the statewide Gujarat CCTV brief to SIH26127 (city-wide). The third-party grid adapter was given a neutral name rather than our product's, because our product is not that vendor. |
