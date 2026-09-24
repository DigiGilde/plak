"""Audit log model: append-only through triggers and account separation."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, LargeBinary, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from plak.models.base import Base, IDMixin


class ActorKind(enum.StrEnum):
    MEMBER = "member"
    CI = "ci"
    SYSTEM = "system"
    ANONYMOUS = "anonymous"


class AuditLogEntry(IDMixin, Base):
    """Rows are never updated or deleted: the migration installs triggers that refuse
    UPDATE and DELETE on this table. They hold for every session on the one database
    account Plak has, including the app's own, so an owner who disables them gets past
    them; `docs/audit-log.md` says what that costs.

    The chain columns (`chain_shard`, `chain_seq`, `chain_hash`) are deliberately not
    mapped here: they are written by the BEFORE INSERT trigger and read by
    `audit/chain.py`, and a mapping would invite the application to supply them.
    `occurred_at` is overwritten by that same trigger, whatever the INSERT carried.
    """

    __tablename__ = "audit_log_entries"

    actor_kind: Mapped[ActorKind] = mapped_column(
        SAEnum(ActorKind, name="actor_kind", values_callable=lambda c: [member.value for member in c]),
        nullable=False,
    )
    actor_pseudonym: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    result: Mapped[str] = mapped_column(String, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String, nullable=True)
    refs: Mapped[dict | None] = mapped_column(postgresql.JSONB, nullable=True)
    ip_truncated: Mapped[str | None] = mapped_column(String, nullable=True)
    # AES-256-GCM (audit/ip_crypto.py) under PLAK_AUDIT_IP_KEY, its own key
    # separate from the pepper: reveal is a deliberate, audited act
    # (audit_ip_reveal), never a byproduct of reading ip_truncated.
    ip_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ContentViewer(IDMixin, Base):
    """SSO subjects seen on the content host, kept only to make the audit log
    traceable to a person: a content-only viewer never gets
    a `members` row, so without this table `actor-identity` could not resolve
    them at all.

    Upserted on every successful content-host SSO login; purged by
    `audit/retention.py` once `last_seen_at` is more than 90 days old, the
    same term as `content_access`/`allowed`.
    """

    __tablename__ = "content_viewers"

    sso_subject: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Nullable: a login is always recorded (the sub is what makes someone
    # traceable at all), even when the IdP sends no email claim.
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    # An unverified email is not proof the viewer controls that address; the
    # forward lookup (api/admin.py) only matches on a verified one.
    email_verified: Mapped[bool] = mapped_column(nullable=False, default=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
