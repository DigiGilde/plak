"""Test double for the audit log: keeps the records instead of writing them,
plus the one database helper that fabricates an aged row.

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
