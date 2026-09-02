#!/usr/bin/env python3
"""Generate `docs/API.md` from the live OpenAPI document.

Hand-written API documentation drifts. It drifts silently, and the first person
to notice is someone integrating against it who now distrusts the rest of the
document too. This reads the spec the application actually serves, so the file
is wrong only if the API is.

Run it from the host, against the published port — `docs/` is not mounted
into the api container:

    python3 scripts/generate_api_docs.py
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

SPEC_URL = "http://localhost:8000/openapi.json"
OUT = Path(__file__).resolve().parent.parent / "docs" / "API.md"

#: Which permission gates each router, for the table. Read from the route's
#: own dependencies where FastAPI exposes them, and named here where it does
#: not — the RBAC matrix in `app/core/rbac.py` remains the authority.
TAG_NOTES = {
    "auth": "Public for login; the rest need a valid access token.",
    "cameras": "`camera.read` to list, `camera.create`/`update`/`delete` to change.",
    "streams": "`stream.view`. Every stream open writes an audit row.",
    "detections": "`search.execute`. A plate filter writes an audit row.",
    "vehicles": "`search.execute`. Route and convoy queries are audited explicitly.",
    "watchlist": "`watchlist.read` to view; separate grants to create, update, delete.",
    "alerts": "`alert.read`; each transition needs its own grant.",
    "users": "`user.read` / `user.create` / `user.update` / `user.delete`. Admin only.",
    "audit": "`audit.read`. Reading the trail is itself audited.",
    "fleet health": "`camera.read`.",
    "events": "WebSocket. Token passed on the query string — browsers cannot set headers on the handshake.",
    "health": "Unauthenticated, and exempt from rate limiting: a throttled probe would restart the container.",
}


def load_spec() -> dict:
    try:
        with urllib.request.urlopen(SPEC_URL, timeout=15) as response:  # noqa: S310
            return json.load(response)
    except Exception as exc:  # noqa: BLE001 - a script boundary
        print(f"Could not read {SPEC_URL}: {exc}", file=sys.stderr)
        print("Is the API running?  docker compose up -d api", file=sys.stderr)
        raise SystemExit(1) from exc


def main() -> int:
    spec = load_spec()
    paths = spec.get("paths", {})

    by_tag: dict[str, list[tuple[str, str, dict]]] = {}
    for path, methods in paths.items():
        for method, operation in methods.items():
            for tag in operation.get("tags", ["untagged"]):
                by_tag.setdefault(tag, []).append((method.upper(), path, operation))

    operations = sum(len(v) for v in by_tag.values())
    lines: list[str] = [
        "# API reference",
        "",
        "**Generated** by `scripts/generate_api_docs.py` from the OpenAPI document",
        "the application actually serves. Do not edit by hand — regenerate it.",
        "",
        f"*{operations} operations across {len(paths)} paths. "
        f"Generated {datetime.now(UTC):%Y-%m-%d}.*",
        "",
        "Interactive documentation, with request bodies and schemas, is at",
        "`http://localhost:8000/docs` while the stack is running.",
        "",
        "## Conventions",
        "",
        "* All routes are under `/api/v1`, except `/health`, `/ready` and `/ws/events`.",
        "* Authentication is a bearer token: `Authorization: Bearer <access_token>`.",
        "* Timestamps are **UTC, ISO 8601** on the wire. The UI converts to IST at",
        "  the presentation edge; nothing is stored in local time.",
        "* Every list endpoint is paginated. There is no unbounded read.",
        "* Errors return `{\"detail\": ...}` with a conventional status code.",
        "  A `429` carries `Retry-After`.",
        "",
        "## Authorisation at a glance",
        "",
        "`app/core/rbac.py` is the single source of truth; see",
        "[`SECURITY.md`](SECURITY.md) §2 for the full matrix.",
        "",
    ]

    for tag in sorted(by_tag):
        lines.append(f"## {tag}")
        lines.append("")
        note = TAG_NOTES.get(tag)
        if note:
            lines.append(f"*{note}*")
            lines.append("")
        lines.append("| Method | Path | Summary |")
        lines.append("|---|---|---|")
        for method, path, operation in sorted(by_tag[tag], key=lambda x: (x[1], x[0])):
            summary = (
                operation.get("summary")
                or (operation.get("description") or "").split("\n")[0]
                or operation.get("operationId", "")
            )
            lines.append(f"| `{method}` | `{path}` | {summary.strip()} |")
        lines.append("")

    lines += [
        "## WebSocket",
        "",
        "```",
        "ws://<host>/ws/events?token=<access_token>",
        "```",
        "",
        "Three payload shapes share the socket:",
        "",
        "| `event` | Meaning |",
        "|---|---|",
        "| `vehicle.observed` | A vehicle still in view; the current best reading. React now. |",
        "| `vehicle.completed` | The vehicle has left and consensus is settled. This is what was persisted. |",
        "| `alert.raised` | A watchlist hit, with the entry and its case reference. |",
        "",
        "Keepalives arrive every 20 s so proxies do not close an idle feed —",
        "and an alert feed is idle most of the time, which is exactly when it",
        "must stay connected.",
        "",
        "Bounding boxes are in **source-frame pixels**, and every vehicle event",
        "carries the `frame` dimensions they were measured in. A consumer that",
        "draws them cannot scale them otherwise.",
        "",
    ]

    OUT.write_text("\n".join(lines) + "\n")
    print(f"Wrote {OUT} — {operations} operations across {len(paths)} paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
