"""The federated grid's media proxy.

The grid's credentials must never reach the browser, and a viewing token must
authorise exactly one camera. Both are easy to get wrong in a proxy, and both
fail silently: a leaked credential works, and a token that authorises
everything looks identical to one that authorises the right thing.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.security import create_stream_token
from app.db.session import SessionLocal
from app.models.registry import Camera

pytestmark = pytest.mark.asyncio


async def grid_camera() -> Camera | None:
    async with SessionLocal() as session:
        return (
            await session.execute(
                select(Camera).where(Camera.camera_code.startswith("SBX-")).limit(1)
            )
        ).scalar_one_or_none()


class TestTokenScope:
    async def test_a_token_for_one_camera_does_not_open_another(self, client: AsyncClient):
        """Camera scope is the entire point of a stream token."""
        async with SessionLocal() as session:
            cameras = (
                (
                    await session.execute(
                        select(Camera).where(Camera.camera_code.startswith("SBX-")).limit(2)
                    )
                )
                .scalars()
                .all()
            )
        if len(cameras) < 2:
            pytest.skip("needs two grid cameras in the registry")

        token = create_stream_token(
            user_id=uuid.uuid4(), camera_id=cameras[0].id, username="tester"
        )
        response = await client.get(f"/api/v1/grid/{cameras[1].id}/{token}/index.m3u8")
        assert response.status_code == 403

    async def test_an_invalid_token_is_refused(self, client: AsyncClient):
        camera = await grid_camera()
        if camera is None:
            pytest.skip("needs a grid camera")
        response = await client.get(f"/api/v1/grid/{camera.id}/not-a-token/index.m3u8")
        assert response.status_code == 403

    async def test_an_access_token_is_not_a_stream_token(self, client: AsyncClient, login):
        """Token *type* is checked, not merely the signature."""
        camera = await grid_camera()
        if camera is None:
            pytest.skip("needs a grid camera")
        tokens = await login("admin")
        response = await client.get(f"/api/v1/grid/{camera.id}/{tokens['access_token']}/index.m3u8")
        assert response.status_code == 403


class TestPathRestriction:
    async def test_only_media_paths_are_proxied(self, client: AsyncClient):
        """This endpoint serves one grid's media, not arbitrary fetches."""
        camera = await grid_camera()
        if camera is None:
            pytest.skip("needs a grid camera")
        token = create_stream_token(user_id=uuid.uuid4(), camera_id=camera.id, username="tester")
        for path in ("cameras.json", "auth/login", "index.html"):
            response = await client.get(f"/api/v1/grid/{camera.id}/{token}/{path}")
            assert response.status_code == 400, f"{path} should not be proxied"

    async def test_traversal_is_refused(self, client: AsyncClient):
        camera = await grid_camera()
        if camera is None:
            pytest.skip("needs a grid camera")
        token = create_stream_token(user_id=uuid.uuid4(), camera_id=camera.id, username="tester")
        response = await client.get(f"/api/v1/grid/{camera.id}/{token}/..%2F..%2Fenc.key")
        assert response.status_code in (400, 404)


class TestCredentialsStayServerSide:
    async def test_the_grant_does_not_hand_the_browser_grid_credentials(
        self, client: AsyncClient, auth_headers
    ):
        """The whole reason the proxy exists.

        The RTSP URL carries a username and password. It must not appear in a
        response the browser receives.
        """
        from app.core.config import settings

        camera = await grid_camera()
        if camera is None:
            pytest.skip("needs a grid camera")
        headers = await auth_headers("admin")
        response = await client.get(f"/api/v1/cameras/{camera.id}/stream", headers=headers)
        assert response.status_code == 200

        body = response.text
        if settings.sandbox_rtsp_password:
            assert settings.sandbox_rtsp_password not in body

    async def test_the_hls_url_points_at_us_not_at_the_grid(
        self, client: AsyncClient, auth_headers
    ):
        camera = await grid_camera()
        if camera is None:
            pytest.skip("needs a grid camera")
        headers = await auth_headers("admin")
        grant = (await client.get(f"/api/v1/cameras/{camera.id}/stream", headers=headers)).json()
        assert grant["hls_url"].startswith("/api/v1/grid/"), (
            "a federated camera's HLS must come through our proxy — the browser "
            "has no session with the grid"
        )
