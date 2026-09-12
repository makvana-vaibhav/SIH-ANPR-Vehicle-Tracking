"""Vehicle re-identification: ranked candidates for an unreadable plate.

See `app.schemas.reid` and `app.services.reid` for the product argument and
the matching mechanics. This router is thin by design — audit and RBAC
plumbing only, the same shape as `routers.vehicles`'s fuzzy-search endpoint
(P8), since a re-identification lookup is the same kind of
privacy-sensitive movement query a plate search is: "which vehicle was
this" rather than "where has this plate been", but still tracking a
specific vehicle's movement across cameras.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import CurrentUserDep, DbSession
from app.core.logging import get_logger
from app.core.rbac import Permission, require_permission
from app.schemas.reid import ReidMatchResponse
from app.services import audit, reid

router = APIRouter(prefix="/api/v1/reid", tags=["reid"])
log = get_logger(__name__)


@router.get(
    "/candidates",
    response_model=ReidMatchResponse,
    dependencies=[Depends(require_permission(Permission.SEARCH_EXECUTE))],
    summary="Candidate cross-camera matches for one detection, appearance-ranked when possible",
)
async def candidates(
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    detection_id: Annotated[uuid.UUID, Query(description="The detection to find matches for")],
    detection_ts: Annotated[
        datetime, Query(description="That detection's ts — required, same composite key detections use everywhere")
    ],
) -> ReidMatchResponse:
    result = await reid.find_reid_matches(session, detection_id, detection_ts)

    await audit.record_plate_search(
        request=request,
        user=user,
        query=f"reid:{detection_id}",
        filters={"action": "reid", "embedding_available": result.embedding_available},
        result_count=len(result.candidates),
    )

    return result
