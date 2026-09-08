#!/usr/bin/env python3
"""Point the registry at the grid's current endpoints.

The organisers moved the grid and changed its addressing:

    was   catalogue https://live.corp8.cloud/api/ingest, ids `7`,
          everything on one host
    now   catalogue https://cctv.corp8.cloud/cameras.json, ids `cam07`,
          RTSP and WHEP on a direct IP because a CDN cannot proxy them

`sync_sandbox.py` is the proper way to pick this up — it reads the catalogue
and rewrites everything. But the catalogue now sits behind a login, so a
deployment without a session for it cannot re-sync, and the thirty cameras
already in the registry would keep pointing at a host that no longer serves
them.

This script bridges that gap. It rewrites the stored endpoints to the current
scheme and records each camera's grid id as a tag, so the adapter stops
deriving the id from our own camera code.

**It assumes `SBX-000NN` corresponds to `camNN`.** That held when the ids were
bare numbers and is the obvious reading of the renumbering, but it is an
assumption and it is recorded as one: every camera it touches is tagged
`grid-id-source:inferred`. A successful `sync_sandbox.py` run overwrites both
the tag and the assumption with what the catalogue actually says.

    docker compose exec api python /app/scripts/retarget_grid.py --dry-run
    docker compose exec api python /app/scripts/retarget_grid.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from sqlalchemy import select  # noqa: E402

from app.adapters.sandbox import GRID_ID_TAG, HostedGridAdapter  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal, dispose_engine  # noqa: E402
from app.models.enums import AdapterType  # noqa: E402
from app.models.registry import Camera, VmsInstance  # noqa: E402

GRID_PREFIX = "SBX-"


#: How the grid names its cameras now. Only used to seed the tag for rows that
#: predate it; the catalogue supersedes this the moment it can be read.
def inferred_grid_id(camera_code: str) -> str:
    tail = camera_code.rsplit("-", 1)[-1]
    return f"cam{int(tail):02d}" if tail.isdigit() else tail.lower()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--base-url",
        default=settings.sandbox_base_url,
        help="catalogue/HLS host (the CDN)",
    )
    parser.add_argument(
        "--media-host",
        default=settings.sandbox_media_host,
        help="host serving RTSP and WHEP (not the CDN)",
    )
    args = parser.parse_args()

    async with SessionLocal() as session:
        vms = (
            (
                await session.execute(
                    select(VmsInstance).where(
                        VmsInstance.adapter_type == AdapterType.HOSTED_GRID.value
                    )
                )
            )
            .scalars()
            .first()
        )

        cameras = (
            (
                await session.execute(
                    select(Camera)
                    .where(Camera.camera_code.startswith(GRID_PREFIX))
                    .order_by(Camera.camera_code)
                )
            )
            .scalars()
            .all()
        )

        adapter = HostedGridAdapter(name="retarget", base_url=args.base_url)
        changed = 0
        for camera in cameras:
            grid_id = inferred_grid_id(camera.camera_code)
            endpoints = adapter._endpoints_for(grid_id)

            tags = [
                t
                for t in (camera.tags or [])
                if not (
                    isinstance(t, str)
                    and (t.startswith(GRID_ID_TAG) or t.startswith("grid-id-source:"))
                )
            ]
            tags += [f"{GRID_ID_TAG}{grid_id}", "grid-id-source:inferred"]

            if not args.dry_run:
                camera.tags = tags
                camera.stream_url = endpoints.rtsp
                camera.sub_stream_url = endpoints.hls
                camera.protocol = "rtsp"
                if vms is not None:
                    camera.vms_id = vms.id
            changed += 1

            if changed <= 3:
                print(f"  {camera.camera_code}  →  {grid_id}")
                print(f"      rtsp {endpoints.rtsp}")
                print(f"      hls  {endpoints.hls}")

        if not args.dry_run:
            if vms is not None:
                vms.base_url = args.base_url
            await session.commit()

    suffix = " (dry run — nothing written)" if args.dry_run else ""
    print(f"\n  {changed} grid cameras retargeted{suffix}")
    print(f"  catalogue/HLS  {args.base_url}")
    print(f"  RTSP/WHEP      {args.media_host}")
    print("\n  Grid ids are inferred from our camera codes and tagged as such.")
    print("  Run sync_sandbox.py once the catalogue is reachable to replace")
    print("  them with what the catalogue actually says.\n")

    await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
