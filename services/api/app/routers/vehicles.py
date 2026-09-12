"""Vehicle intelligence: where a plate has been, and who it travelled with.

This is the endpoint behind the organisers' scored live test case (FAQ Q26–28):
a designated vehicle tracked across cameras with a complete timestamped route.

Every response here says what it does not know. A route carries its flagged
legs, its confidence, and an explicit note that distances are straight lines
between cameras rather than roads driven — because the difference decides
whether an implied speed can be used as evidence.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select, text

from app.api.deps import CurrentUserDep, DbSession
from app.core.logging import get_logger
from app.core.rbac import Permission, require_permission
from app.models.intelligence import Watchlist
from app.schemas.intelligence import (
    PlateSearchResponse,
    PlateSearchResult,
    WatchlistHit,
    normalise_plate,
)
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


#: pg_trgm's own documented default similarity threshold. Kept as the floor
#: for the `threshold` query param rather than letting a caller ask for
#: something looser: the `%` operator below already excludes anything under
#: 0.3 via the GIN index (`pg_trgm.similarity_threshold`, session-default
#: 0.3), so a caller-supplied floor under that would filter nothing the
#: index scan had not already dropped — a parameter that looks tunable but
#: is not.
DEFAULT_SIMILARITY_THRESHOLD = 0.3

#: `%` is what makes Postgres use `ix_detections_plate_trgm` (a GIN index on
#: `gin_trgm_ops`) instead of a sequential scan — `similarity() >= threshold`
#: alone is not index-aware. The explicit `similarity() >= :threshold` clause
#: alongside it is not redundant: `%` uses the *session's* GUC default (0.3)
#: to decide what the index scan returns, and re-checking with a bind
#: parameter is what lets a caller ask for a stricter match than 0.3 without
#: touching session state that would outlive this request.
#:
#: `:camera_id` and `:vehicle_type` use `CAST(:x AS type)` rather than
#: `:x::type` for the reason `analytics.py` documents at length: SQLAlchemy's
#: `text()` scans the string for `:word` parameters itself, and a `::cast`
#: immediately after one is misread as the start of another.
_FUZZY_SEARCH_SQL = """
    SELECT
        d.plate_normalised,
        similarity(d.plate_normalised, :query) AS score,
        count(*)                               AS sightings,
        count(DISTINCT d.camera_id)            AS cameras,
        min(d.ts)                              AS first_seen,
        max(d.ts)                              AS last_seen
    FROM detections d
    WHERE d.plate_normalised % :query
      AND similarity(d.plate_normalised, :query) >= :threshold
      AND (CAST(:since AS timestamptz) IS NULL OR d.ts >= :since)
      AND (CAST(:until AS timestamptz) IS NULL OR d.ts <= :until)
      AND (CAST(:camera_id AS uuid) IS NULL OR d.camera_id = :camera_id)
      AND (CAST(:vehicle_type AS text) IS NULL OR d.vehicle_type = :vehicle_type)
    GROUP BY d.plate_normalised
    ORDER BY score DESC, sightings DESC
    LIMIT :limit
"""


@router.get(
    "/search",
    response_model=PlateSearchResponse,
    dependencies=[Depends(require_permission(Permission.SEARCH_EXECUTE))],
    summary="Fuzzy and partial plate search",
)
async def search_plates(
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
    q: Annotated[
        str, Query(min_length=3, max_length=24, description="A plate, or a guess at one")
    ],
    since: Annotated[datetime | None, Query(description="UTC lower bound")] = None,
    until: Annotated[datetime | None, Query(description="UTC upper bound")] = None,
    camera_id: Annotated[uuid.UUID | None, Query()] = None,
    vehicle_type: Annotated[str | None, Query()] = None,
    threshold: Annotated[
        float,
        Query(
            ge=DEFAULT_SIMILARITY_THRESHOLD,
            le=1.0,
            description="Minimum trigram similarity, 0.3-1.0",
        ),
    ] = DEFAULT_SIMILARITY_THRESHOLD,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> PlateSearchResponse:
    """Trigram-similarity search over every plate ever read.

    For the case an exact search returns nothing: a misread character
    (`GJ03A81234` for `GJ03AB1234`), a partial plate off a blurred crop, a
    plate typed from memory. Queries the GIN trigram index directly rather
    than through the ORM, for the same reason `analytics.py` does — the
    result is a per-plate aggregate over however many detections that plate
    has, which the ORM has no concise way to express.

    Each result carries a faceted summary (sightings, distinct cameras, first
    and last seen) and whether the plate is on an active watchlist entry —
    an operator triaging a misread plate needs to see it is a blacklist match
    without opening the full route first.
    """
    normalised = normalise_plate(q)
    if len(normalised) < 3:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A search needs at least three alphanumeric characters.",
        )

    rows = (
        await session.execute(
            text(_FUZZY_SEARCH_SQL),
            {
                "query": normalised,
                "threshold": threshold,
                "since": since,
                "until": until,
                "camera_id": camera_id,
                "vehicle_type": vehicle_type,
                "limit": limit,
            },
        )
    ).mappings().all()

    plates = [row["plate_normalised"] for row in rows]
    watchlist_hits: dict[str, WatchlistHit] = {}
    if plates:
        entries = (
            await session.execute(
                select(Watchlist).where(
                    Watchlist.active.is_(True), Watchlist.plate_normalised.in_(plates)
                )
            )
        ).scalars().all()
        for entry in entries:
            # A plate can legitimately carry more than one active entry
            # (several open cases); the first one found is shown rather than
            # inventing a ranking between cases here.
            watchlist_hits.setdefault(
                entry.plate_normalised,
                WatchlistHit(
                    category=entry.category, priority=entry.priority, case_ref=entry.case_ref
                ),
            )

    results = [
        PlateSearchResult(
            plate_normalised=row["plate_normalised"],
            similarity=round(float(row["score"]), 3),
            sightings=row["sightings"],
            cameras=row["cameras"],
            first_seen=row["first_seen"],
            last_seen=row["last_seen"],
            watchlist=watchlist_hits.get(row["plate_normalised"]),
        )
        for row in rows
    ]

    # A fuzzy search is still a plate search — CLAUDE.md's audit rule does
    # not carve out an exception for "the query might not have matched".
    await audit.record_plate_search(
        request=request,
        user=user,
        query=normalised,
        filters={
            "action": "search",
            "threshold": threshold,
            "camera_id": str(camera_id) if camera_id else None,
            "vehicle_type": vehicle_type,
        },
        result_count=len(results),
    )

    return PlateSearchResponse(
        query=normalised,
        threshold=threshold,
        exact_match=any(r.plate_normalised == normalised for r in results),
        results=results,
    )
