"""Shared declarative base and the two shared enum types.

The access base is a single PostgreSQL enum type, used on
groups.default_access_base, sites.access_base and
previews.access_base_override. All three columns refer to the same Enum
instance (ACCESS_BASE_ENUM), so the column definitions cannot drift apart.
The two extras beside it (secret links, invitees) are plain booleans on those
same three tables.

For the same reason role is a single enum type spanning two levels
(group_members.role and site_members.role): a group role and a site role are
compared against each other ("the widest wins"), and two separate types would
let that pass silently.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from plak.constants import AccessBase, Role


class Base(DeclarativeBase):
    pass


class IDMixin:
    """Standard UUID primary key, generated client-side (no DB extension required)."""

    id: Mapped[uuid.UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


ACCESS_BASE_ENUM = SAEnum(
    AccessBase,
    name="access_base",
    values_callable=lambda enumcls: [member.value for member in enumcls],
)

ROLE_ENUM = SAEnum(
    Role,
    name="role",
    values_callable=lambda enumcls: [member.value for member in enumcls],
)
