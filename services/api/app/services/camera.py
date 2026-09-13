"""Camera registry business logic: queries, GIS, and bulk onboarding.

Kept out of the router so the same operations serve HTTP requests, the seed
script, and the Phase 3 adapters that sync cameras in from federated VMS.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Any

from geoalchemy2 import Geometry
from geoalchemy2.functions import ST_Distance, ST_DWithin, ST_MakePoint, ST_SetSRID
from pydantic import ValidationError
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.registry import Camera, Department, VmsInstance
from app.schemas.camera import (
    BulkRowError,
    BulkUploadResult,
    CameraCreate,
    CameraOut,
    CameraUpdate,
    GeoJSONFeature,
    GeoJSONFeatureCollection,
    GeoJSONGeometry,
)

log = get_logger("api.cameras")

#: Hard ceiling on any page of cameras. Even an admin cannot ask for the whole
#: 80,000-camera estate in one response.
MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 50

#: Ceiling on a single CSV upload, so one request cannot exhaust memory.
MAX_BULK_ROWS = 10_000


def _point(lat: float, lon: float):
    """Build a WGS-84 geography point. Note the (lon, lat) argument order."""
    return ST_SetSRID(ST_MakePoint(lon, lat), 4326)


def to_out(camera: Camera, *, distance_km: float | None = None) -> CameraOut:
    """Map an ORM row to the API shape, flattening coordinates and relations."""
    # Read coordinates through PostGIS-provided attributes populated by the
    # query; falling back to shapely keeps this usable for freshly-created rows.
    lat, lon = _extract_coordinates(camera)

    return CameraOut(
        id=camera.id,
        camera_code=camera.camera_code,
        name=camera.name,
        department_id=camera.department_id,
        department_code=camera.department.code if camera.department else None,
        department_name=camera.department.name if camera.department else None,
        vms_id=camera.vms_id,
        vms_name=camera.vms.name if camera.vms else None,
        vms_vendor=camera.vms.vendor if camera.vms else None,
        district=camera.district,
        city=camera.city,
        junction=camera.junction,
        address=camera.address,
        lat=lat,
        lon=lon,
        heading_deg=camera.heading_deg,
        camera_type=camera.camera_type,
        protocol=camera.protocol,
        resolution=camera.resolution,
        fps=camera.fps,
        anpr_enabled=camera.anpr_enabled,
        status=camera.status,
        installed_on=camera.installed_on,
        tags=camera.tags,
        # A boolean, never the URL. See CameraOut.has_stream.
        has_stream=bool(camera.stream_url),
        # Returned in full, unlike stream_url: a clip name carries no
        # credentials, and the edit form cannot show which clip is currently
        # pinned without it.
        source_file=camera.source_file,
        created_at=camera.created_at,
        updated_at=camera.updated_at,
        distance_km=distance_km,
    )


def _extract_coordinates(camera: Camera) -> tuple[float, float]:
    """Return ``(lat, lon)`` from a geography column.

    GeoAlchemy2 hands back a WKBElement; shapely decodes it. Values cached on
    the instance by a query that already selected them take precedence.
    """
    cached = getattr(camera, "_lat_lon", None)
    if cached is not None:
        return cached

    from geoalchemy2.shape import to_shape

    point = to_shape(camera.location)
    return point.y, point.x  # shapely is (x=lon, y=lat)


def _base_query() -> Select[tuple[Camera]]:
    """Camera query with relations eagerly loaded.

    ``selectinload`` rather than lazy loading: a lazy relation accessed while
    serialising would emit a query per camera (the N+1 that turns a 250-row
    page into 501 round trips), and in async SQLAlchemy it raises outright.
    """
    return select(Camera).options(selectinload(Camera.department), selectinload(Camera.vms))


def apply_filters(
    stmt: Select[Any],
    *,
    department_code: str | None = None,
    district: str | None = None,
    city: str | None = None,
    status: str | None = None,
    camera_type: str | None = None,
    vendor: str | None = None,
    anpr_enabled: bool | None = None,
    search: str | None = None,
) -> Select[Any]:
    """Apply the map/table filter panel to a query."""
    if department_code:
        stmt = stmt.join(Department, Camera.department_id == Department.id).where(
            Department.code == department_code.upper()
        )
    if district:
        stmt = stmt.where(func.lower(Camera.district) == district.lower())
    if city:
        stmt = stmt.where(func.lower(Camera.city) == city.lower())
    if status:
        stmt = stmt.where(Camera.status == status)
    if camera_type:
        stmt = stmt.where(Camera.camera_type == camera_type)
    if vendor:
        stmt = stmt.join(VmsInstance, Camera.vms_id == VmsInstance.id).where(
            VmsInstance.vendor == vendor
        )
    if anpr_enabled is not None:
        stmt = stmt.where(Camera.anpr_enabled == anpr_enabled)
    if search:
        # Operators search by code, name, or junction interchangeably.
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            func.lower(Camera.camera_code).like(pattern)
            | func.lower(Camera.name).like(pattern)
            | func.lower(func.coalesce(Camera.junction, "")).like(pattern)
        )
    return stmt


async def list_cameras(
    session: AsyncSession,
    *,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    **filters: Any,
) -> tuple[list[CameraOut], int]:
    """Return one page of cameras plus the total matching count."""
    limit = min(max(limit, 1), MAX_PAGE_SIZE)
    offset = max(offset, 0)

    count_stmt = apply_filters(select(func.count(Camera.id)), **filters)
    total = await session.scalar(count_stmt) or 0

    stmt = (
        apply_filters(_base_query(), **filters)
        .order_by(Camera.camera_code)
        .limit(limit)
        .offset(offset)
    )
    cameras = (await session.scalars(stmt)).all()
    return [to_out(c) for c in cameras], total


async def get_camera(session: AsyncSession, camera_id: uuid.UUID) -> Camera | None:
    return await session.scalar(_base_query().where(Camera.id == camera_id))


async def get_camera_by_code(session: AsyncSession, code: str) -> Camera | None:
    return await session.scalar(_base_query().where(Camera.camera_code == code.upper()))


async def find_nearby(
    session: AsyncSession,
    *,
    lat: float,
    lon: float,
    radius_km: float,
    limit: int = 50,
    **filters: Any,
) -> list[CameraOut]:
    """Cameras within ``radius_km``, nearest first.

    Uses ``ST_DWithin`` on the geography column so the GiST index does the
    work — a bounding-box prefilter followed by exact distance. Ordering by
    ``ST_Distance`` alone would force a full scan and compute a distance for
    every camera in the state.

    Geography (not geometry) means distances are true metres on a spheroid,
    which matters across Gujarat's ~700 km span.
    """
    limit = min(max(limit, 1), MAX_PAGE_SIZE)
    origin = _point(lat, lon)
    distance_m = ST_Distance(Camera.location, origin)

    stmt = (
        apply_filters(
            select(Camera, distance_m.label("distance_m")).options(
                selectinload(Camera.department), selectinload(Camera.vms)
            ),
            **filters,
        )
        .where(ST_DWithin(Camera.location, origin, radius_km * 1000))
        .order_by(distance_m)
        .limit(limit)
    )

    rows = (await session.execute(stmt)).all()
    return [to_out(camera, distance_km=round(distance / 1000.0, 3)) for camera, distance in rows]


async def find_in_district(
    session: AsyncSession, district: str, *, limit: int = MAX_PAGE_SIZE
) -> list[CameraOut]:
    """Every camera in a named district."""
    stmt = (
        _base_query()
        .where(func.lower(Camera.district) == district.lower())
        .order_by(Camera.camera_code)
        .limit(limit)
    )
    return [to_out(c) for c in (await session.scalars(stmt)).all()]


async def to_geojson(
    session: AsyncSession, *, limit: int = 5000, **filters: Any
) -> GeoJSONFeatureCollection:
    """Camera estate as an RFC 7946 FeatureCollection for MapLibre.

    Returned in the shape MapLibre consumes directly, so the browser does no
    transformation before rendering the fleet.
    """
    # Extract coordinates in SQL rather than decoding WKB per row in Python —
    # at fleet scale that difference is most of the response time. ST_X/ST_Y
    # operate on geometry, so the geography column is cast for the projection.
    location_geom = Camera.location.cast(Geometry)

    stmt = apply_filters(
        select(
            Camera.id,
            Camera.camera_code,
            Camera.name,
            Camera.district,
            Camera.city,
            Camera.junction,
            Camera.status,
            Camera.camera_type,
            Camera.anpr_enabled,
            Camera.heading_deg,
            Department.code.label("department_code"),
            Department.name.label("department_name"),
            VmsInstance.vendor.label("vendor"),
            func.ST_Y(location_geom).label("lat"),
            func.ST_X(location_geom).label("lon"),
        ).select_from(Camera),
        **filters,
    )
    # Outer joins so a camera without a department or VMS still appears on the
    # map — an unassigned camera is exactly what an administrator needs to see.
    stmt = stmt.outerjoin(Department, Camera.department_id == Department.id)
    stmt = stmt.outerjoin(VmsInstance, Camera.vms_id == VmsInstance.id)
    stmt = stmt.limit(limit)

    rows = (await session.execute(stmt)).all()

    features = [
        GeoJSONFeature(
            id=str(row.camera_code),
            geometry=GeoJSONGeometry(coordinates=(row.lon, row.lat)),
            properties={
                "id": str(row.id),
                "camera_code": row.camera_code,
                "name": row.name,
                "district": row.district,
                "city": row.city,
                "junction": row.junction,
                "status": row.status,
                "camera_type": row.camera_type,
                "anpr_enabled": row.anpr_enabled,
                "heading_deg": row.heading_deg,
                "department_code": row.department_code,
                "department_name": row.department_name,
                "vendor": row.vendor,
            },
        )
        for row in rows
    ]

    status_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1

    return GeoJSONFeatureCollection(
        features=features,
        properties={"count": len(features), "by_status": status_counts},
    )


async def fleet_summary(session: AsyncSession) -> dict[str, Any]:
    """Aggregate counts for the dashboard KPI strip.

    Three grouped queries rather than fetching every camera and counting in
    Python — the difference between a constant-time dashboard and one that
    slows down as the estate grows.
    """
    total = await session.scalar(select(func.count(Camera.id))) or 0

    status_rows = (
        await session.execute(select(Camera.status, func.count(Camera.id)).group_by(Camera.status))
    ).all()

    dept_rows = (
        await session.execute(
            select(Department.code, func.count(Camera.id))
            .select_from(Camera)
            .outerjoin(Department, Camera.department_id == Department.id)
            .group_by(Department.code)
        )
    ).all()

    district_rows = (
        await session.execute(
            select(Camera.district, func.count(Camera.id))
            .group_by(Camera.district)
            .order_by(func.count(Camera.id).desc())
        )
    ).all()

    anpr = (
        await session.scalar(select(func.count(Camera.id)).where(Camera.anpr_enabled.is_(True)))
        or 0
    )

    return {
        "total": total,
        "by_status": {s or "unknown": c for s, c in status_rows},
        "by_department": {d or "unassigned": c for d, c in dept_rows},
        "by_district": {d or "unassigned": c for d, c in district_rows},
        "anpr_enabled": anpr,
    }


# ── Mutations ─────────────────────────────────────────────────────────


async def _resolve_department(session: AsyncSession, code: str | None) -> uuid.UUID | None:
    if not code:
        return None
    return await session.scalar(select(Department.id).where(Department.code == code.upper()))


async def _resolve_vms(session: AsyncSession, name: str | None) -> uuid.UUID | None:
    if not name:
        return None
    return await session.scalar(select(VmsInstance.id).where(VmsInstance.name == name))


async def create_camera(session: AsyncSession, payload: CameraCreate) -> Camera:
    """Register one camera. Raises IntegrityError on a duplicate code."""
    camera = Camera(
        camera_code=payload.camera_code,
        name=payload.name,
        department_id=await _resolve_department(session, payload.department_code),
        vms_id=await _resolve_vms(session, payload.vms_name),
        district=payload.district,
        city=payload.city,
        junction=payload.junction,
        address=payload.address,
        location=_point(payload.lat, payload.lon),
        heading_deg=payload.heading_deg,
        camera_type=payload.camera_type.value if payload.camera_type else None,
        protocol=payload.protocol.value if payload.protocol else None,
        stream_url=payload.stream_url,
        sub_stream_url=payload.sub_stream_url,
        # Omitted here until now, so a camera registered with a clip already
        # chosen was stored without one and fell back to the round-robin. The
        # field list is explicit rather than a model_dump, which is what let a
        # newly added column go missing silently; `update_camera` sets it
        # generically and so never had the gap.
        source_file=payload.source_file,
        resolution=payload.resolution,
        fps=payload.fps,
        anpr_enabled=payload.anpr_enabled,
        installed_on=payload.installed_on,
        tags=payload.tags,
    )
    session.add(camera)
    await session.flush()
    # Re-read with relations eagerly loaded. After a flush, server-generated
    # columns (created_at/updated_at) are expired and relationships unloaded;
    # touching either during serialisation emits lazy IO, which async
    # SQLAlchemy refuses outright with MissingGreenlet.
    return await get_camera(session, camera.id) or camera


async def update_camera(session: AsyncSession, camera: Camera, payload: CameraUpdate) -> Camera:
    """Apply a partial update."""
    data = payload.model_dump(exclude_unset=True)

    if "department_code" in data:
        camera.department_id = await _resolve_department(session, data.pop("department_code"))
    if "lat" in data and "lon" in data:
        camera.location = _point(data.pop("lat"), data.pop("lon"))

    for field, value in data.items():
        # Enum-valued fields are stored as their string value.
        setattr(camera, field, value.value if hasattr(value, "value") else value)

    await session.flush()
    # As in create_camera: re-read so updated_at and relations are populated.
    return await get_camera(session, camera.id) or camera


# ── Bulk CSV onboarding ───────────────────────────────────────────────

#: Columns a bulk upload accepts. camera_code, name, lat and lon are required.
CSV_COLUMNS = (
    "camera_code",
    "name",
    "department_code",
    "vms_name",
    "district",
    "city",
    "junction",
    "address",
    "lat",
    "lon",
    "heading_deg",
    "camera_type",
    "protocol",
    "stream_url",
    "sub_stream_url",
    "resolution",
    "fps",
    "anpr_enabled",
    "installed_on",
    "tags",
)

_TRUE_VALUES = {"true", "yes", "y", "1", "t"}
_FALSE_VALUES = {"false", "no", "n", "0", "f"}


def _clean_row(raw: dict[str, str]) -> dict[str, Any]:
    """Normalise one CSV row into something Pydantic can validate.

    Real onboarding files come from spreadsheets exported by a dozen different
    departments: blank strings, stray whitespace, and 'YES'/'Y'/'1' for booleans
    are the norm, not the exception.
    """
    row: dict[str, Any] = {}
    for key, value in raw.items():
        if key is None:
            continue
        key = key.strip().lower()
        if key not in CSV_COLUMNS:
            continue  # ignore extra columns rather than failing the row
        value = (value or "").strip()
        if value == "":
            continue  # absent, so the schema default applies
        row[key] = value

    for numeric in ("lat", "lon"):
        if numeric in row:
            row[numeric] = float(row[numeric])
    for integer in ("heading_deg", "fps"):
        if integer in row:
            row[integer] = int(float(row[integer]))

    if "anpr_enabled" in row:
        text = str(row["anpr_enabled"]).lower()
        if text in _TRUE_VALUES:
            row["anpr_enabled"] = True
        elif text in _FALSE_VALUES:
            row["anpr_enabled"] = False
        else:
            raise ValueError(f"anpr_enabled: cannot interpret {row['anpr_enabled']!r}")

    if "installed_on" in row:
        row["installed_on"] = date.fromisoformat(str(row["installed_on"]))

    if "tags" in row:
        row["tags"] = [t.strip() for t in str(row["tags"]).split("|") if t.strip()]

    return row


async def bulk_upload(
    session: AsyncSession,
    content: bytes,
    *,
    dry_run: bool = False,
    update_existing: bool = True,
) -> BulkUploadResult:
    """Import cameras from CSV, validating row by row.

    Valid rows are committed even when others fail. A department onboarding
    4,000 cameras must not lose the 3,997 good ones because three had a typo —
    they get a row-numbered error report for the rest.

    ``dry_run`` validates without writing, so an operator can check a file
    before committing to it.
    """
    try:
        text = content.decode("utf-8-sig")  # tolerate the BOM Excel writes
    except UnicodeDecodeError as exc:
        raise ValueError(f"File is not valid UTF-8 text: {exc}") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("CSV has no header row")

    headers = {h.strip().lower() for h in reader.fieldnames if h}
    missing = {"camera_code", "name", "lat", "lon"} - headers
    if missing:
        raise ValueError(f"CSV is missing required column(s): {', '.join(sorted(missing))}")

    created = updated = skipped = failed = 0
    errors: list[BulkRowError] = []

    for index, raw in enumerate(reader, start=2):  # row 1 is the header
        if index - 1 > MAX_BULK_ROWS:
            errors.append(
                BulkRowError(
                    row=index,
                    errors=[f"File exceeds the {MAX_BULK_ROWS}-row limit; split it"],
                )
            )
            failed += 1
            break

        code = (raw.get("camera_code") or "").strip().upper() or None

        try:
            payload = CameraCreate(**_clean_row(raw))
        except (ValidationError, ValueError, TypeError) as exc:
            failed += 1
            errors.append(BulkRowError(row=index, camera_code=code, errors=_format_errors(exc)))
            continue

        existing = await get_camera_by_code(session, payload.camera_code)

        if existing is not None and not update_existing:
            skipped += 1
            continue

        if dry_run:
            if existing is None:
                created += 1
            else:
                updated += 1
            continue

        try:
            if existing is None:
                await create_camera(session, payload)
                created += 1
            else:
                await update_camera(
                    session,
                    existing,
                    CameraUpdate(
                        **payload.model_dump(exclude={"camera_code", "vms_name"}, exclude_none=True)
                    ),
                )
                updated += 1
        except IntegrityError as exc:
            # Roll back to a savepoint so one bad row does not poison the
            # transaction and take the whole file down with it.
            await session.rollback()
            failed += 1
            errors.append(
                BulkRowError(
                    row=index,
                    camera_code=payload.camera_code,
                    errors=[f"Database rejected the row: {exc.orig}"],
                )
            )

    total = created + updated + skipped + failed
    log.info(
        "cameras.bulk_upload",
        total=total,
        created=created,
        updated=updated,
        skipped=skipped,
        failed=failed,
        dry_run=dry_run,
    )

    return BulkUploadResult(
        total_rows=total,
        created=created,
        updated=updated,
        skipped=skipped,
        failed=failed,
        errors=errors,
        dry_run=dry_run,
    )


def _format_errors(exc: Exception) -> list[str]:
    """Turn a validation failure into messages an operator can act on."""
    if isinstance(exc, ValidationError):
        out = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "row"
            out.append(f"{field}: {err['msg']}")
        return out
    return [str(exc)]
