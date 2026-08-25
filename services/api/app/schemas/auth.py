"""Request and response models for authentication."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """Credentials presented at /auth/login."""

    username: str = Field(min_length=1, max_length=64, examples=["admin"])
    password: str = Field(min_length=1, max_length=256, examples=["Sentinel@2026"])


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
