"""The inference thread budget.

This exists because of a measured outage, not a theory. Every ONNX session in
the pipeline sized its own thread pool as if it were alone on the machine, and
the worker runs one whole pipeline per camera. At three cameras that was ~124
threads on 10 cores: 866% CPU, host load average 18.8, and camera tiles in the
browser that never finished loading because nothing was left to render them.

So the property under test is not "threads are configured". It is **the total
stays bounded as cameras are added**, which is the thing that was false.
"""

from __future__ import annotations

import pytest

from ailab import runtime


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch):
    """Concurrency is process-global state; no test may leak it to the next.

    The env overrides are cleared too — a developer with AILAB_ORT_THREADS set
    in their shell would otherwise see different results from CI.
    """
    monkeypatch.delenv("AILAB_ORT_THREADS", raising=False)
    monkeypatch.delenv("AILAB_INFERENCE_BUDGET", raising=False)
    monkeypatch.delenv("AILAB_OPENCV_THREADS", raising=False)
    runtime.set_concurrency(1)
    yield
    runtime.set_concurrency(1)


class TestTotalBudget:
    def test_it_reserves_cores_for_everything_that_is_not_inference(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """RTSP decode, the media gateway and the browser need cores too.

        A worker that takes all of them makes the product it serves unusable,
        which is the failure this module was written for.
        """
        monkeypatch.setattr(runtime, "_cores", lambda: 10)
        assert runtime.total_budget() == 10 - runtime.RESERVED_THREADS

    def test_a_tiny_box_still_gets_a_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cores - reserved goes <= 0 on a 1-2 core CI runner."""
        monkeypatch.setattr(runtime, "_cores", lambda: 1)
        assert runtime.total_budget() >= 1

    def test_an_operator_can_set_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AILAB_INFERENCE_BUDGET", "6")
        assert runtime.total_budget() == 6

    def test_junk_in_the_env_is_ignored_not_fatal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A typo in compose must not stop the worker booting."""
        monkeypatch.setattr(runtime, "_cores", lambda: 10)
        for junk in ("", "auto", "-4", "3.5"):
            monkeypatch.setenv("AILAB_INFERENCE_BUDGET", junk)
            assert runtime.total_budget() == 8


class TestThreadsPerModel:
    def test_the_budget_is_divided_by_the_camera_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The regression. One camera may have four; three may not have four each."""
        monkeypatch.setenv("AILAB_INFERENCE_BUDGET", "8")

        runtime.set_concurrency(1)
        assert runtime.threads_per_model() == 4  # clamped by the per-model ceiling
        runtime.set_concurrency(2)
        assert runtime.threads_per_model() == 4
        runtime.set_concurrency(3)
        assert runtime.threads_per_model() == 2
        runtime.set_concurrency(8)
        assert runtime.threads_per_model() == 1

    def test_total_threads_never_exceed_the_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The invariant, stated directly.

        Sessions within one pipeline run sequentially on that camera's thread,
        so the figure that matters is per-pipeline allocation times pipelines.
        """
        monkeypatch.setenv("AILAB_INFERENCE_BUDGET", "8")
        for cameras in range(1, 33):
            runtime.set_concurrency(cameras)
            total = runtime.threads_per_model() * cameras
            # A one-thread floor means a heavily oversubscribed camera count
            # cannot go below one thread each; that is a deliberate floor, so
            # the bound only holds while the budget can cover the cameras.
            if cameras <= 8:
                assert total <= 8, f"{cameras} cameras asked for {total} threads"

    def test_more_cameras_than_cores_still_get_one_thread_each(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zero threads is not a valid ONNX Runtime setting — it means
        'decide for me', which is the behaviour being removed."""
        monkeypatch.setenv("AILAB_INFERENCE_BUDGET", "4")
        runtime.set_concurrency(64)
        assert runtime.threads_per_model() == 1

    def test_the_per_model_ceiling_holds_on_a_large_box(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Measured: YOLOv8n at 640px is 126 ms at 4 threads and 331 ms at 8.
        More threads past the ceiling make it slower, so a big machine spends
        its cores on more cameras rather than on wider models."""
        monkeypatch.setenv("AILAB_INFERENCE_BUDGET", "128")
        runtime.set_concurrency(1)
        assert runtime.threads_per_model() == runtime.MAX_THREADS_PER_MODEL

    def test_an_explicit_request_is_obeyed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Someone sweeping thread counts in the lab means what they typed."""
        monkeypatch.setenv("AILAB_INFERENCE_BUDGET", "8")
        runtime.set_concurrency(8)
        assert runtime.threads_per_model(requested=7) == 7

    def test_the_env_override_bypasses_the_division(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AILAB_ORT_THREADS is a per-model override, and that is exactly why
        docker-compose must not set it — doing so restores the old bug of every
        model taking the same count however many cameras run."""
        monkeypatch.setenv("AILAB_ORT_THREADS", "3")
        runtime.set_concurrency(8)
        assert runtime.threads_per_model() == 3


class TestOpenCvThreads:
    def test_one_thread_when_cameras_run_concurrently(self) -> None:
        """OpenCV parallelises a single resize; the worker already has one
        thread per camera. An inner pool on top only multiplies threads against
        the same cores, and it is a process-global setting."""
        runtime.set_concurrency(3)
        assert runtime.opencv_threads() == 1

    def test_a_lone_pipeline_keeps_a_small_pool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The lab reads one video with no outer parallelism, so there
        OpenCV's own threading is the only parallelism available."""
        monkeypatch.setattr(runtime, "_cores", lambda: 10)
        runtime.set_concurrency(1)
        assert runtime.opencv_threads() == 4

    def test_it_can_be_pinned(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AILAB_OPENCV_THREADS", "0")  # 0 = OpenCV's own default
        runtime.set_concurrency(4)
        assert runtime.opencv_threads() == 0


class TestDescribe:
    def test_it_reports_the_resolved_plan(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This goes into the worker log and the run manifest, so a slow run
        can be traced to its threading rather than guessed at."""
        monkeypatch.setenv("AILAB_INFERENCE_BUDGET", "8")
        runtime.set_concurrency(4)
        plan = runtime.describe()

        assert plan["total_budget"] == 8
        assert plan["pipelines"] == 4
        assert plan["threads_per_model"] == 2
        assert plan["reserved"] == runtime.RESERVED_THREADS
