"""Live event feed for the command centre.

    ws://<api>/ws/events

Streams vehicle events from the AI workers to the operator's browser as they
happen. Two kinds arrive, and the client should treat them differently:

  vehicle.observed   a vehicle still in view, current best reading. Use it to
                     react now — this is what makes a watchlist hit an alert
                     rather than a history entry.
  vehicle.completed  the vehicle has left and consensus is settled. This is the
                     one that was written to the database.

Authentication is by query-string token, not header: browsers give no way to
set headers on a WebSocket handshake. The token is the same short-lived access
token every other endpoint takes.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core.logging import get_logger
from app.core.security import TokenError, TokenPayload, decode_token
from app.services.event_bus import broadcaster
from app.services.token_store import is_revoked

log = get_logger("events")

router = APIRouter(tags=["events"])

# How long to wait for an event before sending a keepalive. Proxies and load
# balancers close idle WebSockets, and an alert feed is idle most of the time —
# which is exactly when it must stay connected.
KEEPALIVE_SECONDS = 20.0


async def _authorise(token: str | None) -> TokenPayload | None:
    """Validate the access token supplied on the query string.

    Revocation is checked as well as the signature: a logged-out operator whose
    token has not yet expired must not keep receiving the live feed, and the
    feed is long-lived enough for that gap to matter.
    """
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
    except TokenError:
        return None

    if await is_revoked(payload.jti):
        return None
    return payload


@router.websocket("/ws/events")
async def event_feed(websocket: WebSocket, token: str | None = Query(default=None)) -> None:
    claims = await _authorise(token)
    if claims is None:
        # Closed before accept, so an unauthenticated client never receives a
        # single event.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid or missing token")
        return

    await websocket.accept()
    queue = await broadcaster.subscribe()
    subject = claims.username or claims.subject
    log.info("events.connected", user=subject, subscribers=broadcaster.subscriber_count)

    try:
        await websocket.send_json(
            {"event": "connected", "subscribers": broadcaster.subscriber_count}
        )
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                # Nothing happened. Say so, rather than letting an idle feed be
                # culled by something in the middle.
                await websocket.send_json({"event": "keepalive"})
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        log.info("events.disconnected", user=subject)
    except (RuntimeError, ConnectionError) as exc:
        # The socket went away mid-send. Normal for a closed browser tab.
        log.info("events.dropped", user=subject, reason=str(exc))
    finally:
        await broadcaster.unsubscribe(queue)
        with contextlib.suppress(RuntimeError):
            await websocket.close()
