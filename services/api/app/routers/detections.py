"""Detections: what the AI read, after the fact.

The live feed (`/ws/events`) carries what is happening *now* and is the right
source for an overlay on moving video. It is not a substitute for this: an
operator who opens a camera wants the sightings from before they arrived, and a
plate search has to look further back than a socket that was opened a minute
ago.

Every read carries its own evidence — how many frames agreed, what the runners
up were, whether the grammar had to repair it — so an operator can judge a
reading rather than being asked to trust it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Select, func, select

from app.api.deps import CurrentUserDep, DbSession
from app.core.logging import get_logger
from app.core.rbac import Permission, require_permission
from app.models.intelligence import Detection
from app.models.registry import Camera
from app.schemas.intelligence import DetectionOut, DetectionPage
from app.services import audit

router = APIRouter(prefix="/api/v1/detections", tags=["detections"])
log = get_logger(__name__)

#: How far back an unbounded request looks. Detections are a hypertable that
#: grows without limit, so "recent" has to mean something specific or the first
#: query after a long run scans the lot.
DEFAULT_WINDOW = timedelta(hours=6)


def _normalise(plate: str) -> str:
    """Match the normalisation the watchlist and the writer both use."""
    return "".join(ch for ch in plate.upper() if ch.isalnum())


def _apply_filters(
    query: Select,
    *,
    camera_id: uuid.UUID | None,
    plate: str | None,
    plate_prefix: str | None,
    since: datetime,
    until: datetime | None,
    readable_only: bool,
    min_confidence: float | None,
) -> Select:
    query = query.where(Detection.ts >= since)
    if until is not None:
        query = query.where(Detection.ts <= until)
    if camera_id is not None:
        query = query.where(Detection.camera_id == camera_id)
    if plate:
        query = query.where(Detection.plate_normalised == _normalise(plate))
    elif plate_prefix:
        # Partial plates are how an operator actually remembers one. Real
        # fuzzy search is Phase 8; this is the honest interim — a prefix match
        # on an indexed column, not a pretence at ranking.
        query = query.where(Detection.plate_normalised.startswith(_normalise(plate_prefix)))
    if readable_only:
        query = query.where(
            Detection.plate_normalised.isnot(None), Detection.plate_normalised != ""
        )
    if min_confidence is not None:
        query = query.where(Detection.plate_confidence >= min_confidence)
    return query


@router.get(
    "",
    response_model=DetectionPage,
    dependencies=[Depends(require_permission(Permission.SEARCH_EXECUTE))],
    summary="List detections",
)
async def list_detections(
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    camera_id: Annotated[uuid.UUID | None, Query(description="Restrict to one camera")] = None,
    plate: Annotated[str | None, Query(description="Exact plate, normalised for you")] = None,
    plate_prefix: Annotated[str | None, Query(description="Plate starts with")] = None,
    since: Annotated[datetime | None, Query(description="UTC lower bound")] = None,
    until: Annotated[datetime | None, Query(description="UTC upper bound")] = None,
    readable_only: Annotated[bool, Query(description="Only sightings with a plate")] = True,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DetectionPage:
    window_start = since or (datetime.now(UTC) - DEFAULT_WINDOW)

    filters = {
        "camera_id": camera_id,
        "plate": plate,
        "plate_prefix": plate_prefix,
        "since": window_start,
        "until": until,
        "readable_only": readable_only,
        "min_confidence": min_confidence,
    }
    query = _apply_filters(select(Detection), **filters)
    count_query = _apply_filters(select(func.count()).select_from(Detection), **filters)

    total = (await session.execute(count_query)).scalar_one()
    rows = (
        (await session.execute(query.order_by(Detection.ts.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )

    # Camera codes rather than bare ids: an operator reads "CAM-00001", and
    # without this every row in the table costs the client another request.
    codes: dict[uuid.UUID, tuple[str, str]] = {}
    camera_ids = {row.camera_id for row in rows if row.camera_id}
    if camera_ids:
        for cam_id, code, name in (
            await session.execute(
                select(Camera.id, Camera.camera_code, Camera.name).where(Camera.id.in_(camera_ids))
            )
        ).all():
            codes[cam_id] = (code, name)

    # A plate search is one of the three actions CLAUDE.md §5 requires an
    # explicit audit row for, whatever the middleware already recorded.
    if plate or plate_prefix:
        await audit.record_plate_search(
            request=request,
            user=user,
            query=plate or f"{plate_prefix}*",
            filters={"camera_id": str(camera_id) if camera_id else None},
            result_count=total,
        )

    return DetectionPage(
        items=[DetectionOut.from_row(row, *codes.get(row.camera_id, ("", ""))) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
