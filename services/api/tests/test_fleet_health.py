"""Fleet health, gap analysis, and the stream gateway.

Covers the two named Model 1 requirements from challenge FAQ Q15 — camera
health monitoring and gap-analysis reports — plus the audited stream-token flow.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


class TestFleetHealthEndpoint:
    async def test_reports_the_whole_estate(self, client: AsyncClient, auth_headers) -> None:
        body = (
            await client.get("/api/v1/health/fleet", headers=await auth_headers("operator"))
        ).json()

        assert body["total"] == 250
        assert body["online"] + body["offline"] + body["degraded"] + body["unknown"] == 250

    async def test_separates_integrated_from_registered(
        self, client: AsyncClient, auth_headers
    ) -> None:
        """Availability over the whole registry would understate the health of
        the estate we actually operate, and overstate an outage."""
        body = (
            await client.get("/api/v1/health/fleet", headers=await auth_headers("operator"))
        ).json()

        assert body["integrated"] == body["online"] + body["offline"] + body["degraded"]
        assert body["awaiting_integration"] == body["unknown"]
        assert body["integrated"] + body["awaiting_integration"] == body["total"]

    async def test_breaks_down_by_department_and_vendor(
        self, client: AsyncClient, auth_headers
    ) -> None:
        """A commissioner needs to see which department is degrading, not just
        a statewide average."""
        body = (
            await client.get("/api/v1/health/fleet", headers=await auth_headers("supervisor"))
        ).json()

        assert len(body["by_department"]) >= 4
        assert sum(r["total"] for r in body["by_department"]) == 250
        # Multi-vendor federation is the architecture's claim; assert it holds.
        assert len({r["vendor"] for r in body["by_vendor"]}) >= 3

    async def test_groups_failure_causes(self, client: AsyncClient, auth_headers) -> None:
        """Grouping by error code turns many symptoms into one root cause."""
        body = (
            await client.get("/api/v1/health/fleet", headers=await auth_headers("operator"))
        ).json()

        assert isinstance(body["top_errors"], list)
        for entry in body["top_errors"]:
            assert {"error_code", "count"} <= entry.keys()

    async def test_requires_authentication(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/health/fleet")).status_code == 401


class TestGapAnalysis:
    """ "Where are we blind?" — a different question from "what is broken?"."""

    async def test_reports_four_distinct_gap_types(self, client: AsyncClient, auth_headers) -> None:
        body = (
            await client.get("/api/v1/health/gaps", headers=await auth_headers("supervisor"))
        ).json()

        assert {
            "availability_gaps",
            "capability_gaps",
            "coverage_gaps",
            "reliability_gaps",
        } <= body.keys()

    async def test_identifies_districts_with_no_cameras(
        self, client: AsyncClient, auth_headers
    ) -> None:
        """Gujarat has 33 districts; our fleet covers 8. The rest are blind
        spots a vehicle can cross entirely unobserved."""
        body = (
            await client.get("/api/v1/health/gaps", headers=await auth_headers("analyst"))
        ).json()

        uncovered = {g["district"] for g in body["coverage_gaps"]}
        assert len(uncovered) > 0
        # Districts we do cover must not be listed as blind.
        assert "Rajkot" not in uncovered
        assert "Ahmedabad" not in uncovered

    async def test_every_gap_explains_itself(self, client: AsyncClient, auth_headers) -> None:
        """A report saying only "gap" is not actionable."""
        body = (
            await client.get("/api/v1/health/gaps", headers=await auth_headers("analyst"))
        ).json()

        for gap in body["coverage_gaps"] + body["capability_gaps"]:
            assert gap.get("reason")

    async def test_district_coverage_totals_match_the_fleet(
        self, client: AsyncClient, auth_headers
    ) -> None:
        body = (
            await client.get("/api/v1/health/gaps", headers=await auth_headers("analyst"))
        ).json()

        assert sum(d["cameras"] for d in body["district_coverage"]) == 250


class TestCameraHealthHistory:
    async def test_returns_probe_history_and_uptime(
        self, client: AsyncClient, auth_headers
    ) -> None:
        headers = await auth_headers("operator")
        page = (await client.get("/api/v1/cameras?limit=1", headers=headers)).json()
        camera_id = page["items"][0]["id"]

        body = (await client.get(f"/api/v1/cameras/{camera_id}/health", headers=headers)).json()

        assert body["camera_id"] == camera_id
        assert "uptime" in body
        assert isinstance(body["history"], list)

    async def test_unknown_camera_is_404(self, client: AsyncClient, auth_headers) -> None:
        response = await client.get(
            "/api/v1/cameras/11111111-1111-1111-1111-111111111111/health",
            headers=await auth_headers("operator"),
        )
        assert response.status_code == 404


class TestIntegrationAdapters:
    async def test_lists_every_supported_protocol(self, client: AsyncClient, auth_headers) -> None:
        """The interoperability evidence: one interface, many vendors."""
        body = (
            await client.get("/api/v1/integration/adapters", headers=await auth_headers("operator"))
        ).json()

        assert {"rtsp", "onvif", "vendor_api", "sentinel_sandbox"} <= body["adapters"].keys()


class TestStreamGateway:
    """Opening a live feed — the most privacy-sensitive routine action."""

    async def _first_camera_id(self, client: AsyncClient, headers: dict) -> str:
        page = (await client.get("/api/v1/cameras?limit=1", headers=headers)).json()
        return page["items"][0]["id"]

    async def test_issues_a_camera_scoped_token(self, client: AsyncClient, auth_headers) -> None:
        headers = await auth_headers("operator")
        camera_id = await self._first_camera_id(client, headers)

        response = await client.get(f"/api/v1/cameras/{camera_id}/stream", headers=headers)

        assert response.status_code == 200
        grant = response.json()
        assert grant["camera_id"] == camera_id
        assert grant["token"]
        # Short-lived: a URL leaked from browser history dies quickly.
        assert grant["expires_in"] <= 300
        assert grant["whep_url"]
        assert grant["hls_url"], "HLS fallback must be offered when WebRTC is blocked"

    async def test_token_is_verifiable(self, client: AsyncClient, auth_headers) -> None:
        headers = await auth_headers("operator")
        camera_id = await self._first_camera_id(client, headers)
        grant = (await client.get(f"/api/v1/cameras/{camera_id}/stream", headers=headers)).json()

        verify = await client.get(
            f"/api/v1/streams/verify?token={grant['token']}&camera_id={camera_id}"
        )

        assert verify.status_code == 200
        assert verify.json()["valid"] is True

    async def test_token_for_one_camera_cannot_open_another(
        self, client: AsyncClient, auth_headers
    ) -> None:
        """Camera scoping is the point of the token."""
        headers = await auth_headers("operator")
        page = (await client.get("/api/v1/cameras?limit=2", headers=headers)).json()
        first, second = page["items"][0]["id"], page["items"][1]["id"]

        grant = (await client.get(f"/api/v1/cameras/{first}/stream", headers=headers)).json()

        verify = await client.get(
            f"/api/v1/streams/verify?token={grant['token']}&camera_id={second}"
        )
        assert verify.status_code == 403

    async def test_unauthenticated_token_request_is_rejected(
        self, client: AsyncClient, auth_headers
    ) -> None:
        headers = await auth_headers("operator")
        camera_id = await self._first_camera_id(client, headers)

        assert (await client.get(f"/api/v1/cameras/{camera_id}/stream")).status_code == 401

    async def test_garbage_token_is_rejected(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/streams/verify?token=not-a-token")
        assert response.status_code == 401

    @pytest.mark.parametrize("role", ["analyst", "auditor"])
    async def test_roles_without_stream_view_are_denied(
        self, client: AsyncClient, auth_headers, role: str
    ) -> None:
        """Analysts and auditors work over recorded detections, not live video."""
        admin = await auth_headers("admin")
        camera_id = await self._first_camera_id(client, admin)

        response = await client.get(
            f"/api/v1/cameras/{camera_id}/stream", headers=await auth_headers(role)
        )
        assert response.status_code == 403

    async def test_opening_a_stream_writes_an_audit_row(
        self, client: AsyncClient, auth_headers
    ) -> None:
        """One of the three actions CLAUDE.md promises to record."""
        from sqlalchemy import select

        from app.db.session import SessionLocal
        from app.models.security import AuditLog

        headers = await auth_headers("operator")
        camera_id = await self._first_camera_id(client, headers)

        await client.get(f"/api/v1/cameras/{camera_id}/stream", headers=headers)

        async with SessionLocal() as session:
            row = await session.scalar(
                select(AuditLog)
                .where(AuditLog.action == "camera.view")
                .order_by(AuditLog.ts.desc())
                .limit(1)
            )

        assert row is not None
        assert row.username == "operator"
        assert row.resource_id == camera_id

    async def test_stream_urls_are_not_exposed_without_a_token_request(
        self, client: AsyncClient, auth_headers
    ) -> None:
        """Listing cameras must never hand out unaudited viewing access."""
        headers = await auth_headers("operator")
        listing = (await client.get("/api/v1/cameras?limit=50", headers=headers)).text

        assert "whep" not in listing.lower()
        assert "rtsp://" not in listing
