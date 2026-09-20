"""Tests for access/keys.py: creating, verifying, cookie validation and
revoking secret links, against the real PostgreSQL test container."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from plak.access import keys
from plak.constants import AccessBase
from plak.expiry import MAX_VALIDITY, ExpiryError
from plak.models.identity import Group
from plak.models.publication import AccessKey, KeyStatus, Site

PLAINTEXT_RE = re.compile(r"^[A-Za-z0-9]{8}\.[A-Za-z0-9]{32}$")


@pytest_asyncio.fixture
async def db(migrated_dsn: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(migrated_dsn)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
            try:
                yield session
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def site_id(db: AsyncSession) -> uuid.UUID:
    group = Group(slug="aurora", name="Aurora", default_access_base=AccessBase.PUBLIC)
    db.add(group)
    await db.flush()
    site = Site(group_id=group.id, slug="site", title="Site", access_base=AccessBase.NOBODY, access_keys=True)
    db.add(site)
    await db.flush()
    return site.id


@pytest_asyncio.fixture
async def other_site_id(db: AsyncSession, site_id: uuid.UUID) -> uuid.UUID:
    group = Group(slug="borealis", name="Borealis", default_access_base=AccessBase.PUBLIC)
    db.add(group)
    await db.flush()
    site = Site(group_id=group.id, slug="ander", title="Ander", access_base=AccessBase.NOBODY, access_keys=True)
    db.add(site)
    await db.flush()
    return site.id


async def test_make_key_format_and_storage(db, site_id):
    key, plaintext = await keys.create_key(db, site_id, "ci-link")

    assert PLAINTEXT_RE.match(plaintext), plaintext
    selector, verifier = plaintext.split(".")
    assert key.selector == selector
    assert key.label == "ci-link"
    assert key.status == KeyStatus.ACTIVE
    assert key.expires_at is not None
    # The DB stores SHA-256(verifier), never the verifier itself.
    assert key.verifier_hash == hashlib.sha256(verifier.encode()).hexdigest()
    assert verifier not in key.verifier_hash

    row = await db.get(AccessKey, key.id)
    assert row is not None
    assert row.verifier_hash == key.verifier_hash


async def test_missing_label_gets_dutch_date_default(db, site_id):
    key, _ = await keys.create_key(db, site_id, None)
    today = datetime.now(UTC)
    assert key.label == f"Link van {today.day} {keys._DUTCH_MONTHS[today.month - 1]}"


async def test_empty_label_gets_dutch_date_default(db, site_id):
    key, _ = await keys.create_key(db, site_id, "")
    assert key.label.startswith("Link van ")


async def test_whitespace_only_label_gets_dutch_date_default(db, site_id):
    key, _ = await keys.create_key(db, site_id, "   ")
    assert key.label.startswith("Link van ")


async def test_real_label_is_trimmed_and_kept(db, site_id):
    key, _ = await keys.create_key(db, site_id, "  reviewers  ")
    assert key.label == "reviewers"


def test_default_label_shape():
    moment = datetime(2026, 9, 20, tzinfo=UTC)
    assert keys.default_label(moment) == "Link van 20 september"


async def test_key_without_expiry_gets_the_default(db, site_id):
    key, _ = await keys.create_key(db, site_id, "zonder-vervaldatum")
    expected = datetime.now(UTC) + timedelta(days=90)
    assert abs((key.expires_at - expected).total_seconds()) < 60


async def test_key_beyond_the_maximum_is_refused(db, site_id):
    with pytest.raises(ExpiryError) as caught:
        await keys.create_key(
            db, site_id, "te-lang", expires_at=datetime.now(UTC) + MAX_VALIDITY + timedelta(days=1)
        )
    assert caught.value.reason == "EXPIRY_TOO_FAR"


async def test_key_in_the_past_is_refused(db, site_id):
    with pytest.raises(ExpiryError) as caught:
        await keys.create_key(db, site_id, "verleden", expires_at=datetime.now(UTC) - timedelta(minutes=1))
    assert caught.value.reason == "EXPIRY_IN_PAST"


async def test_make_key_unique_per_call(db, site_id):
    _, plaintext_a = await keys.create_key(db, site_id, "a")
    _, plaintext_b = await keys.create_key(db, site_id, "b")
    assert plaintext_a != plaintext_b
    assert plaintext_a.split(".")[0] != plaintext_b.split(".")[0]


async def test_verify_valid_key(db, site_id):
    key, plaintext = await keys.create_key(db, site_id, "valid")
    found = await keys.verify(db, site_id, plaintext)
    assert found is not None
    assert found.id == key.id


async def test_verify_wrong_verifier(db, site_id):
    _, plaintext = await keys.create_key(db, site_id, "x")
    selector = plaintext.split(".")[0]
    assert await keys.verify(db, site_id, f"{selector}.{'A' * 32}") is None


async def test_verify_unknown_selector(db, site_id):
    await keys.create_key(db, site_id, "x")
    assert await keys.verify(db, site_id, f"{'z' * 8}.{'A' * 32}") is None


async def test_verify_malformed_input(db, site_id):
    _, plaintext = await keys.create_key(db, site_id, "x")
    selector, verifier = plaintext.split(".")
    assert await keys.verify(db, site_id, "") is None
    assert await keys.verify(db, site_id, "geen-punt") is None
    assert await keys.verify(db, site_id, f"{selector}.") is None
    assert await keys.verify(db, site_id, f".{verifier}") is None
    assert await keys.verify(db, site_id, f"tekort.{verifier}") is None
    assert await keys.verify(db, site_id, f"veeltelangeselector.{verifier}") is None


async def test_verify_wrong_site(db, site_id, other_site_id):
    _, plaintext = await keys.create_key(db, site_id, "x")
    assert await keys.verify(db, other_site_id, plaintext) is None


async def test_verify_expired_key(db, site_id):
    # create_key refuses a past expiry, so this builds an already-expired
    # record directly, bypassing the guarantee, to test verify's own check.
    verifier = keys._chars(keys.VERIFIER_LENGTH)
    access_key = AccessKey(
        site_id=site_id,
        label="expired",
        selector=keys._chars(keys.SELECTOR_LENGTH),
        verifier_hash=keys.hash_verifier(verifier),
        status=KeyStatus.ACTIVE,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db.add(access_key)
    await db.flush()
    plaintext = f"{access_key.selector}.{verifier}"
    assert await keys.verify(db, site_id, plaintext) is None


async def test_verify_future_expiry_date(db, site_id):
    key, plaintext = await keys.create_key(
        db, site_id, "nog-geldig", expires_at=datetime.now(UTC) + timedelta(days=7)
    )
    found = await keys.verify(db, site_id, plaintext)
    assert found is not None
    assert found.id == key.id


async def test_revoke_breaks_verify_and_cookie(db, site_id):
    key, plaintext = await keys.create_key(db, site_id, "x")
    assert await keys.verify(db, site_id, plaintext) is not None
    assert await keys.validate_cookie(db, site_id, str(key.id)) is not None

    assert await keys.revoke(db, key.id) is True

    assert await keys.verify(db, site_id, plaintext) is None
    assert await keys.validate_cookie(db, site_id, str(key.id)) is None


async def test_revoke_idempotent_and_unknown(db, site_id):
    key, _ = await keys.create_key(db, site_id, "x")
    assert await keys.revoke(db, key.id) is True
    assert await keys.revoke(db, key.id) is True
    assert await keys.revoke(db, uuid.uuid4()) is False


async def test_validate_cookie_valid(db, site_id):
    key, _ = await keys.create_key(db, site_id, "x")
    found = await keys.validate_cookie(db, site_id, str(key.id))
    assert found is not None
    assert found.id == key.id


async def test_validate_cookie_wrong_site(db, site_id, other_site_id):
    key, _ = await keys.create_key(db, site_id, "x")
    assert await keys.validate_cookie(db, other_site_id, str(key.id)) is None


async def test_validate_cookie_no_uuid(db, site_id):
    assert await keys.validate_cookie(db, site_id, "geen-uuid") is None
    assert await keys.validate_cookie(db, site_id, "") is None


async def test_validate_cookie_unknown_id(db, site_id):
    assert await keys.validate_cookie(db, site_id, str(uuid.uuid4())) is None


async def test_validate_cookie_expired_key(db, site_id):
    # create_key refuses a past expiry, so this builds an already-expired
    # record directly, bypassing the guarantee, to test validate_cookie's own check.
    access_key = AccessKey(
        site_id=site_id,
        label="expired",
        selector=keys._chars(keys.SELECTOR_LENGTH),
        verifier_hash=keys.hash_verifier(keys._chars(keys.VERIFIER_LENGTH)),
        status=KeyStatus.ACTIVE,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db.add(access_key)
    await db.flush()
    assert await keys.validate_cookie(db, site_id, str(access_key.id)) is None
