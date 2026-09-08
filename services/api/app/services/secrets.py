"""Credential resolution.

``vms_instances.credentials_ref`` stores a **pointer**, never a secret:

    vault://nagarnetra/vms/rajkot-milestone
    env://RAJKOT_VMS_USERNAME:RAJKOT_VMS_PASSWORD
    file:///run/secrets/rajkot_vms

The registry is the single most valuable table in the state — it lists every
camera and how to reach it. Putting VMS passwords in it would mean one SQL
injection compromises live video across every department. Storing a pointer
means an attacker who reads the whole table still has to separately compromise
the secret store.

For the laptop demo, ``env://`` resolves from the process environment and
``vault://`` is unavailable (no Vault on the demo path) — the adapter then
proceeds unauthenticated against the simulator, which is correct behaviour for
a pilot VMS on an internal VLAN. Production wiring is in docs/SECURITY.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

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
