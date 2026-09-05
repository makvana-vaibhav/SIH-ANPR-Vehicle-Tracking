"""Rate limiting, counted in Redis.

## Why Redis rather than a process-local counter

The scale profile runs two API replicas behind nginx (and production would run
more). A per-process counter would give each replica its own allowance, so the
real limit would be `configured × replicas` and would change whenever the
deployment was scaled — a limit that moves when you add capacity is not a
limit. Redis is already a hard dependency of this service, so counting there
costs no new infrastructure.

## Why a fixed window

A sliding-window log is more precise and costs a sorted set per client plus a
trim on every request. For the thing this is defending against — credential
guessing and accidental client loops — a fixed window is sufficient, and its
worst case (twice the allowance across a window boundary) is not a meaningful
weakness at these thresholds. Precision here would be paying for accuracy
nobody needs.

## Failing open

If Redis is unreachable the request is allowed. That is a deliberate trade:
this platform's job is to be available to a control room during an incident,
and an operator locked out of the alert screen because a cache is down is a
worse outcome than an unthrottled minute. The failure is logged so it cannot
pass unnoticed.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import redis.asyncio as aioredis
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

#: Paths where guessing is the attack. On these, **only failures are counted**.
#:
#: Counting every attempt looked right and was wrong: a control room sits
#: behind one NAT, so an unauthenticated caller is keyed by a shared address,
#: and ten operators signing in at shift change would lock each other out. A
#: successful login is not an attack, so it does not spend the budget. What is
#: being limited is *guessing*, and guessing is failure by definition.
AUTH_PATHS = ("/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/password")

#: Responses that mean the credential was wrong. 422 is included because a
#: malformed body is indistinguishable from a probe.
FAILURE_CODES = frozenset({400, 401, 403, 422})

#: Never limited: the container healthcheck runs every few seconds and a
#: throttled probe would report the service unhealthy and restart it, turning a
#: rate limit into an outage.
EXEMPT_PATHS = ("/health", "/ready", "/metrics")


@dataclass(frozen=True, slots=True)
class Budget:
    requests: int
    window_seconds: int


def budget_for(path: str) -> Budget:
    if path.startswith(AUTH_PATHS):
        return Budget(settings.rate_limit_auth_requests, settings.rate_limit_auth_window_seconds)
    return Budget(settings.rate_limit_requests, settings.rate_limit_window_seconds)


def client_key(request: Request) -> str:
    """Who is being counted.

    The authenticated subject when there is one, so a shared control-room NAT
    does not make every operator share one allowance; the peer address
    otherwise, which is all an unauthenticated caller offers.

    `X-Forwarded-For` is trusted only because nginx sets it and nothing else
    can reach this service — see web/nginx.conf. Exposed directly to the
    internet that header is caller-controlled and this would need revisiting.
    """
    subject = getattr(request.state, "user_id", None)
    if subject:
        return f"user:{subject}"

    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Fixed-window counter, shared across replicas."""

    def __init__(self, app, redis_url: str | None = None) -> None:
        super().__init__(app)
        self._redis_url = redis_url or settings.redis_url
        self._client: aioredis.Redis | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def _redis(self) -> aioredis.Redis:
        """A client bound to the loop currently running.

        redis-py's async pool holds connections tied to the event loop that
        opened them, so a client cached across loops raises on every call. In
        production there is one loop and this never shows; under a test runner
        that gives each test its own loop, every request after the first fails
        open — which is to say the rate limiter silently stops limiting. It
        would have failed the same way behind any server that recycles loops.
        """
        running = asyncio.get_running_loop()
        if self._client is None or self._loop is not running:
            self._client = aioredis.from_url(self._redis_url, decode_responses=True)
            self._loop = running
        return self._client

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if not settings.rate_limit_enabled or path.startswith(EXEMPT_PATHS):
            return await call_next(request)

        budget = budget_for(path)
        window = int(time.time()) // budget.window_seconds
        failures_only = path.startswith(AUTH_PATHS)
        scope = "auth" if failures_only else "api"
        key = f"sentinel:ratelimit:{scope}:{client_key(request)}:{window}"

        try:
            client = await self._redis()
            if failures_only:
                # Read the count; spend the budget afterwards, and only if the
                # attempt turns out to have been a failed one.
                used = int(await client.get(key) or 0)
            else:
                pipe = client.pipeline()
                pipe.incr(key)
                # Expiry is set every time rather than only on creation: a key
                # whose EXPIRE was lost to a failover would otherwise count for
                # ever and lock the caller out permanently.
                pipe.expire(key, budget.window_seconds)
                counted, _ = await pipe.execute()
                used = int(counted)
        except Exception as exc:
            # Fail open, loudly. A control room that cannot reach its alerts
            # because a cache is down is the worse failure.
            log.warning("ratelimit.unavailable", error=str(exc))
            return await call_next(request)

        remaining = max(0, budget.requests - used)
        if used > budget.requests:
            retry_after = budget.window_seconds - (int(time.time()) % budget.window_seconds)
            log.warning(
                "ratelimit.exceeded",
                path=path,
                scope=scope,
                client=client_key(request),
                used=used,
                allowed=budget.requests,
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "detail": (
                        f"Rate limit exceeded: {budget.requests} requests per "
                        f"{budget.window_seconds}s. Retry in {retry_after}s."
                    )
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(budget.requests),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)

        if failures_only and response.status_code in FAILURE_CODES:
            try:
                client = await self._redis()
                pipe = client.pipeline()
                pipe.incr(key)
                pipe.expire(key, budget.window_seconds)
                await pipe.execute()
                remaining = max(0, remaining - 1)
            except Exception as exc:
                log.warning("ratelimit.count_failed", error=str(exc))

        response.headers["X-RateLimit-Limit"] = str(budget.requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
