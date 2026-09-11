"""Fuzzy and partial plate search — `GET /api/v1/vehicles/search` (P8).

Every test builds its own `detections` (and where relevant, `watchlist`)
rows inside a fixed historical window, for the same reason
`test_analytics.py` does: the properties under test are precise claims about
ranking and thresholds that a live, concurrently-running demo cannot
guarantee, and a fixed past date the simulator will never touch gives full
isolation with no mocking.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import SessionLocal
from app.models.intelligence import Detection, Watchlist

pytestmark = pytest.mark.integration

WINDOW_START = datetime(2024, 3, 4, 8, 0, 0, tzinfo=UTC)

#: Plates used by this file. Long enough that a one-character edit stays a
#: clear near-match rather than accidentally matching something else built
#: from the same prefix.
CORRECT_PLATE = "ZZSEARCH9001AB"
MISREAD_PLATE = "ZZSEARCH9081AB"  # one substitution from CORRECT_PLATE
UNRELATED_PLATE = "ZZOTHER4002CD"


async def _insert_detection(
    plate: str,
    ts: datetime,
    camera_id: uuid.UUID,
    *,
    vehicle_type: str | None = None,
) -> None:
    async with SessionLocal() as session:
        session.add(
            Detection(
                id=uuid.uuid4(),
                ts=ts,
                camera_id=camera_id,
                track_id=f"search-test-{uuid.uuid4().hex[:8]}",
                plate_normalised=plate,
                plate_confidence=0.9,
                vehicle_type=vehicle_type,
            )
        )
        await session.commit()


async def _insert_watchlist(plate: str, *, active: bool = True, priority: str = "high") -> None:
    async with SessionLocal() as session:
        session.add(
            Watchlist(
                id=uuid.uuid4(),
                plate_normalised=plate,
                category="stolen",
                priority=priority,
                case_ref="FIR/TEST/0001",
                active=active,
            )
        )
        await session.commit()


async def _cleanup() -> None:
    async with SessionLocal() as session:
        await session.execute(
            text("DELETE FROM detections WHERE plate_normalised LIKE 'ZZ%'")
        )
        await session.execute(
            text("DELETE FROM watchlist WHERE plate_normalised LIKE 'ZZ%'")
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
            await session.execute(text("SELECT id, camera_code FROM cameras ORDER BY camera_code LIMIT 2"))
        ).mappings()
        result = [dict(r) for r in rows]
    if len(result) < 2:
        pytest.skip("fewer than 2 cameras seeded — run scripts/seed.py")
    return result


class TestFuzzyMatch:
    async def test_a_misread_plate_finds_the_correct_one(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        await _insert_detection(CORRECT_PLATE, WINDOW_START, cameras[0]["id"])

        response = await client.get(
            "/api/v1/vehicles/search",
            params={"q": MISREAD_PLATE},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["query"] == MISREAD_PLATE
        assert body["exact_match"] is False
        assert body["results"], "a one-character-off query must still find the real plate"
        top = body["results"][0]
        assert top["plate_normalised"] == CORRECT_PLATE
        assert 0.3 <= top["similarity"] < 1.0

    async def test_an_exact_query_is_marked_exact(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        await _insert_detection(CORRECT_PLATE, WINDOW_START, cameras[0]["id"])

        response = await client.get(
            "/api/v1/vehicles/search",
            params={"q": CORRECT_PLATE},
            headers=await auth_headers("analyst"),
        )
        body = response.json()

        assert body["exact_match"] is True
        assert body["results"][0]["plate_normalised"] == CORRECT_PLATE
        assert body["results"][0]["similarity"] == 1.0

    async def test_an_unrelated_plate_is_not_returned(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        """A query sharing no real trigram similarity with anything present
        must not force-match the closest thing available — see the threshold
        floor `_FUZZY_SEARCH_SQL` documents."""
        await _insert_detection(UNRELATED_PLATE, WINDOW_START, cameras[0]["id"])

        response = await client.get(
            "/api/v1/vehicles/search",
            params={"q": CORRECT_PLATE},
            headers=await auth_headers("analyst"),
        )
        body = response.json()

        assert UNRELATED_PLATE not in {r["plate_normalised"] for r in body["results"]}

    async def test_a_query_too_short_is_rejected(
        self, client: AsyncClient, auth_headers
    ) -> None:
        response = await client.get(
            "/api/v1/vehicles/search",
            params={"q": "G1"},
            headers=await auth_headers("analyst"),
        )
        assert response.status_code == 422


class TestFacetsAndFilters:
    async def test_sightings_and_cameras_are_counted_correctly(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        a, b = cameras
        await _insert_detection(CORRECT_PLATE, WINDOW_START, a["id"])
        await _insert_detection(CORRECT_PLATE, WINDOW_START + timedelta(minutes=5), a["id"])
        await _insert_detection(CORRECT_PLATE, WINDOW_START + timedelta(minutes=10), b["id"])

        response = await client.get(
            "/api/v1/vehicles/search",
            params={"q": CORRECT_PLATE},
            headers=await auth_headers("analyst"),
        )
        result = response.json()["results"][0]

        assert result["sightings"] == 3
        assert result["cameras"] == 2
        assert result["first_seen"].startswith("2024-03-04T08:00:00")
        assert result["last_seen"].startswith("2024-03-04T08:10:00")

    async def test_camera_filter_narrows_the_facets(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        a, b = cameras
        await _insert_detection(CORRECT_PLATE, WINDOW_START, a["id"])
        await _insert_detection(CORRECT_PLATE, WINDOW_START + timedelta(minutes=5), b["id"])

        response = await client.get(
            "/api/v1/vehicles/search",
            params={"q": CORRECT_PLATE, "camera_id": str(a["id"])},
            headers=await auth_headers("analyst"),
        )
        result = response.json()["results"][0]

        assert result["sightings"] == 1
        assert result["cameras"] == 1

    async def test_time_window_narrows_the_facets(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        await _insert_detection(CORRECT_PLATE, WINDOW_START, cameras[0]["id"])
        await _insert_detection(CORRECT_PLATE, WINDOW_START + timedelta(days=1), cameras[0]["id"])

        response = await client.get(
            "/api/v1/vehicles/search",
            params={
                "q": CORRECT_PLATE,
                "since": WINDOW_START.isoformat(),
                "until": (WINDOW_START + timedelta(hours=1)).isoformat(),
            },
            headers=await auth_headers("analyst"),
        )
        result = response.json()["results"][0]

        assert result["sightings"] == 1

    async def test_vehicle_type_filters_the_facets(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        await _insert_detection(CORRECT_PLATE, WINDOW_START, cameras[0]["id"], vehicle_type="car")
        await _insert_detection(
            CORRECT_PLATE, WINDOW_START + timedelta(minutes=5), cameras[0]["id"], vehicle_type="truck"
        )

        response = await client.get(
            "/api/v1/vehicles/search",
            params={"q": CORRECT_PLATE, "vehicle_type": "truck"},
            headers=await auth_headers("analyst"),
        )
        result = response.json()["results"][0]

        assert result["sightings"] == 1


class TestWatchlistFacet:
    async def test_an_active_watchlist_hit_is_surfaced(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        await _insert_detection(CORRECT_PLATE, WINDOW_START, cameras[0]["id"])
        await _insert_watchlist(CORRECT_PLATE, priority="critical")

        response = await client.get(
            "/api/v1/vehicles/search",
            params={"q": MISREAD_PLATE},
            headers=await auth_headers("analyst"),
        )
        result = response.json()["results"][0]

        assert result["watchlist"] is not None
        assert result["watchlist"]["category"] == "stolen"
        assert result["watchlist"]["priority"] == "critical"
        assert result["watchlist"]["case_ref"] == "FIR/TEST/0001"

    async def test_a_retired_watchlist_entry_is_not_surfaced(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        await _insert_detection(CORRECT_PLATE, WINDOW_START, cameras[0]["id"])
        await _insert_watchlist(CORRECT_PLATE, active=False)

        response = await client.get(
            "/api/v1/vehicles/search",
            params={"q": CORRECT_PLATE},
            headers=await auth_headers("analyst"),
        )
        result = response.json()["results"][0]

        assert result["watchlist"] is None


class TestAccessControlAndAudit:
    async def test_unauthenticated_is_refused(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/vehicles/search", params={"q": CORRECT_PLATE})
        assert response.status_code == 401

    async def test_an_auditor_may_not_search(self, client: AsyncClient, auth_headers) -> None:
        """Same matrix entry as vehicle_route and vehicle_convoy: search is
        withheld from the role that reviews searches."""
        response = await client.get(
            "/api/v1/vehicles/search",
            params={"q": CORRECT_PLATE},
            headers=await auth_headers("auditor"),
        )
        assert response.status_code == 403

    async def test_a_search_is_recorded_in_the_audit_trail(
        self, client: AsyncClient, auth_headers, cameras: list[dict]
    ) -> None:
        from sqlalchemy import func, select

        from app.models.security import AuditLog

        await _insert_detection(CORRECT_PLATE, WINDOW_START, cameras[0]["id"])
        headers = await auth_headers("analyst")

        async def audit_rows() -> int:
            async with SessionLocal() as session:
                return (
                    await session.execute(
                        select(func.count())
                        .select_from(AuditLog)
                        .where(AuditLog.action == "search.plate", AuditLog.resource_id == CORRECT_PLATE)
                    )
                ).scalar_one()

        before = await audit_rows()
        await client.get("/api/v1/vehicles/search", params={"q": CORRECT_PLATE}, headers=headers)
        assert await audit_rows() == before + 1
