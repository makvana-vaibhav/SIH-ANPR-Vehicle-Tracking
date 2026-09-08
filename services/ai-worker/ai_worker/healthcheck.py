"""Container healthcheck.

Healthy means "this worker can reach the bus it publishes to". It deliberately
does not require cameras to be assigned: a worker with an empty shard, or one
waiting for cameras to start publishing, is idle rather than broken, and marking
it unhealthy would restart it forever for doing the right thing.
"""

from __future__ import annotations

import sys

import redis

from ai_worker.config import settings


def main() -> int:
    try:
        client = redis.Redis.from_url(settings.redis_url, socket_timeout=3.0)
        client.ping()
        client.close()
    except redis.RedisError as exc:
        print(
            f"unhealthy: cannot reach redis at {settings.redis_url}: {exc}",
            file=sys.stderr,
        )
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
