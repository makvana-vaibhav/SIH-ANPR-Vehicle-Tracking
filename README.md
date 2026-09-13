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
| `make ai` | Start the AI worker (the `ai` compose profile — **required** for ANPR) |
| `make scale` | Bring up the horizontally-scaled ingest profile |
| `make load` | Run the k6 load test against it |
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
commodity hardware and a metro deployment run at all. Full derivation is in
[docs/HLD.md](docs/HLD.md).

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

## Model optimization

The pipeline was, at one point, the reason the demo did not work — and the fix was
not a smaller model. It was arithmetic about threads.

### The bug

Every inference session sized its own thread pool as if it owned the machine, and
nothing divided that by the number of cameras. One `Pipeline` runs per camera on
its own thread; each builds **five** ONNX sessions (vehicle detector, plate
detector, and OCR's detect/classify/recognise trio). On a 10-core host at three
cameras:

```
3 cameras × 3 OCR sessions × 10 threads  = 90
3 cameras × 2 YOLO sessions ×  4 threads = 24
OpenCV's own pool, per camera thread     = 10
                                          ---
                                          124+ threads on 10 cores
```

**Measured: 168 OS threads, 866% CPU, load average 18.8.** The AI worker alone took
~8.7 of 10 cores, starving MediaMTX, the browser and the compositor. The symptom
presented as "camera tiles won't load".

### Why oversubscription was not even a trade

It was not latency-for-throughput. Measured on this host, OCR recognition on one
plate crop:

| Threads | Wall/call | CPU/call | Cores used |
|---|---|---|---|
| `intra_op=2` | 11.8 ms | 23.6 ms | 2.0× |
| `intra_op=4` | **8.0 ms** | 32.0 ms | 4.0× |
| ONNX Runtime default (all cores) | 14.3 ms | 132.6 ms | 9.3× |

The default burns **5.6× the CPU of two threads to return a slower answer**. These
are small models — past a handful of threads the convolutions spend longer
synchronising than computing. The detector shows the same shape: YOLOv8n at 640px
runs 275 ms at 1 thread, **126 ms at 4**, 194 ms at 6, 331 ms at 8.

### The rule

`ai-lab/ailab/runtime.py` owns one budget for the whole process and divides it by
the number of pipelines sharing it:

```
budget       = cores(affinity-aware) − 2 reserved
per-pipeline = clamp(budget ÷ concurrent_pipelines, 1, 4)
```

- **Affinity-aware.** `sched_getaffinity`, not `cpu_count` — a container pinned to
  a CPU subset still reports the host's total, and sizing a pool to cores you may
  not use is how oversubscription starts.
- **Two threads reserved** for RTSP decode, the media gateway, the event sink and
  the browser. Inference that consumes every core makes the product it serves
  unusable.
- **Capped at 4 per model** regardless of free budget, per the table above.
- **The divisor is the camera count, not the session count** — the five sessions in
  one pipeline run *sequentially* on that camera's thread, so they never contend
  with each other. Contention is strictly between cameras.

### Result

| | Before | After |
|---|---|---|
| Worker CPU | 866% | **~570%** |
| Detections/min | 79 | **~150** |
| Camera slots | 3 (CPU-bound) | **4** (now memory-bound, ~1.4 GB/slot) |

**Never construct an inference session outside `ailab.runtime`,** and never set
`AILAB_ORT_THREADS` in compose — it bypasses the division. See
[docs/PERFORMANCE.md](docs/PERFORMANCE.md).

### Runtime footprint

Ultralytics (AGPL-3.0) + PyTorch is ~2.5 GB and is used **only at build time**, in a
throwaway tools image, to export ONNX. The shipped `ai-worker` carries
`onnxruntime` + OpenCV only — **~400 MB**. This protects the 5-minute demo
constraint and keeps AGPL code out of the deployed artifact.

**On GPUs:** both CUDA and CoreML paths are wired (`device: auto|cpu|cuda|coreml`)
and **neither is measured here**. There is no accelerator of any kind inside Docker
on Apple Silicon — onnxruntime reports exactly
`['AzureExecutionProvider', 'CPUExecutionProvider']`, verified. Any GPU figure in
these docs is labelled *extrapolated*. See [docs/GPU.md](docs/GPU.md).

---

## Security

This platform tracks the movement of private vehicles. That makes the security
posture part of the product, not paperwork around it.

### Authentication

| Control | Implementation |
|---|---|
| Password hashing | **argon2id**, OWASP-recommended parameters (19 MiB, 2 iterations) — not bcrypt, because it resists GPU cracking far better |
| Access token | JWT, **15 minutes** |
| Refresh token | JWT, **7 days**, revocable — every token carries a `jti`, and logout adds it to a denylist |
| Issuer / audience | Asserted on decode, so a token minted by another system or for another audience cannot be replayed against this API |
| Stream token | **Separate token type, ~2 minute life, scoped to one camera.** A viewing URL that leaks from browser history is useless within minutes and cannot be replayed against a different camera |

`app/core/security.py` is pure crypto — no database, no network. Anything needing
storage (denylists, attempt counters) lives in `app/services/`, which keeps the
security-critical logic in one readable, trivially testable place.

### Authorization and audit

RBAC in `app/core/rbac.py`. Above it, an **audit trail that is enforced and
tested**: every plate search, every camera stream open, and every
watchlist/blacklist mutation writes an `audit_log` row. Middleware covers all
mutating requests; the three named actions also get explicit calls. There is a test
asserting the rows appear.

Traceability of *who looked at what* is both a feature to point at and the right
thing to build into a system like this.

### Rate limiting

`app/core/ratelimit.py` — a fixed-window counter in **Redis**, not in-process.

- **Why Redis:** the scale profile runs multiple API replicas. A per-process counter
  gives each replica its own allowance, so the real limit becomes
  `configured × replicas` and moves whenever you scale. A limit that changes when
  you add capacity is not a limit.
- **Why fixed-window:** a sliding-window log costs a sorted set and a trim per
  request. Against credential guessing and runaway client loops, a fixed window is
  sufficient, and its worst case (2× allowance across a boundary) is not a
  meaningful weakness at these thresholds.

| Scope | Budget |
|---|---|
| General API | **300 requests / 60 s** |
| `/auth/login`, `/auth/refresh`, `/auth/password` | **10 / 300 s** |
| `/health`, `/ready`, `/metrics` | Exempt |

Two decisions worth knowing:

- **On auth paths, only failures are counted.** Counting every attempt looked right
  and was wrong: a control room sits behind one NAT, so ten operators signing in at
  shift change would lock each other out. A successful login is not an attack. What
  is limited is *guessing*, and guessing is failure by definition.
- **It fails open.** If Redis is unreachable the request is allowed, and the failure
  is logged. An operator locked out of the alert screen during an incident because a
  cache is down is a worse outcome than an unthrottled minute.

Health paths are exempt because a throttled container probe reports the service
unhealthy and restarts it — turning a rate limit into an outage.

### Middleware ordering

Starlette runs middleware in reverse registration order. The intended order,
outermost first:

```
CORS  →  rate limit  →  audit  →  route
```

CORS outermost, so a 429 still carries the headers a browser needs to read it. The
rate limiter **outside** the audit middleware, so a flood is rejected before it can
write a row per request — an attacker who can make the platform fill its own audit
table has found a way to destroy the record of what they did.

### Secrets

`vms_instances.credentials_ref` stores a **pointer, never a secret**:

```
vault://nagarnetra/vms/rajkot-milestone
env://RAJKOT_VMS_USERNAME:RAJKOT_VMS_PASSWORD
file:///run/secrets/rajkot_vms
```

The registry is the most valuable table in the system — it lists every camera and
how to reach it. Putting VMS passwords in it would mean one SQL injection
compromises live video across every department. A pointer means an attacker who
reads the whole table still has to separately compromise the secret store.

`.env.example` is committed, `.env` is not. Secrets have **no in-code defaults** —
the API refuses to start rather than sign tokens with a key readable in this
repository.

### Transport

TLS terminates at the edge (`deploy/nginx/`). `X-Forwarded-For` is trusted for
rate-limit keying **only because nginx sets it and nothing else can reach the
service**; exposed directly to the internet that header is caller-controlled and
this would need revisiting. That caveat lives in the code, at the line that trusts
it.

Full model, including retention and lawful-use safeguards:
[docs/SECURITY.md](docs/SECURITY.md).

---

## What it does

The PS defines eight things a city command centre must do. This is where each one
honestly stands — status is per-item, because the interesting part of a hackathon
README is the part that says what is *not* finished.

| # | Capability | State |
|---|---|---|
| 1 | **Live multi-camera detection** | ✅ **51 cameras live** on 12 real Ahmedabad corridors |
| 2 | **ANPR** — plate, confidence, camera, timestamp | ✅ 9 fps, p90 266 ms, 0.70–0.94 confidence |
| 3 | **Multi-camera linking** | 🟡 engine linking real sightings; hop timing not yet plausible |
| 4 | **Vehicle journey** — distance, duration, animated route | ✅ profile + timeline playback on the map |
| 5 | **Vehicle search** | 🟡 fuzzy search code complete (P8), gate not yet run |
| 6 | **Blacklist alerts** — with plate crop | ✅ fires by itself, with the crop |
| 7 | **Trajectory anomalies** — flagged *with reasons* | 🟡 code complete (P5), gate not yet run |
| 8 | **City traffic analytics** | 🟡 code complete (P4), gate not yet run |

Beyond the eight: **predictive traffic** (P10) is code-complete and ungated;
**attribute search** (P9) and **re-identification** (P11) are half built — both need
a vision pipeline for their remaining half.

**[docs/ROADMAP.md](docs/ROADMAP.md) is the plan of record**;
[PROGRESS.md](docs/PROGRESS.md) is the one-page current state. Nothing in this README
describes a screen that does not exist.

### Accuracy — the claim we do not yet make

The PS sets a hard target of **>90% plate recognition under real-world conditions** —
motion blur, night, angle, occlusion, damaged plates.

**We cannot make that claim yet, and we do not.** Accuracy has been measured only on
*synthetic* footage: **62.5–87.5% end to end**, with 100% exact-match on plates the
pipeline chooses to attempt and a character error rate of 0.000. Those numbers are
optimistic by construction — the plates are rendered from a clean font, with no
embossing, dirt or real motion blur.

The interesting part is *where* it falls short. When the pipeline commits to a read
it is almost always right; it declines to read roughly a third of plates. **The
bottleneck is recall, not recognition** — which means a better recogniser is the
wrong fix.

Measurement lives in [`ai-lab/`](ai-lab/), a harness with hard-case mining and a
labelling loop. The figure we publish will be the figure the harness printed, with
its conditions attached.

### Language discipline

The system reports a **trajectory anomaly** — never a "suspicious", "criminal" or
"wanted" vehicle, plate or driver. It reports movement that differs from the norm; a
human decides what that means. An operator shown "SUSPECT" for a lane change stops
trusting the tool.

---

## Design constraints

| Constraint | How it is honoured |
|---|---|
| **One laptop** | `docker compose up`. No cluster, no cloud account, no manual data entry. |
| **Fully offline** | Local models, local database, local map data. No Google Maps, no hosted inference, no paid API anywhere. |
| **CPU must work** | GPU is an accelerator, never a requirement. Device auto-detected, clean CPU fallback. |
| **Degrades, never dies** | Lose WebRTC and video falls back to HLS. Lose a dependency and the readiness endpoint names it rather than failing opaquely. |
| **Computed, never invented** | Every congestion figure, delay and score derives from observed data. Where there is not enough data to compute one, the UI says so rather than showing a plausible number. |
| **Explainable** | Intended: an alert carries the factors that raised it, because `Risk: 87%` alone is a bug. *(P5 adds the `reasons` column — code complete, gate not yet run.)* |
| **Auditable** | Enforced and tested — see [Security](#security). |
| **No secrets in git** | `.env.example` committed, `.env` not. No in-code secret defaults. |

---

## Stack

| Layer | Choice |
|---|---|
| API | FastAPI (Python 3.11), SQLAlchemy 2.0 async, Alembic, Pydantic v2 |
| Database | PostgreSQL 16 + PostGIS 3.4 + TimescaleDB |
| Search | OpenSearch 2.19 (Apache-2.0), Postgres `pg_trgm` fallback |
| Event bus | **Redis 7 Streams with consumer groups.** A `kafka` backend is declared in config but not implemented |
| Object store | MinIO (S3 API) |
| Media gateway | MediaMTX — RTSP in, WebRTC/HLS out |
| AI | YOLO → ONNX (vehicle + plate), ByteTrack, ONNX CRNN OCR, ONNX Runtime |
| Auth | JWT access + refresh, argon2id, RBAC |
| Orchestration | **Docker Compose profiles** (`base`, `ai`, `scale`) + Makefile. No Kubernetes |
| Frontend | React 18 + Vite + TypeScript + Tailwind + shadcn/ui |
| Map | MapLibre GL, offline GeoJSON basemap — no tile server, no downloads |
| Tests | pytest + httpx, vitest + RTL, k6 — **546 tests** |

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
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | Thread budget, slot sizing, the CPU-starvation fix |
| [docs/GPU.md](docs/GPU.md) | Why there is no accelerator in Docker on Apple Silicon |
| [docs/SECURITY.md](docs/SECURITY.md) | RBAC, encryption, audit, retention, lawful-use safeguards |
| [docs/API.md](docs/API.md) | Every endpoint, generated from the live OpenAPI document |
| [docs/HLD.md](docs/HLD.md) | High-level design, scaling, measured performance |
| [docs/INFRASTRUCTURE.md](docs/INFRASTRUCTURE.md) | Bandwidth, storage tiering, DR *(sizing ~4× optimistic — use `scripts/capacity_model.py`)* |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | Minute-by-minute walkthrough, with fallbacks |

> **Note:** this codebase was renamed and re-aimed from an earlier *statewide* CCTV
> project. `docs/HLD.md`, `docs/INFRASTRUCTURE.md`, `docs/BRIEFING.md`,
> `docs/REQUIREMENTS.md`, `docs/STATUS.md`, `docs/submission/*` and the build history
> in `docs/BUILD_STATE.md` still argue that older brief in places.
> [CLAUDE.md §10](CLAUDE.md#10-migration-ledger) tracks every site; fixing them is P12.

---

## Development

```bash
make up          # start the platform
make ai          # start the AI worker (required for ANPR)
make test        # backend (pytest) + frontend (vitest)
make lint        # ruff + format check + tsc
make logs S=api  # tail one service
make status      # dependency readiness
make clean       # stop and delete all data volumes
```

**Known:** `mypy` does not currently pass (17 errors across 8 files) and `make lint`
does not run it. P12.

---

<div align="center">
<sub>Apache-2.0 · SIH26127 — City-Wide AI Engine for Multi-Camera ANPR Trajectory Tracking and Urban Traffic Analytics</sub>
</div>
