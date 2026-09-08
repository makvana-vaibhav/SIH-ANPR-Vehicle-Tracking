"""Vehicle intelligence: where a plate has been, and who it travelled with.

This is the endpoint behind the organisers' scored live test case (FAQ Q26–28):
a designated vehicle tracked across cameras with a complete timestamped route.

Every response here says what it does not know. A route carries its flagged
legs, its confidence, and an explicit note that distances are straight lines
between cameras rather than roads driven — because the difference decides
whether an implied speed can be used as evidence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import CurrentUserDep, DbSession
from app.core.logging import get_logger
from app.core.rbac import Permission, require_permission
from app.schemas.intelligence import normalise_plate
from app.services import audit, correlator

router = APIRouter(prefix="/api/v1/vehicles", tags=["vehicles"])
log = get_logger(__name__)

#: How far back a route looks when no window is given. A day covers the
#: question an operator usually has ("where has it been today?") without
#: scanning a hypertable that grows forever.
DEFAULT_WINDOW = timedelta(days=1)


def _window(since: datetime | None, until: datetime | None) -> tuple[datetime, datetime | None]:
    return since or (datetime.now(UTC) - DEFAULT_WINDOW), until


@router.get(
    "/{plate}/route",
    dependencies=[Depends(require_permission(Permission.SEARCH_EXECUTE))],
    summary="Reconstruct a vehicle's route across cameras",
)
async def vehicle_route(
    plate: str,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    since: Annotated[datetime | None, Query(description="UTC lower bound")] = None,
    until: Annotated[datetime | None, Query(description="UTC upper bound")] = None,
    fmt: Annotated[
        str, Query(alias="format", pattern="^(json|geojson)$", description="json | geojson")
    ] = "json",
) -> dict[str, Any]:
    normalised = normalise_plate(plate)
    if len(normalised) < 4:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A plate needs at least four alphanumeric characters to search on.",
        )

    window_from, window_to = _window(since, until)
    route = await correlator.build_route(session, normalised, window_from, window_to)

    # Reconstructing a vehicle's movements is the most privacy-sensitive read
    # this system offers, so it is audited explicitly rather than left to the
    # middleware — CLAUDE.md §5.
    await audit.record_plate_search(
        request=request,
        user=user,
        query=normalised,
        filters={
            "action": "route",
            "from": window_from.isoformat(),
            "to": window_to.isoformat() if window_to else None,
        },
        result_count=len(route.hops),
    )

    if fmt == "geojson":
        return route.to_geojson()
    return route.to_dict()


@router.get(
    "/{plate}/convoy",
    dependencies=[Depends(require_permission(Permission.SEARCH_EXECUTE))],
    summary="Vehicles seen travelling with this one",
)
async def vehicle_convoy(
    plate: str,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    window_s: Annotated[
        float, Query(ge=5.0, le=900.0, description="Seconds apart still counted as together")
    ] = 120.0,
    min_shared_cameras: Annotated[int, Query(ge=2, le=20)] = 3,
) -> dict[str, Any]:
    normalised = normalise_plate(plate)
    if len(normalised) < 4:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A plate needs at least four alphanumeric characters to search on.",
        )

    window_from, window_to = _window(since, until)
    convoys = await correlator.find_convoys(
        session,
        normalised,
        window_from,
        window_to,
        window_s=window_s,
        min_shared_cameras=min_shared_cameras,
    )

    await audit.record_plate_search(
        request=request,
        user=user,
        query=normalised,
        filters={"action": "convoy", "window_s": window_s},
        result_count=len(convoys),
    )

    return {
        "plate": normalised,
        "window": {
            "from": window_from.isoformat(),
            "to": window_to.isoformat() if window_to else None,
        },
        "criteria": {
            "seconds_apart": window_s,
            "min_shared_cameras": min_shared_cameras,
        },
        "convoys": [convoy.to_dict() for convoy in convoys],
        "note": (
            "Co-occurrence is evidence of association, not proof of it. Two vehicles "
            "on the same road at the same time will co-occur without being related."
        ),
    }


@router.get(
    "/routable",
    dependencies=[Depends(require_permission(Permission.SEARCH_EXECUTE))],
    summary="Plates seen on enough cameras to have a route",
)
async def routable_plates(
    session: DbSession,
    since: Annotated[datetime | None, Query()] = None,
    min_cameras: Annotated[int, Query(ge=2, le=50)] = 2,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """What the operator can usefully ask for a route on.

    Offering a route search with no way to discover which plates have one leads
    an operator to conclude the feature is broken when the real answer is that
    the vehicle was only ever seen once.
    """
    window = since or (datetime.now(UTC) - DEFAULT_WINDOW)
    plates = await correlator.recent_plates(session, window, min_cameras, limit)
    return {
        "since": window.isoformat(),
        "min_cameras": min_cameras,
        "plates": [{"plate": plate, "cameras": cameras} for plate, cameras in plates],
    }
