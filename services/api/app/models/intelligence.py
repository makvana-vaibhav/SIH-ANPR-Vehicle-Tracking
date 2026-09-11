"""Detections, watchlist, alerts, and reconstructed vehicle journeys.

This is the intelligence tier: what the AI pipeline produces, what it is
checked against, what fires as a result, and the cross-camera journeys the
correlator rebuilds from it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.enums import AlertStatus


class Detection(Base):
    """One vehicle sighting: **one row per track, not per frame**.

    The AI pipeline buffers every OCR read for a track and emits a single
    consensus result when the track ends (Phase 5). ``ocr_raw`` keeps the
    per-frame candidates behind that consensus so an operator can see why the
    system chose a plate, and override it.

    TimescaleDB hypertable chunked by ``ts``; the primary key is composite
    ``(id, ts)`` because Timescale requires the partitioning column in every
    unique index.
    """

    __tablename__ = "detections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)

    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="SET NULL")
    )
    # Unique within a camera session, assigned by ByteTrack.
    track_id: Mapped[str] = mapped_column(String(64), nullable=False)

    vehicle_type: Mapped[str | None] = mapped_column(String(16))
    vehicle_colour: Mapped[str | None] = mapped_column(String(24))

    # plate_text is what OCR read; plate_normalised is uppercased with
    # separators stripped and position-aware O/0, I/1, B/8 disambiguation
    # applied. Every lookup and every watchlist comparison uses the normalised
    # form — comparing raw OCR output would miss on formatting alone.
    plate_text: Mapped[str | None] = mapped_column(String(24))
    plate_normalised: Mapped[str | None] = mapped_column(String(24))
    plate_confidence: Mapped[float | None] = mapped_column(Float)
    ocr_raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    detection_confidence: Mapped[float | None] = mapped_column(Float)

    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    plate_bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # MinIO object keys, not URLs: the bucket and endpoint can move without
    # rewriting history.
    crop_key: Mapped[str | None] = mapped_column(Text)
    frame_key: Mapped[str | None] = mapped_column(Text)

    direction: Mapped[str | None] = mapped_column(String(16))
    speed_kmph: Mapped[float | None] = mapped_column(Float)

    # False when the read does not satisfy Indian plate grammar. Such reads are
    # flagged and kept, never silently dropped — a partially-read plate is
    # still evidence, and hiding it would misrepresent what the system saw.
    is_validated: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        # The plate-search hot path: "every sighting of this plate, newest first".
        Index("ix_detections_plate_ts", "plate_normalised", ts.desc()),
        Index("ix_detections_camera_ts", "camera_id", ts.desc()),
        Index("ix_detections_ts", ts.desc()),
        # Trigram index for partial-plate search when OpenSearch is unavailable.
        Index(
            "ix_detections_plate_trgm",
            "plate_normalised",
            postgresql_using="gin",
            postgresql_ops={"plate_normalised": "gin_trgm_ops"},
        ),
    )

    def __repr__(self) -> str:
        return f"<Detection {self.plate_normalised} @{self.ts}>"


class Watchlist(Base):
    """Plates of interest, with validity windows.

    ``valid_from`` / ``valid_to`` exist so a BOLO can expire on its own. An
    entry that never expires is a surveillance liability, and purpose
    limitation is a requirement we take seriously (docs/SECURITY.md).
    """

    __tablename__ = "watchlist"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plate_normalised: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    case_ref: Mapped[str | None] = mapped_column(String(64))
    remarks: Mapped[str | None] = mapped_column(Text)

    added_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # The same plate can legitimately appear under several cases.
        UniqueConstraint("plate_normalised", "case_ref", name="uq_watchlist_plate_case"),
        # Partial index: the matcher only ever looks at active entries.
        Index(
            "ix_watchlist_plate_active",
            "plate_normalised",
            postgresql_where=text("active"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Watchlist {self.plate_normalised} {self.category}/{self.priority}>"


class Alert(Base):
    """A raised alert and its handling lifecycle.

    Detection identity is stored as ``(detection_id, detection_ts)`` rather
    than a foreign key: ``detections`` is a hypertable whose primary key is
    composite, and detection chunks are dropped by retention policy long before
    alerts are. An alert must survive the expiry of the detection that caused it.
    """

    __tablename__ = "alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    detection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detection_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    watchlist_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("watchlist.id", ondelete="SET NULL")
    )
    camera_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cameras.id", ondelete="SET NULL")
    )

    alert_type: Mapped[str] = mapped_column(String(24), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    plate_normalised: Mapped[str | None] = mapped_column(String(24))
    confidence: Mapped[float | None] = mapped_column(Float)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=AlertStatus.NEW.value)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    #: Why this alert fired, as a JSON array of `{"factor", "detail"}` objects
    #: — the explainability rule in CLAUDE.md §5. Null for alert types that
    #: predate migration 0005; `anomaly.py` is the first producer.
    reasons: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    __table_args__ = (
        # The alerts screen: open alerts, highest priority first, newest first.
        Index("ix_alerts_status_created", "status", created_at.desc()),
        Index("ix_alerts_priority_created", "priority", created_at.desc()),
        Index("ix_alerts_plate", "plate_normalised"),
        Index("ix_alerts_camera_created", "camera_id", created_at.desc()),
    )

    def __repr__(self) -> str:
        return f"<Alert {self.alert_type} {self.plate_normalised} {self.status}>"


class VehicleTrack(Base):
    """A reconstructed cross-camera journey for one plate.

    ``hops`` carries the ordered per-leg detail (camera, timestamp, gap,
    distance, implied speed, and whether that leg is plausible). Legs that fail
    plausibility are marked, not removed: a system that quietly deletes the
    inconvenient parts of a route is not one a court should trust.
    """

    __tablename__ = "vehicle_tracks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plate_normalised: Mapped[str] = mapped_column(String(24), nullable=False)

    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    camera_count: Mapped[int | None] = mapped_column(Integer)
    hop_count: Mapped[int | None] = mapped_column(Integer)

    path: Mapped[str | None] = mapped_column(
        Geography(geometry_type="LINESTRING", srid=4326, spatial_index=False)
    )
    hops: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    confidence: Mapped[float | None] = mapped_column(Float)
    is_plausible: Mapped[bool | None] = mapped_column(Boolean)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_vehicle_tracks_plate_seen", "plate_normalised", first_seen.desc()),
        Index("ix_vehicle_tracks_path", "path", postgresql_using="gist"),
    )

    def __repr__(self) -> str:
        return f"<VehicleTrack {self.plate_normalised} hops={self.hop_count}>"
