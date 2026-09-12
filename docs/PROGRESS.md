# NagarNetra — progress & next steps

**Read this first.** One page: what works, what doesn't, what to do next.

*Updated 12 Sep 2026 · P1–P3 complete, plus unplanned worker-CPU and
overlay-latency fixes ·
**P4, P5, P8 and P10 code complete, P9 and P11 half built (both need a
vision pipeline this session didn't have for their other half), gates not
yet run — P7 blocked on data — see below***

| Document | What it holds |
|---|---|
| this file | one-page state, and the next thing to do |
| [docs/ROADMAP.md](ROADMAP.md) | **phase definitions and gates** — the plan of record |
| [BUILD_STATE.md](BUILD_STATE.md) | per-phase build history and the evidence each gate produced |
| [CLAUDE.md](../CLAUDE.md) | the contract: rules, conventions, architecture |

---

## Where we are

The **platform** is strong. The **intelligence the PS is actually about is mostly missing.**

Auth, RBAC, audit, the camera registry, the GIS map, fleet health, the ANPR pipeline, the stream
gateway and the watchlist→alert path all work, with 546 tests behind them. The event tier has been
load-tested at 2,774 events/s with zero failures. Every screen shows live data — an audit found no
placeholder data anywhere in the frontend.

But of the PS's **8 demo steps: 2 work, 4 are partial, 2 do not exist.** There is no traffic
analytics router and no analytics page. Trajectory anomaly detection is an enum value with zero
producers. Predictive traffic, attribute search and re-identification do not exist.

*(That last paragraph described the state before P1. The registry now holds 58 Ahmedabad
cameras; see BUILD_STATE P1.)*

**The worker no longer starves the machine.** It was taking 866% CPU and 168 threads on a
10-core host, which is why camera tiles hung — nothing was left to render them. One thread
budget divided across cameras, plus a pipeline pool that stops rotation leaking a thread pool
per camera change, took it to ~570% and **doubled** detections/min (79 → ~150). Slots are now
capped by memory rather than CPU, so the default moved 3 → 4. Details and knobs in
[PERFORMANCE.md](PERFORMANCE.md); the GPU question is answered in [GPU.md](GPU.md) — inside
Docker on Apple Silicon there is none, and no setting creates one.

**The box now appears when a plate is *located*, not when it is read** (unplanned, 12 Sep).
The overlay used to draw nothing until OCR had reached consensus at >=0.8 confidence with valid
grammar. Two consequences, both bad: a second or so of delay on the vehicles that do get read,
and **nothing at all for the two-thirds that never yield a plate** (33.9% plate yield,
[PERFORMANCE.md](PERFORMANCE.md) §6). The plate detector knows where a plate is well before
anything reads it, and that is now what puts a rectangle on screen — dashed and faint, with the
plate text arriving later if it arrives at all. Three visual tiers keep it honest: `located`
(dashed, faint, no text), `reading` (dashed, firmer), `read` (solid, plate printed).

This needed a fact the pipeline was throwing away. `track.plate_detections` is appended *after* a
successful OCR read, so a localised-but-unread plate was dropped on the floor — and that list
cannot be widened, because `report/stats.py` counts it and the accuracy harness reads those
numbers. So `Track.plate_location` is a separate field, set at localisation time and counted by
nothing.

It also needed a new channel. `camera.tracks` publishes **one message per camera per tick**
carrying every drawable vehicle, instead of one event per vehicle: measured at a 305-byte envelope
plus 132 bytes per located vehicle and 218 per read one, so ten vehicles is 1,987 bytes and
9.7 KB/s per camera at five batches a second — against 39.6 KB/s for the ten per-vehicle refreshes
it replaces, which covered only the vehicles already read. Because the batch describes the whole
camera it is authoritative: a vehicle absent from the newest one is gone, so a box now disappears
when the car does rather than when a timeout expires. Per-vehicle position refreshes
(`observed_refresh_s`) are off by default as a result. One subtlety worth knowing: the scheduler
*stops searching* a vehicle once its plate converges, so a plate box left at its measured position
would freeze while the car drove on — the batch therefore anchors it to the vehicle box it was
measured against and carries it along.

**The plate box no longer trails the vehicle** (unplanned, 12 Sep). Boxes were drawn at the
coordinates a frame was measured at — ~270 ms old on arrival — and then held still until the next
position refresh 400 ms later, so on a moving car the rectangle sat behind it and jumped to catch
up. Four things were wrong at once and all four are fixed: the HLS player forced itself two
seconds behind the live edge (`liveSyncDurationCount: 2`) *on top of* the gateway's own ~600 ms
low-latency hold-back; the capture clock that says which instant is on screen was withheld from
the overlay unless an operator ticked "sync to video", and WebRTC reported no clock at all when it
can in fact be asked (`jitterBufferDelay`); the overlay never predicted, so a box was only ever as
current as the last event; and every position refresh carried the full evidence payload —
**measured 3,787 bytes against 793** for the trimmed one, growing with every further read of the
same plate. The overlay now schedules each box against the instant the picture is showing and
carries it forward on the vehicle's own measured velocity, capped at 700 ms so a track that stops
reporting stops moving. A box also appears from the *first* reading rather than waiting for
consensus — unlabelled and dashed until the plate is confirmed, which is what used to delay it by
a second or more. **Not yet verified on a running stack**: this machine has no Docker, so the
numbers above are the ones the code and the payload measurement give, not a stopwatch on the demo.
Run `make ai` and watch a corridor camera to close it.

```
DONE     platform ──▶ ANPR ──▶ scale ──▶ P1 fleet ──▶ P2 journey ──▶ P3 evidence crops
         └─ plus: worker CPU budget + pipeline pool (unplanned, 10 Sep)
NEXT     P4 analytics + P5 anomaly + P8 search + P9 attributes(half) + P10 predict
         + P11 re-ID(half) — code complete, gates not yet run
         ← run `make demo` and check all six
BLOCKED  P7 accuracy — needs real footage and a person to label it, not more code
         P9's colour half + P11's embedding half — need numpy/opencv + real footage
THEN     P6 harden                                                             (V1, ~2 weeks)
LATER    P12 docs
```

---

## The 8 PS demo steps — the honest scorecard

| # | Step | State |
|---|---|---|
| 1 | Live multi-camera detection (4–6 feeds) | ✅ **51 cameras live** on 12 real Ahmedabad corridors |
| 2 | ANPR: plate, confidence, camera, time | ✅ works — 9 fps, p90 266 ms, 0.70–0.94 confidence |
| 3 | Same vehicle linked across cameras | 🟡 engine ready and linking real sightings; timing not yet plausible (see below) |
| 4 | Vehicle journey + animated route | ✅ **profile + timeline playback** on the map |
| 5 | Plate search | 🟡 fuzzy search code complete (P8), gate not yet run |
| 6 | Blacklist alert **with plate crop** | ✅ **alert fires by itself, with the plate crop** |
| 7 | Trajectory anomaly + explanation | 🟡 **code complete (P5), gate not yet run** — was a physics filter only; now has a real data-driven detector and stored, rendered factors |
| 8 | City traffic analytics | 🟡 **code complete (P4), gate not yet run** — router, page and heatmap all exist; unverified against a live fleet |

Search beyond plates (PS §14, not one of the 8 numbered steps but named as a
differentiator): 🟡 **half built (P9)** — type/camera/time filtering on
`GET /api/v1/detections` plus a UI toggle; vehicle-colour extraction itself
was not attempted (no numpy/opencv, no real footage this session).

Predictive traffic (PS §12, differentiator #2): 🟡 **code complete (P10),
gate not yet run** — `GET /api/v1/predictions/congestion` (relative-volume
index, 15/30-min forecast, contributing factors, backtested error) and an
Analytics.tsx panel exist; the forecasting arithmetic was directly executed
against synthetic data and verified correct (see BUILD_STATE.md's P10
section), but the endpoint itself has not run against a live fleet.

Vehicle re-identification (differentiator #4's "even when the plate is
unreadable" half): 🟡 **half built (P11)** — `GET /api/v1/reid/candidates`
ranks other cameras' detections near an unreadable-plate sighting by
spatiotemporal plausibility (real physics, reused from the correlator) and,
once one exists, appearance similarity. No ReID model exists anywhere in
`ai-lab` today, so `embedding_available` is `false` on every real response
and every candidate is plausibility-only — a real, useful, but explicitly
weaker signal than appearance re-identification, labelled as such
everywhere it's shown, never presented as a match.

---

## ✅ What genuinely works

- **Auth, RBAC, audit** — six roles, and an audit row is written for every plate search, every
  stream open and every watchlist mutation. Tested.
- **Camera registry + GIS** — CRUD, bulk CSV import with a dry-run, GeoJSON, proximity search,
  district filter, offline GeoJSON basemap with an optional satellite layer that probes first and
  falls back.
- **Fleet health** — every camera probed on a ~15 s sweep, with real online/offline transitions and
  camera-down alerts. Gap analysis reports districts with no coverage.
- **ANPR pipeline** — detect → track → plate detect → OCR → multi-frame consensus → event.
  Verified live end to end.
- **Stream gateway** — RTSP in, WebRTC (WHEP) or HLS out, camera-scoped short-lived tokens.
- **Watchlist → alert** — exact and one-edit near match, dedup, priority capping for near matches,
  and the alert reaches a connected operator over WebSocket in ~24 ms.
- **Trajectory engine core** — hop clustering, great-circle distances, dwell, implied speeds, six
  plausibility flags, GeoJSON output. Honest about being straight lines, not roads.
- **Scale proof** — 2,774 events/s sustained, 0 failures, across 80,000 camera identities.
- **Capacity model** — `python3 scripts/capacity_model.py --compare` sizes and costs any fleet.

---

## ⏳ What is missing, in priority order

2. **Traffic analytics — verification.** Router, page and heatmap now exist (P4), built and reasoned
   through carefully but never run: this session had no Docker and no Node/npm, so nobody has opened
   `/analytics` in a browser or run `make test` against a live fleet. See BUILD_STATE.md's P4 section
   for exactly what was built and what "next step" means concretely. → **P4, gate**
4. **Anomaly detection — verification.** `anomaly.py` now compares each new leg against 30-day
   transition-frequency and duration baselines and raises a real, explained `AlertType.ANOMALY`
   alert (migration 0005 adds the `reasons` column that makes this possible). Built and tested
   against hand-constructed scenarios; never run against a live fleet — same caveat as analytics
   above. → **P5, gate**
6. **A real accuracy number.** → **P7**
7. **Attribute search — verification, and the colour half.** Type/camera/time filtering on
   `GET /api/v1/detections` and a `By attributes` UI toggle exist now (P9), same never-run caveat as
   P4/P5/P8. Vehicle-colour extraction was not attempted — no numpy/opencv and no real footage this
   session, same blocker as P7. → **P9, gate + colour producer**
8. **Predictive traffic — verification.** `GET /api/v1/predictions/congestion` and an Analytics.tsx
   panel exist now (P10): a relative-volume index against each camera's own history, a 15/30-min
   forecast, contributing factors and a self-backtested error. The forecasting arithmetic itself was
   directly executed against synthetic data this session (not just linted) and is verified correct;
   the endpoint has not run against a live fleet. → **P10, gate**
9. **Re-identification — verification, and the embedding half.** `GET /api/v1/reid/candidates` and
   a "Find similar" action in `VehicleSearch.tsx` exist now (P11): candidate cross-camera matches
   for an unreadable-plate detection, ranked by real spatiotemporal plausibility and, once a
   producer exists, appearance similarity. The embedding itself was not attempted — no ReID model
   anywhere in `ai-lab`, no numpy/opencv this session, same blocker as P7/P9. →
   **P11, gate + embedding producer**

---

## 🔧 Known debt worth knowing before you touch anything

- **Four `detections` columns are permanently NULL** — `vehicle_colour`, `frame_key`, `direction`,
  `speed_kmph`. (`crop_key` is populated as of P3.) `vehicle_colour` now has a real, tested filter
  clause (P9) — it is just never populated, so the filter always matches zero rows. Documented in
  the query param's own description, not hidden.
- **`packages/contracts/` is one empty file.** The event is a hand-rolled dict, duplicated by hand in
  the load generator, with no validation on either side.
- ~~`alerts` has no reasons/factors column~~ **Added (P5, migration 0005)**, code complete, gate not
  yet run — see ROADMAP.md's P5 section.
- **OpenSearch runs and does nothing** — health-probed only, indexes nothing. It costs demo-laptop
  memory for no function. P8 investigated this deliberately and left it as-is: its own compose
  comment and `.env.example` describe it as the *intended primary* fuzzy-search backend with pg_trgm
  as fallback, so indexing it properly or removing it are both real changes to someone else's
  decision, not a call this session could safely make blind. See ROADMAP.md's P8 section.
- ~~The `pg_trgm` index on `plate_normalised` exists and nothing queries it~~ **Queries it now (P8)**
  — `GET /api/v1/vehicles/search`, code complete, gate not yet run.
- **Accuracy is unmeasured on real footage.** Synthetic only: 62.5–87.5% end-to-end, 100%
  exact-match on plates attempted. The bottleneck is recall, not OCR. Demo footage carries UK plates.
  P7 investigated 11 Sep 2026: blocked on real footage and human labelling, neither producible in a
  coding session. One correction worth knowing — `ai-lab/FINDINGS.md` used to list "fix track
  fragmentation" as unstarted; `ailab/track/merge.py` already does it and is wired into both
  pipelines, it has just never been re-measured. See ROADMAP.md's P7 section.
- **Sizing docs are ~4× optimistic.** `docs/INFRASTRUCTURE.md` §2 treats a 4-thread worker as one
  core. `scripts/capacity_model.py` supersedes it.
- **`mypy` does not pass** (17 errors) and `make lint` does not run it.
- Six specific bugs are listed in [docs/ROADMAP.md](ROADMAP.md) under **P6** — two are
  demo-visible, one silently loses data.

---

## ▶ Next step: run the P4 gate

Full definition and gate: [ROADMAP.md](ROADMAP.md#p4--city-traffic-analytics--biggest-missing-module).
Full build detail: [BUILD_STATE.md](BUILD_STATE.md), P4 section.

The router, the page and the heatmap layer all exist now — `analytics.py` over
the `detections` hypertable and `vehicle_tracks` (density, corridor average
speed, route density, travel time vs baseline, hotspots), `Analytics.tsx`
actually using `recharts`, and a `heatmap`-type MapLibre layer wired into both
the new page's data flow and a toggle on the existing GIS map. A test suite
for the endpoints exists too (`test_analytics.py`), built against a fixed
historical window so it is deterministic against a live, concurrently-running
demo.

**None of it has been run.** The session that built it had no Docker and no
Node/npm — everything was written and reasoned through against the actual
source (the SQL, the Pydantic schemas, the existing RBAC matrix), but the gate
itself — "the analytics page shows non-zero density, per-corridor average
speed, a populated route-density table and a heatmap, every figure traceable to
real rows" — needs a real `make demo` and a browser. That is the actual next
step, not more code.

**Honesty rule, unchanged:** every figure computed from observed rows. Where a
baseline has too little history, the UI says "insufficient history" — never a
plausible invented number. On this demo fleet, expect the speed endpoint to
report `insufficient_data` for most or all corridors — a replayed clip makes
every implied speed hundreds of km/h, so the correlator excludes it, correctly.
That is documented, expected behaviour, not a bug to chase.

---

## ▶ Also next: run the P5 gate

Full definition and gate: [ROADMAP.md](ROADMAP.md#p5--trajectory-anomaly-detection--explainable-alerts).
Full build detail: [BUILD_STATE.md](BUILD_STATE.md), P5 section.

Migration 0005 (`alerts.reasons`), `app/services/anomaly.py` (transition-frequency
and duration baselines learned from `vehicle_tracks`), `alerts.raise_for_anomaly`,
the fanout wiring in `monitor.py`, and `Alerts.tsx` rendering the factor list all
exist now, with `test_anomaly.py` behind them. Same caveat as P4: built and
reasoned through against the actual code, never run — needs `alembic upgrade
head` and a live fleet to confirm.

**Expect noise at first.** With fewer than 3 vehicles having made any given
transition yet, nearly every journey will read as a rare transition until the
fleet accumulates real history — the risk ROADMAP.md names explicitly. The
mitigation it also names, `scripts/replay_history.py` (run the real pipeline
over footage at accelerated pace to bootstrap genuine history), was **not**
built this session — it needs a runnable ai-worker stack to write against at
all, which this session did not have. Worth doing before judging on this
feature; not required for the code to be correct.

---

## ▶ Also next: run the P9 gate (half of it)

Full definition and gate: [ROADMAP.md](ROADMAP.md#p9--attribute-search-search-beyond-plates).
Full build detail: [BUILD_STATE.md](BUILD_STATE.md), P9 section.

`vehicle_type`/`vehicle_colour` filtering on `GET /api/v1/detections`, the
`By plate`/`By attributes` toggle in `VehicleSearch.tsx`, and `test_detections.py`
all exist now. Same never-run caveat as P4/P5/P8 — needs a live fleet and
`make test`.

**The colour half is not code-complete, and cannot be finished by this
session.** It needs `numpy`/`opencv` (not installed here) and real footage to
extract a colour from and check the result against — the same blocker P7 has
for accuracy. The type/camera/time filtering is genuinely useful on its own
and is what "attribute search" means until a colour producer exists; do not
read the gate as fully met by it.

---

## ▶ Also next: run the P10 gate

Full definition and gate: [ROADMAP.md](ROADMAP.md#p10--predictive-traffic).
Full build detail: [BUILD_STATE.md](BUILD_STATE.md), P10 section.

`GET /api/v1/predictions/congestion`, the Analytics.tsx "Predicted
congestion" panel, and `test_predictions.py` all exist now. Same never-run
caveat as P4/P5/P8/P9 for the endpoint itself — needs a live fleet and
`make test`.

**One thing is different from every other never-run phase this session:**
the forecasting method's core arithmetic (`_fit_line`, `_backtest` in
`app/routers/predictions.py`) does not touch the database, so it was
copied into a standalone script and actually run against synthetic data —
not just linted. A perfectly linear synthetic sequence recovers its exact
slope, a flat sequence yields slope 0, backtest MAE lands at 0.00 on
perfectly linear data and rises correctly when a deviation is planted in
the held-out portion, and forecast deltas at +15/+30 match `slope ×
horizon` exactly. That is real evidence the method is implemented
correctly — narrower than "the gate passed", but stronger than "it was
read carefully and looks right", which is all P4/P5/P8/P9 could offer.

**What still needs a live fleet:** whether real detection volume produces
a sensible baseline and a forecast worth showing a judge, and whether
`speed_trend`/`upstream_inflow` ever actually appear — both depend on
plausible legs existing, which P4's own findings say the replayed demo
fleet mostly does not produce (see P7).

---

## ▶ Also next: run the P11 gate (half of it)

Full definition and gate: [ROADMAP.md](ROADMAP.md#p11--vehicle-re-identification).
Full build detail: [BUILD_STATE.md](BUILD_STATE.md), P11 section.

Migration `0006` (`detections.appearance_embedding`), `app/services/reid.py`
(cosine similarity + a plausibility fallback reusing the correlator's own
physics), `GET /api/v1/reid/candidates`, a "Find similar" action in
`VehicleSearch.tsx`, and `test_reid.py` all exist now. Same never-run
caveat as every other phase this session for the endpoint itself.

**The embedding half is not code-complete, and cannot be finished by this
session.** No ReID model is fetched anywhere in `ai-lab`, and there is no
numpy/opencv here to run one even if it existed — the same blocker P7 and
P9's colour half share. `ai-lab` already crops the full vehicle body per
track (`pipeline.py:_save_vehicle_crops`), so the pipeline is one model
away from feeding this; nothing about that crop path needed to change.

**What is real today without an embedding:** the spatiotemporal-plausibility
ranking. It reuses `correlator.py`'s own physics (haversine distance, the
150 km/h implausible-hop ceiling) rather than inventing new thresholds, so
"candidates near this unreadable-plate sighting, ranked by how physically
reachable they are" is a genuine, demonstrable capability today — just not
appearance re-identification. `embedding_available: false` on every real
response says so explicitly; do not read the gate as met by this half
alone.

---

## Running it

```bash
make demo        # up + migrate + seed + open browser
make ai          # add the AI worker (it is behind the `ai` compose profile)
make videos      # fetch real traffic footage (one time)
make status      # dependency readiness
make test        # API + ai-worker + e2e + frontend
make lint        # ruff + format + tsc
make logs S=api  # tail one service

cd ai-lab
make doctor      # AI models and engines
make test        # ai-lab tests
make evaluate GT=runs/_synthetic/ground_truth.csv   # accuracy against known plates
```

| Surface | URL |
|---|---|
| Command centre | http://localhost:8080 — `admin` / `NagarNetra@2026` |
| API docs | http://localhost:8000/docs |
| Simulator control | http://localhost:9100/streams |

Watch health monitoring react live:

```bash
curl -X POST http://localhost:9100/streams/CAM-DEMO/stop
# the marker turns red and a high-priority alert is raised
curl -X POST http://localhost:9100/streams/CAM-DEMO/start
```

Size any fleet:

```bash
python3 scripts/capacity_model.py --cameras 100000
python3 scripts/capacity_model.py --provenance    # every constant's source
```

---

## The three things that must stay true

1. **Nothing fake.** No placeholder numbers, no stubs, no cameras without a source. A plausible
   invented figure survives the demo and destroys the claim.
2. **The central tier carries events, not video.** Federate rather than centralise; run AI at the
   edge. This is the scaling argument and it survives at any city size.
3. **Show uncertainty rather than hiding it.** Low-confidence hops are marked, not dropped.
   Great-circle distances are labelled as lower bounds. Judges respect a system that knows what it
   does not know.
