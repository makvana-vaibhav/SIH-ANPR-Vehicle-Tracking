"""Container healthcheck for the health-monitor daemon.

The monitor has no HTTP surface, so liveness is proven by what it is *for*:
a recent row in ``camera_health``. A process that is still running but has
stopped probing is not healthy — and that is exactly the failure a naive
"is the process alive" check would miss.

Exit 0 = healthy, 1 = unhealthy.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal, dispose_engine

#: Allow a few missed cycles before declaring the monitor unhealthy, so a
#: single slow sweep does not trigger a restart.
STALENESS_MULTIPLIER = 4


async def _recent_probe_count() -> int:
    window_seconds = settings.health_probe_interval_seconds * STALENESS_MULTIPLIER
    async with SessionLocal() as session:
        result = await session.execute(
            text(
                "SELECT count(*) FROM camera_health "
                "WHERE ts > now() - make_interval(secs => :window)"
            ),
            {"window": window_seconds},
        )
        return int(result.scalar_one())


async def main() -> int:
    try:
        count = await _recent_probe_count()
    except Exception as exc:  # healthcheck boundary: any failure is unhealthy
        sys.stderr.write(f"unhealthy: {exc}\n")
        return 1
    finally:
        await dispose_engine()

    if count == 0:
        sys.stderr.write("unhealthy: no probes recorded recently\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
