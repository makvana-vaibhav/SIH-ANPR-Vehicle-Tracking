# High-Level Design

NagarNetra is a **federation platform** for CCTV that already exists across
Gujarat's departments and vendors. ANPR is one capability inside it, not the
product.

The whole design follows from one sentence:

> **The central tier carries events, not video.**

---

## 1. The architectural choice

The challenge names five reference models. Model 1 (Centralised CCTV Registry &
GIS Foundation) is compulsory and must be combined with another (FAQ Q12).

| Model | What it means | Why not, alone |
|---|---|---|
| 1 — Registry + GIS | Know every camera, place it on a map | Compulsory, but knows *about* cameras without seeing through them |
| 2 — Unified viewing | One pane of glass over every feed | Centralising 80,000 live streams is ~320 Gbps. Not a budget problem — a physics problem |
| 3 — Federation middleware | Existing VMS stay authoritative; ingest metadata, pull streams on demand | Strong, but on its own gives no registry and no map |
| 4 — Central VMS | Replace departmental VMS with one system | Politically and financially unrealistic. Gujarat already runs Milestone, Genetec, CP Plus and Hikvision deployments that departments own and budget for |
| 5 — Hybrid | 1 + 3 + selective 2 | **Chosen** |

### Why Model 5

Ripping out working departmental VMS (Model 4) asks each department to abandon
a system it owns, has trained on, and has a support contract for. That is the
kind of proposal that is agreed in a meeting and never implemented.

So we **federate**. Existing VMS stay authoritative for their own video. We:

* ingest **metadata** centrally — every camera, its position, its health;
* run AI **at the edge**, next to the video, so only events travel;
* pull **streams on demand**, and only for the handful somebody is actually
  watching.

### The arithmetic that decides it

| | Centralised video (Model 2/4) | Federated events (Model 5) |
|---|---|---|
| 80,000 cameras × 4 Mbps H.264 | **320 Gbps** sustained to the centre | — |
| 80,000 cameras × 1 event / 30 s × ~2 KB | — | 2,667 events/s ≈ **43 Mbps** |
| Ratio | | **~7,000× less** |

Plus on-demand pull: if 50 operators each watch one feed, that is 50 × 4 Mbps
= 200 Mbps, not 320 Gbps.

*Status: the 43 Mbps figure is arithmetic — it multiplies a measured event size
by an assumed camera count. What is now **measured** is that the platform
absorbs that event rate: 2,774 events/s sustained across 80,000 camera
identities, with zero failures. See §7.*

---

## 2. Context

```
   Departmental VMS                        Operators
   (Milestone, Genetec,                    (control room,
    CP Plus, Hikvision)                     district offices)
          │                                      │
          │ RTSP / ONVIF / vendor REST           │ HTTPS + WebSocket
          ▼                                      ▼
   ┌──────────────────────────────────────────────────────┐
   │                    NagarNetra                        │
   │                                                      │
   │   Integration ──▶ Stream gateway ──▶ AI workers      │
   │        │                                   │         │
   │        ▼                                   ▼         │
   │     Registry ◀────── Event bus ◀───── Detections     │
   │     + GIS                │                           │
   │                          ▼                           │
   │              Watchlist · Alerts · Correlator          │
   └──────────────────────────────────────────────────────┘
          │
          ▼
   The organisers' sandbox grid  (federated, consume-only)
```

## 3. Containers

Eleven services, all pinned, all arm64-native, brought up by one
`docker compose up`.

| Service | Image | Responsibility |
|---|---|---|
| `postgres` | `timescale/timescaledb-ha:pg16.14-ts2.29.2-all` | Registry, detections (hypertable), alerts, audit. PostGIS **and** TimescaleDB in one image |
| `redis` | `redis:7.4.11-alpine` | Event bus (Streams + consumer groups), token revocation, rate-limit counters, fleet roster |
| `api` | FastAPI / Python 3.11 | Registry, auth, RBAC, watchlist, alerts, correlator, audit, WebSocket |
| `ingest` | same image, different command | Camera health monitoring |
| `ai-worker` | ONNX Runtime + OpenCV | The vision pipeline. Torch-free, ~400 MB |
| `mediamtx` | `bluenviron/mediamtx:1.20.1` | RTSP in → WebRTC (WHEP) / HLS out, on-demand |
| `web` | nginx + React build | Command centre; single origin |
| `minio` | S3-compatible | Plate crops, snapshots |
| `opensearch` | `2.19.6` | Search index (Phase 8) |
| `simulator` | same image as api | Demonstration feed only |

### Why the AI runs where it does

The worker is a **separate container from the API on purpose**. Inference is
CPU-bound and bursty; serving an operator's alert screen is latency-sensitive.
Sharing a process would mean a busy camera making the control room slow.

At scale the same container runs at the edge — in a district data centre next
to the cameras — and only its events cross the WAN. Nothing about the code
changes; it is the same image with a different `AI_WORKER_CAMERAS`.

---

## 4. The data path

```
camera ──RTSP──▶ reader (drop-to-latest, PTS-driven)
                     │
                     ▼
              YOLO vehicle detect ──▶ ByteTrack
                     │
                     ▼            selective inference
              plate detect ◀── PlateScheduler + CropGate
                     │
                     ▼
              crop conditioning ──▶ OCR
                     │
                     ▼
              multi-frame consensus ──▶ track merge
                     │
                     ▼
              vehicle.completed ──▶ Redis Stream
                                        │
                                        ▼
                          API consumer ──┬──▶ detections (hypertable)
                                         ├──▶ watchlist match ──▶ alert
                                         └──▶ /ws/events (operators)
```

### Decisions worth naming

**Selective inference, not frame skipping.** A vehicle is searched for a plate
when its view has *materially changed*, and a crop is read when it is *new
evidence*. Plate detection fell from 13.3 calls per frame to 1.45; OCR from
14.2 to 0.7. Blind frame-skipping loses vehicles; this loses redundant work.

**Multi-frame consensus.** Character-position voting weighted by OCR confidence
× crop quality, because a recogniser handed an unreadable crop returns a
*confident wrong answer* rather than an error. One event per vehicle, carrying
`frames_agreeing / frames_total` and the runners-up so an operator can overrule
it.

**Grammar repair is capped.** Position-aware confusion correction fits the
string to a plate template, but a "correction" that changes more than two
characters is proposing a different plate, not repairing a misread. Above that
cap the reading is returned untouched and flagged.

**The bus is an interface.** `RedisStreamBus` and `RedpandaBus` implement the
same protocol, so the scale profile is a config swap rather than a rewrite.

---

## 5. Failure modes

Designed-for, not hypothetical.

| Failure | Behaviour |
|---|---|
| A camera stops publishing | Reader reconnects with exponential backoff 2 s → 30 s. Health monitor flips status and raises `camera_down` |
| A federated gateway returns 502 | Cameras stay in the registry marked unreachable. They are *not* removed — unreachable is a problem, not an absence |
| The stream loops / scene cuts | PTS discontinuity detected; long-lived tracker state is rebuilt rather than carried across the cut |
| More cameras than inference slots | **Rotation**, not truncation. Each camera holds a slot for a bounded slice, and the coverage gap is *stated* (`31 cameras across 3 slots, each watched 45 s every 11.2 min`) rather than hidden |
| Redis unreachable | Rate limiter fails open and logs. Event publishing retries; the worker keeps its last roster |
| API restarts | Workers keep running off the cached roster; the Redis stream retains events for the consumer group to pick up |
| OpenSearch down | Search falls back to Postgres `pg_trgm` at runtime |
| WebRTC blocked | Player falls back to HLS automatically |
| Satellite tiles unreachable | Map falls back to the committed GeoJSON basemap |

---

## 6. Measured performance

Everything here was measured on the development machine — **Apple Silicon,
10 cores, 16 GB, CPU only**. No GPU number is claimed anywhere in this
repository because no GPU has ever executed this code.

### Pipeline, 4K source

| Configuration | ms/frame | fps |
|---|---|---|
| Naive baseline | 7,665 | 0.130 |
| + selective inference | 1,151 | 0.869 |
| + diagnostics off (`bench`) | 360 | 2.78 |

### Pipeline, 720p

**4.39 fps** per worker — roughly one camera per spare core.

### Live stream

| | |
|---|---|
| Capture-to-event latency, median | **385 ms** |
| p90 / p99 | 493 ms / 559 ms |
| Frames dropped under load | ~70% — deliberate: latency stays bounded rather than growing |

### End to end

| | |
|---|---|
| Detection → alert on a connected WebSocket | **24 ms** (budget: 2 s) |
| Killed stream → camera marked offline | **19 s** (budget: 30 s) |

### ANPR accuracy — read this carefully

| Source | Result |
|---|---|
| Generated plates with ground truth | 100% exact match, CER 0.000 |
| Real footage (`anpr_demo.mp4`, no ground truth) | 12 of 13 plates resolve to a valid format at 0.74–0.95 confidence |
| **The organisers' grid** | Occasional. `GJ11CO5913` at 0.92, grammar-valid, in daylight |

The 100% figure is **optimistic by construction** — rendered plates have no
embossing, dirt, motion blur or regional fonts.

The grid figure is the honest one and it moves with the light. Sampled at
night, it read **zero** plates: headlight glare, vehicles facing away, and
junction overviews rather than lane-facing cameras. In daylight it reads plates
occasionally — roughly one valid plate per hundred detections. Detection,
tracking, health and events work against it at any hour; ANPR does not.

It also reads **signage** as plates: `DELIGHT` at 0.99 confidence from a camera
called Delight. Grammar marks it invalid so it never reaches the watchlist,
which is precisely why the alert path is gated on `grammar_valid` and not on
confidence.

---

## 7. Scaling to 80,000 cameras

### What the architecture does

* **Edge inference.** Workers run beside the cameras; events cross the WAN.
* **Stateless workers, hash-sharded.** Ownership is a stable hash of the camera
  code, so N workers split the fleet with no coordinator. Adding a worker
  rebalances; losing one leaves its cameras unclaimed *visibly*.
* **Rotation within a worker.** More cameras than slots is normal, and coverage
  becomes partial in **time** (measurable) rather than partial in **space**
  (invisible).
* **Batched persistence.** `COPY` for detections, bulk indexing for search.
* **The bus is swappable** — Redis Streams for one node, Redpanda for many.

### Sizing, extrapolated from measured CPU throughput

At 4.39 fps per 720p stream and one analysed frame every 2 s per camera, one
CPU core sustains roughly **8 cameras**. 80,000 cameras ≈ 10,000 cores ≈
**250 nodes at 40 cores**, before any GPU.

**This is extrapolation, not measurement.** A single mid-range GPU would change
it by an order of magnitude, and this repository has never run one.

### The load test, and what it measured

**Run.** `make load` — 80,000 camera identities, three ingest workers, one
laptop, CPU only. The generator replays AI-tier *output*; no inference happens
in the measurement, because no laptop can produce 2,667 events/s of real ANPR.

| | Measured |
|---|---|
| Offered rate | 2,664 events/s |
| **Ingest sustained (median)** | **2,774 events/s** |
| Range across the run | 2,280 – 4,002 events/s |
| Events consumed / detections written | 388,748 / 388,713 |
| **Failures** | **0** |
| Watchlist alerts raised under load | 397 |
| Backlog peak → after the generator stopped | 10,237 → **cleared** |
| Capture → persisted, p50 / p95 / p99 | 4,812 / 5,670 / 5,904 ms |

**The throughput target is met; the latency target is not.** Phase 10's gate
asked for p95 under 3 s and the measurement is 5.7 s. The reason is visible in
the numbers above: a backlog of up to ten thousand events forms, so most of
that latency is queue wait rather than processing. It clears completely once
the offered rate stops. On a laptop sharing ten cores between the generator,
three workers, Postgres, Redis and the API, that is the expected shape; the
fix in a real deployment is more workers, which the consumer group supports
without configuration.

### Two bugs this test found, which nothing else did

**A case-insensitive lookup was defeating a database index.** Every ingested
detection resolves a camera *code* to a camera *id*, deliberately
case-insensitively — MediaMTX reports stream paths lowercased while the
registry stores canonical uppercase. But `upper(camera_code) = ...` cannot use
a plain index on `camera_code`. At the 281 cameras this repository develops
against, the resulting sequential scan is sub-millisecond and invisible. At
80,000 it was **106 ms per lookup, 80,278 rows discarded each time**, and it
collapsed ingest from ~1,500 events/s to 140. Fixed by migration `0003`, an
expression index. Throughput recovered 7.5×.

**Two API replicas would have split the live event feed between them.** One
class both persisted events and fanned them out to operators, and it did both
through a Redis *consumer group* — which delivers each entry to exactly one
member. That is correct for persistence and exactly wrong for a shared
operations picture: two replicas would have shown each half of their operators
half the state's traffic, with nothing anywhere reporting a fault. The platform
was correct only while exactly one API process ran, and nobody had written that
down. Live fan-out (`EventTailer`, a plain `XREAD`) is now separate from
durable consumption (`EventConsumer`, the group), and alerts travel on their
own pub/sub channel so one raised by any worker reaches operators on every
replica.

Both are the reason to run a load test at all. A load test that finds nothing
was not a load test.

---

## 8. Deployment

```
        ┌───────────── District data centre ─────────────┐
        │  cameras ──▶ ai-worker ──▶ (events only) ──────┼──┐
        └────────────────────────────────────────────────┘  │
                                                            │  WAN
        ┌───────────── State command centre ─────────────┐  │  ~43 Mbps
        │  Redpanda ◀───────────────────────────────────┼──┘
        │      │                                        │
        │      ▼                                        │
        │  API replicas ──▶ Postgres/Timescale          │
        │      │            OpenSearch, MinIO           │
        │      ▼                                        │
        │  nginx ──▶ operators                          │
        └───────────────────────────────────────────────┘
```

On one laptop the same topology collapses into `docker compose up`, which is
what makes the demo possible and the architecture reviewable at the same time.

---

## 9. Stack deviations

Recorded in full in `CLAUDE.md` §9. The three that affect this design:

1. **ONNX CRNN instead of PaddleOCR** — PaddlePaddle ships no aarch64 Linux
   wheel. The OCR engine sits behind an interface; an amd64 deployment can swap
   back with one env var.
2. **`timescale/timescaledb-ha` instead of `postgis/postgis`** — the PostGIS
   image has no arm64 build. This one carries both extensions.
3. **Torch-free runtime.** Ultralytics (AGPL-3.0) exports to ONNX at build time
   in a throwaway image; the shipped worker carries ONNX Runtime and OpenCV
   only. Cuts ~2.1 GB and keeps AGPL code out of the deployed artifact.

---

## 10. Related documents

* [`SECURITY.md`](SECURITY.md) — controls, and what is not implemented
* [`INFRASTRUCTURE.md`](INFRASTRUCTURE.md) — bandwidth, storage, DR
* [`REQUIREMENTS.md`](REQUIREMENTS.md) — challenge compliance map
* [`STATUS.md`](STATUS.md) — real vs simulated, explained
* [`../BUILD_STATE.md`](../BUILD_STATE.md) — per-phase state and every known gap
* [`../ai-lab/PERFORMANCE.md`](../ai-lab/PERFORMANCE.md) — the profiling behind §6
