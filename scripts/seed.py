"""Seed the platform with a working demo dataset.

Run with ``make seed``. Idempotent: safe to run repeatedly, it upserts rather
than duplicating, so a judge can re-run it without wiping the database first.

Phase 1 seeds departments and one user per role.
Phase 2 extends this with the 250-camera fleet and the watchlist.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# The script runs from /app inside the container; make the API package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal, dispose_engine  # noqa: E402
from app.models.enums import AdapterType, CameraStatus, Role, VmsVendor  # noqa: E402
from app.models.registry import Camera, Department, VmsInstance  # noqa: E402
from app.models.security import User  # noqa: E402
from app.services.camera import bulk_upload  # noqa: E402

SEED_DIR = Path("/data/seed")
if not SEED_DIR.exists():  # running outside the container
    SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"

# ── Departments ───────────────────────────────────────────────────────
# The five departments whose live feeds the challenge sandbox exposes, plus
# SCRB which operates the state aggregation tier.
DEPARTMENTS: list[dict[str, str]] = [
    {
        "code": "POLICE",
        "name": "Gujarat Police",
        "contact_email": "control@police.gujarat.gov.in",
    },
    {
        "code": "HEALTH",
        "name": "Department of Health & Family Welfare",
        "contact_email": "cctv@health.gujarat.gov.in",
    },
    {
        "code": "GSRTC",
        "name": "Gujarat State Road Transport Corporation",
        "contact_email": "security@gsrtc.gujarat.gov.in",
    },
    {
        "code": "PANCHAYAT",
        "name": "Panchayat, Rural Housing & Rural Development",
        "contact_email": "cctv@panchayat.gujarat.gov.in",
    },
    {
        "code": "MUNICIPAL",
        "name": "Urban Development & Urban Housing (Municipal)",
        "contact_email": "citysurveillance@udd.gujarat.gov.in",
    },
    {
        "code": "SCRB",
        "name": "State Crime Records Bureau",
        "contact_email": "scrb@gujarat.gov.in",
    },
]

# ── Federated VMS instances ───────────────────────────────────────────
# Four vendors plus the challenge's own camera grid. Each stays authoritative
# for its own recordings; this platform holds metadata and pulls streams on
# demand — the essence of Model 5.
#
# credentials_ref is a POINTER into a secret store, never a credential. That is
# enforced by convention here and documented in docs/SECURITY.md.
VMS_INSTANCES: list[dict[str, str]] = [
    {
        "name": "Rajkot City Command Centre",
        "vendor": VmsVendor.MILESTONE.value,
        "adapter_type": AdapterType.VENDOR_API.value,
        "base_url": "https://vms.rajkot.gujarat.gov.in/api",
        "credentials_ref": "vault://sentinel/vms/rajkot-milestone",
        "department": "MUNICIPAL",
    },
    {
        "name": "Ahmedabad Smart City VMS",
        "vendor": VmsVendor.GENETEC.value,
        "adapter_type": AdapterType.VENDOR_API.value,
        "base_url": "https://smartcity.ahmedabad.gov.in/vms/api",
        "credentials_ref": "vault://sentinel/vms/ahmedabad-genetec",
        "department": "MUNICIPAL",
    },
    {
        "name": "GSRTC Depot Surveillance",
        "vendor": VmsVendor.CPPLUS.value,
        "adapter_type": AdapterType.RTSP.value,
        "base_url": "rtsp://depot-nvr.gsrtc.gujarat.gov.in:554",
        "credentials_ref": "vault://sentinel/vms/gsrtc-cpplus",
        "department": "GSRTC",
    },
    {
        "name": "Gujarat Highway ANPR Grid",
        "vendor": VmsVendor.HIKVISION.value,
        "adapter_type": AdapterType.ONVIF.value,
        "base_url": "https://anpr.highways.gujarat.gov.in",
        "credentials_ref": "vault://sentinel/vms/highway-hikvision",
        "department": "POLICE",
    },
    {
        # The challenge sandbox (sentinel.gujarat.gov.in) publishes a camera
        # catalogue at /api/ingest and serves RTSP/WHEP/HLS. Phase 3's
        # SentinelSandboxAdapter federates it through this record.
        "name": "Sentinel Sandbox Grid",
        "vendor": VmsVendor.SENTINEL_SANDBOX.value,
        "adapter_type": AdapterType.SENTINEL_SANDBOX.value,
        "base_url": "https://sentinel.gujarat.gov.in",
        "credentials_ref": "vault://sentinel/vms/sandbox-grid",
        "department": "SCRB",
    },
]

# ── Demo users ────────────────────────────────────────────────────────
# One per role, so a judge can sign in as any role and see the RBAC matrix
# take effect. The admin password comes from the environment; the rest share
# a documented demo password. In production these accounts would not exist —
# see docs/SECURITY.md on account provisioning.
DEMO_PASSWORD = "Sentinel@2026"  # noqa: S105 - documented demo credential

DEMO_USERS: list[dict[str, str | None]] = [
    {
        "username": "admin",
        "full_name": "System Administrator",
        "role": Role.ADMIN.value,
        "department": "SCRB",
    },
    {
        "username": "supervisor",
        "full_name": "Control Room Supervisor",
        "role": Role.SUPERVISOR.value,
        "department": "POLICE",
    },
    {
        "username": "operator",
        "full_name": "Control Room Operator",
        "role": Role.OPERATOR.value,
        "department": "POLICE",
    },
    {
        "username": "analyst",
        "full_name": "Crime Analyst",
        "role": Role.ANALYST.value,
        "department": "SCRB",
    },
    {
        "username": "auditor",
        "full_name": "Compliance Auditor",
        "role": Role.AUDITOR.value,
        "department": "SCRB",
    },
    {
        "username": "gsrtc-integration",
        "full_name": "GSRTC Camera Onboarding Client",
        "role": Role.API_CLIENT.value,
        "department": "GSRTC",
    },
]


async def seed_departments() -> dict[str, object]:
    """Insert departments, returning ``{code: id}``."""
    created = 0
    ids: dict[str, object] = {}

    async with SessionLocal() as session:
        for spec in DEPARTMENTS:
            existing = await session.scalar(
                select(Department).where(Department.code == spec["code"])
            )
            if existing is None:
                dept = Department(
                    code=spec["code"],
                    name=spec["name"],
                    contact_email=spec["contact_email"],
                )
                session.add(dept)
                await session.flush()
                ids[spec["code"]] = dept.id
                created += 1
            else:
                ids[spec["code"]] = existing.id
        await session.commit()

    print(f"  departments: {created} created, {len(DEPARTMENTS) - created} already present")
    return ids


async def seed_users(department_ids: dict[str, object]) -> None:
    """Insert one user per role."""
    created = 0

    async with SessionLocal() as session:
        for spec in DEMO_USERS:
            username = str(spec["username"])
            existing = await session.scalar(
                select(User).where(User.username == username)
            )
            if existing is not None:
                continue

            # The admin password is configurable so a deployment can set a real
            # one; demo accounts use the documented shared password.
            password = (
                settings.bootstrap_admin_password
                if username == settings.bootstrap_admin_username
                else DEMO_PASSWORD
            )

            session.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    full_name=spec["full_name"],
                    role=str(spec["role"]),
                    department_id=department_ids.get(str(spec["department"])),
                    is_active=True,
                )
            )
            created += 1
        await session.commit()

    print(f"  users: {created} created, {len(DEMO_USERS) - created} already present")


async def seed_vms(department_ids: dict[str, object]) -> None:
    """Register the federated VMS instances."""
    created = 0
    async with SessionLocal() as session:
        for spec in VMS_INSTANCES:
            existing = await session.scalar(
                select(VmsInstance).where(VmsInstance.name == spec["name"])
            )
            if existing is not None:
                continue
            session.add(
                VmsInstance(
                    name=spec["name"],
                    vendor=spec["vendor"],
                    adapter_type=spec["adapter_type"],
                    base_url=spec["base_url"],
                    credentials_ref=spec["credentials_ref"],
                    department_id=department_ids.get(spec["department"]),
                    status=CameraStatus.UNKNOWN.value,
                )
            )
            created += 1
        await session.commit()
    print(f"  vms instances: {created} created, {len(VMS_INSTANCES) - created} already present")


async def seed_cameras() -> None:
    """Load the 250-camera fleet from data/seed/cameras.csv.

    Deliberately routed through the same `bulk_upload` the API exposes, so the
    seed exercises real onboarding code — validation, geographic bounds checks
    and all — rather than a private shortcut that could silently diverge.
    """
    csv_path = SEED_DIR / "cameras.csv"
    if not csv_path.exists():
        print(f"  cameras: SKIPPED — {csv_path} not found (run scripts/generate_cameras.py)")
        return

    content = csv_path.read_bytes()
    async with SessionLocal() as session:
        result = await bulk_upload(session, content, update_existing=True)
        await session.commit()

    print(
        f"  cameras: {result.created} created, {result.updated} updated, "
        f"{result.failed} failed"
    )
    for err in result.errors[:5]:
        print(f"    row {err.row} ({err.camera_code}): {'; '.join(err.errors)}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed Sentinel-GJ demo data")
    parser.add_argument(
        "--only",
        choices=["departments", "users", "vms", "cameras", "all"],
        default="all",
        help="Seed a single dataset instead of everything",
    )
    args = parser.parse_args()

    print("Seeding Sentinel-GJ")
    try:
        department_ids = await seed_departments()
        if args.only in ("users", "all"):
            await seed_users(department_ids)
        if args.only in ("vms", "all"):
            await seed_vms(department_ids)
        if args.only in ("cameras", "all"):
            await seed_cameras()
    finally:
        await dispose_engine()

    print("\nDemo credentials:")
    print(f"  admin / {settings.bootstrap_admin_password}")
    print(f"  supervisor · operator · analyst · auditor / {DEMO_PASSWORD}")
    print("\nSeed complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
