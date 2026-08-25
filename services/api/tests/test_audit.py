"""The audit trail.

CLAUDE.md commits to a specific, testable rule:

> Every plate search, every camera stream open, and every watchlist mutation
> writes an ``audit_log`` row.

These tests are the enforcement of that promise. They also cover the two
properties that make a trail trustworthy: it must never store credentials, and
it must record *failed* and *denied* attempts, not just successful ones.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.models.security import AuditLog
from app.services.audit import scrub

pytestmark = pytest.mark.integration


async def _rows(action: str, *, limit: int = 20) -> list[AuditLog]:
    """Fetch the most recent audit rows for an action."""
    async with SessionLocal() as session:
        result = await session.scalars(
            select(AuditLog)
            .where(AuditLog.action == action)
            .order_by(AuditLog.ts.desc())
            .limit(limit)
        )
        return list(result)


async def _count(action: str) -> int:
    async with SessionLocal() as session:
        rows = await session.scalars(select(AuditLog).where(AuditLog.action == action))
        return len(list(rows))


class TestScrubbing:
    """Credentials must never reach the table designed to be read by reviewers."""

    def test_redacts_passwords(self) -> None:
        assert scrub({"username": "admin", "password": "hunter2"}) == {
            "username": "admin",
            "password": "***redacted***",
        }

    def test_redacts_tokens_and_secrets(self) -> None:
        cleaned = scrub({"access_token": "ey...", "mfa_secret": "ABC", "api_key": "k-1"})
        assert set(cleaned.values()) == {"***redacted***"}

    def test_redaction_is_case_insensitive(self) -> None:
        assert scrub({"Password": "x"})["Password"] == "***redacted***"

    def test_redacts_nested_values(self) -> None:
        cleaned = scrub({"outer": {"inner": {"password": "x", "keep": "yes"}}})
        assert cleaned["outer"]["inner"]["password"] == "***redacted***"
        assert cleaned["outer"]["inner"]["keep"] == "yes"

    def test_truncates_oversized_values(self) -> None:
        """A bulk CSV upload should leave a record, not a copy of itself."""
        cleaned = scrub({"csv": "x" * 10_000})
        assert len(cleaned["csv"]) < 5000
        assert "truncated" in cleaned["csv"]

    def test_preserves_ordinary_values(self) -> None:
        assert scrub({"district": "Rajkot", "count": 250}) == {
            "district": "Rajkot",
            "count": 250,
        }


class TestAuthenticationIsAudited:
    async def test_successful_login_is_recorded(self, client: AsyncClient) -> None:
        before = await _count("auth.login")
        await client.post(
            "/api/v1/auth/login",
            json={"username": "analyst", "password": "Sentinel@2026"},
        )
        assert await _count("auth.login") == before + 1

    async def test_failed_login_is_recorded_with_its_reason(self, client: AsyncClient) -> None:
        """Repeated failures against a valid username are how credential
        stuffing appears in the trail."""
        before = await _count("auth.login_failed")
        await client.post(
            "/api/v1/auth/login",
            json={"username": "analyst", "password": "definitely-wrong"},
        )
        assert await _count("auth.login_failed") == before + 1

        latest = (await _rows("auth.login_failed", limit=1))[0]
        assert latest.username == "analyst"
        assert latest.result == "denied"
        assert latest.params["reason"] == "bad_password"

    async def test_unknown_user_attempt_is_recorded(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/auth/login",
            json={"username": "intruder", "password": "x"},
        )
        latest = (await _rows("auth.login_failed", limit=1))[0]
        assert latest.username == "intruder"
        assert latest.params["reason"] == "unknown_user"

    async def test_login_audit_never_stores_the_password(self, client: AsyncClient) -> None:
        """The single most important property of this table."""
        await client.post(
            "/api/v1/auth/login",
            json={"username": "analyst", "password": "Sentinel@2026"},
        )
        for row in await _rows("auth.login_failed") + await _rows("auth.login"):
            assert "Sentinel@2026" not in str(row.params)

    async def test_logout_is_recorded(self, client: AsyncClient, login) -> None:
        tokens = await login("operator")
        before = await _count("auth.logout")
        await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": tokens["refresh_token"]},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert await _count("auth.logout") == before + 1


class TestAuditRowContents:
    async def test_records_who_did_what(self, client: AsyncClient) -> None:
        """Identity is denormalised so the trail stays readable years later."""
        await client.post(
            "/api/v1/auth/login",
            json={"username": "supervisor", "password": "Sentinel@2026"},
        )
        latest = (await _rows("auth.login", limit=1))[0]

        assert latest.username == "supervisor"
        assert latest.role == "supervisor"
        assert latest.user_id is not None
        assert latest.ts is not None
        assert latest.ts.tzinfo is not None, "audit timestamps must be UTC-aware"

    async def test_records_the_caller_address(self, client: AsyncClient) -> None:
        """Answering 'who, from where' requires the client IP."""
        await client.post(
            "/api/v1/auth/login",
            json={"username": "analyst", "password": "Sentinel@2026"},
            headers={"X-Forwarded-For": "10.20.30.40, 172.28.0.5"},
        )
        latest = (await _rows("auth.login", limit=1))[0]
        # Left-most entry of the proxy chain is the real client.
        assert str(latest.ip) == "10.20.30.40"

    async def test_password_change_failure_is_audited(
        self, client: AsyncClient, auth_headers
    ) -> None:
        before = await _count("user.password_change")
        response = await client.post(
            "/api/v1/auth/password",
            json={"current_password": "wrong", "new_password": "a-long-new-password"},
            headers=await auth_headers("operator"),
        )
        assert response.status_code == 400
        assert await _count("user.password_change") == before + 1

        latest = (await _rows("user.password_change", limit=1))[0]
        assert latest.result == "denied"
        # And the attempted passwords are not in the trail.
        assert "wrong" not in str(latest.params)


class TestAuditDurability:
    async def test_audit_row_survives_a_failed_request(self, client: AsyncClient) -> None:
        """Audit writes use their own session.

        A denied action rolls back the request transaction; the evidence that
        it was attempted must survive that rollback.
        """
        before = await _count("auth.login_failed")
        await client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
        assert await _count("auth.login_failed") > before

    async def test_writer_tolerates_a_bad_row_without_failing_the_request(
        self,
    ) -> None:
        """Auditing must never become an availability risk for policing work.

        An oversized resource_id would violate the column width; the write is
        logged and dropped rather than propagating to the operator.
        """
        from app.services.audit import record

        # Must not raise.
        await record(
            action="test.oversized",
            resource_type="camera",
            resource_id="x" * 500,  # column is VARCHAR(128)
            username="tester",
        )

    @pytest.fixture(autouse=True)
    async def _cleanup(self):
        yield
        async with SessionLocal() as session:
            await session.execute(delete(AuditLog).where(AuditLog.action == "test.oversized"))
            await session.commit()


class TestAuditQueryability:
    async def test_indexed_lookup_by_user_and_time(self) -> None:
        """The audit viewer's primary query: one user, most recent first."""
        async with SessionLocal() as session:
            rows = await session.scalars(
                select(AuditLog)
                .where(AuditLog.username == "admin")
                .order_by(AuditLog.ts.desc())
                .limit(5)
            )
            found = list(rows)

        assert found, "expected audit history for admin"
        timestamps = [r.ts for r in found]
        assert timestamps == sorted(timestamps, reverse=True)

    async def test_unknown_user_id_is_allowed(self) -> None:
        """user_id is deliberately not a foreign key.

        The trail must remain readable after a user record is deleted; an FK
        would either block that deletion or null the evidence.
        """
        from app.services.audit import record

        orphan = uuid.uuid4()
        await record(action="test.orphan", user_id=orphan, username="ghost")

        rows = await _rows("test.orphan", limit=1)
        assert rows[0].user_id == orphan

        async with SessionLocal() as session:
            await session.execute(delete(AuditLog).where(AuditLog.action == "test.orphan"))
            await session.commit()
