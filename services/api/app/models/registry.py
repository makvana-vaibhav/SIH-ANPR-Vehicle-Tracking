"""Camera estate: departments, VMS instances, cameras, and camera health.

This is the Model 1 (Registry + GIS) half of the platform. The registry is the
single statewide answer to "what cameras exist, who owns them, and where are
they" — a question Gujarat currently cannot answer in one query, because the
estate is spread across departments and vendors.

Crucially, registering a camera here does **not** move its video. The owning
VMS stays authoritative; we hold metadata and resolve a stream URL on demand.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.models.enums import CameraStatus


class Department(Base):
    """An owning government department (Police, Health, GSRTC, ...)."""

    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    cameras: Mapped[list[Camera]] = relationship(back_populates="department")

    def __repr__(self) -> str:
        return f"<Department {self.code}>"


class VmsInstance(Base):
    """A federated video management system we integrate with.

    ``credentials_ref`` is a *pointer* into a secret store (vault path, k8s
    secret name, env var name) — never the credential itself. Storing a VMS
    password in this table would make the registry the single most valuable
    target in the state.
    """

    __tablename__ = "vms_instances"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor: Mapped[str] = mapped_column(String(32), nullable=False)
    adapter_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text)
    credentials_ref: Mapped[str | None] = mapped_column(Text)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CameraStatus.UNKNOWN.value
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    cameras: Mapped[list[Camera]] = relationship(back_populates="vms")

    def __repr__(self) -> str:
        return f"<VmsInstance {self.name} vendor={self.vendor}>"


class Camera(Base):
    """One camera in the statewide estate."""

    __tablename__ = "cameras"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    camera_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL")
    )
    vms_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vms_instances.id", ondelete="SET NULL")
    )

    district: Mapped[str | None] = mapped_column(String(64))
    city: Mapped[str | None] = mapped_column(String(64))
    junction: Mapped[str | None] = mapped_column(String(128))
    address: Mapped[str | None] = mapped_column(Text)

    #: The arterial this camera watches, e.g. "Ashram Road".
    #:
    #: Traffic analytics reports per corridor, so this is a grouping key and not
    #: decoration. It exists as a column rather than being parsed out of
    #: `camera_code` or `tags` because both encode it only by convention: the
    #: code stops saying it the moment a camera is renamed (the demo fleet is
    #: `CAM-DEMO-01..03` on Ashram Road), and in `tags` it is one unprefixed
    #: slug among several. NULL means the camera is on no corridor we model,
    #: which analytics reports as such rather than attributing it to a road.
    corridor: Mapped[str | None] = mapped_column(String(64))

    # WGS-84. Geography (not Geometry) so distance maths is in metres on a
    # spheroid — Gujarat spans ~700 km, where planar approximations drift.
    location: Mapped[str] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )

    # Direction the camera faces, 0-359. Used by the correlator to reject
    # implausible route hops: a vehicle cannot be travelling east past a
    # camera that only sees westbound traffic.
    heading_deg: Mapped[int | None] = mapped_column(SmallInteger)

    camera_type: Mapped[str | None] = mapped_column(String(16))
    protocol: Mapped[str | None] = mapped_column(String(16))
    stream_url: Mapped[str | None] = mapped_column(Text)
    sub_stream_url: Mapped[str | None] = mapped_column(Text)
    #: Bare filename of a recorded clip for the simulator to replay, or None to
    #: let it deal one per corridor. Resolved against `SIM_VIDEO_DIR` by the
    #: simulator and validated to a basename by `CameraCreate`; deliberately
    #: not a path and deliberately not `stream_url` (see migration 0008).
    source_file: Mapped[str | None] = mapped_column(Text)
    resolution: Mapped[str | None] = mapped_column(String(16))
    fps: Mapped[int | None] = mapped_column(SmallInteger)

    anpr_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CameraStatus.UNKNOWN.value
    )
    installed_on: Mapped[date | None] = mapped_column(Date)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    department: Mapped[Department | None] = relationship(back_populates="cameras")
    vms: Mapped[VmsInstance | None] = relationship(back_populates="cameras")

    __table_args__ = (
        # GiST over geography powers /cameras/nearby and map-viewport queries.
        Index("ix_cameras_location", "location", postgresql_using="gist"),
        # The map's primary filter is department + status.
        Index("ix_cameras_department_status", "department_id", "status"),
        Index("ix_cameras_district", "district"),
        Index("ix_cameras_status", "status"),
        # Analytics groups flow, speed and hotspots by corridor.
        Index("ix_cameras_corridor", "corridor"),
    )

    def __repr__(self) -> str:
        return f"<Camera {self.camera_code} {self.status}>"


class CameraHealth(Base):
    """Time-series health samples. TimescaleDB hypertable, chunked by ``ts``.

    At 80,000 cameras probed every 30s this is ~230M rows/day, which is exactly
    why it is a hypertable with a retention policy rather than a plain table.
    """

    __tablename__ = "camera_health"

    camera_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cameras.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, nullable=False)

    reachable: Mapped[bool | None] = mapped_column(Boolean)
    fps_actual: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    bitrate_kbps: Mapped[int | None] = mapped_column(Integer)
    frame_drop_pct: Mapped[float | None] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_camera_health_camera_ts", "camera_id", ts.desc()),)

    def __repr__(self) -> str:
        return f"<CameraHealth {self.camera_id} @{self.ts} reachable={self.reachable}>"
