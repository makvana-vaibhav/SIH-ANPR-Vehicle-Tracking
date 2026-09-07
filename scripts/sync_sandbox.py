#!/usr/bin/env python3
"""Onboard the hosted grid camera grid into the registry.

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
from app.adapters.sandbox import HostedGridAdapter  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.enums import CameraStatus, CameraType, Protocol  # noqa: E402
from app.models.registry import Camera, VmsInstance  # noqa: E402

VMS_NAME = "Hosted Camera Grid"
CODE_PREFIX = "SBX"
GAZETTEER = (
    Path(__file__).resolve().parent.parent / "data" / "seed" / "gujarat_places.csv"
)

#: `cameras.location` is NOT NULL, so a camera we cannot place still needs a
#: point. It gets the state centroid and the tag `placement:unknown` — the tag
#: is what matters: it is the difference between "we do not know where this is"
#: and a coordinate that looks surveyed. Any map must filter on it.
GUJARAT_CENTROID = (22.2587, 71.1924)


def load_gazetteer() -> list[dict[str, str]]:
    """Keyword -> settlement, for placing cameras the catalogue does not locate."""
    import csv

    if not GAZETTEER.exists():
        return []
    with GAZETTEER.open(encoding="utf-8") as handle:
        rows = [line for line in handle if not line.startswith("#")]
    return list(csv.DictReader(rows))


def place(location: str, gazetteer: list[dict[str, str]]) -> dict[str, str] | None:
    """Match a location name to a settlement.

    Returns None rather than a guess when nothing matches. A camera with no
    coordinates is registered and watchable but stays off the map — putting it
    somewhere plausible would be worse than admitting we do not know.
    """
    text = (location or "").lower()
    for row in gazetteer:
        if row["keyword"] and row["keyword"].lower() in text:
            return row
    return None


async def reachable_transport(base_url: str, cameras: list) -> str:
    """Which transport this network can use: rtsp, hls, or neither.

    The integration guide anticipates this exactly — "If port 8554 is blocked on
    your network, use the HLS endpoint instead" — so probe once rather than
    registering RTSP URLs that will time out on every camera.
    """
    import asyncio
    from urllib.parse import urlparse

    import httpx

    host = urlparse(base_url).hostname or base_url
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, 8554), timeout=8)
        w.close()
        await w.wait_closed()
        return "rtsp"
    except (OSError, asyncio.TimeoutError):
        pass

    sample = next((c for c in cameras if c.raw.get("_hls")), None)
    if sample is not None:
        url = sample.raw["_hls"]
        url = url if url.startswith("http") else f"{base_url.rstrip('/')}{url}"
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                response = await client.get(url)
            if response.status_code == 200 and "#EXTM3U" in response.text:
                return "hls"
        except httpx.HTTPError:
            pass
    return "none"


async def sync(base_url: str, dry_run: bool = False) -> int:
    adapter = HostedGridAdapter(name=VMS_NAME, base_url=base_url)

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
        print(
            "catalogue returned no cameras — check the host and that you are authorised"
        )
        return 1

    gazetteer = load_gazetteer()
    transport = await reachable_transport(base_url, discovered)
    print(f"  usable transport from this network: {transport}")
    if transport == "none":
        print(
            "  neither RTSP (8554) nor HLS is reachable — cameras will register "
            "but cannot be watched from here"
        )

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
        for camera in discovered:
            hit = place(camera.raw.get("location", ""), gazetteer)
            where = f"{hit['place']} ({hit['precision']})" if hit else "UNPLACED"
            print(f"  {camera.external_id:>4}  {camera.name[:34]:34}  {where:26}")
        return 0

    created = updated = skipped = from_gazetteer = 0
    async with SessionLocal() as session:
        vms = (
            await session.execute(
                select(VmsInstance).where(VmsInstance.name == VMS_NAME)
            )
        ).scalar_one_or_none()
        if vms is None:
            vms = VmsInstance(
                name=VMS_NAME,
                vendor="nagarnetra",
                adapter_type="hosted_grid",
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

            # The catalogue publishes a location *name*, not coordinates. Where
            # the name identifies a settlement we place the camera at that
            # settlement's centre and record precision='city' — it is the town,
            # not the junction. Published coordinates, if they ever appear,
            # always win.
            lat, lon = item.lat, item.lon
            precision = "exact" if lat is not None else None
            hit_place = None
            if lat is None:
                hit_place = place(item.raw.get("location", ""), gazetteer)
                if hit_place:
                    lat, lon = float(hit_place["lat"]), float(hit_place["lon"])
                    precision = hit_place["precision"]
                    from_gazetteer += 1
                else:
                    lat, lon = GUJARAT_CENTROID
                    precision = "unknown"
                    skipped += 1

            # Register the URL this network can actually open, not the one the
            # catalogue lists first.
            hls = item.raw.get("_hls") or ""
            if hls and not hls.startswith("http"):
                hls = f"{base_url.rstrip('/')}{hls}"
            stream_url = (
                item.stream_url if transport != "hls" else (hls or item.stream_url)
            )

            raw_location = str(item.raw.get("location") or "").strip()
            fields = {
                "camera_code": code,
                "name": item.name,
                # Without this the camera has no VMS, so adapter_for falls back
                # to the generic RTSP adapter and hands the player our own
                # gateway's URLs for a camera our gateway has never seen.
                "vms_id": vms.id,
                "stream_url": stream_url,
                "sub_stream_url": hls or None,
                "protocol": Protocol.RTSP.value,
                "camera_type": CameraType.FIXED.value,
                "resolution": item.resolution,
                "fps": item.fps,
                "status": (
                    CameraStatus.ONLINE.value
                    if item.is_live
                    else CameraStatus.UNKNOWN.value
                ),
                # The grid's location string is the junction the camera watches;
                # the gazetteer supplies the settlement it sits in. Keeping both
                # means the map can show a city while the operator still sees
                # the junction name the organisers use.
                "junction": raw_location[:200] or None,
                "address": raw_location[:400] or None,
                "anpr_enabled": True,
            }
            if hit_place is not None:
                fields["city"] = hit_place["place"]
                fields["district"] = hit_place["district"]

            # How the coordinate was arrived at travels with the camera, so a
            # city-level placement can never be mistaken for a surveyed one.
            fields["tags"] = [
                "sandbox",
                # The grid's own id, stored rather than derived from our code.
                # Their id format has already changed once (7 → cam07) and the
                # guide says the catalogue is the source of truth, so deriving
                # it would break silently the next time they renumber.
                f"grid-id:{item.external_id}",
                f"transport:{transport}",
                f"placement:{precision or 'none'}",
            ]

            if camera is None:
                camera = Camera(**fields)
                if lat is not None and lon is not None:
                    camera.location = f"SRID=4326;POINT({lon} {lat})"
                session.add(camera)
                created += 1
            else:
                for key, value in fields.items():
                    setattr(camera, key, value)
                if lat is not None and lon is not None:
                    camera.location = f"SRID=4326;POINT({lon} {lat})"
                updated += 1

        await session.commit()

    print(f"\n  created {created}, updated {updated}")
    print(f"  placed from the gazetteer (city precision): {from_gazetteer}")
    print(f"  location not identifiable, tagged placement:unknown: {skipped}")
    if skipped:
        print("    (these sit at the state centroid so they can be watched; the tag")
        print("     is what stops the map treating that as a real position)")
    print("\nNext: point the AI worker at these cameras and open the map.")
    return 0


def _placement_note(item, precision: str | None, transport: str) -> str:
    """Say plainly where the coordinate came from and how the feed is reached."""
    source = item.raw.get("location", "")
    if precision == "exact":
        where = "coordinates published by the grid"
    elif precision:
        where = f"placed at settlement centre from '{source}' — town, not junction"
    else:
        where = f"no coordinates; '{source}' did not identify a settlement"
    return f"hosted grid. {where}. Watched over {transport.upper()}."


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="sandbox host, e.g. https://<host> (no /api/ingest)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show what the catalogue returns without writing",
    )
    args = parser.parse_args()
    return asyncio.run(sync(args.base_url, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
