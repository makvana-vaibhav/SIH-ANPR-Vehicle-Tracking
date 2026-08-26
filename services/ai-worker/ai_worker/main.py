"""Entry point for the AI worker.

    python -m ai_worker

Reads its camera assignment from MediaMTX, runs inference on each assigned
stream, and publishes vehicle events to the platform's Redis Stream.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from ailab.config import RunConfig

from ai_worker.config import settings
from ai_worker.worker import AiWorker

log = logging.getLogger("ai_worker")


def _configure_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # The lab narrates every frame at debug; the worker wants its summaries.
    logging.getLogger("ailab").setLevel(logging.INFO)


def _load_config() -> RunConfig:
    config = RunConfig.load(settings.ai_config)
    # Model paths are baked into the image at a known location, so a config
    # written for the lab's layout still resolves here.
    config.detector.weights = f"{settings.ai_models_dir}/{config.detector.weights.split('/')[-1]}"
    config.plate.weights = f"{settings.ai_models_dir}/{config.plate.weights.split('/')[-1]}"
    return config


async def _run() -> int:
    config = _load_config()
    worker = AiWorker(settings, config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Containers are stopped with SIGTERM. Handling it means the streams
        # close and the final events are emitted, instead of the process being
        # killed mid-vehicle.
        loop.add_signal_handler(sig, worker.shutdown)

    await worker.run()
    log.info("ai-worker stopped: %s", worker.status())
    return 0


def main() -> int:
    _configure_logging()
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
