"""What counts as an acceptable password here.

## Why not just a length rule

`min_length=12` was the whole policy, and length alone admits
`NagarNetra@2026` — the documented demo credential, which is exactly the string
an attacker tries first against a system whose seed script is on GitHub.

The rules below are deliberately modest. Long, arbitrary complexity
requirements push people toward `Password1!` and a sticky note; NIST's own
guidance moved away from them years ago for that reason. What is enforced is:

* **length**, which is the only requirement that reliably buys entropy;
* **not a known credential of this system**, because those are published;
* **not obviously derived from the username**, because that is the second guess;
* **not a single repeated or sequential run**, which defeats length.

Everything here runs on the API. A frontend meter is a courtesy to the person
typing, never the check.
"""

from __future__ import annotations

import re

#: Police infrastructure. Twelve is the floor, not the target.
MIN_LENGTH = 12
MAX_LENGTH = 256

#: Credentials this repository publishes. They are in `scripts/seed.py`, in the
#: README and in the demo script, so they must never survive into a deployment.
KNOWN_CREDENTIALS = frozenset(
    {
        "nagarnetra@2026",
        "nagarnetra@2025",
        "changeme",
        "password",
        "admin",
        "nagarnetra",
        "nagarnetra-web",
    }
)

#: Substrings that make a password guessable regardless of its length.
WEAK_PATTERNS = (
    (re.compile(r"^(.)\1+$"), "is the same character repeated"),
    (re.compile(r"(?i)\b(qwerty|asdfgh|zxcvbn)"), "contains a keyboard run"),
    (re.compile(r"(012345|123456|234567|345678|456789|987654|876543)"), "contains a digit run"),
)


class PasswordRejected(ValueError):
    """The password does not meet policy. The message is shown to the user."""


def _looks_like(candidate: str, username: str) -> bool:
    """True when the password is the username with decoration.

    `vaibhav` → `Vaibhav@2026` is the second thing anyone tries, and a length
    rule alone waves it through.
    """
    if not username or len(username) < 3:
        return False
    stripped = re.sub(r"[^a-z]", "", candidate.lower())
    return username.lower() in stripped


def validate_password(password: str, *, username: str = "") -> None:
    """Raise `PasswordRejected` if the password may not be used.

    Returns nothing on success: there is no score to report, because a score
    invites arguing with the threshold rather than choosing a better password.
    """
    if len(password) < MIN_LENGTH:
        raise PasswordRejected(
            f"Password must be at least {MIN_LENGTH} characters. " "This is police infrastructure."
        )
    if len(password) > MAX_LENGTH:
        raise PasswordRejected(f"Password must be at most {MAX_LENGTH} characters.")

    if password.lower() in KNOWN_CREDENTIALS:
        raise PasswordRejected(
            "That is a documented demo credential for this system and is "
            "published in the repository. Choose something else."
        )

    if _looks_like(password, username):
        raise PasswordRejected("Password must not contain your username.")

    for pattern, why in WEAK_PATTERNS:
        if pattern.search(password):
            raise PasswordRejected(f"Password {why}.")

    # Enough distinct characters that length is real rather than padded.
    if len(set(password)) < 6:
        raise PasswordRejected("Password uses too few distinct characters to be worth its length.")
