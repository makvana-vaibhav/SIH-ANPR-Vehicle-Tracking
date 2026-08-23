"""Tests for the liveness and readiness endpoints.

These encode the operational contract described in ``app/routers/health.py``:
liveness must never depend on infrastructure, and readiness must distinguish a
fatal dependency loss from a degraded-but-serving state. Both distinctions are
load-bearing — getting them wrong produces either crash loops or an API that
claims health while unable to answer a query.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


class TestLiveness:
    """/health — is the process alive?"""

    async def test_returns_200_and_alive(self, client: AsyncClient) -> None:
        response = await client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "alive"
        assert body["service"] == "Sentinel-GJ"

    async def test_reports_uptime_and_utc_timestamps(self, client: AsyncClient) -> None:
        """Timestamps are timezone-aware UTC (CLAUDE.md §5)."""
        body = (await client.get("/health")).json()

        assert body["uptime_seconds"] >= 0
        # A naive datetime would have no offset suffix at all.
        assert body["now"].endswith("+00:00")
        assert body["started_at"].endswith("+00:00")

    async def test_performs_no_dependency_io(self, client: AsyncClient) -> None:
        """Liveness must not touch the database.

        If it did, a brief Postgres blip would restart every healthy API
        container and turn an outage into a crash loop. Asserted by making the
        database probe explode and confirming liveness is unaffected.
        """
        import app.routers.health as health_module

        async def _explode() -> dict[str, object]:
            raise AssertionError("liveness must not probe dependencies")

        original = health_module.check_database
        health_module.check_database = _explode  # type: ignore[assignment]
        try:
            response = await client.get("/health")
        finally:
            health_module.check_database = original  # type: ignore[assignment]

        assert response.status_code == 200

    async def test_returns_correlation_id_header(self, client: AsyncClient) -> None:
        """Every response carries an X-Request-ID an operator can quote."""
        response = await client.get("/health")

        assert response.headers.get("X-Request-ID")

    async def test_echoes_supplied_correlation_id(self, client: AsyncClient) -> None:
        """A caller-supplied id is preserved so traces span services."""
        supplied = "11111111-2222-3333-4444-555555555555"

        response = await client.get("/health", headers={"X-Request-ID": supplied})

        assert response.headers["X-Request-ID"] == supplied


@pytest.mark.integration
class TestReadiness:
    """/ready — can this process actually serve traffic?

    Integration-marked: these assert against the real dependencies started by
    docker-compose.
    """

    async def test_reports_every_dependency(self, client: AsyncClient) -> None:
        body = (await client.get("/ready")).json()

        assert set(body["dependencies"]) == {
            "postgres",
            "redis",
            "opensearch",
            "minio",
            "mediamtx",
        }

    async def test_all_dependencies_healthy(self, client: AsyncClient) -> None:
        """With the full stack up, the platform reports ready."""
        response = await client.get("/ready")
        body = response.json()

        assert response.status_code == 200, body
        assert body["status"] == "ready", body["dependencies"]
        assert body["critical_failures"] == []

    async def test_postgis_and_timescale_are_installed(
        self, client: AsyncClient
    ) -> None:
        """The platform is useless without geospatial and time-series support.

        A plain Postgres would connect happily and then fail at the first
        radius query, so readiness verifies the extensions themselves.
        """
        body = (await client.get("/ready")).json()
        extensions = body["dependencies"]["postgres"]["extensions"]

        assert "postgis" in extensions
        assert "timescaledb" in extensions
        assert "pg_trgm" in extensions

    async def test_classifies_critical_versus_optional(
        self, client: AsyncClient
    ) -> None:
        """Losing search is survivable; losing the database is not."""
        deps = (await client.get("/ready")).json()["dependencies"]

        assert deps["postgres"]["critical"] is True
        assert deps["redis"]["critical"] is True
        # Search degrades to Postgres trigram; media and streaming are
        # non-fatal to the intelligence pipeline.
        assert deps["opensearch"]["critical"] is False
        assert deps["minio"]["critical"] is False
        assert deps["mediamtx"]["critical"] is False

    async def test_probe_is_bounded(self, client: AsyncClient) -> None:
        """Readiness answers quickly even if a dependency is slow."""
        body = (await client.get("/ready")).json()

        # Probes run concurrently with a 3s per-probe timeout, so the whole
        # check must not approach the sum of its parts.
        assert body["probe_duration_ms"] < 5000


class TestServiceBanner:
    """/ — the entrypoint a human hits first."""

    async def test_declares_the_architecture_model(self, client: AsyncClient) -> None:
        """The banner states which reference model this implements."""
        body = (await client.get("/")).json()

        assert "Model 5" in body["model"]
        assert body["docs"] == "/docs"


class TestOpenAPI:
    """The generated contract, which docs/API.md is built from."""

    async def test_schema_is_served(self, client: AsyncClient) -> None:
        response = await client.get("/openapi.json")

        assert response.status_code == 200
        schema = response.json()
        assert schema["info"]["title"] == "Sentinel-GJ"
        assert "/health" in schema["paths"]
        assert "/ready" in schema["paths"]
