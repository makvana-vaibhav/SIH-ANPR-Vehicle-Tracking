# ROADMAP — SIH26127

The plan of record. **Phase definitions and gates live here.** Per-phase build history and the
evidence each gate produced live in [BUILD_STATE.md](BUILD_STATE.md); the one-page "where are we
right now" is [PROGRESS.md](PROGRESS.md).

*Written 9 Sep 2026, from an audit of the code rather than of the documents — the documents were
found wrong in several places, and those corrections are recorded in
[CLAUDE.md §10](../CLAUDE.md#10-migration-ledger).*

---

## Two milestones

| Milestone | When | Goal |
|---|---|---|
| **V1 — internal hackathon** | ~2 weeks | All **8 PS demo steps** work end to end, on one laptop, with nothing faked |
| **V2 — SIH** | ~2 months | The four differentiators, and the **>90% accuracy claim earned** |

V1 is the prototype V2 is built on. Nothing in V1 is throwaway.

**Bar:** *demo-grade hardened* — nothing fake, no crashes, real measured numbers, clean fallbacks.
Explicitly **not** in scope: replacing the AGPL-3.0 plate weights, DR drills, pen-testing,
procurement certification.

**Team shape:** 2–3 devs on two tracks — **A: backend intelligence**, **B: frontend + analytics**.

---

## Scorecard against the PS's 8 demo steps

This is the acceptance test for V1. Status as of 9 Sep 2026.

| # | PS demo step | State | Blocking work |
|---|---|---|---|
| 1 | Live multi-camera detection (4–6 feeds) | 🔴 **1 camera** | **P1** |
| 2 | ANPR: plate + confidence + camera + time | ✅ works | — |
| 3 | Same vehicle linked across cameras | 🟡 engine ready, no cameras to link between | **P1** |
| 4 | Vehicle journey + animated route | 🟡 no average speed, no playback | **P2** |
| 5 | Plate search | 🟡 exact + prefix only | P8 (V2) |
| 6 | Blacklist alert **with plate crop** | 🟡 alert fires, crop impossible | **P3** |
| 7 | Trajectory anomaly + explanation | 🟡 physics filter only, no detector | **P5** |
| 8 | City traffic analytics | 🟡 **code complete, gate not run** | **P4** |

---

## V1 — Internal hackathon

### P1 — A real multi-camera Ahmedabad fleet ⭐ do this first
**Track A · ~2 days · unblocks steps 1, 3, 4, 7, 8**

Nothing in the PS can be demonstrated with one camera. The registry holds only `CAM-DEMO`.

- Rewrite the camera generator for **Ahmedabad**. Fetch OSM arterial geometry (SG Highway, Ring
  Road, Ashram Road, CG Road, Sarkhej–Gandhinagar, Narol–Naroda) plus ward boundaries and
  localities. Walk each corridor at ~1.5–2 km spacing and **snap every camera onto a real road
  vertex**, so a camera is never in the middle of a building. Target 60–80 cameras, including
  east–west links so a vehicle can plausibly change corridor mid-journey.
- Port **ffmpeg copy-mode** into `services/simulator/simulator/publisher.py`. The publisher
  re-encodes with libx264 (~10% of a core per stream), which caps the live fleet at a handful. The
  seed clips are already H.264, so remuxing is nearly free — **measured at 64 live streams on ~1.1
  cores**. Without this, step 1 is tight and step 8 is infeasible on one laptop.
- Seed the fleet against a simulator-served VMS so **every camera has a stream URL that resolves**.
  A camera with no source is the fiction commit `4d0346f` deleted; do not reintroduce it.
- ⚠️ Do **not** set `stream_url` to the gateway's own URL. `gateway.py` registers a MediaMTX *pull*
  path from `camera.stream_url`; pointing it at MediaMTX makes MediaMTX pull a path from itself,
  which loops and blocks publishing. This cost an hour to diagnose once already.
- Replay the clip set across corridor cameras with **time offsets**, so the same plates appear at
  successive cameras and multi-camera links form. Real ANPR on real video — document that the
  *footage* is replayed and the *detections* are not.
- Update the Gujarat-geography assertions in `services/api/tests/test_cameras.py` and
  `test_fleet_health.py` (they assert Rajkot and 33 districts).

**Gate:** ≥40 cameras online in fleet health · ≥6 live feeds in Live ANPR · one plate present on ≥3
distinct cameras in the database · `make demo` reaches all of that from empty volumes.

---

### P2 — Journey profile + animated playback
**Track split · ~2 days · PS §5, §11**

- `services/api/app/services/correlator.py`: add **route-level average speed** (none exists — the
  only speed anywhere is per-leg `implied_kmph`) and surface `first_seen`/`last_seen` in
  `Route.to_dict`. Keep the existing lower-bound framing: distances are great-circle, not road.
- **Wire `persist_route`.** It is currently dead code, never called from anywhere, so the
  `vehicle_tracks` history table is never written. P5 needs that history to learn norms from.
- `web/src/pages/VehicleSearch.tsx`: render first seen, last seen and average speed. (`first_seen`
  and `last_seen` are already in the TS type and fetched — they are simply never displayed.)
- `web/src/components/RouteMap.tsx`: add **timeline playback** — play/pause, a scrubber, and a
  vehicle marker moving hop to hop with a running clock. Keep the straight dashed legs; the
  docstring's reasoning is right and road-snapping would assert evidence we do not have.

**Gate:** search a plate → full profile (first seen, last seen, cameras, distance, duration, average
speed) → press play → the marker animates the route with a moving clock.

---

### P3 — Evidence crops in MinIO
**Track A · ~2 days · PS §8**

Bigger than it looks. **Two failures stack, and there is no MinIO client anywhere in the repo** —
zero `put_object` / `presigned` / `boto3` hits. MinIO exists only as config and a health probe.

- Add an S3/MinIO client to the API and the worker.
- Give the worker a real run directory. Today `worker.py` constructs `StreamRunner` with no
  `run_dir`, so `_NullRunDir` silently discards every crop — `evidence.plate_crop` is always null.
- Upload plate and vehicle crops; set `Detection.crop_key` to the **object key**. Note the lab's
  `RunDirectory.save_crop` returns a run-relative *filesystem path*, so the two sides are not
  type-compatible as they stand.
- Serve crops through a short-lived signed URL from the API.
- Render the crop on the alert card in `web/src/pages/Alerts.tsx` and in the live plate feed.

**Gate:** a blacklist alert renders with a visible plate crop image.

---

### P4 — City traffic analytics ⭐ biggest missing module
**Track split, contract-first · ~3–4 days · PS §7**

> 🟡 **Code complete, gate not yet run.** Router, page and heatmap layer all exist — see
> [BUILD_STATE.md](BUILD_STATE.md)'s P4 section for exactly what was built. The session that built
> the frontend half and the tests had no Docker and no Node/npm, so the gate below has not been
> confirmed against a live fleet. That confirmation is the next step.

Nothing existed at the start of this phase: no analytics router, no analytics page, no heatmap layer, zero `time_bucket` /
`date_trunc` / continuous aggregates anywhere. `recharts` is already a dependency and is **imported
zero times** — every "chart" on screen today is a Tailwind div with a percentage width.

New `services/api/app/routers/analytics.py`, over the existing `detections` hypertable using
TimescaleDB `time_bucket`:

| Metric | Derivation |
|---|---|
| Vehicle count / density | count per camera and per corridor, per time bucket |
| Average speed per corridor | correlator hop distance ÷ inter-camera time |
| Route density | most-travelled camera-pair sequences |
| Travel time | current vs baseline, baseline = rolling median of the same time-of-day bucket |
| Hotspots | cameras ranked by count, and by slowdown against baseline |

New `web/src/pages/Analytics.tsx` — **actually use `recharts`**: count time-series, corridor speed
bars, route-density table, travel-time delta cards. Add a **heatmap layer** to
`web/src/components/CameraMap.tsx` weighted by detection count.

> **Honesty rule ([CLAUDE.md §5](../CLAUDE.md#5-coding-conventions)).** Every figure is computed
> from observed rows. Where a baseline has too little history, the UI says **"insufficient
> history"**. A plausible invented number is the worst thing that can go in this repository: it
> survives the demo and destroys the claim.

**Contract first.** This is the one phase where both tracks touch the same thing — agree the
response shapes before either side starts, so neither blocks the other.

**Gate:** with the P1 fleet running, the analytics page shows non-zero density, per-corridor average
speed, a populated route-density table and a heatmap — every figure traceable to real rows.

---

### P5 — Trajectory anomaly detection + explainable alerts
**Track A · ~3 days · PS §9, §13**

What exists today is a **physics filter, not an anomaly detector**: six per-leg flags
(`revisit`, `co_located`, `impossible_simultaneous`, `implausible_speed`, `unobserved_gap`,
`heading_conflict`) that catch cloned plates and OCR misreads. Nothing compares a journey against a
norm. `AlertType.ANOMALY` has **zero producers** — the only two alert producers in the repo are
watchlist match and camera-down.

- **Alembic migration: add a `reasons` JSONB column to `alerts`.** There is nowhere to store factors
  today, which is why CLAUDE.md's "explainability rule (enforced, tested)" is currently false. This
  migration is what makes it true.
- Learn norms from the history `persist_route` now writes (P2): per camera-pair transition frequency
  and travel-time distribution. A rare transition, or a duration well outside its distribution, is
  the signal.
- Promote the per-leg flags into a **journey-level anomaly score with named factors**: unexpected
  camera sequence, travel time vs baseline, unusual speed, impossible hop.
- Raise `AlertType.ANOMALY` through the existing fanout so it appears in `Alerts.tsx` with no new
  transport.
- Frontend: render the factor list — *"flagged because: unusual route (this transition seen 3 times
  in 30 days), travel time 2.4× baseline"*.

> **Language discipline.** Call it a **trajectory anomaly**. Never label a vehicle, plate or driver
> "suspicious" or "criminal" on the basis of a route deviation. An operator shown "SUSPECT" for a
> lane change stops trusting the tool.

**Risk:** a two-week-old system has almost no history to baseline against. **Mitigation:**
`scripts/replay_history.py` — run the **real pipeline** over footage at accelerated pace to
accumulate genuine detection history. Real ANPR output, time-shifted. Label it as such; do not
fabricate rows.

**Gate:** a vehicle driven along a rare camera sequence raises an ANOMALY alert whose stored factors
are shown in the UI; a normal journey does not.

---

### P6 — Bug fixes, tests, demo hardening
**Both tracks · ~2 days**

Six real bugs found during the audit. Each gets a test — several are in currently-untested paths.

| # | Bug | Where |
|---|---|---|
| 1 | `revisit` / `co_located` legs `continue` early, **skipping** the `unobserved_gap` and `heading_conflict` checks entirely | `correlator.py` |
| 2 | `sightings_for` applies `LIMIT 5000` *before* dropping NULL positions and returns the **oldest** rows, with **no truncation indicator** — a "30-day" route can silently stop at day 3 | `correlator.py` |
| 3 | `Match.distance` is hardcoded to `1`, so every near-match alert note reads "(1 character different)" regardless | `watchlist.py` |
| 4 | Dedup `repeats` is never persisted, though the module docstring claims "seen 12 times" is kept | `alerts.py` |
| 5 | `transition()` overwrites `acknowledged_by` on **every** transition, erasing who originally acknowledged | `alerts.py` |
| 6 | `_within_one_edit` duplicated in two files with two different implementations | `watchlist.py`, `correlator.py` |

Untested critical paths to cover: `raise_for_match`, `build_route`, `sightings_for`, `find_convoys`,
`WatchlistIndex.refresh()`.

Also:
- Fix the **HLS fallback** — it opens a raw `.m3u8`, which downloads rather than plays outside
  Safari. Add `hls.js`.
- Seed a **blacklist vehicle that actually appears** in the replayed footage, so step 6 fires on cue,
  and an **anomalous journey** so step 7 does.
- Rewrite `docs/DEMO_SCRIPT.md` to the 8 PS steps, with a fallback for each.
- Rewrite `tests/e2e/test_judge_flow.py` to assert the **8 PS steps** instead of the old brief's
  five judge moments, so the demo path is regression-tested instead of manually trusted.
- Three timed `make clean && make demo` rehearsals from empty volumes.

**Gate:** three consecutive clean-clone runs reach all 8 demo steps in under 5 minutes.

---

## V2 — SIH

### P7 — Earn the >90% accuracy claim ⭐ highest-value V2 item
**~4–5 days, mostly human labelling**

The PS states **>90% plate recognition accuracy under real-world conditions**. We currently have no
real-world number at all. Synthetic-only measurements: **62.5–87.5% end-to-end** across two runs,
**100% exact-match on plates attempted**, CER 0.000. `ai-lab/FINDINGS.md` is explicit that these are
optimistic and must not be quoted as system accuracy.

The bottleneck is **recall, not OCR**. When the pipeline commits to a read it is right; it declines
to read 3 of 8 plates. Chasing a better recogniser is the wrong move.

- Source **real Indian footage** with legible plates, day and night. (`anpr_demo.mp4` is UK — a
  per-camera `plate-region:GB` tag works around it, and no shipped config may accept GB.)
- `make mine` → label `ai-lab/datasets/mined/to_label/labels_to_fill.csv` by hand. The loop already
  exists and pre-fills the pipeline's guess.
- `make evaluate GT=…` → exact-match, CER, precision/recall, calibration table.
- Attack recall: **re-export the detector at 1280** instead of the fixed 640 (known to lose distant
  and night vehicles), and fix **track fragmentation** — one car came back as three tracks, which is
  the entire reason precision is 0.70 rather than ~1.0.
- Publish the measured figure **with its conditions**. If it is below 90%, say so and say why.

**Gate:** a measured exact-match figure on labelled real footage, reproducible from a committed run.

### P8 — Fuzzy and partial plate search
**~2 days · closes the long-abandoned old Phase 8**

- Query the **`pg_trgm` GIN index that already exists on `plate_normalised` and that nothing
  queries**: `GJ03A81234` → suggests `GJ03AB1234`.
- Faceted summary ("7 sightings · 3 cameras · 1 blacklist match"); filters on time, camera, type.
- **Decide OpenSearch's fate.** It runs in the base profile, is only health-probed, and indexes
  nothing. Either index detections into it properly or remove it — today it costs demo-laptop memory
  for no function.

**Gate:** a misread plate returns the correct vehicle ranked first, in under 300 ms.

### P9 — Attribute search (search beyond plates)
**~3 days · PS §14**

Five `detections` columns are **permanently NULL** because nothing produces them:
`vehicle_colour`, `crop_key` (P3), `frame_key`, `direction`, `speed_kmph`.

- **Extract vehicle colour** in the pipeline and populate `vehicle_colour`.
- Extend `vehicle_type` beyond the raw COCO class string it stores today (which also means `person`
  rows currently land in `detections`).
- Query params and UI: *"white SUV, near CAM-17, 10:30–11:00"* → ranked candidates from appearance +
  time + camera adjacency.

**Gate:** an attribute-only query returns plausible candidates with no plate supplied.

### P10 — Predictive traffic
**~4 days · PS §12 · depends on P4 baselines**

- Short-horizon congestion forecast (15 / 30 min) per junction and corridor, from inflow trend, speed
  trend and upstream state.
- Show the **contributing factors** beside the number, per the explainability rule.
- **Backtest against held-out history** and report the forecast's own error. A prediction with no
  error bar is decoration.

### P11 — Vehicle re-identification
**~4 days**

Appearance embeddings, so a vehicle links across cameras even when the plate is unreadable — this
raises effective trajectory recall directly. Nothing exists today: intra-camera tracking is
motion-only (ByteTrack, no appearance branch), and cross-camera linking is plate-string equality.

### P12 — Truth pass on docs and contracts
**~2 days · parallelisable throughout**

- Fix the **~4× core-sizing error** in `docs/INFRASTRUCTURE.md` §2 (it treats a 4-thread worker as
  one core) and align `docs/BRIEFING.md`. Use `scripts/capacity_model.py` as the source.
- **`packages/contracts/` is one empty file.** Either build it for real — JSON Schema, generated
  Pydantic and TS, validation on both sides of the bus — or amend the CLAUDE.md claim. Today the
  event is a hand-rolled dict in `ai-lab/ailab/stream/events.py`, duplicated by hand in
  `simulator/load_mode.py` (which even emits `"auto"`, a vehicle type the real detector cannot
  produce), with no validation either side.
- Purge old-brief residue: "statewide", 80,000 cameras, Model 1/3/5, FAQ Q12/Q15/Q26, Gujarat Police
  / Home Department, SCRB. [CLAUDE.md §10](../CLAUDE.md#10-migration-ledger) lists the sites.
- Rewrite `docs/REQUIREMENTS.md` to map **SIH26127** requirements → implementation. It currently maps
  the old Gujarat challenge.
- `docs/STATUS.md` is stale (written 25 Aug, after Phase 4). Refresh or retire it.
- Make **`mypy` pass** (17 errors across 8 files) and add it to `make lint`, or amend CLAUDE.md §5's
  claim that it passes.

---

## Sequencing for 2–3 devs

| | Track A — backend intelligence | Track B — frontend + analytics |
|---|---|---|
| **Week 1** | P1 fleet → P3 crops | P2 journey UI + playback |
| **Week 2** | P5 anomaly + migration | P4 analytics page |
| **Week 2 end** | P6 — both tracks | P6 — both tracks |
| **SIH wk 1–4** | P7 accuracy → P8 search | P9 attribute search UI |
| **SIH wk 5–8** | P10 prediction → P11 re-ID | P10 UI → P12 docs |

P1 is a hard dependency for almost everything; it is the only phase that must be done before others
can start in earnest. P4 needs its contract agreed before either track begins.

---

## Verification

Every phase gate must produce **real output, pasted into [BUILD_STATE.md](BUILD_STATE.md)** — not
"the code exists". Repo-wide, after every phase:

```bash
make up      # 9 containers healthy
make ai      # ai-worker joins and publishes events
make test    # API + ai-worker + e2e + frontend vitest
make lint    # ruff + ruff format + tsc --noEmit
python3 scripts/capacity_model.py --compare     # sizing still derives
```

Acceptance test for V1:

```bash
make clean && make demo    # from empty volumes, timed
```

Then walk the 8 PS demo steps in the browser. From P6 onward
`tests/e2e/test_judge_flow.py` asserts those 8 steps, so the demo path is regression-tested.
