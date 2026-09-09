#!/usr/bin/env python3
"""Remove registry records that have no video source behind them.

## Why this exists

The registry used to be seeded with 250 synthetic cameras so the GIS map had
something to show at scale. Of 281 cameras, **251 had no stream URL at all**.
They sat permanently at status `unknown`, could never be opened, could never
produce a detection, and made every count on every screen mostly fiction —
fleet health was reporting on cameras that could not be unhealthy, and the map
looked impressive while meaning nothing.

A camera the state has recorded but not yet connected is a legitimate registry
record. Two hundred and fifty of them, invented, are not.

## What it removes

Cameras that have **neither a `stream_url` nor an adapter that can derive
one**. Cameras on a `simulated` VMS — the demonstration feed and the whole
Ahmedabad city fleet — resolve their stream from their own camera code through
MediaMTX, so a NULL `stream_url` there is correct and they are kept.

Children go first and explicitly. `detections.camera_id` is ON DELETE SET NULL,
so deleting a camera does not remove its sightings — it orphans them, and they
then appear in search attached to no camera at all, which is worse than either
keeping or deleting them.

    python scripts/prune_registry.py --dry-run
    python scripts/prune_registry.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from sqlalchemy import delete, func, select  # noqa: E402

from app.db.session import SessionLocal, dispose_engine  # noqa: E402
from app.models.enums import AdapterType  # noqa: E402
from app.models.intelligence import Alert, Detection  # noqa: E402
from app.models.registry import Camera, CameraHealth, VmsInstance  # noqa: E402

#: Adapters that resolve a camera's stream from its **code** rather than from a
#: stored URL. `SimulatedVmsAdapter` builds `rtsp://mediamtx:8554/<code>` and
#: asks MediaMTX whether that path is publishing, so a NULL `stream_url` on one
#: of these cameras is correct rather than missing.
#:
#: This used to be the single hardcoded code `CAM-DEMO`, which was right when
#: the demonstration feed was the only simulator-published camera. P1 added an
#: entire Ahmedabad city fleet on the same footing — 69 cameras, none with a
#: `stream_url`, all of them resolvable — and a code-based exemption would have
#: deleted every one of them the next time this ran.
SOURCELESS_OK_ADAPTERS = (AdapterType.SIMULATED.value,)


def _sourceless():
    """Cameras with no way at all to produce video.

    A camera is sourceless only if it has neither a stored `stream_url` nor an
    adapter that can derive one. Judged on the adapter, not on the camera code,
    for the same reason `gateway.reconcile()` is: how a camera is integrated is
    a fact about its VMS, not something to infer from a naming convention.
    """
    resolvable_by_adapter = (
        select(VmsInstance.id)
        .where(VmsInstance.adapter_type.in_(SOURCELESS_OK_ADAPTERS))
        .scalar_subquery()
    )
    return select(Camera.id).where(
        Camera.stream_url.is_(None),
        # `NOT IN` with a NULL vms_id yields NULL, not TRUE, so a camera with no
        # VMS at all would never match and would survive a prune it deserves.
        # Spelled out rather than relying on that.
        (Camera.vms_id.is_(None)) | (Camera.vms_id.notin_(resolvable_by_adapter)),
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    args = parser.parse_args()
    try:
        return await _prune(args)
    finally:
        # Disposed on the loop that created the engine. Doing it from a second
        # `asyncio.run` closes connections belonging to a loop that has already
        # gone, which raises "Event loop is closed" over a job that succeeded.
        await dispose_engine()


async def _prune(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        doomed = (await session.execute(_sourceless())).scalars().all()
        kept = (await session.execute(select(func.count()).select_from(Camera))).scalar_one()

        if not doomed:
            print(f"  registry: {kept} cameras, all with a video source — nothing to prune")
            return 0

        detections = (
            await session.execute(
                select(func.count())
                .select_from(Detection)
                .where(Detection.camera_id.in_(_sourceless()))
            )
        ).scalar_one()
        alerts = (
            await session.execute(
                select(func.count())
                .select_from(Alert)
                .where(Alert.camera_id.in_(_sourceless()))
            )
        ).scalar_one()

        print(f"  cameras with no video source: {len(doomed)} of {kept}")
        print(f"  their detections: {detections:,}")
        print(f"  their alerts: {alerts:,}")

        if args.dry_run:
            print("  --dry-run: nothing changed")
            return 0

        # Order matters. Alerts reference detections *and* cameras, so a police
        # record would be left pointing at nothing if the parents went first.
        await session.execute(delete(Alert).where(Alert.camera_id.in_(_sourceless())))
        await session.execute(
            delete(Detection).where(Detection.camera_id.in_(_sourceless()))
        )
        await session.execute(
            delete(CameraHealth).where(CameraHealth.camera_id.in_(_sourceless()))
        )
        await session.execute(delete(Camera).where(Camera.id.in_(doomed)))
        await session.commit()

        remaining = (
            await session.execute(select(func.count()).select_from(Camera))
        ).scalar_one()
        print(f"  pruned. {remaining} cameras remain, every one with a video source.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
