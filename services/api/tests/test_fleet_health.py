"""Fleet health, gap analysis, and the stream gateway.

Camera health monitoring, coverage and capability gap reporting, and the
audited stream-token flow.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.services.fleet import CITY_TALUKAS

#: A floor, not a count. The Ahmedabad fleet is ~58 cameras
#: (scripts/generate_ahmedabad_cameras.py) and federated sources add to it, so
#: an exact figure would fail the moment the registry does its job.
#:
#: It is deliberately a floor rather than a large number: the registry was once
#: seeded with 250 synthetic rows, 251 of 281 of which had no stream URL at all
#: and so could never be watched, analysed, or even be unhealthy. Asserting a
#: count in the hundreds tested that a CSV had loaded, not that the platform
#: worked. What matters is that a real fleet is onboarded and every screen
#: agrees about its size.
ONBOARDED_FLEET = 10

pytestmark = pytest.mark.integration


class TestFleetHealthEndpoint:
    async def test_reports_the_whole_estate(self, client: AsyncClient, auth_headers) -> None:
        body = (
            await client.get("/api/v1/health/fleet", headers=await auth_headers("operator"))
        ).json()

        assert body["total"] >= ONBOARDED_FLEET
        assert (body["online"] + body["offline"] + body["degraded"] + body["unknown"]) == body[
            "total"
        ]

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

        assert len(body["by_department"]) >= 1
        assert sum(r["total"] for r in body["by_department"]) >= ONBOARDED_FLEET

        # What matters is that every vendor row represents real cameras — not
        # how many rows there are.
        #
        # The seed once registered four extra VMS instances — Milestone,
        # Genetec, CP Plus, Hikvision — each `adapter_type: simulated` with not
        # one camera behind it. A "at least N vendors" assertion passed happily
        # on those while the breakdown represented no integration at all, which
        # is the failure worth guarding against. Counting rows cannot catch it;
        # requiring every row to have cameras can.
        #
        # This also stopped being a count the fleet could satisfy: the hosted
        # grid federates **zero** cameras today (it is a removal candidate —
        # CLAUDE.md §10), so the honest number of vendors actually carrying
        # traffic is one. The multi-vendor claim is asserted where it is true:
        # the adapter registry below reports the interface's real
        # implementations, which are code and are unit-tested.
        vendors = body["by_vendor"]
        assert vendors, "fleet health must break the estate down by vendor"
        assert all(row["total"] > 0 for row in vendors), (
            f"a vendor row with no cameras represents no integration: {vendors}"
        )
        assert sum(row["total"] for row in vendors) >= ONBOARDED_FLEET

    async def test_the_adapter_interface_covers_more_than_is_connected(
        self, client: AsyncClient, auth_headers
    ) -> None:
        """Capability and deployment are different claims and are reported apart."""
        body = (
            await client.get(
                "/api/v1/integration/adapters", headers=await auth_headers("supervisor")
            )
        ).json()
        assert set(body["adapters"]) >= {"rtsp", "onvif", "vendor_api"}

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

    async def test_coverage_gaps_name_only_genuinely_blind_talukas(
        self, client: AsyncClient, auth_headers
    ) -> None:
        """A blind taluka is one a vehicle can cross entirely unobserved.

        This asserts the report is *correct* rather than that gaps exist. The
        previous version required `len(uncovered) > 0`, which quietly encoded
        the old statewide fleet's coverage — 8 of Gujarat's 33 districts — and
        broke as soon as the fleet covered every taluka it claims to. A fully
        covered city is a passing state, not a failing one.
        """
        headers = await auth_headers("analyst")
        body = (await client.get("/api/v1/health/gaps", headers=headers)).json()
        summary = (await client.get("/api/v1/cameras/summary", headers=headers)).json()

        # `by_district` is a mapping of district -> camera count.
        with_cameras = {name for name, count in summary["by_district"].items() if count > 0}
        uncovered = {gap["district"] for gap in body["coverage_gaps"]}

        # A taluka holding cameras can never be blind.
        assert not (
            uncovered & with_cameras
        ), f"reported as blind despite having cameras: {uncovered & with_cameras}"
        # And every blind taluka must be one the platform actually claims to
        # cover, rather than an arbitrary string.
        assert (
            uncovered <= CITY_TALUKAS
        ), f"unknown talukas in gap report: {uncovered - CITY_TALUKAS}"

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

        assert sum(d["cameras"] for d in body["district_coverage"]) >= ONBOARDED_FLEET


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

        assert {"rtsp", "onvif", "vendor_api", "hosted_grid"} <= body["adapters"].keys()


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
