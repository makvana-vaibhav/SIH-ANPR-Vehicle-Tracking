"""Give detections the two numbers traffic intelligence cannot work without.

The platform stores one row per *retired track* — `event_consumer` persists
only `vehicle.completed`. That is the right call for history (one car, one
row) and it is why density, queue detection and stopped-vehicle detection
have been impossible: "how long was this vehicle in view" and "did it
actually move" are simply not in the record.

Both numbers already exist. The worker has computed `duration_s` since
streaming was built, and now computes net image-space displacement too
(`ailab.types.Track.net_motion`); the consumer was throwing both away. So
this migration is not new measurement — it is keeping measurement the
pipeline already performs.

Three columns, one index:

``dwell_s``     seconds the vehicle was in the camera's view. Density is
                occupancy over these intervals; a queue is several vehicles
                whose dwell is long at once.
``motion_px``   net displacement across the frame, in pixels. Distinguishes a
                vehicle that drove through from one that sat still — box
                jitter alone accumulates path length, so only *net*
                displacement separates the two.
``direction``   already existed from migration 0001 and has never been
                written by anything. No DDL needed; `event_consumer` simply
                starts populating it.

The index is for the vehicle-type breakdown. `detections` already indexes
`(camera_id, ts)`, but counting cars/motorcycles/buses/trucks per camera
adds `vehicle_type` to both the filter and the grouping, and that is the
hot path of the traffic dashboard's headline panel.

`ALTER TABLE ... ADD COLUMN` is safe on a Timescale hypertable: it applies
to the parent and propagates to every chunk, and all three columns are
nullable, so no chunk is rewritten and existing rows keep NULL — honestly
representing that nothing measured them.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("detections", sa.Column("dwell_s", sa.Float(), nullable=True))
    op.add_column("detections", sa.Column("motion_px", sa.Float(), nullable=True))
    op.create_index(
        "ix_detections_camera_type_ts",
        "detections",
        ["camera_id", "vehicle_type", sa.text("ts DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_detections_camera_type_ts", table_name="detections")
    op.drop_column("detections", "motion_px")
    op.drop_column("detections", "dwell_s")
