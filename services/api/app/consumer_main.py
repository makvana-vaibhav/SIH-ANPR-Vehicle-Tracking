"""A dedicated event-ingest worker: persistence only, no HTTP.

The API process can persist events itself, and on a single-node deployment it
does. That stops being the right shape at scale for a specific reason: ingest
capacity and operator-serving capacity are driven by completely different
things. Ingest scales with the size of the camera fleet; serving scales with
how many officers are logged in. Tying them together means adding an API
replica because the control room is busy also adds a consumer nobody needed,
and adding ingest capacity for a larger fleet also adds idle web servers.

Running this process instead — with `INGEST_ENABLED=false` on the API replicas
— separates them. Redis hands each stream entry to exactly one member of the
consumer group, so N workers share the fleet's events N ways with no
coordination between them and no configuration naming which cameras belong to
whom.

Alerts these workers raise reach operators over the pub/sub channel in
`alert_fanout.py`; the live event feed reaches them from each API replica's own
`EventTailer`. Neither needs this process to be reachable, which is why it
listens on nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.services import event_consumer, watchlist

log = get_logger("consumer_worker")


async def run() -> None:
    configure_logging()
    log.info(
        "consumer_worker.starting",
        stream=settings.event_stream_key,
        environment=settings.environment,
    )

    # The watchlist index is consulted for every plate that arrives, so it is
    # loaded before the first event rather than on the first match — otherwise
    # the worker's first batch pays for it while the vehicle drives away.
    await watchlist.index.refresh()
    await event_consumer.consumer.start()

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # Orchestrators stop containers with SIGTERM. Handling it means the
        # consumer acknowledges what it has finished and leaves the rest for
        # another worker to claim, rather than being killed mid-batch.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stopping.set)

    await stopping.wait()

    log.info("consumer_worker.stopping", **event_consumer.consumer.stats())
    await event_consumer.consumer.stop()
    await dispose_engine()
    log.info("consumer_worker.stopped")


if __name__ == "__main__":
    asyncio.run(run())
