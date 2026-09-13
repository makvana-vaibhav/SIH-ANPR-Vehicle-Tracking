"""Credential resolution.

``vms_instances.credentials_ref`` stores a **pointer**, never a secret:

    vault://nagarnetra/vms/rajkot-milestone
    env://RAJKOT_VMS_USERNAME:RAJKOT_VMS_PASSWORD
    file:///run/secrets/rajkot_vms
    enc://v1.2025a.<nonce>.<ciphertext>

The registry is the single most valuable table in the state — it lists every
camera and how to reach it. Putting VMS passwords in it would mean one SQL
injection compromises live video across every department. Storing a pointer
means an attacker who reads the whole table still has to separately compromise
the secret store.

``enc://`` is the exception that proves the rule, and it is deliberate. It
carries an AES-256-GCM ciphertext rather than a pointer, for the deployment
that has no secret store to point at — an air-gapped pilot, or the single-node
install this project targets. The column is then still unreadable to anyone who
obtains it without the key, which is the property the pointer discipline exists
to preserve; the alternative in that deployment is plaintext, not Vault. The
ciphertext is bound to the context ``vms-credentials``, so it cannot be moved
to another column and opened there. See ``app/core/crypto.py``.

For the laptop demo, ``env://`` resolves from the process environment and
``vault://`` is unavailable (no Vault on the demo path) — the adapter then
proceeds unauthenticated against the simulator, which is correct behaviour for
a pilot VMS on an internal VLAN. Production wiring is in docs/SECURITY.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.crypto import DecryptionError, EncryptionNotConfigured
from app.core.crypto import decrypt as decrypt_value
from app.core.logging import get_logger

log = get_logger("api.secrets")


@dataclass(frozen=True, slots=True)
class Credentials:
    """A resolved username/password pair.

    ``__repr__`` is overridden so a credential can never be printed into a log
    line or a traceback by accident.
    """

    username: str
    password: str

    def __repr__(self) -> str:
        return f"<Credentials username={self.username!r} password=***>"

    __str__ = __repr__


def resolve_credentials(reference: str | None) -> Credentials | None:
    """Resolve a credentials pointer, or None when unavailable.

    Returns None rather than raising: a VMS with no configured credentials is a
    normal state during phased onboarding, and the adapter handles it.
    """
    if not reference:
        return None

    scheme, _, remainder = reference.partition("://")

    if scheme == "env":
        return _from_env(remainder)
    if scheme == "file":
        return _from_file(remainder)
    if scheme == "enc":
        return _from_ciphertext(remainder)
    if scheme == "vault":
        # Deliberately not implemented on the demo path: there is no Vault in
        # the compose stack, and pretending otherwise would be a stub. The
        # production integration is specified in docs/SECURITY.md.
        log.info(
            "secrets.vault_not_configured",
            reference=reference,
            detail="No secret store on the demo path; adapter proceeds unauthenticated",
        )
        return None

    log.warning("secrets.unknown_scheme", scheme=scheme, reference=reference)
    return None


def _from_env(spec: str) -> Credentials | None:
    """``env://USERNAME_VAR:PASSWORD_VAR``."""
    username_var, _, password_var = spec.partition(":")
    if not username_var or not password_var:
        log.warning("secrets.malformed_env_reference", spec=spec)
        return None

    username = os.environ.get(username_var)
    password = os.environ.get(password_var)
    if username is None or password is None:
        log.info("secrets.env_vars_absent", username_var=username_var)
        return None
    return Credentials(username=username, password=password)


def _from_file(spec: str) -> Credentials | None:
    """``file:///path`` containing ``username:password`` on one line.

    This is the Docker/Kubernetes secret-mount convention.
    """
    path = Path(spec)
    if not path.is_file():
        log.info("secrets.file_absent", path=str(path))
        return None

    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("secrets.file_unreadable", path=str(path), error=str(exc))
        return None

    username, separator, password = content.partition(":")
    if not separator:
        log.warning("secrets.file_malformed", path=str(path))
        return None
    return Credentials(username=username.strip(), password=password.strip())


#: Associated data for every credential ciphertext. Authenticated but not
#: secret, and its whole job is to be *specific*: a ciphertext sealed for this
#: context cannot be pasted into a different column and opened there.
CREDENTIALS_CONTEXT = "vms-credentials"


def _from_ciphertext(spec: str) -> Credentials | None:
    """``enc://<token>`` — an AES-256-GCM sealed ``username:password``.

    Returns None on any failure, consistent with the rest of this module: an
    unresolvable credential is a normal state the adapter handles, and raising
    here would take down stream supervision for the whole fleet because one
    VMS row was sealed with a key this process does not hold.

    The failure is logged at warning, because unlike an absent ``env://`` var
    it is never expected in a working deployment.
    """
    token = spec.strip()
    if not token:
        log.warning("secrets.malformed_enc_reference", detail="empty token")
        return None

    try:
        content = decrypt_value(token, context=CREDENTIALS_CONTEXT)
    except EncryptionNotConfigured:
        log.warning(
            "secrets.enc_no_key_ring",
            detail="credentials_ref is enc:// but ENCRYPTION_KEYS is unset",
        )
        return None
    except DecryptionError as exc:
        # The token is never logged: it is the ciphertext, and pairing it with
        # a decrypt failure in a log aggregator is free work for an attacker.
        log.warning("secrets.enc_undecryptable", error=str(exc))
        return None

    username, separator, password = content.partition(":")
    if not separator:
        log.warning("secrets.enc_malformed_plaintext", detail="expected username:password")
        return None
    return Credentials(username=username.strip(), password=password.strip())
