# NagarNetra — progress & next steps

**Read this first.** One page: what works, what doesn't, what to do next.

*Updated 9 Sep 2026 · after a full code audit · **next task: P1, the Ahmedabad fleet***

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

And the immediate blocker is smaller than any of that: **the registry holds one camera.** A
platform about connecting observations across cameras currently has nothing to connect.

```
DONE     platform ──▶ ANPR ──▶ watchlist alerts ──▶ trajectory engine ──▶ scale proof
NEXT     P1 fleet ← start here
THEN     P2 journey ──▶ P3 crops ──▶ P4 analytics ──▶ P5 anomaly ──▶ P6 harden   (V1, ~2 weeks)
LATER    P7 accuracy ──▶ P8 search ──▶ P9 attributes ──▶ P10 predict ──▶ P11 re-ID ──▶ P12 docs
```

---

## The 8 PS demo steps — the honest scorecard

| # | Step | State |
|---|---|---|
| 1 | Live multi-camera detection (4–6 feeds) | 🔴 **one camera in the registry** |
| 2 | ANPR: plate, confidence, camera, time | ✅ works — 9 fps, p90 266 ms, 0.70–0.94 confidence |
| 3 | Same vehicle linked across cameras | 🟡 engine ready, nothing to link between |
| 4 | Vehicle journey + animated route | 🟡 no average speed, no playback |
| 5 | Plate search | 🟡 exact and prefix only, no fuzzy |
| 6 | Blacklist alert **with plate crop** | 🟡 alert fires; the crop cannot be shown |
| 7 | Trajectory anomaly + explanation | 🟡 a physics filter, not a detector |
| 8 | City traffic analytics | 🔴 **absent — no router, no page** |

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

1. **A fleet.** One camera. Blocks steps 1, 3, 4, 7 and 8. → **P1**
2. **Traffic analytics.** No router, no page, no heatmap. `recharts` is a dependency imported zero
   times; every "chart" today is a Tailwind div bar. → **P4**
3. **Journey completeness.** No route-level average speed anywhere; `first_seen`/`last_seen` are
   fetched but never rendered; no animation. → **P2**
4. **Anomaly detection.** The six existing flags are a cloned-plate/OCR physics filter. Nothing
   compares a journey to a norm. `AlertType.ANOMALY` has zero producers. → **P5**
5. **Evidence crops.** The alert cannot show a plate crop. → **P3**
6. **A real accuracy number.** → **P7**

---

## 🔧 Known debt worth knowing before you touch anything

- **Five `detections` columns are permanently NULL** — `vehicle_colour`, `crop_key`, `frame_key`,
  `direction`, `speed_kmph`. Nothing produces them anywhere in pipeline, event or consumer.
- **`persist_route` is dead code**, never called, so `vehicle_tracks` is never written. There is no
  journey history to learn anomaly baselines from. P2 wires it.
- **No MinIO client exists** anywhere in the repo — zero `put_object`/`boto3` hits. MinIO is config
  and a health probe only.
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

## ▶ Next step: P1 — the Ahmedabad fleet

Full definition and gate: [docs/ROADMAP.md](ROADMAP.md#p1--a-real-multi-camera-ahmedabad-fleet--do-this-first).

In short: generate 60–80 cameras along real Ahmedabad arterials from OSM geometry, snapping each one
onto an actual road vertex; port ffmpeg copy-mode into the simulator publisher so the whole fleet can
stream on one laptop; seed so every camera has a resolving stream URL; replay footage with time
offsets so plates appear at successive cameras.

**Two traps, both already paid for once:**

- Do **not** set a simulator-published camera's `stream_url` to the gateway's own URL. `gateway.py`
  registers a MediaMTX *pull* path from `stream_url`, so MediaMTX ends up pulling a path from
  itself, loops, and blocks publishing.
- Do **not** seed cameras without a stream source. That was the fiction commit `4d0346f` deleted —
  251 of 281 cameras that could never produce a detection, making every count on every screen
  meaningless.

**Gate:** ≥40 cameras online · ≥6 live feeds · one plate on ≥3 distinct cameras · reached by
`make demo` from empty volumes.

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
