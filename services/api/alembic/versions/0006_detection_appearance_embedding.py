"""Give detections somewhere to store an appearance embedding.

P11 (vehicle re-identification) needs a way to link a vehicle across cameras
when its plate is unreadable — today linking is plate-string equality only,
so an unreadable plate is a dead end no matter how good the rest of the
system is. An appearance embedding is the fix, but nothing in `ai-lab`
computes one yet (no ReID model is fetched or wired into the pipeline) —
this migration adds only the place to put one, the same "build the honest
half, document the rest" split as `vehicle_colour` (P9): the column, the
candidate-matching query and the ranking logic are the part that does not
depend on a vision model, and they should already be correct the day a
producer exists.

Stored as JSONB (a plain array of floats) rather than a `pgvector` column:
`pgvector` is not installed as a Postgres extension anywhere in this stack
(`infra/postgres/init/01-extensions.sql` provisions postgis/timescaledb/
pg_trgm/pgcrypto/btree_gist only) and adding a new extension with no live
database to verify it against is exactly the kind of blind architectural
change CLAUDE.md's working rules ask to be careful about. It is also not
needed: a re-identification match is only ever scored against a small,
already-narrowed candidate set (other detections within a plausible
time/distance window of one query detection — see `app/services/reid.py`),
not a full-table nearest-neighbour search, so there is no ANN-index workload
here that would justify the new dependency. Cosine similarity in plain
Python over that narrow candidate set is both correct and honest about what
this phase actually needs.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "detections",
        sa.Column(
            "appearance_embedding",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("detections", "appearance_embedding")
