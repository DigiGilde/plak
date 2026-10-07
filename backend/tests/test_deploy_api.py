"""Tests for api/deploys.py and api/errors.py: CI ID token, CLI token and
session auth, repository trust and live branch, preview semantics, teardown
and problem+json for every failure class (spec §8).

The tests build an app of their own (router + middleware + error handlers)
without touching main.py; the integration wires that in there later. Deploys
really commit in the shared test container; conftest wipes the tables clean
before every DB test.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import tracemalloc
import uuid
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_ci import AUDIENCE, FORGEJO_HOST, OMIT, MockCi
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request

from plak import i18n, messages
from plak.api import deploys
from plak.api.deploys import BearerOutsideDeploysMiddleware
from plak.api.errors import PROBLEM_CONTENT_TYPE, ApiError, register_error_handlers
from plak.audit.log import AuditLog
from plak.audit.pseudonymisation import pseudonymise
from plak.auth import sessions
from plak.ci import trust
from plak.ci.providers import ProviderClient
from plak.ci.tokens import CiTokenVerifier
from plak.cli import service as cli
from plak.config import Settings
from plak.constants import AccessBase, Role
from plak.db import make_engine, make_session_factory
from plak.ingest.service import IngestService, RoomGuard
from plak.ingest.store import ContentStore
from plak.messages import Msg
from plak.models.audit import ActorKind, AuditLogEntry
from plak.models.ci import CiProvider, SiteRepository
from plak.models.cli import CliSession
from plak.models.identity import Group, GroupMember, Member, MemberStatus
from plak.models.publication import Preview, Site, Version, VersionTarget

BASE_URL = "https://plak.example"
DEPLOY_PATH = "/-/api/v1/sites/team-aurora/website/deploys"


def _make_settings(tmp_path: Path, dsn: str, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "db_url": dsn,
        "content_root": tmp_path / "content",
        "oidc_issuer": "https://idp.example",
        "oidc_client_id": "plak",
        "oidc_client_private_jwk": "{}",
        "oidc_required_acr": "urn:acr:hoog",
        "session_secret": "sessie-geheim-van-minstens-32-bytes!",
        "audit_pepper": "audit-pepper-van-minstens-32-bytes!!",
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "environment": "dev",
        "content_base_url": "https://plak.example",
        "base_url": AUDIENCE,
    }
    base.update(overrides)
    return Settings(**base)


def _make_app(settings: Settings, mock_ci: MockCi | None = None) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    app.add_middleware(BearerOutsideDeploysMiddleware)
    app.include_router(deploys.router)

    @app.get("/-/api/v1/groups")
    async def _groups() -> list:  # target for the bearer-elsewhere tests
        return []

    @app.get("/-/api/v1/ratelimit-demo")
    async def _demo() -> None:
        raise ApiError(429, "TOO_MANY_REQUESTS", headers={"Retry-After": "7"})

    engine = make_engine(settings)
    factory = make_session_factory(engine)
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.session_store = sessions.SessionStore()
    app.state.content_store = ContentStore(settings.content_root)
    app.state.audit_log = AuditLog(factory, settings.audit_pepper, settings.audit_ip_key_bytes)
    mock_ci = mock_ci or MockCi()
    app.state.ci_verifier = CiTokenVerifier(settings, mock_ci.client())
    app.state.ci_providers = ProviderClient(mock_ci.client())
    return app


@dataclass(frozen=True)
class Environment:
    app: FastAPI
    settings: Settings
    session_factory: async_sessionmaker[AsyncSession]
    member: Member
    outsider: Member
    group: Group
    site: Site
    second_site: Site
    other_group: Group
    other_site: Site
    ci: MockCi
    cli_token: str

    @property
    def ci_token(self) -> str:
        """A push to main of minbzk/website, the repository linked to team-aurora/website."""
        return self.ci.token()

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=BASE_URL, follow_redirects=False
        )

    def session_for(self, sub: str) -> tuple[dict[str, str], dict[str, str]]:
        """Returns (cookies, headers) for a logged-in admin session with CSRF."""
        session = self.app.state.session_store.create_session(
            sub=sub, email=f"{sub}@example.org", email_verified=True, acr="urn:acr:hoog"
        )
        cookies = {
            sessions.SESSION_COOKIE: sessions.sign(self.settings.session_secret, session.id),
            sessions.CSRF_COOKIE: session.csrf_token,
        }
        headers = {sessions.CSRF_HEADER: session.csrf_token}
        return cookies, headers


async def _cli_login(factory: async_sessionmaker[AsyncSession], member: Member) -> str:
    """Runs the device flow for `member` and returns the CLI access token."""
    async with factory() as db:
        created = await cli.create_device_authorization(db, client_name="plak-cli test", ip_truncated=None)
    async with factory() as db:
        member_row = await db.get(Member, member.id)
        await cli.decide(db, created.user_code, member_row, approve=True)
    async with factory() as db:
        issued = await cli.exchange_device_code(db, created.device_code)
    return issued.access_token


@asynccontextmanager
async def _environment(tmp_path: Path, dsn: str, **overrides: object):
    settings = _make_settings(tmp_path, dsn, **overrides)
    mock_ci = MockCi()
    mock_ci.add_forgejo("minbzk", "website", 3003, 4004)
    app = _make_app(settings, mock_ci)
    factory = app.state.session_factory
    try:
        member = Member(
            id=uuid.uuid4(), sso_subject="sub-actief", email="actief@example.org", status=MemberStatus.ACTIVE
        )
        outsider = Member(
            id=uuid.uuid4(), sso_subject="sub-buiten", email="buiten@example.org", status=MemberStatus.ACTIVE
        )
        group = Group(id=uuid.uuid4(), slug="team-aurora", name="Team Aurora", default_access_base=AccessBase.PUBLIC)
        other_group = Group(
            id=uuid.uuid4(), slug="extern", name="Extern", default_access_base=AccessBase.PUBLIC
        )
        site = Site(
            id=uuid.uuid4(),
            group_id=group.id,
            slug="website",
            title="Website",
            access_base=AccessBase.PUBLIC,
        )
        second_site = Site(
            id=uuid.uuid4(), group_id=group.id, slug="docs", title="Docs", access_base=AccessBase.PUBLIC
        )
        other_site = Site(
            id=uuid.uuid4(),
            group_id=other_group.id,
            slug="site",
            title="Site",
            access_base=AccessBase.PUBLIC,
        )
        async with factory() as db:
            db.add_all([member, outsider, group, other_group])
            await db.flush()
            db.add_all(
                [
                    GroupMember(group_id=group.id, member_id=member.id, role=Role.ADMIN),
                    site,
                    second_site,
                    other_site,
                ]
            )
            await db.commit()
        async with factory() as db:
            db.add(
                SiteRepository(
                    site_id=site.id,
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
        cli_token = await _cli_login(factory, member)

        yield Environment(
            app=app,
            settings=settings,
            session_factory=factory,
            member=member,
            outsider=outsider,
            group=group,
            site=site,
            second_site=second_site,
            other_group=other_group,
            other_site=other_site,
            ci=mock_ci,
            cli_token=cli_token,
        )
    finally:
        await app.state.engine.dispose()


@pytest_asyncio.fixture
async def environment(tmp_path: Path, migrated_dsn: str):
    async with _environment(tmp_path, migrated_dsn) as env:
        yield env


def _zip_bytes(files: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in (files or {"index.html": b"<h1>hoi</h1>"}).items():
            archive.writestr(path, content)
    return buffer.getvalue()


def _upload(data: bytes | None = None) -> dict:
    return {"file": ("site.zip", data if data is not None else _zip_bytes(), "application/zip")}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


BOUNDARY = "plak-testgrens"
MULTIPART_HEADERS = {"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"}


def _part_header(field: str, filename: str | None = None) -> bytes:
    disposition = f'form-data; name="{field}"'
    if filename is not None:
        disposition += f'; filename="{filename}"'
    return f"--{BOUNDARY}\r\nContent-Disposition: {disposition}\r\n\r\n".encode()


def _multipart_parts(
    filename: str, content_chunks: list[bytes], fields: dict[str, str] | None = None
) -> list[bytes]:
    """Hand-built multipart body as separate chunks, so a test can count how
    much of the stream was actually consumed."""
    parts_: list[bytes] = []
    for name, value in (fields or {}).items():
        parts_.append(_part_header(name) + value.encode() + b"\r\n")
    parts_.append(_part_header("file", filename))
    parts_.extend(content_chunks)
    parts_.append(f"\r\n--{BOUNDARY}--\r\n".encode())
    return parts_


class _Counter:
    """Async iterator over chunks that tracks how many have been requested."""

    def __init__(self, parts_: list[bytes]) -> None:
        self.parts_ = parts_
        self.delivered = 0

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._read()

    async def _read(self) -> AsyncIterator[bytes]:
        for part_item in self.parts_:
            self.delivered += 1
            yield part_item


def _tmp_dir(environment: Environment) -> Path:
    return environment.settings.content_root / "_tmp"


def _assert_problem(resp: httpx.Response, status: int) -> dict:
    assert resp.status_code == status
    assert resp.headers["content-type"] == PROBLEM_CONTENT_TYPE
    content = resp.json()
    assert content["status"] == status
    assert content["title"]
    assert content["detail"]
    return content


async def test_live_deploy_with_ci_token(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))

    assert resp.status_code == 201
    version_id = uuid.UUID(resp.json()["versionId"])
    assert resp.json()["url"] == "https://plak.example/team-aurora/website/"

    async with environment.session_factory() as db:
        version = await db.scalar(select(Version).where(Version.id == version_id))
        live_id = await db.scalar(select(Site.live_version_id).where(Site.id == environment.site.id))
        row = (await db.execute(select(AuditLogEntry))).scalar_one()
    assert version is not None
    assert version.target == VersionTarget.LIVE
    assert version.ci_repository == "github.com/minbzk/website"
    assert version.member_id is None
    assert live_id == version_id
    assert (environment.settings.content_root / version.storage_ref / "index.html").is_file()
    assert row.actor_kind == ActorKind.CI
    assert row.actor_pseudonym == pseudonymise(environment.settings.audit_pepper, "github:https://github.com:1001")
    assert row.result == "allowed"
    assert row.refs["repository"] == "minbzk/website"
    assert row.refs["ref"] == "refs/heads/main"
    assert row.refs["sha"] == "0123456789abcdef0123456789abcdef01234567"
    assert row.refs["run_id"] == "4242"
    assert row.refs["workflow"] == "Publiceer"
    assert row.refs["event_name"] == "push"
    assert row.refs["provider"] == "github"


async def test_ci_token_never_lands_in_the_audit_log(environment: Environment) -> None:
    token = environment.ci_token
    async with environment.client() as client:
        await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))
        await client.post("/-/api/v1/sites/team-aurora/docs/deploys", files=_upload(), headers=_bearer(token))

    async with environment.session_factory() as db:
        rows = (await db.execute(select(AuditLogEntry))).scalars().all()
    assert len(rows) == 2
    for row in rows:
        assert token not in str(row.refs)
        assert token.split(".")[2] not in str(row.refs)


async def test_live_deploy_from_another_branch_refused_after_reading(environment: Environment) -> None:
    token = environment.ci.token(ref="refs/heads/feature", sub="repo:minbzk/website:ref:refs/heads/feature")
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))

    content = _assert_problem(resp, 403)
    assert content["code"] == "CI_BRANCH_NOT_ALLOWED"
    assert "main" in content["detail"]
    async with environment.session_factory() as db:
        versions = await db.scalar(select(func.count()).select_from(Version))
        row = (await db.execute(select(AuditLogEntry))).scalar_one()
    assert versions == 0
    assert row.result == "refused"
    assert row.reason_code == "CI_BRANCH_NOT_ALLOWED"
    assert row.actor_kind == ActorKind.CI
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_live_deploy_from_a_pull_request_on_the_live_branch_refused(environment: Environment) -> None:
    token = environment.ci.token(event_name="pull_request")
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))
    assert _assert_problem(resp, 403)["code"] == "CI_BRANCH_NOT_ALLOWED"


async def test_preview_from_any_branch_is_allowed(environment: Environment) -> None:
    token = environment.ci.token(ref="refs/pull/7/merge", event_name="pull_request")
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), data={"preview": "pr-7"}, headers=_bearer(token))
    assert resp.status_code == 201


async def test_without_live_branch_every_branch_may_go_live(environment: Environment) -> None:
    async with environment.session_factory() as db:
        await db.execute(update(SiteRepository).values(live_branch=None))
        await db.commit()
    token = environment.ci.token(ref="refs/heads/feature")
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))
        assert resp.status_code == 201
        # Without a live branch the event allowlist still holds.
        for event in ("pull_request", "workflow_run", "issue_comment"):
            refused = await client.post(
                DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci.token(event_name=event))
            )
            assert _assert_problem(refused, 403)["code"] == "CI_BRANCH_NOT_ALLOWED"
        missing = await client.post(
            DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci.token(event_name=OMIT))
        )
        assert _assert_problem(missing, 403)["code"] == "CI_BRANCH_NOT_ALLOWED"
        preview = await client.post(
            DEPLOY_PATH,
            files=_upload(),
            data={"preview": "pr-3"},
            headers=_bearer(environment.ci.token(event_name="workflow_run")),
        )
        assert preview.status_code == 201


async def test_ci_token_for_a_site_without_that_repository_403(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            "/-/api/v1/sites/team-aurora/docs/deploys", files=_upload(), headers=_bearer(environment.ci_token)
        )
    content = _assert_problem(resp, 403)
    assert content["code"] == "CI_REPOSITORY_NOT_TRUSTED"
    async with environment.session_factory() as db:
        row = (await db.execute(select(AuditLogEntry))).scalar_one()
    # A refused CI actor is pseudonymised on the claimed repository id.
    assert row.actor_pseudonym == pseudonymise(environment.settings.audit_pepper, "github:https://github.com:1001")


async def test_ci_token_from_another_repository_403(environment: Environment) -> None:
    token = environment.ci.token(repository="minbzk/ander", repository_id="999")
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))
    assert _assert_problem(resp, 403)["code"] == "CI_REPOSITORY_NOT_TRUSTED"


async def test_ci_origin_follows_the_token_not_the_stored_name(environment: Environment) -> None:
    async with environment.session_factory() as db:
        await db.execute(update(SiteRepository).values(owner="andere-org", repo="getypte-naam"))
        await db.commit()
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))
    assert resp.status_code == 201

    async with environment.session_factory() as db:
        version = (await db.execute(select(Version))).scalar_one()
        link = (await db.execute(select(SiteRepository))).scalar_one()
        rename = (
            await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == "site_repository_rename"))
        ).scalar_one()
        deploy_row = (await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == "deploy"))).scalar_one()
        actions = list(await db.scalars(select(AuditLogEntry.action).order_by(AuditLogEntry.occurred_at)))
    assert version.ci_repository == "github.com/minbzk/website"
    # The link follows the token, so the Deploy tab shows the name too.
    assert (link.owner, link.repo) == ("minbzk", "website")
    assert actions == ["site_repository_rename", "deploy"]
    assert rename.result == "allowed"
    assert rename.actor_kind == ActorKind.CI
    assert rename.actor_pseudonym == deploy_row.actor_pseudonym
    assert rename.refs == {
        "group": "team-aurora",
        "site": "website",
        "provider": "github",
        "host": "https://github.com",
        "repository": "minbzk/website",
        "previous_repository": "andere-org/getypte-naam",
        "repository_id": 1001,
    }


@pytest.mark.parametrize(("owner_claim", "confirmed"), [("2002", True), (OMIT, False)])
async def test_a_ci_deploy_confirms_entered_ids_when_the_token_carries_both(
    environment: Environment, owner_claim, confirmed
) -> None:
    async with environment.session_factory() as db:
        await db.execute(update(SiteRepository).values(ids_confirmed=False))
        await db.commit()
    token = environment.ci.token(repository_owner_id=owner_claim)
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))
    assert resp.status_code == 201

    async with environment.session_factory() as db:
        link = (await db.execute(select(SiteRepository))).scalar_one()
    assert link.ids_confirmed is confirmed


async def test_a_refused_live_deploy_still_follows_the_rename(environment: Environment) -> None:
    """The rename rests on the trusted ids, not on the event, so a live deploy
    refused afterwards keeps it; both rows land in the log."""
    async with environment.session_factory() as db:
        await db.execute(update(SiteRepository).values(repo="oude-naam"))
        await db.commit()
    token = environment.ci.token(event_name="pull_request")
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))
    assert _assert_problem(resp, 403)["code"] == "CI_BRANCH_NOT_ALLOWED"

    async with environment.session_factory() as db:
        link = (await db.execute(select(SiteRepository))).scalar_one()
        rows = list(await db.scalars(select(AuditLogEntry).order_by(AuditLogEntry.occurred_at)))
        versions = await db.scalar(select(func.count()).select_from(Version))
    assert versions == 0
    assert link.repo == "website"
    assert [(row.action, row.result, row.reason_code) for row in rows] == [
        ("site_repository_rename", "allowed", None),
        ("deploy", "refused", "CI_BRANCH_NOT_ALLOWED"),
    ]
    assert rows[0].refs["previous_repository"] == "minbzk/oude-naam"
    assert rows[0].actor_pseudonym == rows[1].actor_pseudonym


async def test_a_second_ci_deploy_under_the_same_name_writes_no_rename(environment: Environment) -> None:
    async with environment.session_factory() as db:
        await db.execute(update(SiteRepository).values(owner="andere-org", repo="getypte-naam"))
        await db.commit()
    async with environment.client() as client:
        for _ in range(2):
            resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))
            assert resp.status_code == 201

    async with environment.session_factory() as db:
        renames = await db.scalar(
            select(func.count()).select_from(AuditLogEntry).where(AuditLogEntry.action == "site_repository_rename")
        )
    assert renames == 1


async def test_preview_teardown_from_a_renamed_repository_renames_the_link(environment: Environment) -> None:
    async with environment.session_factory() as db:
        await db.execute(update(SiteRepository).values(repo="oude-naam"))
        await db.commit()
    async with environment.client() as client:
        resp = await client.delete(
            "/-/api/v1/sites/team-aurora/website/previews/pr-7", headers=_bearer(environment.ci_token)
        )
    assert resp.status_code == 204

    async with environment.session_factory() as db:
        link = (await db.execute(select(SiteRepository))).scalar_one()
        rename = (
            await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == "site_repository_rename"))
        ).scalar_one()
    assert link.repo == "website"
    assert rename.refs["previous_repository"] == "minbzk/oude-naam"


async def test_ci_origin_without_a_repository_claim_is_the_stored_name(environment: Environment) -> None:
    async with environment.session_factory() as db:
        await db.execute(update(SiteRepository).values(owner="andere-org", repo="getypte-naam"))
        await db.commit()
    token = environment.ci.token(repository=OMIT)
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))
    assert resp.status_code == 201

    async with environment.session_factory() as db:
        version = (await db.execute(select(Version))).scalar_one()
        link = (await db.execute(select(SiteRepository))).scalar_one()
        actions = list(await db.scalars(select(AuditLogEntry.action)))
    assert version.ci_repository == "github.com/andere-org/getypte-naam"
    assert (link.owner, link.repo) == ("andere-org", "getypte-naam")
    assert actions == ["deploy"]


async def test_forgejo_token_without_ids_is_rechecked_against_the_api(environment: Environment) -> None:
    async with environment.session_factory() as db:
        await db.execute(
            update(SiteRepository).values(
                provider=CiProvider.FORGEJO, host=FORGEJO_HOST, repository_id=3003, owner_id=4004
            )
        )
        await db.commit()
    token = environment.ci.token("forgejo", repository="MinBZK/Website")
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))
    assert resp.status_code == 201
    assert FORGEJO_HOST + "/api/v1/repos/minbzk/website" in environment.ci.requests

    async with environment.session_factory() as db:
        version = (await db.execute(select(Version))).scalar_one()
        link = (await db.execute(select(SiteRepository))).scalar_one()
        rename = (
            await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == "site_repository_rename"))
        ).scalar_one()
    assert version.ci_repository == "code.overheid.nl/MinBZK/Website"
    # Only the spelling can differ here, and the link takes the token's.
    assert (link.owner, link.repo) == ("MinBZK", "Website")
    assert rename.refs["provider"] == "forgejo"
    assert rename.refs["host"] == FORGEJO_HOST
    assert rename.refs["repository"] == "MinBZK/Website"
    assert rename.refs["previous_repository"] == "minbzk/website"
    assert rename.refs["repository_id"] == 3003


async def test_forgejo_unreachable_for_the_recheck_503(environment: Environment) -> None:
    async with environment.session_factory() as db:
        await db.execute(
            update(SiteRepository).values(
                provider=CiProvider.FORGEJO, host=FORGEJO_HOST, repository_id=3003, owner_id=4004
            )
        )
        await db.commit()
    environment.ci.failures[FORGEJO_HOST + "/api/v1/repos/minbzk/website"] = 502
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci.token("forgejo"))
        )
    assert _assert_problem(resp, 503)["code"] == "CI_PROVIDER_UNREACHABLE"
    assert "www-authenticate" not in resp.headers


async def test_ci_token_with_wrong_audience_401(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci.token(aud="https://elders.example"))
        )
    assert _assert_problem(resp, 401)["code"] == "CI_AUDIENCE_MISMATCH"
    assert resp.headers["www-authenticate"].startswith("Bearer")


async def test_ci_token_from_an_unknown_issuer_401(environment: Environment) -> None:
    token = environment.ci.token(iss="https://token.elders.example")
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))
    assert _assert_problem(resp, 401)["code"] == "CI_ISSUER_UNKNOWN"
    async with environment.session_factory() as db:
        row = (await db.execute(select(AuditLogEntry))).scalar_one()
    assert row.actor_kind == ActorKind.ANONYMOUS
    assert row.reason_code == "CI_ISSUER_UNKNOWN"


async def test_expired_ci_token_401(environment: Environment) -> None:
    token = environment.ci.token(exp=int(datetime.now(UTC).timestamp()) - 600)
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(token))
    assert _assert_problem(resp, 401)["code"] == "CI_TOKEN_INVALID"


async def test_ci_token_without_expiry_401(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci.token(exp=OMIT)))
    assert _assert_problem(resp, 401)["code"] == "CI_TOKEN_INVALID"


async def test_unknown_bearer_format_401(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer("plak_abcdefgh_" + "0" * 32))
    content = _assert_problem(resp, 401)
    assert content["code"] == "TOKEN_INVALID"
    assert resp.headers["www-authenticate"].startswith("Bearer")


async def test_a_non_bearer_authorization_scheme_is_ignored(environment: Environment) -> None:
    """`Authorization: Basic ...` is not a Bearer token: it falls through to
    the session-auth path, and without a session that is 401, not a 500 from
    treating the whole header as a token."""
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers={"Authorization": "Basic dXNlcjpwYXNz"})
    content = _assert_problem(resp, 401)
    assert content["code"] == "NO_AUTHENTICATION"


async def test_cli_token_deploys_as_its_member(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.cli_token))

    assert resp.status_code == 201
    async with environment.session_factory() as db:
        version = (await db.execute(select(Version))).scalar_one()
        row = (await db.execute(select(AuditLogEntry))).scalar_one()
        session = (await db.execute(select(CliSession))).scalar_one()
    assert version.member_id == environment.member.id
    assert version.ci_repository is None
    assert row.actor_kind == ActorKind.MEMBER
    assert row.actor_pseudonym == pseudonymise(environment.settings.audit_pepper, "sub-actief")
    assert row.refs["via"] == "cli"
    assert row.refs["cli_session"] == str(session.id)
    assert session.last_used_at is not None
    assert environment.cli_token not in str(row.refs)


async def test_failing_last_used_bookkeeping_does_not_fail_the_deploy(
    environment: Environment, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def unavailable(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(cli, "mark_used", unavailable)
    with caplog.at_level(logging.WARNING, logger=deploys.__name__):
        async with environment.client() as client:
            resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.cli_token))

    assert resp.status_code == 201
    async with environment.session_factory() as db:
        version = (await db.execute(select(Version))).scalar_one()
        row = (await db.execute(select(AuditLogEntry))).scalar_one()
        session = (await db.execute(select(CliSession))).scalar_one()
    assert version.member_id == environment.member.id
    assert row.result == "allowed"
    assert row.actor_pseudonym == pseudonymise(environment.settings.audit_pepper, "sub-actief")
    assert session.last_used_at is None
    assert "database unavailable" in caplog.text


async def test_cli_token_of_a_member_without_role_403(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            "/-/api/v1/sites/extern/site/deploys", files=_upload(), headers=_bearer(environment.cli_token)
        )
    assert _assert_problem(resp, 403)["code"] == "INSUFFICIENT_ROLE"


async def test_cli_token_of_a_deactivated_member_403(environment: Environment) -> None:
    async with environment.session_factory() as db:
        await db.execute(
            update(Member).where(Member.id == environment.member.id).values(status=MemberStatus.DEACTIVATED)
        )
        await db.commit()
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.cli_token))
    assert _assert_problem(resp, 403)["code"] == "MEMBER_NOT_ACTIVE"


async def test_cli_token_needs_no_csrf(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.delete(
            "/-/api/v1/sites/team-aurora/website/previews/pr-1", headers=_bearer(environment.cli_token)
        )
    assert resp.status_code == 204


async def test_deploy_of_archive_with_wrapping_dir(environment: Environment) -> None:
    """Manual upload of a zipped folder: the site lands on the site root,
    not on /{group}/{site}/dist/."""
    data = _zip_bytes({"dist/": b"", "dist/index.html": b"<h1>hoi</h1>", "dist/assets/s.css": b"body{}"})
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(data), headers=_bearer(environment.ci_token))

    assert resp.status_code == 201
    version_id = uuid.UUID(resp.json()["versionId"])

    async with environment.session_factory() as db:
        version = await db.scalar(select(Version).where(Version.id == version_id))
    assert version is not None
    version_dir = environment.settings.content_root / version.storage_ref
    assert (version_dir / "index.html").is_file()
    assert (version_dir / "assets" / "s.css").is_file()
    assert not (version_dir / "dist").exists()


async def test_bundle_without_index_422_with_suggestion(environment: Environment) -> None:
    """The zipped site folder: a 422 instead of a 201 with a site that 404s,
    with the suggestion machine-readable in the problem+json response."""
    data = _zip_bytes({"mijnsite/README.md": b"x", "mijnsite/dist/index.html": b"<h1>hoi</h1>"})
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(data), headers=_bearer(environment.ci_token))

    content = _assert_problem(resp, 422)
    assert content["code"] == "NO_INDEX"
    assert content["indexCandidates"] == ["dist/index.html"]
    assert "dist/index.html" in content["detail"]


async def test_refused_bundle_leaves_nothing_behind(environment: Environment) -> None:
    data = _zip_bytes({"mijnsite/README.md": b"x", "mijnsite/dist/index.html": b"<h1>hoi</h1>"})
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(data), headers=_bearer(environment.ci_token))
    assert resp.status_code == 422

    async with environment.session_factory() as db:
        versions = (await db.execute(select(Version))).scalars().all()
        live_id = await db.scalar(select(Site.live_version_id).where(Site.id == environment.site.id))
        row = (
            await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_DEPLOY))
        ).scalar_one()
    assert versions == []
    assert live_id is None
    assert row.result == "refused"
    assert row.reason_code == "NO_INDEX"
    assert not (environment.settings.content_root / str(environment.site.id)).exists()
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_base_path_creates_that_dir_the_root(environment: Environment) -> None:
    data = _zip_bytes(
        {
            "mijnsite/README.md": b"x",
            "mijnsite/dist/index.html": b"<h1>hoi</h1>",
            "mijnsite/dist/assets/s.css": b"body{}",
        }
    )
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            files=_upload(data),
            data={"basePath": "dist"},
            headers=_bearer(environment.ci_token),
        )

    assert resp.status_code == 201, resp.text
    version_id = uuid.UUID(resp.json()["versionId"])
    async with environment.session_factory() as db:
        version = await db.scalar(select(Version).where(Version.id == version_id))
    version_dir = environment.settings.content_root / version.storage_ref
    assert (version_dir / "index.html").is_file()
    assert (version_dir / "assets" / "s.css").is_file()
    assert not (version_dir / "README.md").exists()


async def test_invalid_base_path_422(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            files=_upload(),
            data={"basePath": "../ontsnapping"},
            headers=_bearer(environment.ci_token),
        )

    content = _assert_problem(resp, 422)
    assert content["code"] == "BASE_PATH_INVALID"

    async with environment.session_factory() as db:
        row = (
            await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_DEPLOY))
        ).scalar_one()
    assert row.reason_code == "BASE_PATH_INVALID"
    assert row.refs["base_path"] == "../ontsnapping"


async def test_preview_deploy_with_base_path(environment: Environment) -> None:
    data = _zip_bytes({"mijnsite/README.md": b"x", "mijnsite/dist/index.html": b"<p>pr</p>"})
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            files=_upload(data),
            data={"preview": "pr-9", "basePath": "dist"},
            headers=_bearer(environment.ci_token),
        )

    assert resp.status_code == 201, resp.text
    version_id = uuid.UUID(resp.json()["versionId"])
    async with environment.session_factory() as db:
        version = await db.scalar(select(Version).where(Version.id == version_id))
        preview = await db.scalar(select(Preview).where(Preview.site_id == environment.site.id))
    assert version.target == VersionTarget.PREVIEW
    assert preview.ref == "pr-9"
    index = environment.settings.content_root / version.storage_ref / "index.html"
    assert index.read_bytes() == b"<p>pr</p>"


async def test_preview_deploy_with_ci_token_from_a_pull_request(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            files=_upload(),
            data={"preview": "pr-42"},
            headers=_bearer(environment.ci.token(ref="refs/pull/42/merge", event_name="pull_request")),
        )

    assert resp.status_code == 201
    version_id = uuid.UUID(resp.json()["versionId"])
    assert resp.json()["url"] == "https://plak.example/team-aurora/website/_preview/pr-42/"

    async with environment.session_factory() as db:
        preview = await db.scalar(
            select(Preview).where(Preview.site_id == environment.site.id, Preview.ref == "pr-42")
        )
        live_id = await db.scalar(select(Site.live_version_id).where(Site.id == environment.site.id))
    assert preview is not None
    assert preview.version_id == version_id
    assert live_id is None
    remaining = preview.expires_at - datetime.now(UTC)
    assert timedelta(days=29) < remaining < timedelta(days=31)


async def test_deploy_answers_with_the_access_of_the_site(environment: Environment) -> None:
    """A CI can tell from the answer whether the link needs a sign-in, without
    a second request that its token is not allowed to make."""
    async with environment.session_factory() as db:
        await db.execute(
            update(Site)
            .where(Site.id == environment.site.id)
            .values(access_base=AccessBase.SITE_TEAM, access_keys=False, access_invitees=True)
        )
        await db.commit()

    async with environment.client() as client:
        live = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))
        preview = await client.post(
            DEPLOY_PATH, files=_upload(), data={"preview": "pr-42"}, headers=_bearer(environment.ci_token)
        )

    expected = {"base": "site_team", "keys": False, "invitees": True}
    assert live.json()["access"] == expected
    # A preview without its own access setting follows the site.
    assert preview.json()["access"] == expected


async def test_preview_deploy_answers_with_the_access_override_of_the_preview(
    environment: Environment,
) -> None:
    """The override is what the gate applies to the preview, so the answer
    names that and not the site's access, which here is wider."""
    async with environment.client() as client:
        first = await client.post(
            DEPLOY_PATH, files=_upload(), data={"preview": "pr-42"}, headers=_bearer(environment.ci_token)
        )
        assert first.json()["access"] == {"base": "public", "keys": False, "invitees": False}
        async with environment.session_factory() as db:
            await db.execute(
                update(Preview)
                .where(Preview.site_id == environment.site.id, Preview.ref == "pr-42")
                .values(
                    access_base_override=AccessBase.NOBODY,
                    access_keys_override=True,
                    access_invitees_override=False,
                )
            )
            await db.commit()
        again = await client.post(
            DEPLOY_PATH, files=_upload(), data={"preview": "pr-42"}, headers=_bearer(environment.ci_token)
        )

    assert again.status_code == 201
    assert again.json()["access"] == {"base": "nobody", "keys": True, "invitees": False}


async def test_preview_ref_becomes_slug_validated(environment: Environment) -> None:
    async with environment.client() as client:
        for error_ref in ("PR-42", "pr_42", "-pr", "pr-", "a" * 64):
            resp = await client.post(
                DEPLOY_PATH,
                files=_upload(),
                data={"preview": error_ref},
                headers=_bearer(environment.ci_token),
            )
            _assert_problem(resp, 422)

    async with environment.session_factory() as db:
        count = await db.scalar(select(func.count()).select_from(Version))
    assert count == 0


async def test_long_base_path_is_capped_in_the_audit_refs(environment: Environment) -> None:
    """A form field is attacker-controlled up to MAX_FIELD_BYTES; the log keeps
    no more of it than a CI claim."""
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            files=_upload(),
            data={"basePath": "a" * 1000},
            headers=_bearer(environment.ci_token),
        )

    _assert_problem(resp, 422)
    async with environment.session_factory() as db:
        row = (
            await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_DEPLOY))
        ).scalar_one()
    assert row.refs["base_path"] == "a" * trust.MAX_CLAIM_LENGTH


async def test_invalid_preview_ref_is_not_recorded(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            files=_upload(),
            data={"preview": "P" * 1000},
            headers=_bearer(environment.ci_token),
        )

    content = _assert_problem(resp, 422)
    assert content["code"] == "PREVIEW_REF_INVALID"
    async with environment.session_factory() as db:
        row = (
            await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_DEPLOY))
        ).scalar_one()
    assert "preview" not in row.refs


async def test_deeply_nested_ci_token_401(environment: Environment) -> None:
    """A payload of thousands of nested arrays makes json.loads raise
    RecursionError; that is a refusal, never an unhandled 500."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(b"[" * 12000).rstrip(b"=").decode()
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(f"{header}.{payload}.sig"))

    content = _assert_problem(resp, 401)
    assert content["code"] == "CI_TOKEN_INVALID"


async def test_malformed_multipart_carries_no_parser_text(environment: Environment) -> None:
    """api/errors.py: `detail` never carries internal details, so the wording
    python-multipart chose stays out of the answer."""
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            content=b"rommel",
            headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS},
        )

    content = _assert_problem(resp, 422)
    assert content["code"] == "MULTIPART_INVALID"
    assert content["detail"] == "Invalid multipart request."


async def _no_space(*_args: object, **_kwargs: object) -> None:
    raise OSError(28, "No space left on device")


async def test_failed_upload_still_writes_a_refused_audit_row(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deploys, "_spool_upload", _no_space)
    async with environment.client() as client:
        with pytest.raises(OSError, match="No space left"):
            await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))

    async with environment.session_factory() as db:
        row = (
            await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_DEPLOY))
        ).scalar_one()
    assert row.result == "refused"
    assert row.reason_code == deploys.AUDIT_REASON_INTERNAL


async def test_an_unexpected_error_before_the_ingest_cleans_up_the_spool(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error that is neither `ApiError` nor `IngestError`, raised after the
    upload was already spooled (here: during the live-branch check), still
    has to remove the spool file it made."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("unexpected error")

    monkeypatch.setattr(trust, "check_live_deploy", _boom)
    async with environment.client() as client:
        with pytest.raises(RuntimeError, match="unexpected error"):
            await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))

    async with environment.session_factory() as db:
        row = (
            await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_DEPLOY))
        ).scalar_one()
    assert row.result == "refused"
    assert row.reason_code == deploys.AUDIT_REASON_INTERNAL
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_failed_ingest_still_writes_a_refused_audit_row(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk halfway through the unpack leaves no version, but it does
    leave a row."""
    monkeypatch.setattr(IngestService, "deploy", _no_space)
    async with environment.client() as client:
        with pytest.raises(OSError, match="No space left"):
            await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))

    async with environment.session_factory() as db:
        row = (
            await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_DEPLOY))
        ).scalar_one()
        assert await db.scalar(select(func.count()).select_from(Version)) == 0
    assert row.result == "refused"
    assert row.reason_code == deploys.AUDIT_REASON_INTERNAL
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_a_failing_audit_write_does_not_mask_the_ingest_failure(
    environment: Environment, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The audit row is best-effort inside the handler: whatever it does, the
    original failure is what the operator reads and what the client answers on."""

    async def no_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit log unreachable")

    monkeypatch.setattr(IngestService, "deploy", _no_space)
    monkeypatch.setattr(deploys, "_audit", no_audit)

    with caplog.at_level(logging.ERROR, logger=deploys.__name__):
        async with environment.client() as client:
            with pytest.raises(OSError, match="No space left") as raised:
                await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))

    chain = []
    error: BaseException | None = raised.value
    while error is not None:
        chain.append(type(error))
        error = error.__context__
    assert RuntimeError not in chain
    assert "audit log unreachable" in caplog.text

    # The client sees what it saw before: the generic 500, no exception text.
    transport = httpx.ASGITransport(app=environment.app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))
    assert resp.status_code == 500
    assert "No space left" not in resp.text


async def test_bearer_outside_deploy_endpoints_401(environment: Environment) -> None:
    async with environment.client() as client:
        # A valid token on a different API endpoint.
        resp = await client.get("/-/api/v1/groups", headers=_bearer(environment.ci_token))
        content = _assert_problem(resp, 401)
        assert "www-authenticate" in resp.headers
        assert content["status"] == 401

        # On arbitrary non-API paths, and on the deploy path with the wrong
        # method, bearer is refused too.
        resp = await client.get("/team-aurora/website/", headers=_bearer(environment.ci_token))
        _assert_problem(resp, 401)

        resp = await client.get(DEPLOY_PATH, headers=_bearer(environment.ci_token))
        _assert_problem(resp, 401)

        # Without bearer the same endpoint stays reachable as usual.
        resp = await client.get("/-/api/v1/groups")
        assert resp.status_code == 200


async def test_bearer_outside_deploys_middleware_passes_non_http_scopes_through() -> None:
    """The middleware only inspects `http` scopes; a `lifespan` (or
    `websocket`) scope must reach the inner app unchanged, bearer header or
    not."""
    calls: list[dict] = []

    async def inner_app(scope, receive, send) -> None:
        calls.append(scope)

    middleware = BearerOutsideDeploysMiddleware(inner_app)

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(_message) -> None:
        pass

    scope = {"type": "lifespan", "headers": [(b"authorization", b"Bearer plakcli_abc")]}
    await middleware(scope, receive, send)

    assert calls == [scope]


def test_auth_refs_of_no_established_auth_is_empty() -> None:
    """`_auth_refs` is only ever called after `_authenticate` has returned or
    raised, so `auth` is never actually `None` at either call site; this
    pins the fallback its type hint promises."""
    assert deploys._auth_refs(None) == {}


async def test_revoked_cli_token_401_with_www_authenticate(environment: Environment) -> None:
    async with environment.session_factory() as db:
        session = (await db.execute(select(CliSession))).scalar_one()
        await cli.revoke(db, session.id)

    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.cli_token))

    assert _assert_problem(resp, 401)["code"] == "TOKEN_INVALID"
    assert resp.headers["www-authenticate"].startswith("Bearer")


async def test_expired_cli_token_401_with_www_authenticate(environment: Environment) -> None:
    async with environment.session_factory() as db:
        await db.execute(update(CliSession).values(access_expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        await db.commit()

    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.cli_token))

    _assert_problem(resp, 401)
    assert resp.headers["www-authenticate"].startswith("Bearer")


async def test_unknown_site_404(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            "/-/api/v1/sites/team-aurora/onbestaand/deploys",
            files=_upload(),
            headers=_bearer(environment.ci_token),
        )
    _assert_problem(resp, 404)


async def test_session_deploy_with_csrf(environment: Environment) -> None:
    cookies, headers = environment.session_for("sub-actief")
    async with environment.client() as client:
        client.cookies.update(cookies)
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=headers)

    assert resp.status_code == 201
    version_id = uuid.UUID(resp.json()["versionId"])
    async with environment.session_factory() as db:
        version = await db.scalar(select(Version).where(Version.id == version_id))
    assert version.member_id == environment.member.id
    assert version.ci_repository is None


async def test_session_without_csrf_header_403(environment: Environment) -> None:
    cookies, _ = environment.session_for("sub-actief")
    async with environment.client() as client:
        client.cookies.update(cookies)
        resp = await client.post(DEPLOY_PATH, files=_upload())
    _assert_problem(resp, 403)


async def test_without_session_and_without_token_401(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload())
    _assert_problem(resp, 401)


async def test_session_of_not_group_member_403(environment: Environment) -> None:
    cookies, headers = environment.session_for("sub-buiten")
    async with environment.client() as client:
        client.cookies.update(cookies)
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=headers)
    _assert_problem(resp, 403)


async def test_refused_deploy_is_audited_once(environment: Environment) -> None:
    """api/deploys.py marks its own refusals audited, so the generic ApiError
    handler in api/errors.py does not add a second, thinner admin_access row."""
    cookies, headers = environment.session_for("sub-buiten")
    async with environment.client() as client:
        client.cookies.update(cookies)
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=headers)
    _assert_problem(resp, 403)

    async with environment.session_factory() as db:
        rows = (await db.execute(select(AuditLogEntry))).scalars().all()
    assert [row.action for row in rows] == ["deploy"]


async def test_session_of_a_deactivated_member_403(environment: Environment) -> None:
    """The session-auth path checks membership status itself (the CLI-token
    path has its own equivalent check, in cli_member)."""
    cookies, headers = environment.session_for("sub-actief")
    async with environment.session_factory() as db:
        await db.execute(
            update(Member).where(Member.id == environment.member.id).values(status=MemberStatus.DEACTIVATED)
        )
        await db.commit()
    async with environment.client() as client:
        client.cookies.update(cookies)
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=headers)
    content = _assert_problem(resp, 403)
    assert content["code"] == "MEMBER_NOT_ACTIVE"


async def test_teardown_idempotent_204(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, files=_upload(), data={"preview": "pr-7"}, headers=_bearer(environment.ci_token)
        )
        assert resp.status_code == 201
        version_id = uuid.UUID(resp.json()["versionId"])

        async with environment.session_factory() as db:
            storage_ref = await db.scalar(select(Version.storage_ref).where(Version.id == version_id))
        assert (environment.settings.content_root / storage_ref).is_dir()

        resp = await client.delete(
            "/-/api/v1/sites/team-aurora/website/previews/pr-7", headers=_bearer(environment.ci_token)
        )
        assert resp.status_code == 204

        # Idempotent: deleting again stays a 204, also for a ref that has
        # never existed.
        resp = await client.delete(
            "/-/api/v1/sites/team-aurora/website/previews/pr-7", headers=_bearer(environment.ci_token)
        )
        assert resp.status_code == 204
        resp = await client.delete(
            "/-/api/v1/sites/team-aurora/website/previews/nooit-bestaan",
            headers=_bearer(environment.ci_token),
        )
        assert resp.status_code == 204

    async with environment.session_factory() as db:
        preview_count = await db.scalar(select(func.count()).select_from(Preview))
        version_count = await db.scalar(select(func.count()).select_from(Version))
    assert preview_count == 0
    assert version_count == 0
    assert not (environment.settings.content_root / storage_ref).exists()


async def test_failed_teardown_still_writes_a_refused_audit_row(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(IngestService, "delete_preview", _no_space)
    async with environment.client() as client:
        with pytest.raises(OSError, match="No space left"):
            await client.delete(
                "/-/api/v1/sites/team-aurora/website/previews/pr-7", headers=_bearer(environment.ci_token)
            )

    async with environment.session_factory() as db:
        row = (
            await db.execute(
                select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_PREVIEW_TEARDOWN)
            )
        ).scalar_one()
    assert row.result == "refused"
    assert row.reason_code == deploys.AUDIT_REASON_INTERNAL
    assert row.refs["preview"] == "pr-7"


async def test_teardown_invalid_ref_422(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.delete(
            "/-/api/v1/sites/team-aurora/website/previews/PR_7", headers=_bearer(environment.ci_token)
        )
    _assert_problem(resp, 422)


async def test_teardown_via_session(environment: Environment) -> None:
    cookies, headers = environment.session_for("sub-actief")
    async with environment.client() as client:
        client.cookies.update(cookies)
        resp = await client.delete(
            "/-/api/v1/sites/team-aurora/website/previews/pr-9", headers=headers
        )
    assert resp.status_code == 204


async def test_upload_above_max_body_413(tmp_path: Path, migrated_dsn: str) -> None:
    async with _environment(tmp_path, migrated_dsn, ingest_max_body=64) as environment:
        async with environment.client() as client:
            resp = await client.post(
                DEPLOY_PATH,
                files=_upload(_zip_bytes({"index.html": b"x" * 4096})),
                headers=_bearer(environment.ci_token),
            )
        _assert_problem(resp, 413)
        assert list(_tmp_dir(environment).iterdir()) == []


async def test_oversized_body_without_content_length_stops_413_before_end_of_stream(
    tmp_path: Path, migrated_dsn: str
) -> None:
    """Chunked upload (no Content-Length): the body limit is watched
    incrementally and reading stops as soon as it is exceeded, well before the
    end."""
    limit = 64 * 1024
    async with _environment(tmp_path, migrated_dsn, ingest_max_body=limit) as environment:
        parts_ = _multipart_parts("site.zip", [b"\0" * 4096] * 256)  # ~1 MB
        counter = _Counter(parts_)
        async with environment.client() as client:
            resp = await client.post(
                DEPLOY_PATH,
                content=counter,
                headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS},
            )
        content = _assert_problem(resp, 413)
        assert content["code"] == "BODY_TOO_LARGE"
        # At most the limit plus one chunk was read, not the whole stream.
        assert counter.delivered <= limit // 4096 + 2
        assert counter.delivered < len(parts_)
        assert list(_tmp_dir(environment).iterdir()) == []

    async with environment.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Version)) == 0


async def test_content_length_above_limit_413_without_too_read(tmp_path: Path, migrated_dsn: str) -> None:
    async with _environment(tmp_path, migrated_dsn, ingest_max_body=64 * 1024) as environment:
        parts_ = _multipart_parts("site.zip", [b"\0" * 4096] * 256)
        counter = _Counter(parts_)
        async with environment.client() as client:
            resp = await client.post(
                DEPLOY_PATH,
                content=counter,
                headers={
                    **_bearer(environment.ci_token),
                    **MULTIPART_HEADERS,
                    "Content-Length": str(sum(len(s) for s in parts_)),
                },
            )
        _assert_problem(resp, 413)
        assert counter.delivered == 0
        assert list(_tmp_dir(environment).iterdir()) == []


async def test_unauthenticated_does_not_read_the_body(environment: Environment) -> None:
    parts_ = _multipart_parts("site.zip", [_zip_bytes()])
    counter = _Counter(parts_)
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, content=counter, headers=MULTIPART_HEADERS)
    _assert_problem(resp, 401)
    assert counter.delivered == 0
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_truncated_multipart_422_without_version(environment: Environment) -> None:
    """Without a closing boundary the upload is incomplete: fail-closed 422, no
    versie and no spool leftovers."""
    parts_ = _multipart_parts("site.zip", [_zip_bytes()])[:-1]
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, content=_Counter(parts_), headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS}
        )
    _assert_problem(resp, 422)
    async with environment.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Version)) == 0
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_no_multipart_422(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            content=b"{}",
            headers={**_bearer(environment.ci_token), "Content-Type": "application/json"},
        )
    _assert_problem(resp, 422)


async def test_second_file_field_422(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            files=[
                ("file", ("site.zip", _zip_bytes(), "application/zip")),
                ("extra", ("ander.zip", _zip_bytes(), "application/zip")),
            ],
            headers=_bearer(environment.ci_token),
        )
    _assert_problem(resp, 422)
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_two_parts_both_named_file_is_422(environment: Environment) -> None:
    """Unlike `test_second_file_field_422` (a second file under another field
    name), this is the same field name twice: `unexpected_file_field` never
    fires, only `more_than_one_file` does."""
    body = (
        _part_header("file", "een.zip")
        + _zip_bytes()
        + b"\r\n"
        + _part_header("file", "twee.zip")
        + _zip_bytes()
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, content=body, headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS}
        )
    _assert_problem(resp, 422)
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_a_field_without_a_name_is_422(environment: Environment) -> None:
    body = (
        f"--{BOUNDARY}\r\nContent-Disposition: form-data\r\n\r\nwaarde\r\n".encode()
        + _part_header("file", "site.zip")
        + _zip_bytes()
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, content=body, headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS}
        )
    _assert_problem(resp, 422)
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_more_than_the_maximum_number_of_fields_is_422(environment: Environment) -> None:
    fields = {f"veld{i}": "x" for i in range(deploys.MAX_FIELDS + 1)}
    parts_ = _multipart_parts("site.zip", [_zip_bytes()], fields=fields)
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            content=b"".join(parts_),
            headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS},
        )
    _assert_problem(resp, 422)
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_a_field_value_over_the_byte_cap_is_422(environment: Environment) -> None:
    parts_ = _multipart_parts(
        "site.zip", [_zip_bytes()], fields={"comment": "a" * (deploys.MAX_FIELD_BYTES + 1)}
    )
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH,
            content=b"".join(parts_),
            headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS},
        )
    _assert_problem(resp, 422)
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_a_non_utf8_field_value_falls_back_to_latin1(environment: Environment) -> None:
    """A form field is raw bytes, not text: a byte sequence that is invalid
    utf-8 but valid latin-1 must not crash the parser."""
    body = (
        _part_header("comment")
        + b"caf\xe9"
        + b"\r\n"
        + _part_header("file", "site.zip")
        + _zip_bytes()
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, content=body, headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS}
        )
    assert resp.status_code == 201, resp.text


async def test_a_boundary_over_the_parsers_own_limit_is_422(environment: Environment) -> None:
    """The parser itself refuses construction (FormParserError) for a
    boundary longer than it accepts, before any body is read."""
    huge_boundary = "x" * 300
    headers = {**_bearer(environment.ci_token), "Content-Type": f"multipart/form-data; boundary={huge_boundary}"}
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, content=b"rommel", headers=headers)
    content = _assert_problem(resp, 422)
    assert content["code"] == "MULTIPART_INVALID"


async def test_a_missing_closing_boundary_is_incomplete_422(environment: Environment) -> None:
    """Unlike `test_truncated_multipart_422_without_version` (cut off inside
    the file data, so the file is never marked complete), this body's file
    part finishes cleanly but the final `--boundary--` never arrives: a bare
    `--boundary` reads as the start of a new part instead."""
    body = _part_header("file", "site.zip") + _zip_bytes() + f"\r\n--{BOUNDARY}\r\n".encode()
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, content=body, headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS}
        )
    _assert_problem(resp, 422)
    async with environment.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Version)) == 0
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_a_client_disconnect_during_the_upload_is_a_400(tmp_path: Path) -> None:
    """`_spool_upload` reads the body from `request.stream()`, which raises
    `ClientDisconnect` once the client is gone; that must not surface as a
    raw exception. Exercised directly against a bare `Request`, since httpx's
    `ASGITransport` has no way to simulate a mid-stream disconnect."""
    store = ContentStore(tmp_path)

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"content-type", f"multipart/form-data; boundary={BOUNDARY}".encode())],
    }
    request = Request(scope, receive)

    with pytest.raises(ApiError) as excinfo:
        await deploys._spool_upload(request, store, 10 * 1024 * 1024, RoomGuard(store, 0))
    assert excinfo.value.status == 400
    assert excinfo.value.reason == "CLIENT_ABORTED"


async def test_preview_field_for_the_file_works_also(environment: Environment) -> None:
    parts_ = _multipart_parts("site.zip", [_zip_bytes()], fields={"preview": "pr-5"})
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, content=_Counter(parts_), headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS}
        )
    assert resp.status_code == 201, resp.text
    async with environment.session_factory() as db:
        preview = await db.scalar(select(Preview).where(Preview.site_id == environment.site.id))
    assert preview is not None
    assert preview.ref == "pr-5"


async def test_spool_and_workdir_empty_after_success(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))
    assert resp.status_code == 201
    assert list(_tmp_dir(environment).iterdir()) == []


async def test_upload_streams_without_keeping_the_body_in_memory(
    tmp_path: Path, migrated_dsn: str
) -> None:
    """A 64 MB html upload through a generator: peak memory of the whole
    request handling (spool plus ingest) stays orders of magnitude below the
    body."""
    size = 64 * 1024 * 1024
    chunk = b"<p>" + b"a" * (64 * 1024 - 7) + b"</p>"
    count = size // len(chunk)

    async def body() -> AsyncIterator[bytes]:
        yield _part_header("file", "rapport.html")
        for _ in range(count):
            yield chunk
        yield f"\r\n--{BOUNDARY}--\r\n".encode()

    # A single 64 MB page is above the default per-file limit.
    async with _environment(tmp_path, migrated_dsn, ingest_max_file=2 * size) as environment:
        tracemalloc.start()
        try:
            async with environment.client() as client:
                resp = await client.post(
                    DEPLOY_PATH, content=body(), headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS}
                )
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert resp.status_code == 201, resp.text
        version_id = uuid.UUID(resp.json()["versionId"])
        async with environment.session_factory() as db:
            storage_ref = await db.scalar(select(Version.storage_ref).where(Version.id == version_id))
        index = environment.settings.content_root / storage_ref / "index.html"
        assert index.stat().st_size == count * len(chunk)
        assert peak < 8 * 1024 * 1024, f"peak {peak} bytes for a body of {size} bytes"
        assert list(_tmp_dir(environment).iterdir()) == []


async def test_unpacked_file_above_limit_413(tmp_path: Path, migrated_dsn: str) -> None:
    async with _environment(tmp_path, migrated_dsn, ingest_max_file=8) as environment:
        async with environment.client() as client:
            resp = await client.post(
                DEPLOY_PATH,
                files=_upload(_zip_bytes({"index.html": b"x" * 1024})),
                headers=_bearer(environment.ci_token),
            )
        _assert_problem(resp, 413)


async def test_invalid_archive_422(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, files=_upload(b"dit is geen zip"), headers=_bearer(environment.ci_token)
        )
    _assert_problem(resp, 422)


async def test_missing_file_field_422(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.post(
            DEPLOY_PATH, data={"preview": "pr-1"}, headers=_bearer(environment.ci_token)
        )
    content = _assert_problem(resp, 422)
    assert "file" in content["detail"]


async def test_429_problem_json_with_retry_after(environment: Environment) -> None:
    async with environment.client() as client:
        resp = await client.get("/-/api/v1/ratelimit-demo")
    _assert_problem(resp, 429)
    assert resp.headers["retry-after"] == "7"


async def test_concurrent_same_ref_deploys_give_one_preview_row(environment: Environment) -> None:
    async def deploy(number: int) -> httpx.Response:
        async with environment.client() as client:
            return await client.post(
                DEPLOY_PATH,
                files=_upload(_zip_bytes({"index.html": f"<p>deploy {number}</p>".encode()})),
                data={"preview": "pr-13"},
                headers=_bearer(environment.ci_token),
            )

    first, second_one = await asyncio.gather(deploy(1), deploy(2))
    assert first.status_code == 201
    assert second_one.status_code == 201
    version_ids = {uuid.UUID(first.json()["versionId"]), uuid.UUID(second_one.json()["versionId"])}
    assert len(version_ids) == 2

    async with environment.session_factory() as db:
        query = select(Preview).where(Preview.site_id == environment.site.id)
        previews = (await db.execute(query)).scalars().all()
        query = select(Version).where(Version.site_id == environment.site.id)
        versions = (await db.execute(query)).scalars().all()

    assert len(previews) == 1
    assert previews[0].ref == "pr-13"
    assert previews[0].version_id in version_ids
    assert len(versions) == 1
    assert versions[0].id == previews[0].version_id

    # Only the file tree of the winning versie is left.
    sitedir = environment.settings.content_root / str(environment.site.id)
    assert {entry.name for entry in sitedir.iterdir()} == {str(previews[0].version_id)}


async def test_a_full_site_quota_is_413(tmp_path: Path, migrated_dsn: str) -> None:
    """A limit on what the site may occupy, so it lands beside the per-bundle
    limits as a 413 rather than in the 422 bucket of an invalid archive."""
    async with _environment(tmp_path, migrated_dsn, site_max_bytes=10) as environment:
        async with environment.client() as client:
            resp = await client.post(
                DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token)
            )

        content = _assert_problem(resp, 413)
        assert content["code"] == "SITE_QUOTA_EXCEEDED"

        async with environment.session_factory() as db:
            row = (
                await db.execute(
                    select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_DEPLOY)
                )
            ).scalar_one()
        assert row.reason_code == "SITE_QUOTA_EXCEEDED"


async def test_a_volume_without_room_is_503(tmp_path: Path, migrated_dsn: str) -> None:
    """503, not 4xx: nothing is wrong with this request, the platform has no
    room at this moment. The refusal comes before the body is read."""
    async with _environment(tmp_path, migrated_dsn, storage_min_free_bytes=2**62) as environment:
        async with environment.client() as client:
            resp = await client.post(
                DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token)
            )

        content = _assert_problem(resp, 503)
        assert content["code"] == "STORAGE_UNAVAILABLE"

        async with environment.session_factory() as db:
            row = (
                await db.execute(
                    select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_DEPLOY)
                )
            ).scalar_one()
        assert row.reason_code == "STORAGE_UNAVAILABLE"
        assert list((environment.settings.content_root / "_tmp").iterdir()) == []


FLOOR = 1024 * 1024
STORAGE_UNAVAILABLE_BODY = {
    "type": "about:blank",
    "title": messages.title(i18n.API_DEFAULT, 503),
    "status": 503,
    "detail": messages.render(i18n.API_DEFAULT, Msg("STORAGE_UNAVAILABLE")),
    "code": "STORAGE_UNAVAILABLE",
}


def _tree_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _fake_volume(monkeypatch: pytest.MonkeyPatch, environment: Environment, *, free: int) -> None:
    """A content volume with `free` bytes free now, that fills up with what
    lands under the content root from here on (spool and work directory)."""
    store: ContentStore = environment.app.state.content_store
    baseline = _tree_size(store.root)
    monkeypatch.setattr(store, "free_bytes", lambda: free - (_tree_size(store.root) - baseline))


async def _assert_storage_refusal(environment: Environment, resp: httpx.Response) -> None:
    """The same 503 whichever moment the room ran out, the audit row with it,
    and nothing left on the volume: no spool, no work directory, no version."""
    _assert_problem(resp, 503)
    assert resp.json() == STORAGE_UNAVAILABLE_BODY
    async with environment.session_factory() as db:
        row = (
            await db.execute(
                select(AuditLogEntry).where(AuditLogEntry.action == deploys.AUDIT_ACTION_DEPLOY)
            )
        ).scalar_one()
        assert await db.scalar(select(func.count()).select_from(Version)) == 0
    assert row.result == "refused"
    assert row.reason_code == "STORAGE_UNAVAILABLE"
    assert list(_tmp_dir(environment).iterdir()) == []
    assert not (environment.settings.content_root / str(environment.site.id)).exists()


async def test_a_declared_size_without_room_is_503_before_the_body_is_read(
    tmp_path: Path, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _environment(tmp_path, migrated_dsn, storage_min_free_bytes=FLOOR) as environment:
        _fake_volume(monkeypatch, environment, free=FLOOR + 1000)
        parts_ = _multipart_parts("site.zip", [b"\0" * 4096])
        counter = _Counter(parts_)
        async with environment.client() as client:
            resp = await client.post(
                DEPLOY_PATH,
                content=counter,
                headers={
                    **_bearer(environment.ci_token),
                    **MULTIPART_HEADERS,
                    "Content-Length": str(sum(len(s) for s in parts_)),
                },
            )
        assert counter.delivered == 0
        await _assert_storage_refusal(environment, resp)


async def test_a_declared_size_above_the_body_limit_stays_a_413(
    tmp_path: Path, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The room check asks for no more than the body limit: a declared size
    above it is the client's to fix, not a state of the platform."""
    async with _environment(
        tmp_path, migrated_dsn, storage_min_free_bytes=FLOOR, ingest_max_body=64 * 1024
    ) as environment:
        _fake_volume(monkeypatch, environment, free=FLOOR + 64 * 1024)
        parts_ = _multipart_parts("site.zip", [b"\0" * 4096] * 32)
        async with environment.client() as client:
            resp = await client.post(
                DEPLOY_PATH,
                content=_Counter(parts_),
                headers={
                    **_bearer(environment.ci_token),
                    **MULTIPART_HEADERS,
                    "Content-Length": str(sum(len(s) for s in parts_)),
                },
            )
        assert _assert_problem(resp, 413)["code"] == "BODY_TOO_LARGE"


async def test_the_spool_stops_when_the_volume_fills_up(
    tmp_path: Path, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a Content-Length nothing is known up front: the spool is
    watched as it is written, and reading stops well before the end."""
    async with _environment(tmp_path, migrated_dsn, storage_min_free_bytes=FLOOR) as environment:
        _fake_volume(monkeypatch, environment, free=FLOOR + 64 * 1024)
        parts_ = _multipart_parts("site.zip", [b"\0" * 4096] * 256)  # ~1 MB
        counter = _Counter(parts_)
        async with environment.client() as client:
            resp = await client.post(
                DEPLOY_PATH,
                content=counter,
                headers={**_bearer(environment.ci_token), **MULTIPART_HEADERS},
            )
        assert counter.delivered < len(parts_) // 2
        await _assert_storage_refusal(environment, resp)


async def test_unpacking_past_the_floor_is_503_and_leaves_nothing_behind(
    tmp_path: Path, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A small upload that unpacks big: it passes the check before spooling,
    and is stopped while it unpacks."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", b"<h1>hoi</h1>")
        archive.writestr("big.bin", b"\0" * (1024 * 1024))
    async with _environment(tmp_path, migrated_dsn, storage_min_free_bytes=FLOOR) as environment:
        _fake_volume(monkeypatch, environment, free=FLOOR + 256 * 1024)
        async with environment.client() as client:
            resp = await client.post(
                DEPLOY_PATH, files=_upload(buffer.getvalue()), headers=_bearer(environment.ci_token)
            )
        await _assert_storage_refusal(environment, resp)


async def test_a_small_deploy_passes_on_a_nearly_full_volume(
    environment: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production failure: a small page refused on a 1 GiB volume because
    the check asked for room for the largest deploy allowed."""
    settings = environment.settings
    free = settings.storage_min_free_bytes + 64 * 1024
    assert free < (
        settings.storage_min_free_bytes + settings.ingest_max_body + settings.ingest_max_total
    )
    _fake_volume(monkeypatch, environment, free=free)
    async with environment.client() as client:
        resp = await client.post(DEPLOY_PATH, files=_upload(), headers=_bearer(environment.ci_token))
    assert resp.status_code == 201, resp.text
