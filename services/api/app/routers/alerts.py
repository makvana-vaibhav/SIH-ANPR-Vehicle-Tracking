"""Alerts: reading them, and moving them through their lifecycle.

Every transition records who and when. `false_positive` is a first-class
outcome rather than a delete: a system that lets operators quietly erase its
mistakes cannot be audited, and those mistakes are the data that improves it.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.api.deps import CurrentUserDep, DbSession
from app.core.logging import get_logger
from app.core.rbac import Permission, permissions_for, require_permission
from app.models.enums import AlertStatus
from app.models.intelligence import Alert, Detection
from app.schemas.intelligence import AlertOut, AlertPage, AlertTransition
from app.services import alerts as alert_service
from app.services import audit, evidence

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])
log = get_logger(__name__)

#: Which permission each transition needs. Acknowledging is routine; dispatching
#: commits a unit; closing ends the record. They are separate grants because in
#: a control room they are separate authorities.
TRANSITION_PERMISSION = {
    AlertStatus.ACKNOWLEDGED.value: Permission.ALERT_ACKNOWLEDGE,
    AlertStatus.DISPATCHED.value: Permission.ALERT_DISPATCH,
    AlertStatus.CLOSED.value: Permission.ALERT_CLOSE,
    AlertStatus.FALSE_POSITIVE.value: Permission.ALERT_CLOSE,
}


@router.get(
    "",
    response_model=AlertPage,
    dependencies=[Depends(require_permission(Permission.ALERT_READ))],
    summary="List alerts",
)
async def list_alerts(
    session: DbSession,
    alert_status: Annotated[AlertStatus | None, Query(alias="status")] = None,
    plate: Annotated[str | None, Query(description="Exact normalised plate")] = None,
    camera_id: Annotated[uuid.UUID | None, Query()] = None,
    open_only: Annotated[bool, Query(description="Only alerts still needing action")] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AlertPage:
    query = select(Alert)
    count_query = select(func.count()).select_from(Alert)

    filters = []
    if alert_status is not None:
        filters.append(Alert.status == alert_status.value)
    if open_only:
        filters.append(
            Alert.status.in_(
                [
                    AlertStatus.NEW.value,
                    AlertStatus.ACKNOWLEDGED.value,
                    AlertStatus.DISPATCHED.value,
                ]
            )
        )
    if plate:
        cleaned = "".join(ch for ch in plate.upper() if ch.isalnum())
        filters.append(Alert.plate_normalised == cleaned)
    if camera_id is not None:
        filters.append(Alert.camera_id == camera_id)

    for condition in filters:
        query = query.where(condition)
        count_query = count_query.where(condition)

    total = (await session.execute(count_query)).scalar_one()
    result = await session.execute(
        query.order_by(Alert.created_at.desc()).limit(limit).offset(offset)
    )
    alerts = list(result.scalars().all())
    return AlertPage(
        items=await _with_crops(session, alerts),
        total=total,
        limit=limit,
        offset=offset,
    )


async def _with_crops(session: DbSession, alerts: list[Alert]) -> list[AlertOut]:
    """Attach each alert's plate crop, resolved from its detection.

    `alerts` deliberately stores no crop of its own — the evidence belongs to
    the detection that raised it, and duplicating the key would be two places to
    keep in step. One `IN` query for the whole page rather than a lookup per
    row, so a page of 200 alerts costs one extra round trip and not 200.
    """
    detection_ids = [a.detection_id for a in alerts if a.detection_id]
    keys: dict[uuid.UUID, str | None] = {}
    if detection_ids:
        rows = await session.execute(
            select(Detection.id, Detection.crop_key).where(Detection.id.in_(detection_ids))
        )
        keys = dict(rows.all())

    out: list[AlertOut] = []
    for alert in alerts:
        item = AlertOut.model_validate(alert)
        item.crop_url = evidence.crop_url(keys.get(alert.detection_id))
        out.append(item)
    return out


@router.get(
    "/{alert_id}",
    response_model=AlertOut,
    dependencies=[Depends(require_permission(Permission.ALERT_READ))],
    summary="One alert",
)
async def get_alert(alert_id: uuid.UUID, session: DbSession) -> Alert:
    alert = await alert_service.get_alert(session, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such alert")
    return alert


@router.post(
    "/{alert_id}/transition",
    response_model=AlertOut,
    summary="Acknowledge, dispatch, close, or mark an alert a false positive",
)
async def transition_alert(
    alert_id: uuid.UUID,
    payload: AlertTransition,
    session: DbSession,
    user: CurrentUserDep,
    request: Request,
) -> Alert:
    alert = await alert_service.get_alert(session, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such alert")

    # Authorised per transition, not per endpoint: dispatching a unit is a
    # different authority from acknowledging that an alert was seen.
    required = TRANSITION_PERMISSION.get(payload.status.value)
    if required is not None and required not in permissions_for(user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{payload.status.value}' requires the {required.value} permission",
        )

    try:
        await alert_service.transition(
            session, alert, to=payload.status.value, user_id=user.id, notes=payload.notes
        )
    except alert_service.InvalidTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(alert)

    await audit.record(
        request=request,
        user=user,
        action="alert.transition",
        resource_type="alert",
        resource_id=str(alert.id),
        params={"to": payload.status.value, "plate": alert.plate_normalised},
    )
    return alert


@router.get(
    "/stats/summary",
    dependencies=[Depends(require_permission(Permission.ALERT_READ))],
    summary="Alert counts by status and priority",
)
async def alert_summary(session: DbSession) -> dict[str, object]:
    by_status = await session.execute(select(Alert.status, func.count()).group_by(Alert.status))
    by_priority = await session.execute(
        select(Alert.priority, func.count())
        .where(Alert.status.in_([AlertStatus.NEW.value, AlertStatus.ACKNOWLEDGED.value]))
        .group_by(Alert.priority)
    )
    return {
        "by_status": dict(by_status.all()),
        "open_by_priority": dict(by_priority.all()),
    }
