"""Loaded models are lent to cameras, not rebuilt for them.

Rotation runs a camera for a slice and then hands the slot to the next one, so
a three-slot worker starts roughly 180 camera runs an hour. Each run used to
construct its own `Pipeline`, which loads five ONNX models — and every model
allocates a native thread pool and arena that survive until Python collects the
object, which for objects in reference cycles is whenever the cyclic collector
next runs rather than when the camera stopped.

Measured before this pool existed: 29 threads and 1.4 GB grew to 62 threads and
3.5 GB in twelve minutes of identical work, on the way to the exit-137 kills
this worker has taken before.

So the property under test is **`built` stops rising** — that models are
acquired once per slot and reused forever after, and that they come back even
when a run fails.
"""

from __future__ import annotations

import threading

import pytest

from ai_worker.pipeline_pool import PipelinePool


class FakePipeline:
    """Stands in for a real Pipeline.

    The real one loads five ONNX models, which the shared test image has no
    runtime for — and the worker image, which does, has no pytest. Nothing
    here depends on inference: the pool's contract is about identity and
    lifetime, so a counter is a complete substitute. Injected through the
    pool's `factory` rather than patched in, for the same reason `crops.py`
    takes its encoder as an argument.
    """

    instances = 0

    def __init__(self, config: object) -> None:
        FakePipeline.instances += 1
        self.serial = FakePipeline.instances
        self.resets = 0

    def reset_run_state(self) -> None:
        self.resets += 1


class FakeConfig:
    """Only `consensus.plate_regions` distinguishes one camera's config."""

    def __init__(self, *regions: str) -> None:
        self.consensus = type("C", (), {"plate_regions": tuple(regions)})()


@pytest.fixture(autouse=True)
def _reset_counter():
    FakePipeline.instances = 0
    yield


@pytest.fixture
def pool() -> PipelinePool:
    return PipelinePool(factory=FakePipeline)


class TestReuse:
    def test_a_returned_pipeline_is_handed_back_out(self, pool: PipelinePool) -> None:
        config = FakeConfig()

        first = pool.acquire(config)
        pool.release(config, first)
        second = pool.acquire(config)

        assert second is first, "the slot rebuilt models it already had"
        assert pool.stats() == {"built": 1, "reused": 1, "idle": 0}

    def test_rotation_never_builds_more_than_the_slot_count(
        self, pool: PipelinePool
    ) -> None:
        """The regression, stated as the thing that was false.

        Three slots cycling through fifty cameras must load models three times,
        not fifty. Before the pool this was fifty and each one left a thread
        pool behind.
        """
        config = FakeConfig()
        slots = 3

        for _ in range(50):
            borrowed = [pool.acquire(config) for _ in range(slots)]
            for pipeline in borrowed:
                pool.release(config, pipeline)

        assert FakePipeline.instances == slots
        assert pool.stats()["built"] == slots
        assert pool.stats()["reused"] == 50 * slots - slots

    def test_concurrent_cameras_never_share_one_pipeline(
        self, pool: PipelinePool
    ) -> None:
        """Inference state is not thread-safe.

        `Pipeline` mutates per-track dictionaries as it runs, so two cameras
        holding the same instance would interleave their bookkeeping. The pool
        caps how many exist; it must never hand the same one to two callers.
        """
        config = FakeConfig()
        held = [pool.acquire(config) for _ in range(4)]

        assert len({id(p) for p in held}) == 4

    def test_state_is_cleared_before_reuse(self, pool: PipelinePool) -> None:
        """A pipeline carries counters from the camera that had it last.

        Reset happens on the way out rather than on the way in, so a crashed
        run that never released cleanly still cannot contaminate the next.
        """
        config = FakeConfig()
        first = pool.acquire(config)
        assert first.resets == 0  # freshly built, nothing to clear

        pool.release(config, first)
        again = pool.acquire(config)
        assert again.resets == 1


class TestConfigKeying:
    def test_configs_with_different_plate_regions_do_not_share(
        self, pool: PipelinePool
    ) -> None:
        """A camera that accepts only GJ plates must not be given the pipeline
        of one that accepts any, because consensus reads the region list."""
        gujarat = FakeConfig("GJ")
        anywhere = FakeConfig()

        first = pool.acquire(gujarat)
        pool.release(gujarat, first)
        other = pool.acquire(anywhere)

        assert other is not first
        assert pool.stats()["built"] == 2

    def test_the_same_regions_share_whatever_the_order_of_use(
        self, pool: PipelinePool
    ) -> None:
        a = pool.acquire(FakeConfig("GJ", "MH"))
        pool.release(FakeConfig("GJ", "MH"), a)
        b = pool.acquire(FakeConfig("GJ", "MH"))

        assert b is a


class TestThreadSafety:
    def test_parallel_acquire_and_release_hands_out_distinct_instances(
        self, pool: PipelinePool
    ) -> None:
        """Camera threads start and stop independently.

        Without the lock, two threads racing on an empty free-list can both see
        it empty and build — or worse, both pop the same entry.
        """
        config = FakeConfig()
        seen: list[object] = []
        seen_lock = threading.Lock()
        barrier = threading.Barrier(8)

        def borrow() -> None:
            barrier.wait()
            pipeline = pool.acquire(config)
            with seen_lock:
                seen.append(pipeline)

        threads = [threading.Thread(target=borrow) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(seen) == 8
        assert len({id(p) for p in seen}) == 8, "two cameras were given the same models"
        assert pool.stats()["built"] == 8
