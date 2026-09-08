"""Request and response models for authentication and user administration."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress

from app.models.enums import Role


class LoginRequest(BaseModel):
    """Credentials presented at /auth/login."""

    username: str = Field(min_length=1, max_length=64, examples=["admin"])
    password: str = Field(min_length=1, max_length=256, examples=["NagarNetra@2026"])


class TokenPair(BaseModel):
    """Issued on successful login or refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type literal, not a credential
    expires_in: int = Field(description="Access token lifetime in seconds")


class RefreshRequest(BaseModel):
    refresh_token: str


class UserProfile(BaseModel):
    """The authenticated caller's own record, returned by /auth/me."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    full_name: str | None = None
    role: str
    department_id: uuid.UUID | None = None
    is_active: bool
    last_login_at: datetime | None = None
    # Surfaced so the interface can stop offering what the caller cannot do.
    # The API enforces every one of these regardless; this is for usability.
    must_change_password: bool = False
    permissions: list[str] = Field(
        default_factory=list,
        description="Effective grants for this role, so the UI can hide what the user cannot do",
    )


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(
        min_length=12,
        max_length=256,
        description="Minimum 12 characters — this is police infrastructure",
    )


class MessageResponse(BaseModel):
    detail: str


# ── User administration ───────────────────────────────────────────────


class UserCreate(BaseModel):
    """A new account. Created by an administrator, never self-service.

    There is no public registration: every account on this platform belongs to
    a named officer whose actions are attributable, and self-service signup
    would break that at the root.
    """

    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    password: str = Field(min_length=12, max_length=256)
    full_name: str | None = Field(default=None, max_length=200)
    role: Role
    department_id: uuid.UUID | None = None
    # A password chosen by an administrator is known to the administrator, so
    # the account is unusable for attribution until the holder changes it.
    must_change_password: bool = True


class UserUpdate(BaseModel):
    """Amend an account. Username is immutable — it is what the audit log says."""

    full_name: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    department_id: uuid.UUID | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    """An administrator setting somebody else's password."""

    new_password: str = Field(min_length=12, max_length=256)
    must_change_password: bool = True


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    full_name: str | None
    role: str
    department_id: uuid.UUID | None
    is_active: bool
    must_change_password: bool = False
    last_login_at: datetime | None
    created_at: datetime | None = None


class UserPage(BaseModel):
    items: list[UserOut]
    total: int


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime
    user_id: uuid.UUID | None
    username: str | None
    role: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    # The column is INET, so asyncpg hands back an IPv4Address/IPv6Address.
    # Serialised as a string because that is what a reviewer reads and what a
    # CSV export needs; the network type buys nothing past the database.
    ip: IPvAnyAddress | str | None
    result: str
    params: dict[str, Any] | None


class AuditPage(BaseModel):
    items: list[AuditEntry]
    total: int
    limit: int
    offset: int
