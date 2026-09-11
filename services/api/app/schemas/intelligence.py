"""Request and response shapes for detections, the watchlist and alerts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AlertStatus, Priority
from app.services import evidence as evidence_service


def normalise_plate(value: str) -> str:
    """Uppercase and strip separators.

    Watchlist entries are matched against normalised detections, so an entry
    typed as "GJ 03 AB 1234" must be stored the way it will be compared —
    otherwise it silently never matches, which is the worst possible failure
    for a BOLO.
    """
    return "".join(ch for ch in value.upper() if ch.isalnum())


class WatchlistCreate(BaseModel):
    plate: str = Field(min_length=4, max_length=24)
    category: str = Field(max_length=16)
    priority: Priority = Priority.HIGH
    case_ref: str | None = Field(default=None, max_length=64)
    remarks: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @field_validator("plate")
    @classmethod
    def normalise(cls, value: str) -> str:
        plate = normalise_plate(value)
        if len(plate) < 4:
            raise ValueError("plate has too few alphanumeric characters to match on")
        return plate

    @field_validator("valid_to")
    @classmethod
    def check_window(cls, value: datetime | None, info: Any) -> datetime | None:
        start = info.data.get("valid_from")
        if value is not None and start is not None and value <= start:
            raise ValueError("valid_to must be after valid_from")
        return value


class WatchlistUpdate(BaseModel):
    category: str | None = Field(default=None, max_length=16)
    priority: Priority | None = None
    case_ref: str | None = Field(default=None, max_length=64)
    remarks: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    active: bool | None = None


class WatchlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plate_normalised: str
    category: str
    priority: str
    case_ref: str | None
    remarks: str | None
    valid_from: datetime | None
    valid_to: datetime | None
    active: bool
    created_at: datetime


class AlertReason(BaseModel):
    """One factor behind an alert. See `app/services/anomaly.py`.

    `factor` is a short machine-stable slug (`rare_transition`,
    `slow_transition`, `impossible_hop`) a UI can branch or group on;
    `detail` is the sentence an operator actually reads.
    """

    factor: str
    detail: str


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    alert_type: str
    priority: str
    plate_normalised: str | None
    confidence: float | None
    status: str
    camera_id: uuid.UUID | None
    detection_id: uuid.UUID | None
    watchlist_id: uuid.UUID | None
    acknowledged_by: uuid.UUID | None
    acknowledged_at: datetime | None
    notes: str | None
    #: Signed URL for the plate crop of the detection that raised this alert.
    #: The PS asks an alert to show the plate crop, and an operator confirming a
    #: blacklist hit needs to see the vehicle rather than trust a string.
    #: Resolved from the detection, because `alerts` stores no crop of its own.
    crop_url: str | None = None
    #: The explainability rule in CLAUDE.md §5: every alert should carry its
    #: reasons. Null for a watchlist hit or a camera-down alert — the match
    #: itself, and the notes field, already say why. Populated for `anomaly`.
    reasons: list[AlertReason] | None = None


class AlertTransition(BaseModel):
    """Move an alert along its lifecycle."""

    status: AlertStatus
    notes: str | None = Field(
        default=None,
        description="Why. Required when closing as a false positive, because "
        "that is the transition worth being able to review later.",
    )

    @field_validator("notes")
    @classmethod
    def require_reason(cls, value: str | None, info: Any) -> str | None:
        if info.data.get("status") == AlertStatus.FALSE_POSITIVE and not (value or "").strip():
            raise ValueError("a false positive must record why")
        return value


class AlertPage(BaseModel):
    items: list[AlertOut]
    total: int
    limit: int
    offset: int


class DetectionOut(BaseModel):
    """One sighting, with the evidence behind it.

    The evidence fields are not decoration. A plate the pipeline repaired, or
    one where the frames disagreed, is a different thing from a clean read, and
    an operator deciding whether to act on a hit needs to see which they have.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ts: datetime
    camera_id: uuid.UUID | None
    camera_code: str
    camera_name: str
    track_id: str
    vehicle_type: str | None
    plate: str | None
    plate_confidence: float | None
    detection_confidence: float | None
    grammar_valid: bool | None
    bbox: dict[str, Any] | None
    plate_bbox: dict[str, Any] | None
    crop_key: str | None
    #: Short-lived signed URL for the plate crop, or None when the detection
    #: has no crop. Signed rather than proxied so an <img> can fetch it without
    #: an Authorization header it cannot send; see app/services/evidence.py.
    #: May 404 briefly after a detection while the upload is still in flight.
    crop_url: str | None = None

    # ── evidence ──
    reads_total: int | None
    agreement: float | None
    corrected_from: str | None
    ambiguous: bool
    candidates: list[dict[str, Any]]

    @classmethod
    def from_row(cls, row: Any, camera_code: str, camera_name: str) -> DetectionOut:
        raw = row.ocr_raw or {}
        evidence = raw.get("evidence") or {}
        return cls(
            id=row.id,
            ts=row.ts,
            camera_id=row.camera_id,
            camera_code=camera_code,
            camera_name=camera_name,
            track_id=row.track_id,
            vehicle_type=row.vehicle_type,
            plate=row.plate_normalised or None,
            plate_confidence=row.plate_confidence,
            detection_confidence=row.detection_confidence,
            grammar_valid=row.is_validated,
            bbox=row.bbox,
            plate_bbox=row.plate_bbox,
            crop_key=row.crop_key,
            crop_url=evidence_service.crop_url(row.crop_key),
            reads_total=evidence.get("reads_total"),
            agreement=evidence.get("agreement"),
            corrected_from=raw.get("corrected_from"),
            ambiguous=bool(raw.get("ambiguous", False)),
            candidates=raw.get("candidates") or [],
        )


class DetectionPage(BaseModel):
    items: list[DetectionOut]
    total: int
    limit: int
    offset: int


# ── Fuzzy plate search (P8) ────────────────────────────────────────────
class WatchlistHit(BaseModel):
    """Why a search result is also a blacklist match — see
    `app/routers/vehicles.py::search_plates`."""

    category: str
    priority: Priority
    case_ref: str | None


class PlateSearchResult(BaseModel):
    """One candidate plate: how well it matches the query, and a faceted
    summary an operator can triage from without opening the full route."""

    plate_normalised: str
    #: pg_trgm trigram similarity to the query, 0-1. 1.0 means the query was
    #: found exactly; this is what lets a UI distinguish "the plate you typed"
    #: from "the plate we think you meant".
    similarity: float
    sightings: int
    cameras: int
    first_seen: datetime
    last_seen: datetime
    #: Present when this plate is on an active watchlist entry — the reason
    #: a misread plate matching a blacklisted vehicle must still surface.
    watchlist: WatchlistHit | None = None


class PlateSearchResponse(BaseModel):
    query: str
    #: The minimum trigram similarity a result had to clear. Echoed back so a
    #: UI can explain an empty result ("nothing scored above 0.30") rather
    #: than leave it looking broken.
    threshold: float
    #: True when `query` itself, normalised, is among the results at
    #: similarity 1.0 — the case where nothing needed correcting.
    exact_match: bool
    results: list[PlateSearchResult]
