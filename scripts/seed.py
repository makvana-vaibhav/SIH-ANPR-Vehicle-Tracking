"""Seed the platform with a working demo dataset.

Run with ``make seed``. Idempotent: safe to run repeatedly, it upserts rather
than duplicating, so a judge can re-run it without wiping the database first.

Phase 1 seeds departments and one user per role.
Phase 2 extends this with the 250-camera fleet and the watchlist.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from datetime import datetime
from pathlib import Path

# The script runs from /app inside the container; make the API package importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import SessionLocal, dispose_engine  # noqa: E402
from app.models.enums import AdapterType, CameraStatus, Role, VmsVendor  # noqa: E402
from app.models.intelligence import Watchlist  # noqa: E402
from app.models.registry import Camera, Department, VmsInstance  # noqa: E402
from app.models.security import User  # noqa: E402
from app.schemas.intelligence import normalise_plate  # noqa: E402

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
# The federated systems. Each stays authoritative for its own recordings; this
# platform holds their metadata and pulls streams on demand — the essence of
# Model 5.
#
# credentials_ref is a POINTER into a secret store, never a credential. That is
# enforced by convention here and documented in docs/SECURITY.md.
#
# The registry lists only systems we genuinely federate.
#
# It used to seed four more — a Milestone deployment in Rajkot, a Genetec one
# in Ahmedabad, a CP Plus depot system and a Hikvision highway grid — all with
# `adapter_type: simulated`, none with a single camera behind them. On screen
# they read as four integrations the platform maintains. It maintains none of
# them, and no server of any of those vendors has ever been on the other end.
#
# The multi-vendor claim belongs where it is true: `/integration/adapters`
# reports the adapter interface's real implementations — RTSP, ONVIF, vendor
# REST, sandbox — which are code, and unit-tested. What a *connected system*
# list should show is what is connected.

#: The city fleet's recorder and the CSV that populates it. Defined before
#: VMS_INSTANCES because that list references FLEET_VMS at module level.
FLEET_VMS = "Ahmedabad City ANPR Grid (simulated)"
FLEET_CSV = SEED_DIR / "cameras.csv"

VMS_INSTANCES: list[dict[str, str]] = [
    {
        # The city fleet's recorder. `adapter_type: simulated` is the honest
        # label: footage is replayed into MediaMTX by services/simulator and
        # read back over RTSP, so every camera filed here has a stream that
        # genuinely resolves, can be opened, and can produce a detection.
        "name": FLEET_VMS,
        "vendor": VmsVendor.SIMULATED.value,
        "adapter_type": AdapterType.SIMULATED.value,
        "base_url": "",
        "credentials_ref": "vault://nagarnetra/vms/ahmedabad-city-grid",
        "department": "POLICE",
    },
    {
        # The challenge sandbox (sentinel.gujarat.gov.in) publishes a camera
        # catalogue at /api/ingest and serves RTSP/WHEP/HLS. Phase 3's
        # HostedGridAdapter federates it through this record.
        "name": "Hosted Camera Grid",
        "vendor": VmsVendor.HOSTED_GRID.value,
        "adapter_type": AdapterType.HOSTED_GRID.value,
        "base_url": "https://sentinel.gujarat.gov.in",
        "credentials_ref": "vault://nagarnetra/vms/sandbox-grid",
        "department": "SCRB",
    },
]

# ── Demo users ────────────────────────────────────────────────────────
# One per role, so a judge can sign in as any role and see the RBAC matrix
# take effect. The admin password comes from the environment; the rest share
# a documented demo password. In production these accounts would not exist —
# see docs/SECURITY.md on account provisioning.
DEMO_PASSWORD = "NagarNetra@2026"  # noqa: S105 - documented demo credential

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

    print(
        f"  departments: {created} created, {len(DEPARTMENTS) - created} already present"
    )
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
                # Reconcile: a changed adapter_type or base_url must reach an
                # already-seeded database, or re-running the seed silently
                # leaves stale integration settings in place.
                existing.vendor = spec["vendor"]
                existing.adapter_type = spec["adapter_type"]
                existing.base_url = spec["base_url"]
                existing.credentials_ref = spec["credentials_ref"]
                existing.department_id = department_ids.get(spec["department"])
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
    print(
        f"  vms instances: {created} created, {len(VMS_INSTANCES) - created} already present"
    )


#: The one camera allowed to carry recorded footage, named so nobody has to
#: guess. Its code is deliberately unlike the grid's, and its tags say plainly
#: what it is.
DEMO_CODE = "CAM-DEMO"
DEMO_NAME = "ANPR Demonstration Feed (recorded)"
DEMO_VMS = "NagarNetra ANPR Demonstration"
#: Ahmedabad city centre. It has to be somewhere to appear on the map at all,
#: and the name says it is a demonstration, so nothing here claims the footage
#: was shot at this point.
DEMO_LAT, DEMO_LON = 23.0225, 72.5714


async def seed_demo_camera() -> None:
    """The demonstration camera, and nothing else.

    This used to load 250 synthetic cameras from `data/seed/cameras.csv` so the
    GIS map had something to show at scale. That was a mistake and the file is
    gone: **251 of 281 cameras had no stream URL at all.** They sat permanently
    at status `unknown`, could never be watched, could never produce a
    detection, and made every count on every screen mostly fiction — fleet
    health was reporting on cameras that could not be unhealthy.

    The registry now holds only cameras with a real video source. This one, and
    the organisers' grid via `sync_sandbox.py`. Roughly 31 rather than 281, and
    every one of them can be opened, watched and analysed.

    The demonstration camera gets **its own VMS** rather than a department's:
    filing recorded footage under a real department would make it look like
    that department's live feed, which is exactly the confusion being removed.
    """
    async with SessionLocal() as session:
        vms = (
            await session.execute(
                select(VmsInstance).where(VmsInstance.name == DEMO_VMS)
            )
        ).scalar_one_or_none()
        if vms is None:
            vms = VmsInstance(
                name=DEMO_VMS,
                vendor=VmsVendor.GENERIC_RTSP.value,
                adapter_type=AdapterType.SIMULATED.value,
                base_url="rtsp://mediamtx:8554",
            )
            session.add(vms)
            await session.flush()

        department = (
            await session.execute(select(Department).order_by(Department.code).limit(1))
        ).scalar_one_or_none()

        camera = (
            await session.execute(select(Camera).where(Camera.camera_code == DEMO_CODE))
        ).scalar_one_or_none()
        created = camera is None
        if camera is None:
            camera = Camera(
                camera_code=DEMO_CODE,
                department_id=department.id if department else None,
                location=func.ST_SetSRID(func.ST_MakePoint(DEMO_LON, DEMO_LAT), 4326),
            )
            session.add(camera)

        camera.name = DEMO_NAME
        camera.vms_id = vms.id
        camera.anpr_enabled = True
        camera.camera_type = "anpr"
        camera.protocol = "rtsp"
        camera.city = "Ahmedabad"
        # The taluka actually containing DEMO_LAT/DEMO_LON, so the demo camera
        # groups with the rest of the fleet instead of inventing a "district"
        # named after the city that gap analysis could never reason about.
        camera.district = "Sabarmati"
        camera.junction = "Demonstration feed"
        camera.resolution = "1920x1080"
        camera.fps = 15
        camera.status = CameraStatus.UNKNOWN.value
        # `plate-region:GB` changes behaviour: the demonstration footage is
        # British, and a camera that sees British plates should have them read
        # as British plates. Which formats a camera sees is a property of where
        # it points, so it belongs on the camera rather than in a worker-wide
        # setting that would make the whole fleet accept UK plates.
        camera.tags = [
            "demo",
            "recorded-footage",
            "not-a-real-camera",
            "plate-region:GB",
        ]
        await session.commit()

    print(f"  demo camera: {DEMO_CODE} {'created' if created else 'refreshed'}")


async def seed_fleet() -> None:
    """Load the Ahmedabad ANPR fleet from data/seed/cameras.csv.

    The CSV is generated by `scripts/generate_ahmedabad_cameras.py`, which
    thins the committed OpenStreetMap arterial network so that **every camera
    sits on a real road vertex** of a real named corridor, in a real taluka,
    at least 250 m from any other camera. So each row is a position a camera
    could actually occupy.

    ## Why `stream_url` stays NULL

    This is the part that is easy to get wrong, and getting it wrong breaks the
    whole fleet rather than one camera.

    `gateway.reconcile()` runs at API startup and, for every camera that has a
    `stream_url` and whose adapter is not federated, tells MediaMTX to open a
    **pull** path from that URL. `simulated` is not in `FEDERATED_ADAPTERS`. So
    setting `stream_url` to the gateway's own address — the obvious thing to
    write, since that is where the stream really is — makes MediaMTX pull each
    path *from itself*: an infinite loop that silently blocks the simulator from
    publishing, on every camera, on every restart.

    These cameras are *published into* MediaMTX, not pulled from anywhere, so
    they have no upstream URL and the column stays NULL. `SimulatedVmsAdapter`
    derives the RTSP, WHEP and HLS URLs from `camera_code`, and probes health by
    asking MediaMTX which paths are actually live — which is a better health
    check than a stored string could ever be.

    This is not the fiction commit `4d0346f` deleted. Those 251 cameras had no
    source *at all*: nothing published them, they could never be watched and
    never produced a detection. Every camera here is published by the simulator
    and is genuinely watchable.
    """
    if not FLEET_CSV.exists():
        print(
            f"  fleet: SKIPPED — {FLEET_CSV.name} not found "
            f"(run scripts/generate_ahmedabad_cameras.py)"
        )
        return

    created = refreshed = 0
    async with SessionLocal() as session:
        vms = (
            await session.execute(
                select(VmsInstance).where(VmsInstance.name == FLEET_VMS)
            )
        ).scalar_one_or_none()
        if vms is None:
            print(f"  fleet: SKIPPED — VMS {FLEET_VMS!r} missing; seed vms first")
            return

        departments = {
            code: ident
            for code, ident in (
                await session.execute(select(Department.code, Department.id))
            ).all()
        }

        with FLEET_CSV.open(newline="") as handle:
            for row in csv.DictReader(handle):
                code = row["camera_code"].strip().upper()
                if not code:
                    continue
                lon, lat = float(row["lon"]), float(row["lat"])

                camera = (
                    await session.execute(
                        select(Camera).where(Camera.camera_code == code)
                    )
                ).scalar_one_or_none()
                if camera is None:
                    camera = Camera(camera_code=code)
                    session.add(camera)
                    created += 1
                else:
                    refreshed += 1

                camera.name = row["name"].strip()
                camera.department_id = departments.get(
                    row["department_code"].strip().upper()
                )
                camera.vms_id = vms.id
                camera.location = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
                camera.district = row["district"].strip()
                camera.city = row["city"].strip()
                camera.junction = row["junction"].strip()
                camera.heading_deg = int(row["heading_deg"])
                camera.camera_type = row["camera_type"].strip()
                camera.protocol = row["protocol"].strip()
                # Deliberately NULL — see the docstring. Do not "fix" this.
                camera.stream_url = None
                camera.resolution = row["resolution"].strip()
                camera.fps = int(row["fps"])
                camera.anpr_enabled = row["anpr_enabled"].strip().lower() == "true"
                # UNKNOWN, not ONLINE: the health monitor decides what is up.
                # Seeding a camera as online would be its own small lie.
                camera.status = CameraStatus.UNKNOWN.value
                camera.tags = [tag for tag in row["tags"].split("|") if tag]

        await session.commit()

    print(f"  fleet: {created} created, {refreshed} refreshed")


async def seed_watchlist() -> None:
    """Load the demo watchlist from data/seed/watchlist.csv.

    This file has existed since Phase 2 and **nothing read it**. The watchlist
    was populated by hand during development, so it survived on this machine
    and nowhere else: a fresh clone reached `make demo` with an empty watchlist
    and judge moments 3 and 4 — the automatic alert and the tracked vehicle —
    had nothing to fire on.

    Existing entries are **reactivated**, not skipped. Deleting a watchlist
    entry through the API deactivates it rather than removing it, which is
    right for an audit trail and means a demo plate retired during testing
    stays retired through every future re-seed unless seeding says otherwise.
    That is exactly how this database ended up with all eight of its entries
    inactive.
    """
    csv_path = SEED_DIR / "watchlist.csv"
    if not csv_path.exists():
        print(f"  watchlist: SKIPPED — {csv_path} not found")
        return

    created = reactivated = updated = 0
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    async with SessionLocal() as session:
        for row in rows:
            plate = normalise_plate(row["plate"])
            existing = (
                (
                    await session.execute(
                        select(Watchlist).where(Watchlist.plate_normalised == plate)
                    )
                )
                .scalars()
                .first()
            )

            valid_from = _timestamp(row.get("valid_from"))
            valid_to = _timestamp(row.get("valid_to"))

            if existing is None:
                session.add(
                    Watchlist(
                        plate_normalised=plate,
                        category=row["category"],
                        priority=row["priority"],
                        case_ref=row.get("case_ref") or None,
                        remarks=row.get("remarks") or None,
                        valid_from=valid_from,
                        valid_to=valid_to,
                        active=True,
                    )
                )
                created += 1
                continue

            if not existing.active:
                reactivated += 1
            else:
                updated += 1
            existing.category = row["category"]
            existing.priority = row["priority"]
            existing.case_ref = row.get("case_ref") or None
            existing.remarks = row.get("remarks") or None
            existing.valid_from = valid_from
            existing.valid_to = valid_to
            existing.active = True

        await session.commit()

    print(
        f"  watchlist: {created} created, {reactivated} reactivated, {updated} refreshed"
    )


def _timestamp(value: str | None) -> datetime | None:
    """Parse an ISO timestamp from the CSV, tolerating the Z suffix."""
    if not value or not value.strip():
        return None
    return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))


async def main() -> int:
    parser = argparse.ArgumentParser(description="Seed NagarNetra demo data")
    parser.add_argument(
        "--only",
        choices=[
            "departments",
            "users",
            "vms",
            "demo-camera",
            "fleet",
            "watchlist",
            "all",
        ],
        default="all",
        help="Seed a single dataset instead of everything",
    )
    args = parser.parse_args()

    print("Seeding NagarNetra")
    try:
        department_ids = await seed_departments()
        if args.only in ("users", "all"):
            await seed_users(department_ids)
        if args.only in ("vms", "all"):
            await seed_vms(department_ids)
        if args.only in ("demo-camera", "all"):
            await seed_demo_camera()
        if args.only in ("fleet", "all"):
            await seed_fleet()
        if args.only in ("watchlist", "all"):
            await seed_watchlist()
    finally:
        await dispose_engine()

    print("\nDemo credentials:")
    print(f"  admin / {settings.bootstrap_admin_password}")
    print(f"  supervisor · operator · analyst · auditor / {DEMO_PASSWORD}")
    print("\nSeed complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
