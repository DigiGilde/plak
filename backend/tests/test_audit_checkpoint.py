"""Tests for publishing the head of every audit chain and for checking the
database against a head published earlier (plak/audit/checkpoint.py).

The interesting case is the one `chain.py` cannot see: an attacker who owns the
schema, rewrites a row and recomputes the hashes. The chain then verifies, and
only a line published before the rewrite still says otherwise.

Every test here commits, because `collect` and `compare` open a connection of
their own. The autouse `_clean_db` fixture empties the table before each test.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import asyncpg
import pytest
import pytest_asyncio

from plak.audit import chain, checkpoint

# asyncio_mode = "auto" (pyproject.toml) picks up the async tests by itself; this
# file deliberately mixes sync and async tests, so no module-wide asyncio marker.


@pytest_asyncio.fixture
async def connection(migrated_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    open_connection = await asyncpg.connect(migrated_dsn.replace("postgresql+asyncpg://", "postgresql://", 1))
    try:
        yield open_connection
    finally:
        await open_connection.close()


async def _insert(connection: asyncpg.Connection, action: str = "test_actie") -> uuid.UUID:
    entry_id = uuid.uuid4()
    await connection.execute(
        "INSERT INTO audit_log_entries (id, actor_kind, action, result) VALUES ($1, 'system', $2, 'allowed')",
        entry_id,
        action,
    )
    return entry_id


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


async def _rewrite_and_recompute(connection: asyncpg.Connection, entry_id: uuid.UUID) -> None:
    """What the verifier cannot catch: the row is changed and its hash is
    recomputed with the database's own function, so the chain still adds up."""
    await _without_guards(
        connection,
        """
        UPDATE audit_log_entries AS target
        SET action = 'vervalst',
            chain_hash = audit_log_chain_hash(
                (SELECT before.chain_hash FROM audit_log_entries AS before
                 WHERE before.chain_shard = target.chain_shard
                   AND before.chain_seq = target.chain_seq - 1),
                target.id, target.actor_kind::text, target.actor_pseudonym, 'vervalst',
                target.result, target.reason_code, target.refs, target.ip_truncated,
                target.ip_encrypted, target.occurred_at, target.chain_shard, target.chain_seq)
        WHERE target.id = $1
        """,
        entry_id,
    )


async def _a_head(connection: asyncpg.Connection) -> tuple[uuid.UUID, int, int]:
    """The last row of the busiest chain: id, shard and position."""
    row = await connection.fetchrow(
        """
        SELECT DISTINCT ON (chain_shard) id, chain_shard, chain_seq
        FROM audit_log_entries
        ORDER BY chain_shard, chain_seq DESC
        """
    )
    return row["id"], row["chain_shard"], row["chain_seq"]


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


# --- What is published ----------------------------------------------------------------


async def test_an_empty_table_publishes_an_empty_checkpoint(migrated_dsn: str) -> None:
    published = await checkpoint.collect(migrated_dsn)
    assert published.heads == ()
    assert published.entries_total == 0


async def test_every_chain_with_rows_gets_both_its_ends(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    for index in range(20):
        await _insert(connection, action=f"test_actie_{index}")

    published = await checkpoint.collect(migrated_dsn)
    # One head per chain that has rows, each at the highest position in it, and
    # together they account for every row written.
    assert published.entries_total == 20
    for head in published.heads:
        highest = await connection.fetchval(
            "SELECT max(chain_seq) FROM audit_log_entries WHERE chain_shard = $1", head.shard
        )
        assert head.seq == highest
        stored = await connection.fetchval(
            "SELECT chain_hash FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq = $2",
            head.shard,
            head.seq,
        )
        assert head.hash_hex == bytes(stored).hex()
        # And the other end, which is what a later run holds the purge against.
        lowest = await connection.fetchval(
            "SELECT min(chain_seq) FROM audit_log_entries WHERE chain_shard = $1", head.shard
        )
        assert head.first_seq == lowest


async def test_the_line_is_one_json_object_behind_a_grep_handle(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    await _insert(connection)
    published = await checkpoint.collect(migrated_dsn)

    payload = json.loads(published.as_json())
    assert payload["format"] == checkpoint.FORMAT
    assert payload["entries_total"] == 1
    assert payload["shards"] == [
        {
            "shard": head.shard,
            "first": head.first_seq,
            "first_hash": head.first_hash_hex,
            "seq": head.seq,
            "hash": head.hash_hex,
        }
        for head in published.heads
    ]
    assert payload["taken_at"] == published.taken_at.isoformat()
    assert payload["digest"] == published.digest
    assert "\n" not in published.as_json()


async def test_the_digest_follows_the_heads(migrated_dsn: str, connection: asyncpg.Connection) -> None:
    await _insert(connection)
    first = await checkpoint.collect(migrated_dsn)
    await _insert(connection)
    second = await checkpoint.collect(migrated_dsn)

    assert first.digest != second.digest
    assert second.entries_total == 2


# --- Reading a published line back ----------------------------------------------------


def test_a_published_line_reads_back_as_it_was_written() -> None:
    line = checkpoint.Checkpoint(
        taken_at=datetime.fromisoformat("2026-09-24T10:00:00+00:00"),
        heads=(checkpoint.Head(shard=3, seq=7, hash_hex="ab" * 32),),
    ).as_json()

    assert checkpoint.parse(line) == checkpoint.parse(f"INFO {checkpoint.MARKER} {line}")
    assert checkpoint.parse(line).heads[0].seq == 7


def test_a_line_that_is_not_a_checkpoint_is_refused() -> None:
    for line in ("", "INFO iets anders", '{"format":"iets-anders/1"}', '{"format":"' + checkpoint.FORMAT + '"}'):
        with pytest.raises(checkpoint.MalformedCheckpointError):
            checkpoint.parse(line)


# --- Holding the database against it --------------------------------------------------


async def test_an_untouched_log_matches_its_publication(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    for index in range(10):
        await _insert(connection, action=f"test_actie_{index}")
    published = checkpoint.parse((await checkpoint.collect(migrated_dsn)).as_json())

    # Rows written after the publication do not make it stop matching: a chain
    # is only allowed to grow.
    await _insert(connection, action="test_actie_later")

    assert await checkpoint.compare(migrated_dsn, published) == []


async def test_a_rewrite_the_chain_cannot_see_shows_up_against_the_publication(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """The whole point of publishing: the chain verifies, and the line from
    before the rewrite says the history is not the one that was published."""
    for index in range(10):
        await _insert(connection, action=f"test_actie_{index}")
    published = checkpoint.parse((await checkpoint.collect(migrated_dsn)).as_json())
    entry_id, shard, seq = await _a_head(connection)
    await _rewrite_and_recompute(connection, entry_id)

    assert await chain.verify(migrated_dsn) == []
    assert [(one.shard, one.seq, one.reason) for one in await checkpoint.compare(migrated_dsn, published)] == [
        (shard, seq, checkpoint.HASH_MISMATCH)
    ]


async def test_rows_dropped_off_the_end_show_up_against_the_publication(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """Also invisible to the chain: nothing is left that pointed at the rows."""
    for index in range(10):
        await _insert(connection, action=f"test_actie_{index}")
    published = checkpoint.parse((await checkpoint.collect(migrated_dsn)).as_json())
    _, shard, seq = await _a_head(connection)
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq = $2", shard, seq
    )

    assert await chain.verify(migrated_dsn) == []
    assert [(one.shard, one.seq, one.reason) for one in await checkpoint.compare(migrated_dsn, published)] == [
        (shard, seq, checkpoint.CHAIN_SHORTENED)
    ]


async def test_a_published_row_removed_from_the_middle_is_reported_as_missing(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """Separate from a shortened chain, because the retention purge removes old
    rows too: this one says the position is gone while the chain grew past it,
    not that the log went backwards."""
    for index in range(10):
        await _insert(connection, action=f"test_actie_{index}")
    published = checkpoint.parse((await checkpoint.collect(migrated_dsn)).as_json())
    _, shard, seq = await _a_head(connection)
    highest = "SELECT max(chain_seq) FROM audit_log_entries WHERE chain_shard = $1"
    while (await connection.fetchval(highest, shard)) <= seq:
        await _insert(connection, action="test_actie_later")
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq = $2", shard, seq
    )

    assert [(one.shard, one.seq, one.reason) for one in await checkpoint.compare(migrated_dsn, published)] == [
        (shard, seq, checkpoint.ROW_MISSING)
    ]


async def test_an_emptied_table_shows_up_against_the_publication(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """TRUNCATE fires no row trigger, so the log can be emptied in one command
    and the chain has nothing left to say."""
    for index in range(10):
        await _insert(connection, action=f"test_actie_{index}")
    published = checkpoint.parse((await checkpoint.collect(migrated_dsn)).as_json())
    await connection.execute("TRUNCATE audit_log_entries")

    assert await chain.verify(migrated_dsn) == []
    findings = await checkpoint.compare(migrated_dsn, published)
    shortened = [one for one in findings if one.reason == checkpoint.CHAIN_SHORTENED]
    assert {one.shard for one in shortened} == {head.shard for head in published.heads}
    assert all(one.serious for one in shortened)


async def test_a_purged_front_is_reported_but_is_not_an_accusation(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """The purge takes the oldest rows every night, so a published front that
    is gone is the normal state of an old environment. It is reported, because
    only its date says whether it was allowed to go, and it does not make the
    check fail."""
    shard = await _fill_one_shard(connection, count=3)
    published = checkpoint.parse((await checkpoint.collect(migrated_dsn)).as_json())
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq = 1", shard
    )

    findings = await checkpoint.compare(migrated_dsn, published)
    assert [(one.shard, one.seq, one.reason) for one in findings] == [(shard, 1, checkpoint.FRONT_PURGED)]
    assert not any(one.serious for one in findings)


async def test_a_rewritten_front_row_is_a_mismatch(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """Rewriting the oldest rows is what someone rewriting history does; as
    long as they are still there, the published line pins them down."""
    shard = await _fill_one_shard(connection, count=3)
    published = checkpoint.parse((await checkpoint.collect(migrated_dsn)).as_json())
    victim = await connection.fetchval(
        "SELECT id FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq = 1", shard
    )
    await _rewrite_and_recompute(connection, victim)

    findings = await checkpoint.compare(migrated_dsn, published)
    assert (shard, 1, checkpoint.HASH_MISMATCH) in [(one.shard, one.seq, one.reason) for one in findings]
    assert all(one.serious for one in findings)


async def test_a_line_in_the_older_format_reads_without_a_front(
    migrated_dsn: str, connection: asyncpg.Connection
) -> None:
    """Format /1 published only the head. Such a line still checks what it
    carries; it simply says nothing about the oldest end."""
    await _insert(connection)
    published = await checkpoint.collect(migrated_dsn)
    older = json.loads(published.as_json())
    older["format"] = "plak-audit-chain-checkpoint/1"
    for shard in older["shards"]:
        del shard["first"], shard["first_hash"]

    read_back = checkpoint.parse(json.dumps(older))
    assert read_back.heads[0].first_seq is None
    assert await checkpoint.compare(migrated_dsn, read_back) == []


# --- The command line -----------------------------------------------------------------


async def test_main_without_dsn_says_so(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv(checkpoint.DB_URL_VAR, raising=False)
    with caplog.at_level("ERROR"):
        assert checkpoint.main([]) == 1
    assert checkpoint.DB_URL_VAR in caplog.text


async def test_main_publishes_a_line_that_reads_back(
    migrated_dsn: str, connection: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _insert(connection)
    monkeypatch.setenv(checkpoint.DB_URL_VAR, migrated_dsn)
    with caplog.at_level("INFO"):
        assert await asyncio.to_thread(checkpoint.main, []) == 0

    line = next(record.getMessage() for record in caplog.records if checkpoint.MARKER in record.getMessage())
    assert await checkpoint.compare(migrated_dsn, checkpoint.parse(line)) == []


async def test_main_checks_the_database_against_a_published_line(
    migrated_dsn: str, connection: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, tmp_path,
) -> None:
    for index in range(10):
        await _insert(connection, action=f"test_actie_{index}")
    line = tmp_path / "kop.log"
    line.write_text(f"INFO {checkpoint.MARKER} {(await checkpoint.collect(migrated_dsn)).as_json()}\n")
    monkeypatch.setenv(checkpoint.DB_URL_VAR, migrated_dsn)

    with caplog.at_level("INFO"):
        assert await asyncio.to_thread(checkpoint.main, ["--against", str(line)]) == 0
    assert "komt overeen" in caplog.text

    entry_id, _, _ = await _a_head(connection)
    await _rewrite_and_recompute(connection, entry_id)
    caplog.clear()
    with caplog.at_level("ERROR"):
        assert await asyncio.to_thread(checkpoint.main, ["--against", str(line)]) == 1
    assert checkpoint.HASH_MISMATCH in caplog.text


async def test_main_reads_the_published_line_from_stdin(
    migrated_dsn: str, connection: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await _insert(connection)
    line = f"INFO {checkpoint.MARKER} {(await checkpoint.collect(migrated_dsn)).as_json()}\n"
    monkeypatch.setenv(checkpoint.DB_URL_VAR, migrated_dsn)
    monkeypatch.setattr("sys.stdin", io.StringIO(line))

    with caplog.at_level("INFO"):
        assert await asyncio.to_thread(checkpoint.main, ["--against", "-"]) == 0
    assert "komt overeen" in caplog.text


async def test_main_does_not_fail_on_what_the_purge_did(
    migrated_dsn: str, connection: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, tmp_path,
) -> None:
    """A nightly check that goes red on the nightly purge would be turned off
    within a week, so this one reports it and exits 0."""
    shard = await _fill_one_shard(connection, count=3)
    line = tmp_path / "kop.log"
    line.write_text(f"INFO {checkpoint.MARKER} {(await checkpoint.collect(migrated_dsn)).as_json()}\n")
    await _without_guards(
        connection, "DELETE FROM audit_log_entries WHERE chain_shard = $1 AND chain_seq = 1", shard
    )
    monkeypatch.setenv(checkpoint.DB_URL_VAR, migrated_dsn)

    with caplog.at_level("INFO"):
        assert await asyncio.to_thread(checkpoint.main, ["--against", str(line)]) == 0
    assert "Opgeruimd sinds de publicatie" in caplog.text
    assert "bewaartermijn" in caplog.text


async def test_main_says_so_when_the_line_cannot_be_read(
    migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path
) -> None:
    monkeypatch.setenv(checkpoint.DB_URL_VAR, migrated_dsn)
    with caplog.at_level("ERROR"):
        assert checkpoint.main(["--against", str(tmp_path / "bestaat-niet.log")]) == 1
    assert "niet lezen" in caplog.text

    rubbish = tmp_path / "rommel.log"
    rubbish.write_text("dit is geen publicatie\n")
    caplog.clear()
    with caplog.at_level("ERROR"):
        assert checkpoint.main(["--against", str(rubbish)]) == 1
    assert "niet lezen" in caplog.text
