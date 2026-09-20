"""Role predicate: the single answer to "which role does this member have here".

The effective role on a site is the widest of the
group role on that site's group and the site role on that site. No
site role lowers a group role, and a site role never grants group
authority: `group_role` looks at `group_members` only.

This module does no HTTP and raises no errors; the management API turns the
answer into a 403 (api/authorization.py) and the access gate into a neutral
404.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plak.constants import ROLE_RANK, Role
from plak.models.identity import GroupMember, SiteMember
from plak.models.publication import Site


def at_least(role: Role | None, minimum: Role) -> bool:
    """Is `role` at least as wide as `minimum`? No role is never enough."""
    return role is not None and ROLE_RANK[role] >= ROLE_RANK[minimum]


def widest(*roles: Role | None) -> Role | None:
    """The widest of the given roles; None when there is no role at all."""
    present = [role for role in roles if role is not None]
    if not present:
        return None
    return max(present, key=lambda role: ROLE_RANK[role])


async def group_role(db: AsyncSession, group_id: uuid.UUID, member_id: uuid.UUID) -> Role | None:
    """This member's role on this group, or None when it is not a group member."""
    return await db.scalar(
        select(GroupMember.role).where(GroupMember.group_id == group_id, GroupMember.member_id == member_id)
    )


async def site_role(db: AsyncSession, site_id: uuid.UUID, member_id: uuid.UUID) -> Role | None:
    """This member's role on this one site, independent of its group role."""
    return await db.scalar(
        select(SiteMember.role).where(SiteMember.site_id == site_id, SiteMember.member_id == member_id)
    )


async def effective_site_role(db: AsyncSession, site: Site, member_id: uuid.UUID) -> Role | None:
    """The widest of group role and site role; None when the member has neither.

    The site supplies its own group, so a caller cannot mix up a site and
    a group.
    """
    return widest(
        await group_role(db, site.group_id, member_id),
        await site_role(db, site.id, member_id),
    )


__all__ = ["at_least", "effective_site_role", "group_role", "site_role", "widest"]
