"""Background daemon: fleet health, and journey snapshots.

Runs as its own container (`ingest` in docker-compose.yml) so probing the fleet
never competes with serving operator requests. It shares the API image rather
than being a separate codebase — see CLAUDE.md §11 for why.

Two jobs on two cadences. The **health sweep** probes every camera often, because
a camera that has dropped off should show as down within seconds. The **route
snapshot** reconstructs journeys far less often: it is much more expensive per
cycle, nothing depends on it being seconds-fresh, and it belongs off the ingest
path because that path's measured p95 latency is already over its target.

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
from app.services import alert_fanout, health_monitor, route_history

configure_logging(service="ingest")
log = get_logger("ingest.monitor")

_shutdown = asyncio.Event()

#: Health sweeps between journey snapshots. At the default 15 s sweep interval
#: this snapshots roughly once a minute — often enough that a journey in progress
#: is recorded while it is still interesting, rare enough that reconstructing up
#: to 40 routes never delays a health sweep.
SNAPSHOT_EVERY_N_SWEEPS = 4


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

        # Journey snapshots, on a slower cadence than the health sweep.
        if sweeps and sweeps % SNAPSHOT_EVERY_N_SWEEPS == 0:
            try:
                async with SessionLocal() as session:
                    routes = await route_history.snapshot(session)
                raised_alerts = routes.pop("raised_alerts", [])
                if routes.get("persisted"):
                    log.info("monitor.routes_snapshotted", sweep=sweeps, **routes)
                # Announced after the snapshot's own commit, same reason as
                # the ingest path: an operator must never see a trajectory
                # anomaly that a rolled-back transaction means does not exist.
                for payload in raised_alerts:
                    await alert_fanout.publish(payload)
            except Exception as exc:
                # Same supervisor boundary as the sweep: losing a snapshot cycle
                # costs some journey history, which is recoverable. Killing the
                # daemon would also stop health monitoring, which is not.
                log.error("monitor.snapshot_failed", error=str(exc), exc_info=True)

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
