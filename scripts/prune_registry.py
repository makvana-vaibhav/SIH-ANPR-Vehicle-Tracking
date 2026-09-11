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

Cameras with **no `stream_url`**, except the demonstration camera, which
resolves its source through MediaMTX rather than a stored URL.

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
from app.models.intelligence import Alert, Detection  # noqa: E402
from app.models.registry import Camera, CameraHealth  # noqa: E402

#: These resolve their source through MediaMTX rather than a stored URL, so a
#: NULL `stream_url` on them is correct rather than decorative. The simulator
#: publishes them, `SimulatedVmsAdapter` derives RTSP/WHEP/HLS from the camera
#: code, and health is probed by asking MediaMTX which paths are actually live.
#:
#: Everything the simulator publishes has to be listed here. It is not a
#: cosmetic exclusion: this script deletes cameras that have no `stream_url`,
#: so a published camera missing from this tuple is deleted along with its
#: detections and alerts on the next run.
KEEP_WITHOUT_URL = ("CAM-DEMO", "CAM-DEMO-01", "CAM-DEMO-02", "CAM-DEMO-03")


def _sourceless():
    return select(Camera.id).where(
        Camera.stream_url.is_(None),
        Camera.camera_code.notin_(KEEP_WITHOUT_URL),
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
