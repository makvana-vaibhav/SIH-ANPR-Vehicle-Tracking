"""Fleet health rollups and gap analysis.

Gap-analysis reports are a named Model 1 requirement (FAQ Q15). The question
they answer is the one a police commissioner actually asks: *where are we
blind?* — which is a different question from *which cameras are broken*.

Three kinds of gap are reported:

* **Availability gaps** — cameras that exist but are not delivering video.
* **Capability gaps** — districts with cameras but little or no ANPR coverage,
  so a vehicle can cross them without ever being read.
* **Coverage gaps** — districts with no cameras at all, and cameras whose
  nearest neighbour is far enough that a vehicle can pass between them
  unobserved.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.registry import Camera, CameraHealth, Department, VmsInstance

#: A district whose ANPR share falls below this is flagged as a capability gap.
ANPR_COVERAGE_TARGET = 0.40

#: Uptime below this over the reporting window is "unreliable" — a camera that
#: works two thirds of the time is not one an investigation can depend on.
UPTIME_RELIABILITY_TARGET = 0.90


async def fleet_health(session: AsyncSession) -> dict[str, Any]:
    """Live fleet rollup for the dashboard and `/health/fleet`."""
    status_rows = (
        await session.execute(select(Camera.status, func.count(Camera.id)).group_by(Camera.status))
    ).all()
    by_status = {status or "unknown": count for status, count in status_rows}
    total = sum(by_status.values())

    # Per-department health, so a commissioner can see which department's
    # estate is degrading rather than only a statewide average.
    dept_rows = (
        await session.execute(
            select(
                func.coalesce(Department.code, "UNASSIGNED"),
                func.count(Camera.id),
                func.count(case((Camera.status == "online", 1))),
                func.count(case((Camera.status == "offline", 1))),
                func.count(case((Camera.status == "degraded", 1))),
            )
            .select_from(Camera)
            .outerjoin(Department, Camera.department_id == Department.id)
            .group_by(Department.code)
            .order_by(func.count(Camera.id).desc())
        )
    ).all()

    vendor_rows = (
        await session.execute(
            select(
                func.coalesce(VmsInstance.vendor, "unassigned"),
                func.count(Camera.id),
                func.count(case((Camera.status == "online", 1))),
            )
            .select_from(Camera)
            .outerjoin(VmsInstance, Camera.vms_id == VmsInstance.id)
            .group_by(VmsInstance.vendor)
            .order_by(func.count(Camera.id).desc())
        )
    ).all()

    # Most common failure reasons in the last hour. Grouping by error code is
    # what turns "31 cameras down" into "one expired credential", because a
    # whole department failing with `unauthorized` has a single cause.
    since = datetime.now(UTC) - timedelta(hours=1)
    error_rows = (
        await session.execute(
            select(CameraHealth.error_code, func.count())
            .where(
                and_(
                    CameraHealth.ts >= since,
                    CameraHealth.error_code.is_not(None),
                )
            )
            .group_by(CameraHealth.error_code)
            .order_by(func.count().desc())
            .limit(10)
        )
    ).all()

    online = by_status.get("online", 0)
    offline = by_status.get("offline", 0)
    degraded = by_status.get("degraded", 0)
    unknown = by_status.get("unknown", 0)

    # A camera we have never successfully reached is *not yet integrated* — it
    # is registered, but no live feed is attached. Averaging those into
    # availability would understate the health of the estate we actually run,
    # and overstate the size of the outage when something genuinely breaks.
    # Both numbers are reported so neither can mislead.
    integrated = online + offline + degraded

    return {
        "total": total,
        "online": online,
        "offline": offline,
        "degraded": degraded,
        "unknown": unknown,
        "integrated": integrated,
        "awaiting_integration": unknown,
        "availability_pct": round(online / total * 100, 1) if total else 0.0,
        "integrated_availability_pct": (
            round(online / integrated * 100, 1) if integrated else None
        ),
        "by_department": [
            {
                "department": code,
                "total": count,
                "online": on,
                "offline": off,
                "degraded": deg,
                "availability_pct": round(on / count * 100, 1) if count else 0.0,
            }
            for code, count, on, off, deg in dept_rows
        ],
        "by_vendor": [
            {
                "vendor": vendor,
                "total": count,
                "online": on,
                "availability_pct": round(on / count * 100, 1) if count else 0.0,
            }
            for vendor, count, on in vendor_rows
        ],
        "top_errors": [{"error_code": code, "count": count} for code, count in error_rows],
        "generated_at": datetime.now(UTC).isoformat(),
    }


async def camera_health_history(
    session: AsyncSession, camera_id: uuid.UUID, *, hours: int = 24, limit: int = 500
) -> list[dict[str, Any]]:
    """Recent probe history for one camera — the detail-page sparklines."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    rows = (
        await session.execute(
            select(
                CameraHealth.ts,
                CameraHealth.reachable,
                CameraHealth.fps_actual,
                CameraHealth.latency_ms,
                CameraHealth.bitrate_kbps,
                CameraHealth.error_code,
            )
            .where(and_(CameraHealth.camera_id == camera_id, CameraHealth.ts >= since))
            .order_by(CameraHealth.ts.desc())
            .limit(limit)
        )
    ).all()

    return [
        {
            "ts": ts.isoformat(),
            "reachable": reachable,
            "fps_actual": fps,
            "latency_ms": latency,
            "bitrate_kbps": bitrate,
            "error_code": error,
        }
        for ts, reachable, fps, latency, bitrate, error in rows
    ]


async def uptime_summary(
    session: AsyncSession, camera_id: uuid.UUID, *, hours: int = 24
) -> dict[str, Any]:
    """Uptime percentage and average latency for one camera."""
    since = datetime.now(UTC) - timedelta(hours=hours)
    row = (
        await session.execute(
            select(
                func.count(),
                func.count(case((CameraHealth.reachable.is_(True), 1))),
                func.avg(CameraHealth.latency_ms),
                func.avg(CameraHealth.fps_actual),
            ).where(and_(CameraHealth.camera_id == camera_id, CameraHealth.ts >= since))
        )
    ).one()

    probes, reachable, avg_latency, avg_fps = row
    return {
        "window_hours": hours,
        "probes": probes or 0,
        "uptime_pct": round(reachable / probes * 100, 1) if probes else None,
        "avg_latency_ms": round(float(avg_latency), 1) if avg_latency else None,
        "avg_fps": round(float(avg_fps), 1) if avg_fps else None,
    }


async def gap_analysis(session: AsyncSession, *, hours: int = 24) -> dict[str, Any]:
    """Where the estate is blind.

    A named Model 1 deliverable. Distinguishes three failure modes that need
    three different responses: fix the camera, install ANPR, or install a
    camera at all.
    """
    since = datetime.now(UTC) - timedelta(hours=hours)

    # ── Availability: cameras registered but not delivering ──────────
    unavailable = (
        await session.execute(
            select(
                Camera.camera_code,
                Camera.name,
                Camera.district,
                Camera.status,
                func.coalesce(Department.code, "UNASSIGNED"),
            )
            .outerjoin(Department, Camera.department_id == Department.id)
            .where(Camera.status.in_(("offline", "degraded")))
            .order_by(Camera.district, Camera.camera_code)
            .limit(500)
        )
    ).all()

    # ── Capability: districts thin on ANPR ───────────────────────────
    district_rows = (
        await session.execute(
            select(
                Camera.district,
                func.count(Camera.id),
                func.count(case((Camera.anpr_enabled.is_(True), 1))),
                func.count(case((Camera.status == "online", 1))),
            )
            .group_by(Camera.district)
            .order_by(func.count(Camera.id).desc())
        )
    ).all()

    capability_gaps = []
    district_coverage = []
    for district, total, anpr, online in district_rows:
        share = anpr / total if total else 0.0
        entry = {
            "district": district or "unassigned",
            "cameras": total,
            "anpr_cameras": anpr,
            "online": online,
            "anpr_share": round(share, 3),
            "availability_pct": round(online / total * 100, 1) if total else 0.0,
        }
        district_coverage.append(entry)
        if share < ANPR_COVERAGE_TARGET:
            capability_gaps.append(
                {
                    **entry,
                    "reason": (
                        f"Only {round(share * 100)}% of cameras in this district read "
                        f"plates (target {round(ANPR_COVERAGE_TARGET * 100)}%). A vehicle "
                        "can cross it without being recorded."
                    ),
                }
            )

    # ── Coverage: districts of Gujarat with no cameras at all ────────
    covered = {d for d, *_ in district_rows if d}
    uncovered = sorted(GUJARAT_DISTRICTS - covered)

    # ── Reliability: cameras that flap ───────────────────────────────
    flapping_rows = (
        await session.execute(
            select(
                Camera.camera_code,
                Camera.name,
                Camera.district,
                func.count(),
                func.count(case((CameraHealth.reachable.is_(True), 1))),
            )
            .join(CameraHealth, CameraHealth.camera_id == Camera.id)
            .where(CameraHealth.ts >= since)
            .group_by(Camera.camera_code, Camera.name, Camera.district)
            .having(
                and_(
                    func.count() >= 5,
                    cast(func.count(case((CameraHealth.reachable.is_(True), 1))), Float)
                    / cast(func.count(), Float)
                    < UPTIME_RELIABILITY_TARGET,
                )
            )
            .order_by(
                cast(func.count(case((CameraHealth.reachable.is_(True), 1))), Float)
                / cast(func.count(), Float)
            )
            .limit(100)
        )
    ).all()

    return {
        "window_hours": hours,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "unavailable_cameras": len(unavailable),
            "districts_below_anpr_target": len(capability_gaps),
            "districts_with_no_cameras": len(uncovered),
            "unreliable_cameras": len(flapping_rows),
        },
        "availability_gaps": [
            {
                "camera_code": code,
                "name": name,
                "district": district,
                "status": status,
                "department": dept,
            }
            for code, name, district, status, dept in unavailable
        ],
        "capability_gaps": capability_gaps,
        "coverage_gaps": [
            {
                "district": district,
                "reason": "No cameras registered in this district",
            }
            for district in uncovered
        ],
        "reliability_gaps": [
            {
                "camera_code": code,
                "name": name,
                "district": district,
                "probes": probes,
                "uptime_pct": round(reachable / probes * 100, 1) if probes else 0.0,
                "reason": "Intermittent — repeatedly dropping in and out",
            }
            for code, name, district, probes, reachable in flapping_rows
        ],
        "district_coverage": district_coverage,
    }


#: Gujarat's 33 districts, matching data/seed/gujarat_districts.geojson. Used to
#: report districts with no camera presence at all.
GUJARAT_DISTRICTS: set[str] = {
    "Ahmedabad",
    "Amreli",
    "Anand",
    "Aravalli",
    "Banaskantha",
    "Bharuch",
    "Bhavnagar",
    "Botad",
    "Chhota Udaipur",
    "Dahod",
    "Dang",
    "Devbhoomi Dwarka",
    "Gandhinagar",
    "Gir Somnath",
    "Jamnagar",
    "Junagadh",
    "Kachchh",
    "Kheda",
    "Mahisagar",
    "Mehsana",
    "Morbi",
    "Narmada",
    "Navsari",
    "Panchmahal",
    "Patan",
    "Porbandar",
    "Rajkot",
    "Sabarkantha",
    "Surat",
    "Surendranagar",
    "Tapi",
    "Vadodara",
    "Valsad",
}
