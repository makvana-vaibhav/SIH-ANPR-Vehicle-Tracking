"""User administration.

Every account here belongs to a named officer. There is no public
registration and no self-service signup: the audit log's value rests entirely
on being able to say *who* did something, and an account nobody vouched for
breaks that at the root.

Three decisions worth stating:

* **Usernames are immutable.** The audit log records a username alongside the
  user id precisely so history survives an account being deleted. Renaming
  would silently rewrite what the log appears to say about past actions.
* **Accounts are deactivated, not deleted.** A deleted user id turns every
  audit row that references it into an orphan. `DELETE` exists for genuine
  mistakes — an account created in error, before it did anything — and is
  admin-only.
* **An administrator setting a password sets `must_change_password`.** Until
  the holder replaces it, two people know the credential and nothing the
  account does is attributable to one of them.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select

from app.api.deps import CurrentUserDep, DbSession
from app.core.logging import get_logger
from app.core.password_policy import PasswordRejected, validate_password
from app.core.rbac import Permission, require_permission
from app.core.security import hash_password
from app.models.security import User
from app.schemas.auth import (
    PasswordReset,
    UserCreate,
    UserOut,
    UserPage,
    UserUpdate,
)
from app.services import audit

router = APIRouter(prefix="/api/v1/users", tags=["users"])
log = get_logger(__name__)


async def _get_user(session: DbSession, user_id: uuid.UUID) -> User:
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such user")
    return user


def _reject_weak(password: str, username: str) -> None:
    try:
        validate_password(password, username=username)
    except PasswordRejected as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get(
    "",
    response_model=UserPage,
    dependencies=[Depends(require_permission(Permission.USER_READ))],
    summary="List accounts",
)
async def list_users(
    session: DbSession,
    include_inactive: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> UserPage:
    query = select(User).order_by(User.username)
    count_query = select(func.count()).select_from(User)
    if not include_inactive:
        query = query.where(User.is_active.is_(True))
        count_query = count_query.where(User.is_active.is_(True))

    total = (await session.execute(count_query)).scalar_one()
    rows = (await session.execute(query.limit(limit))).scalars().all()
    return UserPage(items=[UserOut.model_validate(row) for row in rows], total=total)


@router.post(
    "",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.USER_CREATE))],
    summary="Create an account",
)
async def create_user(
    payload: UserCreate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> User:
    existing = (
        await session.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{payload.username} already exists",
        )

    _reject_weak(payload.password, payload.username)

    account = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role.value,
        department_id=payload.department_id,
        is_active=True,
        must_change_password=payload.must_change_password,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)

    await audit.record(
        action="user.create",
        user=user,
        request=request,
        resource_type="user",
        resource_id=str(account.id),
        params={"username": account.username, "role": account.role},
    )
    log.info("users.created", username=account.username, role=account.role, by=user.username)
    return account


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    dependencies=[Depends(require_permission(Permission.USER_UPDATE))],
    summary="Amend an account",
)
async def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> User:
    account = await _get_user(session, user_id)

    # An administrator who removes their own admin role, or deactivates their
    # own account, locks themselves and possibly everyone else out. Refusing is
    # kinder than a support call to the database.
    if account.id == user.id:
        if payload.is_active is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot deactivate your own account.",
            )
        if payload.role is not None and payload.role.value != account.role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot change your own role. Ask another administrator.",
            )

    changes = payload.model_dump(exclude_unset=True)
    if "role" in changes and payload.role is not None:
        changes["role"] = payload.role.value
    for field, value in changes.items():
        setattr(account, field, value)

    await session.commit()
    await session.refresh(account)

    await audit.record(
        action="user.update",
        user=user,
        request=request,
        resource_type="user",
        resource_id=str(account.id),
        params={"username": account.username, "changes": list(changes)},
    )
    return account


@router.post(
    "/{user_id}/password",
    response_model=UserOut,
    dependencies=[Depends(require_permission(Permission.USER_UPDATE))],
    summary="Reset somebody's password",
)
async def reset_password(
    user_id: uuid.UUID,
    payload: PasswordReset,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> User:
    account = await _get_user(session, user_id)
    _reject_weak(payload.new_password, account.username)

    account.password_hash = hash_password(payload.new_password)
    account.must_change_password = payload.must_change_password
    await session.commit()
    await session.refresh(account)

    # The password itself is never recorded — `app/services/audit.py` scrubs
    # credentials, and this passes only the fact that a reset happened.
    await audit.record(
        action="user.password_reset",
        user=user,
        request=request,
        resource_type="user",
        resource_id=str(account.id),
        params={"username": account.username, "forced_change": account.must_change_password},
    )
    log.info("users.password_reset", username=account.username, by=user.username)
    return account


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.USER_DELETE))],
    summary="Delete an account created in error",
)
async def delete_user(
    user_id: uuid.UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> Response:
    account = await _get_user(session, user_id)

    if account.id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account.",
        )

    # Deleting an account that has acted orphans every audit row naming it.
    # Deactivation keeps the history readable, and is what should be reached
    # for in every case except an account created by mistake.
    acted = await audit.count_for_user(session, account.id)
    if acted:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{account.username} has {acted} audit entries and cannot be "
                "deleted — the trail would be orphaned. Deactivate the account "
                "instead."
            ),
        )

    username = account.username
    await session.delete(account)
    await session.commit()

    await audit.record(
        action="user.delete",
        user=user,
        request=request,
        resource_type="user",
        resource_id=str(user_id),
        params={"username": username},
    )
    log.warning("users.deleted", username=username, by=user.username)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
