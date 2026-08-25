# BUILD_STATE.md

Living checklist for the Sentinel-GJ build. **Updated after every phase.**
Written for a session that remembers nothing about previous sessions — read this, then `CLAUDE.md`,
then continue at the first unchecked phase.

- **Cadence:** stop after every phase and wait for the user. 12 review gates.
- **Commits:** one per phase, conventional (`feat(ai): multi-frame plate consensus`).
- **A phase is complete only when its gate command passes with real output shown.**

---

## Status

| Phase | Title | State |
|---|---|---|
| 0 | Foundation & self-documentation | ✅ **complete** |
| 1 | Auth, RBAC, audit | ✅ **complete** |
| 2 | Camera registry + GIS + bulk onboarding | ✅ **complete** |
| 3 | Integration layer (adapters) + health monitoring | ⬜ not started |
| 4 | Stream gateway | ⬜ not started |
| 5 | AI pipeline | ⬜ not started |
| 6 | Event engine, watchlist, alerts | ⬜ not started |
| 7 | Correlator: cross-camera tracking & routes | ⬜ not started |
| 8 | Search | ⬜ not started |
| 9 | Command centre UI | ⬜ not started |
| 10 | Scale profile & 80,000-camera proof | ⬜ not started |
| 11 | Documentation & submission artifacts | ⬜ not started |
| 12 | Demo hardening | ⬜ not started |

---

## Phase 0 — Foundation & self-documentation ✅

- [x] `CLAUDE.md` written: architecture, stack, repo layout, conventions, "never mock, never stub"
- [x] `BUILD_STATE.md` written: all 12 phases with acceptance criteria
- [x] Repo tree scaffolded exactly as specified
- [x] `.env.example` committed, `.env` gitignored, `.gitignore` covers weights/videos/minio data
- [x] `Makefile`: `up down demo seed test lint models videos logs scale load tiles preflight` (+ `status`, `config`, `wait-healthy`)
- [x] `docker-compose.yml`: postgres(+postgis+timescale), redis, minio, opensearch, mediamtx, api, web
- [x] Every service has a healthcheck; all images pinned to explicit versions
- [x] FastAPI app with real liveness/readiness, structlog JSON logging, OpenAPI at `/docs`
- [x] React 18 + Vite + TS + Tailwind web tier served by nginx, proxying `/api` and `/ws`
- [x] 12 passing tests establishing the pytest harness

**Gate — PASSED:**

```
$ make config
✓ docker-compose.yml is valid

$ make up
✓ Docker daemon reachable
✓ Docker memory allocation sufficient
✓ all containers healthy          # 7/7, ~21s warm
Platform online
  Command centre  http://localhost:8080
  API docs        http://localhost:8000/docs

$ curl -s localhost:8000/ready
overall: ready
  postgres    ok  critical   PostgreSQL 16.14 · postgis 3.6.4 · timescaledb 2.29.2 · pg_trgm 1.6
  redis       ok  critical   7.4.11
  opensearch  ok  optional   cluster green, 1 node
  minio       ok  optional   HTTP 200
  mediamtx    ok  optional   control API reachable

$ docker compose exec -T -w /app/services/api api python -m pytest tests -q
12 passed in 0.27s

$ ruff check . && ruff format --check .
All checks passed!  ·  14 files already formatted
```

### What a judge can do now
Run `make up` and reach a live platform: the command centre at :8080 renders a
readiness board showing every dependency's real state, and the API serves
interactive OpenAPI docs at :8000/docs.

### Environment facts established (verified by probing, not assumed)

| Fact | Consequence |
|---|---|
| Host is macOS **arm64**, 10 cores, Docker allocated 11.7 GB | Native arm64 images throughout; no QEMU |
| `postgis/postgis:16-3.4` is **amd64-only** | Using `timescale/timescaledb-ha:pg16.14-ts2.29.2-all` (arm64, ships PostGIS **and** TimescaleDB). Verified: PostGIS 3.6.4 + TimescaleDB 2.29.2 |
| `paddlepaddle` publishes **no aarch64 wheel** | OCR will be ONNX CRNN (Phase 5), behind an `OcrEngine` interface |
| `onnxruntime` has cp311 aarch64 wheels | Confirmed viable as the runtime |
| macOS ships **GNU Make 3.81** (no `.ONESHELL`) | Makefile recipes written per-line with `; \` continuations. **Do not add bare multi-line if/while blocks** — they break on a stock macOS toolchain |
| No CUDA on Apple Silicon containers | AI runs CPU-only here; GPU numbers in docs must be labelled *extrapolated* |

### Problems hit and how they were fixed (do not re-introduce)

1. **`tsc` TS6305/TS6310** — `tsconfig.json` both `include`d `vite.config.ts` and referenced a
   composite `tsconfig.node.json` with `noEmit`. Fixed by removing project references and using one
   `tsconfig.json` covering `src` + `vite.config.ts`. Also added `@types/node`, and replaced
   `__dirname` (undefined in ESM) with `fileURLToPath(new URL(...))`.
2. **nginx healthcheck failed while the site worked** — nginx bound IPv4 only, but `localhost`
   resolves to `::1` first. Fixed with `listen [::]:80;` **and** `127.0.0.1` in every healthcheck.
   All container healthchecks now use `127.0.0.1`, never `localhost`.
3. **MediaMTX crash-looped** — `sourceOnDemand` is rejected when combined with `source: publisher`.
   On-demand pull belongs on per-camera *pull* paths, which Phase 4 generates from the registry.
   Also renamed deprecated `protocols` → `rtspTransports`, `hlsAllowOrigin` → `hlsAllowOrigins`.
4. **MediaMTX control API returned 401 to the API container** — MediaMTX grants the `api` action to
   127.0.0.1 only by default. Fixed by pinning the compose network to `172.28.0.0/16` and granting
   `api`/`metrics` to that CIDR in `authInternalUsers`. Caught by the readiness probe, not by the
   container healthcheck — which is the point of having both.
5. **structlog double-rendered every line** — the structlog chain ended in a renderer *and* the
   stdlib `ProcessorFormatter` rendered again. Fixed: structlog's chain must end with
   `ProcessorFormatter.wrap_for_formatter`.
6. **Test suite: "attached to a different loop"** — the module-level async engine pools connections
   bound to one event loop; pytest-asyncio makes a fresh loop per test. Fixed with an autouse
   fixture disposing the engine after each test. Harness-only; production runs one long-lived loop.

### Security decisions made here
- Secrets (`JWT_SECRET_KEY`, `MINIO_ROOT_PASSWORD`, `BOOTSTRAP_ADMIN_PASSWORD`) have **no in-code
  defaults** — `Field(...)` makes them required, so the app refuses to boot rather than silently
  using a credential readable in this repository. A validator additionally rejects the `.env.example`
  sample secret when `ENVIRONMENT=production`.
- MediaMTX control API is reachable only from the platform's own subnet.

### What Phase 1 needs from here
- `app/core/config.py` — `Settings` singleton, already carries JWT/argon2 knobs.
- `app/db/session.py` — `Base`, `SessionLocal`, `get_session()` dependency, `engine`.
- `app/main.py` — mount new routers on `app.include_router(...)`; `request_context` middleware
  already assigns `X-Request-ID` (operational telemetry, **not** the audit trail — Phase 1 adds the
  durable `audit_log` table and middleware).
- `services/api/tests/conftest.py` — `client` fixture (ASGI transport) and the engine-dispose fixture.
- Alembic is installed and pinned but **not yet initialised** — Phase 2 owns migration 0001. Phase 1
  may need an earlier migration for `users` / `audit_log`; create it as 0001 and let Phase 2 add 0002,
  or fold both into 0001. Decide at the start of Phase 1.

---

## Phase 1 — Auth, RBAC, audit ✅

- [x] JWT access (15 min) + refresh (7 d), argon2id password hashing
- [x] `require_permission()` / `require_role()` dependencies implementing the RBAC matrix
- [x] Audit middleware: every mutating request and every search writes an `audit_log` row
- [x] Explicit audit helpers for the three named actions (plate search, stream open, watchlist change)
- [x] Alembic initialised; **migration 0001 carries the full schema** (see note below)
- [x] Seed script: 6 departments + one user per role
- [x] 80 tests passing, ruff clean

**Gate — PASSED:**

```
$ alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, Initial schema

$ psql -c "SELECT hypertable_name FROM timescaledb_information.hypertables"
camera_health, detections            # both hypertables live

$ python -m scripts.seed
  departments: 6 created    users: 6 created

$ pytest tests -q
80 passed in 1.36s
   ├─ test_rbac.py    17  the matrix vs. the specification table
   ├─ test_auth.py    33  login, token integrity, refresh rotation, logout
   ├─ test_audit.py   18  trail contents, scrubbing, durability
   └─ test_health.py  12

$ ruff check . && ruff format --check .
All checks passed!
```

### What a judge can now do
Sign in as any of six roles and see the RBAC matrix take effect; every action
they take — including failed logins and permission denials — appears in
`audit_log` with who, what, when, and from which IP.

### Scope decision: migration 0001 carries the WHOLE schema
`users.department_id` references `departments`, so Phase 1 could not create its
tables in isolation. Rather than split across two migrations with awkward FK
ordering, 0001 creates all ten tables exactly as the brief specifies
("implement as Alembic migration 0001"). **Phase 2's migration checkbox is
therefore already satisfied** — Phase 2 adds endpoints and seed data, not DDL.

### Security decisions made here
- **argon2id** (OWASP params: 19 MiB, t=2, p=1), not bcrypt.
- **Refresh token rotation**: refreshing revokes the presented token, so a
  stolen refresh token works at most once and its reuse is detectable.
- **Revocation fails closed** — if Redis is unreachable, `is_revoked()` returns
  True. A user re-authenticates rather than the platform honouring a token an
  administrator believes they cancelled.
- **Timing-safe login**: an unknown username is verified against a dummy argon2
  hash so response time cannot enumerate valid accounts. Both failure modes
  return an identical message.
- **Token type confusion is blocked** — refresh and camera-scoped stream tokens
  are rejected when presented as access tokens (tested).
- **Audit never stores credentials** (recursive scrub) and **never fails a
  request** — a failed audit write is logged at ERROR instead, because auditing
  must not become an availability risk for the policing work it oversees.
- Audit `user_id` is deliberately **not** a foreign key, so the trail survives
  user deletion.

### Reference material incorporated (sentinel.gujarat.gov.in)
Fetched the challenge site and its resource guide. Findings that changed the build:

| Finding | Action taken |
|---|---|
| Real departments are **Health, Police, GSRTC, Panchayat, Municipal** (+ SCRB) | Replaced the assumed POLICE/RTO/MUNI/HIGHWAY codes in `models/enums.py` and the seed |
| Sandbox exposes `GET /api/ingest` returning every camera with id, location, codec, live status and all three stream URLs | Phase 3 gains a `SentinelSandboxAdapter`; `AdapterType.SENTINEL_SANDBOX` and `VmsVendor.SENTINEL_SANDBOX` already added |
| Stream URLs: `rtsp://<host>:8554/stream/<id>`, `http://<host>:8889/stream/<id>/whep`, `http://<host>/live/stream/<id>/index.m3u8` | **Our MediaMTX ports already match exactly** (8554/8889) — the adapter is a thin mapping, not a translation layer |
| Exact catalogue JSON schema is **not published**; ids "can change" | Phase 3 adapter must parse defensively and re-sync, never assume field names or a fixed id set |
| Scale: 30+ live cameras, 12 h footage each, 5 departments → 80,000+ target | Confirms the Phase 10 load-test target |
| Also required: **face/person detection**, "cross-reference with Government databases" | **Open scope question for the user** — the current plan is vehicle/ANPR-centric. Flagged, not silently skipped |
| Event 10–11 Sept 2026; registration closes 7 Sept | ~2 weeks from 25 Aug 2026 |

### What Phase 2 needs from here
- `app/api/deps.py` — `CurrentUserDep`, `DbSession` annotated dependencies.
- `app/core/rbac.py` — wrap camera endpoints in `require_permission(Permission.CAMERA_*)`.
- `app/services/audit.py` — call `record_stream_open` / `record_plate_search` explicitly.
- `scripts/seed.py` — extend `seed_departments`/`seed_users` pattern with cameras + watchlist.
- Migration 0001 already created `cameras` with the GiST index; no new DDL required.

## Phase 2 — Camera registry + GIS + bulk onboarding ✅

- [x] Migration 0001 (delivered in Phase 1 — full schema, hypertables, GiST index)
- [x] Camera CRUD with RBAC on every endpoint and audit on every mutation
- [x] CSV bulk upload: per-row validation, row-numbered error report, `dry_run` mode
- [x] `POST /api/v1/cameras` self-registration for the `api_client` role
- [x] PostGIS: `/cameras/nearby`, `/cameras/in-district/{name}`, `/cameras/geojson`
- [x] Extra: `/cameras/summary`, `/cameras/vocabularies`, `/departments`, `/vms`
- [x] Seed: **250 cameras**, 6 departments, 5 VMS instances across 5 vendors
- [x] Gujarat district boundary GeoJSON — 33 districts, 330 KB, committed
- [x] 124 tests passing, ruff clean

**Gate — PASSED:**

```
$ make clean && make up && make migrate && make seed
  departments: 6 created    users: 6 created    vms instances: 5 created
  cameras.bulk_upload created=250 failed=0
  cameras: 250 created, 0 updated, 0 failed

$ GET /api/v1/cameras/geojson
  ✓ FeatureCollection with 250 features

$ GET /api/v1/cameras/nearby?lat=22.3039&lon=70.8022&radius_km=8
     1.388 km  CAM-00105  Trikon Baug CCTV 20
     1.454 km  CAM-00097  Trikon Baug CCTV 12
     1.566 km  CAM-00173  NH-27 km 52 ANPR Gantry
     1.748 km  CAM-00102  Kalawad Road Junction CCTV 17
  ✓ monotonic by distance

$ GET /api/v1/cameras/summary
  total: 250 | ANPR-capable: 209
  POLICE 131 · GSRTC 48 · MUNICIPAL 39 · PANCHAYAT 22 · HEALTH 10
  8 districts

$ pytest tests -q  →  124 passed
$ ruff check . && ruff format --check .  →  All checks passed
```

### What a judge can now do
Query the whole 250-camera estate: filter by department, district, status,
vendor or ANPR capability; ask "what covered this junction?" and get cameras
ordered by true spheroidal distance; pull the fleet as GeoJSON ready for
MapLibre. **Judge Moment 1 is data-complete** — the map screen in Phase 9 has
everything it needs.

### Seed data design (this matters for Judge Moment 4)
`scripts/generate_cameras.py` is deterministic (seed 20260910) and writes the
committed `data/seed/cameras.csv`. Cameras sit on **real** coordinates: 161 at
named city junctions across 10 cities, 89 strung along the actual NH-27 and
NH-48 alignments, with `heading_deg` computed from the road bearing.

That geographic realism is load-bearing. The demo route Rajkot → Gondal →
Jetpur → Junagadh has 26/11/11/11 cameras within 6 km of each waypoint, and
legs of 38.1 / 29.5 / 31.0 km — about 25 minutes apart at highway speed. Random
coordinates would produce a "route" at impossible speeds through empty desert,
and the correlator's plausibility scoring (Phase 7) would correctly reject it.

`make seed` loads the CSV **through the same `bulk_upload` the API exposes**, so
seeding exercises real onboarding code rather than a private shortcut that
could silently diverge from it.

### Problems hit and how they were fixed (do not re-introduce)

1. **Every guarded endpoint returned `422: query.user Field required`.**
   `app/core/rbac.py` uses `from __future__ import annotations`, so FastAPI
   resolves dependency type hints against **module globals**. `CurrentUser` and
   `get_current_user` were imported *inside* the factory functions, so the name
   could not be resolved and FastAPI silently degraded the parameter into a
   required query parameter. Fixed by hoisting those imports to module level —
   the circular-import risk that motivated the lazy import does not actually
   exist (`api/deps.py` does not import `rbac`, and `services/audit.py` imports
   deps only under `TYPE_CHECKING`). **Never lazily import a name used in a
   FastAPI dependency signature in a module using postponed annotations.**
2. **`MissingGreenlet` on camera create/update.** After `session.flush()`,
   server-generated columns (`created_at`/`updated_at`) are expired and
   relationships unloaded; serialising them attempted lazy IO, which async
   SQLAlchemy refuses. Fixed by re-reading through `get_camera()` (which
   `selectinload`s relations) instead of `session.refresh(attribute_names=...)`.
3. **`make demo` would fail on a fresh clone** — it ran `up` then `seed` with no
   migration in between. `demo` now runs `up → migrate → seed`.
4. **District boundaries: 31/33 matched.** geoBoundaries uses census spellings —
   `Ahmadabad` (not Ahmedabad) and `Batod` (not Botad). Aliases added; now 33/33.
   Ahmedabad missing would have blanked the state's largest camera cluster.
5. **geoBoundaries is git-lfs backed** — `raw.githubusercontent.com` returns a
   132-byte pointer/404. Must use `media.githubusercontent.com`. The fetch
   script fails loudly if the download is under 100 KB rather than writing a
   corrupt file.

### Security decisions made here
- **`stream_url` is never returned by any list or detail endpoint.** Viewing
  URLs are issued per request as short-lived signed tokens by
  `/cameras/{id}/stream` (Phase 4), and that issuance is audited. Returning raw
  stream URLs in a list response would hand out unaudited access to every feed
  in the estate. Asserted by a test.
- **Credentials embedded in a stream URL are rejected at validation.**
  `rtsp://admin:pw@host/stream` is how camera passwords leak into databases,
  logs and screenshots.
- **`credentials_ref` is never serialised**, even to admins — it points into a
  secret store, and exposing the pointer narrows an attacker's search.
- **Coordinates outside Gujarat are rejected**, catching the classic swapped
  lat/lon onboarding error before it puts a camera in the Indian Ocean.
- Every read endpoint is paginated with a hard cap (500); no route can return
  the whole estate.

### Map data
`data/seed/gujarat_districts.geojson` — 33 districts, 330 KB, from geoBoundaries
gbOpen ADM2 (ODbL 1.0 / CC-BY 4.0), regenerable with `scripts/fetch_geodata.sh`.
This plus the camera GeoJSON is the entire basemap: **no tile server, no tile
download, works offline on a plane.** Every district our cameras sit in is
covered.

### What Phase 3 needs from here
- `app/models/registry.py` — `VmsInstance.adapter_type` is what the adapter
  registry resolves on; `CameraHealth` is a hypertable ready for probe writes.
- `AdapterType`/`VmsVendor` already include `SENTINEL_SANDBOX`, and the seeded
  "Sentinel Sandbox Grid" VMS points at `https://sentinel.gujarat.gov.in`.
- `app/services/camera.py::bulk_upload` is the reusable path for adapters that
  sync a camera list in from a federated VMS.
- **Sandbox adapter caveat:** the exact `/api/ingest` JSON schema is not
  published and camera ids "can change" — parse defensively, re-sync rather
  than assuming a fixed id set.

## Phase 3 — Integration layer (adapters) + health monitoring

- [ ] `CameraAdapter` ABC: `connect`, `get_stream_url`, `probe_health`, `list_cameras`, `get_recording`
- [ ] `RtspAdapter` (ffprobe-based health)
- [ ] `OnvifAdapter` (WS-Discovery + device/media service, profile enumeration)
- [ ] `VendorVmsAdapter` (generic REST federation: token auth, camera list sync, stream URL resolution)
- [ ] `SimulatedVmsAdapter` (powers the demo)
- [ ] Adapter registry resolving by `vms_instances.adapter_type`
- [ ] Health monitor: staggered probe loop → `camera_health` → flips `cameras.status` →
      raises `camera_down` alerts; `/api/v1/health/fleet` rollup

**Gate:** simulator publishes 250 cameras; a deliberately killed stream is marked offline within 30 s
and raises an alert.

---

## Phase 4 — Stream gateway

- [ ] MediaMTX: RTSP ingest, WebRTC (WHEP) + HLS egress, **on-demand publishing**
- [ ] `GET /api/v1/cameras/{id}/stream` returns a short-lived signed viewing token
- [ ] Web player: WHEP with HLS fallback
- [ ] Simulator loops sample MP4s into MediaMTX as `rtsp://mediamtx:8554/cam-00034`
- [ ] `MTX_WEBRTCADDITIONALHOSTS` configured (Docker Desktop NAT yields unreachable ICE candidates)

**Gate:** video plays in the browser at < 2 s latency over WebRTC; an unauthenticated token is rejected.

---

## Phase 5 — AI pipeline

- [ ] Async frame-reader pool, drop-to-latest, 8–12 analysed fps
- [ ] YOLO vehicle detector (car/truck/bus/motorcycle/auto/tractor)
- [ ] ByteTrack (pure NumPy) → persistent `track_id` per camera session
- [ ] Plate detector on per-track ROI
- [ ] Rectification: 4-point perspective warp + CLAHE + upscale
- [ ] ONNX CRNN OCR → per-frame candidates
- [ ] **Multi-frame consensus:** normalise → char-position voting weighted by OCR confidence ×
      crop sharpness (variance of Laplacian) → position-aware confusion correction
      (`0↔O 1↔I 8↔B 5↔S 2↔Z 6↔G` by slot) → grammar validation → one event per track
- [ ] Grammar: `^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$` + BH `^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$`;
      failures flagged `grammar_valid=false`, **not dropped**
- [ ] Confidence gating (emit ≥ 0.55, auto-alert ≥ 0.80)
- [ ] Duplicate suppression (same plate + camera within 60 s)
- [ ] Plate crop + full frame → MinIO
- [ ] GPU auto-detect with CPU fallback; batch inference
- [ ] `--benchmark` mode printing per-stage fps/latency

**Gate:** `python -m ai_worker --source data/videos/sample_traffic.mp4 --benchmark` produces events
with correct plates and prints a per-stage latency table.

---

## Phase 6 — Event engine, watchlist, alerts

- [ ] `EventBus` interface + `RedisStreamBus` implementation (consumer groups)
- [ ] Consumer: validate against contract → persist detection → index to OpenSearch → watchlist check
- [ ] Watchlist matcher: in-memory bloom filter refreshed on change; exact match on
      `plate_normalised`; Levenshtein ≤ 1 as a **separate lower-priority "possible match"**
- [ ] Alert raise → WebSocket push
- [ ] Alert lifecycle: new → acknowledged → dispatched → closed / false_positive, with who and when
- [ ] Deduplication window per (plate, camera)

**Gate:** inject a watchlisted plate; alert appears on a connected WebSocket client in **under 2 s**
end-to-end from event publish.

---

## Phase 7 — Correlator: cross-camera tracking & route reconstruction

- [ ] Fetch detections by plate + window, ordered by ts
- [ ] Cluster into hops, merging same-camera sightings within a dwell window
- [ ] Great-circle distance + elapsed time → implied speed per consecutive pair
- [ ] Plausibility scoring: > 150 km/h, or < 2 km/h over a long gap, or `heading_deg` contradicting
      direction of travel → **marked low-confidence, not dropped**
- [ ] Persist to `vehicle_tracks`; `GET /api/v1/vehicles/{plate}/route?from&to` → GeoJSON + hop table
- [ ] Straight-line segments explicitly labelled as such
- [ ] Convoy detection (2 plates co-occurring across ≥ 3 cameras in a tight window)
- [ ] First-sighting / ANPR-gap analysis

**Gate:** seeded `GJ03AB1234` produces Rajkot → Gondal → Jetpur → Junagadh with sane implied speeds,
rendered as valid GeoJSON.

---

## Phase 8 — Search

- [ ] OpenSearch detections index: edge-ngram + fuzzy analyzers
- [ ] Partial plate (`GJ03AB` → all matches) and fuzzy (`GJ03A81234` → suggests `GJ03AB1234`)
- [ ] Filters: camera, department, district, time range, vehicle type, event type, alert type,
      confidence floor
- [ ] Faceted summary: "7 detections · 3 cameras · 1 watchlist match"
- [ ] Postgres `pg_trgm` fallback chosen at runtime when OpenSearch is unavailable
- [ ] Every search writes an audit row

**Gate:** partial-plate search returns ranked results in **< 300 ms** on 500k seeded detections;
fallback path proven by stopping the OpenSearch container mid-test.

---

## Phase 9 — Command centre UI

- [ ] Dark operations-centre theme, IST timestamps everywhere, Gujarati + English primary nav
- [ ] Login (role-based)
- [ ] Live Dashboard: fleet KPI strip, live event ticker, alert feed, mini map
- [ ] GIS Map: clustered status-coloured markers, district choropleth, filters, popup →
      [View Camera] [Analytics] [Events]
- [ ] Camera Detail: WebRTC player, live detection overlay, health sparklines, recent events
- [ ] Video Wall: 2×2 / 3×3 / 4×4, drag cameras in
- [ ] Alerts: priority-sorted, red critical banner with plate crop, ack/dispatch/close
- [ ] Vehicle Search + Vehicle Profile: sightings, thumbnail strip, route map with animated
      playback, timeline scrubber
- [ ] Watchlist Manager: CRUD, CSV import, category/priority, per-entry audit trail
- [ ] Camera Onboarding: single form + CSV upload with row-level error display
- [ ] Admin: users, roles, VMS instances, audit log viewer, system health
- [ ] Architecture: HLD + scaling diagram + **live numbers from the load test**
- [ ] Skeletons (never block on a slow request), WS reconnect with backoff, CSV/PDF export,
      keyboard shortcuts for alert triage

**Gate:** full click-through of the five judge moments with no console errors.

---

## Phase 10 — Scale profile & the 80,000-camera proof

- [ ] `docker-compose.scale.yml`: Redpanda behind the same `EventBus`, 3 AI workers,
      2 API replicas behind nginx, Prometheus + Grafana
- [ ] `services/simulator/load_mode.py`: 80,000 camera identities, configurable rate
      (default 80k × 1/30 s ≈ 2,667 events/s)
- [ ] k6 against the API concurrently
- [ ] Batched DB writes (COPY) + OpenSearch bulk indexing
- [ ] Benchmark script writes the measured table into `docs/HLD.md` automatically
- [ ] Bandwidth arithmetic in the doc: 320 Gbps centralised vs ~43 Mbps metadata (~7,000×)
- [ ] Edge GPU sizing derived from Phase 5's **measured** per-stream fps

**Gate:** scale profile sustains ≥ 2,000 events/sec for 10 minutes with p95 end-to-end latency
under 3 s, numbers written into the docs by the benchmark script.

**Honesty clause:** if the persistence tier caps below target, report the measured number, name the
bottleneck, and extrapolate per-node. Do not tune the benchmark until it flatters us.

---

## Phase 11 — Documentation & submission artifacts

- [ ] `docs/HLD.md` — context/container/component/deployment mermaid diagrams, four reference models
      + Model-5 justification, data flow, failure modes, measured performance table
- [ ] `docs/INFRASTRUCTURE.md` — per-district bandwidth math, GPU sizing from measured fps,
      storage tiering (hot 7 d NVMe / warm 90 d HDD / cold 1 y object) with capacity math,
      store-and-forward for intermittent links, DR with RPO/RTO
- [ ] `docs/SECURITY.md` — TLS, encryption at rest, RBAC matrix, audit trail, network segmentation,
      API key lifecycle, retention & purge, lawful-use safeguards, DPDP-Act notes
- [ ] `docs/API.md` — generated from OpenAPI
- [ ] `docs/DEMO_SCRIPT.md` — minute-by-minute 8-minute walkthrough, exact click paths,
      fallback action per component
- [ ] `README.md` — three commands, screenshot strip, architecture image

---

## Phase 12 — Demo hardening

- [ ] `make demo`: fresh DB → 250 cameras → watchlist incl. `GJ03AB1234` HIGH/stolen →
      simulator replaying 6 clips → backfill 30 days / 500k detections on a diurnal curve with the
      demo vehicle's route planted → open browser
- [ ] `scripts/generate_synthetic_plates.py` → accuracy report (precision / recall / CER) in the docs
- [ ] `tests/e2e/test_judge_flow.py` — all five judge moments, headless
- [ ] `PANIC.md` — projector / network / GPU failure, pre-recorded fallback video path

**Gate:** fresh clone → `make demo` → all five judge moments, timed under 5 minutes.

---

## Definition of done

A judge clones the repo, runs `make demo`, and within five minutes sees: 250 cameras on a Gujarat
map, live video from a click, plates read from moving traffic in real time, a red watchlist alert
firing automatically with the plate crop, a searched plate producing a four-camera route drawn on the
map with timestamps and implied speeds, and an architecture page with load-tested numbers backing an
80,000-camera claim.
