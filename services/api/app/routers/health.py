"""Liveness and readiness endpoints.

The distinction matters operationally and is not cosmetic:

* ``/health``  — is this process alive? No dependency calls. Used by Docker
  and by an orchestrator to decide whether to restart the container.
* ``/ready``   — can this process actually serve traffic? Probes every
  dependency concurrently. Used by a load balancer to decide whether to send
  it requests.

A degraded-but-serving state is reported honestly: OpenSearch being down is
*not* fatal, because plate search falls back to Postgres trigram matching.
Losing Postgres is fatal. The endpoint distinguishes the two rather than
collapsing everything into a single boolean.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Response, status

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import check_database

log = get_logger("api.health")

router = APIRouter(tags=["health"])

# Process start time, for uptime reporting.
_STARTED_AT = time.monotonic()
_STARTED_WALL = datetime.now(UTC)

# Probe timeout. Readiness must answer quickly even when a dependency is
# hanging — a readiness check that blocks is itself an outage.
_PROBE_TIMEOUT_SECONDS = 3.0

DependencyStatus = Literal["ok", "degraded", "down"]


async def _probe_postgres() -> dict[str, Any]:
    """Postgres + required extensions. Fatal if unreachable."""
    try:
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            info = await check_database()
    except TimeoutError:
        return {"status": "down", "critical": True, "error": "probe timed out"}
    except (OSError, ConnectionError) as exc:
        return {"status": "down", "critical": True, "error": str(exc)}
    except Exception as exc:  # supervisor boundary — report, never crash readiness
        log.warning("health.postgres_probe_failed", error=str(exc), exc_info=True)
        return {"status": "down", "critical": True, "error": str(exc)}

    if info["missing_extensions"]:
        return {
            "status": "degraded",
            "critical": True,
            "error": f"missing extensions: {', '.join(info['missing_extensions'])}",
            "server_version": info["server_version"],
        }

    return {
        "status": "ok",
        "critical": True,
        "server_version": info["server_version"],
        "extensions": info["extensions"],
    }


async def _probe_redis() -> dict[str, Any]:
    """Redis — the event bus in the base profile. Fatal if unreachable."""
    client: aioredis.Redis | None = None
    try:
        client = aioredis.from_url(
            settings.redis_url,
            socket_connect_timeout=_PROBE_TIMEOUT_SECONDS,
            socket_timeout=_PROBE_TIMEOUT_SECONDS,
        )
        async with asyncio.timeout(_PROBE_TIMEOUT_SECONDS):
            await client.ping()
            info = await client.info("server")
        return {
            "status": "ok",
            "critical": True,
            "server_version": info.get("redis_version", "unknown"),
        }
    except (TimeoutError, aioredis.RedisError, OSError) as exc:
        return {"status": "down", "critical": True, "error": str(exc)}
    finally:
        if client is not None:
            await client.aclose()


async def _probe_opensearch() -> dict[str, Any]:
    """OpenSearch — NOT fatal. Search degrades to Postgres pg_trgm."""
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{settings.opensearch_url}/_cluster/health")
            resp.raise_for_status()
            body = resp.json()
        cluster_status = body.get("status", "unknown")
        return {
            "status": "ok" if cluster_status in ("green", "yellow") else "degraded",
            "critical": False,
            "cluster_status": cluster_status,
            "nodes": body.get("number_of_nodes"),
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "status": "down",
            "critical": False,
            "error": str(exc),
            "fallback": ("postgres_trgm" if settings.search_fallback_enabled else "none"),
        }


async def _probe_minio() -> dict[str, Any]:
    """MinIO — NOT fatal. Events still flow; crops become unavailable."""
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{settings.minio_url}/minio/health/live")
    except httpx.HTTPError as exc:
        return {"status": "down", "critical": False, "error": str(exc)}
    else:
        return {
            "status": "ok" if resp.status_code == 200 else "degraded",
            "critical": False,
            "http_status": resp.status_code,
        }


async def _probe_mediamtx() -> dict[str, Any]:
    """MediaMTX — NOT fatal. Analytics continue; live viewing is unavailable."""
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{settings.mediamtx_api_url}/v3/paths/list")
            resp.raise_for_status()
            body = resp.json()
        return {
            "status": "ok",
            "critical": False,
            "active_paths": body.get("itemCount", 0),
        }
    except (httpx.HTTPError, ValueError) as exc:
        return {"status": "down", "critical": False, "error": str(exc)}


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, Any]:
    """Is the process alive? Deliberately performs no dependency I/O.

    A liveness probe that depends on Postgres will restart a perfectly healthy
    API container every time the database hiccups, turning a brief blip into
    a crash loop.
    """
    return {
        "status": "alive",
        "service": settings.app_name,
        "environment": settings.environment,
        "uptime_seconds": round(time.monotonic() - _STARTED_AT, 1),
        "started_at": _STARTED_WALL.isoformat(),
        "now": datetime.now(UTC).isoformat(),
    }


@router.get("/ready", summary="Readiness probe")
async def ready(response: Response) -> dict[str, Any]:
    """Can this process serve traffic? Probes all dependencies concurrently.

    Returns 200 when every *critical* dependency is healthy, even if optional
    ones are degraded (the response body says which). Returns 503 when a
    critical dependency is unavailable.
    """
    names = ("postgres", "redis", "opensearch", "minio", "mediamtx")
    probes = (
        _probe_postgres(),
        _probe_redis(),
        _probe_opensearch(),
        _probe_minio(),
        _probe_mediamtx(),
    )

    started = time.perf_counter()
    results = await asyncio.gather(*probes, return_exceptions=True)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    dependencies: dict[str, Any] = {}
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("health.probe_raised", dependency=name, error=str(result))
            dependencies[name] = {
                "status": "down",
                "critical": name in ("postgres", "redis"),
                "error": str(result),
            }
        else:
            dependencies[name] = result

    critical_failures = [
        name
        for name, dep in dependencies.items()
        if dep.get("critical") and dep.get("status") != "ok"
    ]
    optional_failures = [
        name
        for name, dep in dependencies.items()
        if not dep.get("critical") and dep.get("status") != "ok"
    ]

    if critical_failures:
        overall = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif optional_failures:
        overall = "degraded"
        response.status_code = status.HTTP_200_OK
    else:
        overall = "ready"
        response.status_code = status.HTTP_200_OK

    if critical_failures or optional_failures:
        log.info(
            "health.not_fully_ready",
            overall=overall,
            critical_failures=critical_failures,
            optional_failures=optional_failures,
        )

    return {
        "status": overall,
        "probe_duration_ms": elapsed_ms,
        "dependencies": dependencies,
        "critical_failures": critical_failures,
        "degraded": optional_failures,
        "now": datetime.now(UTC).isoformat(),
    }
