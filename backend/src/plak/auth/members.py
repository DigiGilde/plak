"""Member administration: upsert only on a beheer visit.

Viewing never creates a member record (data minimisation); a record comes
into being only once require_active_member runs, so on management endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from plak.api.errors import ApiError
from plak.auth.sessions import Session, session_from_request
from plak.models.identity import Member, MemberStatus, PlatformRole

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Message keys in plak/messages.py; the code a client sees is what stands
# before the dot.
KEY_NOT_LOGGED_IN = "NO_SESSION.not_logged_in"
KEY_DEACTIVATED = "MEMBER_DEACTIVATED"

REASON_DEACTIVATED = "MEMBER_DEACTIVATED"
REASON_NO_SESSION = "NO_SESSION"


async def get_or_create_member(db: AsyncSession, session: Session, bootstrap_sub: str) -> Member:
    """Looks up the member for the session sub, or creates it active.

    Anyone who can complete SSO Rijk login may use Plak, so a first beheer
    visit needs no approval: the member is created active straight away. The
    bootstrap sub (PLAK_BOOTSTRAP_ADMIN_SUB) becomes admin besides, also when
    the record already existed before the bootstrap was configured
    (idempotent promotion).
    """
    is_bootstrap = bool(bootstrap_sub) and session.sub == bootstrap_sub
    # Only an address the IdP vouched for is stored: adding a member with a
    # role resolves an email to a member (api/admin.py), and an unverified
    # claim is whatever someone typed into their IdP profile.
    verified_email = (session.email or "").lower() if session.email_verified else ""
    # The display name has no verified counterpart in OIDC, so there is nothing
    # to hold it against; it is shown next to the email, never matched on.
    claimed_name = (session.name or "").strip()

    result = await db.execute(select(Member).where(Member.sso_subject == session.sub))
    member = result.scalar_one_or_none()

    if member is None:
        member = Member(
            sso_subject=session.sub,
            email=verified_email,
            name=claimed_name or None,
            platform_role=PlatformRole.ADMIN if is_bootstrap else PlatformRole.MEMBER,
            status=MemberStatus.ACTIVE,
            last_login_at=datetime.now(UTC),
        )
        db.add(member)
        try:
            await db.commit()
        except IntegrityError:
            # Two concurrent first visits: the unique on sso_subject wins,
            # we adopt the record that was created already.
            await db.rollback()
            result = await db.execute(select(Member).where(Member.sso_subject == session.sub))
            member = result.scalar_one()
        return member

    if is_bootstrap and (member.platform_role != PlatformRole.ADMIN or member.status != MemberStatus.ACTIVE):
        member.platform_role = PlatformRole.ADMIN
        member.status = MemberStatus.ACTIVE
    if not member.email and verified_email:
        member.email = verified_email
    # Unlike the email, which is only filled in where it is missing because
    # adding a member by address resolves against it, the name follows the IdP:
    # nothing else writes it, and a changed name should show.
    if claimed_name and member.name != claimed_name:
        member.name = claimed_name
    member.last_login_at = datetime.now(UTC)
    await db.commit()
    return member


async def require_active_member(request: Request) -> Member:
    """FastAPI dependency for management endpoints: requires an active member.

    The member record is upserted here (a first beheer visit creates it,
    active) and stays in place even when the 403 follows: a deactivated
    member keeps its record, so reactivation by a platform administrator
    brings back the same one.
    """
    session = session_from_request(request)
    if session is None:
        # The same code the admin API's own session check uses: a 401 with no
        # session is one condition, whichever dependency happens to spot it,
        # and the SPA should not have to tell them apart by their wording.
        raise ApiError(401, KEY_NOT_LOGGED_IN)

    session_factory = request.app.state.session_factory
    bootstrap_sub = request.app.state.settings.bootstrap_admin_sub
    async with session_factory() as db:
        member = await get_or_create_member(db, session, bootstrap_sub)

    if member.status != MemberStatus.ACTIVE:
        raise ApiError(403, KEY_DEACTIVATED)
    return member


__all__ = [
    "KEY_DEACTIVATED",
    "KEY_NOT_LOGGED_IN",
    "REASON_DEACTIVATED",
    "REASON_NO_SESSION",
    "get_or_create_member",
    "require_active_member",
]
