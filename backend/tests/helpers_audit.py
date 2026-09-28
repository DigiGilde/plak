"""Test double for the audit log: keeps the records instead of writing them,
plus the database helpers that fabricate aged rows.

For the tests that check WHICH record a call site writes, without a database.
test_audit.py covers the writing itself, and pins that this double keeps the
same write signature as AuditLog.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

import asyncpg
from fastapi import FastAPI

from plak.audit.log import Actor


@dataclass(frozen=True)
class Record:
    action: str
    actor: Actor
    result: str
    reason_code: str | None
    refs: dict | None
    ip: str | None


@dataclass
class AuditRecorder:
    records: list[Record] = field(default_factory=list)

    async def write(
        self,
        action: str,
        actor: Actor,
        result: str,
        reason_code: str | None = None,
        refs: dict | None = None,
        ip: str | None = None,
    ) -> None:
        self.records.append(Record(action, actor, result, reason_code, refs, ip))

    def only(self) -> Record:
        assert len(self.records) == 1, self.records
        return self.records[0]


def install_audit_recorder(app: FastAPI) -> AuditRecorder:
    recorder = AuditRecorder()
    app.state.audit_log = recorder
    return recorder


async def insert_aged_audit_row(
    connection: asyncpg.Connection,
    action: str,
    result: str,
    age: timedelta,
) -> uuid.UUID:
    """Inserts an audit row and then back-dates it, which the schema forbids:
    `audit_log_chain` stamps `occurred_at` itself and `audit_log_no_update`
    refuses the correction, so the UPDATE runs with that trigger switched off.
    The row is left with a chain link that no longer follows, the same trace an
    attacker leaves; `audit/chain.py` reports it. Only retention tests, which
    need rows older than their term, may use this.
    """
    entry_id = uuid.uuid4()
    await connection.execute(
        "INSERT INTO audit_log_entries (id, actor_kind, action, result) VALUES ($1, 'system', $2, $3)",
        entry_id,
        action,
        result,
    )
    await connection.execute("ALTER TABLE audit_log_entries DISABLE TRIGGER audit_log_no_update")
    try:
        await connection.execute(
            "UPDATE audit_log_entries SET occurred_at = now() - $2::interval WHERE id = $1",
            entry_id,
            age,
        )
    finally:
        await connection.execute("ALTER TABLE audit_log_entries ENABLE TRIGGER audit_log_no_update")
    return entry_id


# What audit_log_chain() does, with the moment given instead of read from the
# clock: same shard, same predecessor, same hash, same head.
_CHAINED_INSERT = """
WITH stamp AS (
    SELECT
        $1::uuid AS id,
        $2::text AS action,
        $3::text AS result,
        now() - $4::interval AS occurred_at,
        audit_log_chain_shard($1::uuid, $2::text, $3::text) AS shard
), previous AS (
    SELECT stamp.*, coalesce(head.chain_seq, 0) + 1 AS seq, head.chain_hash AS previous_hash
    FROM stamp
    LEFT JOIN audit_log_chain_heads AS head ON head.chain_shard = stamp.shard
), entry AS (
    INSERT INTO audit_log_entries
        (id, actor_kind, action, result, occurred_at, chain_shard, chain_seq, chain_prev_hash, chain_hash)
    SELECT id, 'system', action, result, occurred_at, shard, seq, previous_hash,
        audit_log_chain_hash(
            previous_hash, id, 'system', NULL, action, result, NULL, NULL, NULL, NULL, occurred_at, shard, seq
        )
    FROM previous
    RETURNING chain_shard, chain_seq, chain_hash, occurred_at
)
INSERT INTO audit_log_chain_heads (chain_shard, chain_seq, chain_hash, occurred_at)
SELECT chain_shard, chain_seq, chain_hash, occurred_at FROM entry
ON CONFLICT (chain_shard) DO UPDATE
SET chain_seq = EXCLUDED.chain_seq, chain_hash = EXCLUDED.chain_hash, occurred_at = EXCLUDED.occurred_at
"""


async def id_in_chain(connection: asyncpg.Connection, shard: int, action: str, result: str) -> uuid.UUID:
    """An id that sends an (action, result) row to chain `shard`: the only way
    to line up rows in one chain, since the chain follows from the id."""
    while True:
        candidate = uuid.uuid4()
        if await connection.fetchval("SELECT audit_log_chain_shard($1, $2, $3)", candidate, action, result) == shard:
            return candidate


async def insert_chained_audit_row(
    connection: asyncpg.Connection,
    action: str,
    result: str,
    age: timedelta,
    entry_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Writes an audit row as the chain trigger would have written it `age`
    ago: a valid link in its chain, unlike `insert_aged_audit_row`. The
    trigger stamps the current time and the head guard admits only the
    trigger, so both are switched off for the one statement, the way the
    schema owner could. Rows of one chain must go in oldest first, as they
    would have in real time. For tests of the purge against the chain."""
    entry_id = entry_id or uuid.uuid4()
    await connection.execute("ALTER TABLE audit_log_entries DISABLE TRIGGER audit_log_chain")
    await connection.execute("ALTER TABLE audit_log_chain_heads DISABLE TRIGGER audit_log_chain_head_guard")
    try:
        await connection.execute(_CHAINED_INSERT, entry_id, action, result, age)
    finally:
        await connection.execute("ALTER TABLE audit_log_chain_heads ENABLE TRIGGER audit_log_chain_head_guard")
        await connection.execute("ALTER TABLE audit_log_entries ENABLE TRIGGER audit_log_chain")
    return entry_id
