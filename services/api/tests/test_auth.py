"""Authentication: login, token handling, refresh rotation, logout, profile."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    create_stream_token,
    decode_token,
    hash_password,
    verify_password,
)

pytestmark = pytest.mark.integration

VALID_USER = "admin"
VALID_PASSWORD = "Sentinel@2026"


class TestPasswordHashing:
    """Unit-level: no database involved."""

    def test_hash_verifies(self) -> None:
        h = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", h)

    def test_wrong_password_rejected(self) -> None:
        h = hash_password("correct horse battery staple")
        assert not verify_password("Correct Horse Battery Staple", h)

    def test_hash_is_salted(self) -> None:
        """Identical passwords must not produce identical hashes."""
        assert hash_password("same") != hash_password("same")

    def test_uses_argon2id(self) -> None:
        assert hash_password("x").startswith("$argon2id$")

    def test_malformed_hash_is_rejected_not_raised(self) -> None:
        """A corrupt stored hash must deny access, not crash the login path."""
        assert not verify_password("anything", "not-a-hash")


class TestTokenIntegrity:
    """Unit-level token behaviour."""

    def test_access_token_roundtrip(self) -> None:
        token = create_access_token(
            user_id="11111111-1111-1111-1111-111111111111",
            username="tester",
            role="operator",
        )
        payload = decode_token(token, expected_type="access")
        assert payload.username == "tester"
        assert payload.role == "operator"
        assert payload.token_type == "access"

    def test_refresh_token_carries_no_role(self) -> None:
        """Roles are re-read from the database at refresh, never carried over."""
        token = create_refresh_token(
            user_id="11111111-1111-1111-1111-111111111111", username="tester"
        )
        assert decode_token(token, expected_type="refresh").role is None

    def test_refresh_token_rejected_as_access_token(self) -> None:
        """Type confusion is the classic JWT mistake; it must fail loudly."""
        token = create_refresh_token(user_id="1" * 32, username="tester")
        with pytest.raises(TokenError, match="Expected a access token"):
            decode_token(token, expected_type="access")

    def test_stream_token_rejected_as_access_token(self) -> None:
        """A camera viewing token must never authorise API calls."""
        token = create_stream_token(user_id="1" * 32, camera_id="2" * 32, username="tester")
        with pytest.raises(TokenError):
            decode_token(token, expected_type="access")

    def test_stream_token_is_scoped_to_one_camera(self) -> None:
        token = create_stream_token(
            user_id="11111111-1111-1111-1111-111111111111",
            camera_id="22222222-2222-2222-2222-222222222222",
            username="tester",
        )
        payload = decode_token(token, expected_type="stream")
        assert payload.camera_id == "22222222-2222-2222-2222-222222222222"

    def test_tampered_token_rejected(self) -> None:
        token = create_access_token(user_id="1" * 32, username="t", role="analyst")
        # Flip a character in the payload segment.
        head, body, sig = token.split(".")
        tampered = f"{head}.{body[:-2]}XY.{sig}"
        with pytest.raises(TokenError):
            decode_token(tampered)

    def test_garbage_rejected(self) -> None:
        with pytest.raises(TokenError):
            decode_token("not.a.token")

    def test_each_token_has_a_unique_jti(self) -> None:
        """Unique ids are what make targeted revocation possible."""
        a = decode_token(create_refresh_token(user_id="1" * 32, username="t"))
        b = decode_token(create_refresh_token(user_id="1" * 32, username="t"))
        assert a.jti != b.jti


class TestLogin:
    async def test_valid_credentials_return_a_token_pair(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": VALID_USER, "password": VALID_PASSWORD},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 900  # 15 minutes
        assert decode_token(body["access_token"], expected_type="access")
        assert decode_token(body["refresh_token"], expected_type="refresh")

    async def test_wrong_password_is_401(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": VALID_USER, "password": "wrong"},
        )
        assert response.status_code == 401

    async def test_unknown_user_is_401(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "whatever"},
        )
        assert response.status_code == 401

    async def test_error_does_not_reveal_which_half_was_wrong(self, client: AsyncClient) -> None:
        """Distinct messages would let an attacker enumerate valid usernames."""
        unknown = await client.post(
            "/api/v1/auth/login", json={"username": "nobody", "password": "x"}
        )
        bad_password = await client.post(
            "/api/v1/auth/login", json={"username": VALID_USER, "password": "x"}
        )
        assert unknown.json()["detail"] == bad_password.json()["detail"]

    async def test_rejects_malformed_payload(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/auth/login", json={"username": ""})
        assert response.status_code == 422


class TestProtectedEndpoints:
    async def test_me_requires_a_token(self, client: AsyncClient) -> None:
        assert (await client.get("/api/v1/auth/me")).status_code == 401

    async def test_me_rejects_garbage_token(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer nonsense"})
        assert response.status_code == 401

    async def test_me_rejects_a_refresh_token(self, client: AsyncClient, login) -> None:
        """Presenting a refresh token as a bearer credential must fail."""
        tokens = await login("admin")
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
        )
        assert response.status_code == 401

    async def test_me_returns_profile_and_permissions(
        self, client: AsyncClient, auth_headers
    ) -> None:
        response = await client.get("/api/v1/auth/me", headers=await auth_headers("operator"))

        assert response.status_code == 200
        body = response.json()
        assert body["username"] == "operator"
        assert body["role"] == "operator"
        # The UI uses this to hide actions the user cannot perform.
        assert "camera.read" in body["permissions"]
        assert "camera.delete" not in body["permissions"]

    async def test_401_carries_www_authenticate_header(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/auth/me")
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    @pytest.mark.parametrize(
        "role", ["admin", "supervisor", "operator", "analyst", "auditor", "api_client"]
    )
    async def test_every_seeded_role_can_authenticate(
        self, client: AsyncClient, auth_headers, role: str
    ) -> None:
        """All six roles exist and can sign in — the demo depends on it."""
        response = await client.get("/api/v1/auth/me", headers=await auth_headers(role))
        assert response.status_code == 200
        assert response.json()["role"] == role


class TestRefreshAndLogout:
    async def test_refresh_issues_a_new_pair(self, client: AsyncClient, login) -> None:
        tokens = await login("admin")
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )

        assert response.status_code == 200
        assert decode_token(response.json()["access_token"], expected_type="access")

    async def test_refresh_rotates_the_token(self, client: AsyncClient, login) -> None:
        """A refresh token is single-use: reusing it must fail.

        This is what makes theft detectable — the legitimate holder and the
        thief cannot both keep refreshing.
        """
        tokens = await login("admin")
        first = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert first.status_code == 200

        replay = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert replay.status_code == 401

    async def test_refresh_rejects_an_access_token(self, client: AsyncClient, login) -> None:
        tokens = await login("admin")
        response = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["access_token"]}
        )
        assert response.status_code == 401

    async def test_logout_revokes_the_refresh_token(self, client: AsyncClient, login) -> None:
        tokens = await login("operator")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        logout = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": tokens["refresh_token"]},
            headers=headers,
        )
        assert logout.status_code == 200

        # The session cannot be renewed after signing out.
        after = await client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert after.status_code == 401
