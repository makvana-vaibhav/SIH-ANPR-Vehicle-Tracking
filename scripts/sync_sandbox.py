#!/usr/bin/env python3
"""Onboard the Sentinel sandbox camera grid into the registry.

Reads the organisers' catalogue at ``GET <base>/api/ingest`` and creates or
updates one camera row per entry, carrying the coordinates the catalogue
publishes. That is the answer to "how do I know where a camera is": we do not
guess it, and we do not invent it — the catalogue is the source of truth, and
cameras without coordinates are recorded as such rather than dropped onto the
map somewhere plausible.

The integration guide is explicit that "camera ids and the set of available
cameras can change", so this is a re-sync rather than a one-off import: run it
again and it reconciles.

    python scripts/sync_sandbox.py --base-url https://<sandbox-host>

Nothing here is specific to a host; supply the one the organisers give you.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from sqlalchemy import select  # noqa: E402

from app.adapters.base import AdapterError  # noqa: E402
from app.adapters.sandbox import SentinelSandboxAdapter  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.enums import CameraStatus, CameraType, Protocol  # noqa: E402
from app.models.registry import Camera, VmsInstance  # noqa: E402

VMS_NAME = "Sentinel Sandbox Grid"
CODE_PREFIX = "SBX"


async def sync(base_url: str, dry_run: bool = False) -> int:
    adapter = SentinelSandboxAdapter(name=VMS_NAME, base_url=base_url)

    print(f"reading catalogue from {base_url.rstrip('/')}/api/ingest ...")
    try:
        discovered = await adapter.list_cameras()
    except AdapterError as exc:
        # An unreachable catalogue is the normal first failure, and a traceback
        # tells the operator nothing they can act on.
        print(f"\ncould not read the catalogue: {exc}\n")
        print("Check that:")
        print("  * the host is the sandbox host from the organisers, not the")
        print("    public site (sentinel.gujarat.gov.in/api/ingest returns 404)")
        print("  * you are on a network permitted to reach it")
        print("  * the catalogue does not require an auth header — if it does,")
        print("    the adapter needs the credential wired through")
        return 1
    if not discovered:
        print("catalogue returned no cameras — check the host and that you are authorised")
        return 1

    with_location = [c for c in discovered if c.lat is not None and c.lon is not None]
    live = [c for c in discovered if c.is_live]
    print(
        f"  {len(discovered)} cameras, {len(with_location)} with coordinates, "
        f"{len(live)} reporting live"
    )
    codecs = sorted({c.codec for c in discovered if c.codec})
    resolutions = sorted({c.resolution for c in discovered if c.resolution})
    print(f"  codecs: {codecs or 'not reported'}")
    print(f"  resolutions: {resolutions or 'not reported'}")

    if dry_run:
        print("\n(dry run — nothing written)")
        for camera in discovered[:10]:
            where = f"{camera.lat:.5f},{camera.lon:.5f}" if camera.lat is not None else "no location"
            print(f"  {camera.external_id:>6}  {camera.name[:38]:38}  {where:24}  {camera.stream_url}")
        return 0

    created = updated = skipped = 0
    async with SessionLocal() as session:
        vms = (
            await session.execute(select(VmsInstance).where(VmsInstance.name == VMS_NAME))
        ).scalar_one_or_none()
        if vms is None:
            vms = VmsInstance(
                name=VMS_NAME,
                vendor="sentinel",
                adapter_type="sentinel_sandbox",
                base_url=base_url,
                # A pointer, never a secret: see CLAUDE.md on credentials_ref.
                credentials_ref="env:SANDBOX_CREDENTIALS",
                status=CameraStatus.ONLINE.value,
            )
            session.add(vms)
            await session.flush()
            print(f"  created VMS instance {vms.id}")
        else:
            vms.base_url = base_url

        for item in discovered:
            code = f"{CODE_PREFIX}-{item.external_id.zfill(5)}"
            camera = (
                await session.execute(select(Camera).where(Camera.camera_code == code))
            ).scalar_one_or_none()

            if item.lat is None or item.lon is None:
                # A camera with no coordinates cannot go on the map. It is still
                # registered — it has a stream and can be watched — but placing
                # it at a guessed location would be worse than leaving it off.
                skipped += 1

            fields = {
                "camera_code": code,
                "name": item.name,
                "vms_id": vms.id,
                "external_id": item.external_id,
                "stream_url": item.stream_url,
                "protocol": Protocol.RTSP.value,
                "camera_type": CameraType.FIXED.value,
                "resolution": item.resolution,
                "fps": item.fps,
                "status": (
                    CameraStatus.ONLINE.value if item.is_live else CameraStatus.UNKNOWN.value
                ),
            }

            if camera is None:
                camera = Camera(**fields)
                if item.lat is not None and item.lon is not None:
                    camera.location = f"SRID=4326;POINT({item.lon} {item.lat})"
                session.add(camera)
                created += 1
            else:
                for key, value in fields.items():
                    setattr(camera, key, value)
                if item.lat is not None and item.lon is not None:
                    camera.location = f"SRID=4326;POINT({item.lon} {item.lat})"
                updated += 1

        await session.commit()

    print(f"\n  created {created}, updated {updated}, {skipped} without coordinates")
    print("\nNext: point the AI worker at these cameras and open the map.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True,
                        help="sandbox host, e.g. https://<host> (no /api/ingest)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what the catalogue returns without writing")
    args = parser.parse_args()
    return asyncio.run(sync(args.base_url, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
