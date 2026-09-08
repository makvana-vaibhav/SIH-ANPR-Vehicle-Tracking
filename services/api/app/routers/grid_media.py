"""Serves the federated grid's video to the browser, authenticated.

## Why the browser cannot fetch it directly

The grid's HLS sits behind a login on the organisers' CDN. Our command centre
runs on a different origin, so a browser opening one of their playlists sends
no session cookie, is redirected to a login page, and the player reports "no
video is being published" — which is what the operator saw, and which is not
what happened.

Handing the browser those credentials is not an option: they would be readable
by anyone with the developer tools open, and they are credentials for a
government camera network.

So the API fetches on the browser's behalf. It already holds the session
(`app/services/grid_session.py`) and is already the platform's authentication
boundary. The browser presents the same short-lived, camera-scoped stream token
it uses for every other feed; the grid's credentials never leave the server.

## Why the token is in the path

A player resolves `seg00123.ts` against the *path* of the playlist it came
from, and drops the query string doing so. With the token in `?token=`, the
playlist loaded and every segment then arrived unauthenticated — a 422 that
looked like a malformed request and was really a lost credential.

Putting it in the path means relative resolution carries it for free, and the
playlist needs no per-segment rewriting: 7,200 segments each carrying a JWT
would be a two-megabyte playlist.

The AES key is still rewritten, because `/enc.key` is an absolute path that
would otherwise resolve against our origin and 404.
"""

from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.adapters.sandbox import _external_id_of
from app.api.deps import DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import TokenError, decode_token
from app.models.registry import Camera
from app.services import grid_session

router = APIRouter(prefix="/api/v1/grid", tags=["streams"])
log = get_logger(__name__)

#: Content types we hand back unchanged. Anything else is refused rather than
#: relayed: this endpoint exists to serve one grid's media, not to be a general
#: purpose fetcher for whatever a caller can name.
PLAYLIST_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl")

#: Only these suffixes are proxied. A path traversal or an attempt to reach the
#: grid's own API through this endpoint gets a 400.
ALLOWED_SUFFIXES = (".m3u8", ".ts", ".m4s", ".mp4", ".key", ".aac")

#: What each suffix really is. The CDN labels `.ts` segments
#: `text/vnd.trolltech.linguist` — a genuine mis-detection, and one Safari's
#: native HLS player refuses to decode. hls.js reads segments as an array
#: buffer and would not have noticed, which is how this survives unspotted
#: until somebody demonstrates on a Mac.
MEDIA_TYPES = {
    ".ts": "video/mp2t",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
    ".aac": "audio/aac",
    ".key": "application/octet-stream",
}


async def _fetch(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> httpx.Response:
    """One GET, retried once if the session turns out to have lapsed.

    A redirect here means the login expired. Following it would hand the
    operator an HTML login page with a 200 status, which a video element
    reports as a decode failure — a confusing way to say "log in again".
    """
    response = await client.send(client.build_request("GET", url, headers=headers), stream=True)
    if response.status_code not in (301, 302, 303, 307, 308):
        return response

    await response.aclose()
    grid_session.session.invalidate()
    fresh = await grid_session.session.headers(force=True)
    return await client.send(client.build_request("GET", url, headers=fresh), stream=True)


async def _camera_for_token(session: DbSession, token: str) -> Camera:
    """The camera this stream token authorises, or a 403."""
    try:
        payload = decode_token(token, expected_type="stream")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid or expired stream token"
        ) from exc

    if not payload.camera_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Token is not camera-scoped"
        )

    camera = (
        await session.execute(select(Camera).where(Camera.id == uuid.UUID(payload.camera_id)))
    ).scalar_one_or_none()
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such camera")
    return camera


def _rewrite_playlist(body: str, camera_id: str, token: str) -> str:
    """Point the key URI back through this proxy.

    Only the key needs it — segments are relative and inherit the token from
    the playlist's own path.
    """
    return body.replace(
        'URI="/enc.key"',
        f'URI="/api/v1/grid/{camera_id}/{token}/enc.key"',
    )


@router.get(
    "/{camera_id}/{token}/{path:path}",
    summary="Proxy the federated grid's HLS to an authorised viewer",
)
async def grid_media(
    camera_id: uuid.UUID,
    token: str,
    path: str,
    session: DbSession,
    request: Request,
) -> Response:
    camera = await _camera_for_token(session, token)
    if camera.id != camera_id:
        # The token names a different camera. Scope is the point of the token.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This token does not authorise that camera",
        )

    if not path.endswith(ALLOWED_SUFFIXES) or ".." in path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Not a media path")

    grid_id = _external_id_of(camera)
    base = settings.sandbox_base_url.rstrip("/")
    # `enc.key` sits at the root, not under the camera.
    upstream = f"{base}/enc.key" if path == "enc.key" else f"{base}/{grid_id}/{path}"

    headers = await grid_session.session.headers()
    if "Cookie" not in headers:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No session with the camera grid. Set SANDBOX_RTSP_USERNAME and "
                "SANDBOX_RTSP_PASSWORD, then restart the API."
            ),
        )

    client = httpx.AsyncClient(timeout=30.0, follow_redirects=False)
    try:
        response = await _fetch(client, upstream, headers)
    except httpx.HTTPError as exc:
        await client.aclose()
        log.warning("grid.media_failed", camera=camera.camera_code, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach the grid: {exc}",
        ) from exc

    if response.status_code != 200:
        body = (await response.aread()).decode("utf-8", "replace")[:200]
        await response.aclose()
        await client.aclose()
        log.warning(
            "grid.media_refused",
            camera=camera.camera_code,
            path=path,
            status=response.status_code,
            detail=body,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The grid refused this stream ({response.status_code}): {body}",
        )

    try:
        suffix = path[path.rfind(".") :] if "." in path else ""
        content_type = MEDIA_TYPES.get(
            suffix, response.headers.get("content-type", "application/octet-stream")
        )

        # A playlist is small and needs rewriting, so it is read whole.
        if path.endswith(".m3u8") or content_type.lower().startswith(PLAYLIST_TYPES):
            body = (await response.aread()).decode("utf-8", "replace")
            await response.aclose()
            await client.aclose()
            return Response(
                content=_rewrite_playlist(body, str(camera_id), token),
                media_type="application/vnd.apple.mpegurl",
                headers={"Cache-Control": "no-store"},
            )

        # Segments and keys are streamed. A 7,200-segment playlist is not
        # something to buffer, and the client is a video element that wants
        # bytes as they arrive.
        async def body_stream():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return StreamingResponse(
            body_stream(),
            media_type=content_type,
            headers={"Cache-Control": "no-store"},
        )
    except httpx.HTTPError as exc:
        await response.aclose()
        await client.aclose()
        log.warning("grid.media_failed", camera=camera.camera_code, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach the grid: {exc}",
        ) from exc
