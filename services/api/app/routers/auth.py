"""Authentication endpoints: login, refresh, logout, profile, password change."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, CurrentUserDep, DbSession
from app.core.config import settings
from app.core.logging import get_logger
from app.core.rbac import permissions_for
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models.security import User
from app.schemas.auth import (
    LoginRequest,
    MessageResponse,
    PasswordChangeRequest,
    RefreshRequest,
    TokenPair,
    UserProfile,
)
from app.services import audit, token_store

log = get_logger("api.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# A precomputed argon2 hash of a random value. Verifying against it when a
# username does not exist makes a missing account cost the same as a wrong
# password, so response timing cannot be used to enumerate valid usernames.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


def _token_pair(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(
            user_id=user.id,
            username=user.username,
            role=user.role,
            department_id=user.department_id,
        ),
        refresh_token=create_refresh_token(user_id=user.id, username=user.username),
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange credentials for an access and refresh token pair",
)
async def login(payload: LoginRequest, request: Request, session: DbSession) -> TokenPair:
    """Authenticate a user.

    Every outcome — success, unknown user, wrong password, deactivated account —
    is written to the audit trail. Repeated failures against a valid username
    are what credential stuffing looks like to a reviewer.
    """
    user = await session.scalar(select(User).where(User.username == payload.username))

    if user is None:
        # Spend the same time as a real verification (see _DUMMY_HASH).
        verify_password(payload.password, _DUMMY_HASH)
        await audit.record_failed_login(
            request=request, username=payload.username, reason="unknown_user"
        )
        # The client is told only that the pair was wrong, never which half.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    if not verify_password(payload.password, user.password_hash):
        await audit.record_failed_login(
            request=request, username=payload.username, reason="bad_password"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    if not user.is_active:
        await audit.record_failed_login(
            request=request, username=payload.username, reason="inactive_account"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated",
        )

    # Transparently upgrade the hash if argon2 parameters have been raised.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        log.info("auth.password_rehashed", username=user.username)

    user.last_login_at = datetime.now(UTC)
    await session.flush()

    current = CurrentUser(
        id=user.id,
        username=user.username,
        role=user.role,
        department_id=user.department_id,
        is_active=user.is_active,
    )
    await audit.record_login(request=request, user=current, success=True)
    log.info("auth.login", username=user.username, role=user.role)

    return _token_pair(user)


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Exchange a refresh token for a new token pair",
)
async def refresh(payload: RefreshRequest, request: Request, session: DbSession) -> TokenPair:
    """Issue a new token pair.

    Role and department are re-read from the database rather than carried over
    from the old token, so a user demoted mid-session cannot retain their
    previous authority by refreshing. The presented refresh token is revoked as
    part of the exchange (rotation): a stolen token is usable at most once, and
    its reuse is visible.
    """
    try:
        token = decode_token(payload.refresh_token, expected_type="refresh")
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if await token_store.is_revoked(token.jti):
        log.warning("auth.revoked_refresh_reuse", jti=token.jti, sub=token.subject)
        await audit.record(
            action="auth.refresh_rejected",
            request=request,
            username=token.username,
            resource_type="user",
            resource_id=token.subject,
            params={"reason": "revoked_token_reuse"},
            result="denied",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This refresh token is no longer valid; sign in again",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await session.scalar(select(User).where(User.id == token.user_id))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account is no longer active",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Rotate: the presented token cannot be used again.
    await token_store.revoke(token.jti, token.expires_at)

    return _token_pair(user)


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Revoke a refresh token",
)
async def logout(
    payload: RefreshRequest, request: Request, user: CurrentUserDep
) -> MessageResponse:
    """Revoke the supplied refresh token.

    The access token is not revoked — it is stateless and expires within
    minutes. Ending the *session* means ensuring it cannot be renewed.
    """
    try:
        token = decode_token(payload.refresh_token, expected_type="refresh")
    except TokenError:
        # An unusable token is already effectively revoked; report success so
        # a client can always reach a signed-out state.
        return MessageResponse(detail="Signed out")

    await token_store.revoke(token.jti, token.expires_at)
    await audit.record(
        action="auth.logout",
        user=user,
        request=request,
        resource_type="user",
        resource_id=user.id,
    )
    log.info("auth.logout", username=user.username)
    return MessageResponse(detail="Signed out")


@router.get(
    "/me",
    response_model=UserProfile,
    summary="The authenticated caller's profile and effective permissions",
)
async def me(user: CurrentUserDep, session: DbSession) -> UserProfile:
    """Return the caller's record plus their effective grants.

    The permission list lets the command centre hide actions the user cannot
    perform, rather than showing buttons that fail with a 403.
    """
    record = await session.scalar(select(User).where(User.id == user.id))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    profile = UserProfile.model_validate(record)
    profile.permissions = sorted(p.value for p in permissions_for(record.role))
    return profile


@router.post(
    "/password",
    response_model=MessageResponse,
    summary="Change your own password",
)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: CurrentUserDep,
    session: DbSession,
) -> MessageResponse:
    """Change the caller's password after re-verifying the current one."""
    record = await session.scalar(select(User).where(User.id == user.id))
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if not verify_password(payload.current_password, record.password_hash):
        await audit.record(
            action="user.password_change",
            user=user,
            request=request,
            resource_type="user",
            resource_id=user.id,
            params={"reason": "current_password_incorrect"},
            result="denied",
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )

    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must differ from the current one",
        )

    # argon2 is intentionally slow; keep it off the event loop.
    record.password_hash = await asyncio.to_thread(hash_password, payload.new_password)
    await session.flush()

    await audit.record(
        action="user.password_change",
        user=user,
        request=request,
        resource_type="user",
        resource_id=user.id,
    )
    log.info("auth.password_changed", username=user.username)

    return MessageResponse(detail="Password updated")
