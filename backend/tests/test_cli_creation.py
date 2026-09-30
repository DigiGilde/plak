"""Creating a group or a site and linking a repository with the CLI token
(`plak group create`, `plak site create`, `plak site link`), and the edge of
that: those three routes of api/admin.py take a bearer, no other admin
route does.

The app under test wires the admin router, the CLI router and
BearerOutsideDeploysMiddleware the way main.py does. The full stack (origin
guard, rate-limit middleware) is in test_api_integration.py.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.routing import APIRoute
from helpers_ci import MockCi
from helpers_oidc import APP_BASE_URL, CONTENT_BASE_URL, make_test_client, set_session_cookie
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from plak.api import admin
from plak.api import cli as cli_api
from plak.api.admin import CREATION_MAX_PER_WINDOW, CREATION_WINDOW_S, make_admin_router
from plak.api.deploys import BearerOutsideDeploysMiddleware
from plak.api.errors import register_error_handlers
from plak.audit.log import AuditLog
from plak.audit.pseudonymisation import pseudonymise
from plak.auth.sessions import CSRF_COOKIE, CSRF_HEADER, SessionStore
from plak.ci.providers import ProviderClient
from plak.cli import service as cli
from plak.config import Settings
from plak.constants import AccessBase, Role
from plak.db import make_session_factory
from plak.ingest.store import ContentStore
from plak.models.audit import AuditLogEntry
from plak.models.ci import SiteRepository
from plak.models.cli import CliSession
from plak.models.identity import Group, GroupMember, Member, MemberStatus, PlatformRole, SiteMember
from plak.models.publication import Site

BASE = "/-/api/v1"
PROBLEM = "application/problem+json"
PEPPER = "audit-pepper-van-minstens-32-bytes!!"
WWW_AUTHENTICATE = 'Bearer realm="plak", error="invalid_token"'


def _settings(content_root) -> Settings:
    return Settings(
        db_url="postgresql+asyncpg://plak:plak@localhost:5432/plak",
        content_root=content_root,
        oidc_issuer="https://idp.example",
        oidc_client_id="plak-client",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr:hoog",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper=PEPPER,
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
def app(factory, tmp_path) -> FastAPI:
    content_root = tmp_path / "content"
    content_root.mkdir()
    settings = _settings(content_root)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_store = SessionStore()
    app.state.session_factory = factory
    app.state.content_store = ContentStore(content_root)
    app.state.audit_log = AuditLog(factory, settings.audit_pepper, settings.audit_ip_key_bytes)
    ci = MockCi()
    ci.add_github("MinBZK", "Website", 1001, 2002)
    app.state.ci_providers = ProviderClient(ci.client())
    register_error_handlers(app)
    app.add_middleware(BearerOutsideDeploysMiddleware)
    app.include_router(cli_api.router)
    app.include_router(make_admin_router())
    return app


@pytest_asyncio.fixture
async def client(app):
    async with make_test_client(app) as client:
        yield client


async def _member(factory, sub: str, *, platform_role: PlatformRole = PlatformRole.MEMBER) -> Member:
    async with factory() as db:
        member = Member(
            sso_subject=sub, email=f"{sub}@example.nl", status=MemberStatus.ACTIVE, platform_role=platform_role
        )
        db.add(member)
        await db.commit()
        return member


async def _token(factory, member: Member) -> str:
    """A CLI access token for this member, through the real device flow."""
    async with factory() as db:
        created = await cli.create_device_authorization(db, client_name="plak-cli", ip_truncated=None)
    async with factory() as db:
        await cli.decide(db, created.user_code, await db.get(Member, member.id), approve=True)
    async with factory() as db:
        return (await cli.exchange_device_code(db, created.device_code)).access_token


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _group(
    factory,
    slug: str,
    *,
    members: dict[Member, Role] | None = None,
    base: AccessBase = AccessBase.SITE_TEAM,
    keys: bool = False,
    invitees: bool = False,
) -> Group:
    async with factory() as db:
        group = Group(
            slug=slug,
            name=slug.title(),
            default_access_base=base,
            default_access_keys=keys,
            default_access_invitees=invitees,
        )
        db.add(group)
        await db.flush()
        for member, role in (members or {}).items():
            db.add(GroupMember(group_id=group.id, member_id=member.id, role=role))
        await db.commit()
        return group


def _login(client, app, sub: str) -> dict[str, str]:
    session = set_session_cookie(client, app, sub=sub, email=f"{sub}@example.nl")
    client.cookies.set(CSRF_COOKIE, session.csrf_token, domain="plak.example", path="/")
    return {CSRF_HEADER: session.csrf_token}


async def _rows(factory, action: str | None = None) -> list[AuditLogEntry]:
    async with factory() as db:
        stmt = select(AuditLogEntry).order_by(AuditLogEntry.occurred_at)
        if action is not None:
            stmt = stmt.where(AuditLogEntry.action == action)
        return list(await db.scalars(stmt))


async def _cli_session_id(factory, member: Member) -> str:
    async with factory() as db:
        return str(await db.scalar(select(CliSession.id).where(CliSession.member_id == member.id)))


async def _group_exists(factory, slug: str) -> bool:
    async with factory() as db:
        return await db.scalar(select(Group).where(Group.slug == slug)) is not None


async def _site(factory, group_slug: str, site_slug: str) -> Site | None:
    async with factory() as db:
        return await db.scalar(
            select(Site).join(Group, Group.id == Site.group_id).where(Group.slug == group_slug, Site.slug == site_slug)
        )


def _problem(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith(PROBLEM)
    body = response.json()
    assert body["code"] == code
    return body


# -- Creating a group with the CLI token --------------------------------------


class TestCreateGroupWithTheCliToken:
    async def test_creates_the_group_with_the_default_access_and_no_csrf(self, client, factory):
        member = await _member(factory, "maker")
        token = await _token(factory, member)

        response = await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=_bearer(token))

        assert response.status_code == 201, response.text
        assert response.json()["defaultAccess"] == {"base": "site_team", "keys": False, "invitees": False}

    async def test_the_creator_becomes_group_admin(self, client, factory):
        """What makes choosing the access up front no bypass: the creator
        could set it on the next request anyway."""
        member = await _member(factory, "maker")
        token = await _token(factory, member)

        await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=_bearer(token))

        async with factory() as db:
            role = await db.scalar(
                select(GroupMember.role)
                .join(Group, Group.id == GroupMember.group_id)
                .where(Group.slug == "team", GroupMember.member_id == member.id)
            )
        assert role == Role.ADMIN

    async def test_a_partial_default_access_fills_the_rest_from_the_server_default(self, client, factory):
        member = await _member(factory, "maker")
        token = await _token(factory, member)

        response = await client.post(
            f"{BASE}/groups",
            json={"name": "Team", "slug": "team", "defaultAccess": {"base": "nobody", "keys": True}},
            headers=_bearer(token),
        )

        assert response.status_code == 201, response.text
        assert response.json()["defaultAccess"] == {"base": "nobody", "keys": True, "invitees": False}
        async with factory() as db:
            group = await db.scalar(select(Group).where(Group.slug == "team"))
        assert (group.default_access_base, group.default_access_keys, group.default_access_invitees) == (
            AccessBase.NOBODY,
            True,
            False,
        )

    async def test_the_audit_row_says_cli_and_records_the_access(self, client, factory):
        member = await _member(factory, "maker")
        token = await _token(factory, member)

        await client.post(
            f"{BASE}/groups",
            json={"name": "Team", "slug": "team", "defaultAccess": {"base": "public", "invitees": True}},
            headers=_bearer(token),
        )

        [row] = await _rows(factory, "group_create")
        assert row.result == "allowed"
        assert row.actor_pseudonym == pseudonymise(PEPPER, "maker")
        assert row.refs == {
            "group": "team",
            "base": "public",
            "keys": False,
            "invitees": True,
            "via": "cli",
            "cli_session": await _cli_session_id(factory, member),
        }

    async def test_the_same_slug_twice_is_409(self, client, factory):
        member = await _member(factory, "maker")
        token = await _token(factory, member)
        await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=_bearer(token))

        response = await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=_bearer(token))

        _problem(response, 409, "SLUG_EXISTS")

    async def test_an_unknown_access_base_is_422_and_creates_nothing(self, client, factory):
        member = await _member(factory, "maker")
        token = await _token(factory, member)

        response = await client.post(
            f"{BASE}/groups",
            json={"name": "Team", "slug": "team", "defaultAccess": {"base": "everyone"}},
            headers=_bearer(token),
        )

        assert response.status_code == 422
        assert not await _group_exists(factory, "team")


class TestCreateGroupWithASession:
    async def test_without_default_access_the_group_starts_on_site_team(self, client, app, factory):
        await _member(factory, "maker")
        headers = _login(client, app, "maker")

        response = await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=headers)

        assert response.status_code == 201
        assert response.json()["defaultAccess"] == {"base": "site_team", "keys": False, "invitees": False}
        [row] = await _rows(factory, "group_create")
        # A session leaves no CLI trace in the row.
        assert row.refs == {"group": "team", "base": "site_team", "keys": False, "invitees": False}

    async def test_the_session_takes_a_default_access_too(self, client, app, factory):
        await _member(factory, "maker")
        headers = _login(client, app, "maker")

        response = await client.post(
            f"{BASE}/groups",
            json={"name": "Team", "slug": "team", "defaultAccess": {"base": "sso", "keys": True, "invitees": True}},
            headers=headers,
        )

        assert response.json()["defaultAccess"] == {"base": "sso", "keys": True, "invitees": True}

    async def test_the_session_still_needs_csrf(self, client, app, factory):
        await _member(factory, "maker")
        _login(client, app, "maker")

        response = await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"})

        _problem(response, 403, "CSRF_INVALID")
        assert not await _group_exists(factory, "team")


# -- Creating a site with the CLI token ---------------------------------------


class TestCreateSiteWithTheCliToken:
    async def test_an_editor_creates_a_site_that_inherits_the_group_default(self, client, factory):
        member = await _member(factory, "redacteur")
        await _group(factory, "team", members={member: Role.EDITOR}, base=AccessBase.SSO, keys=True)
        token = await _token(factory, member)

        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "Docs", "slug": "docs"}, headers=_bearer(token)
        )

        assert response.status_code == 201, response.text
        assert response.json()["access"] == {"base": "sso", "keys": True, "invitees": False}

    async def test_the_creator_becomes_site_admin(self, client, factory):
        """The editor who creates the site may set its access afterwards
        (PUT .../access needs site admin), so choosing it up front adds
        nothing they could not do."""
        member = await _member(factory, "redacteur")
        await _group(factory, "team", members={member: Role.EDITOR})
        token = await _token(factory, member)

        await client.post(f"{BASE}/groups/team/sites", json={"title": "Docs", "slug": "docs"}, headers=_bearer(token))

        site = await _site(factory, "team", "docs")
        async with factory() as db:
            role = await db.scalar(
                select(SiteMember.role).where(SiteMember.site_id == site.id, SiteMember.member_id == member.id)
            )
        assert role == Role.ADMIN
        assert site.created_by == member.id

    async def test_given_fields_override_and_the_rest_follows_the_group(self, client, factory):
        member = await _member(factory, "redacteur")
        await _group(factory, "team", members={member: Role.EDITOR}, base=AccessBase.NOBODY, keys=True, invitees=True)
        token = await _token(factory, member)

        response = await client.post(
            f"{BASE}/groups/team/sites",
            json={"title": "Docs", "slug": "docs", "access": {"base": "public"}},
            headers=_bearer(token),
        )

        assert response.json()["access"] == {"base": "public", "keys": True, "invitees": True}

    async def test_a_full_access_is_taken_as_given(self, client, factory):
        member = await _member(factory, "redacteur")
        await _group(factory, "team", members={member: Role.EDITOR}, base=AccessBase.PUBLIC, keys=True, invitees=True)
        token = await _token(factory, member)

        response = await client.post(
            f"{BASE}/groups/team/sites",
            json={"title": "Docs", "slug": "docs", "access": {"base": "nobody", "keys": False, "invitees": False}},
            headers=_bearer(token),
        )

        assert response.json()["access"] == {"base": "nobody", "keys": False, "invitees": False}
        site = await _site(factory, "team", "docs")
        assert (site.access_base, site.access_keys, site.access_invitees) == (AccessBase.NOBODY, False, False)

    async def test_the_audit_row_says_cli_and_records_the_access(self, client, factory):
        member = await _member(factory, "redacteur")
        await _group(factory, "team", members={member: Role.EDITOR})
        token = await _token(factory, member)

        await client.post(
            f"{BASE}/groups/team/sites",
            json={"title": "Docs", "slug": "docs", "access": {"keys": True}},
            headers=_bearer(token),
        )

        [row] = await _rows(factory, "site_create")
        assert row.actor_pseudonym == pseudonymise(PEPPER, "redacteur")
        assert row.refs == {
            "group": "team",
            "site": "docs",
            "base": "site_team",
            "keys": True,
            "invitees": False,
            "via": "cli",
            "cli_session": await _cli_session_id(factory, member),
        }

    async def test_the_session_path_records_the_inherited_access_without_cli(self, client, app, factory):
        member = await _member(factory, "redacteur")
        await _group(factory, "team", members={member: Role.EDITOR})
        headers = _login(client, app, "redacteur")

        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "Docs", "slug": "docs"}, headers=headers
        )

        assert response.status_code == 201
        [row] = await _rows(factory, "site_create")
        assert row.refs == {"group": "team", "site": "docs", "base": "site_team", "keys": False, "invitees": False}


# -- Refusals on the creation routes, one per ground --------------------------


class TestCreationRefusals:
    async def test_a_ci_id_token_is_401_and_names_the_cli_token(self, client, factory):
        await _group(factory, "team")
        jwt_shaped = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln"

        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "Docs", "slug": "docs"}, headers=_bearer(jwt_shaped)
        )

        body = _problem(response, 401, "TOKEN_INVALID")
        assert "plak login" in body["detail"]
        assert response.headers["WWW-Authenticate"] == WWW_AUTHENTICATE
        assert await _site(factory, "team", "docs") is None

    async def test_an_empty_bearer_is_401(self, client, factory):
        response = await client.post(
            f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers={"Authorization": "Bearer "}
        )

        _problem(response, 401, "TOKEN_INVALID")
        assert not await _group_exists(factory, "team")

    async def test_an_unknown_cli_token_is_401(self, client, factory):
        response = await client.post(
            f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=_bearer("plakcli_abc_def")
        )

        _problem(response, 401, "TOKEN_INVALID")
        assert response.headers["WWW-Authenticate"] == WWW_AUTHENTICATE
        assert not await _group_exists(factory, "team")

    async def test_a_revoked_cli_token_is_401(self, client, factory):
        member = await _member(factory, "maker")
        token = await _token(factory, member)
        async with factory() as db:
            await cli.revoke(db, uuid.UUID(await _cli_session_id(factory, member)))

        response = await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=_bearer(token))

        _problem(response, 401, "TOKEN_INVALID")
        assert not await _group_exists(factory, "team")

    async def test_an_expired_cli_token_is_401(self, client, factory):
        member = await _member(factory, "maker")
        token = await _token(factory, member)
        async with factory() as db:
            await db.execute(update(CliSession).values(access_expires_at=datetime.now(UTC) - timedelta(minutes=1)))
            await db.commit()

        response = await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=_bearer(token))

        _problem(response, 401, "TOKEN_INVALID")
        assert not await _group_exists(factory, "team")

    async def test_a_deactivated_member_is_403(self, client, factory):
        member = await _member(factory, "maker")
        token = await _token(factory, member)
        async with factory() as db:
            await db.execute(update(Member).where(Member.id == member.id).values(status=MemberStatus.DEACTIVATED))
            await db.commit()

        response = await client.post(f"{BASE}/groups", json={"name": "Team", "slug": "team"}, headers=_bearer(token))

        _problem(response, 403, "MEMBER_NOT_ACTIVE")
        assert not await _group_exists(factory, "team")

    async def test_a_valid_session_does_not_rescue_a_bad_token(self, client, app, factory):
        """With a bearer header the bearer decides; a session cookie and CSRF
        header riding along are not a second chance."""
        await _member(factory, "maker")
        headers = _login(client, app, "maker")

        response = await client.post(
            f"{BASE}/groups",
            json={"name": "Team", "slug": "team"},
            headers={**headers, "Authorization": "Bearer plakcli_abc_def"},
        )

        _problem(response, 401, "TOKEN_INVALID")
        assert not await _group_exists(factory, "team")

    async def test_a_reader_may_not_create_a_site_and_the_refusal_is_audited_as_the_cli_member(
        self, client, factory
    ):
        member = await _member(factory, "lezer")
        await _group(factory, "team", members={member: Role.READER})
        token = await _token(factory, member)

        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "Docs", "slug": "docs"}, headers=_bearer(token)
        )

        _problem(response, 403, "INSUFFICIENT_ROLE")
        assert await _site(factory, "team", "docs") is None
        [row] = await _rows(factory, "admin_access")
        assert row.result == "refused"
        assert row.reason_code == "INSUFFICIENT_ROLE"
        assert row.actor_pseudonym == pseudonymise(PEPPER, "lezer")
        assert row.refs == {
            "method": "POST",
            "route": f"{BASE}/groups/{{group_slug}}/sites",
            "status": 403,
            "via": "cli",
            "cli_session": await _cli_session_id(factory, member),
        }

    async def test_a_member_outside_the_group_may_not_create_a_site(self, client, factory):
        member = await _member(factory, "buitenstaander")
        await _group(factory, "team")
        token = await _token(factory, member)

        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "Docs", "slug": "docs"}, headers=_bearer(token)
        )

        _problem(response, 403, "INSUFFICIENT_ROLE")
        assert await _site(factory, "team", "docs") is None

    async def test_a_platform_admin_without_a_group_role_may_not_create_a_site(self, client, factory):
        member = await _member(factory, "platformbeheer", platform_role=PlatformRole.ADMIN)
        await _group(factory, "team")
        token = await _token(factory, member)

        response = await client.post(
            f"{BASE}/groups/team/sites", json={"title": "Docs", "slug": "docs"}, headers=_bearer(token)
        )

        _problem(response, 403, "INSUFFICIENT_ROLE")
        assert await _site(factory, "team", "docs") is None

    async def test_an_unknown_group_is_404(self, client, factory):
        member = await _member(factory, "maker")
        token = await _token(factory, member)

        response = await client.post(
            f"{BASE}/groups/nergens/sites", json={"title": "Docs", "slug": "docs"}, headers=_bearer(token)
        )

        _problem(response, 404, "UNKNOWN_GROUP")


# -- Linking a repository with the CLI token -------------------------------------


REPOSITORY = f"{BASE}/sites/team/docs/repository"
PRIVATE = {"provider": "github", "owner": "minbzk", "repo": "prive", "liveBranch": "main",
           "repositoryId": 5005, "ownerId": 6006}


async def _site_with(factory, members: dict[Member, Role]) -> None:
    group = await _group(factory, "team", members=members)
    async with factory() as db:
        db.add(Site(group_id=group.id, slug="docs", title="Docs", access_base=AccessBase.SITE_TEAM))
        await db.commit()


async def _linked(factory) -> SiteRepository | None:
    async with factory() as db:
        return await db.scalar(select(SiteRepository))


class TestLinkRepositoryWithTheCliToken:
    async def test_a_site_admin_links_a_private_repository_without_csrf(self, client, factory):
        member = await _member(factory, "beheerder")
        await _site_with(factory, {member: Role.ADMIN})
        token = await _token(factory, member)

        response = await client.put(REPOSITORY, json=PRIVATE, headers=_bearer(token))

        assert response.status_code == 200, response.text
        assert (response.json()["repositoryId"], response.json()["ownerId"]) == (5005, 6006)
        linked = await _linked(factory)
        assert (linked.owner, linked.repo, linked.created_by) == ("minbzk", "prive", member.id)

    async def test_the_audit_row_says_cli(self, client, factory):
        member = await _member(factory, "beheerder")
        await _site_with(factory, {member: Role.ADMIN})
        token = await _token(factory, member)

        await client.put(REPOSITORY, json=PRIVATE, headers=_bearer(token))

        [row] = await _rows(factory, "site_repository_set")
        assert row.actor_pseudonym == pseudonymise(PEPPER, "beheerder")
        assert row.refs["via"] == "cli"
        assert row.refs["cli_session"] == await _cli_session_id(factory, member)
        assert row.refs["ids_confirmed"] is False

    async def test_a_public_repository_is_looked_up_as_with_a_session(self, client, factory):
        member = await _member(factory, "beheerder")
        await _site_with(factory, {member: Role.ADMIN})
        token = await _token(factory, member)

        response = await client.put(
            REPOSITORY, json={"provider": "github", "owner": "minbzk", "repo": "website", "liveBranch": None},
            headers=_bearer(token),
        )

        assert response.status_code == 200, response.text
        assert (response.json()["owner"], response.json()["repositoryId"]) == ("MinBZK", 1001)

    async def test_a_group_editor_may_not_link_and_the_refusal_is_audited_as_the_cli_member(self, client, factory):
        member = await _member(factory, "redacteur")
        await _site_with(factory, {member: Role.EDITOR})
        token = await _token(factory, member)

        response = await client.put(REPOSITORY, json=PRIVATE, headers=_bearer(token))

        _problem(response, 403, "INSUFFICIENT_ROLE")
        assert await _linked(factory) is None
        [row] = await _rows(factory, "admin_access")
        assert row.result == "refused"
        assert row.actor_pseudonym == pseudonymise(PEPPER, "redacteur")
        assert row.refs["via"] == "cli"

    async def test_a_ci_id_token_links_nothing(self, client, factory):
        await _site_with(factory, {})
        jwt_shaped = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln"

        response = await client.put(REPOSITORY, json=PRIVATE, headers=_bearer(jwt_shaped))

        _problem(response, 401, "TOKEN_INVALID")
        assert response.headers["WWW-Authenticate"] == WWW_AUTHENTICATE
        assert await _linked(factory) is None

    async def test_a_revoked_cli_token_links_nothing(self, client, factory):
        member = await _member(factory, "beheerder")
        await _site_with(factory, {member: Role.ADMIN})
        token = await _token(factory, member)
        async with factory() as db:
            await cli.revoke(db, uuid.UUID(await _cli_session_id(factory, member)))

        response = await client.put(REPOSITORY, json=PRIVATE, headers=_bearer(token))

        _problem(response, 401, "TOKEN_INVALID")
        assert await _linked(factory) is None

    async def test_a_deactivated_admin_links_nothing(self, client, factory):
        member = await _member(factory, "beheerder")
        await _site_with(factory, {member: Role.ADMIN})
        token = await _token(factory, member)
        async with factory() as db:
            await db.execute(update(Member).where(Member.id == member.id).values(status=MemberStatus.DEACTIVATED))
            await db.commit()

        response = await client.put(REPOSITORY, json=PRIVATE, headers=_bearer(token))

        _problem(response, 403, "MEMBER_NOT_ACTIVE")
        assert await _linked(factory) is None

    async def test_a_valid_session_does_not_rescue_a_bad_token(self, client, app, factory):
        member = await _member(factory, "beheerder")
        await _site_with(factory, {member: Role.ADMIN})
        headers = _login(client, app, "beheerder")

        response = await client.put(
            REPOSITORY, json=PRIVATE, headers={**headers, "Authorization": "Bearer plakcli_abc_def"}
        )

        _problem(response, 401, "TOKEN_INVALID")
        assert await _linked(factory) is None


# -- The creation budget ---------------------------------------------------------


class TestCreationBudget:
    async def test_groups_and_sites_through_session_and_cli_share_one_budget(self, client, app, factory):
        member = await _member(factory, "druk")
        await _group(factory, "team", members={member: Role.EDITOR})
        token = await _token(factory, member)
        headers = _login(client, app, "druk")

        half = CREATION_MAX_PER_WINDOW // 2
        for index in range(half):
            response = await client.post(
                f"{BASE}/groups", json={"name": f"G{index}", "slug": f"g{index}"}, headers=headers
            )
            assert response.status_code == 201, response.text
        client.cookies.clear()
        for index in range(CREATION_MAX_PER_WINDOW - half):
            response = await client.post(
                f"{BASE}/groups/team/sites", json={"title": f"S{index}", "slug": f"s{index}"}, headers=_bearer(token)
            )
            assert response.status_code == 201, response.text

        refused = await client.post(
            f"{BASE}/groups", json={"name": "Een te veel", "slug": "te-veel"}, headers=_bearer(token)
        )

        body = _problem(refused, 429, "TOO_MANY_CREATIONS")
        assert str(CREATION_MAX_PER_WINDOW) in body["detail"]
        assert 1 <= int(refused.headers["Retry-After"]) <= CREATION_WINDOW_S
        assert not await _group_exists(factory, "te-veel")
        [row] = await _rows(factory, "admin_access")
        assert (row.result, row.reason_code) == ("refused", "TOO_MANY_CREATIONS")
        assert row.actor_pseudonym == pseudonymise(PEPPER, "druk")
        assert row.refs["via"] == "cli"

    async def test_the_session_path_is_limited_too(self, client, app, factory, monkeypatch):
        monkeypatch.setattr(admin, "CREATION_MAX_PER_WINDOW", 1)
        await _member(factory, "druk")
        headers = _login(client, app, "druk")
        await client.post(f"{BASE}/groups", json={"name": "Een", "slug": "een"}, headers=headers)

        refused = await client.post(f"{BASE}/groups", json={"name": "Twee", "slug": "twee"}, headers=headers)

        _problem(refused, 429, "TOO_MANY_CREATIONS")
        assert "Retry-After" in refused.headers
        assert not await _group_exists(factory, "twee")

    async def test_the_budget_is_per_member(self, client, factory, monkeypatch):
        monkeypatch.setattr(admin, "CREATION_MAX_PER_WINDOW", 1)
        first = await _member(factory, "eerste")
        second = await _member(factory, "tweede")
        first_token = await _token(factory, first)
        second_token = await _token(factory, second)
        await client.post(f"{BASE}/groups", json={"name": "Een", "slug": "een"}, headers=_bearer(first_token))

        response = await client.post(
            f"{BASE}/groups", json={"name": "Twee", "slug": "twee"}, headers=_bearer(second_token)
        )

        assert response.status_code == 201

    async def test_invalid_input_and_a_refused_role_do_not_spend_the_budget(self, client, factory, monkeypatch):
        monkeypatch.setattr(admin, "CREATION_MAX_PER_WINDOW", 1)
        member = await _member(factory, "druk")
        await _group(factory, "team", members={member: Role.READER})
        token = await _token(factory, member)
        await client.post(f"{BASE}/groups", json={"name": "X", "slug": "Geen Slug"}, headers=_bearer(token))
        await client.post(f"{BASE}/groups/team/sites", json={"title": "X", "slug": "x"}, headers=_bearer(token))

        response = await client.post(f"{BASE}/groups", json={"name": "Een", "slug": "een"}, headers=_bearer(token))

        assert response.status_code == 201


# -- Bearer on every other admin route: refused exactly as before ------------


def _admin_routes() -> list[tuple[str, str]]:
    """Every (method, concrete path) the admin router serves, apart from the
    two creation routes and the repository link."""
    router = make_admin_router()
    routes = []
    for route in router.routes:
        assert isinstance(route, APIRoute)
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method_ in sorted(route.methods):
            if method_ == "POST" and (path == f"{BASE}/groups" or path == f"{BASE}/groups/x/sites"):
                continue
            if method_ == "PUT" and path == f"{BASE}/sites/x/x/repository":
                continue
            routes.append((method_, path))
    return routes


ADMIN_ROUTES = _admin_routes()


@pytest.mark.parametrize(("method_", "path"), ADMIN_ROUTES, ids=[f"{m} {p}" for m, p in ADMIN_ROUTES])
async def test_a_valid_cli_token_on_any_other_admin_route_is_refused(client, factory, method_, path):
    member = await _member(factory, "maker")
    token = await _token(factory, member)

    response = await client.request(method_, path, headers=_bearer(token), json={})

    body = _problem(response, 401, "BEARER_NOT_ACCEPTED")
    assert response.headers["WWW-Authenticate"] == WWW_AUTHENTICATE
    assert body["detail"] == (
        "Bearer authentication is accepted on the deploy and CLI endpoints, for creating a group or site and "
        "for linking a repository only."
    )


class TestNeighbouringRoutesStayShut:
    """The routes next to the two that opened, with a CLI token of a member
    who holds every role the action would need, and a session besides."""

    @pytest_asyncio.fixture
    async def owner(self, factory) -> tuple[Member, str]:
        member = await _member(factory, "eigenaar", platform_role=PlatformRole.ADMIN)
        group = await _group(factory, "team", members={member: Role.ADMIN})
        async with factory() as db:
            site = Site(group_id=group.id, slug="docs", title="Docs", access_base=AccessBase.SITE_TEAM)
            db.add(site)
            await db.commit()
        return member, await _token(factory, member)

    async def test_delete_group(self, client, factory, owner):
        async with factory() as db:
            await db.execute(Site.__table__.delete())
            await db.commit()
        response = await client.delete(f"{BASE}/groups/team", headers=_bearer(owner[1]))
        _problem(response, 401, "BEARER_NOT_ACCEPTED")
        assert await _group_exists(factory, "team")

    async def test_delete_site(self, client, factory, owner):
        response = await client.delete(f"{BASE}/sites/team/docs", headers=_bearer(owner[1]))
        _problem(response, 401, "BEARER_NOT_ACCEPTED")
        assert await _site(factory, "team", "docs") is not None

    async def test_put_site_access(self, client, factory, owner):
        response = await client.put(
            f"{BASE}/sites/team/docs/access", json={"base": "public"}, headers=_bearer(owner[1])
        )
        _problem(response, 401, "BEARER_NOT_ACCEPTED")
        assert (await _site(factory, "team", "docs")).access_base == AccessBase.SITE_TEAM

    async def test_put_group_default_access(self, client, factory, owner):
        response = await client.put(
            f"{BASE}/groups/team/default-access", json={"base": "public"}, headers=_bearer(owner[1])
        )
        _problem(response, 401, "BEARER_NOT_ACCEPTED")
        async with factory() as db:
            group = await db.scalar(select(Group).where(Group.slug == "team"))
        assert group.default_access_base == AccessBase.SITE_TEAM

    async def test_add_a_group_member(self, client, factory, owner):
        other = await _member(factory, "ander")
        response = await client.post(
            f"{BASE}/groups/team/members", json={"identifier": other.email, "role": "admin"}, headers=_bearer(owner[1])
        )
        _problem(response, 401, "BEARER_NOT_ACCEPTED")
        async with factory() as db:
            assert await db.scalar(select(GroupMember).where(GroupMember.member_id == other.id)) is None

    async def test_add_a_site_member(self, client, factory, owner):
        other = await _member(factory, "ander")
        response = await client.post(
            f"{BASE}/sites/team/docs/members", json={"identifier": other.email}, headers=_bearer(owner[1])
        )
        _problem(response, 401, "BEARER_NOT_ACCEPTED")
        async with factory() as db:
            assert await db.scalar(select(SiteMember).where(SiteMember.member_id == other.id)) is None

    async def test_unlink_a_repository(self, client, factory, owner):
        async with factory() as db:
            site = await db.scalar(select(Site).where(Site.slug == "docs"))
            db.add(SiteRepository(site_id=site.id, provider="github", host="https://github.com", owner="minbzk",
                                  repo="website", repository_id=1001, owner_id=2002))
            await db.commit()
        response = await client.delete(f"{BASE}/sites/team/docs/repository", headers=_bearer(owner[1]))
        _problem(response, 401, "BEARER_NOT_ACCEPTED")
        assert await _linked(factory) is not None

    async def test_read_the_linked_repository(self, client, owner):
        response = await client.get(f"{BASE}/sites/team/docs/repository", headers=_bearer(owner[1]))
        _problem(response, 401, "BEARER_NOT_ACCEPTED")

    async def test_get_group(self, client, owner):
        response = await client.get(f"{BASE}/groups/team", headers=_bearer(owner[1]))
        _problem(response, 401, "BEARER_NOT_ACCEPTED")

    async def test_a_session_riding_along_does_not_open_them(self, client, app, factory, owner):
        headers = _login(client, app, "eigenaar")
        response = await client.delete(f"{BASE}/sites/team/docs", headers={**headers, **_bearer(owner[1])})
        _problem(response, 401, "BEARER_NOT_ACCEPTED")
        assert await _site(factory, "team", "docs") is not None

    @pytest.mark.parametrize(
        ("method_", "path"),
        [
            ("POST", f"{BASE}/groups/"),
            ("POST", f"{BASE}/groups/team/sites/"),
            ("POST", f"{BASE}/groups/team/sites/docs"),
            ("PUT", f"{BASE}/groups/team/sites"),
            ("GET", f"{BASE}/groups"),
            ("POST", f"{BASE}/groups/team/extra/sites"),
            ("POST", f"{BASE}/sites/team/docs/repository"),
            ("PUT", f"{BASE}/sites/team/docs/repository/"),
            ("PUT", f"{BASE}/sites/team/repository"),
            ("PUT", f"{BASE}/sites/team/docs/extra/repository"),
        ],
    )
    async def test_the_opened_paths_open_only_for_their_exact_method_and_shape(
        self, client, owner, method_, path
    ):
        response = await client.request(method_, path, headers=_bearer(owner[1]), json={"title": "X", "slug": "x"})
        _problem(response, 401, "BEARER_NOT_ACCEPTED")
