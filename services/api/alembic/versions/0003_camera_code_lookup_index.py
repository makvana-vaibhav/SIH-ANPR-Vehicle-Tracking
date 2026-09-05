"""Functional index on upper(camera_code), so the ingest path stops seq-scanning.

Every detection the platform ingests is resolved from a camera *code* to a
camera *id*. That lookup is deliberately case-insensitive — the registry
stores canonical uppercase codes (CAM-00042) while MediaMTX reports its stream
paths lowercased, and an exact match silently left `camera_id` NULL on every
detection — but `upper(camera_code) = ...` cannot use the plain unique index on
`camera_code`. Postgres falls back to a sequential scan.

At the 281 cameras this repository develops against, that scan is sub-millisecond
and nothing shows. The Phase 10 load test put 80,000 cameras in the registry and
it became **106 ms per lookup, 80,278 rows discarded each time**, collapsing
ingest from ~1,500 events/s to ~140.

An expression index restores the index lookup while keeping the
case-insensitive semantics that were added for a real reason.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cameras_camera_code_upper "
        "ON cameras (upper(camera_code))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_cameras_camera_code_upper")
