"""Removes audit rows that have outlived their retention (BIO2 8.15.04).

Runs outside the app, on the same PLAK_DB_URL account. Which rows have expired
is known only to the database (audit_log_chain_retention() in 0001_base); this
file carries no term of its own, so the two cannot drift apart. The BEFORE
DELETE trigger, not this account, decides what may go.

The term belongs to the chain a row sits in, and a chain holds one term with
occurred_at rising along it, so what expires is always the oldest end of each
chain: the purge never leaves a gap between two surviving rows.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from plak.audit import vocabulary
from plak.db import make_session_factory
from plak.models.audit import ActorKind, AuditLogEntry

DB_URL_VAR = "PLAK_DB_URL"
BATCH_DEFAULT = 10_000

_logger = logging.getLogger(__name__)

# In batches, because the trigger fires per row: one DELETE over half a million
# rows is one long transaction that keeps the app's audit inserts waiting.
# chain_seq breaks ties in occurred_at, so each batch still takes a prefix of
# every chain and no committed batch leaves a gap behind.
_DELETE_BATCH = text(
    """
    DELETE FROM audit_log_entries
    WHERE id IN (
        SELECT id
        FROM audit_log_entries
        WHERE occurred_at <= now() - audit_log_chain_retention(chain_shard)
        ORDER BY occurred_at, chain_seq
        LIMIT :batch
    )
    """
)

# content_viewers has one term, fixed at 90 days like content_access/allowed
# (docs/audit-log.md): no per-row (action, result) lookup, so no function of
# its own next to audit_log_chain_retention().
_DELETE_CONTENT_VIEWERS_BATCH = text(
    """
    DELETE FROM content_viewers
    WHERE id IN (
        SELECT id
        FROM content_viewers
        WHERE last_seen_at <= now() - interval '90 days'
        ORDER BY last_seen_at
        LIMIT :batch
    )
    """
)


async def _delete_in_batches(session, statement, *, batch: int) -> tuple[int, Exception | None]:
    """Runs the batched delete, returning what it managed even if a later
    batch fails: each batch already commits on its own, so a partial count is
    the true number of rows removed, not a guess."""
    deleted = 0
    try:
        while True:
            async with session.begin():
                result = await session.execute(statement, {"batch": batch})
            deleted += result.rowcount
            if result.rowcount < batch:
                break
    except Exception as error:  # reported to the caller, not swallowed
        return deleted, error
    return deleted, None


async def purge(dsn: str, *, batch: int = BATCH_DEFAULT) -> int:
    """Deletes expired audit rows and content_viewers rows, and records that
    it did. The two run independently: a failure purging one table does not
    stop the other, and whatever either one deleted still lands in the
    audit_purge row."""
    engine = create_async_engine(dsn, hide_parameters=True)
    factory = make_session_factory(engine)
    try:
        async with factory() as session:
            deleted_audit, audit_error = await _delete_in_batches(session, _DELETE_BATCH, batch=batch)
            if audit_error is not None:
                _logger.error("Purging audit_log_entries failed", exc_info=audit_error)
            deleted_viewers, viewers_error = await _delete_in_batches(
                session, _DELETE_CONTENT_VIEWERS_BATCH, batch=batch
            )
            if viewers_error is not None:
                _logger.error("Purging content_viewers failed", exc_info=viewers_error)
            total = deleted_audit + deleted_viewers
            if total:
                async with session.begin():
                    session.add(
                        AuditLogEntry(
                            actor_kind=ActorKind.SYSTEM,
                            action=vocabulary.AUDIT_PURGE,
                            result=vocabulary.ALLOWED,
                            refs={"audit_log_entries": deleted_audit, "content_viewers": deleted_viewers},
                        )
                    )
            if audit_error is not None or viewers_error is not None:
                raise audit_error or viewers_error
    finally:
        await engine.dispose()
    return total


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dsn = os.environ.get(DB_URL_VAR)
    if not dsn:
        _logger.error("%s is not set; the purge does not know which database to talk to.", DB_URL_VAR)
        return 1
    deleted = asyncio.run(purge(dsn))
    _logger.info("Audit log purged: %d rows deleted", deleted)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
