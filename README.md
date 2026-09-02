<div align="center">

# Sentinel-GJ

**Statewide CCTV intelligence platform — Gujarat Police / Home Department**

Federates the CCTV that already exists across departments and vendors,
instead of replacing it.

</div>

---

## Run it

```bash
git clone <this-repo> && cd sentinel-gj
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

## What it is

This is **not** an ANPR model. ANPR is one capability inside a platform.

```
GOVERNMENT CCTV (multi-vendor, multi-department)
        │
   Integration Layer  ── adapters: RTSP / ONVIF / Vendor VMS API
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
 Intelligence Layer ── watchlist match, vehicle history, cross-camera correlation
        │
 GIS + Command Centre ── live view, map, alerts, route replay, search, reports
```

### The architecture decision

The organisers named five reference models. **We build Model 5 (Hybrid) =
Registry + GIS (M1) + Federation middleware (M3) + selective unified viewing (M2).**

Gujarat already runs departmental VMS deployments from several vendors. Ripping
them out and centralising everything (Model 4) is neither politically nor
financially realistic. So we federate: **existing VMS stay authoritative for
their own video**, we ingest metadata centrally, pull streams on demand, and run
AI at the edge — so that

> **the central tier carries events, not video.**

That is also the scaling argument, and it is arithmetic rather than optimism:

| Approach | Central bandwidth at 80,000 cameras |
|---|---|
| Centralise video (Model 4) | 80,000 × 4 Mbps ≈ **320 Gbps** |
| Centralise metadata (this design) | ~2,667 events/s × ~2 KB ≈ **43 Mbps** |

A **~7,000× reduction**, plus on-demand pull for the handful of feeds an
operator is actually watching. Full derivation and the measured load-test
numbers are in [docs/HLD.md](docs/HLD.md).

---

## What a judge can do

1. See ~250 cameras on a Gujarat map, filter by department and status, click one and watch live video.
2. Feed a video in and watch plates get read and events appear in real time.
3. Watch a **watchlist hit fire automatically** — red alert with plate crop, camera, time, confidence.
4. Search a plate and get every sighting plus a **route drawn across multiple cameras** with timestamps and implied speeds.
5. Open an architecture page whose 80,000-camera numbers come from a load test we actually ran.

---

## Design constraints

| Constraint | How it is honoured |
|---|---|
| **One laptop** | `docker compose up`. No cluster, no cloud account, no manual data entry. |
| **Fully offline** | Local models, local database, local map data. No Google Maps, no hosted inference, no paid API anywhere. |
| **CPU must work** | GPU is an accelerator, never a requirement. Device is auto-detected with a clean CPU fallback. |
| **Degrades, never dies** | Lose OpenSearch and plate search falls back to Postgres trigram. Lose WebRTC and video falls back to HLS. The readiness endpoint reports exactly which. |
| **Auditable** | Every plate search, every stream open, and every watchlist change writes an `audit_log` row. This is a surveillance system; traceability of who looked at what is a feature. |
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
| [CLAUDE.md](CLAUDE.md) | Architecture, conventions, and recorded stack deviations |
| [BUILD_STATE.md](BUILD_STATE.md) | Phase-by-phase build status and acceptance criteria |
| [docs/HLD.md](docs/HLD.md) | High-level design, scaling to 80,000 cameras, measured performance |
| [docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) | Bandwidth, GPU sizing, storage tiering, DR |
| [docs/SECURITY.md](docs/SECURITY.md) | RBAC, encryption, audit, retention, lawful-use safeguards |
| [docs/API.md](docs/API.md) | Every endpoint, generated from the live OpenAPI document |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | Minute-by-minute walkthrough, with fallbacks |

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

Build progress and what each phase delivered is tracked in
[BUILD_STATE.md](BUILD_STATE.md).

---

<div align="center">
<sub>Apache-2.0 · Built for the Gujarat Police / Home Department CCTV challenge</sub>
</div>
