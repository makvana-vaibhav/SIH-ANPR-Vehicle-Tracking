"""Shared pytest fixtures for the Sentinel-GJ API test suite.

Tests run inside the api container (``make test``), so the live infrastructure
from docker-compose is reachable. Tests that require it are marked
``@pytest.mark.integration``; the rest exercise the application in-process
through an ASGI transport with no network involved.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.db.session import engine
from app.main import app
from app.services import token_store


@pytest.fixture(autouse=True)
async def _reset_pooled_clients() -> AsyncGenerator[None, None]:
    """Return pooled connections to a clean state after every test.

    Both the SQLAlchemy engine and the Redis client are module-level singletons
    created on first use, while pytest-asyncio runs each test in its own event
    loop. Without this, connections outlive the loop that created them and the
    next test fails with "attached to a different loop" / "Event loop is closed".

    This is a test-harness concern only: in production both run inside a single
    long-lived loop, which is exactly what connection pools are designed for.
    """
    yield
    await engine.dispose()
    await token_store.close()


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client bound directly to the ASGI app.

    Uses ASGITransport rather than a live server: the tests exercise real
    routing, dependencies and middleware, but do not need a port to be open.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://sentinel.test",
    ) as async_client:
        yield async_client


@pytest.fixture
def settings() -> Settings:
    """The process-wide settings object."""
    return get_settings()


# ── Authentication helpers ────────────────────────────────────────────
# The seeded demo accounts (scripts/seed.py) give one user per role, which is
# exactly what the RBAC matrix tests need.

DEMO_PASSWORD = "Sentinel@2026"

DEMO_ACCOUNTS = {
    "admin": "admin",
    "supervisor": "supervisor",
    "operator": "operator",
    "analyst": "analyst",
    "auditor": "auditor",
    "api_client": "gsrtc-integration",
}


@pytest.fixture
async def login(client: AsyncClient):
    """Return a callable that logs in as a role and yields its token pair."""

    async def _login(role: str = "admin") -> dict[str, str]:
        username = DEMO_ACCOUNTS[role]
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": DEMO_PASSWORD},
        )
        assert response.status_code == 200, (
            f"login as {username} failed: {response.status_code} {response.text}. "
            "Has `make seed` been run?"
        )
        return response.json()

    return _login


@pytest.fixture
async def auth_headers(login):
    """Return a callable producing Authorization headers for a role."""

    async def _headers(role: str = "admin") -> dict[str, str]:
        tokens = await login(role)
        return {"Authorization": f"Bearer {tokens['access_token']}"}

    return _headers
