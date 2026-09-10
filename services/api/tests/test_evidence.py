"""Signed URLs for evidence crops.

The thing worth pinning here is the host. SigV4 includes `host` in the signed
headers, so a URL signed against the internal `minio:9000` and then rewritten to
`localhost:9000` for the browser fails its own signature check — silently, as a
403 that looks like a missing image. The first version of this service did
exactly that. These tests hold the corrected behaviour: sign against the address
the browser will actually request.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.core.config import settings
from app.services import evidence


class TestCropUrl:
    def test_no_key_means_no_url(self) -> None:
        """A detection without a crop is normal, not an error."""
        assert evidence.crop_url(None) is None
        assert evidence.crop_url("") is None

    def test_a_key_produces_a_signed_url(self) -> None:
        url = evidence.crop_url("crops/plates/2026/09/09/track_0001_AB12CDE_0.91.jpg")

        assert url is not None
        query = parse_qs(urlparse(url).query)
        # SigV4 query-string auth: these four are what make the URL usable
        # without an Authorization header, which an <img> cannot send.
        for parameter in (
            "X-Amz-Algorithm",
            "X-Amz-Credential",
            "X-Amz-Signature",
            "X-Amz-Expires",
        ):
            assert parameter in query, f"{parameter} missing from {url}"

    def test_it_is_signed_for_the_browser_facing_host(self) -> None:
        """The bug this file exists for.

        Signing against the internal endpoint and swapping the host afterwards
        invalidates the signature, because SigV4 covers `host`.
        """
        url = evidence.crop_url("crops/plates/x.jpg")

        assert url is not None
        assert url.startswith(settings.minio_public_endpoint), f"signed for the wrong host: {url}"

    def test_the_key_and_bucket_survive_into_the_path(self) -> None:
        key = "crops/plates/2026/09/09/track_0007_GJ01AB1234_0.88.jpg"
        url = evidence.crop_url(key)

        assert url is not None
        path = urlparse(url).path
        assert settings.minio_bucket in path
        assert "track_0007_GJ01AB1234_0.88.jpg" in path

    def test_the_grant_is_short_lived(self) -> None:
        """A crop is a photograph of a specific vehicle at a specific place and
        time. A link that still works tomorrow is one that can be forwarded."""
        url = evidence.crop_url("crops/plates/x.jpg")

        assert url is not None
        expires = int(parse_qs(urlparse(url).query)["X-Amz-Expires"][0])
        assert 0 < expires <= 900, f"crop URLs should expire quickly, got {expires}s"
        assert expires == evidence.URL_TTL_SECONDS

    def test_signing_does_no_network_io(self) -> None:
        """Signing must stay cheap enough to do per row.

        A page of alerts signs one URL each; if this reached object storage,
        rendering a hundred alerts would be a hundred round trips. Pointing the
        client at an endpoint that cannot resolve proves nothing is dialled.
        """
        evidence._client.cache_clear()
        original = settings.minio_public_endpoint
        try:
            settings.minio_public_endpoint = "http://this-host-does-not-exist.invalid:9000"
            url = evidence.crop_url("crops/plates/x.jpg")
            assert url is not None
            assert url.startswith("http://this-host-does-not-exist.invalid:9000")
        finally:
            settings.minio_public_endpoint = original
            evidence._client.cache_clear()
