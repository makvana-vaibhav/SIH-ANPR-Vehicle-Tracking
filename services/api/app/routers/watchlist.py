"""Watchlist management.

Every mutation is audited. This is the list that decides which vehicles get a
police response, so who added a plate, who removed one, and when, is not
optional bookkeeping — it is the difference between a tool that can be
scrutinised and one that cannot.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUserDep, DbSession
from app.core.logging import get_logger
from app.core.rbac import Permission, require_permission
from app.models.intelligence import Watchlist
from app.schemas.intelligence import WatchlistCreate, WatchlistOut, WatchlistUpdate
from app.services import audit
from app.services import watchlist as watchlist_service

router = APIRouter(prefix="/api/v1/watchlist", tags=["watchlist"])
log = get_logger(__name__)


@router.get(
    "",
    response_model=list[WatchlistOut],
    dependencies=[Depends(require_permission(Permission.WATCHLIST_READ))],
    summary="List watchlist entries",
)
async def list_entries(
    session: DbSession,
    active_only: Annotated[bool, Query(description="Hide expired and disabled entries")] = True,
    plate: Annotated[str | None, Query(description="Filter by plate fragment")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Watchlist]:
    query = select(Watchlist).order_by(Watchlist.created_at.desc())
    if active_only:
        query = query.where(Watchlist.active.is_(True))
    if plate:
        cleaned = "".join(ch for ch in plate.upper() if ch.isalnum())
        query = query.where(Watchlist.plate_normalised.contains(cleaned))
    result = await session.execute(query.limit(limit).offset(offset))
    return list(result.scalars().all())


@router.post(
    "",
    response_model=WatchlistOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission(Permission.WATCHLIST_CREATE))],
    summary="Add a plate to the watchlist",
)
async def create_entry(
    payload: WatchlistCreate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> Watchlist:
    existing = (
        await session.execute(
            select(Watchlist).where(
                Watchlist.plate_normalised == payload.plate,
                Watchlist.active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{payload.plate} is already on the watchlist (entry {existing.id})",
        )

    # A retired entry for the same plate *and* case reference still occupies the
    # unique constraint, so inserting alongside it raises an integrity error the
    # caller sees as "Internal server error". Retiring is the only removal this
    # API offers — deleting a watchlist entry deactivates it, because a
    # surveillance decision that can be erased cannot be reviewed — so an
    # operator re-issuing a BOLO under the same FIR would hit a 500 with no way
    # forward. Re-issuing it is the intent, so re-issue it.
    retired = (
        await session.execute(
            select(Watchlist).where(
                Watchlist.plate_normalised == payload.plate,
                Watchlist.case_ref == payload.case_ref,
                Watchlist.active.is_(False),
            )
        )
    ).scalar_one_or_none()

    operation = "create"
    if retired is not None:
        operation = "reinstate"
        entry = retired
        entry.category = payload.category
        entry.priority = payload.priority.value
        entry.remarks = payload.remarks
        entry.valid_from = payload.valid_from
        entry.valid_to = payload.valid_to
        entry.added_by = user.id
        entry.active = True
    else:
        entry = Watchlist(
            plate_normalised=payload.plate,
            category=payload.category,
            priority=payload.priority.value,
            case_ref=payload.case_ref,
            remarks=payload.remarks,
            valid_from=payload.valid_from,
            valid_to=payload.valid_to,
            added_by=user.id,
            active=True,
        )
        session.add(entry)

    await session.commit()
    await session.refresh(entry)

    # The matcher holds the watchlist in memory; without this the new plate
    # would not be matched until the next periodic refresh.
    watchlist_service.index.mark_stale()
    await audit.record_watchlist_change(
        request=request,
        user=user,
        # Recorded distinctly: reinstating a retired BOLO is a different act
        # from raising a new one, and the trail should say which happened.
        operation=operation,
        plate=entry.plate_normalised,
        watchlist_id=entry.id,
    )
    return entry


@router.patch(
    "/{entry_id}",
    response_model=WatchlistOut,
    dependencies=[Depends(require_permission(Permission.WATCHLIST_UPDATE))],
    summary="Update a watchlist entry",
)
async def update_entry(
    entry_id: uuid.UUID,
    payload: WatchlistUpdate,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> Watchlist:
    entry = (
        await session.execute(select(Watchlist).where(Watchlist.id == entry_id))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such entry")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(entry, field, value.value if hasattr(value, "value") else value)

    await session.commit()
    await session.refresh(entry)

    watchlist_service.index.mark_stale()
    await audit.record_watchlist_change(
        request=request,
        user=user,
        operation="update",
        plate=entry.plate_normalised,
        watchlist_id=entry.id,
    )
    return entry


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission(Permission.WATCHLIST_DELETE))],
    summary="Remove a plate from the watchlist",
)
async def delete_entry(
    entry_id: uuid.UUID,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> Response:
    entry = (
        await session.execute(select(Watchlist).where(Watchlist.id == entry_id))
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such entry")

    # Deactivated, not deleted. The alerts this entry raised reference it, and
    # a surveillance decision that can be erased cannot be reviewed.
    entry.active = False
    await session.commit()

    watchlist_service.index.mark_stale()
    await audit.record_watchlist_change(
        request=request,
        user=user,
        operation="deactivate",
        plate=entry.plate_normalised,
        watchlist_id=entry.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/index/status",
    dependencies=[Depends(require_permission(Permission.WATCHLIST_READ))],
    summary="State of the in-memory matcher",
)
async def index_status() -> dict[str, object]:
    """Whether matching is seeing the current list, and how big it is."""
    return {
        "entries_indexed": watchlist_service.index.entry_count,
        "needs_refresh": watchlist_service.index.needs_refresh,
        "refresh_interval_seconds": watchlist_service.REFRESH_SECONDS,
    }
