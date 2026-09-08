"""Refresh-token revocation.

Access tokens are short-lived and not individually revocable — that is the
accepted trade-off for stateless authorisation. Refresh tokens are long-lived,
so they *are* revocable: logging out adds the token's ``jti`` to a Redis
denylist, and any attempt to use it afterwards fails.

Entries expire automatically at the token's own expiry, so the denylist stays
bounded no matter how many sessions come and go.
"""

from __future__ import annotations

from datetime import UTC, datetime

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("api.tokens")

_KEY_PREFIX = "nagarnetra:revoked_jti:"

_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    """Lazily create the shared Redis client."""
    global _client
    if _client is None:
        _client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
    return _client


async def revoke(jti: str, expires_at: datetime) -> None:
    """Deny future use of a token, until it would have expired anyway."""
    ttl = int((expires_at - datetime.now(UTC)).total_seconds())
    if ttl <= 0:
        # Already expired; nothing to revoke.
        return
    try:
        await _redis().setex(f"{_KEY_PREFIX}{jti}", ttl, "1")
    except aioredis.RedisError as exc:
        # Logout must not appear to fail, but an un-revoked refresh token is a
        # real security gap, so log it as an error.
        log.error("token.revoke_failed", jti=jti, error=str(exc), exc_info=True)


async def is_revoked(jti: str) -> bool:
    """Whether a token has been revoked.

    **Fails closed.** If Redis is unreachable we cannot prove a token is still
    valid, so it is treated as revoked. The caller re-authenticates with their
    password — a minor inconvenience, versus honouring a token that an
    administrator believes they cancelled.
    """
    try:
        return await _redis().exists(f"{_KEY_PREFIX}{jti}") == 1
    except aioredis.RedisError as exc:
        log.error("token.revocation_check_failed", jti=jti, error=str(exc), exc_info=True)
        return True


async def close() -> None:
    """Release the client on application shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
