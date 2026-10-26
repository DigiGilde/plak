"""Ingest service: deploy, preview upsert, preview removal and rollback.

Going live is an atomic pointer swap in the same transaction as the
version insert; the preview upsert is an atomic INSERT .. ON CONFLICT
(site_id, ref); replaced preview versions (row and files) are cleaned up
here, old live versions by the nightly cleanup job.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from plak import i18n, messages
from plak.config import Settings
from plak.ingest.store import ContentStore, VersionWriter
from plak.ingest.unpacker import Limits, unpack
from plak.messages import Msg
from plak.models.publication import Preview, Site, Version, VersionTarget

PREVIEW_VALIDITY = timedelta(days=30)

# How far a deploy writes on one measurement of the content volume. Other
# writers (a concurrent deploy) can only shift that reading by this much before
# it is taken again, and statvfs stays out of the per-chunk loop.
ROOM_CHECK_STRIDE = 4 * 1024 * 1024


class IngestError(Exception):
    """Refused ingest action, with a machine-readable reason code.

    Like BundleError it names a message key from plak/messages.py, so the
    refusal reaches the client in the language it asked for."""

    def __init__(self, key: str, *, params: Mapping[str, object] | None = None) -> None:
        self.message = Msg(key, dict(params or {}))
        self.reason = messages.code_of(key)
        super().__init__(messages.render(i18n.API_DEFAULT, self.message))


@dataclass(frozen=True)
class Deployer:
    """Origin of a deploy: exactly one of the two (ck_versions_origin)."""

    member_id: uuid.UUID | None = None
    ci_repository: str | None = None

    def __post_init__(self) -> None:
        if (self.member_id is None) == (self.ci_repository is None):
            raise IngestError("ORIGIN_INVALID")


class RoomGuard:
    """Keeps one deploy's writes above storage_min_free_bytes on the content
    volume. Called with the size of every chunk before it is written; refuses
    with STORAGE_UNAVAILABLE once that chunk would take the volume below the
    floor. The caller cleans up what it wrote, as on any other refusal."""

    def __init__(self, store: ContentStore, min_free: int) -> None:
        self._store = store
        self._min_free = min_free
        # What may still be written above the floor, and before the volume is
        # measured again, both counted down from the last measurement.
        self._left = 0
        self._stride_left = 0

    def reserve(self, count: int) -> None:
        if not self._min_free:
            return
        if count > self._left or count > self._stride_left:
            # Measured again rather than refused on the countdown alone: other
            # deploys and the cleanup job free space as well as take it.
            self._left = self._store.free_bytes() - self._min_free
            self._stride_left = ROOM_CHECK_STRIDE
            if count > self._left:
                raise IngestError("STORAGE_UNAVAILABLE")
        self._left -= count
        self._stride_left -= count


class _GuardedFile:
    """A file in the work directory whose every write passes the guard first."""

    def __init__(self, file: IO[bytes], room: RoomGuard) -> None:
        self._file = file
        self._room = room

    def __enter__(self) -> _GuardedFile:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._file.close()

    def write(self, data: bytes) -> int:
        self._room.reserve(len(data))
        return self._file.write(data)


class _GuardedDestination:
    def __init__(self, writer: VersionWriter, room: RoomGuard) -> None:
        self._writer = writer
        self._room = room

    def open_file(self, rel_path: str) -> _GuardedFile:
        return _GuardedFile(self._writer.open_file(rel_path), self._room)


class IngestService:
    def __init__(self, store: ContentStore, settings: Settings) -> None:
        self._content_store = store
        self._limits = Limits.from_settings(settings)
        self._site_max_bytes = settings.site_max_bytes
        self._min_free = settings.storage_min_free_bytes

    def check_room(self, incoming: int = 0) -> None:
        """Refuses before the first byte is spooled when `incoming` more bytes
        (the declared size of the upload, if the client sent one) would take
        the content volume below storage_min_free_bytes. The RoomGuard keeps
        watching from there, while the upload is spooled and unpacked."""
        if self._min_free and self._content_store.free_bytes() - incoming < self._min_free:
            raise IngestError("STORAGE_UNAVAILABLE")

    def room_guard(self) -> RoomGuard:
        return RoomGuard(self._content_store, self._min_free)

    def _store_sync(
        self, site: Site, filename: str, source: Path, base_path: str | None
    ) -> tuple[uuid.UUID, str]:
        version_id = uuid.uuid4()
        with self._content_store.write_version(site.id, version_id) as writer:
            destination = _GuardedDestination(writer, self.room_guard())
            unpack(filename, source, destination, self._limits, base_path=base_path)
            # Inside the with: on a refusal the work directory is cleaned up
            # and nothing is renamed into place. The site is measured only now
            # because the size of this version is not known before it is
            # unpacked, and the older versions are all still there.
            self._check_quota(site, writer.total_bytes())
        return version_id, writer.storage_ref

    def _check_quota(self, site: Site, added: int) -> None:
        if not self._site_max_bytes:
            return
        used = self._content_store.site_bytes(site.id)
        if used + added > self._site_max_bytes:
            raise IngestError(
                "SITE_QUOTA_EXCEEDED",
                params={"used": used, "added": added, "max_bytes": self._site_max_bytes},
            )

    async def _store(
        self, site: Site, filename: str, source: Path, base_path: str | None
    ) -> tuple[uuid.UUID, str]:
        """Unpacks the spooled upload `source` into a new version; the blocking disk
        work runs in a thread so serving keeps going meanwhile."""
        return await asyncio.to_thread(
            self._store_sync, site, filename, source, base_path
        )

    async def deploy(
        self,
        db: AsyncSession,
        site: Site,
        filename: str,
        source: Path,
        deployer: Deployer,
        base_path: str | None = None,
    ) -> uuid.UUID:
        version_id, storage_ref = await self._store(site, filename, source, base_path)
        try:
            async with db.begin():
                db.add(
                    Version(
                        id=version_id,
                        site_id=site.id,
                        target=VersionTarget.LIVE,
                        storage_ref=storage_ref,
                        member_id=deployer.member_id,
                        ci_repository=deployer.ci_repository,
                    )
                )
                await db.flush()
                await db.execute(
                    update(Site).where(Site.id == site.id).values(live_version_id=version_id)
                )
        except BaseException:
            self._content_store.delete_version(storage_ref)
            raise
        return version_id

    async def preview_deploy(
        self,
        db: AsyncSession,
        site: Site,
        ref: str,
        filename: str,
        source: Path,
        deployer: Deployer,
        base_path: str | None = None,
    ) -> uuid.UUID:
        version_id, storage_ref = await self._store(site, filename, source, base_path)
        expires_at = datetime.now(tz=UTC) + PREVIEW_VALIDITY
        old_storage_ref: str | None = None
        try:
            async with db.begin():
                db.add(
                    Version(
                        id=version_id,
                        site_id=site.id,
                        target=VersionTarget.PREVIEW,
                        storage_ref=storage_ref,
                        member_id=deployer.member_id,
                        ci_repository=deployer.ci_repository,
                    )
                )
                await db.flush()

                # A deliberate no-op DO UPDATE (ref := ref): it takes the
                # row lock and lets RETURNING show the OLD version_id, also
                # when the row was created concurrently by another deploy; a
                # bare RETURNING after a real update can no longer supply the
                # old value.
                upsert = (
                    pg_insert(Preview)
                    .values(
                        id=uuid.uuid4(),
                        site_id=site.id,
                        ref=ref,
                        version_id=version_id,
                        expires_at=expires_at,
                    )
                    .on_conflict_do_update(
                        constraint="uq_previews_site_ref",
                        set_={"ref": ref},
                    )
                    .returning(Preview.id, Preview.version_id)
                )
                row = (await db.execute(upsert)).one()

                if row.version_id != version_id:
                    old_version_id = row.version_id
                    await db.execute(
                        update(Preview)
                        .where(Preview.id == row.id)
                        .values(
                            version_id=version_id,
                            expires_at=expires_at,
                            last_updated_at=func.now(),
                        )
                    )
                    old_storage_ref = (
                        await db.execute(
                            select(Version.storage_ref).where(Version.id == old_version_id)
                        )
                    ).scalar_one()
                    # Delete only after the preview row has been repointed:
                    # the FK previews.version_id cascades on delete.
                    await db.execute(delete(Version).where(Version.id == old_version_id))
        except BaseException:
            self._content_store.delete_version(storage_ref)
            raise
        if old_storage_ref is not None:
            self._content_store.delete_version(old_storage_ref)
        return version_id

    async def delete_preview(self, db: AsyncSession, site: Site, ref: str) -> bool:
        """Removes a preview (row, version row and files); idempotent."""
        async with db.begin():
            preview = (
                await db.execute(
                    select(Preview)
                    .where(Preview.site_id == site.id, Preview.ref == ref)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if preview is None:
                return False
            storage_ref = (
                await db.execute(
                    select(Version.storage_ref).where(Version.id == preview.version_id)
                )
            ).scalar_one()
            version_id = preview.version_id
            await db.delete(preview)
            await db.flush()
            await db.execute(delete(Version).where(Version.id == version_id))
        self._content_store.delete_version(storage_ref)
        return True

    async def rollback_to(
        self, db: AsyncSession, site: Site, version_id: uuid.UUID
    ) -> None:
        async with db.begin():
            # Before the lookup: the cleanup job removes old live versions
            # under this lock, so a version it is removing reads as unknown
            # here instead of failing the foreign key on the update below.
            await db.execute(
                select(Site.id).where(Site.id == site.id).with_for_update(key_share=True)
            )
            version = (
                await db.execute(select(Version).where(Version.id == version_id))
            ).scalar_one_or_none()
            if version is None:
                raise IngestError("UNKNOWN_VERSION")
            if version.site_id != site.id:
                raise IngestError("VERSION_OTHER_SITE")
            if version.target == VersionTarget.PREVIEW:
                raise IngestError("ROLLBACK_TARGET_PREVIEW")
            await db.execute(
                update(Site).where(Site.id == site.id).values(live_version_id=version_id)
            )
