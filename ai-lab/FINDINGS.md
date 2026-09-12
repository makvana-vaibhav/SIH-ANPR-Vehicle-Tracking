# Findings — first runs

What the lab actually measured, including where it fell short. Every number here
came out of a run in `runs/`; nothing is estimated.

Host: Docker Desktop on macOS arm64 (Apple Silicon), 10 vCPU, CPU-only.
There is no CUDA on this machine, so no GPU figure appears anywhere below.

---

## 1. The bundled sample footage cannot test ANPR

The four clips in `data/videos/` were fetched for the platform's video-wall
demo, not for plate recognition, and none of them has a legible plate:

| clip | what it actually is | usable for ANPR |
|---|---|---|
| `highway_congestion.mp4` | aerial view from a tower; vehicles a few pixels wide | no |
| `junction_dashcam.mp4` | bicycle POV, Poland | no |
| `road_cctv.mp4` | street camera, pedestrians and parked cars, plates not facing camera | no |
| `street_crossing.mp4` | pedestrian crossing | no |

A full run over `road_cctv.mp4` found **6 vehicles and 0 plates**. That is the
correct result for that footage — there are no readable plates in it — but it
means the footage can tell us nothing about OCR.

**Consequence:** the accuracy figures below come from generated footage, and
real Gujarat CCTV is the single most valuable thing that could be added next.

---

## 2. Generated footage, so the pipeline could be measured at all

`scripts/make_test_footage.py` cuts real vehicle crops out of real footage using
the same YOLO detector the pipeline uses, composites a synthetic Gujarat plate
onto each with perspective, blur and exposure matched to the crop, and animates
them across a real road background. Plates are known, so accuracy is measurable.

**This measures the pipeline's mechanics, not real-world accuracy.** The plates
are rendered from a clean font: no embossing, no dirt, no motion blur from a real
shutter, no regional font variation. Numbers from it are optimistic and must not
be quoted as system accuracy.

---

## 3. Measured accuracy on generated footage

Run: `2026-08-25T18-45-36__synthetic_traffic__default`, 240 frames, 8 plates.

| metric | value |
|---|---|
| exact plate match, of plates attempted | **7 / 7 = 100%** |
| end-to-end, of plates present | **7 / 8 = 87.5%** |
| character error rate | **0.000** |
| wrong | 0 |
| missed | 1 |
| spurious | 3 |
| precision / recall | 0.70 / 0.875 |

Every plate the pipeline committed to was exactly right. That is the good news
and it is also the least interesting number here, because of what follows.

### The real weakness was track fragmentation, not OCR — now merged, not yet re-measured

8 real vehicles produced **18 vehicle tracks** and 10 resolved plates. One car
(`GJ30V7380`) came back as **three separate tracks**, and another appeared twice
— once correctly as `GJ12HH8771` and once as `GJ12H8771` with a dropped
character. Those duplicates are the entire reason precision is 0.70 rather than
1.00.

For this platform that matters more than a character error would. Cross-camera
route reconstruction joins sightings by plate and time; one vehicle arriving as
three tracks at one camera becomes three route hops that never happened.

> **Status correction, 11 Sep 2026.** This section's own "next step" language
> below used to say this was unfixed. It is not: `ailab/track/merge.py` exists,
> is wired into both the batch pipeline (`pipeline.py`) and the live worker
> (`stream/runner.py`), and does exactly what this finding calls for — fragments
> that resolve the *same plate* (allowing the small edit distance the
> `GJ12HH8771`/`GJ12H8771` case above needed) and are compatible in time are
> merged into one `Vehicle`, with consensus recomputed over the pooled reads.
> `fragmentation_stats()` reports the before/after ratio on every run
> (`tracks_per_vehicle`, 1.0 = perfect). It has its own test suite
> (`tests/test_merge.py`), including the exact "two invalid-grammar plates must
> not merge on their own" and "an invalid fragment that started first still
> joins the cluster it belongs to" cases this finding's numbers motivated.
>
> **What is still true: the fix has not been re-measured.** The 18-tracks /
> 8-vehicles numbers above predate `merge()` being wired in — there is no run in
> `runs/` (gitignored, and none exists in this checkout) showing the *after*
> `tracks_per_vehicle` figure. Whoever runs the lab next should re-run this same
> 240-frame clip and record the merged number here, replacing this note with a
> real measurement rather than a claim about the code.

It is a tracker problem, not an OCR problem, and the fix accordingly lives in
tracking, not recognition. The synthetic sprites jump and rescale more abruptly
than real vehicles do, so some of the original fragmentation was an artefact of
the generator — which is exactly why real footage is still needed before tuning
either the tracker or the merge thresholds any further.

### Calibration

| confidence | n | claimed | measured | gap |
|---|---|---|---|---|
| 0.8-0.9 | 2 | 86% | 100% | −14pp |
| 0.9-1.0 | 5 | 99% | 100% | −1pp |

Slightly *under*-confident, which is the safe direction. With 7 samples this is
far too little evidence to act on; it is reported to show the mechanism works,
not to make a claim.

---

> **Superseded in part.** The performance section below was measured on 720p
> footage. A later investigation on 4K footage with ~20 vehicles per frame found
> a different bottleneck ranking and a further 6.7x — see `PERFORMANCE.md`.

## 4. Four performance faults found and fixed

The first working configuration ran at roughly **0.1 fps**. It now runs at
**0.92 fps** on the same footage — about 10x — after four fixes, each found by
measurement rather than inspection.

| # | fault | measured effect |
|---|---|---|
| 1 | ONNX Runtime's default thread count | YOLOv8n at 640px: 452 ms with the library default, **126 ms at 4 threads**. Its default of one thread per core is the *worst* setting available on this host — 3.6x slower than the best. |
| 2 | Plate crops letterboxed to 640px | A 90x70 vehicle crop was being inflated fiftyfold. Sizing the input to the crop: **302 ms → 72 ms**, 4.2x. |
| 3 | OCR ran text detection before recognition | PP-OCR detection costs **1334 ms** against **74 ms** for recognition alone — **18x** — and returns the identical string. The plate detector has already located the plate; asking a text detector to find it again is pure waste. Recognition now runs first, detection is the fallback. |
| 4 | The fallback fired on unreadable crops | Distant plates fail recognition, so every one of them was paying the 1334 ms detection path where it had nothing to offer. Now gated on crop geometry. |

Fix 1 is worth stating plainly because it is invisible: left alone, ORT's
default silently dominated every timing the lab reported, and no amount of model
selection would have recovered it.

### Where the time goes now

| stage | mean/frame | share |
|---|---|---|
| plate detection | 637 ms | 60% |
| object detection | 239 ms | 22% |
| OCR | 175 ms | 15% |
| everything else | ~65 ms | 3% |

Plate detection dominates because it runs on every tracked vehicle every frame —
841 plate detections over 240 frames. `plate.every_n_frames: 2` roughly halves it
and is the obvious next lever.

**0.92 fps is not production throughput** and is not presented as such. It is
one CPU process on a laptop with no GPU, running every diagnostic the lab
produces. The platform's architecture puts inference at the edge for exactly
this reason.

---

## 5. A refusal that improved accuracy

Adding a minimum crop width before OCR (`ocr.min_crop_width: 52`) was intended
purely as a speed fix. It also removed a class of error.

Before, a single vehicle produced these reads as it approached:

```
6Y7458      J36Y7458    GJ36Y7458   GJ3617458   GJ36Y7458   GJ38Y7458
```

Four correct, two wrong — and note `GJ38Y7458` carried **0.98 confidence** while
being wrong. Consensus outvoted the errors and got `GJ36Y7458` right, which is
the mechanism working as designed. But the errors all came from crops too small
to read. Declining to OCR them removed the noise at the source: the same vehicle
now yields six reads, all correct.

The lesson generalises. A recogniser given an unreadable crop does not return
"I can't read this" — it returns a confident wrong answer. Refusing to ask is
better than voting down the reply.

---

## 6. Preprocessing: perspective correction is not earning its place

Across 95 reads:

| variant | times it won | mean confidence |
|---|---|---|
| `rectified_fallback` | 65 | 0.942 |
| `upscaled` | 21 | 0.970 |
| `rectified` | 9 | 0.950 |

True four-point rectification applied to only **9 of 95** crops — in the other 65
the plate border was not clean enough to trust, so the code correctly fell back
rather than warping to a guess. And plain `upscaled` scored *highest* of the
three.

On this footage, perspective correction costs work and returns nothing. That is
a finding, not a verdict: these are synthetic plates pasted with mild skew, and
real off-axis roadside cameras are the case rectification exists for. Re-measure
on real footage before removing it — `configs/experiments/` is set up for exactly
that comparison.

---

## 7. What is not yet known

* **Real-world accuracy.** Unmeasured. Everything above is generated footage.
* **Real Indian plates.** Never tested — no embossing, no two-row motorcycle
  plates, no dirt, damage or regional fonts have been through this.
* **The plate detector's licence.** The current weights are an Ultralytics
  AGPL-3.0 export. Fine for evaluation; must be replaced before production.
* **GPU throughput.** Cannot be measured on this machine and is not estimated.
* **Two-row plates.** The detection fallback exists for them and is untested
  against a real one.
* **EasyOCR and Ultralytics comparisons.** The configs exist; the torch profile
  has not been built, so those runs have not happened.

---

## 8. Next steps, in order of value

1. **Get real Gujarat CCTV footage with labelled plates.** Nothing else on this
   list produces a trustworthy number without it. Even 50 labelled plates would
   change what can be claimed.
2. **Re-measure track fragmentation with `merge()` applied.** The fix (§3
   above) is built and wired into both pipelines; it has not been re-run
   against this or any clip to confirm `tracks_per_vehicle` actually moved
   toward 1.0. Do this before claiming the fragmentation problem is closed.
3. **Re-measure preprocessing and the plate detector on real footage**, using
   the ablation configs, before tuning anything against synthetic results.
4. **Replace the plate detector weights** with a permissively licensed or
   self-trained model.
5. **Then** consider integration into the platform.
