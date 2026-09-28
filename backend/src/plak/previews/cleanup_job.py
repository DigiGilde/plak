"""Preview cleanup job: daily sweep at 03:00.

Cleans up: expired previews (row, version row and file tree), orphaned
preview versions (target=preview without a matching preview row, left behind
by an interrupted upsert/teardown), stale `_tmp` directories in the
ContentStore, and expired CLI device authorizations and CLI sessions.

`delete_expired` is the core and can be called on its own by tests; main.py
starts the background loop through `cleanup_job()`, an async context manager,
inside its lifespan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plak.cli import service as cli
from plak.ingest.store import ContentStore
from plak.models.publication import Preview, Version, VersionTarget

TIMESTAMP_DEFAULT = time(3, 0)
TMP_OLDER_THAN_DEFAULT = timedelta(hours=24)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupResult:
    expired_previews: int
    orphan_versions: int
    tmp_swept: int
    cli_device_authorizations: int = 0
    cli_sessions: int = 0


async def _cleanup_expired_previews(
    factory: async_sessionmaker[AsyncSession], store: ContentStore, now: datetime
) -> int:
    async with factory() as db:
        async with db.begin():
            # One statement that decides and deletes: a concurrent same-ref
            # deploy may renew a row (new version, later expiry) after any
            # separate SELECT, and Postgres re-checks this predicate against
            # the renewed row. RETURNING gives the version each deleted row
            # pointed at when it was deleted.
            version_ids = (
                await db.scalars(
                    delete(Preview)
                    .where(Preview.expires_at.is_not(None), Preview.expires_at < now)
                    .returning(Preview.version_id)
                )
            ).all()
            storage_refs = []
            if version_ids:
                storage_refs = (
                    await db.scalars(
                        delete(Version).where(Version.id.in_(version_ids)).returning(Version.storage_ref)
                    )
                ).all()
        for storage_ref in storage_refs:
            store.delete_version(storage_ref)
        return len(version_ids)


async def _cleanup_orphan_preview_versions(
    factory: async_sessionmaker[AsyncSession], store: ContentStore
) -> int:
    async with factory() as db:
        async with db.begin():
            orphan_selection = select(Version.id, Version.storage_ref).where(
                Version.target == VersionTarget.PREVIEW,
                Version.id.not_in(select(Preview.version_id)),
            )
            rows = (await db.execute(orphan_selection)).all()
            ids = [row.id for row in rows]
            if ids:
                await db.execute(delete(Version).where(Version.id.in_(ids)))
        for row in rows:
            store.delete_version(row.storage_ref)
        return len(rows)


async def delete_expired(
    factory: async_sessionmaker[AsyncSession],
    store: ContentStore,
    now: datetime,
    *,
    tmp_older_than: timedelta = TMP_OLDER_THAN_DEFAULT,
) -> CleanupResult:
    """Runs the full sweep once; callable straight from tests."""
    expired = await _cleanup_expired_previews(factory, store, now)
    orphans = await _cleanup_orphan_preview_versions(factory, store)
    swept = store.sweep_tmp(tmp_older_than)
    async with factory() as db:
        authorizations, cli_sessions = await cli.delete_expired(db, now)
    return CleanupResult(
        expired_previews=expired,
        orphan_versions=orphans,
        tmp_swept=swept,
        cli_device_authorizations=authorizations,
        cli_sessions=cli_sessions,
    )


def _seconds_until(occurred_at: time, reference: datetime) -> float:
    candidate = reference.replace(
        hour=occurred_at.hour, minute=occurred_at.minute, second=occurred_at.second, microsecond=0
    )
    if candidate <= reference:
        candidate += timedelta(days=1)
    return (candidate - reference).total_seconds()


async def _run_daily(
    factory: async_sessionmaker[AsyncSession],
    store: ContentStore,
    *,
    occurred_at: time,
    tmp_older_than: timedelta,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        wait_time = _seconds_until(occurred_at, datetime.now(tz=UTC))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=wait_time)
        if stop.is_set():
            break
        try:
            await delete_expired(factory, store, datetime.now(tz=UTC), tmp_older_than=tmp_older_than)
        except Exception:
            # A transient failure (DB hiccup, ...) must not end the loop: the
            # process would then keep running with no sweep for the rest of
            # its life. Log loud, wait for the next scheduled run.
            _logger.exception("Dagelijkse opschoning mislukt, volgende poging op de volgende ronde")


@asynccontextmanager
async def cleanup_job(
    factory: async_sessionmaker[AsyncSession],
    store: ContentStore,
    *,
    occurred_at: time = TIMESTAMP_DEFAULT,
    tmp_older_than: timedelta = TMP_OLDER_THAN_DEFAULT,
) -> AsyncIterator[asyncio.Task]:
    """Background task that runs the sweep daily at `occurred_at` (UTC).

    Meant to be opened inside the lifespan of main.py:
    `async with cleanup_job(factory, store): yield`.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(
        _run_daily(factory, store, occurred_at=occurred_at, tmp_older_than=tmp_older_than, stop=stop)
    )
    try:
        yield task
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
