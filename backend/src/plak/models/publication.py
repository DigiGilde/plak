"""Publication models: sites, versions, previews, access_keys, invitees."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, false, func, true
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from plak.models.base import ACCESS_BASE_ENUM, Base, IDMixin


class VersionTarget(enum.StrEnum):
    LIVE = "live"
    PREVIEW = "preview"


class KeyStatus(enum.StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


class Site(IDMixin, Base):
    __tablename__ = "sites"

    group_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    slug: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    # Base plus extras: the base answers "who can view this site", the two
    # booleans widen it. They never narrow it, so on base `public` they change
    # nothing.
    access_base: Mapped[str] = mapped_column(ACCESS_BASE_ENUM, nullable=False)
    access_keys: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    access_invitees: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    # Whether published content may load scripts, styles and fonts from the
    # fixed set of external hosts in serving/response.py.
    external_sources: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
    # Whether published content is served with the CSP sandbox that gives it an
    # opaque origin (serving/response.py). Off means it shares an origin with
    # every other site on the content hostname.
    sandbox: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=true(), default=True
    )
    # use_alter: breaks the circular dependency with versions (versions.site_id -> sites.id).
    live_version_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="SET NULL", use_alter=True, name="fk_sites_live_version_id"),
        nullable=True,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("group_id", "slug", name="uq_sites_group_slug"),)


class Version(IDMixin, Base):
    """target=live or preview; the origin is exactly one of member_id (a member,
    in the SPA or through the CLI) or ci_repository (a CI deploy, e.g.
    "github.com/owner/repo") - enforced by ck_versions_origin."""

    __tablename__ = "versions"

    site_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    target: Mapped[VersionTarget] = mapped_column(
        SAEnum(VersionTarget, name="version_target", values_callable=lambda c: [member.value for member in c]),
        nullable=False,
    )
    storage_ref: Mapped[str] = mapped_column(String, nullable=False)
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    ci_repository: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "(member_id IS NOT NULL AND ci_repository IS NULL) OR "
            "(member_id IS NULL AND ci_repository IS NOT NULL)",
            name="ck_versions_origin",
        ),
    )


class Preview(IDMixin, Base):
    __tablename__ = "previews"

    site_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    ref: Mapped[str] = mapped_column(String, nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("versions.id", ondelete="CASCADE"), nullable=False
    )
    # All three together or none: an override is one whole policy, never a
    # base borrowed from the preview with extras borrowed from the site.
    access_base_override: Mapped[str | None] = mapped_column(ACCESS_BASE_ENUM, nullable=True)
    access_keys_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    access_invitees_override: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("site_id", "ref", name="uq_previews_site_ref"),
        CheckConstraint(
            "(access_base_override IS NULL AND access_keys_override IS NULL "
            "AND access_invitees_override IS NULL) OR "
            "(access_base_override IS NOT NULL AND access_keys_override IS NOT NULL "
            "AND access_invitees_override IS NOT NULL)",
            name="ck_previews_access_override",
        ),
    )


class Invitee(IDMixin, Base):
    __tablename__ = "invitees"

    site_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    identifier: Mapped[str] = mapped_column(String, nullable=False)
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("site_id", "identifier", name="uq_invitees_site_identifier"),
        CheckConstraint("identifier = lower(identifier)", name="ck_invitees_identifier_lowercase"),
    )


class AccessKey(IDMixin, Base):
    __tablename__ = "access_keys"

    site_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    selector: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    verifier_hash: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[KeyStatus] = mapped_column(
        SAEnum(KeyStatus, name="key_status", values_callable=lambda c: [member.value for member in c]),
        nullable=False,
        default=KeyStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
