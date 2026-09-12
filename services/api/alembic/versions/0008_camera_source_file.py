"""Let a camera name a recorded clip as its source, from the UI.

Until now the only way to put footage behind a camera was `SIM_CAMERA_VIDEOS`,
an environment variable read once at simulator start. That is fine for the
seeded demo fleet and useless for the person holding a phone full of footage
they just shot: adding it meant editing `.env` and restarting a container.

``source_file`` is the database's answer to "which clip does this camera
replay". One column, nullable, holding a **bare filename** — never a path.
The simulator resolves it against its own `SIM_VIDEO_DIR`, so the value is
meaningless outside that directory and a row cannot name `/etc/passwd` or
`../../secrets`. Validation lives in `app/schemas/camera.py`; this column is
deliberately dumb.

Why not reuse `stream_url`? Because `gateway.py` registers a MediaMTX *pull*
path from `stream_url`, and a `file://` URL there would have MediaMTX trying
to pull a path it cannot serve — a variant of the self-pull loop that CLAUDE.md
§Start-here lists as trap 1. A separate column keeps "pull this URL" and
"replay this file" as the different things they are.

NULL means "no clip pinned", which is what every existing camera is: the
simulator falls back to its per-corridor round-robin exactly as before, so
this migration changes no behaviour on its own.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("cameras", sa.Column("source_file", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("cameras", "source_file")
