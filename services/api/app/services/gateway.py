"""Registers a camera's real source with the stream gateway.

## The gap this closes

`RtspAdapter.get_stream_url` hands out a **gateway** URL —
`rtsp://mediamtx:8554/<camera code>` — rather than the camera's own. That is
right, and for two reasons: a browser cannot reach the camera VLAN, and giving
it the camera's URL would bypass the audited token flow.

But nothing ever put the camera's stream *into* the gateway. A camera onboarded
through the admin panel with a perfectly good RTSP URL was told to be read from
a gateway path that had never been published, so it could be neither watched
nor analysed. The URL the operator typed was stored and then ignored.

This is the missing half: tell MediaMTX to pull that source and republish it
under the camera's code, which is the path everything else already expects.

## On demand, deliberately

Paths are registered with `sourceOnDemand`, so MediaMTX connects to the camera
only while something is actually reading — an operator watching, or the ANPR
worker analysing. An unwatched camera costs no bandwidth, which is the property
the whole federation argument rests on. Registering a thousand cameras eagerly
would pull a thousand streams into a building that asked for none of them.

## Where the credentials live

The source URL carries them, because RTSP has nowhere else to put them. It goes
from this service to MediaMTX over the internal network and is never returned
to a browser: `CameraOut` has no `stream_url` field, and every log line here
redacts. See `docs/SECURITY.md` §6.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.urls import redact

log = get_logger("gateway")

TIMEOUT_S = 6.0

#: Adapters whose cameras live on somebody else's gateway. Their video is read
#: from that gateway directly, by the worker and by the media proxy; pulling it
#: through ours as well would double the bandwidth for no gain.
FEDERATED_ADAPTERS = frozenset({"hosted_grid", "vendor_api"})


def path_for(camera_code: str) -> str:
    """MediaMTX path name for a camera. Codes are lowercased everywhere."""
    return camera_code.lower()


async def register(camera: Any) -> bool:
    """Publish a camera's source into the gateway. Idempotent.

    Returns whether the gateway now carries it. A failure is logged and
    reported rather than raised: onboarding a camera must not fail because the
    media gateway is briefly unreachable, and `reconcile()` will pick it up.
    """
    stream_url = getattr(camera, "stream_url", None)
    code = getattr(camera, "camera_code", "")
    if not stream_url or not code:
        return False

    name = path_for(code)
    body = {
        "source": stream_url,
        "sourceOnDemand": True,
        # Long enough that the ANPR worker's rotation, which revisits a camera
        # every few minutes, does not pay reconnection on every visit; short
        # enough that a camera nobody is watching is genuinely released.
        "sourceOnDemandCloseAfter": "60s",
    }

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            response = await client.post(
                f"{settings.mediamtx_api_url}/v3/config/paths/add/{name}", json=body
            )
            if response.status_code == 400:
                # Already configured. Patch it, so an edited URL takes effect
                # rather than silently leaving the camera on its old source.
                response = await client.patch(
                    f"{settings.mediamtx_api_url}/v3/config/paths/patch/{name}", json=body
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning(
                "gateway.register_failed",
                camera=code,
                source=redact(str(stream_url)),
                error=str(exc),
            )
            return False

    log.info("gateway.registered", camera=code, source=redact(str(stream_url)))
    return True


async def unregister(camera_code: str) -> bool:
    """Remove a camera's path. Idempotent — a missing path is success."""
    name = path_for(camera_code)
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        try:
            response = await client.delete(
                f"{settings.mediamtx_api_url}/v3/config/paths/delete/{name}"
            )
            if response.status_code == 404:
                return True
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("gateway.unregister_failed", camera=camera_code, error=str(exc))
            return False

    log.info("gateway.unregistered", camera=camera_code)
    return True


async def reconcile() -> dict[str, int]:
    """Make the gateway's paths match the registry.

    Run at startup and after bulk onboarding. Without it the gateway's view is
    only as good as the last successful call: a camera added while MediaMTX was
    restarting would stay unwatchable until somebody edited it, with nothing
    reporting a fault.

    Only cameras that are **republished through our gateway** are registered.
    A federated camera on the organisers' grid is read from their gateway
    directly by both the worker and the media proxy, and pulling it through
    ours as well would double the bandwidth to no purpose.
    """
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.registry import Camera, VmsInstance

    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(Camera.camera_code, Camera.stream_url, VmsInstance.adapter_type)
                .outerjoin(VmsInstance, VmsInstance.id == Camera.vms_id)
                .where(Camera.stream_url.is_not(None))
            )
        ).all()

    # Judged on the adapter, not on what the URL looks like. A federated
    # camera is one whose video lives on somebody else's gateway, and that is
    # a fact about how it is integrated — not something to infer by matching
    # substrings against a hostname that can change.
    wanted = {
        path_for(code): url
        for code, url, adapter_type in rows
        if adapter_type not in FEDERATED_ADAPTERS
    }

    registered = failed = 0
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        for name, url in wanted.items():
            body = {
                "source": url,
                "sourceOnDemand": True,
                "sourceOnDemandCloseAfter": "60s",
            }
            try:
                response = await client.post(
                    f"{settings.mediamtx_api_url}/v3/config/paths/add/{name}", json=body
                )
                if response.status_code == 400:
                    response = await client.patch(
                        f"{settings.mediamtx_api_url}/v3/config/paths/patch/{name}",
                        json=body,
                    )
                response.raise_for_status()
                registered += 1
            except httpx.HTTPError as exc:
                failed += 1
                log.warning("gateway.reconcile_failed", path=name, error=str(exc))

    log.info("gateway.reconciled", registered=registered, failed=failed, skipped=len(rows) - len(wanted))
    return {"registered": registered, "failed": failed, "federated": len(rows) - len(wanted)}
