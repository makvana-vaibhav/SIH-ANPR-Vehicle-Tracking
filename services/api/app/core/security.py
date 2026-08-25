"""Password hashing and JWT issuance/verification.

Pure cryptographic operations — no database, no network, no I/O. Anything that
needs to *store* something (revocation lists, login attempts) lives in
``app/services/``. That separation keeps this module trivially testable and
keeps the security-critical logic in one readable place.

Design decisions worth knowing:

* **argon2id**, not bcrypt. It is the OWASP first choice and resists GPU
  cracking far better. Parameters below follow OWASP's minimum guidance.
* **Access tokens are short (15 min) and refresh tokens long (7 d).** A stolen
  access token expires quickly; a stolen refresh token can be revoked, because
  every token carries a ``jti`` and logout adds it to a denylist.
* **Stream tokens are a separate token type** with a ~2 minute life, scoped to
  one camera. A viewing URL that leaks from a browser history is useless within
  minutes and cannot be replayed against a different camera.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from argon2.low_level import Type

from app.core.config import settings

TokenType = Literal["access", "refresh", "stream"]

# Issuer/audience are asserted on decode, so a token minted by another system
# (or for another audience) cannot be replayed against this API.
JWT_ISSUER = "sentinel-gj"
JWT_AUDIENCE = "sentinel-gj-api"

# OWASP-recommended argon2id parameters: 19 MiB memory, 2 iterations,
# 1 degree of parallelism. Memory cost is what defeats GPU attacks.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


# ── Passwords ─────────────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Hash a plaintext password with argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against its hash.

    Returns False rather than raising for every failure mode, so callers cannot
    accidentally distinguish "wrong password" from "corrupt hash" in a way that
    leaks through timing or error handling.
    """
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def needs_rehash(password_hash: str) -> bool:
    """True when a stored hash uses outdated parameters.

    Lets us raise argon2 cost over time and transparently upgrade each user's
    hash on their next successful login.
    """
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# ── Tokens ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TokenPayload:
    """Decoded, verified JWT claims."""

    subject: str
    token_type: TokenType
    jti: str
    issued_at: datetime
    expires_at: datetime
    username: str | None = None
    role: str | None = None
    department_id: str | None = None
    camera_id: str | None = None

    @property
    def user_id(self) -> uuid.UUID | None:
        """The subject as a UUID, when it is one (not for stream tokens)."""
        try:
            return uuid.UUID(self.subject)
        except ValueError:
            return None


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or not the type expected."""


def _encode(claims: dict[str, Any]) -> str:
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _base_claims(subject: str, token_type: TokenType, lifetime: timedelta) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "sub": subject,
        "type": token_type,
        # Unique id per token: what makes targeted revocation possible.
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }


def create_access_token(
    *,
    user_id: uuid.UUID | str,
    username: str,
    role: str,
    department_id: uuid.UUID | str | None = None,
) -> str:
    """Mint a short-lived access token carrying the caller's role.

    Role travels in the token so authorisation needs no database round trip on
    every request. The trade-off is that a role change takes effect at the next
    refresh (≤15 min) rather than instantly; deactivating a user is immediate,
    because that is checked against the database.
    """
    claims = _base_claims(
        str(user_id), "access", timedelta(minutes=settings.access_token_expire_minutes)
    )
    claims |= {
        "username": username,
        "role": role,
        "dept": str(department_id) if department_id else None,
    }
    return _encode(claims)


def create_refresh_token(*, user_id: uuid.UUID | str, username: str) -> str:
    """Mint a long-lived refresh token.

    Deliberately carries no role or department: it is an identity assertion
    only, exchanged for a fresh access token whose claims are re-read from the
    database. A user demoted at 09:00 cannot keep admin rights by refreshing.
    """
    claims = _base_claims(
        str(user_id), "refresh", timedelta(days=settings.refresh_token_expire_days)
    )
    claims |= {"username": username}
    return _encode(claims)


def create_stream_token(
    *, user_id: uuid.UUID | str, camera_id: uuid.UUID | str, username: str
) -> str:
    """Mint a viewing token scoped to a single camera.

    Short life and a camera scope mean a URL captured from browser history or a
    screen-share cannot be used to watch a different camera, or the same one an
    hour later.
    """
    claims = _base_claims(
        str(user_id), "stream", timedelta(seconds=settings.stream_token_expire_seconds)
    )
    claims |= {"camera_id": str(camera_id), "username": username}
    return _encode(claims)


def decode_token(token: str, *, expected_type: TokenType | None = None) -> TokenPayload:
    """Verify a token's signature, claims, and type.

    Args:
        token: The encoded JWT.
        expected_type: When given, the token's ``type`` claim must match. This
            is what stops a refresh token being presented as an access token,
            or a camera-scoped stream token being used to call the API.

    Raises:
        TokenError: signature invalid, expired, wrong issuer/audience, or wrong type.
    """
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub", "jti", "type"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token has expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenError("Token audience is not this API") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenError("Token was not issued by this platform") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"Invalid token: {exc}") from exc

    token_type = claims.get("type")
    if expected_type is not None and token_type != expected_type:
        raise TokenError(f"Expected a {expected_type} token, received a {token_type} token")

    return TokenPayload(
        subject=claims["sub"],
        token_type=token_type,
        jti=claims["jti"],
        issued_at=datetime.fromtimestamp(claims["iat"], tz=UTC),
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
        username=claims.get("username"),
        role=claims.get("role"),
        department_id=claims.get("dept"),
        camera_id=claims.get("camera_id"),
    )
