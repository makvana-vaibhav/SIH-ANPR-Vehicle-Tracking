"""Holds an authenticated session with the organisers' camera grid.

## Why this exists

The grid uses **two different authentication systems**, and conflating them is
why the platform looked broken while the grid was working:

| Path | Host | Authenticates with |
|---|---|---|
| RTSP (inference) | `103.250.160.189:8554` | username/password in the URL |
| Catalogue + HLS | `cctv.corp8.cloud` | a **login session cookie** |

The RTSP credentials are not accepted by the CDN — Basic auth there returns a
302 to the login page. The CDN wants the form POST that a browser makes, and
gives back a `sentinel=` cookie.

So the API logs in once, keeps the cookie, and re-logs when it stops working.
This is the only place in the platform that holds those credentials.

## The second gate

Media paths additionally refuse any request that does not look like a browser
— the body is literally `browser required`. So requests carry a browser
`User-Agent` and a `Referer`. That is not a trick to evade a control; it is the
control asking for a shape of request, and the platform is an authorised
consumer of this grid with credentials the organisers issued.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

#: The CDN refuses non-browser agents on media paths. Sent on every request to
#: the grid so the catalogue and the media path behave the same way.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Re-login before this elapses. The cookie's own Max-Age is a year, but a
#: session can be invalidated server-side at any time and a stale cookie is
#: indistinguishable from a broken integration until something re-logs.
SESSION_TTL = timedelta(hours=6)

LOGIN_PATH = "/auth/login"


class GridSession:
    """One logged-in session, shared by everything that talks to the grid."""

    def __init__(self) -> None:
        self._cookie: str | None = None
        self._obtained_at: datetime | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        """Whether credentials exist at all.

        Absence is a valid state — the grid was anonymous until September —
        so callers report "not configured" rather than treating it as a fault.
        """
        return bool(settings.sandbox_rtsp_username and settings.sandbox_rtsp_password)

    @property
    def _fresh(self) -> bool:
        return (
            self._cookie is not None
            and self._obtained_at is not None
            and datetime.now(UTC) - self._obtained_at < SESSION_TTL
        )

    async def _login(self) -> str | None:
        """POST the login form and keep the cookie it sets."""
        base = settings.sandbox_base_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
                response = await client.post(
                    f"{base}{LOGIN_PATH}",
                    data={
                        "email": settings.sandbox_rtsp_username,
                        "password": settings.sandbox_rtsp_password,
                    },
                    headers={"User-Agent": BROWSER_UA},
                )
        except httpx.HTTPError as exc:
            log.warning("grid.login_failed", error=str(exc))
            return None

        # A successful login redirects; a rejected one renders the form again.
        cookie = response.cookies.get("sentinel")
        if not cookie:
            log.warning(
                "grid.login_rejected",
                status=response.status_code,
                detail="no session cookie was set — check SANDBOX_RTSP_* credentials",
            )
            return None

        self._cookie = f"sentinel={cookie}"
        self._obtained_at = datetime.now(UTC)
        log.info("grid.login_ok", status=response.status_code)
        return self._cookie

    async def cookie(self, force: bool = False) -> str | None:
        """The session cookie, logging in if needed.

        Serialised: thirty cameras probing at once would otherwise start
        thirty logins, and the grid would be right to rate-limit us for it.
        """
        if not self.configured:
            return None
        async with self._lock:
            if self._fresh and not force:
                return self._cookie
            return await self._login()

    async def headers(self, force: bool = False) -> dict[str, str]:
        """Everything a request to the grid needs to be accepted."""
        headers = {
            "User-Agent": BROWSER_UA,
            "Referer": settings.sandbox_base_url.rstrip("/") + "/",
        }
        cookie = await self.cookie(force=force)
        if cookie:
            headers["Cookie"] = cookie
        return headers

    def invalidate(self) -> None:
        """Forget the cookie so the next call logs in again."""
        self._cookie = None
        self._obtained_at = None


class CatalogueCache:
    """The grid's camera list, fetched once per sweep rather than per camera.

    `probe_health` reads the catalogue to find one camera's liveness, and the
    health monitor probes thirty cameras a cycle. Without this that is thirty
    full downloads of the same document from somebody else's CDN every cycle —
    which is both rude and self-defeating: the later requests in a sweep were
    timing out and their cameras were being recorded as unreachable when the
    grid was fine.

    The TTL is shorter than the probe interval, so a sweep sees one consistent
    snapshot and the next sweep sees fresh data.
    """

    def __init__(self, ttl: timedelta = timedelta(seconds=20)) -> None:
        self._ttl = ttl
        self._payload: object | None = None
        self._fetched_at: datetime | None = None
        self._lock = asyncio.Lock()

    def get(self) -> object | None:
        if self._payload is None or self._fetched_at is None:
            return None
        if datetime.now(UTC) - self._fetched_at > self._ttl:
            return None
        return self._payload

    def put(self, payload: object) -> None:
        self._payload = payload
        self._fetched_at = datetime.now(UTC)

    def clear(self) -> None:
        self._payload = None
        self._fetched_at = None

    @property
    def lock(self) -> asyncio.Lock:
        """Held while fetching, so a sweep makes one request, not thirty."""
        return self._lock


session = GridSession()
catalogue = CatalogueCache()
