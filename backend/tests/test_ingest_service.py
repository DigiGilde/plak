"""Tests for the ingest service against a real PostgreSQL (testcontainers).

These tests really commit (pointer swap, upsert, concurrency) and therefore
use sessions of their own with unique slugs per test, instead of the rollback
fixture db_verbinding from conftest.
"""

from __future__ import annotations

import asyncio
import io
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plak.config import Settings
from plak.constants import AccessBase
from plak.ingest.service import (
    PREVIEW_VALIDITY,
    ROOM_CHECK_STRIDE,
    Deployer,
    IngestError,
    IngestService,
    RoomGuard,
)
from plak.ingest.store import ContentStore
from plak.ingest.unpacker import BundleError
from plak.models.identity import Group, Member
from plak.models.publication import Preview, Site, Version, VersionTarget


@pytest_asyncio.fixture
async def session_factory(migrated_dsn: str):
    engine = create_async_engine(migrated_dsn)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class Environment:
    member: Member
    group: Group
    site: Site
    store: ContentStore
    service: IngestService
    deployer: Deployer
    content_root: Path
    session_factory: async_sessionmaker[AsyncSession]

    @property
    def sitedir(self) -> Path:
        return self.content_root / str(self.site.id)

    def source(self, content: bytes) -> Path:
        """Writes an upload to disk the way the API spools it (outside _tmp, so
        the tmp-is-empty assertions measure the ingest alone)."""
        path = self.content_root.parent / "uploads" / uuid.uuid4().hex
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
        return path

    def version_dirs(self) -> set[str]:
        if not self.sitedir.exists():
            return set()
        return {entry.name for entry in self.sitedir.iterdir()}


def _settings(content_root: Path, **overrides) -> Settings:
    return Settings(
        **overrides,
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

    content_root = tmp_path / "content"
    store = ContentStore(content_root)
    return Environment(
        member=member,
        group=group,
        site=site,
        store=store,
        service=IngestService(store, _settings(content_root)),
        deployer=Deployer(member_id=member.id),
        content_root=content_root,
        session_factory=session_factory,
    )


async def _live_pointer(environment: Environment) -> uuid.UUID | None:
    async with environment.session_factory() as session:
        return (
            await session.execute(
                select(Site.live_version_id).where(Site.id == environment.site.id)
            )
        ).scalar_one()


async def _versions(environment: Environment) -> list[Version]:
    async with environment.session_factory() as session:
        return list(
            (
                await session.execute(
                    select(Version).where(Version.site_id == environment.site.id)
                )
            ).scalars()
        )


async def _previews(environment: Environment) -> list[Preview]:
    async with environment.session_factory() as session:
        return list(
            (
                await session.execute(
                    select(Preview).where(Preview.site_id == environment.site.id)
                )
            ).scalars()
        )


def test_deployer_requires_exactly_a_origin():
    with pytest.raises(IngestError):
        Deployer()
    with pytest.raises(IngestError):
        Deployer(member_id=uuid.uuid4(), ci_repository="github.com/minbzk/website")


class TestDeploy:
    async def test_deploy_creates_version_and_set_live(self, environment: Environment):
        async with environment.session_factory() as session:
            version_id = await environment.service.deploy(
                session, environment.site, "index.html", environment.source(b"<h1>v1</h1>"),
                environment.deployer,
            )

        assert await _live_pointer(environment) == version_id
        (version,) = await _versions(environment)
        assert version.id == version_id
        assert version.target == VersionTarget.LIVE
        assert version.member_id == environment.member.id
        path = environment.store.file_path(version.storage_ref, "index.html")
        assert path is not None
        assert path.read_bytes() == b"<h1>v1</h1>"

    async def test_second_deploy_swaps_pointer_and_keeps_old_version(self, environment: Environment):
        async with environment.session_factory() as session:
            first = await environment.service.deploy(
                session, environment.site, "a.html", environment.source(b"v1"),
                environment.deployer,
            )
        async with environment.session_factory() as session:
            second_one = await environment.service.deploy(
                session, environment.site, "b.html", environment.source(b"v2"),
                environment.deployer,
            )

        assert await _live_pointer(environment) == second_one
        versions = await _versions(environment)
        assert {version.id for version in versions} == {first, second_one}
        # Retention: both versions keep their files (rollback and _version).
        assert environment.version_dirs() == {str(first), str(second_one)}

    async def test_zip_with_wrapping_dir_lands_on_the_site_root(self, environment: Environment):
        """An archive of the folder itself (Finder) rather than of its contents:
        the site belongs at /{group}/{site}/, not at .../dist/."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("dist/", b"")
            archive.writestr("dist/index.html", b"<h1>hoi</h1>")
            archive.writestr("dist/assets/stijl.css", b"body{}")
        async with environment.session_factory() as session:
            version_id = await environment.service.deploy(
                session,
                environment.site,
                "site.zip",
                environment.source(buf.getvalue()),
                environment.deployer,
            )

        (version,) = await _versions(environment)
        assert version.id == version_id
        version_dir = environment.content_root / version.storage_ref
        assert (version_dir / "index.html").read_bytes() == b"<h1>hoi</h1>"
        assert (version_dir / "assets" / "stijl.css").read_bytes() == b"body{}"
        assert not (version_dir / "dist").exists()

    async def test_invalid_bundle_leaves_nothing_behind(self, environment: Environment):
        async with environment.session_factory() as session:
            with pytest.raises(BundleError):
                await environment.service.deploy(
                    session, environment.site, "site.rar", environment.source(b"x"),
                    environment.deployer,
                )
        assert await _versions(environment) == []
        assert await _live_pointer(environment) is None
        assert environment.version_dirs() == set()
        assert list((environment.content_root / "_tmp").iterdir()) == []

    async def test_midway_refused_bundle_leaves_nothing_behind(self, environment: Environment):
        """A good entry followed by a refused one: the partly written work dir
        disappears completely and no versie is created."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("index.html", b"<h1>ok</h1>")
            archive.writestr("../ontsnapping", b"kwaad")
        async with environment.session_factory() as session:
            with pytest.raises(BundleError) as error:
                await environment.service.deploy(
                    session,
                    environment.site,
                    "site.zip",
                    environment.source(buf.getvalue()),
                    environment.deployer,
                )
        assert error.value.reason == "PATH_TRAVERSAL"
        assert await _versions(environment) == []
        assert environment.version_dirs() == set()
        assert list((environment.content_root / "_tmp").iterdir()) == []

    async def test_bundle_without_index_leaves_nothing_behind(self, environment: Environment):
        """The refusal lands after unpacking; the work dir with the files
        already written disappears completely and no versie is created."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("mijnsite/README.md", b"x")
            archive.writestr("mijnsite/dist/index.html", b"<h1>hoi</h1>")
        async with environment.session_factory() as session:
            with pytest.raises(BundleError) as error:
                await environment.service.deploy(
                    session,
                    environment.site,
                    "site.zip",
                    environment.source(buf.getvalue()),
                    environment.deployer,
                )
        assert error.value.reason == "NO_INDEX"
        assert error.value.index_candidates == ("dist/index.html",)
        assert await _versions(environment) == []
        assert await _live_pointer(environment) is None
        assert environment.version_dirs() == set()
        assert list((environment.content_root / "_tmp").iterdir()) == []

    async def test_base_path_publishes_only_that_dir(self, environment: Environment):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("mijnsite/README.md", b"x")
            archive.writestr("mijnsite/dist/index.html", b"<h1>hoi</h1>")
        async with environment.session_factory() as session:
            version_id = await environment.service.deploy(
                session,
                environment.site,
                "site.zip",
                environment.source(buf.getvalue()),
                environment.deployer,
                base_path="dist",
            )

        (version,) = await _versions(environment)
        assert version.id == version_id
        version_dir = environment.content_root / version.storage_ref
        assert (version_dir / "index.html").read_bytes() == b"<h1>hoi</h1>"
        assert not (version_dir / "README.md").exists()

    async def test_db_error_cleans_files_on(self, environment: Environment):
        # Non-existent lid: an FK violation on the versie insert.
        ghost_deployer = Deployer(member_id=uuid.uuid4())
        async with environment.session_factory() as session:
            with pytest.raises(IntegrityError):
                await environment.service.deploy(
                    session, environment.site, "index.html", environment.source(b"x"),
                    ghost_deployer,
                )
        assert await _versions(environment) == []
        assert environment.version_dirs() == set()


class TestPreviewDeploy:
    async def test_new_preview(self, environment: Environment):
        before = datetime.now(tz=UTC)
        async with environment.session_factory() as session:
            version_id = await environment.service.preview_deploy(
                session, environment.site, "pr-7", "index.html", environment.source(b"p1"),
                environment.deployer,
            )

        (preview,) = await _previews(environment)
        assert preview.ref == "pr-7"
        assert preview.version_id == version_id
        assert preview.access_base_override is None
        expected = before + PREVIEW_VALIDITY
        assert abs((preview.expires_at - expected).total_seconds()) < 120
        (version,) = await _versions(environment)
        assert version.target == VersionTarget.PREVIEW
        assert await _live_pointer(environment) is None

    async def test_upsert_same_ref_replaces_version_and_files(self, environment: Environment):
        async with environment.session_factory() as session:
            first = await environment.service.preview_deploy(
                session, environment.site, "pr-1", "index.html", environment.source(b"p1"),
                environment.deployer,
            )
        # Override set in between: it has to survive the redeploy.
        async with environment.session_factory() as session, session.begin():
            await session.execute(
                update(Preview)
                .where(Preview.site_id == environment.site.id, Preview.ref == "pr-1")
                .values(
                    access_base_override=AccessBase.NOBODY,
                    access_keys_override=False,
                    access_invitees_override=True,
                )
            )
        async with environment.session_factory() as session:
            second_one = await environment.service.preview_deploy(
                session, environment.site, "pr-1", "index.html", environment.source(b"p2"),
                environment.deployer,
            )

        (preview,) = await _previews(environment)
        assert preview.version_id == second_one
        assert preview.access_base_override == AccessBase.NOBODY
        assert preview.access_invitees_override is True
        versions = await _versions(environment)
        assert [version.id for version in versions] == [second_one]
        # Replaced preview versie: row gone and files gone.
        assert environment.version_dirs() == {str(second_one)}
        assert first != second_one

    async def test_different_refs_alongside_each_other(self, environment: Environment):
        async with environment.session_factory() as session:
            await environment.service.preview_deploy(
                session, environment.site, "pr-1", "index.html", environment.source(b"a"),
                environment.deployer,
            )
        async with environment.session_factory() as session:
            await environment.service.preview_deploy(
                session, environment.site, "pr-2", "index.html", environment.source(b"b"),
                environment.deployer,
            )
        assert len(await _previews(environment)) == 2
        assert len(await _versions(environment)) == 2

    async def test_db_error_cleans_files_on(self, environment: Environment):
        # Non-existent lid: an FK violation on the versie insert.
        ghost_deployer = Deployer(member_id=uuid.uuid4())
        async with environment.session_factory() as session:
            with pytest.raises(IntegrityError):
                await environment.service.preview_deploy(
                    session, environment.site, "pr-ghost", "index.html",
                    environment.source(b"p"), ghost_deployer,
                )
        assert await _versions(environment) == []
        assert await _previews(environment) == []
        assert environment.version_dirs() == set()

    async def test_concurrent_same_ref_deploys(self, environment: Environment):
        async def deploy_task(content: bytes) -> uuid.UUID:
            async with environment.session_factory() as session:
                return await environment.service.preview_deploy(
                    session,
                    environment.site,
                    "pr-race",
                    "index.html",
                    environment.source(content),
                    environment.deployer,
                )

        results = await asyncio.gather(deploy_task(b"race-a"), deploy_task(b"race-b"))

        previews = await _previews(environment)
        assert len(previews) == 1
        winner = previews[0].version_id
        assert winner in results
        versions = await _versions(environment)
        assert [version.id for version in versions] == [winner]
        # No orphaned files: only the winning versie is on disk.
        assert environment.version_dirs() == {str(winner)}
        assert list((environment.content_root / "_tmp").iterdir()) == []


class TestDeletePreview:
    async def test_deletes_row_version_and_files(self, environment: Environment):
        async with environment.session_factory() as session:
            await environment.service.preview_deploy(
                session, environment.site, "pr-1", "index.html", environment.source(b"p"),
                environment.deployer,
            )
        async with environment.session_factory() as session:
            assert await environment.service.delete_preview(session, environment.site, "pr-1") is True
        assert await _previews(environment) == []
        assert await _versions(environment) == []
        assert environment.version_dirs() == set()

    async def test_idempotent(self, environment: Environment):
        async with environment.session_factory() as session:
            assert await environment.service.delete_preview(session, environment.site, "pr-x") is False
        async with environment.session_factory() as session:
            await environment.service.preview_deploy(
                session, environment.site, "pr-x", "index.html", environment.source(b"p"),
                environment.deployer,
            )
        async with environment.session_factory() as session:
            assert await environment.service.delete_preview(session, environment.site, "pr-x") is True
        async with environment.session_factory() as session:
            assert await environment.service.delete_preview(session, environment.site, "pr-x") is False


class TestRollback:
    async def test_rollback_to_earlier_live_version(self, environment: Environment):
        async with environment.session_factory() as session:
            first = await environment.service.deploy(
                session, environment.site, "a.html", environment.source(b"v1"),
                environment.deployer,
            )
        async with environment.session_factory() as session:
            await environment.service.deploy(
                session, environment.site, "b.html", environment.source(b"v2"),
                environment.deployer,
            )
        async with environment.session_factory() as session:
            await environment.service.rollback_to(session, environment.site, first)
        assert await _live_pointer(environment) == first

    async def test_refuses_preview_version(self, environment: Environment):
        async with environment.session_factory() as session:
            preview_version = await environment.service.preview_deploy(
                session, environment.site, "pr-1", "index.html", environment.source(b"p"),
                environment.deployer,
            )
        async with environment.session_factory() as session:
            with pytest.raises(IngestError) as error:
                await environment.service.rollback_to(session, environment.site, preview_version)
        assert error.value.reason == "ROLLBACK_TARGET_PREVIEW"
        assert await _live_pointer(environment) is None

    async def test_refuses_version_of_other_site(self, environment: Environment):
        other = Site(
            id=uuid.uuid4(),
            group_id=environment.group.id,
            slug=f"ander{uuid.uuid4().hex[:8]}",
            title="Ander",
            access_base=AccessBase.PUBLIC,
        )
        async with environment.session_factory() as session, session.begin():
            session.add(other)
        async with environment.session_factory() as session:
            someone_elses_version = await environment.service.deploy(
                session, other, "index.html", environment.source(b"x"), environment.deployer
            )
        async with environment.session_factory() as session:
            with pytest.raises(IngestError) as error:
                await environment.service.rollback_to(session, environment.site, someone_elses_version)
        assert error.value.reason == "VERSION_OTHER_SITE"
        assert await _live_pointer(environment) is None

    async def test_refuses_unknown_version(self, environment: Environment):
        async with environment.session_factory() as session:
            with pytest.raises(IngestError) as error:
                await environment.service.rollback_to(session, environment.site, uuid.uuid4())
        assert error.value.reason == "UNKNOWN_VERSION"


async def test_preview_expires_on_column_type(environment: Environment):
    """expires_at is stored with a timezone and lies about 30 days ahead."""
    async with environment.session_factory() as session:
        await environment.service.preview_deploy(
            session, environment.site, "pr-tz", "index.html", environment.source(b"p"),
            environment.deployer,
        )
    async with environment.session_factory() as session:
        expires_at, now_ = (
            await session.execute(
                select(Preview.expires_at, func.now()).where(
                    Preview.site_id == environment.site.id, Preview.ref == "pr-tz"
                )
            )
        ).one()
    assert expires_at.tzinfo is not None
    assert timedelta(days=29) < (expires_at - now_) < timedelta(days=31)


class TestStorageRoom:
    """Bounds on the history a site leaves behind, rather than on one bundle:
    live versions are kept forever, so without these a site fills the volume
    by publishing often enough."""

    def _service(self, environment: Environment, **overrides) -> IngestService:
        return IngestService(environment.store, _settings(environment.content_root, **overrides))

    async def test_a_deploy_over_the_site_quota_is_refused(self, environment: Environment):
        service = self._service(environment, site_max_bytes=200)
        async with environment.session_factory() as session:
            await service.deploy(
                session, environment.site, "index.html",
                environment.source(b"x" * 150), environment.deployer,
            )
        async with environment.session_factory() as session:
            with pytest.raises(IngestError) as error:
                await service.deploy(
                    session, environment.site, "index.html",
                    environment.source(b"x" * 150), environment.deployer,
                )
        assert error.value.reason == "SITE_QUOTA_EXCEEDED"

    async def test_the_refused_version_leaves_nothing_behind(self, environment: Environment):
        """The check sits inside write_version, so the work directory is
        cleaned up and the earlier version is the only one left."""
        service = self._service(environment, site_max_bytes=200)
        async with environment.session_factory() as session:
            await service.deploy(
                session, environment.site, "index.html",
                environment.source(b"x" * 150), environment.deployer,
            )
        async with environment.session_factory() as session:
            with pytest.raises(IngestError):
                await service.deploy(
                    session, environment.site, "index.html",
                    environment.source(b"x" * 150), environment.deployer,
                )
        assert len(environment.version_dirs()) == 1
        assert list((environment.content_root / "_tmp").iterdir()) == []
        assert len(await _versions(environment)) == 1

    async def test_a_quota_of_zero_lets_everything_through(self, environment: Environment):
        service = self._service(environment, site_max_bytes=0)
        async with environment.session_factory() as session:
            for _ in range(3):
                await service.deploy(
                    session, environment.site, "index.html",
                    environment.source(b"x" * 150), environment.deployer,
                )
        assert len(environment.version_dirs()) == 3

    async def test_check_room_refuses_when_the_volume_is_nearly_full(self, environment: Environment):
        # A floor above the size of any real volume, so the check fires on the
        # free space this test run actually has.
        service = self._service(environment, storage_min_free_bytes=2**62)
        with pytest.raises(IngestError) as error:
            service.check_room()
        assert error.value.reason == "STORAGE_UNAVAILABLE"

    async def test_check_room_passes_on_a_real_volume_with_room(self, environment: Environment):
        self._service(environment, storage_min_free_bytes=1).check_room()

    async def test_check_room_counts_the_declared_size_against_the_floor(
        self, environment: Environment, monkeypatch: pytest.MonkeyPatch
    ):
        _fake_volume(monkeypatch, environment.store, free=FLOOR + 1000)
        service = self._service(environment, storage_min_free_bytes=FLOOR)
        service.check_room(1000)
        with pytest.raises(IngestError) as error:
            service.check_room(1001)
        assert error.value.reason == "STORAGE_UNAVAILABLE"

    async def test_check_room_is_off_at_zero(
        self, environment: Environment, monkeypatch: pytest.MonkeyPatch
    ):
        _fake_volume(monkeypatch, environment.store, free=0)
        self._service(environment, storage_min_free_bytes=0).check_room(10**12)

    async def test_a_small_deploy_passes_on_a_nearly_full_volume(
        self, environment: Environment, monkeypatch: pytest.MonkeyPatch
    ):
        """The production failure: a 349-byte page refused on a 1 GiB volume
        because the check asked for room for the largest deploy allowed."""
        settings = _settings(environment.content_root)
        free = settings.storage_min_free_bytes + 64 * 1024
        assert free < (
            settings.storage_min_free_bytes + settings.ingest_max_body + settings.ingest_max_total
        )
        _fake_volume(monkeypatch, environment.store, free=free)
        service = IngestService(environment.store, settings)
        service.check_room(349)
        async with environment.session_factory() as session:
            version_id = await service.deploy(
                session, environment.site, "index.html",
                environment.source(b"x" * 349), environment.deployer,
            )
        assert environment.version_dirs() == {str(version_id)}

    @pytest.mark.parametrize("preview", [False, True])
    async def test_unpacking_past_the_floor_is_refused_and_leaves_nothing_behind(
        self, environment: Environment, monkeypatch: pytest.MonkeyPatch, preview: bool
    ):
        """The upload itself is small, so the check before spooling lets it
        in; what it unpacks to does not fit above the floor."""
        _fake_volume(monkeypatch, environment.store, free=FLOOR + 256 * 1024)
        service = self._service(environment, storage_min_free_bytes=FLOOR)
        source = environment.source(_compressible_zip(1024 * 1024))
        assert source.stat().st_size < 64 * 1024
        async with environment.session_factory() as session:
            with pytest.raises(IngestError) as error:
                if preview:
                    await service.preview_deploy(
                        session, environment.site, "pr-1", "site.zip",
                        source, environment.deployer,
                    )
                else:
                    await service.deploy(
                        session, environment.site, "site.zip",
                        source, environment.deployer,
                    )
        assert error.value.reason == "STORAGE_UNAVAILABLE"
        assert environment.version_dirs() == set()
        assert list((environment.content_root / "_tmp").iterdir()) == []
        assert await _versions(environment) == []
        assert await _previews(environment) == []
        assert await _live_pointer(environment) is None


FLOOR = 1024 * 1024


def _tree_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _fake_volume(monkeypatch: pytest.MonkeyPatch, store: ContentStore, *, free: int) -> None:
    """A content volume with `free` bytes free now, that fills up with what
    lands under the content root from here on and empties with what leaves."""
    baseline = _tree_size(store.root)
    monkeypatch.setattr(store, "free_bytes", lambda: free - (_tree_size(store.root) - baseline))


def _compressible_zip(unpacked: int) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", b"<h1>hoi</h1>")
        archive.writestr("big.bin", b"\0" * unpacked)
    return buffer.getvalue()


class _Volume:
    """Stands in for the ContentStore: free space the test sets, and a count
    of how often the guard measured it."""

    def __init__(self, free: int) -> None:
        self.free = free
        self.measured = 0

    def free_bytes(self) -> int:
        self.measured += 1
        return self.free


class TestRoomGuard:
    def _guard(self, volume: _Volume, min_free: int = FLOOR) -> RoomGuard:
        return RoomGuard(volume, min_free)

    def test_a_chunk_that_would_cross_the_floor_is_refused(self):
        self._guard(_Volume(FLOOR + 100)).reserve(100)
        with pytest.raises(IngestError) as error:
            self._guard(_Volume(FLOOR + 100)).reserve(101)
        assert error.value.reason == "STORAGE_UNAVAILABLE"

    def test_it_counts_down_between_measurements(self):
        volume = _Volume(FLOOR + 1024 * 1024)
        guard = self._guard(volume)
        for _ in range(10):
            guard.reserve(4096)
        assert volume.measured == 1

    def test_the_writes_it_counted_down_are_refused_once_they_show_on_the_volume(self):
        volume = _Volume(FLOOR + 64 * 1024)
        guard = self._guard(volume)
        guard.reserve(64 * 1024)
        # The volume now shows those bytes; the next chunk has no room left.
        volume.free = FLOOR
        with pytest.raises(IngestError):
            guard.reserve(1)
        assert volume.measured == 2

    def test_it_measures_again_before_refusing_on_the_countdown(self):
        """Other writers free space too (a replaced preview, the cleanup job):
        the countdown alone never refuses."""
        volume = _Volume(FLOOR + 10)
        guard = self._guard(volume)
        guard.reserve(10)
        volume.free = FLOOR + 20
        guard.reserve(15)
        assert volume.measured == 2

    def test_it_measures_again_after_every_stride(self):
        """Another writer can take the room between measurements; the stride
        bounds how far this deploy writes on a stale reading."""
        volume = _Volume(FLOOR + 10 * ROOM_CHECK_STRIDE)
        guard = self._guard(volume)
        guard.reserve(ROOM_CHECK_STRIDE)
        assert volume.measured == 1
        volume.free = FLOOR
        with pytest.raises(IngestError):
            guard.reserve(1)
        assert volume.measured == 2

    def test_a_floor_of_zero_turns_it_off(self):
        volume = _Volume(0)
        self._guard(volume, min_free=0).reserve(10**12)
        assert volume.measured == 0
