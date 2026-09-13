#!/usr/bin/env python3
"""Mint an AES-256 key for ENCRYPTION_KEYS, and seal a credential with it.

    python3 scripts/generate_encryption_key.py --key-id 2025a
    python3 scripts/generate_encryption_key.py --key-id 2025a --seal admin:hunter2

The first form prints a key ring line to paste into `.env`. The second also
prints the `credentials_ref` value for a VMS row, so a deployment with no Vault
can populate the registry without a plaintext password ever being typed into
SQL.

This is a script, so `print` is correct here (CLAUDE.md §5 permits it under
`scripts/`). It deliberately does **not** write to `.env` or the database: a
key that a tool placed for you is a key nobody chose where to store.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys

# Generating a key needs nothing from the app; sealing needs AESGCM directly so
# the script stays runnable without the API's settings (and therefore without a
# database URL, a JWT secret, or any of the rest of the environment).
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - depends on the caller's environment
    print(
        "cryptography is not installed. Run:  pip install cryptography",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

KEY_BYTES = 32
NONCE_BYTES = 12
TOKEN_VERSION = "v1"
CREDENTIALS_CONTEXT = "vms-credentials"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def seal(plaintext: str, key: bytes, key_id: str, context: str) -> str:
    """Produce the same token format as `app.core.crypto.encrypt`."""
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(key).encrypt(
        nonce, plaintext.encode("utf-8"), context.encode("utf-8")
    )
    return ".".join((TOKEN_VERSION, key_id, _b64url(nonce), _b64url(sealed)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--key-id",
        default="2025a",
        help="Identifier stored in every token sealed by this key (default: 2025a)",
    )
    parser.add_argument(
        "--seal",
        metavar="USERNAME:PASSWORD",
        help="Also emit a credentials_ref for this pair, sealed with the new key",
    )
    args = parser.parse_args()

    if "." in args.key_id or ":" in args.key_id or "," in args.key_id:
        print("key id may not contain '.', ':' or ','", file=sys.stderr)
        return 2

    key = os.urandom(KEY_BYTES)
    encoded = base64.b64encode(key).decode("ascii")

    print("# Add to .env — keep every key you have ever sealed with:")
    print(f'ENCRYPTION_KEYS="{args.key_id}:{encoded}"')
    print(f'ENCRYPTION_ACTIVE_KEY_ID="{args.key_id}"')

    if args.seal:
        if ":" not in args.seal:
            print("\n--seal expects USERNAME:PASSWORD", file=sys.stderr)
            return 2
        token = seal(args.seal, key, args.key_id, CREDENTIALS_CONTEXT)
        print("\n# credentials_ref for the vms_instances row:")
        print(f"enc://{token}")

    print("\n# Rotation: add the new key alongside the old, move ACTIVE_KEY_ID to")
    print("# it, re-encrypt, then drop the old key. Tokens name their own key,")
    print("# so both work at once.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
