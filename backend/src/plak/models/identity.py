"""Identity models: members, groups, group_members, site_members."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, false, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from plak.constants import RESERVED_SLUGS, Role
from plak.models.base import ACCESS_BASE_ENUM, ROLE_ENUM, Base, IDMixin


class PlatformRole(enum.StrEnum):
    ADMIN = "admin"
    MEMBER = "member"


class MemberStatus(enum.StrEnum):
    ACTIVE = "active"
    DEACTIVATED = "deactivated"


class MemberLanguage(enum.StrEnum):
    """The interface language a member picked for themselves.

    The values are the ones plak.i18n supports; test_identity.py holds the two
    together. "Follow my browser" is not a value here but the absence of one,
    see Member.language.
    """

    NL = "nl"
    EN = "en"


# The same reserved words the application applies when creating a group slug,
# repeated here as a defence-in-depth CHECK.
_RESERVED_SLUGS_SQL = ", ".join(f"'{slug}'" for slug in sorted(RESERVED_SLUGS))


class Member(IDMixin, Base):
    """Comes into being on the first visit to the admin host; logging in alone creates no member."""

    __tablename__ = "members"

    sso_subject: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    platform_role: Mapped[PlatformRole] = mapped_column(
        SAEnum(PlatformRole, name="platform_role", values_callable=lambda c: [member.value for member in c]),
        nullable=False,
        default=PlatformRole.MEMBER,
    )
    status: Mapped[MemberStatus] = mapped_column(
        SAEnum(MemberStatus, name="member_status", values_callable=lambda c: [member.value for member in c]),
        nullable=False,
        default=MemberStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # NULL is "follow my browser", and is the state every member starts in.
    # A third enum value would be a language that is not a language: every
    # reader would have to map it onto an Accept-Language negotiation anyway,
    # and nothing could then ask the column what language this member reads.
    # Absence of a choice is genuinely absence, so NULL says it.
    language: Mapped[MemberLanguage | None] = mapped_column(
        SAEnum(MemberLanguage, name="member_language", values_callable=lambda c: [member.value for member in c]),
        nullable=True,
        default=None,
    )

    __table_args__ = (CheckConstraint("email = lower(email)", name="ck_members_email_lowercase"),)


class Group(IDMixin, Base):
    __tablename__ = "groups"

    slug: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # What a new site in this group starts on; existing sites never follow.
    default_access_base: Mapped[str] = mapped_column(ACCESS_BASE_ENUM, nullable=False)
    default_access_keys: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )
    default_access_invitees: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), default=False
    )

    __table_args__ = (
        CheckConstraint(f"slug NOT IN ({_RESERVED_SLUGS_SQL})", name="ck_groups_slug_reserved"),
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    # Deliberately no default: whoever adds someone to a group picks the role,
    # instead of silently getting the strongest one thrown in.
    role: Mapped[Role] = mapped_column(ROLE_ENUM, nullable=False)


class SiteMember(Base):
    """Adds to the group role and never subtracts from it: the effective role
    on a site is the widest of the two. A site role never grants group
    authority.
    """

    __tablename__ = "site_members"

    site_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[Role] = mapped_column(ROLE_ENUM, nullable=False)
    added_by: Mapped[uuid.UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
