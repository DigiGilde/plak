"""Ingest service: deploy, preview upsert, preview removal and rollback.

Going live is an atomic pointer swap in the same transaction as the
version insert; the preview upsert is an atomic INSERT .. ON CONFLICT
(site_id, ref); every live version is kept, replaced preview versions (row
and files) are cleaned up.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from plak import i18n, messages
from plak.config import Settings
from plak.ingest.store import ContentStore
from plak.ingest.unpacker import Limits, unpack
from plak.messages import Msg
from plak.models.identity import Group
from plak.models.publication import Preview, Site, Version, VersionTarget

PREVIEW_VALIDITY = timedelta(days=30)


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


class IngestService:
    def __init__(self, store: ContentStore, settings: Settings) -> None:
        self._content_store = store
        self._limits = Limits.from_settings(settings)

    def _store_sync(
        self, group: Group, site: Site, filename: str, source: Path, base_path: str | None
    ) -> tuple[uuid.UUID, str]:
        version_id = uuid.uuid4()
        with self._content_store.write_version(group.slug, site.slug, version_id) as writer:
            unpack(filename, source, writer, self._limits, base_path=base_path)
        return version_id, writer.storage_ref

    async def _store(
        self, group: Group, site: Site, filename: str, source: Path, base_path: str | None
    ) -> tuple[uuid.UUID, str]:
        """Unpacks the spooled upload `source` into a new version; the blocking disk
        work runs in a thread so serving keeps going meanwhile."""
        return await asyncio.to_thread(
            self._store_sync, group, site, filename, source, base_path
        )

    async def deploy(
        self,
        db: AsyncSession,
        group: Group,
        site: Site,
        filename: str,
        source: Path,
        deployer: Deployer,
        base_path: str | None = None,
    ) -> uuid.UUID:
        version_id, storage_ref = await self._store(group, site, filename, source, base_path)
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
        group: Group,
        site: Site,
        ref: str,
        filename: str,
        source: Path,
        deployer: Deployer,
        base_path: str | None = None,
    ) -> uuid.UUID:
        version_id, storage_ref = await self._store(group, site, filename, source, base_path)
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
