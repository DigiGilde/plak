"""Tests for api/admin.py: the session API for the SPA (spec §7, §8).

Authorization per endpoint (non-member 403, inactive 403, platform actions for
admins only), CSRF double-submit on mutations, origin guarding on the
whole router, and the delete cascade including file trees. Uploads over the
session run through the same app via api/deploys.py (bearer as well as
session).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_audit import install_audit_recorder
from helpers_ci import FORGEJO_HOST, MockCi
from helpers_oidc import APP_BASE_URL, CONTENT_BASE_URL, make_test_client, set_session_cookie
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from plak.access import keys as access_keys
from plak.api import deploys
from plak.api.admin import make_admin_router
from plak.api.errors import register_error_handlers
from plak.audit.log import ANONYMOUS, AuditLog
from plak.audit.pseudonymisation import pseudonymise
from plak.auth.sessions import CSRF_COOKIE, CSRF_HEADER, SessionStore
from plak.ci.providers import ProviderClient
from plak.cli import service as cli
from plak.config import Settings
from plak.constants import AccessBase, Role
from plak.db import make_session_factory
from plak.ingest.store import ContentStore
from plak.models.audit import AuditLogEntry, ContentViewer
from plak.models.ci import CiProvider, SiteRepository
from plak.models.cli import CliSession
from plak.models.identity import Group, GroupMember, Member, MemberStatus, PlatformRole, SiteMember
from plak.models.publication import AccessKey, Invitee, Preview, Site, Version, VersionTarget

BASE = "/-/api/v1"
PROBLEM = "application/problem+json"

KEY_VALUE_RE = re.compile(r"^[A-Za-z0-9]{8}\.[A-Za-z0-9]{32}$")


def _settings(content_root) -> Settings:
    return Settings(
        db_url="postgresql+asyncpg://plak:plak@localhost:5432/plak",
        content_root=content_root,
        oidc_issuer="https://idp.example",
        oidc_client_id="plak-client",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr:hoog",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper="audit-pepper-van-minstens-32-bytes!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        base_url=APP_BASE_URL,
        content_base_url=CONTENT_BASE_URL,
        environment="dev",
    )


@pytest_asyncio.fixture
async def factory(migrated_dsn: str):
    engine = create_async_engine(migrated_dsn, poolclass=NullPool)
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def content_root(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    return root


def _mock_ci() -> MockCi:
    ci = MockCi()
    ci.add_github("MinBZK", "Website", 1001, 2002)
    ci.add_forgejo("minbzk", "plak", 3003, 4004)
    return ci


@pytest.fixture
def app(factory, content_root) -> FastAPI:
    settings = _settings(content_root)
    mock_ci = _mock_ci()
    app = FastAPI()
    app.state.settings = settings
    app.state.session_store = SessionStore()
    app.state.session_factory = factory
    app.state.content_store = ContentStore(content_root)
    app.state.audit_log = AuditLog(factory, settings.audit_pepper, settings.audit_ip_key_bytes)
    app.state.ci_providers = ProviderClient(mock_ci.client())
    app.state.mock_ci = mock_ci
    register_error_handlers(app)
    app.include_router(make_admin_router())
    app.include_router(deploys.router)
    return app


@pytest.fixture
def mock_ci(app) -> MockCi:
    """The mock GitHub and Forgejo behind this app's ProviderClient."""
    return app.state.mock_ci


@pytest_asyncio.fixture
async def client(app):
    async with make_test_client(app) as client:
        yield client


@pytest_asyncio.fixture
async def data(factory) -> SimpleNamespace:
    """An admin, an active groepslid (lid-a), an active non-member (lid-b),
    and groep 'team' with site 'site'."""
    async with factory() as db:
        admin_member = Member(
            sso_subject="admin-sub",
            email="admin@example.nl",
            platform_role=PlatformRole.ADMIN,
            status=MemberStatus.ACTIVE,
        )
        member_a = Member(sso_subject="lid-a", email="a@example.nl", status=MemberStatus.ACTIVE)
        member_b = Member(sso_subject="lid-b", email="b@example.nl", status=MemberStatus.ACTIVE)
        group = Group(slug="team", name="Team", default_access_base=AccessBase.SITE_TEAM)
        db.add_all([admin_member, member_a, member_b, group])
        await db.flush()
        db.add(GroupMember(group_id=group.id, member_id=member_a.id, role=Role.ADMIN))
        site = Site(
            group_id=group.id,
            slug="site",
            title="Site",
            access_base=AccessBase.SITE_TEAM,
            created_by=member_a.id,
        )
        db.add(site)
        await db.commit()
        return SimpleNamespace(
            admin_member=admin_member, member_a=member_a, member_b=member_b, group=group, site=site
        )


def login(client, app, *, sub: str, email: str | None = None) -> dict[str, str]:
    """Sets the session and CSRF cookie and returns the matching mutation headers."""
    session = set_session_cookie(client, app, sub=sub, email=email or f"{sub}@example.nl")
    client.cookies.set(CSRF_COOKIE, session.csrf_token, domain="plak.example", path="/")
    return {CSRF_HEADER: session.csrf_token}


async def _join_group(factory, group, member, role: Role) -> None:
    """Puts a lid in the groep with this role; setup, never the thing tested."""
    async with factory() as db:
        db.add(GroupMember(group_id=group.id, member_id=member.id, role=role))
        await db.commit()


async def _join_site(factory, site, member, role: Role) -> None:
    async with factory() as db:
        db.add(SiteMember(site_id=site.id, member_id=member.id, role=role))
        await db.commit()


async def _new_member(factory, *, sub: str, email: str, name: str | None = None,
                      status: MemberStatus = MemberStatus.ACTIVE) -> Member:
    """An extra platformlid; setup, never the thing tested."""
    async with factory() as db:
        member = Member(sso_subject=sub, email=email, name=name, status=status)
        db.add(member)
        await db.commit()
        return member


async def _count(factory, model, **filters) -> int:
    async with factory() as db:
        stmt = select(func.count()).select_from(model)
        for name, value in filters.items():
            stmt = stmt.where(getattr(model, name) == value)
        return await db.scalar(stmt)


def _upload(name: str = "index.html", content: bytes = b"<h1>hoi</h1>"):
    return {"file": (name, content, "text/html")}


# -- Authorization ----------------------------------------------------------


class TestAuthorization:
    async def test_without_session_401_problem_json(self, client, data):
        response = await client.get(f"{BASE}/overview")
        assert response.status_code == 401
        assert response.headers["content-type"] == PROBLEM
        assert response.json()["status"] == 401

    async def test_a_401_always_names_its_reason(self, client, data):
        """Two dependencies check for a session; both must answer 401 with a
        code. The SPA should not have to tell one answer from the other by its
        wording."""
        read = await client.get(f"{BASE}/overview")
        write = await client.post(f"{BASE}/groups", json={"name": "Nieuw", "slug": "nieuw"})

        assert read.json()["code"] == "NO_SESSION"
        assert write.json()["code"] == "NO_SESSION"

    async def test_first_login_gets_access_no_approval_needed(self, client, app, data):
        login(client, app, sub="iemand-nieuw")
        response = await client.get(f"{BASE}/overview")
        assert response.status_code == 200

    async def test_deactivated_member_403(self, client, app, factory, data):
        async with factory() as db:
            db.add(Member(sso_subject="oud-lid", email="oud@example.nl", status=MemberStatus.DEACTIVATED))
            await db.commit()
        login(client, app, sub="oud-lid", email="oud@example.nl")
        assert (await client.get(f"{BASE}/me")).status_code == 403

    async def test_me_gives_active_member_back(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.get(f"{BASE}/me")
        assert response.status_code == 200
        assert response.json()["ssoSubject"] == "lid-a"
        assert response.json()["status"] == "active"

    async def test_me_gives_content_base_back(self, client, app, data):
        """The SPA builds public, secret and preview links on contentBaseUrl:
        the configured content host, never the admin host it is talking to."""
        login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.get(f"{BASE}/me")
        assert response.json()["contentBaseUrl"] == CONTENT_BASE_URL

        app.state.settings = app.state.settings.model_copy(
            update={"content_base_url": "https://sites.plak.example/"}
        )
        response = await client.get(f"{BASE}/me")
        assert response.json()["contentBaseUrl"] == "https://sites.plak.example"

    async def test_not_group_member_may_no_site_create(self, client, app, data):
        headers = login(client, app, sub="lid-b", email="b@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/sites",
            json={"title": "Nieuw", "slug": "nieuw"},
            headers=headers,
        )
        assert response.status_code == 403

    async def test_non_group_member_does_not_see_group_detail(self, client, app, data):
        login(client, app, sub="lid-b", email="b@example.nl")
        assert (await client.get(f"{BASE}/groups/team")).status_code == 403

    async def test_non_group_member_does_not_see_site_resources(self, client, app, data):
        """A 404, not a 403: without a role the answer may not give away that
        this site exists. See _site_with_role."""
        login(client, app, sub="lid-b", email="b@example.nl")
        for path in ("invitees", "keys", "repository", "versions", "previews"):
            response = await client.get(f"{BASE}/sites/team/site/{path}")
            assert response.status_code == 404, path
            assert response.json()["code"] == "UNKNOWN_SITE", path

    async def test_a_root_level_spa_page_is_no_group_slug(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups", json={"name": "Koppelen", "slug": "cli-link"}, headers=headers
        )
        assert response.status_code == 422
        assert response.json()["code"] == "SLUG_INVALID"

    async def test_every_active_member_may_a_group_create(self, client, app, data):
        """Creating a groep is not a reserved action: whoever wants to publish
        something has to be able to make a place for it themselves. The creator
        becomes a member at once, so they can put a site in it at once."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups", json={"name": "Eigen groep", "slug": "eigen"}, headers=headers
        )
        assert response.status_code == 201

        site = await client.post(
            f"{BASE}/groups/eigen/sites",
            json={"title": "Site", "slug": "site"},
            headers=headers,
        )
        assert site.status_code == 201

    async def test_group_create_requires_a_active_member(self, client, app, data):
        response = await client.post(f"{BASE}/groups", json={"name": "Nieuw", "slug": "nieuw"})
        assert response.status_code == 401

    async def test_group_create_creates_creator_group_member(self, client, app, data):
        """Whoever creates a groep is a member immediately and can create a
        site in it at once; without membership that would give a 403
        NOT_GROUP_MEMBER."""
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        response = await client.post(
            f"{BASE}/groups", json={"name": "Nieuw", "slug": "nieuw"}, headers=headers
        )
        assert response.status_code == 201

        members = (await client.get(f"{BASE}/groups/nieuw/members")).json()
        assert [member["identifier"] for member in members] == ["admin@example.nl"]

        response = await client.post(
            f"{BASE}/groups/nieuw/sites",
            json={"title": "Site", "slug": "site"},
            headers=headers,
        )
        assert response.status_code == 201
        assert response.json()["createdBy"] == str(data.admin_member.id)

        # Adding again is the ordinary duplicate, not a double membership.
        response = await client.post(
            f"{BASE}/groups/nieuw/members", json={"identifier": "admin@example.nl"}, headers=headers
        )
        assert response.status_code == 409

    async def test_platform_members_only_admin(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        assert (await client.get(f"{BASE}/platform/members")).status_code == 403

        login(client, app, sub="admin-sub", email="admin@example.nl")
        response = await client.get(f"{BASE}/platform/members")
        assert response.status_code == 200
        assert {member["ssoSubject"] for member in response.json()} >= {"admin-sub", "lid-a", "lid-b"}

    async def test_platform_members_carry_both_dates(self, client, app, data):
        """The platform page sorts on these two, so they have to be in the payload."""
        login(client, app, sub="admin-sub", email="admin@example.nl")
        members = (await client.get(f"{BASE}/platform/members")).json()
        admin = next(member for member in members if member["ssoSubject"] == "admin-sub")
        assert admin["createdAt"].endswith("Z")
        # Logging in is what fetched this list, so the admin has been seen.
        assert admin["lastLoginAt"].endswith("Z")

    async def test_deactivate_and_reactivate_by_admin(self, client, app, data):
        login(client, app, sub="iemand-nieuw")
        assert (await client.get(f"{BASE}/me")).status_code == 200

        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        members = (await client.get(f"{BASE}/platform/members")).json()
        new = next(member for member in members if member["ssoSubject"] == "iemand-nieuw")
        response = await client.post(
            f"{BASE}/platform/members/{new['id']}/_deactivate", headers=headers
        )
        assert response.status_code == 200
        assert response.json()["status"] == "deactivated"

        login(client, app, sub="iemand-nieuw")
        assert (await client.get(f"{BASE}/me")).status_code == 403

        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        response = await client.post(
            f"{BASE}/platform/members/{new['id']}/_activate", headers=headers
        )
        assert response.json()["status"] == "active"

    async def test_deactivating_revokes_every_cli_session_and_reactivating_revives_none(
        self, client, app, data, factory
    ):
        async def cli_login(member):
            async with factory() as db:
                created = await cli.create_device_authorization(db, client_name=None, ip_truncated=None)
            async with factory() as db:
                await cli.decide(db, created.user_code, await db.get(Member, member.id), approve=True)
            async with factory() as db:
                return await cli.exchange_device_code(db, created.device_code)

        first = await cli_login(data.member_a)
        second = await cli_login(data.member_a)
        bystander = await cli_login(data.member_b)

        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        response = await client.post(f"{BASE}/platform/members/{data.member_a.id}/_deactivate", headers=headers)
        assert response.status_code == 200
        response = await client.post(f"{BASE}/platform/members/{data.member_a.id}/_activate", headers=headers)
        assert response.status_code == 200

        async with factory() as db:
            remaining = set(await db.scalars(select(CliSession.id)))
            row = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "member_deactivate"))
        assert remaining == {bystander.session.id}
        assert first.session.id not in remaining and second.session.id not in remaining
        assert row.refs["cli_sessions_revoked"] == 2
        async with factory() as db:
            assert await cli.session_for_access_token(db, first.access_token) is None

    async def test_an_admin_may_not_deactivate_himself(self, client, app, data):
        """The admin API is the only way back in, so switching yourself off
        would lock the platform rather than only your own account."""
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        response = await client.post(
            f"{BASE}/platform/members/{data.admin_member.id}/_deactivate", headers=headers
        )
        assert response.status_code == 409
        assert response.json()["code"] == "SELF_NOT_ALLOWED"

    async def test_the_last_active_admin_stays(self, client, app, data, factory):
        # A second admin, so the first one may step down after all.
        async with factory() as db:
            second = Member(
                sso_subject="admin-twee",
                email="twee@example.nl",
                platform_role=PlatformRole.ADMIN,
                status=MemberStatus.ACTIVE,
            )
            db.add(second)
            await db.commit()
            second_id = second.id

        headers = login(client, app, sub="admin-twee", email="twee@example.nl")
        response = await client.post(
            f"{BASE}/platform/members/{data.admin_member.id}/_deactivate", headers=headers
        )
        assert response.status_code == 200

        # Now the second one is the only one left, and nobody can remove them.
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        assert (await client.get(f"{BASE}/me")).status_code == 403
        headers = login(client, app, sub="admin-twee", email="twee@example.nl")
        response = await client.put(
            f"{BASE}/platform/members/{second_id}/platform-role",
            json={"platformRole": "member"},
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["code"] == "SELF_NOT_ALLOWED"

    async def test_the_bootstrap_account_refuses_instead_of_silently_returning(
        self, client, app, data, factory
    ):
        """Without this the change looks like it worked and auth/members.py
        undoes it on the next request."""
        app.state.settings = app.state.settings.model_copy(update={"bootstrap_admin_sub": "admin-sub"})
        async with factory() as db:
            other = Member(
                sso_subject="admin-drie",
                email="drie@example.nl",
                platform_role=PlatformRole.ADMIN,
                status=MemberStatus.ACTIVE,
            )
            db.add(other)
            await db.commit()

        headers = login(client, app, sub="admin-drie", email="drie@example.nl")
        response = await client.post(
            f"{BASE}/platform/members/{data.admin_member.id}/_deactivate", headers=headers
        )
        assert response.status_code == 409
        assert response.json()["code"] == "BOOTSTRAP_MEMBER"

    async def test_an_admin_appoints_and_stands_down_another(self, client, app, data):
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")

        response = await client.put(
            f"{BASE}/platform/members/{data.member_a.id}/platform-role",
            json={"platformRole": "admin"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["platformRole"] == "admin"

        # And the new admin really may do admin things.
        headers_a = login(client, app, sub="lid-a", email="a@example.nl")
        assert (await client.get(f"{BASE}/platform/members")).status_code == 200

        response = await client.put(
            f"{BASE}/platform/members/{data.member_a.id}/platform-role",
            json={"platformRole": "member"},
            headers=headers_a,
        )
        assert response.status_code == 409, "je eigen rol afnemen mag niet"

        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        response = await client.put(
            f"{BASE}/platform/members/{data.member_a.id}/platform-role",
            json={"platformRole": "member"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["platformRole"] == "member"

    async def test_appointing_a_admin_is_for_admins_only(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(
            f"{BASE}/platform/members/{data.member_b.id}/platform-role",
            json={"platformRole": "admin"},
            headers=headers,
        )
        assert response.status_code == 403
        assert response.json()["code"] == "NOT_ADMIN"

    async def test_activate_by_plain_member_403(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/platform/members/{data.member_b.id}/_activate", headers=headers
        )
        assert response.status_code == 403

    async def test_an_unknown_member_id_is_404_on_activate_deactivate_and_role(
        self, client, app, data
    ):
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        unknown = uuid.uuid4()

        activate = await client.post(f"{BASE}/platform/members/{unknown}/_activate", headers=headers)
        assert activate.status_code == 404
        assert activate.json()["code"] == "UNKNOWN_MEMBER"

        deactivate = await client.post(f"{BASE}/platform/members/{unknown}/_deactivate", headers=headers)
        assert deactivate.status_code == 404
        assert deactivate.json()["code"] == "UNKNOWN_MEMBER"

        role = await client.put(
            f"{BASE}/platform/members/{unknown}/platform-role",
            json={"platformRole": "admin"},
            headers=headers,
        )
        assert role.status_code == 404
        assert role.json()["code"] == "UNKNOWN_MEMBER"


# -- My own account ---------------------------------------------------------


class TestMyLanguage:
    """The interface language a member picks for themselves. It hangs on the
    account rather than on the browser, so it has to survive a login from
    another device."""

    async def test_a_fresh_member_has_no_language_of_their_own(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        assert (await client.get(f"{BASE}/me")).json()["language"] is None

    async def test_setting_a_language_shows_up_on_me(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(f"{BASE}/me/language", json={"language": "en"}, headers=headers)
        assert response.status_code == 204
        assert (await client.get(f"{BASE}/me")).json()["language"] == "en"

    async def test_null_hands_the_choice_back_to_the_browser(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        await client.put(f"{BASE}/me/language", json={"language": "en"}, headers=headers)
        response = await client.put(f"{BASE}/me/language", json={"language": None}, headers=headers)
        assert response.status_code == 204
        assert (await client.get(f"{BASE}/me")).json()["language"] is None

    async def test_an_unsupported_language_is_refused(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(f"{BASE}/me/language", json={"language": "fr"}, headers=headers)
        assert response.status_code == 422
        assert (await client.get(f"{BASE}/me")).json()["language"] is None

    async def test_without_csrf_header_403(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(f"{BASE}/me/language", json={"language": "en"})
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_INVALID"

    async def test_no_session_401(self, client, data):
        response = await client.put(f"{BASE}/me/language", json={"language": "en"})
        assert response.status_code in (401, 403)

    async def test_a_deactivated_member_may_not_set_it(self, client, app, factory, data):
        async with factory() as db:
            db.add(Member(sso_subject="oud-lid", email="oud@example.nl", status=MemberStatus.DEACTIVATED))
            await db.commit()
        headers = login(client, app, sub="oud-lid", email="oud@example.nl")
        response = await client.put(f"{BASE}/me/language", json={"language": "en"}, headers=headers)
        assert response.status_code == 403

    async def test_the_choice_is_audited_with_its_value(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        recorder = install_audit_recorder(app)
        await client.put(f"{BASE}/me/language", json={"language": "en"}, headers=headers)
        record = recorder.only()
        assert record.action == "member_language"
        assert record.refs == {"language": "en"}

    async def test_handing_the_choice_back_is_audited_too(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        recorder = install_audit_recorder(app)
        await client.put(f"{BASE}/me/language", json={"language": None}, headers=headers)
        assert recorder.only().refs == {"language": None}

    async def test_one_member_does_not_change_another_members_language(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        await client.put(f"{BASE}/me/language", json={"language": "en"}, headers=headers)
        login(client, app, sub="lid-b", email="b@example.nl")
        assert (await client.get(f"{BASE}/me")).json()["language"] is None


# -- CSRF -------------------------------------------------------------------


class TestCsrf:
    async def test_mutation_without_header_403(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "X", "slug": "x"}
        )
        assert response.status_code == 403
        assert response.headers["content-type"] == PROBLEM

    async def test_mutation_with_wrong_header_403(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/sites",
            json={"title": "X", "slug": "x"},
            headers={CSRF_HEADER: "vervalst"},
        )
        assert response.status_code == 403

    async def test_mutation_with_correct_header_succeeds(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "X", "slug": "x"}, headers=headers
        )
        assert response.status_code == 201

    async def test_read_requires_no_csrf_header(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        assert (await client.get(f"{BASE}/overview")).status_code == 200

    async def test_csrf_fails_before_member_becomes_created(self, client, app, factory, data):
        """A forged request without a CSRF header must not upsert a lid record."""
        set_session_cookie(client, app, sub="spook-sub", email="spook@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "X", "slug": "x"}
        )
        assert response.status_code == 403
        assert await _count(factory, Member, sso_subject="spook-sub") == 0


# -- Origin guarding on the admin API --------------------------------------


class TestOriginGuard:
    async def test_cross_origin_refused_also_with_valid_session(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.get(
            f"{BASE}/overview", headers={**headers, "Origin": "https://evil.example"}
        )
        assert response.status_code == 403
        assert response.headers["content-type"] == PROBLEM
        assert "access-control-allow-origin" not in response.headers

    async def test_without_origin_with_cross_site_fetch_refused(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.get(
            f"{BASE}/overview", headers={"Sec-Fetch-Site": "cross-site"}
        )
        assert response.status_code == 403

    async def test_same_origin_allowed(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.get(f"{BASE}/overview", headers={"Origin": APP_BASE_URL})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers


# -- Groups and sites ----------------------------------------------------


class TestGroupsAndSites:
    async def test_group_create_validates_slug(self, client, app, data):
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        # "beheer" and "admin" are ordinary slugs now that the SPA sits at the
        # root of its own host; what stays reserved is what the web claims.
        for slug in ("robots.txt", "Hoofdletters", "-x", "_x"):
            response = await client.post(
                f"{BASE}/groups", json={"name": "N", "slug": slug}, headers=headers
            )
            assert response.status_code == 422, slug

    @pytest.mark.parametrize(
        "bad_text",
        [
            "Team\x00stil",
            "Team\nnieuw",
            "Team\tinsprong",
            "Team ‮gedraaid",
        ],
    )
    async def test_group_name_and_site_title_refuse_control_characters(
        self, client, app, data, bad_text
    ):
        """A name is shown back in lists and in the confirmation modals of
        remove actions. A U+202E reverses the reading order of everything
        after it, so the name under a "remove X?" can read as another."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        group = await client.post(
            f"{BASE}/groups", json={"name": bad_text, "slug": "stuurteken"}, headers=headers
        )
        assert group.status_code == 422
        assert group.json()["code"] == "FIELD_CONTROL_CHARACTERS"

        site = await client.post(
            f"{BASE}/groups/team/sites", json={"title": bad_text, "slug": "stuurteken"}, headers=headers
        )
        assert site.status_code == 422
        assert site.json()["code"] == "FIELD_CONTROL_CHARACTERS"

    async def test_group_name_and_site_title_refuse_blank_text(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        group = await client.post(
            f"{BASE}/groups", json={"name": "   ", "slug": "leeg"}, headers=headers
        )
        assert group.status_code == 422
        assert group.json()["code"] == "FIELD_EMPTY"

        site = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "  ", "slug": "leeg"}, headers=headers
        )
        assert site.status_code == 422
        assert site.json()["code"] == "FIELD_EMPTY"

    async def test_group_slug_duplicate_409(self, client, app, data):
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        response = await client.post(
            f"{BASE}/groups", json={"name": "Team 2", "slug": "team"}, headers=headers
        )
        assert response.status_code == 409

    async def test_409_conflicts_distinguishable_via_code(self, client, app, data):
        """Two different 409s each carry their own stable `code`."""
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        duplicate = await client.post(
            f"{BASE}/groups", json={"name": "Team 2", "slug": "team"}, headers=headers
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["type"] == "about:blank"
        assert duplicate.json()["code"] == "SLUG_EXISTS"

        not_empty = await client.delete(f"{BASE}/groups/team", headers=headers)
        assert not_empty.status_code == 409
        assert not_empty.json()["code"] == "GROUP_NOT_EMPTY"

    async def test_overview_shows_only_own_groups(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        overview = (await client.get(f"{BASE}/overview")).json()
        assert [group["group"]["slug"] for group in overview["groups"]] == ["team"]
        sites = overview["groups"][0]["sites"]
        assert [site["slug"] for site in sites] == ["site"]
        assert sites[0]["hasLiveVersion"] is False

        login(client, app, sub="lid-b", email="b@example.nl")
        overview = (await client.get(f"{BASE}/overview")).json()
        assert overview["groups"] == []

    async def test_admin_sees_all_groups(self, client, app, data):
        login(client, app, sub="admin-sub", email="admin@example.nl")
        overview = (await client.get(f"{BASE}/overview")).json()
        assert [group["group"]["slug"] for group in overview["groups"]] == ["team"]

    async def test_admin_sees_a_group_without_any_sites(self, client, app, data, factory):
        async with factory() as db:
            db.add(Group(slug="leeg", name="Leeg", default_access_base=AccessBase.SITE_TEAM))
            await db.commit()
        login(client, app, sub="admin-sub", email="admin@example.nl")

        overview = (await client.get(f"{BASE}/overview")).json()

        empty_group = next(group for group in overview["groups"] if group["group"]["slug"] == "leeg")
        assert empty_group["sites"] == []

    async def test_a_group_role_covers_a_site_a_site_role_also_names(
        self, client, app, data, factory
    ):
        """member_a already sees the whole groep through its group role; a
        siterol on a site already inside that groep adds nothing and must not
        list the site twice."""
        await _join_site(factory, data.site, data.member_a, Role.EDITOR)
        login(client, app, sub="lid-a", email="a@example.nl")

        overview = (await client.get(f"{BASE}/overview")).json()

        assert [group["group"]["slug"] for group in overview["groups"]] == ["team"]
        assert [site["slug"] for site in overview["groups"][0]["sites"]] == ["site"]

    async def test_group_detail_contains_members_and_sites(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        detail = (await client.get(f"{BASE}/groups/team")).json()
        assert detail["group"]["slug"] == "team"
        assert [site["slug"] for site in detail["sites"]] == ["site"]
        assert [member["identifier"] for member in detail["members"]] == ["a@example.nl"]
        assert "tokens" not in detail

    async def test_unknown_group_404(self, client, app, data):
        login(client, app, sub="admin-sub", email="admin@example.nl")
        assert (await client.get(f"{BASE}/groups/bestaat-niet")).status_code == 404

    async def test_default_access_set_and_inherit(self, client, app, data):
        """A new site inherits the extras as well as the base, or the group
        default would only ever be half a setting."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(
            f"{BASE}/groups/team/default-access",
            json={"base": "nobody", "keys": True, "invitees": False},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["defaultAccess"] == {
            "base": "nobody",
            "keys": True,
            "invitees": False,
        }

        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "Erft", "slug": "erft"}, headers=headers
        )
        assert response.json()["access"] == {"base": "nobody", "keys": True, "invitees": False}

    async def test_invalid_access_base_422(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(
            f"{BASE}/sites/team/site/access",
            json={"base": "geheim"},
            headers=headers,
        )
        assert response.status_code == 422
        assert response.headers["content-type"] == PROBLEM

    async def test_site_access_set_with_both_extras(self, client, app, data, factory):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(
            f"{BASE}/sites/team/site/access",
            json={"base": "sso", "keys": True, "invitees": True},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["access"] == {"base": "sso", "keys": True, "invitees": True}

        async with factory() as db:
            row = await db.scalar(
                select(AuditLogEntry).where(AuditLogEntry.action == "site_visibility")
            )
        assert row is not None
        assert row.refs["base"] == "sso"
        assert row.refs["keys"] is True
        assert row.refs["invitees"] is True

    async def test_site_access_extras_default_to_off(self, client, app, data):
        """Leaving them out turns them off rather than keeping what stood
        there: one PUT sets the whole policy, so a missing field is a choice."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        await client.put(
            f"{BASE}/sites/team/site/access",
            json={"base": "nobody", "keys": True, "invitees": True},
            headers=headers,
        )
        response = await client.put(
            f"{BASE}/sites/team/site/access", json={"base": "public"}, headers=headers
        )
        assert response.json()["access"] == {"base": "public", "keys": False, "invitees": False}

    async def test_external_sources_default_on_and_switchable(self, client, app, data, factory):
        """Aan, tenzij je het uitzet: a site that never touches this setting
        may load from the fixed list of hosts."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        group = await client.get(f"{BASE}/groups/team")
        site_row = next(p for p in group.json()["sites"] if p["slug"] == "site")
        assert site_row["externalSources"] is True

        response = await client.put(
            f"{BASE}/sites/team/site/external-sources",
            json={"externalSources": False},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["externalSources"] is False

        async with factory() as db:
            row = await db.scalar(
                select(AuditLogEntry).where(AuditLogEntry.action == "site_external_sources")
            )
        assert row is not None
        assert row.result == "allowed"
        assert row.refs["site"] == "site"
        assert row.refs["external_sources"] is False

        back = await client.put(
            f"{BASE}/sites/team/site/external-sources",
            json={"externalSources": True},
            headers=headers,
        )
        assert back.json()["externalSources"] is True

    async def test_external_sources_needs_the_admin_role(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-b", email="b@example.nl")
        refused = await client.put(
            f"{BASE}/sites/team/site/external-sources",
            json={"externalSources": True},
            headers=headers,
        )
        assert refused.status_code == 403
        assert refused.json()["code"] == "INSUFFICIENT_ROLE"

    async def test_external_sources_needs_csrf(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        refused = await client.put(
            f"{BASE}/sites/team/site/external-sources", json={"externalSources": True}
        )
        assert refused.status_code == 403

    async def test_sandbox_default_on_and_switchable(self, client, app, data, factory):
        """The safe value is what a site gets for free; the switch is what
        gives it up, for a site that needs browser storage."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        group = await client.get(f"{BASE}/groups/team")
        site_row = next(p for p in group.json()["sites"] if p["slug"] == "site")
        assert site_row["sandbox"] is True

        response = await client.put(
            f"{BASE}/sites/team/site/sandbox", json={"sandbox": False}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["sandbox"] is False

        async with factory() as db:
            row = await db.scalar(
                select(AuditLogEntry).where(AuditLogEntry.action == "site_sandbox")
            )
        assert row is not None
        assert row.result == "allowed"
        assert row.refs["site"] == "site"
        assert row.refs["sandbox"] is False

        back = await client.put(
            f"{BASE}/sites/team/site/sandbox", json={"sandbox": True}, headers=headers
        )
        assert back.json()["sandbox"] is True

    async def test_sandbox_needs_the_admin_role(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-b", email="b@example.nl")
        refused = await client.put(
            f"{BASE}/sites/team/site/sandbox", json={"sandbox": False}, headers=headers
        )
        assert refused.status_code == 403
        assert refused.json()["code"] == "INSUFFICIENT_ROLE"

    async def test_sandbox_needs_csrf(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        refused = await client.put(f"{BASE}/sites/team/site/sandbox", json={"sandbox": False})
        assert refused.status_code == 403

    async def test_site_slug_duplicate_409(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "Dubbel", "slug": "site"}, headers=headers
        )
        assert response.status_code == 409

    async def test_group_delete_only_empty_and_only_admin(self, client, app, data):
        headers_member = login(client, app, sub="lid-a", email="a@example.nl")
        assert (
            await client.delete(f"{BASE}/groups/team", headers=headers_member)
        ).status_code == 403

        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        assert (await client.delete(f"{BASE}/groups/team", headers=headers)).status_code == 409

        headers_member = login(client, app, sub="lid-a", email="a@example.nl")
        assert (
            await client.delete(f"{BASE}/sites/team/site", headers=headers_member)
        ).status_code == 204

        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        assert (await client.delete(f"{BASE}/groups/team", headers=headers)).status_code == 204
        assert (await client.get(f"{BASE}/groups/team")).status_code == 404


# -- Group members ----------------------------------------------------------


class TestGroupMembers:
    async def test_add_and_delete(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "B@Example.NL"}, headers=headers
        )
        assert response.status_code == 201
        assert response.json()["identifier"] == "b@example.nl"

        members = (await client.get(f"{BASE}/groups/team/members")).json()
        assert [member["identifier"] for member in members] == ["a@example.nl", "b@example.nl"]

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_b.id}", headers=headers
        )
        assert response.status_code == 204
        members = (await client.get(f"{BASE}/groups/team/members")).json()
        assert [member["identifier"] for member in members] == ["a@example.nl"]

    async def test_add_by_an_ambiguous_email_is_409(self, client, app, factory, data):
        """email has no unique constraint on members; a lookup that falls
        back to it can genuinely match more than one person."""
        async with factory() as db:
            db.add(Member(sso_subject="dubbel-x", email="dubbel@example.nl", status=MemberStatus.ACTIVE))
            db.add(Member(sso_subject="dubbel-y", email="dubbel@example.nl", status=MemberStatus.ACTIVE))
            await db.commit()
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "dubbel@example.nl"}, headers=headers
        )
        assert response.status_code == 409
        assert response.json()["code"] == "IDENTIFIER_AMBIGUOUS"

    async def test_add_by_the_exact_sso_subject_is_never_ambiguous(self, client, app, factory, data):
        """Even with a duplicate email in play, the exact sub still resolves
        (sso_subject is unique)."""
        async with factory() as db:
            db.add(Member(sso_subject="dubbel-x", email="dubbel@example.nl", status=MemberStatus.ACTIVE))
            db.add(Member(sso_subject="dubbel-y", email="dubbel@example.nl", status=MemberStatus.ACTIVE))
            await db.commit()
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "dubbel-x"}, headers=headers
        )
        assert response.status_code == 201

    async def test_add_by_name_resolves(self, client, app, factory, data):
        """Neither sso_subject nor e-mail matches, but the name matches exactly
        one active member: that member is added."""
        await _new_member(factory, sub="lid-c", email="c@example.nl", name="Cato Jansen")
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "  Cato Jansen  "}, headers=headers
        )
        assert response.status_code == 201
        assert response.json()["identifier"] == "c@example.nl"

    async def test_add_by_name_is_case_insensitive(self, client, app, factory, data):
        await _new_member(factory, sub="lid-c", email="c@example.nl", name="Cato Jansen")
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "cato jansen"}, headers=headers
        )
        assert response.status_code == 201
        assert response.json()["identifier"] == "c@example.nl"

    async def test_add_by_an_ambiguous_name_is_409(self, client, app, factory, data):
        await _new_member(factory, sub="lid-c", email="c@example.nl", name="Cato Jansen")
        await _new_member(factory, sub="lid-d", email="d@example.nl", name="Cato Jansen")
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "Cato Jansen"}, headers=headers
        )
        assert response.status_code == 409
        assert response.json()["code"] == "IDENTIFIER_AMBIGUOUS"
        # English: the request asks for no language of its own.
        assert "e-mail address" in response.json()["detail"]

    async def test_add_by_the_name_of_a_deactivated_member_does_not_resolve(
        self, client, app, factory, data
    ):
        """A deactivated member is refused everywhere else the API touches
        them; resolving one by name would be a backdoor around that."""
        await _new_member(
            factory, sub="lid-c", email="c@example.nl", name="Cato Jansen", status=MemberStatus.DEACTIVATED
        )
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "Cato Jansen"}, headers=headers
        )
        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_MEMBER"

    async def test_email_and_sso_subject_take_precedence_over_name(self, client, app, factory, data):
        """A name that happens to equal someone else's e-mail address or
        SSO-subject never shadows the exact match on those."""
        await _new_member(factory, sub="lid-c", email="c@example.nl", name="b@example.nl")
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "b@example.nl"}, headers=headers
        )
        assert response.status_code == 201
        assert response.json()["identifier"] == "b@example.nl"

    async def test_last_member_can_there_not_from(self, client, app, data):
        """A groep without members is unmanageable: only someone who is in it may
        add people, so removing the last member makes the groep unrecoverable."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_a.id}", headers=headers
        )
        assert response.status_code == 409
        assert response.json()["code"] == "LAST_GROUP_MEMBER"

        members = (await client.get(f"{BASE}/groups/team/members")).json()
        assert [member["identifier"] for member in members] == ["a@example.nl"]

    async def test_last_member_can_there_does_from_as_soon_as_there_a_second_is(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        assert (
            await client.post(
                f"{BASE}/groups/team/members",
                json={"identifier": "b@example.nl", "role": "admin"},
                headers=headers,
            )
        ).status_code == 201

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_a.id}", headers=headers
        )
        assert response.status_code == 204

        # a is no longer a member and cannot add themselves back; b can.
        headers_b = login(client, app, sub="lid-b", email="b@example.nl")
        members = (await client.get(f"{BASE}/groups/team/members", headers=headers_b)).json()
        assert [member["identifier"] for member in members] == ["b@example.nl"]

    async def test_admin_may_members_manage_without_membership(self, client, app, data):
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "b@example.nl"}, headers=headers
        )
        assert response.status_code == 201

    async def test_unknown_member_404_and_duplicate_409(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "bestaat-niet@example.nl"}, headers=headers
        )
        assert response.status_code == 404

        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "a@example.nl"}, headers=headers
        )
        assert response.status_code == 409

    async def test_an_unverified_email_does_not_resolve_to_a_member(self, client, app, data, factory):
        """Someone whose IdP profile carries an unverified alice@ visits admin
        before Alice does: adding "alice@" must not pick that person."""
        set_session_cookie(client, app, sub="aanvaller", email="alice@example.nl", email_verified=False)
        assert (await client.get(f"{BASE}/me")).status_code == 200

        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "alice@example.nl"}, headers=headers
        )
        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_MEMBER"

        set_session_cookie(client, app, sub="alice", email="alice@example.nl", email_verified=True)
        assert (await client.get(f"{BASE}/me")).status_code == 200
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "alice@example.nl"}, headers=headers
        )
        assert response.status_code == 201
        async with factory() as db:
            added = await db.get(Member, uuid.UUID(response.json()["memberId"]))
        assert added.sso_subject == "alice"

    async def test_removal_by_id_is_404_for_an_unknown_id_and_a_non_member(
        self, client, app, data
    ):
        """The id in the path is not a licence: it still has to belong to a
        member of this group."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        unknown = await client.delete(
            f"{BASE}/groups/team/members/{uuid.uuid4()}", headers=headers
        )
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "UNKNOWN_MEMBER"

        outsider = await client.delete(
            f"{BASE}/groups/team/members/{data.member_b.id}", headers=headers
        )
        assert outsider.status_code == 404
        assert outsider.json()["code"] == "NOT_GROUP_MEMBER"

    async def test_not_group_member_may_no_members_manage(self, client, app, data):
        headers = login(client, app, sub="lid-b", email="b@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "b@example.nl"}, headers=headers
        )
        assert response.status_code == 403

    async def test_the_role_stands_in_the_payload(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        members = (await client.get(f"{BASE}/groups/team/members")).json()
        assert [member["role"] for member in members] == ["admin"]
        detail = (await client.get(f"{BASE}/groups/team")).json()
        assert [member["role"] for member in detail["members"]] == ["admin"]

    async def test_who_is_added_becomes_reader(self, client, app, data):
        """Whoever joins looks on first; the wider role is given on purpose."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "b@example.nl"}, headers=headers
        )
        assert response.status_code == 201
        assert response.json()["role"] == "reader"

    async def test_a_role_can_be_given_along(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/members",
            json={"identifier": "b@example.nl", "role": "editor"},
            headers=headers,
        )
        assert response.status_code == 201
        assert response.json()["role"] == "editor"

    async def test_role_change_adjusts_what_someone_may(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.READER)
        headers_b = login(client, app, sub="lid-b", email="b@example.nl")
        assert (
            await client.post(
                f"{BASE}/groups/team/sites", json={"title": "X", "slug": "x"}, headers=headers_b
            )
        ).status_code == 403

        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(
            f"{BASE}/groups/team/members/{data.member_b.id}/role",
            json={"role": "editor"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["role"] == "editor"

        headers_b = login(client, app, sub="lid-b", email="b@example.nl")
        assert (
            await client.post(
                f"{BASE}/groups/team/sites", json={"title": "X", "slug": "x"}, headers=headers_b
            )
        ).status_code == 201

    async def test_role_change_only_for_group_admins(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.EDITOR)
        headers_b = login(client, app, sub="lid-b", email="b@example.nl")
        response = await client.put(
            f"{BASE}/groups/team/members/{data.member_b.id}/role",
            json={"role": "admin"},
            headers=headers_b,
        )
        assert response.status_code == 403
        assert response.json()["code"] == "INSUFFICIENT_ROLE"

    async def test_role_change_unknown_member_and_outsider_404(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(
            f"{BASE}/groups/team/members/{uuid.uuid4()}/role",
            json={"role": "editor"},
            headers=headers,
        )
        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_MEMBER"

        response = await client.put(
            f"{BASE}/groups/team/members/{data.member_b.id}/role",
            json={"role": "editor"},
            headers=headers,
        )
        assert response.status_code == 404
        assert response.json()["code"] == "NOT_GROUP_MEMBER"

    async def test_the_last_group_admin_cannot_be_demoted(self, client, app, data, factory):
        """The database trigger refuses this; the API has to answer 409 rather
        than let a 500 through."""
        await _join_group(factory, data.group, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(
            f"{BASE}/groups/team/members/{data.member_a.id}/role",
            json={"role": "editor"},
            headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["code"] == "LAST_GROUP_ADMIN"

        members = (await client.get(f"{BASE}/groups/team/members")).json()
        assert {member["identifier"]: member["role"] for member in members} == {
            "a@example.nl": "admin",
            "b@example.nl": "editor",
        }

    async def test_the_last_group_admin_cannot_be_removed(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.READER)
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_a.id}", headers=headers
        )
        assert response.status_code == 409
        assert response.json()["code"] == "LAST_GROUP_ADMIN"

    async def test_a_different_database_error_during_a_role_change_is_not_swallowed(
        self, client, app, data, monkeypatch
    ):
        """`_group_keeps_an_admin` only turns the ck_groups_keep_one_admin
        trigger's own sqlstate into LAST_GROUP_ADMIN; anything else has to
        keep propagating rather than being read as the same refusal."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        class _FakeOrigError(Exception):
            sqlstate = "40001"

        original_commit = AsyncSession.commit
        calls = {"n": 0}

        async def _boom(self, *args, **kwargs):
            calls["n"] += 1
            # The first commit on this request is require_active_member's own
            # login bookkeeping (auth/members.py), not the role change under
            # test: only the second, inside `_group_keeps_an_admin`, fails.
            if calls["n"] == 1:
                return await original_commit(self, *args, **kwargs)
            raise DBAPIError("UPDATE", {}, _FakeOrigError())

        monkeypatch.setattr(AsyncSession, "commit", _boom)

        with pytest.raises(DBAPIError):
            await client.put(
                f"{BASE}/groups/team/members/{data.member_a.id}/role",
                json={"role": "reader"},
                headers=headers,
            )

    async def test_demoting_yourself_may_as_long_as_there_a_other_admin_is(
        self, client, app, data, factory
    ):
        """Unlike the platform role this is no lockout: the other admin can
        put you back, so there is no guard against it."""
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(
            f"{BASE}/groups/team/members/{data.member_a.id}/role",
            json={"role": "reader"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["role"] == "reader"


class TestGroupMemberSiteRoles:
    """The site roles a group member holds inside this group: what the members
    list reports, and what the removal can take along.

    The boundary being tested throughout is the group in the path. A role on a
    site in another group says where else in the organisation this person
    works, and an admin of this group may neither read nor touch it here.
    """

    @pytest_asyncio.fixture
    async def elsewhere(self, factory, data) -> SimpleNamespace:
        """A second group with a site of its own, where member_a holds a site
        role that has nothing to do with groep 'team'."""
        async with factory() as db:
            group = Group(slug="ander", name="Ander", default_access_base=AccessBase.SITE_TEAM)
            db.add(group)
            await db.flush()
            site = Site(
                group_id=group.id,
                slug="elders",
                title="Elders",
                access_base=AccessBase.SITE_TEAM,
                created_by=data.member_a.id,
            )
            db.add(site)
            await db.flush()
            db.add(SiteMember(site_id=site.id, member_id=data.member_a.id, role=Role.ADMIN))
            await db.commit()
            return SimpleNamespace(group=group, site=site)

    async def test_the_members_list_carries_the_site_roles_in_this_group(
        self, client, app, data, factory
    ):
        await _join_site(factory, data.site, data.member_a, Role.EDITOR)
        login(client, app, sub="lid-a", email="a@example.nl")

        members = (await client.get(f"{BASE}/groups/team/members")).json()

        assert members[0]["siteRoles"] == [
            {"siteSlug": "site", "siteTitle": "Site", "role": "editor"}
        ]

    async def test_without_a_site_role_the_list_says_so_with_an_empty_list(
        self, client, app, data
    ):
        login(client, app, sub="lid-a", email="a@example.nl")

        members = (await client.get(f"{BASE}/groups/team/members")).json()

        assert members[0]["siteRoles"] == []

    async def test_a_site_role_in_another_group_stays_out_of_this_list(
        self, client, app, data, elsewhere
    ):
        """The whole point: an admin of groep 'team' may not learn from this
        screen that this person also works somewhere else."""
        login(client, app, sub="lid-a", email="a@example.nl")

        members = (await client.get(f"{BASE}/groups/team/members")).json()

        assert members[0]["siteRoles"] == []

    async def test_adding_and_a_role_change_report_the_same_site_roles(
        self, client, app, data, factory
    ):
        """One row out of the add and the role change, built the same way the
        listing builds it, so the answer cannot disagree with the list."""
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        added = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": "b@example.nl"}, headers=headers
        )
        assert added.json()["siteRoles"] == [
            {"siteSlug": "site", "siteTitle": "Site", "role": "editor"}
        ]

        changed = await client.put(
            f"{BASE}/groups/team/members/{data.member_b.id}/role",
            json={"role": "editor"},
            headers=headers,
        )
        assert changed.json()["siteRoles"] == [
            {"siteSlug": "site", "siteTitle": "Site", "role": "editor"}
        ]

    async def test_removal_keeps_the_site_roles_by_default(self, client, app, data, factory):
        """Taking more than was asked has to be asked for: no parameter means
        the site role stands, and this person can still reach that site."""
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_b.id}", headers=headers
        )

        assert response.status_code == 204
        assert await _count(factory, SiteMember, member_id=data.member_b.id) == 1

    async def test_removal_takes_the_site_roles_along_when_asked(
        self, client, app, data, factory
    ):
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_b.id}?siteRoles=remove", headers=headers
        )

        assert response.status_code == 204
        assert await _count(factory, SiteMember, member_id=data.member_b.id) == 0
        assert await _count(factory, GroupMember, member_id=data.member_b.id) == 0

    async def test_a_site_role_in_another_group_is_not_touched(
        self, client, app, data, factory, elsewhere
    ):
        """The other half of the boundary: `siteRoles=remove` reaches only the
        sites of the group in the path."""
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        await _join_site(factory, elsewhere.site, data.member_b, Role.EDITOR)
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_b.id}?siteRoles=remove", headers=headers
        )

        assert response.status_code == 204
        async with factory() as db:
            remaining = list(
                await db.scalars(
                    select(SiteMember.site_id).where(SiteMember.member_id == data.member_b.id)
                )
            )
        assert remaining == [elsewhere.site.id]

    async def test_every_removed_site_role_writes_the_audit_row_of_the_site_screen(
        self, client, app, data, factory
    ):
        """Same action and the same refs as `DELETE /sites/{groep}/{site}/members`:
        one vocabulary, whichever screen the removal came from."""
        async with factory() as db:
            second = Site(
                group_id=data.group.id,
                slug="tweede",
                title="Tweede",
                access_base=AccessBase.SITE_TEAM,
                created_by=data.member_a.id,
            )
            db.add(second)
            await db.commit()
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        await _join_site(factory, second, data.member_b, Role.READER)
        recorder = install_audit_recorder(app)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        await client.delete(
            f"{BASE}/groups/team/members/{data.member_b.id}?siteRoles=remove", headers=headers
        )

        assert [(record.action, record.refs) for record in recorder.records] == [
            ("group_member_remove", {"group": "team", "member_id": str(data.member_b.id)}),
            (
                "site_member_remove",
                {"group": "team", "site": "site", "member_id": str(data.member_b.id)},
            ),
            (
                "site_member_remove",
                {"group": "team", "site": "tweede", "member_id": str(data.member_b.id)},
            ),
        ]

    async def test_keeping_them_writes_no_site_audit_rows(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        recorder = install_audit_recorder(app)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        await client.delete(f"{BASE}/groups/team/members/{data.member_b.id}", headers=headers)

        assert [record.action for record in recorder.records] == ["group_member_remove"]

    async def test_the_last_admin_refusal_leaves_the_site_roles_standing(
        self, client, app, data, factory
    ):
        """The one way this can half-happen, and it does not: the trigger fires
        inside the same transaction as the site roles, so a refusal takes the
        whole thing back."""
        await _join_group(factory, data.group, data.member_b, Role.READER)
        await _join_site(factory, data.site, data.member_a, Role.EDITOR)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_a.id}?siteRoles=remove", headers=headers
        )

        assert response.status_code == 409
        assert response.json()["code"] == "LAST_GROUP_ADMIN"
        assert await _count(factory, SiteMember, member_id=data.member_a.id) == 1
        assert await _count(factory, GroupMember, member_id=data.member_a.id) == 1

    async def test_the_last_member_refusal_leaves_the_site_roles_standing(
        self, client, app, data, factory
    ):
        await _join_site(factory, data.site, data.member_a, Role.EDITOR)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_a.id}?siteRoles=remove", headers=headers
        )

        assert response.status_code == 409
        assert response.json()["code"] == "LAST_GROUP_MEMBER"
        assert await _count(factory, SiteMember, member_id=data.member_a.id) == 1

    async def test_someone_who_is_no_group_member_keeps_their_site_role(
        self, client, app, data, factory
    ):
        """A 404 is not a licence to take the site role: the membership is
        checked before anything is deleted."""
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_b.id}?siteRoles=remove", headers=headers
        )

        assert response.status_code == 404
        assert response.json()["code"] == "NOT_GROUP_MEMBER"
        assert await _count(factory, SiteMember, member_id=data.member_b.id) == 1

    async def test_asking_for_them_when_there_are_none_is_an_ordinary_removal(
        self, client, app, data, factory
    ):
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        recorder = install_audit_recorder(app)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_b.id}?siteRoles=remove", headers=headers
        )

        assert response.status_code == 204
        assert [record.action for record in recorder.records] == ["group_member_remove"]

    async def test_an_unknown_value_for_the_parameter_is_refused(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.delete(
            f"{BASE}/groups/team/members/{data.member_b.id}?siteRoles=misschien", headers=headers
        )

        assert response.status_code == 422


# -- Roles per endpoint -----------------------------------------------------


class TestRoles:
    """What each role is allowed to do on a site, and how a site role adds to a group role."""

    async def test_a_site_you_have_no_role_on_reads_as_missing(self, client, app, data, factory):
        """The role needs the site, so the lookup comes first. Without this an
        answer would tell any member which sites exist in a group they have
        nothing to do with."""
        login(client, app, sub="lid-b", email="b@example.nl")

        existing = await client.get(f"{BASE}/sites/{data.group.slug}/{data.site.slug}/versions")
        missing = await client.get(f"{BASE}/sites/{data.group.slug}/bestaat-niet/versions")

        assert existing.status_code == 404
        assert missing.status_code == 404
        assert existing.json() == missing.json()

    async def test_a_role_that_is_only_too_narrow_says_so(self, client, app, data, factory):
        """Someone who is inside already gets the honest 403; a neutral 404
        would send them looking for a mistake they did not make."""
        await _join_group(factory, data.group, data.member_b, Role.READER)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        assert (await client.get(f"{BASE}/sites/{data.group.slug}/{data.site.slug}/versions")).status_code == 200
        refused = await client.put(
            f"{BASE}/sites/{data.group.slug}/{data.site.slug}/access",
            json={"base": "public"},
            headers=headers,
        )
        assert refused.status_code == 403
        assert refused.json()["code"] == "INSUFFICIENT_ROLE"

    async def test_a_reader_reads_and_publishes_not(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.READER)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        assert (await client.get(f"{BASE}/sites/team/site/versions")).status_code == 200
        assert (await client.get(f"{BASE}/sites/team/site/previews")).status_code == 200
        assert (await client.get(f"{BASE}/groups/team")).status_code == 200

        assert (
            await client.post(f"{BASE}/sites/team/site/deploys", files=_upload(), headers=headers)
        ).status_code == 403
        for path in ("invitees", "keys", "repository"):
            response = await client.get(f"{BASE}/sites/team/site/{path}")
            assert response.status_code == 403, path
            assert response.json()["code"] == "INSUFFICIENT_ROLE"

    async def test_a_editor_publishes_and_changes_no_policy(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        assert (
            await client.post(f"{BASE}/sites/team/site/deploys", files=_upload(), headers=headers)
        ).status_code == 201
        assert (await client.get(f"{BASE}/sites/team/site/keys")).status_code == 200
        assert (await client.get(f"{BASE}/sites/team/site/invitees")).status_code == 200

        refused = {
            "zichtbaarheid": await client.put(
                f"{BASE}/sites/team/site/access", json={"base": "public"}, headers=headers
            ),
            "sleutel": await client.post(
                f"{BASE}/sites/team/site/keys",
                json={"label": "x", "expiresAt": None},
                headers=headers,
            ),
            "genodigde": await client.post(
                f"{BASE}/sites/team/site/invitees",
                json={"identifier": "gast@example.nl"},
                headers=headers,
            ),
            "verwijderen": await client.delete(f"{BASE}/sites/team/site", headers=headers),
        }
        for what, response in refused.items():
            assert response.status_code == 403, what
            assert response.json()["code"] == "INSUFFICIENT_ROLE", what

    async def test_a_site_role_widens_the_group_role_on_that_one_site(
        self, client, app, data, factory
    ):
        """A siterol adds and never subtracts: the reader publishes on this one
        site and stays a reader on the rest of the groep."""
        second = Site(
            group_id=data.group.id,
            slug="tweede",
            title="Tweede",
            access_base=AccessBase.SITE_TEAM,
            created_by=data.member_a.id,
        )
        async with factory() as db:
            db.add(second)
            await db.commit()
        await _join_group(factory, data.group, data.member_b, Role.READER)
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)

        headers = login(client, app, sub="lid-b", email="b@example.nl")
        assert (
            await client.post(f"{BASE}/sites/team/site/deploys", files=_upload(), headers=headers)
        ).status_code == 201
        assert (
            await client.post(f"{BASE}/sites/team/tweede/deploys", files=_upload(), headers=headers)
        ).status_code == 403

        # And an editor, not an admin: linking a repository stays out of reach.
        response = await client.put(
            f"{BASE}/sites/team/site/repository",
            json={"provider": "github", "owner": "minbzk", "repo": "website", "liveBranch": "main"},
            headers=headers,
        )
        assert response.status_code == 403

    async def test_a_platform_admin_without_group_role_does_not_manage_content(
        self, client, app, data
    ):
        """A platform admin manages people and groups; for content he gives
        himself a visible groepsrol (spec §3.4)."""
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        assert (await client.get(f"{BASE}/groups/team")).status_code == 200
        assert (await client.get(f"{BASE}/groups/team/members")).status_code == 200

        refused = {
            "site aanmaken": await client.post(
                f"{BASE}/groups/team/sites", json={"title": "X", "slug": "x"}, headers=headers
            ),
            "standaardzichtbaarheid": await client.put(
                f"{BASE}/groups/team/default-access",
                json={"base": "public"},
                headers=headers,
            ),
            "repository": await client.get(f"{BASE}/sites/team/site/repository"),
            "versies": await client.get(f"{BASE}/sites/team/site/versions"),
        }
        for what, response in refused.items():
            assert response.status_code == 403, what
            assert response.json()["code"] == "INSUFFICIENT_ROLE", what


# -- Site members -----------------------------------------------------------


class TestSiteMembers:
    """Who can reach one site, and the site role as the only thing these routes change."""

    async def test_the_list_shows_group_members_next_to_site_members(
        self, client, app, data, factory
    ):
        """A list of only the site_members rows would leave out everyone who
        reaches this site through the group, and read as if far fewer people
        had access than really do."""
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        login(client, app, sub="lid-a", email="a@example.nl")

        rows = (await client.get(f"{BASE}/sites/team/site/members")).json()

        assert [
            (row["identifier"], row["groupRole"], row["siteRole"], row["effectiveRole"])
            for row in rows
        ] == [
            ("a@example.nl", "admin", None, "admin"),
            ("b@example.nl", None, "editor", "editor"),
        ]
        assert [row["groupSlug"] for row in rows] == ["team", "team"]
        assert [row["siteSlug"] for row in rows] == ["site", "site"]

    async def test_the_widest_of_the_two_roles_wins(self, client, app, data, factory):
        """A lezer in the groep who is admin here is admin here, both
        in the list and in what the API lets them do."""
        await _join_group(factory, data.group, data.member_b, Role.READER)
        await _join_site(factory, data.site, data.member_b, Role.ADMIN)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        rows = (await client.get(f"{BASE}/sites/team/site/members")).json()
        row = next(row for row in rows if row["identifier"] == "b@example.nl")
        assert (row["groupRole"], row["siteRole"], row["effectiveRole"]) == (
            "reader",
            "admin",
            "admin",
        )

        allowed = await client.put(
            f"{BASE}/sites/team/site/access", json={"base": "public"}, headers=headers
        )
        assert allowed.status_code == 200

    async def test_a_narrower_site_role_takes_nothing_away(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.ADMIN)
        await _join_site(factory, data.site, data.member_b, Role.READER)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        rows = (await client.get(f"{BASE}/sites/team/site/members")).json()
        row = next(row for row in rows if row["identifier"] == "b@example.nl")
        assert (row["groupRole"], row["siteRole"], row["effectiveRole"]) == (
            "admin",
            "reader",
            "admin",
        )

        allowed = await client.put(
            f"{BASE}/sites/team/site/access", json={"base": "public"}, headers=headers
        )
        assert allowed.status_code == 200

    async def test_add_change_and_remove_a_site_role(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        added = await client.post(
            f"{BASE}/sites/team/site/members", json={"identifier": "B@Example.NL"}, headers=headers
        )
        assert added.status_code == 201
        assert added.json()["identifier"] == "b@example.nl"
        assert (added.json()["groupRole"], added.json()["siteRole"]) == (None, "reader")

        changed = await client.put(
            f"{BASE}/sites/team/site/members/{data.member_b.id}/role",
            json={"role": "editor"},
            headers=headers,
        )
        assert changed.status_code == 200
        assert (changed.json()["siteRole"], changed.json()["effectiveRole"]) == ("editor", "editor")

        removed = await client.delete(
            f"{BASE}/sites/team/site/members/{data.member_b.id}", headers=headers
        )
        assert removed.status_code == 204

        rows = (await client.get(f"{BASE}/sites/team/site/members")).json()
        assert [row["identifier"] for row in rows] == ["a@example.nl"]

    async def test_removing_a_site_role_leaves_the_group_role_standing(
        self, client, app, data, factory
    ):
        await _join_group(factory, data.group, data.member_b, Role.READER)
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        removed = await client.delete(
            f"{BASE}/sites/team/site/members/{data.member_b.id}", headers=headers
        )
        assert removed.status_code == 204

        rows = (await client.get(f"{BASE}/sites/team/site/members")).json()
        row = next(row for row in rows if row["identifier"] == "b@example.nl")
        assert (row["groupRole"], row["siteRole"], row["effectiveRole"]) == (
            "reader",
            None,
            "reader",
        )

        headers_b = login(client, app, sub="lid-b", email="b@example.nl")
        assert (await client.get(f"{BASE}/sites/team/site/versions")).status_code == 200
        assert (
            await client.post(f"{BASE}/sites/team/site/deploys", files=_upload(), headers=headers_b)
        ).status_code == 403

    async def test_unknown_member_404_and_duplicate_409(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        unknown = await client.post(
            f"{BASE}/sites/team/site/members",
            json={"identifier": "bestaat-niet@example.nl"},
            headers=headers,
        )
        assert unknown.status_code == 404
        assert unknown.json()["code"] == "UNKNOWN_MEMBER"

        assert (
            await client.post(
                f"{BASE}/sites/team/site/members",
                json={"identifier": "b@example.nl"},
                headers=headers,
            )
        ).status_code == 201
        duplicate = await client.post(
            f"{BASE}/sites/team/site/members", json={"identifier": "b@example.nl"}, headers=headers
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "ALREADY_SITE_MEMBER"

    async def test_change_and_removal_of_an_unknown_member_404(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        refused = {
            "wijzigen": await client.put(
                f"{BASE}/sites/team/site/members/{uuid.uuid4()}/role",
                json={"role": "editor"},
                headers=headers,
            ),
            "weghalen": await client.delete(
                f"{BASE}/sites/team/site/members/{uuid.uuid4()}", headers=headers
            ),
        }
        for what, response in refused.items():
            assert response.status_code == 404, what
            assert response.json()["code"] == "UNKNOWN_MEMBER", what

    async def test_a_group_member_has_no_site_role_to_change_here(
        self, client, app, data, factory
    ):
        """Someone who reaches this site through the groep is changed on the
        group page; these routes touch only a role of their own."""
        await _join_group(factory, data.group, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        rows = (await client.get(f"{BASE}/sites/team/site/members")).json()
        row = next(row for row in rows if row["identifier"] == "b@example.nl")
        assert (row["groupRole"], row["siteRole"], row["effectiveRole"]) == (
            "editor",
            None,
            "editor",
        )

        refused = {
            "wijzigen": await client.put(
                f"{BASE}/sites/team/site/members/{data.member_b.id}/role",
                json={"role": "admin"},
                headers=headers,
            ),
            "weghalen": await client.delete(
                f"{BASE}/sites/team/site/members/{data.member_b.id}", headers=headers
            ),
        }
        for what, response in refused.items():
            assert response.status_code == 404, what
            assert response.json()["code"] == "NOT_SITE_MEMBER", what

    async def test_a_reader_looks_on_and_manages_nothing(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.READER)
        headers = login(client, app, sub="lid-b", email="b@example.nl")

        assert (await client.get(f"{BASE}/sites/team/site/members")).status_code == 200

        refused = {
            "toevoegen": await client.post(
                f"{BASE}/sites/team/site/members",
                json={"identifier": "admin@example.nl"},
                headers=headers,
            ),
            "wijzigen": await client.put(
                f"{BASE}/sites/team/site/members/{data.member_a.id}/role",
                json={"role": "reader"},
                headers=headers,
            ),
            "weghalen": await client.delete(
                f"{BASE}/sites/team/site/members/{data.member_a.id}", headers=headers
            ),
        }
        for what, response in refused.items():
            assert response.status_code == 403, what
            assert response.json()["code"] == "INSUFFICIENT_ROLE", what

    async def test_without_any_role_the_site_reads_as_missing(self, client, app, data):
        login(client, app, sub="lid-b", email="b@example.nl")

        response = await client.get(f"{BASE}/sites/team/site/members")
        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_SITE"

    async def test_a_site_role_alone_shows_only_that_site_in_the_overview(
        self, client, app, data, factory
    ):
        """A site role grants nothing at group level, so the rest of the groep
        stays hidden. This is Noor Bakker in the dev seed."""
        async with factory() as db:
            db.add(
                Site(
                    group_id=data.group.id,
                    slug="tweede",
                    title="Tweede",
                    access_base=AccessBase.SITE_TEAM,
                    created_by=data.member_a.id,
                )
            )
            await db.commit()
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        login(client, app, sub="lid-b", email="b@example.nl")

        overview = (await client.get(f"{BASE}/overview")).json()

        assert [group["group"]["slug"] for group in overview["groups"]] == ["team"]
        assert [site["slug"] for site in overview["groups"][0]["sites"]] == ["site"]

    async def test_me_reports_group_and_site_roles(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.READER)
        await _join_site(factory, data.site, data.member_b, Role.ADMIN)
        login(client, app, sub="lid-b", email="b@example.nl")

        me = (await client.get(f"{BASE}/me")).json()

        assert me["groupRoles"] == [{"groupSlug": "team", "role": "reader"}]
        assert me["siteRoles"] == [
            {"groupSlug": "team", "siteSlug": "site", "role": "admin", "effectiveRole": "admin"}
        ]


class TestMemberSearch:
    """Searching for someone to add, on the groep and on de site.

    Searching demands exactly the role that adding demands, so the permission
    to look someone up equals the permission to add them.
    """

    GROUP = f"{BASE}/groups/team/members/search"
    SITE = f"{BASE}/sites/team/site/members/search"

    async def test_a_name_fragment_finds_someone(self, client, app, data, factory):
        await _new_member(factory, sub="lid-jansen", email="jj@example.nl", name="Joke Jansen")
        login(client, app, sub="lid-a", email="a@example.nl")

        hits = (await client.get(self.GROUP, params={"q": "joke"})).json()

        assert hits == [
            {
                "identifier": "jj@example.nl",
                "name": "Joke Jansen",
                "email": "jj@example.nl",
                "alreadyMember": False,
                "groupRole": None,
            }
        ]

    async def test_an_email_fragment_finds_someone_too(self, client, app, data, factory):
        await _new_member(factory, sub="lid-jansen", email="jj@example.nl", name="Joke Jansen")
        login(client, app, sub="lid-a", email="a@example.nl")

        hits = (await client.get(self.SITE, params={"q": "jj@"})).json()

        assert [hit["identifier"] for hit in hits] == ["jj@example.nl"]

    async def test_the_search_ignores_case(self, client, app, data, factory):
        await _new_member(factory, sub="lid-jansen", email="jj@example.nl", name="Joke Jansen")
        login(client, app, sub="lid-a", email="a@example.nl")

        for url in (self.GROUP, self.SITE):
            hits = (await client.get(url, params={"q": "jOkE jAnSeN"})).json()
            assert [hit["identifier"] for hit in hits] == ["jj@example.nl"], url

    async def test_a_query_of_one_character_is_refused(self, client, app, data):
        """Without a floor an empty query hands out the whole directory."""
        login(client, app, sub="lid-a", email="a@example.nl")

        for url in (self.GROUP, self.SITE):
            response = await client.get(url, params={"q": "a"})
            assert response.status_code == 422, url
            assert response.headers["content-type"].startswith(PROBLEM), url
            assert response.json()["code"] == "SEARCH_TOO_SHORT", url

            assert (await client.get(url)).status_code == 422, url

            # Whitespace is not a character to search on: " a " is that same
            # single letter, and has to be refused as one.
            assert (await client.get(url, params={"q": " a "})).status_code == 422, url

    async def test_two_characters_are_enough(self, client, app, data, factory):
        """The floor sits at two, so two has to come through it."""
        await _new_member(factory, sub="lid-jansen", email="jj@example.nl", name="Joke Jansen")
        login(client, app, sub="lid-a", email="a@example.nl")

        for url in (self.GROUP, self.SITE):
            hits = (await client.get(url, params={"q": "jo"})).json()
            assert [hit["identifier"] for hit in hits] == ["jj@example.nl"], url

    async def test_a_wildcard_is_a_character_like_any_other(self, client, app, data):
        """`%` and `_` steer LIKE; here they are what someone typed, nothing more."""
        login(client, app, sub="lid-a", email="a@example.nl")

        # Unescaped these would match every address on the platform.
        assert (await client.get(self.GROUP, params={"q": "%@"})).json() == []
        assert (await client.get(self.GROUP, params={"q": "_@"})).json() == []

    async def test_the_order_runs_by_name_and_then_by_address(self, client, app, data, factory):
        """Sorted on what the list shows. A member without a name carries an
        empty one, which puts them at the top and not behind everybody."""
        await _new_member(factory, sub="lid-zzz", email="zzz-op-naam@example.nl", name="Bea Zeeman")
        await _new_member(factory, sub="lid-aaa", email="aaa-op-naam@example.nl", name="Zus Aalten")
        await _new_member(factory, sub="lid-mmm", email="mmm-op-naam@example.nl")
        login(client, app, sub="lid-a", email="a@example.nl")

        hits = (await client.get(self.GROUP, params={"q": "op-naam"})).json()

        assert [hit["identifier"] for hit in hits] == [
            "mmm-op-naam@example.nl",
            "zzz-op-naam@example.nl",
            "aaa-op-naam@example.nl",
        ]

    async def test_someone_who_is_not_active_stays_out(self, client, app, data, factory):
        await _new_member(factory, sub="lid-nora", email="nora@example.nl", name="Nora Actief")
        await _new_member(
            factory, sub="lid-uit", email="uit@example.nl", name="Nora Uit",
            status=MemberStatus.DEACTIVATED,
        )
        login(client, app, sub="lid-a", email="a@example.nl")

        for url in (self.GROUP, self.SITE):
            hits = (await client.get(url, params={"q": "nora"})).json()
            assert [hit["identifier"] for hit in hits] == ["nora@example.nl"], url

    async def test_the_flag_says_who_is_already_in_the_group(self, client, app, data, factory):
        """Already a member stays findable: saying so beats a row that goes missing."""
        await _new_member(factory, sub="lid-buiten", email="buiten@example.nl", name="Bea Buiten")
        login(client, app, sub="lid-a", email="a@example.nl")

        hits = {
            hit["identifier"]: hit["alreadyMember"]
            for hit in (await client.get(self.GROUP, params={"q": "@example.nl"})).json()
        }

        assert hits["a@example.nl"] is True
        assert hits["b@example.nl"] is False
        assert hits["buiten@example.nl"] is False

    async def test_on_a_site_only_an_own_site_role_counts_as_already_there(
        self, client, app, data, factory
    ):
        """A groepslid can still be given a siterol, because a siterol only
        widens. Only an eigen siterol rules someone out."""
        await _new_member(factory, sub="lid-buiten", email="buiten@example.nl", name="Bea Buiten")
        await _join_site(factory, data.site, data.member_b, Role.EDITOR)
        login(client, app, sub="lid-a", email="a@example.nl")

        hits = {
            hit["identifier"]: hit
            for hit in (await client.get(self.SITE, params={"q": "@example.nl"})).json()
        }

        assert hits["a@example.nl"]["alreadyMember"] is False
        assert hits["b@example.nl"]["alreadyMember"] is True
        assert hits["buiten@example.nl"]["alreadyMember"] is False

    async def test_on_a_site_the_group_role_comes_along(self, client, app, data, factory):
        """What someone reaches this site with through the groep says whether a
        siterol adds anything, so it is in the answer."""
        await _new_member(factory, sub="lid-buiten", email="buiten@example.nl", name="Bea Buiten")
        await _join_group(factory, data.group, data.member_b, Role.READER)
        login(client, app, sub="lid-a", email="a@example.nl")

        hits = {
            hit["identifier"]: hit["groupRole"]
            for hit in (await client.get(self.SITE, params={"q": "@example.nl"})).json()
        }

        assert hits["a@example.nl"] == "admin"
        assert hits["b@example.nl"] == "reader"
        assert hits["buiten@example.nl"] is None

    async def test_on_a_group_the_group_role_stays_out(self, client, app, data):
        """`groupRole` belongs to the sitezoekveld; on the groep it says nothing
        that `alreadyMember` does not already say."""
        login(client, app, sub="lid-a", email="a@example.nl")

        hits = (await client.get(self.GROUP, params={"q": "a@example.nl"})).json()

        assert [hit["groupRole"] for hit in hits] == [None]

    @pytest.mark.parametrize("role", [Role.READER, Role.EDITOR])
    async def test_a_role_under_admin_may_not_search(self, client, app, data, factory, role):
        """Searching demands what adding demands, so nothing under admin."""
        await _join_group(factory, data.group, data.member_b, role)
        login(client, app, sub="lid-b", email="b@example.nl")

        for url in (self.GROUP, self.SITE):
            response = await client.get(url, params={"q": "joke"})
            assert response.status_code == 403, url
            assert response.json()["code"] == "INSUFFICIENT_ROLE", url

    async def test_a_platform_admin_searches_without_being_a_group_member(
        self, client, app, data, factory
    ):
        """Whoever may add someone to the groep may look them up there."""
        await _new_member(factory, sub="lid-jansen", email="jj@example.nl", name="Joke Jansen")
        login(client, app, sub="admin-sub", email="admin@example.nl")

        hits = (await client.get(self.GROUP, params={"q": "joke"})).json()

        assert [hit["identifier"] for hit in hits] == ["jj@example.nl"]

    async def test_at_most_ten_come_back(self, client, app, data, factory):
        """The crowd lives here and not in the shared fixture: no other test wants it."""
        async with factory() as db:
            db.add_all(
                [
                    Member(
                        sso_subject=f"lid-veel-{index:02d}",
                        email=f"veel{index:02d}@example.nl",
                        name=f"Veel Volk {index:02d}",
                        status=MemberStatus.ACTIVE,
                    )
                    for index in range(12)
                ]
            )
            await db.commit()
        login(client, app, sub="lid-a", email="a@example.nl")

        hits = (await client.get(self.GROUP, params={"q": "veel volk"})).json()

        assert [hit["name"] for hit in hits] == [f"Veel Volk {index:02d}" for index in range(10)]


class TestVersionDeployer:
    async def test_a_version_carries_the_name_of_whoever_uploaded_it(self, client, app, data, factory):
        """Without the name the list has only a UUID to show."""
        async with factory() as db:
            version = Version(
                site_id=data.site.id,
                target=VersionTarget.LIVE,
                storage_ref="ref-naam",
                member_id=data.member_a.id,
            )
            db.add(version)
            await db.commit()

        login(client, app, sub="lid-a", email="a@example.nl")
        rows = (await client.get(f"{BASE}/sites/team/site/versions")).json()
        row = next(r for r in rows if r["storageRef"] == "ref-naam")
        assert row["createdByName"] == data.member_a.email
        assert row["createdByMember"] == str(data.member_a.id)

    async def test_a_ci_deploy_has_no_name_to_show(self, client, app, data, factory):
        """ck_versions_origin demands one of the two, so a CI deploy names its
        repository; what it must not have is a member to name."""
        async with factory() as db:
            db.add(
                Version(
                    site_id=data.site.id,
                    target=VersionTarget.LIVE,
                    storage_ref="ref-ci",
                    member_id=None,
                    ci_repository="github.com/minbzk/website",
                )
            )
            await db.commit()

        login(client, app, sub="lid-a", email="a@example.nl")
        rows = (await client.get(f"{BASE}/sites/team/site/versions")).json()
        row = next(r for r in rows if r["storageRef"] == "ref-ci")
        assert row["createdByName"] is None
        assert row["createdByRepository"] == "github.com/minbzk/website"
        assert row["origin"] == "action"


# -- Invitees ---------------------------------------------------------------


class TestInvitees:
    async def test_crud_and_normalisation(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/sites/team/site/invitees",
            json={"identifier": "Gast@Example.NL"},
            headers=headers,
        )
        assert response.status_code == 201
        assert response.json()["identifier"] == "gast@example.nl"

        response = await client.post(
            f"{BASE}/sites/team/site/invitees",
            json={"identifier": "gast@example.nl"},
            headers=headers,
        )
        assert response.status_code == 409

        items = (await client.get(f"{BASE}/sites/team/site/invitees")).json()
        assert [invitee["identifier"] for invitee in items] == ["gast@example.nl"]

        invitee_id = items[0]["id"]
        response = await client.delete(
            f"{BASE}/sites/team/site/invitees/{invitee_id}", headers=headers
        )
        assert response.status_code == 204
        assert (await client.get(f"{BASE}/sites/team/site/invitees")).json() == []

        response = await client.delete(
            f"{BASE}/sites/team/site/invitees/{invitee_id}", headers=headers
        )
        assert response.status_code == 404

    async def test_an_invitee_of_another_site_cannot_be_removed_here(
        self, client, app, data, factory
    ):
        """The id in the path is not a licence: it still has to sit on the
        list of the site in the path."""
        async with factory() as db:
            other = Site(
                group_id=data.group.id,
                slug="andere",
                title="Andere",
                access_base=AccessBase.SITE_TEAM,
                created_by=data.member_a.id,
            )
            db.add(other)
            await db.commit()
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        added = await client.post(
            f"{BASE}/sites/team/site/invitees",
            json={"identifier": "gast@example.nl"},
            headers=headers,
        )
        assert added.status_code == 201

        response = await client.delete(
            f"{BASE}/sites/team/andere/invitees/{added.json()['id']}", headers=headers
        )
        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_INVITEE"
        assert len((await client.get(f"{BASE}/sites/team/site/invitees")).json()) == 1


# -- Keys -------------------------------------------------------------------


class TestKeys:
    async def test_create_shows_plaintext_once(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/sites/team/site/keys",
            json={"label": "reviewers", "expiresAt": None},
            headers=headers,
        )
        assert response.status_code == 201
        body = response.json()
        assert KEY_VALUE_RE.match(body["value"])
        assert body["key"]["label"] == "reviewers"
        assert body["value"].startswith(body["key"]["selector"] + ".")
        # A null expiresAt takes the default rather than staying empty.
        assert body["key"]["expiresAt"] is not None

        items = (await client.get(f"{BASE}/sites/team/site/keys")).json()
        assert len(items) == 1
        assert "value" not in items[0]
        assert items[0]["status"] == "active"

    async def test_create_refuses_a_label_with_control_characters(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/sites/team/site/keys",
            json={"label": "reviewers ‮txt.exe", "expiresAt": None},
            headers=headers,
        )
        assert response.status_code == 422

    async def test_create_without_label_gets_dutch_date_default(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/sites/team/site/keys",
            json={"expiresAt": None},
            headers=headers,
        )
        assert response.status_code == 201
        label = response.json()["key"]["label"]
        today = datetime.now(UTC)
        assert label == f"Link van {today.day} {access_keys._DUTCH_MONTHS[today.month - 1]}"

    async def test_create_with_empty_label_gets_default(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/sites/team/site/keys",
            json={"label": "", "expiresAt": None},
            headers=headers,
        )
        assert response.status_code == 201
        assert response.json()["key"]["label"].startswith("Link van ")

    async def test_create_with_whitespace_label_gets_default(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/sites/team/site/keys",
            json={"label": "   ", "expiresAt": None},
            headers=headers,
        )
        assert response.status_code == 201
        assert response.json()["key"]["label"].startswith("Link van ")

    async def test_create_with_real_label_keeps_it(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/sites/team/site/keys",
            json={"label": "  reviewers  ", "expiresAt": None},
            headers=headers,
        )
        assert response.status_code == 201
        assert response.json()["key"]["label"] == "reviewers"

    async def test_create_without_label_still_audits_selector(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        recorder = install_audit_recorder(app)
        response = await client.post(
            f"{BASE}/sites/team/site/keys",
            json={"expiresAt": None},
            headers=headers,
        )
        assert response.status_code == 201
        selector = response.json()["key"]["selector"]
        record = recorder.only()
        assert record.action == "key_create"
        assert record.refs == {"group": "team", "site": "site", "selector": selector}

    async def test_expiry_beyond_the_maximum_is_422(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        future = (datetime.now(UTC) + timedelta(days=400)).isoformat()
        response = await client.post(
            f"{BASE}/sites/team/site/keys",
            json={"label": "te-lang", "expiresAt": future},
            headers=headers,
        )
        assert response.status_code == 422
        assert response.json()["code"] == "EXPIRY_TOO_FAR"

    async def test_revoke(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        created = (
            await client.post(
                f"{BASE}/sites/team/site/keys",
                json={"label": "x", "expiresAt": None},
                headers=headers,
            )
        ).json()
        selector = created["key"]["selector"]

        response = await client.delete(
            f"{BASE}/sites/team/site/keys/{selector}", headers=headers
        )
        assert response.status_code == 204
        items = (await client.get(f"{BASE}/sites/team/site/keys")).json()
        assert items[0]["status"] == "revoked"

        response = await client.delete(
            f"{BASE}/sites/team/site/keys/onbekend1", headers=headers
        )
        assert response.status_code == 404


# -- Linked repository (CI trust) --------------------------------------------


REPOSITORY = f"{BASE}/sites/team/site/repository"


def _github(**overrides) -> dict:
    return {"provider": "github", "owner": "minbzk", "repo": "website", "liveBranch": "main", **overrides}


class TestSiteRepository:
    async def test_not_linked_is_a_404_with_its_own_code(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.get(REPOSITORY)
        assert response.status_code == 404
        assert response.json()["code"] == "REPOSITORY_NOT_SET"

    async def test_link_resolves_the_ids_and_the_canonical_spelling(self, client, app, data, factory):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(REPOSITORY, json=_github(), headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "github"
        assert body["host"] == "https://github.com"
        assert (body["owner"], body["repo"]) == ("MinBZK", "Website")
        assert (body["repositoryId"], body["ownerId"]) == (1001, 2002)
        assert body["liveBranch"] == "main"
        assert body["createdBy"] == "a@example.nl"
        assert (await client.get(REPOSITORY)).json() == body

        async with factory() as db:
            row = (
                await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == "site_repository_set"))
            ).scalar_one()
        assert row.refs == {
            "group": "team",
            "site": "site",
            "provider": "github",
            "host": "https://github.com",
            "repository": "MinBZK/Website",
            "repository_id": 1001,
            "live_branch": "main",
        }

    async def test_relinking_replaces_the_one_row(self, client, app, data, factory):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        await client.put(REPOSITORY, json=_github(), headers=headers)
        response = await client.put(
            REPOSITORY,
            json={"provider": "forgejo", "host": "https://Code.Overheid.nl/", "owner": "minbzk", "repo": "plak",
                  "liveBranch": ""},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["host"] == FORGEJO_HOST
        assert response.json()["liveBranch"] is None
        assert await _count(factory, SiteRepository, site_id=data.site.id) == 1

    async def test_the_live_branch_may_be_null(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(REPOSITORY, json=_github(liveBranch=None), headers=headers)
        assert response.status_code == 200
        assert response.json()["liveBranch"] is None

    @pytest.mark.parametrize(
        "branch", ["-x", "/main", "main/", "a..b", "a//b", "refs/heads/main", "main.lock", "met spatie", "x" * 256]
    )
    async def test_an_invalid_live_branch_is_422(self, client, app, data, branch):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(REPOSITORY, json=_github(liveBranch=branch), headers=headers)
        assert response.status_code == 422
        assert response.json()["code"] == "LIVE_BRANCH_INVALID"

    @pytest.mark.parametrize(("owner", "repo"), [("", "x"), ("minbzk", ".."), ("min bzk", "x"), ("a/b", "c")])
    async def test_an_invalid_name_is_422(self, client, app, data, owner, repo):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(REPOSITORY, json=_github(owner=owner, repo=repo), headers=headers)
        assert response.status_code == 422
        assert response.json()["code"] == "REPOSITORY_INVALID"

    @pytest.mark.parametrize(
        "body",
        [
            {"provider": "github", "host": "https://gitlab.example"},
            {"provider": "forgejo", "host": "https://codeberg.org"},
            {"provider": "forgejo", "host": "http://code.overheid.nl"},
            {"provider": "forgejo"},
        ],
    )
    async def test_a_host_outside_the_configuration_is_422(self, client, app, data, mock_ci, body):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(REPOSITORY, json={**_github(), **body}, headers=headers)
        assert response.status_code == 422
        assert response.json()["code"] == "HOST_NOT_ALLOWED"
        assert mock_ci.requests == []

    async def test_github_accepts_its_own_host_spelled_out(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(REPOSITORY, json=_github(host="https://github.com/"), headers=headers)
        assert response.status_code == 200

    async def test_an_unknown_repository_is_422(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(REPOSITORY, json=_github(repo="bestaat-niet"), headers=headers)
        assert response.status_code == 422
        assert response.json()["code"] == "REPOSITORY_NOT_FOUND"
        assert "not public" in response.json()["detail"]

    async def test_a_rate_limited_provider_is_503(self, client, app, data, mock_ci):
        mock_ci.failures["https://api.github.com/repos/minbzk/website"] = 429
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(REPOSITORY, json=_github(), headers=headers)
        assert response.status_code == 503
        assert response.json()["code"] == "CI_PROVIDER_RATE_LIMITED"

    async def test_an_unreachable_provider_is_503(self, client, app, data, mock_ci):
        mock_ci.failures["https://api.github.com/repos/minbzk/website"] = 500
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(REPOSITORY, json=_github(), headers=headers)
        assert response.status_code == 503
        assert response.json()["code"] == "CI_PROVIDER_UNREACHABLE"

    async def test_unlink(self, client, app, data, factory):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        await client.put(REPOSITORY, json=_github(), headers=headers)
        assert (await client.delete(REPOSITORY, headers=headers)).status_code == 204
        assert await _count(factory, SiteRepository, site_id=data.site.id) == 0
        response = await client.delete(REPOSITORY, headers=headers)
        assert response.status_code == 404
        assert response.json()["code"] == "REPOSITORY_NOT_SET"
        async with factory() as db:
            actions = list(await db.scalars(select(AuditLogEntry.action).order_by(AuditLogEntry.occurred_at)))
        assert "site_repository_remove" in actions

    async def test_an_editor_reads_but_does_not_link_or_unlink(self, client, app, data, factory):
        await _join_group(factory, data.group, data.member_b, Role.EDITOR)
        headers = login(client, app, sub="lid-b", email="b@example.nl")
        assert (await client.get(REPOSITORY)).json()["code"] == "REPOSITORY_NOT_SET"
        for response in (
            await client.put(REPOSITORY, json=_github(), headers=headers),
            await client.delete(REPOSITORY, headers=headers),
        ):
            assert response.status_code == 403
            assert response.json()["code"] == "INSUFFICIENT_ROLE"

    async def test_without_csrf_nothing_is_linked(self, client, app, data, factory):
        login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(REPOSITORY, json=_github())
        assert response.status_code == 403
        assert response.json()["code"] == "CSRF_INVALID"
        assert await _count(factory, SiteRepository, site_id=data.site.id) == 0

    async def test_a_creator_who_left_is_shown_empty(self, client, app, data, factory):
        async with factory() as db:
            db.add(
                SiteRepository(
                    site_id=data.site.id,
                    provider=CiProvider.GITHUB,
                    host="https://github.com",
                    owner="minbzk",
                    repo="website",
                    repository_id=1,
                    owner_id=2,
                    live_branch=None,
                    created_by=None,
                )
            )
            await db.commit()
        login(client, app, sub="lid-a", email="a@example.nl")
        assert (await client.get(REPOSITORY)).json()["createdBy"] == ""


# -- Versions, previews and upload over the session -------------------------


class TestVersionsPreviewsUpload:
    async def test_upload_via_session_and_version_list(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/sites/team/site/deploys", files=_upload(), headers=headers
        )
        assert response.status_code == 201
        version_id = response.json()["versionId"]

        versions = (await client.get(f"{BASE}/sites/team/site/versions")).json()
        assert len(versions) == 1
        assert versions[0]["id"] == version_id
        assert versions[0]["isLive"] is True
        assert versions[0]["origin"] == "upload"

        overview = (await client.get(f"{BASE}/overview")).json()
        site = overview["groups"][0]["sites"][0]
        assert site["hasLiveVersion"] is True
        assert site["liveVersionId"] == version_id
        assert site["lastPublishedAt"] is not None

    async def test_upload_without_csrf_403(self, client, app, data):
        login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(f"{BASE}/sites/team/site/deploys", files=_upload())
        assert response.status_code == 403

    async def test_rollback_to_older_version(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        first = (
            await client.post(f"{BASE}/sites/team/site/deploys", files=_upload(), headers=headers)
        ).json()["versionId"]
        second_one = (
            await client.post(f"{BASE}/sites/team/site/deploys", files=_upload(), headers=headers)
        ).json()["versionId"]
        assert first != second_one

        response = await client.post(
            f"{BASE}/sites/team/site/versions/{first}/_set-live", headers=headers
        )
        assert response.status_code == 200
        assert response.json()["liveVersionId"] == first

        unknown = uuid.uuid4()
        response = await client.post(
            f"{BASE}/sites/team/site/versions/{unknown}/_set-live", headers=headers
        )
        assert response.status_code == 404

    async def test_rollback_refuses_preview_version(self, client, app, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        await client.post(
            f"{BASE}/sites/team/site/deploys",
            files=_upload(),
            data={"preview": "pr-1"},
            headers=headers,
        )
        previews = (await client.get(f"{BASE}/sites/team/site/previews")).json()
        preview_version = previews[0]["versionId"]
        response = await client.post(
            f"{BASE}/sites/team/site/versions/{preview_version}/_set-live", headers=headers
        )
        assert response.status_code == 422

    async def test_preview_upload_override_and_delete(self, client, app, data, content_root):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/sites/team/site/deploys",
            files=_upload(),
            data={"preview": "pr-7"},
            headers=headers,
        )
        assert response.status_code == 201

        previews = (await client.get(f"{BASE}/sites/team/site/previews")).json()
        assert len(previews) == 1
        assert previews[0]["ref"] == "pr-7"
        assert previews[0]["url"] == "/team/site/_preview/pr-7/"
        assert previews[0]["accessOverride"] is None
        assert previews[0]["expiresAt"] is not None

        response = await client.put(
            f"{BASE}/sites/team/site/previews/pr-7/access",
            json={"access": {"base": "nobody", "keys": True, "invitees": False}},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["accessOverride"] == {
            "base": "nobody",
            "keys": True,
            "invitees": False,
        }

        response = await client.put(
            f"{BASE}/sites/team/site/previews/pr-7/access",
            json={"access": None},
            headers=headers,
        )
        assert response.json()["accessOverride"] is None

        response = await client.put(
            f"{BASE}/sites/team/site/previews/onbekend/access",
            json={"access": {"base": "public", "keys": False, "invitees": False}},
            headers=headers,
        )
        assert response.status_code == 404

        storage_ref = (await client.get(f"{BASE}/sites/team/site/versions")).json()[0][
            "storageRef"
        ]
        assert (content_root / storage_ref).is_dir()

        response = await client.delete(f"{BASE}/sites/team/site/previews/pr-7", headers=headers)
        assert response.status_code == 204
        # Idempotent, just like the deploy API teardown.
        response = await client.delete(f"{BASE}/sites/team/site/previews/pr-7", headers=headers)
        assert response.status_code == 204
        assert (await client.get(f"{BASE}/sites/team/site/previews")).json() == []
        assert not (content_root / storage_ref).exists()


# -- Delete cascade ---------------------------------------------------------


class TestSiteDeletion:
    async def test_cascade_deletes_rows_and_file_trees(
        self, client, app, factory, data, content_root
    ):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        await client.post(f"{BASE}/sites/team/site/deploys", files=_upload(), headers=headers)
        await client.post(
            f"{BASE}/sites/team/site/deploys",
            files=_upload(),
            data={"preview": "pr-1"},
            headers=headers,
        )
        await client.post(
            f"{BASE}/sites/team/site/invitees",
            json={"identifier": "gast@example.nl"},
            headers=headers,
        )
        await client.post(
            f"{BASE}/sites/team/site/keys",
            json={"label": "x", "expiresAt": None},
            headers=headers,
        )
        await client.put(
            f"{BASE}/sites/team/site/repository",
            json={"provider": "github", "owner": "minbzk", "repo": "website", "liveBranch": None},
            headers=headers,
        )

        versions = (await client.get(f"{BASE}/sites/team/site/versions")).json()
        storage_refs = [version["storageRef"] for version in versions]
        assert len(storage_refs) == 2
        for storage_ref in storage_refs:
            assert (content_root / storage_ref).is_dir()

        response = await client.delete(f"{BASE}/sites/team/site", headers=headers)
        assert response.status_code == 204

        site_id = data.site.id
        assert await _count(factory, Site, id=site_id) == 0
        assert await _count(factory, Version, site_id=site_id) == 0
        assert await _count(factory, Preview, site_id=site_id) == 0
        assert await _count(factory, Invitee, site_id=site_id) == 0
        assert await _count(factory, AccessKey, site_id=site_id) == 0
        assert await _count(factory, SiteRepository, site_id=site_id) == 0
        for storage_ref in storage_refs:
            assert not (content_root / storage_ref).exists()

        assert (await client.get(f"{BASE}/sites/team/site/versions")).status_code == 404

    async def test_not_group_member_may_site_not_delete(self, client, app, data):
        """A 404, not a 403: deleting is refused without giving away that there
        is something here to delete. See _site_with_role."""
        headers = login(client, app, sub="lid-b", email="b@example.nl")
        response = await client.delete(f"{BASE}/sites/team/site", headers=headers)
        assert response.status_code == 404
        assert response.json()["code"] == "UNKNOWN_SITE"


# -- Audit ------------------------------------------------------------------


async def _refusal_rows(factory) -> list[AuditLogEntry]:
    async with factory() as db:
        result = await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == "admin_access"))
        return list(result.scalars().all())


class TestAudit:
    async def test_admin_action_becomes_audited(self, client, app, factory, data):
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        await client.post(
            f"{BASE}/groups/team/sites", json={"title": "X", "slug": "x"}, headers=headers
        )
        async with factory() as db:
            count = await db.scalar(
                select(func.count()).select_from(AuditLogEntry).where(AuditLogEntry.action == "site_create")
            )
        assert count == 1

    async def test_refused_platform_action_becomes_audited(self, client, app, factory, data):
        """The example from the finding: a non-admin trying to promote another
        member is refused, and now also audited."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        response = await client.put(
            f"{BASE}/platform/members/{data.member_b.id}/platform-role",
            json={"platformRole": "admin"},
            headers=headers,
        )
        assert response.status_code == 403

        rows = await _refusal_rows(factory)
        assert len(rows) == 1
        row = rows[0]
        assert row.reason_code == "NOT_ADMIN"
        assert row.refs["method"] == "PUT"
        assert row.refs["route"] == "/-/api/v1/platform/members/{member_id}/platform-role"
        assert row.actor_pseudonym == pseudonymise("audit-pepper-van-minstens-32-bytes!!", "lid-a")

    async def test_csrf_refusal_becomes_audited(self, client, app, factory, data):
        set_session_cookie(client, app, sub="lid-a", email="a@example.nl")
        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "X", "slug": "x"}
        )
        assert response.status_code == 403

        rows = await _refusal_rows(factory)
        assert len(rows) == 1
        assert rows[0].reason_code == "CSRF_INVALID"

    async def test_neutral_404_on_a_mutation_becomes_audited_without_the_slugs(
        self, client, app, factory, data
    ):
        headers = login(client, app, sub="lid-b", email="b@example.nl")
        response = await client.delete(f"{BASE}/sites/team/site", headers=headers)
        assert response.status_code == 404

        rows = await _refusal_rows(factory)
        assert len(rows) == 1
        row = rows[0]
        assert row.reason_code == "UNKNOWN_SITE"
        assert row.refs["status"] == 404
        # "site" is not checked on its own: it is also a substring of the
        # route template's own static text ("/sites/"), so it proves nothing.
        assert "team" not in json.dumps(row.refs)
        assert row.refs["route"] == "/-/api/v1/sites/{group_slug}/{site_slug}"

    async def test_read_404_does_not_become_audited(self, client, app, factory, data):
        headers = login(client, app, sub="lid-b", email="b@example.nl")
        response = await client.get(f"{BASE}/sites/team/site/versions", headers=headers)
        assert response.status_code == 404
        assert await _refusal_rows(factory) == []

    async def test_a_missing_audit_log_does_not_block_an_ordinary_action(self, client, app, data):
        """`_audit` (the fire-and-forget variant used for ordinary admin
        actions) is a no-op without a configured log: the action itself must
        not fail because there is nowhere to record it."""
        app.state.audit_log = None
        headers = login(client, app, sub="lid-a", email="a@example.nl")

        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "Zonder log", "slug": "zonder-log"}, headers=headers
        )
        assert response.status_code == 201

    async def test_a_missing_audit_log_gives_503_on_the_audit_read(self, client, app, data):
        """Unlike `_audit`, `_audit_strict` fails closed: reading the log is
        itself a disclosure, so a missing log must not let the page through."""
        app.state.audit_log = None
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")

        response = await client.get(f"{BASE}/platform/audit", headers=headers)
        assert response.status_code == 503
        assert response.json()["code"] == "AUDIT_UNAVAILABLE"

    async def test_a_missing_audit_log_gives_503_on_a_deanonymisation_lookup(
        self, client, app, data
    ):
        """Same fail-closed shape as the audit read, for `_audit_strict_limited`
        (actor-pseudonym, actor-identity, the IP reveal)."""
        app.state.audit_log = None
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")

        response = await client.post(
            f"{BASE}/platform/audit/actor-pseudonym",
            json={"identifier": "a@example.nl", "reason": "onderzoek naar een melding"},
            headers=headers,
        )
        assert response.status_code == 503
        assert response.json()["code"] == "AUDIT_UNAVAILABLE"


class TestAuditFilters:
    """Each query filter on GET /platform/audit narrows the page: proven by a
    second row that a filter leaves out, not only by one that stays in."""

    async def test_since_and_until_bound_the_window(self, client, app, data):
        """Also proves `_require_aware`: a naive `since`/`until` (no
        timezone) is read as UTC rather than compared against an aware
        `occurred_at` and failing outright."""
        await app.state.audit_log.write("test_sinds", ANONYMOUS, "allowed")
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")

        before = (datetime.now(UTC) - timedelta(hours=1)).replace(tzinfo=None).isoformat()
        after = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

        since_before = await client.get(f"{BASE}/platform/audit", params={"since": before}, headers=headers)
        since_after = await client.get(f"{BASE}/platform/audit", params={"since": after}, headers=headers)
        assert any(entry["action"] == "test_sinds" for entry in since_before.json()["entries"])
        assert not any(entry["action"] == "test_sinds" for entry in since_after.json()["entries"])

        until_after = await client.get(f"{BASE}/platform/audit", params={"until": after}, headers=headers)
        until_before = await client.get(f"{BASE}/platform/audit", params={"until": before}, headers=headers)
        assert any(entry["action"] == "test_sinds" for entry in until_after.json()["entries"])
        assert not any(entry["action"] == "test_sinds" for entry in until_before.json()["entries"])

    async def test_reason_code_filters_to_that_refusal(self, client, app, data):
        await app.state.audit_log.write("test_a", ANONYMOUS, "refused", reason_code="CODE_A")
        await app.state.audit_log.write("test_b", ANONYMOUS, "refused", reason_code="CODE_B")
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")

        response = await client.get(f"{BASE}/platform/audit", params={"reasonCode": "CODE_A"}, headers=headers)

        actions = {entry["action"] for entry in response.json()["entries"]}
        assert "test_a" in actions
        assert "test_b" not in actions

    async def test_group_filters_to_that_groups_rows(self, client, app, data):
        await app.state.audit_log.write(
            "test_groep", ANONYMOUS, "allowed", refs={"group": "team", "site": "site"}
        )
        await app.state.audit_log.write(
            "test_ander", ANONYMOUS, "allowed", refs={"group": "ander", "site": "site"}
        )
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")

        response = await client.get(f"{BASE}/platform/audit", params={"group": "team"}, headers=headers)

        actions = {entry["action"] for entry in response.json()["entries"]}
        assert "test_groep" in actions
        assert "test_ander" not in actions


class TestActorLookupBranches:
    """The loop-shaped paths in the pseudonym lookups, and the exact
    sso_subject match that the forward lookup only reaches by name."""

    async def test_an_exact_sso_subject_resolves_to_the_member_directly(self, client, app, data):
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")

        response = await client.post(
            f"{BASE}/platform/audit/actor-pseudonym",
            json={"identifier": data.member_a.sso_subject, "reason": "onderzoek naar een melding"},
            headers=headers,
        )

        assert response.status_code == 200
        assert response.json()["resolvedAs"] == "member"
        assert response.json()["actorPseudonym"] == pseudonymise(
            app.state.settings.audit_pepper, data.member_a.sso_subject
        )

    async def test_a_content_viewer_pseudonym_is_found_past_one_that_does_not_match(
        self, client, app, factory, data
    ):
        async with factory() as db:
            db.add_all(
                [
                    ContentViewer(sso_subject="kijker-een", email="een@example.nl"),
                    ContentViewer(sso_subject="kijker-twee", email="twee@example.nl"),
                ]
            )
            await db.commit()
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        pseudonym = pseudonymise(app.state.settings.audit_pepper, "kijker-twee")

        response = await client.post(
            f"{BASE}/platform/audit/actor-identity",
            json={"actorPseudonym": pseudonym, "reason": "onderzoek naar een melding"},
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "content_viewer"
        assert body["email"] == "twee@example.nl"

    async def test_a_ci_pseudonym_is_found_past_a_repository_that_does_not_match(
        self, client, app, factory, data
    ):
        async with factory() as db:
            second_site = Site(
                group_id=data.group.id, slug="tweede", title="Tweede", access_base=AccessBase.SITE_TEAM
            )
            db.add(second_site)
            await db.flush()
            db.add_all(
                [
                    SiteRepository(
                        site_id=data.site.id,
                        provider=CiProvider.GITHUB,
                        host="https://github.com",
                        owner="minbzk",
                        repo="een",
                        repository_id=1,
                        owner_id=2,
                        live_branch="main",
                    ),
                    SiteRepository(
                        site_id=second_site.id,
                        provider=CiProvider.GITHUB,
                        host="https://github.com",
                        owner="minbzk",
                        repo="twee",
                        repository_id=2,
                        owner_id=2,
                        live_branch="main",
                    ),
                ]
            )
            await db.commit()
        headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        pseudonym = pseudonymise(app.state.settings.audit_pepper, "github:https://github.com:2")

        response = await client.post(
            f"{BASE}/platform/audit/actor-identity",
            json={"actorPseudonym": pseudonym, "reason": "onderzoek naar een melding"},
            headers=headers,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "ci"
        assert body["repository"] == "minbzk/twee"


class TestIpRevealDecryptFailure:
    async def test_a_key_that_matches_neither_current_nor_previous_is_404(
        self, client, app, factory, data
    ):
        """decrypt_ip raising IpDecryptError (the pepper rotated further than
        the single previous key covers) is indistinguishable from no
        encrypted IP at all to the caller."""
        headers = login(client, app, sub="lid-a", email="a@example.nl")
        await client.post(
            f"{BASE}/groups/team/sites", json={"title": "X", "slug": "x"}, headers=headers
        )
        async with factory() as db:
            entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "site_create"))
        assert entry.ip_encrypted is not None

        app.state.settings.audit_ip_key = "bm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm4="
        app.state.audit_log._ip_key = app.state.settings.audit_ip_key_bytes

        admin_headers = login(client, app, sub="admin-sub", email="admin@example.nl")
        response = await client.post(
            f"{BASE}/platform/audit/entries/{entry.id}/ip",
            json={"reason": "onderzoek naar een melding"},
            headers=admin_headers,
        )

        assert response.status_code == 404
        assert response.json()["code"] == "AUDIT_IP_UNKNOWN"
