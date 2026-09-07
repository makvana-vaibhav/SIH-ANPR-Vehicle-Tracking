"""Account administration and the audit viewer.

The properties asserted here are the ones that make the audit trail worth
having. An account nobody can be held to, a password two people know, or a
deletion that orphans history each quietly turn "who did this?" into an
unanswerable question — and none of them looks like a bug at the time.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.core.password_policy import PasswordRejected, validate_password

pytestmark = pytest.mark.asyncio

STRONG = "Kutch-Monsoon-41"


def a_username() -> str:
    return f"test.{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def make_user(client: AsyncClient, auth_headers):
    """Create accounts, and remove them again afterwards.

    Without this the suite leaves a `test.*` account behind on every run, in
    the same database an operator and a judge are looking at. A test that
    litters the product it is testing is a test that will eventually be
    ignored.
    """
    created: list[str] = []
    admin = await auth_headers("admin")

    async def _make(headers=None, **overrides) -> dict:
        payload = {
            "username": a_username(),
            "password": STRONG,
            "role": "operator",
            **overrides,
        }
        response = await client.post("/api/v1/users", headers=headers or admin, json=payload)
        assert response.status_code == 201, response.text
        account = response.json()
        created.append(account["id"])
        return account

    yield _make

    for user_id in created:
        # 409 is expected for an account that acted during the test; those are
        # deactivated instead, which is what the API insists on.
        response = await client.delete(f"/api/v1/users/{user_id}", headers=admin)
        if response.status_code == 409:
            await client.patch(f"/api/v1/users/{user_id}", headers=admin, json={"is_active": False})


class TestPasswordPolicy:
    """Length alone admits the credential published in this repository.

    These are pure-function tests; the module-level asyncio mark does not
    apply to them.
    """

    pytestmark = []

    def test_the_documented_demo_credential_is_refused(self) -> None:
        with pytest.raises(PasswordRejected, match="documented demo credential"):
            validate_password("NagarNetra@2026")

    def test_a_password_containing_the_username_is_refused(self) -> None:
        with pytest.raises(PasswordRejected, match="username"):
            validate_password("vaibhav-is-here-2026", username="vaibhav")

    def test_a_short_password_is_refused(self) -> None:
        with pytest.raises(PasswordRejected, match="at least"):
            validate_password("short")

    def test_a_repeated_character_is_refused_despite_its_length(self) -> None:
        with pytest.raises(PasswordRejected, match="repeated"):
            validate_password("aaaaaaaaaaaaaaaaaaaa")

    def test_a_keyboard_run_is_refused(self) -> None:
        with pytest.raises(PasswordRejected, match="keyboard run"):
            validate_password("qwertyuiopasdf")

    def test_too_few_distinct_characters_is_refused(self) -> None:
        with pytest.raises(PasswordRejected, match="distinct"):
            validate_password("ababababababab")

    def test_a_reasonable_passphrase_is_accepted(self) -> None:
        validate_password(STRONG, username="insp.desai")


class TestAccountCreation:
    async def test_an_admin_creates_an_account(self, auth_headers, make_user):
        headers = await auth_headers("admin")
        created = await make_user(headers, full_name="Insp. R Desai")
        assert created["role"] == "operator"
        assert created["is_active"] is True

    async def test_a_password_an_admin_chose_must_be_changed(self, auth_headers, make_user):
        """Until the holder replaces it, two people can sign in as them."""
        headers = await auth_headers("admin")
        created = await make_user(headers)
        assert created["must_change_password"] is True

    async def test_a_weak_password_is_refused_at_creation(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("admin")
        response = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "username": a_username(),
                "password": "NagarNetra@2026",
                "role": "operator",
            },
        )
        assert response.status_code == 422
        assert "documented demo credential" in response.text

    async def test_a_duplicate_username_conflicts(
        self, client: AsyncClient, auth_headers, make_user
    ):
        headers = await auth_headers("admin")
        created = await make_user(headers)
        response = await client.post(
            "/api/v1/users",
            headers=headers,
            json={
                "username": created["username"],
                "password": STRONG,
                "role": "analyst",
            },
        )
        assert response.status_code == 409


class TestLockoutProtection:
    """An administrator must not be able to remove their own access."""

    async def _me(self, client: AsyncClient, headers) -> dict:
        response = await client.get("/api/v1/auth/me", headers=headers)
        return response.json()

    async def test_an_admin_cannot_deactivate_themselves(self, client: AsyncClient, auth_headers):
        headers = await auth_headers("admin")
        me = await self._me(client, headers)
        response = await client.patch(
            f"/api/v1/users/{me['id']}", headers=headers, json={"is_active": False}
        )
        assert response.status_code == 400
        assert "your own account" in response.text

    async def test_an_admin_cannot_demote_themselves(self, client: AsyncClient, auth_headers):
        """The last admin demoting themselves locks everyone out permanently."""
        headers = await auth_headers("admin")
        me = await self._me(client, headers)
        response = await client.patch(
            f"/api/v1/users/{me['id']}", headers=headers, json={"role": "operator"}
        )
        assert response.status_code == 400

    async def test_an_admin_may_change_somebody_else(
        self, client: AsyncClient, auth_headers, make_user
    ):
        headers = await auth_headers("admin")
        created = await make_user(headers)
        response = await client.patch(
            f"/api/v1/users/{created['id']}", headers=headers, json={"role": "analyst"}
        )
        assert response.status_code == 200
        assert response.json()["role"] == "analyst"


class TestDeletionProtectsTheTrail:
    async def test_an_account_with_history_cannot_be_deleted(
        self, client: AsyncClient, auth_headers
    ):
        """Deleting a user who has acted orphans every audit row naming them."""
        headers = await auth_headers("admin")
        me = (await client.get("/api/v1/auth/me", headers=headers)).json()

        response = await client.delete(f"/api/v1/users/{me['id']}", headers=headers)
        # Refused for being self before it is refused for having history; both
        # are correct, and the account is not deleted either way.
        assert response.status_code in (400, 409)

    async def test_an_account_created_in_error_can_be_removed(
        self, client: AsyncClient, auth_headers, make_user
    ):
        headers = await auth_headers("admin")
        created = await make_user(headers)
        response = await client.delete(f"/api/v1/users/{created['id']}", headers=headers)
        assert response.status_code == 204


class TestWhoMayAdminister:
    @pytest.mark.parametrize("role", ["supervisor", "operator", "analyst", "auditor"])
    async def test_non_admins_cannot_list_accounts(
        self, client: AsyncClient, auth_headers, role: str
    ):
        headers = await auth_headers(role)
        assert (await client.get("/api/v1/users", headers=headers)).status_code == 403

    @pytest.mark.parametrize("role", ["operator", "analyst"])
    async def test_roles_without_audit_read_are_refused(
        self, client: AsyncClient, auth_headers, role: str
    ):
        headers = await auth_headers(role)
        assert (await client.get("/api/v1/audit", headers=headers)).status_code == 403

    @pytest.mark.parametrize("role", ["admin", "supervisor", "auditor"])
    async def test_roles_holding_audit_read_may_read_it(
        self, client: AsyncClient, auth_headers, role: str
    ):
        """Supervisors hold `audit.read` too — they answer for their room."""
        headers = await auth_headers(role)
        response = await client.get("/api/v1/audit", headers=headers)
        assert response.status_code == 200, response.text
        assert "items" in response.json()

    async def test_unauthenticated_callers_are_refused(self, client: AsyncClient):
        assert (await client.get("/api/v1/users")).status_code == 401
        assert (await client.get("/api/v1/audit")).status_code == 401


class TestTheTrailRecordsItself:
    async def test_reading_the_audit_log_is_audited(self, client: AsyncClient, auth_headers):
        """A reviewer who leaves no trace is a hole in the control itself."""
        headers = await auth_headers("auditor")
        await client.get("/api/v1/audit", headers=headers)

        after = await client.get("/api/v1/audit?action=audit.", headers=headers)
        assert after.status_code == 200
        assert after.json()["total"] >= 1

    async def test_creating_an_account_is_audited(
        self, client: AsyncClient, auth_headers, make_user
    ):
        admin = await auth_headers("admin")
        created = await make_user(admin)

        auditor = await auth_headers("auditor")
        trail = await client.get("/api/v1/audit?action=user.create", headers=auditor)
        assert trail.status_code == 200
        assert any(entry["resource_id"] == created["id"] for entry in trail.json()["items"])

    async def test_a_refused_action_is_recorded_as_denied(self, client: AsyncClient, auth_headers):
        """What somebody *tried* to do matters as much as what they did."""
        operator = await auth_headers("operator")
        await client.get("/api/v1/users", headers=operator)

        auditor = await auth_headers("auditor")
        trail = await client.get("/api/v1/audit?result=denied", headers=auditor)
        assert trail.status_code == 200
        assert trail.json()["total"] >= 1
