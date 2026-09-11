# COMPLETE PROJECT KNOWLEDGE — NagarNetra (SIH26127)

**Single source of truth. Reverse-engineered from the code on 11 September 2026.**

> **How to read this.** Every technical claim carries a file path. Where the code
> and the documentation disagree, the code wins and the disagreement is recorded.
> Status markers: ✅ implemented · 🟡 partial · 📄 documented only · 🔴 not built · ❓ unknown.

---

## ⚠️ READ THIS FIRST — corrections to what you believed

Your brief listed several facts that the code does not support. These are the
ones most likely to embarrass you in front of a judge.

| You believed | Reality | Evidence |
|---|---|---|
| ~51 camera feeds, 12 corridors | **12 cameras, 4 corridors** | `SELECT count(*) FROM cameras` → 12; `data/seed/cameras.csv` |
| Fuzzy search "may be implemented" | **🔴 Not implemented.** `pg_trgm` is installed and *nothing queries it* | no `similarity(`/`%` anywhere in `services/api/app` |
| Trajectory anomaly "basic mechanism exists" | **🔴 No anomaly detector.** Six physics flags catch cloned plates/misreads. `AlertType.ANOMALY` has **zero producers** | `correlator.py:431-464`; only producers are watchlist-hit and camera-down |
| City analytics "may be incomplete" | **🟡 Backend built today; no UI page at all** | `routers/analytics.py` exists; `web/src/pages/Analytics.tsx` does not |
| OCR confidence 0.70–0.94, 9 fps, p90 266 ms | Recorded in `docs/PROGRESS.md`, **measured on the old 4K fleet**, not re-measured since | `docs/PROGRESS.md:56` |
| shadcn/ui (per CLAUDE.md §4) | **Not used.** No Radix, no `components/ui/`. Hand-rolled Tailwind | `web/package.json` |
| `packages/contracts/` is the single source of truth | **Empty.** One `.gitkeep`. Event shape is a hand-rolled dict, duplicated | CLAUDE.md §5 admits this |
| >90% plate accuracy | **Never measured on real footage.** Synthetic only: 62.5–87.5% end-to-end | `ai-lab/FINDINGS.md`, CLAUDE.md §10 |

**The single most important correction:** the headline claim "city-wide traffic
analytics" currently has a backend API and **no screen**. Do not demo it yet.

---

# PART 1 — EXECUTIVE SUMMARY

## What problem are we solving?

A city has hundreds of CCTV cameras. Each one sees vehicles pass. But each
camera is an island — it knows what *it* saw, and nothing else. Nobody can ask
"where did that car go?" without a person manually watching hours of footage
from dozens of cameras.

## What our system does

It connects those isolated observations into **journeys**.

```
Camera A sees Car X at 10:00  ─┐
Camera B sees Car X at 10:08  ─┼─→  "Car X travelled A→B→C,
Camera C sees Car X at 10:15  ─┘     14.8 km, 31 minutes"
```

## Who would use it

Traffic police, city traffic management, and investigators who need to trace a
vehicle's movement.

## What makes it different from normal ANPR

A basic ANPR system answers **"what plate is this?"**. That is one step. Our
platform's value is everything *after* identification: linking, journeys,
search, alerts and analytics.

```
OBSERVE → IDENTIFY → LINK → UNDERSTAND → PREDICT → ALERT
                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                     this is the project
```

### One-line

> Cameras tell us what they see. NagarNetra connects those observations to
> understand how vehicles move across the entire city.

### 30-second (say this to a judge)

> Most ANPR systems read a number plate and stop there. That's one camera
> answering one question. We built the layer above it: our engine takes
> sightings from many cameras, works out when two sightings are the same
> vehicle, and reconstructs its journey across the city — with distance,
> duration, and the route drawn on a map. On top of that we run real-time
> blacklist alerts with photographic evidence, and city traffic analytics.
> The architecture detail that matters is that AI runs at the edge, so the
> central system carries *events, not video* — about a 7,000× reduction in
> bandwidth, which is what makes a city-scale deployment affordable.

### 1-minute

Add to the above:

> Concretely: twelve cameras across four Ahmedabad corridors stream video. An
> AI worker pulls each stream, detects vehicles with YOLO, tracks them with
> ByteTrack, finds the number plate, reads it with OCR, and votes across
> multiple frames so one bad frame can't produce a wrong plate. It publishes one
> event per vehicle to Redis. The API turns those events into history, checks
> each plate against a watchlist, and raises alerts that reach the operator's
> browser over a WebSocket in about 24 milliseconds. A correlator then groups
> sightings of the same plate into a journey, applies six physics checks to
> reject impossible legs, and draws it on the map with animated playback.

### 3-minute technical

> The stack is FastAPI with async SQLAlchemy over PostgreSQL 16 with PostGIS
> and TimescaleDB; Redis Streams as the event bus; MinIO for evidence crops;
> MediaMTX as the video gateway; React with MapLibre on the front.
>
> The AI pipeline lives in `ai-lab/` and is imported as a library by the worker,
> so the code we measure is literally the code that runs. It's YOLOv8n exported
> to ONNX for vehicles, a second YOLO for plates, ByteTrack for tracking (pure
> NumPy, motion-only), and RapidOCR/PP-OCRv4 for reading. Multi-frame consensus
> votes per character position weighted by OCR confidence times crop quality,
> because OCR confidence is poorly calibrated — one sharp misread at 0.95 beats
> three correct reads at 0.7 if you just take the maximum.
>
> Runtime is CPU-only and torch-free — Ultralytics is used at build time to
> export ONNX and never ships, which keeps AGPL code out of the artifact and the
> image at ~400 MB instead of 2.5 GB.
>
> Cross-camera linking today is plate-string equality plus a physics filter.
> That is a real limitation and I'll be straight about it: we have no appearance
> re-identification yet, so a vehicle whose plate can't be read is not linked.

---

# PART 2 — PROBLEM STATEMENT MAPPING

| PS Requirement | What it means | Our implementation | Status | Evidence |
|---|---|---|---|---|
| Multi-camera ANPR | Many cameras, plates read automatically | 12 cameras, 4 corridors, AI worker with rotation | ✅ | `data/seed/cameras.csv`, `ai_worker/worker.py` |
| OCR | Image → text | RapidOCR (PP-OCRv4 ONNX), recognition-first | ✅ | `ai-lab/ailab/ocr/rapid.py` |
| >90% accuracy, real-world | Measured on hard footage | **Never measured on real footage.** Synthetic 62.5–87.5% | 🔴 | `ai-lab/FINDINGS.md` |
| Trajectory tracking | Journey across cameras | Correlator builds hops, distance, duration | ✅ | `services/api/app/services/correlator.py` |
| Spatial-temporal validation | Reject impossible movement | 6 flags incl. 150 km/h ceiling, 50 m co-location | ✅ | `correlator.py:431-464` |
| GIS / map | Cameras and routes on a map | MapLibre, offline GeoJSON basemap + optional satellite | ✅ | `web/src/components/CameraMap.tsx`, `RouteMap.tsx` |
| Traffic analytics | Density, speed, hotspots | **6 API endpoints; no UI page** | 🟡 | `routers/analytics.py` |
| Blacklist alerts | Auto-alert on watchlist hit | Exact + one-edit near match, WebSocket push, plate crop | ✅ | `services/watchlist.py`, `alert_fanout.py` |
| Suspicious / unusual routes | Detect abnormal journeys | **Physics filter only, not an anomaly detector** | 🔴 | `AlertType.ANOMALY` has no producer |
| Scalable architecture | Handles growth | Sharded workers, Redis Streams, load-tested 2,774 events/s | 🟡 | `docs/BUILD_STATE.md:1004` |
| Query / search interface | Find a vehicle | Exact + prefix plate search | 🟡 | `routers/detections.py:60-68` |

### Completed · Partial · Remaining

**Completed:** multi-camera ANPR, OCR, tracking, cross-camera linking,
trajectory + animated playback, blacklist alerts with evidence crops, GIS,
auth/RBAC/audit, camera health.

**Partial:** analytics (API only), search (no fuzzy), scalability (proven on the
event tier, not the AI tier).

**Remaining:** real accuracy measurement, anomaly detection, explainable alerts,
attribute search, re-identification, predictive traffic.

---

# PART 3 — SYSTEM ARCHITECTURE

```
   12 SIMULATED CCTV CAMERAS  (ffmpeg replaying real traffic clips)
                │  RTSP publish
                ▼
        ┌───────────────┐
        │   MediaMTX    │  video gateway — RTSP in, WebRTC/HLS out
        └───────┬───────┘
       RTSP pull │            │ WHEP / HLS
                ▼             └──────────────┐
        ┌───────────────┐                    │
        │   AI WORKER   │  ai-lab pipeline   │
        │  detect→track │                    │
        │  →plate→OCR   │                    │
        │  →consensus   │                    │
        └───────┬───────┘                    │
                │ 1 event/vehicle            │
                ▼                            │
        ┌───────────────┐                    │
        │ REDIS STREAMS │  the event bus     │
        └───────┬───────┘                    │
                │ consumer group             │
                ▼                            │
        ┌───────────────────────────┐        │
        │        FastAPI API        │        │
        │  event_consumer → persist │        │
        │  watchlist match → alert  │        │
        │  correlator → journeys    │        │
        │  analytics → aggregates   │        │
        └──┬─────────┬──────────┬───┘        │
           │         │          │            │
           ▼         ▼          ▼            │
     PostgreSQL   MinIO    WebSocket         │
     +PostGIS    (crops)   /ws/events        │
     +Timescale                 │            │
           │                    │            │
           └────────┬───────────┴────────────┘
                    ▼
            REACT COMMAND CENTRE
     map · live ANPR · alerts · search · health
```

## Every box explained

### MediaMTX — video gateway
- **What:** a media server that accepts RTSP and re-serves it as WebRTC (WHEP) or HLS.
- **Why:** browsers cannot play RTSP. Something must translate.
- **In:** RTSP from the simulator. **Out:** WHEP/HLS to browsers, RTSP to the AI worker.
- **Where:** `infra/mediamtx/`, service `mediamtx` in `docker-compose.yml:178`.

### AI worker — the vision pipeline
- **What:** pulls camera streams and turns pixels into structured events.
- **Why:** this is the "AI" of the project.
- **In:** RTSP. **Out:** JSON events to Redis.
- **Where:** `services/ai-worker/ai_worker/worker.py`, pipeline in `ai-lab/ailab/`.
- **Key classes:** `AiWorker`, `PipelinePool`, `StreamRunner`, `Pipeline`.

### Redis Streams — the event bus
- **What:** a durable, ordered log of events with consumer groups.
- **Why:** decouples AI from API. If the API restarts, events queue rather than vanish.
- **Where:** `services/api/app/services/event_bus.py`, `event_consumer.py`.

### FastAPI API — the brain
- **What:** persists events, matches watchlists, builds journeys, serves everything.
- **Where:** `services/api/app/`.

### PostgreSQL + PostGIS + TimescaleDB
- **PostGIS** = geography types and distance maths on a sphere.
- **TimescaleDB** = time-series superpowers; `detections` is a hypertable.
- **Where:** `services/api/alembic/versions/0001_initial_schema.py:383`.

### MinIO — evidence store
- **What:** S3-compatible object storage for plate crop JPEGs.
- **Why:** putting images on the event bus would multiply traffic ~10×.
- **Where:** `services/ai-worker/ai_worker/crops.py`, `services/api/app/services/evidence.py`.

### React command centre
- **Where:** `web/src/`. Routes in `web/src/App.tsx`.

---

# PART 4 — TECHNOLOGY STACK

| Technology | Category | Where used | Location | Why | Status |
|---|---|---|---|---|---|
| Python 3.11 | Language | API, worker, simulator | all `services/` | async ecosystem, CV libraries | ✅ |
| FastAPI 0.115.6 | Web framework | REST + WebSocket | `services/api/app/main.py` | async, auto OpenAPI, Pydantic | ✅ |
| SQLAlchemy 2.0.36 (async) | ORM | all DB access | `app/models/`, `app/services/` | async sessions, typed | ✅ |
| Alembic 1.14.0 | Migrations | schema changes | `services/api/alembic/versions/` | never `create_all` | ✅ |
| Pydantic 2.10.4 | Validation | request/response schemas | `app/schemas/` | typed contracts | ✅ |
| PostgreSQL 16 | Database | everything | `timescale/timescaledb-ha:pg16.14` | relational + extensions | ✅ |
| PostGIS 3.4 | Geospatial | camera location, distance | `Geography(POINT/LINESTRING)` | metre-accurate distance | ✅ |
| TimescaleDB | Time-series | `detections`, `camera_health` | migration 0001 | `time_bucket`, chunking | ✅ |
| `pg_trgm` | Fuzzy index | **installed, unused** | `db/session.py:93` | intended for P8 | 🔴 |
| Redis 7.4 | Event bus + cache | worker→API | `app/services/event_bus.py` | Streams + consumer groups | ✅ |
| MinIO | Object storage | plate crops | `ai_worker/crops.py` | S3 API, local | ✅ |
| OpenSearch 2.19 | Search | **health-probed only** | `routers/health.py:105` | indexes nothing | 🔴 |
| MediaMTX 1.20.1 | Media gateway | RTSP→WHEP/HLS | `infra/mediamtx/` | browser playback | ✅ |
| ONNX Runtime 1.29 | Inference | all models | `ailab/detect/onnx_backend.py` | CPU, torch-free | ✅ |
| YOLOv8n (ONNX) | Vehicle detection | frames→boxes | `ailab/detect/yolo_onnx.py` | fast, small | ✅ |
| YOLO11n plate (ONNX) | Plate detection | vehicle→plate box | `ailab/plate/yolo_onnx.py` | purpose-trained | ✅ |
| ByteTrack | Tracking | boxes→track IDs | `ailab/track/bytetrack.py` | pure NumPy, no deps | ✅ |
| RapidOCR 1.4.4 (PP-OCRv4) | OCR | plate crop→text | `ailab/ocr/rapid.py` | ONNX, offline, 2-row plates | ✅ |
| ONNX CRNN | OCR (alternative) | selectable | `ailab/ocr/crnn_onnx.py` | interface allows swap | 🟡 |
| OpenCV 4.11 headless | Image ops | decode, crop, resize | worker | standard CV | ✅ |
| React 18.3 | Frontend | UI | `web/src/` | — | ✅ |
| TypeScript 5.7 | Frontend types | all `.tsx` | `web/` | `tsc --noEmit` enforced | ✅ |
| Vite 6.0.7 | Build tool | dev + build | `web/vite.config.ts` | fast HMR | ✅ |
| TailwindCSS 3.4 | Styling | all components | `web/src/styles/` | — | ✅ |
| shadcn/ui | UI kit | **NOT USED** | — | CLAUDE.md §4 claims it | 🔴 |
| MapLibre GL 4.7.1 | Map | GIS | `components/CameraMap.tsx` | no API key, offline | ✅ |
| Recharts 2.15.0 | Charts | **imported zero times** | — | installed for P4 UI | 🔴 |
| hls.js 1.7.1 | Video | HLS fallback | `components/StreamPlayer.tsx` | non-Safari HLS | ✅ |
| react-router-dom 6.28 | Routing | pages | `web/src/App.tsx` | — | ✅ |
| WebSocket | Realtime | live events | `routers/events.py`, `hooks/useEventStream.tsx` | push alerts | ✅ |
| SSE | Realtime fallback | dependency present | `sse-starlette` | ❓ wiring unverified | ❓ |
| JWT (PyJWT) + argon2 | Auth | login, RBAC | `app/core/security.py` | stateless + strong hash | ✅ |
| structlog | Logging | everywhere | `app/core/logging.py` | JSON, contextual | ✅ |
| Prometheus client | Metrics | `/metrics` | `routers/health.py` | — | ✅ |
| Docker Compose | Orchestration | all services | `docker-compose.yml` | one-laptop demo | ✅ |
| pytest + httpx | Backend tests | 410 API tests | `services/api/tests/` | — | ✅ |
| vitest + RTL | Frontend tests | 40 tests | `web/src/**/__tests__/` | — | ✅ |
| k6 | Load tests | scale proof | `tests/load/k6` | 2,774 events/s | ✅ |
| mypy | Type checking | **does not pass** (17 errors) | — | not in `make lint` | 🔴 |

**State management:** none. No Redux, no Zustand — React hooks + a
`useEventStream` context. Verified in `web/package.json`.

---

# PART 5 — REPOSITORY MAP

```
SIH-ANPR-Vehicle-Tracking/
├── ai-lab/              THE VISION PIPELINE + its measurement harness
├── services/
│   ├── api/             FastAPI: everything the browser talks to
│   ├── ai-worker/       supervisor that runs ai-lab against live cameras
│   └── simulator/       fake camera fleet — replays clips over RTSP
├── web/                 React command centre
├── data/seed/           cameras.csv, watchlist.csv, GeoJSON basemaps
├── infra/               mediamtx, opensearch, postgres init config
├── scripts/             seed, generate cameras, capacity model, prune
├── docs/                all documentation
├── tests/e2e            end-to-end judge-flow tests
└── docker-compose.yml   9 services
```

## `ai-lab/` — the pipeline AND the lab

**Responsible for:** every decision about what is detected and what a plate says.
The AI worker **imports this as a library**, so the code measured here is
literally the code that runs in production. (CLAUDE.md once claimed the lab was
"not wired in" — that was wrong and is now corrected.)

```
File:    ai-lab/ailab/pipeline.py
Purpose: Builds and owns the five models; runs a frame through the full chain.
Called by: StreamRunner (live) and the batch CLI (lab).
Calls:   detector → tracker → plate detector → OCR → consensus.
Input:   a video frame. Output: tracked vehicles with plate readings.
Key:     class Pipeline, Pipeline.reset_run_state()
Matters: it is the single place the whole AI chain is assembled.
```

```
File:    ai-lab/ailab/stream/runner.py
Purpose: Continuous inference over a live stream (as opposed to a file).
Key:     class StreamRunner, StreamRunner.run()
Differs: emits events as they happen, bounds memory by retiring tracks, and
         measures how many frames it dropped.
Matters: a worker that silently drops 90% of frames while reporting healthy
         fps is reporting a number nobody asked for.
```

```
File:    ai-lab/ailab/aggregate/consensus.py
Purpose: Turn 10–60 noisy plate reads of one vehicle into one answer.
Key:     consensus(reads, config)
Matters: THE most important accuracy component. See Part 11.
```

```
File:    ai-lab/ailab/runtime.py          (added 10 Sep)
Purpose: One CPU thread budget, divided across concurrent cameras.
Matters: without it the worker took 866% CPU / 168 threads on a 10-core box
         and starved the browser. See Part 30.
```

## `services/ai-worker/`

```
File:    ai_worker/worker.py
Purpose: Supervise one inference loop per camera; restart, rotate, shard.
Key:     class AiWorker, AiWorker._process()
Matters: rotation is how 12 cameras are covered by 4 inference slots.
```

```
File:    ai_worker/pipeline_pool.py       (added 10 Sep)
Purpose: Lend loaded models to whichever camera holds a slot.
Matters: rebuilding a Pipeline per rotation leaked a native thread pool each
         time — 29 threads/1.4 GB became 62/3.5 GB in 12 minutes.
```

```
File:    ai_worker/crops.py
Purpose: Upload plate crop JPEGs to MinIO from the edge; return only the key.
Matters: keeps "the central tier carries events, not video" true.
```

## `services/api/`

```
File:    app/services/correlator.py
Purpose: THE trajectory engine. Groups sightings into journeys, validates them.
Key:     build_route(), Route, Hop, persist_route()
Matters: this is the core differentiator of the whole product.
```

```
File:    app/services/watchlist.py
Purpose: In-memory index of watched plates; exact + one-edit near match.
Key:     WatchlistIndex.match(), Match
```

```
File:    app/services/event_consumer.py
Purpose: Read Redis Stream via consumer group, persist detections, raise alerts.
Key:     _handle_batch()
```

```
File:    app/routers/analytics.py          (added 11 Sep)
Purpose: Six aggregate endpoints over detections + journeys.
```

## `web/src/`

| File | Purpose |
|---|---|
| `App.tsx` | Route table, auth gate |
| `lib/api.ts` | Typed fetch client, token refresh |
| `lib/types.ts` | All shared API types |
| `lib/journey.ts` | Pure playback maths (tested: 16 tests) |
| `hooks/useEventStream.tsx` | ONE WebSocket per tab, shared |
| `components/CameraMap.tsx` | MapLibre map |
| `components/RouteMap.tsx` | Journey + animated playback |
| `components/StreamPlayer.tsx` | WHEP with HLS fallback |
| `components/AnprOverlay.tsx` | Live bounding boxes over video |

---

# PART 6 — COMPLETE DATA FLOW

Tracing one vehicle, plate `GJ01AB1234`:

```
 1. SIMULATOR         ffmpeg replays a clip → RTSP publish to MediaMTX
                      services/simulator/simulator/publisher.py
 2. MEDIAMTX          holds path  cam-demo-01
 3. AI WORKER         opens rtsp://mediamtx:8554/cam-demo-01
                      ai_worker/worker.py:_process()
 4. FRAME             OpenCV decodes; StreamReader drops frames to stay live
 5. VEHICLE DETECT    YOLOv8n @640 → box (x1,y1,x2,y2), class "car", conf 0.87
 6. TRACK             ByteTrack assigns track_id=7; same car next frame = 7
 7. PLATE SCHEDULE    should we look for a plate this frame? (cost control)
 8. PLATE DETECT      YOLO11n @320 on the vehicle crop → plate box
 9. CROP GATE         is this crop new evidence, or same as one already read?
10. OCR               RapidOCR → "GJ01AB1234", confidence 0.91
11. REPEAT            steps 5-10 across many frames → many reads
12. CONSENSUS         per-character weighted vote → final plate + evidence
13. CROP UPLOAD       JPEG → MinIO; only the object KEY is kept
14. EVENT             vehicle.completed JSON → Redis Stream
                      ai-lab/ailab/stream/events.py
15. EVENT CONSUMER    API reads via consumer group
                      app/services/event_consumer.py
16. PERSIST           INSERT INTO detections (hypertable)
17. WATCHLIST         WatchlistIndex.match("GJ01AB1234") → hit?
18. ALERT             INSERT INTO alerts; publish to alert channel
19. WEBSOCKET         EventTailer → /ws/events → browser (~24 ms)
20. CORRELATOR        later: build_route("GJ01AB1234") groups sightings
21. JOURNEY           hops, distance, duration, 6 plausibility flags
22. SNAPSHOT          route_history.snapshot() → vehicle_tracks
23. FRONTEND          VehicleSearch → RouteMap → animated playback
```

---

# PART 7 — VIDEO INGESTION ✅

### Concepts first

- **A frame** is one still picture. Video is many per second.
- **FPS** = frames per second. Our streams are 15 fps.
- **RTSP** = Real Time Streaming Protocol, the standard CCTV cameras speak.
- **Why not process every frame?** At 15 fps a car is in view for ~30 frames.
  Running full detection+OCR on all 30 is wasted work; the car doesn't change
  much between consecutive frames. See the plate scheduler, Part 10.

### Where video comes from

**There are no physical cameras.** `services/simulator/` replays real traffic
clips from `data/videos/` and publishes them as RTSP. This is honest simulation:
the *footage* is replayed, the *detections* are real AI output on real video.

- Clips are **prepared once** into a keyframe-dense, 1080p15-capped copy
  (`publisher.py:prepare_clip`). Two source clips are 4K — decoding those cost
  the worker heavily and the browser more, for pixels the detector throws away
  when it letterboxes to 640px.
- Clips are dealt **per corridor**, so all cameras on one road replay the same
  clip and a vehicle is genuinely seen by successive cameras (`main.py:build_specs`).
- Cameras on the same clip get **staggered start offsets** so they aren't
  showing the same instant.

### How multiple cameras are handled

`AI_WORKER_MAX_CAMERAS` (default **4**) inference slots. Cameras beyond that are
**rotated** through the slots every `AI_WORKER_SLICE_SECONDS` (45). `CAM-DEMO-01`
is pinned and never yields. Sharding across workers is by stable hash of camera
code, so `--scale ai-worker=N` rebalances with no coordinator.

### Failure handling

`StreamUnavailable` distinguishes "the far end is down" (expected, one log line)
from "our loop broke" (traceback). Reasons are mapped to owners:
`unauthorized` → find a password; `timeout` → check the network.

### Camera status

A separate `ingest` container probes every camera on a ~15 s sweep and writes
`camera_health` rows. Health is "did a probe succeed recently", not "is the
process alive".

---

# PART 8 — VEHICLE DETECTION ✅

### What is object detection?

Given a picture, output *what* is in it and *where*.

```
Frame 1920×1080
┌──────────────────────────────────┐
│                                  │
│     ┌────────────┐               │
│     │    CAR     │ conf 0.87     │   box = (x1,y1,x2,y2)
│     └────────────┘               │
│                    ┌──────┐      │
│                    │ BUS  │ 0.79 │
│                    └──────┘      │
└──────────────────────────────────┘
```

### Our model

| Property | Value |
|---|---|
| Model | YOLOv8n exported to ONNX |
| Input size | **640×640, fixed** |
| Classes used | car, motorcycle, bus, truck, bicycle, person |
| Plate-bearing | car, motorcycle, bus, truck |
| Confidence threshold | 0.30 |
| NMS IoU | 0.45 |
| Runtime | ONNX Runtime, **CPU** |
| Loaded at | `ailab/detect/onnx_backend.py:OnnxYoloModel.__init__` |

**"nano" (n)** is the smallest YOLOv8 — chosen because it must run on a laptop
CPU alongside everything else.

### Important detail

The export has a **fixed 640px input**. Setting `detector.imgsz=1280` in config
is silently ignored — the loader logs a warning about it, because a configured
value being ignored is the most expensive kind of silence. Raising resolution
requires re-exporting the model. This is the known fix for the recall problem
(Part 36).

---

# PART 9 — VEHICLE TRACKING ✅

### The three concepts

| Term | Question it answers | Do we have it? |
|---|---|---|
| **Detection** | "I see a vehicle" | ✅ YOLO |
| **Tracking** | "This is the *same* vehicle as the previous frame" | ✅ ByteTrack |
| **Re-identification** | "This may be the same vehicle on a *different camera*" | 🔴 **Not built** |

That third row is the single most important limitation to understand. We link
across cameras by **plate string equality**, not by appearance.

### ByteTrack — how it works

`ai-lab/ailab/track/bytetrack.py`, pure NumPy, motion-only (no appearance model).

```
Frame N      boxes: [A, B, C]
Frame N+1    boxes: [A', B', D]

1. Predict where each existing track should be  (Kalman filter, kalman.py)
2. Match high-confidence boxes to predictions   (IoU overlap)
3. THEN match the LOW-confidence boxes too      ← the "Byte" idea
4. Unmatched box → new track (D gets a new ID)
5. Unmatched track → kept alive `track_buffer` frames (30), then dropped
```

Step 3 is ByteTrack's contribution: a partly-occluded car drops to low
confidence, and most trackers throw those away and lose the track.

### Configuration

| Parameter | Value | Meaning |
|---|---|---|
| `track_high_thresh` | 0.50 | first-pass match threshold |
| `track_low_thresh` | 0.10 | second-pass (the Byte trick) |
| `new_track_thresh` | 0.55 | confidence needed to start a track |
| `match_thresh` | 0.80 | IoU needed to associate |
| `track_buffer` | 30 frames | how long a lost track survives |

### Track merging

`ailab/track/merge.py` — the tracker fragments (one car becomes 3 tracks when it
passes behind a pole). Fragments sharing a plate within 3 seconds are merged into
one **Vehicle**. Raw tracks are kept as diagnostic evidence, because "the tracker
fragmented here" is a finding, not noise.

### Limitations

- Motion-only: two similar cars crossing can swap IDs.
- Fragmentation is the measured cause of precision 0.70 in the lab.
- Track IDs are **per camera**. Camera A's track 7 and camera B's track 7 are unrelated.

---

# PART 10 — NUMBER PLATE DETECTION ✅

```
Vehicle box → crop → plate detector → plate box → crop again → OCR
```

| Property | Value |
|---|---|
| Model | YOLO11n plate, ONNX |
| Input size | **320px** (not 640) |
| Confidence | 0.25 |
| Location | `ailab/plate/yolo_onnx.py` |

**Why 320 not 640?** Measured to cost a quarter as much per search with no loss
of plate recall on the tested footage. The plate detector runs on an
already-cropped vehicle, so it doesn't need full-frame resolution.

### The plate scheduler — the cost control that matters

`ai-lab/ailab/plate/scheduler.py`. Without it, plate search runs on every vehicle
on every frame. Measured on 4K bus-stand footage: 20.2 plate-bearing vehicles
*per frame* → 20 plate searches and up to 40 OCR calls per frame.

Rules (from `stream.yaml` / `default.yaml`):

| Rule | Value | Why |
|---|---|---|
| `min_interval` | 4 frames | consecutive frames can't show a different plate |
| `max_interval` | 16 frames | look anyway, for stationary vehicles |
| `min_vehicle_width` | 100 px | a plate is ~1/5 of vehicle width; below this it's illegible |
| `miss_backoff` | on, ×4 | back off vehicles that never yield a plate (facing away) |
| `min_attempts_before_cooldown` | 4 | short tracks get a fair chance first |

### The crop gate

`CropGate` decides whether a *found* crop is worth *reading*. A vehicle can move
enough to justify a new search yet produce a crop nearly identical to one read
four frames ago. Gate signals: sharpness gain ≥25%, width gain ≥20%, perceptual
hash distance ≥0.15. If the best read so far is weak (<0.75), keep reading
regardless — a poor result is exactly the case more evidence might fix.

---

# PART 11 — OCR / ANPR ✅

### What is OCR?

Optical Character Recognition converts an **image of text** into **text**.

```
┌──────────────┐
│  GJ01AB1234  │   →  OCR  →  "GJ01AB1234"
└──────────────┘
```

### Our engine

**RapidOCR 1.4.4** — PP-OCRv4 models shipped as ONNX inside the wheel.

Chosen for three project-specific reasons (`ailab/ocr/rapid.py` docstring):
1. **No PyTorch** — keeps the runtime image torch-free.
2. **No download at first use** — EasyOCR fetches weights from the internet;
   we must work offline at a venue.
3. **Handles two-row plates** — detection runs before recognition, so each row
   comes back as its own line.

### The clever part: recognition FIRST

PP-OCR normally does *detect text regions → recognise each*. We do the opposite.

> Measured on this host: the **detection stage costs 1334 ms against 74 ms for
> recognition alone — 18×** — and returns the identical string.

The plate detector has *already* localised the plate, so asking a text detector
to find it again is pure waste. Detection is kept only as a **fallback**, gated
by `_detection_could_help()`: a wide crop holds one row of text, so if
recognition alone failed, locating that same row won't rescue it. A squarer crop
may be a two-row plate, where detection genuinely helps.

### Multi-frame consensus ✅ — the most important accuracy component

`ai-lab/ailab/aggregate/consensus.py`.

**The problem:** taking the single highest-confidence read is worse than voting,
because OCR confidence is poorly calibrated — *a sharp crop read wrongly at 0.95
beats three correct reads at 0.7 every time.*

**The solution:** vote **per character position**, weighting each read by
`ocr_confidence × crop_quality`.

```
  GJ03AB1234  0.92        position 8:  '3' seen 3× at high weight
  GJ03AB1234  0.95                     '8' seen 1× at low weight
  GJ03AB1284  0.61        → consensus '3', position 8 flagged least certain
  GJ03AB1234  0.94
              ───────
  consensus:  GJ03AB1234
```

Nothing is discarded silently. Low-weight reads still appear in the output; they
merely don't vote. The disagreement **is** the evidence that the plate was
difficult, and that crop is what a future fine-tune should train on.

### Early stopping

Reading stops once the plate has clearly converged: `stop_min_agreeing: 4`
frames agreeing at `stop_min_confidence: 0.85`. OCR dominates cost, and the
twentieth identical read changes nothing.

### Grammar validation

`ailab/aggregate/grammar.py` — Indian plate format validation. Config carries
`plate_regions` (e.g. `IN`, `GB`), and `allowlist` restricts characters to
`A-Z0-9`. **Note:** the demo cameras accept `GB` grammar because the demo
footage carries UK plates. CLAUDE.md warns never to ship GB grammar in a real
Gujarat deployment.

---

# PART 12 — CONFIDENCE SYSTEM ✅

Four distinct numbers, often confused:

| Confidence | Range | Means | Where |
|---|---|---|---|
| `detection_confidence` | 0–1 | "I'm 87% sure this box is a car" | YOLO vehicle |
| plate box confidence | 0–1 | "I'm 65% sure this is a number plate" | YOLO plate |
| OCR confidence | 0–1 | "I'm 91% sure these characters are right" | RapidOCR, per read |
| `plate_confidence` (final) | 0–1 | consensus confidence after voting | `consensus()` |

Plus **`agreement`** — how many reads agreed — and **`reads_total`**. Those two
matter more than the confidence number alone: 0.85 from 12 agreeing reads is a
far stronger claim than 0.85 from one.

**How it's used:**
- below `min_confidence: 0.20` → discarded as noise
- `ambiguous` flag when the top two candidates are within `ambiguity_margin: 0.15`
- watchlist near-matches are **priority-capped** regardless of the entry's priority
- the UI shows candidates and `corrected_from` so an operator can judge a
  reading rather than being asked to trust it

---

# PART 13 — VEHICLE EVENT MODEL ✅

Two event types on the bus (`ai-lab/ailab/stream/events.py`):

| Event | When | Trust for |
|---|---|---|
| `vehicle.observed` | vehicle still in view, provisional | live overlay, early alerting |
| `vehicle.completed` | vehicle left, consensus settled | history — this is the one persisted |

> A system that only emitted the final event could not raise a real-time alert,
> because the alert would arrive after the car had gone.

### Actual event shape

```json
{
  "event": "vehicle.completed",
  "event_time": "2026-09-11T10:31:04Z",
  "source": { "camera_id": "CAM-DEMO-01", "name": "..." },
  "vehicle": {
    "vehicle_id": 12, "track_ids": [7, 9], "type": "car",
    "confidence": 0.87, "bbox": {...},
    "first_seen_s": 12.4, "last_seen_s": 15.1
  },
  "plate": {
    "text": "GJ01AB1234", "confidence": 0.91,
    "readable": true, "grammar_valid": true, "ambiguous": false,
    "corrected_from": null, "format": "IN",
    "evidence": { "reads_total": 14, "agreement": 11, "method": "positional_vote" }
  },
  "evidence": { "plate_crop": "crops/plates/2026/09/11/track_7_GJ01AB1234.jpg" },
  "frame": { "width": 1920, "height": 1080 }
}
```

⚠️ **This shape is hand-rolled and duplicated by hand** in
`services/simulator/simulator/load_mode.py`. `packages/contracts/` is empty.
There is **no validation on either side of the bus**.

---

# PART 14 — DATABASE ✅

### What is a database?

Organised, queryable, durable storage. We need one because a detection must
survive a restart and be searchable months later.

### Ten tables

| Table | Holds | Notes |
|---|---|---|
| `departments` | owning organisations | |
| `vms_instances` | video management systems | 2 rows; hosted grid has 0 cameras |
| `cameras` | the fleet | PostGIS `Geography(POINT)`, `corridor` (new) |
| `camera_health` | probe results | **hypertable**, 7-day chunks |
| `detections` | every sighting | **hypertable**, 1-day chunks |
| `watchlist` | plates of interest | |
| `alerts` | raised alerts | **no `reasons` column** → explainability impossible |
| `vehicle_tracks` | journey snapshots | `hops` JSONB, `path` LINESTRING |
| `users` | operators | argon2 hashes |
| `audit_log` | who did what | |

### Relationships

```
DEPARTMENT ──< CAMERA >── VMS_INSTANCE
                 │
                 ├──< CAMERA_HEALTH   (hypertable)
                 │
                 └──< DETECTION       (hypertable)
                          │
                          └──< ALERT >── WATCHLIST
                                 │
                               USER (acknowledged_by)

VEHICLE_TRACK  (keyed by plate string — NO FK to detections)
AUDIT_LOG      (keyed by user)
```

### `detections` — the important columns

| Column | Populated? |
|---|---|
| `ts`, `camera_id`, `track_id` | ✅ |
| `vehicle_type`, `plate_text`, `plate_normalised` | ✅ |
| `plate_confidence`, `detection_confidence` | ✅ |
| `ocr_raw` (JSONB evidence), `bbox`, `plate_bbox` | ✅ |
| `crop_key` | ✅ since P3 |
| `vehicle_colour` | 🔴 **always NULL** — no producer |
| `frame_key` | 🔴 **always NULL** |
| `direction` | 🔴 **always NULL** |
| `speed_kmph` | 🔴 **always NULL** |

Those four NULL columns are why attribute search (Part 36) is a real build, not
a query.

### ⚠️ The `vehicle_tracks` trap

`route_history.snapshot()` **INSERTs a new row every time a journey advances** —
each row holding the *entire* journey so far.

> Measured: **2,659 rows for 106 distinct plates.** One plate (`BG65USJ`) has
> **212 snapshots** of a 55-hop journey.

Any query must use `DISTINCT ON (plate_normalised) ORDER BY created_at DESC`.
Exploding the table naively inflates counts ~200× with no symptom but large
numbers. `routers/analytics.py` does this correctly.

---

# PART 15 — MULTI-CAMERA LINKING ⭐ THE CORE

**The question:** how does the system know Camera B saw the same vehicle as Camera A?

### Signals ACTUALLY used

| Signal | Used? | How |
|---|---|---|
| **Plate string equality** | ✅ | `plate_normalised` exact match — **the only linking signal** |
| Timestamp | ✅ | ordering and gap calculation |
| Camera location | ✅ | PostGIS great-circle distance |
| Distance | ✅ | validation only |
| Travel time | ✅ | validation only |
| Camera heading | ✅ | validation only |
| **Vehicle appearance / ReID** | 🔴 | **not built** |
| **Road topology** | 🔴 | distances are straight lines, not roads |
| Tracking ID | 🔴 | per-camera only, deliberately not used across cameras |

> **Be honest with judges:** linking is plate equality. Physics is a *filter*
> applied afterwards, not a matching signal. A vehicle whose plate can't be read
> is not linked at all.

### The algorithm, step by step

`services/api/app/services/correlator.py:build_route()`

```
1. SELECT all detections WHERE plate_normalised = 'GJ01AB1234'
   AND ts >= since, ordered by ts.          (LIMIT 5000)

2. CLUSTER into visits:
   sightings at the SAME camera within DWELL_WINDOW (5 min) = ONE visit.
   Why: a car waiting at a signal produces a detection every few seconds;
   without this a two-minute wait becomes forty hops.

3. For each consecutive pair of visits, build a HOP:
      distance_m   = great-circle between the two cameras (PostGIS)
      elapsed_s    = arrival(B) − departure(A)
      implied_kmph = distance / elapsed

4. VALIDATE the hop against six rules → flags.

5. Aggregate: total distance, duration, moving time, average speed,
   confidence, is_plausible.
```

### The six plausibility flags

| Flag | Condition | What it means |
|---|---|---|
| `revisit` | same camera again | the vehicle came back |
| `co_located` | distance < **50 m** | effectively the same place; implied speed meaningless (1 m of GPS error over 4 s is 900 km/h) |
| `impossible_simultaneous` | zero elapsed, real distance | two places at once → **cloned plate or misread** |
| `implausible_speed` | > **150 km/h** | generous for Indian highways; catches clones, not fast driving |
| `unobserved_gap` | > **1 hour** between sightings | went somewhere unwatched — not impossible, but bounds what the route can claim |
| `heading_conflict` | camera heading vs travel > **100°** | a camera watching westbound can't have seen an eastbound car |

**Flagged hops are marked, never deleted.** A system that quietly removes the
inconvenient parts of a route is not one a court should trust.

### Worked example

```
CAMERA A  10:00  GJ01AB1234   Ashram Rd @ Paldi
CAMERA B  10:08  GJ01AB1234   Ashram Rd @ Navrangpura   2.25 km
CAMERA C  10:15  GJ01AB1234   Ashram Rd @ Usmanpura     3.09 km

hop A→B: 2248 m / 480 s = 16.9 km/h   ✓ plausible
hop B→C: 3093 m / 420 s = 26.5 km/h   ✓ plausible
→ journey: 3 cameras, 5.34 km, 15 min, avg 21.4 km/h
```

---

# PART 16 — TRAJECTORY ENGINE ✅

### What is a trajectory?

The ordered sequence of *where* and *when* a vehicle was seen — its journey.

### Produced figures

| Field | Meaning |
|---|---|
| `hop_count`, `camera_count` | size of the journey |
| `distance_km` | **sum of straight lines** — a lower bound on road distance |
| `duration_s` | first sighting → last |
| `moving_s` | excludes dwell at each camera |
| `average_kmph` | journey average, **includes** time stationary |
| `moving_kmph` | average while moving |
| `is_plausible`, `confidence`, `flagged_hop_count` | trust indicators |
| `geometry_note` | explicit text saying these are straight lines |

**`average_kmph` is a lower bound twice over:** distances are great-circle rather
than road, and dwell inflates the denominator. This is stated in the API type.

### Storage

`persist_route()` writes a `vehicle_tracks` row: `hops` JSONB + a PostGIS
LINESTRING `path`. Called by `route_history.snapshot()` from the ingest daemon —
deliberately off the API hot path, because ingest p95 was already 5.7 s against
a 3 s target.

### Display

`web/src/components/RouteMap.tsx` draws the route as **dashed straight lines**
(honest — no fake road-snapping) with timeline playback. `web/src/lib/journey.ts`
holds the pure interpolation maths, extracted specifically so it could be
unit-tested: *a wrong interpolation makes the map lie smoothly.* Playback follows
**elapsed time**, not hop count — 16 tests enforce this.

### Limitations

1. Straight lines, not roads — distance is always an underestimate.
2. `sightings_for` applies `LIMIT 5000` **before** dropping NULL positions and
   returns the **oldest** rows with **no truncation indicator** — a "30-day"
   route can silently stop at day 3. *(Catalogued bug, unfixed.)*
3. Linking is plate-only (Part 15).

---

# PART 17 — TRAJECTORY ANOMALY 🔴 NOT IMPLEMENTED

**This is the most important honesty point in the document.**

### What exists

Six physics flags (Part 15). They are a **plausibility / cloned-plate filter**.

### What does NOT exist

- No learned norms. Nothing knows what a "normal" route is.
- No baseline comparison.
- No `AlertType.ANOMALY` producer — the enum value exists with **zero** producers.
  The only two alert producers in the entire repo are **watchlist hit** and
  **camera down**.
- No `reasons` column on `alerts`, so an explainable alert cannot be stored even
  if it were computed.

### What the flags would catch

```
Expected:  A → B → C → D
Observed:  A → B → X → D      ← we would NOT flag this. We have no model
                                 of "expected". We only catch physics:
                                 A(10:00) → D(10:00:02), 40 km apart.
```

### Language discipline — say this correctly

Call it a **trajectory anomaly**. Never call a vehicle, plate or driver
"suspicious", "criminal" or "wanted" on the basis of a route deviation.

> **Operational anomaly** = "this movement differs from the norm; a human should
> look." **Criminal determination** = a judgment only a person makes.

An operator shown "SUSPECT" for a lane change stops trusting the tool.

---

# PART 18 — VEHICLE SEARCH 🟡

| Capability | Status | Evidence |
|---|---|---|
| Exact plate | ✅ | `Detection.plate_normalised == _normalise(plate)` |
| Prefix | ✅ | `.startswith(...)` on an indexed column |
| Time range | ✅ | `since` / `until` |
| Camera filter | ✅ | `camera_id` |
| Min confidence | ✅ | `min_confidence` |
| Readable-only | ✅ | default true |
| **Fuzzy / near-miss** | 🔴 | `pg_trgm` installed, **never queried** |
| **Attribute (colour/type)** | 🔴 | `vehicle_colour` always NULL |

Normalisation strips non-alphanumerics and uppercases, so `gj 01 ab 1234`
matches `GJ01AB1234`.

The code is honest about the gap:

> *"Real fuzzy search is Phase 8; this is the honest interim — a prefix match on
> an indexed column, not a pretence at ranking."*

### Flow

```
User types plate → VehicleSearch.tsx → api.ts
   → GET /api/v1/detections?plate=GJ01AB1234       (sightings)
   → GET /api/v1/vehicles/GJ01AB1234/route         (journey)
        → correlator.build_route()
             → SELECT detections JOIN cameras
   → RouteMap renders hops + playback
```

Every plate search writes an **audit row** (enforced, tested).

---

# PART 19 — VEHICLE PROFILE ✅

`web/src/pages/VehicleSearch.tsx` shows, all from `/vehicles/{plate}/route`:

| Shown | Source |
|---|---|
| plate, first seen, last seen | `Route.first_seen` / `last_seen` |
| camera count, hop count | `Route` |
| total distance (km) | sum of hop distances |
| duration, moving time | `duration_s`, `moving_s` |
| average / moving speed | `average_kmph`, `moving_kmph` |
| per-hop timeline | `hops[]` — camera, arrival, dwell, distance, implied speed |
| flags per hop | `hops[].flags` |
| animated map route | `RouteMap.tsx` + `journey.ts` |
| plate crop evidence | `crop_url` presigned from MinIO |
| convoy partners | `/vehicles/{plate}/convoy` |

**Convoy detection** ✅ finds plates repeatedly seen at the same cameras within
seconds — and flags `likely_same_vehicle` when the partner plate is within one
character, because that's almost always one vehicle read two ways.

---

# PART 20 — BLACKLIST / WATCHLIST ✅

### How it works

```
Event arrives with plate GJ01AB1234
        ↓
WatchlistIndex.match()            ← in-memory, refreshed on change
        ↓
   ┌────┴─────┐
 EXACT      NEAR (one edit distance)
   │            │
priority as   priority CAPPED
  entered     (a lead, not a hit)
   ↓            ↓
INSERT INTO alerts
        ↓
publish → Redis alert channel → EventTailer → WebSocket → browser (~24 ms)
```

### Near-match reasoning

OCR confuses `0/O`, `1/I`, `8/B`. Requiring exactness would lose real hits. So a
one-character-different match raises `POSSIBLE_MATCH`, never `WATCHLIST_HIT`, and
its priority is **capped** whatever the entry says — a near match is a lead for a
human, not a confirmed identification.

### Alert contents

plate, confidence, camera, timestamp, priority, status, `detection_id`,
`watchlist_id`, notes, and **`crop_url`** — a presigned MinIO URL to the actual
plate photograph.

### Verified end-to-end (P3 gate)

```
blacklist a plate being read right now:  BG65USJ
  watchlist add                          HTTP 201
  ALERT BG65USJ · watchlist_hit · critical   (fired by itself)
  crop_url                               PRESENT
  crop fetches                           HTTP 200, 1977 bytes, image/jpeg
```

The fetched JPEG was visually confirmed as a legible plate reading **BG65 USJ**.

### Not implemented 🔴

`alerts` has **no `reasons`/`factors` column**, so CLAUDE.md's "explainability
rule" cannot be honoured. The only explanation ever stored is free-text prose in
`notes` for near matches.

---

# PART 21 — GIS / MAP ✅

### What is GIS?

Geographic Information System — storing and reasoning about *where* things are.

### Storage

`cameras.location` is PostGIS `Geography(POINT, SRID 4326)` — **Geography, not
Geometry**, so distance maths is in metres on a spheroid. Gujarat spans ~700 km,
where planar approximations drift. A GiST index powers `/cameras/nearby`.

### Rendering

**MapLibre GL JS 4.7.1**, `web/src/components/CameraMap.tsx`.

Two basemaps:
1. **Offline** — committed GeoJSON from `data/seed/`. No tile server, no network.
   Identical on a plane or at a venue with hostile wifi. **This path must keep working.**
2. **Satellite** — Esri World Imagery, no API key, probed before use, falls back
   automatically. The only external dependency in the product.

### A constraint worth knowing

**No MapLibre symbol/text layers.** They require a `glyphs` font endpoint, which
would be another network dependency. **Every map label is HTML** overlaid on the
canvas.

### Routes

`RouteMap.tsx` draws journeys as dashed LineStrings between camera points, with
a moving marker and an IST clock during playback.

---

# PART 22 — CITY TRAFFIC ANALYTICS 🟡 BACKEND ONLY

**Built 11 Sep 2026. Six endpoints. No UI page.**

| Metric | Endpoint | Status | Source |
|---|---|---|---|
| Vehicle count / density | `GET /analytics/flow` | ✅ | `detections` + `time_bucket` |
| Average speed per corridor | `GET /analytics/speed` | ✅ code, ⚠️ no data | `vehicle_tracks.hops` |
| Route density | `GET /analytics/routes` | ✅ | journey legs |
| Travel time vs baseline | `GET /analytics/travel-time` | ✅ code, 🟡 needs history | rolling median, same time-of-day |
| Hotspots | `GET /analytics/hotspots` | ✅ | volume ranked; slowdown needs baseline |
| Heatmap | `GET /analytics/heatmap` | ✅ | GeoJSON weighted by count |
| **Analytics UI page** | — | 🔴 **does not exist** | `recharts` imported 0 times |
| Predictive traffic | — | 🔴 | nothing |

### Verified live

```
GET /analytics/flow?bucket=15m&group_by=corridor
  → total vehicles: 307   Ashram Road: 307 across 2 buckets
```

### ⚠️ Why speed reports nothing — understand this before demoing

A replayed clip repeats every **P** seconds, so the largest possible gap between
two cameras seeing the same vehicle is P. With cameras 2–6 km apart:

| Corridor | spacing | clip | max gap | implied speed |
|---|---|---|---|---|
| Ashram Road | 2671 m | 15 s | 5.0 s | **1923 km/h** |
| Naroda Road | 1934 m | 60 s | 20.0 s | **348 km/h** |
| SG Highway | 6097 m | 13 s | 4.5 s | **4914 km/h** |
| Sardar Patel Ring Rd | 3737 m | 8 s | 2.6 s | **5174 km/h** |

Measured across stored journeys: **median 425.7 km/h, mean 7,226 km/h.**

This is a property of *replay*, not a bug. The endpoint therefore averages only
legs the correlator accepts (≤150 km/h), reports how many it excluded, and says
`insufficient_data` when none survive — rather than printing a confident number
describing the footage instead of the traffic. **It starts producing real figures
unchanged the moment the pipeline runs on real footage.**

### Honesty design

Every response carries `samples`. Absence is `status: insufficient_history` /
`insufficient_data` with a **null** figure — never a plausible invented number.
Volume is never relabelled as congestion: a busy road may be flowing freely.

---

# PART 23 — FRONTEND ✅

| Aspect | Choice |
|---|---|
| Framework | React 18.3 + TypeScript 5.7 |
| Build | Vite 6 |
| Routing | react-router-dom 6.28 |
| State | **React hooks only** — no Redux/Zustand |
| Styling | TailwindCSS 3.4, hand-rolled (no shadcn/ui) |
| Map | MapLibre GL |
| Charts | Recharts installed, **used nowhere** |
| Video | WHEP (native WebRTC) + hls.js fallback |
| Realtime | one WebSocket per tab via `useEventStream` |
| Tests | vitest + RTL, 40 tests |

### Pages

| Route | Page | Purpose | Key APIs |
|---|---|---|---|
| `/dashboard` | `Dashboard.tsx` | overview counters | cameras/summary, alerts/stats |
| `/map` | `MapView.tsx` | GIS fleet map | `/cameras/geojson` |
| `/anpr` | `LiveAnpr.tsx` | live video + plate feed | `/cameras`, `/cameras/{id}/stream`, `/detections`, WS |
| `/alerts` | `Alerts.tsx` | alert queue + crops | `/alerts`, `/alerts/{id}/transition` |
| `/vehicles` | `VehicleSearch.tsx` | search + journey + playback | `/detections`, `/vehicles/{plate}/route` |
| `/watchlist` | `Watchlist.tsx` | manage watched plates | `/watchlist` CRUD |
| `/health` | `FleetHealth.tsx` | camera uptime, gaps | `/health/fleet`, `/health/gaps` |
| `/cameras` | `Cameras.tsx` | registry CRUD, CSV import | `/cameras`, `/cameras/bulk` |
| `/users` | `Users.tsx` | user admin | `/users` |
| `/audit` | `AuditLog.tsx` | who did what | `/audit` |
| **`/analytics`** | — | 🔴 **missing** | — |

### `LiveAnpr.tsx` detail

Opens **one** stream at a time (the selected camera), not a grid — verified. The
sidebar lists all ANPR cameras with status dots and a "reading plates" badge
driven by recent detections. `AnprOverlay` draws live bounding boxes from
WebSocket events over the video.

---

# PART 24 — BACKEND ✅

### Structure — routers → services → models

```
HTTP request
    ↓
Middleware   (request ID, audit, rate limit)   app/middleware/, app/core/
    ↓
Router       app/routers/*.py     — HTTP shape, permissions, pagination
    ↓
Service      app/services/*.py    — business logic (correlator, watchlist…)
    ↓
Model        app/models/*.py      — SQLAlchemy ORM
    ↓
PostgreSQL
```

There is **no separate repository layer** — services use the ORM directly.

### Background workers inside the API process

| Worker | Purpose |
|---|---|
| `event_consumer` | reads Redis Stream, persists detections, raises alerts |
| `event_tailer` | fans the stream out to connected WebSockets |
| `alert_fanout` | publishes alerts to the broadcast channel |
| `gateway` | reconciles MediaMTX paths |
| `fleet_roster` | publishes the camera roster for worker discovery |
| `retention` | data retention sweeps |
| `token_store` | stream token lifecycle |

### Separate containers from the same image

`ingest` runs the health monitor from the **API image** with a different command
— it shares the ORM models and adapters. One image build instead of two protects
the 5-minute demo, and it is still a separate container so probing never competes
with serving operators.

---

# PART 25 — API MAP

| Method | Endpoint | Purpose | Used by |
|---|---|---|---|
| POST | `/api/v1/auth/login` | get JWT | Login |
| POST | `/api/v1/auth/refresh` | renew token | api.ts |
| GET | `/api/v1/auth/me` | current user + permissions | App |
| GET | `/api/v1/cameras` | list (paginated, ≤200) | Cameras |
| GET | `/api/v1/cameras/geojson` | map features | MapView |
| GET | `/api/v1/cameras/nearby` | proximity (PostGIS) | MapView |
| POST | `/api/v1/cameras/bulk` | CSV import with dry-run | Cameras |
| GET | `/api/v1/cameras/{id}/stream` | **scoped stream grant** | LiveAnpr |
| GET | `/api/v1/detections` | **plate search** | VehicleSearch |
| GET | `/api/v1/vehicles/{plate}/route` | **journey** | VehicleSearch |
| GET | `/api/v1/vehicles/{plate}/convoy` | travelling companions | VehicleSearch |
| GET | `/api/v1/watchlist` + CRUD | manage watched plates | Watchlist |
| GET | `/api/v1/alerts` | alert queue | Alerts |
| POST | `/api/v1/alerts/{id}/transition` | ack/dispatch/close | Alerts |
| GET | `/api/v1/health/fleet` | fleet availability | FleetHealth |
| GET | `/api/v1/health/gaps` | coverage gaps | FleetHealth |
| GET | `/api/v1/analytics/*` | six analytics endpoints | 🔴 nothing yet |
| GET | `/api/v1/audit` | audit trail | AuditLog |
| WS | `/ws/events` | live detections + alerts | useEventStream |
| GET | `/health`, `/ready`, `/metrics` | ops | Docker, Prometheus |

**Stream grant** is worth understanding: `GET /cameras/{id}/stream` returns a
**short-lived, camera-scoped token** plus WHEP/HLS URLs. The browser never
receives the camera's real RTSP URL, which for a federated camera carries grid
credentials.

---

# PART 26 — REAL-TIME SYSTEM ✅

```
AI worker detects vehicle
        ↓  XADD
REDIS STREAM  nagarnetra:events:detections   (maxlen ~100,000)
        ↓  XREADGROUP (consumer group)
 ┌──────┴───────┐
 │              │
event_consumer  event_tailer
 │ persist      │ fan out
 │ + watchlist  │
 ↓              ↓
PostgreSQL   WebSocket /ws/events
                ↓
         useEventStream (ONE socket per browser tab)
                ↓
   LiveAnpr overlay · Alerts toast · plate feed
```

**Mechanisms confirmed:** WebSocket ✅, Redis Streams with consumer groups ✅,
background asyncio workers ✅. `sse-starlette` is a dependency and CLAUDE.md
mentions an SSE fallback — ❓ I did not verify it is wired.

**Why a consumer group:** if the API restarts, unacknowledged events are
redelivered rather than lost. The stream is capped at ~100,000 entries so a
stopped consumer cannot fill Redis.

**Why one socket per tab:** a control room may have many components wanting
events; one socket per component would open dozens.

Measured alert latency to a connected operator: **~24 ms**.

---

# PART 27 — DEPLOYMENT ✅

### Nine services

| Service | Image | Purpose |
|---|---|---|
| `postgres` | `timescale/timescaledb-ha:pg16.14-ts2.29.2-all` | DB + PostGIS + Timescale |
| `redis` | `redis:7.4.11-alpine` | event bus |
| `minio` | `minio/minio:2025-09-07` | crops |
| `opensearch` | `opensearchproject/opensearch:2.19.6` | **unused** |
| `mediamtx` | `bluenviron/mediamtx:1.20.1-ffmpeg` | video gateway |
| `api` | `nagarnetra/api:dev` | FastAPI |
| `ingest` | same image | health monitor |
| `simulator` | same image | camera fleet |
| `web` | `nagarnetra/web:dev` | React |
| `ai-worker` | `nagarnetra/ai-worker:dev` | **profile `ai`** |

### Compose profiles

- **base** — everything except AI
- **`ai`** — adds `ai-worker`. ⚠️ **`make up` starts NO AI.** Use `make ai`.
- **`scale`** — Redpanda swap for load testing

### Why `timescale/timescaledb-ha`

`postgis/postgis` has **no arm64 build**. This image ships PostGIS *and*
TimescaleDB for arm64 in one container. Migration 0001 asserts both extensions
exist.

### Why the runtime is torch-free

Ultralytics (AGPL-3.0) + PyTorch is ~2.5 GB and is used **only at model-fetch
time** in a throwaway tools image to export ONNX. The shipped worker carries
`onnxruntime` + OpenCV only — ~400 MB. **AGPL code never ships in the deployed
artifact.**

---

# PART 28 — CONFIGURATION

- `.env` (gitignored) · `.env.example` (committed)
- Parsed **once** into a Pydantic `Settings` object at `app/core/config.py`.
  No scattered `os.getenv`.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | `<environment variable>` |
| `REDIS_URL` | event bus |
| `MINIO_*` | object storage credentials + public endpoint |
| `JWT_SECRET` | `<environment variable>` |
| `BOOTSTRAP_ADMIN_*` | first admin |
| `AI_WORKER_MAX_CAMERAS` | inference slots (default 4) |
| `AI_WORKER_SLICE_SECONDS` | rotation slice (45) |
| `AI_WORKER_PINNED` | cameras that never yield |
| `AILAB_INFERENCE_BUDGET` | total inference threads (default cores−2) |
| `AILAB_OPENCV_THREADS` | OpenCV pool |
| `SIM_MAX_HEIGHT` / `SIM_MAX_FPS` | published stream cap (1080/15) |

Pipeline tuning lives in `ai-lab/configs/*.yaml` (`default.yaml` → `stream.yaml`),
mounted so retuning needs no rebuild.

**Secrets rule:** `credentials_ref` stores a *pointer* to a secret, never a
secret. No secrets in git.

---

# PART 29 — ERROR HANDLING

| Failure | Handling | Where |
|---|---|---|
| Corrupt clip | `is_playable()` ffprobe screen; excluded from replay | `publisher.py` |
| Corrupt frame | RTSP forced over **TCP** — UDP drops packets silently and hands the decoder smeared plates that look like model bugs | `reader.py:35` |
| No vehicle | normal; no event | — |
| No plate | event emitted with `readable: false` | `events.py` |
| OCR failure | caught (`ValueError/RuntimeError/IndexError`), logged debug, counted as skipped read | `rapid.py` |
| Low confidence | below `min_confidence` discarded; `ambiguous` flagged | `consensus.py` |
| Camera unreachable | `StreamUnavailable` with a **reason** mapped to an owner; one log line, not a traceback | `worker.py` |
| Camera dies mid-stream | supervisor restarts with backoff | `worker.py` |
| Stream ends | rotation releases the slot | `rotation.py` |
| Crop upload queue full | returns **None** → no dangling key in DB | `crops.py` |
| Crop 404 in browser | `PlateCrop` hides itself (upload may be in flight) | `PlateCrop.tsx` |
| API 500 | request ID returned; structlog with `exc_info` | `main.py` |
| DB down | health `/ready` fails; container unhealthy | `health.py` |
| OpenSearch down | **not fatal** — explicitly non-blocking | `health.py:106` |
| Permission denied | audit row written **before** the 403 | `rbac.py` |

### Weak areas 🟠

- No validation on either side of the event bus (`packages/contracts/` empty).
- `sightings_for` truncates at 5000 with **no indicator** — silent data loss.
- `transition()` overwrites `acknowledged_by` on **every** transition, erasing
  who acknowledged.
- Dedup `repeats` counter never persisted despite the docstring claiming it is.

---

# PART 30 — PERFORMANCE (measured, not invented)

**Host:** Docker Desktop, macOS arm64, 10 vCPU, 11.67 GB to the VM, **CPU-only**.

### AI pipeline

| Metric | Value | Source |
|---|---|---|
| Processing rate | ~9 fps | `docs/PROGRESS.md` (old 4K fleet) |
| Capture→event p90 | 266 ms | same |
| OCR confidence range | 0.70–0.94 | same |
| Plate yield | **37.9%** of detections carry a plate | live DB, 11 Sep |

### Worker CPU — the 10 Sep investigation

| | before | after |
|---|---|---|
| OS threads | **168** | 79, stable |
| Worker CPU | 866% (1 sample) | ~656% (38-sample mean) |
| Memory | 3.2 GB, climbing | 3.3 GB, flat |
| **Detections/min** | **79** | **202** |
| Models loaded/hour | ~180 | 4 |

**Root causes:** (1) nothing divided the ONNX thread budget by camera count —
RapidOCR's three sessions took one thread per core *each*, per camera;
(2) rotation rebuilt a `Pipeline` per camera change, leaking a native thread pool
each time; (3) a quarter of cameras published 4K60 at 24 Mbps.

**The measurement that settles it** — RapidOCR on one plate crop:

| | wall/call | CPU/call | cores |
|---|---|---|---|
| `intra_op=2` | 11.8 ms | 23.6 ms | 2.0× |
| **ORT default** | **14.3 ms** | **132.6 ms** | **9.3×** |

The library default burned **5.6× the CPU to return a slower answer**.

### Model micro-benchmarks

| Stage | Cost |
|---|---|
| YOLOv8n @640, 1 thread | 275 ms |
| YOLOv8n @640, **4 threads** | **126 ms** (best) |
| YOLOv8n @640, 8 threads | 331 ms |
| YOLOv8n @640, ORT default | 452 ms |
| PP-OCR **detection** stage | 1334 ms |
| PP-OCR **recognition** only | 74 ms (**18× cheaper**) |

### Event tier scale proof

**2,774 events/s sustained, 0 failures**, across 80,000 camera identities
(range 2,280–4,002). `docs/BUILD_STATE.md:1004`. Throughput gate met; **latency
gate not**.

### Slot scaling (11 Sep)

| slots | CPU | memory | detections/min | cameras seen /5 min |
|---|---|---|---|---|
| 3 | 574% | 2.7 GB | 150 | 10 |
| **4** (default) | 647% | 4.1 GB | 124 | 15 |
| 6 | 589% | 6.8 GB | 133 | 27 |

**CPU is flat; only memory scales** (~1.4 GB/slot). Memory, not CPU, caps the
slot count. Six slots is where `exit 137` OOM kills start.

---

# PART 31 — SECURITY & PRIVACY

### Implemented ✅

| Control | Detail |
|---|---|
| Authentication | JWT access + refresh, **argon2** hashing |
| Authorization | RBAC, 6 roles, declared once as a matrix, **tested** |
| Audit trail | every plate search, stream open, watchlist mutation → `audit_log` |
| Denied-access logging | audit row written **before** the 403 |
| Scoped stream tokens | short-lived, camera-scoped; browser never sees RTSP creds |
| Presigned crop URLs | expire in **300 s** — a link that works tomorrow can be forwarded |
| Credential redaction | RTSP URLs redacted in every log line |
| Rate limiting | `RateLimitMiddleware` |
| Secrets | `.env` gitignored; `credentials_ref` is a pointer, never a secret |
| Forced password change | `must_change_password` on admin-set passwords |

### The RBAC matrix

| Role | Cameras | Streams | Watchlist | Alerts | Search | Analytics | Users | Audit |
|---|---|---|---|---|---|---|---|---|
| admin | CRUD | view | CRUD | all | yes | read | CRUD | read |
| supervisor | CRU | view | CRU | ack/close | yes | read | – | read |
| operator | read | view | read | ack | yes | read | – | – |
| analyst | read | – | read | read | yes | read | – | – |
| auditor | read | – | read | read | **no** | read | – | read |
| api_client | create | – | – | – | – | – | – | – |

**Auditor has no search** — an auditor reviews *who looked at what*; granting
them search defeats the separation. **Auditor does have analytics** because
analytics identifies nobody.

### Missing 🔴 — say this plainly

- **No HTTPS/TLS in the compose stack.** Everything is HTTP on localhost.
- No penetration testing.
- No encryption at rest.
- No data-retention enforcement by policy (a `retention` service exists; ❓ policy unverified).
- No PII/DPDP-Act compliance review.
- No MFA.
- JWT secret is an env var, not a managed secret store.

> Do **not** tell a judge the system is secure because it uses JWT. Say: "we
> built auth, RBAC and an audit trail from the start because this platform
> tracks private vehicles; TLS, encryption at rest and a compliance review are
> production work we have not done."

### Privacy stance

This platform tracks the movement of private vehicles. Traceability of who
looked at what is deliberately a feature, not an afterthought.

---

# PART 32 — SCALABILITY

### What currently works ✅

| Mechanism | Detail |
|---|---|
| Worker sharding | stable hash of camera code; `--scale ai-worker=N`, no coordinator, two workers can never open the same stream |
| Event bus | Redis Streams + consumer groups; **2,774 events/s proven** |
| Hypertables | `detections` in 1-day chunks — droppable/compressible as a unit |
| Camera rotation | covers N cameras with M slots |
| Thread budget | bounded CPU regardless of camera count |
| Edge AI | central tier carries **events, not video** |

### The architecture claim

For ~1,000 cameras: 1,000 × 4 Mbps ≈ **4 Gbps** of centralised video versus
~35 events/s × ~2 KB ≈ **0.6 Mbps** of metadata. The ratio is ~**7,000×**. That
is what makes a city deployment run on commodity hardware.

### Required for production scale 📄

- GPU inference (CUDA path exists, **untested** — see `docs/GPU.md`)
- Redpanda/Kafka (scale profile exists, not load-tested end to end)
- Read replicas, continuous aggregates for analytics
- Distributed object storage
- Real re-identification to reduce dependence on plate reads

### Honest sizing

`scripts/capacity_model.py` models any fleet, tagging every constant MEASURED
(with source) or ASSUMED (with rationale). At **100,000 cameras, CPU-only**:
45,558 cores / 1,139 nodes / **₹49.14 crore capex** / ₹10.98 crore-per-year opex.

⚠️ `docs/INFRASTRUCTURE.md` §2 is **~4× optimistic** (treats a 4-thread worker as
one core). The capacity model supersedes it.

---

# PART 33 — CURRENT DEMO FEATURES

| Feature | Status | How | Demo |
|---|---|---|---|
| Multi-camera feeds | ✅ | 12 cameras, 4 corridors | Live ANPR sidebar |
| Live video | ✅ | WHEP + hls.js fallback | click a camera |
| Vehicle detection | ✅ | YOLOv8n ONNX | overlay boxes |
| ANPR + confidence | ✅ | RapidOCR + consensus | plate feed |
| Tracking | ✅ | ByteTrack | stable IDs |
| **Cross-camera linking** | ✅ | plate equality + physics | search a plate |
| **Journey + playback** | ✅ | correlator + RouteMap | press play |
| Plate search | 🟡 | exact + prefix | type a plate |
| **Blacklist alert + crop** | ✅ | watchlist → WS | add plate, wait |
| Convoy detection | ✅ | shared cameras in time | vehicle profile |
| Camera health | ✅ | 15 s probe sweep | Fleet Health |
| GIS map | ✅ | MapLibre, offline basemap | Map |
| Audit trail | ✅ | every sensitive action | Audit page |
| **City analytics** | 🟡 | API only | **curl only — no UI** |
| Trajectory anomaly | 🔴 | — | **do not demo** |
| Fuzzy search | 🔴 | — | **do not demo** |
| Predictive traffic | 🔴 | — | **do not demo** |

---

# PART 34 — WHAT IS ACTUALLY IMPRESSIVE

**Ranked.**

1. **Cross-camera journey reconstruction with honest validation.** Not just
   linking — six physics rules that mark implausible legs rather than hiding
   them. Most teams will show OCR; few show a system that knows what it doesn't
   know.
2. **Multi-frame consensus with per-character voting.** Demonstrably better than
   max-confidence, with the reasoning measured. This is a genuine engineering
   decision, not a library call.
3. **Recognition-before-detection OCR ordering.** An 18× saving found by
   profiling, contradicting the library's normal usage.
4. **The edge/central split with a measured 7,000× argument.** This is the
   scaling story and it's architecturally real.
5. **Evidence crops from the edge.** Only the object key rides the bus — keeps
   the architecture claim true while still showing operators a photograph.
6. **The CPU budget work.** 2.6× throughput at lower CPU by fixing thread
   oversubscription, with before/after measurements.
7. **Auth/RBAC/audit built in from the start**, tested, with a declared matrix.
8. **Honest degradation everywhere** — "insufficient history", flagged hops,
   excluded implausible legs.

### Do NOT present these as innovation

- YOLO object detection (a library call)
- Reading a plate with OCR (a library call)
- Showing markers on a map
- Docker Compose
- CRUD screens

---

# PART 35 — DIFFERENTIATION

| Compared with | They do | We add |
|---|---|---|
| **Basic ANPR** | plate from one image | journeys, linking, analytics |
| **CCTV monitoring** | humans watch screens | automatic structured events |
| **Vehicle detection** | boxes on frames | identity, then movement |
| **Plate OCR** | one read, one answer | multi-frame consensus + evidence |
| **Single-camera tracking** | IDs within one feed | cross-camera correlation |

### The positioning line — verified accurate

> "ANPR tells us what a camera sees. Our platform connects observations across
> cameras to understand how vehicles move through the city."

✅ This is accurate. `correlator.build_route()` is real, produces real journeys
from real detections, and the map renders them.

**Caveat to volunteer if asked:** linking is by plate string. Appearance-based
re-identification is not built, so a vehicle whose plate cannot be read is not
linked.

---

# PART 36 — LIMITATIONS (be brutally honest)

| Limitation | Why | Impact | Fix |
|---|---|---|---|
| **Accuracy never measured on real footage** | only synthetic clips labelled | cannot claim >90% | label real footage, run `make evaluate` |
| **Recall is the bottleneck, not OCR** | detector fixed at 640px | misses distant/night vehicles; declines 3 of 8 plates | re-export detector at 1280 |
| **Track fragmentation** | motion-only tracker | one car → three tracks; precision 0.70 | tune buffer / add appearance |
| **No re-identification** | not built | unreadable plate = no link | appearance embeddings (P11) |
| **Straight-line distances** | no road network | distance always underestimated | OSRM/road graph |
| **No anomaly detection** | no learned norms | PS requirement unmet | P5 |
| **No fuzzy search** | pg_trgm unused | misread plate not found | P8 |
| **Speed unmeasurable on replay** | clip period bounds gap | speed panel says "no data" | real footage |
| **Analytics has no UI** | not built yet | cannot demo step 8 | P4.3 |
| **Night/rain/blur untested** | no such footage | unknown real accuracy | source footage |
| **Camera clock sync assumed** | no NTP validation | wrong timestamps → wrong journeys | NTP + drift detection |
| **False links on cloned plates** | plate equality only | two cars, one journey | flagged, not prevented |
| **No TLS** | dev stack | not production-safe | reverse proxy |
| **12 cameras, not a city** | laptop constraint | "city-wide" is aspirational | scale test |
| **Demo footage is UK plates** | sourcing | grammar must accept GB | Indian footage |

---

# PART 37 — TECHNICAL DEBT

### 🔴 Critical

| Debt | Detail |
|---|---|
| `packages/contracts/` empty | event dict hand-rolled and **duplicated by hand**; no validation either side |
| No `alerts.reasons` column | explainability rule cannot be honoured |
| `sightings_for` silent truncation | LIMIT 5000 before filtering, oldest-first, **no indicator** |
| Accuracy unmeasured | the headline PS requirement |

### 🟠 Important

| Debt | Detail |
|---|---|
| `mypy` fails | 17 errors, not in `make lint`, CLAUDE.md claims it passes |
| `transition()` erases `acknowledged_by` | on every transition |
| `_within_one_edit` duplicated | in `watchlist.py` and `correlator.py`, **different implementations** |
| OpenSearch dead weight | runs, indexes nothing, eats demo-laptop memory |
| `HOSTED_GRID` adapter | old challenge's grid, no standing in SIH26127; federates 0 cameras |
| `docs/INFRASTRUCTURE.md` 4× optimistic | superseded by capacity model |
| Analytics endpoints untested | violates CLAUDE.md §8.4 |

### 🟡 Minor

- `Match.distance` hardcoded to 1 → every near-match note says "(1 character different)"
- Dedup `repeats` never persisted despite docstring
- `revisit`/`co_located` legs `continue` early, skipping `unobserved_gap` and `heading_conflict`
- `docs/STATUS.md` stale (25 Aug)
- Old-brief residue ("statewide", 80,000 cameras, Gujarat Police) across docs
- `shadcn/ui` claimed in CLAUDE.md §4, not used

---

# PART 38 — HOW TO RUN

```bash
# 0. Prerequisites: Docker Desktop running, ≥8 GB allocated to the VM

# 1. One-shot demo: builds, starts, migrates, seeds, opens the browser
make demo

# 2. AI is behind a compose profile — `make up` starts NO AI
make ai

# 3. One-time: fetch sample footage and model weights
make videos
make models
```

### What each does

| Command | Does |
|---|---|
| `make up` | start the 9 base containers, wait for healthy |
| `make ai` | add `ai-worker` (compose profile `ai`) |
| `make demo` | `up` + migrate + seed + open browser |
| `make seed` | load cameras, watchlist, users |
| `make test` | API + ai-worker + simulator + e2e + frontend |
| `make lint` | ruff + ruff format + `tsc --noEmit` |
| `make status` | dependency readiness |
| `make clean` | tear down including volumes |

### Login

```
admin / NagarNetra@2026
also: supervisor · operator · analyst · auditor
```

Browser: `http://localhost:5173` (web), `http://localhost:8000/docs` (API).

---

# PART 39 — HOW TO DEBUG

### Frontend doesn't load
1. `docker compose ps` — is `web` up?
2. `docker logs nagarnetra-web`
3. Browser console — is `VITE_API_BASE_URL` resolving?

### Camera doesn't appear
1. `docker exec nagarnetra-postgres psql -U nagarnetra -d nagarnetra -c "SELECT camera_code,status FROM cameras;"`
2. Is it in `data/seed/cameras.csv`? Seeding is **additive** — trimming the CSV does not delete rows. Use `python -m scripts.prune_registry --match-seed`.
3. `docker logs nagarnetra-simulator | grep stream_started`

### Video doesn't play
1. Is the MediaMTX path ready? `curl http://localhost:9997/v3/paths/list` (may need auth)
2. WHEP blocked? The player falls back to HLS via hls.js.
3. ⚠️ **Never set a simulator camera's `stream_url` to the gateway's own URL** —
   MediaMTX will pull the path from itself, loop forever, and silently block
   publishing for the whole fleet. This cost an hour once.

### ANPR isn't detecting
1. **Is the worker even running?** `docker compose ps ai-worker` — `make up` does
   not start it. Use `make ai`.
2. `docker logs nagarnetra-ai-worker | grep "inference budget"` — should show
   threads per model.
3. `docker logs nagarnetra-ai-worker | grep "models built"` — `built` must settle
   at the slot count. If it keeps climbing, the pipeline pool is leaking.
4. Are models present? `ls ai-lab/models/` → `make models`.

### OCR is wrong
1. Check `ocr_raw` on the detection — it holds every candidate and its score.
2. Check `agreement` / `reads_total`. Low agreement = hard plate.
3. Is the crop too small? `min_crop_width: 52`.
4. Is grammar rejecting a valid plate? Check `plate_regions` on the camera.

### API returns 500
1. Response carries a `request_id` — grep it: `docker logs nagarnetra-api | grep <id>`
2. Common: a SQL error. **SQLAlchemy `text()` parses `:word` inside SQL comments**
   — never write a colon-prefixed word in a SQL comment, and use
   `CAST(:x AS text)` not `:x::text`.

### Trajectory doesn't appear
1. Does the plate exist on ≥2 cameras? `GET /api/v1/vehicles/routable`
2. Are cameras on the same corridor replaying the **same clip**? Different clips
   means no shared vehicles and no links.
3. Check hop `flags` — the route may exist but be entirely implausible.

### Everything is slow / cameras hang
1. `docker stats` — is `ai-worker` above ~700%?
2. Lower `AILAB_INFERENCE_BUDGET` (default cores−2).
3. Raise Docker Desktop memory — 6 slots needs ~6.8 GB for the worker alone.

---

# PART 40 — THE 25 FILES THAT MATTER

| # | File | Why it matters for Q&A |
|---|---|---|
| 1 | `ai-lab/ailab/pipeline.py` | where the whole AI chain is assembled |
| 2 | `ai-lab/ailab/stream/runner.py` | live inference; emits observed vs completed |
| 3 | `ai-lab/ailab/aggregate/consensus.py` | **per-character voting — the accuracy story** |
| 4 | `ai-lab/ailab/ocr/rapid.py` | recognition-first, 18× saving |
| 5 | `ai-lab/ailab/detect/onnx_backend.py` | model loading, thread counts, providers |
| 6 | `ai-lab/ailab/track/bytetrack.py` | the two-pass association trick |
| 7 | `ai-lab/ailab/track/merge.py` | fragments → one Vehicle |
| 8 | `ai-lab/ailab/plate/scheduler.py` | why we don't run OCR every frame |
| 9 | `ai-lab/ailab/aggregate/grammar.py` | plate format validation |
| 10 | `ai-lab/ailab/runtime.py` | CPU budget divided across cameras |
| 11 | `services/ai-worker/ai_worker/worker.py` | supervision, rotation, sharding |
| 12 | `services/ai-worker/ai_worker/pipeline_pool.py` | closes the rotation thread leak |
| 13 | `services/ai-worker/ai_worker/crops.py` | edge upload, bounded queue |
| 14 | **`services/api/app/services/correlator.py`** | **THE trajectory engine** |
| 15 | `services/api/app/services/watchlist.py` | exact + near match, priority capping |
| 16 | `services/api/app/services/event_consumer.py` | Redis → DB → alert |
| 17 | `services/api/app/services/route_history.py` | journey snapshots (and the 200× trap) |
| 18 | `services/api/app/services/evidence.py` | presigned URLs; **SigV4 signs Host** |
| 19 | `services/api/app/routers/analytics.py` | the six aggregate endpoints |
| 20 | `services/api/app/routers/detections.py` | plate search |
| 21 | `services/api/app/core/rbac.py` | the authorisation matrix |
| 22 | `services/api/alembic/versions/0001_initial_schema.py` | schema + hypertables |
| 23 | `services/simulator/simulator/publisher.py` | clip prep, 1080p15 cap, offsets |
| 24 | `web/src/lib/journey.ts` | playback maths (tested) |
| 25 | `web/src/hooks/useEventStream.tsx` | one WebSocket per tab |

---

# PART 41 — FOLLOW ONE VEHICLE

**A white hatchback, plate `GJ01AB1234`, drives down Ashram Road.**

**10:31:04 — Camera CAM-DEMO-01, Paldi.**
The simulator is publishing this camera's clip to MediaMTX. The AI worker holds
an inference slot for it and is pulling RTSP over TCP.

OpenCV hands `StreamRunner` a 1920×1080 frame. `Pipeline.process()` letterboxes
it to 640×640 and runs YOLOv8n. Output: a box at (820, 410)–(1150, 690), class
`car`, confidence 0.87.

ByteTrack compares this box against its predictions. It matches an existing
track → `track_id = 7`. The car has been visible for 11 frames.

The plate scheduler asks: has this vehicle moved enough, and has it been 4+
frames? Yes → search. The vehicle crop goes to the plate detector at 320px,
which returns a plate box. `CropGate` compares this crop's sharpness and
perceptual hash against the best one already read — it's sharper, so it's new
evidence.

RapidOCR runs **recognition-only** on the crop: `"GJ01AB1234"`, confidence 0.91.
This is read number 12 for track 7.

**10:31:07 — the car leaves the frame.**
After `retire_after_s: 1.5`, the track retires. `consensus()` takes all 14 reads
and votes per character position, weighted by confidence × crop quality. Eleven
reads agree exactly. Final: `GJ01AB1234`, confidence 0.91, `reads_total=14`,
`agreement=11`, `grammar_valid=true`.

The best crop is JPEG-encoded and pushed to a bounded queue; a background thread
uploads it to MinIO as `crops/plates/2026/09/11/track_7_GJ01AB1234.jpg`. The
**key** is returned synchronously — the bytes never touch the event bus.

A `vehicle.completed` event is `XADD`ed to Redis.

**10:31:07.02 — the API.**
`event_consumer` reads the batch via its consumer group. It inserts a
`detections` row. Then `WatchlistIndex.match("GJ01AB1234")` — no hit today.
`EventTailer` fans the event to every open WebSocket; the operator's Live ANPR
screen draws a box and adds a row to the plate feed.

**10:39:12 — Camera CAM-DEMO-02, Navrangpura, 2.25 km north.**
The same sequence. A second `detections` row.

**10:46:31 — Camera CAM-DEMO-03, Usmanpura.**
A third.

**Later — the journey.**
`route_history.snapshot()` runs from the ingest daemon. It asks the correlator
for plates seen on ≥2 cameras. `build_route("GJ01AB1234")` selects all three
detections, clusters each camera's sightings into one visit (5-minute dwell
window), and builds two hops:

```
hop 1  DEMO-01 → DEMO-02   2248 m / 488 s = 16.6 km/h   flags: []
hop 2  DEMO-02 → DEMO-03   3093 m / 439 s = 25.4 km/h   flags: []
```

Both pass all six checks. A `vehicle_tracks` row is written with the hops JSONB
and a PostGIS LINESTRING.

**An operator searches.**
They type `GJ01AB1234` into Vehicle Search. The frontend calls `/detections` for
sightings and `/vehicles/GJ01AB1234/route` for the journey. `RouteMap` draws
three markers joined by dashed lines. The operator presses **play**, and a marker
moves along the route with an IST clock — the position interpolated by elapsed
*time*, not hop index, so the vehicle moves fast on the fast leg.

An **audit row** records that this user searched this plate.

---

# PART 42 — JUDGE Q&A (50+)

### PRODUCT

**1. What does your system do?**
*Simple:* it connects what different cameras see so we can follow a vehicle
across the city.
*Technical:* multi-camera ANPR with cross-camera correlation producing validated
trajectories, plus watchlist alerting and traffic analytics.
*Evidence:* `correlator.py:build_route()`.
*Don't claim:* that it predicts anything.

**2. How is this different from ANPR software I can buy?**
ANPR answers "what plate is this". We answer "where has this vehicle been".
*Evidence:* journey with distance/duration/flags.
*Don't claim:* that commercial systems can't do this — many can; claim our
implementation and honesty.

**3. Who is the user?**
Traffic police, city traffic management, investigators. Six RBAC roles reflect
different jobs.

**4. Is this deployable tomorrow?**
No. It's a demo-grade prototype: no TLS, accuracy unmeasured on real footage,
12 cameras. Be direct about this.

### PROBLEM STATEMENT

**5. Does it meet the >90% accuracy requirement?**
*Honest answer:* we cannot claim it yet. We have 62.5–87.5% end-to-end on
synthetic footage and 100% exact-match on plates the pipeline chose to attempt.
The bottleneck is **recall** — it declines to read 3 of 8 plates — not OCR
correctness. **Never claim 90%.**

**6. Which PS requirements are unmet?**
Anomaly detection, fuzzy search, predictive traffic, attribute search,
re-identification, and analytics has no UI yet.

### AI / ML

**7. Which detection model and why?**
YOLOv8n exported to ONNX. Nano because it must run on a laptop CPU alongside
eight other containers.

**8. Why ONNX and not PyTorch?**
Cuts the image from ~2.5 GB to ~400 MB and keeps AGPL-licensed Ultralytics out
of the shipped artifact. Ultralytics is used at *build* time only, to export.

**9. Did you train your own model?**
No — pretrained YOLO plus a plate-detection model. Our engineering contribution
is the pipeline: scheduling, crop gating, consensus, and the correlator.
*Don't claim* you trained a model.

**10. Do you use a GPU?**
No. CPU-only. Inside Docker on Apple Silicon there is literally no accelerator —
providers are `['AzureExecutionProvider','CPUExecutionProvider']`. CUDA and
CoreML code paths exist and are unmeasured. See `docs/GPU.md`.

**11. How fast is inference?**
YOLOv8n @640: 126 ms at 4 threads. Whole pipeline ~9 fps, capture-to-event p90
266 ms.

### COMPUTER VISION

**12. What is a bounding box?** Four numbers giving a rectangle around an object.

**13. What is NMS?** Non-Max Suppression — removes duplicate overlapping boxes
for the same object. IoU 0.45.

**14. What is letterboxing?** Resizing to the model's square input while keeping
aspect ratio, padding the rest.

### ANPR / OCR

**15. Which OCR?** RapidOCR — PP-OCRv4 as ONNX, bundled in the wheel so it works
offline.

**16. Why not Tesseract / Google Vision?** Tesseract is weak on plates; cloud
APIs violate our zero-paid-API, offline-capable constraint.

**17. How do you handle a misread?**
Multi-frame consensus. We vote per character position across all reads of that
vehicle, weighted by OCR confidence × crop quality.

**18. Why not just take the highest-confidence read?**
Because OCR confidence is poorly calibrated. A sharp crop read *wrongly* at 0.95
beats three correct reads at 0.70. We measured this; voting wins.

**19. Two-row plates?**
Handled — PP-OCR's detection stage returns each row as its own line, used as a
fallback when recognition-only produces something implausible.

**20. Why is OCR the expensive part?**
On 4K footage with 20 plate-bearing vehicles per frame, naive OCR was 66% of
total runtime. The plate scheduler and crop gate exist to control it.

### TRACKING

**21. Which tracker?** ByteTrack, pure NumPy, motion-only with a Kalman filter.

**22. What's special about ByteTrack?**
It associates *low*-confidence detections in a second pass. Most trackers discard
those and lose partly-occluded vehicles.

**23. Do you track across cameras?**
Not with the tracker. Track IDs are per camera. Cross-camera linking is by plate.

**24. What is re-identification and do you have it?**
Matching a vehicle by appearance rather than plate. **We do not have it.** It's
the highest-value next step because it would link vehicles whose plates are
unreadable.

### MULTI-CAMERA TRAJECTORY

**25. How do you know it's the same vehicle?**
Normalised plate string equality, then six physics checks to *validate* the link.

**26. What if the plate is misread?**
Then it isn't linked — and that's a real limitation. A one-character misread
creates a separate identity. Our convoy detector partially surfaces this: a
partner plate within one edit is flagged `likely_same_vehicle`.

**27. What if two cars have the same plate (cloned)?**
`impossible_simultaneous` and `implausible_speed` catch it: the same plate in two
places it cannot have travelled between. We flag it, we don't silently merge.

**28. Why 150 km/h?**
Generous for Indian roads. The point isn't to flag fast driving — it's to catch
physically impossible movement.

**29. Why 50 m co-location?**
Below that, implied speed is meaningless: one metre of GPS error over four
seconds is 900 km/h.

**30. Are your distances road distances?**
No — great-circle straight lines. So `distance_km` is always a **lower bound**.
We say so in the API and on screen rather than pretending.

**31. What happens to an implausible leg?**
It's **marked, not deleted**. A system that quietly removes inconvenient parts of
a route isn't one a court should trust.

### BACKEND / DATABASE

**32. Why FastAPI?** Async (we're I/O bound), automatic OpenAPI, Pydantic validation.

**33. Why PostgreSQL and not MongoDB?**
Relational integrity for cameras/alerts/audit, **plus** PostGIS for geospatial
and TimescaleDB for time-series — in one database.

**34. What is a hypertable?**
TimescaleDB partitions a table by time automatically. `detections` uses 1-day
chunks — large enough to be efficient, small enough to drop or compress as a unit.

**35. Why Redis Streams and not a plain queue?**
Consumer groups give at-least-once delivery with redelivery after a crash, and
the stream is capped so a stopped consumer can't fill memory.

**36. How do you avoid losing events if the API restarts?**
Unacknowledged entries are redelivered to the consumer group.

### GIS

**37. Which map library?** MapLibre GL — open source, no API key.

**38. Does it work offline?**
Yes. The default basemap is committed GeoJSON. Satellite imagery is optional,
probed, and falls back automatically.

**39. Geography or Geometry?**
`Geography` — distance in metres on a spheroid. Gujarat spans ~700 km where
planar maths drifts.

### PERFORMANCE / SCALE

**40. How many cameras can one worker handle?**
4 concurrent inference slots on this laptop, with rotation covering more. Memory
(~1.4 GB/slot), not CPU, is the cap.

**41. How do you scale out?**
`--scale ai-worker=N`. Cameras are sharded by stable hash of camera code — no
coordinator, and two workers can never open the same stream.

**42. What's your proven throughput?**
2,774 events/s sustained with zero failures across 80,000 camera identities.
That's the **event tier**, not the AI tier.

**43. Why does the central system not receive video?**
1,000 cameras × 4 Mbps ≈ 4 Gbps of video versus ~0.6 Mbps of events — roughly
7,000×. That ratio is the entire scaling argument.

**44. What would 100,000 cameras cost?**
`scripts/capacity_model.py`: 45,558 cores, 1,139 nodes, ₹49.14 crore capex,
₹10.98 crore/year opex — CPU-only, every constant tagged measured or assumed.

### SECURITY / PRIVACY

**45. How is it secured?**
JWT + argon2, RBAC across six roles declared as a tested matrix, and an audit row
for every plate search, stream open and watchlist change.
**Don't claim** it's production-secure — there's no TLS, no encryption at rest,
no pen test.

**46. This tracks private citizens. What about privacy?**
We built the audit trail from day one precisely because of that. Every lookup is
attributable. A full DPDP-Act compliance review is production work we haven't done.

**47. Can an operator see everything?**
No. An analyst can't view live video; an auditor can't search plates — because an
auditor oversees searches.

### DEPLOYMENT

**48. How long to get it running?**
`make demo` then `make ai`. Target under 5 minutes from a clean clone.

**49. Any cloud dependency?**
One optional one: Esri satellite imagery, probed and with automatic fallback.
Everything else is local.

### LIMITATIONS / FUTURE

**50. What's the weakest part?**
That accuracy is unmeasured on real footage, and that linking depends entirely on
reading the plate.

**51. What would you do next with a month?**
(1) Label real Indian footage and measure honestly. (2) Re-export the detector at
1280 to fix recall. (3) Appearance re-identification. (4) Finish the analytics UI.

**52. Is the anomaly detection working?**
No. We have a physics filter that catches impossible movement. Learned
route-normality is not built. I'd rather tell you that than show you a number we
invented.

---

# PART 43 — RAPID-FIRE

| Q | A |
|---|---|
| What is ANPR? | Automatic Number Plate Recognition |
| What is OCR? | Converts an image of text into text |
| What is a frame? | One still picture from a video |
| What is FPS? | Frames per second |
| What is RTSP? | The streaming protocol CCTV cameras speak |
| What is WHEP? | WebRTC playback protocol browsers can use |
| What is HLS? | HTTP streaming; our fallback via hls.js |
| What is detection? | Finding what and where in an image |
| What is tracking? | Keeping the same identity across frames |
| What is re-identification? | Matching by appearance across cameras |
| Do we have re-ID? | **No** |
| What is a bounding box? | Rectangle around an object |
| What is confidence? | The model's own certainty, 0–1 |
| What is NMS? | Removes duplicate overlapping boxes |
| What is YOLO? | A one-pass object detection model family |
| Which YOLO? | YOLOv8n for vehicles, YOLO11n for plates |
| What is ONNX? | A portable model format |
| What is ONNX Runtime? | The engine that runs ONNX models |
| What is ByteTrack? | Our tracker; associates low-confidence boxes too |
| What is a Kalman filter? | Predicts where a tracked object goes next |
| What is consensus? | Voting across many reads of one plate |
| Why vote? | OCR confidence is poorly calibrated |
| What is a trajectory? | Ordered where-and-when of a vehicle |
| What is a hop? | One camera-to-camera leg of a journey |
| What is dwell? | Time spent at one camera |
| What is `implied_kmph`? | Distance ÷ time between two cameras |
| Max plausible speed? | 150 km/h |
| Co-location threshold? | 50 m |
| Unobserved gap? | 1 hour |
| What is a watchlist? | Plates of interest |
| What is a near match? | One character different; a lead, not a hit |
| What is an alert? | An automatic notification of a match |
| Alert latency? | ~24 ms to a connected operator |
| What is PostGIS? | Geospatial extension for PostgreSQL |
| What is TimescaleDB? | Time-series extension |
| What is a hypertable? | Auto time-partitioned table |
| What is `time_bucket`? | Groups rows into time intervals |
| What is Redis Streams? | Durable ordered event log |
| What is a consumer group? | Lets consumers share a stream with redelivery |
| What is MinIO? | S3-compatible object storage |
| Why not put crops on the bus? | ~10× the event traffic |
| What is a presigned URL? | A time-limited link that carries its own auth |
| Crop URL lifetime? | 300 seconds |
| What is MediaMTX? | Our video gateway |
| What is a WebSocket? | A two-way always-open browser connection |
| What is JWT? | A signed token proving who you are |
| What is argon2? | A slow, strong password hash |
| What is RBAC? | Permissions by role |
| How many roles? | Six |
| What is an audit log? | A record of who did what |
| What is Docker Compose? | Runs many containers together |
| How many services? | 9 (+ ai-worker behind a profile) |
| What is inference? | Running a trained model on new data |
| What are model weights? | The learned numbers inside a model |
| Do we use a GPU? | No — CPU only |
| Total cameras? | 12 |
| Total corridors? | 4 |
| Plate yield? | ~38% of detections carry a plate |

---

# PART 44 — GLOSSARY

**AI** — software that performs tasks needing human-like judgment.
**ML** — programs that learn patterns from data rather than following written rules.
**Computer Vision** — teaching computers to interpret images.
**Object Detection** — finding *what* and *where* in an image.
**YOLO** — "You Only Look Once", a fast one-pass detector family.
**Bounding Box** — four numbers describing a rectangle around an object.
**Confidence** — the model's own certainty, 0 to 1. Not a probability of truth.
**Inference** — running a trained model on new input.
**Model Weights** — the learned numbers that make a model work.
**ONNX** — a portable format so a model can run outside the framework that trained it.
**Tracking** — keeping the same ID on one object across frames.
**Re-identification** — matching an object by *appearance* across cameras. *We don't have this.*
**Embedding** — a numerical description of appearance. Two similar vehicles have similar numbers.
**ANPR** — Automatic Number Plate Recognition.
**OCR** — turning an image of text into text.
**Consensus** — combining many noisy reads into one answer by voting.
**Frame / FPS** — one picture; how many per second.
**Latency** — how long from input to result. **p90** = 90% were faster than this.
**RTSP** — the CCTV streaming protocol.
**API / REST** — a defined way for programs to talk over HTTP.
**WebSocket** — a persistent two-way connection for pushing updates.
**Database** — durable, queryable storage.
**PostgreSQL** — our relational database.
**PostGIS** — adds geography and distance maths.
**TimescaleDB** — adds time-series partitioning.
**Hypertable** — a table auto-partitioned by time.
**GIS** — systems for reasoning about location.
**Event** — a structured record that something happened.
**Queue / Stream** — a buffer decoupling producer from consumer.
**Cache** — fast temporary storage.
**Docker / Container** — packaging an app with its dependencies so it runs anywhere.
**GPU** — a processor good at parallel maths. *We don't use one.*
**Geospatial distance** — distance on the Earth's curved surface.
**Trajectory** — the ordered path of a vehicle through space and time.
**Watchlist** — plates flagged for attention.
**Alert** — an automatic notification.
**Hop** — one camera-to-camera leg of a journey.
**Dwell** — time spent stationary at one camera.

---

# PART 45 — TOP 20 THINGS TO MEMORISE

1. **The problem:** cameras are isolated; nobody can follow a vehicle across them.
2. **One-liner:** cameras tell us what they see; we connect observations into journeys.
3. **The differentiator:** everything *after* identification — link, understand, alert.
4. **The pipeline:** detect → track → plate → OCR → consensus → event.
5. **Detector:** YOLOv8n ONNX, 640px fixed, CPU.
6. **Tracker:** ByteTrack, motion-only, per camera.
7. **OCR:** RapidOCR PP-OCRv4, **recognition-first** (18× cheaper than detect-first).
8. **Consensus:** per-character voting weighted by confidence × crop quality — because OCR confidence is poorly calibrated.
9. **Linking:** plate string equality. **Not** appearance. Say this honestly.
10. **Validation:** six physics flags; 150 km/h ceiling; 50 m co-location; flagged, never deleted.
11. **Journey:** hops, distance (**straight lines — a lower bound**), duration, average + moving speed.
12. **Database:** PostgreSQL + PostGIS + TimescaleDB; `detections` is a hypertable.
13. **Bus:** Redis Streams with consumer groups; events, never video.
14. **The scaling argument:** ~7,000× less bandwidth than centralising video.
15. **Alerts:** watchlist exact + one-edit near match, priority-capped, ~24 ms to browser, with a real plate photograph.
16. **Security:** JWT + argon2 + RBAC(6 roles) + audit on every sensitive action. **No TLS** — say so.
17. **Performance:** ~9 fps, p90 266 ms; event tier proven at 2,774 events/s.
18. **Accuracy:** **not measured on real footage.** Bottleneck is recall, not OCR. Never claim 90%.
19. **Not built:** anomaly detection, fuzzy search, re-ID, attribute search, prediction, analytics UI.
20. **Fleet today:** 12 cameras, 4 Ahmedabad corridors — not 51.

---

# PART 46 — EXPLAIN MY PROJECT TO ME

## 2 minutes

A city has hundreds of CCTV cameras, and each one only knows what it saw. If you
want to know where a particular car went, someone has to watch hours of footage
from dozens of cameras by hand.

We built a system that does it automatically. Cameras stream video to our
software. For each camera, an AI program watches the video and does four things:
it finds vehicles, it follows each vehicle from frame to frame, it locates the
number plate, and it reads the plate.

Because a single frame can be blurry, we read the plate many times as the car
crosses the view and then **vote** — letter by letter — to decide the final
answer. That voting is why we're more reliable than reading one frame.

Each vehicle becomes a small record: plate, camera, time, confidence, and a
photograph of the plate. Those records go into a database.

Then the interesting part: our correlator looks for the same plate across
different cameras and stitches those sightings into a **journey** — camera A at
10:00, camera B at 10:08, camera C at 10:15, 5.3 km, 15 minutes. It checks the
physics and marks anything impossible. An operator types a plate and watches the
journey animate on a map.

On top of that we run a watchlist: if a flagged plate appears, an alert reaches
the operator's screen in about 24 milliseconds with the plate photograph attached.

## 5 minutes

*(everything above, plus)*

**The architecture decision that matters.** Cameras are already deployed and come
from different vendors. Rather than replacing them, we *federate*: existing
recorders keep their video, and we run the AI **at the edge**, near the cameras.
Only small event records travel to the central system. For a thousand cameras
that's roughly 4 gigabits per second of video versus about 0.6 megabits per
second of events — around seven thousand times less. That ratio is why a
city-scale deployment is affordable at all.

**The AI stack.** YOLOv8n finds vehicles. ByteTrack follows them — and its trick
is that it also considers *low*-confidence detections, so a car passing behind a
pole isn't lost. A second YOLO finds the plate inside the vehicle crop, and
RapidOCR reads it.

One optimisation worth knowing: the OCR library normally locates text and then
reads it. We do the opposite — we read first — because our plate detector has
*already* found the plate, and asking a text detector to find it again measured
**18× slower for the identical answer**.

**Cost control.** We don't run plate search on every vehicle on every frame. A
scheduler decides when a vehicle has moved enough to be worth another look, and a
crop gate decides whether a new crop is actually new evidence or just the same
plate again.

**The data.** PostgreSQL with two extensions: PostGIS for geography — so distance
between cameras is real metres on a sphere — and TimescaleDB, which
auto-partitions the detections table by day.

**Honesty by design.** Where we can't compute something, the system says so. Our
analytics endpoint returns "insufficient history" instead of a plausible-looking
number. Journey legs that fail physics are *marked*, not deleted. We'd rather
show a judge a system that knows what it doesn't know.

## 10 minutes

*(everything above, plus these five points — this is the version for a technical judge)*

**1. What we did NOT build, and why that matters.**
Cross-camera linking is plate-string equality plus a physics filter. We do not
have appearance-based re-identification, so a vehicle whose plate cannot be read
is not linked. We have no learned model of "normal routes", so we have a
plausibility filter, not an anomaly detector. And our accuracy has not been
measured on real-world footage — we have 62.5–87.5% end-to-end on synthetic
clips, and the bottleneck is **recall** (the pipeline declines to read 3 of 8
plates) rather than OCR correctness. I'd rather state that than quote 90%.

**2. The consensus argument in full.**
Taking the highest-confidence read is the obvious approach and it's measurably
worse, because OCR confidence is poorly calibrated: a sharp crop read *wrongly*
at 0.95 beats three correct reads at 0.70. So we vote per character position,
weighting each read by OCR confidence × crop quality. Position 8 might see '3'
three times at high weight and '8' once at low weight — consensus takes '3' and
marks position 8 as the least certain character. Nothing is discarded: the
disagreement itself is evidence that the plate was difficult, and that crop is
exactly what a future fine-tune should train on.

**3. Trajectory validation.**
Six checks, each with a reason. `co_located` below 50 m — because one metre of
GPS error over four seconds implies 900 km/h. `implausible_speed` above 150 km/h
— generous for Indian roads, because the point isn't to catch fast driving but
the same plate being in two places it couldn't have travelled between, which is
what a cloned plate or a misread looks like. `unobserved_gap` beyond an hour —
not impossible, but it bounds what the route can honestly claim. Everything is
marked rather than removed.

**4. The performance work.**
Our AI worker was taking 866% CPU and 168 OS threads on a 10-core machine, which
starved the browser and made cameras appear to hang. Three causes: nothing
divided the ONNX thread budget by the number of cameras; rotating cameras through
inference slots rebuilt the models each time and leaked a native thread pool; and
a quarter of our cameras were publishing 4K60 video that the detector immediately
downscaled to 640px anyway. Fixing those took detections from 79 to 202 per
minute *while lowering CPU*. The measurement that settles it: the OCR library's
default threading burned 132 ms of CPU per call to return a **slower** answer than
a 2-thread setting that used 23 ms.

**5. Security posture, stated honestly.**
JWT with argon2, six RBAC roles declared as a single tested matrix, and an audit
row for every plate search, every stream open and every watchlist change — built
in from the start, because this platform tracks the movement of private vehicles
and traceability of who looked at what is the right thing to build. What we do
**not** have: TLS, encryption at rest, MFA, a penetration test, or a DPDP-Act
compliance review. Those are production work, and I won't claim them.

---

# IMPLEMENTATION MATRIX

| Component | Status | Technology | Path | Input | Output |
|---|---|---|---|---|---|
| Camera simulator | ✅ | ffmpeg + FastAPI | `services/simulator/` | clips | RTSP |
| Video gateway | ✅ | MediaMTX | `infra/mediamtx/` | RTSP | WHEP/HLS |
| Stream reader | ✅ | OpenCV/FFmpeg | `ailab/stream/reader.py` | RTSP | frames |
| Vehicle detection | ✅ | YOLOv8n ONNX | `ailab/detect/yolo_onnx.py` | frame | boxes |
| Tracking | ✅ | ByteTrack | `ailab/track/bytetrack.py` | boxes | track IDs |
| Track merge | ✅ | plate+time | `ailab/track/merge.py` | tracks | vehicles |
| Plate scheduling | ✅ | heuristics | `ailab/plate/scheduler.py` | track state | search? |
| Plate detection | ✅ | YOLO11n ONNX | `ailab/plate/yolo_onnx.py` | vehicle crop | plate box |
| Crop gate | ✅ | sharpness+phash | `ailab/plate/scheduler.py` | crop | read? |
| OCR | ✅ | RapidOCR PP-OCRv4 | `ailab/ocr/rapid.py` | plate crop | text+conf |
| Consensus | ✅ | per-char voting | `ailab/aggregate/consensus.py` | reads | final plate |
| Grammar | ✅ | regex/format | `ailab/aggregate/grammar.py` | text | valid? |
| Thread budget | ✅ | custom | `ailab/runtime.py` | camera count | threads |
| Worker supervision | ✅ | asyncio+threads | `ai_worker/worker.py` | registry | events |
| Pipeline pool | ✅ | custom | `ai_worker/pipeline_pool.py` | config | Pipeline |
| Crop upload | ✅ | MinIO SDK | `ai_worker/crops.py` | image | object key |
| Event bus | ✅ | Redis Streams | `app/services/event_bus.py` | events | stream |
| Event consumer | ✅ | redis.asyncio | `app/services/event_consumer.py` | stream | DB rows |
| Watchlist match | ✅ | in-memory index | `app/services/watchlist.py` | plate | Match |
| Alert fanout | ✅ | pub/sub | `app/services/alert_fanout.py` | alert | WS |
| **Correlator** | ✅ | PostGIS + Python | `app/services/correlator.py` | plate | Route |
| Route history | ✅ | snapshots | `app/services/route_history.py` | Route | `vehicle_tracks` |
| Evidence URLs | ✅ | botocore SigV4 | `app/services/evidence.py` | key | presigned URL |
| Health monitor | ✅ | probes | `app/services/health_monitor.py` | cameras | `camera_health` |
| Analytics API | ✅ | Timescale+JSONB | `app/routers/analytics.py` | window | aggregates |
| Analytics UI | 🔴 | — | — | — | — |
| Plate search | 🟡 | SQL exact/prefix | `app/routers/detections.py` | plate | sightings |
| Fuzzy search | 🔴 | — | — | — | — |
| Anomaly detection | 🔴 | — | — | — | — |
| Re-identification | 🔴 | — | — | — | — |
| Attribute search | 🔴 | — | — | — | — |
| Prediction | 🔴 | — | — | — | — |
| Auth/RBAC | ✅ | JWT+argon2 | `app/core/rbac.py` | credentials | token |
| Audit | ✅ | middleware+explicit | `app/services/audit.py` | action | `audit_log` |
| Map | ✅ | MapLibre | `components/CameraMap.tsx` | GeoJSON | map |
| Journey playback | ✅ | custom | `lib/journey.ts` | Route | positions |
| Realtime UI | ✅ | WebSocket | `hooks/useEventStream.tsx` | WS | events |

---

# ARCHITECTURE TRUTH TABLE

| Feature | Designed | Implemented | Working | Demo Ready | Production Ready |
|---|---|---|---|---|---|
| Multi-camera ingestion | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Vehicle detection | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Tracking | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Plate detection | ✅ | ✅ | ✅ | ✅ | 🟡 |
| OCR | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Consensus | ✅ | ✅ | ✅ | ✅ | ✅ |
| Evidence crops | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Cross-camera linking | ✅ | ✅ | ✅ | ✅ | ❌ |
| Trajectory + validation | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Journey playback | ✅ | ✅ | ✅ | ✅ | ✅ |
| Plate search (exact/prefix) | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Fuzzy search | ✅ | ❌ | ❌ | ❌ | ❌ |
| Watchlist alerts | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Convoy detection | ✅ | ✅ | ✅ | ✅ | 🟡 |
| GIS map | ✅ | ✅ | ✅ | ✅ | ✅ |
| Camera health | ✅ | ✅ | ✅ | ✅ | ✅ |
| Traffic analytics (API) | ✅ | ✅ | 🟡 | ❌ | ❌ |
| Traffic analytics (UI) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Anomaly detection | ✅ | ❌ | ❌ | ❌ | ❌ |
| Explainable alerts | ✅ | ❌ | ❌ | ❌ | ❌ |
| Re-identification | ✅ | ❌ | ❌ | ❌ | ❌ |
| Predictive traffic | ✅ | ❌ | ❌ | ❌ | ❌ |
| Auth/RBAC/audit | ✅ | ✅ | ✅ | ✅ | 🟡 |
| Horizontal scaling | ✅ | ✅ | 🟡 | ✅ | ❌ |
| TLS / encryption | ✅ | ❌ | ❌ | ❌ | ❌ |

---

# FINAL SCORECARD

| Area | Score | Reason |
|---|---|---|
| Problem alignment | **7/10** | Core journey-tracking nailed; analytics UI, anomaly and fuzzy search missing |
| AI/ML | **6/10** | Solid engineering around pretrained models; no training, no custom model, accuracy unmeasured |
| ANPR | **7/10** | Consensus is genuinely good; recall is the weak point and it's known |
| Tracking | **6/10** | ByteTrack works; fragmentation unresolved; no re-ID |
| Multi-camera trajectory | **8/10** | The strongest part — real linking, honest validation, animated playback |
| GIS | **8/10** | Offline-capable, PostGIS-backed, no API keys |
| Analytics | **3/10** | API built today; **no UI at all**; speed unmeasurable on replay |
| Backend | **8/10** | Clean async architecture, 410 tests, real migrations |
| Frontend | **7/10** | Every screen live data, typed, tested; one page missing |
| Scalability | **6/10** | Event tier proven at 2,774/s; AI tier untested beyond one laptop |
| Reliability | **7/10** | Good supervision and degradation; OOM history now fixed |
| Security | **5/10** | Auth/RBAC/audit genuinely good; **no TLS, no encryption at rest, no pen test** |
| Demo readiness | **7/10** | 7 of 8 PS demo steps work; step 8 has no screen |
| Innovation | **6/10** | The consensus and OCR-ordering decisions are real; the rest is solid integration |

### Biggest weaknesses

1. **Accuracy is unmeasured on real footage** — the headline PS requirement.
2. **Analytics has no UI** — a whole PS demo step cannot be shown.
3. **No anomaly detection** — another explicit PS requirement.
4. **Linking depends entirely on reading the plate.**

### Highest-value improvements, in order

1. Build `Analytics.tsx` — the backend already exists; this is the cheapest large win.
2. Label real Indian footage and publish a measured accuracy number, whatever it says.
3. Re-export the detector at 1280px to attack recall.
4. Add `alerts.reasons` + a journey-level anomaly score with named factors.
5. Appearance re-identification.

---

*Generated 11 September 2026 from commit `dc77c47` on branch
`feat/p4-traffic-analytics`. Every claim traced to a file. Where code and docs
disagreed, the code won.*
