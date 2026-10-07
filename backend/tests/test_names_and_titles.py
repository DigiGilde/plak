"""Changing a group's name and a site's title: the text validation behind it
and the two endpoints, `PUT /groups/{group}/name` and
`PUT /sites/{group}/{site}/title`.

Reuses the admin API fixtures.
"""

# Fixtures imported from test_admin_api are found by name, which ruff reads as
# a parameter shadowing the import.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass

import pytest
from helpers_oidc import make_test_client
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError
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

from plak.api import admin, openapi_file
from plak.api.admin import _has_forbidden_characters, _validate_text
from plak.api.errors import ApiError
from plak.audit.pseudonymisation import pseudonymise
from plak.constants import AccessBase, Role
from plak.models.audit import AuditLogEntry
from plak.models.identity import Group
from plak.models.publication import Site, Version, VersionTarget

NAME = f"{BASE}/groups/team/name"
TITLE = f"{BASE}/sites/team/site/title"
NAME_ROUTE = "/-/api/v1/groups/{group_slug}/name"
TITLE_ROUTE = "/-/api/v1/sites/{group_slug}/{site_slug}/title"


class TestMaximumLength:
    def test_a_text_of_exactly_the_maximum_is_kept(self):
        assert _validate_text("a" * 5, "name", max_length=5) == "aaaaa"

    def test_one_character_more_is_refused(self):
        with pytest.raises(ApiError) as error:
            _validate_text("a" * 6, "name", max_length=5)

        assert error.value.status == 422
        assert error.value.reason == "FIELD_TOO_LONG"
        assert error.value.message.params == {"field": "name", "max": 5}

    def test_spaces_around_the_text_do_not_count(self):
        assert _validate_text("  " + "a" * 5 + "  ", "name", max_length=5) == "aaaaa"

    def test_characters_are_counted_and_not_bytes(self):
        assert _validate_text("ë" * 5, "name", max_length=5) == "ë" * 5
        with pytest.raises(ApiError) as error:
            _validate_text("ë" * 6, "name", max_length=5)
        assert error.value.reason == "FIELD_TOO_LONG"

    def test_a_text_that_is_too_long_answers_too_long_even_with_control_characters(self):
        with pytest.raises(ApiError) as error:
            _validate_text("ab\x00cd", "name", max_length=3)

        assert error.value.reason == "FIELD_TOO_LONG"

    def test_a_text_that_is_too_long_is_not_scanned(self, monkeypatch):
        """A multi-megabyte name must cost no per-character scan."""

        def refuse_to_scan(value):
            raise AssertionError("scanned a text that is too long")

        monkeypatch.setattr(admin, "_has_forbidden_characters", refuse_to_scan)

        with pytest.raises(ApiError) as error:
            _validate_text("a" * 6, "name", max_length=5)

        assert error.value.reason == "FIELD_TOO_LONG"

    def test_without_a_maximum_any_length_is_kept(self):
        """The create routes pass none: a new `maxLength` on an existing request
        field would be a breaking API change."""
        assert _validate_text("a" * 5000, "name") == "a" * 5000


@dataclass(frozen=True)
class Subject:
    """One of the two endpoints, so the rules they share are written once."""

    url: str
    route: str
    field: str
    model: type
    row_id: uuid.UUID
    original: str
    action: str
    previous_ref: str
    lookup: str
    unknown: str  # the code of the 404 once the row is gone
    child: Site | Version  # a row that refers to the one being changed

    async def current(self, factory) -> str:
        async with factory() as db:
            return getattr(await db.get(self.model, self.row_id), self.field)

    async def store(self, factory, text: str) -> None:
        async with factory() as db:
            setattr(await db.get(self.model, self.row_id), self.field, text)
            await db.commit()


@pytest.fixture(params=["group", "site"])
def subject(request, data) -> Subject:
    if request.param == "group":
        return Subject(
            url=NAME,
            route=NAME_ROUTE,
            field="name",
            model=Group,
            row_id=data.group.id,
            original="Team",
            action="group_name",
            previous_ref="previous_name",
            lookup="_group_with_role",
            unknown="UNKNOWN_GROUP",
            child=Site(group_id=data.group.id, slug="nieuw", title="Nieuw", access_base=AccessBase.SITE_TEAM),
        )
    return Subject(
        url=TITLE,
        route=TITLE_ROUTE,
        field="title",
        model=Site,
        row_id=data.site.id,
        original="Site",
        action="site_title",
        previous_ref="previous_title",
        lookup="_site_with_role",
        unknown="UNKNOWN_SITE",
        child=Version(
            site_id=data.site.id,
            target=VersionTarget.LIVE,
            storage_ref="ref-deploy",
            ci_repository="github.com/minbzk/website",
        ),
    )


def _as_group_admin(client, app) -> dict[str, str]:
    return login(client, app, sub="lid-a", email="a@example.nl")


async def _audit_rows(factory, action: str) -> list[AuditLogEntry]:
    async with factory() as db:
        return list(await db.scalars(select(AuditLogEntry).where(AuditLogEntry.action == action)))


async def _assert_refusal_logged(factory, *, code: str, route: str, text: str) -> None:
    """One `admin_access` row for the attempt: the route and the reason, and
    nothing of the text that was sent."""
    (row,) = await _refusal_rows(factory)
    assert row.result == "refused"
    assert row.reason_code == code
    assert row.refs["method"] == "PUT"
    assert row.refs["route"] == route
    assert text not in json.dumps(row.refs)


class TestTheTextRules:
    """The same rules for a group's name and a site's title."""

    async def test_spaces_around_the_text_are_stripped(self, client, app, data, factory, subject):
        headers = _as_group_admin(client, app)

        response = await client.put(subject.url, json={subject.field: "  Nieuw  "}, headers=headers)

        assert response.status_code == 200
        assert response.json()[subject.field] == "Nieuw"
        assert await subject.current(factory) == "Nieuw"

    async def test_a_text_of_200_characters_is_accepted(self, client, app, data, factory, subject):
        headers = _as_group_admin(client, app)

        response = await client.put(subject.url, json={subject.field: "a" * 200}, headers=headers)

        assert response.status_code == 200
        assert await subject.current(factory) == "a" * 200

    async def test_spaces_around_a_text_of_200_characters_do_not_count(self, client, app, data, factory, subject):
        headers = _as_group_admin(client, app)

        response = await client.put(subject.url, json={subject.field: "  " + "a" * 200 + "  "}, headers=headers)

        assert response.status_code == 200
        assert await subject.current(factory) == "a" * 200

    async def test_a_text_of_201_characters_is_refused(self, client, app, data, factory, subject):
        headers = _as_group_admin(client, app)

        response = await client.put(subject.url, json={subject.field: "a" * 201}, headers=headers)

        assert response.status_code == 422
        assert response.json()["code"] == "FIELD_TOO_LONG"
        assert await subject.current(factory) == subject.original
        assert await _audit_rows(factory, subject.action) == []
        assert await _refusal_rows(factory) == []

    async def test_the_refusal_is_told_in_the_language_asked_for(self, client, app, data, subject):
        headers = _as_group_admin(client, app)
        too_long = {subject.field: "a" * 201}

        english = await client.put(subject.url, json=too_long, headers=headers)
        dutch = await client.put(subject.url, json=too_long, headers={**headers, "Accept-Language": "nl"})

        assert english.json()["detail"] == f"Field '{subject.field}' may be at most 200 characters long."
        assert dutch.json()["detail"] == f"Veld '{subject.field}' mag hoogstens 200 tekens lang zijn."

    @pytest.mark.parametrize("blank", ["", "   "])
    async def test_an_empty_text_is_refused(self, client, app, data, factory, subject, blank):
        headers = _as_group_admin(client, app)

        response = await client.put(subject.url, json={subject.field: blank}, headers=headers)

        assert response.status_code == 422
        assert response.json()["code"] == "FIELD_EMPTY"
        assert await subject.current(factory) == subject.original

    async def test_a_body_without_the_text_is_refused(self, client, app, data, factory, subject):
        headers = _as_group_admin(client, app)

        missing = await client.put(subject.url, json={}, headers=headers)
        null = await client.put(subject.url, json={subject.field: None}, headers=headers)

        assert missing.status_code == null.status_code == 422
        assert await subject.current(factory) == subject.original

    @pytest.mark.parametrize("bad_text", ["Team\x00stil", "Team\nnieuw", "Team\tinsprong", "Team \u202egedraaid"])
    async def test_control_and_formatting_characters_are_refused(
        self, client, app, data, factory, subject, bad_text
    ):
        headers = _as_group_admin(client, app)

        response = await client.put(subject.url, json={subject.field: bad_text}, headers=headers)

        assert response.status_code == 422
        assert response.json()["code"] == "FIELD_CONTROL_CHARACTERS"
        assert await subject.current(factory) == subject.original

    async def test_saving_the_text_that_is_already_there_changes_nothing(self, client, app, data, factory, subject):
        headers = _as_group_admin(client, app)

        for sent in (subject.original, f"  {subject.original}  "):
            response = await client.put(subject.url, json={subject.field: sent}, headers=headers)

            assert response.status_code == 200
            assert response.json()[subject.field] == subject.original
        assert await _audit_rows(factory, subject.action) == []

    async def test_the_text_is_checked_before_the_unchanged_check(self, client, app, data, factory, subject):
        """A name from before the maximum existed, sent back as it stands, is
        refused like any other text that is too long."""
        await subject.store(factory, "a" * 201)
        headers = _as_group_admin(client, app)

        response = await client.put(subject.url, json={subject.field: "a" * 201}, headers=headers)

        assert response.status_code == 422
        assert response.json()["code"] == "FIELD_TOO_LONG"

    async def test_the_role_is_checked_before_the_text(self, client, app, data, factory, subject):
        await _join_group(factory, data.group, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(subject.url, json={subject.field: ""}, headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"

    async def test_the_request_without_the_csrf_header_is_refused(self, client, app, data, factory, subject):
        _as_group_admin(client, app)

        response = await client.put(subject.url, json={subject.field: "Nieuwe tekst"})

        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_INVALID"
        assert await subject.current(factory) == subject.original
        await _assert_refusal_logged(factory, code="CSRF_INVALID", route=subject.route, text="Nieuwe tekst")

    async def test_a_platform_administrator_without_a_role_is_refused(self, client, app, data, factory, subject):
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")

        response = await client.put(subject.url, json={subject.field: "Nieuwe tekst"}, headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"
        assert await subject.current(factory) == subject.original
        await _assert_refusal_logged(factory, code="INSUFFICIENT_ROLE", route=subject.route, text="Nieuwe tekst")

    @pytest.mark.parametrize("role", [Role.EDITOR, Role.READER])
    async def test_a_group_member_below_admin_is_refused(self, client, app, data, factory, subject, role):
        await _join_group(factory, data.group, data.member_b, role)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(subject.url, json={subject.field: "Nieuwe tekst"}, headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"
        assert await subject.current(factory) == subject.original
        await _assert_refusal_logged(factory, code="INSUFFICIENT_ROLE", route=subject.route, text="Nieuwe tekst")

    async def test_two_changes_at_the_same_moment_each_keep_what_they_replaced(
        self, client, app, data, factory, subject, monkeypatch
    ):
        """Both requests have read the row and passed the role check before
        either takes the lock, so only the lock lets the second one see what the
        first wrote."""
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        barrier = asyncio.Barrier(2)
        real = getattr(admin, subject.lookup)

        async def meet(*args, **kwargs):
            found = await real(*args, **kwargs)
            await barrier.wait()
            return found

        monkeypatch.setattr(admin, subject.lookup, meet)
        first = _as_group_admin(client, app)
        async with make_test_client(app) as other:
            second = login(other, app, sub="lid-b", email="b@example.nl")
            responses = await asyncio.wait_for(
                asyncio.gather(
                    client.put(subject.url, json={subject.field: "Een"}, headers=first),
                    other.put(subject.url, json={subject.field: "Twee"}, headers=second),
                ),
                timeout=30,
            )

        assert [response.status_code for response in responses] == [200, 200]
        written_first = ({"Een", "Twee"} - {await subject.current(factory)}).pop()
        rows = await _audit_rows(factory, subject.action)
        assert sorted(row.refs[subject.previous_ref] for row in rows) == sorted([subject.original, written_first])

    async def test_a_change_does_not_hold_back_the_rows_that_refer_to_its_row(
        self, client, app, data, factory, subject, monkeypatch
    ):
        """A site added to the group, or a version a deploy writes, does not wait
        for a rename: the lock keeps out other changes of the row, not the rows
        that point at it."""
        locked, release = asyncio.Event(), asyncio.Event()
        reread_locked = admin._reread_locked

        async def lock_and_hold(*args, **kwargs):
            await reread_locked(*args, **kwargs)
            locked.set()
            await release.wait()

        monkeypatch.setattr(admin, "_reread_locked", lock_and_hold)
        headers = _as_group_admin(client, app)
        change = asyncio.create_task(client.put(subject.url, json={subject.field: "Nieuw"}, headers=headers))
        try:
            async with asyncio.timeout(10):
                await locked.wait()
            # The row is locked: FOR UPDATE NOWAIT is refused at once. Without
            # this the insert below passing would prove nothing.
            async with factory() as db:
                with pytest.raises(DBAPIError) as probe:
                    await db.execute(
                        select(subject.model.id).where(subject.model.id == subject.row_id).with_for_update(nowait=True)
                    )
            assert getattr(probe.value.orig, "sqlstate", None) == "55P03"
            async with asyncio.timeout(5), factory() as db:
                db.add(subject.child)
                await db.commit()
        finally:
            release.set()
            (response,) = await asyncio.gather(change, return_exceptions=True)

        assert response.status_code == 200
        assert await subject.current(factory) == "Nieuw"

    async def test_a_row_deleted_after_the_role_check_is_unknown(
        self, client, app, data, factory, subject, monkeypatch
    ):
        """Another admin deletes the group or site between the role check and
        the lock: a 404 as for one that was never there, not a server error."""
        real = getattr(admin, subject.lookup)

        async def delete_meanwhile(*args, **kwargs):
            found = await real(*args, **kwargs)
            async with factory() as db:
                await db.execute(delete(subject.model).where(subject.model.id == subject.row_id))
                await db.commit()
            return found

        monkeypatch.setattr(admin, subject.lookup, delete_meanwhile)
        headers = _as_group_admin(client, app)

        response = await client.put(subject.url, json={subject.field: "Nieuw"}, headers=headers)

        assert response.status_code == 404
        assert response.json()["code"] == subject.unknown
        assert await _audit_rows(factory, subject.action) == []


class TestGroupName:
    async def test_a_group_admin_changes_the_name(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        response = await client.put(NAME, json={"name": "Team Aurora"}, headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Team Aurora"
        assert body["slug"] == "team"
        assert body["defaultAccess"]["base"] == "site_team"
        async with factory() as db:
            assert (await db.get(Group, data.group.id)).name == "Team Aurora"

    async def test_the_audit_row_keeps_the_previous_name_and_nothing_else_of_it(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        await client.put(NAME, json={"name": "Team Aurora"}, headers=headers)

        (row,) = await _audit_rows(factory, "group_name")
        assert row.result == "allowed"
        assert row.actor_pseudonym == pseudonymise(app.state.settings.audit_pepper, "lid-a")
        assert row.refs["group"] == "team"
        assert row.refs["group_id"] == str(data.group.id)
        assert row.refs["previous_name"] == "Team"
        assert set(row.refs) - {"ip_unvouched"} == {"group", "group_id", "previous_name"}

    async def test_the_second_change_keeps_the_name_the_first_wrote(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        await client.put(NAME, json={"name": "Eerste"}, headers=headers)
        await client.put(NAME, json={"name": "Tweede"}, headers=headers)

        rows = await _audit_rows(factory, "group_name")
        assert sorted(row.refs["previous_name"] for row in rows) == ["Eerste", "Team"]

    async def test_a_member_without_a_role_in_the_group_is_refused(self, client, app, data, factory):
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(NAME, json={"name": "Nieuwe naam"}, headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"
        async with factory() as db:
            assert (await db.get(Group, data.group.id)).name == "Team"
        await _assert_refusal_logged(factory, code="INSUFFICIENT_ROLE", route=NAME_ROUTE, text="Nieuwe naam")

    async def test_an_unknown_group_is_404(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        response = await client.put(f"{BASE}/groups/bestaat-niet/name", json={"name": "Nieuwe naam"}, headers=headers)

        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_GROUP"
        await _assert_refusal_logged(factory, code="UNKNOWN_GROUP", route=NAME_ROUTE, text="Nieuwe naam")


class TestSiteTitle:
    async def test_a_group_admin_changes_the_title(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        response = await client.put(TITLE, json={"title": "Documentatie"}, headers=headers)

        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Documentatie"
        assert body["slug"] == "site"
        assert body["groupSlug"] == "team"
        assert body["access"]["base"] == "site_team"
        async with factory() as db:
            assert (await db.get(Site, data.site.id)).title == "Documentatie"

    async def test_an_admin_of_the_site_alone_changes_the_title(self, client, app, data, factory):
        await _join_site(factory, data.site, data.member_b, Role.ADMIN)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(TITLE, json={"title": "Documentatie"}, headers=headers)

        assert response.status_code == 200
        async with factory() as db:
            assert (await db.get(Site, data.site.id)).title == "Documentatie"

    async def test_the_audit_row_keeps_the_previous_title_and_nothing_else_of_it(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        await client.put(TITLE, json={"title": "Documentatie"}, headers=headers)

        (row,) = await _audit_rows(factory, "site_title")
        assert row.result == "allowed"
        assert row.actor_pseudonym == pseudonymise(app.state.settings.audit_pepper, "lid-a")
        assert row.refs["group"] == "team"
        assert row.refs["site"] == "site"
        assert row.refs["site_id"] == str(data.site.id)
        assert row.refs["previous_title"] == "Site"
        assert set(row.refs) - {"ip_unvouched"} == {"group", "site", "site_id", "previous_title"}

    async def test_the_second_change_keeps_the_title_the_first_wrote(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        await client.put(TITLE, json={"title": "Eerste"}, headers=headers)
        await client.put(TITLE, json={"title": "Tweede"}, headers=headers)

        rows = await _audit_rows(factory, "site_title")
        assert sorted(row.refs["previous_title"] for row in rows) == ["Eerste", "Site"]

    @pytest.mark.parametrize("role", [Role.EDITOR, Role.READER])
    async def test_a_site_role_below_admin_is_refused(self, client, app, data, factory, role):
        await _join_site(factory, data.site, data.member_b, role)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(TITLE, json={"title": "Nieuwe titel"}, headers=headers)

        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"
        async with factory() as db:
            assert (await db.get(Site, data.site.id)).title == "Site"
        await _assert_refusal_logged(factory, code="INSUFFICIENT_ROLE", route=TITLE_ROUTE, text="Nieuwe titel")

    async def test_a_member_without_a_role_on_the_site_gets_the_404_of_an_unknown_site(
        self, client, app, data, factory
    ):
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.put(TITLE, json={"title": "Nieuwe titel"}, headers=headers)

        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_SITE"
        async with factory() as db:
            assert (await db.get(Site, data.site.id)).title == "Site"
        await _assert_refusal_logged(factory, code="UNKNOWN_SITE", route=TITLE_ROUTE, text="Nieuwe titel")

    async def test_an_unknown_site_is_404(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        response = await client.put(
            f"{BASE}/sites/team/bestaat-niet/title", json={"title": "Nieuwe titel"}, headers=headers
        )

        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_SITE"
        await _assert_refusal_logged(factory, code="UNKNOWN_SITE", route=TITLE_ROUTE, text="Nieuwe titel")

    async def test_an_unknown_group_is_404(self, client, app, data, factory):
        headers = _as_group_admin(client, app)

        response = await client.put(
            f"{BASE}/sites/bestaat-niet/site/title", json={"title": "Nieuwe titel"}, headers=headers
        )

        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_GROUP"
        await _assert_refusal_logged(factory, code="UNKNOWN_GROUP", route=TITLE_ROUTE, text="Nieuwe titel")


class TestCreateHasNoMaximum:
    """A maximum on a request field the create routes already have would be a
    breaking API change, so only the two new bodies carry one."""

    async def test_a_group_can_still_be_created_with_a_long_name(self, client, app, data):
        headers = _as_group_admin(client, app)

        response = await client.post(f"{BASE}/groups", json={"name": "a" * 300, "slug": "lang"}, headers=headers)

        assert response.status_code == 201

    async def test_a_site_can_still_be_created_with_a_long_title(self, client, app, data):
        headers = _as_group_admin(client, app)

        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "a" * 300, "slug": "lang"}, headers=headers
        )

        assert response.status_code == 201


SURROGATE = chr(0xD800)


async def _send_json(client, method: str, url: str, headers: dict[str, str], fields: dict):
    """json.dumps writes a lone surrogate as an escape, the way a client other
    than httpx would: httpx cannot encode the character itself."""
    return await client.request(
        method, url, content=json.dumps(fields), headers={**headers, "Content-Type": "application/json"}
    )


class TestALoneSurrogate:
    """A lone surrogate (category Cs) is no text: asyncpg cannot encode it,
    which made it a 500."""

    @pytest.mark.parametrize("half", [0xD800, 0xDBFF, 0xDC00, 0xDFFF])
    def test_either_half_alone_is_forbidden(self, half):
        assert _has_forbidden_characters("a" + chr(half))

    @pytest.mark.parametrize(
        ("method", "url", "fields"),
        [
            ("PUT", NAME, {"name": f"Team {SURROGATE} x"}),
            ("PUT", TITLE, {"title": f"Site {SURROGATE} x"}),
            ("POST", f"{BASE}/groups", {"name": f"Team {SURROGATE} x", "slug": "nieuw"}),
            ("POST", f"{BASE}/groups/team/sites", {"title": f"Site {SURROGATE} x", "slug": "nieuw"}),
            ("POST", f"{BASE}/sites/team/site/invitees", {"identifier": f"a{SURROGATE}b@example.nl"}),
            ("POST", f"{BASE}/groups/team/members", {"identifier": f"a{SURROGATE}b@example.nl"}),
            ("POST", f"{BASE}/sites/team/site/members", {"identifier": f"a{SURROGATE}b@example.nl"}),
        ],
        ids=[
            "rename-group",
            "rename-site",
            "create-group",
            "create-site",
            "add-invitee",
            "add-group-member",
            "add-site-member",
        ],
    )
    async def test_it_is_refused_like_a_control_character(self, client, app, data, method, url, fields):
        headers = _as_group_admin(client, app)

        response = await _send_json(client, method, url, headers, fields)

        assert response.status_code == 422
        assert response.json()["code"] == "FIELD_CONTROL_CHARACTERS"

    async def test_a_secret_link_label_with_one_is_refused(self, client, app, data):
        headers = _as_group_admin(client, app)

        response = await _send_json(
            client, "POST", f"{BASE}/sites/team/site/keys", headers, {"label": f"link {SURROGATE} x", "expiresAt": None}
        )

        assert response.status_code == 422

    async def test_an_audit_lookup_reason_with_one_is_refused(self, client, app, data):
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")

        response = await _send_json(
            client,
            "POST",
            f"{BASE}/platform/audit/actor-pseudonym",
            headers,
            {"identifier": "a@example.nl", "reason": f"onderzoek {SURROGATE} naar een melding"},
        )

        assert response.status_code == 422

    async def test_a_pair_sent_as_two_escapes_is_one_ordinary_character(self, client, app, data):
        """json.dumps escapes an emoji as a surrogate pair; the server reads the
        pair back as one character, which is text."""
        headers = _as_group_admin(client, app)

        response = await _send_json(client, "PUT", NAME, headers, {"name": f"Team {chr(0x1F600)}"})

        assert response.status_code == 200
        assert response.json()["name"] == f"Team {chr(0x1F600)}"


@pytest.fixture(scope="module")
def schemas():
    return openapi_file.schema()["components"]["schemas"]


class TestSchema:
    def test_the_new_bodies_declare_the_maximum(self, schemas):
        assert schemas["GroupNameBody"]["properties"]["name"]["maxLength"] == 200
        assert schemas["SiteTitleBody"]["properties"]["title"]["maxLength"] == 200

    def test_the_create_bodies_declare_none(self, schemas):
        assert "maxLength" not in schemas["GroupCreate"]["properties"]["name"]
        assert "maxLength" not in schemas["SiteCreate"]["properties"]["title"]
