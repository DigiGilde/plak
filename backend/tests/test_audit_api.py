"""The read path on the audit log: platform admins only, keyset pagination,
and actors that stay pseudonymous. docs/audit-log.md, section "Access".

Reuses the admin API fixtures; every read writes an audit_read row of its own,
which the tests below account for.
"""

# Fixtures imported from test_admin_api are found by name, which ruff reads as
# a parameter shadowing the import.
# ruff: noqa: F811

from __future__ import annotations

import pytest
from sqlalchemy import select
from test_admin_api import (  # noqa: F401 - fixtures are found by name
    BASE,
    _count,
    _new_member,
    app,
    client,
    content_root,
    data,
    factory,
    login,
)

from plak.audit import vocabulary
from plak.audit.log import ANONYMOUS, Actor
from plak.audit.pseudonymisation import pseudonymise
from plak.constants import AccessBase
from plak.models.audit import ActorKind, AuditLogEntry, ContentViewer
from plak.models.ci import CiProvider, SiteRepository
from plak.models.identity import Member, MemberStatus, PlatformRole
from plak.models.publication import Site

AUDIT = f"{BASE}/platform/audit"
REASON = "onderzoek naar een melding over misbruik"
LOOKUP = f"{BASE}/platform/audit/actor-pseudonym"
IDENTITY = f"{BASE}/platform/audit/actor-identity"


async def _write(app, *, action: str, result: str = "allowed", sub: str | None = None, refs: dict | None = None):
    actor = Actor(ActorKind.MEMBER, sub) if sub else ANONYMOUS
    await app.state.audit_log.write(action, actor, result, refs=refs)


def _as_admin(client, app) -> dict[str, str]:
    return login(client, app, sub="admin-sub", email="admin@example.nl")


async def _link(factory, data) -> None:
    """Links minbzk/website on GitHub to team/site and a second site team/tweede."""
    async with factory() as db:
        second = Site(group_id=data.group.id, slug="tweede", title="Tweede", access_base=AccessBase.SITE_TEAM)
        db.add(second)
        await db.flush()
        for site_id in (data.site.id, second.id):
            db.add(
                SiteRepository(
                    site_id=site_id,
                    provider=CiProvider.GITHUB,
                    host="https://github.com",
                    owner="minbzk",
                    repo="website",
                    repository_id=1001,
                    owner_id=2002,
                    live_branch="main",
                )
            )
        await db.commit()


class TestCiLookup:
    @pytest.mark.parametrize(
        "identifier", ["minbzk/website", "MinBZK/Website", "github.com/minbzk/website", "https://github.com/minbzk/website/"]
    )
    async def test_a_linked_repository_is_looked_up_by_name(self, client, app, factory, data, identifier):
        await _link(factory, data)
        headers = _as_admin(client, app)
        response = await client.post(LOOKUP, json={"identifier": identifier, "reason": REASON}, headers=headers)
        assert response.status_code == 200
        assert response.json() == {
            "actorPseudonym": pseudonymise(app.state.settings.audit_pepper, "github:https://github.com:1001"),
            "resolvedAs": "ci",
        }

    async def test_another_host_does_not_match(self, client, app, factory, data):
        await _link(factory, data)
        headers = _as_admin(client, app)
        response = await client.post(
            LOOKUP, json={"identifier": "code.overheid.nl/minbzk/website", "reason": REASON}, headers=headers
        )
        assert response.json()["resolvedAs"] == "unknown"

    @pytest.mark.parametrize("identifier", ["website", "a/b/c/d", "geen.host/x"])
    async def test_other_shapes_are_no_repository(self, client, app, factory, data, identifier):
        await _link(factory, data)
        headers = _as_admin(client, app)
        response = await client.post(LOOKUP, json={"identifier": identifier, "reason": REASON}, headers=headers)
        assert response.json()["resolvedAs"] == "unknown"

    async def test_the_same_name_on_two_providers_is_ambiguous(self, client, app, factory, data):
        await _link(factory, data)
        async with factory() as db:
            third = Site(group_id=data.group.id, slug="derde", title="Derde", access_base=AccessBase.SITE_TEAM)
            db.add(third)
            await db.flush()
            db.add(
                SiteRepository(
                    site_id=third.id,
                    provider=CiProvider.FORGEJO,
                    host="https://code.overheid.nl",
                    owner="minbzk",
                    repo="website",
                    repository_id=77,
                    owner_id=88,
                )
            )
            await db.commit()
        headers = _as_admin(client, app)
        response = await client.post(LOOKUP, json={"identifier": "minbzk/website", "reason": REASON}, headers=headers)
        assert response.status_code == 409
        assert response.json()["code"] == "IDENTIFIER_AMBIGUOUS"
        response = await client.post(
            LOOKUP, json={"identifier": "code.overheid.nl/minbzk/website", "reason": REASON}, headers=headers
        )
        assert response.json()["actorPseudonym"] == pseudonymise(
            app.state.settings.audit_pepper, "forgejo:https://code.overheid.nl:77"
        )


class TestReading:
    async def test_only_a_platform_admin_reads_the_audit_log(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        refused = await client.get(AUDIT)
        assert refused.status_code == 403
        assert refused.json()["code"] == "NOT_ADMIN"

        _as_admin(client, app)
        assert (await client.get(AUDIT)).status_code == 200

    async def test_newest_first_and_the_cursor_walks_on_without_overlap(self, client, app, data):
        for number in range(3):
            await _write(app, action=f"test_{number}")
        _as_admin(client, app)

        page = (await client.get(AUDIT, params={"limit": 2})).json()
        assert [row["action"] for row in page["entries"]] == ["test_2", "test_1"]
        assert page["nextCursor"]

        # The first read wrote an audit_read row, but it is newer than the
        # cursor, so the second page starts where the first stopped.
        rest = (await client.get(AUDIT, params={"limit": 2, "cursor": page["nextCursor"]})).json()
        assert [row["action"] for row in rest["entries"]] == ["test_0"]
        assert rest["nextCursor"] is None

    async def test_the_filters_combine(self, client, app, data):
        await _write(app, action="content_access", result="refused", refs={"group": "team", "site": "site"})
        await _write(app, action="content_access", result="allowed", refs={"group": "team", "site": "ander"})
        _as_admin(client, app)

        body = (await client.get(AUDIT, params={"site": "site", "result": "refused"})).json()
        assert [row["refs"]["site"] for row in body["entries"]] == ["site"]

    async def test_a_change_of_address_is_found_from_the_old_and_the_new_slug(self, client, app, data):
        await _write(
            app,
            action="group_slug",
            refs={"group": "ploeg", "previous_group": "team", "group_id": "g", "sites": ["blog", "site"]},
        )
        await _write(
            app, action="site_slug", refs={"group": "ploeg", "site": "docs", "previous_site": "site", "site_id": "s"}
        )
        await _write(app, action="content_access", refs={"group": "ander", "site": "blogger"})
        _as_admin(client, app)

        async def found(**filters) -> list[str]:
            body = (await client.get(AUDIT, params=filters)).json()
            return sorted(row["action"] for row in body["entries"])

        assert await found(group="team") == ["group_slug"]
        assert await found(group="ploeg") == ["group_slug", "site_slug"]
        assert await found(site="site") == ["group_slug", "site_slug"]
        assert await found(site="docs") == ["site_slug"]
        assert await found(site="blog") == ["group_slug"]
        # A whole element of `sites`, never a part of one.
        assert await found(site="blogger") == ["content_access"]

    async def test_the_actor_stays_pseudonymous(self, client, app, data):
        await _write(app, action="test_actor", sub="lid-a")
        _as_admin(client, app)

        response = await client.get(AUDIT, params={"action": "test_actor"})
        assert "lid-a" not in response.text
        expected = pseudonymise(app.state.settings.audit_pepper, "lid-a")
        assert response.json()["entries"][0]["actorPseudonym"] == expected

    async def test_an_unreadable_cursor_and_a_wrong_pseudonym_give_422(self, client, app, data):
        _as_admin(client, app)
        cursor = await client.get(AUDIT, params={"cursor": "geen-cursor"})
        assert cursor.status_code == 422
        assert cursor.json()["code"] == "CURSOR_INVALID"

        pseudonym = await client.get(AUDIT, params={"actorPseudonym": "xyz"})
        assert pseudonym.status_code == 422
        assert pseudonym.json()["code"] == "ACTOR_PSEUDONYM_INVALID"

    async def test_limit_is_capped(self, client, app, data):
        _as_admin(client, app)
        assert (await client.get(AUDIT, params={"limit": 201})).status_code == 422
        assert (await client.get(AUDIT, params={"limit": 0})).status_code == 422

    async def test_every_read_is_itself_audited(self, client, app, factory, data):
        """Access to the log is a power of its own. Every page counts, and the
        row carries the filters, never the cursor."""
        _as_admin(client, app)
        await client.get(AUDIT, params={"result": "refused"})
        await client.get(AUDIT, params={"result": "refused"})

        assert await _count(factory, AuditLogEntry, action=vocabulary.AUDIT_READ) == 2
        async with factory() as db:
            row = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.AUDIT_READ))
        assert row.refs["filters"] == {"limit": 50, "result": "refused"}

    async def test_a_failed_audit_write_gives_503_and_no_page(self, client, app, data, monkeypatch):
        """The read is itself a disclosure of pseudonyms; if the audit row
        that records it cannot be written, the page must not go out either."""
        await _write(app, action="test_actor", sub="lid-a")
        _as_admin(client, app)

        async def _boom(*args, **kwargs):
            raise RuntimeError("audit log write error")

        monkeypatch.setattr(app.state.audit_log, "write_strict", _boom)
        response = await client.get(AUDIT)
        assert response.status_code == 503
        assert response.json()["code"] == "AUDIT_UNAVAILABLE"
        assert "lid-a" not in response.text
        assert "test_actor" not in response.text


class TestLookingSomeoneUp:
    async def test_a_member_is_looked_up_by_email_and_the_pseudonym_filters(self, client, app, data):
        await _write(app, action="test_actor", sub="lid-a")
        headers = _as_admin(client, app)

        lookup = await client.post(LOOKUP, json={"identifier": "a@example.nl", "reason": REASON}, headers=headers)
        assert lookup.status_code == 200
        assert lookup.json()["resolvedAs"] == "member"

        found = await client.get(AUDIT, params={"actorPseudonym": lookup.json()["actorPseudonym"]})
        assert [row["action"] for row in found.json()["entries"]] == ["test_actor"]

    async def test_a_members_name_does_not_resolve_here(self, client, app, factory, data):
        """Unlike the group and site member endpoints, the actor lookup never
        resolves a name: it is not an identifier you should be able to guess
        your way to someone's pseudonym with. A name is pseudonymised
        literally, exactly like any other unknown non-e-mail identifier."""
        await _new_member(factory, sub="lid-c", email="c@example.nl", name="Cato Jansen")
        headers = _as_admin(client, app)

        response = await client.post(LOOKUP, json={"identifier": "Cato Jansen", "reason": REASON}, headers=headers)
        body = response.json()

        assert body["resolvedAs"] == "unknown"
        assert body["actorPseudonym"] == pseudonymise(app.state.settings.audit_pepper, "Cato Jansen")

    async def test_an_unknown_non_email_identifier_is_pseudonymised_literally(self, client, app, data):
        """A raw sub with nothing behind it (e.g. from Keycloak) is still hashed."""
        headers = _as_admin(client, app)
        body_ = {"identifier": "nooit-bestaan-heeft-dit", "reason": REASON}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        body = response.json()

        assert body["resolvedAs"] == "unknown"
        assert body["actorPseudonym"] == pseudonymise(app.state.settings.audit_pepper, "nooit-bestaan-heeft-dit")

    async def test_an_unknown_email_identifier_is_404_and_never_hashed(self, client, app, factory, data):
        """The pitfall: hashing an unknown e-mail address gains nothing (it can
        never appear in the log), so it must not happen."""
        headers = _as_admin(client, app)
        body_ = {"identifier": "niemand@example.nl", "reason": REASON}
        response = await client.post(LOOKUP, json=body_, headers=headers)

        assert response.status_code == 404
        assert response.json()["code"] == "IDENTIFIER_UNKNOWN"

        async with factory() as db:
            row = await db.scalar(
                select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.AUDIT_ACTOR_LOOKUP)
            )
        assert row is not None
        assert row.refs["resolved_as"] == "unknown"
        assert "pseudonym" not in row.refs
        assert "niemand@example.nl" not in str(row.refs)

    async def test_the_lookup_demands_platform_admin_and_csrf(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        refused = await client.post(LOOKUP, json={"identifier": "a@example.nl", "reason": REASON}, headers=headers)
        assert refused.status_code == 403
        assert refused.json()["code"] == "NOT_ADMIN"

        _as_admin(client, app)
        without_csrf = await client.post(LOOKUP, json={"identifier": "a@example.nl", "reason": REASON})
        assert without_csrf.status_code == 403
        assert without_csrf.json()["code"] == "CSRF_INVALID"

    async def test_the_lookup_records_the_pseudonym_and_never_the_identifier(self, client, app, factory, data):
        headers = _as_admin(client, app)
        await client.post(LOOKUP, json={"identifier": "a@example.nl", "reason": REASON}, headers=headers)

        async with factory() as db:
            row = await db.scalar(
                select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.AUDIT_ACTOR_LOOKUP)
            )
        assert row is not None
        assert "a@example.nl" not in str(row.refs)
        assert "lid-a" not in str(row.refs)
        assert row.refs["pseudonym"] == pseudonymise(app.state.settings.audit_pepper, "lid-a")
        assert row.refs["resolved_as"] == "member"

    async def test_a_failed_audit_write_gives_503_and_no_pseudonym(self, client, app, factory, data, monkeypatch):
        headers = _as_admin(client, app)

        async def _boom(*args, **kwargs):
            raise RuntimeError("audit log write error")

        monkeypatch.setattr(app.state.audit_log, "write_strict_limited", _boom)
        response = await client.post(LOOKUP, json={"identifier": "a@example.nl", "reason": REASON}, headers=headers)
        assert response.status_code == 503
        assert response.json()["code"] == "AUDIT_UNAVAILABLE"
        assert "a@example.nl" not in response.text
        assert "lid-a" not in response.text
        assert await _count(factory, AuditLogEntry, action=vocabulary.AUDIT_ACTOR_LOOKUP) == 0


class TestIdentifyingAPseudonym:
    async def test_a_member_pseudonym_resolves_to_the_member(self, client, app, data):
        headers = _as_admin(client, app)
        pseudonym = pseudonymise(app.state.settings.audit_pepper, data.member_a.sso_subject)

        response = await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "member"
        assert body["memberId"] == str(data.member_a.id)
        assert body["email"] == data.member_a.email
        assert body["memberStatus"] == "active"

    async def test_a_ci_pseudonym_resolves_to_the_repository_and_its_sites(self, client, app, data, factory):
        await _link(factory, data)
        headers = _as_admin(client, app)
        pseudonym = pseudonymise(app.state.settings.audit_pepper, "github:https://github.com:1001")

        response = await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "ci"
        assert body["provider"] == "github"
        assert body["host"] == "https://github.com"
        assert body["repository"] == "minbzk/website"
        assert body["sites"] == ["team/site", "team/tweede"]

    async def test_a_refused_ci_pseudonym_by_name_resolves_too(self, client, app, data, factory):
        """A Forgejo 15 token without ids that was refused is pseudonymised on
        `owner/repo`; the reverse lookup tries that spelling as well."""
        await _link(factory, data)
        headers = _as_admin(client, app)
        pseudonym = pseudonymise(app.state.settings.audit_pepper, "github:https://github.com:minbzk/website")

        response = await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)
        assert response.json()["kind"] == "ci"

    async def test_an_unknown_pseudonym_is_404_and_audited_as_unknown(self, client, app, factory, data):
        headers = _as_admin(client, app)
        pseudonym = pseudonymise(app.state.settings.audit_pepper, "nooit-bestaan-heeft-dit")

        response = await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)
        assert response.status_code == 404
        assert response.json()["code"] == "PSEUDONYM_UNKNOWN"

        async with factory() as db:
            row = await db.scalar(
                select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.AUDIT_ACTOR_IDENTITY)
            )
        assert row is not None
        assert row.refs["pseudonym"] == pseudonym
        assert row.refs["resolved_as"] == "unknown"

    async def test_an_unknown_pseudonym_writes_exactly_one_row(self, client, app, factory, data):
        """api/errors.py::_audit_refusal would otherwise add a second,
        generic admin_access row for the same 404."""
        headers = _as_admin(client, app)
        pseudonym = pseudonymise(app.state.settings.audit_pepper, "nooit-bestaan-heeft-dit")

        response = await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)
        assert response.status_code == 404

        assert await _count(factory, AuditLogEntry, action=vocabulary.AUDIT_ACTOR_IDENTITY) == 1
        assert await _count(factory, AuditLogEntry, action=vocabulary.ADMIN_ACCESS) == 0

    async def test_the_ci_audit_row_carries_only_pseudonym_resolved_as_and_reason(
        self, client, app, factory, data
    ):
        await _link(factory, data)
        headers = _as_admin(client, app)
        pseudonym = pseudonymise(app.state.settings.audit_pepper, "github:https://github.com:1001")
        await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)

        async with factory() as db:
            row = await db.scalar(
                select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.AUDIT_ACTOR_IDENTITY)
            )
        assert row is not None
        assert set(row.refs.keys()) == {"pseudonym", "resolved_as", "reason"}
        assert row.refs["resolved_as"] == "ci"
        assert "minbzk" not in str(row.refs)

    async def test_a_deactivated_platform_admin_is_refused(self, client, app, factory, data):
        async with factory() as db:
            db.add(
                Member(
                    sso_subject="oud-beheerder",
                    email="oud-beheerder@example.nl",
                    platform_role=PlatformRole.ADMIN,
                    status=MemberStatus.DEACTIVATED,
                )
            )
            await db.commit()

        headers = login(client, app, sub="oud-beheerder", email="oud-beheerder@example.nl")
        pseudonym = pseudonymise(app.state.settings.audit_pepper, data.member_a.sso_subject)
        response = await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "MEMBER_DEACTIVATED"

    async def test_a_failed_audit_write_gives_503_and_no_identity(self, client, app, factory, data, monkeypatch):
        headers = _as_admin(client, app)
        pseudonym = pseudonymise(app.state.settings.audit_pepper, data.member_a.sso_subject)

        async def _boom(*args, **kwargs):
            raise RuntimeError("audit log write error")

        monkeypatch.setattr(app.state.audit_log, "write_strict_limited", _boom)
        response = await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)
        assert response.status_code == 503
        assert response.json()["code"] == "AUDIT_UNAVAILABLE"
        assert data.member_a.email not in response.text
        assert data.member_a.sso_subject not in response.text
        assert await _count(factory, AuditLogEntry, action=vocabulary.AUDIT_ACTOR_IDENTITY) == 0

    async def test_an_invalid_pseudonym_is_422(self, client, app, data):
        headers = _as_admin(client, app)
        response = await client.post(IDENTITY, json={"actorPseudonym": "niet-hex", "reason": REASON}, headers=headers)
        assert response.status_code == 422
        assert response.json()["code"] == "ACTOR_PSEUDONYM_INVALID"

    async def test_identity_lookup_demands_platform_admin_and_csrf(self, client, app, data):
        pseudonym = pseudonymise(app.state.settings.audit_pepper, data.member_a.sso_subject)
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        refused = await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)
        assert refused.status_code == 403
        assert refused.json()["code"] == "NOT_ADMIN"

        _as_admin(client, app)
        without_csrf = await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON})
        assert without_csrf.status_code == 403
        assert without_csrf.json()["code"] == "CSRF_INVALID"

    async def test_the_identity_audit_row_never_contains_email_or_sub(self, client, app, factory, data):
        headers = _as_admin(client, app)
        pseudonym = pseudonymise(app.state.settings.audit_pepper, data.member_a.sso_subject)
        await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)

        async with factory() as db:
            row = await db.scalar(
                select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.AUDIT_ACTOR_IDENTITY)
            )
        assert row is not None
        assert row.refs["pseudonym"] == pseudonym
        assert row.refs["resolved_as"] == "member"
        assert data.member_a.email not in str(row.refs)
        assert data.member_a.sso_subject not in str(row.refs)


class TestContract:
    def test_the_filters_are_described_camelcase_query_parameters(self, app):
        parameters = app.openapi()["paths"][AUDIT]["get"]["parameters"]
        assert {parameter["name"] for parameter in parameters} == {
            "limit",
            "cursor",
            "since",
            "until",
            "action",
            "result",
            "reasonCode",
            "group",
            "site",
            "actorPseudonym",
        }
        assert all(parameter["in"] == "query" for parameter in parameters)
        assert all(parameter["description"] for parameter in parameters)
        # An empty call gives the newest page, not a 422.
        assert not [parameter["name"] for parameter in parameters if parameter.get("required")]


async def _add_content_viewer(
    factory, *, sub: str, email: str, email_verified: bool = True
) -> ContentViewer:
    async with factory() as db:
        viewer = ContentViewer(sso_subject=sub, email=email, email_verified=email_verified)
        db.add(viewer)
        await db.commit()
        await db.refresh(viewer)
    return viewer


class TestContentViewerLookup:
    async def test_a_content_viewer_is_looked_up_by_email(self, client, app, factory, data):
        await _add_content_viewer(factory, sub="viewer-sub", email="kijker@example.nl")
        headers = _as_admin(client, app)

        body_ = {"identifier": "kijker@example.nl", "reason": REASON}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.status_code == 200
        assert response.json()["resolvedAs"] == "content_viewer"
        assert response.json()["actorPseudonym"] == pseudonymise(app.state.settings.audit_pepper, "viewer-sub")

    async def test_a_content_viewer_pseudonym_resolves_back(self, client, app, factory, data):
        await _add_content_viewer(factory, sub="viewer-sub", email="kijker@example.nl")
        headers = _as_admin(client, app)
        pseudonym = pseudonymise(app.state.settings.audit_pepper, "viewer-sub")

        response = await client.post(IDENTITY, json={"actorPseudonym": pseudonym, "reason": REASON}, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "content_viewer"
        assert body["email"] == "kijker@example.nl"
        assert body["lastSeenAt"]

    async def test_members_take_priority_over_content_viewers(self, client, app, factory, data):
        """lid-a is both a member and, hypothetically, could be a viewer; the
        member record wins so the resolution stays deterministic."""
        await _add_content_viewer(factory, sub="lid-a", email="a@example.nl")
        headers = _as_admin(client, app)

        body_ = {"identifier": "a@example.nl", "reason": REASON}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.json()["resolvedAs"] == "member"


class TestReasonRequired:
    async def test_a_missing_reason_is_422(self, client, app, data):
        headers = _as_admin(client, app)
        response = await client.post(LOOKUP, json={"identifier": "a@example.nl"}, headers=headers)
        assert response.status_code == 422

    async def test_a_too_short_reason_is_422(self, client, app, data):
        headers = _as_admin(client, app)
        body_ = {"identifier": "a@example.nl", "reason": "te kort"}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.status_code == 422

    async def test_a_too_long_reason_is_422(self, client, app, data):
        headers = _as_admin(client, app)
        body_ = {"identifier": "a@example.nl", "reason": "x" * 501}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.status_code == 422

    async def test_the_reason_lands_in_the_audit_row(self, client, app, factory, data):
        headers = _as_admin(client, app)
        body_ = {"identifier": "a@example.nl", "reason": REASON}
        await client.post(LOOKUP, json=body_, headers=headers)

        async with factory() as db:
            row = await db.scalar(
                select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.AUDIT_ACTOR_LOOKUP)
            )
        assert row.refs["reason"] == REASON


class TestDailyLookupLimit:
    async def test_the_limit_is_enforced_and_the_refusal_is_audited(self, client, app, factory, data):
        app.state.settings.audit_lookup_daily_limit = 2
        headers = _as_admin(client, app)
        body_ = {"identifier": "a@example.nl", "reason": REASON}

        assert (await client.post(LOOKUP, json=body_, headers=headers)).status_code == 200
        assert (await client.post(LOOKUP, json=body_, headers=headers)).status_code == 200

        over_limit = await client.post(LOOKUP, json=body_, headers=headers)
        assert over_limit.status_code == 429
        assert over_limit.json()["code"] == "LOOKUP_LIMIT_REACHED"

        assert await _count(factory, AuditLogEntry, action=vocabulary.AUDIT_ACTOR_LOOKUP) == 2
        assert await _count(factory, AuditLogEntry, action=vocabulary.ADMIN_ACCESS, result="refused") == 1

    async def test_the_limit_is_per_admin(self, client, app, factory, data):
        app.state.settings.audit_lookup_daily_limit = 1
        headers = _as_admin(client, app)
        body_ = {"identifier": "a@example.nl", "reason": REASON}
        assert (await client.post(LOOKUP, json=body_, headers=headers)).status_code == 200
        assert (await client.post(LOOKUP, json=body_, headers=headers)).status_code == 429

        async with factory() as db:
            db.add(
                Member(
                    sso_subject="andere-beheerder",
                    email="andere-beheerder@example.nl",
                    platform_role=PlatformRole.ADMIN,
                    status=MemberStatus.ACTIVE,
                )
            )
            await db.commit()
        other_headers = login(client, app, sub="andere-beheerder", email="andere-beheerder@example.nl")
        assert (await client.post(LOOKUP, json=body_, headers=other_headers)).status_code == 200

    async def test_a_404_lookup_still_counts_toward_the_limit(self, client, app, factory, data):
        app.state.settings.audit_lookup_daily_limit = 1
        headers = _as_admin(client, app)

        first = await client.post(
            LOOKUP, json={"identifier": "niemand@example.nl", "reason": REASON}, headers=headers
        )
        assert first.status_code == 404
        over_limit = await client.post(
            LOOKUP, json={"identifier": "a@example.nl", "reason": REASON}, headers=headers
        )
        assert over_limit.status_code == 429
        assert over_limit.json()["code"] == "LOOKUP_LIMIT_REACHED"


class TestUnverifiedContentViewerEmail:
    async def test_an_unverified_email_does_not_match(self, client, app, factory, data):
        await _add_content_viewer(
            factory, sub="viewer-ongeverifieerd", email="ongeverifieerd@example.nl", email_verified=False
        )
        headers = _as_admin(client, app)

        body_ = {"identifier": "ongeverifieerd@example.nl", "reason": REASON}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.status_code == 404
        assert response.json()["code"] == "IDENTIFIER_UNKNOWN"

    async def test_the_sub_of_an_unverified_viewer_still_resolves(self, client, app, factory, data):
        """The exact sso_subject always resolves, verified or not: only the
        email-based fallback requires verification."""
        await _add_content_viewer(
            factory, sub="viewer-ongeverifieerd", email="ongeverifieerd@example.nl", email_verified=False
        )
        headers = _as_admin(client, app)

        body_ = {"identifier": "viewer-ongeverifieerd", "reason": REASON}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.status_code == 200
        assert response.json()["resolvedAs"] == "content_viewer"


class TestAmbiguousIdentifier:
    async def test_two_members_with_the_same_email_are_ambiguous(self, client, app, factory, data):
        async with factory() as db:
            db.add(Member(sso_subject="dubbel-1", email="dubbel@example.nl", status=MemberStatus.ACTIVE))
            db.add(Member(sso_subject="dubbel-2", email="dubbel@example.nl", status=MemberStatus.ACTIVE))
            await db.commit()
        headers = _as_admin(client, app)

        body_ = {"identifier": "dubbel@example.nl", "reason": REASON}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.status_code == 409
        assert response.json()["code"] == "IDENTIFIER_AMBIGUOUS"

    async def test_a_member_and_a_verified_viewer_with_the_same_email_are_ambiguous(
        self, client, app, factory, data
    ):
        await _add_content_viewer(factory, sub="viewer-dubbel", email="dubbel2@example.nl")
        async with factory() as db:
            db.add(Member(sso_subject="lid-dubbel", email="dubbel2@example.nl", status=MemberStatus.ACTIVE))
            await db.commit()
        headers = _as_admin(client, app)

        body_ = {"identifier": "dubbel2@example.nl", "reason": REASON}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.status_code == 409
        assert response.json()["code"] == "IDENTIFIER_AMBIGUOUS"

    async def test_ambiguity_is_audited_and_counted_no_pseudonym_leaks(self, client, app, factory, data):
        async with factory() as db:
            db.add(Member(sso_subject="dubbel-3", email="dubbel3@example.nl", status=MemberStatus.ACTIVE))
            db.add(Member(sso_subject="dubbel-4", email="dubbel3@example.nl", status=MemberStatus.ACTIVE))
            await db.commit()
        headers = _as_admin(client, app)

        body_ = {"identifier": "dubbel3@example.nl", "reason": REASON}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.status_code == 409

        async with factory() as db:
            row = await db.scalar(
                select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.AUDIT_ACTOR_LOOKUP)
            )
        assert row is not None
        assert row.refs["resolved_as"] == "ambiguous"
        assert row.refs["matches"] == 2
        assert "pseudonym" not in row.refs
        assert "dubbel3@example.nl" not in str(row.refs)

    async def test_ambiguous_lookup_counts_toward_the_daily_limit(self, client, app, factory, data):
        async with factory() as db:
            db.add(Member(sso_subject="dubbel-5", email="dubbel5@example.nl", status=MemberStatus.ACTIVE))
            db.add(Member(sso_subject="dubbel-6", email="dubbel5@example.nl", status=MemberStatus.ACTIVE))
            await db.commit()
        app.state.settings.audit_lookup_daily_limit = 1
        headers = _as_admin(client, app)
        body_ = {"identifier": "dubbel5@example.nl", "reason": REASON}

        assert (await client.post(LOOKUP, json=body_, headers=headers)).status_code == 409
        over_limit = await client.post(LOOKUP, json=body_, headers=headers)
        assert over_limit.status_code == 429
        assert over_limit.json()["code"] == "LOOKUP_LIMIT_REACHED"


class TestReasonContent:
    @pytest.mark.parametrize(
        "bad_reason",
        [
            "bevat een NUL\x00-teken hier",
            "bevat een nieuwe\nregel hierbinnen",
            "bevat een tab\ttussen woorden",
            "rtl-override ‮test hierbinnen",
        ],
    )
    async def test_control_and_format_characters_are_422(self, client, app, data, bad_reason):
        headers = _as_admin(client, app)
        response = await client.post(LOOKUP, json={"identifier": "a@example.nl", "reason": bad_reason}, headers=headers)
        assert response.status_code == 422

    async def test_an_email_address_in_the_reason_is_422(self, client, app, data):
        headers = _as_admin(client, app)
        body_ = {"identifier": "a@example.nl", "reason": "vraag het aan iemand@example.nl maar"}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.status_code == 422

    async def test_a_case_or_ticket_reference_is_accepted(self, client, app, data):
        headers = _as_admin(client, app)
        body_ = {"identifier": "a@example.nl", "reason": "onderzoek naar zaak SR-2026-0912"}
        response = await client.post(LOOKUP, json=body_, headers=headers)
        assert response.status_code == 200


class TestAdminCheckBeforeBodyValidation:
    """A non-admin's request must be refused (403, audited) before a
    malformed body would even be looked at (422) - otherwise a non-admin
    probing the endpoint goes unaudited."""

    async def test_non_admin_with_a_malformed_body_gets_403_not_422(self, client, app, factory, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(LOOKUP, json={"identifier": "a@example.nl"}, headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "NOT_ADMIN"

        assert await _count(factory, AuditLogEntry, action=vocabulary.ADMIN_ACCESS, result="refused") == 1

    async def test_non_admin_with_a_malformed_ip_reveal_body_gets_403_not_422(self, client, app, factory, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        entry_id = "00000000-0000-0000-0000-000000000000"
        response = await client.post(f"{BASE}/platform/audit/entries/{entry_id}/ip", json={}, headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == "NOT_ADMIN"
