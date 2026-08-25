"""Shared FastAPI dependencies: authentication and database sessions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import TokenError, decode_token
from app.db.session import get_session
from app.models.security import User

log = get_logger("api.auth")

# auto_error=False so a missing header produces our own 401 with a useful
# message, rather than FastAPI's bare "Not authenticated".
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

DbSession = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The authenticated caller.

    A value object rather than the ORM row: it cannot be mutated by accident,
    cannot lazily emit SQL from inside a request handler, and is safe to keep
    in the audit context after the session closes.
    """

    id: uuid.UUID
    username: str
    role: str
    department_id: uuid.UUID | None
    is_active: bool

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _unauthorised(detail: str) -> HTTPException:
    """401 with the WWW-Authenticate header the spec requires."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> CurrentUser:
    """Resolve and validate the caller from the Authorization header.

    The user record is re-read on every request rather than trusted wholesale
    from the token. That costs one indexed primary-key lookup and buys
    immediate revocation: deactivating an account takes effect on the next
    request, not whenever their 15-minute access token happens to expire.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorised("Missing bearer token")

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except TokenError as exc:
        # Logged at info, not warning: expired tokens are routine, and a noisy
        # log here would bury genuine attacks.
        log.info("auth.token_rejected", reason=str(exc), path=request.url.path)
        raise _unauthorised(str(exc)) from exc

    user_id = payload.user_id
    if user_id is None:
        raise _unauthorised("Token subject is not a valid user id")

    user = await session.scalar(select(User).where(User.id == user_id))

    if user is None:
        # The account was deleted after the token was issued.
        log.warning("auth.user_not_found", user_id=str(user_id))
        raise _unauthorised("User no longer exists")

    if not user.is_active:
        log.warning("auth.user_inactive", username=user.username)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated",
        )

    current = CurrentUser(
        id=user.id,
        username=user.username,
        role=user.role,
        department_id=user.department_id,
        is_active=user.is_active,
    )

    # Stash on request.state so the audit middleware can attribute the action
    # without repeating the lookup.
    request.state.current_user = current
    return current


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


async def get_optional_user(
    request: Request,
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> CurrentUser | None:
    """Resolve the caller if a valid token is present, else ``None``.

    For endpoints that serve both authenticated and anonymous callers while
    still attributing the action when it can.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_user(request, session, credentials)
    except HTTPException:
        return None
