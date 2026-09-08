"""The audit trail.

CLAUDE.md states the rule this module enforces:

> Every plate search, every camera stream open, and every watchlist mutation
> writes an ``audit_log`` row.

Two mechanisms, deliberately overlapping:

1. **Middleware** (``app/middleware/audit.py``) catches every mutating request
   and every search automatically, so a new endpoint is audited the day it is
   written, without anyone remembering to add a call.
2. **Explicit calls** for the three named actions, which record domain detail
   the middleware cannot see — *which* plate was searched, *which* camera was
   opened.

Failures are swallowed and logged loudly. That is a deliberate trade-off: a
police operator chasing a stolen vehicle must not be blocked because the audit
writer had a transient error. The alternative — failing the request — would
make the audit system an availability risk for the platform it oversees. The
error is logged at ERROR so the gap is visible to whoever reviews the trail.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.models.enums import AuditResult
from app.models.security import AuditLog

if TYPE_CHECKING:
    from app.api.deps import CurrentUser

log = get_logger("api.audit")

#: Request fields never written to the audit trail. Recording a password in the
#: table designed to be read by reviewers would be its own breach.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "jwt_secret_key",
        "mfa_secret",
        "credentials",
        "authorization",
        "api_key",
    }
)

_REDACTED = "***redacted***"

#: Cap on stored parameter payloads. A 5 MB CSV upload should leave a record
#: that it happened, not a copy of itself in the audit table.
_MAX_PARAM_CHARS = 4000


def scrub(params: Any) -> Any:
    """Recursively redact credentials and truncate oversized values."""
    if isinstance(params, dict):
        return {
            key: (_REDACTED if key.lower() in _SENSITIVE_KEYS else scrub(value))
            for key, value in params.items()
        }
    if isinstance(params, list):
        return [scrub(item) for item in params[:50]]
    if isinstance(params, str) and len(params) > _MAX_PARAM_CHARS:
        return f"{params[:_MAX_PARAM_CHARS]}…[truncated {len(params)} chars]"
    return params


def client_ip(request: Request) -> str | None:
    """The caller's address, honouring the proxy chain.

    nginx sits in front of the API and sets X-Forwarded-For; without reading it
    every action would be attributed to the reverse proxy, making the trail
    useless for answering "who, from where".
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Left-most entry is the original client.
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else None


async def record(
    *,
    action: str,
    user: CurrentUser | None = None,
    request: Request | None = None,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    params: dict[str, Any] | None = None,
    result: AuditResult | str = AuditResult.SUCCESS,
    username: str | None = None,
    role: str | None = None,
    user_id: uuid.UUID | None = None,
) -> None:
    """Write one audit row.

    Uses its own short-lived session so the row survives even when the
    surrounding request transaction rolls back — a *denied* or *failed* action
    is precisely the one a reviewer must still be able to see.
    """
    entry = AuditLog(
        user_id=user.id if user else user_id,
        username=user.username if user else username,
        role=user.role if user else role,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        ip=client_ip(request) if request else None,
        user_agent=request.headers.get("user-agent") if request else None,
        params=scrub(params) if params else None,
        result=str(result),
    )

    try:
        async with SessionLocal() as session:
            session.add(entry)
            await session.commit()
    except SQLAlchemyError as exc:
        # Never fail the caller's request because auditing failed — but make
        # the gap unmissable in the logs.
        log.error(
            "audit.write_failed",
            action=action,
            username=entry.username,
            error=str(exc),
            exc_info=True,
        )


# ── The three explicitly-audited actions ──────────────────────────────


async def record_plate_search(
    *,
    request: Request,
    user: CurrentUser,
    query: str,
    filters: dict[str, Any] | None = None,
    result_count: int | None = None,
) -> None:
    """Audit a plate search. Records the query itself, not just that one ran."""
    await record(
        action="search.plate",
        user=user,
        request=request,
        resource_type="detection",
        resource_id=query,
        params={"query": query, "filters": filters or {}, "results": result_count},
    )


async def record_stream_open(
    *,
    request: Request,
    user: CurrentUser,
    camera_id: uuid.UUID | str,
    camera_code: str | None = None,
) -> None:
    """Audit an operator opening a live camera feed.

    Watching a citizen through a government camera is the most privacy-sensitive
    action in the platform, so it is recorded at the moment the viewing token is
    issued rather than trusting the client to report it.
    """
    await record(
        action="camera.view",
        user=user,
        request=request,
        resource_type="camera",
        resource_id=camera_id,
        params={"camera_code": camera_code},
    )


async def record_watchlist_change(
    *,
    request: Request,
    user: CurrentUser,
    operation: str,
    watchlist_id: uuid.UUID | str | None,
    plate: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Audit a watchlist create/update/delete.

    Adding a plate to a watchlist causes automated alerts about a specific
    person's vehicle. Who authorised that, and when, must be answerable.
    """
    await record(
        action=f"watchlist.{operation}",
        user=user,
        request=request,
        resource_type="watchlist",
        resource_id=watchlist_id,
        params={"plate": plate, **(details or {})},
    )


# ── Authentication and authorisation events ───────────────────────────


async def record_login(*, request: Request, user: CurrentUser, success: bool = True) -> None:
    await record(
        action="auth.login" if success else "auth.login_failed",
        user=user,
        request=request,
        resource_type="user",
        resource_id=user.id,
        result=AuditResult.SUCCESS if success else AuditResult.DENIED,
    )


async def record_failed_login(*, request: Request, username: str, reason: str) -> None:
    """Audit a rejected login attempt.

    There is no user object to attribute this to, which is the point: repeated
    failures against a valid username are how credential stuffing looks in the
    trail.
    """
    await record(
        action="auth.login_failed",
        request=request,
        username=username,
        resource_type="user",
        params={"reason": reason},
        result=AuditResult.DENIED,
    )


async def record_permission_denied(
    *, request: Request, user: CurrentUser, missing: list[str]
) -> None:
    """Audit an authorisation failure.

    Called from the RBAC dependencies. An attempt to reach something you are
    not entitled to is exactly what a reviewer is looking for.
    """
    await record(
        action="auth.permission_denied",
        user=user,
        request=request,
        resource_type="endpoint",
        resource_id=request.url.path,
        params={"method": request.method, "missing_permissions": missing},
        result=AuditResult.DENIED,
    )


async def count_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    """How many audit entries name this user.

    Used before deleting an account: removing a user who has acted orphans
    every row that references them, and an audit trail full of unresolvable
    ids is not one an inquiry can use.
    """
    return (
        await session.execute(
            select(func.count()).select_from(AuditLog).where(AuditLog.user_id == user_id)
        )
    ).scalar_one()


async def recent(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    action: str | None = None,
    username: str | None = None,
    result: str | None = None,
    since: datetime | None = None,
) -> tuple[list[AuditLog], int]:
    """Read the trail, newest first.

    Reading the audit log is itself an audited action — recorded by the caller,
    because "who has been reviewing whom" is exactly the question an inquiry
    into misuse would ask, and a reviewer who leaves no trace is a gap in the
    control this table exists to provide.
    """
    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)

    filters = []
    if action:
        filters.append(AuditLog.action.startswith(action))
    if username:
        filters.append(AuditLog.username == username)
    if result:
        filters.append(AuditLog.result == result)
    if since is not None:
        filters.append(AuditLog.ts >= since)

    for clause in filters:
        query = query.where(clause)
        count_query = count_query.where(clause)

    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (await session.execute(query.order_by(AuditLog.ts.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), total
