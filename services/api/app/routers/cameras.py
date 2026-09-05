"""Camera registry, GIS queries, and bulk onboarding endpoints.

Every endpoint is guarded by an explicit permission from the RBAC matrix, and
every mutation is audited. The read endpoints are paginated without exception —
no route in this file can return the whole estate.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.core.rbac import Permission, require_permission
from app.models.enums import (
    AdapterType,
    CameraStatus,
    CameraType,
    Protocol,
    VmsVendor,
)
from app.models.registry import Camera, Department, VmsInstance
from app.schemas.camera import (
    BulkUploadResult,
    CameraCreate,
    CameraOut,
    CameraPage,
    CameraUpdate,
    DepartmentOut,
    FleetSummary,
    GeoJSONFeatureCollection,
    VendorEnumOut,
    VmsInstanceOut,
)
from app.services import audit, gateway
from app.services import camera as camera_service

log = get_logger("api.cameras")

router = APIRouter(prefix="/api/v1", tags=["cameras"])

#: Upload ceiling, enforced before the file is read into memory.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


# ══ Reference data ════════════════════════════════════════════════════


@router.get(
    "/departments",
    response_model=list[DepartmentOut],
    summary="Departments and their camera counts",
)
async def list_departments(
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
) -> list[DepartmentOut]:
    """Owning departments, with how many cameras each contributes."""
    rows = (
        await session.execute(
            select(Department, func.count(Camera.id))
            .outerjoin(Camera, Camera.department_id == Department.id)
            .group_by(Department.id)
            .order_by(Department.code)
        )
    ).all()

    return [
        DepartmentOut(
            id=d.id,
            code=d.code,
            name=d.name,
            contact_email=d.contact_email,
            camera_count=count,
        )
        for d, count in rows
    ]


@router.get(
    "/vms",
    response_model=list[VmsInstanceOut],
    summary="Federated VMS instances",
)
async def list_vms(
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
) -> list[VmsInstanceOut]:
    """The vendor systems this platform federates with.

    Each remains authoritative for its own recordings — this platform holds
    metadata and resolves streams on demand.
    """
    rows = (
        await session.execute(
            select(VmsInstance, func.count(Camera.id))
            .outerjoin(Camera, Camera.vms_id == VmsInstance.id)
            .group_by(VmsInstance.id)
            .order_by(VmsInstance.name)
        )
    ).all()

    return [
        VmsInstanceOut(
            id=v.id,
            name=v.name,
            vendor=v.vendor,
            adapter_type=v.adapter_type,
            base_url=v.base_url,
            status=v.status,
            last_sync_at=v.last_sync_at,
            camera_count=count,
        )
        for v, count in rows
    ]


@router.get(
    "/cameras/vocabularies",
    response_model=VendorEnumOut,
    summary="Enumerations for onboarding form dropdowns",
)
async def vocabularies(
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
) -> VendorEnumOut:
    """Valid values, served from the server so the UI cannot drift from it."""
    codes = (await session.scalars(select(Department.code).order_by(Department.code))).all()
    return VendorEnumOut(
        departments=list(codes),
        vendors=list(VmsVendor),
        adapter_types=list(AdapterType),
        camera_types=list(CameraType),
        protocols=list(Protocol),
        statuses=list(CameraStatus),
    )


# ══ Fleet views ═══════════════════════════════════════════════════════


@router.get(
    "/cameras/summary",
    response_model=FleetSummary,
    summary="Fleet rollup for the dashboard KPI strip",
)
async def fleet_summary(
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
) -> FleetSummary:
    return FleetSummary(**await camera_service.fleet_summary(session))


@router.get(
    "/cameras/geojson",
    response_model=GeoJSONFeatureCollection,
    summary="Camera estate as GeoJSON for the map",
)
async def cameras_geojson(
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
    department_code: str | None = None,
    district: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    vendor: str | None = None,
    camera_type: str | None = None,
    anpr_enabled: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=100_000)] = 5000,
) -> GeoJSONFeatureCollection:
    """RFC 7946 FeatureCollection, consumed directly by MapLibre.

    Clustering is done client-side by MapLibre's own cluster layer, which keeps
    zoom interactions instant instead of re-querying the server on every pan.
    """
    return await camera_service.to_geojson(
        session,
        limit=limit,
        department_code=department_code,
        district=district,
        status=status_filter,
        vendor=vendor,
        camera_type=camera_type,
        anpr_enabled=anpr_enabled,
    )


@router.get(
    "/cameras/nearby",
    response_model=list[CameraOut],
    summary="Cameras within a radius, nearest first",
)
async def cameras_nearby(
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
    lat: Annotated[float, Query(ge=-90, le=90, examples=[22.3039])],
    lon: Annotated[float, Query(ge=-180, le=180, examples=[70.8022])],
    radius_km: Annotated[float, Query(gt=0, le=500)] = 5.0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    department_code: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> list[CameraOut]:
    """Proximity search — "what did we have covering this location?"

    The operational question after an incident is which cameras could have seen
    it. Results carry ``distance_km`` and are ordered nearest first.
    """
    return await camera_service.find_nearby(
        session,
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        limit=limit,
        department_code=department_code,
        status=status_filter,
    )


@router.get(
    "/cameras/in-district/{district}",
    response_model=list[CameraOut],
    summary="Every camera in a district",
)
async def cameras_in_district(
    district: str,
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> list[CameraOut]:
    return await camera_service.find_in_district(session, district, limit=limit)


@router.get(
    "/cameras",
    response_model=CameraPage,
    summary="List cameras with filters and pagination",
)
async def list_cameras(
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    department_code: str | None = None,
    district: str | None = None,
    city: str | None = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    camera_type: str | None = None,
    vendor: str | None = None,
    anpr_enabled: bool | None = None,
    search: Annotated[
        str | None, Query(description="Match against code, name, or junction")
    ] = None,
) -> CameraPage:
    items, total = await camera_service.list_cameras(
        session,
        limit=limit,
        offset=offset,
        department_code=department_code,
        district=district,
        city=city,
        status=status_filter,
        camera_type=camera_type,
        vendor=vendor,
        anpr_enabled=anpr_enabled,
        search=search,
    )
    return CameraPage(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/cameras/{camera_id}",
    response_model=CameraOut,
    summary="One camera by id",
)
async def get_camera(
    camera_id: uuid.UUID,
    session: DbSession,
    _user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_READ))],
) -> CameraOut:
    camera = await camera_service.get_camera(session, camera_id)
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")
    return camera_service.to_out(camera)


# ══ Mutations ═════════════════════════════════════════════════════════


@router.post(
    "/cameras",
    response_model=CameraOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a camera",
)
async def create_camera(
    payload: CameraCreate,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_CREATE))],
) -> CameraOut:
    """Register a single camera.

    Available to the ``api_client`` role, which is how another department
    self-registers its estate with an API key. That role holds this permission
    and nothing else, so a leaked onboarding key cannot read the fleet, search,
    or view alerts.
    """
    if await camera_service.get_camera_by_code(session, payload.camera_code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Camera code {payload.camera_code} is already registered",
        )

    try:
        camera = await camera_service.create_camera(session, payload)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Camera could not be registered: {exc.orig}",
        ) from exc

    await audit.record(
        action="camera.create",
        user=user,
        request=request,
        resource_type="camera",
        resource_id=camera.id,
        params={"camera_code": camera.camera_code, "district": camera.district},
    )
    log.info("camera.created", camera_code=camera.camera_code, by=user.username)

    # Tell the gateway to pull this camera's source. Without it the stream URL
    # the operator just typed is stored and never used: everything downstream
    # reads the camera from `rtsp://<gateway>/<code>`, and nothing would ever
    # have published it there.
    await gateway.register(camera)

    return camera_service.to_out(camera)


@router.patch(
    "/cameras/{camera_id}",
    response_model=CameraOut,
    summary="Update a camera",
)
async def update_camera(
    camera_id: uuid.UUID,
    payload: CameraUpdate,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_UPDATE))],
) -> CameraOut:
    camera = await camera_service.get_camera(session, camera_id)
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    changed = payload.model_dump(exclude_unset=True)
    camera = await camera_service.update_camera(session, camera, payload)

    await audit.record(
        action="camera.update",
        user=user,
        request=request,
        resource_type="camera",
        resource_id=camera.id,
        # Record which fields changed — an audit row saying only "updated" does
        # not answer the question a reviewer is asking.
        params={"camera_code": camera.camera_code, "fields": sorted(changed)},
    )

    # Only when the source actually moved. Re-registering on every edit would
    # tear down a live pull because somebody corrected a spelling.
    if "stream_url" in changed:
        if camera.stream_url:
            await gateway.register(camera)
        else:
            await gateway.unregister(camera.camera_code)

    return camera_service.to_out(camera)


@router.delete(
    "/cameras/{camera_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a camera from the registry (admin only)",
)
async def delete_camera(
    camera_id: uuid.UUID,
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_DELETE))],
) -> Response:
    """Delete a camera.

    Detections already recorded from it are retained — their ``camera_id``
    becomes NULL rather than cascading. Deleting a camera must not delete
    evidence gathered while it was operating.
    """
    camera = await camera_service.get_camera(session, camera_id)
    if camera is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Camera not found")

    code = camera.camera_code
    await session.delete(camera)

    await audit.record(
        action="camera.delete",
        user=user,
        request=request,
        resource_type="camera",
        resource_id=camera_id,
        params={"camera_code": code},
    )
    log.warning("camera.deleted", camera_code=code, by=user.username)

    # Leave the gateway holding no path for a camera that no longer exists —
    # otherwise it keeps trying to reach a source nobody can now see or manage.
    await gateway.unregister(code)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/cameras/bulk",
    response_model=BulkUploadResult,
    summary="Bulk-onboard cameras from CSV",
)
async def bulk_upload(
    request: Request,
    session: DbSession,
    user: Annotated[CurrentUser, Depends(require_permission(Permission.CAMERA_CREATE))],
    file: Annotated[UploadFile, File(description="UTF-8 CSV")],
    dry_run: Annotated[bool, Query(description="Validate without writing anything")] = False,
    update_existing: Annotated[
        bool, Query(description="Update cameras whose code already exists")
    ] = True,
) -> BulkUploadResult:
    """Onboard many cameras at once.

    Required columns: ``camera_code``, ``name``, ``lat``, ``lon``. Everything
    else is optional; unknown columns are ignored so a department's own
    spreadsheet export usually works unmodified.

    Valid rows are committed even when others fail — a file of 4,000 cameras
    should not be rejected wholesale because three rows have a typo. The
    response reports every failure by row number. Use ``dry_run`` to check a
    file before committing to it.
    """
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )

    try:
        result = await camera_service.bulk_upload(
            session, content, dry_run=dry_run, update_existing=update_existing
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await audit.record(
        action="camera.bulk_upload",
        user=user,
        request=request,
        resource_type="camera",
        params={
            "filename": file.filename,
            "created": result.created,
            "updated": result.updated,
            "failed": result.failed,
            "dry_run": dry_run,
        },
    )

    # One reconcile for the whole file rather than a registration per row: a
    # four-thousand-camera estate would otherwise make four thousand calls to
    # the gateway while the operator waits for the response.
    if not dry_run and (result.created or result.updated):
        await gateway.reconcile()

    return result
