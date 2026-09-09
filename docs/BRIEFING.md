# NagarNetra — your briefing

Everything you need to answer any question a judge asks. Written for you, not
for them.

**Read §14 first if you only have five minutes before you present.**

---

## 1. The one-sentence answer

> NagarNetra is a federation platform that lets Gujarat see, search and reason
> about CCTV it already owns across departments and vendors — without replacing
> a single existing system, because the central tier carries **events, not
> video**.

If you say nothing else, say that. Everything below is elaboration.

---

## 2. What the challenge asked, and what we built

The organisers named five reference models. **Model 1 is compulsory** and must
be combined with another (FAQ Q12).

| Model | What it is | Why not, alone |
|---|---|---|
| 1 — Registry + GIS | Know every camera, put it on a map | Compulsory. But it knows *about* cameras without seeing through them |
| 2 — Unified viewing | One screen showing every feed | 80,000 × 4 Mbps = **320 Gbps** to the centre. Not a budget problem, a physics problem |
| 3 — Federation middleware | Departments keep their VMS; we ingest metadata, pull streams on demand | Strong — but gives no registry, no map |
| 4 — Central VMS | Replace every departmental VMS with one system | Asks every department to abandon a system it owns, budgets for and is trained on. Agreed in a meeting, never implemented |
| **5 — Hybrid** | **1 + 3 + selective 2** | **What we built** |

### Why Model 5 — the answer that wins the question

Gujarat already runs Milestone, Genetec, CP Plus and Hikvision deployments.
Each belongs to a department with its own budget and support contract. Model 4
asks them all to throw that away. It is the kind of proposal that gets polite
agreement and no implementation.

So we federate: **existing VMS stay authoritative for their own video.** We
ingest metadata centrally, run AI at the edge next to the cameras, and pull
video only for the handful of feeds somebody is actually watching.

### The arithmetic that settles it

| | Centralised video | Federated events |
|---|---|---|
| 80,000 cameras × 4 Mbps H.264 | **320 Gbps** sustained | — |
| 80,000 × 1 event/30 s × ~2 KB | — | 2,667 events/s ≈ **43 Mbps** |
| **Ratio** | | **~7,000× less** |

Say this number. It is the single most persuasive thing in the project.

**Be honest about its status:** the event size is measured from real payloads
(1.8–2.4 KB); the camera count is the challenge's own figure. It is arithmetic,
not a load test. See §13.

---

## 3. The full tech stack, and why each piece

| Layer | Choice | Why this and not the alternative |
|---|---|---|
| API | **FastAPI**, Python 3.11 | Async throughout — this is an I/O-bound system (cameras, DB, Redis, HTTP). Auto-generates the OpenAPI doc that `docs/API.md` is built from |
| ORM | **SQLAlchemy 2.0 async** | Real async sessions, not a thread pool pretending |
| DB | **`timescale/timescaledb-ha:pg16.14-ts2.29.2-all`** | One image with **PostGIS + TimescaleDB**. PostGIS for the map, Timescale for detections |
| Migrations | **Alembic** | Never `create_all`. Both up and down are tested |
| Bus | **Redis 7 Streams** with consumer groups | Durable, replayable, at-least-once. Also holds token revocation, rate limits, the fleet roster |
| Bus at scale | **Redpanda** behind the same `EventBus` interface | Config swap, not a rewrite. That's *why* the interface exists |
| Object store | **MinIO** (S3 API) | Plate crops, snapshots. S3-compatible so it lifts to any cloud |
| Search | **OpenSearch 2.19** + Postgres `pg_trgm` fallback | Apache-2.0, not the Elastic licence |
| Media gateway | **MediaMTX 1.20.1** | RTSP in → WebRTC/HLS out, **on-demand** — an unwatched camera costs nothing |
| Inference | **ONNX Runtime** | Torch-free. The worker is ~400 MB, not ~2.5 GB |
| Models | YOLOv8n (vehicles), YOLO11n (plates), **RapidOCR** (PP-OCRv4) | All exported to ONNX at build time |
| Tracking | **ByteTrack**, pure NumPy | ~250 lines, no torch. Two-stage association keeps low-confidence detections |
| Frontend | **React 18 + Vite + TypeScript + Tailwind** | |
| Map | **MapLibre GL** + local GeoJSON | **No tile server.** Works on a plane |
| Realtime | **WebSocket** `/ws/events` | |
| Auth | **JWT** access+refresh, **argon2id**, RBAC | |
| Orchestration | **Docker Compose** profiles: `base`, `ai`, `scale` | |

### The three deviations you must be able to defend

Judges may spot these. Own them before they ask.

**1. ONNX CRNN instead of PaddleOCR.**
PaddlePaddle publishes **no aarch64 Linux wheel** — it literally cannot install
on Apple Silicon in a Linux container. The brief allowed either. The OCR engine
sits behind an `OcrEngine` interface, so an amd64 deployment swaps back with one
env var.

**2. `timescaledb-ha` instead of `postgis/postgis` + extension.**
`postgis/postgis:16-3.4` has **no arm64 build**. The Timescale HA image is arm64
*and* ships PostGIS. One container instead of a custom Dockerfile. Migration
0001 asserts both extensions exist, so a wrong image fails at startup rather
than at Phase 7.

**3. Torch-free runtime.**
Ultralytics is **AGPL-3.0** and ~2.5 GB with PyTorch. We use it *only at build
time* in a throwaway image to export ONNX. The shipped worker carries ONNX
Runtime + OpenCV. This cuts the image by ~2.1 GB **and keeps AGPL code out of
the deployed artifact** — a licensing answer, not just a size one.

---

## 4. How it all connects — the data path

```
  Departmental VMS / the organisers' grid
              │
              │  RTSP (TCP) · ONVIF · vendor REST
              ▼
     ┌─────────────────┐
     │  Integration    │  5 adapters behind one interface
     │  layer          │  RtspAdapter · OnvifAdapter · VendorVmsAdapter
     └────────┬────────┘  HostedGridAdapter · SimulatedVmsAdapter
              │
      ┌───────┴────────┐
      ▼                ▼
 ┌─────────┐    ┌──────────────┐
 │ Registry│    │  AI worker   │   ← runs at the EDGE in production
 │ + GIS   │    │              │
 │(Postgres│    │ decode       │
 │ PostGIS)│    │  → YOLO vehicle detect
 └────┬────┘    │  → ByteTrack
      │         │  → plate detect  (gated: PlateScheduler + CropGate)
      │         │  → crop conditioning
      │         │  → OCR
      │         │  → multi-frame consensus
      │         │  → track merge
      │         └───────┬──────┘
      │                 │  vehicle.completed  (~2 KB JSON)
      │                 ▼
      │         ┌───────────────┐
      │         │ Redis Stream  │  consumer group
      │         └───────┬───────┘
      │                 ▼
      │      ┌──────────────────────┐
      └─────▶│  API event consumer  │
             └──┬────────┬──────┬───┘
                │        │      │
                ▼        ▼      ▼
         detections  watchlist  /ws/events
        (hypertable)  match      (browser)
                        │
                        ▼
                     alert  ──▶ operator screen
```

### The key insight to state out loud

**The AI runs where the video is.** Only ~2 KB events cross the network. In
production the same container runs in a district data centre; nothing about the
code changes, only `AI_WORKER_CAMERAS`. That is the entire scaling argument made
concrete.

---

## 5. The database — what a judge might probe

**14 tables, 2 TimescaleDB hypertables.**

| Table | Purpose | Notes |
|---|---|---|
| `cameras` | The registry | `location` is a **PostGIS `Geography(POINT)`** — metre-accurate distance |
| `departments`, `vms_instances` | Who owns what | `credentials_ref` is a **pointer** to a secret store, never a secret |
| `detections` | Every plate read | **Hypertable**, partitioned by `ts` |
| `camera_health` | Probe history | **Hypertable** |
| `watchlist` | Plates being looked for | |
| `alerts` | Raised hits, with lifecycle | |
| `vehicle_tracks` | Reconstructed routes | `path` is a PostGIS `LINESTRING` |
| `audit_log` | Who did what | Append-only in practice |
| `users` | Accounts | argon2id hashes |

### Three design decisions worth explaining

**Why a hypertable for detections?**
At 80,000 cameras that is ~460 GB/day. Expiry uses `drop_chunks`, which unlinks
whole time partitions. A row-by-row `DELETE` over 168 TB would run for hours,
hold locks, and bloat the table it was trying to shrink.

**Why `Geography` not `Geometry`?**
Distance maths comes out in **metres on a sphere**, not degrees. The correlator
computes real distances between junctions without projecting.

**Why does `alerts.detection_id` have no foreign key?**
Deliberate. An alert is a **police record** with an operator's name against each
transition; the raw detection is **evidence with a shorter lawful life**
(365 days vs the alert). When the detection expires the alert must survive, so
the link dangles by design — and `retention.evidence_expired()` exists so a
screen says *"the evidence has passed its retention date"* rather than showing a
lookup failure that looks like a bug.

*(Also: TimescaleDB requires the partitioning column in every unique index, so
the FK would have needed `(id, ts)` anyway.)*

---

## 6. How ANPR actually works

The pipeline, and the reasoning behind each stage:

```
decode → vehicle detect → track → plate detect → crop conditioning
       → OCR → multi-frame consensus → grammar → event
```

### Selective inference — the thing that makes it viable

**Naive:** run plate detection on every vehicle in every frame.
**Ours:** search a vehicle when its *view has materially changed*, and read a
crop when it is *new evidence*.

| | Before | After |
|---|---|---|
| Plate detections per frame | 13.3 | **1.45** |
| OCR calls per frame | 14.2 | **0.7** |

Two components do it:

* **`PlateScheduler`** — per-vehicle. A car that has been read at high
  confidence is not re-searched every frame.
* **`CropGate`** — a perceptual hash (dHash). If this crop looks like the last
  one, OCR would return the same answer, so it is skipped.

**Say this if asked about frame-skipping:** blind frame-skipping loses vehicles.
This loses *redundant work*. Different thing.

### Multi-frame consensus — the accuracy story

A recogniser handed an unreadable crop returns a **confident wrong answer**, not
an error. So one read is never trusted.

Every candidate per track is buffered, then:

1. **Character-position voting**, weighted by OCR confidence × **crop quality**
   (variance of Laplacian for sharpness, plus width, exposure, contrast). A
   large sharp crop outvotes a small blurry one.
2. **Position-aware confusion correction** — `0↔O`, `1↔I`, `8↔B` resolved *by
   slot*, because slots 0–1 are letters and 2–3 are digits in an Indian plate.
3. **Grammar validation** against `^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$` plus
   the BH series.

**The cap that matters:** a "correction" changing more than **two characters** is
not repairing a misread — it is proposing a different plate. Above that the
reading is returned untouched and flagged. *(We found this the hard way: the
grammar was rewriting `GX15OGJ` into a valid-looking Indian plate by changing 4
of 7 characters.)*

One event per vehicle carries `frames_agreeing / frames_total` and the top-3
candidates, so an operator can overrule it.

### Measured performance — quote these exactly

| | |
|---|---|
| 720p, full pipeline | **4.39 fps** per worker |
| 4K, diagnostics off | **2.78 fps** |
| Capture-to-event latency | **385 ms** median, 559 ms p99 |
| Detection → alert on a browser | **24 ms** (budget was 2 s) |
| Killed stream → marked offline | **19 s** (budget was 30 s) |

All measured on **Apple Silicon, 10 cores, CPU only**. No GPU has ever run this
code and **no GPU number is claimed anywhere in the repository.** Say that
proactively — it buys more credibility than a made-up figure.

### Accuracy — the honest version

| Source | Result |
|---|---|
| Generated plates with ground truth | 100% exact, CER 0.000 |
| Real footage (`anpr_demo.mp4`) | 12 of 13 resolve to a valid format, 0.74–0.95 |
| **The organisers' grid** | Occasional. `GJ11CO5913` at 0.92, grammar-valid |

**Never say "99% accurate".** The 100% is on *rendered* plates — no embossing,
no dirt, no motion blur, no regional fonts. It is optimistic by construction and
you should be the one to say so.

**The grid reads plates rarely** (~1 valid plate per 100 detections) because its
cameras are night-time junction overviews with headlight glare, where vehicles
face away or are far. Detection, tracking, health and events work fine on them.
ANPR needs a camera pointed **down a lane**, not across a junction.

**It also reads signage:** `DELIGHT` at 0.99 confidence from a camera called
Delight. Grammar marks it invalid so it never reaches the watchlist — which is
exactly why the alert path is gated on `grammar_valid`, **not on confidence**.
That is a good answer to "how do you avoid false alerts?"

---

## 7. How search works

Three different questions, three mechanisms.

### (a) "Show me every sighting of this plate"

`GET /api/v1/detections?plate=GJ03AB1234`

Backed by an index built for exactly this: `ix_detections_plate_ts` on
`(plate_normalised, ts DESC)` — "every sighting of this plate, newest first" is
one index scan.

**Normalisation matters:** `GJ 03 AB-1234` and `gj03ab1234` both become
`GJ03AB1234` before comparison. Comparing raw OCR output would miss on
formatting alone.

### (b) "I only remember part of it"

`GET /api/v1/detections?plate_prefix=GJ03AB` — prefix match on the indexed
column. There is also a **`pg_trgm` GIN index** on `plate_normalised` in the
schema for fuzzy matching.

**Be straight about this:** full fuzzy/ranked search (OpenSearch, edge-ngram) is
**Phase 8 and is not built**. What exists is exact + prefix, which covers the
demo. Don't oversell it.

### (c) "Where has this vehicle been?"

`GET /api/v1/vehicles/{plate}/route` — see §9.

### The audit rule

**Every plate search writes an `audit_log` row** carrying the query itself, not
just the fact that a search ran. This is required by CLAUDE.md §5 and asserted
by tests. For a surveillance system it is a feature to point at, not overhead.

---

## 8. Watchlist and alerts

### How matching is fast

The watchlist lives **in memory**, refreshed on change. Matching runs on every
settled read, so it cannot afford a query.

Two match types:

| Type | Meaning | Priority |
|---|---|---|
| `watchlist_hit` | Exact match on the normalised plate | The entry's own |
| `possible_match` | **One character** different | **Capped at medium** |

**Why the cap:** raising a *maybe* as critical teaches operators to distrust
critical. That is how alerting systems die.

**The deletion index:** near-matching uses a deletion-variant index, so lookup
is proportional to **plate length**, not watchlist size. A 100,000-plate
watchlist matches as fast as a 10-plate one.

### Deduplication

Per `(plate, camera)` over **90 seconds** — a car stopped at a junction must not
raise 40 alerts. But the same plate at a **different** camera is deliberately a
new alert, because that is the vehicle *moving*.

### Lifecycle

`new → acknowledged → dispatched → closed / false_positive`

Every transition records **who and when**. `false_positive` is a first-class
outcome, not a delete: an operator who can only "close" will quietly close the
wrong ones, and those are exactly the ones worth knowing about.

**Each transition is a separate permission** — acknowledging is routine,
dispatching commits a unit, closing ends the record. In a control room those are
different authorities.

**Measured: 24 ms** from detection to a connected browser.

---

## 9. The correlator — cross-camera routes

`GET /api/v1/vehicles/{plate}/route?format=geojson`

### The one argument this rests on

Distance between cameras is **great-circle**, not road distance. A road is never
shorter than the straight line between its endpoints, therefore:

> **implied speed is a *lower bound* on the speed actually driven.**

That asymmetry decides how results may be read:

* If the **lower bound already exceeds** what a car can do → the leg is
  **impossible**. Something is wrong: a cloned plate, or a misread.
* A leg that *looks* fine has only passed a **weak test**.

So `implausible` is a **finding**; `plausible` is merely the absence of one. The
API never presents them as symmetric, and the note travels in every response.

### Flags — nothing is dropped

| Flag | Meaning | Route implausible? |
|---|---|---|
| `implausible_speed` | Lower bound > 150 km/h | **Yes** |
| `impossible_simultaneous` | Same plate, two separated cameras, one instant | **Yes** |
| `revisit` | Vehicle returned to a camera it already passed | No |
| `co_located` | Two *different* cameras < 50 m apart | No |
| `unobserved_gap` | Over an hour unwatched | No |
| `heading_conflict` | Camera faces > 100° from travel direction | No |

**A flagged leg stays in the route.** A route that quietly deletes its
inconvenient parts cannot be audited — and the flagged legs are usually the
interesting ones. **An impossible leg is what a cloned plate looks like.**

### Convoy detection

Plates co-occurring across ≥3 cameras within a tight window. **Partners within
one edit of the subject are flagged `likely_same_vehicle`** and sorted below
genuine ones — because OCR reading the same car as `AP05JEO` and `AP05JE0`
produces two plates that co-occur *perfectly* at every camera, which is exactly
the signature of a convoy. That would have embarrassed us on stage.

---

## 10. Security and RBAC — the police-force question

A judge from a police department will ask about traceability. Lead with this.

### Six roles, 19 permissions, one matrix

| Role | Can | Notably cannot |
|---|---|---|
| `admin` | everything | — |
| `supervisor` | cameras RW, watchlist RW, full alert lifecycle, streams, search, **audit read** | manage accounts |
| `operator` | cameras, streams, watchlist read, alert read + **acknowledge**, search | dispatch or close; change the watchlist |
| `analyst` | cameras, watchlist, alerts (read), search | **view streams**; change anything |
| `auditor` | read-only + **audit read** | view streams; **run vehicle searches** |
| `api_client` | camera create only | everything else |

**Why an auditor cannot trace a vehicle:** reading the record of who traced whom
is a *different job* from tracing people. Combining them removes the point of
having an auditor.

**The UI obeys this.** A role does not see a tab it cannot use, and typing the
URL gives a clean refusal that *names the missing permission*. The API enforces
it regardless — the UI is a usability decision that happens to align.

### The audit trail

Every mutating request, every search, every stream open, every sign-in.

Three properties worth stating:

1. **Denied attempts are recorded.** What somebody *tried* matters as much as
   what they managed.
2. **Reading the audit log is itself audited.** No role reads the trail unseen —
   including admin. A reviewer who leaves no trace is a hole in the control.
3. **Username stored alongside user id**, so history survives account deletion.
   Usernames are immutable for the same reason.

### Accounts

* **No self-service registration.** Every account belongs to a named officer.
* **An admin cannot deactivate or demote themselves** — the last one to do so
  locks everybody out.
* **Accounts are deactivated, not deleted.** Deleting a user who has acted
  orphans every audit row naming them; the API refuses it.
* **`must_change_password`** — a password an admin chose is a *shared secret*.
  Until the holder replaces it, nothing that account does is attributable to one
  person.

### Password policy

Rejects **`NagarNetra@2026`** — this repository's own demo credential, published
in `scripts/seed.py`. Length alone admitted it. Also rejects passwords
containing the username, repeated characters, and keyboard runs.

### Retention — enforced, not just stated

| Data | Retained | Mechanism |
|---|---|---|
| Detections | 365 days | `drop_chunks` |
| Camera health | 90 days | `drop_chunks` |
| Audit log | **1825 days** | predicate delete |

Under the **DPDP Act** the stated period is the lawful basis for holding the
data at all. A policy nobody enforces is worse than none — it is documented as a
control that does not exist.

**The audit log outlives everything it describes.** It is the record of who
looked at whom, which is what an inquiry into misuse needs.

### Rate limiting

300 req/min general; **10 *failures* / 5 min** on auth.

**Why failures, not attempts:** a control room sits behind one NAT. Counting
every attempt means ten officers signing in at shift change lock each other out.
What is limited is *guessing*, and guessing is failure by definition.

---

## 11. What is running right now — the current configuration

| | |
|---|---|
| Cameras in registry | **281** |
| Analysed for plates | **31** (30 grid + 1 demo) |
| Online | **31**, 0 offline |
| Containers | 10 base + ai-worker |
| Code | ~32,600 lines Python, ~7,700 TypeScript |
| Tests | **389 API + 54 worker + 175 lab + 24 frontend = 642** |

### The camera fleet, honestly

* **`SBX-00001`–`SBX-00030`** — the organisers' real grid. Live RTSP from
  `103.250.160.189:8554`, 1080p mostly, 24× H.264 + 6× HEVC.
* **`CAM-DEMO`** — the only recorded footage, clearly labelled *"ANPR
  Demonstration Feed (recorded)"*. It exists because the grid's cameras rarely
  yield plates.
* **250 others** — registry records for the GIS map at scale. **No video
  attached**, and the dashboard says *"280 of 281 never probed"* rather than
  pretending.

### The grid's two authentication systems — know this

| Path | Host | Auth |
|---|---|---|
| RTSP (inference) | `103.250.160.189:8554` | username/password in the URL |
| Catalogue + HLS | `cctv.corp8.cloud` | **login session cookie** |

They are *not* interchangeable — Basic auth on the CDN returns a 302 to the
login page. The API holds the session; **the browser never sees grid
credentials**. HLS is proxied through `/api/v1/grid/...`, authorised by the same
short-lived camera-scoped stream token used everywhere else.

Credentials live in `.env` (gitignored) and are **redacted from every log line**,
enforced by a test that greps the source for an unredacted one.

---

## 12. How to onboard a new camera

Four ways, all built. This is a **Model 1 mandatory requirement** (FAQ Q15).

### 1. Single camera, through the UI
Integration screen → form → validated → appears on the map.

### 2. Bulk CSV
`POST /api/v1/cameras/bulk` with **row-level errors and a dry-run**. Upload
2,000 cameras, get back exactly which rows failed and why.

### 3. API self-registration
A department gets an `api_client` credential holding **only** `camera.create`.
They register their own cameras; they can do nothing else.

### 4. Federated sync from a VMS
`scripts/sync_sandbox.py` reads a vendor catalogue and onboards everything in
it. That is how the 30 grid cameras arrived.

### To add a whole new VMS vendor

Implement **one interface** — `CameraAdapter`:

```python
class CameraAdapter(ABC):
    async def connect(self) -> bool
    async def list_cameras(self) -> list[DiscoveredCamera]
    async def get_stream_url(self, camera) -> StreamEndpoints
    async def probe_health(self, camera) -> HealthProbe
```

Register it in `_ADAPTERS`, set `vms_instances.adapter_type`, done. Five exist
already: RTSP, ONVIF, VendorVMS (Milestone/Genetec/CP Plus/Hikvision), the
challenge sandbox, and the simulator.

**An unrecognised vendor falls back to direct RTSP** rather than dropping the
camera off the map — degrade to the lowest common denominator that usually
works.

---

## 13. Scaling to 80,000 cameras

### What the architecture does

* **Edge inference** — workers run beside the cameras; only ~2 KB events cross
  the WAN.
* **Stateless, hash-sharded workers** — ownership is a stable hash of the camera
  code, so N workers split the fleet with **no coordinator**. Add a worker and
  it rebalances; lose one and its cameras are unclaimed *visibly*.
* **Rotation within a worker** — more cameras than inference slots is normal.
  Coverage becomes partial **in time** (measurable) rather than partial **in
  space** (invisible). The platform states the gap:

  > *31 cameras across 3 slots, each watched 45 s every 11.2 min*

  **This is a strong answer.** The alternative — take the first N and ignore the
  rest — under-reports silently, and a vehicle passing camera 20 is never seen.
* **Batched persistence** — `COPY` for detections, bulk indexing for search.
* **The bus is an interface** — Redis Streams for one node, Redpanda for many.

### Sizing, from measured throughput

At 4.39 fps per 720p stream and one analysed frame every 2 s per camera, one
core sustains roughly **8 cameras**.

| Fleet | Cores | Nodes @ 40 cores |
|---|---|---|
| 2,400 (one district) | ~300 | ~8 |
| **80,000 (statewide)** | **~10,000** | **~250** |

**Label this as extrapolation.** A single mid-range GPU running the same ONNX
graphs would change it by roughly an order of magnitude — likely one or two
boxes per district — but **no GPU has run this code**, so say "expected", not a
number.

### Bandwidth per district

~2,400 cameras per district (80,000 ÷ 33):

| | Per district |
|---|---|
| Events to the centre | **~1.3 Mbps** |
| Video if centralised | **9.6 Gbps** |
| Video on demand (5 viewers) | **20 Mbps** |

**A 10 Mbps district uplink carries the event stream with room to spare.**
Nothing here needs new fibre. That is the practical argument for Model 5 over
Model 4, and it is the one a Home Department budget-holder cares about.

### Storage at 80,000 cameras

| Item | Per day | Per year |
|---|---|---|
| Detection rows (~2 KB) | **~460 GB** | ~168 TB |
| Plate crops (~15 KB) | ~3.5 TB | 90-day cap ≈ 310 TB |
| Audit rows | tens of MB | ~10 GB |

**Crops are the expensive tier.** If 310 TB is unaffordable, the lever is
storing crops only for **watchlist hits and low-confidence reads** — the ones a
human might need to check — rather than every vehicle. Say that; it shows you
have thought past the happy path.

### The honest gap

**The Phase 10 load test has not been run.** There is no measured
events-per-second figure and no p95 under load. If asked whether it scales:

> *"The architecture is designed for it and here is the arithmetic. The load
> test that would make 2,667 events/s a measured number rather than a
> calculated one has not been run, and `docs/HLD.md` §7 says so."*

That answer is stronger than a number you cannot defend.

---

## 14. Real-world infrastructure and cost

**All figures are estimates for discussion, not quotes.** Say that. Indian
public-sector pricing varies enormously with procurement route.

### Per-district edge node (~2,400 cameras)

| Item | Spec | Indicative |
|---|---|---|
| Inference server | 40-core, 128 GB, 1× A2/L4 GPU | ₹6–9 lakh |
| Local storage | 8 TB NVMe (hot, 7 days) | ₹60,000 |
| Networking | 10 Gb LAN, 100 Mbps uplink | existing |
| **Per district** | | **₹7–10 lakh** |
| **× 33 districts** | | **₹2.3–3.3 crore** |

### State command centre

| Item | Spec | Indicative |
|---|---|---|
| DB cluster | 3 × 32-core, 256 GB, NVMe | ₹25–35 lakh |
| Warm/cold storage | 500 TB tiered | ₹40–60 lakh |
| API + bus + search | 6 × mid-range nodes | ₹18–25 lakh |
| Redundancy / DR site | ~60% of primary | ₹50–70 lakh |
| **Centre total** | | **₹1.3–1.9 crore** |

### Recurring, per year

| Item | Indicative |
|---|---|
| District uplinks (33 × ~₹1.5 L) | ₹50 lakh |
| Power and cooling | ₹35–50 lakh |
| Support / operations staff | ₹80 lakh–1.2 crore |
| **Annual** | **₹1.6–2.2 crore** |

### Indicative total

**Capex ₹3.6–5.2 crore · Opex ₹1.6–2.2 crore/year** — excluding the cameras
themselves, which already exist and belong to departments. **That is the whole
point of Model 5.**

### The comparison that lands

Centralising video instead would need **320 Gbps** of aggregated backhaul. At
Indian enterprise fibre rates that alone runs to **tens of crores per year**,
before a single server. Federation replaces that with ~43 Mbps of metadata.

### Software licensing

**Zero.** Everything is Apache-2.0, MIT, BSD or PostgreSQL-licensed:
PostgreSQL/PostGIS/TimescaleDB (Apache-2.0 community), Redis, MinIO (AGPL — used
unmodified as a service, which is compliant), OpenSearch (Apache-2.0),
MediaMTX (MIT), FastAPI/React (MIT), ONNX Runtime (MIT).

**One caveat you must own:** the plate-detector weights are an **Ultralytics
export, AGPL-3.0**. Fine for evaluation; **must be replaced or the obligation
accepted before production**. It is in `docs/SECURITY.md` §8. Say it before
they find it.

---

## 15. The demo — how to impress judges

Full script with click paths: **`docs/DEMO_SCRIPT.md`**. The essentials:

### Pre-flight (do not skip)

```bash
docker compose ps                                  # all healthy
curl -s localhost:9100/streams | grep CAM-DEMO     # demo publishing
docker logs nagarnetra-ai-worker --tail 5 | grep watching
```

In the browser at **localhost:8080**, sign in as `admin` / `NagarNetra@2026` and
confirm the dashboard says **event feed live** (green).

### The eight minutes

| # | Screen | The line to say |
|---|---|---|
| 1 | **Dashboard** | *"280 of 281 never probed — the tile says so rather than showing a flattering number."* |
| 2 | **GIS Map** | 281 cameras, filter by department and status |
| 3 | **Live ANPR → CAM-DEMO** | Plates read live with boxes and confidence |
| 4 | **Live ANPR → SBX-00001** | *"A real government camera. Vehicles tracked, no plates — night junction overview with glare. Same pipeline, different camera angle."* |
| 5 | **Watchlist** | Add a plate you just saw. Category, priority, case reference |
| 6 | **Alerts** | It fires by itself. Acknowledge with `a` |
| 7 | **Vehicle Search** | Route drawn — *"dashed and straight, because we know where it was seen, not the roads it took"* |
| 8 | **Audit** | *"Reading this page is itself recorded."* Then sign in as `auditor` — tabs are **gone** |

### The three moments that actually win it

**1. Step 4 — showing a camera that does *not* work.**
Nobody expects it. It converts you from "person with a demo" to "person who
understands their system". Every claim after it is believed.

**2. Step 8 — the auditor's missing tabs.**
Concrete, visual proof of RBAC. Takes ten seconds.

**3. The 7,000× number.**
`320 Gbps vs 43 Mbps`. It is the whole architecture in one comparison.

### If something breaks

| Symptom | Fix |
|---|---|
| Event feed amber | `docker compose --profile ai up -d --force-recreate ai-worker` |
| No video on CAM-DEMO | `docker compose restart simulator`, wait 15 s |
| Grid camera won't play | Expected if their gateway is down. Say so, use CAM-DEMO |
| Everything wrong | `make down && make demo` — two minutes |

**Last resort:** `ai-lab/runs/.../annotated.mp4` — real rendered output of the
pipeline reading plates with crops overlaid. Play it and narrate.

### Four things never to claim

* ✗ *"It reads plates from all 30 government cameras."* It reads from almost
  none — and that is a camera-angle fact, not a pipeline failure.
* ✗ *"99% accuracy."* Optimistic by construction. Say so first.
* ✗ *"It scales to 80,000 cameras."* Say *designed for*, with the arithmetic,
  and that the load test has not run.
* ✗ *"It's production ready."* No TLS, no backups, AGPL weights.

**Being the one who says it first is worth more than hoping nobody asks.**

---

## 16. Likely judge questions, with answers

**"Why not just use an existing ANPR product?"**
> The challenge is not ANPR — it is federating 80,000 cameras across
> departments and vendors. ANPR is one capability inside that. An ANPR product
> gives no registry, no GIS, no health monitoring, no federation, and no answer
> to the bandwidth problem.

**"How do you avoid false alerts?"**
> Three ways. Multi-frame consensus rather than a single read. Grammar
> validation gates the alert path — not confidence, which is why `DELIGHT` at
> 0.99 never became an alert. And near-matches are capped at medium priority
> and labelled `possible_match`, because raising a maybe as critical teaches
> operators to distrust critical.

**"What if a camera goes down?"**
> Detected in 19 seconds, measured. Status flips, a `camera_down` alert is
> raised, and the map turns it red. The reader reconnects with exponential
> backoff, 2 s to 30 s.

**"How would a department integrate?"**
> Four ways, all built: a form, a bulk CSV with row-level errors, an API key
> scoped to `camera.create` only, or a federated sync from their VMS. A new
> vendor means implementing one four-method interface.

**"What about privacy / the DPDP Act?"**
> Retention is enforced daily, not merely stated — detections 365 days, audit
> five years, with the audit log deliberately outliving the data it describes.
> Every trace is attributable and audited. Watchlist entries carry a case
> reference. What we cannot provide is the legal basis, the DPIA and the
> authorisation policy — those are not engineering questions.

**"Is this production ready?"**
> No, and `docs/SECURITY.md` §8 lists exactly why: no TLS, no encryption at
> rest, no backups, no penetration test, and an AGPL model weight to replace.
> What it is, is architecturally sound and honestly documented.

**"Why Python and not C++/Go for the AI?"**
> Inference is in ONNX Runtime, which is C++. Python orchestrates I/O. The
> bottleneck is the model, not the language — measured at 4.39 fps on CPU with
> the pipeline overhead being a small fraction of it.

**"What happens if the network to a district fails?"**
> Events buffer and the bus retains them for the consumer group, so an outage
> delays rather than loses. On-disk spooling for very long outages is designed
> and **not implemented** — `docs/INFRASTRUCTURE.md` says so.

**"How is this different from what Gujarat already has?"**
> Departments have cameras and their own VMS. What does not exist is a single
> place that knows every camera, watches their health, reads plates across all
> of them, and reconstructs one vehicle's movement across departmental
> boundaries. That crossing of boundaries is the product.

---

## 17. What is genuinely pending

Be able to list these. It is a strength.

### For the hackathon (10–11 Sep)

| # | Item | Why it matters |
|---|---|---|
| ~~1~~ | ~~Phase 12 — demo hardening~~ | **Done.** `make demo` verifies each judge moment; docs/PANIC.md; 35 headless e2e tests |
| ~~2~~ | ~~Phase 10 — load test~~ | **Done.** 2,774 events/s across 80,000 identities, 0 failures — and it found three real bugs |
| 1 | **Detector re-export at 1280** | YOLOv8n is fixed at 640×640, so 1080p is downscaled and distant vehicles vanish. ~8% recall on grid night scenes |
| 2 | Phase 8 — real fuzzy search | Exact + prefix works; ranked fuzzy does not exist |
| 3 | p95 ingest latency | 5.7 s under full statewide load against a 3 s goal. It is queue wait and it clears; more workers is the fix |

### For production

| Item | Status |
|---|---|
| **TLS** | Not implemented. Disqualifying alone |
| **Encryption at rest** | Not implemented |
| **Backups / tested restore** | Not implemented — so no RPO/RTO can be claimed |
| **AGPL plate weights** | Must be replaced or the obligation accepted |
| **Media retention** | Configured but unwired — crops are not uploaded to MinIO at all |
| **mypy** | 33 errors, 11 files. `make lint` does not run it |
| **Vendor adapters** | Real and unit-tested; no Milestone/Genetec server has ever been on the other end |
| **Penetration test** | Never done |

**All of this is already in `docs/BUILD_STATE.md` and `docs/SECURITY.md` §8.** None
of it is hidden. If a judge finds something not on this list, that is a genuine
finding — thank them.

---

## 18. Quick reference

| | |
|---|---|
| UI | `http://localhost:8080` — `admin` / `NagarNetra@2026` |
| API docs | `http://localhost:8000/docs` |
| Simulator | `http://localhost:9100/streams` |
| Start | `make demo` |
| Tests | `make test` |
| Regenerate API docs | `make docs` |

**Roles for the demo:** `admin`, `supervisor`, `operator`, `analyst`, `auditor`
— all password `NagarNetra@2026`.

### The documents, and what each is for

| File | Use it for |
|---|---|
| `docs/HLD.md` | Architecture, models, failure modes, measured performance |
| `docs/INFRASTRUCTURE.md` | Bandwidth, compute, storage, DR |
| `docs/SECURITY.md` | Controls — and §8, what is not implemented |
| `docs/DEMO_SCRIPT.md` | The eight minutes, with fallbacks |
| `docs/API.md` | Every endpoint, generated from OpenAPI |
| `docs/REQUIREMENTS.md` | Challenge compliance map |
| `docs/BUILD_STATE.md` | Per-phase state and every known gap |
| **`docs/BRIEFING.md`** | **This file — your answers** |
