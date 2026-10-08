"""Where an old address of a site leads now (spec 7.7).

Serving asks only after the access gate found no group or no site at the
requested slugs, so ordinary traffic costs nothing extra. Group and site are
then looked up as current or old slugs (migration 0005); an old one counts
only while it still redirects (slug_window.py). The gate itself never learns
of old slugs: serving lets it decide again at the current address.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plak.models.identity import Group
from plak.models.publication import Site
from plak.models.slugs import GroupSlug, SiteSlug
from plak.slug_window import redirect_ends_at, still_redirects


@dataclass(frozen=True)
class CurrentAddress:
    """The current slugs of a site asked for at an old address, and the
    moment the first of the old slugs used stops redirecting."""

    group: str
    site: str
    ends_at: datetime


async def current_address(db: AsyncSession, group_slug: str, site_slug: str, now: datetime) -> CurrentAddress | None:
    """None when the slugs lead nowhere, or to a site at its current address."""
    group = (
        await db.execute(
            select(Group, GroupSlug.retired_at)
            .join(GroupSlug, GroupSlug.group_id == Group.id)
            .where(GroupSlug.slug == group_slug)
        )
    ).one_or_none()
    if group is None:
        return None
    site = (
        await db.execute(
            select(Site, SiteSlug.retired_at)
            .join(SiteSlug, SiteSlug.site_id == Site.id)
            .where(SiteSlug.group_id == group.Group.id, SiteSlug.slug == site_slug)
        )
    ).one_or_none()
    if site is None:
        return None
    retired = [retired_at for retired_at in (group.retired_at, site.retired_at) if retired_at is not None]
    if not retired or not all(still_redirects(retired_at, now) for retired_at in retired):
        return None
    return CurrentAddress(group.Group.slug, site.Site.slug, min(redirect_ends_at(retired_at) for retired_at in retired))


def relocate(path: str, group_slug: str, site_slug: str, address: CurrentAddress) -> str:
    """`path`, as sent and starting with `/{group_slug}/{site_slug}`, with
    those two segments replaced by the current ones. Old slugs are plain
    slugs, so the sent path holds them as they are."""
    return f"/{quote(address.group)}/{quote(address.site)}{path[len(f'/{group_slug}/{site_slug}') :]}"
