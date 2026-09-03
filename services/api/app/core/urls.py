"""URL handling that has to be right about credentials.

RTSP puts its username and password in the URL — the protocol offers nowhere
else — so any code that logs, stores or returns a stream URL is a place a
password can escape. The AI worker has its own copy of this for the same
reason; it is duplicated rather than shared because the worker's image
deliberately carries no part of the API (CLAUDE.md §9 note 3), and coupling
them to save nine lines would undo that.
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def redact(url: str | None) -> str | None:
    """A URL safe to log or return to a client.

    The host and path survive, because an operator diagnosing a camera needs
    to see which host and which stream. Only the password is replaced.
    """
    if not url:
        return url
    parsed = urlparse(url)
    if not parsed.password:
        return url
    netloc = f"{parsed.username or ''}:***@{parsed.hostname}"
    if parsed.port:
        netloc += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))
