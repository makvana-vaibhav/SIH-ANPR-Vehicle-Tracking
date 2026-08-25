"""Automatic audit middleware.

Catches every mutating request and every search without the endpoint author
having to remember. Explicit calls in ``app/services/audit.py`` add domain
detail on top for the three actions we specifically promise to record.

Why both: relying only on explicit calls means the audit trail silently
develops holes as the API grows — the endpoint added under time pressure is
exactly the one that forgets. Relying only on middleware means the trail
records "POST /watchlist" without the plate. Together they give complete
coverage plus meaningful detail.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import get_logger
from app.models.enums import AuditResult
from app.services.audit import record

log = get_logger("api.audit.middleware")

#: Methods that change state and are therefore always audited.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Read-only paths that are audited anyway, because looking is the sensitive
#: act: searching for a vehicle, viewing a stream, or reading the audit trail.
_AUDITED_READ_PREFIXES = (
    "/api/v1/search",
    "/api/v1/vehicles",
    "/api/v1/audit",
)

#: Never audited: health probes and docs would drown the trail in noise. Login
#: is excluded here because the auth router records it explicitly, with the
#: outcome and reason attached.
_EXCLUDED_PATHS = frozenset(
    {
        "/health",
        "/ready",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/",
        "/api/v1/auth/login",
        "/api/v1/auth/refresh",
    }
)

#: Maps a path segment to the resource type recorded in the trail.
_RESOURCE_TYPES = {
    "cameras": "camera",
    "watchlist": "watchlist",
    "alerts": "alert",
    "users": "user",
    "search": "detection",
    "vehicles": "vehicle",
    "audit": "audit",
    "departments": "department",
    "vms": "vms_instance",
}


def _should_audit(request: Request) -> bool:
    path = request.url.path
    if path in _EXCLUDED_PATHS:
        return False
    if request.method in _MUTATING_METHODS:
        return True
    return any(path.startswith(prefix) for prefix in _AUDITED_READ_PREFIXES)


def _derive_action(request: Request) -> tuple[str, str | None, str | None]:
    """Turn a request into ``(action, resource_type, resource_id)``.

    ``POST /api/v1/cameras``          → ("camera.create", "camera", None)
    ``DELETE /api/v1/watchlist/{id}`` → ("watchlist.delete", "watchlist", "{id}")
    ``GET /api/v1/search/plates``     → ("search.read", "detection", None)
    """
    segments = [s for s in request.url.path.split("/") if s]
    # Strip the /api/v1 prefix.
    if len(segments) >= 2 and segments[0] == "api":
        segments = segments[2:]

    resource_segment = segments[0] if segments else "unknown"
    resource_type = _RESOURCE_TYPES.get(resource_segment, resource_segment)

    # A second segment that is not a known sub-route is treated as an id.
    resource_id: str | None = None
    if len(segments) >= 2 and not segments[1].isalpha():
        resource_id = segments[1]

    verb = {
        "POST": "create",
        "PUT": "update",
        "PATCH": "update",
        "DELETE": "delete",
        "GET": "read",
    }.get(request.method, request.method.lower())

    return f"{resource_segment}.{verb}", resource_type, resource_id


class AuditMiddleware(BaseHTTPMiddleware):
    """Writes an ``audit_log`` row for every mutating request and every search."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not _should_audit(request):
            return await call_next(request)

        # Read query params before the handler runs; the body is deliberately
        # not consumed here — doing so would require buffering and re-injecting
        # the stream for every upload, and endpoints that need body detail call
        # the audit service directly.
        query_params = dict(request.query_params)

        response = await call_next(request)

        # Authentication happens inside the route, so the user is only known
        # afterwards — get_current_user stashes it on request.state.
        user = getattr(request.state, "current_user", None)

        if response.status_code == 401:
            # Unauthenticated attempts are recorded by the auth layer with
            # their reason; re-recording here would duplicate them.
            return response
        if response.status_code == 403:
            # Recorded in detail by the RBAC dependency.
            return response

        result = AuditResult.SUCCESS if response.status_code < 400 else AuditResult.ERROR

        action, resource_type, resource_id = _derive_action(request)

        await record(
            action=action,
            user=user,
            request=request,
            resource_type=resource_type,
            resource_id=resource_id,
            params={"method": request.method, "query": query_params}
            if query_params
            else {"method": request.method},
            result=result,
        )

        return response
