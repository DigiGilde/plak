"""Test double for the audit log: keeps the records instead of writing them.

For the tests that check WHICH record a call site writes, without a database.
test_audit.py covers the writing itself, and pins that this double keeps the
same write signature as AuditLog.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
