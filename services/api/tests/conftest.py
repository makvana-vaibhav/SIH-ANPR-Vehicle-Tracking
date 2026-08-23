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


@pytest.fixture(autouse=True)
async def _dispose_engine_between_tests() -> AsyncGenerator[None, None]:
    """Return the connection pool to a clean state after every test.

    The engine is a module-level singleton created at import time, while
    pytest-asyncio runs each test in its own event loop. Without this, pooled
    asyncpg connections outlive the loop that created them and the next test
    fails with "attached to a different loop".

    This is a test-harness concern only: in production the API runs in a single
    long-lived loop, which is exactly what the pool is designed for.
    """
    yield
    await engine.dispose()


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
