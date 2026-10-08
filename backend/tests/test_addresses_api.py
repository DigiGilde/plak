"""Changing the address of a group or a site: `PUT /groups/{group}/slug` and
`PUT /sites/{group}/{site}/slug`, the collisions they share with the create
routes, the creation budget and the limit of old addresses.

Reuses the admin API fixtures.
"""

# Fixtures imported from test_admin_api are found by name, which ruff reads as
# a parameter shadowing the import.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from helpers_oidc import APP_BASE_URL, make_test_client
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from test_admin_api import (  # noqa: F401 - fixtures are found by name
    BASE,
    _join_group,
    _join_site,
    _refusal_rows,
    app,
    client,
    content_root,
    data,
    factory,
    login,
)

from plak.api import admin
from plak.api.admin import CREATION_MAX_PER_WINDOW
from plak.audit.pseudonymisation import pseudonymise
from plak.constants import MAX_PREVIOUS_SLUGS, SLUG_REDIRECT_DAYS, AccessBase, Role
from plak.models.audit import AuditLogEntry
from plak.models.identity import Group, GroupMember
from plak.models.publication import Site
from plak.models.slugs import GroupSlug, SiteSlug
from plak.slug_window import redirect_ends_at

GROUP_ROUTE = "/-/api/v1/groups/{group_slug}/slug"
SITE_ROUTE = "/-/api/v1/sites/{group_slug}/{site_slug}/slug"


@dataclass(frozen=True)
class Subject:
    """A group or a site of the `data` fixture, so the rules both routes share
    are written once."""

    kind: str
    model: type[Group] | type[Site]
    row_id: uuid.UUID
    group_id: uuid.UUID
    original: str
    action: str
    route: str

    def url(self, current: str) -> str:
        if self.kind == "group":
            return f"{BASE}/groups/{current}/slug"
        return f"{BASE}/sites/team/{current}/slug"

    async def current(self, factory) -> str:
        async with factory() as db:
            return (await db.get(self.model, self.row_id)).slug

    async def another(self, factory, slug: str) -> uuid.UUID:
        """Another group, or another site in the same group, with this slug."""
        async with factory() as db:
            if self.kind == "group":
                row = Group(slug=slug, name=slug, default_access_base=AccessBase.SITE_TEAM)
            else:
                row = Site(group_id=self.group_id, slug=slug, title=slug, access_base=AccessBase.SITE_TEAM)
            db.add(row)
            await db.commit()
            return row.id

    async def rename(self, factory, row_id: uuid.UUID, slug: str) -> None:
        """A rename straight in the database; setup, never the thing tested."""
        async with factory() as db:
            (await db.get(self.model, row_id)).slug = slug
            await db.commit()

    async def age(self, factory, slug: str, days: int) -> None:
        """Moves the retirement of one of its old slugs this many days back."""
        namespace = GroupSlug if self.kind == "group" else SiteSlug
        owner = GroupSlug.group_id if self.kind == "group" else SiteSlug.site_id
        async with factory() as db:
            await db.execute(
                update(namespace)
                .where(owner == self.row_id, namespace.slug == slug)
                .values(retired_at=namespace.retired_at - timedelta(days=days))
            )
            await db.commit()


@pytest.fixture(params=["group", "site"])
def subject(request, data) -> Subject:
    if request.param == "group":
        return Subject("group", Group, data.group.id, data.group.id, "team", "group_slug", GROUP_ROUTE)
    return Subject("site", Site, data.site.id, data.group.id, "site", "site_slug", SITE_ROUTE)


def _as_group_admin(client, app) -> dict[str, str]:
    return login(client, app, sub="lid-a", email="a@example.nl")


async def _change(client, factory, subject: Subject, headers: dict[str, str], slug: str, **kwargs) -> httpx.Response:
    """The change, sent to wherever the group or site is now."""
    return await client.put(
        subject.url(await subject.current(factory)), json={"slug": slug}, headers={**headers, **kwargs}
    )


async def _audit_rows(factory, action: str) -> list[AuditLogEntry]:
    async with factory() as db:
        return list(await db.scalars(select(AuditLogEntry).where(AuditLogEntry.action == action)))


async def _assert_refusal_logged(factory, *, code: str, route: str) -> None:
    (row,) = await _refusal_rows(factory)
    assert row.result == "refused"
    assert row.reason_code == code
    assert row.refs["method"] == "PUT"
    assert row.refs["route"] == route


class TestTheRulesBothShare:
    async def test_the_new_address_is_answered_and_stored(self, client, app, data, factory, subject):
        headers = _as_group_admin(client, app)

        response = await _change(client, factory, subject, headers, "  nieuw  ")

        assert response.status_code == 200
        assert response.json()["slug"] == "nieuw"
        assert await subject.current(factory) == "nieuw"
        (row,) = await _audit_rows(factory, subject.action)
        assert row.result == "allowed"
        assert row.actor_pseudonym == pseudonymise(app.state.settings.audit_pepper, "lid-a")

    async def test_the_address_it_already_has_changes_nothing_and_costs_nothing(
        self, client, app, data, factory, subject
    ):
        headers = _as_group_admin(client, app)

        for sent in [subject.original, f" {subject.original} "] * (CREATION_MAX_PER_WINDOW // 2 + 1):
            response = await _change(client, factory, subject, headers, sent)
            assert response.status_code == 200
            assert response.json()["slug"] == subject.original

        assert await _audit_rows(factory, subject.action) == []
        assert (await _change(client, factory, subject, headers, "nieuw")).status_code == 200

    @pytest.mark.parametrize("bad", ["Hoofdletters", "met spatie", "-voor", "na-", "a" * 64, ""])
    async def test_a_slug_that_is_no_slug_is_refused(self, client, app, data, factory, subject, bad):
        headers = _as_group_admin(client, app)

        response = await _change(client, factory, subject, headers, bad)

        assert response.status_code == 422
        assert response.json()["code"] == "SLUG_INVALID"
        assert await subject.current(factory) == subject.original

    async def test_the_current_address_of_another_is_taken(self, client, app, data, factory, subject):
        await subject.another(factory, "bezet")
        headers = _as_group_admin(client, app)

        response = await _change(client, factory, subject, headers, "bezet")

        assert response.status_code == 409
        assert response.json()["code"] == "SLUG_EXISTS"
        assert response.json()["detail"] == (
            "A group with slug 'bezet' already exists."
            if subject.kind == "group"
            else "A site with slug 'bezet' already exists in this group."
        )
        assert await subject.current(factory) == subject.original

    async def test_a_recent_address_of_another_is_taken_too(self, client, app, data, factory, subject):
        other = await subject.another(factory, "oud")
        await subject.rename(factory, other, "verhuisd")
        headers = _as_group_admin(client, app)

        english = await _change(client, factory, subject, headers, "oud")
        dutch = await _change(client, factory, subject, headers, "oud", **{"Accept-Language": "nl"})

        assert english.status_code == dutch.status_code == 409
        assert english.json()["code"] == "SLUG_EXISTS"
        what = "group" if subject.kind == "group" else "site in this group"
        assert english.json()["detail"] == (
            f"The slug 'oud' was recently the address of another {what}. Choose another slug."
        )
        wat = "groep" if subject.kind == "group" else "site in deze groep"
        assert dutch.json()["detail"] == (
            f"De slug 'oud' was kort geleden het adres van een andere {wat}. Kies een andere slug."
        )
        assert await subject.current(factory) == subject.original

    async def test_its_own_recent_address_can_be_taken_back(self, client, app, data, factory, subject):
        headers = _as_group_admin(client, app)
        await _change(client, factory, subject, headers, "tijdelijk")

        response = await _change(client, factory, subject, headers, subject.original)

        assert response.status_code == 200
        assert await subject.current(factory) == subject.original
        rows = sorted(await _audit_rows(factory, subject.action), key=lambda row: row.occurred_at)
        previous = "previous_group" if subject.kind == "group" else "previous_site"
        assert [row.refs[previous] for row in rows] == [subject.original, "tijdelijk"]

    async def test_the_twenty_first_change_in_an_hour_is_refused_and_a_collision_counts(
        self, client, app, data, factory, subject
    ):
        await subject.another(factory, "bezet")
        headers = _as_group_admin(client, app)
        back_and_forth = ["heen", subject.original]
        for number in range(CREATION_MAX_PER_WINDOW - 1):
            response = await _change(client, factory, subject, headers, back_and_forth[number % 2])
            assert response.status_code == 200, response.text
        assert (await _change(client, factory, subject, headers, "bezet")).status_code == 409

        refused = await _change(client, factory, subject, headers, "nieuw")

        assert refused.status_code == 429
        assert refused.json()["code"] == "TOO_MANY_CREATIONS"
        assert refused.json()["detail"] == (
            f"At most {CREATION_MAX_PER_WINDOW} new groups, sites and addresses per hour; try again later."
        )
        assert "Retry-After" in refused.headers
        assert await subject.current(factory) == "heen"
        await _assert_refusal_logged(factory, code="TOO_MANY_CREATIONS", route=subject.route)

    async def test_creating_and_changing_share_one_budget(self, client, app, data, factory, subject, monkeypatch):
        monkeypatch.setattr(admin, "CREATION_MAX_PER_WINDOW", 1)
        headers = _as_group_admin(client, app)
        created = await client.post(f"{BASE}/groups", json={"name": "Nieuw", "slug": "nieuwe-groep"}, headers=headers)
        assert created.status_code == 201

        refused = await _change(client, factory, subject, headers, "nieuw")

        assert refused.status_code == 429

    async def test_five_old_addresses_redirect_and_a_sixth_waits_but_going_back_does_not(
        self, client, app, data, factory, subject
    ):
        headers = _as_group_admin(client, app)
        for slug in ("b", "c", "d", "e", "f"):
            assert (await _change(client, factory, subject, headers, slug)).status_code == 200

        refused = await _change(client, factory, subject, headers, "g")
        dutch = await _change(client, factory, subject, headers, "g", **{"Accept-Language": "nl"})

        assert refused.status_code == 409
        assert refused.json()["code"] == "TOO_MANY_PREVIOUS_SLUGS"
        assert refused.json()["detail"] == (
            f"This already has {MAX_PREVIOUS_SLUGS} previous addresses that still redirect. Change back to one of "
            "them, or wait until one has expired."
        )
        assert dutch.json()["detail"] == (
            f"Er zijn al {MAX_PREVIOUS_SLUGS} oude adressen die nog doorsturen. Zet een ervan terug, of wacht tot er "
            "een is verlopen."
        )
        assert await subject.current(factory) == "f"
        back = await _change(client, factory, subject, headers, "c")
        assert back.status_code == 200
        assert await subject.current(factory) == "c"

    async def test_an_old_address_whose_redirect_ended_no_longer_counts(self, client, app, data, factory, subject):
        headers = _as_group_admin(client, app)
        for slug in ("b", "c", "d", "e", "f"):
            await _change(client, factory, subject, headers, slug)
        await subject.age(factory, subject.original, days=32)

        response = await _change(client, factory, subject, headers, "g")

        assert response.status_code == 200

    async def test_the_role_is_checked_before_the_slug(self, client, app, data, factory, subject):
        await _join_group(factory, data.group, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await _change(client, factory, subject, headers, "Geen Slug")

        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"

    async def test_without_the_csrf_header_it_is_refused(self, client, app, data, factory, subject):
        _as_group_admin(client, app)

        response = await client.put(subject.url(subject.original), json={"slug": "nieuw"})

        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_INVALID"
        assert await subject.current(factory) == subject.original
        await _assert_refusal_logged(factory, code="CSRF_INVALID", route=subject.route)

    async def test_a_platform_administrator_without_a_role_is_refused(self, client, app, data, factory, subject):
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")

        response = await _change(client, factory, subject, headers, "nieuw")

        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"
        assert await subject.current(factory) == subject.original
        await _assert_refusal_logged(factory, code="INSUFFICIENT_ROLE", route=subject.route)

    @pytest.mark.parametrize("role", [Role.EDITOR, Role.READER])
    async def test_a_group_member_below_admin_is_refused(self, client, app, data, factory, subject, role):
        await _join_group(factory, data.group, data.member_b, role)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await _change(client, factory, subject, headers, "nieuw")

        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"
        assert await subject.current(factory) == subject.original
        await _assert_refusal_logged(factory, code="INSUFFICIENT_ROLE", route=subject.route)

    async def test_two_changes_to_the_same_address_at_once_change_it_once(
        self, client, app, data, factory, subject, monkeypatch
    ):
        """Both requests have read the row before either takes the lock; the
        second then finds the address already changed, and changes nothing."""
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        lookup = "_group_with_role" if subject.kind == "group" else "_site_with_role"
        barrier = asyncio.Barrier(2)
        real = getattr(admin, lookup)

        async def meet(*args, **kwargs):
            found = await real(*args, **kwargs)
            await barrier.wait()
            return found

        monkeypatch.setattr(admin, lookup, meet)
        first = _as_group_admin(client, app)
        url = subject.url(subject.original)
        async with make_test_client(app) as other:
            second = login(other, app, sub="lid-b", email="b@example.nl")
            responses = await asyncio.wait_for(
                asyncio.gather(
                    client.put(url, json={"slug": "nieuw"}, headers=first),
                    other.put(url, json={"slug": "nieuw"}, headers=second),
                ),
                timeout=30,
            )

        assert [response.status_code for response in responses] == [200, 200]
        assert [response.json()["slug"] for response in responses] == ["nieuw", "nieuw"]
        assert len(await _audit_rows(factory, subject.action)) == 1

    async def test_two_changes_at_once_each_keep_what_they_replaced(
        self, client, app, data, factory, subject, monkeypatch
    ):
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        lookup = "_group_with_role" if subject.kind == "group" else "_site_with_role"
        barrier = asyncio.Barrier(2)
        real = getattr(admin, lookup)

        async def meet(*args, **kwargs):
            found = await real(*args, **kwargs)
            await barrier.wait()
            return found

        monkeypatch.setattr(admin, lookup, meet)
        first = _as_group_admin(client, app)
        url = subject.url(subject.original)
        async with make_test_client(app) as other:
            second = login(other, app, sub="lid-b", email="b@example.nl")
            responses = await asyncio.wait_for(
                asyncio.gather(
                    client.put(url, json={"slug": "een"}, headers=first),
                    other.put(url, json={"slug": "twee"}, headers=second),
                ),
                timeout=30,
            )

        assert [response.status_code for response in responses] == [200, 200]
        written_first = ({"een", "twee"} - {await subject.current(factory)}).pop()
        previous = "previous_group" if subject.kind == "group" else "previous_site"
        rows = await _audit_rows(factory, subject.action)
        assert sorted(row.refs[previous] for row in rows) == sorted([subject.original, written_first])


class TestAGroup:
    async def test_the_audit_row_names_both_addresses_the_id_and_the_sites_that_moved(
        self, client, app, data, factory
    ):
        async with factory() as db:
            db.add(Site(group_id=data.group.id, slug="blog", title="Blog", access_base=AccessBase.SITE_TEAM))
            await db.commit()
        headers = _as_group_admin(client, app)

        response = await client.put(f"{BASE}/groups/team/slug", json={"slug": "ploeg"}, headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Team"
        assert body["defaultAccess"]["base"] == "site_team"
        (row,) = await _audit_rows(factory, "group_slug")
        assert {key: value for key, value in row.refs.items() if key != "ip_unvouched"} == {
            "group": "ploeg",
            "previous_group": "team",
            "group_id": str(data.group.id),
            "sites": ["blog", "site"],
        }

    async def test_a_reserved_slug_is_refused(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        response = await client.put(f"{BASE}/groups/team/slug", json={"slug": "cli-link"}, headers=headers)

        assert response.status_code == 422
        assert response.json()["code"] == "SLUG_INVALID"

    async def test_a_member_without_a_role_in_the_group_is_refused(self, client, app, data, factory):
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(f"{BASE}/groups/team/slug", json={"slug": "nieuw"}, headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"
        await _assert_refusal_logged(factory, code="INSUFFICIENT_ROLE", route=GROUP_ROUTE)

    async def test_an_unknown_group_is_404(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        response = await client.put(f"{BASE}/groups/bestaat-niet/slug", json={"slug": "nieuw"}, headers=headers)

        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_GROUP"
        await _assert_refusal_logged(factory, code="UNKNOWN_GROUP", route=GROUP_ROUTE)

    async def test_the_old_path_answers_404_after_the_change(self, client, app, data, factory):
        """The API follows no old address: a request that lost its answer
        reads the group at the new one."""
        headers = _as_group_admin(client, app)
        await client.put(f"{BASE}/groups/team/slug", json={"slug": "ploeg"}, headers=headers)

        again = await client.put(f"{BASE}/groups/team/slug", json={"slug": "ploeg"}, headers=headers)
        at_the_new = await client.put(f"{BASE}/groups/ploeg/slug", json={"slug": "ploeg"}, headers=headers)

        assert again.status_code == 404
        assert again.json()["code"] == "UNKNOWN_GROUP"
        assert at_the_new.status_code == 200
        assert len(await _audit_rows(factory, "group_slug")) == 1


class TestASite:
    async def test_the_audit_row_names_both_addresses_and_the_id(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        response = await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["groupSlug"] == "team"
        assert body["id"] == str(data.site.id)
        assert body["title"] == "Site"
        (row,) = await _audit_rows(factory, "site_slug")
        assert {key: value for key, value in row.refs.items() if key != "ip_unvouched"} == {
            "group": "team",
            "site": "docs",
            "previous_site": "site",
            "site_id": str(data.site.id),
        }

    async def test_an_admin_of_the_site_who_reads_in_the_group_changes_it(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.READER)
        await _join_site(factory, data.site, data.member_b, Role.ADMIN)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)

        assert response.status_code == 200
        async with factory() as db:
            assert (await db.get(Site, data.site.id)).slug == "docs"

    async def test_an_admin_of_the_site_alone_is_refused(self, client, app, data, factory):
        await _join_site(factory, data.site, data.member_b, Role.ADMIN)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        english = await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)
        dutch = await client.put(
            f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers={**headers, "Accept-Language": "nl"}
        )

        assert english.status_code == 403
        assert english.json()["code"] == "INSUFFICIENT_ROLE"
        assert english.json()["detail"] == "Changing a site's address needs a role in its group."
        assert dutch.json()["detail"] == (
            "Het adres van een site wijzigen kan alleen met een rol in de groep van de site."
        )
        async with factory() as db:
            assert (await db.get(Site, data.site.id)).slug == "site"
        rows = await _refusal_rows(factory)
        assert [(row.reason_code, row.refs["route"]) for row in rows] == [("INSUFFICIENT_ROLE", SITE_ROUTE)] * 2

    async def test_an_admin_of_the_site_alone_is_refused_before_the_slug_is_read(self, client, app, data, factory):
        await _join_site(factory, data.site, data.member_b, Role.ADMIN)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "Geen Slug"}, headers=headers)

        assert response.status_code == 403

    async def test_an_editor_of_the_site_is_refused(self, client, app, data, factory):
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"
        assert response.json()["detail"] == "This needs at least the role admin."
        await _assert_refusal_logged(factory, code="INSUFFICIENT_ROLE", route=SITE_ROUTE)

    async def test_a_member_without_a_role_gets_the_404_of_an_unknown_site(self, client, app, data, factory):
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)

        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_SITE"
        await _assert_refusal_logged(factory, code="UNKNOWN_SITE", route=SITE_ROUTE)

    async def test_the_same_slug_in_another_group_is_no_collision(self, client, app, data, factory):
        async with factory() as db:
            other = Group(slug="ander", name="Ander", default_access_base=AccessBase.SITE_TEAM)
            db.add(other)
            await db.flush()
            db.add(Site(group_id=other.id, slug="docs", title="Docs", access_base=AccessBase.SITE_TEAM))
            await db.commit()
        headers = _as_group_admin(client, app)

        response = await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)

        assert response.status_code == 200


async def _until_a_lock_is_awaited(factory) -> None:
    """Returns once some transaction waits for a lock: the other side of the
    test has run into what the change holds."""
    async with asyncio.timeout(10):
        while True:
            async with factory() as db:
                if await db.scalar(text("SELECT count(*) FROM pg_locks WHERE NOT granted")):
                    return
            await asyncio.sleep(0.02)


def _held(locked: asyncio.Event, release: asyncio.Event, real=None):
    """A stand-in for the creation budget, which a change asks for after its
    locks: the first change to get there holds them until `release`. With
    `real` the others count against the budget as usual."""

    async def hold(*args, **kwargs):
        if not locked.is_set():
            locked.set()
            await release.wait()
        elif real is not None:
            await real(*args, **kwargs)

    return hold


class TestChangesAtTheSameMoment:
    """What a change locks, and in which order: changes and deletes that meet
    neither deadlock nor leave an address in the audit row that was already
    gone."""

    async def test_a_row_deleted_after_the_role_check_is_unknown(
        self, client, app, data, factory, subject, monkeypatch
    ):
        """Another admin deletes the group or site between the role check and
        the lock: the 404 of one that was never there, not a server error."""
        lookup = "_group_with_role" if subject.kind == "group" else "_site_with_role"
        real = getattr(admin, lookup)

        async def delete_meanwhile(*args, **kwargs):
            found = await real(*args, **kwargs)
            async with factory() as db:
                await db.execute(delete(subject.model).where(subject.model.id == subject.row_id))
                await db.commit()
            return found

        monkeypatch.setattr(admin, lookup, delete_meanwhile)
        headers = _as_group_admin(client, app)

        response = await client.put(subject.url(subject.original), json={"slug": "nieuw"}, headers=headers)

        assert response.status_code == 404
        assert response.json()["code"] == ("UNKNOWN_GROUP" if subject.kind == "group" else "UNKNOWN_SITE")
        assert await _audit_rows(factory, subject.action) == []

    async def test_a_site_whose_group_is_deleted_before_the_lock_is_unknown(
        self, client, app, data, factory, monkeypatch
    ):
        real = admin._reread_locked

        async def delete_the_group_first(db, row, **kwargs):
            if isinstance(row, Group):
                async with factory() as other:
                    await other.execute(delete(Group).where(Group.id == data.group.id))
                    await other.commit()
            await real(db, row, **kwargs)

        monkeypatch.setattr(admin, "_reread_locked", delete_the_group_first)
        headers = _as_group_admin(client, app)

        response = await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)

        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_GROUP"
        assert await _audit_rows(factory, "site_slug") == []

    async def test_changing_a_site_and_deleting_its_group_wait_for_each_other(
        self, client, app, data, factory, monkeypatch
    ):
        """Deleting a group locks the group and then its sites. A change that
        locked the site and then needed the group would deadlock with that;
        the change locks the group first, so the delete waits for it. Held
        after the first lock, which is where the order shows."""
        locked, release = asyncio.Event(), asyncio.Event()
        real = admin._reread_locked

        async def lock_and_hold_the_first(*args, **kwargs):
            await real(*args, **kwargs)
            if not locked.is_set():
                locked.set()
                await release.wait()

        monkeypatch.setattr(admin, "_reread_locked", lock_and_hold_the_first)
        headers = _as_group_admin(client, app)

        async def delete_the_group():
            async with factory() as db:
                await db.execute(delete(Group).where(Group.id == data.group.id))
                await db.commit()

        change = asyncio.create_task(
            client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)
        )
        deleting = None
        try:
            async with asyncio.timeout(10):
                await locked.wait()
            deleting = asyncio.create_task(delete_the_group())
            await _until_a_lock_is_awaited(factory)
        finally:
            release.set()
            response, deleted = await asyncio.wait_for(
                asyncio.gather(change, deleting or asyncio.sleep(0), return_exceptions=True), timeout=30
            )

        assert response.status_code == 200
        assert deleted is None
        async with factory() as db:
            assert await db.get(Group, data.group.id) is None

    async def test_a_site_change_names_the_group_as_it_is_once_locked(
        self, client, app, data, factory, monkeypatch
    ):
        """The group gets another address between the role check and the
        lock: the audit row and the answer name the one it has now."""
        real = admin._site_with_role

        async def change_the_group_meanwhile(*args, **kwargs):
            found = await real(*args, **kwargs)
            async with factory() as db:
                (await db.get(Group, data.group.id)).slug = "ploeg"
                await db.commit()
            return found

        monkeypatch.setattr(admin, "_site_with_role", change_the_group_meanwhile)
        headers = _as_group_admin(client, app)

        response = await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)

        assert response.status_code == 200
        assert response.json()["groupSlug"] == "ploeg"
        (row,) = await _audit_rows(factory, "site_slug")
        assert row.refs["group"] == "ploeg"

    async def test_a_site_change_holds_its_group_against_other_changes_but_not_new_sites(
        self, client, app, data, factory, monkeypatch
    ):
        """Address changes in one group run one after the other, so two sites
        trading addresses cannot deadlock. A site added to the group does not
        wait."""
        locked, release = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(admin, "_require_creation_budget", _held(locked, release))
        headers = _as_group_admin(client, app)
        change = asyncio.create_task(
            client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)
        )
        try:
            async with asyncio.timeout(10):
                await locked.wait()
            # The lock another change of the group takes, refused at once.
            async with factory() as db:
                with pytest.raises(DBAPIError) as probe:
                    await db.execute(
                        select(Group.id)
                        .where(Group.id == data.group.id)
                        .with_for_update(key_share=True, nowait=True)
                    )
            assert getattr(probe.value.orig, "sqlstate", None) == "55P03"
            async with asyncio.timeout(5), factory() as db:
                db.add(Site(group_id=data.group.id, slug="blog", title="Blog", access_base=AccessBase.SITE_TEAM))
                await db.commit()
        finally:
            release.set()
            (response,) = await asyncio.gather(change, return_exceptions=True)

        assert response.status_code == 200

    async def test_a_row_that_points_at_the_one_changing_waits(
        self, client, app, data, factory, subject, monkeypatch
    ):
        """The change takes FOR UPDATE, as changing the slug would anyway: a
        site added to the group, or a version written for the site, waits
        instead of taking a share that the change would then wait for."""
        locked, release = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(admin, "_require_creation_budget", _held(locked, release))
        headers = _as_group_admin(client, app)
        change = asyncio.create_task(
            client.put(subject.url(subject.original), json={"slug": "nieuw"}, headers=headers)
        )
        try:
            async with asyncio.timeout(10):
                await locked.wait()
            # The lock a foreign key check takes on the row, refused at once.
            async with factory() as db:
                with pytest.raises(DBAPIError) as probe:
                    await db.execute(
                        select(subject.model.id)
                        .where(subject.model.id == subject.row_id)
                        .with_for_update(read=True, key_share=True, nowait=True)
                    )
            assert getattr(probe.value.orig, "sqlstate", None) == "55P03"
        finally:
            release.set()
            (response,) = await asyncio.gather(change, return_exceptions=True)

        assert response.status_code == 200

    async def test_address_changes_of_two_groups_run_one_after_the_other(
        self, client, app, data, factory, monkeypatch
    ):
        """Two groups have no row in common to lock, and trading addresses
        would leave each waiting for the other's."""
        async with factory() as db:
            ander = Group(slug="ander", name="Ander", default_access_base=AccessBase.SITE_TEAM)
            db.add(ander)
            await db.flush()
            db.add(GroupMember(group_id=ander.id, member_id=data.member_b.id, role=Role.ADMIN))
            await db.commit()
        locked, release = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(
            admin, "_require_creation_budget", _held(locked, release, real=admin._require_creation_budget)
        )
        first = _as_group_admin(client, app)
        async with make_test_client(app) as other:
            second = login(other, app, sub="lid-b", email="b@example.nl")
            change = asyncio.create_task(client.put(f"{BASE}/groups/team/slug", json={"slug": "ploeg"}, headers=first))
            waiting = None
            try:
                async with asyncio.timeout(10):
                    await locked.wait()
                waiting = asyncio.create_task(
                    other.put(f"{BASE}/groups/ander/slug", json={"slug": "anders"}, headers=second)
                )
                await _until_a_lock_is_awaited(factory)
                assert not waiting.done()
            finally:
                release.set()
                responses = await asyncio.wait_for(
                    asyncio.gather(change, waiting or asyncio.sleep(0), return_exceptions=True), timeout=30
                )

        assert [response.status_code for response in responses] == [200, 200]

    async def test_two_trading_addresses_at_once_are_both_refused(
        self, client, app, data, factory, subject, monkeypatch
    ):
        """Each asks for the address the other has. Whichever goes first finds
        it taken, and then so does the other: two 409s, no server error."""
        other_id = await subject.another(factory, "ander")
        if subject.kind == "group":
            async with factory() as db:
                db.add(GroupMember(group_id=other_id, member_id=data.member_b.id, role=Role.ADMIN))
                await db.commit()
        else:
            await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        lookup = "_group_with_role" if subject.kind == "group" else "_site_with_role"
        barrier = asyncio.Barrier(2)
        real = getattr(admin, lookup)

        async def meet(*args, **kwargs):
            found = await real(*args, **kwargs)
            await barrier.wait()
            return found

        monkeypatch.setattr(admin, lookup, meet)
        first = _as_group_admin(client, app)
        async with make_test_client(app) as other:
            second = login(other, app, sub="lid-b", email="b@example.nl")
            responses = await asyncio.wait_for(
                asyncio.gather(
                    client.put(subject.url(subject.original), json={"slug": "ander"}, headers=first),
                    other.put(subject.url("ander"), json={"slug": subject.original}, headers=second),
                ),
                timeout=30,
            )

        assert [response.status_code for response in responses] == [409, 409]
        assert [response.json()["code"] for response in responses] == ["SLUG_EXISTS", "SLUG_EXISTS"]
        assert await subject.current(factory) == subject.original


async def _redirects_until(factory, namespace, slug: str) -> str:
    """The `redirectsUntil` the API should give for this old slug."""
    async with factory() as db:
        retired_at = await db.scalar(select(namespace.retired_at).where(namespace.slug == slug))
    return redirect_ends_at(retired_at).isoformat().replace("+00:00", "Z")


class TestPreviousSlugs:
    async def test_without_a_change_the_lists_are_empty(self, client, app, data, factory):
        _as_group_admin(client, app)

        overview = (await client.get(f"{BASE}/overview")).json()
        detail = (await client.get(f"{BASE}/groups/team")).json()

        assert overview["groups"][0]["group"]["previousSlugs"] == []
        assert overview["groups"][0]["sites"][0]["previousSlugs"] == []
        assert detail["group"]["previousSlugs"] == []
        assert detail["sites"][0]["previousSlugs"] == []

    async def test_old_slugs_are_listed_newest_first_with_the_end_of_their_redirect(
        self, client, app, data, factory
    ):
        headers = _as_group_admin(client, app)
        await client.put(f"{BASE}/groups/team/slug", json={"slug": "ploeg"}, headers=headers)
        await client.put(f"{BASE}/groups/ploeg/slug", json={"slug": "team-aurora"}, headers=headers)
        await client.put(f"{BASE}/sites/team-aurora/site/slug", json={"slug": "docs"}, headers=headers)

        detail = (await client.get(f"{BASE}/groups/team-aurora")).json()

        assert detail["group"]["previousSlugs"] == [
            {"slug": "ploeg", "redirectsUntil": await _redirects_until(factory, GroupSlug, "ploeg")},
            {"slug": "team", "redirectsUntil": await _redirects_until(factory, GroupSlug, "team")},
        ]
        assert detail["sites"][0]["slug"] == "docs"
        assert detail["sites"][0]["groupSlug"] == "team-aurora"
        assert detail["sites"][0]["previousSlugs"] == [
            {"slug": "site", "redirectsUntil": await _redirects_until(factory, SiteSlug, "site")}
        ]

    async def test_the_answer_to_a_change_lists_the_slug_it_replaced(self, client, app, data, factory, subject):
        headers = _as_group_admin(client, app)

        response = await _change(client, factory, subject, headers, "nieuw")

        namespace = GroupSlug if subject.kind == "group" else SiteSlug
        assert response.json()["previousSlugs"] == [
            {"slug": subject.original, "redirectsUntil": await _redirects_until(factory, namespace, subject.original)}
        ]

    async def test_one_whose_redirect_ended_is_gone_before_the_cleanup_removed_it(
        self, client, app, data, factory, subject
    ):
        headers = _as_group_admin(client, app)
        await _change(client, factory, subject, headers, "nieuw")
        await subject.age(factory, subject.original, days=32)

        overview = (await client.get(f"{BASE}/overview")).json()

        row = overview["groups"][0]
        listed = row["group"] if subject.kind == "group" else row["sites"][0]
        assert listed["previousSlugs"] == []
        namespace = GroupSlug if subject.kind == "group" else SiteSlug
        async with factory() as db:
            assert await db.scalar(select(namespace.slug).where(namespace.slug == subject.original))

    async def test_the_overview_lists_every_group_and_site_with_its_own(self, client, app, data, factory):
        headers = _as_group_admin(client, app)
        created = await client.post(f"{BASE}/groups", json={"name": "Twee", "slug": "twee"}, headers=headers)
        assert created.json()["previousSlugs"] == []
        await client.post(f"{BASE}/groups/twee/sites", json={"title": "Blog", "slug": "blog"}, headers=headers)
        await client.put(f"{BASE}/groups/twee/slug", json={"slug": "tweede"}, headers=headers)
        await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)

        overview = (await client.get(f"{BASE}/overview")).json()

        listed = {
            row["group"]["slug"]: (
                [old["slug"] for old in row["group"]["previousSlugs"]],
                {site["slug"]: [old["slug"] for old in site["previousSlugs"]] for site in row["sites"]},
            )
            for row in overview["groups"]
        }
        assert listed == {"team": ([], {"docs": ["site"]}), "tweede": (["twee"], {"blog": []})}

    async def test_other_answers_about_a_group_carry_them_too(self, client, app, data, factory):
        headers = _as_group_admin(client, app)
        await client.put(f"{BASE}/groups/team/slug", json={"slug": "ploeg"}, headers=headers)

        named = await client.put(f"{BASE}/groups/ploeg/name", json={"name": "Ploeg"}, headers=headers)
        access = await client.put(
            f"{BASE}/groups/ploeg/default-access",
            json={"base": "sso", "keys": False, "invitees": False},
            headers=headers,
        )
        unchanged = await client.put(f"{BASE}/groups/ploeg/slug", json={"slug": "ploeg"}, headers=headers)

        for response in (named, access, unchanged):
            assert [old["slug"] for old in response.json()["previousSlugs"]] == ["team"]


class TestMe:
    async def test_it_says_how_many_days_an_old_address_redirects(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.get(f"{BASE}/me")

        assert response.json()["slugRedirectDays"] == SLUG_REDIRECT_DAYS == 30


class TestCreatingOnARecentAddress:
    async def test_a_new_group_cannot_take_the_recent_address_of_another(self, client, app, data, factory):
        headers = _as_group_admin(client, app)
        await client.put(f"{BASE}/groups/team/slug", json={"slug": "ploeg"}, headers=headers)

        response = await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=headers)

        assert response.status_code == 409
        assert response.json()["code"] == "SLUG_EXISTS"
        assert response.json()["detail"] == (
            "The slug 'team' was recently the address of another group. Choose another slug."
        )

    async def test_a_new_site_cannot_take_the_recent_address_of_another(self, client, app, data, factory):
        headers = _as_group_admin(client, app)
        await client.put(f"{BASE}/sites/team/site/slug", json={"slug": "docs"}, headers=headers)

        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "Site", "slug": "site"}, headers=headers
        )

        assert response.status_code == 409
        assert response.json()["code"] == "SLUG_EXISTS"
        assert response.json()["detail"] == (
            "The slug 'site' was recently the address of another site in this group. Choose another slug."
        )

    async def test_a_new_group_on_a_current_address_keeps_its_own_message(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        response = await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=headers)

        assert response.status_code == 409
        assert response.json()["detail"] == "A group with slug 'team' already exists."


class _OtherConstraintError(Exception):
    constraint_name = "ck_iets_anders"


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("PUT", f"{BASE}/groups/team/slug", {"slug": "nieuw"}),
        ("PUT", f"{BASE}/sites/team/site/slug", {"slug": "nieuw"}),
        ("POST", f"{BASE}/groups", {"name": "Nieuw", "slug": "nieuw"}),
        ("POST", f"{BASE}/groups/team/sites", {"title": "Nieuw", "slug": "nieuw"}),
    ],
    ids=["group-slug", "site-slug", "create-group", "create-site"],
)
async def test_another_integrity_error_is_a_fault_and_not_a_collision(
    app, data, factory, monkeypatch, method, url, body
):
    """A collision is told by its constraint; anything else the database
    refuses is a fault in the triggers, and answers 500 rather than a 409
    that would send the member looking for another slug."""

    async def refuse(self, *args, **kwargs):
        raise IntegrityError("UPDATE ...", None, _OtherConstraintError())

    monkeypatch.setattr(AsyncSession, "flush", refuse)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=APP_BASE_URL) as client:
        headers = _as_group_admin(client, app)
        response = await client.request(method, url, json=body, headers=headers)

    assert response.status_code == 500
    async with factory() as db:
        assert (await db.get(Group, data.group.id)).slug == "team"
        assert (await db.get(Site, data.site.id)).slug == "site"
        assert list(await db.scalars(select(Group.slug).order_by(Group.slug))) == ["team"]
