"""Tests for the integrity chain over audit_log_entries: the BEFORE INSERT
trigger from 0001_base and the verifier in plak/audit/chain.py.

Every test here commits, because `verify` opens a connection of its own and
would not see an uncommitted row. The autouse `_clean_db` fixture empties the
table before each test, so the chains always start from scratch.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest
import pytest_asyncio
from helpers_audit import id_in_chain, insert_aged_audit_row, insert_chained_audit_row
from sqlalchemy.ext.asyncio import create_async_engine

from plak.audit import chain, checkpoint, retention
from plak.audit.log import SYSTEM, AuditLog
from plak.db import make_session_factory

pytestmark = pytest.mark.asyncio

_PEPPER = "c" * 32
_IP_KEY = b"k" * 32


@pytest_asyncio.fixture
async def connection(migrated_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    """Outside a transaction, unlike the shared `db_connection` fixture: these
    tests need their rows committed."""
    open_connection = await asyncpg.connect(migrated_dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        yield open_connection
    finally:
        await open_connection.close()


@pytest_asyncio.fixture
async def audit_log(migrated_dsn: str) -> AsyncIterator[AuditLog]:
    engine = create_async_engine(migrated_dsn)
    try:
        yield AuditLog(make_session_factory(engine), pepper=_PEPPER, ip_key=_IP_KEY)
    finally:
        await engine.dispose()


async def _insert(
    connection: asyncpg.Connection, action: str = "test_actie", result: str = "allowed"
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    await connection.execute(
        "INSERT INTO audit_log_entries (id, actor_kind, action, result) VALUES ($1, 'system', $2, $3)",
        entry_id,
        action,
        result,
    )
    return entry_id


# --- What the trigger writes ----------------------------------------------------------


async def test_occurred_at_is_not_the_callers_to_choose(connection: asyncpg.Connection) -> None:
    """The one thing the app account is allowed to do is INSERT, so a forged
    timestamp needs no trigger switched off at all. The trigger overwrites it."""
    entry_id = uuid.uuid4()
    claimed = datetime(2020, 1, 1, tzinfo=UTC)
    await connection.execute(
        """
        INSERT INTO audit_log_entries (id, actor_kind, action, result, occurred_at)
        VALUES ($1, 'system', 'test_actie', 'allowed', $2)
        """,
        entry_id,
        claimed,
    )

    stored = await connection.fetchval("SELECT occurred_at FROM audit_log_entries WHERE id = $1", entry_id)
    assert stored != claimed
    assert datetime.now(tz=UTC) - stored < timedelta(minutes=5)


async def test_every_row_gets_a_chain_position(connection: asyncpg.Connection) -> None:
    for _ in range(8):
        await _insert(connection)

    rows = await connection.fetch("SELECT chain_shard, chain_seq, chain_hash FROM audit_log_entries")
    assert len(rows) == 8
    for row in rows:
        assert 0 <= row["chain_shard"] < 32
        assert row["chain_seq"] >= 1
        assert len(row["chain_hash"]) == 32
    # Each chain numbers its own rows from one upwards, without gaps.
    per_shard: dict[int, list[int]] = {}
    for row in rows:
        per_shard.setdefault(row["chain_shard"], []).append(row["chain_seq"])
    for sequence in per_shard.values():
        assert sorted(sequence) == list(range(1, len(sequence) + 1))


async def test_each_retention_term_has_chains_of_its_own(connection: asyncpg.Connection) -> None:
    """A chain holds rows of one term only: 90-day rows in chains 0 to 15,
    three-year rows in 16 to 31. Mixed, the purge would cut the short rows out
    from between the long ones."""
    for _ in range(6):
        await _insert(connection, action="content_access")
        await _insert(connection, action="admin_access", result="refused")

    rows = await connection.fetch("SELECT action, chain_shard FROM audit_log_entries")
    for row in rows:
        if row["action"] == "content_access":
            assert 0 <= row["chain_shard"] < 16
        else:
            assert 16 <= row["chain_shard"] < 32


async def test_every_row_carries_its_predecessors_hash(connection: asyncpg.Connection) -> None:
    shard = await _fill_one_shard(connection, count=3)
    rows = await connection.fetch(
        "SELECT chain_seq, chain_hash, chain_prev_hash FROM audit_log_entries WHERE chain_shard = $1 "
        "ORDER BY chain_seq",
        shard,
    )
    assert rows[0]["chain_prev_hash"] is None
    assert rows[1]["chain_prev_hash"] == rows[0]["chain_hash"]
    assert rows[2]["chain_prev_hash"] == rows[1]["chain_hash"]
    head = await connection.fetchrow("SELECT * FROM audit_log_chain_heads WHERE chain_shard = $1", shard)
    assert (head["chain_seq"], head["chain_hash"]) == (3, rows[2]["chain_hash"])


async def test_chain_position_is_taken_only_once(connection: asyncpg.Connection) -> None:
    """The unique constraint is the last line of defence under the lock: two
    rows may never claim the same predecessor."""
    entry_id = await _insert(connection)
    shard, seq = await connection.fetchrow(
        "SELECT chain_shard, chain_seq FROM audit_log_entries WHERE id = $1", entry_id
    )
    await connection.execute("ALTER TABLE audit_log_entries DISABLE TRIGGER audit_log_chain")
    try:
        with pytest.raises(asyncpg.UniqueViolationError):
            await connection.execute(
                """
                INSERT INTO audit_log_entries
                    (id, actor_kind, action, result, chain_shard, chain_seq, chain_hash)
                VALUES ($1, 'system', 'test_actie', 'allowed', $2, $3, '\\x00')
                """,
                uuid.uuid4(),
                shard,
                seq,
            )
    finally:
        await connection.execute("ALTER TABLE audit_log_entries ENABLE TRIGGER audit_log_chain")


# --- The verifier ---------------------------------------------------------------------


async def test_empty_table_has_no_broken_chain(migrated_dsn: str) -> None:
    assert await chain.verify(migrated_dsn) == []


async def test_an_ordinary_run_of_inserts_verifies(
    migrated_dsn: str, connection: asyncpg.Connection, audit_log: AuditLog
) -> None:
    for index in range(20):
        await _insert(connection, action=f"test_actie_{index}")
    await audit_log.write("test_actie_via_app", SYSTEM, "allowed", refs={"site": "voorbeeld"}, ip="203.0.113.7")

    assert await chain.verify(migrated_dsn) == []


async def test_a_deleted_row_breaks_the_chain(migrated_dsn: str, connection: asyncpg.Connection) -> None:
    """The attacker's move: switch the guards off and delete. What is left
    behind is a gap in the numbering of that chain."""
    shard = await _fill_one_shard(connection, count=3)
    victim = await connection.fetchval(
        "SELECT id FROM audit_log_entries WHERE chain_shard = $1 ORDER BY chain_seq LIMIT 1 OFFSET 1", shard
    )
    await _without_guards(connection, "DELETE FROM audit_log_entries WHERE id = $1", victim)

    breaks = await chain.verify(migrated_dsn)
    assert [(one.shard, one.seq, one.reason) for one in breaks] == [(shard, 3, chain.SEQUENCE_GAP)]


async def test_a_rewritten_row_breaks_the_chain(migrated_dsn: str, connection: asyncpg.Connection) -> None:
    shard = await _fill_one_shard(connection, count=3)
    victim = await connection.fetchval(
        "SELECT id FROM audit_log_entries WHERE chain_shard = $1 ORDER BY chain_seq LIMIT 1 OFFSET 1", shard
    )
    await _without_guards(
        connection, "UPDATE audit_log_entries SET result = 'refused' WHERE id = $1", victim
    )

    breaks = await chain.verify(migrated_dsn)
    assert [(one.shard, one.seq, one.reason) for one in breaks] == [(shard, 2, chain.HASH_MISMATCH)]


async def test_a_back_dated_row_breaks_the_chain(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    await insert_aged_audit_row(connection, "content_access", "allowed", timedelta(days=730))

    breaks = await chain.verify(migrated_dsn)
    assert [one.reason for one in breaks] == [chain.HASH_MISMATCH]


async def test_a_recomputed_back_dated_row_is_still_caught(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """An attacker who back-dates a row and recomputes its hash leaves the
    chain arithmetically sound, but the row now claims to precede the one it
    follows."""
    shard = await _fill_one_shard(connection, count=3)
    victim = await connection.fetchval(
        "SELECT id FROM audit_log_entries WHERE chain_shard = $1 ORDER BY chain_seq DESC LIMIT 1", shard
    )
    await _without_guards(
        connection,
        """
        UPDATE audit_log_entries AS target
        SET occurred_at = target.occurred_at - interval '1 day',
            chain_hash = audit_log_chain_hash(
                (SELECT before.chain_hash FROM audit_log_entries AS before
                 WHERE before.chain_shard = target.chain_shard
                   AND before.chain_seq = target.chain_seq - 1),
                target.id, target.actor_kind::text, target.actor_pseudonym, target.action,
                target.result, target.reason_code, target.refs, target.ip_truncated,
                target.ip_encrypted, target.occurred_at - interval '1 day',
                target.chain_shard, target.chain_seq)
        WHERE target.id = $1
        """,
        victim,
    )

    breaks = await chain.verify(migrated_dsn)
    assert [(one.shard, one.reason) for one in breaks] == [(shard, chain.TIME_WENT_BACK)]


async def test_only_the_first_break_of_a_chain_is_reported(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """Everything behind a break is unverifiable, not necessarily tampered
    with, so reporting it twice would only bury the one row that matters."""
    shard = await _fill_one_shard(connection, count=5)
    victims = [
        row["id"]
        for row in await connection.fetch(
            "SELECT id FROM audit_log_entries WHERE chain_shard = $1 ORDER BY chain_seq LIMIT 2 OFFSET 1",
            shard,
        )
    ]
    for victim in victims:
        await _without_guards(
            connection, "UPDATE audit_log_entries SET result = 'refused' WHERE id = $1", victim
        )

    breaks = await chain.verify(migrated_dsn)
    assert [(one.shard, one.seq) for one in breaks] == [(shard, 2)]


async def test_a_rewritten_oldest_row_is_a_break(migrated_dsn: str, connection: asyncpg.Connection) -> None:
    """The anchor is judged too: its hash is recomputed over the predecessor
    hash it carries itself, so dropping the front and then rewriting the new
    oldest row does not slip through."""
    shard = await _fill_one_shard(connection, count=4)
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq <= 2", shard
    )
    await _without_guards(
        connection,
        "UPDATE audit_log_entries SET action = 'vervalst', actor_pseudonym = 'iemand-anders' "
        "WHERE chain_shard = $1 AND chain_seq = 3",
        shard,
    )

    breaks = await chain.verify(migrated_dsn)
    assert [(one.shard, one.seq, one.reason) for one in breaks] == [(shard, 3, chain.HASH_MISMATCH)]


async def test_a_recomputed_oldest_row_no_longer_links_to_the_next(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """Recomputing the rewritten anchor's hash only moves the break one row on:
    the next row still carries the hash the anchor had."""
    shard = await _fill_one_shard(connection, count=4)
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq <= 2", shard
    )
    await _without_guards(connection, _REWRITE_AND_RECOMPUTE + " WHERE chain_shard = $1 AND chain_seq = 3", shard)

    breaks = await chain.verify(migrated_dsn)
    assert [(one.shard, one.seq, one.reason) for one in breaks] == [(shard, 4, chain.LINK_MISMATCH)]


async def test_a_row_pointing_at_another_predecessor_is_a_break(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """A row whose own hash adds up but whose predecessor hash is not the hash
    of the row before it has been spliced in from somewhere else."""
    shard = await _fill_one_shard(connection, count=3)
    await _without_guards(
        connection,
        _REPOINT_AND_RECOMPUTE + " WHERE chain_shard = $1 AND chain_seq = 2",
        shard,
    )

    breaks = await chain.verify(migrated_dsn)
    assert [(one.shard, one.seq, one.reason) for one in breaks] == [(shard, 2, chain.LINK_MISMATCH)]


async def test_position_one_cannot_claim_a_predecessor(migrated_dsn: str, connection: asyncpg.Connection) -> None:
    shard = await _fill_one_shard(connection, count=1)
    await _without_guards(
        connection,
        _REPOINT_AND_RECOMPUTE + " WHERE chain_shard = $1 AND chain_seq = 1",
        shard,
    )

    breaks = await chain.verify(migrated_dsn)
    assert [(one.shard, one.seq, one.reason) for one in breaks] == [(shard, 1, chain.LINK_MISMATCH)]


async def test_a_purged_front_is_not_a_break(
    migrated_dsn: str, connection: asyncpg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    """What the retention purge leaves behind on every environment older than
    ninety days: a chain that no longer starts at position 1. Reporting that as
    a break would make the check cry wolf as a matter of routine, and the first
    real break would be read as "the purge again"."""
    shard = await _fill_one_shard(connection, count=4)
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq <= 2", shard
    )

    with caplog.at_level("INFO"):
        assert await chain.verify(migrated_dsn) == []
    # It is not silent about it either: the rows in front of position 3 cannot
    # be judged from here at all, and a reader has to know that.
    assert f"Keten {shard} begint op positie 3" in caplog.text


async def test_a_gap_after_a_purged_front_is_still_a_break(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """The asymmetry the walk leans on: the purge only ever takes from the
    oldest end, so it never leaves a hole between two surviving rows."""
    shard = await _fill_one_shard(connection, count=5)
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq <= 2", shard
    )
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq = 4", shard
    )

    breaks = await chain.verify(migrated_dsn)
    assert [(one.shard, one.seq, one.reason) for one in breaks] == [(shard, 5, chain.SEQUENCE_GAP)]


async def test_a_rewrite_after_a_purged_front_is_still_a_break(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """Only the oldest surviving row goes unjudged; everything after it is
    checked as before."""
    shard = await _fill_one_shard(connection, count=4)
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq <= 2", shard
    )
    victim = await connection.fetchval(
        "SELECT id FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq = 4", shard
    )
    await _without_guards(connection, "UPDATE audit_log_entries SET result = 'refused' WHERE id = $1", victim)

    breaks = await chain.verify(migrated_dsn)
    assert [(one.shard, one.seq, one.reason) for one in breaks] == [(shard, 4, chain.HASH_MISMATCH)]


async def test_only_a_published_line_sees_a_truncated_front_at_all(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """Rows deleted from the oldest end leave exactly what the purge leaves,
    and the walk has nothing left that pointed at them. A line published
    before they went does see that the front moved (`front_purged`), and the
    deadline it published says this was no purge."""
    shard = await _fill_one_shard(connection, count=3)
    published = await checkpoint.collect(migrated_dsn)
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq = 1", shard
    )

    assert await chain.verify(migrated_dsn) == []
    findings = await checkpoint.compare(migrated_dsn, published)
    assert [(one.shard, one.seq, one.reason, one.serious) for one in findings] == [
        (shard, 1, checkpoint.FRONT_PURGED, True)
    ]


# --- The chain against the purge ------------------------------------------------------


async def test_the_purge_leaves_every_chain_verifiable(migrated_dsn: str, connection: asyncpg.Connection) -> None:
    """Rows of both terms, written over two hundred days. The purge takes the
    expired 90-day rows and nothing else, and because each term has chains of
    its own that is always the front of a chain: every chain still verifies,
    and a line published before the purge finds nothing but what aged out."""
    for days_ago in range(200, 0, -5):
        await insert_chained_audit_row(connection, "site_create", "allowed", timedelta(days=days_ago))
        await insert_chained_audit_row(connection, "login", "allowed", timedelta(days=days_ago, hours=-1))
    assert await chain.verify(migrated_dsn) == []
    published = await checkpoint.collect(migrated_dsn)

    deleted = await retention.purge(migrated_dsn)

    expired = len(range(200, 90, -5))
    assert deleted == expired
    counts = dict(await connection.fetch("SELECT action, count(*) FROM audit_log_entries GROUP BY action"))
    assert counts["site_create"] == 40
    assert counts["login"] == 40 - expired
    assert await chain.verify(migrated_dsn) == []
    findings = await checkpoint.compare(migrated_dsn, published)
    assert findings
    assert {one.reason for one in findings} <= {checkpoint.FRONT_PURGED, checkpoint.ROW_MISSING}
    assert not any(one.serious for one in findings)


async def test_a_chain_purged_to_nothing_continues_where_it_stood(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """A quiet chain whose rows all age out: the next row takes the next
    position and chains onto the hash that was published for the last one,
    so no position is handed out twice and no published line goes stale."""
    first = await insert_chained_audit_row(connection, "login", "allowed", timedelta(days=120))
    shard = await connection.fetchval("SELECT chain_shard FROM audit_log_entries WHERE id = $1", first)
    for days_ago in (110, 100):
        await insert_chained_audit_row(
            connection,
            "login",
            "allowed",
            timedelta(days=days_ago),
            entry_id=await id_in_chain(connection, shard, "login", "allowed"),
        )
    published = await checkpoint.collect(migrated_dsn)
    last_hash = await connection.fetchval(
        "SELECT chain_hash FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq = 3", shard
    )

    await retention.purge(migrated_dsn)
    assert await connection.fetchval("SELECT count(*) FROM audit_log_entries WHERE chain_shard = $1", shard) == 0
    findings = await checkpoint.compare(migrated_dsn, published)
    assert [(one.shard, one.reason, one.serious) for one in findings] == [
        (shard, checkpoint.FRONT_PURGED, False),
        (shard, checkpoint.ROW_MISSING, False),
    ]

    newcomer = await id_in_chain(connection, shard, "login", "allowed")
    await connection.execute(
        "INSERT INTO audit_log_entries (id, actor_kind, action, result) VALUES ($1, 'system', 'login', 'allowed')",
        newcomer,
    )
    seq, previous = await connection.fetchrow(
        "SELECT chain_seq, chain_prev_hash FROM audit_log_entries WHERE id = $1", newcomer
    )
    assert (seq, previous) == (4, last_hash)
    assert await chain.verify(migrated_dsn) == []
    assert not any(one.serious for one in await checkpoint.compare(migrated_dsn, published))


async def test_concurrent_writes_keep_the_chain_whole(
    migrated_dsn: str, connection: asyncpg.Connection, audit_log: AuditLog
) -> None:
    """Thirty audit rows written at once, the way a burst of requests writes
    them. No two may claim the same predecessor, and the chains have to verify
    afterwards."""
    await asyncio.gather(
        *(audit_log.write_strict(f"test_gelijktijdig_{index}", SYSTEM, "allowed") for index in range(30))
    )

    positions = await connection.fetch("SELECT chain_shard, chain_seq FROM audit_log_entries")
    assert len(positions) == 30
    assert len({(row["chain_shard"], row["chain_seq"]) for row in positions}) == 30
    assert await chain.verify(migrated_dsn) == []


# --- The command line -----------------------------------------------------------------


async def test_main_without_dsn_says_so(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv(chain.DB_URL_VAR, raising=False)
    with caplog.at_level("ERROR"):
        assert chain.main() == 1
    assert chain.DB_URL_VAR in caplog.text


async def test_main_reports_a_whole_chain(
    migrated_dsn: str, connection: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _insert(connection)
    monkeypatch.setenv(chain.DB_URL_VAR, migrated_dsn)
    with caplog.at_level("INFO"):
        assert await asyncio.to_thread(chain.main) == 0
    assert "ongeschonden" in caplog.text


async def test_main_reports_a_broken_chain(
    migrated_dsn: str, connection: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    shard = await _fill_one_shard(connection, count=2)
    victim = await connection.fetchval(
        "SELECT id FROM audit_log_entries WHERE chain_shard = $1 ORDER BY chain_seq DESC LIMIT 1", shard
    )
    await _without_guards(
        connection, "UPDATE audit_log_entries SET action = 'vervalst' WHERE id = $1", victim
    )

    monkeypatch.setenv(chain.DB_URL_VAR, migrated_dsn)
    with caplog.at_level("ERROR"):
        assert await asyncio.to_thread(chain.main) == 1
    assert "gebroken" in caplog.text


# --- Helpers --------------------------------------------------------------------------


# Rewrites a row and recomputes its hash over the predecessor hash it carries,
# the way the schema owner can.
_REWRITE_AND_RECOMPUTE = """
UPDATE audit_log_entries
SET action = 'vervalst',
    chain_hash = audit_log_chain_hash(
        chain_prev_hash, id, actor_kind::text, actor_pseudonym, 'vervalst', result, reason_code, refs,
        ip_truncated, ip_encrypted, occurred_at, chain_shard, chain_seq)
"""

# Points a row at a predecessor that is not there and recomputes its hash to
# match, so only the link can give it away.
_REPOINT_AND_RECOMPUTE = """
UPDATE audit_log_entries
SET chain_prev_hash = sha256('elders'::bytea),
    chain_hash = audit_log_chain_hash(
        sha256('elders'::bytea), id, actor_kind::text, actor_pseudonym, action, result, reason_code, refs,
        ip_truncated, ip_encrypted, occurred_at, chain_shard, chain_seq)
"""



async def _fill_one_shard(connection: asyncpg.Connection, *, count: int) -> int:
    """Inserts rows until one chain holds `count` of them, and returns that
    chain. Which chain a row lands in follows from its id, so this is the only
    way to get a run of consecutive rows in a single chain."""
    while True:
        await _insert(connection)
        row = await connection.fetchrow(
            "SELECT chain_shard FROM audit_log_entries GROUP BY chain_shard HAVING count(*) = $1 LIMIT 1",
            count,
        )
        if row is not None:
            return row["chain_shard"]


async def _without_guards(connection: asyncpg.Connection, statement: str, *args) -> None:
    """Runs a statement the append-only triggers would refuse, the way the
    owner of the schema can: by switching them off first."""
    await connection.execute(
        "ALTER TABLE audit_log_entries DISABLE TRIGGER audit_log_no_update, "
        "DISABLE TRIGGER audit_log_delete_after_retention"
    )
    try:
        await connection.execute(statement, *args)
    finally:
        await connection.execute(
            "ALTER TABLE audit_log_entries ENABLE TRIGGER audit_log_no_update, "
            "ENABLE TRIGGER audit_log_delete_after_retention"
        )
