"""Stream gateway: issuing viewing access to a camera.

Opening a live feed is the most privacy-sensitive routine action in this
platform — it is a government employee watching a public place in real time. So
it is deliberately not a static URL:

* access is granted per request, as a **signed token scoped to one camera**,
  valid for ~2 minutes;
* **every issuance writes an audit row** (`camera.view`), which is one of the
  three actions CLAUDE.md promises to record;
* the token is verified before the gateway will serve the stream.

A viewing URL that leaks from browser history or a screen-share is therefore
useless within minutes, and cannot be repointed at a different camera.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.adapters import adapter_for
from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.core.rbac import Permission, require_permission
from app.core.security import TokenError, create_stream_token, decode_token
from app.core.urls import redact
from app.services import audit
from app.services import camera as camera_service

log = get_logger("api.streams")

router = APIRouter(prefix="/api/v1", tags=["streams"])


class StreamGrant(BaseModel):
    """A short-lived grant to watch one camera."""

    camera_id: uuid.UUID
    camera_code: str
    name: str
    status: str
    token: str = Field(description="Camera-scoped viewing token")
    expires_in: int = Field(description="Token lifetime in seconds")

    whep_url: str | None = Field(
        default=None, description="WebRTC (WHEP) — primary, sub-second latency"
    )
    hls_url: str | None = Field(default=None, description="HLS — fallback for restrictive networks")
    rtsp_url: str | None = Field(default=None, description="RTSP — for AI workers, not browsers")

    protocol_preference: list[str] = Field(
        default_factory=lambda: ["whep", "hls"],
        description="Try these in order; WHEP first, HLS when WebRTC is blocked",
    )


@router.get(
    "/cameras/{camera_id}/stream",
    response_model=StreamGrant,
    summary="Request viewing access to a camera",
)
async def open_stream(
    camera_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.STREAM_VIEW))],
) -> StreamGrant:
    """Issue a short-lived, camera-scoped viewing token.

    Requires `stream.view`, which analysts and auditors deliberately do not
    hold — they work over recorded detections, not live video.

    The stream URL is resolved through the camera's VMS adapter at request
    time rather than stored, because vendor URLs often carry their own
    short-lived session tokens, and because pulling a feed only while someone
    is watching is the platform's central bandwidth argument.
    """
    camera = await camera_service.get_camera(session, camera_id)
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    adapter = adapter_for(camera.vms)
    try:
        endpoints = await adapter.get_stream_url(camera)
    except Exception as exc:
        log.warning(
            "stream.resolve_failed",
            camera_code=camera.camera_code,
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not resolve a stream for {camera.camera_code}: {exc}",
        ) from exc
    finally:
        await adapter.close()

    token = create_stream_token(user_id=user.id, camera_id=camera.id, username=user.username)

    # One of the three explicitly audited actions. Recorded at issuance rather
    # than trusting the client to report that it started watching.
    await audit.record_stream_open(
        request=request,
        user=user,
        camera_id=camera.id,
        camera_code=camera.camera_code,
    )
    log.info(
        "stream.opened",
        camera_code=camera.camera_code,
        by=user.username,
        adapter=adapter.adapter_type,
    )

    # A federated camera's HLS sits behind the grid's own login, which the
    # browser has no session for. Point it at our authenticated proxy instead;
    # the grid's credentials stay on the server. See routers/grid_media.py.
    hls_url = endpoints.hls
    if adapter.adapter_type == "hosted_grid" and hls_url:
        hls_url = f"/api/v1/grid/{camera.id}/{token}/index.m3u8"

    # The RTSP URL carries the grid's username and password — RTSP has nowhere
    # else to put them. It is returned for operator diagnosis and no browser
    # can play it, so the credentials are stripped before it leaves the server.
    # Without this the whole point of proxying HLS is undone by the field
    # directly beneath it.
    rtsp_url = redact(endpoints.rtsp) if endpoints.rtsp else None

    return StreamGrant(
        camera_id=camera.id,
        camera_code=camera.camera_code,
        name=camera.name,
        status=camera.status,
        token=token,
        expires_in=settings.stream_token_expire_seconds,
        whep_url=endpoints.whep,
        hls_url=hls_url,
        rtsp_url=rtsp_url,
    )


@router.get(
    "/streams/verify",
    summary="Verify a stream token (called by the gateway)",
)
async def verify_stream_token(
    token: Annotated[str, Query(description="Camera-scoped viewing token")],
    camera_id: Annotated[uuid.UUID | None, Query()] = None,
) -> dict[str, Any]:
    """Validate a viewing token.

    Deliberately unauthenticated: it is called by the media gateway, which has
    only the token itself. It grants nothing — it merely reports whether a
    token is currently valid and which camera it is scoped to.
    """
    try:
        payload = decode_token(token, expected_type="stream")
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    # A token for camera A must never authorise camera B.
    if camera_id is not None and payload.camera_id != str(camera_id):
        log.warning(
            "stream.token_camera_mismatch",
            requested=str(camera_id),
            token_scope=payload.camera_id,
            username=payload.username,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This token is scoped to a different camera",
        )

    return {
        "valid": True,
        "camera_id": payload.camera_id,
        "username": payload.username,
        "expires_at": payload.expires_at.isoformat(),
    }
