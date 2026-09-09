# NagarNetra — Solution Presentation

**How to use this file.** Every slide below gives you: a title, the exact text
to put on the slide, what visual belongs there, and speaker notes. Hand the
whole file to an AI deck builder, or build it manually — the content is final
either way.

**Design direction for the whole deck:** dark navy (`#0f172a`) background,
white/slate text, one cyan accent (`#0891b2`) and one red accent (`#dc2626`)
used *only* for alerts. Big numbers, few words. This is a police operations
product; it should look like a control room, not a startup pitch.

**Two images ship with this file** and are referenced by slide:
`architecture.svg` and `workflow.svg`.

**A rule that runs through the deck:** every number is labelled *measured*,
*arithmetic* or *estimated*. Judges from a government department have sat
through many decks of invented figures. Being the one that distinguishes them
is worth more than a bigger number would be.

---

## Slide 1 — Title

> # NagarNetra
> ### Statewide CCTV Intelligence Platform
> **Gujarat Police / Home Department — CCTV Challenge**
>
> Model 5 (Hybrid): Registry + GIS · Federation middleware · Unified viewing
>
> `Team name` · `Date`

**Visual:** full-bleed dark map of Gujarat with ~280 glowing camera points,
taken as a screenshot of your own GIS map screen. Not stock imagery — your
actual product.

**Speaker note:** "We were asked to design a system for 80,000 cameras the
state does not uniformly own. So we did not build a camera system. We built
the layer that makes 80,000 cameras usable."

---

## Slide 2 — The problem, stated as the state experiences it

> ## Gujarat already has the cameras.
> ## It does not have one way to use them.
>
> - Multiple **departments** — police, traffic, municipal, highways, transport
> - Multiple **vendors** — Milestone, Genetec, Hikvision, and more
> - Each with its own VMS, its own login, its own island of footage
>
> **Tracking one vehicle across three districts today means three phone calls
> and hours of manual review.**

**Visual:** four disconnected silos, each with a padlock, no lines between
them. Deliberately ugly.

**Speaker note:** Do not say "there is no integration". Say what it costs:
time, during an investigation, when time is the thing that matters.

---

## Slide 3 — The decision that shapes everything

> ## We federate. We do not replace.
>
> | Model 4 — Central VMS | Model 5 — Hybrid (ours) |
> |---|---|
> | Rip out every departmental VMS | Existing VMS stay authoritative |
> | One vendor owns the state | Vendor-neutral adapters |
> | 320 Gbps of video to the centre | 43 Mbps of metadata |
> | Politically and financially unrealistic | Deployable on existing links |
>
> ### The central tier carries **events, not video**.

**Visual:** two-column comparison, Model 4 in muted grey, Model 5 in cyan.

**Speaker note:** This is the single most important slide. Rehearse it. The
reason we do not centralise video is not technical preference — it is that
ripping out working departmental systems is not something a state can actually
do. Federation is the only realistic path, and it happens to also be ~7,000×
cheaper in bandwidth.

---

## Slide 4 — Architecture

> ## One platform, five layers

**Visual:** `architecture.svg`, full slide. Do not shrink it into a corner.

**Speaker note:** Walk it bottom-up in three sentences: "Existing cameras stay
where they are. Adapters bring their metadata into one registry and pull their
video only when someone is watching. AI runs at the edge, so what crosses the
state network is a two-kilobyte event, not a video stream."

---

## Slide 5 — The number the architecture rests on

> ## 320 Gbps → 43 Mbps
> ### ~7,000× less
>
> | | Centralise video | Federate events |
> |---|---|---|
> | Per camera | 4 Mbps H.264 | ~2 KB per vehicle |
> | 80,000 cameras | **320 Gbps** | **43 Mbps** |
> | Per district (2,400 cams) | 9.6 Gbps | **1.3 Mbps** |
>
> A district's existing 10 Mbps uplink already carries this.
> *Arithmetic. Event size measured from real payloads.*

**GRAPH 1 — "The bandwidth argument"**
Horizontal bar chart, log scale on x-axis.
- Bar A: "Centralised video — 320,000 Mbps" (red `#dc2626`)
- Bar B: "Federated events — 43 Mbps" (green `#16a34a`)
Annotate the gap with "~7,000×". Log scale is essential — on a linear axis the
second bar is invisible, which is itself the point, so consider showing both.

**Speaker note:** "No new fibre. That is the practical argument."

---

## Slide 6 — Judge moment 1: the registry and the map

> ## 281 cameras, federated, on one map
>
> - Bulk **CSV** onboarding with per-row validation and an error report
> - **Manual** form for a single camera
> - **Self-registration API** so another department onboards its own
> - Filter by department · district · status · vendor · ANPR capability
> - **Works offline** — local GeoJSON basemap, no tile server, no API key

**Visual:** screenshot `01-gis-map.png` (see `SCREENSHOTS.md`).

**Speaker note:** "The map has no external dependency. On a venue with no wifi,
it renders identically." That claim wins more trust than a prettier basemap.

---

## Slide 7 — Judge moment 2: live ANPR

> ## Plates read from live traffic, in the browser
>
> - Runs across the **whole fleet in the background**, not one camera at a time
> - Open any camera and see what it has been reading
> - Bounding boxes drawn live over the video
> - **One event per vehicle**, not one per frame

**Visual:** screenshot `02-live-anpr.png` — video playing with plate overlay.

**Speaker note:** The distinction to land: "Most ANPR demos read a plate from
one frame. A car crossing a junction gives you twenty frames and twenty
slightly different answers. Reconciling those is the actual problem."

---

## Slide 8 — How we get the plate right

> ## Multi-frame consensus
>
> 1. Buffer **every** read for a tracked vehicle — not just the best frame
> 2. Vote **per character position**, weighted by
>    **OCR confidence × crop sharpness** — a large sharp read outvotes a small blurry one
> 3. Correct confusions **by slot**: `0↔O` only where the format expects a letter
> 4. Validate against Indian + **BH-series** grammar
> 5. Emit **one** event, carrying the top-3 candidates so an operator can overrule it

**GRAPH 2 — "Consensus in action"**
A table styled as a graphic. Rows = 5 frames, columns = characters.
Show frames disagreeing (`GJ03AB1Z34`, `GJ03A81234`, `GJ03AB1234`…) and the
consensus row beneath in cyan: `GJ03AB1234`, with a confidence bar per
character. This visual makes the idea land in three seconds.

**Speaker note:** "The system also tells you when it is unsure. A read that
fails grammar validation is flagged, not dropped, and never reaches the
watchlist on its own."

---

## Slide 9 — Judge moment 3: the alert fires by itself

> ## Nobody has to be watching
>
> `GJ03AB1234` added to watchlist → seen by any camera → **red alert**
> with plate crop, camera, timestamp, confidence, case reference
>
> ### Detection → alert on a connected screen: **24 ms**
> *Measured. Budget was 2 seconds.*
>
> - Matching is **O(plate length)**, not O(watchlist) — a deletion index
> - Near-matches (one character off) surface as **medium priority, labelled**
> - Lifecycle: new → acknowledged → dispatched → closed — each a separate permission

**Visual:** screenshot `03-alert-fired.png` — the red alert card, full detail.

**Speaker note:** "Acknowledging an alert, dispatching a unit and closing a
record are three different authorities in a real control room. So they are
three different permissions here."

---

## Slide 10 — Judge moment 4: a plate becomes a route

> ## One search → the whole journey
>
> Type `GJ03AB1234` → every sighting → **a drawn route across cameras**
> with timestamps, distances and implied speeds
>
> - Repeat sightings at one camera merged into a single hop
> - Implied speed from great-circle distance — so it is a **lower bound**
> - Implausible legs are **flagged, not deleted**

**Visual:** screenshot `04-vehicle-route.png` — the route drawn on the map with
the hop table beside it.

**Speaker note (this is the line that impresses):** "The distance we measure is
a straight line. The road is longer. So when we say the implied speed was
60 km/h, the real speed was *at least* that. We show the number we can defend."

---

## Slide 11 — Judge moment 5: does it scale?

> ## We ran the load test. Here is what it said.
>
> **80,000 camera identities · 3 ingest workers · one laptop · CPU only**
>
> | | |
> |---|---|
> | Offered | 2,664 events/s |
> | **Sustained** | **2,774 events/s** |
> | Detections written | **388,713** |
> | Failures | **0** |
> | Watchlist alerts raised under load | **397** |
> | Backlog | peaked at 10,237, **cleared completely** |
> | p95 capture → persisted | 5.7 s |
>
> *Measured, not extrapolated.*

**GRAPH 3 — "Ingest under statewide load"**
Dual-axis line chart over the run's elapsed time.
- Left axis, cyan line: ingest rate (events/s) — hovering around the offered rate
- Right axis, grey filled area: backlog (queued events) — staying near zero
The story the shape tells: offered load goes in, nothing piles up.
*(Data: `tests/load/results/latest.md` and the matching JSON.)*

**Speaker note:** "This is one laptop. The architecture is horizontally scaled —
three workers here, and the rate scales with workers because Redis hands each
event to exactly one of them."

---

## Slide 12 — What the load test found

> ## A load test that finds nothing was not a load test
>
> ### `upper(camera_code)` defeated the database index.
>
> - At our 281 development cameras: **invisible**, sub-millisecond
> - At 80,000 cameras: **106 ms per lookup**, 80,278 rows scanned each time
> - Ingest collapsed from ~1,500 events/s to **140**
>
> **Fixed** — migration `0003`, an expression index. Throughput recovered **7.5×**.
>
> It also found that two API replicas would have split the live event feed
> between them, so half the control room would silently see half the traffic.
> **Fixed** — live fan-out separated from durable consumption.

**Visual:** before/after bar pair — 140 ev/s vs 1,050+ ev/s — with the
`EXPLAIN` output as a small monospace inset showing `Seq Scan` → `Index Scan`.

**Speaker note:** This slide is why judges will believe the rest of the deck.
A team that reports the bug its own test found is a team whose other numbers
mean something.

---

## Slide 13 — Security and lawful use

> ## It is a surveillance system. That shapes the build.
>
> - **6 roles × 19 permissions**, enforced server-side on every request
> - **Every** plate search, stream open and watchlist change writes an audit row
> - **Denied attempts are recorded** — what someone tried is as interesting as what they managed
> - **Reading the audit log is itself audited** — including for `admin`
> - An **auditor cannot run a vehicle search**: reviewing who traced whom is a
>   different job from tracing people
> - Retention is **enforced by deletion**, not stated in a policy document
> - Watchlist entries carry a **case reference** — purpose limitation made visible

**GRAPH 4 — "RBAC matrix"**
Grid: 6 role rows × 19 permission columns, filled cyan / empty. Highlight two
cells in red with callouts: "operator cannot dispatch" and "auditor cannot
search vehicles". The visual asymmetry is the argument.

**Speaker note:** "Technical controls do not make surveillance lawful. What we
can provide is that every trace is attributable to a named officer, and that
the record of who looked at whom outlives the data it describes."

---

## Slide 14 — What is NOT built

> ## What we have not done
>
> | Gap | Consequence |
> |---|---|
> | **No TLS** | Cleartext. Disqualifying for deployment on its own. |
> | **No encryption at rest** | DB and object store unencrypted |
> | **No backups, no tested restore** | So we claim **no** RPO or RTO |
> | **Plate weights are AGPL-3.0** | Licence obligation to resolve before shipping |
> | **Media retention unwired** | The 90-day crop rule currently deletes nothing |
> | **No penetration test** | Unknown unknowns |
>
> All of this is in our `docs/SECURITY.md` §8 and `docs/BUILD_STATE.md`.
> **If you find something not on this list, that is a real finding.**

**Speaker note:** Put this slide in. Every judge is looking for what you are
hiding; showing them first is the strongest position in the room. Say the
line: "A security document that omits its gaps is worse than none, because it
stops anyone looking."

---

## Slide 15 — Deployment reality

> ## What a real deployment needs
>
> | Fleet | Ingest nodes | Bandwidth to centre |
> |---|---|---|
> | 2,400 (one district) | ~8 × 40-core | 1.3 Mbps |
> | 80,000 (statewide) | ~250 × 40-core | 43 Mbps |
>
> - **CPU-only figures.** A single mid-range GPU per node changes this by
>   roughly an order of magnitude — *stated as expectation, because this
>   repository has never executed a GPU path*
> - Storage: detections ~168 TB/year at full scale — a **TimescaleDB hypertable**,
>   so expiry unlinks partitions rather than scanning rows
> - **Crops are the expensive tier.** The lever is storing them only for
>   watchlist hits and low-confidence reads

**GRAPH 5 — "Storage tiering"**
Three stacked horizontal bands: Hot (7 d, NVMe) → Warm (90 d, HDD) → Cold
(1–5 y, object store), with capacity annotated on each.

---

## Slide 16 — How a new camera joins

> ## Integrating a camera the state buys tomorrow
>
> 1. Add a row — CSV, form, or API
> 2. Name its **adapter type**: `rtsp` · `onvif` · `vendor_vms`
> 3. Point `credentials_ref` at a **secret store entry — never a secret**
> 4. Health probing starts automatically; it appears on the map
> 5. Set `anpr_enabled` and the worker picks it up on its next roster refresh
>
> ### No code change. No redeploy. No downtime.
>
> A vendor we have never seen needs **one new adapter class** implementing five
> methods — `connect`, `get_stream_url`, `probe_health`, `list_cameras`,
> `get_recording`.

**Visual:** the five-method interface as a code block, syntax-highlighted.

---

## Slide 17 — Tech stack

> ## Built to run on one laptop, offline, with zero paid APIs
>
> | Layer | Choice | Why |
> |---|---|---|
> | API | FastAPI · Python 3.11 · SQLAlchemy 2.0 async | async throughout, typed |
> | Database | PostgreSQL 16 + **PostGIS** + **TimescaleDB** | geospatial + time-series in one |
> | Search | OpenSearch 2.19, **pg_trgm fallback at runtime** | the demo survives a dead container |
> | Bus | Redis Streams → **Redpanda** at scale, one interface | config swap, not a rewrite |
> | AI | YOLO + ByteTrack + ONNX CRNN, **ONNX Runtime** | torch-free runtime, ~400 MB not 2.5 GB |
> | Media | MediaMTX — RTSP → WebRTC/HLS, **on-demand** | an unwatched camera costs zero |
> | Frontend | React 18 · Vite · TypeScript · **MapLibre GL** | no tile server, no API key |
>
> **Zero paid APIs. Zero cloud. Fully offline-capable.**

**Speaker note:** "No Google Maps, no OpenAI, no hosted inference. This is
police infrastructure — it cannot depend on someone else's uptime or invoice."

---

## Slide 18 — Engineering discipline

> ## How we know it works
>
> | | |
> |---|---|
> | Automated tests | **443** |
> | Judge scenarios run headlessly | **5 of 5** |
> | Database schema | Alembic migrations — never `create_all` |
> | Demo path | `make demo` **verifies each judge moment** before saying ready |
> | If it breaks on stage | `docs/PANIC.md` — triage per component |
>
> `make demo` → populated map, live ANPR, alerts firing. **Under 5 minutes,
> from a fresh clone.**

**Speaker note:** Mention that `make demo` caught a real bug: the seed had
never loaded the watchlist file, so a fresh clone would have reached an empty
watchlist and two judge moments would have had nothing to fire on.

---

## Slide 19 — Impact

> ## What this changes
>
> ### Operationally
> Investigation that took **hours of phone calls** becomes a **search box**.
>
> ### Financially
> Uses cameras the state **already owns**. No rip-and-replace.
> No new fibre — 43 Mbps fits existing district links.
>
> ### For governance
> Every trace **attributable**. Every search **audited**.
> Retention **enforced**, not merely stated.
>
> ### Measured today
> 281 cameras federated · 24 ms to alert · 19 s to detect a dead camera ·
> 388,713 events written with zero failures

---

## Slide 20 — Close

> # The central tier carries events, not video.
>
> ### That one sentence is why this scales to 80,000 cameras
> ### on infrastructure Gujarat already has.
>
> `github.com/<your-repo>` · `make demo`

**Visual:** the Gujarat map again, now with a route drawn across four cameras.
Bookends the deck with slide 1.

---

# Appendix slides (hold in reserve for Q&A)

**A1 — Failure modes.** Ten of them, each with the degradation: OpenSearch down
→ trigram search; WebRTC blocked → HLS; satellite basemap unreachable → offline
vectors; Redis down → rate limiter fails open (a locked-out control room is the
worse failure).

**A2 — Why ONNX CRNN and not PaddleOCR.** PaddlePaddle ships no aarch64 Linux
wheel. The engine sits behind an `OcrEngine` interface with a `PaddleOcrEngine`
selectable by env var for amd64 deployments. *Have this ready — it shows the
substitution was forced and reversible, not lazy.*

**A3 — ANPR accuracy, honestly.** Generated plates with ground truth: 100%
exact, CER 0.000 — **optimistic by construction**, since rendered plates have no
embossing, dirt or motion blur. Real footage: 12 of 13 plates resolve to a valid
format. The organisers' grid: occasional, and it moves with the light — at night
it reads roughly zero, because those are junction overviews with headlight
glare, not lane-facing ANPR cameras. Detection, tracking, health and events work
against it at any hour; plate reading does not.

**A4 — The system also reads signage.** A camera called "Delight" produced
`DELIGHT` at 0.99 confidence. Grammar validation marks it invalid so it never
reaches the watchlist — which is exactly why the alert path is gated on
`grammar_valid` and not on confidence.
