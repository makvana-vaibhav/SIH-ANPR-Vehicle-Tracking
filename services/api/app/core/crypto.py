"""AES-256-GCM envelope encryption for values held at rest.

## What this is for, and what it is not

`docs/SECURITY.md` records encryption at rest as a gap with two halves. The
half that belongs to the *deployment* — an encrypted Postgres volume, MinIO
server-side encryption — is not something application code can provide, and
this module does not pretend to. The half that belongs to the *application* is
a value that must survive in a row or a file while remaining unreadable to
anyone who obtains that row or file without also obtaining the key. That is
what this module does.

The first caller is `app.services.secrets`, which gains an ``enc://`` pointer
scheme. The existing pointer discipline stands: `credentials_ref` still holds a
*reference*, never a plaintext credential. ``enc://`` adds the case where there
is no Vault to point at — an air-gapped pilot, or the single-node deployment
this project targets — and the alternative to a ciphertext in the column would
otherwise be a plaintext one.

## The construction

**AES-256-GCM**, which is authenticated encryption: decryption either returns
the exact bytes that were sealed or raises. There is no mode in which it
returns attacker-chosen plaintext, which is the failure that unauthenticated
CBC invites and the reason it is not offered here.

* **256-bit keys.** Rejected at load time if they are any other length, because
  a 16-byte key silently gives AES-128 and the name of this module would then
  be a lie.
* **96-bit nonce, fresh from `os.urandom` per message.** 96 bits is the size
  GCM is specified for; anything else forces an extra derivation step. Nonce
  reuse under one key is catastrophic for GCM — it leaks the XOR of two
  plaintexts and, worse, the authentication subkey — so a nonce is generated
  per call and never derived from the plaintext or a counter.
* **Associated data is mandatory.** The caller must name the context a
  ciphertext belongs to (``"vms-credentials"``, say). The AAD is authenticated
  but not encrypted, so a ciphertext lifted out of one column and pasted into
  another fails to decrypt rather than silently decrypting into the wrong
  meaning. Ciphertext-relocation is the attack that authenticated encryption
  alone does not stop, and the AAD is what stops it.

## Key rotation

A token names the key that sealed it, so more than one key can be live at once:

    ENCRYPTION_KEYS="2025a:<base64>,2025b:<base64>"
    ENCRYPTION_ACTIVE_KEY_ID="2025b"

Everything new is sealed with the active key; anything sealed with a retired
key still opens for as long as that key stays in the ring. Rotation is
therefore: add the new key, point the active id at it, re-encrypt at leisure,
then drop the old key. Without the key id in the token, rotation would mean
trial decryption against every key, which turns a wrong key from an error into
a timing signal.

## Token format

    v1.<key_id>.<base64url nonce>.<base64url ciphertext||tag>

Versioned so the construction can change without the stored values becoming
ambiguous, and base64url without padding so a token is safe in a URL, a header
or a CSV cell without further escaping.

## Absent configuration

With no keys configured, `is_enabled()` is False and `encrypt` raises. It does
not fall back to storing plaintext. A caller that can operate without
encryption checks `is_enabled()` first and says so in its log; a caller that
cannot must fail. Silently degrading to plaintext under a function named
`encrypt` is precisely the failure this module exists to prevent.
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("api.crypto")

#: Only version this module writes. Present in every token so the construction
#: can be changed later without stored values becoming ambiguous.
TOKEN_VERSION = "v1"  # noqa: S105 - a format version, not a credential

#: AES-256. Enforced at key load rather than assumed.
KEY_BYTES = 32

#: GCM's specified nonce size. Fresh per message, never reused under a key.
NONCE_BYTES = 12

#: Field separator. Not present in base64url output, so a token always splits
#: into exactly four parts.
SEPARATOR = "."


class EncryptionError(RuntimeError):
    """Encryption or decryption could not be completed."""


class EncryptionNotConfigured(EncryptionError):
    """No key ring is configured, so nothing can be sealed."""


class DecryptionError(EncryptionError):
    """A token failed to open: wrong key, wrong context, or tampering."""


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (binascii.Error, ValueError) as exc:
        raise DecryptionError("token component is not valid base64url") from exc


@dataclass(frozen=True, slots=True)
class KeyRing:
    """The keys this process may use, and which one seals new values."""

    keys: dict[str, bytes]
    active_key_id: str

    def active(self) -> bytes:
        return self.keys[self.active_key_id]

    def get(self, key_id: str) -> bytes:
        try:
            return self.keys[key_id]
        except KeyError:
            # Named rather than silently tried against every key: trial
            # decryption turns a retired key into a timing oracle.
            raise DecryptionError(
                f"token was sealed with key {key_id!r}, which is not in this ring"
            ) from None

    def __repr__(self) -> str:
        # Key material must never reach a log line or a traceback.
        return f"<KeyRing ids={sorted(self.keys)!r} active={self.active_key_id!r}>"


def _parse_key_ring(spec: str, active_key_id: str) -> KeyRing | None:
    """Parse ``id:base64,id:base64`` into a validated ring, or None if empty."""
    if not spec.strip():
        return None

    keys: dict[str, bytes] = {}
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        key_id, separator, encoded = entry.partition(":")
        key_id = key_id.strip()
        if not separator or not key_id:
            raise EncryptionError(
                "ENCRYPTION_KEYS entries must be 'key_id:base64_key', got: " f"{entry!r}"
            )
        if SEPARATOR in key_id:
            # Would make a token ambiguous to split.
            raise EncryptionError(f"key id may not contain {SEPARATOR!r}: {key_id!r}")

        try:
            raw = base64.b64decode(encoded.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise EncryptionError(f"key {key_id!r} is not valid base64") from exc

        if len(raw) != KEY_BYTES:
            # A 16-byte key would quietly give AES-128 under a module named for
            # AES-256, so this is refused rather than accepted.
            raise EncryptionError(
                f"key {key_id!r} is {len(raw)} bytes; AES-256 requires {KEY_BYTES}"
            )
        keys[key_id] = raw

    if not keys:
        return None

    active = active_key_id.strip()
    if not active:
        if len(keys) > 1:
            raise EncryptionError(
                "ENCRYPTION_ACTIVE_KEY_ID must name a key when more than one is configured"
            )
        active = next(iter(keys))
    if active not in keys:
        raise EncryptionError(
            f"ENCRYPTION_ACTIVE_KEY_ID={active!r} is not present in ENCRYPTION_KEYS"
        )

    return KeyRing(keys=keys, active_key_id=active)


@lru_cache(maxsize=1)
def key_ring() -> KeyRing | None:
    """The configured ring, parsed once.

    Cached because parsing validates every key on every call otherwise, and
    this sits on the credential-resolution path. `reset_cache` clears it, which
    tests use to swap configuration.
    """
    ring = _parse_key_ring(settings.encryption_keys, settings.encryption_active_key_id)
    if ring is None:
        log.info(
            "crypto.not_configured",
            detail="ENCRYPTION_KEYS is empty; enc:// pointers cannot be resolved",
        )
    else:
        log.info("crypto.ready", key_ids=sorted(ring.keys), active=ring.active_key_id)
    return ring


def reset_cache() -> None:
    """Forget the parsed ring. For tests that change the configuration."""
    key_ring.cache_clear()


def is_enabled() -> bool:
    """Whether a usable key ring is configured."""
    return key_ring() is not None


def encrypt(plaintext: str, *, context: str) -> str:
    """Seal ``plaintext`` under the active key, bound to ``context``.

    ``context`` becomes the AAD, so the value can only be opened by a caller
    that names the same context. Pass the field or purpose the value belongs
    to, not anything secret — the AAD is authenticated, never encrypted.
    """
    if not context:
        # An empty AAD would let a ciphertext be relocated between fields,
        # which is the whole thing the AAD is here to prevent.
        raise EncryptionError("context is required and may not be empty")

    ring = key_ring()
    if ring is None:
        raise EncryptionNotConfigured(
            "ENCRYPTION_KEYS is not configured; refusing to store this value unencrypted"
        )

    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(ring.active()).encrypt(
        nonce, plaintext.encode("utf-8"), context.encode("utf-8")
    )
    return SEPARATOR.join(
        (TOKEN_VERSION, ring.active_key_id, _b64encode(nonce), _b64encode(sealed))
    )


def decrypt(token: str, *, context: str) -> str:
    """Open a token sealed by `encrypt` under the same ``context``.

    Raises `DecryptionError` for a wrong key, a wrong context, or any
    modification to the ciphertext. There is no return value that means
    "probably fine".
    """
    if not context:
        raise EncryptionError("context is required and may not be empty")

    ring = key_ring()
    if ring is None:
        raise EncryptionNotConfigured("ENCRYPTION_KEYS is not configured; cannot decrypt")

    parts = token.split(SEPARATOR)
    if len(parts) != 4:
        raise DecryptionError(f"malformed token: expected 4 parts, got {len(parts)}")

    version, key_id, encoded_nonce, encoded_ciphertext = parts
    if version != TOKEN_VERSION:
        raise DecryptionError(f"unsupported token version {version!r}")

    nonce = _b64decode(encoded_nonce)
    if len(nonce) != NONCE_BYTES:
        raise DecryptionError(f"nonce is {len(nonce)} bytes, expected {NONCE_BYTES}")

    key = ring.get(key_id)
    try:
        opened = AESGCM(key).decrypt(nonce, _b64decode(encoded_ciphertext), context.encode("utf-8"))
    except InvalidTag as exc:
        # Deliberately does not distinguish tampering from a wrong context: the
        # difference is only useful to someone probing, and the remedy is the
        # same either way.
        raise DecryptionError(
            "authentication failed — wrong key, wrong context, or the value was modified"
        ) from exc

    try:
        return opened.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DecryptionError("decrypted value is not valid UTF-8") from exc


def generate_key() -> str:
    """A fresh base64 AES-256 key, for `scripts/generate_encryption_key.py`."""
    return base64.b64encode(os.urandom(KEY_BYTES)).decode("ascii")
