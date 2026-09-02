"""The audit trail, read by those authorised to read it.

`AUDIT_READ` has existed as a permission since Phase 1 with nothing behind it,
which made it a control on paper. This is the endpoint it was always gating.

**Reading the trail is itself audited.** "Who has been reviewing whom" is
precisely the question an inquiry into misuse would ask, and a reviewer who
leaves no trace is a hole in the control this table exists to provide. That
includes administrators: there is no role here that reads without being seen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import CurrentUserDep, DbSession
from app.core.rbac import Permission, require_permission
from app.schemas.auth import AuditEntry, AuditPage
from app.services import audit

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

#: Unbounded reads of an append-only table that grows with every request are a
#: good way to take the API down; a week is what a reviewer usually wants.
DEFAULT_WINDOW = timedelta(days=7)


@router.get(
    "",
    response_model=AuditPage,
    dependencies=[Depends(require_permission(Permission.AUDIT_READ))],
    summary="Read the audit trail",
)
async def read_audit(
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    action: Annotated[str | None, Query(description="Prefix, e.g. `watchlist.`")] = None,
    username: Annotated[str | None, Query()] = None,
    result: Annotated[str | None, Query(description="success | denied | failure")] = None,
    since: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPage:
    window = since or (datetime.now(UTC) - DEFAULT_WINDOW)
    rows, total = await audit.recent(
        session,
        limit=limit,
        offset=offset,
        action=action,
        username=username,
        result=result,
        since=window,
    )

    await audit.record(
        action="audit.read",
        user=user,
        request=request,
        resource_type="audit_log",
        params={
            "filters": {"action": action, "username": username, "result": result},
            "since": window.isoformat(),
            "returned": len(rows),
        },
    )

    return AuditPage(
        items=[AuditEntry.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
