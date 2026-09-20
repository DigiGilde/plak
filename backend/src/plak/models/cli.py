"""CLI login models: the device authorization grant (RFC 8628) Plak brokers
itself, and the CLI sessions it results in.

Every secret is stored as selector plus SHA-256 hash, never in plain text.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from plak.models.base import Base, IDMixin


class CliDeviceStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class CliDeviceAuthorization(IDMixin, Base):
    """Short-lived: expires after ten minutes, deleted once exchanged, and
    swept by the cleanup job after expiry."""

    __tablename__ = "cli_device_authorizations"

    device_selector: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    device_hash: Mapped[str] = mapped_column(String, nullable=False)
    user_code_hash: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    client_name: Mapped[str | None] = mapped_column(String, nullable=True)
    ip_truncated: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[CliDeviceStatus] = mapped_column(
        SAEnum(CliDeviceStatus, name="cli_device_status", values_callable=lambda c: [member.value for member in c]),
        nullable=False,
        default=CliDeviceStatus.PENDING,
    )
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("status <> 'approved' OR member_id IS NOT NULL", name="ck_cli_device_authorizations_member"),
    )


class CliSession(IDMixin, Base):
    """One `plak login`: a rotating refresh token plus the current access
    token. `expires_at` slides with every refresh, `max_expires_at` does not."""

    __tablename__ = "cli_sessions"

    member_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), nullable=False
    )
    client_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    max_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    access_selector: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    access_hash: Mapped[str] = mapped_column(String, nullable=False)
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CliRefreshToken(IDMixin, Base):
    """Every refresh token a session ever had. The current one has
    `used_at` NULL; a used one coming back means two parties hold the chain,
    so the whole session goes (RFC 9700 4.14.2)."""

    __tablename__ = "cli_refresh_tokens"

    session_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("cli_sessions.id", ondelete="CASCADE"), nullable=False
    )
    selector: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    verifier_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
