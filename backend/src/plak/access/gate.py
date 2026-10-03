"""Access gate: decides on view access to live content, previews and _version
views.

Rules: deny by default; every refusal becomes a neutral 404 carrying a
reason_code internally only. Access is a base level plus two extras that widen
it, so a visitor gets in when the base lets them in, OR they hand in a valid
secret link, OR they are an invitee with a
session. A visitor without a session gets a LOGIN_REDIRECT for live content
whenever logging in could grant them something, and a neutral 404 for previews
(the existence of a preview does not leak). With a preview override that
override is the policy, base and extras together. The serving layer turns
every anonymous refusal on a preview or `_version` view into the login
redirect on a top-level navigation, whatever its reason (serving/router.py),
so that answer leaks nothing either.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plak.access import keys
from plak.access.decision import (
    REASON_KEY_INVALID,
    REASON_NO_ACCESS,
    REASON_NO_LIVE_VERSION,
    REASON_PREVIEW_EXPIRED,
    REASON_UNKNOWN_GROUP,
    REASON_UNKNOWN_PREVIEW,
    REASON_UNKNOWN_SITE,
    REASON_UNKNOWN_VERSION,
    AccessDecision,
    allow,
    login_redirect,
    neutral_404,
)
from plak.constants import AccessBase, AccessPolicy
from plak.models.identity import Group, GroupMember, Member, MemberStatus, SiteMember
from plak.models.publication import Invitee, Preview, Site, Version


@dataclass(frozen=True)
class Visitor:
    """Derived from session plus request; no member lookup up front."""

    sub: str | None = None
    email: str | None = None
    email_verified: bool = False
    key_cookie: str | None = None
    key_query: str | None = None


async def decide(
    db: AsyncSession, group_slug: str, site_slug: str, visitor: Visitor
) -> AccessDecision:
    """Access decision for live content."""
    _, site, refusal = await _find_site(db, group_slug, site_slug)
    if refusal is not None:
        return refusal
    if site.live_version_id is None:
        # Also on base publiek: without a live version the site
        # does not exist to the outside world (spec §5.6).
        return neutral_404(REASON_NO_LIVE_VERSION)
    return await _assess(
        db, site, _site_access(site), site.live_version_id, visitor, login_redirect_allowed=True
    )


async def decide_preview(
    db: AsyncSession, group_slug: str, site_slug: str, ref: str, visitor: Visitor
) -> AccessDecision:
    """Access decision for preview content; the expiry check happens here
    itself, the cleanup job is only the safety net."""
    _, site, refusal = await _find_site(db, group_slug, site_slug)
    if refusal is not None:
        return refusal
    preview = await db.scalar(select(Preview).where(Preview.site_id == site.id, Preview.ref == ref))
    if preview is None:
        return neutral_404(REASON_UNKNOWN_PREVIEW)
    if preview.expires_at is not None and preview.expires_at <= datetime.now(UTC):
        return neutral_404(REASON_PREVIEW_EXPIRED)
    return await _assess(
        db, site, effective_access(site, preview), preview.version_id, visitor, login_redirect_allowed=False
    )


async def decide_version(
    db: AsyncSession, group_slug: str, site_slug: str, version_id: uuid.UUID, visitor: Visitor
) -> AccessDecision:
    """_version view: active members of the site's group only, whatever the
    site's access setting; for anyone else (also without a session) the neutral
    404, never a login redirect."""
    _, site, refusal = await _find_site(db, group_slug, site_slug)
    if refusal is not None:
        return refusal
    # Membership before the version lookup: non-members cannot probe for the
    # existence of version ids.
    if visitor.sub is None or not await _belongs_to_site(db, site, visitor.sub):
        return neutral_404(REASON_NO_ACCESS)
    version = await db.scalar(select(Version).where(Version.id == version_id, Version.site_id == site.id))
    if version is None:
        return neutral_404(REASON_UNKNOWN_VERSION)
    return allow(version.id, AccessPolicy(AccessBase.SITE_TEAM))


async def code_page_needed(db: AsyncSession, group_slug: str, site_slug: str, selector: str) -> bool:
    """Whether a `?key=` carrying the selector alone should ask for the code
    rather than get the neutral 404 (a link shared without its code).

    Everything else stays the neutral 404, or the page itself would say which
    selectors exist: group and site have to exist, the site has to be live and
    on the secret link, and the selector has to belong to a key of this site
    that is neither revoked nor expired.
    """
    _, site, refusal = await _find_site(db, group_slug, site_slug)
    if refusal is not None:
        return False
    if site.live_version_id is None or not site.access_keys:
        return False
    return await keys.selector_usable(db, site.id, selector)


async def _find_site(
    db: AsyncSession, group_slug: str, site_slug: str
) -> tuple[Group, Site, None] | tuple[None, None, AccessDecision]:
    group = await db.scalar(select(Group).where(Group.slug == group_slug))
    if group is None:
        return None, None, neutral_404(REASON_UNKNOWN_GROUP)
    site = await db.scalar(select(Site).where(Site.group_id == group.id, Site.slug == site_slug))
    if site is None:
        return None, None, neutral_404(REASON_UNKNOWN_SITE)
    return group, site, None


def effective_access(site: Site, preview: Preview | None) -> AccessPolicy:
    """Who may see the live site, or with a preview that preview: its own
    access when it has an override, else the site's."""
    if preview is not None and preview.access_base_override is not None:
        return AccessPolicy(
            AccessBase(preview.access_base_override),
            keys=bool(preview.access_keys_override),
            invitees=bool(preview.access_invitees_override),
        )
    return _site_access(site)


def _site_access(site: Site) -> AccessPolicy:
    return AccessPolicy(
        AccessBase(site.access_base), keys=bool(site.access_keys), invitees=bool(site.access_invitees)
    )


async def _assess(
    db: AsyncSession,
    site: Site,
    access: AccessPolicy,
    version_id: uuid.UUID,
    visitor: Visitor,
    *,
    login_redirect_allowed: bool,
) -> AccessDecision:
    """The decision table in one place: every way in is tried before any
    refusal is chosen, because the base and the extras are an OR and stopping
    at the first closed door would turn that into an AND."""
    if access.base is AccessBase.PUBLIC:
        return allow(version_id, access)

    key_presented = visitor.key_query is not None or visitor.key_cookie is not None
    if access.keys and key_presented:
        selector = await _valid_key_selector(db, site.id, visitor)
        if selector is not None:
            return allow(version_id, access, key_selector=selector)

    if visitor.sub is not None:
        if access.base is AccessBase.SSO:
            return allow(version_id, access)
        if access.base is AccessBase.SITE_TEAM and await _belongs_to_site(db, site, visitor.sub):
            return allow(version_id, access)
        if access.invitees and await _is_invitee(db, site.id, visitor):
            return allow(version_id, access)
        return neutral_404(REASON_NO_ACCESS)

    # Anonymous, and no secret link got them in.
    if access.login_can_help:
        if login_redirect_allowed:
            return login_redirect(access)
        return neutral_404(REASON_NO_ACCESS)
    # Only secret links could have helped here, so a key that was handed in and
    # refused is the one thing worth distinguishing from "nothing was tried".
    if access.keys and key_presented:
        return neutral_404(REASON_KEY_INVALID)
    return neutral_404(REASON_NO_ACCESS)


async def _valid_key_selector(db: AsyncSession, site_id: uuid.UUID, visitor: Visitor) -> str | None:
    if visitor.key_query is not None:
        key = await keys.verify(db, site_id, visitor.key_query)
        if key is not None:
            return key.selector
    if visitor.key_cookie is not None:
        key = await keys.validate_cookie(db, site_id, visitor.key_cookie)
        if key is not None:
            return key.selector
    return None


async def _belongs_to_site(db: AsyncSession, site: Site, sub: str) -> bool:
    """Everyone with an active role on this site: a role on its group, whatever
    that role is, or a role on the site itself.

    One predicate rather than two, because "who may manage this site" and "who
    may look at it on its narrowest logged-in setting" have to be the same set.
    Were they not, someone with a role on one site could deploy to it and then
    not look at what they deployed, not even through _version.
    """
    result = await db.scalar(
        select(Member.id)
        .outerjoin(
            GroupMember,
            (GroupMember.member_id == Member.id) & (GroupMember.group_id == site.group_id),
        )
        .outerjoin(
            SiteMember,
            (SiteMember.member_id == Member.id) & (SiteMember.site_id == site.id),
        )
        .where(
            Member.sso_subject == sub,
            Member.status == MemberStatus.ACTIVE,
            (GroupMember.member_id.is_not(None)) | (SiteMember.member_id.is_not(None)),
        )
        .limit(1)
    )
    return result is not None


async def _is_invitee(db: AsyncSession, site_id: uuid.UUID, visitor: Visitor) -> bool:
    identifiers: list[str] = []
    if visitor.sub is not None:
        # `invitees.identifier` carries a `lower(identifier)` check constraint,
        # so an invitation on an SSO subject with an uppercase character could
        # never match without lowercasing here too.
        identifiers.append(visitor.sub.lower())
    # Email match only for an address the IdP has verified.
    if visitor.email and visitor.email_verified:
        identifiers.append(visitor.email.lower())
    if not identifiers:
        return False
    result = await db.scalar(
        select(Invitee.id)
        .where(Invitee.site_id == site_id, Invitee.identifier.in_(identifiers))
        .limit(1)
    )
    return result is not None
