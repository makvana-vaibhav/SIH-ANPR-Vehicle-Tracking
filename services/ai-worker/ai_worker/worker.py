"""The worker: one inference loop per camera, supervised.

Each camera gets its own thread running the lab's StreamRunner, which reads
RTSP, tracks vehicles, reads plates and emits events. The supervisor's job is
narrow and unglamorous: keep the right set of cameras running, restart the ones
that die, and stop cleanly.

Restarting matters more than it sounds. A CCTV stream drops — the encoder
reboots, the network blips, a technician unplugs something — and a worker that
treats that as fatal takes a camera offline until someone notices. The reader
already reconnects within a session; this handles the case where the session
itself ends.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ailab import runtime
from ailab.config import RunConfig
from ailab.stream import SourceIdentity, StreamRunner, StreamUnavailable
from ailab.stream.reader import redact

from ai_worker.config import WorkerSettings
from ai_worker.crops import MinioCropStore
from ai_worker.discovery import (
    CameraStream,
    discover,
    discover_registry,
    discover_sandbox,
)
from ai_worker.pipeline_pool import PipelinePool
from ai_worker.rotation import Rotation
from ai_worker.publisher import RedisEventSink

log = logging.getLogger(__name__)

#: What each diagnosis means for whoever is reading the log. The point of the
#: table is that these have different owners: a network team, an integration
#: contact, and us.
REASON_ADVICE = {
    "unauthorized": "the far end rejected our credentials",
    "not found": "the far end has no such camera id",
    "timeout": "the far end accepted the connection then went quiet",
    "opened but no media": "answers, but sends no decodable video",
}


@dataclass
class CameraTask:
    """One camera being processed."""

    stream: CameraStream
    thread: threading.Thread
    stop: threading.Event
    started_at: float = field(default_factory=time.perf_counter)
    restarts: int = 0
    last_report: dict[str, Any] = field(default_factory=dict)
    #: True when the loop ended because its time slice ran out rather than
    #: because the stream failed. The supervisor treats the two differently:
    #: one is normal rotation, the other is worth a warning and a backoff.
    completed_slice: bool = False
    #: True when the stream could not be opened at all. Distinguishes "the far
    #: end is down" from "our inference loop broke", which are different
    #: problems with different owners.
    unreachable: bool = False
    #: Why, from `ailab.stream.reader.diagnose`.
    reason: str = ""

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()


class AiWorker:
    """Supervises per-camera inference loops."""

    def __init__(self, settings: WorkerSettings, config: RunConfig) -> None:
        self.settings = settings
        self.config = config
        # Declare the thread budget BEFORE anything builds an inference
        # session, because a session's thread pool is fixed at construction and
        # cannot be resized afterwards.
        #
        # Each camera runs a whole pipeline on its own thread, and every one of
        # its five ONNX sessions used to size its pool as though it were alone
        # on the machine. Left undeclared that is ~124 threads on 10 cores at
        # three cameras, which measured 866% CPU and a host load average of
        # 18.8 — the AI worker starving the media gateway and the browser it
        # exists to serve. See `ailab.runtime`.
        runtime.set_concurrency(settings.ai_worker_max_cameras)
        opencv_threads = runtime.apply_opencv_threads()
        log.info(
            "inference threading: %s (opencv=%d)", runtime.describe(), opencv_threads
        )
        self.sink = RedisEventSink(
            settings.redis_url, settings.event_stream_key, settings.event_stream_maxlen
        )
        self.tasks: dict[str, CameraTask] = {}
        self.rotation = Rotation(
            slots=settings.ai_worker_max_cameras,
            slice_seconds=settings.ai_worker_slice_seconds,
            pinned=frozenset(
                c.strip() for c in settings.ai_worker_pinned.split(",") if c.strip()
            ),
        )
        # Evidence crops go straight from here to object storage; only the
        # object key rides the event bus. Created once and shared by every
        # camera thread, because it owns a bounded upload queue and its whole
        # purpose is to keep that bounded.
        self._crops = MinioCropStore()
        # Models outlive the cameras that borrow them; see PipelinePool.
        self._pipelines = PipelinePool()
        self._transport: str | None = None
        # Whether RTSP works to a given host, probed once. Which transport a
        # federated grid accepts is a property of this worker's network.
        self._rtsp_probe: dict[str, bool] = {}
        self._shutdown = asyncio.Event()

    # ─────────────────────────────────────────────────────────────────
    async def run(self) -> None:
        log.info(
            "ai-worker %d/%d starting (config=%s, max %d cameras)",
            self.settings.ai_worker_index,
            self.settings.ai_worker_count,
            self.config.name,
            self.settings.ai_worker_max_cameras,
        )
        try:
            while not self._shutdown.is_set():
                await self._reconcile()
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(),
                        timeout=self.settings.discovery_interval_s,
                    )
                except TimeoutError:
                    continue
        finally:
            await self._stop_all()
            # Drain queued crops before the sink closes, so a clean shutdown
            # does not throw away evidence that was already encoded.
            self._crops.close()
            self.sink.close()

    async def _discover(self) -> list[CameraStream]:
        """Every camera this worker owns — not just the ones it can run now."""
        explicit = frozenset(
            c.strip() for c in self.settings.ai_worker_cameras.split(",") if c.strip()
        )

        if self.settings.ai_worker_source == "registry":
            # An explicit list narrows the roster rather than replacing it, so
            # the camera keeps its resolved URL and its plate regions. See
            # discover_registry for what went wrong when it replaced it.
            return await discover_registry(
                self.settings.redis_url,
                self.settings.ai_worker_index,
                self.settings.ai_worker_count,
                rtsp_probe=self._rtsp_probe,
                only=explicit,
            )

        if explicit:
            # No roster to narrow: the codes are all there is.
            return await discover(
                self.settings.mediamtx_api_url,
                self.settings.mediamtx_host,
                self.settings.mediamtx_rtsp_port,
                self.settings.ai_worker_index,
                self.settings.ai_worker_count,
                self.settings.ai_worker_cameras,
                limit=10_000,
            )

        if self.settings.ai_worker_source == "sandbox":
            wanted = await discover_sandbox(
                self.settings.sandbox_base_url,
                self.settings.ai_worker_index,
                self.settings.ai_worker_count,
                10_000,
                self.settings.sandbox_transport or self._transport,
            )
            # Probe once, then reuse: the answer is a property of the network,
            # not of the camera, and re-probing every cycle is wasted latency.
            if wanted and self._transport is None:
                self._transport = wanted[0].transport
            return wanted

        return await discover(
            self.settings.mediamtx_api_url,
            self.settings.mediamtx_host,
            self.settings.mediamtx_rtsp_port,
            self.settings.ai_worker_index,
            self.settings.ai_worker_count,
            limit=10_000,
        )

    async def _reconcile(self) -> None:
        """Make the running set match the set that should be running now."""
        self.rotation.update_assignment(await self._discover())
        assigned = self.rotation.assigned

        # Cameras that left the fleet, or moved to another worker.
        for code in list(self.tasks):
            if code not in assigned:
                log.info("camera %s no longer assigned; stopping", code)
                self._stop(code)

        # Threads that ended — the slice expired, the stream closed, or it
        # errored. Either way the slot is free and goes back to the rotation
        # rather than straight back to the same camera, which is what lets a
        # fleet larger than the slot count get swept.
        for code, task in list(self.tasks.items()):
            if not task.alive:
                ran_for = time.perf_counter() - task.started_at
                if task.completed_slice:
                    log.info(
                        "%s finished its %.0fs slice; yielding the slot", code, ran_for
                    )
                elif task.unreachable:
                    # Already logged as a warning by the loop itself; the slot
                    # goes straight back to the rotation so an unreachable
                    # camera costs the fleet a connect attempt, not a slice.
                    pass
                else:
                    log.warning(
                        "inference loop for %s exited after %.0fs", code, ran_for
                    )
                    await asyncio.sleep(self.settings.restart_delay_s)
                self._stop(code)

        # Cameras whose slice is up and which somebody is waiting behind.
        for code in self.rotation.expired():
            log.info("rotating %s out; its slice is up and cameras are waiting", code)
            self._stop(code)

        for stream in self.rotation.next_up():
            self._start(stream, 0)

        self._log_coverage()

    def _log_coverage(self) -> None:
        """Say what is running and, honestly, what is not."""
        total = len(self.rotation.assigned)
        if not total:
            log.info(
                "no cameras assigned — the roster is empty for this shard. "
                "Is the API publishing it, and are any cameras anpr_enabled?"
            )
            return

        sweep = self.rotation.sweep_seconds
        coverage = (
            f"; {total} cameras across {self.rotation.slots} slots, "
            f"each watched {self.rotation.slice_seconds:.0f}s every {sweep / 60:.1f} min"
            if sweep > 0
            else "; every assigned camera runs continuously"
        )
        pool = self._pipelines.stats()
        log.info(
            "watching %d/%d camera(s): %s · %d events published%s · models built %d, "
            "reused %d",
            len(self.tasks),
            total,
            ", ".join(sorted(self.tasks)),
            self.sink.written,
            coverage,
            # `built` should settle at the slot count and stop rising. If it
            # keeps climbing, pipelines are not being returned and the worker
            # is back to leaking a thread pool per rotation.
            pool["built"],
            pool["reused"],
        )

    # ─────────────────────────────────────────────────────────────────
    def _start(self, stream: CameraStream, restarts: int) -> None:
        stop = threading.Event()
        slice_seconds = self.rotation.slice_for(stream.camera_code)
        thread = threading.Thread(
            target=self._process,
            args=(stream, stop, slice_seconds),
            name=f"cam-{stream.camera_code}",
            daemon=True,
        )
        self.tasks[stream.camera_code] = CameraTask(
            stream=stream, thread=thread, stop=stop, restarts=restarts
        )
        self.rotation.take_slot(stream.camera_code)
        thread.start()
        log.info(
            "processing %s [%s] over %s for %s — %s",
            stream.camera_code,
            stream.label or "unnamed",
            stream.transport,
            f"{slice_seconds:.0f}s" if slice_seconds else "as long as it runs",
            # Redacted: RTSP credentials travel in the URL, and this line goes
            # to stdout, the container log, and wherever those are shipped.
            redact(stream.rtsp_url),
        )

    def _config_for(self, stream: CameraStream) -> RunConfig:
        """The pipeline config for one camera.

        Identical to the worker's config in every inference decision; only the
        accepted plate formats differ, and only when the camera declares them.
        A copy per camera because each runs on its own thread.
        """
        if tuple(stream.plate_regions) == tuple(self.config.consensus.plate_regions):
            return self.config
        config = self.config.model_copy(deep=True)
        config.consensus.plate_regions = tuple(stream.plate_regions)
        return config

    def _process(
        self, stream: CameraStream, stop: threading.Event, slice_seconds: float | None
    ) -> None:
        """One camera's inference loop. Runs on its own thread."""
        source = SourceIdentity(camera_id=stream.camera_code, name=stream.camera_code)
        config = self._config_for(stream)
        # Borrowed, not built. Constructing a Pipeline here loads five ONNX
        # models and leaks their native thread pools on every rotation — see
        # PipelinePool.
        pipeline = self._pipelines.acquire(config)
        # Passing a run_dir is what makes crops exist at all: without one the
        # runner substitutes _NullRunDir and silently discards every crop, which
        # is why Detection.crop_key was NULL for the life of the project.
        runner = StreamRunner(
            config, source, self.sink, run_dir=self._crops, pipeline=pipeline
        )
        try:
            # realtime=True: a live source sets the pace, and falling behind is
            # handled by dropping frames rather than by queueing them.
            report = runner.run(
                stream.rtsp_url, max_seconds=slice_seconds, realtime=True
            )
            task = self.tasks.get(stream.camera_code)
            if task is not None:
                task.last_report = report
                # Returning without an exception, having been given a deadline,
                # means the deadline is what ended it.
                task.completed_slice = slice_seconds is not None
        except StreamUnavailable as exc:
            # Expected for a federated camera whose gateway is down. It is one
            # camera's status, not a fault in this worker, so it gets a line
            # rather than a traceback — thirty unreachable cameras would
            # otherwise bury every real failure in the log.
            #
            # The *reason* is what makes the line worth reading. "unreachable"
            # sends an operator to check the network; "unauthorized" sends them
            # to find a password. Reporting the first when the truth is the
            # second is how a working grid looks broken for a day.
            log.warning(
                "%s not started — %s: %s",
                stream.camera_code,
                REASON_ADVICE.get(exc.reason, exc.reason),
                exc,
            )
            task = self.tasks.get(stream.camera_code)
            if task is not None:
                task.unreachable = True
                task.reason = exc.reason
        except Exception:
            # A supervisor boundary: one camera's failure must not take down
            # the others, and the traceback has to survive to be diagnosed.
            log.exception("inference loop for %s failed", stream.camera_code)
        finally:
            # Returned however the run ended, including after a failure: a
            # pipeline that is not given back is one the next camera has to
            # rebuild, which is the leak this pool exists to close.
            self._pipelines.release(config, pipeline)
            stop.set()

    def _stop(self, code: str) -> None:
        task = self.tasks.pop(code, None)
        self.rotation.release_slot(code)
        if task is None:
            return
        task.stop.set()
        if task.thread.is_alive():
            # StreamRunner returns when its reader ends; give it a moment, then
            # let the daemon thread go rather than blocking shutdown on it.
            task.thread.join(timeout=3.0)

    async def _stop_all(self) -> None:
        for code in list(self.tasks):
            self._stop(code)

    def shutdown(self) -> None:
        self._shutdown.set()

    # ── introspection, used by the healthcheck ──
    def status(self) -> dict[str, Any]:
        return {
            "worker": f"{self.settings.ai_worker_index}/{self.settings.ai_worker_count}",
            "cameras": sorted(self.tasks),
            "assigned": sorted(self.rotation.assigned),
            "slots": self.rotation.slots,
            # How long before every assigned camera has had a turn. The number
            # an operator needs to answer "could we have missed a vehicle?".
            "sweep_seconds": round(self.rotation.sweep_seconds, 1),
            "alive": sum(1 for t in self.tasks.values() if t.alive),
            "events_published": self.sink.written,
            "publish_failures": self.sink.publish_failures,
            "by_kind": dict(self.sink.by_kind),
        }
