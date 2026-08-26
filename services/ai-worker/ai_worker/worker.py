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

from ailab.config import RunConfig
from ailab.stream import SourceIdentity, StreamRunner

from ai_worker.config import WorkerSettings
from ai_worker.discovery import CameraStream, discover
from ai_worker.publisher import RedisEventSink

log = logging.getLogger(__name__)


@dataclass
class CameraTask:
    """One camera being processed."""

    stream: CameraStream
    thread: threading.Thread
    stop: threading.Event
    started_at: float = field(default_factory=time.perf_counter)
    restarts: int = 0
    last_report: dict[str, Any] = field(default_factory=dict)

    @property
    def alive(self) -> bool:
        return self.thread.is_alive()


class AiWorker:
    """Supervises per-camera inference loops."""

    def __init__(self, settings: WorkerSettings, config: RunConfig) -> None:
        self.settings = settings
        self.config = config
        self.sink = RedisEventSink(
            settings.redis_url, settings.event_stream_key, settings.event_stream_maxlen
        )
        self.tasks: dict[str, CameraTask] = {}
        self._shutdown = asyncio.Event()

    # ─────────────────────────────────────────────────────────────────
    async def run(self) -> None:
        log.info(
            "ai-worker %d/%d starting (config=%s, max %d cameras)",
            self.settings.ai_worker_index, self.settings.ai_worker_count,
            self.config.name, self.settings.ai_worker_max_cameras,
        )
        try:
            while not self._shutdown.is_set():
                await self._reconcile()
                try:
                    await asyncio.wait_for(
                        self._shutdown.wait(), timeout=self.settings.discovery_interval_s
                    )
                except TimeoutError:
                    continue
        finally:
            await self._stop_all()
            self.sink.close()

    async def _reconcile(self) -> None:
        """Make the running set match the set we should be running."""
        wanted = await discover(
            self.settings.mediamtx_api_url,
            self.settings.mediamtx_host,
            self.settings.mediamtx_rtsp_port,
            self.settings.ai_worker_index,
            self.settings.ai_worker_count,
            self.settings.ai_worker_cameras,
            self.settings.ai_worker_max_cameras,
        )
        wanted_by_code = {c.camera_code: c for c in wanted}

        # Cameras that stopped publishing, or moved to another worker.
        for code in list(self.tasks):
            if code not in wanted_by_code:
                log.info("camera %s no longer assigned; stopping", code)
                self._stop(code)

        # Threads that died on their own — the stream ended or errored.
        for code, task in list(self.tasks.items()):
            if not task.alive:
                log.warning(
                    "inference loop for %s exited after %.0fs; restarting",
                    code, time.perf_counter() - task.started_at,
                )
                restarts = task.restarts + 1
                self._stop(code)
                await asyncio.sleep(self.settings.restart_delay_s)
                if code in wanted_by_code:
                    self._start(wanted_by_code[code], restarts)

        for code, stream in wanted_by_code.items():
            if code not in self.tasks:
                self._start(stream, 0)

        if self.tasks:
            log.info(
                "watching %d camera(s): %s · %d events published",
                len(self.tasks), ", ".join(sorted(self.tasks)), self.sink.written,
            )
        else:
            log.info("no cameras assigned (nothing publishing to MediaMTX for this shard)")

    # ─────────────────────────────────────────────────────────────────
    def _start(self, stream: CameraStream, restarts: int) -> None:
        stop = threading.Event()
        thread = threading.Thread(
            target=self._process,
            args=(stream, stop),
            name=f"cam-{stream.camera_code}",
            daemon=True,
        )
        self.tasks[stream.camera_code] = CameraTask(
            stream=stream, thread=thread, stop=stop, restarts=restarts
        )
        thread.start()
        log.info("processing %s (%s)", stream.camera_code, stream.rtsp_url)

    def _process(self, stream: CameraStream, stop: threading.Event) -> None:
        """One camera's inference loop. Runs on its own thread."""
        source = SourceIdentity(camera_id=stream.camera_code, name=stream.camera_code)
        runner = StreamRunner(self.config, source, self.sink)
        try:
            # realtime=True: a live source sets the pace, and falling behind is
            # handled by dropping frames rather than by queueing them.
            report = runner.run(stream.rtsp_url, realtime=True)
            task = self.tasks.get(stream.camera_code)
            if task is not None:
                task.last_report = report
        except Exception:
            # A supervisor boundary: one camera's failure must not take down
            # the others, and the traceback has to survive to be diagnosed.
            log.exception("inference loop for %s failed", stream.camera_code)
        finally:
            stop.set()

    def _stop(self, code: str) -> None:
        task = self.tasks.pop(code, None)
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
            "alive": sum(1 for t in self.tasks.values() if t.alive),
            "events_published": self.sink.written,
            "publish_failures": self.sink.publish_failures,
            "by_kind": dict(self.sink.by_kind),
        }
