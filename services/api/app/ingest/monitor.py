"""Health monitor daemon.

Runs as its own container (`ingest` in docker-compose.yml) so probing 80,000
cameras never competes with serving operator requests. It shares the API image
rather than being a separate codebase — see CLAUDE.md §9 for why.

Run with:  python -m app.ingest.monitor
"""

from __future__ import annotations

import asyncio
import signal
import sys
from datetime import UTC, datetime

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal, dispose_engine
from app.services import health_monitor

configure_logging(service="ingest")
log = get_logger("ingest.monitor")

_shutdown = asyncio.Event()


def _handle_signal(signum: int, _frame: object) -> None:
    """Ask the loop to stop after the current sweep."""
    log.info("monitor.signal_received", signal=signal.Signals(signum).name)
    _shutdown.set()


async def run() -> int:
    """Probe the fleet on a fixed interval until asked to stop."""
    interval = settings.health_probe_interval_seconds
    log.info(
        "monitor.starting",
        interval_seconds=interval,
        timeout_seconds=settings.health_probe_timeout_seconds,
        offline_after_failures=settings.health_offline_after_failures,
    )

    sweeps = 0
    while not _shutdown.is_set():
        started = datetime.now(UTC)
        try:
            async with SessionLocal() as session:
                summary = await health_monitor.sweep(session)
            sweeps += 1
        except Exception as exc:
            # Supervisor boundary: a failed sweep must not kill the daemon.
            # A monitor that dies on the first database blip is worse than no
            # monitor, because the fleet view silently freezes at its last
            # known state and nobody notices.
            log.error("monitor.sweep_failed", error=str(exc), exc_info=True)
            summary = {}

        elapsed = (datetime.now(UTC) - started).total_seconds()

        # Sleep the remainder of the interval, so a slow sweep does not cause
        # the cycle to drift later and later.
        delay = max(interval - elapsed, 1.0)
        if summary:
            log.info(
                "monitor.cycle",
                sweep=sweeps,
                elapsed_s=round(elapsed, 2),
                next_in_s=round(delay, 1),
                **summary,
            )

        try:
            await asyncio.wait_for(_shutdown.wait(), timeout=delay)
        except TimeoutError:
            continue

    log.info("monitor.stopping", sweeps_completed=sweeps)
    await dispose_engine()
    return 0


def main() -> int:
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, _handle_signal)
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
