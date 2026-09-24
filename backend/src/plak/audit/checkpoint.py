"""Publishes the head of every audit chain, and checks the database against a
head published earlier.

The chain in `chain.py` proves that nobody edited the table with the triggers
in place. It cannot prove anything against the account that owns the schema:
that account can switch the trigger off, rewrite every row and recompute every
hash. What catches such a rewrite is a copy of the evidence somewhere the
account cannot reach, and the cheapest one is the application log: a line that
has been shipped cannot be retracted afterwards.

So this job writes one line per run with, per chain, the highest position and
its hash. Anyone who kept an older line can hold it against the database later:
a rewrite changes the hash at that position, a truncation makes the chain
shorter than it was.

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
FORMAT = "plak-audit-chain-checkpoint/2"
# /1 published only the head of each chain. A line in that format still reads,
# it simply carries nothing to check the oldest end against.
READABLE_FORMATS = frozenset({FORMAT, "plak-audit-chain-checkpoint/1"})

HASH_MISMATCH = "hash_mismatch"
CHAIN_SHORTENED = "chain_shortened"
ROW_MISSING = "row_missing"
FRONT_PURGED = "front_purged"

_logger = logging.getLogger(__name__)

# Both ends of every chain in one pass: the head is the commitment, the oldest
# surviving position says where the chain begins today, which is the only thing
# a later line can hold a purge against.
_ENDS = text(
    """
    SELECT DISTINCT ON (chain_shard)
        chain_shard,
        first_value(chain_seq) OVER chain AS first_seq,
        first_value(chain_hash) OVER chain AS first_hash,
        last_value(chain_seq) OVER chain AS seq,
        last_value(chain_hash) OVER chain AS hash
    FROM audit_log_entries
    WINDOW chain AS (
        PARTITION BY chain_shard ORDER BY chain_seq
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    )
    ORDER BY chain_shard
    """
)

# clock_timestamp(), the same clock the chain trigger stamps occurred_at with.
_NOW = text("SELECT clock_timestamp()")

_ROW_AT = text(
    """
    SELECT chain_hash
    FROM audit_log_entries
    WHERE chain_shard = :shard AND chain_seq = :seq
    """
)


@dataclass(frozen=True)
class Head:
    """Both ends of one chain. `seq`/`hash_hex` are the last row: the hash there
    covers every row before it, so it is the commitment. `first_seq`/
    `first_hash_hex` are the oldest row still present, which is what a later
    run can hold the retention purge against; they are `None` in a line
    published in format /1."""

    shard: int
    seq: int
    hash_hex: str
    first_seq: int | None = None
    first_hash_hex: str | None = None


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
            f"{head.shard}:{head.first_seq}:{head.first_hash_hex}:{head.seq}:{head.hash_hex}"
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
                    "seq": head.seq,
                    "hash": head.hash_hex,
                }
                for head in self.heads
            ],
        }
        return json.dumps(payload, separators=(",", ":"))


@dataclass(frozen=True)
class Finding:
    """One thing the database says about a published position.

    `serious` is false for the one finding the retention purge produces by
    itself; everything else contradicts what was published. A check that
    reported the purge as tampering would go off on every environment older
    than ninety days, and the real finding after it would be read as the purge
    again.
    """

    shard: int
    seq: int
    reason: str

    @property
    def serious(self) -> bool:
        return self.reason != FRONT_PURGED

    def describe(self) -> str:
        return f"keten {self.shard} positie {self.seq}: {self.reason}"


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
            first_seq=row.first_seq,
            first_hash_hex=bytes(row.first_hash).hex(),
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
        raise MalformedCheckpointError("geen JSON-object gevonden in de regel")
    try:
        payload = json.loads(line[start:])
        if payload.get("format") not in READABLE_FORMATS:
            raise MalformedCheckpointError(f"onbekend formaat: {payload.get('format')!r}")
        heads = tuple(
            Head(
                shard=int(shard["shard"]),
                seq=int(shard["seq"]),
                hash_hex=str(shard["hash"]),
                first_seq=None if shard.get("first") is None else int(shard["first"]),
                first_hash_hex=None if shard.get("first_hash") is None else str(shard["first_hash"]),
            )
            for shard in payload["shards"]
        )
        taken_at = datetime.fromisoformat(payload["taken_at"])
    except MalformedCheckpointError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise MalformedCheckpointError(str(error)) from error
    return Checkpoint(taken_at=taken_at, heads=heads)


async def _at(session: AsyncSession, shard: int, seq: int) -> str | None:
    stored = (await session.execute(_ROW_AT, {"shard": shard, "seq": seq})).scalar_one_or_none()
    return None if stored is None else bytes(stored).hex()


async def compare(dsn: str, published: Checkpoint) -> list[Finding]:
    """Holds the database against a published checkpoint, at both ends of every
    chain. An empty list means the log still contains the history that was
    published, in the same shape.

    The findings say different things on purpose:
    `chain_shortened` is a chain with fewer rows than it had, which appending
    cannot do; `hash_mismatch` is that position rewritten; `row_missing` is the
    row gone while the chain grew past it, which for a head means the whole
    chain aged out or somebody removed it; `front_purged` is the published
    oldest row gone, which is what the retention purge does every night and is
    therefore the one finding that is not an accusation - what makes it one is
    its date: no row may disappear before its retention period has run, so a
    line younger than ninety days whose front is gone is wrong.
    """
    engine = create_async_engine(dsn, hide_parameters=True)
    factory = make_session_factory(engine)
    findings: list[Finding] = []
    try:
        async with factory() as session, session.begin():
            current = {head.shard: head.seq for head in (await _collect(session)).heads}
            for head in published.heads:
                if head.first_seq is not None and head.first_seq != head.seq:
                    front = await _at(session, head.shard, head.first_seq)
                    if front is None:
                        findings.append(Finding(shard=head.shard, seq=head.first_seq, reason=FRONT_PURGED))
                    elif front != head.first_hash_hex:
                        findings.append(Finding(shard=head.shard, seq=head.first_seq, reason=HASH_MISMATCH))
                if current.get(head.shard, 0) < head.seq:
                    findings.append(Finding(shard=head.shard, seq=head.seq, reason=CHAIN_SHORTENED))
                    continue
                stored = await _at(session, head.shard, head.seq)
                if stored is None:
                    findings.append(Finding(shard=head.shard, seq=head.seq, reason=ROW_MISSING))
                elif stored != head.hash_hex:
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
    parser = argparse.ArgumentParser(description="Publiceert de kop van elke auditketen, of controleert er een.")
    parser.add_argument(
        "--against",
        metavar="BESTAND",
        help="controleer de database tegen een eerder gepubliceerde regel (- voor stdin) in plaats van te publiceren",
    )
    arguments = parser.parse_args(argv)

    dsn = os.environ.get(DB_URL_VAR)
    if not dsn:
        _logger.error("%s is niet gezet; de publicatie weet niet met welke database ze moet praten.", DB_URL_VAR)
        return 1

    if arguments.against is None:
        _logger.info("%s %s", MARKER, asyncio.run(collect(dsn)).as_json())
        return 0

    try:
        published = parse(_read_line(arguments.against))
    except (MalformedCheckpointError, OSError) as error:
        _logger.error("Kan de gepubliceerde regel niet lezen: %s", error)
        return 1
    findings = asyncio.run(compare(dsn, published))
    moment = published.taken_at.isoformat()
    for finding in findings:
        if finding.serious:
            _logger.error("Auditlog wijkt af van de publicatie van %s: %s", moment, finding.describe())
        else:
            _logger.info(
                "Opgeruimd sinds de publicatie van %s: %s. Controleer of die regel ouder is dan de bewaartermijn.",
                moment,
                finding.describe(),
            )
    if any(finding.serious for finding in findings):
        return 1
    _logger.info(
        "Database komt overeen met de publicatie van %s: %d ketens, %d regels.",
        moment,
        len(published.heads),
        published.entries_total,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
