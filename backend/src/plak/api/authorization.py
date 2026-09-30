"""Role checks for the admin API: a route demands a minimum role.

Same shape as the existing `_require_*` helpers in api/admin.py: a call at the
start of the route that returns nothing and otherwise raises a 403. The role
computation itself lives in access/roles.py, so that the API and the access
gate use the same answer.

There is deliberately no platformbeheerder bypass here: the platformbeheerder
manages people and groups, not sites, and gets into a group by visibly
giving themselves a group role.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from plak.access import roles
from plak.api.errors import ApiError
from plak.constants import Role
from plak.models.identity import Member
from plak.models.publication import Site

REASON_INSUFFICIENT_ROLE = "INSUFFICIENT_ROLE"


def insufficient_role_params(minimum: Role) -> dict[str, object]:
    """The role names double as the UI names, so the enum value can go into the
    message as is, in either language."""
    return {"role": minimum.value}


async def require_group_role(db: AsyncSession, member: Member, group_id: uuid.UUID, minimum: Role) -> None:
    """Demands a group role of at least `minimum`; a site role never counts here."""
    role = await roles.group_role(db, group_id, member.id)
    if not roles.at_least(role, minimum):
        raise ApiError(403, REASON_INSUFFICIENT_ROLE, params=insufficient_role_params(minimum))


async def require_site_role(db: AsyncSession, member: Member, site: Site, minimum: Role) -> None:
    """Demands an effective site role of at least `minimum`: the widest of
    group role and site role."""
    role = await roles.effective_site_role(db, site, member.id)
    if not roles.at_least(role, minimum):
        raise ApiError(403, REASON_INSUFFICIENT_ROLE, params=insufficient_role_params(minimum))


__all__ = [
    "REASON_INSUFFICIENT_ROLE",
    "insufficient_role_params",
    "require_group_role",
    "require_site_role",
]
