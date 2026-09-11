# BUILD_STATE.md

Living checklist for the NagarNetra build. **Updated after every phase.**
Written for a session that remembers nothing about previous sessions.

**Read in this order:** [PROGRESS.md](PROGRESS.md) for the one-page state →
[docs/ROADMAP.md](ROADMAP.md) for what to build and each phase's gate → [CLAUDE.md](../CLAUDE.md)
for the rules → then this file for what has already been built and what evidence it produced.

- **Cadence:** stop after every phase and wait for the user.
- **Commits:** one per phase, conventional (`feat(ai): multi-frame plate consensus`).
- **A phase is complete only when its gate command passes with real output shown here.**
- **Commits carry the repository owner's name only** — no co-author trailers.

**Where the AI lives.** `ai-lab/` is a standalone evaluation environment and
`services/ai-worker/` is the live worker. The worker *imports* the lab as a library — it is not
separate from the deployed system, despite older wording that said so. Read `ai-lab/README.md`,
`ai-lab/PERFORMANCE.md` and `ai-lab/ARCHITECTURE.md` before changing the vision pipeline; they
record what was measured and why the defaults are what they are.

---

## Status

The project was re-aimed from a **statewide Gujarat Police CCTV** brief to **SIH26127**, a city-wide
vehicle intelligence problem. The old phases 0–14 below are the **build history** and remain
accurate as history. The work that remains is the **P1–P12** sequence, defined in
[docs/ROADMAP.md](ROADMAP.md).

### SIH26127 phases — what remains

| Phase | Title | Milestone | State |
|---|---|---|---|
| **P1** | Multi-camera Ahmedabad fleet | V1 | ✅ **complete** — 58 cameras, 51 live, gate passed |
| **P2** | Journey profile + animated playback | V1 | ✅ **complete** — profile, speeds, playback, history |
| **P3** | Evidence crops in MinIO | V1 | ✅ **complete** — crops upload, alerts show them |
| **P4** | City traffic analytics | V1 | ⬜ **next — start here** |
| **P5** | Trajectory anomaly + explainable alerts | V1 | ⬜ not started |
| **P6** | Bug fixes, tests, demo hardening | V1 | ⬜ not started |
| **P7** | Earn the >90% accuracy claim | V2 | ⬜ not started |
| **P8** | Fuzzy and partial plate search | V2 | ⬜ not started |
| **P9** | Attribute search (colour / type) | V2 | ⬜ not started |
| **P10** | Predictive traffic | V2 | ⬜ not started |
| **P11** | Vehicle re-identification | V2 | ⬜ not started |
| **P12** | Truth pass on docs and contracts | V2 | ⬜ not started |

Evidence for each is logged under *SIH26127 phase evidence* near the end of this file.

### Old-brief phases 0–14 — build history

Corrected 9 Sep 2026: this table previously disagreed with its own body, marking phases 10 and 12
"not started" while their sections were headed ✅.

| Phase | Title | State |
|---|---|---|
| 0 | Foundation & self-documentation | ✅ complete |
| 1 | Auth, RBAC, audit | ✅ complete |
| 2 | Camera registry + GIS + bulk onboarding | ✅ complete |
| 3 | Integration layer (adapters) + health monitoring | ✅ complete |
| 4 | Stream gateway | ✅ complete |
| 5 | AI pipeline | ✅ complete |
| 6 | Event engine, watchlist, alerts | ✅ complete |
| 7 | Correlator: cross-camera tracking & routes | 🟡 engine built and tested; **superseded by P2 / P5** |
| 8 | Search | ⬜ never started — **superseded by P8** |
| 9 | Command centre UI | 🟡 operator screens complete; **analytics page is P4** |
| 10 | Scale profile & 80,000-camera proof | ✅ complete — 2,774 events/s, 0 failures |
| 11 | Documentation & submission artifacts | ✅ complete, but **written for the old brief** — P12 |
| 12 | Demo hardening | ✅ complete for the old brief's demo — **P6 redoes it for the 8 PS steps** |
| 13 | Person & face detection, crowd counting | ⬜ **out of scope for SIH26127** |
| 14 | Government database integration (VAHAN/SARTHI) | ⬜ **out of scope for SIH26127** |

---

# Build history — old-brief phases 0–14

Kept because it records *what was measured and which bugs were fixed*, which is still
true and still useful. The framing (statewide, 80,000 cameras, judge moments, FAQ
references) belongs to the previous brief. Do not treat the goals in this section as
current; [docs/ROADMAP.md](ROADMAP.md) is current.

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
| Sandbox exposes `GET /api/ingest` returning every camera with id, location, codec, live status and all three stream URLs | Phase 3 gains a `HostedGridAdapter`; `AdapterType.HOSTED_GRID` and `VmsVendor.HOSTED_GRID` already added |
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
- `AdapterType`/`VmsVendor` already include `HOSTED_GRID`, and the seeded
  "Hosted Camera Grid" VMS points at `https://sentinel.gujarat.gov.in`.
- `app/services/camera.py::bulk_upload` is the reusable path for adapters that
  sync a camera list in from a federated VMS.
- **Sandbox adapter caveat:** the exact `/api/ingest` JSON schema is not
  published and camera ids "can change" — parse defensively, re-sync rather
  than assuming a fixed id set.

## Phase 3 — Integration layer (adapters) + health monitoring ✅

- [x] `CameraAdapter` ABC: `connect`, `get_stream_url`, `probe_health`, `list_cameras`, `get_recording`
- [x] `RtspAdapter` — real ffprobe session negotiation, errors classified into groupable codes
- [x] `OnvifAdapter` — SOAP device/media services, profile enumeration, **defusedxml**
- [x] `VendorVmsAdapter` — token auth, field-mapped normalisation covering 4 vendors
- [x] `HostedGridAdapter` — the challenge's own `/api/ingest` grid
- [x] `SimulatedVmsAdapter` — MediaMTX-backed, powers the demo
- [x] Adapter registry resolving by `vms_instances.adapter_type`, degrading to RTSP
- [x] Health monitor daemon (own container), staggered + bounded concurrency
- [x] `/health/fleet`, `/health/gaps`, `/cameras/{id}/health`, `/cameras/{id}/probe`, `/vms/{id}/sync`
- [x] **Gap-analysis reports** (mandatory, FAQ Q15)

**Gate — PASSED:**

```
$ curl -X POST :9100/streams/CAM-00086/stop      # kill a stream deliberately
  ✓ OFFLINE detected in 19s          (target <30s)
  alert: high | CAM-00086 (Kalawad Road Junction ANPR 01) unreachable: not_publishing

$ curl :8000/api/v1/health/fleet
  total 250 | online 24 | offline 0 | unknown 226
  integrated 24 | availability of integrated: 100.0%

$ pytest tests -q  →  236 passed
```

### Health status semantics — the most important design decision here
The first implementation marked all 250 cameras `offline`, because their VMS
endpoints are fictional on a laptop. That is **wrong and operationally
expensive**: it would dispatch an engineer to inspect a camera that works
perfectly. Three states are now distinguished:

| Situation | Status | Why |
|---|---|---|
| VMS reachable, camera delivering video | `online` | Verified |
| VMS reachable, camera not delivering | `offline` | Genuine camera fault → alert |
| **VMS unreachable** | `unknown` | We cannot know the camera's state |
| **Never integrated / not currently pulled** | `unknown` | Normal under on-demand publishing |

A camera that **was** online and stops is still `offline` with an alert — the
raw error is identical to a never-integrated camera, so only the *transition*
can tell them apart. That logic lives in `_should_flip`, not in
`HealthProbe.status`.

**Integration failures are aggregated per VMS**, not per camera: one dead
Milestone server is one incident with one fix, not 4,000 alerts.

### Phase 4 — Stream gateway ✅

- [x] MediaMTX RTSP ingest → WHEP + HLS egress
- [x] `GET /cameras/{id}/stream` → short-lived, **camera-scoped** signed token
- [x] `GET /streams/verify` — gateway-callable token verification
- [x] Simulator publishes real RTSP into MediaMTX (24 streams, → ~50 for the live test case)
- [x] Simulator control API to kill/restore a stream (the Phase 3 gate hook)
- [x] Every stream open writes a `camera.view` audit row

**Gate — PASSED:** token issued and verified; a token scoped to camera A is
rejected for camera B (403); `analyst` and `auditor` denied (403); audit row
written; no stream URL appears in any list response.

### Command centre portal (pulled forward from Phase 9)
Built early because the challenge makes it mandatory (**FAQ Q15: "interactive
GIS map with layered filters"**) and it is how the work is actually reviewed.

- **Login** — role switcher with all six demo accounts
- **GIS Map** — 250 cameras on Gujarat's 33 district boundaries, clustered,
  coloured by status, filterable by department / vendor / status / ANPR / text
- **Camera panel** — metadata, 24h uptime strip, probe-now, stream token
- **Fleet Health** — availability by department and vendor, grouped failure
  causes, four-way gap analysis
- **Integration** — federated VMS instances and registered adapters

**No tile server.** MapLibre renders a 330 KB local GeoJSON — the map is
identical offline.

### Problems hit and how they were fixed (do not re-introduce)

1. **Simulator crash-looped**: `uvicorn --log-config /dev/null` → "empty file".
   Removed; `configure_logging()` already routes uvicorn's loggers.
2. **`ModuleNotFoundError: app.core` in the simulator** — both services used the
   package name `app`, so `app.core` resolved to the simulator's own package.
   Renamed the simulator package to `simulator`. **Never name two services'
   root packages the same when they share a PYTHONPATH.**
3. **`ingest` never became healthy** — the healthcheck used `pgrep`, absent from
   the slim image. Replaced with `app.ingest.healthcheck`, which asserts a
   *recent row in `camera_health`* — proving the monitor is doing its job, not
   merely that a process exists.
4. **All 250 cameras reported offline** — see the status-semantics table above.
5. **`tsc` failed on every status-colour lookup** — `Record<string, string>`
   plus `noUncheckedIndexedAccess` widens property access to `| undefined`.
   Fixed with `as const` objects.
6. **Detection took 58s against a <30s target** — 2 failures × 30s interval.
   Probe interval lowered to 15s, keeping the two-failure debounce: 19s measured.
7. **S314: unsafe XML parsing** in the ONVIF adapter. ONVIF replies come from
   devices on the camera VLAN — untrusted input. Now uses `defusedxml`;
   verified an entity-expansion bomb raises `EntitiesForbidden`.

### Demo adapter note (important, and honest)
The four vendor VMS rows keep their real `vendor` and `base_url`, but their
`adapter_type` is `simulated` on the demo path, because those hosts do not
exist on a laptop. Production is a one-field change per row. "Hosted Grid
Grid" keeps its real adapter — that endpoint is genuinely remote.

## Phase 5 — AI pipeline ✅

Built as **`ai-lab/`** — a standalone, measurable environment kept separate from
the platform — then deployed as the **`ai-worker`** service. The split is
deliberate: the lab may carry heavy, AGPL-licensed, experiment-only
dependencies; the worker may not.

- [x] Frame reader: drop-to-latest, threaded, live-stream aware
- [x] YOLO vehicle detector (car/motorcycle/bus/truck/bicycle/person) on ONNX Runtime
- [x] ByteTrack (pure NumPy + SciPy) → persistent `track_id`
- [x] Plate detector on per-vehicle ROI; classical contour fallback needing no weights
- [x] Rectification: 4-point warp + CLAHE + upscale, competing preprocessing variants
- [x] OCR: RapidOCR (PP-OCRv4, ONNX, no torch); EasyOCR and CRNN selectable
- [x] **Multi-frame consensus** — character-position voting weighted by OCR
      confidence × crop quality, position-aware confusion correction by slot,
      Indian plate grammar, one event per vehicle
- [x] Grammar: standard + BH series + legacy; failures flagged, **never dropped**
- [x] Deduplication and plate-ownership resolution across overlapping vehicles
- [x] Track merging by plate identity — one physical vehicle, one event
- [x] GPU provider selection written; **never executed** (no CUDA on this host)
- [x] Benchmark modes: `diagnostic` / `accurate` / `default` / `fast` / `bench`

### Gate — passed

`ailab run <video> --ground-truth gt.csv` on footage with known plates:

```
exact plate match       100.0%
character error rate    0.000
missed                  0
duplicate vehicles      0
precision               0.83
```

Per-stage latency table printed on every run, with model **invocation counts**
alongside — a stage's cost is rate × price, and a profile reporting only price
cannot tell a slow model from one called too often.

### What the profiling found (measured, not guessed)

On 4K footage with ~20 vehicles per frame, throughput went from **7665 ms/frame
to 1151 ms** (360 ms with diagnostics off):

| fault | effect |
|---|---|
| ONNX Runtime default thread count | 452 ms → **126 ms** at four threads; its default was the worst setting available |
| Plate input scaled with the crop | Fixed at 320px: a plate is a constant *fraction* of a vehicle, so scaling buys no detail |
| OCR ran text detection first | **1334 ms → 74 ms** for the identical answer on an already-cropped plate |
| Selective inference absent | Plate detection 13.3 → **1.45** calls/frame; OCR 14.2 → **0.7** |

Every skip is counted by reason in `summary.json`, so the speedup is auditable
rather than a silent quality change.

### Live path

Threaded drop-to-latest reader, continuous event emission, bounded memory.
Measured **385 ms median capture-to-event latency** while dropping 70% of
frames — latency stays bounded instead of growing, which is the difference
between an alert and a historical record.

Compliant with the organisers' streaming contract: RTSP forced over TCP, timing
driven by PTS rather than arrival time, exponential reconnect backoff (2s→30s),
and tracker state rebuilt at the loop-point scene discontinuity.

### Problems hit and how they were fixed (do not re-introduce)

* **`np.float16` is a scalar type, not a dtype instance.** `self.input_dtype.type(255.0)`
  raised `AttributeError` on the first real frame. Use the type directly.
* **Overlapping vehicle boxes handed the same plate to several tracks** — 76
  cases in one clip at IoU up to 0.91, causing duplicate OCR *and* one car
  reporting as three vehicles. Plate regions are now deduplicated per frame and
  attributed to the smallest containing vehicle.
* **Evaluation scored raw tracks, not merged vehicles**, so a fragmented car was
  counted once as correct and once as spurious — penalising the pipeline for an
  artefact the merge step had already repaired.
* **The scheduler starved short tracks.** A vehicle visible for four frames got
  one look before the cooldown silenced it. A vehicle that has never yielded a
  plate now gets guaranteed attempts before any cooldown applies.
* **The benchmark generator overlapped its own sprites**, occluding two of eight
  plates so they could never be read. Several rounds of "missed plates" were the
  test rig, not the pipeline. Vehicles are now placed in separated lanes.
* **`inspect.py` as a filename shadows the stdlib**, breaking numpy's import.

---

## Phase 6 — Event engine, watchlist, alerts ✅

- [x] `EventBus` + Redis Streams with consumer groups; worker publishes, API consumes
- [x] Consumer: parse → broadcast to operators → persist detection → match → alert
- [x] Watchlist matcher: in-memory index refreshed on change, **deletion index**
      for near matches so lookup is proportional to plate length, not list size
- [x] Exact → `watchlist_hit` at the entry's priority; within one character →
      `possible_match` **capped at medium**
- [x] Validity windows enforced at match time, so a BOLO expiring between
      refreshes stops matching immediately
- [x] Alert raise → WebSocket push, broadcast **after** commit
- [x] Lifecycle new → acknowledged → dispatched → closed / false_positive, every
      transition recording who and when; illegal moves rejected
- [x] Deduplication per (plate, camera) over 90s, repeats counted on the original
- [x] Watchlist CRUD and alert triage endpoints, every mutation audited

### Gate — passed

Watchlisted plate injected → alert on a connected WebSocket client in **24 ms**
(budget: 2 s), carrying the case reference so no follow-up lookup is needed.

Verified live against the running stack:

| injected | result |
|---|---|
| `GJ03AB1234` (listed, stolen) | `watchlist_hit`, **critical** |
| `GJ03AB1284` (one character off) | `possible_match`, **medium**, with a verify-before-acting note |
| `GJ99ZZ0000` (unlisted) | no alert |
| `GJ01XY7788` (expired BOLO) | no alert |
| `GJ03AB1234` repeated, same camera | deduplicated into the original |

### Design decisions worth keeping

* **A near match is capped at medium however severe the entry.** OCR misreads a
  character often enough that requiring exactness loses real hits, but raising a
  maybe as critical teaches operators to distrust critical.
* **The same plate at a different camera is a new alert.** That is the vehicle
  moving, which is precisely what a cross-camera system exists to notice.
* **`false_positive` is an outcome, not a delete.** A system where operators can
  quietly erase mistakes cannot be audited, and those corrections are the data
  that improves the models.
* **Acknowledge / dispatch / close are separate permissions**, because in a
  control room they are separate authorities.

### Problems hit and how they were fixed (do not re-introduce)

* **`CurrentUser` is a value object with no `has_permission`.** Permissions come
  from the role matrix via `permissions_for(user.role)`.
* **FastAPI rejects a 204 endpoint annotated `-> None`.** Return `Response(status_code=204)`.
* **Watchlist entries must be stored normalised.** An entry typed
  "GJ 03 AB 1234" that is not normalised silently never matches — the worst
  possible failure for a BOLO.

---

## Phase 7 — Correlator: cross-camera tracking & route reconstruction 🟡

`app/services/correlator.py` + `app/routers/vehicles.py`. **50 tests** (35 unit on the
pure logic, 15 over HTTP).

- [x] Fetch detections by plate + window, ordered by ts, camera position joined
- [x] Cluster into hops, merging same-camera sightings within a 5-minute dwell window
- [x] Great-circle distance + elapsed time → implied speed per consecutive pair
- [x] Plausibility scoring — **marked, never dropped**
- [x] `GET /api/v1/vehicles/{plate}/route?since&until&format=json|geojson`
- [x] Straight-line segments explicitly labelled in the GeoJSON properties
- [x] Convoy detection (`/convoy`, thresholds are the caller's to set)
- [x] Unobserved-gap analysis (`unobserved_gap` flag)
- [x] `/routable` — which plates have enough sightings to have a route at all
- [x] `persist_route()` writes to `vehicle_tracks`
- [x] **Vehicle Search screen** — plate in, journey drawn on a satellite map with a hop
      table, plain-English reasons for every flag, and convoy partners
- [ ] **Gate not run:** needs a plate seen on several *separated* cameras

### The one argument this phase rests on

Distance is great-circle, not road distance. A road is never shorter than the straight
line between its endpoints, so **implied speed is a lower bound on the speed driven**.
That asymmetry is load-bearing:

* if the lower bound already exceeds what a car can do, the leg is **impossible** — the
  cloned-plate and misread signature, and the most useful thing the correlator finds;
* a leg that looks fine has only passed a weak test.

So `implausible` is a finding and `plausible` is merely the absence of one. The API does
not present them as symmetric, and the note travels in every response.

### Flags, and what each means

| Flag | Meaning | Makes the route implausible? |
|---|---|---|
| `implausible_speed` | Lower-bound speed exceeds 150 km/h | **Yes** |
| `impossible_simultaneous` | Same plate at two separated cameras at one instant | **Yes** |
| `revisit` | The vehicle returned to a camera it had already passed | No |
| `co_located` | Two *different* cameras < 50 m apart; speed would be position error | No |
| `unobserved_gap` | Over an hour between sightings — the vehicle went somewhere unwatched | No |
| `heading_conflict` | Camera faces more than 100° away from the direction of travel | No |

### Why the gate has not been run

The gate wants `GJ03AB1234` walking Rajkot → Gondal → Jetpur → Junagadh. That needs one
plate read on four separated cameras, and **no such data exists**: the organisers' grid
is returning 502, and when it is up its cameras cannot resolve a plate at all. The only
camera producing plates is the single demonstration feed.

Verified instead against the real detections that do exist — `NA13NRU` across
`CAM-00001` and `CAM-DEMO`: 4 hops clustered from 170 sightings, `co_located`,
`unobserved_gap` and `heading_conflict` all raised correctly, valid GeoJSON with
`[lon, lat]` ordering over Gujarat.

**To close this gate, one of:** the grid comes back *and* an ANPR-class camera is
available on it; or a second demonstration feed is added; or Phase 12's backfill lands,
in which case the seeded detections **must be labelled synthetic** wherever the route is
displayed.

Until then the screen says so itself: a route whose sightings are all on one camera
shows *"there is no journey to draw — a route needs the plate read on cameras in
different places"* rather than drawing a dot and leaving the operator to work out why.

### Two artefacts the screen had to be taught about

Both were found by looking at the rendered page rather than the tests, and both would
have embarrassed a live demo:

* **A revisit is not a co-location.** Four sightings on one camera were being reported
  as *"these cameras are within 50 m"* — a statement about camera installation, when
  there was only one camera and what actually happened is that the vehicle came back.
  Now a distinct `revisit` flag.
* **A convoy of near-identical plates is one car, not six.** OCR reading the same
  vehicle as `AP05JEO` and `AP05JE0` produces two plates that co-occur *perfectly* at
  every camera — the exact signature of a convoy. Partners within one edit of the
  subject are now marked `likely_same_vehicle`, sorted below genuine associations, and
  collapsed behind a disclosure rather than deleted: how often the reader disagrees
  with itself about a vehicle is worth an operator seeing.

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

## Phase 9 — Command centre UI 🟡

Brought forward ahead of Phase 7: the alerting backend was complete and had no
screen, so three judge moments could only be performed in a terminal.

- [x] Dark operations-centre theme, IST timestamps everywhere, Gujarati + English primary nav
- [x] Login (role-based)
- [x] GIS Map: clustered status-coloured markers, district choropleth, filters
- [x] **Live ANPR**: camera picker with provenance, video with plate overlay, live plate
      feed with per-read evidence, prior sightings for the camera
- [x] **Alerts**: priority-sorted, critical banner, live socket merged with the stored
      list, ack / dispatch / close / false-positive
- [x] **Watchlist Manager**: add with case reference and reason, amend priority, retire
- [x] WS reconnect with backoff and a visible feed status
- [x] **Dashboard** — the landing screen: honest KPI tiles, live plate ticker,
      alerts needing attention, fleet by department
- [x] Toasts replace inline error text; errors do not auto-dismiss
- [x] Loading skeletons, so an unloaded table never reads as an empty one
- [x] Keyboard triage on alerts (`j`/`k` move, `a`/`d`/`c`/`f` act)
- [x] CSV export on the audit trail, with RFC 4180 quoting and a UTF-8 BOM
- [ ] Video Wall: 2×2 / 3×3 / 4×4
- [ ] Vehicle Search + Vehicle Profile with animated route playback — needs Phase 7/8
- [ ] Camera Onboarding form + CSV with row-level errors
- [ ] Admin: users, VMS, audit viewer
- [ ] Architecture page — needs Phase 10's measured numbers
- [ ] PDF export; CSV on the remaining tables

**Verified in a browser:** login → 31 ANPR cameras listed with provenance badges →
event feed live → plate cards streaming with evidence → overlay boxes rendering →
watchlist add returns a correct 409 on a duplicate → alerts listed with criticals
banner. Zero console errors, zero failed requests.

### Bugs this surfaced

All were invisible to a passing suite, because nothing exercised the paths.

| Bug | Effect |
|---|---|
| Handlers annotated the user as `CurrentUser` (the bare model) instead of the `Annotated[…, Depends(…)]` alias | FastAPI expected it in the request body: **every mutating watchlist and alert endpoint returned 422** |
| `watchlist` and `alerts` mounted at the root, every other router under `/api/v1` | Both **unreachable through the web container's nginx** |
| `detection_id` read before flush, where a Python-side default assigns it | **Every watchlist alert recorded `detection_id=None`**, losing its link to the evidence |
| nginx forwarded `/api/` with a trailing slash, stripping the prefix | Split-origin dev hid it; a same-origin deployment would 404 on every call |
| `useCameraEvents` never cleared on camera change | The previous camera's plates showed under the new camera's name — for an unreachable feed, invented results |

---

## The grid's two authentication systems ✅

The grid authenticates **two different ways**, and treating them as one is why
the platform looked broken while the grid was fine:

| Path | Host | Authenticates with |
|---|---|---|
| RTSP (inference) | `103.250.160.189:8554` | username/password in the URL |
| Catalogue + HLS | `cctv.corp8.cloud` | a **login session cookie** |

The RTSP credentials are not accepted by the CDN — Basic auth there returns a
302 to the login page. The CDN wants the form POST a browser makes and returns
a `nagarnetra=` cookie. Media paths additionally refuse any request that does not
look like a browser; the 403 body is literally `browser required`.

That explained both visible symptoms at once:

* **Health monitoring** fetched the catalogue, got a login page, and left all
  30 cameras `unknown` — so the map showed 1 online out of 281.
* **Video playback** pointed the browser straight at the CDN, where it has no
  session, so the player reported *"No video is being published"* for a camera
  that was publishing perfectly.

- [x] `app/services/grid_session.py` — logs in, holds the cookie, re-logs when
      it lapses. The only place in the platform holding those credentials
- [x] The catalogue fetch uses it → **all 30 grid cameras now report online**
- [x] `app/routers/grid_media.py` — an authenticated HLS proxy. The browser
      presents the same short-lived, camera-scoped stream token it uses
      everywhere else; the grid's credentials never leave the server
- [x] Catalogue cached for 20 s → **30 fetches per health sweep became 4**.
      The later requests in a sweep had been timing out and recording healthy
      cameras as unreachable
- [x] The dead `/grid/` nginx block and its `throughProxy` helper removed —
      both pointed at a host that no longer serves us

### Two bugs the work surfaced

**The token was in the query string.** A player resolves `seg00123.ts` against
the *path* of its playlist and drops the query doing so, so the playlist loaded
and every segment then arrived unauthenticated — a 422 that looked like a
malformed request and was really a lost credential. The token moved into the
path, where relative resolution carries it for free and 7,200 segments do not
each need a JWT appended.

**The stream grant handed the browser the grid's password.** `rtsp_url` carries
credentials because RTSP has nowhere else to put them, and it was being
returned verbatim — undoing the entire point of proxying HLS with the field
directly beneath it. Caught by a test written for exactly that, now redacted by
`app/core/urls.py`.

**Measured:** 31 cameras online (30 grid + demo), 0 offline. Playlist, AES key
and segments all 200 through the proxy; a browser plays `SBX-00001` at
1920×1080 with `readyState 4` and no console errors. A stream token for one
camera returns 403 on another.

---

## The grid started requiring credentials ✅

On 3 Sep the grid began rejecting anonymous RTSP with **401 Unauthorized**; it
had been open the day before, and the integrator guide still describes it as
needing no registration.

**Our side reported this as "SBX-00001 is unreachable".** That single word cost
the most: it sends whoever reads it to check the network when the real answer
is a password. OpenCV returns a bare `False` for a refused connection, a DNS
failure, an authentication rejection and a missing path alike, so nothing
downstream could tell them apart.

- [x] `reader.diagnose()` reproduces the first RTSP `DESCRIBE` (or an HTTP GET)
      by hand and reports **unauthorized / not found / timeout / no route /
      opened but no media**
- [x] `StreamUnavailable` carries the reason; the worker prints what it means
      for whoever is reading — *"the far end rejected our credentials"*
- [x] `SANDBOX_RTSP_USERNAME` / `_PASSWORD`, read from `.env` (gitignored),
      documented empty in `.env.example`. Empty is a valid configuration: the
      grid was anonymous until this week
- [x] **`reader.redact()` on every log line that prints a source**

**The credentials leaked into the worker log on the first attempt** — 15
occurrences, from three separate call sites that each had to be found by hand.
`tests/test_stream.py::TestCredentialsNeverReachTheLog` now greps the source
for any `log.*` call that prints a stream URL without redacting it, and was
verified to fail against a reintroduced leak. It is scoped per file, because
`self.source` is the URL in `reader.py` and a `SourceIdentity` object in
`runner.py` — conflating them produced a confident false positive.

**Measured after the fix:** 10 grid cameras producing detections within ten
minutes, 0 occurrences of the token in any log, and the log showing
`rtsp://vaibhav.r.makvana%40gmail.com:***@103.250.160.189:8554/…` — host and
camera still legible for diagnosis.

---

## Accounts, RBAC in the interface, and the audit viewer ✅

Three controls existed on paper and not in the product.

**The interface ignored the RBAC the API enforces.** `hasPermission()` had been
in `useAuth` since Phase 1 and no screen called it, so every role saw every tab:
an auditor was offered Live ANPR and Vehicle Search and refused on arrival, and
an analyst was shown the watchlist add form and refused on submit. Teaching a
control room that this system's errors are noise is expensive.

- [x] Navigation filtered by permission; `RequirePermission` guards each route
      so a typed URL refuses cleanly and *names the missing permission*
- [x] Write controls gated — no "Add to watchlist" for a role that will be
      refused, no alert transition a role cannot make
- [x] Verified per role in a browser: **0 unexpected 403s across all five**

**There was no account administration at all.** No endpoint could create a
user, assign a role, deactivate an account or reset a password.

- [x] `/api/v1/users` — create, amend, reset, delete, all audited
- [x] An administrator cannot deactivate or demote **themselves**
- [x] An account that has acted **cannot be deleted** — it would orphan every
      audit row naming it; the API insists on deactivation instead
- [x] `must_change_password` (migration `0002`): a password an administrator
      chose is a shared secret, and nothing the account does is attributable
      until the holder replaces it
- [x] Password policy rejects **the credential documented in this repository**,
      passwords containing the username, repeated characters and keyboard runs

**`AUDIT_READ` gated nothing.** It was a permission with no endpoint.

- [x] `/api/v1/audit` with filters, and the screen behind it
- [x] **Reading the trail is itself audited** — a reviewer who leaves no trace
      is a hole in the control

### Bugs this surfaced

| Bug | Effect |
|---|---|
| Middleware derived actions from the URL path (`users.create`) while routers recorded the singular (`user.create`) | **Two names for one event.** A reviewer filtering `user.` silently saw half the entries |
| `<select>` had no `api_client` option | The camera-onboarding machine identity **rendered as "admin"** |
| `AuditEntry.ip` typed `str`, column is `INET` | Every audit read returned **500** |
| Tests created accounts and never removed them | Ten `test.*` accounts accumulated **in the demo database** |

**382 API tests pass** (29 new), `make lint` clean.

---

## The grid moved, and RTSP was never blocked ✅

On 2 Sep the organisers published a revised integrator guide. The change that
matters is not the new hostname but the **split of media from metadata**:

    catalogue   https://cctv.corp8.cloud/cameras.json     (CDN, password)
    HLS         https://cctv.corp8.cloud/<id>/index.m3u8  (CDN, password)
    RTSP        rtsp://103.250.160.189:8554/stream/<id>   (direct)
    WHEP        http://103.250.160.189:8889/stream/<id>/whep (direct)

Their guide states the reason plainly: RTSP and WebRTC "carry media over
TCP/UDP that a CDN cannot proxy", so they are served on a static IP.

**This invalidates a conclusion held since Phase 5.** The adapter derived all
four URLs from one hostname, so it probed `rtsp://<cdn-host>:8554`, found the
port shut, and recorded that the grid was RTSP-blocked and HLS-only. The port
was never blocked — we were knocking on the CDN. Both 8554 and 8889 are open
and always were.

- [x] `HostedGridAdapter` models the two hosts separately, with the reason
      in the docstring so it cannot be "simplified" back
- [x] Catalogue path falls back `/cameras.json` → `/api/ingest`; a redirect to
      a login page is reported as a login page, not as "unreachable"
- [x] The grid's own camera id is **stored** (`grid-id:` tag) rather than derived
      from our camera code — their ids changed `7` → `cam07` and will again
- [x] `scripts/retarget_grid.py` moves the 30 registered cameras to the new
      scheme, tagging the inferred ids as inferred
- [x] Worker probes and uses RTSP directly

**Measured:** all 30 cameras respond to `ffprobe` over RTSP — 24 × H.264,
6 × HEVC, resolutions 960×576 to 2560×1440, most 1080p. The worker now
processes them live: *"RTSP to 103.250.160.189 is reachable"*, *"processing
SBX-00001 [Chiman bhai Bridge] over rtsp"*, and detections from Chiman bhai
Bridge, Janpath, Paldi Circle and Visat teen Rasta are in the database.

---

## The fleet: real feeds only ✅

The ANPR fleet is the organisers' 30 grid cameras plus one clearly labelled
demonstration feed. Nothing else has video attached.

- [x] `scripts/shape_fleet.py` — idempotent. Creates `CAM-DEMO`, moves seeded cameras
      off the federated VMS they were never part of, and restricts `anpr_enabled` to
      cameras with a real source
- [x] Simulator publishes **only** cameras with footage pinned to them
      (`SIM_STREAM_COUNT=0` by default)
- [x] `app/services/fleet_roster.py` — the API publishes the fleet to Redis; the worker
      reads it. No ORM in the minimal worker image, no service credential, and a
      federated camera stays in the fleet while its gateway is down
- [x] `ai_worker/rotation.py` — cameras beyond the slot count are **rotated, not
      dropped**, and the coverage is stated rather than implied
- [x] Plate formats are a property of the camera (`plate-region:GB`), not a worker-wide
      setting
- [x] RTSP vs HLS probed once per host by the worker

**Measured:** all 30 grid cameras attempted and reported unreachable while the grid
returns 502; `CAM-DEMO` read 572 plates in 20 minutes of which 406 are grammar-valid;
coverage reported as *"31 cameras across 3 slots, each watched 45s every 11.2 min"*.

**Gate for the rest of Phase 9:** full click-through of the five judge moments with no
console errors.

---

## Phase 10 — Scale profile & the 80,000-camera proof ✅

- [x] `docker-compose.scale.yml` — API stops ingesting (`INGEST_ENABLED=false`),
      three dedicated `consumer` replicas take it over. Redpanda stays behind the
      same interface for a production bus; the measurement runs on Redis Streams,
      which is what the demo ships with.
- [x] `services/simulator/simulator/load_mode.py` — 80,000 camera identities,
      configurable rate, payloads the exact shape and size of real events
- [x] `tests/load/run_load_test.py` — seeds the fleet, drives the generator,
      samples the API, drains, reports, and **tears down what it created**
- [x] `tests/load/k6/api_load.js` — operators working while ingest runs
      (`make load-operators`, containerised so no k6 install is needed)
- [x] Batched DB writes — one transaction per batch, not per event
- [x] Bandwidth arithmetic in the docs: 320 Gbps vs ~43 Mbps (~7,000×)

### Measured — 80,000 camera identities, 3 workers, one laptop, CPU only

| | |
|---|---|
| Offered | 2,664 events/s |
| **Ingest sustained (median)** | **2,774 events/s** |
| Range | 2,280 – 4,002 events/s |
| Consumed / written | 388,748 / 388,713 |
| **Failed** | **0** |
| Alerts raised under load | 397 |
| Backlog peak → after generator stopped | 10,237 → cleared |
| p50 / p95 / p99 capture→persisted | 4,812 / 5,670 / 5,904 ms |

**Gate: throughput met, latency not.** ≥2,000 events/s sustained — yes, 2,774.
p95 under 3 s — no, 5.7 s. The gap is queue wait, not processing: a backlog
forms and then clears completely. Reported rather than tuned away, per the
phase's own honesty clause.

### Three bugs this phase found that 443 tests did not

**`upper(camera_code)` defeated the index.** Every ingested detection resolves a
camera code to an id, case-insensitively — MediaMTX lowercases stream paths
while the registry stores uppercase. `upper(camera_code) = ...` cannot use a
plain index. At 281 cameras: invisible. At 80,000: **106 ms per lookup, 80,278
rows discarded**, ingest collapsed from ~1,500 events/s to 140. Migration
`0003` adds an expression index; throughput recovered 7.5×.

**Two API replicas would have split the live event feed.** `EventConsumer` both
persisted events and fanned them out to operators, both through a consumer
group — which hands each entry to exactly *one* member. Correct for
persistence, exactly wrong for a shared operations picture. Two replicas would
each have shown their operators half the state's traffic with nothing reporting
a fault. Now `EventTailer` (plain `XREAD`, every replica sees everything) is
separate from `EventConsumer` (the group, exactly once), and alerts travel on a
pub/sub channel so one raised by any worker reaches every replica.

**Unknown cameras were cached forever.** A camera code the registry did not
know was cached as "no such camera" for the life of the process. Cameras are
onboarded *while the platform runs* — that is the point of the registry — so
every detection from a newly-onboarded camera would have been stored
unattributed until a restart, with nothing indicating a fault. Negative lookups
now expire after 60 s; positive ones are kept, because a camera's id does not
change.

### And two in the test itself, worth recording

`XLEN` was used for backlog and reported a permanent 100,000-event queue on a
consumer that was fully caught up — the stream is length-capped, so `XLEN` sits
at the cap regardless. The consumer group's `lag` is the right measure.

The verdict compared the final backlog to twice the *first sample's*, and the
first sample was already 50,000 deep — so it certified "sustained the target"
on a run doing 180 events/s against 2,667. It now judges throughput against
what was actually offered. A load test that grades itself generously is worse
than none, because it is believed.

---

## Phase 11 — Documentation & submission artifacts ✅

- [x] `docs/HLD.md` — four reference models with the Model-5 justification, the
      320 Gbps ÷ 43 Mbps arithmetic, container and deployment diagrams, the data
      path, ten failure modes, and a measured-performance table
- [x] `docs/INFRASTRUCTURE.md` — per-district bandwidth, compute sizing from
      measured throughput, storage tiering with capacity maths, availability
- [x] `docs/SECURITY.md` — RBAC matrix, audit trail, retention, rate limiting,
      lawful-use safeguards, **and §8: what is not implemented**
- [x] `docs/API.md` — generated by `scripts/generate_api_docs.py` from the live
      OpenAPI document (`make docs`), so it cannot drift silently
- [x] `docs/DEMO_SCRIPT.md` — eight minutes, five judge moments, a fallback per
      step, and §8 "what not to claim"
- [x] `README.md` — already referenced these; they now exist

### The rule these documents follow

Every number is labelled **measured**, **arithmetic**, **estimated** or
**unproven**, and each document carries a section listing what it does *not*
have:

| Document | Its own gaps section |
|---|---|
| `SECURITY.md` | §8 — no TLS, no encryption at rest, no backups, AGPL weights, no pen test |
| `INFRASTRUCTURE.md` | §6 — the summary of what is unproven, including the un-run load test |
| `HLD.md` | §7 — "Phase 10's load test has not been run", stated in the scaling section itself |
| `DEMO_SCRIPT.md` | §8 — "what not to claim", four specific overreaches to avoid on stage |

A security document that omits its gaps is worse than none, because it stops
anyone looking. The same reasoning applies to the rest.

**Verified:** every internal link across all six documents resolves.

---

## Phase 12 — Demo hardening ✅

- [x] `make demo` → `scripts/demo_up.sh`: containers → migrate → seed → `shape_fleet.py`
      → `retarget_grid.py` → simulator → ai-worker → **verifies each judge moment before
      claiming ready**. Measured: 281 cameras on the map, 31 in the ANPR fleet.
- [x] Synthetic plates with ground truth → accuracy report. Built in Phase 5 under
      `ai-lab/scripts/make_test_footage.py` + `ailab/evaluate/groundtruth.py`, not the
      `scripts/generate_synthetic_plates.py` the plan named. Result (100% exact, CER 0.000)
      is in `docs/HLD.md` §6, labelled optimistic-by-construction.
- [x] `tests/e2e/test_judge_flow.py` — all five judge moments, headless. 17 tests.
- [x] `PANIC.md` — projector / network / grid / GPU failure, with a triage path per component.

**Gate passed.** `make demo` reports "Demo ready"; `make test` runs the e2e suite last and
all four suites are green: 389 API + 54 worker + 33 e2e + 24 frontend.

### Two things this phase found that the other 443 tests did not

**The demo path had never been run end to end.** `make demo` previously called a
target that assumed a populated database. A judge cloning fresh would have got an
empty map. Every phase gate had passed against a database that earlier phases had
already filled.

**Test pollution reached the demo data.** The watchlist held **151 stray `GJ01*`
entries** written by API tests that never cleaned up, alongside the 5 real ones — so
the Watchlist screen a judge opens was 97% test litter. Fixed with an autouse
pattern-scoped cleanup fixture; the same class of bug had already been fixed once for
`test.*` users, which is why it is worth stating that a passing suite can still be
degrading the product it tests.

### And one about timestamps

The e2e suite failed 7 of 17 on `datetime.isoformat()`, which emits `+00:00`; an
unencoded `+` in a query string decodes to a space, so the API correctly rejected it.
The frontend never hit this because `toISOString()` emits `Z`. Worth knowing before
someone writes a curl example into the docs.

---

## Phase 13 — Person & face detection, crowd counting

Required by the challenge (FAQ Q22: "ANPR, face recognition, crowd/vehicle
counting, anomaly detection"). Sequenced **after** ANPR because the scored live
test case (Q26–28) designates a *vehicle*, not a person.

- [ ] Person detection on the same ONNX pipeline as vehicles
- [ ] Crowd density / occupancy counting per camera and per zone
- [ ] Face detection with quality gating (blur, pose, minimum resolution)
- [ ] Face matching against an enrolled watchlist, behind its own permission
- [ ] Every match written to `audit_log`; no silent enrolment
- [ ] Retention limits enforced separately and more strictly than vehicle data
- [ ] UI clearly labels confidence and never presents a match as an identification

**Controls this phase must ship with, not after:** face recognition against a
person database is the most privacy-sensitive capability in the platform. It
inherits the existing RBAC and audit trail and adds a dedicated permission,
so holding `search.execute` does not imply the ability to search faces.

**Gate:** person and face detection measured on held-out footage with reported
precision/recall; every match produces an audit row; a role without the face
permission receives 403.

---

## Phase 14 — Government database integration

FAQ Q22 also requires cross-referencing **VAHAN, SARTHI, eGujCop, AFIS, NAFIS**.

- [ ] `GovDatabaseAdapter` interface mirroring the `CameraAdapter` pattern
- [ ] VAHAN adapter (vehicle registration) — owner, make/model, insurance, status
- [ ] SARTHI adapter (driving licence)
- [ ] eGujCop adapter (case/FIR linkage)
- [ ] Local reference dataset standing in for the live services
- [ ] Every lookup audited, rate-limited, and attributed to a case reference
- [ ] UI states plainly when data came from the local stand-in, not the live registry

**Honesty constraint:** the real endpoints are not publicly reachable. We build
the real interface and a clearly-labelled local dataset behind it. The UI must
never imply we hold live access to national databases. Swapping in the real
service is a configuration change, exactly as with the camera adapters.

**Gate:** a plate search returns enriched registration detail; the source is
labelled; the lookup appears in `audit_log`; the adapter interface is proven
swappable by a test double.

---

## The ANPR demonstration

`scripts/demo_anpr.py` — run it inside the api container, which has the dependencies:

```
docker compose exec -e NAGARNETRA_API_URL=http://api:8000 api \
    python /app/scripts/demo_anpr.py --plate NA13NRU
```

Prerequisites: `make videos` (fetches and cuts `anpr_demo.mp4`), the simulator publishing
`CAM-00001` (pinned by `SIM_CAMERA_VIDEOS` in compose), and a worker on it:

```
docker compose --profile ai run -d --rm \
    -e AI_WORKER_SOURCE=mediamtx -e AI_WORKER_CAMERAS=cam-00001 \
    -e AI_CONFIG=stream_demo --name nagarnetra-ai-demo ai-worker
```

What it proves, and why each step is not staged:

1. It reads back what the **live pipeline** has written to `detections` — it is never told which
   plates are in the footage.
2. It puts one on the watchlist **over the API** as `supervisor`, so RBAC is checked, an audit row
   is written, and the in-memory matcher is invalidated at once.
3. It waits. Nothing is injected. The clip loops; when the car passes again the pipeline reads it
   afresh.
4. The alert is whatever the platform raised by itself.

Measured on this machine: `NA13NRU` at confidence 0.99–1.00, alert raised 33–99 s after arming
(that interval is how long until the car came round again, **not** pipeline latency — detection to
alert is measured in the Phase 6 tests).

---

# SIH26127 phase evidence

Append each phase's real gate output here as it completes. A phase is not done until its evidence is
in this section. Keep the format: what was built, the gate command, the actual output, and anything
found along the way that the next session must not re-discover.

## P1 — Multi-camera Ahmedabad fleet  ✅

Definition and gate: [ROADMAP.md](ROADMAP.md#p1--a-real-multi-camera-ahmedabad-fleet--do-this-first).

### What was built

- `scripts/fetch_ahmedabad_geodata.py` — OSM via Overpass, with mirror fallback and
  skip-if-present. Writes three committed files: 7 city talukas, 2,319 arterial ways,
  102 localities.
- `scripts/generate_ahmedabad_cameras.py` — 58 cameras on 12 real named corridors
  (Sardar Patel Ring Road, 132 Ft / 120 Feet Ring Roads, Ashram Road, SG Highway,
  CG Road, Naroda Road, Narol–Sarkhej, Airport Road, Ahmedabad–Vadodara Expressway…).
- `seed_fleet()` in `scripts/seed.py`, loading the CSV through the real bulk-import path.
- ffmpeg **copy-mode** and one-time **clip preparation** in the simulator publisher.
- Gap analysis moved from Gujarat's 33 districts to the 7 city talukas.

### Gate output — from empty volumes (`docker compose down -v` first)

```
▸ Starting dependencies      ✓ postgres, redis, minio, mediamtx, opensearch healthy
▸ Schema and seed data       ✓ migrations at head · ✓ seed data loaded
▸ The camera fleet           ✓ 59 cameras seeded across the city corridors
▸ Demonstration footage      ✓ 51 cameras publishing live video
▸ Verifying                  ✓ 51 cameras in the ANPR fleet
                             ✓ 5 plate reads in the last 10 minutes
                             ✓ command centre reachable
make demo: 1m29s
```

| Gate item | Required | Measured |
|---|---|---|
| Cameras online in fleet health | ≥ 40 | **51** |
| Live ANPR feeds | ≥ 6 | **51** |
| A plate on ≥ 3 distinct cameras | 1 | **`EYGINBG` on 3** (480 detections across 15 cameras, 2 min after the worker started) |

Three consecutive clean-volume `make demo` runs, all green.

### Placement quality (measured, not asserted)

- **58/58 cameras sit exactly on a real OSM road vertex** (max distance 0.00 m).
- Closest pair **362 m**, median nearest-neighbour **1,729 m** — clear of the
  correlator's 50 m `co_located` threshold.
- Sardar Patel Ring Road: 17 cameras, all 9–14 km from their own centroid,
  occupying **8/8 compass octants** — a genuine ring.
- All **7 talukas** covered: Sabarmati 13, Vatva 11, Asarva 9, Daskroi 8,
  Vejalpur 8, Ghatlodiya 6, Maninagar 3.

### Cost of the live fleet

51 concurrent streams for **~1.1 cores** (simulator 67%, MediaMTX 47%). Re-encoding
would have cost roughly a tenth of a core *per stream*, capping the fleet at a handful.

### Tests

| Suite | Before | After |
|---|---|---|
| API | 384 passed, 12 failed | **396 passed, 0 failed** (twice consecutively) |
| e2e | 29 passed, 4 failed | **34 passed, 0 failed** |
| ai-worker | 54 passed | 54 passed |
| frontend | 24 passed, tsc clean | 24 passed, tsc clean |
| ruff check | 4 pre-existing errors | 4 pre-existing errors (no new) |
| ruff format | 6 api + 4 scripts | 5 api + 3 scripts (two incidental cleanups) |

### Five bugs found and fixed along the way

1. **`make demo` could never work from a fresh clone.** It ran `docker compose up -d`
   and then waited for the API, but the API queries `cameras` during startup, the table
   does not exist until migrations run, and migrations were run through `compose exec`,
   which needs a healthy container. Compose returned non-zero, `set -e` fired, and the
   script died 15 s in with "Error 1" — on exactly the path a judge takes. Dependencies
   now start first, migrate and seed run via `compose run`, then the app tier starts.
2. **The dependency wait was a race.** `up -d` returns when containers *start*, so
   migrations raced a Postgres still initialising a fresh data directory: it failed on one
   clean run and passed on the next purely on timing. Now `up -d --wait`.
3. **Cross-corridor camera collisions.** Per-corridor thinning put three cameras within
   **13 m** where Ashram Road, Nehru Road and the 132 Ft Ring Road meet, and a pair at
   45 m — under the correlator's 50 m threshold, so hops between them would have been
   flagged `co_located` and carried no implied speed. A fleet-wide 250 m minimum now applies.
4. **A corrupt clip silently killed cameras.** `data/videos/test2.mp4` is a truncated
   download (exactly 8 MiB, unreadable header) and, because clips are handed out
   round-robin, it took down the two cameras it was assigned to after five restarts.
   `find_videos` now screens clips through ffprobe.
5. **The stream check guessed instead of waiting.** A fixed `sleep 8` reported "0 streams
   publishing" on every fresh clone, because clip preparation takes tens of seconds on a
   cold cache. It polls now.

### Two findings that shape later phases

**Plausible cross-camera journeys are not achievable with the current footage.** The only
clip with legible plates is **15 s** long and the fleet's median camera spacing is 1.7 km,
so any offset that fits inside the clip implies **~415 km/h** — the correlator would
correctly flag every hop as `implausible_speed`. The 60 s clips would give a plausible
104 km/h but they are the 4K ones whose plates are not legible. Stream offsets are
therefore used only to *desynchronise* cameras sharing a clip, which removes false
`impossible_simultaneous` readings; they do not simulate a journey. **P2/P5 need either
longer footage with legible plates (P7 sources it) or a scheduled replay
(`scripts/replay_history.py`).**

**`stream_url` must stay NULL for simulator-served cameras.** `gateway.reconcile()` runs at
API startup and opens a MediaMTX *pull* path for every camera with a `stream_url` whose
adapter is not federated — and `simulated` is not in `FEDERATED_ADAPTERS`. Giving these
cameras the gateway's own address makes MediaMTX pull each path from itself: an infinite
loop that silently blocks publishing for the **whole fleet**, on every restart. The
simulated adapter derives every URL from `camera_code` and probes health by asking
MediaMTX which paths are live, which is a better check than a stored string.

---

## P2 — Journey profile + animated playback  ✅

Definition and gate: [ROADMAP.md](ROADMAP.md#p2--journey-profile--animated-playback).

### What was built

- **Route-level speed.** `Route.average_kmph` (first sighting to last, so it includes
  dwell) and `Route.moving_kmph` (legs that covered ground only), plus `moving_s`. No
  route-level speed existed before — the only figure anywhere was per-leg `implied_kmph`.
- **`persist_route` is finally called.** New `app/services/route_history.py` snapshots
  journeys from the health-monitor daemon, every 4th sweep (~1 min), bounded to 40 plates
  a cycle and skipping routes that have not changed.
- **Journey profile on screen.** `VehicleSearch.tsx` now renders first seen, last seen and
  average speed. `first_seen`/`last_seen` were already in the API payload and in the TS
  type, and were displayed nowhere.
- **Timeline playback.** `RouteMap.tsx` gains play/pause, a scrubber, an IST clock, a
  moving vehicle marker and a solid "travelled so far" line over the dashed inferred legs.
- **`web/src/lib/journey.ts`** — the interpolation extracted as pure functions so it can be
  tested. A wrong interpolation makes the map lie *smoothly*: the marker still glides along
  the route, it is simply in the wrong place at the wrong time.

### Playback follows elapsed time, not hop count

The property the module exists for, and the one the tests pin. A four-minute leg takes
eight times as long to cross as a thirty-second one. Stepping uniformly per hop would be
simpler and would misrepresent the journey — a vehicle that sat at a junction for ten
minutes would appear to drive straight through.

### Gate output

```
GET /api/v1/vehicles/EYGINBG/route?window_hours=24

  first_seen      2026-09-09T14:22:25Z      OK
  last_seen       2026-09-09T14:34:29Z      OK
  camera_count    5                         OK
  distance_km     25.86                     OK
  duration_s      723.6                     OK
  average_kmph    128.7                     OK
  moving_kmph     128.7                     OK
  timeline        14:22:25 → 14:22:27 → 14:26:26 → 14:30:30 → 14:34:29
```

Every field the PS's Vehicle Profile asks for, with a real 12-minute timeline for playback
to animate.

`vehicle_tracks` went from **0 rows, ever** to a populated history: 17 journeys, up to 5
hops each, geometry validated as `ST_LineString` / SRID 4326 / `ST_IsValid` true, spanning
24–44 km.

### Tests

| Suite | Before P2 | After |
|---|---|---|
| API | 396 | **402 passed** (6 new: `TestJourneySpeed`) |
| frontend | 24 | **40 passed** (16 new: `journey.test.ts`) |
| e2e | 34 | 34 passed |
| ai-worker | 54 | 54 passed |
| ruff check | 4 pre-existing | 4 pre-existing |

**Verification note.** The playback UI was verified by `tsc --noEmit`, by 16 unit tests over
the interpolation arithmetic, and by confirming the API returns every field it consumes —
**not** visually. Repeated attempts to drive a headless browser failed on Chromium download
timeouts, which is network friction rather than a code problem. A visual pass is still
worth doing before the demo.

### Three bugs found

1. **`persist_route` had never worked.** Its first ever execution failed outright:
   `function st_makeline(unknown) is not unique`. SQLAlchemy renders a Python list as an
   untyped array and PostGIS has several `ST_MakeLine` overloads, so Postgres refused the
   call. Nobody noticed because the function had no caller. Now built as WKT and passed as
   a bound parameter.
2. **`moving_s` counted parked time as moving time** — caught by the test written for it.
   A vehicle seen at one camera and again at that same camera 90 minutes later produces a
   `revisit` leg: 90 minutes across 0 metres. Summing every leg's elapsed time made
   `moving_kmph` identical to `average_kmph` on exactly the journey the two figures exist
   to tell apart. Zero-length legs are now excluded.
3. **The AI worker OOM-killed at 6 concurrent cameras** (exit 137), which silently stopped
   all detection for two hours and looked like an e2e timing flake. At the compose default
   of 3 it is stable but heavy: **855% CPU and 1.9 GB**. Raising `AI_WORKER_MAX_CAMERAS` on
   this hardware is not free, and the e2e watchlist test now targets the pinned camera so
   it does not depend on rotation luck.

---

## P3 — Evidence crops in MinIO  ✅

Definition and gate: [ROADMAP.md](ROADMAP.md#p3--evidence-crops-in-minio).

### What was built

`Detection.crop_key` had been NULL for the life of the project, and three
separate things had to be true before it could be anything else.

- **`ai_worker/crops.py`** — a `MinioCropStore` that duck-types the lab's run
  directory (`save_plate_crop(image, name) -> str`), so `ailab` needed no change
  at all. The worker previously passed no `run_dir`, so the runner substituted
  `_NullRunDir` and discarded every crop silently.
- **`configs/stream.yaml`** had `save_plate_crops: false`. That was correct when
  streaming meant latency measurement and crops meant JPEGs on disk; it meant the
  crop code could never run. Now true, capped at 2 per track rather than the
  batch default of 25. Vehicle crops stay off — several times the size, shown on
  no screen.
- **`api/services/evidence.py`** — signs a short-lived URL per crop.

### Why the worker uploads, not the consumer

Putting crop bytes on the event bus was the obvious alternative and would have
broken the architecture's one load-bearing claim. A detection event is ~2 KB; a
15 KB JPEG base64-encoded is ~20 KB, so crops on the bus multiply event traffic
roughly tenfold and "the central tier carries events, not video" stops being
true. Crops go from the edge straight to object storage; only the key travels.

The upload is asynchronous but the key is not: `save_plate_crop` is called from
inside the inference loop, on a worker measured at 855% CPU, so a blocking PUT
there would add network latency to every frame that resolved a plate. The key is
derived from camera, date and crop name — knowable before the bytes land — and
two background threads drain a **bounded** queue. Bounded because an unbounded
one grows until the worker is OOM-killed, which has already happened once and
takes every camera down with it.

### Gate output

```
blacklist a plate being read right now:  BG65USJ
  watchlist add                          HTTP 201
  ALERT BG65USJ · watchlist_hit · critical      (fired by itself)
  crop_url                               PRESENT
  crop fetches                           HTTP 200, 1977 bytes, image/jpeg
```

The fetched image is a legible plate reading **BG65 USJ** — the same plate the
alert names. Verified twice, on `EY61NBG` and `BG65USJ`.

Crops flowing: **68 of 193** detections in a two-minute window carried a key
(crops are only saved for plates that were actually read, so this tracks the
plate-read rate), 153 objects in the bucket at ~1.5–2.2 KiB each, zero upload
failures logged.

### Tests

| Suite | Before P3 | After |
|---|---|---|
| API | 402 | **408 passed** (6 new: `test_evidence.py`) |
| ai-worker | 54 | **61 passed** (7 new: `test_crops.py`) |
| frontend | 40 | 40 passed, tsc clean |
| ruff check | 4 pre-existing | 4 pre-existing |

### Three things worth not rediscovering

1. **SigV4 signs the `Host` header.** The first version signed against the
   internal `minio:9000` and rewrote the host to `localhost:9000` for the
   browser, which invalidates the signature — and fails as a 403 that looks
   exactly like a missing image. Sign against the address the browser will
   actually request; the signing client never connects, so an endpoint this
   process cannot reach is fine. `test_evidence.py` pins this.
2. **An `<img>` cannot send an Authorization header.** That is why the crop URL
   is signed and returned inline rather than served from a protected endpoint,
   and why `crop_url` appears on `DetectionOut`, `AlertOut` and the WebSocket
   event. Presigning does no I/O — it is an HMAC — so signing a page of 200
   alerts costs microseconds, where a `head_object` per row would cost 200 round
   trips.
3. **Tests must run where the code does.** `crops.py` imported `cv2` and
   `ailab.logging` at module scope; the shared test image (the API container)
   carries neither, so the whole test file was uncollectable. Worse, the
   worker image has no pytest, so tests needing real OpenCV would have run in
   **neither** image. The encoder and the thread count are now injected, which
   leaves the behaviour that matters — key format, bounded queue, never
   returning a key for a dropped crop — testable everywhere.

---

## P4 — City traffic analytics  🟡 code complete, gate not yet run

Definition and gate: [ROADMAP.md](ROADMAP.md#p4--city-traffic-analytics--biggest-missing-module).

**Honest status up front, per CLAUDE.md §8.2: a phase is complete when its
gate command passes, not when the code exists.** Everything below was built
and reasoned through carefully, but this session had no Docker and no
Node/npm available — there was no way to run `make demo`, hit the analytics
page in a browser, or run the new pytest suite against a live Postgres. The
gate — "the analytics page shows non-zero density, per-corridor average
speed, a populated route-density table and a heatmap, every figure traceable
to real rows" — is **not yet confirmed**. That confirmation is the actual
next step, on a machine that can run the stack.

### What was built

Backend (`dc77c47`, prior session): migration 0004 (`corridor` column,
backfilled), the 12-camera / 4-corridor fleet, and the six
`/api/v1/analytics/*` endpoints (`flow`, `speed`, `routes`, `travel-time`,
`hotspots`, `heatmap`) — see `services/api/app/routers/analytics.py` and
`app/schemas/analytics.py`.

This session, closing the gap that section left open ("the page and the
heatmap layer are not here yet, and neither are the endpoint tests"):

- **`services/api/tests/test_analytics.py`** — new. Every test builds its own
  `vehicle_tracks` / `detections` rows inside a fixed historical window
  (2024-03-04) rather than trusting whatever the simulator happens to have
  produced, so the suite is deterministic against a live, concurrently-running
  demo with no mocking. Covers: the `DISTINCT ON` dedup trap directly (a
  plate re-persisted three times must contribute one leg, not three — proven
  on `/speed`'s `samples` field and `/routes`' `median_gap_seconds`, not on
  `journeys`, which is a Python `set` and would mask the regression);
  implausible-leg exclusion (an excluded leg produces no `SegmentSpeed` at
  all, and never reaches a median); `insufficient_history` vs
  `insufficient_data` as two genuinely different code paths on
  `/travel-time` (no baseline vs no current leg); RBAC (every role but
  `api_client` reads; `api_client` is refused); and the `bucket`/`group_by`
  params on `/flow`, including the documented silent fallback for an unknown
  bucket string.
- **`web/src/pages/Analytics.tsx`** — new. Flow (stacked area, recharts, one
  series per corridor), corridor speed (bar chart for `status="ok"`
  corridors only, with a `Badge` list naming the rest and why — a chart
  never draws a bar for a figure that doesn't exist), route density (table),
  travel-time (a card grid, `insufficient_history` rendered as exactly that,
  never a zero delta), and busiest cameras. Registered at `/analytics` in
  `App.tsx`, next to `/map`, gated on the new `analytics.read` permission
  (added to `web/src/lib/permissions.ts` — every role but `api_client`, per
  `rbac.py`).
- **Heatmap layer, `web/src/components/CameraMap.tsx`** — a `heatmap`-type
  MapLibre layer, additive to the existing camera markers and district
  boundaries. Takes `/analytics/heatmap`'s GeoJSON directly (it is already
  shaped for this — see that endpoint's own docstring). Wired into a toggle
  on the existing GIS map (`MapView.tsx`, "Detection density heatmap"),
  gated the same way, rather than only living on the new page — inserted
  *below* the camera-marker layers so a dense heatmap can never hide a
  camera.
- `corridor` added to the `Camera` / `CameraFeatureProperties` frontend
  types, and the full analytics response contract mirrored into
  `lib/types.ts`, matching `app/schemas/analytics.py` field for field.

### What is not done

- **The gate itself.** Nobody has looked at the rendered page.
- Two spot-fixes made in the same session, also unverified live: the
  design-system pass across every screen (merged from `riya-frontend-vadi`
  onto this branch first, so the analytics page is built on the current
  primitives rather than the pre-redesign markup), and a colour-token fix in
  `AnprOverlay.tsx` (an inline `rgb()` literal replaced with
  `hsl(var(--priority-high))`, for consistency with the rest of the palette
  — not a behaviour change).
- The ANPR bounding-box lag an operator reported ("the box appears after the
  car is already gone") was investigated but not changed: the sync
  architecture in `AnprOverlay.tsx`/`StreamPlayer.tsx` is already correct on
  reading, and the far more likely cause is the same one this phase's own
  `SpeedProvenance` note names — a replayed clip loops every few seconds, so
  `hls.playingDate` (real wall-clock) and what is visually on screen fall
  out of correspondence at each loop boundary. That is a property of
  replayed demo footage the project has already decided not to chase before
  P7, not a frontend bug, so nothing was changed speculatively.

### Next actual step

On a machine with Docker and Node: `make demo`, `make ai`, open `/analytics`
as any non-`api_client` role, and check the gate's four claims against what
renders. Then `make test` for the new suite against the live fleet. If a
figure is wrong or a chart is empty when it should not be, that is real
signal this session could not get — bring it back with what the page
actually showed.

---

## Worker CPU and the thread budget  ✅ (unplanned — 10 Sep 2026)

Not a roadmap phase. Raised as "cameras are not loading properly, and if they
load it hangs", which turned out to be the platform starving itself.

### What was wrong

The AI worker was taking **866% CPU and 168 OS threads on a 10-core host**,
with a load average of 18.8. MediaMTX, the browser and the compositor were
competing for what was left, so a WebRTC handshake that could not get
scheduled looked exactly like a camera that would not load.

Two independent causes, both invisible from the outside:

1. **Nothing divided the thread budget by the camera count.** The worker runs
   one `Pipeline` per camera, each building five ONNX sessions. The two YOLO
   sessions were capped at four threads; the three OCR sessions were capped at
   nothing — `RapidOCR()` was constructed with no arguments and the wheel
   defaults to `intra_op_num_threads: -1`, ONNX Runtime's one-per-core.
   `crnn_onnx.py` built its session with no `SessionOptions` at all.
   `OMP_NUM_THREADS` was set in `ai-lab/Dockerfile` but **not** in the worker
   Dockerfile that ships, and `cv2.setNumThreads` was never called anywhere.

2. **Rotation leaked a thread pool per camera change.** Each rotation built a
   fresh `Pipeline`; every ONNX session allocates a native thread pool and
   arena freed only when Python collects the object, which for objects in
   reference cycles means whenever the cyclic collector next runs. Measured:
   29 threads / 1.4 GB at four minutes became 62 threads / 3.5 GB at twelve,
   doing identical work — the road to the `exit 137` kills seen before.

### The measurement that settles it

Oversubscription was not a trade of latency for throughput. RapidOCR
recognition on one plate crop:

| | wall/call | CPU/call | cores used |
|---|---|---|---|
| `intra_op=2` | 11.8 ms | 23.6 ms | 2.0x |
| `intra_op=4` | 8.0 ms | 32.0 ms | 4.0x |
| **ORT default** | **14.3 ms** | **132.6 ms** | **9.3x** |

The default burned **5.6x the CPU to return a slower answer**.

### What was built

* `ai-lab/ailab/runtime.py` — one process-wide budget (`cores - 2`), divided by
  the camera count, clamped to a measured per-model ceiling of 4. The worker
  declares its slot count in `AiWorker.__init__`, before any session exists,
  because a session's thread pool is fixed at construction.
* `services/ai-worker/ai_worker/pipeline_pool.py` — models loaded once per slot
  and lent to whichever camera holds it. A pool rather than one shared
  instance, because `Pipeline` mutates per-track state and is not thread-safe.
* Thread discipline applied to `rapid.py`, `crnn_onnx.py` and
  `onnx_backend.py`; `OMP_NUM_THREADS` added to the worker Dockerfile;
  `cv2.setNumThreads` set from the same budget.
* `device` gains `coreml`; `cuda`/`coreml` now raise when the provider is
  absent rather than silently running on CPU.

### A third cause: the fleet was publishing 4K60

Two seed clips are **3840x2160 at 24 Mbps** (`anpr_sample.mp4` at 60 fps,
`test1.mp4` at 30), and roughly a quarter of the 51 cameras is assigned one.
So a browser tile was decoding 4K60, and the worker was decoding every 4K
frame **only to letterbox it to the detector's fixed 640px input** — the extra
pixels are discarded before inference sees them. It is also unrepresentative;
city ANPR cameras are 1080p at 12–15 fps.

`prepare_clip` already re-encodes once to disk to fix keyframe sparsity, so a
`SIM_MAX_HEIGHT`/`SIM_MAX_FPS` cap there costs nothing per stream. It never
upscales: only the two 4K clips are touched, 126 MB → 35 MB and 129 MB → 30 MB.

### Gate output

Same fleet, same footage, same models:

```
                            before          after
OS threads                     168        79, stable
worker CPU              866% (1 sample)   ~656% (38-sample mean)
memory                  3.2 GB, climbing  3.3 GB, flat
detections/min                  79            202
inference slots                  3              4
models loaded per hour         ~180              4
published 4K cameras     2160p60 24Mbps    1080p15
```

**Detections per minute rose 2.6x while CPU fell**, because the machine had
been losing most of its work to context switching and to decoding pixels that
were thrown away before inference.

The resolution cap measured on its own, 4 slots either side, everything else
identical:

| | 4K sources | capped |
|---|---|---|
| detections/min | 124 | **202** |
| worker memory | 4.1 GB | 3.3 GB |
| **plate yield** | **34.1%** | **33.9%** |

Yield is the figure that had to hold, since downscaling is the one change that
could have cost legibility. It did not.

**Two caveats, stated rather than buried.** The 866% "before" is a single
`docker stats` sample against a 38-sample mean after; worker CPU swings between
~230% and ~770% as cameras rotate, so thread count, memory trend and
detections/min are the trustworthy comparisons. And overall plate yield moved
38% → 34% across the whole exercise — that is the slot count changing which
cameras rotate, not a regression, as the controlled comparison above shows.

Pool health is in the periodic log line — `built` must settle at the slot count:

```
watching 3/51 camera(s): ... · models built 3, reused 18
```

### Slots are now bounded by memory, not CPU

| slots | threads/model | CPU | memory | detections/min | cameras / 5 min |
|---|---|---|---|---|---|
| 3 | 2 | 574% | 2.7 GB | 150 | 10 |
| **4** (new default) | 2 | 647% | 4.1 GB | 124 | 15 |
| 6 | 1 | 589% | 6.8 GB | 133 | 27 |

CPU and throughput are flat; only memory scales, at ~1.4 GB per slot. The
default moved 3 → 4: free in CPU, safe in memory, and the floor of the PS's
"4–6 feeds". Six reaches 6.8 GB against an 11.67 GB VM ceiling with ~2.7 GB
already spent — that is where the OOM kills start, so Docker's memory
allocation has to rise first.

### Tests

33 new — `test_runtime.py` (14), `test_pipeline_pool.py` (7) and
`test_publisher.py` (12). ai-worker **61 → 82**; the simulator had **no tests
at all** and now has 12, wired into `make test`. API 408 and frontend 40
unchanged, `tsc` clean, ruff at the pre-existing baseline. The worker tests
live in `services/ai-worker/tests/` and run in the shared API
image, which meant `ailab.runtime` had to stay stdlib-only (no `rich`) and the
pool had to take its factory by injection — the worker image has no pytest and
the test image has no OpenCV, so anything reaching through `worker.py` would
have run in neither.

### GPU — what is actually possible

`docs/GPU.md` records the matrix. Inside Docker on Apple Silicon the providers
are exactly `['AzureExecutionProvider', 'CPUExecutionProvider']`, verified:
Virtualization.framework passes no GPU to a Linux guest, so there is no setting
that creates one. CoreML is reachable only by running the worker natively on
macOS; CUDA needs an amd64 host with an NVIDIA card. Both paths are wired and
neither is measured here.

---

# Known gaps (audited 9 Sep 2026)

Recorded so no session mistakes these for done. Verified against the code, not the docs.

## Capability gaps — these are the phases

| Gap | Detail | Phase |
|---|---|---|
| **Only one camera in the registry** | `CAM-DEMO` alone. A platform about linking observations across cameras has nothing to link. Blocks PS steps 1, 3, 4, 7, 8. | P1 |
| ~~No traffic analytics at all~~ | 🟡 **Code complete (P4)**, gate not yet run — see the P4 section above. Router, page, and heatmap layer all exist now; nobody has confirmed the rendered numbers against a live fleet. | — |
| **No route-level average speed** | The only speed figure in the system is per-leg `implied_kmph`. `Route` has no speed property. | P2 |
| **`first_seen` / `last_seen` never rendered** | Present in the API payload and in the TS type; displayed on no screen. | P2 |
| **No journey animation** | `RouteMap.tsx` animates only camera movement (`easeTo`/`fitBounds`). No timeline, scrubber, moving marker or `requestAnimationFrame` anywhere in `web/src`. | P2 |
| **No anomaly detector** | The six per-leg flags are a cloned-plate/OCR **physics filter**; nothing compares a journey to a norm. `AlertType.ANOMALY`, `SPEED` and `CONVOY` have **zero producers** — the only two alert producers are watchlist match and camera-down. | P5 |
| **`alerts` has no reasons/factors column** | So CLAUDE.md's "explainability rule (enforced, tested)" cannot be true. There is no column and no test. P5 adds the migration. | P5 |
| **Search is exact + prefix only** | The `pg_trgm` GIN index on `plate_normalised` **exists and nothing queries it**. A misread plate suggests nothing. | P8 |
| **OpenSearch runs and does nothing** | Health-probed only; indexes nothing, queries nothing. Costs demo-laptop memory for no function. Wire it or drop it. | P8 |
| **Attribute search impossible** | `vehicle_colour` is never computed anywhere. | P9 |
| **No re-identification** | Intra-camera tracking is motion-only (ByteTrack, no appearance branch); cross-camera linking is plate-string equality. No embedding model anywhere. | P11 |

## Data and wiring gaps

| Gap | Detail | Phase |
|---|---|---|
| **Five `detections` columns are permanently NULL** | `vehicle_colour`, `crop_key`, `frame_key`, `direction`, `speed_kmph` — **no producer anywhere** in pipeline, event or consumer. | P3, P9 |
| **No MinIO client exists** | Zero `put_object` / `presigned` / `boto3` hits in the repo. MinIO is config plus a health probe. Nothing is ever uploaded. | P3 |
| **The worker never writes crops** | `worker.py` builds `StreamRunner` with no `run_dir`, so `_NullRunDir` discards every crop and `evidence.plate_crop` is always null. Two failures stack with the missing client. | P3 |
| **`persist_route` is dead code** | Never called from anywhere, so `vehicle_tracks` is **never written**. There is no journey history to learn anomaly baselines from. | P2 |
| **`packages/contracts/` is one empty file** | `generated/.gitkeep`, 0 bytes. No schemas, no generated types, nothing imports it. The event is a hand-rolled dict in `ai-lab/ailab/stream/events.py`, duplicated by hand in `simulator/load_mode.py` (which emits `"auto"`, a vehicle type the real COCO detector cannot produce), with **no validation on either side**. CLAUDE.md §5's contracts claim is false. | P12 |
| **`vehicle_type` is a raw COCO class** | Stored verbatim, so `person` rows land in `detections`. | P9 |
| **`plate_text` and `plate_normalised` hold the same value** | Both read the consensus string. The model docstring claiming one is raw OCR is wrong; raw text survives only inside `ocr_raw`. | P12 |
| **`detection_confidence` is the vehicle detector's** | The plate detector's own confidence is carried in the event and silently dropped by the consumer. Easy to misread this column. | P12 |

## Bugs found, not yet fixed → P6

| # | Bug |
|---|---|
| 1 | `revisit` / `co_located` legs `continue` early in `score_legs`, **skipping** the `unobserved_gap` and `heading_conflict` checks entirely. A vehicle returning 5 hours later never gets `unobserved_gap`. |
| 2 | `sightings_for` applies `LIMIT 5000` in SQL *before* NULL positions are dropped in Python, returns the **oldest** rows, and carries **no truncation indicator** — a route claiming a 30-day window can silently stop at day 3. |
| 3 | `Match.distance` is hardcoded to `1`, so every near-match alert note reads "(1 character different)" regardless of the actual edit. |
| 4 | The dedup **repeat count is never persisted**, though `alerts.py`'s docstring claims "seen 12 times" is kept on the original alert. There is no column and the payload omits it. |
| 5 | `transition()` sets `acknowledged_by`/`acknowledged_at` unconditionally, so closing an alert **erases who originally acknowledged it**. Untested — the lifecycle test only covers NEW→ACKNOWLEDGED. |
| 6 | `_within_one_edit` exists **twice** with two different implementations (`watchlist.py` two-pointer, `correlator.py` slice comparison). The correlator docstring says "shared with the watchlist matcher" — it is a copy. |

Untested critical paths: `raise_for_match`, `build_route`, `sightings_for`, `find_convoys`,
`WatchlistIndex.refresh()`, `alert_fanout.publish`.

## Accuracy and measurement

| Gap | Detail |
|---|---|
| **Accuracy is unmeasured on real footage** | Synthetic only: **62.5–87.5% end-to-end** across two runs, **100% exact-match on plates attempted**, CER 0.000. `ai-lab/FINDINGS.md` states these are optimistic and must not be quoted as system accuracy. The PS demands >90% real-world. → P7 |
| **The bottleneck is recall, not OCR** | When the pipeline commits to a read it is right; it declines to read 3 of 8 plates. A better recogniser is the wrong fix. → P7 |
| **Demo footage carries UK plates** | `anpr_demo.mp4` is the only clip with legible plates. A per-camera `plate-region:GB` tag handles it. **Never ship a config that accepts GB.** |
| **Sizing docs are ~4× optimistic** | `docs/INFRASTRUCTURE.md` §2 divides by 8.78 cameras-per-worker but treats the 4-thread worker as one core. 80,000 cameras needs ~36,000 cores, not ~10,000. `scripts/capacity_model.py` supersedes both sizing tables. → P12 |
| **Load-test latency missed its gate** | Throughput passed (2,774 events/s, 0 failures); p95 capture-to-persisted was **5.7 s against a 3 s target**. Queue wait, not processing. Reported rather than tuned away. |
| **The GPU path has never executed** | The CUDA branch is written; this machine has no CUDA. **No GPU figure is claimed anywhere.** |
| **`mypy` does not pass** | 17 errors across 8 files, and `make lint` does not run it, while CLAUDE.md §5 claims it passes. → P12 |
| **Plate detector weights are AGPL-3.0** | An Ultralytics export. Acceptable for evaluation; must be replaced before any real production deployment. Out of scope at the current demo-grade bar. |

## Smaller, still real

- **HLS fallback opens a raw `.m3u8`**, which downloads rather than plays outside Safari. Needs
  `hls.js`. → P6
- **Health debounce counters are in-memory**, so a monitor restart resets the consecutive-failure
  count. Deliberate (it is debounce state, not a fact), but worth knowing.
- **Vendor adapters are unexercised against real VMS.** The code is real and unit-tested; no
  Milestone/Genetec/Hikvision server has ever been on the other end.
- **Live reads converge less than offline ones.** Streaming emits `vehicle.observed` incrementally,
  so an early event can carry a partial read; the final `vehicle.completed` is right.
- **The simulator publisher re-encodes with libx264** (~10% of a core per stream), capping the live
  fleet. The clips are already H.264 — copy-mode was measured at **64 streams on ~1.1 cores**. → P1
- **`gateway.py` registers a MediaMTX pull path from `camera.stream_url`.** Pointing a
  simulator-published camera's `stream_url` at the gateway makes MediaMTX pull a path from itself,
  which loops and blocks publishing. Diagnosed once already; do not repeat.
- **Portrait source clips are pillarboxed** — `fetch_videos.sh` pads to 16:9 rather than cropping.

---

# Definition of done

**V1 (internal hackathon).** A judge clones the repo, runs `make demo`, and within five minutes
walks all **8 PS demo steps** in the browser:

1. Several live camera feeds, vehicles detected automatically.
2. A plate resolving with confidence, camera and timestamp.
3. The same vehicle appearing on a second camera and being **linked automatically**.
4. That vehicle's **journey** — cameras visited, distance, duration, average speed — drawn and
   **animated** on the city map.
5. A plate search returning every historical sighting and the full route.
6. A blacklisted vehicle firing a **red alert by itself**, with a visible plate crop.
7. A **trajectory anomaly** raised, with the factors that raised it shown.
8. A **city analytics** dashboard: density, average speed, route density, travel time, hotspots,
   heatmap — every figure computed from real rows.

**V2 (SIH).** All of the above, plus a **measured** >90% plate accuracy figure on labelled real
footage, attribute search, predictive traffic with a backtested error figure, and
re-identification.

The bar throughout: **nothing fake**. No placeholder numbers, no stubbed functions, no cameras
without a source. If there is not enough data to compute a figure, the UI says so.