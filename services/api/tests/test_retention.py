"""Retention and rate limiting: the two controls that were documented but absent.

Retention is tested against the real database because the whole point is that
it deletes. A test with a mocked session would prove the code calls
`drop_chunks` and prove nothing about whether Postgres accepts the call — which
is exactly where the first three attempts failed (an uninferable parameter
type, a poisoned transaction, and a column that does not exist).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text

from app.core.config import Settings, settings
from app.db.session import SessionLocal
from app.models.intelligence import Detection
from app.services import retention

pytestmark = pytest.mark.asyncio


class TestTheSweepRuns:
    async def test_a_sweep_completes_without_errors(self) -> None:
        """The regression that matters: three separate bugs made this fail."""
        result = await retention.sweep(dry_run=True)
        assert result.errors == [], f"sweep reported errors: {result.errors}"

    async def test_every_configured_hypertable_is_swept(self) -> None:
        result = await retention.sweep(dry_run=True)
        assert set(result.chunks_dropped) == set(retention.HYPERTABLES)

    async def test_a_dry_run_reports_without_deleting(self) -> None:
        async with SessionLocal() as session:
            before = (
                await session.execute(select(func.count()).select_from(Detection))
            ).scalar_one()

        result = await retention.sweep(dry_run=True)
        assert result.dry_run is True

        async with SessionLocal() as session:
            after = (
                await session.execute(select(func.count()).select_from(Detection))
            ).scalar_one()
        assert after == before

    async def test_one_failure_does_not_abort_the_rest(self) -> None:
        """A shared transaction meant the first failure silently ate the sweep.

        Postgres aborts the whole transaction on a failed statement, so the
        original per-table try/except isolated nothing — the sweep reported
        success having enforced almost none of the policy.
        """
        original = dict(retention.HYPERTABLES)
        retention.HYPERTABLES["no_such_table"] = "retention_detections_days"
        try:
            result = await retention.sweep(dry_run=True)
            assert any("no_such_table" in e for e in result.errors)
            # The real tables were still swept despite the bogus one failing.
            assert "detections" in result.chunks_dropped
            assert "camera_health" in result.chunks_dropped
        finally:
            retention.HYPERTABLES.clear()
            retention.HYPERTABLES.update(original)


class TestExpiryActuallyDeletes:
    async def test_a_chunk_older_than_the_cutoff_is_dropped(self) -> None:
        """Insert a detection well past retention and watch it go.

        `drop_chunks` removes whole partitions, so the row is written far
        enough back that its chunk contains nothing else.
        """
        ancient = datetime.now(UTC) - timedelta(days=settings.retention_detections_days + 400)
        detection_id = uuid.uuid4()

        async with SessionLocal() as session:
            session.add(
                Detection(
                    id=detection_id,
                    ts=ancient,
                    track_id="retention-test",
                    plate_normalised="GJ00TEST0000",
                )
            )
            await session.commit()

        async with SessionLocal() as session:
            present = (
                await session.execute(
                    select(func.count()).select_from(Detection).where(Detection.id == detection_id)
                )
            ).scalar_one()
        assert present == 1, "the fixture row was not written"

        result = await retention.sweep(dry_run=False)
        assert result.errors == []
        assert result.chunks_dropped["detections"] >= 1

        async with SessionLocal() as session:
            remaining = (
                await session.execute(
                    select(func.count()).select_from(Detection).where(Detection.id == detection_id)
                )
            ).scalar_one()
        assert remaining == 0, "a detection past its retention date survived the sweep"

    async def test_recent_detections_are_untouched(self) -> None:
        """The sweep must not take today's data with it.

        A specific row is planted and looked for by id rather than counting
        recent rows before and after: the AI worker writes detections
        continuously, so a count taken twice a second apart legitimately
        differs and the test would fail for a reason that has nothing to do
        with retention.
        """
        detection_id = uuid.uuid4()
        async with SessionLocal() as session:
            session.add(
                Detection(
                    id=detection_id,
                    ts=datetime.now(UTC),
                    track_id="retention-keep",
                    plate_normalised="GJ00KEEP0000",
                )
            )
            await session.commit()

        await retention.sweep(dry_run=False)

        async with SessionLocal() as session:
            survived = (
                await session.execute(
                    select(func.count()).select_from(Detection).where(Detection.id == detection_id)
                )
            ).scalar_one()
        assert survived == 1, "the sweep deleted a detection well inside its retention"


class TestPolicyShape:
    async def test_the_audit_log_outlives_the_data_it_describes(self) -> None:
        """An inquiry into misuse needs the record of who looked, not the data."""
        assert settings.retention_audit_days > settings.retention_detections_days
        assert settings.retention_audit_days > settings.retention_media_days

    async def test_media_expires_before_the_detections_referencing_it(self) -> None:
        assert settings.retention_media_days <= settings.retention_detections_days

    async def test_the_cutoff_is_in_the_past(self) -> None:
        assert retention.cutoff_for(30) < datetime.now(UTC)

    async def test_hypertables_named_here_really_are_hypertables(self) -> None:
        """A typo would make the sweep silently skip a table for ever."""
        async with SessionLocal() as session:
            names = set(
                (
                    await session.execute(
                        text("SELECT hypertable_name FROM timescaledb_information.hypertables")
                    )
                )
                .scalars()
                .all()
            )
        assert set(retention.HYPERTABLES).issubset(names)


class TestRateLimiting:
    async def test_ordinary_requests_carry_their_budget(self, client: AsyncClient):
        response = await client.get("/health")
        # /health is exempt — a throttled probe would restart the container.
        assert "x-ratelimit-limit" not in response.headers

        response = await client.get("/api/v1/watchlist")
        assert response.headers.get("x-ratelimit-limit") == str(settings.rate_limit_requests)

    async def test_authentication_is_limited_harder_than_reading(self):
        """Login is the endpoint worth guessing against.

        Asserted against the *declared* defaults rather than the live settings
        object, because the suite relaxes the live budget (see conftest) and
        this is a claim about the policy, not about the value a test happens to
        be running under.
        """
        fields = Settings.model_fields
        assert fields["rate_limit_auth_requests"].default < fields["rate_limit_requests"].default

    async def test_a_spent_budget_returns_429(self, client: AsyncClient):
        """The limit must actually bite, not merely be configured."""
        from app.core import ratelimit

        original = settings.rate_limit_requests
        settings.rate_limit_requests = 3
        try:
            codes = []
            for _ in range(6):
                response = await client.get("/api/v1/cameras/summary")
                codes.append(response.status_code)
            assert 429 in codes, f"the budget was never enforced: {codes}"

            refused = next(
                r for r in [await client.get("/api/v1/cameras/summary")] if r.status_code == 429
            )
            # A client that is being throttled must be told when to come back.
            assert refused.headers["retry-after"].isdigit()
            assert refused.headers["x-ratelimit-remaining"] == "0"
        finally:
            settings.rate_limit_requests = original
            # Leave no counter behind for the next test.
            client_redis = await ratelimit.RateLimitMiddleware(None)._redis()
            keys = await client_redis.keys("sentinel:ratelimit:*")
            if keys:
                await client_redis.delete(*keys)

    async def test_health_probes_are_never_limited(self):
        """A throttled healthcheck reports unhealthy and restarts the service."""
        from app.core.ratelimit import EXEMPT_PATHS

        assert "/health" in EXEMPT_PATHS
        assert "/ready" in EXEMPT_PATHS

    async def test_an_authenticated_caller_is_counted_as_themselves(self):
        """Otherwise a control-room NAT makes every operator share one budget."""
        from starlette.datastructures import Headers

        from app.core.ratelimit import client_key

        class FakeRequest:
            def __init__(self, user_id=None, forwarded=None):
                self.state = type("S", (), {"user_id": user_id})()
                self.headers = Headers({"x-forwarded-for": forwarded} if forwarded else {})
                self.client = type("C", (), {"host": "10.0.0.9"})()

        assert client_key(FakeRequest(user_id="abc")) == "user:abc"
        assert client_key(FakeRequest(forwarded="203.0.113.7")) == "ip:203.0.113.7"
        assert client_key(FakeRequest()) == "ip:10.0.0.9"

    async def test_the_first_forwarded_address_is_the_client(self):
        """X-Forwarded-For accumulates hops; the client is the leftmost."""
        from starlette.datastructures import Headers

        from app.core.ratelimit import client_key

        class FakeRequest:
            state = type("S", (), {"user_id": None})()
            headers = Headers({"x-forwarded-for": "203.0.113.7, 10.0.0.1, 10.0.0.2"})
            client = type("C", (), {"host": "10.0.0.9"})()

        assert client_key(FakeRequest()) == "ip:203.0.113.7"
