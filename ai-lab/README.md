# NagarNetra AI Lab

A standalone environment for building, measuring and improving the CCTV vision
pipeline — **deliberately not connected to the NagarNetra platform.**

Give it footage, get back everything the AI understood about it: every vehicle,
every track, every plate region, every OCR attempt including the wrong ones, the
final consensus per vehicle, an annotated video, structured JSON/CSV, and an
inspectable HTML report.

```
video ─► frames ─► object detection ─► tracking ─► plate detection
                                                        │
        vehicle result ◄─ consensus ◄─ OCR ◄─ crop conditioning
                │
                └─► annotated.mp4 · vehicles.csv · plate_reads.csv · events.jsonl · report.html
```

---

## Why this is separate

The platform's job is to be reliable. The lab's job is to find out what is true.
Those need different rules, so they get different code:

* The lab may carry heavy or AGPL-licensed dependencies (Ultralytics, PyTorch)
  that must never ship in a deployed NagarNetra artifact.
* The lab keeps everything — every failed read, every rejected crop. The
  platform would drown in that.
* The lab is allowed to be slow. The platform is not.

Nothing here writes to the platform's database, bus, or API. Integration comes
later, through a clean interface, once the pipeline has earned it. `events.jsonl`
is already shaped like the platform's future event contract so that step is a
transport change rather than a data-model change.

---

## Quick start

```bash
cd ai-lab

./scripts/fetch_models.sh      # ~22 MB of ONNX weights, no API key
make build                     # build the container
make doctor                    # verify dependencies, engines and weights

make run VIDEO=/data/videos/road_cctv.mp4
open runs/<latest>/report.html
```

`/data/videos` inside the container is the main repo's `data/videos`, mounted
read-only.

Without Docker: `pip install -r requirements.txt && pip install -e .`, then use
the `ailab` command directly. Python 3.11 is required.

---

## What you get from one run

```
runs/2026-08-25T18-30-00__road_cctv__default/
├── run.json               config + environment + model provenance
├── summary.json           every statistic the run produced
├── detections.csv         one row per object per frame
├── plate_detections.csv   every plate region located
├── plate_reads.csv        every OCR attempt — including rejected ones
├── vehicles.csv           one row per vehicle: the final answer
├── events.jsonl           integration-shaped, one event per vehicle
├── evaluation.json        measured accuracy (only with --ground-truth)
├── annotated.mp4          boxes, IDs, plates, confidences, trails
├── report.html            self-contained; opens offline
├── crops/plates/          every plate crop that was read
├── crops/vehicles/        best frame of each tracked vehicle
└── hard_cases/            what the pipeline struggled with, and why
```

### The plate reads are the point

`plate_reads.csv` keeps every observation, not just the winner:

| track | frame | text | ocr_conf | weight | variant | sharpness |
|---|---|---|---|---|---|---|
| 7 | 142 | GJ03AB1234 | 0.92 | 0.71 | rectified | 240 |
| 7 | 149 | GJ03AB1234 | 0.95 | 0.78 | rectified | 310 |
| 7 | 156 | GJ03AB1284 | 0.61 | 0.22 | upscaled  | 61  |
| 7 | 163 | GJ03AB1234 | 0.94 | 0.75 | rectified | 290 |

`vehicles.csv` then carries the consensus — `GJ03AB1234`, 3 of 4 reads agreeing,
with the disputed character position marked as the least confident. The outlier
is never thrown away: it is the evidence that this was a difficult plate, and it
is exactly what a future fine-tune should train on.

---

## Measuring instead of trusting

**Model confidence is a claim. Accuracy is a measurement.** The lab keeps them
apart everywhere, and refuses to report accuracy it has not measured.

```bash
ailab evaluate latest --ground-truth truth.csv
```

Ground truth is a CSV with a `plate` column, optionally keyed by `track_id`:

```csv
track_id,plate
7,GJ03AB1234
12,MH12DE1433
```

You get exact-match accuracy, character error rate, precision/recall, a
character-substitution breakdown, a threshold sweep — and the calibration table,
which is the one that changes decisions:

```
confidence   n   claimed  measured   gap
0.9-1.0     10       92%       50%  +42pp     ← badly overconfident
0.7-0.8     14       74%       71%   +3pp     ← trustworthy
```

A pipeline that is 92% confident and 50% right is not a pipeline you can
threshold at 0.9.

---

## Comparing models

Every component is swappable by name, so a comparison is a config change:

```bash
ailab run input.mp4 --ocr easyocr           # swap one engine
ailab sweep input.mp4 --configs default,fast,accurate
ailab compare runs/A runs/B
```

Shipped experiments in `configs/experiments/`:

| config | question it answers |
|---|---|
| `no_consensus` | Is multi-frame voting worth its complexity, or is best-read enough? |
| `classical_plates` | What does the learned plate detector actually buy over edge detection? |
| `ocr_shootout` | Does EasyOCR — what every tutorial uses — beat RapidOCR on *our* footage? |
| `detector_yolo11` | Is the newer detector better on grainy CCTV, or only on COCO? |

`make ablation` runs the two that matter most.

---

## Improving the models

```bash
ailab mine latest --out datasets/v1
```

Exports what the pipeline got wrong, sorted into:

* `plate_detection/` — YOLO-format vehicle crops with plate boxes
* `plate_ocr/` — crops auto-labelled from high-confidence consensus only
* `to_label/` — **the uncertain cases, with a CSV for a human to correct**

That third directory is the honest one. A hard case has no reliable label by
definition — if the pipeline could label it correctly it would not be hard.
Training on the pipeline's own uncertain output teaches the model to repeat its
mistakes with more conviction. Fill in `labels_to_fill.csv` first.

---

## Components

| stage | default | alternatives |
|---|---|---|
| object detection | `yolo_onnx` (YOLOv8n COCO) | `ultralytics` *(torch)* |
| tracking | `bytetrack` | `iou` (baseline) |
| plate detection | `plate_yolo_onnx` (YOLO11n) | `contour` (classical, no weights), `plate_ultralytics` *(torch)* |
| OCR | `rapidocr` (PP-OCRv4 ONNX) | `easyocr` *(torch)*, `crnn_onnx` (bring your own) |

`ailab engines` lists them; `ailab doctor` says which are actually usable here.

The default stack is **torch-free** — the whole pipeline runs on ONNX Runtime,
which is also what the production worker will run, so measurements here transfer
instead of having to be redone after an export. Build with
`make build PROFILE=torch` to add the PyTorch engines for export, fine-tuning
and cross-engine comparison.

---

## Design decisions worth knowing

**Consensus weights by crop quality, not just OCR confidence.** Recognition
engines are confidently wrong on blurry input. A read's vote is
`ocr_confidence × crop_quality`, where quality combines sharpness (variance of
Laplacian), plate width, exposure and contrast. One sharp read outvotes three
blurry ones.

**Confusion correction is position-aware.** `8`→`B` is right in a letter slot and
catastrophic in a digit slot. The corrector fits the string to an Indian plate
template first, then only repairs characters sitting in the wrong kind of slot.
A global find-and-replace would introduce as many errors as it fixes.

**Invalid plates are flagged, never dropped.** A string that matches no plate
format is either a misread (evidence about the model) or an unusual plate
(evidence about the road). Both are worth keeping.

**Plates nothing owns are still reported.** A plate read from a frame where the
object detector missed the vehicle entirely is a detector-recall failure, and it
appears in `plate_reads.csv` with a blank `track_id` rather than vanishing.

**Preprocessing variants compete.** Several conditioning recipes run per crop and
the winner is recorded, so the report can answer "does perspective correction
help our footage?" with counts rather than intuition.

---

## Licensing

The COCO and plate detection weights are Ultralytics exports under **AGPL-3.0**,
and the optional torch profile installs Ultralytics itself. This is a development
and evaluation tool: those artifacts are used here and do not ship in any
deployed NagarNetra container. That separation is the reason the production
`ai-worker` image is torch-free and carries only ONNX Runtime — see the main
repository's `CLAUDE.md` §9, deviation 3.

Before any of this reaches production, the plate detector must be retrained on a
permissively licensed or self-collected dataset.

---

## Status

Working end to end, with one caveat that matters.

* 103 unit tests, lint clean.
* On generated footage with known plates: **7/8 plates read exactly right, 0
  wrong, character error rate 0.000.**
* Four performance faults found by measurement and fixed — about 10x overall.
* **Track fragmentation is fixed.** One car reporting as three vehicles was
  traced to overlapping vehicle crops handing the same plate to several tracks;
  plate-ownership resolution and plate-identity track merging took precision
  from 0.71 to 0.83 and duplicate vehicles from 2 to 0.
* **A live-stream path exists and is measured**: median capture-to-event latency
  **385 ms** (p99 559 ms), holding steady while dropping 70% of frames — latency
  stays bounded rather than growing.

**The caveat:** none of the four bundled sample clips has a legible plate — they
are an aerial highway view, a bicycle POV and two pedestrian streets. So the
accuracy above is measured on synthetic plates that this repo generated, and it
is optimistic by construction. Real Gujarat CCTV with labelled plates is the
single most valuable thing that could be added next.

`ARCHITECTURE.md` describes the inference pipeline, the streaming path and how
it scales. `FINDINGS.md` has the accuracy numbers and what is still unknown.
`PERFORMANCE.md` has the profiling investigation: where the time went, the six
optimisations, and the before/after — **6.7x faster end to end, 21x for
inference alone, and more accurate rather than less.**
