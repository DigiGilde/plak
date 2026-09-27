"""Tests for previews/cleanup_job.py: expired previews, orphaned preview
versions and stale _tmp get swept; and that the docs HTML touches no external
origin (spec §8).

The DB tests really commit against the test container database (through a
session factory of their own), just like test_ingest_service.py.
"""

from __future__ import annotations

import asyncio
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest_asyncio
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.util import await_only

from plak.api.docs import _ASSETS, _DOCS_HTML, DOCS_CSP, STATIC_DOCS_DIR
from plak.cli import service as cli
from plak.config import Settings
from plak.constants import AccessBase
from plak.ingest.service import Deployer, IngestService
from plak.ingest.store import ContentStore
from plak.models.cli import CliDeviceAuthorization, CliSession
from plak.models.identity import Group, Member, MemberStatus
from plak.models.publication import Preview, Site, Version, VersionTarget
from plak.previews.cleanup_job import delete_expired


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
        assert on_disk == set(_ASSETS)

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
