"""Tests for previews/cleanup_job.py: expired previews, orphaned preview
versions, live versions beyond the kept number and stale _tmp get swept; and
that the docs HTML touches no external origin (spec §8).

The DB tests really commit against the test container database (through a
session factory of their own), just like test_ingest_service.py.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.util import await_only

from plak.api.docs import _ASSETS, _DOCS_HTML, DOCS_CSP, STATIC_DOCS_DIR
from plak.audit import vocabulary
from plak.cli import service as cli
from plak.config import Settings
from plak.constants import AccessBase
from plak.ingest.service import Deployer, IngestError, IngestService
from plak.ingest.store import ContentStore
from plak.models.audit import ActorKind, AuditLogEntry
from plak.models.cli import CliDeviceAuthorization, CliSession
from plak.models.identity import Group, Member, MemberStatus
from plak.models.publication import Preview, Site, Version, VersionTarget
from plak.previews.cleanup_job import (
    TIMESTAMP_DEFAULT,
    TMP_OLDER_THAN_DEFAULT,
    CleanupResult,
    _cleanup_site_live_versions,
    _run_daily,
    _seconds_until,
    cleanup_job,
    delete_expired,
)


@pytest_asyncio.fixture
async def session_factory(migrated_dsn: str):
    engine = create_async_engine(migrated_dsn)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class Environment:
    group: Group
    site: Site
    member: Member
    store: ContentStore
    content_root: Path
    session_factory: async_sessionmaker[AsyncSession]


@pytest_asyncio.fixture
async def environment(session_factory, tmp_path: Path) -> Environment:
    unique_ = uuid.uuid4().hex[:10]
    member = Member(id=uuid.uuid4(), sso_subject=f"sub-{unique_}", email=f"lid-{unique_}@example.org", name="Testlid")
    group = Group(
        id=uuid.uuid4(), slug=f"g{unique_}", name="Testgroep", default_access_base=AccessBase.PUBLIC
    )
    site = Site(
        id=uuid.uuid4(),
        group_id=group.id,
        slug=f"p{unique_}",
        title="Testsite",
        access_base=AccessBase.PUBLIC,
    )
    async with session_factory() as session, session.begin():
        session.add_all([member, group, site])

    store = ContentStore(tmp_path)
    return Environment(
        group=group, site=site, member=member, store=store, content_root=tmp_path, session_factory=session_factory
    )


async def _make_preview_version(
    environment: Environment, *, expires_at: datetime | None, ref: str | None = None
) -> tuple[uuid.UUID, str]:
    """Creates a versie with doel=preview and, when ref is set, a matching
    preview row; returns (version_id, storage_ref)."""
    version_id = uuid.uuid4()
    storage_ref = environment.store.store_version(
        environment.group.slug, environment.site.slug, version_id, {"index.html": b"<h1>preview</h1>"}
    )
    async with environment.session_factory() as session, session.begin():
        session.add(
            Version(
                id=version_id,
                site_id=environment.site.id,
                target=VersionTarget.PREVIEW,
                storage_ref=storage_ref,
                member_id=environment.member.id,
            )
        )
        await session.flush()
        if ref is not None:
            session.add(
                Preview(
                    id=uuid.uuid4(),
                    site_id=environment.site.id,
                    ref=ref,
                    version_id=version_id,
                    expires_at=expires_at,
                )
            )
    return version_id, storage_ref


async def _all_versions(environment: Environment) -> list[Version]:
    async with environment.session_factory() as session:
        return list((await session.execute(select(Version))).scalars())


async def _all_previews(environment: Environment) -> list[Preview]:
    async with environment.session_factory() as session:
        return list((await session.execute(select(Preview))).scalars())


def _settings(content_root: Path) -> Settings:
    return Settings(
        db_url="postgresql+asyncpg://ongebruikt/ongebruikt",
        content_root=content_root,
        oidc_issuer="https://idp.example",
        oidc_client_id="plak",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr",
        session_secret="s" * 32,
        audit_pepper="p" * 32,
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        content_base_url="https://plak.example",
        environment="dev",
    )


async def _wait_for_a_lock_wait(environment: Environment) -> None:
    """Returns once another backend is blocked on a row lock."""
    async with environment.session_factory() as session:
        for _ in range(200):
            waiting = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
            )
            await session.rollback()
            if waiting:
                return
            await asyncio.sleep(0.05)
    raise AssertionError("the sweep never waited on the renewed row")


class TestExpiredPreviews:
    async def test_expired_preview_deletes_row_version_and_files(self, environment: Environment):
        now_ = datetime.now(tz=UTC)
        version_id, storage_ref = await _make_preview_version(
            environment, expires_at=now_ - timedelta(days=1), ref="pr-1"
        )
        file_path = environment.content_root / storage_ref

        result = await delete_expired(environment.session_factory, environment.store, now_)

        assert result.expired_previews == 1
        assert await _all_previews(environment) == []
        assert all(v.id != version_id for v in await _all_versions(environment))
        assert not file_path.exists()

    async def test_not_expired_preview_stays(self, environment: Environment):
        now_ = datetime.now(tz=UTC)
        version_id, storage_ref = await _make_preview_version(
            environment, expires_at=now_ + timedelta(days=29), ref="pr-2"
        )

        result = await delete_expired(environment.session_factory, environment.store, now_)

        assert result.expired_previews == 0
        previews = await _all_previews(environment)
        assert len(previews) == 1
        assert previews[0].version_id == version_id
        assert (environment.content_root / storage_ref).exists()

    async def test_preview_without_expiry_date_stays(self, environment: Environment):
        now_ = datetime.now(tz=UTC)
        await _make_preview_version(environment, expires_at=None, ref="pr-geen-vervaldatum")

        result = await delete_expired(environment.session_factory, environment.store, now_)

        assert result.expired_previews == 0
        assert len(await _all_previews(environment)) == 1


    async def test_a_preview_renewed_while_the_sweep_runs_survives(self, environment: Environment, tmp_path: Path):
        """A same-ref deploy renews the expired row (new version, later expiry)
        and commits while the sweep is already under way: the sweep must not
        delete the renewed preview, nor, as an orphan, its new version."""
        now_ = datetime.now(tz=UTC)
        old_version_id, old_storage_ref = await _make_preview_version(
            environment, expires_at=now_ - timedelta(days=1), ref="pr-renewed"
        )
        service = IngestService(environment.store, _settings(environment.content_root))
        source = tmp_path / "upload.html"
        source.write_bytes(b"<h1>renewed</h1>")
        commit_gate = asyncio.Event()
        renewal_ready = asyncio.Event()

        async def renew() -> uuid.UUID:
            async with environment.session_factory() as db:
                # Holds the renewed row locked, uncommitted, until released.
                def hold_before_commit(_session: object) -> None:
                    renewal_ready.set()
                    await_only(commit_gate.wait())

                event.listen(db.sync_session, "before_commit", hold_before_commit)
                return await service.preview_deploy(
                    db,
                    environment.group,
                    environment.site,
                    "pr-renewed",
                    "index.html",
                    source,
                    Deployer(member_id=environment.member.id),
                )

        renewal = asyncio.create_task(renew())
        await asyncio.wait_for(renewal_ready.wait(), timeout=10)
        sweep = asyncio.create_task(delete_expired(environment.session_factory, environment.store, now_))
        await _wait_for_a_lock_wait(environment)
        commit_gate.set()
        new_version_id = await renewal
        result = await sweep

        assert result.expired_previews == 0
        assert result.orphan_versions == 0
        previews = await _all_previews(environment)
        assert [preview.version_id for preview in previews] == [new_version_id]
        assert previews[0].expires_at > now_
        versions = await _all_versions(environment)
        assert [version.id for version in versions] == [new_version_id]
        assert (environment.content_root / versions[0].storage_ref).exists()
        assert all(version.id != old_version_id for version in versions)
        assert not (environment.content_root / old_storage_ref).exists()


class TestOrphanPreviewVersions:
    async def test_orphan_preview_version_without_preview_row_becomes_deleted(self, environment: Environment):
        now_ = datetime.now(tz=UTC)
        version_id, storage_ref = await _make_preview_version(environment, expires_at=None, ref=None)
        file_path = environment.content_root / storage_ref

        result = await delete_expired(environment.session_factory, environment.store, now_)

        assert result.orphan_versions == 1
        assert all(v.id != version_id for v in await _all_versions(environment))
        assert not file_path.exists()

    async def test_preview_version_with_row_is_no_orphan(self, environment: Environment):
        now_ = datetime.now(tz=UTC)
        version_id, _ = await _make_preview_version(
            environment, expires_at=now_ + timedelta(days=1), ref="pr-actief"
        )

        result = await delete_expired(environment.session_factory, environment.store, now_)

        assert result.orphan_versions == 0
        assert any(v.id == version_id for v in await _all_versions(environment))


async def _make_site(environment: Environment) -> Site:
    site = Site(
        id=uuid.uuid4(),
        group_id=environment.group.id,
        slug=f"p{uuid.uuid4().hex[:10]}",
        title="Andere site",
        access_base=AccessBase.PUBLIC,
    )
    async with environment.session_factory() as session, session.begin():
        session.add(site)
    return site


async def _make_live_versions(
    environment: Environment, count: int, *, site: Site | None = None, live: int | None = -1
) -> list[tuple[uuid.UUID, str]]:
    """Creates `count` live versions an hour apart, oldest first, and points the
    site at the one with index `live` (None leaves it without a live version)."""
    site = site or environment.site
    start = datetime.now(tz=UTC) - timedelta(days=30)
    versions = []
    async with environment.session_factory() as session, session.begin():
        for index in range(count):
            version_id = uuid.uuid4()
            storage_ref = environment.store.store_version(
                environment.group.slug, site.slug, version_id, {"index.html": f"<h1>{index}</h1>".encode()}
            )
            session.add(
                Version(
                    id=version_id,
                    site_id=site.id,
                    target=VersionTarget.LIVE,
                    storage_ref=storage_ref,
                    member_id=environment.member.id,
                    created_at=start + timedelta(hours=index),
                )
            )
            versions.append((version_id, storage_ref))
        await session.flush()
        if live is not None:
            await session.execute(
                update(Site).where(Site.id == site.id).values(live_version_id=versions[live][0])
            )
    return versions


async def _live_version_id(environment: Environment, site: Site | None = None) -> uuid.UUID | None:
    async with environment.session_factory() as session:
        return await session.scalar(select(Site.live_version_id).where(Site.id == (site or environment.site).id))


async def _version_ids(environment: Environment) -> set[uuid.UUID]:
    return {version.id for version in await _all_versions(environment)}


async def _cleanup_rows(environment: Environment) -> list[AuditLogEntry]:
    async with environment.session_factory() as session:
        return list(
            await session.scalars(select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.VERSION_CLEANUP))
        )


class TestOldLiveVersions:
    async def test_keeps_the_live_version_and_exactly_the_n_newest_others(self, environment: Environment):
        versions = await _make_live_versions(environment, 8)

        result = await delete_expired(
            environment.session_factory, environment.store, datetime.now(tz=UTC), live_versions_kept=5
        )

        assert result.old_live_versions == 2
        assert await _version_ids(environment) == {version_id for version_id, _ in versions[2:]}
        assert await _live_version_id(environment) == versions[7][0]
        for _, storage_ref in versions[:2]:
            assert not (environment.content_root / storage_ref).exists()
        for _, storage_ref in versions[2:]:
            assert (environment.content_root / storage_ref).exists()

    async def test_a_site_at_exactly_the_kept_number_loses_nothing(self, environment: Environment):
        versions = await _make_live_versions(environment, 6)

        result = await delete_expired(
            environment.session_factory, environment.store, datetime.now(tz=UTC), live_versions_kept=5
        )

        assert result.old_live_versions == 0
        assert await _version_ids(environment) == {version_id for version_id, _ in versions}
        assert await _cleanup_rows(environment) == []

    async def test_after_a_rollback_the_old_live_version_survives_and_the_window_skips_it(
        self, environment: Environment
    ):
        versions = await _make_live_versions(environment, 8)
        service = IngestService(environment.store, _settings(environment.content_root))
        async with environment.session_factory() as session:
            await service.rollback_to(session, environment.site, versions[1][0])

        result = await delete_expired(
            environment.session_factory, environment.store, datetime.now(tz=UTC), live_versions_kept=5
        )

        # Live is index 1; the five newest others are 3..7; 0 and 2 go.
        assert result.old_live_versions == 2
        assert await _version_ids(environment) == {versions[i][0] for i in (1, 3, 4, 5, 6, 7)}
        assert await _live_version_id(environment) == versions[1][0]
        assert (environment.content_root / versions[1][1]).exists()
        assert not (environment.content_root / versions[2][1]).exists()

    async def test_a_site_without_a_live_version_keeps_the_n_newest(self, environment: Environment):
        versions = await _make_live_versions(environment, 4, live=None)

        result = await delete_expired(
            environment.session_factory, environment.store, datetime.now(tz=UTC), live_versions_kept=3
        )

        assert result.old_live_versions == 1
        assert await _version_ids(environment) == {version_id for version_id, _ in versions[1:]}

    async def test_zero_keeps_every_live_version(self, environment: Environment):
        versions = await _make_live_versions(environment, 8)

        explicit = await delete_expired(
            environment.session_factory, environment.store, datetime.now(tz=UTC), live_versions_kept=0
        )
        default = await delete_expired(environment.session_factory, environment.store, datetime.now(tz=UTC))

        assert explicit.old_live_versions == default.old_live_versions == 0
        assert await _version_ids(environment) == {version_id for version_id, _ in versions}

    async def test_previews_are_neither_removed_nor_counted(self, environment: Environment):
        now_ = datetime.now(tz=UTC)
        live = await _make_live_versions(environment, 3)
        previews = [
            await _make_preview_version(environment, expires_at=now_ + timedelta(days=1), ref=f"pr-{i}")
            for i in range(4)
        ]

        result = await delete_expired(environment.session_factory, environment.store, now_, live_versions_kept=1)

        assert result.old_live_versions == 1
        assert await _version_ids(environment) == {live[1][0], live[2][0]} | {v for v, _ in previews}
        assert len(await _all_previews(environment)) == 4
        for _, storage_ref in previews:
            assert (environment.content_root / storage_ref).exists()

    async def test_other_sites_are_neither_touched_nor_counted(self, environment: Environment):
        other = await _make_site(environment)
        mine = await _make_live_versions(environment, 4)
        theirs = await _make_live_versions(environment, 2, site=other)

        result = await delete_expired(
            environment.session_factory, environment.store, datetime.now(tz=UTC), live_versions_kept=1
        )

        assert result.old_live_versions == 2
        assert await _version_ids(environment) == {mine[2][0], mine[3][0]} | {v for v, _ in theirs}
        assert await _live_version_id(environment, other) == theirs[1][0]

    async def test_the_removal_is_audited_once_per_site_as_a_system_action(self, environment: Environment):
        versions = await _make_live_versions(environment, 4)

        await delete_expired(
            environment.session_factory, environment.store, datetime.now(tz=UTC), live_versions_kept=1
        )

        [row] = await _cleanup_rows(environment)
        assert row.actor_kind == ActorKind.SYSTEM
        assert row.actor_pseudonym is None
        assert row.result == vocabulary.ALLOWED
        assert row.refs.pop("versions") in (
            [str(versions[0][0]), str(versions[1][0])],
            [str(versions[1][0]), str(versions[0][0])],
        )
        assert row.refs == {"group": environment.group.slug, "site": environment.site.slug, "kept": 1}

    async def test_a_failing_audit_write_is_logged_and_the_removal_still_counts(
        self, environment: Environment, monkeypatch, caplog
    ):
        versions = await _make_live_versions(environment, 3)

        def broken_entry(**_kwargs):
            raise RuntimeError("audit storing")

        monkeypatch.setattr("plak.previews.cleanup_job.AuditLogEntry", broken_entry)
        with caplog.at_level(logging.ERROR):
            result = await delete_expired(
                environment.session_factory, environment.store, datetime.now(tz=UTC), live_versions_kept=1
            )

        assert result.old_live_versions == 1
        assert versions[0][0] not in await _version_ids(environment)
        assert not (environment.content_root / versions[0][1]).exists()
        assert any("live versions" in record.getMessage() for record in caplog.records)

    async def test_a_rollback_that_commits_while_the_sweep_waits_keeps_its_version(
        self, environment: Environment
    ):
        """The rollback holds the site row and commits only once the sweep is
        blocked on it: the sweep must read the new live pointer, not the old."""
        versions = await _make_live_versions(environment, 8)
        service = IngestService(environment.store, _settings(environment.content_root))
        commit_gate = asyncio.Event()
        rollback_ready = asyncio.Event()

        async def roll_back() -> None:
            async with environment.session_factory() as db:
                def hold_before_commit(_session: object) -> None:
                    rollback_ready.set()
                    await_only(commit_gate.wait())

                event.listen(db.sync_session, "before_commit", hold_before_commit)
                await service.rollback_to(db, environment.site, versions[0][0])

        rollback = asyncio.create_task(roll_back())
        await asyncio.wait_for(rollback_ready.wait(), timeout=10)
        sweep = asyncio.create_task(
            delete_expired(
                environment.session_factory, environment.store, datetime.now(tz=UTC), live_versions_kept=5
            )
        )
        await _wait_for_a_lock_wait(environment)
        commit_gate.set()
        await rollback
        result = await sweep

        # Live is index 0 now; 3..7 are the five newest others; 1 and 2 go.
        assert result.old_live_versions == 2
        assert await _live_version_id(environment) == versions[0][0]
        assert await _version_ids(environment) == {versions[i][0] for i in (0, 3, 4, 5, 6, 7)}
        assert (environment.content_root / versions[0][1]).exists()

    async def test_a_rollback_to_a_version_being_removed_finds_it_unknown(self, environment: Environment):
        """The sweep has deleted the old rows but not committed yet; a rollback to
        one of them waits for it and then refuses cleanly, leaving the live
        pointer where it was rather than failing on the foreign key."""
        versions = await _make_live_versions(environment, 3)
        service = IngestService(environment.store, _settings(environment.content_root))
        commit_gate = asyncio.Event()
        sweep_ready = asyncio.Event()

        def holding_factory() -> AsyncSession:
            session = environment.session_factory()

            def hold_before_commit(_session: object) -> None:
                sweep_ready.set()
                await_only(commit_gate.wait())

            event.listen(session.sync_session, "before_commit", hold_before_commit)
            return session

        sweep = asyncio.create_task(
            _cleanup_site_live_versions(holding_factory, environment.store, environment.site.id, 1)
        )
        await asyncio.wait_for(sweep_ready.wait(), timeout=10)

        async def roll_back() -> None:
            async with environment.session_factory() as db:
                await service.rollback_to(db, environment.site, versions[0][0])

        rollback = asyncio.create_task(roll_back())
        await _wait_for_a_lock_wait(environment)
        commit_gate.set()
        removed, kept = await sweep

        with pytest.raises(IngestError) as refused:
            await rollback
        assert refused.value.reason == "UNKNOWN_VERSION"
        assert (removed, kept) == ([versions[0][0]], 1)
        assert await _live_version_id(environment) == versions[2][0]
        assert not (environment.content_root / versions[0][1]).exists()


async def _set_site_kept(environment: Environment, kept: int | None, site: Site | None = None) -> None:
    async with environment.session_factory() as session, session.begin():
        await session.execute(
            update(Site).where(Site.id == (site or environment.site).id).values(live_versions_kept=kept)
        )


class TestLiveVersionsKeptPerSite:
    """A site's own number wins over the platform default, in both directions."""

    async def _sweep(self, environment: Environment, default: int) -> CleanupResult:
        return await delete_expired(
            environment.session_factory, environment.store, datetime.now(tz=UTC), live_versions_kept=default
        )

    async def test_an_own_number_above_the_default_keeps_more(self, environment: Environment):
        versions = await _make_live_versions(environment, 8)
        await _set_site_kept(environment, 6)

        result = await self._sweep(environment, 2)

        assert result.old_live_versions == 1
        assert await _version_ids(environment) == {version_id for version_id, _ in versions[1:]}

    async def test_an_own_number_below_the_default_keeps_fewer(self, environment: Environment):
        versions = await _make_live_versions(environment, 8)
        await _set_site_kept(environment, 1)

        result = await self._sweep(environment, 5)

        assert result.old_live_versions == 6
        assert await _version_ids(environment) == {versions[6][0], versions[7][0]}
        [row] = await _cleanup_rows(environment)
        assert row.refs["kept"] == 1

    async def test_an_own_zero_keeps_everything_under_a_default_of_five(self, environment: Environment):
        versions = await _make_live_versions(environment, 8)
        await _set_site_kept(environment, 0)

        result = await self._sweep(environment, 5)

        assert result.old_live_versions == 0
        assert await _version_ids(environment) == {version_id for version_id, _ in versions}
        assert await _cleanup_rows(environment) == []

    async def test_an_own_number_is_cleaned_under_a_default_that_keeps_all(self, environment: Environment):
        other = await _make_site(environment)
        mine = await _make_live_versions(environment, 6)
        theirs = await _make_live_versions(environment, 6, site=other)
        await _set_site_kept(environment, 3)

        result = await self._sweep(environment, 0)

        assert result.old_live_versions == 2
        assert await _version_ids(environment) == {v for v, _ in mine[2:]} | {v for v, _ in theirs}
        [row] = await _cleanup_rows(environment)
        assert (row.refs["site"], row.refs["kept"]) == (environment.site.slug, 3)

    async def test_null_follows_the_default(self, environment: Environment):
        other = await _make_site(environment)
        mine = await _make_live_versions(environment, 5)
        theirs = await _make_live_versions(environment, 5, site=other)
        await _set_site_kept(environment, 4, site=other)

        result = await self._sweep(environment, 2)

        assert result.old_live_versions == 2
        assert await _version_ids(environment) == {v for v, _ in mine[2:]} | {v for v, _ in theirs}

    async def test_a_site_switched_to_keep_all_after_selection_loses_nothing(self, environment: Environment):
        """The number is read again under the site lock: a site picked as a
        candidate whose admin then set 0 must not lose its history."""
        versions = await _make_live_versions(environment, 4)
        await _set_site_kept(environment, 0)

        removed = await _cleanup_site_live_versions(
            environment.session_factory, environment.store, environment.site.id, 1
        )

        assert removed == ([], 0)
        assert await _version_ids(environment) == {version_id for version_id, _ in versions}

    async def test_a_site_deleted_after_selection_is_skipped(self, environment: Environment):
        removed = await _cleanup_site_live_versions(
            environment.session_factory, environment.store, uuid.uuid4(), 1
        )

        assert removed == ([], 0)


class TestTmpSweeper:
    async def test_stale_tmp_becomes_swept(self, environment: Environment):
        stale_map = environment.content_root / "_tmp" / "stale-werkdir"
        stale_map.mkdir(parents=True)
        (stale_map / "restant.bin").write_bytes(b"x")
        old = datetime.now(tz=UTC) - timedelta(hours=48)
        os.utime(stale_map, (old.timestamp(), old.timestamp()))

        fresh_dir = environment.content_root / "_tmp" / "vers-werkdir"
        fresh_dir.mkdir(parents=True)

        result = await delete_expired(
            environment.session_factory, environment.store, datetime.now(tz=UTC), tmp_older_than=timedelta(hours=24)
        )

        assert result.tmp_swept == 1
        assert not stale_map.exists()
        assert fresh_dir.exists()


class TestSecondsUntil:
    def test_later_today_needs_no_rollover(self):
        reference = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        assert _seconds_until(time(12, 0), reference) == 2 * 3600

    def test_already_passed_today_rolls_over_to_tomorrow(self):
        reference = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        assert _seconds_until(time(3, 0), reference) == 17 * 3600


class TestRunDaily:
    """`_run_daily` must survive a sweep that raises, and its stop event must
    still end the loop without running another sweep."""

    async def test_a_failing_sweep_is_logged_and_the_next_tick_still_runs(
        self, environment: Environment, monkeypatch, caplog
    ):
        calls: list[datetime] = []
        second_call = asyncio.Event()

        async def fake_delete_expired(factory, store, now, *, tmp_older_than, live_versions_kept):
            calls.append(now)
            if len(calls) == 1:
                raise RuntimeError("tijdelijke databankstoring")
            second_call.set()
            return CleanupResult(expired_previews=0, orphan_versions=0, tmp_swept=0)

        monkeypatch.setattr("plak.previews.cleanup_job.delete_expired", fake_delete_expired)
        # No real day-long wait between ticks: each iteration is due at once.
        monkeypatch.setattr("plak.previews.cleanup_job._seconds_until", lambda occurred_at, reference: 0.0)

        stop = asyncio.Event()
        with caplog.at_level(logging.ERROR):
            task = asyncio.create_task(
                _run_daily(
                    environment.session_factory,
                    environment.store,
                    occurred_at=TIMESTAMP_DEFAULT,
                    tmp_older_than=TMP_OLDER_THAN_DEFAULT,
                    live_versions_kept=5,
                    stop=stop,
                )
            )
            await asyncio.wait_for(second_call.wait(), timeout=5)
            stop.set()
            await asyncio.wait_for(task, timeout=5)

        assert len(calls) >= 2
        assert any("cleanup" in record.getMessage().lower() for record in caplog.records)
        assert any(record.levelno == logging.ERROR for record in caplog.records)

    async def test_stopping_while_waiting_runs_no_sweep_and_ends_the_task(
        self, environment: Environment, monkeypatch
    ):
        called = False

        async def fake_delete_expired(*args, **kwargs):
            nonlocal called
            called = True

        monkeypatch.setattr("plak.previews.cleanup_job.delete_expired", fake_delete_expired)
        # A long wait, so the task is still blocked on stop.wait() when stop is set.
        monkeypatch.setattr("plak.previews.cleanup_job._seconds_until", lambda occurred_at, reference: 3600.0)

        stop = asyncio.Event()
        task = asyncio.create_task(
            _run_daily(
                environment.session_factory,
                environment.store,
                occurred_at=TIMESTAMP_DEFAULT,
                tmp_older_than=TMP_OLDER_THAN_DEFAULT,
                live_versions_kept=5,
                stop=stop,
            )
        )
        await asyncio.sleep(0)  # lets the task start waiting on stop.wait()
        stop.set()
        await asyncio.wait_for(task, timeout=5)

        assert called is False

    async def test_the_kept_number_reaches_the_sweep(self, environment: Environment, monkeypatch):
        received: asyncio.Queue[int] = asyncio.Queue()

        async def fake_delete_expired(factory, store, now, *, tmp_older_than, live_versions_kept):
            received.put_nowait(live_versions_kept)
            return CleanupResult(expired_previews=0, orphan_versions=0, tmp_swept=0)

        monkeypatch.setattr("plak.previews.cleanup_job.delete_expired", fake_delete_expired)
        monkeypatch.setattr("plak.previews.cleanup_job._seconds_until", lambda occurred_at, reference: 0.0)

        async with cleanup_job(environment.session_factory, environment.store, live_versions_kept=3):
            assert await asyncio.wait_for(received.get(), timeout=5) == 3

    async def test_stop_already_set_before_the_first_tick_runs_no_sweep(
        self, environment: Environment, monkeypatch
    ):
        called = False

        async def fake_delete_expired(*args, **kwargs):
            nonlocal called
            called = True

        monkeypatch.setattr("plak.previews.cleanup_job.delete_expired", fake_delete_expired)

        stop = asyncio.Event()
        stop.set()
        await asyncio.wait_for(
            _run_daily(
                environment.session_factory,
                environment.store,
                occurred_at=TIMESTAMP_DEFAULT,
                tmp_older_than=TMP_OLDER_THAN_DEFAULT,
                live_versions_kept=5,
                stop=stop,
            ),
            timeout=5,
        )

        assert called is False


_EXTERN_ORIGIN_RE = re.compile(r"""(?:src|href)\s*=\s*["']https?://""", re.IGNORECASE)


# The assets we write ourselves; the bundled swagger-ui files do name URLs in
# their texts (RFC references) but load nothing from them.
_OWN_ASSETS = ("docs.css", "docs-init.js", "docs-theme.js")
_CSS_URL_RE = re.compile(r"""url\(\s*(?!["\']?data:)""", re.IGNORECASE)


class TestDocsNoExternalOrigins:
    def test_docs_html_points_not_to_external_origin(self):
        assert _EXTERN_ORIGIN_RE.search(_DOCS_HTML) is None
        assert "//cdn." not in _DOCS_HTML

    def test_own_assets_point_not_to_external_origin(self):
        for name in _OWN_ASSETS:
            content = (STATIC_DOCS_DIR / name).read_text(encoding="utf-8")
            assert _EXTERN_ORIGIN_RE.search(content) is None, name
            assert "http://" not in content, name
            assert "https://" not in content, name

    def test_swagger_stylesheet_loads_no_external_files(self):
        # A `url(https://fonts...)` in the bundled CSS would be the only way
        # left for the docs page to still fetch from a CDN.
        content = (STATIC_DOCS_DIR / "swagger-ui.css").read_text(encoding="utf-8")
        assert _CSS_URL_RE.search(content) is None

    def test_all_served_assets_exist_and_stand_on_the_allowlist(self):
        on_disk = {file.name for file in STATIC_DOCS_DIR.iterdir() if file.is_file()}
        # SHA256SUMS records which bytes the vendored files are; it is a
        # reviewed manifest, not an asset, and may never become servable.
        assert "SHA256SUMS" in on_disk
        assert "SHA256SUMS" not in _ASSETS
        assert on_disk - {"SHA256SUMS"} == set(_ASSETS)

    def test_the_theme_script_is_for_the_bundle_and_without_defer(self):
        # Swagger UI's dark theme hangs off `html.dark-mode` and knows no
        # prefers-color-scheme; this script sets that class. If it only runs
        # after rendering, the page flashes white first.
        header = _DOCS_HTML.split("</head>")[0]
        assert "docs-theme.js" in header
        # The tag itself, not the explanation beside it.
        tag = re.search(r"<script[^>]*docs-theme\.js[^>]*>", header)
        assert tag is not None
        assert "defer" not in tag.group(0)
        assert "async" not in tag.group(0)
        assert header.index("docs-theme.js") < _DOCS_HTML.index("swagger-ui-bundle.js")

    def test_the_dark_theme_sits_in_the_bundled_stylesheet(self):
        content = (STATIC_DOCS_DIR / "swagger-ui.css").read_text(encoding="utf-8")
        # If a new version dropped this, the theme script would set a class
        # nothing hangs off any more and dark mode would be silently gone.
        assert "html.dark-mode" in content

    def test_docs_csp_keeps_scripts_on_the_own_origin(self):
        # Swagger UI sets inline style attributes, so style-src is widened;
        # script-src must never be, because that is where the attack route is.
        assert "script-src 'self';" in DOCS_CSP
        assert "'unsafe-inline'" not in DOCS_CSP.split("style-src")[0]
        assert "<script>" not in _DOCS_HTML


class TestCliSweep:
    async def test_expired_device_codes_and_cli_sessions_go_and_live_ones_stay(self, environment: Environment):
        now_ = datetime.now(UTC)
        async with environment.session_factory() as db:
            # Fresh first: creating one sweeps what has expired by then.
            fresh = await cli.create_device_authorization(db, client_name=None, ip_truncated=None, now=now_)
            old = await cli.create_device_authorization(
                db, client_name=None, ip_truncated=None, now=now_ - timedelta(minutes=30)
            )
        async with environment.session_factory() as db:
            member = await db.get(Member, environment.member.id)
            member.status = MemberStatus.ACTIVE
            await db.commit()
            await cli.decide(db, fresh.user_code, member, approve=True, now=now_)
        async with environment.session_factory() as db:
            live = await cli.exchange_device_code(db, fresh.device_code, now=now_)
        async with environment.session_factory() as db:
            for expires_at, max_expires_at in (
                (now_ - timedelta(days=1), now_ + timedelta(days=10)),
                (now_ + timedelta(days=10), now_ - timedelta(days=1)),
            ):
                db.add(
                    CliSession(
                        member_id=environment.member.id,
                        expires_at=expires_at,
                        max_expires_at=max_expires_at,
                        access_selector=uuid.uuid4().hex,
                        access_hash="x",
                        access_expires_at=now_,
                    )
                )
            await db.commit()

        result = await delete_expired(environment.session_factory, environment.store, now_)

        assert (result.cli_device_authorizations, result.cli_sessions) == (1, 2)
        async with environment.session_factory() as db:
            authorizations = list(await db.scalars(select(CliDeviceAuthorization.id)))
            sessions = list(await db.scalars(select(CliSession.id)))
        assert old.authorization.id not in authorizations
        assert sessions == [live.session.id]
