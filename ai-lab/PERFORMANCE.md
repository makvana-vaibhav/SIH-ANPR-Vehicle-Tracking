# Performance investigation

Profiled first, then optimised. Every number below was measured on this machine;
nothing is estimated except where explicitly labelled as an extrapolation.

**Host:** Docker Desktop on macOS arm64 (Apple Silicon), 10 vCPU, CPU-only.
There is no CUDA on this machine, so no GPU figure appears anywhere in this
document.

**Footage:** `test1.mp4` — **3840x2160 (4K), 30 fps, 60 s**. A 15 s slice
(`runs/_bench/test1_15s.mp4`, 452 frames) was used for the speed A/B, and the
synthetic clip with known plates for the accuracy A/B.

---

## 1. The finding that reframes everything

`test1.mp4` is 4K, and it contains **20.2 plate-bearing vehicles per frame**
(measured across six sampled frames: 21, 20, 19, 22, 21, 18).

That single fact explains the runtime. The pipeline ran plate detection and OCR
*per vehicle, per frame*, so 20 vehicles meant 20 plate searches and up to 40 OCR
calls on every single frame.

It also corrected an assumption we started with. The brief stated plate detection
was the largest bottleneck, which was true of the earlier 720p profile. **At 4K
the ranking flips: OCR is 66% of the run and plate detection 22%.**

---

## 2. Complete profile — before

Run A, 20 frames of 4K, full diagnostic output. Everything is timed, including
the file writing and reporting that were previously unmeasured.

| stage | calls | mean | total | share |
|---|---|---|---|---|
| **ocr** | **284** | **363.2 ms** | **103.1 s** | **66%** |
| **plate_detect** | **266** | **128.7 ms** | **34.2 s** | **22%** |
| detect | 20 | 297.7 ms | 6.0 s | 4% |
| video_transcode | 1 | 3164.0 ms | 3.2 s | 2% |
| video_encode | 20 | 152.3 ms | 3.0 s | 2% |
| decode | 20 | 111.3 ms | 2.2 s | 1% |
| crop_save_vehicle | 20 | 60.1 ms | 1.2 s | 1% |
| preprocess | 142 | 6.9 ms | 1.0 s | 1% |
| annotate_draw | 20 | 47.7 ms | 1.0 s | 1% |
| crop_save_plate | 69 | 13.5 ms | 0.9 s | 1% |
| quality_measure | 308 | 0.6 ms | 0.2 s | 0% |
| html_report | 1 | 112.8 ms | 0.1 s | 0% |
| track | 20 | 5.0 ms | 0.1 s | 0% |
| consensus_incremental | 69 | 1.2 ms | 0.1 s | 0% |
| write_outputs | 1 | 43.6 ms | 0.0 s | 0% |
| crop_extract_plate / _vehicle | 170 / 266 | 0.1 / 0.0 ms | 0.0 s | 0% |
| hard_cases, consensus_final | 1 each | 3.5 / 2.8 ms | 0.0 s | 0% |

**Total: 153.3 s for 20 frames — 0.130 fps, 7665 ms/frame.**
Extrapolated to the full 60 s clip (1800 frames): **~3.8 hours**.

### How often each model actually ran

| stage | calls | per frame |
|---|---|---|
| object detection | 20 | 1.0 |
| **plate detection** | **266** | **13.3** |
| **OCR** | **284** | **14.2** |
| preprocessing | 142 | 7.1 |

This is the table that mattered. A stage's cost is *rate x price*, and a profile
that reports only price cannot tell a slow model from one called too often.

---

## 3. Bottlenecks, ranked by impact

1. **OCR invocation rate — 66%.** 14.2 calls per frame at 363 ms mean. Two
   compounding causes: it ran on every vehicle every frame, and each call was
   frequently taking PP-OCR's expensive text-detection path.
2. **Plate detection invocation rate — 22%.** 13.3 calls per frame. The same
   vehicle was re-searched on consecutive frames that could not possibly show a
   materially different plate.
3. **Object detection — 4%.** Once per frame, unavoidable, and the model itself
   is the cost (see §6).
4. **Diagnostic output — ~6%** (video encode 2%, transcode 2%, crop saving 2%).
   Real but an order of magnitude below inference.
5. **Result writing and reporting — under 0.2%.** CSV/JSON 44 ms, HTML report
   113 ms for the whole run.

### What turned out **not** to be a bottleneck

Worth recording, because three of these were plausible and wrong:

* **Frame resizing.** Letterboxing a 4K frame to 640 px costs **5.2 ms**. Doing
  the downscale in two steps, or with a cheaper interpolation, saves single-digit
  milliseconds against a ~110 ms model call. Not worth doing.
* **Video decoding.** 111 ms/frame at 4K, 1% of the run.
* **CSV/JSON/HTML output.** Under 0.2% combined.
* **The new gates themselves.** The perceptual crop gate costs **0.1 ms** per
  decision against the ~75 ms OCR call it may avoid.

---

## 4. The six optimisations

Each was applied because a measurement pointed at it, not because it seemed
sensible.

### 1. Selective plate scheduling — the largest win

Per-vehicle scheduling replaces "search every vehicle every frame". A vehicle is
searched when it is new, when its box has changed scale by ≥18% or moved ≥30% of
its short side, or when a maximum interval has elapsed; vehicles that repeatedly
yield nothing back off progressively.

Measured on run B: **searched 29 of 266 opportunities — 11%.** Skips by reason:
`unchanged` 182, `cooldown` 55.

### 2. Perceptual crop gate — stop re-reading the same plate

A 12x12 difference hash fingerprints each plate crop. A crop earns an OCR call
only when it is genuinely new evidence: a first read, materially sharper or
larger than the best so far, perceptually different from the last one, or when
what we already have is weak or unreadable.

This is what stops track #17 having its plate read 50 times across 50 frames
while still collecting the several *distinct* looks consensus needs.

### 3. Recognition-first OCR, with a gated fallback

PP-OCR's text-detection stage costs **1334 ms** against **74 ms** for recognition
alone — **18x** — and returns the identical string on an already-cropped plate.
Recognition now runs first; detection is a fallback, allowed only where it could
help (a squarer crop that might be a two-row plate, tall enough to resolve two
rows).

Evidence for the gate: your `rajkot-bus-stand` run averaged **974 ms per OCR call
across 186 plate regions and produced zero successful reads.** Every one of those
calls was paying the expensive path to discover the plate was unreadable.

### 4. Fixed 320 px plate-detector input

The previous rule scaled the network input with the vehicle crop, capped at 640.
At 4K every crop hits the cap, so it degenerated to always using the maximum.

A plate is a roughly *constant fraction* of a vehicle — about a fifth of its
width — so letterboxing any vehicle crop to 320 px puts the plate at roughly
70 px whether the crop came from a 4K frame or a 720p one. Cost becomes constant
per search and independent of source resolution.

Measured on a 728x560 vehicle crop: 640 px 96.1 ms, **320 px 58.9 ms**, 256 px
67.7 ms, 192 px 78.0 ms. 320 is the floor of the curve — smaller is not faster,
because below that the fixed per-call overhead dominates.

### 5. Preprocessing variant early-exit

Variants exist to rescue crops the first recipe failed on. Once one produces a
grammar-valid read above 0.80 confidence there is nothing left to rescue, so the
rest are skipped. Preprocessing calls fell from 7.1 to 0.35 per frame.

### 6. Two invisible inefficiencies

* `best_observation` rescanned a track's entire history on every frame for every
  vehicle — quadratic over a long track, and invisible in a profile that only
  times model inference. Now maintained incrementally.
* Per-variant quality measurement computed a Laplacian variance and then
  discarded it, keeping only brightness and contrast. Now uses a cheap path.

---

## 5. Before and after

### Speed — 4K footage, 20 frames, identical settings except the six changes

| | before | after | change |
|---|---|---|---|
| wall time | 153.3 s | **23.0 s** | **6.7x faster** |
| throughput | 0.130 fps | **0.869 fps** | 6.7x |
| per frame | 7665 ms | **1151 ms** | |
| detect calls | 20 (1.0/frame) | 20 (1.0/frame) | unchanged |
| plate detect calls | 266 (13.3/frame) | **29 (1.45/frame)** | **9x fewer** |
| OCR calls | 284 (14.2/frame) | **14 (0.7/frame)** | **20x fewer** |
| tracks found | 16 | 16 | unchanged |
| vehicles | 16 | 16 | unchanged |

Extrapolated to the full 60 s 4K clip: **3.8 hours → 35 minutes.**

### Inference alone, with diagnostics switched off

`bench` mode runs the same models and makes the same decisions, but writes no
annotated video, no crops and no HTML report:

| | before | after (default) | after (bench) |
|---|---|---|---|
| per frame | 7665 ms | 1151 ms | **360 ms** |
| throughput | 0.130 fps | 0.869 fps | **2.78 fps** |
| vs before | — | 6.7x | **21x** |

Diagnostic output costs **3.2x the inference itself** at 4K — most of it the
annotated-video encode and the H.264 transcode. That is a reasonable price for
what the lab exists to do, and it is why the two are now separable: benchmarking
inference with video writing switched on measures the video writer.

Extrapolated to the full 60 s 4K clip in `bench` mode: **~11 minutes.**

### Accuracy — synthetic footage with known plates, 120 frames

The 4K clip cannot answer the accuracy question: its plates are not Indian and
every reading from it is grammar-invalid regardless of configuration. So accuracy
was measured where the right answer is known.

| | before | after |
|---|---|---|
| throughput | 0.97 fps | **4.39 fps** (4.5x) |
| plate detections | 376 | 101 |
| OCR attempts | 60 | 40 |
| plates resolved | 7 of 13 | 7 of 13 |
| **exact plate match** | 80.0% | **100.0%** |
| **end-to-end accuracy** | 50.0% | **62.5%** |
| **character error rate** | 0.020 | **0.000** |
| correct / wrong / missed | 4 / 1 / 3 | **5 / 0 / 3** |

**The faster pipeline is also the more accurate one** — one more plate correct,
one fewer wrong, the same three missed.

That is not luck, and it is worth understanding rather than celebrating. The
noise reads consensus previously had to outvote were coming from crops too small
or too repetitive to be worth reading. Declining to read them removes the errors
at the source instead of correcting them afterwards. A recogniser handed an
unreadable crop does not answer "I cannot read this" — it answers confidently and
wrongly.

---

## 6. Trade-offs, stated plainly

* **Fewer observations per vehicle.** This is the intended change, and it is the
  one that could go wrong on different footage. The mitigations are that a
  vehicle is *always* searched on first sight, that a maximum interval forces a
  periodic look even when nothing appears to have changed, and that vehicles
  which have never yielded a readable plate keep getting attempts.
* **A plate visible for only one or two frames** is the realistic failure mode.
  First-sight search covers it, but a vehicle that is briefly legible and then
  occluded could be missed where the old pipeline would have caught it. Not
  observed in these runs; worth watching on real footage.
* **Every skip is counted by reason** in `summary.json` under `plate_schedule`
  and `crop_gate`. The speedup is auditable rather than a silent quality change.
* **Nothing was removed from the lab.** Every output — annotated video, crops,
  raw reads, failed reads, hard cases, consensus, reports — is still produced.
  What changed is how often expensive models run, not what is recorded.

---

## 7. Processing modes

Rather than one compromise, five configurations spanning the trade-off:

| mode | plate input | scheduling | variants | video | report | use |
|---|---|---|---|---|---|---|
| `diagnostic` | 640 proportional | off | 4, all run | yes | yes | study one clip in detail |
| `accurate` | 640 | min interval 1 | 4 | yes | yes | find the accuracy ceiling |
| `default` | 320 fixed | min interval 3 | 2 | yes | yes | balanced |
| `fast` | 320 fixed | min interval 6 | 1 | yes | yes | sweep long footage |
| `bench` | 320 fixed | min interval 3 | 2 | **no** | **no** | measure inference alone |

`bench` matters for this investigation specifically: measured at 360 ms/frame
against 1151 ms/frame for `default` on the same 20 frames, diagnostic output is
**3.2x the cost of the inference it observes**. Benchmarking with the annotated
video switched on measures the video writer as much as the models.

---

## 8. What is left, and what is unknown

**Remaining opportunities, largest first:**

1. **Object detection is now the biggest single stage** (27% of run B). The COCO
   weights are **fp16**, and fp16 on CPU is poorly served by ONNX Runtime — it is
   also why the thread-count effect is so violent (452 ms at the library default,
   126 ms at 4 threads). Converting the model to fp32 is the obvious next test.
   Not done here because it needs the torch profile to re-export.
2. **Diagnostic output is 26% of run B.** Already addressed by `bench` mode; the
   default keeps it because the lab's purpose is observability.
3. **Frame striding.** Untested at 4K. At 30 fps, analysing every second frame is
   likely to cost little in ANPR terms, but this should be measured across the
   metrics in §5 rather than assumed.

**Unknown, and deliberately not guessed:**

* **GPU throughput.** There is no CUDA on this machine. The inference layer
  selects execution providers through one function (`select_providers`) and the
  CUDA path is implemented, but it has never been run. **No GPU FPS number is
  offered, because none has been measured.**
* **Real-time viability.** Run B reaches 0.87 fps at 4K and 4.4 fps at 720p on
  one CPU process. Live CCTV needs far more, and the architecture's answer is
  inference at the edge with one worker per small group of cameras — but that is
  a design claim, not a measurement, until it is run on the target hardware.

---

## 9. Second pass: tracking, streaming, events

A later round of work addressed the defect the first pass identified — track
fragmentation — and added the live-stream path. Measured on the ground-truth
clip (240 frames, 720p, 6 known plates):

| | before this pass | after |
|---|---|---|
| throughput | 3.44 fps | **4.13 fps** |
| raw tracks | 22 | 15 |
| reported vehicles | 22 | **13** |
| exact plate match | 100% | 100% |
| end-to-end accuracy | 75.0% | **83.3%** |
| precision | 0.71 | **0.83** |
| missed | 2 | **0** |
| spurious (duplicate vehicles) | 2 | **0** |

Four changes, each traced to a measurement:

1. **Plate ownership resolution.** Overlapping vehicle boxes meant the same
   plate pixels were found inside several vehicles' crops — 76 cases in one
   clip at IoU up to 0.91, each costing a duplicate OCR call and attaching one
   physical plate to several tracks. Each plate region is now kept once and
   attributed to the smallest tracked vehicle containing it.
2. **Track merging by plate identity.** Fragments carrying the same plate
   (within one character) and compatible in time become one vehicle, reads
   pooled and consensus recomputed. Runs in two passes so that a corrupt
   fragment starting *earlier* than the good one still merges — the measured
   case was `JO8AJ1643` preceding `GJ08AJ1643`.
3. **Evaluation against merged vehicles.** Scoring raw tracks double-counted a
   fragmented car: one instance matched ground truth and the other was scored
   spurious, penalising the pipeline for an artefact the merge had repaired.
4. **The scheduler no longer starves short tracks.** A vehicle that has never
   yielded a plate gets a guaranteed number of attempts before any cooldown
   applies — a sprite visible for four frames previously got one look.

### A benchmark that was measuring the wrong thing

Two plates were being missed entirely, and the cause was not the pipeline. The
footage generator placed vehicles at random heights with nothing preventing
overlap, so two of eight sprites were occluded and never visible to read at all.
Separating them into lanes took the missed count from 2 to **0** without any
change to inference.

That is worth recording as a caution rather than a triumph: for several rounds
the measurement was blaming the pipeline for a defect in the test rig.

### The one remaining error

`GJ35K5714` read as `GJ35X5714`. Both K and X are letters in a letter slot, so
grammar cannot repair it, and every frame agreed — this is a genuine recognition
error, not a consensus failure. It is the kind of case that needs a better OCR
model or a fine-tune, which is what the mined dataset is for.

---

## 10. Live-stream latency

Throughput says nothing about whether an alert arrives in time. Measured over a
20-second run against a 15 fps source, with the reader pacing the file as a
camera would:

| | |
|---|---|
| frames decoded / processed | 240 / 71 |
| frames dropped | 169 (**70%**) |
| processing rate | 4.42 fps |
| **capture-to-event latency, median** | **385 ms** |
| p90 / p99 | 493 ms / 559 ms |
| events emitted | 31 (10 provisional, 21 final) |

The 70% drop rate is the design working, not failing. Inference is slower than
the camera, so the reader keeps only the newest frame and the processor stays
current. **Latency stayed bounded at a few hundred milliseconds instead of
growing without limit** — which is the difference between an alert and a
historical record. The worker says so plainly in its summary rather than
reporting a healthy-looking fps and hiding the drops.

---

## 11. Reproducing this

```bash
# the two speed runs
ailab run runs/_bench/test1_15s.mp4 --limit 20 --config experiments/pre_optimisation
ailab run runs/_bench/test1_15s.mp4 --limit 20 --config default

# the two accuracy runs
ailab run runs/_synthetic/synthetic_traffic.mp4 --limit 120 --no-video \
    --config experiments/pre_optimisation --ground-truth runs/_synthetic/ground_truth.csv
ailab run runs/_synthetic/synthetic_traffic.mp4 --limit 120 --no-video \
    --config default --ground-truth runs/_synthetic/ground_truth.csv

ailab compare <run-a> <run-b>
```

`configs/experiments/pre_optimisation.yaml` reproduces the old behaviour exactly,
differing from `default` only in the six things that were changed — so the
comparison isolates their effect instead of measuring an accumulation of
unrelated differences.
