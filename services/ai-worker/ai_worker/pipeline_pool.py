"""Loaded inference models, lent to cameras one at a time.

## The leak this closes

Cameras rotate through a fixed number of inference slots, so a three-slot
worker starts roughly 180 camera runs an hour. Each run used to construct its
own `Pipeline`, and a Pipeline loads and graph-optimises five ONNX models —
vehicle detector, plate detector, and RapidOCR's detection/classification/
recognition trio. Every one of those allocates a **native thread pool and
memory arena**, freed only when Python collects the owning object, which for
objects caught in reference cycles means whenever the cyclic collector next
runs rather than when the camera stopped.

Measured on this host, doing identical work throughout:

    after  4 min   29 threads   1.4 GB
    after 12 min   62 threads   3.5 GB

That is the road to the `exit 137` OOM kills this worker has already taken,
and it presents as the platform mysteriously degrading the longer it runs.

## Why a pool and not one shared instance

`Pipeline` is not thread-safe: it mutates per-track dictionaries as it runs and
drives its ONNX sessions from the calling thread, so two cameras sharing one
would interleave their track bookkeeping. Checking an instance **out**
guarantees a single user while still capping how many exist at the number of
slots that can run at once.

## Why this is its own module

`ai_worker.worker` imports the vision pipeline, so it needs OpenCV and
onnxruntime. The shared test image carries neither — and the worker image,
which does, carries no pytest — so a test reaching this class through
`worker.py` would run in *neither* image. Keeping the pool import-light, with
the Pipeline import deferred to first use, is what makes its behaviour
testable. `crops.py` is split for the same reason.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)


class Reusable(Protocol):
    """What the pool needs of a pipeline: the ability to forget its last run."""

    def reset_run_state(self) -> None: ...


def _default_factory(config: Any) -> Any:
    """Build a real Pipeline.

    Imported here rather than at module scope so this module stays importable
    without OpenCV or onnxruntime — see the note above about which image the
    tests run in.
    """
    from ailab.pipeline import Pipeline

    return Pipeline(config)


class PipelinePool:
    """Models loaded once per slot, reused for every camera that holds it."""

    def __init__(self, factory: Callable[[Any], Any] | None = None) -> None:
        self._factory = factory or _default_factory
        self._idle: dict[tuple[str, ...], list[Any]] = {}
        self._lock = threading.Lock()
        self.built = 0
        self.reused = 0

    @staticmethod
    def _key(config: Any) -> tuple[str, ...]:
        """Accepted plate regions — the only per-camera config difference.

        `AiWorker._config_for` deep-copies the config solely to override
        `consensus.plate_regions`; nothing that decides which models load
        varies across the fleet. Keying on it means a camera restricted to GJ
        plates never receives the pipeline of one that accepts any, because
        consensus reads that list.
        """
        return tuple(config.consensus.plate_regions)

    def acquire(self, config: Any) -> Any:
        """Borrow a pipeline. Built on first use per slot, reused after."""
        key = self._key(config)
        with self._lock:
            idle = self._idle.get(key)
            if idle:
                self.reused += 1
                pipeline = idle.pop()
                pipeline.reset_run_state()
                return pipeline
            self.built += 1

        # Built outside the lock: loading five models takes ~500 ms and must
        # not stall another camera thread trying to return one.
        log.info("loading models for an inference slot (regions=%s)", key or "any")
        return self._factory(config)

    def release(self, config: Any, pipeline: Any) -> None:
        """Give a pipeline back.

        Called from a `finally`, so it runs however the camera's run ended. A
        pipeline that is not returned is one the next camera has to rebuild,
        which is exactly the leak this exists to close.
        """
        with self._lock:
            self._idle.setdefault(self._key(config), []).append(pipeline)

    def stats(self) -> dict[str, int]:
        """`built` settling at the slot count is the health signal.

        If it keeps climbing, pipelines are not coming back and the worker is
        leaking a thread pool per rotation again.
        """
        with self._lock:
            return {
                "built": self.built,
                "reused": self.reused,
                "idle": sum(len(v) for v in self._idle.values()),
            }
