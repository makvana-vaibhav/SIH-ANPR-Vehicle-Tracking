<div align="center">

# Contrail

**City-wide vehicle intelligence platform**

Multi-camera ANPR trajectory tracking and urban traffic analytics.
Turns the disconnected observations of hundreds of city cameras into
searchable vehicle journeys, traffic intelligence, and real-time alerts.

<sub>SIH26127</sub>

</div>

---

## The idea

Cameras tell us what they see. NagarNetra connects those observations to
understand how vehicles move across the entire city.

A city can run hundreds of ANPR cameras, and normally each one works alone:

```
Camera 1  → GJ03AB1234 at 10:31
Camera 7  → GJ03AB1234 at 10:38
Camera 15 → GJ03AB1234 at 10:47
```

Three rows in three databases. The vehicle travelled a route, took a time, and
covered a distance — and none of that is recorded anywhere. NagarNetra links
them.

So the question this platform answers is not *"which vehicle did this camera
see?"* but:

> *"Where has this vehicle been, where did it travel, how long did it take, what
> is happening on the roads, and is anything unusual happening?"*

**ANPR is one capability inside the platform, not the product.** A basic build is
`Camera → YOLO → OCR → Dashboard`. The value is everything after identification:

```
OBSERVE → IDENTIFY → LINK → UNDERSTAND → PREDICT → ALERT
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                     this is the project
```

---

## Run it

```bash
git clone <this-repo> && cd SIH-ANPR-Vehicle-Tracking
make demo
```

That is the whole setup. `make demo` brings the platform up, seeds it, and opens
the command centre at **http://localhost:8080**.

> **First run** downloads roughly 2 GB of container images and takes a few
> minutes. Every run after that reaches a working demo in well under a minute.
> Requires Docker with **≥ 8 GB** allocated. `make preflight` checks this for you.

| Command | What it does |
|---|---|
| `make up` | Build and start the platform, wait for every container to be healthy |
| `make demo` | `up` + seed data + open the browser |
| `make down` | Stop (data volumes are preserved) |
| `make status` | Show which dependencies are up, degraded, or down |
| `make help` | Everything else |

---

## Architecture

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

### The design decision

City cameras are multi-vendor and already deployed. We **federate** rather than
replace: existing recorders stay authoritative for their own video, we ingest
metadata centrally, pull streams on demand, and run AI **at the edge** — so that

> **the central tier carries events, not video.**

That is also the scaling argument, and it is arithmetic rather than optimism.
For a city of ~1,000 cameras:

| Approach | Central bandwidth |
|---|---|
| Centralise video | 1,000 × 4 Mbps ≈ **4 Gbps** |
| Centralise metadata (this design) | ~35 events/s × ~2 KB ≈ **0.6 Mbps** |

A **~7,000× reduction**, plus on-demand pull for the handful of feeds an
operator is actually watching. It is what lets a city deployment run on
commodity hardware. Full derivation is in [docs/HLD.md](docs/HLD.md).

---

## Scaling

Scale here is three separate problems, solved in three separate places. Every
number below was printed by a run on the development host (macOS arm64, 10 cores
/ 16 GB). Nothing in this section is extrapolated, and where a figure *is* a
projection it says so.

### 1. The event tier scales sideways

The AI workers publish detections to a Redis Stream. Two different consumers read
it, and keeping them separate is what makes replication correct:

| Class | Delivery | Why |
|---|---|---|
| `EventTailer` | **Every replica** sees every event | The live operator picture must be identical on every API process |
| `EventConsumer` | **Exactly one** replica per event (consumer group) | Durable persistence must happen once, not once per replica |

One consumer group cannot do both, and until they were split the platform was
correct only while exactly one API process ran. With them split, adding a
consumer replica adds throughput — with no configuration naming which cameras
belong to which worker, and no coordination between workers.

```bash
make scale    # api stops persisting; 3 consumer replicas share the stream
make load     # k6 against it
```

**Measured** (`docs/BUILD_STATE.md` § Phase 10):

| Metric | Result |
|---|---|
| Sustained ingest | **2,774 events/s** (median) |
| Distinct vehicle identities | 80,000 |
| Failed requests | **0** |
| p95 capture-to-persisted | 5.7 s — **missed its 3 s gate** |

That last row stays in the table. The throughput gate passed and the latency gate
did not; the cause is queue wait rather than processing time. It is reported
rather than tuned away — and the load test found three real bugs on the way.

### 2. The inference tier scales by thread budget

See [Model optimization](#model-optimization). The short version: one process-wide
thread budget divided across concurrent cameras, because the worker runs one
pipeline **per camera** and each pipeline builds five ONNX sessions.

### 3. Fleet sizing is computed, not tabulated

A static sizing table answers one fleet size and drifts out of date. This is a
script instead:

```bash
python3 scripts/capacity_model.py --cameras 100000 --provenance
```

Every figure derives from a declared constant, and every constant carries its
provenance — **MEASURED** ones name the run that produced them, **ASSUMED** ones
name the reasoning. `docs/INFRASTRUCTURE.md` §2 carries an older sizing table
known to be **~4× optimistic** (it treats a 4-thread worker as one core); the
script supersedes it.

### What is *not* built

Honest seams, so nobody is surprised at a demo:

| Item | State |
|---|---|
| **Kafka / Redpanda backend** | `Settings.event_bus_backend` accepts `"kafka"`, and `kafka_bootstrap_servers` / `kafka_topic_detections` are defined — but **only the Redis implementation exists**. Selecting `kafka` today does nothing. The interface seam is real; the driver behind it is not written. |
| **Kubernetes** | **Nothing.** Orchestration is Docker Compose profiles (`base`, `ai`, `scale`). Horizontal scale is proven with compose `replicas`, not with a scheduler. |
| **Multi-node** | Every measurement above is single-host. Cross-node behaviour is unproven. |

---

## What it does

The PS defines eight things a city command centre must do. This is where each one honestly stands —
status is per-item, because the interesting part of a hackathon README is the part that says what is
*not* finished.

| # | Capability | State |
|---|---|---|
| 1 | **Live multi-camera detection** — several feeds, vehicles found automatically | 🟡 pipeline works; the registry currently holds one camera |
| 2 | **ANPR** — plate, confidence, camera, timestamp | ✅ 9 fps, p90 266 ms, 0.70–0.94 confidence |
| 3 | **Multi-camera linking** — the same vehicle on a second camera joined to the first | 🟡 engine built and tested; needs a fleet to link across |
| 4 | **Vehicle journey** — cameras, distance, duration, route on the map | 🟡 route draws; no average speed, no animation yet |
| 5 | **Vehicle search** — a plate in, every sighting and the route out | 🟡 exact and prefix match; fuzzy not yet |
| 6 | **Blacklist alerts** — automatic red alert with camera, time, confidence, map location | 🟡 the alert fires by itself; the plate crop cannot be shown yet |
| 7 | **Trajectory anomalies** — an unusual route flagged *with its reasons* | 🟡 physical-plausibility flags only; no learned-norm detector |
| 8 | **City traffic analytics** — density, average speed, route density, travel time, hotspots | 🔴 not built |

Beyond the eight, the differentiators — **predictive traffic**, **attribute search** (find a white
SUV when the plate is unreadable) and **vehicle re-identification** — are not built.

**[docs/ROADMAP.md](docs/ROADMAP.md) is the plan of record** for all of it; [PROGRESS.md](docs/PROGRESS.md)
is the one-page current state. Nothing in this README describes a screen that does not exist.

### Accuracy

The PS sets a hard target of **>90% plate recognition accuracy under real-world conditions** —
motion blur, night, angle, occlusion, damaged plates.

**We cannot make that claim yet, and we do not.** Accuracy has only been measured on *synthetic*
footage — 62.5–87.5% end to end, with 100% exact-match on the plates the pipeline chooses to attempt
and a character error rate of 0.000. Those numbers are optimistic by construction: the plates are
rendered from a clean font, with no embossing, dirt or real motion blur.

The interesting part is *where* it falls short. When the pipeline commits to a read, it is almost
always right; it simply declines to read roughly a third of plates. **The bottleneck is recall, not
recognition** — which means a better recogniser is the wrong fix.

Measurement lives in [`ai-lab/`](ai-lab/), a harness with a hard-case mining and labelling loop. The
figure we publish will be the figure the harness printed, with its conditions attached.

---

## Design constraints

| Constraint | How it is honoured |
|---|---|
| **One laptop** | `docker compose up`. No cluster, no cloud account, no manual data entry. |
| **Fully offline** | Local models, local database, local map data. No Google Maps, no hosted inference, no paid API anywhere. |
| **CPU must work** | GPU is an accelerator, never a requirement. Device is auto-detected with a clean CPU fallback. |
| **Degrades, never dies** | Lose WebRTC and video falls back to HLS. Lose a dependency and the readiness endpoint names it rather than failing opaquely. *(The documented OpenSearch→trigram search fallback is not built yet — see [docs/ROADMAP.md](docs/ROADMAP.md) P8.)* |
| **Computed, never invented** | Every congestion figure, delay and score is derived from observed data. Where there is not enough data to compute one, the UI says so rather than showing a plausible number. |
| **Explainable** | Intended: an alert carries the factors that raised it, because `Risk: 87%` on its own is a bug. *(Not yet true — `alerts` has no reasons column; P5 adds it.)* |
| **Auditable** | Every plate search, every stream open, and every blacklist change writes an `audit_log` row. This platform tracks the movement of private vehicles; traceability of who looked at what is a feature. |
| **No secrets in git** | `.env.example` is committed, `.env` is not. Secrets have no in-code defaults — the API refuses to start rather than sign tokens with a key that is readable in this repository. |

---

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI (Python 3.11), SQLAlchemy 2.0 async, Alembic, Pydantic v2 |
| Database | PostgreSQL 16 + PostGIS 3.4 + TimescaleDB |
| Search | OpenSearch 2.19 (Apache-2.0), Postgres `pg_trgm` fallback |
| Event bus | Redis 7 Streams (dev) · Redpanda (scale) behind one `EventBus` interface |
| Object store | MinIO (S3 API) |
| Media gateway | MediaMTX — RTSP in, WebRTC/HLS out |
| AI | YOLO → ONNX (vehicle + plate), ByteTrack, ONNX CRNN OCR, ONNX Runtime |
| Frontend | React 18 + Vite + TypeScript + Tailwind + shadcn/ui |
| Map | MapLibre GL, offline GeoJSON basemap — no tile server, no downloads |

Everything is pinned to an exact version, and every image runs natively on both
`linux/amd64` and `linux/arm64`.

---

## Documentation

**Start here, in this order:**

| Document | Contents |
|---|---|
| [PROGRESS.md](docs/PROGRESS.md) | One page: what works, what doesn't, **the next task** |
| [docs/ROADMAP.md](docs/ROADMAP.md) | **The plan of record** — phases P1–P12, each with its gate |
| [CLAUDE.md](CLAUDE.md) | The contract: conventions, the rules, the migration ledger |
| [BUILD_STATE.md](docs/BUILD_STATE.md) | Build history, gate evidence, and the audited gap list |

**Reference:**

| Document | Contents |
|---|---|
| [ai-lab/README.md](ai-lab/README.md) | The accuracy harness — how the >90% claim gets measured |
| [docs/API.md](docs/API.md) | Every endpoint, generated from the live OpenAPI document |
| [docs/SECURITY.md](docs/SECURITY.md) | RBAC, encryption, audit, retention, lawful-use safeguards |
| [docs/HLD.md](docs/HLD.md) | High-level design, scaling, measured performance |
| [docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) | Bandwidth, GPU sizing, storage tiering, DR |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | Minute-by-minute walkthrough, with fallbacks |

> **Note:** this codebase was renamed and re-aimed from an earlier *statewide* CCTV project.
> `docs/HLD.md`, `docs/INFRASTRUCTURE.md`, `docs/BRIEFING.md`, `docs/REQUIREMENTS.md`,
> `docs/STATUS.md`, `docs/submission/*` and the build history in `docs/BUILD_STATE.md` still argue that
> older brief in places, and `docs/INFRASTRUCTURE.md`'s compute sizing is known to be ~4×
> optimistic — use `scripts/capacity_model.py` instead. [CLAUDE.md §10](CLAUDE.md#10-migration-ledger)
> tracks every site; fixing them is P12.

---

## Development

```bash
make up          # start the platform
make test        # backend (pytest) + frontend (vitest)
make lint        # ruff + format check + tsc
make logs S=api  # tail one service
make status      # dependency readiness
make clean       # stop and delete all data volumes
```

---

<div align="center">
<sub>Apache-2.0 · SIH26127 — City-Wide AI Engine for Multi-Camera ANPR Trajectory Tracking and Urban Traffic Analytics</sub>
</div>
