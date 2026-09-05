"""Sentinel-GJ API — application entrypoint.

Statewide CCTV intelligence platform for the Gujarat Police / Home Department.
This tier owns the camera registry, GIS queries, auth and audit, the event and
alert engines, search, and cross-camera correlation.

Phases are built in order (see BUILD_STATE.md); routers are mounted here as
each phase lands.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import configure_logging, get_logger, request_id_var
from app.core.ratelimit import RateLimitMiddleware
from app.db.session import dispose_engine
from app.middleware.audit import AuditMiddleware
from app.routers import (
    alerts,
    audit,
    auth,
    cameras,
    detections,
    events,
    fleet,
    grid_media,
    health,
    streams,
    users,
    vehicles,
    watchlist,
)
from app.services import (
    alert_fanout,
    event_consumer,
    event_tailer,
    fleet_roster,
    retention,
    token_store,
)

configure_logging(service="api")
log = get_logger("api")

DESCRIPTION = """
**Sentinel-GJ** — statewide CCTV intelligence platform.

Federates existing multi-vendor, multi-department CCTV rather than replacing it
(reference Model 5 — Hybrid). Departmental VMS remain authoritative for their own
video; this platform ingests metadata centrally, pulls streams on demand, and runs
AI at the edge so that **the central tier carries events, not video**.

* **Registry + GIS** — camera estate, PostGIS queries, bulk onboarding
* **Integration** — RTSP / ONVIF / vendor-VMS adapters, health monitoring
* **Intelligence** — ANPR events, watchlist matching, cross-camera route reconstruction
* **Audit** — every plate search, stream open, and watchlist change is recorded
"""


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Start-up and shut-down.

    Configuration is logged (redacted) at boot so a failing deployment can be
    diagnosed from logs alone, without shell access to the container.
    """
    log.info(
        "api.starting",
        environment=settings.environment,
        event_bus=settings.event_bus_backend,
        search_fallback=settings.search_fallback_enabled,
    )
    if settings.environment == "development":
        log.debug("api.configuration", **settings.sanitised())

    # The live operations picture: every replica tails the stream and fans
    # every event out to its own sockets. Always on — an API process that
    # cannot show operators what is happening has no reason to be running.
    await event_tailer.tailer.start()
    # And alerts raised by any ingest worker, wherever it runs.
    await alert_fanout.subscriber.start()

    # Durable ingest. Started here rather than lazily on the first WebSocket
    # connection, so detections are persisted whether or not an operator
    # happens to be watching. Disabled on replicas of a deployment with
    # dedicated ingest workers; see Settings.ingest_enabled.
    if settings.ingest_enabled:
        await event_consumer.consumer.start()
    else:
        log.info("api.ingest_delegated", reason="dedicated ingest workers")
    fleet_roster.publisher.start()
    retention.enforcer.start()

    yield

    log.info("api.stopping")
    await retention.enforcer.stop()
    await fleet_roster.publisher.stop()
    await event_consumer.consumer.stop()
    await alert_fanout.subscriber.stop()
    await event_tailer.tailer.stop()
    await token_store.close()
    await dispose_engine()
    log.info("api.stopped")


app = FastAPI(
    title=settings.app_name,
    description=DESCRIPTION,
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    contact={"name": "Sentinel-GJ", "url": "https://sentinel.gujarat.gov.in"},
    license_info={"name": "Apache-2.0"},
)

# Starlette runs middleware in reverse registration order, so what is added
# last is outermost. The intended order, outermost first, is:
#
#   CORS  →  rate limit  →  audit  →  the route
#
# CORS outermost so a 429 still carries the headers a browser needs to read it.
# The rate limiter *outside* the audit middleware so a flood is rejected before
# it can write a row per request — an attacker who can make the platform fill
# its own audit table has found a way to destroy the record of what they did.
app.add_middleware(AuditMiddleware)

app.add_middleware(RateLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)


@app.middleware("http")
async def request_context(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Assign a correlation id to every request and log its outcome.

    The id is bound into the logging context so every line emitted while
    handling the request carries it, and returned as ``X-Request-ID`` so an
    operator reporting a problem can quote a value we can grep for.

    This is *not* the audit trail — audit logging is a durable database record
    added in Phase 1. This is operational telemetry.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    token = request_id_var.set(request_id)

    try:
        response = await call_next(request)
    except Exception as exc:
        # Supervisor boundary: log with a stack trace, return a clean error.
        # An unhandled exception must never leak internals to a client of a
        # surveillance system.
        log.error(
            "request.unhandled_exception",
            method=request.method,
            path=request.url.path,
            error=str(exc),
            exc_info=True,
        )
        request_id_var.reset(token)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    response.headers["X-Request-ID"] = request_id

    # Health probes fire every few seconds; logging them buries real traffic.
    if request.url.path not in ("/health", "/ready", "/metrics"):
        log.info(
            "request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            client=request.client.host if request.client else None,
        )

    request_id_var.reset(token)
    return response


# ── Routers ───────────────────────────────────────────────────────────
# Later phases mount cameras, events, watchlist, alerts, search, vehicles, admin.
app.include_router(health.router)
app.include_router(auth.router)
app.include_router(cameras.router)
app.include_router(fleet.router)
app.include_router(streams.router)
app.include_router(grid_media.router)
app.include_router(events.router)
app.include_router(detections.router)
app.include_router(users.router)
app.include_router(audit.router)
app.include_router(vehicles.router)
app.include_router(watchlist.router)
app.include_router(alerts.router)


@app.get("/", tags=["meta"], summary="Service banner")
async def root() -> dict[str, object]:
    """Human-readable entrypoint pointing at the interactive docs."""
    return {
        "service": settings.app_name,
        "description": "Statewide CCTV intelligence platform — Gujarat",
        "version": app.version,
        "model": "Hybrid (Model 5): Registry+GIS + Federation middleware + selective unified viewing",
        "docs": "/docs",
        "health": "/health",
        "ready": "/ready",
        "api_prefix": settings.api_v1_prefix,
    }
