"""Tests for the audit log (spec §7): pseudonymisation, IP truncation and
fail-open write behaviour."""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import asyncpg
import pytest
import pytest_asyncio
from helpers_audit import AuditRecorder, insert_aged_audit_row
from sqlalchemy.ext.asyncio import create_async_engine

from plak.audit import retention, vocabulary
from plak.audit.ip_crypto import decrypt_ip
from plak.audit.log import ANONYMOUS, SYSTEM, Actor, AuditLog, LookupLimitReachedError
from plak.audit.pseudonymisation import pseudonymise, truncate_ip
from plak.audit.retention import purge
from plak.db import make_session_factory
from plak.models.audit import ActorKind
from plak.net import ClientAddress

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


def test_ip_truncation_ipv4_mapped_ipv6_is_truncated_as_ipv4() -> None:
    """A dual-stack socket reports an IPv4 peer as ::ffff:a.b.c.d. Taken as
    IPv6, its /48 is ::/48 for every IPv4 peer there is."""
    assert truncate_ip("::ffff:203.0.113.42") == "203.0.113.0/24"
    assert truncate_ip("::ffff:203.0.113.42") == truncate_ip("203.0.113.42")
    assert truncate_ip("::ffff:203.0.113.42") != truncate_ip("::ffff:198.51.100.42")


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
        refs={"group_slug": "team-aurora", "site_slug": "website"},
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


async def test_an_unvouched_ip_is_marked_in_the_refs(
    audit_log: AuditLog, db_connection: asyncpg.Connection
) -> None:
    """The address stays exactly what was derived; the row only says that we
    could not tell whether the client wrote it itself."""
    action = f"test_actie_{uuid.uuid4().hex}"
    await audit_log.write(
        action,
        ANONYMOUS,
        "allowed",
        refs={"group_slug": "team-aurora"},
        ip=ClientAddress("203.0.113.42", vouched=False),
    )

    row = await _read_audit_row(db_connection, action)
    assert row is not None
    assert json.loads(row["refs"]) == {"group_slug": "team-aurora", vocabulary.IP_UNVOUCHED: True}
    assert row["ip_truncated"] == "203.0.113.0/24"
    assert decrypt_ip(_IP_KEY_A, row["id"], row["ip_encrypted"]) == "203.0.113.42"


async def test_an_unvouched_ip_gets_refs_of_its_own_when_there_are_none(
    audit_log: AuditLog, db_connection: asyncpg.Connection
) -> None:
    action = f"test_actie_{uuid.uuid4().hex}"
    await audit_log.write(action, ANONYMOUS, "allowed", ip=ClientAddress("203.0.113.42", vouched=False))

    row = await _read_audit_row(db_connection, action)
    assert row is not None
    assert json.loads(row["refs"]) == {vocabulary.IP_UNVOUCHED: True}


async def test_a_vouched_ip_leaves_the_refs_alone(
    audit_log: AuditLog, db_connection: asyncpg.Connection
) -> None:
    """Absence is the normal case, so the flag may never be written as false:
    a reader filtering on it would otherwise see every row."""
    action = f"test_actie_{uuid.uuid4().hex}"
    await audit_log.write(action, ANONYMOUS, "allowed", ip=ClientAddress("203.0.113.42", vouched=True))
    plain = f"test_actie_{uuid.uuid4().hex}"
    await audit_log.write(plain, ANONYMOUS, "allowed", refs={"group_slug": "team-aurora"}, ip="203.0.113.42")

    assert json.loads((await _read_audit_row(db_connection, action))["refs"]) is None
    assert json.loads((await _read_audit_row(db_connection, plain))["refs"]) == {"group_slug": "team-aurora"}


async def test_an_unvouched_ip_is_marked_on_the_limited_write_too(
    audit_log: AuditLog, db_connection: asyncpg.Connection
) -> None:
    action = f"test_actie_{uuid.uuid4().hex}"
    await audit_log.write_strict_limited(
        action,
        Actor(kind=ActorKind.MEMBER, identifier="sub-abc"),
        "allowed",
        refs={"reason": "zaak-1"},
        ip=ClientAddress("203.0.113.42", vouched=False),
        limit=5,
        counted_actions=(action,),
    )

    row = await _read_audit_row(db_connection, action)
    assert json.loads(row["refs"]) == {"reason": "zaak-1", vocabulary.IP_UNVOUCHED: True}


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


async def test_write_strict_limited_refuses_an_actor_without_identifier(audit_log: AuditLog) -> None:
    # The cap is per actor; without an identifier there is nothing to count
    # against, so this is a programming error in the caller, not a runtime
    # refusal.
    with pytest.raises(ValueError):
        await audit_log.write_strict_limited(
            "test_actie", ANONYMOUS, "allowed", refs=None, ip=None, limit=5, counted_actions=("test_actie",)
        )


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
        return await insert_aged_audit_row(connection, action, result, age)
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


def test_the_job_purges_and_logs_the_count_on(
    migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    asyncio.run(_committed_row(migrated_dsn, "logout", "allowed", timedelta(days=91)))
    monkeypatch.setenv(retention.DB_URL_VAR, migrated_dsn)
    caplog.set_level("INFO")

    assert retention.main() == 0
    assert "Auditlog opgeruimd" in caplog.text


class _FailingSession:
    """Fake session whose execute() blows up inside the batch's own
    transaction, to drive _delete_in_batches's except branch without a real
    database."""

    @asynccontextmanager
    async def begin(self):
        yield

    async def execute(self, statement, params):
        raise RuntimeError("verbinding weg")


async def test_delete_in_batches_reports_a_failure_instead_of_raising() -> None:
    deleted, error = await retention._delete_in_batches(_FailingSession(), retention._DELETE_BATCH, batch=10)

    assert deleted == 0
    assert isinstance(error, RuntimeError)


async def test_purge_logs_and_continues_when_the_audit_table_delete_fails(
    migrated_dsn: str, db_connection: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mirrors test_purge_continues_the_other_table_when_one_fails, but with
    the failure on the other table: audit_log_entries fails, content_viewers
    still gets purged."""
    stale_viewer = await _committed_viewer(migrated_dsn, timedelta(days=91))

    original = retention._delete_in_batches

    async def _flaky(session, statement, *, batch):
        if statement is retention._DELETE_BATCH:
            return 0, RuntimeError("audit_log_entries kapot")
        return await original(session, statement, batch=batch)

    monkeypatch.setattr(retention, "_delete_in_batches", _flaky)

    with pytest.raises(RuntimeError):
        await purge(migrated_dsn, batch=1)

    remaining_viewers = {row["id"] for row in await db_connection.fetch("SELECT id FROM content_viewers")}
    assert stale_viewer not in remaining_viewers  # content_viewers purge still ran

    purge_row = await db_connection.fetchrow(
        "SELECT * FROM audit_log_entries WHERE action = $1", vocabulary.AUDIT_PURGE
    )
    assert purge_row is not None
    assert json.loads(purge_row["refs"]) == {"audit_log_entries": 0, "content_viewers": 1}


async def test_purge_skips_the_audit_purge_row_when_nothing_was_deleted(
    migrated_dsn: str, db_connection: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When neither table has anything to remove, purge() must not insert an
    audit_purge row of its own (that would count as something purged)."""

    async def _nothing_deleted(session, statement, *, batch):
        return 0, None

    monkeypatch.setattr(retention, "_delete_in_batches", _nothing_deleted)
    before = await db_connection.fetchval(
        "SELECT count(*) FROM audit_log_entries WHERE action = $1", vocabulary.AUDIT_PURGE
    )

    deleted = await purge(migrated_dsn, batch=1)

    assert deleted == 0
    after = await db_connection.fetchval(
        "SELECT count(*) FROM audit_log_entries WHERE action = $1", vocabulary.AUDIT_PURGE
    )
    assert after == before
