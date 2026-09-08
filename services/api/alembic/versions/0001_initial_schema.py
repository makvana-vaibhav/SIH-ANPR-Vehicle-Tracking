"""Initial schema: registry, intelligence, and security tables

Creates the complete NagarNetra data model in one revision:

* **Registry** — departments, vms_instances, cameras, camera_health
* **Intelligence** — detections, watchlist, alerts, vehicle_tracks
* **Security** — users, audit_log

Two tables become TimescaleDB hypertables (``camera_health`` and
``detections``). At 80,000 cameras these are the only tables whose growth is
unbounded, and chunking them by time is what makes both time-range queries and
retention drops cheap — dropping a chunk is a file unlink, while deleting a
day's rows from a ten-billion-row heap is an outage.

This revision also asserts that PostGIS and TimescaleDB are actually present.
A vanilla PostgreSQL image would accept every CREATE TABLE here and then fail
at the first radius query, so the failure is forced to happen now, loudly.

Revision ID: 0001
Revises:
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Preconditions ────────────────────────────────────────────────
    # The compose image (timescale/timescaledb-ha:*-all) ships both. If someone
    # swaps in a vanilla postgres image, fail here rather than at query time.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        """
        DO $$
        DECLARE missing TEXT;
        BEGIN
            SELECT string_agg(e, ', ') INTO missing
              FROM unnest(ARRAY['postgis','timescaledb','pg_trgm']) AS e
             WHERE NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = e);
            IF missing IS NOT NULL THEN
                RAISE EXCEPTION
                    'NagarNetra requires extensions that are missing: %. '
                    'Use timescale/timescaledb-ha:*-all (see CLAUDE.md section 9).',
                    missing;
            END IF;
        END $$;
        """
    )

    # ══ Registry ═════════════════════════════════════════════════════
    op.create_table(
        "departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("contact_email", sa.String(255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "vms_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("vendor", sa.String(32), nullable=False),
        sa.Column("adapter_type", sa.String(32), nullable=False),
        sa.Column("base_url", sa.Text()),
        # A POINTER into a secret store — never the secret itself.
        sa.Column("credentials_ref", sa.Text()),
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "cameras",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("camera_code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "vms_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("vms_instances.id", ondelete="SET NULL"),
        ),
        sa.Column("district", sa.String(64)),
        sa.Column("city", sa.String(64)),
        sa.Column("junction", sa.String(128)),
        sa.Column("address", sa.Text()),
        sa.Column(
            "location",
            Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("heading_deg", sa.SmallInteger()),
        sa.Column("camera_type", sa.String(16)),
        sa.Column("protocol", sa.String(16)),
        sa.Column("stream_url", sa.Text()),
        sa.Column("sub_stream_url", sa.Text()),
        sa.Column("resolution", sa.String(16)),
        sa.Column("fps", sa.SmallInteger()),
        sa.Column("anpr_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("installed_on", sa.Date()),
        sa.Column("tags", postgresql.ARRAY(sa.Text())),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # GiST over geography: powers /cameras/nearby and map-viewport queries.
    op.create_index("ix_cameras_location", "cameras", ["location"], postgresql_using="gist")
    op.create_index("ix_cameras_department_status", "cameras", ["department_id", "status"])
    op.create_index("ix_cameras_district", "cameras", ["district"])
    op.create_index("ix_cameras_status", "cameras", ["status"])

    op.create_table(
        "camera_health",
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reachable", sa.Boolean()),
        sa.Column("fps_actual", sa.Float()),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("bitrate_kbps", sa.Integer()),
        sa.Column("frame_drop_pct", sa.Float()),
        sa.Column("error_code", sa.String(64)),
        sa.PrimaryKeyConstraint("camera_id", "ts"),
    )

    # ══ Intelligence ═════════════════════════════════════════════════
    op.create_table(
        "detections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id", ondelete="SET NULL"),
        ),
        sa.Column("track_id", sa.String(64), nullable=False),
        sa.Column("vehicle_type", sa.String(16)),
        sa.Column("vehicle_colour", sa.String(24)),
        sa.Column("plate_text", sa.String(24)),
        sa.Column("plate_normalised", sa.String(24)),
        sa.Column("plate_confidence", sa.Float()),
        # Per-frame OCR candidates behind the consensus result, so an operator
        # can see why a plate was chosen and override it.
        sa.Column("ocr_raw", postgresql.JSONB()),
        sa.Column("detection_confidence", sa.Float()),
        sa.Column("bbox", postgresql.JSONB()),
        sa.Column("plate_bbox", postgresql.JSONB()),
        sa.Column("crop_key", sa.Text()),
        sa.Column("frame_key", sa.Text()),
        sa.Column("direction", sa.String(16)),
        sa.Column("speed_kmph", sa.Float()),
        sa.Column("is_validated", sa.Boolean()),
        # ts must be in the PK: TimescaleDB requires the partitioning column
        # in every unique index on a hypertable.
        sa.PrimaryKeyConstraint("id", "ts"),
    )

    op.create_table(
        "watchlist",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plate_normalised", sa.String(24), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("case_ref", sa.String(64)),
        sa.Column("remarks", sa.Text()),
        sa.Column("added_by", postgresql.UUID(as_uuid=True)),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_to", sa.DateTime(timezone=True)),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # One plate may legitimately appear under several distinct cases.
        sa.UniqueConstraint("plate_normalised", "case_ref", name="uq_watchlist_plate_case"),
    )
    # Partial index: the matcher only ever looks at active entries.
    op.create_index(
        "ix_watchlist_plate_active",
        "watchlist",
        ["plate_normalised"],
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Not an FK: `detections` is a hypertable with a composite key, and its
        # chunks are dropped by retention long before alerts are. An alert must
        # outlive the detection that raised it.
        sa.Column("detection_id", postgresql.UUID(as_uuid=True)),
        sa.Column("detection_ts", sa.DateTime(timezone=True)),
        sa.Column(
            "watchlist_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("watchlist.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "camera_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("cameras.id", ondelete="SET NULL"),
        ),
        sa.Column("alert_type", sa.String(24), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("plate_normalised", sa.String(24)),
        sa.Column("confidence", sa.Float()),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("acknowledged_by", postgresql.UUID(as_uuid=True)),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.Text()),
    )
    op.create_index(
        "ix_alerts_status_created", "alerts", ["status", sa.text("created_at DESC")]
    )
    op.create_index(
        "ix_alerts_priority_created", "alerts", ["priority", sa.text("created_at DESC")]
    )
    op.create_index("ix_alerts_plate", "alerts", ["plate_normalised"])
    op.create_index(
        "ix_alerts_camera_created", "alerts", ["camera_id", sa.text("created_at DESC")]
    )

    op.create_table(
        "vehicle_tracks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plate_normalised", sa.String(24), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True)),
        sa.Column("last_seen", sa.DateTime(timezone=True)),
        sa.Column("camera_count", sa.Integer()),
        sa.Column("hop_count", sa.Integer()),
        sa.Column(
            "path", Geography(geometry_type="LINESTRING", srid=4326, spatial_index=False)
        ),
        # Ordered per-leg detail incl. implied speed and plausibility. Legs that
        # fail plausibility are MARKED, never removed.
        sa.Column("hops", postgresql.JSONB()),
        sa.Column("confidence", sa.Float()),
        sa.Column("is_plausible", sa.Boolean()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_vehicle_tracks_plate_seen",
        "vehicle_tracks",
        ["plate_normalised", sa.text("first_seen DESC")],
    )
    op.create_index(
        "ix_vehicle_tracks_path", "vehicle_tracks", ["path"], postgresql_using="gist"
    )

    # ══ Security ═════════════════════════════════════════════════════
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("full_name", sa.String(200)),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column(
            "department_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("mfa_secret", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_department", "users", ["department_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # Identity is denormalised on purpose: the trail must stay readable
        # years later even if the user is renamed, moved, or deleted.
        sa.Column("user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("username", sa.String(64)),
        sa.Column("role", sa.String(32)),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(32)),
        sa.Column("resource_id", sa.String(128)),
        sa.Column("ip", postgresql.INET()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("params", postgresql.JSONB()),
        sa.Column("result", sa.String(16), nullable=False, server_default="success"),
    )
    op.create_index("ix_audit_ts", "audit_log", [sa.text("ts DESC")])
    op.create_index("ix_audit_user_ts", "audit_log", ["user_id", sa.text("ts DESC")])
    op.create_index("ix_audit_action_ts", "audit_log", ["action", sa.text("ts DESC")])
    op.create_index("ix_audit_resource", "audit_log", ["resource_type", "resource_id"])

    # ══ TimescaleDB hypertables ══════════════════════════════════════
    # Must run before any data is inserted.
    #
    # detections: 1-day chunks. At the target rate (~2,700 events/s) a day is
    # ~230M rows — large enough to be efficient, small enough to drop or
    # compress as one unit.
    op.execute(
        """
        SELECT create_hypertable(
            'detections', 'ts',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists => TRUE
        )
        """
    )
    # camera_health: 7-day chunks. Lower volume, queried over longer windows
    # for uptime sparklines.
    op.execute(
        """
        SELECT create_hypertable(
            'camera_health', 'ts',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists => TRUE
        )
        """
    )

    # Indexes on hypertables are created after conversion so they propagate to
    # every chunk, including chunks created later.
    op.create_index(
        "ix_camera_health_camera_ts", "camera_health", ["camera_id", sa.text("ts DESC")]
    )
    op.create_index(
        "ix_detections_plate_ts", "detections", ["plate_normalised", sa.text("ts DESC")]
    )
    op.create_index(
        "ix_detections_camera_ts", "detections", ["camera_id", sa.text("ts DESC")]
    )
    op.create_index("ix_detections_ts", "detections", [sa.text("ts DESC")])
    # Trigram index: the runtime fallback for partial-plate search when
    # OpenSearch is unavailable (Phase 8). The demo never dies on a container.
    op.execute(
        """
        CREATE INDEX ix_detections_plate_trgm
            ON detections USING gin (plate_normalised gin_trgm_ops)
        """
    )


def downgrade() -> None:
    # Hypertables drop with their parent table; chunks follow.
    op.drop_table("audit_log")
    op.drop_table("users")
    op.drop_table("vehicle_tracks")
    op.drop_table("alerts")
    op.drop_table("watchlist")
    op.drop_table("detections")
    op.drop_table("camera_health")
    op.drop_table("cameras")
    op.drop_table("vms_instances")
    op.drop_table("departments")
    # Extensions are deliberately NOT dropped: the init script and other
    # databases in the cluster may depend on them.
