"""Walks the integrity chain over audit_log_entries and reports the first
break per chain.

Every audit row is hashed on INSERT over its predecessor's hash plus its own
content (audit_log_chain() in 0001_base). Removing a row, rewriting one or
back-dating one therefore leaves every row after it in that chain with a hash
that no longer follows. This module recomputes the chain with the very same
SQL function the trigger uses, so the check cannot drift away from the write.

What it proves: nobody has edited the table with the triggers in place, and
nobody who switched them off has bothered to recompute the chain. What it does
not prove: the account Plak runs under owns this schema, so an attacker holding
those credentials can disable the trigger, rewrite rows and recompute every
hash. Only an outside verifier that kept an earlier chain hash of its own can
catch that; against the schema owner this chain raises the cost of tampering
and nothing more.

Rows dropped from either end of a chain are invisible here for the same reason
a missing first or last page is: nothing that is left points at them. At the
front that is not even suspicious, because the retention purge removes the
oldest rows of a chain as a matter of routine, so a chain that starts above
position 1 is the normal state of a system that has been running for ninety
days. Only a head published before those rows went (audit/checkpoint.py) can
tell a purge from a truncation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from plak.db import make_session_factory

DB_URL_VAR = "PLAK_DB_URL"

_logger = logging.getLogger(__name__)

SEQUENCE_GAP = "sequence_gap"
HASH_MISMATCH = "hash_mismatch"
TIME_WENT_BACK = "time_went_back"

# lag() over the chain hands each row its predecessor, so the expected hash is
# recomputed by the database in one pass. A window function may not appear in
# WHERE, hence the comparison happens in Python rather than in the query.
_WALK = text(
    """
    SELECT
        id,
        chain_shard,
        chain_seq,
        chain_hash,
        occurred_at,
        lag(chain_seq) OVER chain AS previous_seq,
        lag(occurred_at) OVER chain AS previous_occurred_at,
        audit_log_chain_hash(
            lag(chain_hash) OVER chain, id, actor_kind::text, actor_pseudonym, action,
            result, reason_code, refs, ip_truncated, ip_encrypted, occurred_at,
            chain_shard, chain_seq
        ) AS expected_hash
    FROM audit_log_entries
    WINDOW chain AS (PARTITION BY chain_shard ORDER BY chain_seq)
    ORDER BY chain_shard, chain_seq
    """
)


@dataclass(frozen=True)
class ChainBreak:
    """The first row of a chain that no longer follows from the one before it.
    Only the first is reported per chain: everything behind a break is
    unverifiable, not necessarily tampered with."""

    shard: int
    seq: int
    entry_id: uuid.UUID
    occurred_at: datetime
    reason: str

    def describe(self) -> str:
        moment = f"{self.occurred_at:%Y-%m-%d %H:%M:%S}"
        return f"keten {self.shard} positie {self.seq} (regel {self.entry_id}, {moment}): {self.reason}"


def _break_reason(row) -> str | None:
    """None when the row follows from its predecessor.

    The oldest surviving row of a chain is the exception: the purge removes
    from that end, so a chain may legitimately start above position 1, and the
    row it starts at was hashed over a predecessor that is no longer there.
    Neither its position nor its hash can be held against anything, so it
    counts as the anchor and the walk starts judging at the row after it. A gap
    between two surviving rows stays a break: the purge never leaves one, it
    only ever takes from the front.
    """
    if row.previous_seq is None:
        if row.chain_seq > 1:
            return None
        # Position 1 is checkable after all: it was hashed over no predecessor,
        # which is exactly what the recomputation assumed here.
        return HASH_MISMATCH if bytes(row.chain_hash) != bytes(row.expected_hash) else None
    expected_seq = row.previous_seq + 1
    if row.chain_seq != expected_seq:
        return SEQUENCE_GAP
    if bytes(row.chain_hash) != bytes(row.expected_hash):
        return HASH_MISMATCH
    if row.previous_occurred_at is not None and row.occurred_at < row.previous_occurred_at:
        return TIME_WENT_BACK
    return None


async def verify(dsn: str) -> list[ChainBreak]:
    """Returns the first break in each chain, in chain order; an empty list
    means every surviving row still follows from the one before it. A chain
    that no longer starts at position 1 is reported as a note, not as a break:
    what stood in front of it cannot be judged from here at all."""
    engine = create_async_engine(dsn, hide_parameters=True)
    factory = make_session_factory(engine)
    breaks: list[ChainBreak] = []
    broken_shards: set[int] = set()
    try:
        async with factory() as session, session.begin():
            result = await session.stream(_WALK)
            async for row in result:
                if row.chain_shard in broken_shards:
                    continue
                if row.previous_seq is None and row.chain_seq > 1:
                    _logger.info(
                        "Keten %d begint op positie %d: wat daarvoor stond is weg. Dat is wat de opruiming doet; "
                        "alleen een eerder gepubliceerde ketenkop kan opruimen van afkappen onderscheiden.",
                        row.chain_shard,
                        row.chain_seq,
                    )
                reason = _break_reason(row)
                if reason is None:
                    continue
                broken_shards.add(row.chain_shard)
                breaks.append(
                    ChainBreak(
                        shard=row.chain_shard,
                        seq=row.chain_seq,
                        entry_id=row.id,
                        occurred_at=row.occurred_at,
                        reason=reason,
                    )
                )
    finally:
        await engine.dispose()
    return breaks


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dsn = os.environ.get(DB_URL_VAR)
    if not dsn:
        _logger.error("%s is niet gezet; de controle weet niet met welke database ze moet praten.", DB_URL_VAR)
        return 1
    breaks = asyncio.run(verify(dsn))
    if not breaks:
        _logger.info("Auditlogketen is ongeschonden.")
        return 0
    for chain_break in breaks:
        _logger.error("Auditlogketen gebroken: %s", chain_break.describe())
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
