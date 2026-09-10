"""Turns an evidence-crop object key into something a browser can display.

The worker uploads a plate crop straight from the edge and records only its
object key on the detection — the bytes never touch the event bus, because a
15 KB JPEG base64-encoded would multiply event traffic tenfold and "the central
tier carries events, not video" would stop being true. See
`ai_worker/crops.py`. This is the read side.

## Why a presigned URL, in the payload

Three constraints meet here and only one design satisfies all of them.

An `<img>` tag **cannot send an Authorization header**, so a protected endpoint
would need the credential in the URL — the same problem the stream gateway
solves with a camera-scoped token in the path.

Proxying the bytes through the API would route every crop through the one tier
that has to stay responsive for operators.

And checking that each object exists before returning a link would mean a
network round trip per row: a hundred of them to render one page of alerts.

So the URL is **presigned and returned inline**. Presigning is pure local
signature computation — no request is made to object storage — so signing a
page of alerts costs microseconds. The signature is itself the capability, so
no separate auth is needed, and it expires quickly because a crop is a
photograph of a specific vehicle at a specific place and time; a link that keeps
working after the operator closes the screen is one that can be forwarded and
replayed.

## Why nothing checks that the object is there

The worker returns the key synchronously and uploads on a background thread, so
for a second after a detection the key names an object that does not exist yet.
The upload can also fail outright.

Rather than pay a round trip to find out, the URL is signed regardless and the
browser discovers the truth: object storage answers 404 and the UI hides the
image. A crop is evidence *about* a sighting, not the sighting itself, and a
missing photograph must never cost the alert.
"""

from __future__ import annotations

import functools
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError
from botocore.session import get_session

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("api.evidence")

#: How long a signed crop URL stays valid. Long enough to render a screen and
#: look at it; short enough that a copied link is stale before it travels.
URL_TTL_SECONDS = 300

_CONFIG = Config(signature_version="s3v4", retries={"max_attempts": 1})


@functools.lru_cache(maxsize=1)
def _client() -> Any:
    """A botocore S3 client, built once.

    botocore rather than aioboto3 because generating a presigned URL performs no
    I/O at all — it is an HMAC over the request — so there is nothing here for
    an async client to await, and a per-call async context manager would cost
    more than the signing does.

    Pointed at the **public** endpoint, which is the one subtlety worth knowing.
    SigV4 includes `host` in the signed headers, so a URL signed against the
    internal `minio:9000` and then rewritten to `localhost:9000` for the browser
    fails the signature check. Signing against the address the browser will
    actually request is the only version that works; the client never connects,
    so an endpoint this process cannot reach is fine.
    """
    return get_session().create_client(
        "s3",
        endpoint_url=settings.minio_public_endpoint or settings.minio_url,
        aws_access_key_id=settings.minio_root_user,
        aws_secret_access_key=settings.minio_root_password,
        config=_CONFIG,
    )


def crop_url(key: str | None) -> str | None:
    """A short-lived URL for one crop, or None when there is no key."""
    if not key:
        return None

    try:
        url: str = _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.minio_bucket, "Key": key},
            ExpiresIn=URL_TTL_SECONDS,
        )
    except (BotoCoreError, ValueError) as exc:
        # Misconfiguration rather than a missing object — signing itself does
        # not touch the network, so a failure here means credentials or an
        # endpoint the client cannot parse.
        log.warning("evidence.presign_failed", key=key, error=str(exc))
        return None

    return url
