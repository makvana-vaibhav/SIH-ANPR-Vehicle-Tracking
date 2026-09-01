"""The vehicle-intelligence endpoints over HTTP.

Reconstructing where a vehicle has been is the most privacy-sensitive read this
platform offers, so two things are asserted here beyond "does it work": that it
is gated on a permission, and that every call leaves an audit row naming who
asked. A surveillance system that cannot say who looked at whom is not one a
police force should be given.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.security import AuditLog

pytestmark = pytest.mark.asyncio

PLATE = "GJ03AB1234"


async def audit_rows(action: str, query: str) -> int:
    async with SessionLocal() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == action, AuditLog.resource_id == query)
            )
        ).scalar_one()


class TestRouteEndpoint:
    async def test_returns_a_route_document(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        response = await client.get(f"/api/v1/vehicles/{PLATE}/route", headers=headers)
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["plate"] == PLATE
        assert "hops" in body and isinstance(body["hops"], list)
        assert body["hop_count"] == len(body["hops"])
        # The claim the whole endpoint rests on must travel with the answer.
        assert "lower bound" in body["geometry_note"]

    async def test_geojson_format_is_valid_geojson(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        response = await client.get(
            f"/api/v1/vehicles/{PLATE}/route?format=geojson", headers=headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["type"] == "FeatureCollection"
        assert isinstance(body["features"], list)

    async def test_a_plate_never_seen_returns_an_empty_route_not_404(
        self, client: AsyncClient, auth_headers
    ):
        """No sightings is an answer, not a missing resource."""
        headers = await auth_headers("analyst")
        response = await client.get("/api/v1/vehicles/GJ99ZZ0000/route", headers=headers)
        assert response.status_code == 200
        assert response.json()["hop_count"] == 0

    async def test_the_plate_is_normalised_for_the_caller(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        response = await client.get("/api/v1/vehicles/gj03%20ab-1234/route", headers=headers)
        assert response.status_code == 200
        assert response.json()["plate"] == PLATE

    async def test_a_plate_too_short_to_search_is_rejected(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        response = await client.get("/api/v1/vehicles/GJ/route", headers=headers)
        assert response.status_code == 422

    async def test_an_unknown_format_is_rejected(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        response = await client.get(f"/api/v1/vehicles/{PLATE}/route?format=xml", headers=headers)
        assert response.status_code == 422


class TestConvoyEndpoint:
    async def test_returns_a_convoy_document(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        response = await client.get(f"/api/v1/vehicles/{PLATE}/convoy", headers=headers)
        assert response.status_code == 200, response.text

        body = response.json()
        assert body["plate"] == PLATE
        assert isinstance(body["convoys"], list)
        assert body["criteria"]["min_shared_cameras"] == 3
        # Co-occurrence is not association, and the response says so.
        assert "not proof" in body["note"]

    async def test_the_thresholds_are_the_callers_to_set(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        response = await client.get(
            f"/api/v1/vehicles/{PLATE}/convoy?window_s=30&min_shared_cameras=5",
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["criteria"] == {
            "seconds_apart": 30.0,
            "min_shared_cameras": 5,
        }

    async def test_an_absurd_window_is_rejected(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        response = await client.get(
            f"/api/v1/vehicles/{PLATE}/convoy?window_s=99999", headers=headers
        )
        assert response.status_code == 422


class TestRoutablePlates:
    async def test_lists_plates_worth_asking_about(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        response = await client.get(
            "/api/v1/vehicles/routable?min_cameras=2&limit=5", headers=headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["min_cameras"] == 2
        assert len(body["plates"]) <= 5
        assert all(p["cameras"] >= 2 for p in body["plates"])


class TestAccessControl:
    async def test_unauthenticated_callers_are_refused(self, client: AsyncClient):
        for path in (
            f"/api/v1/vehicles/{PLATE}/route",
            f"/api/v1/vehicles/{PLATE}/convoy",
            "/api/v1/vehicles/routable",
        ):
            assert (await client.get(path)).status_code == 401

    async def test_an_auditor_may_not_trace_vehicles(self, client: AsyncClient, auth_headers):
        """Auditors read the audit trail; they do not run surveillance queries."""
        headers = await auth_headers("auditor")
        response = await client.get(f"/api/v1/vehicles/{PLATE}/route", headers=headers)
        assert response.status_code == 403

    async def test_an_operator_may_trace_a_vehicle(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("operator")
        response = await client.get(f"/api/v1/vehicles/{PLATE}/route", headers=headers)
        assert response.status_code == 200


class TestAuditTrail:
    async def test_a_route_query_is_recorded(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        before = await audit_rows("search.plate", PLATE)
        await client.get(f"/api/v1/vehicles/{PLATE}/route", headers=headers)
        assert await audit_rows("search.plate", PLATE) == before + 1

    async def test_a_convoy_query_is_recorded(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("analyst")
        before = await audit_rows("search.plate", PLATE)
        await client.get(f"/api/v1/vehicles/{PLATE}/convoy", headers=headers)
        assert await audit_rows("search.plate", PLATE) == before + 1
