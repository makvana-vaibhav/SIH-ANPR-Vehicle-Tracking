# CLAUDE.md — Sentinel-GJ

Read this before writing any code in this repository. It is the contract between sessions.

---

## 1. What this product is

**Sentinel-GJ is a statewide CCTV intelligence platform** for the Gujarat Police / Home Department
CCTV challenge. It federates CCTV that already exists across departments and vendors.

**ANPR is one capability inside the platform. It is not the product.** If you find yourself building
"a number plate reader", you have lost the plot. You are building the system that lets a state
government see, search, and reason about 80,000 cameras it does not uniformly own.

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

### The five judge moments

Everything in this repo exists to serve these. If a change does not serve one of them, question it.

1. See ~250 cameras on a Gujarat map, filter by department/status, click one and watch video.
2. Feed a video in and watch plates get read and events appear in real time.
3. See a **watchlist hit fire automatically** as a red alert with plate crop, camera, time, confidence.
4. Type `GJ03AB1234` into search → every sighting + a **drawn route** across multiple cameras with timestamps.
5. Open an architecture page explaining the scale to **80,000 cameras**, backed by a load test we actually ran.

---

## 2. Architecture decision

The organisers named five reference models: (1) Registry+GIS, (2) Unified viewing,
(3) Federation middleware, (4) Central VMS, (5) Hybrid.

**We build Model 5 = Model 1 + Model 3 + selective Model 2.**

Rationale, which must appear in `docs/HLD.md` and on the Architecture screen: Gujarat already runs
departmental VMS deployments from multiple vendors. Ripping them out (Model 4) is politically and
financially unrealistic. So we **federate**: existing VMS stay authoritative for their own video, we
ingest metadata centrally, pull streams on demand, and run AI at the edge so that

> **the central tier carries events, not video.**

That sentence is the whole scaling argument. 80,000 × 4 Mbps ≈ 320 Gbps of centralised video versus
~2,700 events/s × ~2 KB ≈ 43 Mbps of metadata — a ~7,000× reduction.

---

## 3. Non-negotiable constraints

| Constraint | Meaning in practice |
|---|---|
| **One laptop, `docker compose up`** | A judge with no setup knowledge gets a working demo in < 5 min. Everything else is secondary. |
| **Zero paid APIs, zero cloud, offline-capable** | This is police infrastructure. No Google Maps, no OpenAI, no hosted inference. Local models, local DB, local map data. |
| **CPU must work** | GPU is an accelerator, never a requirement. Auto-detect, fall back cleanly. |
| **Everything seeded** | Fresh clone → `make demo` → populated DB, cameras on map, video replaying, alerts firing. Zero manual data entry. |
| **No secrets in git** | `.env.example` committed, `.env` gitignored. `credentials_ref` stores a *pointer* to a secret, never a secret. |
| **Pin every version** | Python 3.11, Node 20, every image tag, every dependency. |

---

## 4. Stack (fixed — do not substitute without recording it in §9)

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
will not install, swap it and record the swap in §9 — do not fake around it.

- **Async everywhere.** `async def` for all I/O. SQLAlchemy 2.0 async sessions, `httpx.AsyncClient`,
  `redis.asyncio`. Never block the event loop; CPU-bound work goes to a thread/process pool.
- **No bare `except`.** Catch the specific exception. `except Exception` is permitted only at a
  supervisor/task boundary and must log with `exc_info=True` and re-raise or record the failure.
- **structlog, JSON output.** Every log line carries context (`camera_id`, `track_id`, `event_id`,
  `request_id`). No `print()` outside `scripts/`.
- **Time: UTC internally, IST for display.** Every timestamp stored and transported is timezone-aware
  UTC. Conversion to `Asia/Kolkata` happens at the presentation edge only. Never store naive datetimes.
- **Contracts are generated, not hand-written.** Event shapes live in `packages/contracts/schemas`
  as JSON Schema; Pydantic models and TS types are generated from them. No service hand-rolls a dict.
- **Typed.** Python passes `mypy`; frontend passes `tsc --noEmit`. Public functions get type hints.
- **Config via env, parsed once** into a Pydantic `Settings` object in `app/core/config.py`.
  No `os.getenv` scattered through the codebase.
- **Migrations, never `create_all`.** Schema changes are Alembic revisions.
- **Every DB query that can return many rows is paginated.** No unbounded `SELECT *`.

### Audit rule (enforced, tested)

**Every plate search, every camera stream open, and every watchlist mutation writes an `audit_log`
row.** This is a surveillance system for a police force; traceability of who looked at what is a
feature we can point at, and the right thing to build in. Middleware covers mutating requests;
the three named actions also get explicit calls. There is a test asserting the rows appear.

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
- **Map has no tile server.** MapLibre renders Gujarat district boundaries and the NH-27 / NH-48
  corridors from GeoJSON committed under `data/seed/`. Works on a plane, in a basement, at a venue
  with hostile wifi. `docs/INFRASTRUCTURE.md` documents the MBTiles upgrade for production.

---

## 7. Repository layout

```
services/api/         FastAPI: registry, events, watchlist, alerts, search, auth, GIS
services/ingest/      adapters (RTSP/ONVIF/VendorVMS/Simulated) + stream supervisor + health monitor
services/ai-worker/   the vision pipeline
services/correlator/  cross-camera route reconstruction + watchlist matcher
services/simulator/   synthetic camera fleet + video replay → RTSP + load mode
web/                  React command centre
packages/contracts/   JSON Schema + generated TS types + Python models (single source of truth)
infra/                mediamtx, opensearch, postgres/init, grafana, nginx, prometheus config
data/seed/            cameras.csv, watchlist.csv, gujarat districts + highways GeoJSON
data/models/          model weights (gitignored, fetch script committed)
data/videos/          sample clips (gitignored, fetch script committed)
scripts/              fetch_models.sh, fetch_videos.sh, seed.py, generate_synthetic_plates.py
tests/e2e, tests/load/k6
docs/                 HLD, INFRASTRUCTURE, SECURITY, API, DEMO_SCRIPT, diagrams/
```

---

## 8. Working rules

1. **One phase per commit**, conventional messages: `feat(ai): multi-frame plate consensus`.
2. **A phase is complete when its gate command passes**, not when the code exists. Show real output.
3. **Update `BUILD_STATE.md` after every phase** — what was built, how it was verified, what the next
   phase needs. Write it for a session that remembers nothing.
4. **Tests belong to the phase that creates the code**, never deferred to a later phase.
5. **Never commit** `.env`, model weights, videos, or MinIO data.
6. **If you must guess at a hackathon requirement, guess toward "more demonstrable".**
7. **Show uncertainty rather than hiding it.** Low-confidence route hops are marked, not dropped.
   Judges respect a system that knows what it does not know.

---

## 9. Recorded stack deviations

Anything that differs from the original brief goes here, with the reason.

| # | Brief said | We did | Why |
|---|---|---|---|
| 1 | PaddleOCR **or** ONNX CRNN | ONNX CRNN | `paddlepaddle` ships linux **x86_64-only** wheels; cannot install on arm64. Interface allows swapping back on amd64. |
| 2 | PostgreSQL 16 + PostGIS 3.4 + TimescaleDB | `timescale/timescaledb-ha:pg16.14-ts2.29.2-all` | `postgis/postgis:16-3.4` is amd64-only. This image is arm64 and contains both extensions. Same capability, one container. |
| 3 | Ultralytics YOLO at runtime | Ultralytics at **build** time only; ONNX Runtime at runtime | Cuts the AI image from ~2.5 GB to ~400 MB (protects the 5-minute demo) and keeps AGPL-3.0 code out of the shipped artifact. |
| 4 | Self-hosted MBTiles via tileserver-gl | GeoJSON basemap, no tile server | Avoids a ~500 MB download and a container on the demo path. Chosen deliberately; MBTiles path documented for production. |
