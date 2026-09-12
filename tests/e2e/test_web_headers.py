"""The web tier's security headers.

These exist because they silently vanished once already: in nginx, an
``add_header`` inside a ``location`` block cancels inheritance of *every*
parent ``add_header``. A single ``Cache-Control`` line in ``location /``
removed CSP, X-Frame-Options, X-Content-Type-Options and Referrer-Policy from
the entire application, and nothing failed — the site worked perfectly, just
without any of its declared protections.

Run against a live stack:  pytest tests/e2e/test_web_headers.py
"""

from __future__ import annotations

import os

import httpx
import pytest

WEB_URL = os.environ.get("WEB_URL", "http://localhost:8080")

REQUIRED_HEADERS = {
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
}


@pytest.fixture(scope="module")
def index_response() -> httpx.Response:
    try:
        return httpx.get(WEB_URL, timeout=10, follow_redirects=True)
    except httpx.HTTPError as exc:
        pytest.skip(f"web tier not reachable at {WEB_URL}: {exc}")


@pytest.fixture(scope="module")
def asset_url(index_response: httpx.Response) -> str:
    import re

    match = re.search(r'/assets/index-[^"]+\.js', index_response.text)
    if match is None:
        pytest.skip("no hashed asset found in index.html")
    return f"{WEB_URL}{match.group(0)}"


class TestApplicationRoute:
    def test_serves_the_spa(self, index_response: httpx.Response) -> None:
        assert index_response.status_code == 200
        assert "Contrail" in index_response.text

    @pytest.mark.parametrize("header", sorted(REQUIRED_HEADERS))
    def test_security_header_present(
        self, index_response: httpx.Response, header: str
    ) -> None:
        """Every declared header must actually reach the browser."""
        assert header in {k.lower() for k in index_response.headers}, (
            f"{header} is missing. Check for an add_header inside a location "
            "block — it cancels inheritance of all parent add_header directives."
        )

    def test_index_is_not_cached(self, index_response: httpx.Response) -> None:
        """A cached index.html leaves operators on a stale bundle after deploy."""
        assert "no-cache" in index_response.headers.get("cache-control", "")


class TestHashedAssets:
    @pytest.mark.parametrize("header", sorted(REQUIRED_HEADERS))
    def test_security_header_present(self, asset_url: str, header: str) -> None:
        response = httpx.head(asset_url, timeout=10)
        assert header in {k.lower() for k in response.headers}

    def test_assets_are_cached_hard(self, asset_url: str) -> None:
        """Content-hashed filenames make long caching safe."""
        response = httpx.head(asset_url, timeout=10)
        assert "max-age=" in response.headers.get("cache-control", "")


class TestContentSecurityPolicy:
    def test_permits_the_satellite_imagery_host(
        self, index_response: httpx.Response
    ) -> None:
        """The map's satellite basemap loads tiles from Esri."""
        csp = index_response.headers["content-security-policy"]
        assert "arcgisonline.com" in csp

    def test_permits_the_stream_gateway(self, index_response: httpx.Response) -> None:
        """WHEP negotiation posts to MediaMTX from the browser."""
        csp = index_response.headers["content-security-policy"]
        assert "localhost:8889" in csp

    def test_default_src_is_self(self, index_response: httpx.Response) -> None:
        csp = index_response.headers["content-security-policy"]
        assert "default-src 'self'" in csp

    def test_does_not_allow_arbitrary_remote_script(
        self, index_response: httpx.Response
    ) -> None:
        """Imagery is an image source, never a script source."""
        csp = index_response.headers["content-security-policy"]
        script_src = next(
            (d for d in csp.split(";") if d.strip().startswith("script-src")), ""
        )
        assert "arcgisonline" not in script_src
        assert "https://*" not in script_src


class TestOfflineBasemapAsset:
    def test_district_geojson_is_served_locally(self) -> None:
        """The offline basemap must come from our own origin, not a CDN."""
        # Guarded like every other test here: an unreachable web tier is a
        # missing precondition, not a failing assertion. Without this the test
        # passes from the host and fails inside a container, which says
        # nothing about the basemap.
        try:
            response = httpx.get(f"{WEB_URL}/data/gujarat_districts.geojson", timeout=15)
        except httpx.HTTPError as exc:
            pytest.skip(f"web tier not reachable at {WEB_URL}: {exc}")

        assert response.status_code == 200
        body = response.json()
        assert body["type"] == "FeatureCollection"
        assert len(body["features"]) == 33
