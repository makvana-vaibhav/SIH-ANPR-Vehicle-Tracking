"""How many CPU threads each model may use, given how many cameras share the box.

## The bug this module exists to fix

Every inference session in this pipeline sized its own thread pool as if it
owned the whole machine, and nothing divided that by the number of cameras
running concurrently. The worker runs one `Pipeline` per camera on its own
thread, and each pipeline builds five ONNX sessions — vehicle detector, plate
detector, and RapidOCR's detection/classification/recognition trio. The two
YOLO sessions were capped at four threads by `detect.onnx_backend`; the three
OCR sessions were not capped at all, so they took ONNX Runtime's default of one
thread per core.

On this 10-core host at three cameras that is:

    3 cameras x 3 OCR sessions x 10 threads  = 90
    3 cameras x 2 YOLO sessions x 4 threads  = 24
    OpenCV's own pool, per camera thread     = 10
                                             ----
                                              124+ threads on 10 cores

Measured: 168 OS threads in the worker process, 866% CPU, and a host load
average of 18.8 on a 10-core machine. The AI worker alone was consuming ~8.7 of
10 cores, which starves MediaMTX, the browser and the compositor — the symptom
being camera tiles that never load, then hang.

## Why oversubscription is worse than pointless here

It is not a fair trade of latency for throughput. Measured on this host,
RapidOCR recognition on one plate crop:

| threads                 | wall/call | CPU/call | cores used |
|-------------------------|-----------|----------|------------|
| `intra_op=2`            |   11.8 ms |  23.6 ms |       2.0x |
| `intra_op=4`            |    8.0 ms |  32.0 ms |       4.0x |
| ORT default (all cores) |   14.3 ms | 132.6 ms |       9.3x |

The library's default burns **5.6x the CPU of two threads to return a slower
answer**. These are small models: past a handful of threads the convolutions
spend longer synchronising than computing. So capping threads costs nothing and
buys back most of the machine. The same effect is documented for the detector
in `detect/onnx_backend.py` — 452 ms at ORT's default against 126 ms at four.

## The rule

A budget for the whole process, divided by the number of pipelines sharing it.

Within one pipeline the five sessions run **sequentially** on that camera's
thread — a frame is detected, then plates are found, then read — so they never
contend with each other and each may use the pipeline's whole allocation.
Contention is strictly *between* cameras, which is why the divisor is the
camera count and not the session count.
"""

from __future__ import annotations

import logging
import os
import threading

# Stdlib logging, not `ailab.logging`, and deliberately so: that module pulls
# in `rich`, which the shared test image does not carry. This module has to be
# importable wherever the thread budget is decided — the lab, the worker, and
# the test image that has neither OpenCV nor onnxruntime — so its imports stay
# to the standard library.
log = logging.getLogger(__name__)

#: Threads to leave for everything that is not inference: RTSP decode, the
#: media gateway, the event sink, and — on a demo laptop — the browser
#: rendering the map. Inference that consumes every core makes the product it
#: serves unusable, which is the failure this module was written for.
RESERVED_THREADS = 2

#: Per-model ceiling regardless of how much budget is free. Measured on this
#: host: YOLOv8n at 640px runs 275 ms at 1 thread, 126 ms at 4, 194 ms at 6 and
#: 331 ms at 8. Past four, more threads make it slower.
MAX_THREADS_PER_MODEL = 4

_lock = threading.Lock()
_concurrency = 1


def _cores() -> int:
    """Cores this process may actually run on.

    `sched_getaffinity` rather than `cpu_count` because a container pinned to a
    subset of CPUs still reports the host's total, and sizing a thread pool to
    cores we are not allowed to use is how oversubscription starts.
    """
    if hasattr(os, "sched_getaffinity"):
        return max(1, len(os.sched_getaffinity(0)))
    return max(1, os.cpu_count() or 1)


def total_budget() -> int:
    """Inference threads this process may use across all pipelines."""
    override = os.environ.get("AILAB_INFERENCE_BUDGET", "")
    if override.isdigit() and int(override) > 0:
        return int(override)
    return max(1, _cores() - RESERVED_THREADS)


def set_concurrency(pipelines: int) -> None:
    """Declare how many pipelines will run at once in this process.

    The worker calls this once it knows its camera slot count. Without it the
    budget is handed to a single pipeline, which is correct for the lab (one
    video at a time) and catastrophic for the worker (one camera per thread).
    """
    global _concurrency
    with _lock:
        _concurrency = max(1, pipelines)
    log.info(
        "inference budget: %d threads across %d pipeline(s) = %d per model (max %d)",
        total_budget(), _concurrency, threads_per_model(), MAX_THREADS_PER_MODEL,
    )


def concurrency() -> int:
    with _lock:
        return _concurrency


def threads_per_model(requested: int = 0) -> int:
    """Intra-op threads for one model.

    `requested > 0` is an explicit operator choice and is honoured as-is —
    someone sweeping thread counts in the lab means what they typed. Otherwise
    the budget is split evenly across concurrent pipelines and clamped to the
    measured per-model ceiling.
    """
    if requested > 0:
        return requested

    override = os.environ.get("AILAB_ORT_THREADS", "")
    if override.isdigit() and int(override) > 0:
        per_model = int(override)
    else:
        per_model = total_budget() // concurrency()

    return max(1, min(MAX_THREADS_PER_MODEL, per_model))


def opencv_threads() -> int:
    """How many threads OpenCV's own parallel_for may use.

    One, when several cameras run concurrently. OpenCV parallelises individual
    `resize`/`cvtColor`/`imencode` calls, but the worker already has real
    parallelism — one thread per camera — so an internal pool on top of that
    only multiplies the thread count against the same cores. It is a
    process-global setting, so with three camera threads a pool of 10 is up to
    30 runnable threads for pixel shuffling alone.

    A single pipeline (the lab reading one video) has no such outer
    parallelism, so there it keeps a small pool.
    """
    override = os.environ.get("AILAB_OPENCV_THREADS", "")
    if override.isdigit():
        return int(override)
    return 1 if concurrency() > 1 else min(4, _cores())


def apply_opencv_threads() -> int:
    """Push `opencv_threads()` into OpenCV. Returns what was set.

    Imported lazily: this module is imported by config-time code paths that
    must not pay for loading OpenCV.
    """
    import cv2

    threads = opencv_threads()
    cv2.setNumThreads(threads)
    return threads


def describe() -> dict[str, object]:
    """The resolved plan, for logs and run manifests."""
    return {
        "cores": _cores(),
        "total_budget": total_budget(),
        "pipelines": concurrency(),
        "threads_per_model": threads_per_model(),
        "opencv_threads": opencv_threads(),
        "reserved": RESERVED_THREADS,
    }
