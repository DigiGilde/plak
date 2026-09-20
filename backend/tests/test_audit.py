"""Tests for the audit log (spec §7): pseudonymisation, IP truncation and
fail-open write behaviour."""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import asyncpg
import pytest
import pytest_asyncio
from helpers_audit import AuditRecorder
from sqlalchemy.ext.asyncio import create_async_engine

from plak.audit import retention, vocabulary
from plak.audit.ip_crypto import decrypt_ip
from plak.audit.log import ANONYMOUS, SYSTEM, Actor, AuditLog, LookupLimitReachedError
from plak.audit.pseudonymisation import pseudonymise, truncate_ip
from plak.audit.retention import purge
from plak.db import make_session_factory
from plak.models.audit import ActorKind

# asyncio_mode = "auto" (pyproject.toml) picks up async def tests by itself; this
# file deliberately mixes sync and async tests, so no module-wide asyncio marker.

_PEPPER_A = "a" * 32
_PEPPER_B = "b" * 32
_IP_KEY_A = b"k" * 32


# --- Pseudonymisation: no DB needed --------------------------------------------------


def test_pseudonym_stable_per_pepper() -> None:
    first = pseudonymise(_PEPPER_A, "sub-123")
    second_one = pseudonymise(_PEPPER_A, "sub-123")
    assert first == second_one


def test_pseudonym_differs_per_pepper() -> None:
    with_pepper_a = pseudonymise(_PEPPER_A, "sub-123")
    with_pepper_b = pseudonymise(_PEPPER_B, "sub-123")
    assert with_pepper_a != with_pepper_b


def test_pseudonym_differs_per_identifier() -> None:
    first = pseudonymise(_PEPPER_A, "sub-123")
    second_one = pseudonymise(_PEPPER_A, "sub-456")
    assert first != second_one


def test_pseudonym_does_not_leak_the_identifier() -> None:
    pseudonym = pseudonymise(_PEPPER_A, "geheim@example.org")
    assert "geheim" not in pseudonym


def test_ip_truncation_ipv4_on_slash_24() -> None:
    assert truncate_ip("203.0.113.42") == "203.0.113.0/24"


def test_ip_truncation_ipv4_same_subnet_same_result() -> None:
    assert truncate_ip("203.0.113.1") == truncate_ip("203.0.113.254")


def test_ip_truncation_ipv4_other_subnet_other_result() -> None:
    assert truncate_ip("203.0.113.1") != truncate_ip("203.0.114.1")


def test_ip_truncation_ipv6_on_slash_48() -> None:
    assert truncate_ip("2001:db8:abcd:1234::1") == "2001:db8:abcd::/48"


def test_ip_truncation_ipv6_same_prefix_same_result() -> None:
    first = truncate_ip("2001:db8:abcd:0001::1")
    second_one = truncate_ip("2001:db8:abcd:ffff::9")
    assert first == second_one


def test_ip_truncation_ipv6_other_prefix_other_result() -> None:
    assert truncate_ip("2001:db8:abcd::1") != truncate_ip("2001:db8:abce::1")


def test_recorder_matches_the_audit_log_signature() -> None:
    """AuditRecorder is a test double for AuditLog; if the two write signatures
    drift apart, every test built on the double stops meaning anything."""
    assert inspect.signature(AuditRecorder.write) == inspect.signature(AuditLog.write)


# --- Audit log: DB tests --------------------------------------------------------------


@pytest_asyncio.fixture
async def audit_log(migrated_dsn: str) -> AsyncIterator[AuditLog]:
    engine = create_async_engine(migrated_dsn)
    session_factory = make_session_factory(engine)
    try:
        yield AuditLog(session_factory, pepper=_PEPPER_A, ip_key=_IP_KEY_A)
    finally:
        await engine.dispose()


async def _read_audit_row(db_connection: asyncpg.Connection, action: str) -> asyncpg.Record | None:
    return await db_connection.fetchrow("SELECT * FROM audit_log_entries WHERE action = $1", action)


async def test_write_stores_audit_row_on(
    audit_log: AuditLog, db_connection: asyncpg.Connection
) -> None:
    action = f"test_actie_{uuid.uuid4().hex}"
    await audit_log.write(
        action,
        Actor(kind=ActorKind.MEMBER, identifier="sub-abc"),
        "refused",
        reason_code="UNKNOWN_SITE",
        refs={"group_slug": "nldd", "site_slug": "website"},
        ip="203.0.113.42",
    )

    row = await _read_audit_row(db_connection, action)
    assert row is not None
    assert row["actor_kind"] == "member"
    assert row["result"] == "refused"
    assert row["reason_code"] == "UNKNOWN_SITE"
    assert row["ip_truncated"] == "203.0.113.0/24"
    # the raw sub appears nowhere in the row, only the pseudonym
    assert row["actor_pseudonym"] == pseudonymise(_PEPPER_A, "sub-abc")
    assert "sub-abc" not in (row["actor_pseudonym"] or "")
    assert decrypt_ip(_IP_KEY_A, row["id"], row["ip_encrypted"]) == "203.0.113.42"


async def test_write_without_identifier_stores_no_pseudonym_on(
    audit_log: AuditLog, db_connection: asyncpg.Connection
) -> None:
    action = f"test_actie_{uuid.uuid4().hex}"
    await audit_log.write(action, ANONYMOUS, "refused", reason_code="UNKNOWN_SITE")

    row = await _read_audit_row(db_connection, action)
    assert row is not None
    assert row["actor_kind"] == "anonymous"
    assert row["actor_pseudonym"] is None
    assert row["ip_truncated"] is None
    assert row["ip_encrypted"] is None


async def test_write_system_actor(audit_log: AuditLog, db_connection: asyncpg.Connection) -> None:
    action = f"test_actie_{uuid.uuid4().hex}"
    await audit_log.write(action, SYSTEM, "uitgevoerd")

    row = await _read_audit_row(db_connection, action)
    assert row is not None
    assert row["actor_kind"] == "system"


async def test_write_fails_not_on_broken_db(caplog: pytest.LogCaptureFixture) -> None:
    """Fail-open: an audit write failure must never reach the caller."""
    broken_engine = create_async_engine("postgresql+asyncpg://onbestaand:5432/nergens")
    broken_session_factory = make_session_factory(broken_engine)
    log = AuditLog(broken_session_factory, pepper=_PEPPER_A, ip_key=_IP_KEY_A)

    with caplog.at_level("ERROR"):
        # No exception: fail-open on the logging itself.
        await log.write("test_kapotte_db", ANONYMOUS, "refused")

    assert "Auditlog-schrijffout" in caplog.text
    await broken_engine.dispose()


# --- Daily lookup cap: write_strict_limited -------------------------------------------


async def test_write_strict_limited_writes_the_row_under_the_cap(
    audit_log: AuditLog, db_connection: asyncpg.Connection
) -> None:
    action = f"test_actie_{uuid.uuid4().hex}"
    await audit_log.write_strict_limited(
        action,
        Actor(kind=ActorKind.MEMBER, identifier="sub-limiet"),
        "allowed",
        refs={"reason": "test"},
        ip=None,
        limit=5,
        counted_actions=(action,),
    )
    row = await _read_audit_row(db_connection, action)
    assert row is not None


async def test_write_strict_limited_refuses_and_writes_nothing_at_the_cap(
    audit_log: AuditLog, db_connection: asyncpg.Connection
) -> None:
    action = f"test_actie_{uuid.uuid4().hex}"
    actor = Actor(kind=ActorKind.MEMBER, identifier="sub-limiet-2")
    await audit_log.write_strict_limited(
        action, actor, "allowed", refs=None, ip=None, limit=1, counted_actions=(action,)
    )
    with pytest.raises(LookupLimitReachedError):
        await audit_log.write_strict_limited(
            action, actor, "allowed", refs=None, ip=None, limit=1, counted_actions=(action,)
        )
    rows = await db_connection.fetch("SELECT id FROM audit_log_entries WHERE action = $1", action)
    assert len(rows) == 1


async def test_write_strict_limited_is_atomic_under_concurrency(
    audit_log: AuditLog, db_connection: asyncpg.Connection
) -> None:
    """Reproduces the TOCTOU: with a plain count-then-insert, ten concurrent
    calls at limit=1 could all read "under the cap" before any of them
    commits, and all ten would land a row. The advisory lock in
    write_strict_limited serialises them, so exactly one succeeds."""
    action = f"test_actie_{uuid.uuid4().hex}"
    actor = Actor(kind=ActorKind.MEMBER, identifier="sub-race")

    async def _attempt() -> bool:
        try:
            await audit_log.write_strict_limited(
                action, actor, "allowed", refs=None, ip=None, limit=1, counted_actions=(action,)
            )
        except LookupLimitReachedError:
            return False
        return True

    results = await asyncio.gather(*(_attempt() for _ in range(10)))
    assert sum(results) == 1

    rows = await db_connection.fetch("SELECT id FROM audit_log_entries WHERE action = $1", action)
    assert len(rows) == 1


# --- Retention: the cleanup job ------------------------------------------------------


async def _committed_row(dsn: str, action: str, result: str, age: timedelta) -> uuid.UUID:
    connection = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        return await connection.fetchval(
            """
            INSERT INTO audit_log_entries (id, actor_kind, action, result, occurred_at)
            VALUES ($1, 'system', $2, $3, now() - $4::interval)
            RETURNING id
            """,
            uuid.uuid4(),
            action,
            result,
            age,
        )
    finally:
        await connection.close()


async def test_purge_removes_what_has_expired_and_nothing_else(
    migrated_dsn: str, db_connection: asyncpg.Connection
) -> None:
    looked_long_ago = await _committed_row(migrated_dsn, "content_access", "allowed", timedelta(days=91))
    looked_last_week = await _committed_row(migrated_dsn, "content_access", "allowed", timedelta(days=7))
    refused_long_ago = await _committed_row(migrated_dsn, "content_access", "refused", timedelta(days=91))

    deleted = await purge(migrated_dsn, batch=1)

    assert deleted == 1
    remaining = {
        row["id"] for row in await db_connection.fetch("SELECT id FROM audit_log_entries")
    }
    assert looked_long_ago not in remaining
    assert {looked_last_week, refused_long_ago} <= remaining


async def test_purge_records_that_it_ran(migrated_dsn: str, db_connection: asyncpg.Connection) -> None:
    await _committed_row(migrated_dsn, "logout", "allowed", timedelta(days=91))
    await _committed_row(migrated_dsn, "logout", "allowed", timedelta(days=92))

    await purge(migrated_dsn, batch=1)

    row = await db_connection.fetchrow(
        "SELECT * FROM audit_log_entries WHERE action = $1", vocabulary.AUDIT_PURGE
    )
    assert row is not None
    assert row["actor_kind"] == "system"
    assert json.loads(row["refs"]) == {"audit_log_entries": 2, "content_viewers": 0}


async def _committed_viewer(dsn: str, age: timedelta) -> uuid.UUID:
    connection = await asyncpg.connect(dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        return await connection.fetchval(
            """
            INSERT INTO content_viewers (id, sso_subject, email, last_seen_at)
            VALUES ($1, $2, $3, now() - $4::interval)
            RETURNING id
            """,
            uuid.uuid4(),
            f"sub-{uuid.uuid4().hex}",
            "viewer@example.nl",
            age,
        )
    finally:
        await connection.close()


async def test_purge_removes_content_viewers_after_90_days(
    migrated_dsn: str, db_connection: asyncpg.Connection
) -> None:
    stale = await _committed_viewer(migrated_dsn, timedelta(days=91))
    fresh = await _committed_viewer(migrated_dsn, timedelta(days=7))

    deleted = await purge(migrated_dsn, batch=1)

    assert deleted == 1
    remaining = {row["id"] for row in await db_connection.fetch("SELECT id FROM content_viewers")}
    assert stale not in remaining
    assert fresh in remaining


async def test_purge_continues_the_other_table_when_one_fails(
    migrated_dsn: str, db_connection: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure purging content_viewers must not stop audit_log_entries from
    being purged (they run independently), and whatever did get deleted still
    lands in the audit_purge row."""
    await _committed_row(migrated_dsn, "logout", "allowed", timedelta(days=91))
    stale_viewer = await _committed_viewer(migrated_dsn, timedelta(days=91))

    original = retention._delete_in_batches

    async def _flaky(session, statement, *, batch):
        if statement is retention._DELETE_CONTENT_VIEWERS_BATCH:
            # _delete_in_batches never raises itself (see its docstring): it
            # reports a failure back as (deleted-so-far, error).
            return 0, RuntimeError("content_viewers kapot")
        return await original(session, statement, batch=batch)

    monkeypatch.setattr(retention, "_delete_in_batches", _flaky)

    with pytest.raises(RuntimeError):
        await purge(migrated_dsn, batch=1)

    remaining_audit = await db_connection.fetch(
        "SELECT id FROM audit_log_entries WHERE action = 'logout'"
    )
    assert remaining_audit == []  # audit_log_entries purge still ran

    remaining_viewers = {row["id"] for row in await db_connection.fetch("SELECT id FROM content_viewers")}
    assert stale_viewer in remaining_viewers  # content_viewers purge failed, row untouched

    purge_row = await db_connection.fetchrow(
        "SELECT * FROM audit_log_entries WHERE action = $1", vocabulary.AUDIT_PURGE
    )
    assert purge_row is not None
    assert json.loads(purge_row["refs"]) == {"audit_log_entries": 1, "content_viewers": 0}


def test_the_job_refuses_to_run_without_a_dsn(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv(retention.DB_URL_VAR, raising=False)

    assert retention.main() == 1
    assert retention.DB_URL_VAR in caplog.text
