"""The slug namespace: every slug a group or site has, or recently had
(migration 0005).

Triggers on `groups` and `sites` write these rows; the app reads them, and
the nightly cleanup deletes a retired one once its redirect has ended.
`retired_at` is NULL on the current slug, which `groups.slug` or `sites.slug`
also holds.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from plak.models.base import Base


class GroupSlug(Base):
    __tablename__ = "group_slugs"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SiteSlug(Base):
    """A site slug is unique within its group, so the group is part of the key."""

    __tablename__ = "site_slugs"

    group_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    slug: Mapped[str] = mapped_column(String, primary_key=True)
    site_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
