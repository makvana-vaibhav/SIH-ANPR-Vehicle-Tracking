"""Role-based access control.

The matrix below is the authorisation contract for the whole platform. It is
declared once, in data, so that it can be read, reviewed, and tested as a unit —
rather than scattered across endpoint decorators where nobody can answer "what
can an analyst actually do?" without grepping.

The matrix, as specified:

| Role       | Cameras | Streams | Watchlist | Alerts    | Search | Analytics | Users | Audit |
|------------|---------|---------|-----------|-----------|--------|-----------|-------|-------|
| admin      | CRUD    | view    | CRUD      | all       | yes    | read      | CRUD  | read  |
| supervisor | CRU     | view    | CRU       | ack/close | yes    | read      | –     | read  |
| operator   | read    | view    | read      | ack       | yes    | read      | –     | –     |
| analyst    | read    | –       | read      | read      | yes    | read      | –     | –     |
| auditor    | read    | –       | read      | read      | –      | read      | –     | read  |
| api_client | create  | –       | –         | –         | –      | –         | –     | –     |

Three entries deserve explanation:

* **auditor has no search.** An auditor's job is to review *who did what*, not
  to look up vehicles. Granting plate search to the role that oversees plate
  search would defeat the point of separating them.
* **auditor does have analytics.** Unlike search, traffic analytics identifies
  nobody: it reports how many vehicles crossed a junction and how fast the road
  is moving. There is no separation-of-duties reason to withhold it, and an
  auditor reviewing a surge of plate searches benefits from seeing whether the
  road was actually busy.
* **api_client can only create cameras.** It exists so another department can
  self-register its estate with an API key. It cannot read the fleet, search,
  or see alerts — a compromised integration key exposes nothing.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.api.deps import CurrentUser, get_current_user
from app.models.enums import Role
from app.services.audit import record_permission_denied


class Permission(StrEnum):
    """A single grant, named ``resource.action``."""

    # Cameras / registry
    CAMERA_CREATE = "camera.create"
    CAMERA_READ = "camera.read"
    CAMERA_UPDATE = "camera.update"
    CAMERA_DELETE = "camera.delete"

    # Live video
    STREAM_VIEW = "stream.view"

    # Watchlist
    WATCHLIST_CREATE = "watchlist.create"
    WATCHLIST_READ = "watchlist.read"
    WATCHLIST_UPDATE = "watchlist.update"
    WATCHLIST_DELETE = "watchlist.delete"

    # Alerts
    ALERT_READ = "alert.read"
    ALERT_ACKNOWLEDGE = "alert.acknowledge"
    ALERT_DISPATCH = "alert.dispatch"
    ALERT_CLOSE = "alert.close"

    # Search and intelligence
    SEARCH_EXECUTE = "search.execute"

    # City traffic analytics. Separate from SEARCH_EXECUTE because it answers a
    # different question about different subjects: search follows one vehicle
    # and is audited per query, analytics reports aggregate road conditions and
    # identifies nobody.
    ANALYTICS_READ = "analytics.read"

    # User administration
    USER_CREATE = "user.create"
    USER_READ = "user.read"
    USER_UPDATE = "user.update"
    USER_DELETE = "user.delete"

    # Audit trail
    AUDIT_READ = "audit.read"


_CAMERA_RW = {Permission.CAMERA_CREATE, Permission.CAMERA_READ, Permission.CAMERA_UPDATE}
_WATCHLIST_RW = {
    Permission.WATCHLIST_CREATE,
    Permission.WATCHLIST_READ,
    Permission.WATCHLIST_UPDATE,
}
_ALERT_ALL = {
    Permission.ALERT_READ,
    Permission.ALERT_ACKNOWLEDGE,
    Permission.ALERT_DISPATCH,
    Permission.ALERT_CLOSE,
}

#: The authorisation matrix. Single source of truth.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(Permission),  # every grant, by construction
    Role.SUPERVISOR: frozenset(
        _CAMERA_RW
        | _WATCHLIST_RW
        | _ALERT_ALL
        | {
            Permission.STREAM_VIEW,
            Permission.SEARCH_EXECUTE,
            Permission.AUDIT_READ,
            Permission.ANALYTICS_READ,
        }
    ),
    Role.OPERATOR: frozenset(
        {
            Permission.CAMERA_READ,
            Permission.STREAM_VIEW,
            Permission.WATCHLIST_READ,
            Permission.ALERT_READ,
            Permission.ALERT_ACKNOWLEDGE,
            Permission.SEARCH_EXECUTE,
            Permission.ANALYTICS_READ,
        }
    ),
    Role.ANALYST: frozenset(
        {
            Permission.CAMERA_READ,
            Permission.WATCHLIST_READ,
            Permission.ALERT_READ,
            Permission.SEARCH_EXECUTE,
            Permission.ANALYTICS_READ,
        }
    ),
    Role.AUDITOR: frozenset(
        {
            Permission.CAMERA_READ,
            Permission.WATCHLIST_READ,
            Permission.ALERT_READ,
            Permission.AUDIT_READ,
            Permission.ANALYTICS_READ,
        }
    ),
    # Machine identity for departmental self-registration. Nothing else.
    Role.API_CLIENT: frozenset({Permission.CAMERA_CREATE}),
}


def permissions_for(role: Role | str) -> frozenset[Permission]:
    """Grants held by a role. Unknown roles get nothing."""
    try:
        return ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return frozenset()


def role_has(role: Role | str, permission: Permission) -> bool:
    """Whether a role holds a specific grant."""
    return permission in permissions_for(role)


# ── FastAPI dependencies ──────────────────────────────────────────────
#
# CurrentUser and get_current_user are imported at MODULE level, not inside the
# factories. This file uses `from __future__ import annotations`, so FastAPI
# resolves each dependency's type hints against module globals — a name imported
# inside the factory is invisible there, and FastAPI silently degrades the
# parameter into a required query parameter instead. That failure mode is quiet
# and confusing, so keep these imports at the top.


def require_permission(*required: Permission):
    """Dependency requiring **all** of the given permissions.

    Denials are recorded in the audit trail before the 403 is raised: an
    attempt to reach something you are not entitled to is exactly the event a
    reviewer needs to see.
    """

    async def _check(
        request: Request,
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        granted = permissions_for(user.role)
        missing = [p for p in required if p not in granted]
        if missing:
            await record_permission_denied(
                request=request, user=user, missing=[p.value for p in missing]
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{user.role}' lacks required permission(s): "
                    f"{', '.join(p.value for p in missing)}"
                ),
            )
        return user

    return _check


def require_role(*allowed: Role):
    """Dependency restricting an endpoint to specific roles.

    Prefer ``require_permission`` — it says *why* access is granted rather than
    *who* is granted it, and survives the addition of new roles. This exists for
    the few endpoints that are genuinely role-shaped, such as user administration.
    """
    allowed_values = {r.value for r in allowed}

    async def _check(
        request: Request,
        user: Annotated[CurrentUser, Depends(get_current_user)],
    ) -> CurrentUser:
        if user.role not in allowed_values:
            await record_permission_denied(
                request=request, user=user, missing=[f"role:{'|'.join(sorted(allowed_values))}"]
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{user.role}' is not permitted here; "
                    f"requires one of: {', '.join(sorted(allowed_values))}"
                ),
            )
        return user

    return _check
