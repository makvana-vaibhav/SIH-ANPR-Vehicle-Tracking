#!/usr/bin/env python3
"""The end-to-end demonstration: a plate on a watchlist, a car that passes, an alert.

Nothing here is staged. The chain that runs is the production one:

    simulator → RTSP into MediaMTX → ai-worker decodes the stream → YOLO →
    ByteTrack → plate detector → OCR → multi-frame consensus → Redis Stream →
    the API's consumer → watchlist match → alert → /ws/events

The script's only privileges are reading the database to report what happened
and putting a plate on the watchlist. It does not publish detections, does not
write alerts, and does not tell the pipeline what to find. If the pipeline
cannot read the plate off the video, no alert appears and the demo fails —
which is the point of running it.

    docker compose exec api python /app/scripts/demo_anpr.py
    docker compose exec api python /app/scripts/demo_anpr.py --plate NA13NRU
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

import httpx  # noqa: E402
from geoalchemy2 import Geometry  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.intelligence import Alert, Detection, Watchlist  # noqa: E402
from app.models.registry import Camera  # noqa: E402

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
GREEN, RED, YELLOW, CYAN = "\033[32m", "\033[31m", "\033[33m", "\033[36m"

#: The camera the ANPR footage is pinned to, via SIM_CAMERA_VIDEOS in compose.
#: It is the one camera in the fleet carrying recorded video; every other
#: camera is a live feed from the organisers' grid.
DEMO_CAMERA = "CAM-DEMO"
#: The watchlist entry is made the way an officer makes one: an authenticated
#: request to the API. Writing straight to the table would skip RBAC, skip the
#: audit row, and leave the API's in-memory matcher unaware of the new plate
#: for up to a minute.
#: Inside the compose network this is the api service; from the host it is
#: the published port. Overridable so the demo can be driven either way.
API_URL = os.environ.get("NAGARNETRA_API_URL", "http://localhost:8000") + "/api/v1"
DEMO_USER, DEMO_PASSWORD = "supervisor", "NagarNetra@2026"  # noqa: S105 — seeded demo credential
#: How long to let the pipeline run before giving up on seeing the vehicle.
PASS_TIMEOUT_S = 180.0


def step(n: int, title: str) -> None:
    print(f"\n{BOLD}{n}. {title}{RESET}")


def fail(message: str, *hints: str) -> int:
    print(f"   {RED}{message}{RESET}")
    for hint in hints:
        print(f"   {DIM}{hint}{RESET}")
    return 1


async def recent_plates(camera_code: str, since: datetime) -> list[dict]:
    """What the live pipeline has read off this camera, best first.

    These are rows the ai-worker wrote through the event bus. Reading them back
    is how the script learns which plates are actually in the footage — it is
    never told.
    """
    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Detection)
                    .where(
                        Detection.plate_normalised.isnot(None), Detection.ts >= since
                    )
                    .order_by(Detection.ts.desc())
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )

    best: dict[str, dict] = {}
    for row in rows:
        plate = row.plate_normalised
        seen = best.get(plate)
        if seen is None or row.plate_confidence > seen["confidence"]:
            best[plate] = {
                "plate": plate,
                "confidence": row.plate_confidence or 0.0,
                "vehicle": row.vehicle_type or "vehicle",
                "at": row.ts,
            }
    return sorted(best.values(), key=lambda p: -p["confidence"])


async def watch_for(plate: str, case_ref: str) -> str:
    """Put the plate on the watchlist over the API, as an officer would.

    Going through the API rather than the database means this exercises the
    real path: the supervisor role is checked, an audit row is written, and the
    matcher's in-memory index is invalidated immediately instead of waiting out
    its refresh interval.
    """
    async with httpx.AsyncClient(base_url=API_URL, timeout=15.0) as client:
        auth = await client.post(
            "/auth/login", json={"username": DEMO_USER, "password": DEMO_PASSWORD}
        )
        auth.raise_for_status()
        headers = {"Authorization": f"Bearer {auth.json()['access_token']}"}

        created = await client.post(
            "/watchlist",
            headers=headers,
            json={
                "plate": plate,
                "category": "stolen",
                "priority": "critical",
                "case_ref": case_ref,
                "remarks": "Added by the ANPR demonstration",
            },
        )
        if created.status_code == 409:
            # Already watched from an earlier run — make sure it is active and
            # at the priority this demonstration claims.
            existing = await client.get(
                "/watchlist", headers=headers, params={"plate": plate}
            )
            existing.raise_for_status()
            items = existing.json().get("items", [])
            if items:
                patched = await client.patch(
                    f"/watchlist/{items[0]['id']}",
                    headers=headers,
                    json={
                        "category": "stolen",
                        "priority": "critical",
                        "case_ref": case_ref,
                        "active": True,
                    },
                )
                patched.raise_for_status()
        else:
            created.raise_for_status()
    return plate


async def wait_for_alert(
    plate: str, camera_code: str, after: datetime, timeout_s: float
) -> Alert | None:
    """Poll for an alert the platform raised by itself."""
    deadline = time.perf_counter() + timeout_s
    spinner, tick = "|/-\\", 0
    interactive = sys.stdout.isatty()
    while time.perf_counter() < deadline:
        async with SessionLocal() as session:
            alert = (
                await session.execute(
                    select(Alert)
                    .where(Alert.plate_normalised == plate, Alert.created_at >= after)
                    .order_by(Alert.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if alert is not None:
            if interactive:
                print(f"\r   {' ' * 60}\r", end="")
            return alert
        waited = int(timeout_s - (deadline - time.perf_counter()))
        if interactive:
            print(
                f"\r   {spinner[tick % 4]} waiting for {plate} to pass "
                f"{camera_code}… {waited}s",
                end="",
                flush=True,
            )
        elif tick % 20 == 0:
            print(
                f"   … waiting for {plate} to pass {camera_code} ({waited}s)",
                flush=True,
            )
        tick += 1
        await asyncio.sleep(0.5)
    if interactive:
        print(f"\r   {' ' * 60}\r", end="")
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--camera",
        default=DEMO_CAMERA,
        help=f"camera carrying the ANPR footage (default {DEMO_CAMERA})",
    )
    parser.add_argument(
        "--plate", help="plate to watch for (default: the best-read one)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=PASS_TIMEOUT_S,
        help="seconds to wait for the vehicle to come round again",
    )
    args = parser.parse_args()
    camera_code = args.camera.upper()

    print(f"{BOLD}NagarNetra — ANPR end to end{RESET}")
    print(f"{DIM}Every number below is produced by the running platform.{RESET}")

    # ── 1 ────────────────────────────────────────────────────────────────────
    step(1, f"What the live pipeline is reading from {camera_code}")
    async with SessionLocal() as session:
        row = (
            await session.execute(
                # The position is a PostGIS *geography* column — metres-accurate
                # distance maths, but ST_X/ST_Y are geometry functions, so it is
                # cast the same way app/services/camera.py does it.
                select(
                    Camera,
                    func.ST_Y(Camera.location.cast(Geometry)),
                    func.ST_X(Camera.location.cast(Geometry)),
                ).where(Camera.camera_code == camera_code)
            )
        ).first()
    camera, cam_lat, cam_lon = row if row else (None, None, None)
    if camera is None:
        return fail(f"{camera_code} is not in the registry.", "Run: make seed")
    print(
        f"   {camera.camera_code} — {camera.name}"
        f"{f', {camera.city}' if camera.city else ''}"
    )

    window_start = datetime.now(UTC) - timedelta(minutes=10)
    found = await recent_plates(camera_code, window_start)
    if not found:
        return fail(
            "No plates have been read in the last 10 minutes.",
            "The AI worker has to be running and reading the ANPR footage:",
            "  docker compose --profile ai up -d ai-worker",
            f"  AI_WORKER_SOURCE=mediamtx AI_WORKER_CAMERAS={camera_code}",
            "Check it is seeing the stream:  docker compose logs -f ai-worker",
        )
    for p in found[:8]:
        print(
            f"   {CYAN}{p['plate']:10}{RESET} confidence {p['confidence']:.2f}"
            f"   {p['vehicle']:10} last seen {p['at']:%H:%M:%S} UTC"
        )
    if len(found) > 8:
        print(f"   {DIM}… and {len(found) - 8} more{RESET}")

    target = (args.plate or found[0]["plate"]).upper()
    if target not in {p["plate"] for p in found}:
        print(
            f"   {YELLOW}note: {target} has not been read yet; "
            f"waiting for it anyway{RESET}"
        )

    # ── 2 ────────────────────────────────────────────────────────────────────
    case_ref = f"FIR/2026/DEMO/{datetime.now(UTC):%H%M%S}"
    step(2, f"An officer puts {target} on the watchlist")
    await watch_for(target, case_ref)
    print(
        f"   category {BOLD}stolen{RESET}   priority {RED}{BOLD}critical{RESET}"
        f"   case {case_ref}"
    )
    print(
        f"   {DIM}The plate was chosen from what the AI read — it was not "
        f"planted in the footage.{RESET}"
    )

    # ── 3 ────────────────────────────────────────────────────────────────────
    armed_at = datetime.now(UTC)
    step(3, f"The vehicle comes round again on {camera_code}")
    print(
        f"   {DIM}Nothing is injected. The clip loops; when that car passes the"
        f" camera again{RESET}"
    )
    print(
        f"   {DIM}the pipeline reads it afresh and the platform decides for "
        f"itself.{RESET}\n"
    )

    started = time.perf_counter()
    alert = await wait_for_alert(target, camera_code, armed_at, args.timeout)
    if alert is None:
        return fail(
            f"{target} did not trigger an alert within {args.timeout:.0f}s.",
            "Either the vehicle has not come round again, or the read differed.",
            "Watch the detections arrive:  docker compose logs -f ai-worker",
        )
    elapsed = time.perf_counter() - started

    # ── 4 ────────────────────────────────────────────────────────────────────
    step(4, "Alert")
    async with SessionLocal() as session:
        entry = (
            await session.execute(
                select(Watchlist).where(Watchlist.id == alert.watchlist_id)
            )
        ).scalar_one_or_none()
        detection = (
            (
                await session.execute(
                    select(Detection).where(Detection.id == alert.detection_id)
                )
            ).scalar_one_or_none()
            if alert.detection_id
            else None
        )

    colour = RED if alert.priority == "critical" else YELLOW
    label = alert.alert_type.upper().replace("_", " ")
    banner = f"*** {label} ***"
    width = max(len(banner) + 4, 30)
    print(f"   {colour}{BOLD}╔{'═' * width}╗{RESET}")
    print(f"   {colour}{BOLD}║{banner.center(width)}║{RESET}")
    print(f"   {colour}{BOLD}╚{'═' * width}╝{RESET}")
    print(f"   plate       {BOLD}{alert.plate_normalised}{RESET}")
    print(f"   priority    {colour}{BOLD}{alert.priority}{RESET}")
    print(f"   camera      {camera.camera_code} — {camera.name}")
    if cam_lat is not None and cam_lon is not None:
        print(
            f"   location    {cam_lat:.5f}, {cam_lon:.5f}"
            f"{f'  ({camera.district})' if camera.district else ''}"
        )
    if entry is not None:
        print(f"   reason      {entry.category}, case {entry.case_ref}")
    print(f"   confidence  {alert.confidence:.2f}")
    print(f"   raised      {alert.created_at:%H:%M:%S} UTC   status {alert.status}")
    if detection is not None:
        print(
            f"   evidence    detection {str(detection.id)[:8]} "
            f"read at {detection.ts:%H:%M:%S} UTC"
        )
        if detection.crop_key:
            print(f"   crop        {detection.crop_key}")
        raw = detection.ocr_raw or {}
        ev = raw.get("evidence", {})
        if ev.get("reads_total"):
            print(
                f"   read from   {ev['reads_total']} frames, "
                f"{ev.get('agreement', 0) * 100:.0f}% agreement "
                f"({ev.get('method', 'consensus')})"
            )
        if raw.get("corrected_from"):
            print(f"   corrected   {raw['corrected_from']} → {alert.plate_normalised}")

    print(
        f"\n   {GREEN}The vehicle passed and the alert fired "
        f"{elapsed:.1f}s after the watchlist entry was made.{RESET}"
    )
    print(
        f"   {DIM}That interval is how long until the car came round again, not "
        f"pipeline latency;{RESET}"
    )
    print(
        f"   {DIM}detection-to-alert is measured separately in the Phase 6 "
        f"tests.{RESET}"
    )

    print(
        f"\n{DIM}   Operators get this on /ws/events the moment it is raised, and "
        f"can acknowledge,{RESET}"
    )
    print(
        f"{DIM}   dispatch or mark it a false positive from the Alerts screen.{RESET}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
