"""Async database engine and session management.

SQLAlchemy 2.0 async throughout. The engine is created once at import and
disposed on application shutdown by the lifespan handler in ``app.main``.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("api.db")


class Base(DeclarativeBase):
    """Declarative base for every ORM model in the API tier."""


def _create_engine() -> AsyncEngine:
    """Build the async engine with a pool sized for the API tier."""
    return create_async_engine(
        settings.database_url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        # Recycle before typical infrastructure idle timeouts, and verify a
        # connection is alive before handing it out — a laptop that sleeps
        # mid-demo otherwise serves errors from a pool of dead sockets.
        pool_recycle=1800,
        pool_pre_ping=True,
        connect_args={
            "server_settings": {
                "application_name": "nagarnetra-api",
                "timezone": "UTC",
            },
            # asyncpg caches prepared statements per connection; PgBouncer in
            # transaction mode (production topology) cannot support that.
            "statement_cache_size": 0,
        },
    )


engine: AsyncEngine = _create_engine()

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # objects stay usable after commit, inside handlers
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a transactional session.

    Commits on success, rolls back on any exception, always closes.
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database() -> dict[str, Any]:
    """Probe the database for the readiness endpoint.

    Verifies connectivity *and* that the extensions the platform depends on
    are actually installed — a Postgres without PostGIS is reachable but
    useless to us, and readiness should say so.
    """
    from sqlalchemy import text

    async with SessionLocal() as session:
        version_row = await session.execute(text("SELECT version()"))
        extensions_row = await session.execute(
            text(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname IN ('postgis', 'timescaledb', 'pg_trgm')"
            )
        )
        found = dict(extensions_row.all())

    required = {"postgis", "timescaledb", "pg_trgm"}
    missing = sorted(required - set(found))

    return {
        "connected": True,
        "server_version": str(version_row.scalar_one()).split(" (")[0],
        "extensions": found,
        "missing_extensions": missing,
    }


async def dispose_engine() -> None:
    """Close every pooled connection on shutdown."""
    await engine.dispose()
    log.info("db.engine_disposed")
