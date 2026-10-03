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

Rows dropped from the front of a chain are invisible here for the same reason
a missing first page is: nothing that is left points at them. That is not even
suspicious, because the retention purge removes the oldest rows of a chain as
a matter of routine, so a chain that starts above position 1 is the normal
state of a system that has been running for ninety days. Only a head published
before those rows went (audit/checkpoint.py) can tell a purge from a
truncation. The newest end is different: audit_log_chain_heads registers where
every chain stands, so the newest surviving row is held against that, and a
chain with no rows left is only in order once its head has outlived its term.

Every row carries its predecessor's hash (chain_prev_hash), so each row's own
hash is recomputed from the row alone, the oldest surviving one included, and
the link to the row before it is checked separately.
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
LINK_MISMATCH = "link_mismatch"
TIME_WENT_BACK = "time_went_back"
TAIL_MISSING = "tail_missing"
HEAD_MISMATCH = "head_mismatch"

# lag() over the chain hands each row its predecessor; the expected hash is
# recomputed by the database from the row's own chain_prev_hash in the same
# pass. A window function may not appear in WHERE, hence the comparison
# happens in Python rather than in the query.
_WALK = text(
    """
    SELECT
        id,
        chain_shard,
        chain_seq,
        chain_hash,
        chain_prev_hash,
        occurred_at,
        lag(chain_seq) OVER chain AS previous_seq,
        lag(chain_hash) OVER chain AS previous_hash,
        lag(occurred_at) OVER chain AS previous_occurred_at,
        audit_log_chain_hash(
            chain_prev_hash, id, actor_kind::text, actor_pseudonym, action,
            result, reason_code, refs, ip_truncated, ip_encrypted, occurred_at,
            chain_shard, chain_seq
        ) AS expected_hash
    FROM audit_log_entries
    WINDOW chain AS (PARTITION BY chain_shard ORDER BY chain_seq)
    ORDER BY chain_shard, chain_seq
    """
)

# Where every chain stands, and whether its head row may be gone by now: the
# same deadline the delete guard applies (occurred_at + term <= now).
_HEADS = text(
    """
    SELECT
        chain_shard,
        chain_seq,
        chain_hash,
        occurred_at,
        occurred_at + audit_log_chain_retention(chain_shard) <= clock_timestamp() AS expired
    FROM audit_log_chain_heads
    """
)


@dataclass(frozen=True)
class ChainBreak:
    """The first row of a chain that no longer follows from the one before it.
    Only the first is reported per chain: everything behind a break is
    unverifiable, not necessarily tampered with. `entry_id` is None for a
    break at a registered head whose row is gone; `occurred_at` is then the
    moment the head registered."""

    shard: int
    seq: int
    entry_id: uuid.UUID | None
    occurred_at: datetime
    reason: str

    def describe(self) -> str:
        moment = f"{self.occurred_at:%Y-%m-%d %H:%M:%S}"
        where = "chain head" if self.entry_id is None else f"row {self.entry_id}"
        return f"chain {self.shard} position {self.seq} ({where}, {moment}): {self.reason}"


def _hex(value: bytes | None) -> str | None:
    return None if value is None else bytes(value).hex()


def _break_reason(row) -> str | None:
    """None when the row is intact and follows from its predecessor.

    Every row's own hash is checked, the oldest surviving one included: it is
    recomputed over the predecessor's hash the row itself carries. The link to
    the predecessor is checked wherever there is one. The oldest surviving row
    of a chain that starts above position 1 is the one row without it: the
    purge removes from that end, so its predecessor may legitimately be gone,
    and only a published checkpoint can say whether it went in time. A gap
    between two surviving rows stays a break: a chain holds rows of one
    retention period, written in order of time, so the purge only ever takes a
    prefix and never leaves one.
    """
    if _hex(row.chain_hash) != _hex(row.expected_hash):
        return HASH_MISMATCH
    if row.previous_seq is None:
        # Position 1 has no predecessor, so it cannot carry a link to one.
        return LINK_MISMATCH if row.chain_seq == 1 and row.chain_prev_hash is not None else None
    if row.chain_seq != row.previous_seq + 1:
        return SEQUENCE_GAP
    if _hex(row.chain_prev_hash) != _hex(row.previous_hash):
        return LINK_MISMATCH
    if row.occurred_at < row.previous_occurred_at:
        return TIME_WENT_BACK
    return None


def _tail_break(shard: int, head, last) -> ChainBreak | None:
    """Holds a chain's newest surviving row against its registered head.

    The purge takes a chain from the front, and the head row is always the
    last of a chain to expire, so while any row is left the newest one is the
    head: a lower position is rows removed from the newest end
    (`tail_missing`), anything else a head and a row that disagree
    (`head_mismatch`). A chain with no rows left is in order only when its
    head row has passed its retention deadline, or when the head has never
    moved (position 0).
    """
    if last is None:
        if head.chain_seq == 0 or head.expired:
            return None
        return ChainBreak(shard, head.chain_seq, None, head.occurred_at, TAIL_MISSING)
    if head is not None and last.chain_seq < head.chain_seq:
        return ChainBreak(shard, head.chain_seq, None, head.occurred_at, TAIL_MISSING)
    if head is None or last.chain_seq != head.chain_seq or _hex(last.chain_hash) != _hex(head.chain_hash):
        return ChainBreak(shard, last.chain_seq, last.id, last.occurred_at, HEAD_MISMATCH)
    return None


async def verify(dsn: str) -> list[ChainBreak]:
    """Returns the first break in each chain, in chain order; an empty list
    means every surviving row still follows from the one before it. A chain
    that no longer starts at position 1 is reported as a note, not as a break:
    what stood in front of it cannot be judged from here at all."""
    # One snapshot for the rows and the heads, so an insert between the two
    # reads cannot look like a head without its row.
    engine = create_async_engine(dsn, hide_parameters=True, isolation_level="REPEATABLE READ")
    factory = make_session_factory(engine)
    breaks: list[ChainBreak] = []
    broken_shards: set[int] = set()
    newest: dict[int, object] = {}
    try:
        async with factory() as session, session.begin():
            result = await session.stream(_WALK)
            async for row in result:
                newest[row.chain_shard] = row
                if row.chain_shard in broken_shards:
                    continue
                if row.previous_seq is None and row.chain_seq > 1:
                    _logger.info(
                        "Chain %d starts at position %d: earlier rows are gone, which is expected after a purge; "
                        "only a previously published chain head can tell a purge apart from truncation.",
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
            heads = {head.chain_shard: head for head in (await session.execute(_HEADS)).all()}
    finally:
        await engine.dispose()
    for shard in sorted((heads.keys() | newest.keys()) - broken_shards):
        tail_break = _tail_break(shard, heads.get(shard), newest.get(shard))
        if tail_break is not None:
            breaks.append(tail_break)
    return sorted(breaks, key=lambda one: one.shard)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    dsn = os.environ.get(DB_URL_VAR)
    if not dsn:
        _logger.error("%s is not set; the check does not know which database to talk to.", DB_URL_VAR)
        return 1
    breaks = asyncio.run(verify(dsn))
    if not breaks:
        _logger.info("Audit log chain is intact.")
        return 0
    for chain_break in breaks:
        _logger.error("Audit log chain broken: %s", chain_break.describe())
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
