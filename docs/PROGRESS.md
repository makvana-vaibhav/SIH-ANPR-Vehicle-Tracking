# NagarNetra — progress & next steps

**Read this first.** One page: what works, what doesn't, what to do next.

*Updated 11 Sep 2026 · P1–P3 complete, plus an unplanned worker-CPU fix ·
**P4 code complete, gate not yet run — see below***

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

```
DONE     platform ──▶ ANPR ──▶ scale ──▶ P1 fleet ──▶ P2 journey ──▶ P3 evidence crops
         └─ plus: worker CPU budget + pipeline pool (unplanned, 10 Sep)
NEXT     P4 analytics — code complete, gate not yet run ← run `make demo` and check it
THEN     P5 anomaly ──▶ P6 harden                                              (V1, ~2 weeks)
LATER    P7 accuracy ──▶ P8 search ──▶ P9 attributes ──▶ P10 predict ──▶ P11 re-ID ──▶ P12 docs
```

---

## The 8 PS demo steps — the honest scorecard

| # | Step | State |
|---|---|---|
| 1 | Live multi-camera detection (4–6 feeds) | ✅ **51 cameras live** on 12 real Ahmedabad corridors |
| 2 | ANPR: plate, confidence, camera, time | ✅ works — 9 fps, p90 266 ms, 0.70–0.94 confidence |
| 3 | Same vehicle linked across cameras | 🟡 engine ready and linking real sightings; timing not yet plausible (see below) |
| 4 | Vehicle journey + animated route | ✅ **profile + timeline playback** on the map |
| 5 | Plate search | 🟡 exact and prefix only, no fuzzy |
| 6 | Blacklist alert **with plate crop** | ✅ **alert fires by itself, with the plate crop** |
| 7 | Trajectory anomaly + explanation | 🟡 a physics filter, not a detector |
| 8 | City traffic analytics | 🟡 **code complete (P4), gate not yet run** — router, page and heatmap all exist; unverified against a live fleet |

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
4. **Anomaly detection.** The six existing flags are a cloned-plate/OCR physics filter. Nothing
   compares a journey to a norm. `AlertType.ANOMALY` has zero producers. → **P5**
6. **A real accuracy number.** → **P7**

---

## 🔧 Known debt worth knowing before you touch anything

- **Four `detections` columns are permanently NULL** — `vehicle_colour`, `frame_key`, `direction`,
  `speed_kmph`. (`crop_key` is populated as of P3.)
- **`packages/contracts/` is one empty file.** The event is a hand-rolled dict, duplicated by hand in
  the load generator, with no validation on either side.
- **`alerts` has no reasons/factors column**, so the "explainability rule" in CLAUDE.md cannot be
  true yet. P5 adds the migration.
- **OpenSearch runs and does nothing** — health-probed only, indexes nothing. It costs demo-laptop
  memory for no function.
- **The `pg_trgm` index on `plate_normalised` exists and nothing queries it**, so search is exact and
  prefix only.
- **Accuracy is unmeasured on real footage.** Synthetic only: 62.5–87.5% end-to-end, 100%
  exact-match on plates attempted. The bottleneck is recall, not OCR. Demo footage carries UK plates.
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
