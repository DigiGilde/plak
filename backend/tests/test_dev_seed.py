"""The dev seed (dev/seed.py) against a real database.

The seed is the only thing that refills a local environment after a schema
change, so it is exactly the script that rots unnoticed: nothing else imports
it and a stale column name only shows up when someone needs their dev data
back. These tests run it end to end against the migrated test database and a
throwaway content root.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plak.config import Settings
from plak.constants import AccessBase, Role
from plak.db import make_engine, make_session_factory
from plak.models.identity import Group, GroupMember, Member, MemberStatus, PlatformRole, SiteMember
from plak.models.publication import AccessKey, Invitee, Preview, Site, Version

SEED_PATH = Path(__file__).resolve().parents[2] / "dev" / "seed.py"


@pytest.fixture(scope="session")
def seed_module():
    """Loads dev/seed.py by path: it sits outside the installed package on
    purpose, because it is dev tooling and never ships in the image."""
    spec = importlib.util.spec_from_file_location("plak_dev_seed", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    # Registering before exec: `from __future__ import annotations` leaves the
    # dataclass field types as strings, and dataclasses resolves them through
    # sys.modules[cls.__module__].
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def settings(migrated_dsn: str, tmp_path: Path) -> Settings:
    return Settings(
        db_url=migrated_dsn,
        content_root=tmp_path / "content",
        # The seed never logs in; the OIDC fields are here only because
        # Settings refuses to be built without them.
        oidc_issuer="https://idp.example.org",
        oidc_client_id="plak-test",
        oidc_client_auth="client_secret_post",
        oidc_client_secret="client-secret-van-minstens-32-bytes!!",
        session_secret="s" * 32,
        audit_pepper="p" * 32,
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        content_base_url="https://plak.example",
        environment="dev",
        bootstrap_admin_sub="dev-beheerder",
    )


@pytest_asyncio.fixture
async def session_factory(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(settings)
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


def _version_dirs(root: Path) -> set[str]:
    """Every {site_id}/{version_id} directory on the content root."""
    return {
        str(path.relative_to(root))
        for path in root.glob("*/*")
        if path.is_dir() and path.relative_to(root).parts[0] != "_tmp"
    }


async def _count(db: AsyncSession, model) -> int:
    return await db.scalar(select(func.count()).select_from(model))


class TestSeed:
    async def test_two_groups_with_a_different_default_access(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            groups = list(await db.scalars(select(Group).order_by(Group.slug)))
        assert len(groups) == 2
        assert {group.default_access_base for group in groups} == {
            AccessBase.PUBLIC,
            AccessBase.SITE_TEAM,
        }

    async def test_one_group_holds_an_admin_an_editor_and_a_reader(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            group = await db.scalar(select(Group).where(Group.slug == seed_module.GROUP_PUBLIC))
            roles = list(
                await db.scalars(select(GroupMember.role).where(GroupMember.group_id == group.id))
            )
        assert sorted(roles) == sorted([Role.ADMIN, Role.EDITOR, Role.READER])

    async def test_the_bootstrap_member_is_a_platform_admin_and_a_group_admin(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        async with session_factory() as db_pre:
            assert await _count(db_pre, Member) == 0
        await seed_module.seed(settings)
        async with session_factory() as db:
            member = await db.scalar(
                select(Member).where(Member.sso_subject == settings.bootstrap_admin_sub)
            )
            group = await db.scalar(select(Group).where(Group.slug == seed_module.GROUP_PUBLIC))
            role = await db.scalar(
                select(GroupMember.role).where(
                    GroupMember.group_id == group.id, GroupMember.member_id == member.id
                )
            )
        assert member.platform_role == PlatformRole.ADMIN
        assert member.status == MemberStatus.ACTIVE
        assert role == Role.ADMIN

    async def test_one_member_has_a_site_role_without_group_membership(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            site_member = (
                await db.scalars(
                    select(SiteMember).where(
                        SiteMember.member_id.not_in(select(GroupMember.member_id))
                    )
                )
            ).one()
        assert site_member.role == Role.EDITOR

    async def test_a_member_is_deactivated(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            deactivated = list(
                await db.scalars(select(Member).where(Member.status == MemberStatus.DEACTIVATED))
            )
        assert len(deactivated) == 1

    async def test_every_access_base_occurs(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        await seed_module.seed(settings)
        async with session_factory() as db:
            bases = set(await db.scalars(select(Site.access_base)))
        assert bases == set(AccessBase)

    async def test_an_extra_occurs_beside_a_base_that_already_lets_people_in(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        """The combination the old single level could not express, so the dev
        interface has one site that shows it without anyone setting it up."""
        await seed_module.seed(settings)
        async with session_factory() as db:
            combined = list(
                await db.scalars(
                    select(Site.slug).where(
                        Site.access_base != AccessBase.NOBODY,
                        Site.access_keys.is_(True),
                    )
                )
            )
        assert combined == ["toezichtrapport"]

    async def test_a_secret_link_and_an_invitee_list_exist(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        result = await seed_module.seed(settings)
        selector, _, verifier = result.secret_link_key.partition(".")
        async with session_factory() as db:
            access_key = await db.scalar(select(AccessKey).where(AccessKey.selector == selector))
            key_site = await db.get(Site, access_key.site_id)
            invitees = list(await db.scalars(select(Invitee)))
            invitee_site = await db.get(Site, invitees[0].site_id)
        assert verifier
        assert key_site.access_keys is True
        assert len(invitees) == 3
        assert invitee_site.access_invitees is True

    async def test_the_published_versions_really_stand_on_the_content_volume(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        result = await seed_module.seed(settings)
        async with session_factory() as db:
            refs = list(await db.scalars(select(Version.storage_ref)))
            live = list(
                await db.scalars(select(Site.slug).where(Site.live_version_id.is_not(None)))
            )
        assert result.versions == len(refs) == 6
        assert len(live) == 4
        for storage_ref in refs:
            assert (settings.content_root / storage_ref / "index.html").is_file()
            assert (settings.content_root / storage_ref / "assets" / "plak.css").is_file()

    async def test_a_preview_may_be_stricter_than_its_site(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        result = await seed_module.seed(settings)
        async with session_factory() as db:
            previews = {
                preview.ref: preview.access_base_override
                for preview in await db.scalars(select(Preview))
            }
        assert result.previews == 2
        assert previews[seed_module.PREVIEW_OPEN] is None
        assert previews[seed_module.PREVIEW_RESTRICTED] == AccessBase.SITE_TEAM

    async def test_seeding_twice_leaves_no_second_set_and_no_orphan_files(
        self, seed_module, settings: Settings, session_factory
    ) -> None:
        first = await seed_module.seed(settings)
        second = await seed_module.seed(settings)
        async with session_factory() as db:
            refs = set(await db.scalars(select(Version.storage_ref)))
            counts = (
                await _count(db, Member),
                await _count(db, Group),
                await _count(db, Site),
                await _count(db, Preview),
                await _count(db, AccessKey),
                await _count(db, Invitee),
            )
        assert counts == (6, 2, 5, 2, 2, 3)
        assert (second.members, second.groups, second.sites, second.versions) == (
            first.members,
            first.groups,
            first.sites,
            first.versions,
        )
        # The wipe takes the files of the previous run with it, so no version
        # directory survives that no row points at any more.
        assert _version_dirs(settings.content_root) == refs

    async def test_the_seed_refuses_to_run_outside_dev(
        self, seed_module, settings: Settings
    ) -> None:
        production = settings.model_copy(update={"environment": "productie"})
        with pytest.raises(RuntimeError, match="dev"):
            await seed_module.seed(production)


class TestReport:
    async def test_the_report_points_at_the_cli_login_and_names_no_secret_but_the_link_key(
        self, seed_module, settings: Settings
    ) -> None:
        result = await seed_module.seed(settings)
        report = seed_module._report(settings, result)
        assert "plak login" in report
        assert "/cli-link" in report
        assert "plak_" not in report.replace("plak login", "")
