"""Request and response shapes for the watchlist and alerts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import AlertStatus, Priority


def _normalise_plate(value: str) -> str:
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
        plate = _normalise_plate(value)
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
