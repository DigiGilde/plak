"""Publishes the head of every audit chain, and checks the database against a
head published earlier.

The chain in `chain.py` proves that nobody edited the table with the triggers
in place. It cannot prove anything against the account that owns the schema:
that account can switch the trigger off, rewrite every row and recompute every
hash. What catches such a rewrite is a copy of the evidence somewhere the
account cannot reach, and the cheapest one is the application log: a line that
has been shipped cannot be retracted afterwards.

So this job writes one line per run with, per chain, the highest position and
its hash, the oldest position still present and its hash, and for both the
moment their retention runs out. Anyone who kept an older line can hold it
against the database later: a rewrite changes the hash at that position, a
truncation makes the chain shorter than it was, and a row that is gone before
its published deadline was removed, not purged.

The hashes in that line are a commitment, not a credential. They reveal nothing
about the rows (they are hashes), and they are worth only as much as the number
of places that saw them, so they are meant to be copied, forwarded and kept.
Redacting them from the log makes the check impossible and protects nothing.

What it proves: that an outside observer holding an older line can tell that
history was rewritten. What it does not: it prevents nothing, and it catches
nothing at all if nobody kept a line.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from plak.db import make_session_factory

DB_URL_VAR = "PLAK_DB_URL"

# Grep handle. The rest of the line is one JSON object, so a log search finds
# the runs and a machine reads them without a parser of its own.
MARKER = "audit-chain-checkpoint"
# /3 is the chain layout with one set of chains per retention period. A /1 or /2
# line numbered its chains differently, so it is refused rather than held
# against chains it never described.
FORMAT = "plak-audit-chain-checkpoint/3"

HASH_MISMATCH = "hash_mismatch"
CHAIN_SHORTENED = "chain_shortened"
ROW_MISSING = "row_missing"
FRONT_PURGED = "front_purged"

_logger = logging.getLogger(__name__)

# Both ends of every chain in one pass. The head comes from
# audit_log_chain_heads, which the purge does not touch, so a chain whose rows
# have all aged out still publishes where it stands; the oldest surviving row
# says where the chain begins today, which is the only thing a later line can
# hold a purge against. Each end carries the moment its retention runs out.
_ENDS = text(
    """
    SELECT
        head.chain_shard,
        head.chain_seq AS seq,
        head.chain_hash AS hash,
        head.occurred_at + audit_log_chain_retention(head.chain_shard) AS expires,
        front.chain_seq AS first_seq,
        front.chain_hash AS first_hash,
        front.occurred_at + audit_log_chain_retention(head.chain_shard) AS first_expires
    FROM audit_log_chain_heads AS head
    LEFT JOIN LATERAL (
        SELECT chain_seq, chain_hash, occurred_at
        FROM audit_log_entries
        WHERE chain_shard = head.chain_shard
        ORDER BY chain_seq
        LIMIT 1
    ) AS front ON true
    ORDER BY head.chain_shard
    """
)

# clock_timestamp(), the same clock the chain trigger stamps occurred_at with.
_NOW = text("SELECT clock_timestamp()")

# The stored hash and the hash recomputed from the row's content: a row whose
# content was changed while its chain_hash was left alone matches on the first
# and not on the second.
_ROW_AT = text(
    """
    SELECT
        chain_hash,
        audit_log_chain_hash(
            chain_prev_hash, id, actor_kind::text, actor_pseudonym, action,
            result, reason_code, refs, ip_truncated, ip_encrypted, occurred_at,
            chain_shard, chain_seq
        ) AS recomputed
    FROM audit_log_entries
    WHERE chain_shard = :shard AND chain_seq = :seq
    """
)


def _moment(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _read_moment(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


@dataclass(frozen=True)
class Head:
    """Both ends of one chain. `seq`/`hash_hex` are the last row: the hash there
    covers every row before it, so it is the commitment. `first_seq`/
    `first_hash_hex` are the oldest row still present, which is what a later
    run can hold the retention purge against; they are `None` for a chain
    whose rows have all aged out. `expires`/`first_expires` are when those
    rows may go: before that moment, their absence is a removal."""

    shard: int
    seq: int
    hash_hex: str
    expires: datetime
    first_seq: int | None = None
    first_hash_hex: str | None = None
    first_expires: datetime | None = None


@dataclass(frozen=True)
class Checkpoint:
    """What one run published. `entries_total` is the sum of the positions:
    the number of rows ever written to the table, which the retention purge
    does not lower (a deleted row keeps its position spent)."""

    taken_at: datetime
    heads: tuple[Head, ...]

    @property
    def entries_total(self) -> int:
        return sum(head.seq for head in self.heads)

    @property
    def digest(self) -> str:
        """One value to compare two lines by eye or in a ticket; the heads
        themselves stay in the line, because a digest alone cannot say which
        chain moved."""
        material = ";".join(
            f"{head.shard}:{head.first_seq}:{head.first_hash_hex}:{_moment(head.first_expires)}:"
            f"{head.seq}:{head.hash_hex}:{_moment(head.expires)}"
            for head in self.heads
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def as_json(self) -> str:
        payload = {
            "format": FORMAT,
            "taken_at": self.taken_at.isoformat(),
            "entries_total": self.entries_total,
            "digest": self.digest,
            "shards": [
                {
                    "shard": head.shard,
                    "first": head.first_seq,
                    "first_hash": head.first_hash_hex,
                    "first_expires": _moment(head.first_expires),
                    "seq": head.seq,
                    "hash": head.hash_hex,
                    "expires": _moment(head.expires),
                }
                for head in self.heads
            ],
        }
        return json.dumps(payload, separators=(",", ":"))


@dataclass(frozen=True)
class Finding:
    """One thing the database says about a published position.

    `serious` is false only for a published row that is gone after its
    published retention deadline: that is what the purge does by itself, and
    a check that reported it as tampering would go off on every environment
    older than ninety days, so the real finding after it would be read as the
    purge again. The same absence before the deadline is a removal.
    """

    shard: int
    seq: int
    reason: str
    serious: bool = True

    def describe(self) -> str:
        return f"chain {self.shard} position {self.seq}: {self.reason}"


class MalformedCheckpointError(ValueError):
    """The line handed in is not a checkpoint this version can read."""


async def _collect(session: AsyncSession) -> Checkpoint:
    taken_at = (await session.execute(_NOW)).scalar_one()
    rows = (await session.execute(_ENDS)).all()
    heads = tuple(
        Head(
            shard=row.chain_shard,
            seq=row.seq,
            hash_hex=bytes(row.hash).hex(),
            expires=row.expires,
            first_seq=row.first_seq,
            first_hash_hex=None if row.first_hash is None else bytes(row.first_hash).hex(),
            first_expires=row.first_expires,
        )
        for row in rows
    )
    return Checkpoint(taken_at=taken_at, heads=heads)


async def collect(dsn: str) -> Checkpoint:
    """Reads the head of every chain, with the database's own clock: the same
    clock that stamps `occurred_at`, so the two are comparable."""
    engine = create_async_engine(dsn, hide_parameters=True)
    factory = make_session_factory(engine)
    try:
        async with factory() as session, session.begin():
            return await _collect(session)
    finally:
        await engine.dispose()


def parse(line: str) -> Checkpoint:
    """Reads back a published line. Everything before the first `{` is ignored,
    so a line copied straight out of the log, timestamp and marker and all,
    works as well as the bare JSON."""
    start = line.find("{")
    if start < 0:
        raise MalformedCheckpointError("no JSON object found in the line")
    try:
        payload = json.loads(line[start:])
        if payload.get("format") != FORMAT:
            raise MalformedCheckpointError(f"unknown format: {payload.get('format')!r}")
        heads = tuple(
            Head(
                shard=int(shard["shard"]),
                seq=int(shard["seq"]),
                hash_hex=str(shard["hash"]),
                expires=datetime.fromisoformat(shard["expires"]),
                first_seq=None if shard["first"] is None else int(shard["first"]),
                first_hash_hex=None if shard["first_hash"] is None else str(shard["first_hash"]),
                first_expires=_read_moment(shard["first_expires"]),
            )
            for shard in payload["shards"]
        )
        taken_at = datetime.fromisoformat(payload["taken_at"])
    except MalformedCheckpointError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise MalformedCheckpointError(str(error)) from error
    return Checkpoint(taken_at=taken_at, heads=heads)


async def _at(session: AsyncSession, shard: int, seq: int) -> tuple[str, str] | None:
    """The stored and the recomputed hash at a position, or None if the row is gone."""
    row = (await session.execute(_ROW_AT, {"shard": shard, "seq": seq})).one_or_none()
    return None if row is None else (bytes(row.chain_hash).hex(), bytes(row.recomputed).hex())


def _matches(found: tuple[str, str], published_hex: str) -> bool:
    return found == (published_hex, published_hex)


async def compare(dsn: str, published: Checkpoint) -> list[Finding]:
    """Holds the database against a published checkpoint, at both ends of every
    chain. An empty list means the log still contains the history that was
    published, in the same shape.

    The findings say different things on purpose:
    `chain_shortened` is a chain whose head stands lower than it did, which
    appending cannot do; `hash_mismatch` is that position rewritten, whether
    its stored hash changed or only its content; `row_missing` is the
    published head row gone while the chain's head stands at or past it;
    `front_purged` is the published oldest row gone. The last two are what
    the retention purge does, and the deadline published next to the row
    decides between purge and removal: gone after it is not serious, gone
    before it is.
    """
    engine = create_async_engine(dsn, hide_parameters=True)
    factory = make_session_factory(engine)
    findings: list[Finding] = []
    try:
        async with factory() as session, session.begin():
            now = (await session.execute(_NOW)).scalar_one()
            current = {head.shard: head.seq for head in (await _collect(session)).heads}
            for head in published.heads:
                if head.first_seq is not None and head.first_seq != head.seq:
                    front = await _at(session, head.shard, head.first_seq)
                    if front is None:
                        findings.append(
                            Finding(
                                shard=head.shard,
                                seq=head.first_seq,
                                reason=FRONT_PURGED,
                                serious=head.first_expires is None or now < head.first_expires,
                            )
                        )
                    elif not _matches(front, head.first_hash_hex):
                        findings.append(Finding(shard=head.shard, seq=head.first_seq, reason=HASH_MISMATCH))
                if current.get(head.shard, 0) < head.seq:
                    findings.append(Finding(shard=head.shard, seq=head.seq, reason=CHAIN_SHORTENED))
                    continue
                stored = await _at(session, head.shard, head.seq)
                if stored is None:
                    findings.append(
                        Finding(shard=head.shard, seq=head.seq, reason=ROW_MISSING, serious=now < head.expires)
                    )
                elif not _matches(stored, head.hash_hex):
                    findings.append(Finding(shard=head.shard, seq=head.seq, reason=HASH_MISMATCH))
    finally:
        await engine.dispose()
    return findings


def _read_line(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    return Path(source).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Publish the head of every audit chain, or check the database against a published one."
    )
    parser.add_argument(
        "--against",
        metavar="FILE",
        help="check the database against a previously published line (- for stdin) instead of publishing",
    )
    arguments = parser.parse_args(argv)

    dsn = os.environ.get(DB_URL_VAR)
    if not dsn:
        _logger.error("Cannot publish: %s is not set", DB_URL_VAR)
        return 1

    if arguments.against is None:
        _logger.info("%s %s", MARKER, asyncio.run(collect(dsn)).as_json())
        return 0

    try:
        published = parse(_read_line(arguments.against))
    except (MalformedCheckpointError, OSError) as error:
        _logger.error("Cannot read the published line: %s", error)
        return 1
    findings = asyncio.run(compare(dsn, published))
    moment = published.taken_at.isoformat()
    for finding in findings:
        if finding.serious:
            _logger.error("Audit log differs from the checkpoint published at %s: %s", moment, finding.describe())
        else:
            _logger.info(
                "Purged since the checkpoint published at %s, after the retention period: %s.",
                moment,
                finding.describe(),
            )
    if any(finding.serious for finding in findings):
        return 1
    _logger.info(
        "Database matches the checkpoint published at %s: %d chains, %d rows.",
        moment,
        len(published.heads),
        published.entries_total,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
