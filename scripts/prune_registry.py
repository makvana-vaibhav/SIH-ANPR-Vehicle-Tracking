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

Two rules, one at a time.

*Default* — cameras with **no `stream_url`**, except the demonstration cameras,
which resolve their source through MediaMTX rather than a stored URL.

*`--match-seed`* — cameras **`data/seed/cameras.csv` no longer declares**.
Seeding is additive: `scripts/seed.py` creates and refreshes, never deletes. So
trimming the CSV does nothing to a database that has already been seeded, and
because the simulator builds its publishing list from the *registry* rather than
the file, a fleet cut from 61 cameras to 12 in the CSV kept publishing 61
streams and kept appearing in every count on every screen. This makes the file
authoritative again.

Children go first and explicitly. `detections.camera_id` is ON DELETE SET NULL,
so deleting a camera does not remove its sightings — it orphans them, and they
then appear in search attached to no camera at all, which is worse than either
keeping or deleting them.

    python scripts/prune_registry.py --dry-run
    python scripts/prune_registry.py
    python scripts/prune_registry.py --match-seed --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import csv
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


#: Where the declared fleet lives. `--match-seed` treats this file as the
#: authoritative list of cameras that should exist.
#:
#: Resolved the same way `scripts/seed.py` does it: compose mounts the seed data
#: at /data/seed, and the repository path is the fallback for running outside a
#: container. The two must agree, or prune would judge the registry against a
#: different file from the one that populated it.
_SEED_DIR = Path("/data/seed")
if not _SEED_DIR.exists():  # running outside the container
    _SEED_DIR = Path(__file__).resolve().parent.parent / "data" / "seed"
SEED_CSV = _SEED_DIR / "cameras.csv"


def _sourceless():
    return select(Camera.id).where(
        Camera.stream_url.is_(None),
        Camera.camera_code.notin_(KEEP_WITHOUT_URL),
    )


def _seed_codes() -> list[str]:
    with SEED_CSV.open(newline="") as handle:
        return [
            row["camera_code"].strip()
            for row in csv.DictReader(handle)
            if row.get("camera_code", "").strip()
        ]


def _not_in_seed():
    """Cameras the seed file no longer declares.

    Seeding is additive — `scripts/seed.py` creates or refreshes, and never
    deletes — so trimming `cameras.csv` leaves every removed camera running in
    the registry. That is not cosmetic: the simulator builds its publishing list
    from the **registry**, so a fleet cut from 61 cameras to 12 in the CSV went
    on publishing 61 streams, and every count on every screen went on including
    cameras the operator believed were gone.
    """
    codes = _seed_codes()
    if not codes:
        raise SystemExit(f"{SEED_CSV} lists no cameras — refusing to delete everything")
    return select(Camera.id).where(Camera.camera_code.notin_(codes))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    parser.add_argument(
        "--match-seed",
        action="store_true",
        help=(
            "Delete cameras that data/seed/cameras.csv no longer declares, "
            "instead of cameras with no stream_url."
        ),
    )
    args = parser.parse_args()
    try:
        return await _prune(args)
    finally:
        # Disposed on the loop that created the engine. Doing it from a second
        # `asyncio.run` closes connections belonging to a loop that has already
        # gone, which raises "Event loop is closed" over a job that succeeded.
        await dispose_engine()


async def _prune(args: argparse.Namespace) -> int:
    criterion = _not_in_seed if args.match_seed else _sourceless
    rule = (
        "not declared in data/seed/cameras.csv"
        if args.match_seed
        else "with no video source"
    )

    async with SessionLocal() as session:
        doomed = (await session.execute(criterion())).scalars().all()
        kept = (await session.execute(select(func.count()).select_from(Camera))).scalar_one()

        if not doomed:
            print(f"  registry: {kept} cameras, none {rule} — nothing to prune")
            return 0

        detections = (
            await session.execute(
                select(func.count())
                .select_from(Detection)
                .where(Detection.camera_id.in_(criterion()))
            )
        ).scalar_one()
        alerts = (
            await session.execute(
                select(func.count())
                .select_from(Alert)
                .where(Alert.camera_id.in_(criterion()))
            )
        ).scalar_one()

        print(f"  cameras {rule}: {len(doomed)} of {kept}")
        print(f"  their detections: {detections:,}")
        print(f"  their alerts: {alerts:,}")

        if args.dry_run:
            print("  --dry-run: nothing changed")
            return 0

        # Order matters. Alerts reference detections *and* cameras, so a police
        # record would be left pointing at nothing if the parents went first.
        await session.execute(delete(Alert).where(Alert.camera_id.in_(criterion())))
        await session.execute(
            delete(Detection).where(Detection.camera_id.in_(criterion()))
        )
        await session.execute(
            delete(CameraHealth).where(CameraHealth.camera_id.in_(criterion()))
        )
        await session.execute(delete(Camera).where(Camera.id.in_(doomed)))
        await session.commit()

        remaining = (
            await session.execute(select(func.count()).select_from(Camera))
        ).scalar_one()
        print(f"  pruned. {remaining} cameras remain.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
