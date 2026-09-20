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
            rows = (
                await db.execute(
                    select(Preview.id, Preview.version_id, Version.storage_ref)
                    .join(Version, Version.id == Preview.version_id)
                    .where(Preview.expires_at.is_not(None), Preview.expires_at < now)
                )
            ).all()
            for row in rows:
                # Delete the preview row first and only then the version: the
                # FK previews.version_id otherwise refuses deleting the version
                # row first.
                await db.execute(delete(Preview).where(Preview.id == row.id))
                await db.execute(delete(Version).where(Version.id == row.version_id))
        for row in rows:
            store.delete_version(row.storage_ref)
        return len(rows)


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
        await delete_expired(factory, store, datetime.now(tz=UTC), tmp_older_than=tmp_older_than)


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
