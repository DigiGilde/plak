"""CLI login (`plak login`): an OAuth 2.0 device authorization grant
(RFC 8628) that Plak runs itself, on top of the admin SSO login.

The CLI asks for a device code and a user code, the member approves the user
code in the admin SPA with a fresh session, and the CLI trades its device
code for a CLI session: a one-hour access token plus a refresh token that
rotates on every use. A refresh token that comes back after it was rotated
means two parties hold the chain, so the whole session is revoked.

Every secret (device code, access token, refresh token) is `<prefix>_<selector>_<secret>`
and stored as its selector plus the SHA-256 of the whole value, compared in
constant time with a dummy hash for unknown selectors (the access_keys
pattern). User codes are short and meant to be typed; they are stored as a
hash too, looked up by that hash, and only valid for ten minutes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plak import i18n, messages
from plak.messages import Msg
from plak.models.cli import CliDeviceAuthorization, CliDeviceStatus, CliRefreshToken, CliSession
from plak.models.identity import Member, MemberStatus

DEVICE_CODE_PREFIX = "plakdc"
ACCESS_TOKEN_PREFIX = "plakcli"  # noqa: S105 - fixed token format prefix, not a secret
REFRESH_TOKEN_PREFIX = "plakclr"  # noqa: S105 - fixed token format prefix, not a secret
SELECTOR_LENGTH = 16
SECRET_LENGTH = 64

DEVICE_CODE_TTL = timedelta(minutes=10)
POLL_INTERVAL_S = 5
# A client polling on the dot can land a hair early through network jitter.
POLL_TOLERANCE_S = 1
ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_IDLE_TTL = timedelta(days=30)
# Two refreshes racing with the same token (a CLI retrying after a timeout,
# two commands at once) are not theft: the loser gets INVALID_GRANT and the
# session survives, as long as the token it presented is the one rotated
# away just now.
REFRESH_REUSE_GRACE = timedelta(seconds=10)
SESSION_MAX_TTL = timedelta(days=90)

USER_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
USER_CODE_LENGTH = 8
CLIENT_NAME_MAX_LENGTH = 100

# RFC 8628 section 3.5 error codes, in the API's SCREAMING_SNAKE spelling.
AUTHORIZATION_PENDING = "AUTHORIZATION_PENDING"
SLOW_DOWN = "SLOW_DOWN"
EXPIRED_TOKEN = "EXPIRED_TOKEN"  # noqa: S105 - an error code, not a secret
ACCESS_DENIED = "ACCESS_DENIED"
INVALID_GRANT = "INVALID_GRANT"


class GrantError(Exception):
    """The token endpoint refuses; `code` is one of the five above, and the
    key names the message in plak/messages.py that explains it."""

    def __init__(self, key: str, *, params: Mapping[str, object] | None = None) -> None:
        self.message = Msg(key, dict(params or {}))
        self.code = messages.code_of(key)
        super().__init__(messages.render(i18n.API_DEFAULT, self.message))


# The one message key this module names outside a raise: it reaches the
# catalogue through super().__init__ below.
KEY_REFRESH_TOKEN_REUSED = f"{INVALID_GRANT}.refresh_token_reused"


class RefreshReuseError(GrantError):
    """A rotated refresh token came back; its session is gone now."""

    def __init__(self, member_sub: str | None) -> None:
        self.member_sub = member_sub
        super().__init__(KEY_REFRESH_TOKEN_REUSED)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_DUMMY_HASH = hash_secret("plak-onbekende-cli-selector")


def _new_secret(prefix: str) -> tuple[str, str]:
    """Returns (plaintext, selector)."""
    selector = secrets.token_hex(SELECTOR_LENGTH // 2)
    secret = secrets.token_hex(SECRET_LENGTH // 2)
    return f"{prefix}_{selector}_{secret}", selector


def parse_selector(value: str, prefix: str) -> str | None:
    parts = value.split("_")
    if len(parts) != 3:
        return None
    marker, selector, secret = parts
    if marker != prefix or len(selector) != SELECTOR_LENGTH or len(secret) != SECRET_LENGTH:
        return None
    if not all(char in "0123456789abcdef" for char in selector + secret):
        return None
    return selector


def matches(plaintext: str, stored_hash: str | None) -> bool:
    """Constant-time check; hashes a dummy when there is no stored hash, so an
    unknown selector costs the same as a wrong secret."""
    result = hmac.compare_digest(hash_secret(plaintext), stored_hash or _DUMMY_HASH)
    return result and stored_hash is not None


def normalise_user_code(value: str) -> str | None:
    """`abcd-efgh`, `ABCD EFGH` and `ABCDEFGH` are one code; anything else is none."""
    compact = "".join(char for char in value.upper() if char not in "- \t")
    if len(compact) != USER_CODE_LENGTH or any(char not in USER_CODE_ALPHABET for char in compact):
        return None
    return compact


def format_user_code(compact: str) -> str:
    half = USER_CODE_LENGTH // 2
    return f"{compact[:half]}-{compact[half:]}"


def _new_user_code() -> str:
    return "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(USER_CODE_LENGTH))


def sanitise_client_name(value: str | None) -> str | None:
    """Control, format and private-use characters out (no bidi tricks or line
    breaks on the approval screen), whitespace collapsed, empty becomes None."""
    if value is None:
        return None
    kept = []
    for char in value:
        category = unicodedata.category(char)
        if category.startswith("Z") or char in "\t\n\r":
            kept.append(" ")
        elif not category.startswith("C"):
            kept.append(char)
    collapsed = " ".join("".join(kept).split())
    return collapsed[:CLIENT_NAME_MAX_LENGTH] or None


@dataclass(frozen=True)
class NewDeviceAuthorization:
    authorization: CliDeviceAuthorization
    device_code: str
    user_code: str


@dataclass(frozen=True)
class IssuedTokens:
    session: CliSession
    member: Member
    access_token: str
    refresh_token: str


def _now() -> datetime:
    return datetime.now(UTC)


async def create_device_authorization(
    db: AsyncSession, *, client_name: str | None, ip_truncated: str | None, now: datetime | None = None
) -> NewDeviceAuthorization:
    now = now or _now()
    # Opportunistic sweep: the daily cleanup job does the same, but this keeps
    # the table short between runs.
    await db.execute(delete(CliDeviceAuthorization).where(CliDeviceAuthorization.expires_at < now))
    for _ in range(5):
        device_code, selector = _new_secret(DEVICE_CODE_PREFIX)
        user_code = _new_user_code()
        authorization = CliDeviceAuthorization(
            id=uuid.uuid4(),
            device_selector=selector,
            device_hash=hash_secret(device_code),
            user_code_hash=hash_secret(user_code),
            client_name=client_name,
            ip_truncated=ip_truncated,
            status=CliDeviceStatus.PENDING,
            created_at=now,
            expires_at=now + DEVICE_CODE_TTL,
        )
        db.add(authorization)
        try:
            await db.commit()
        except IntegrityError:  # pragma: no cover - a user code collision in a 32^8 space
            await db.rollback()
            continue
        return NewDeviceAuthorization(authorization=authorization, device_code=device_code, user_code=user_code)
    raise RuntimeError("geen unieke gebruikerscode gevonden")  # pragma: no cover - see above


async def pending_by_user_code(
    db: AsyncSession, user_code: str, *, now: datetime | None = None, for_update: bool = False
) -> CliDeviceAuthorization | None:
    """The pending, unexpired authorization behind a user code, or None."""
    compact = normalise_user_code(user_code)
    if compact is None:
        return None
    query = select(CliDeviceAuthorization).where(
        CliDeviceAuthorization.user_code_hash == hash_secret(compact),
        CliDeviceAuthorization.status == CliDeviceStatus.PENDING,
        CliDeviceAuthorization.expires_at > (now or _now()),
    )
    if for_update:
        query = query.with_for_update()
    return await db.scalar(query)


async def decide(
    db: AsyncSession, user_code: str, member: Member, *, approve: bool, now: datetime | None = None
) -> CliDeviceAuthorization | None:
    """Approves or denies a pending authorization; None when there is none."""
    now = now or _now()
    authorization = await pending_by_user_code(db, user_code, now=now, for_update=True)
    if authorization is None:
        await db.rollback()
        return None
    authorization.status = CliDeviceStatus.APPROVED if approve else CliDeviceStatus.DENIED
    authorization.member_id = member.id
    authorization.decided_at = now
    await db.commit()
    return authorization


async def _issue(db: AsyncSession, session: CliSession, now: datetime) -> tuple[str, str]:
    """New access token on the session and a new current refresh token; flushes."""
    access_token, access_selector = _new_secret(ACCESS_TOKEN_PREFIX)
    refresh_token, refresh_selector = _new_secret(REFRESH_TOKEN_PREFIX)
    session.access_selector = access_selector
    session.access_hash = hash_secret(access_token)
    session.access_expires_at = now + ACCESS_TOKEN_TTL
    await db.flush()
    db.add(
        CliRefreshToken(
            id=uuid.uuid4(),
            session_id=session.id,
            selector=refresh_selector,
            verifier_hash=hash_secret(refresh_token),
            created_at=now,
        )
    )
    await db.flush()
    return access_token, refresh_token


async def exchange_device_code(db: AsyncSession, device_code: str, *, now: datetime | None = None) -> IssuedTokens:
    """The polling side of the grant. Single use: a successful exchange
    deletes the authorization, so a second exchange finds nothing."""
    now = now or _now()
    selector = parse_selector(device_code, DEVICE_CODE_PREFIX)
    authorization = None
    if selector is not None:
        authorization = await db.scalar(
            select(CliDeviceAuthorization)
            .where(CliDeviceAuthorization.device_selector == selector)
            .with_for_update()
        )
    if not matches(device_code, authorization.device_hash if authorization else None) or authorization is None:
        await db.rollback()
        raise GrantError(f"{INVALID_GRANT}.device_code_unknown")
    if authorization.expires_at <= now:
        await db.rollback()
        raise GrantError(f"{EXPIRED_TOKEN}.device_code")

    previous_poll = authorization.last_polled_at
    authorization.last_polled_at = now
    if previous_poll is not None and (now - previous_poll).total_seconds() < POLL_INTERVAL_S - POLL_TOLERANCE_S:
        await db.commit()
        raise GrantError(SLOW_DOWN, params={"interval": POLL_INTERVAL_S})
    if authorization.status == CliDeviceStatus.PENDING:
        await db.commit()
        raise GrantError(AUTHORIZATION_PENDING)
    if authorization.status == CliDeviceStatus.DENIED:
        await db.commit()
        raise GrantError(ACCESS_DENIED)

    member = await db.get(Member, authorization.member_id)
    if member is None or member.status != MemberStatus.ACTIVE:
        await db.delete(authorization)
        await db.commit()
        raise GrantError(f"{ACCESS_DENIED}.approver_not_active")

    session = CliSession(
        id=uuid.uuid4(),
        member_id=member.id,
        client_name=authorization.client_name,
        created_at=now,
        expires_at=now + REFRESH_IDLE_TTL,
        max_expires_at=now + SESSION_MAX_TTL,
        access_selector="",
        access_hash="",
        access_expires_at=now,
    )
    await db.delete(authorization)
    db.add(session)
    access_token, refresh_token = await _issue(db, session, now)
    await db.commit()
    return IssuedTokens(session=session, member=member, access_token=access_token, refresh_token=refresh_token)


async def _just_rotated(db: AsyncSession, stored: CliRefreshToken, now: datetime) -> bool:
    """Whether `stored` is the session's most recently rotated token, rotated
    less than REFRESH_REUSE_GRACE ago."""
    if now - stored.used_at >= REFRESH_REUSE_GRACE:
        return False
    latest = await db.scalar(
        select(func.max(CliRefreshToken.used_at)).where(CliRefreshToken.session_id == stored.session_id)
    )
    return latest == stored.used_at


async def refresh(db: AsyncSession, refresh_token: str, *, now: datetime | None = None) -> IssuedTokens:
    now = now or _now()
    selector = parse_selector(refresh_token, REFRESH_TOKEN_PREFIX)
    stored = None
    if selector is not None:
        stored = await db.scalar(
            select(CliRefreshToken).where(CliRefreshToken.selector == selector).with_for_update()
        )
    if not matches(refresh_token, stored.verifier_hash if stored else None) or stored is None:
        await db.rollback()
        raise GrantError(f"{INVALID_GRANT}.refresh_token_unknown")

    session = await db.scalar(select(CliSession).where(CliSession.id == stored.session_id).with_for_update())
    member = await db.get(Member, session.member_id) if session is not None else None
    if stored.used_at is not None and session is not None and await _just_rotated(db, stored, now):
        await db.rollback()
        raise GrantError(f"{INVALID_GRANT}.refresh_token_rotated")
    if stored.used_at is not None:
        await db.execute(delete(CliSession).where(CliSession.id == stored.session_id))
        await db.commit()
        raise RefreshReuseError(member.sso_subject if member is not None else None)
    if session is None or now >= session.expires_at or now >= session.max_expires_at:
        await db.execute(delete(CliSession).where(CliSession.id == stored.session_id))
        await db.commit()
        raise GrantError(f"{INVALID_GRANT}.session_expired")
    if member is None or member.status != MemberStatus.ACTIVE:
        await db.rollback()
        raise GrantError(f"{INVALID_GRANT}.member_not_active")

    stored.used_at = now
    session.expires_at = min(now + REFRESH_IDLE_TTL, session.max_expires_at)
    session.last_used_at = now
    access_token, new_refresh_token = await _issue(db, session, now)
    await db.commit()
    return IssuedTokens(session=session, member=member, access_token=access_token, refresh_token=new_refresh_token)


async def session_for_access_token(
    db: AsyncSession, access_token: str, *, now: datetime | None = None
) -> CliSession | None:
    """The live CLI session behind an access token, or None. Whether its
    member is still active is the caller's check, per request."""
    now = now or _now()
    selector = parse_selector(access_token, ACCESS_TOKEN_PREFIX)
    session = None
    if selector is not None:
        session = await db.scalar(select(CliSession).where(CliSession.access_selector == selector))
    if not matches(access_token, session.access_hash if session else None) or session is None:
        return None
    if session.access_expires_at <= now or session.expires_at <= now or session.max_expires_at <= now:
        return None
    return session


async def session_for_logout(
    db: AsyncSession, *, access_token: str | None = None, refresh_token: str | None = None
) -> CliSession | None:
    """The session a logout names, or None. An expired access token still
    counts as long as it is genuine (right selector and secret) and its
    session still exists: logging out must not need a refresh first. A
    refresh token counts whether current or already rotated (RFC 7009)."""
    if access_token is not None:
        selector = parse_selector(access_token, ACCESS_TOKEN_PREFIX)
        session = None
        if selector is not None:
            session = await db.scalar(select(CliSession).where(CliSession.access_selector == selector))
        if matches(access_token, session.access_hash if session else None) and session is not None:
            return session
    if refresh_token is not None:
        selector = parse_selector(refresh_token, REFRESH_TOKEN_PREFIX)
        stored = None
        if selector is not None:
            stored = await db.scalar(select(CliRefreshToken).where(CliRefreshToken.selector == selector))
        if matches(refresh_token, stored.verifier_hash if stored else None) and stored is not None:
            return await db.get(CliSession, stored.session_id)
    return None


async def revoke_all_of(db: AsyncSession, member_id: uuid.UUID) -> int:
    """Deletes every CLI session of a member; returns how many. The caller commits."""
    result = await db.execute(delete(CliSession).where(CliSession.member_id == member_id).returning(CliSession.id))
    return len(result.all())


async def mark_used(db: AsyncSession, session_id: uuid.UUID, *, now: datetime | None = None) -> None:
    await db.execute(update(CliSession).where(CliSession.id == session_id).values(last_used_at=now or _now()))
    await db.commit()


async def revoke(db: AsyncSession, session_id: uuid.UUID, *, member_id: uuid.UUID | None = None) -> bool:
    """Deletes the session (its refresh tokens cascade); False when there is
    none (for this member, when given)."""
    query = delete(CliSession).where(CliSession.id == session_id)
    if member_id is not None:
        query = query.where(CliSession.member_id == member_id)
    result = await db.execute(query.returning(CliSession.id))
    revoked = result.scalar() is not None
    await db.commit()
    return revoked


async def sessions_of(db: AsyncSession, member_id: uuid.UUID, *, now: datetime | None = None) -> list[CliSession]:
    now = now or _now()
    rows = await db.scalars(
        select(CliSession)
        .where(CliSession.member_id == member_id, CliSession.expires_at > now, CliSession.max_expires_at > now)
        .order_by(CliSession.created_at.desc())
    )
    return list(rows)


async def delete_expired(db: AsyncSession, now: datetime) -> tuple[int, int]:
    """Sweeps expired device authorizations and CLI sessions; returns both counts."""
    authorizations = await db.execute(
        delete(CliDeviceAuthorization)
        .where(CliDeviceAuthorization.expires_at < now)
        .returning(CliDeviceAuthorization.id)
    )
    sessions = await db.execute(
        delete(CliSession)
        .where((CliSession.expires_at < now) | (CliSession.max_expires_at < now))
        .returning(CliSession.id)
    )
    counts = (len(authorizations.all()), len(sessions.all()))
    await db.commit()
    return counts
