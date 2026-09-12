"""Give cameras an explicit corridor, because analytics has to GROUP BY something real.

City traffic analytics is reported *per corridor* — flow along Ashram Road,
average speed on SG Highway — and until now a camera's corridor existed only by
implication. `scripts/generate_ahmedabad_cameras.py` encodes it twice, both
times as a side effect:

* in the camera code (`CAM-ASH-01`), which is a naming convention, not data, and
  which stops being true the moment anyone renames a camera — as happened to the
  demo fleet, now `CAM-DEMO-01..03` on Ashram Road with nothing in the code
  saying so;
* as a bare slug in `tags` (`['demo', 'recorded-footage', 'plate-region:GB',
  'ash', 'arterial', 'sabarmati']`), positional, unprefixed and indistinguishable
  from the five tags around it.

Grouping a metric on either is the kind of thing that works in the demo and
silently mis-buckets later. `GROUP BY corridor` wants a column.

## Backfill

Existing rows are filled from the tag list, matching against the corridors the
generator actually defines. Anything unmatched is left NULL rather than guessed:
a camera with no corridor is reported as uncorridored, which is true, instead of
being quietly attributed to a road it is not on.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


#: tag slug → canonical corridor name.
#:
#: The generator writes `tags` as `{corridor.code.lower()}|arterial|{taluka}`,
#: so the slug is exactly the lowercased corridor code and the name is the first
#: entry in that corridor's OSM `names` tuple. Transcribed from the `CORRIDORS`
#: table in scripts/generate_ahmedabad_cameras.py rather than imported, because
#: a migration must describe the database at *this* revision and must not change
#: meaning when that script does.
_BACKFILL = {
    "spr": "Sardar Patel Ring Road",
    "r132": "132 Ft. Ring Road",
    "r120": "120 Feet Ring Road",
    "ash": "Ashram Road",
    "jnr": "Jawaharlal Nehru Road",
    # OSM splits this between "SG Highway" and "Gandhinagar-Ahmedabad Highway";
    # the generator merges them because they are one road to a driver.
    "sgh": "SG Highway",
    "cgr": "Chimanlal Girdharlal Road",
    "dir": "Drive-in Road",
    "nrd": "Naroda Road",
    "nsr": "Narol Sarkhej Road",
    "apr": "Airport Road",
    "ave": "Ahmadabad Vadodara Expressway",
}


def upgrade() -> None:
    op.add_column("cameras", sa.Column("corridor", sa.String(64), nullable=True))

    for slug, name in _BACKFILL.items():
        op.execute(
            sa.text(
                "UPDATE cameras SET corridor = :name "
                "WHERE corridor IS NULL AND :slug = ANY(tags)"
            ).bindparams(name=name, slug=slug)
        )

    # Analytics groups by corridor over a time window; the corridor is the
    # leading column because every such query filters or groups on it first.
    op.create_index("ix_cameras_corridor", "cameras", ["corridor"])


def downgrade() -> None:
    op.drop_index("ix_cameras_corridor", table_name="cameras")
    op.drop_column("cameras", "corridor")
