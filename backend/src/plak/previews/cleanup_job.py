"""Preview cleanup job: daily sweep at 03:00.

Cleans up: expired previews (row, version row and file tree), orphaned
preview versions (target=preview without a matching preview row, left behind
by an interrupted upsert/teardown), live versions older than the ones a
site keeps (its own number, else PLAK_LIVE_VERSIONS_KEPT; row and file tree),
stale `_tmp` directories in the ContentStore, version and site directories that
lost their row (set aside in `_reclaimed` first, removed a week later),
expired CLI device authorizations and CLI sessions, and the old slugs of
groups and sites whose redirect has ended, which frees them for anyone.

`delete_expired` is the core and can be called on its own by tests; main.py
starts the background loop through `cleanup_job()`, an async context manager,
inside its lifespan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import delete, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plak.audit import vocabulary
from plak.cli import service as cli
from plak.ingest.store import ContentStore
from plak.models.audit import ActorKind, AuditLogEntry
from plak.models.identity import Group
from plak.models.publication import Preview, Site, Version, VersionTarget
from plak.models.slugs import GroupSlug, SiteSlug
from plak.slug_window import still_redirects

TIMESTAMP_DEFAULT = time(3, 0)
TMP_OLDER_THAN_DEFAULT = timedelta(hours=24)
# A directory without a row is left alone this long, so a publish between its
# rename and its insert is never touched.
ORPHAN_OLDER_THAN = timedelta(hours=24)
# How long a reclaimed directory waits in `_reclaimed` before it is removed:
# the time to put it back when the database turns out to be the one that is
# wrong.
RECLAIMED_KEPT = timedelta(days=7)

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CleanupResult:
    expired_previews: int
    orphan_versions: int
    tmp_swept: int
    cli_device_authorizations: int = 0
    cli_sessions: int = 0
    old_live_versions: int = 0
    orphan_directories: int = 0
    held_directories: int = 0
    reclaimed_swept: int = 0
    released_slugs: int = 0


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


async def _cleanup_site_live_versions(
    factory: async_sessionmaker[AsyncSession],
    store: ContentStore,
    site_id: uuid.UUID,
    default_kept: int,
) -> tuple[list[uuid.UUID], int]:
    """Removes the live versions of one site beyond the current live one and
    the newest others it keeps: its own number, else `default_kept`. Returns
    the ids it removed and the number it kept by."""
    async with factory() as db:
        async with db.begin():
            # The site row stays locked until commit: a deploy or a rollback
            # that would move live_version_id waits, so the version read here
            # is still the live one when the delete below runs. rollback_to
            # takes the same lock before it looks its version up.
            site = (
                await db.execute(
                    select(
                        Site.live_version_id,
                        func.coalesce(Site.live_versions_kept, default_kept).label("kept"),
                    )
                    .where(Site.id == site_id)
                    .with_for_update(key_share=True)
                )
            ).one_or_none()
            # Read again under the lock: the site may have gone, or switched to
            # keeping everything, since it was picked as a candidate.
            if site is None or site.kept == 0:
                return [], 0
            others = (Version.site_id == site_id) & (Version.target == VersionTarget.LIVE)
            others &= Version.id.is_distinct_from(site.live_version_id)
            kept_ids = (
                select(Version.id)
                .where(others)
                .order_by(Version.created_at.desc(), Version.id.desc())
                .limit(site.kept)
            )
            removed = (
                await db.execute(
                    delete(Version)
                    .where(others, Version.id.not_in(kept_ids))
                    .returning(Version.id, Version.storage_ref)
                )
            ).all()
        for row in removed:
            store.delete_version(row.storage_ref)
        return [row.id for row in removed], site.kept


async def _record_live_cleanup(
    factory: async_sessionmaker[AsyncSession],
    group_slug: str,
    site_slug: str,
    removed: list[uuid.UUID],
    kept: int,
) -> None:
    # Fail-open like AuditLog.write: the versions are gone either way, and one
    # failed audit row must not stop the sweep of the remaining sites.
    try:
        async with factory() as db, db.begin():
            db.add(
                AuditLogEntry(
                    actor_kind=ActorKind.SYSTEM,
                    action=vocabulary.VERSION_CLEANUP,
                    result=vocabulary.ALLOWED,
                    refs={
                        "group": group_slug,
                        "site": site_slug,
                        "versions": [str(version_id) for version_id in removed],
                        "kept": kept,
                    },
                )
            )
    except Exception:
        _logger.exception("Audit row for purged live versions not written: %s/%s", group_slug, site_slug)


async def _cleanup_old_live_versions(
    factory: async_sessionmaker[AsyncSession], store: ContentStore, default_kept: int
) -> int:
    effective = func.coalesce(Site.live_versions_kept, default_kept)
    async with factory() as db:
        candidates = (
            await db.execute(
                select(Site.id, Group.slug.label("group_slug"), Site.slug)
                .join(Group, Group.id == Site.group_id)
                .join(Version, Version.site_id == Site.id)
                .where(Version.target == VersionTarget.LIVE, effective > 0)
                .group_by(Site.id, Group.slug, Site.slug)
                .having(func.count(Version.id) > effective)
            )
        ).all()
    total = 0
    for candidate in candidates:
        removed, kept = await _cleanup_site_live_versions(factory, store, candidate.id, default_kept)
        if removed:
            await _record_live_cleanup(factory, candidate.group_slug, candidate.slug, removed, kept)
        total += len(removed)
    return total


async def _reclaim_orphan_directories(
    factory: async_sessionmaker[AsyncSession], store: ContentStore
) -> tuple[int, int, int]:
    async with factory() as db:
        site_ids = {str(site_id) for site_id in await db.scalars(select(Site.id))}
        storage_refs = set(await db.scalars(select(Version.storage_ref)))
    # On the event loop, not in a thread: a site deleted through the API then
    # cannot remove a directory halfway through this walk.
    reclaimed = store.reclaim(site_ids, storage_refs, ORPHAN_OLDER_THAN)
    if reclaimed.moved:
        _logger.warning(
            "Moved %d directories without a row into _reclaimed: %s",
            len(reclaimed.moved),
            ", ".join(reclaimed.moved),
        )
    if reclaimed.held:
        _logger.error(
            "Left %d directories without a row in place, the database and the content volume disagree: %s",
            len(reclaimed.held),
            ", ".join(reclaimed.held),
        )
    return len(reclaimed.moved), len(reclaimed.held), store.sweep_reclaimed(RECLAIMED_KEPT)


async def _release_retired_slugs(factory: async_sessionmaker[AsyncSession], now: datetime) -> int:
    """Deletes the old slugs whose redirect has ended (slug_window.py decides
    that), so anyone can claim them from now on. No audit row: the moment
    follows from the change of address, and a claim writes its own.

    A row goes only as it was read: one its group or site took back, or gave
    up once more, in the meantime has another `retired_at` and stays."""
    released = 0
    async with factory() as db, db.begin():
        for namespace, key in ((GroupSlug, (GroupSlug.slug,)), (SiteSlug, (SiteSlug.group_id, SiteSlug.slug))):
            rows = (await db.execute(select(*key, namespace.retired_at).where(namespace.retired_at.is_not(None)))).all()
            ended = [tuple(row) for row in rows if not still_redirects(row.retired_at, now)]
            if ended:
                gone = await db.scalars(
                    delete(namespace)
                    .where(tuple_(*key, namespace.retired_at).in_(ended))
                    .returning(namespace.slug)
                )
                released += len(gone.all())
    return released


async def delete_expired(
    factory: async_sessionmaker[AsyncSession],
    store: ContentStore,
    now: datetime,
    *,
    tmp_older_than: timedelta = TMP_OLDER_THAN_DEFAULT,
    live_versions_kept: int = 0,
) -> CleanupResult:
    """Runs the full sweep once; callable straight from tests.

    `live_versions_kept` is the number for sites without their own, as
    PLAK_LIVE_VERSIONS_KEPT is; 0 keeps every live version of those sites."""
    expired = await _cleanup_expired_previews(factory, store, now)
    orphans = await _cleanup_orphan_preview_versions(factory, store)
    old_live = await _cleanup_old_live_versions(factory, store, live_versions_kept)
    swept = store.sweep_tmp(tmp_older_than)
    async with factory() as db:
        authorizations, cli_sessions = await cli.delete_expired(db, now)
    released_slugs = await _release_retired_slugs(factory, now)
    orphan_directories, held_directories, reclaimed_swept = await _reclaim_orphan_directories(factory, store)
    return CleanupResult(
        expired_previews=expired,
        orphan_versions=orphans,
        tmp_swept=swept,
        cli_device_authorizations=authorizations,
        cli_sessions=cli_sessions,
        old_live_versions=old_live,
        orphan_directories=orphan_directories,
        held_directories=held_directories,
        reclaimed_swept=reclaimed_swept,
        released_slugs=released_slugs,
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
    live_versions_kept: int,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        wait_time = _seconds_until(occurred_at, datetime.now(tz=UTC))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=wait_time)
        if stop.is_set():
            break
        try:
            await delete_expired(
                factory,
                store,
                datetime.now(tz=UTC),
                tmp_older_than=tmp_older_than,
                live_versions_kept=live_versions_kept,
            )
        except Exception:
            # A transient failure (DB hiccup, ...) must not end the loop: the
            # process would then keep running with no sweep for the rest of
            # its life. Log loud, wait for the next scheduled run.
            _logger.exception("Daily cleanup failed, retrying on the next run")


@asynccontextmanager
async def cleanup_job(
    factory: async_sessionmaker[AsyncSession],
    store: ContentStore,
    *,
    live_versions_kept: int,
    occurred_at: time = TIMESTAMP_DEFAULT,
    tmp_older_than: timedelta = TMP_OLDER_THAN_DEFAULT,
) -> AsyncIterator[asyncio.Task]:
    """Background task that runs the sweep daily at `occurred_at` (UTC).

    Meant to be opened inside the lifespan of main.py:
    `async with cleanup_job(factory, store, live_versions_kept=...): yield`.
    """
    stop = asyncio.Event()
    task = asyncio.create_task(
        _run_daily(
            factory,
            store,
            occurred_at=occurred_at,
            tmp_older_than=tmp_older_than,
            live_versions_kept=live_versions_kept,
            stop=stop,
        )
    )
    try:
        yield task
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
