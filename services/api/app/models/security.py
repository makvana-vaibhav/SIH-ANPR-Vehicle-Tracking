"""Users and the audit trail.

The audit trail is a product feature, not plumbing. This is a surveillance
platform operated by a police force: being able to answer *who looked at what,
and when* is a requirement, and `audit_log` is the table we point at when asked.
Retention is governed by ``RETENTION_AUDIT_DAYS`` (default 5 years, longer than
detection data) — see docs/SECURITY.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class User(Base):
    """An operator, analyst, supervisor, auditor, or machine client.

    Passwords are argon2id hashes (see ``app/core/security.py``); the plaintext
    never exists outside the request that set it.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))

    # Role drives the RBAC matrix in app/core/rbac.py.
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # True when an administrator chose this password. Until the holder replaces
    # it, at least two people know it, so nothing the account does can be
    # attributed to one person — which is the whole point of the audit log.
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )

    # Departmental scoping: a municipal operator should not browse the police
    # estate. Enforced at the query layer from Phase 2 onward.
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="SET NULL")
    )

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Reserved for TOTP enrolment. Required for admin accounts in production
    # (docs/SECURITY.md); not enforced in the local demo profile.
    mfa_secret: Mapped[str | None] = mapped_column(Text)

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
        Index("ix_users_role", "role"),
        Index("ix_users_department", "department_id"),
    )

    def __repr__(self) -> str:
        return f"<User {self.username} role={self.role}>"


class AuditLog(Base):
    """One row per auditable action.

    Written for every mutating request and every search (middleware), plus
    explicit rows for the three actions we promise to record: plate search,
    camera stream open, and watchlist mutation.

    Identity is denormalised (``username``, ``role`` stored alongside
    ``user_id``) on purpose: the audit trail must remain readable years later
    even if the user record is renamed, reassigned to another department, or
    deleted. A foreign key would either block that deletion or null the
    evidence.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Deliberately not a ForeignKey — see the class docstring.
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    username: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str | None] = mapped_column(String(32))

    # Dotted action name: search.plate, camera.view, watchlist.create,
    # export.report, auth.login, auth.login_failed.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[str | None] = mapped_column(String(128))

    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)

    # Query parameters and request body, with credentials scrubbed before
    # storage (app/services/audit.py).
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    result: Mapped[str] = mapped_column(String(16), nullable=False, default="success")

    __table_args__ = (
        # Audit review is overwhelmingly "most recent first", optionally
        # narrowed to one user or one kind of action.
        Index("ix_audit_ts", ts.desc()),
        Index("ix_audit_user_ts", "user_id", ts.desc()),
        Index("ix_audit_action_ts", "action", ts.desc()),
        Index("ix_audit_resource", "resource_type", "resource_id"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} by={self.username} at={self.ts}>"
