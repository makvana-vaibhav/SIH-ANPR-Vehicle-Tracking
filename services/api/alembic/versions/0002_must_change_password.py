"""Force a password change when an administrator set it.

An account whose password was chosen by somebody else cannot be used for
attribution: every action it takes is deniable, because at least two people
could have signed in. That matters more here than in most systems — the audit
log is a stated feature of this platform, and a shared credential silently
makes it worthless.

So an administrator creating an account or resetting a password sets a flag,
and the holder must choose their own password before the account is usable.
Existing accounts default to `false`: they were seeded, their passwords are
already documented, and flipping them all to `true` would lock everyone out of
the demo the moment this migration ran. `scripts/seed.py` remains responsible
for saying its credentials are demo credentials.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment=(
                "Set when an administrator chose this password. The holder must "
                "replace it before the account can be relied on for attribution."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "must_change_password")
