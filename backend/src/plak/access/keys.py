"""Secret links (sleutels): creating, verifying, cookie validation and
revoking.

The database keeps selector + SHA-256(verifier); the plaintext
'selector.verifier' exists only at the moment of creation. The cookie is not
proof in its own right but a reference to the access key record, and is validated
server-side on every request, so revoking or expiry breaks outstanding cookies
immediately.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plak.expiry import resolve
from plak.models.publication import AccessKey, KeyStatus

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
SELECTOR_LENGTH = 8
VERIFIER_LENGTH = 32

_DUTCH_MONTHS = (
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
)


def default_label(now: datetime | None = None) -> str:
    """Dutch fallback label for a key created without a name, e.g. 'Link van 20 september'."""
    moment = now or datetime.now(UTC)
    return f"Link van {moment.day} {_DUTCH_MONTHS[moment.month - 1]}"


# Fixed comparison value for unknown selectors: verify_parts then still does a
# hash plus a constant-time comparison (timing-neutral as to whether a
# selector exists).
_DUMMY_HASH = hashlib.sha256(b"plak-onbekende-selector").hexdigest()


def _chars(length: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def hash_verifier(verifier: str) -> str:
    return hashlib.sha256(verifier.encode("utf-8")).hexdigest()


async def create_key(
    db: AsyncSession,
    site_id: uuid.UUID,
    label: str | None,
    expires_at: datetime | None = None,
) -> tuple[AccessKey, str]:
    """Creates a key and returns (record, plaintext); the plaintext
    'selector.verifier' cannot be reconstructed afterwards.

    A missing or blank label falls back to a Dutch date-based default, so the
    column stays NOT NULL and every reader (table, audit refs, revoke
    confirmation) keeps working unchanged."""
    selector = _chars(SELECTOR_LENGTH)
    verifier = _chars(VERIFIER_LENGTH)
    normalised_label = label.strip() if label else ""
    access_key = AccessKey(
        site_id=site_id,
        label=normalised_label or default_label(),
        selector=selector,
        verifier_hash=hash_verifier(verifier),
        status=KeyStatus.ACTIVE,
        expires_at=resolve(expires_at),
    )
    db.add(access_key)
    await db.flush()
    return access_key, f"{selector}.{verifier}"


def _usable(access_key: AccessKey, site_id: uuid.UUID) -> bool:
    if access_key.site_id != site_id:
        return False
    if access_key.status != KeyStatus.ACTIVE:
        return False
    return access_key.expires_at > datetime.now(UTC)


def compare_dummy(verifier: str) -> bool:
    """Burns exactly the work verify_parts does, for a path that refuses before
    any lookup (an unknown site, a locked-out selector): a refusal must not
    come back faster than a wrong code."""
    return hmac.compare_digest(hash_verifier(verifier), _DUMMY_HASH)


def bare_selector(value: str | None) -> str | None:
    """The selector of a `?key=` that carries the selector alone (a link shared
    without its code); None for anything else, a full 'selector.verifier'
    included."""
    if value is None or len(value) != SELECTOR_LENGTH:
        return None
    if not all(character in _ALPHABET for character in value):
        return None
    return value


_FULL_KEY_AT_START = re.compile(rf"[{_ALPHABET}]{{{SELECTOR_LENGTH}}}\.[{_ALPHABET}]{{{VERIFIER_LENGTH}}}")


def carries_full_key(values: Iterable[str]) -> bool:
    """Whether any of the `key` query values starts with a whole secret link
    (selector.verifier). Starts with, not equals: a link copied out of a mail
    often carries a closing bracket or full stop, and the page sees it anyway."""
    return any(_FULL_KEY_AT_START.match(value) for value in values)


def carries_bare_selector(values: Iterable[str]) -> bool:
    """Whether any of the `key` query values is a selector without its code."""
    return any(bare_selector(value) is not None for value in values)


async def verify_parts(
    db: AsyncSession, site_id: uuid.UUID | None, selector: str, verifier: str
) -> AccessKey | None:
    """Validates a selector plus its verifier; None on every rejection.

    `site_id` may be None (an unknown site, or one that is not on the secret
    link): the hash and the constant-time comparison happen anyway, so that
    case costs the same as a wrong code.
    """
    if len(selector) != SELECTOR_LENGTH or not verifier:
        compare_dummy(verifier)
        return None
    access_key = await db.scalar(select(AccessKey).where(AccessKey.selector == selector))
    expected = access_key.verifier_hash if access_key is not None else _DUMMY_HASH
    matches = hmac.compare_digest(hash_verifier(verifier), expected)
    if access_key is None or not matches or site_id is None or not _usable(access_key, site_id):
        return None
    return access_key


async def verify(db: AsyncSession, site_id: uuid.UUID, key: str) -> AccessKey | None:
    """Validates a '?key=selector.verifier' value; None on every rejection."""
    selector, dot, verifier = key.partition(".")
    if not dot:
        return None
    return await verify_parts(db, site_id, selector, verifier)


async def selector_usable(db: AsyncSession, site_id: uuid.UUID, selector: str) -> bool:
    """Whether this selector belongs to a usable key of this site.

    The dummy hash in both branches keeps an unknown selector as expensive as a
    known one, so the code page and the neutral 404 differ in what they say,
    never in how long they take.
    """
    access_key = await db.scalar(select(AccessKey).where(AccessKey.selector == selector))
    compare_dummy(selector)
    return access_key is not None and _usable(access_key, site_id)


async def validate_cookie(db: AsyncSession, site_id: uuid.UUID, cookie_value: str) -> AccessKey | None:
    """Validates a key cookie (the key id) against the record on every request:
    site match, status active, not expired."""
    try:
        key_id = uuid.UUID(cookie_value)
    except (TypeError, ValueError):
        return None
    access_key = await db.get(AccessKey, key_id)
    if access_key is None or not _usable(access_key, site_id):
        return None
    return access_key


async def revoke(db: AsyncSession, key_id: uuid.UUID) -> bool:
    """Marks the key as revoked (idempotent); False when the record does not
    exist."""
    access_key = await db.get(AccessKey, key_id)
    if access_key is None:
        return False
    access_key.status = KeyStatus.REVOKED
    await db.flush()
    return True
