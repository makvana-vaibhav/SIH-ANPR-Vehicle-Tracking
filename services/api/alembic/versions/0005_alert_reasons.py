"""Give alerts somewhere to store why they fired.

CLAUDE.md's explainability rule — "every alert must carry its reasons" — has
been aspirational since it was written: `alerts` has no column for one, and
no test enforces it, because there was nothing to enforce. `Risk: 87%` alone
is a bug; `Risk: HIGH — unusual route, unusual travel time` is the feature,
and this is the column that makes the second one possible instead of just
documented.

P5 is the first producer: a trajectory-anomaly alert stores its factors here
as a JSON array of `{"factor": ..., "detail": ...}` objects — see
`app/services/anomaly.py`. Nullable, because the watchlist-hit and
camera-down alert types that predate this migration have nothing to
backfill it with, and a fabricated reason would be worse than none.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "alerts",
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("alerts", "reasons")
