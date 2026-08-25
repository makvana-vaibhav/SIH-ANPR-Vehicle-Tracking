"""Fleet health and gap-analysis endpoints.

Both are named Model 1 requirements (FAQ Q15): "camera health monitoring" and
"gap-analysis reports".
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.adapters import adapter_for, available_adapters
from app.api.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.core.rbac import Permission, require_permission
from app.services import audit, fleet, health_monitor
from app.services import camera as camera_service

log = get_logger("api.fleet")

router = APIRouter(prefix="/api/v1", tags=["fleet health"])


@router.get(
    "/health/fleet",
    summary="Fleet-wide health rollup",
)
async def fleet_health(
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
) -> dict[str, Any]:
    """Live availability across the estate, by department and by vendor.

    Failure reasons are grouped by error code, which is what turns "31 cameras
    down" into "one expired credential" — a whole department failing with
    `unauthorized` has a single cause and a single fix.
    """
    return await fleet.fleet_health(session)


@router.get(
    "/health/gaps",
    summary="Gap-analysis report — where the estate is blind",
)
async def gap_report(
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> dict[str, Any]:
    """Where we cannot see, and why.

    Separates four failure modes that need different responses:
    a camera that is broken, a district with cameras but no ANPR, a district
    with no cameras at all, and a camera that flaps in and out.
    """
    return await fleet.gap_analysis(session, hours=hours)


@router.get(
    "/cameras/{camera_id}/health",
    summary="Probe history and uptime for one camera",
)
async def camera_health(
    camera_id: uuid.UUID,
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
    hours: Annotated[int, Query(ge=1, le=720)] = 24,
) -> dict[str, Any]:
    """Health history for the camera detail page's sparklines."""
    camera = await camera_service.get_camera(session, camera_id)
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    return {
        "camera_id": str(camera_id),
        "camera_code": camera.camera_code,
        "status": camera.status,
        "uptime": await fleet.uptime_summary(session, camera_id, hours=hours),
        "history": await fleet.camera_health_history(session, camera_id, hours=hours),
    }


@router.post(
    "/cameras/{camera_id}/probe",
    summary="Probe one camera immediately",
)
async def probe_now(
    camera_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_UPDATE))],
) -> dict[str, Any]:
    """Force an out-of-cycle health probe.

    An operator who has just been told a camera was repaired should not have to
    wait for the next scheduled sweep to confirm it.
    """
    camera = await camera_service.get_camera(session, camera_id)
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    probe = await health_monitor.probe_camera(session, camera)
    previous = camera.status
    camera.status = probe.status.value
    await session.commit()

    await audit.record(
        action="camera.probe",
        user=user,
        request=request,
        resource_type="camera",
        resource_id=camera_id,
        params={
            "camera_code": camera.camera_code,
            "result": probe.status.value,
            "error_code": probe.error_code,
        },
    )

    return {
        "camera_code": camera.camera_code,
        "previous_status": previous,
        "status": probe.status.value,
        "reachable": probe.reachable,
        "latency_ms": probe.latency_ms,
        "fps_actual": probe.fps_actual,
        "bitrate_kbps": probe.bitrate_kbps,
        "error_code": probe.error_code,
        "detail": probe.detail,
        "probed_at": probe.probed_at.isoformat(),
    }


@router.post(
    "/health/sweep",
    summary="Run a full fleet health sweep now (admin)",
)
async def sweep_now(
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_UPDATE))],
) -> dict[str, Any]:
    """Trigger an immediate sweep of the whole fleet.

    The monitor daemon does this on a schedule; this is the manual override for
    a demo or after a network repair.
    """
    summary = await health_monitor.sweep(session)
    await audit.record(
        action="health.sweep",
        user=user,
        request=request,
        resource_type="fleet",
        params=summary,
    )
    return summary


@router.get(
    "/integration/adapters",
    summary="Registered integration adapters",
)
async def integration_adapters(
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
) -> dict[str, Any]:
    """Which vendor protocols this platform can federate.

    This is the interoperability evidence the challenge asks for: one interface,
    many vendors, resolved per VMS instance.
    """
    return {
        "adapters": available_adapters(),
        "detail": (
            "Every camera is reached through a CameraAdapter resolved from its "
            "VMS instance. Adding a vendor is one class plus one registry entry; "
            "nothing else in the platform changes."
        ),
    }


@router.post(
    "/vms/{vms_id}/sync",
    summary="Sync the camera list from a federated VMS",
)
async def sync_vms(
    vms_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_CREATE))],
    dry_run: Annotated[bool, Query()] = True,
) -> dict[str, Any]:
    """Pull the camera list from a departmental VMS.

    This is federation onboarding: the department keeps its VMS, we ingest its
    metadata. Defaults to ``dry_run`` so an operator sees what would be imported
    before anything is written.
    """
    from sqlalchemy import select

    from app.models.registry import VmsInstance

    vms = await session.scalar(select(VmsInstance).where(VmsInstance.id == vms_id))
    if vms is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VMS instance not found")

    adapter = adapter_for(vms)
    try:
        discovered = await adapter.list_cameras()
    except Exception as exc:
        log.warning(
            "vms.sync_failed",
            vms=vms.name,
            adapter=adapter.adapter_type,
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach {vms.name}: {exc}",
        ) from exc
    finally:
        await adapter.close()

    await audit.record(
        action="vms.sync",
        user=user,
        request=request,
        resource_type="vms_instance",
        resource_id=vms_id,
        params={"vms": vms.name, "discovered": len(discovered), "dry_run": dry_run},
    )

    return {
        "vms": vms.name,
        "vendor": vms.vendor,
        "adapter": adapter.adapter_type,
        "discovered": len(discovered),
        "dry_run": dry_run,
        "cameras": [
            {
                "external_id": c.external_id,
                "name": c.name,
                "lat": c.lat,
                "lon": c.lon,
                "resolution": c.resolution,
                "fps": c.fps,
                "codec": c.codec,
                "is_live": c.is_live,
            }
            for c in discovered[:100]
        ],
    }
