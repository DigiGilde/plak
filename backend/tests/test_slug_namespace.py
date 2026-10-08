"""The slug namespace (migration 0005): the current and the retired slugs of
groups and sites, one table per kind, kept by triggers on `groups` and
`sites`.

Against the real PostgreSQL test container, through the ORM the app uses.
The constraint a refusal names is what api/admin.py turns into a 409, so the
tests name it too: `uq_*` for the current slug of another group or site,
`pk_*` for a slug another one gave up and still holds.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from plak.constants import AccessBase
from plak.db import make_session_factory, violated_constraint
from plak.models.ci import CiProvider, SiteRepository
from plak.models.identity import Group
from plak.models.publication import Site
from plak.models.slugs import GroupSlug, SiteSlug


@pytest_asyncio.fixture
async def factory(migrated_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_dsn, poolclass=NullPool)
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class Namespace:
    """The groups, or the sites of one group: the same rules, written once."""

    current: str
    previous: str
    group_id: uuid.UUID | None = None

    @property
    def model(self) -> type[Group] | type[Site]:
        return Group if self.group_id is None else Site

    def new(self, slug: str) -> Group | Site:
        if self.group_id is None:
            return Group(slug=slug, name=slug, default_access_base=AccessBase.PUBLIC)
        return Site(group_id=self.group_id, slug=slug, title=slug, access_base=AccessBase.PUBLIC)

    async def create(self, factory, slug: str) -> uuid.UUID:
        async with factory() as db:
            row = self.new(slug)
            db.add(row)
            await db.commit()
            return row.id

    async def rename(self, factory, row_id: uuid.UUID, slug: str) -> None:
        async with factory() as db:
            (await db.get(self.model, row_id)).slug = slug
            await db.commit()

    async def delete(self, factory, row_id: uuid.UUID) -> None:
        async with factory() as db:
            await db.delete(await db.get(self.model, row_id))
            await db.commit()

    async def slugs(self, factory) -> dict[str, tuple[uuid.UUID, bool]]:
        """Every slug in the namespace, with whose it is and whether it is retired."""
        async with factory() as db:
            if self.group_id is None:
                rows = await db.execute(select(GroupSlug.slug, GroupSlug.group_id, GroupSlug.retired_at))
            else:
                rows = await db.execute(
                    select(SiteSlug.slug, SiteSlug.site_id, SiteSlug.retired_at).where(
                        SiteSlug.group_id == self.group_id
                    )
                )
            return {slug: (owner, retired_at is not None) for slug, owner, retired_at in rows}


GROUPS = Namespace(current="uq_groups_slug", previous="pk_group_slugs")


def _sites_of(group_id: uuid.UUID) -> Namespace:
    return Namespace(current="uq_sites_group_slug", previous="pk_site_slugs", group_id=group_id)


@pytest_asyncio.fixture(params=["group", "site"])
async def namespace(request, factory) -> Namespace:
    if request.param == "group":
        return GROUPS
    return _sites_of(await GROUPS.create(factory, "team"))


async def _refusal(attempt) -> str | None:
    """The constraint an attempt that has to fail ran into."""
    with pytest.raises(IntegrityError) as error:
        await attempt
    return violated_constraint(error.value)


async def _wait_for_a_lock_wait(factory) -> None:
    """Returns once another backend is blocked on a lock."""
    async with factory() as db:
        for _ in range(200):
            waiting = await db.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE wait_event_type = 'Lock' AND datname = current_database()"
                )
            )
            await db.rollback()
            if waiting:
                return
            await asyncio.sleep(0.05)
    raise AssertionError("the other transaction never waited")


class TestTheNamespace:
    async def test_a_new_one_holds_its_slug_as_the_current_one(self, factory, namespace):
        owner = await namespace.create(factory, "aurora")

        assert await namespace.slugs(factory) == {"aurora": (owner, False)}

    async def test_a_second_one_with_that_slug_runs_into_the_current_one(self, factory, namespace):
        await namespace.create(factory, "aurora")

        assert await _refusal(namespace.create(factory, "aurora")) == namespace.current

    async def test_a_rename_retires_the_old_slug_and_makes_the_new_one_current(self, factory, namespace):
        owner = await namespace.create(factory, "oud")

        await namespace.rename(factory, owner, "nieuw")

        assert await namespace.slugs(factory) == {"oud": (owner, True), "nieuw": (owner, False)}

    async def test_a_retired_slug_cannot_be_taken_by_a_new_one(self, factory, namespace):
        owner = await namespace.create(factory, "oud")
        await namespace.rename(factory, owner, "nieuw")

        assert await _refusal(namespace.create(factory, "oud")) == namespace.previous
        assert await namespace.slugs(factory) == {"oud": (owner, True), "nieuw": (owner, False)}

    async def test_a_retired_slug_cannot_be_taken_by_a_rename_and_stays_with_its_owner(self, factory, namespace):
        """The pitfall the triggers avoid: an upsert whose WHERE does not hold
        skips the row without an error, and the slug would change hands."""
        owner = await namespace.create(factory, "oud")
        await namespace.rename(factory, owner, "nieuw")
        other = await namespace.create(factory, "ander")

        assert await _refusal(namespace.rename(factory, other, "oud")) == namespace.previous
        assert await namespace.slugs(factory) == {
            "oud": (owner, True),
            "nieuw": (owner, False),
            "ander": (other, False),
        }

    async def test_renaming_back_makes_the_old_slug_current_again(self, factory, namespace):
        owner = await namespace.create(factory, "a")
        await namespace.rename(factory, owner, "b")

        await namespace.rename(factory, owner, "a")

        assert await namespace.slugs(factory) == {"a": (owner, False), "b": (owner, True)}

    async def test_while_it_goes_back_and_forth_nobody_else_claims_either_slug(self, factory, namespace):
        owner = await namespace.create(factory, "a")
        await namespace.rename(factory, owner, "b")
        assert await _refusal(namespace.create(factory, "a")) == namespace.previous

        await namespace.rename(factory, owner, "a")

        assert await _refusal(namespace.create(factory, "b")) == namespace.previous
        assert await _refusal(namespace.create(factory, "a")) == namespace.current

    async def test_deleting_it_frees_every_slug_it_had(self, factory, namespace):
        owner = await namespace.create(factory, "oud")
        await namespace.rename(factory, owner, "nieuw")

        await namespace.delete(factory, owner)

        assert await namespace.slugs(factory) == {}
        claimant = await namespace.create(factory, "oud")
        assert await namespace.slugs(factory) == {"oud": (claimant, False)}

    async def test_another_column_leaves_the_namespace_alone(self, factory, namespace):
        owner = await namespace.create(factory, "aurora")
        column = "name" if namespace.group_id is None else "title"

        async with factory() as db:
            await db.execute(update(namespace.model).where(namespace.model.id == owner).values({column: "Anders"}))
            await db.commit()

        assert await namespace.slugs(factory) == {"aurora": (owner, False)}


class TestAGroup:
    async def test_deleting_it_frees_the_slugs_of_its_sites_too(self, factory):
        group_id = await GROUPS.create(factory, "team")
        sites = _sites_of(group_id)
        site_id = await sites.create(factory, "oud")
        await sites.rename(factory, site_id, "nieuw")

        await GROUPS.delete(factory, group_id)

        async with factory() as db:
            assert list(await db.scalars(select(SiteSlug.slug))) == []
            assert list(await db.scalars(select(GroupSlug.slug))) == []

    async def test_a_rename_leaves_the_slugs_of_its_sites_alone(self, factory):
        group_id = await GROUPS.create(factory, "team")
        sites = _sites_of(group_id)
        site_id = await sites.create(factory, "docs")

        await GROUPS.rename(factory, group_id, "ploeg")

        assert await sites.slugs(factory) == {"docs": (site_id, False)}


class TestASite:
    async def test_the_same_slug_lives_in_two_groups_at_once(self, factory):
        first = _sites_of(await GROUPS.create(factory, "een"))
        second = _sites_of(await GROUPS.create(factory, "twee"))
        moved = await first.create(factory, "docs")
        await first.rename(factory, moved, "handleiding")

        other = await second.create(factory, "docs")

        assert await first.slugs(factory) == {"docs": (moved, True), "handleiding": (moved, False)}
        assert await second.slugs(factory) == {"docs": (other, False)}


async def _exempt_link(factory, site_id: uuid.UUID) -> None:
    async with factory() as db:
        db.add(
            SiteRepository(
                site_id=site_id,
                provider=CiProvider.GITHUB,
                host="https://github.com",
                owner="minbzk",
                repo="website",
                repository_id=1001,
                owner_id=2002,
                site_id_required=False,
            )
        )
        await db.commit()


async def _site_id_required(factory) -> dict[str, bool]:
    async with factory() as db:
        rows = await db.execute(
            select(Group.slug, Site.slug, SiteRepository.site_id_required)
            .join(Site, Site.group_id == Group.id)
            .join(SiteRepository, SiteRepository.site_id == Site.id)
        )
        return {f"{group}/{site}": required for group, site, required in rows}


class TestTheSiteIdAfterARename:
    """A link that still takes a token without the site id stops doing so
    once its site has another address: what a workflow that still names the
    old address publishes must not follow the site there, nor reach whoever
    claims that address later (spec 6.4)."""

    @pytest_asyncio.fixture
    async def sites(self, factory) -> dict[str, uuid.UUID]:
        team = _sites_of(await GROUPS.create(factory, "team"))
        other = _sites_of(await GROUPS.create(factory, "ander"))
        ids = {
            "team": team.group_id,
            "ander": other.group_id,
            "team/docs": await team.create(factory, "docs"),
            "team/blog": await team.create(factory, "blog"),
            "ander/docs": await other.create(factory, "docs"),
        }
        for name in ("team/docs", "team/blog", "ander/docs"):
            await _exempt_link(factory, ids[name])
        return ids

    async def test_a_group_rename_requires_it_on_the_links_of_all_its_sites(self, factory, sites):
        await GROUPS.rename(factory, sites["team"], "ploeg")

        assert await _site_id_required(factory) == {"ploeg/docs": True, "ploeg/blog": True, "ander/docs": False}

    async def test_a_site_rename_requires_it_on_its_own_link_only(self, factory, sites):
        await _sites_of(sites["team"]).rename(factory, sites["team/docs"], "handleiding")

        assert await _site_id_required(factory) == {
            "team/handleiding": True,
            "team/blog": False,
            "ander/docs": False,
        }

    async def test_setting_the_slug_it_already_has_is_no_rename(self, factory, sites):
        async with factory() as db:
            await db.execute(update(Group).values(slug=Group.slug))
            await db.execute(update(Site).values(slug=Site.slug))
            await db.commit()

        assert await _site_id_required(factory) == {"team/docs": False, "team/blog": False, "ander/docs": False}
        async with factory() as db:
            assert list(await db.scalars(select(GroupSlug.slug).where(GroupSlug.retired_at.is_not(None)))) == []
            assert list(await db.scalars(select(SiteSlug.slug).where(SiteSlug.retired_at.is_not(None)))) == []

    async def test_a_new_name_or_title_requires_nothing(self, factory, sites):
        async with factory() as db:
            await db.execute(update(Group).values(name="Anders"))
            await db.execute(update(Site).values(title="Anders"))
            await db.commit()

        assert await _site_id_required(factory) == {"team/docs": False, "team/blog": False, "ander/docs": False}


class TestTwoClaimsAtOnce:
    """No lock of the app's own: the unique checks wait for the transaction
    that holds the row and decide on what it committed."""

    async def test_a_claim_of_the_old_slug_waits_for_the_rename_and_meets_the_reservation(self, factory, namespace):
        owner = await namespace.create(factory, "oud")
        async with factory() as renaming:
            (await renaming.get(namespace.model, owner)).slug = "nieuw"
            await renaming.flush()
            claim = asyncio.create_task(namespace.create(factory, "oud"))
            await _wait_for_a_lock_wait(factory)
            await renaming.commit()

        assert await _refusal(claim) == namespace.previous
        assert await namespace.slugs(factory) == {"oud": (owner, True), "nieuw": (owner, False)}

    async def test_a_rename_into_a_slug_being_claimed_waits_and_meets_the_claim(self, factory, namespace):
        owner = await namespace.create(factory, "oud")
        async with factory() as claiming:
            claimant = namespace.new("nieuw")
            claiming.add(claimant)
            await claiming.flush()
            rename = asyncio.create_task(namespace.rename(factory, owner, "nieuw"))
            await _wait_for_a_lock_wait(factory)
            await claiming.commit()

        assert await _refusal(rename) == namespace.current
        assert await namespace.slugs(factory) == {"oud": (owner, False), "nieuw": (claimant.id, False)}

    async def test_a_claim_waiting_on_a_rename_that_rolls_back_meets_the_slug_still_in_use(self, factory, namespace):
        owner = await namespace.create(factory, "oud")
        async with factory() as renaming:
            (await renaming.get(namespace.model, owner)).slug = "nieuw"
            await renaming.flush()
            claim = asyncio.create_task(namespace.create(factory, "oud"))
            await _wait_for_a_lock_wait(factory)
            await renaming.rollback()

        assert await _refusal(claim) == namespace.current
        assert await namespace.slugs(factory) == {"oud": (owner, False)}


class TestViolatedConstraint:
    """How api/admin.py tells one collision from another: by the constraint's
    name on the driver's error, which asyncpg reports apart from the message."""

    async def test_a_unique_violation_names_its_constraint(self, factory):
        await GROUPS.create(factory, "aurora")

        assert await _refusal(GROUPS.create(factory, "aurora")) == "uq_groups_slug"

    async def test_an_error_without_a_constraint_names_none(self, factory):
        async with factory() as db:
            with pytest.raises(DBAPIError) as error:
                await db.execute(text("SELECT 1 / 0"))

        assert violated_constraint(error.value) is None
