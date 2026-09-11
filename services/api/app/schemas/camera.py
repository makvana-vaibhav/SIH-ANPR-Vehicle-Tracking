"""Camera registry request/response models.

The registry is the statewide answer to "what cameras exist, who owns them, and
where are they" — a question Gujarat currently cannot answer in a single query.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import (
    AdapterType,
    CameraStatus,
    CameraType,
    Protocol,
    VmsVendor,
)

# Gujarat's bounding box, generously padded. A camera outside this is almost
# certainly a data-entry error (swapped lat/lon is the classic one), and
# catching it at the edge keeps nonsense off the operational map.
GUJARAT_LAT_MIN, GUJARAT_LAT_MAX = 20.0, 24.8
GUJARAT_LON_MIN, GUJARAT_LON_MAX = 68.0, 74.6

CAMERA_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9\-]{2,31}$")

Latitude = Annotated[float, Field(ge=-90, le=90, examples=[22.3039])]
Longitude = Annotated[float, Field(ge=-180, le=180, examples=[70.8022])]


class CameraBase(BaseModel):
    """Fields common to creating and updating a camera."""

    name: str = Field(min_length=1, max_length=200, examples=["Kalawad Road Junction"])
    district: str | None = Field(default=None, max_length=64, examples=["Rajkot"])
    city: str | None = Field(default=None, max_length=64, examples=["Rajkot"])
    junction: str | None = Field(default=None, max_length=128)
    #: The arterial this camera watches. Analytics groups on it.
    corridor: str | None = Field(default=None, max_length=64, examples=["Ashram Road"])
    address: str | None = None

    heading_deg: int | None = Field(
        default=None,
        ge=0,
        le=359,
        description=(
            "Compass direction the camera faces. Used by the correlator to reject "
            "implausible route hops — a vehicle cannot pass eastbound through a "
            "camera that only sees westbound traffic."
        ),
    )

    camera_type: CameraType | None = None
    protocol: Protocol | None = None
    stream_url: str | None = None
    sub_stream_url: str | None = Field(
        default=None,
        description="Lower-resolution stream, preferred for AI analysis to save bandwidth",
    )
    resolution: str | None = Field(default=None, max_length=16, examples=["1920x1080"])
    fps: int | None = Field(default=None, ge=1, le=120)
    anpr_enabled: bool = True
    installed_on: date | None = None
    tags: list[str] | None = None

    @field_validator("resolution")
    @classmethod
    def _validate_resolution(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not re.fullmatch(r"\d{3,5}x\d{3,5}", v):
            raise ValueError("resolution must look like 1920x1080")
        return v

    @field_validator("stream_url", "sub_stream_url")
    @classmethod
    def _validate_stream_url(cls, v: str | None) -> str | None:
        """Reject a credential embedded in a stream URL.

        ``rtsp://admin:password@10.0.0.5/stream`` is how camera passwords leak
        into databases, logs, and screenshots. Credentials belong in the VMS
        record's ``credentials_ref``, which points at a secret store.
        """
        if v is None:
            return v
        if "@" in v.split("//", 1)[-1].split("/", 1)[0]:
            raise ValueError(
                "stream_url must not embed credentials; "
                "store them via the VMS instance's credentials_ref"
            )
        return v


class CameraCreate(CameraBase):
    """Payload for registering a single camera."""

    camera_code: str = Field(
        min_length=3,
        max_length=32,
        examples=["CAM-00001"],
        description="Unique estate-wide identifier",
    )
    lat: Latitude
    lon: Longitude
    department_code: str | None = Field(default=None, description="Owning department, e.g. POLICE")
    vms_name: str | None = Field(default=None, description="Federated VMS instance name")

    @field_validator("camera_code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        v = v.strip().upper()
        if not CAMERA_CODE_PATTERN.fullmatch(v):
            raise ValueError("camera_code must be 3-32 chars, uppercase alphanumerics and hyphens")
        return v

    @model_validator(mode="after")
    def _check_within_gujarat(self) -> CameraCreate:
        """Catch swapped or mistyped coordinates at the edge."""
        if not (GUJARAT_LAT_MIN <= self.lat <= GUJARAT_LAT_MAX) or not (
            GUJARAT_LON_MIN <= self.lon <= GUJARAT_LON_MAX
        ):
            raise ValueError(
                f"({self.lat}, {self.lon}) is outside Gujarat "
                f"(lat {GUJARAT_LAT_MIN}-{GUJARAT_LAT_MAX}, "
                f"lon {GUJARAT_LON_MIN}-{GUJARAT_LON_MAX}). "
                "Check the values are not swapped."
            )
        return self


class CameraUpdate(BaseModel):
    """Partial update. Every field optional; omitted fields are left alone."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    district: str | None = None
    city: str | None = None
    junction: str | None = None
    corridor: str | None = None
    address: str | None = None
    lat: Latitude | None = None
    lon: Longitude | None = None
    heading_deg: int | None = Field(default=None, ge=0, le=359)
    camera_type: CameraType | None = None
    protocol: Protocol | None = None
    stream_url: str | None = None
    sub_stream_url: str | None = None
    resolution: str | None = None
    fps: int | None = Field(default=None, ge=1, le=120)
    anpr_enabled: bool | None = None
    status: CameraStatus | None = None
    installed_on: date | None = None
    tags: list[str] | None = None
    department_code: str | None = None

    @model_validator(mode="after")
    def _coordinates_move_together(self) -> CameraUpdate:
        """Moving a camera requires both coordinates, never one."""
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be provided together")
        return self


class CameraOut(BaseModel):
    """A camera as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    camera_code: str
    name: str
    department_id: uuid.UUID | None = None
    department_code: str | None = None
    department_name: str | None = None
    vms_id: uuid.UUID | None = None
    vms_name: str | None = None
    vms_vendor: str | None = None
    district: str | None = None
    city: str | None = None
    junction: str | None = None
    corridor: str | None = None
    address: str | None = None
    lat: float
    lon: float
    heading_deg: int | None = None
    camera_type: str | None = None
    protocol: str | None = None
    resolution: str | None = None
    fps: int | None = None
    anpr_enabled: bool
    status: str
    installed_on: date | None = None
    tags: list[str] | None = None

    #: Whether a video source is configured — **not** the source itself.
    #:
    #: `stream_url` is deliberately absent from this model and must stay
    #: absent: a federated camera's URL carries the credentials for the
    #: organisers' grid, and returning it would hand every operator's browser
    #: a password. But an administrator still needs to know whether a camera
    #: has a source at all, because one without a source can never be watched
    #: or analysed, and that distinction is the whole reason the registry no
    #: longer holds 250 cameras that could do neither.
    has_stream: bool = False
    created_at: datetime
    updated_at: datetime
    # Only populated by /nearby.
    distance_km: float | None = None

    # stream_url is deliberately absent: a viewing URL is issued per request as
    # a short-lived signed token by /cameras/{id}/stream (Phase 4), and that
    # issuance is audited. Returning raw stream URLs in a list response would
    # hand out unaudited access to every feed in the estate.


class CameraPage(BaseModel):
    """A page of cameras. No endpoint returns an unbounded list."""

    items: list[CameraOut]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class FleetSummary(BaseModel):
    """Rollup for the dashboard KPI strip."""

    total: int
    by_status: dict[str, int]
    by_department: dict[str, int]
    by_district: dict[str, int]
    anpr_enabled: int


# ── GeoJSON ───────────────────────────────────────────────────────────
# RFC 7946 shapes, so MapLibre can consume the response directly with no
# client-side transformation.


class GeoJSONGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float] = Field(description="[longitude, latitude]")


class GeoJSONFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeoJSONGeometry
    properties: dict[str, Any]
    id: str | None = None


class GeoJSONFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GeoJSONFeature]
    # Non-standard but harmless extras the map UI uses for its legend.
    properties: dict[str, Any] = Field(default_factory=dict)


# ── Bulk onboarding ───────────────────────────────────────────────────


class BulkRowError(BaseModel):
    """One rejected CSV row, reported back with enough detail to fix it."""

    row: int = Field(description="1-based row number in the uploaded file")
    camera_code: str | None = None
    errors: list[str]


class BulkUploadResult(BaseModel):
    """Outcome of a CSV bulk upload.

    Valid rows are committed even when others fail: a department onboarding
    4,000 cameras should not lose the 3,997 good ones because three had a typo.
    Failures come back row-by-row so they can be corrected and re-submitted.
    """

    total_rows: int
    created: int
    updated: int
    skipped: int
    failed: int
    errors: list[BulkRowError]
    dry_run: bool = False


class VmsInstanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    vendor: str
    adapter_type: str
    base_url: str | None = None
    status: str
    last_sync_at: datetime | None = None
    camera_count: int = 0
    # credentials_ref is never serialised — it is a pointer into a secret
    # store, and exposing even the pointer narrows an attacker's search.


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    contact_email: str | None = None
    camera_count: int = 0


class VendorEnumOut(BaseModel):
    """Vocabularies, so the UI populates dropdowns from the server."""

    departments: list[str]
    vendors: list[VmsVendor]
    adapter_types: list[AdapterType]
    camera_types: list[CameraType]
    protocols: list[Protocol]
    statuses: list[CameraStatus]
