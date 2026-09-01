#!/usr/bin/env python3
"""Make the ANPR fleet mean what it says.

## The problem

The registry grew two ways at once. `seed.py` creates a synthetic fleet so the
GIS map has something to show at scale, and `sync_sandbox.py` onboards the
organisers' real grid. Nothing kept the two apart, so:

* 56 seeded synthetic cameras ended up filed under the **Sentinel Sandbox
  Grid** VMS. On screen they are indistinguishable from the organisers' real
  cameras, which is the single most misleading thing this registry could do.
* 239 cameras were flagged `anpr_enabled`, but only 31 have a video source that
  exists. A worker reading that as its fleet spends its slots opening streams
  that were never going to deliver a frame.

## What this does

Draws the line explicitly, and idempotently:

* **The ANPR fleet is exactly the cameras with a real source.** The
  organisers' grid (`SBX-*`) plus one demonstration camera. Everything else is
  a registry record — real for the map, honest about having no feed.
* **Only genuinely federated cameras belong to a federated VMS.** Seeded
  cameras are moved back to the simulated VMS they should always have had.
* **The demonstration camera says so in its name.** It carried UK motorway
  footage under the name "Ashram Road Circle CCTV 01, Ahmedabad", which is a
  claim about a Gujarat junction that the video does not support.

Run it after seeding and after syncing the grid:

    docker compose exec api python /app/scripts/shape_fleet.py
    docker compose exec api python /app/scripts/shape_fleet.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from sqlalchemy import func, select, update  # noqa: E402

from app.db.session import SessionLocal, dispose_engine  # noqa: E402
from app.models.enums import AdapterType, CameraStatus, VmsVendor  # noqa: E402
from app.models.registry import Camera, Department, VmsInstance  # noqa: E402

#: The one camera allowed to carry recorded footage, and named so nobody has to
#: guess. Its code is deliberately unlike the fleet's numeric codes.
DEMO_CODE = "CAM-DEMO"
DEMO_NAME = "ANPR Demonstration Feed (recorded)"
DEMO_VMS = "Sentinel ANPR Demonstration"

#: Prefix of the organisers' real cameras, assigned by `sync_sandbox.py`.
GRID_PREFIX = "SBX-"

#: Where the demonstration camera sits on the map. Ahmedabad city centre — it
#: has to be somewhere to appear at all, and the name says it is a demo, so
#: nothing here claims the footage was shot at this point.
DEMO_LAT, DEMO_LON = 23.0225, 72.5714


async def demo_vms(session) -> VmsInstance:
    """The demonstration camera gets its own VMS, not a department's.

    Filing it under a real department's VMS would make recorded footage look
    like that department's feed, which is the confusion this script exists to
    remove.
    """
    vms = (
        await session.execute(select(VmsInstance).where(VmsInstance.name == DEMO_VMS))
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
    return vms


async def ensure_demo_camera(session, dry_run: bool) -> tuple[str, bool]:
    """Create or correct the demonstration camera."""
    vms = await demo_vms(session)
    department = (
        await session.execute(select(Department).order_by(Department.code).limit(1))
    ).scalar_one_or_none()

    camera = (
        await session.execute(select(Camera).where(Camera.camera_code == DEMO_CODE))
    ).scalar_one_or_none()

    created = camera is None
    if dry_run:
        return DEMO_CODE, created

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
    camera.district = "Ahmedabad"
    camera.junction = "Demonstration feed"
    camera.resolution = "1920x1080"
    camera.fps = 15
    camera.status = CameraStatus.UNKNOWN.value
    # Tagged so the UI and any report can say plainly what this is, without
    # having to pattern-match on the name.
    #
    # `plate-region:GB` is the one that changes behaviour: the demonstration
    # footage is British, and a camera that sees British plates should have
    # them read as British plates. Which formats a camera sees is a property
    # of where it points, so it belongs on the camera rather than in a
    # worker-wide setting that would make the whole fleet accept UK plates.
    camera.tags = ["demo", "recorded-footage", "not-a-real-camera", "plate-region:GB"]
    # Flush so the counts below see it; without this a freshly created demo
    # camera is missing from the fleet total the script reports.
    await session.flush()
    return DEMO_CODE, created


async def unfile_fake_federated(session, dry_run: bool) -> int:
    """Move seeded cameras off the federated VMS they were never part of."""
    sandbox = (
        (
            await session.execute(
                select(VmsInstance).where(
                    VmsInstance.adapter_type == AdapterType.SENTINEL_SANDBOX.value
                )
            )
        )
        .scalars()
        .all()
    )
    if not sandbox:
        return 0
    sandbox_ids = [v.id for v in sandbox]

    fallback = (
        await session.execute(
            select(VmsInstance)
            .where(VmsInstance.adapter_type == AdapterType.SIMULATED.value)
            .order_by(VmsInstance.name)
            .limit(1)
        )
    ).scalar_one_or_none()

    impostors = (
        (
            await session.execute(
                select(Camera).where(
                    Camera.vms_id.in_(sandbox_ids),
                    ~Camera.camera_code.startswith(GRID_PREFIX),
                )
            )
        )
        .scalars()
        .all()
    )

    if not dry_run and fallback is not None:
        for camera in impostors:
            camera.vms_id = fallback.id
    return len(impostors)


async def restrict_anpr_fleet(session, dry_run: bool) -> tuple[int, int]:
    """`anpr_enabled` means "this camera has a source we analyse"."""
    keep = (
        await session.execute(
            select(func.count())
            .select_from(Camera)
            .where(
                Camera.camera_code.startswith(GRID_PREFIX)
                | (Camera.camera_code == DEMO_CODE)
            )
        )
    ).scalar_one()

    disable = (
        await session.execute(
            select(func.count())
            .select_from(Camera)
            .where(
                Camera.anpr_enabled.is_(True),
                ~Camera.camera_code.startswith(GRID_PREFIX),
                Camera.camera_code != DEMO_CODE,
            )
        )
    ).scalar_one()

    if not dry_run:
        await session.execute(
            update(Camera)
            .where(
                ~Camera.camera_code.startswith(GRID_PREFIX),
                Camera.camera_code != DEMO_CODE,
            )
            .values(anpr_enabled=False)
        )
        await session.execute(
            update(Camera)
            .where(
                Camera.camera_code.startswith(GRID_PREFIX)
                | (Camera.camera_code == DEMO_CODE)
            )
            .values(anpr_enabled=True)
        )
    return keep, disable


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change, change nothing",
    )
    args = parser.parse_args()

    async with SessionLocal() as session:
        code, created = await ensure_demo_camera(session, args.dry_run)
        moved = await unfile_fake_federated(session, args.dry_run)
        kept, disabled = await restrict_anpr_fleet(session, args.dry_run)

        if not args.dry_run:
            await session.commit()

        grid = (
            await session.execute(
                select(func.count())
                .select_from(Camera)
                .where(Camera.camera_code.startswith(GRID_PREFIX))
            )
        ).scalar_one()
        total = (
            await session.execute(select(func.count()).select_from(Camera))
        ).scalar_one()

    verb = "would be" if args.dry_run else "are"
    print(f"\n  Fleet shaped{' (dry run — nothing written)' if args.dry_run else ''}\n")
    print(f"  demonstration camera   {code} {'created' if created else 'updated'}")
    print(f"  federated grid         {grid} cameras from the organisers' grid")
    print(f"  ANPR fleet             {kept} cameras {verb} analysed")
    print(
        f"  registry-only          {disabled} cameras {verb} taken off the ANPR fleet"
    )
    print(
        f"  wrongly federated      {moved} seeded cameras {verb} moved off the grid VMS"
    )
    print(f"  registry total         {total} cameras\n")
    print("  The ANPR fleet is the organisers' real cameras plus one clearly")
    print("  labelled demonstration feed. Every other camera is a registry")
    print("  record with no video attached, which is what it always was.\n")

    await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
