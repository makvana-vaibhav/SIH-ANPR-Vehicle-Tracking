# Worker performance and the CPU budget

How the AI worker spends CPU, why it used to spend far too much of it, and
which knobs change that.

For per-stage pipeline profiling — which model costs what on one video — see
[ai-lab/PERFORMANCE.md](../ai-lab/PERFORMANCE.md). This document is about the
**worker**: many cameras sharing one machine.

**Host for every measurement below:** Docker Desktop on macOS arm64 (Apple
Silicon), 10 vCPU, 11.67 GB to the VM, CPU-only (see [GPU.md](GPU.md)).

---

## 1. The symptom

Camera tiles would not load, and when they did the UI hung. The cause was not
the UI:

```
nagarnetra-ai-worker    866.28%   3.237GiB / 11.67GiB
load averages: 18.81 14.16 10.40      # on a 10-core machine
```

The AI worker alone was taking ~8.7 of 10 cores, so MediaMTX, the browser and
the macOS compositor were competing for what was left. A WebRTC handshake that
cannot get scheduled looks exactly like a camera that will not load.

## 2. The cause: nothing divided by the number of cameras

The worker runs **one whole `Pipeline` per camera, on its own thread**, and
each pipeline builds five ONNX sessions — vehicle detector, plate detector,
and RapidOCR's detection/classification/recognition trio.

Every one of those sessions sized its thread pool as if it owned the machine.
The two YOLO sessions were capped at four threads by `detect/onnx_backend.py`;
**the three OCR sessions were not capped at all**, because
`ailab/ocr/rapid.py` constructed `RapidOCR()` with no arguments and the wheel's
shipped config leaves `intra_op_num_threads: -1` — ONNX Runtime's "one thread
per core". `ailab/ocr/crnn_onnx.py` built its session with no `SessionOptions`
whatsoever.

On this host at three cameras:

| source | threads |
|---|---|
| 3 cameras x 3 OCR sessions x 10 | 90 |
| 3 cameras x 2 YOLO sessions x 4 | 24 |
| OpenCV's own pool (`cv2.getNumThreads()` was 10) | 10 |
| decode, upload, supervisor | ~10 |
| **measured total in the process** | **168** |

Two further gaps fed it. `OMP_NUM_THREADS` was set in `ai-lab/Dockerfile` but
**not** in `services/ai-worker/Dockerfile`, so the setting the lab measured
with was absent in the image that ships. And `cv2.setNumThreads` was never
called anywhere.

## 3. Why oversubscription was not even a trade

It is tempting to read 168 threads as "using the machine hard". Measured on one
plate crop, it was the opposite:

| RapidOCR recognition | wall/call | CPU/call | cores used |
|---|---|---|---|
| `intra_op=2` | 11.8 ms | 23.6 ms | 2.0x |
| `intra_op=4` | 8.0 ms | 32.0 ms | 4.0x |
| **ORT default (all cores)** | **14.3 ms** | **132.6 ms** | **9.3x** |

The default burned **5.6x the CPU of two threads to return a slower answer.**
These models are small; past a handful of threads the convolutions spend longer
synchronising than computing. The same shape was already documented for the
detector: YOLOv8n at 640px is 126 ms at 4 threads, 331 ms at 8, and 452 ms at
ORT's default.

## 4. The fix: one budget, divided

[`ai-lab/ailab/runtime.py`](../ai-lab/ailab/runtime.py) owns a single
process-wide thread budget and divides it across the pipelines that share the
process. The worker declares its camera count in `AiWorker.__init__`, before
anything builds a session — a session's thread pool is fixed at construction
and cannot be resized later.

Sessions **within** one pipeline run sequentially on that camera's thread — a
frame is detected, then plates are found, then read — so they never contend
with each other and each may use the whole per-pipeline allocation. Contention
is strictly *between* cameras, which is why the divisor is the camera count and
not the session count.

```
budget = AILAB_INFERENCE_BUDGET or (cores - 2)
per model = clamp(budget / cameras, 1, 4)
```

The two reserved cores are for RTSP decode, the media gateway and the browser
rendering the map. Inference that consumes every core makes the product it
serves unusable, which was the original bug.

## 5. The second fix: rotation was leaking a thread pool per camera

Cameras rotate through a fixed number of slots, so a three-slot worker starts
roughly 180 camera runs an hour. Each run constructed its **own** `Pipeline`,
and every ONNX session in it allocates a native thread pool and memory arena
that are released only when Python collects the owning object — which, for
objects in reference cycles, is whenever the cyclic collector next runs, not
when the camera stopped.

Measured, doing identical work throughout:

```
after  4 min   29 threads   1.4 GB
after 12 min   62 threads   3.5 GB
```

That is the path to the `exit 137` OOM kills this worker has taken before, and
it presents as the platform mysteriously degrading the longer it runs.

[`services/ai-worker/ai_worker/pipeline_pool.py`](../services/ai-worker/ai_worker/pipeline_pool.py)
loads models once per slot and lends them to whichever camera holds it. Not one
shared instance — `Pipeline` mutates per-track state and is not thread-safe —
but a pool that guarantees a single user while capping how many exist.

The health signal is in the worker's periodic log line:

```
watching 3/51 camera(s): ... · models built 3, reused 18
```

**`built` must settle at the slot count and stop rising.** If it keeps
climbing, pipelines are not being returned and the leak is back.

## 6. The third fix: the fleet was publishing 4K60

Two seed clips are **3840x2160 at 24 Mbps** — `anpr_sample.mp4` at 60 fps and
`test1.mp4` at 30 — and roughly a quarter of the 51 cameras is assigned one.
Published verbatim through copy-mode, that means:

* a browser tile decoding **4K60 at 24 Mbps**, which is enough on its own to
  hang a tile on a machine sharing 10 cores;
* the AI worker decoding every 4K frame **only to letterbox it to the
  detector's fixed 640px input** — the extra pixels are discarded before
  inference sees them.

It is also unrepresentative. City ANPR cameras are 1080p at 12–15 fps; a 4K60
feed is not what a municipal deployment looks like, and
`scripts/capacity_model.py` sizes against the realistic figure.

`prepare_clip` already re-encodes each clip **once to disk** to fix keyframe
sparsity, so capping there costs nothing per stream. `SIM_MAX_HEIGHT` (1080)
and `SIM_MAX_FPS` (15) are applied in that pass, and the cap is part of the
cache filename so changing it invalidates prepared clips rather than silently
serving ones built to the old ceiling.

**The rule is never up.** A 720p15 clip is left byte-identical: upscaling
invents detail the sensor never captured, and resampling 15 fps to 15 fps is
loss for nothing. Only the two 4K clips are touched.

```
anpr_sample.mp4   126 MB → 35 MB     2160p60 → 1080p15
test1.mp4         129 MB → 30 MB     2160p30 → 1080p15
```

Measured at 4 slots, before and after, everything else identical:

| | 4K sources | capped to 1080p15 |
|---|---|---|
| detections/min | 124 | **202** |
| distinct cameras / window | 15 | 20 |
| worker memory | 4.1 GB | **3.3 GB** |
| worker CPU | 647% | 656% |
| **plate yield** | **34.1%** | **33.9%** |

Detections rose **63%** because decode had been consuming the time inference
needed. **Plate yield is unchanged** — 34.1% against 33.9% — which is the
number that had to hold, since downscaling is the one change here that could
have cost legibility. It did not: the detector letterboxes to 640 regardless,
and 1080p still leaves a plate crop far above the recogniser's 52px floor.

> **If plate accuracy is ever measured against real footage (P7), re-check
> this.** A cap that is free at 1080p would not be free at 720p, and the
> harness is where that question gets answered rather than guessed.

## 7. Result

Same fleet, same footage, same models.

All three fixes together, against the original state:

| | before | after |
|---|---|---|
| OS threads in the worker | 168 | 79, stable |
| worker CPU | 866% (1 sample) | ~656% (38-sample mean) |
| memory | 3.2 GB, climbing | 3.3 GB, flat |
| **detections/min** | **79** | **202** |
| inference slots | 3 | 4 |
| models loaded per hour | ~180 | 4 |
| published stream (4K cameras) | 2160p60, 24 Mbps | 1080p15 |

**Detections per minute went up 2.6x while CPU came down**, because the
machine had been losing most of its work to context switching and to decoding
pixels that were discarded before inference.

Two honest caveats on those figures:

* The 866% "before" is a single `docker stats` sample; the "after" is a
  38-sample mean. Worker CPU swings between roughly 230% and 770% as cameras
  rotate, so any single reading of either state is unreliable. The thread
  count, the memory trend and the detections/min are the solid comparisons.
* Plate **yield** moved 38% → 34% across the whole exercise, but that is the
  slot count changing which cameras are in rotation, not a regression: the
  controlled before/after on the resolution cap alone was 34.1% → 33.9%.

## 7. Knobs

All optional; every one has a measured default.

| Variable | Default | Effect |
|---|---|---|
| `AILAB_INFERENCE_BUDGET` | `cores - 2` | Total intra-op threads across all cameras. **Lower it to make the UI more responsive at the cost of detections.** |
| `AI_WORKER_MAX_CAMERAS` | 3 | Concurrent inference slots. More cameras split the same budget rather than adding to it. |
| `AI_WORKER_SLICE_SECONDS` | 45 | How long a camera holds a slot before yielding. Rotation is what covers 51 cameras with 3 slots. |
| `AILAB_OPENCV_THREADS` | 1 when >1 camera | OpenCV's internal pool. The worker already has one thread per camera. |
| `AILAB_ORT_THREADS` | unset | **Per-model override that bypasses the division.** For sweeping thread counts by hand. Setting it in compose restores the original bug — don't. |
| `SIM_MAX_HEIGHT` | 1080 | Ceiling on published stream height. Applied in the simulator's one-time prepare pass; never upscales. |
| `SIM_MAX_FPS` | 15 | Ceiling on published frame rate. Never speeds a slower source up. |

### If the UI is still not responsive

Lower the budget first; it is the one that trades directly against
responsiveness:

```bash
AILAB_INFERENCE_BUDGET=4 docker compose --profile ai up -d ai-worker
```

Raising `AI_WORKER_MAX_CAMERAS` does **not** raise CPU — cameras split a fixed
budget — but it does raise **memory**, and steeply. Measured, same fleet:

| slots | threads/model | worker CPU | worker memory | detections/min | distinct cameras / 5 min |
|---|---|---|---|---|---|
| 3 | 2 | 574% | 2.7 GB | 150 | 10 |
| **4** (default) | 2 | 647% | **4.1 GB** | 124 | 15 |
| 6 | 1 | 589% | **6.8 GB** | 133 | 27 |

**CPU is flat across all three** — 574/647/589% is within the noise of a
laptop also running an editor and a browser. That is the budget doing its job:
cameras divide a fixed allocation instead of each claiming the machine.
Detections/min is flat for the same reason.

What scales is **memory**, at ~1.4 GB per slot, because each slot holds its own
copy of five models plus their arenas. So **memory, not CPU, caps the slot
count on this laptop**: six slots put the worker at 6.8 GB against a VM ceiling
of 11.67 GB with ~2.7 GB already spent on the rest of the stack. That is the
`exit 137` zone. Raise Docker Desktop's memory allocation before going past
four.

The thing slots actually buy is **coverage**: 10 → 15 → 27 distinct cameras
seen in five minutes, at no CPU cost. That matters for PS demo step 3, which
needs the same vehicle on more than one camera before it can link anything.
