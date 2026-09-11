"""`GET /api/v1/detections` — including attribute search beyond plates (P9).

No test file existed for this endpoint before this change, despite it
already having real filter logic (`camera_id`, `plate`, `plate_prefix`,
`readable_only`, `min_confidence`) — this covers that alongside the new
`vehicle_type`/`vehicle_colour` filters this session added, since both sets
of filters are built by the same `_apply_filters` function and a change to
one risks the other.

Same historical-window isolation as `test_analytics.py`/`test_anomaly.py`/
`test_search.py`, for the same reason: deterministic against a live,
concurrently-running demo with no mocking.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.intelligence import Detection

pytestmark = pytest.mark.integration

WINDOW_START = datetime(2024, 3, 4, 8, 0, 0, tzinfo=UTC)

#: Distinguishes this file's rows for cleanup without touching real
#: simulator data or another test file's fixtures.
TRACK_PREFIX = "detections-test-"


async def _insert(
    *,
    camera_id: uuid.UUID,
    ts: datetime,
    vehicle_type: str | None,
    plate: str | None = None,
    suffix: str = "",
) -> None:
    async with SessionLocal() as session:
        session.add(
            Detection(
                id=uuid.uuid4(),
                ts=ts,
                camera_id=camera_id,
                track_id=f"{TRACK_PREFIX}{uuid.uuid4().hex[:8]}{suffix}",
                vehicle_type=vehicle_type,
                plate_normalised=plate,
                plate_confidence=0.9 if plate else None,
            )
        )
        await session.commit()


async def _cleanup() -> None:
    async with SessionLocal() as session:
        await session.execute(
            text("DELETE FROM detections WHERE track_id LIKE :p"), {"p": f"{TRACK_PREFIX}%"}
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _clean_synthetic_rows():
    await _cleanup()
    yield
    await _cleanup()


@pytest.fixture
async def cameras() -> list[dict]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                text("SELECT id, camera_code FROM cameras ORDER BY camera_code LIMIT 2")
            )
        ).mappings()
        result = [dict(r) for r in rows]
    if len(result) < 2:
        pytest.skip("fewer than 2 cameras seeded — run scripts/seed.py")
    return result


class TestAttributeSearch:
    """Search beyond plates: type, camera and time all work today; colour
    is filterable but always empty — see `routers/detections.py`."""

    async def test_type_filter_excludes_other_classes(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        await _insert(camera_id=cameras[0]["id"], ts=WINDOW_START, vehicle_type="car", suffix="car")
        await _insert(
            camera_id=cameras[0]["id"], ts=WINDOW_START, vehicle_type="motorcycle", suffix="moto"
        )

        response = await client.get(
            "/api/v1/detections",
            params={
                "vehicle_type": "car",
                "since": WINDOW_START.isoformat(),
                "until": (WINDOW_START + timedelta(hours=1)).isoformat(),
                "readable_only": "false",
            },
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        body = response.json()

        types = {item["vehicle_type"] for item in body["items"]}
        assert types == {"car"}

    async def test_no_type_given_defaults_to_vehicle_classes_only(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        """An attribute search with no plate and no explicit type is asking
        about vehicles — a tracked person must not appear as a candidate."""
        await _insert(
            camera_id=cameras[0]["id"], ts=WINDOW_START, vehicle_type="car", suffix="car2"
        )
        await _insert(
            camera_id=cameras[0]["id"], ts=WINDOW_START, vehicle_type="person", suffix="person"
        )

        response = await client.get(
            "/api/v1/detections",
            params={
                "since": WINDOW_START.isoformat(),
                "until": (WINDOW_START + timedelta(hours=1)).isoformat(),
                "readable_only": "false",
            },
            headers=await auth_headers("analyst"),
        )
        body = response.json()
        types = {item["vehicle_type"] for item in body["items"]}

        assert "car" in types
        assert "person" not in types

    async def test_an_explicit_person_type_is_still_respected(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        """The vehicle-only default fills a gap; it never overrides an
        explicit choice."""
        await _insert(
            camera_id=cameras[0]["id"], ts=WINDOW_START, vehicle_type="person", suffix="person2"
        )

        response = await client.get(
            "/api/v1/detections",
            params={
                "vehicle_type": "person",
                "since": WINDOW_START.isoformat(),
                "until": (WINDOW_START + timedelta(hours=1)).isoformat(),
                "readable_only": "false",
            },
            headers=await auth_headers("analyst"),
        )
        body = response.json()

        assert body["total"] >= 1
        assert all(item["vehicle_type"] == "person" for item in body["items"])

    async def test_a_plate_search_is_not_restricted_to_vehicle_classes(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        """The vehicle-class default only applies when there is no plate to
        anchor the query — a plate search must not silently drop a match
        just because `vehicle_type` was stored oddly."""
        plate = "ZZDETECT0099XY"
        await _insert(
            camera_id=cameras[0]["id"],
            ts=WINDOW_START,
            vehicle_type="bicycle",
            plate=plate,
            suffix="platebike",
        )

        response = await client.get(
            "/api/v1/detections",
            params={"plate": plate, "since": WINDOW_START.isoformat()},
            headers=await auth_headers("analyst"),
        )
        body = response.json()

        assert body["total"] == 1
        assert body["items"][0]["plate"] == plate

    async def test_vehicle_colour_matches_nothing_today(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        """Honest limitation, not a bug: nothing populates this column yet."""
        await _insert(
            camera_id=cameras[0]["id"], ts=WINDOW_START, vehicle_type="car", suffix="colour"
        )

        response = await client.get(
            "/api/v1/detections",
            params={
                "vehicle_colour": "white",
                "since": WINDOW_START.isoformat(),
                "until": (WINDOW_START + timedelta(hours=1)).isoformat(),
                "readable_only": "false",
            },
            headers=await auth_headers("analyst"),
        )
        assert response.json()["total"] == 0

    async def test_camera_and_time_filters_narrow_an_attribute_search(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        a, b = cameras
        await _insert(camera_id=a["id"], ts=WINDOW_START, vehicle_type="truck", suffix="truckA")
        await _insert(camera_id=b["id"], ts=WINDOW_START, vehicle_type="truck", suffix="truckB")

        response = await client.get(
            "/api/v1/detections",
            params={
                "vehicle_type": "truck",
                "camera_id": str(a["id"]),
                "since": WINDOW_START.isoformat(),
                "until": (WINDOW_START + timedelta(hours=1)).isoformat(),
                "readable_only": "false",
            },
            headers=await auth_headers("analyst"),
        )
        body = response.json()

        assert body["total"] == 1
        assert body["items"][0]["camera_id"] == str(a["id"])

    async def test_an_attribute_only_search_is_audited(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        from sqlalchemy import func, select

        from app.models.security import AuditLog

        await _insert(
            camera_id=cameras[0]["id"], ts=WINDOW_START, vehicle_type="bus", suffix="audit"
        )
        headers = await auth_headers("analyst")

        async def audit_rows() -> int:
            async with SessionLocal() as session:
                return (
                    await session.execute(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(AuditLog.action == "search.plate", AuditLog.username == "analyst")
                    )
                ).scalar_one()

        before = await audit_rows()
        await client.get(
            "/api/v1/detections",
            params={
                "vehicle_type": "bus",
                "since": WINDOW_START.isoformat(),
                "readable_only": "false",
            },
            headers=headers,
        )
        assert await audit_rows() == before + 1


class TestExistingFiltersStillWork:
    """Regression coverage for the plate/pagination behaviour that predates
    this change — `_apply_filters` is shared, so a bug in the new clauses
    could plausibly break these instead."""

    async def test_readable_only_defaults_to_true(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        await _insert(
            camera_id=cameras[0]["id"], ts=WINDOW_START, vehicle_type="car", suffix="noplate"
        )

        response = await client.get(
            "/api/v1/detections",
            params={
                "since": WINDOW_START.isoformat(),
                "until": (WINDOW_START + timedelta(hours=1)).isoformat(),
            },
            headers=await auth_headers("analyst"),
        )
        # The unplated row from this test must not appear without
        # readable_only=false, whatever else is in the window.
        assert all(item["plate"] for item in response.json()["items"])

    async def test_plate_prefix_matches_a_partial_plate(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        await _insert(
            camera_id=cameras[0]["id"],
            ts=WINDOW_START,
            vehicle_type="car",
            plate="ZZDETECT0077PQ",
            suffix="prefix",
        )

        response = await client.get(
            "/api/v1/detections",
            params={"plate_prefix": "ZZDETECT0077", "since": WINDOW_START.isoformat()},
            headers=await auth_headers("analyst"),
        )
        body = response.json()

        assert body["total"] == 1
        assert body["items"][0]["plate"] == "ZZDETECT0077PQ"

    async def test_pagination_total_is_independent_of_the_page_size(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        for i in range(3):
            await _insert(
                camera_id=cameras[0]["id"],
                ts=WINDOW_START + timedelta(minutes=i),
                vehicle_type="car",
                suffix=f"page{i}",
            )

        response = await client.get(
            "/api/v1/detections",
            params={
                "since": WINDOW_START.isoformat(),
                "until": (WINDOW_START + timedelta(hours=1)).isoformat(),
                "readable_only": "false",
                "vehicle_type": "car",
                "limit": 2,
            },
            headers=await auth_headers("analyst"),
        )
        body = response.json()

        assert body["total"] == 3
        assert len(body["items"]) == 2


class TestAccessControl:
    async def test_unauthenticated_is_refused(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/detections")).status_code == 401
