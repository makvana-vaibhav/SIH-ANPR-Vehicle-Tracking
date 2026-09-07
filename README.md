<div align="center">

# NagarNetra

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

## What it does

1. **Live multi-camera detection** — several feeds at once, vehicles detected automatically.
2. **ANPR** — plate, confidence, camera, timestamp: `GJ03AB1234 · 96.4% · CAM-01 · 10:31:04`.
3. **Multi-camera linking** — the same vehicle on a second camera is connected to the first
   automatically. This is the core module.
4. **Vehicle journey** — cameras visited, distance, average speed, duration, and the full
   trajectory drawn and animated on the city map.
5. **Vehicle search** — one plate in, every historical sighting and the complete route out.
6. **Blacklist alerts** — a listed vehicle fires a red alert with plate crop, camera, time,
   confidence and map location.
7. **Trajectory anomalies** — an unusual route is flagged *with the reasons that flagged it*.
8. **City traffic analytics** — density, average speed, route density, travel time, hotspots.

Items 7 and 8, predictive traffic, and attribute search are **in progress** — see the
migration ledger in [CLAUDE.md](CLAUDE.md#10-migration-ledger-sentinel-gj--nagarnetra) for
exactly what is built and what is not. Nothing in this README describes a screen that does
not exist except where it says so here.

### Accuracy

The PS sets a hard target of **>90% plate recognition accuracy under real-world
conditions** — motion blur, night, angle, occlusion, damaged plates. That number is
earned and measured in [`ai-lab/`](ai-lab/), a standalone evaluation harness that is
deliberately *not* wired into the platform, on held-out footage. The figure we publish
is the figure the harness printed.

---

## Design constraints

| Constraint | How it is honoured |
|---|---|
| **One laptop** | `docker compose up`. No cluster, no cloud account, no manual data entry. |
| **Fully offline** | Local models, local database, local map data. No Google Maps, no hosted inference, no paid API anywhere. |
| **CPU must work** | GPU is an accelerator, never a requirement. Device is auto-detected with a clean CPU fallback. |
| **Degrades, never dies** | Lose OpenSearch and plate search falls back to Postgres trigram. Lose WebRTC and video falls back to HLS. The readiness endpoint reports exactly which. |
| **Computed, never invented** | Every congestion figure, delay and score is derived from observed data. Where there is not enough data to compute one, the UI says so rather than showing a plausible number. |
| **Explainable** | An alert carries the factors that raised it. `Risk: 87%` on its own is a bug. |
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

| Document | Contents |
|---|---|
| [CLAUDE.md](CLAUDE.md) | The contract: product definition, conventions, migration ledger, recorded stack deviations |
| [BUILD_STATE.md](BUILD_STATE.md) | Phase-by-phase build status and acceptance criteria |
| [docs/HLD.md](docs/HLD.md) | High-level design, scaling, measured performance |
| [docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) | Bandwidth, GPU sizing, storage tiering, DR |
| [docs/SECURITY.md](docs/SECURITY.md) | RBAC, encryption, audit, retention, lawful-use safeguards |
| [docs/API.md](docs/API.md) | Every endpoint, generated from the live OpenAPI document |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | Minute-by-minute walkthrough, with fallbacks |
| [ai-lab/README.md](ai-lab/README.md) | The accuracy harness — how the >90% claim is measured |

> **Note:** this codebase was renamed and re-aimed from an earlier statewide CCTV
> project. Documents under `docs/` and `BUILD_STATE.md` / `PROGRESS.md` still argue that
> older brief in places. [CLAUDE.md §10](CLAUDE.md#10-migration-ledger-sentinel-gj--nagarnetra)
> tracks what has been migrated and what has not.

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
