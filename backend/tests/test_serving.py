"""Tests for the serving router (spec §5 in full): the lexical 301, path
validation, access, ETag/304, headers, Range, neutral 404s, 404.html, the
login redirect, key redemption and audit; against the real PostgreSQL test
container. Viewers are content sessions (spec §4a); an admin session never
grants access to content.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar
from urllib.parse import quote, unquote

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import set_content_session_cookie, set_session_cookie
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plak.access import gate, keys
from plak.access.decision import allow
from plak.audit.log import AuditLog
from plak.auth.sessions import CONTENT_SESSION_COOKIE, KEY_COOKIE, SessionStore, check_signature, sign, sign_key_cookie
from plak.config import Settings
from plak.constants import AccessBase, AccessPolicy, Role
from plak.head_requests import ContentHeadMiddleware
from plak.ingest.store import ContentStore
from plak.models.audit import AuditLogEntry
from plak.models.identity import Group, GroupMember, Member, MemberStatus
from plak.models.publication import Preview, Site, Version, VersionTarget
from plak.serving.response import PLATFORM_CSP
from plak.serving.router import router as serving_router

BASE_URL = "https://plak.example"

FULL_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' data:; connect-src 'self'; media-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
    "object-src 'none'"
)

EXTERNAL_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net "
    "https://unpkg.com https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net "
    "https://unpkg.com https://fonts.googleapis.com; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data: https://fonts.gstatic.com; connect-src 'self'; media-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
    "object-src 'none'"
)

SANDBOX_DIRECTIVE = "; sandbox allow-scripts allow-forms allow-popups"

SANDBOX_CSP = FULL_CSP + SANDBOX_DIRECTIVE

SITE_INDEX = b"<h1>site</h1>"
SITE_404 = b"<h1>site-404</h1>"
PREVIEW_INDEX = b"<h1>preview</h1>"
SECRET_INDEX = b"<h1>geheim</h1>"


def make_settings(content_root: Path) -> Settings:
    return Settings(
        db_url="postgresql+asyncpg://ongebruikt:ongebruikt@localhost:5432/ongebruikt",
        content_root=content_root,
        oidc_issuer="https://idp.example",
        oidc_client_id="plak-client",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr:hoog",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper="audit-pepper-van-minstens-32-bytes!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        content_base_url=BASE_URL,
        environment="dev",
    )


@dataclass
class World:
    site_live_id: uuid.UUID
    site_storage: str
    preview_version_id: uuid.UUID
    preview_storage: str
    secret_live_id: uuid.UUID
    key_plain: str
    key_id: uuid.UUID
    external_live_id: uuid.UUID
    sandboxed_live_id: uuid.UUID


@dataclass
class Environment:
    factory: async_sessionmaker[AsyncSession]
    store: ContentStore
    world: World
    app: FastAPI


def _make_app(settings: Settings, factory, store: ContentStore) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    app.state.session_store = SessionStore()
    app.state.session_factory = factory
    app.state.content_store = store
    app.state.audit_log = AuditLog(factory, settings.audit_pepper, settings.audit_ip_key_bytes)
    app.include_router(serving_router)
    # As in main.py: on the content host HEAD reaches the routes as GET.
    app.add_middleware(ContentHeadMiddleware, content_host="plak.example")
    return app


async def _seed(factory, store: ContentStore) -> World:
    async with factory() as db:
        group = Group(slug="aurora", name="Aurora", default_access_base=AccessBase.PUBLIC)
        db.add(group)
        await db.flush()

        member = Member(sso_subject="lid-actief", email="lid@example.org", status=MemberStatus.ACTIVE)
        db.add(member)
        await db.flush()
        db.add(GroupMember(group_id=group.id, member_id=member.id, role=Role.ADMIN))

        # Both content switches are on by default, so the sites that must show
        # the strict CSP turn them off here; `extern` and `afgeschermd` each
        # leave one of them at its default.
        site = Site(
            group_id=group.id,
            slug="site",
            title="Site",
            access_base=AccessBase.PUBLIC,
            external_sources=False,
            sandbox=False,
        )
        secret = Site(
            group_id=group.id,
            slug="geheim",
            title="Geheim",
            access_base=AccessBase.NOBODY,
            access_keys=True,
            external_sources=False,
            sandbox=False,
        )
        internal = Site(
            group_id=group.id,
            slug="intern",
            title="Intern",
            access_base=AccessBase.SSO,
            external_sources=False,
            sandbox=False,
        )
        empty = Site(
            group_id=group.id,
            slug="leeg",
            title="Leeg",
            access_base=AccessBase.PUBLIC,
            external_sources=False,
            sandbox=False,
        )
        without_404 = Site(
            group_id=group.id,
            slug="zonder404",
            title="Zonder",
            access_base=AccessBase.PUBLIC,
            external_sources=False,
            sandbox=False,
        )
        external = Site(
            group_id=group.id,
            slug="extern",
            title="Extern",
            access_base=AccessBase.PUBLIC,
            sandbox=False,
        )
        sandboxed = Site(
            group_id=group.id,
            slug="afgeschermd",
            title="Afgeschermd",
            access_base=AccessBase.PUBLIC,
            external_sources=False,
        )
        db.add_all([site, secret, internal, empty, without_404, external, sandboxed])
        await db.flush()

        def new_version(site: Site, files: dict[str, bytes], target=VersionTarget.LIVE) -> Version:
            version_id = uuid.uuid4()
            storage_ref = store.store_version(site.id, version_id, files)
            return Version(
                id=version_id, site_id=site.id, target=target, storage_ref=storage_ref, member_id=member.id
            )

        site_live = new_version(
            site,
            {
                "index.html": SITE_INDEX,
                "stijl.css": b"body{}",
                "app.mjs": b"export default 1;",
                "docs/index.html": b"<h1>docs</h1>",
                "diep/map/bestand.txt": b"diep",
                "404.html": SITE_404,
            },
        )
        preview_version = new_version(site, {"index.html": PREVIEW_INDEX}, target=VersionTarget.PREVIEW)
        restricted_version = new_version(site, {"index.html": b"<h1>besloten</h1>"}, target=VersionTarget.PREVIEW)
        secret_live = new_version(
            secret,
            {
                "index.html": SECRET_INDEX,
                "map/index.html": b"<h1>map</h1>",
                "404.html": b"<h1>geheim-404</h1>",
                "stijl.css": b"body{}",
            },
        )
        internal_live = new_version(
            internal, {"index.html": b"<h1>intern</h1>", "stijl.css": b"body{}"}
        )
        without_404_live = new_version(without_404, {"index.html": b"<h1>kaal</h1>"})
        external_live = new_version(external, {"index.html": b"<h1>extern</h1>", "stijl.css": b"body{}"})
        sandboxed_live = new_version(
            sandboxed, {"index.html": b"<h1>afgeschermd</h1>", "stijl.css": b"body{}"}
        )
        sandboxed_preview = new_version(
            sandboxed, {"index.html": b"<h1>afgeschermd-preview</h1>"}, target=VersionTarget.PREVIEW
        )
        external_preview = new_version(
            external, {"index.html": b"<h1>extern-preview</h1>"}, target=VersionTarget.PREVIEW
        )
        db.add_all(
            [
                site_live,
                preview_version,
                restricted_version,
                secret_live,
                internal_live,
                without_404_live,
                external_live,
                external_preview,
                sandboxed_live,
                sandboxed_preview,
            ]
        )
        await db.flush()

        site.live_version_id = site_live.id
        secret.live_version_id = secret_live.id
        internal.live_version_id = internal_live.id
        without_404.live_version_id = without_404_live.id
        external.live_version_id = external_live.id
        sandboxed.live_version_id = sandboxed_live.id

        db.add(Preview(site_id=site.id, ref="pr-42", version_id=preview_version.id))
        db.add(Preview(site_id=external.id, ref="pr-extern", version_id=external_preview.id))
        db.add(
            Preview(
                site_id=sandboxed.id, ref="pr-afgeschermd", version_id=sandboxed_preview.id
            )
        )
        db.add(
            Preview(
                site_id=site.id,
                ref="pr-besloten",
                version_id=restricted_version.id,
                access_base_override=AccessBase.SSO,
                access_keys_override=False,
                access_invitees_override=False,
            )
        )

        key, plain = await keys.create_key(db, secret.id, "testsleutel")
        world = World(
            site_live_id=site_live.id,
            site_storage=site_live.storage_ref,
            preview_version_id=preview_version.id,
            preview_storage=preview_version.storage_ref,
            secret_live_id=secret_live.id,
            key_plain=plain,
            key_id=key.id,
            external_live_id=external_live.id,
            sandboxed_live_id=sandboxed_live.id,
        )
        await db.commit()
        return world


@pytest_asyncio.fixture
async def environment(migrated_dsn: str, tmp_path: Path) -> AsyncIterator[Environment]:
    engine = create_async_engine(migrated_dsn)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            factory = async_sessionmaker(
                bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
            )
            store = ContentStore(tmp_path)
            world = await _seed(factory, store)
            app = _make_app(make_settings(tmp_path), factory, store)
            try:
                yield Environment(factory=factory, store=store, world=world, app=app)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


def _make_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL, follow_redirects=False)


@pytest_asyncio.fixture
async def client(environment: Environment) -> AsyncIterator[httpx.AsyncClient]:
    async with _make_client(environment.app) as c:
        yield c


async def _raw_request(
    app: FastAPI, raw_path: str, method: str = "GET"
) -> tuple[int, list[tuple[str, str]], bytes]:
    """Sends a request with an exactly given raw path, the way a real ASGI
    server delivers it (path URL-decoded once). Needed because httpx can
    normalise percent-encoded dot segments away by itself."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": unquote(raw_path),
        "raw_path": raw_path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"plak.example")],
        "client": ("203.0.113.5", 4711),
        "server": ("plak.example", 443),
    }
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(scope, receive, send)
    status = 0
    headers: list[tuple[str, str]] = []
    body = b""
    for message in messages:
        if message["type"] == "http.response.start":
            status = message["status"]
            headers = [(k.decode().lower(), v.decode()) for k, v in message["headers"]]
        elif message["type"] == "http.response.body":
            body += message.get("body", b"")
    return status, sorted(headers), body


def _header_list(response: httpx.Response) -> list[tuple[str, str]]:
    return sorted((k.lower(), v) for k, v in response.headers.multi_items())


def _signed_key_cookie(environment: Environment) -> str:
    return sign_key_cookie(environment.app.state.settings.session_secret, str(environment.world.key_id))


async def _audit_rows(environment: Environment) -> list[AuditLogEntry]:
    async with environment.factory() as db:
        result = await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == "content_access"))
        return list(result.scalars().all())


class TestLexical301:
    async def test_site_root_without_slash(self, client):
        response = await client.get("/aurora/site")
        assert response.status_code == 301
        assert response.headers["location"] == "/aurora/site/"

    async def test_also_for_unknown_site_and_unknown_group(self, client):
        response = await client.get("/aurora/bestaat-niet")
        assert response.status_code == 301
        assert response.headers["location"] == "/aurora/bestaat-niet/"
        response = await client.get("/nergens/niks")
        assert response.status_code == 301
        assert response.headers["location"] == "/nergens/niks/"

    async def test_query_stays_kept(self, client, environment):
        response = await client.get(f"/aurora/geheim?key={environment.world.key_plain}")
        assert response.status_code == 301
        assert response.headers["location"] == f"/aurora/geheim/?key={environment.world.key_plain}"

    async def test_preview_and_version_root_without_slash(self, client, environment):
        response = await client.get("/aurora/site/_preview/pr-42")
        assert response.status_code == 301
        assert response.headers["location"] == "/aurora/site/_preview/pr-42/"
        response = await client.get(f"/aurora/site/_version/{environment.world.site_live_id}")
        assert response.status_code == 301
        assert response.headers["location"] == f"/aurora/site/_version/{environment.world.site_live_id}/"

    async def test_reserved_slug_gets_no_redirect(self, client):
        # Since the SPA moved to the root of the admin host, "admin" is a slug
        # like any other. What stays reserved is what the web itself claims.
        response = await client.get("/robots.txt/onbekend")
        assert response.status_code == 404

    async def test_no_redirect_leaks_no_audit_or_db_state(self, client, environment):
        # The redirect is purely lexical: no audit row for existing or for
        # non-existent sites.
        await client.get("/aurora/site")
        await client.get("/nergens/niks")
        assert await _audit_rows(environment) == []


class TestLiveServing:
    async def test_root_index_rewrite_and_headers(self, client, environment):
        response = await client.get("/aurora/site/")
        assert response.status_code == 200
        assert response.content == SITE_INDEX
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["cache-control"] == "no-cache, must-revalidate"
        assert response.headers["etag"] == f'"{environment.world.site_live_id}"'
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == FULL_CSP
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert "x-robots-tag" not in response.headers

    async def test_asset_immutable_cache(self, client):
        response = await client.get("/aurora/site/stijl.css")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "max-age=31536000, immutable"
        assert response.headers["content-type"] == "text/css; charset=utf-8"

    async def test_private_asset_is_not_immutable(self, client, environment):
        # The URL is not content-addressed and survives a redeploy, so
        # `immutable` would hide a new version even on a reload.
        set_content_session_cookie(client, environment.app, sub="willekeurige-kijker", sites=("/aurora/intern/",))
        response = await client.get("/aurora/intern/stijl.css")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, max-age=31536000"

    async def test_mjs_gets_text_javascript(self, client):
        response = await client.get("/aurora/site/app.mjs")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/javascript; charset=utf-8"

    async def test_directory_301_after_allow(self, client):
        response = await client.get("/aurora/site/docs")
        assert response.status_code == 301
        # A 301 is heuristically cacheable: without no-store a shared cache
        # could hand the directory of a private site to the next visitor.
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["location"] == "/aurora/site/docs/"
        followed = await client.get("/aurora/site/docs/")
        assert followed.status_code == 200
        assert followed.content == b"<h1>docs</h1>"

    async def test_directory_301_keeps_the_query_string(self, client):
        response = await client.get("/aurora/site/docs?x=1")
        assert response.status_code == 301
        assert response.headers["location"] == "/aurora/site/docs/?x=1"

    async def test_directory_301_not_for_refused_visitor(self, client):
        # geheim/map/index.html exists, but for an anonymous visitor without a
        # key the answer is the neutral 404, not a redirect (existence does not
        # leak).
        response = await client.get("/aurora/geheim/map")
        assert response.status_code == 404

    async def test_nested_file(self, client):
        response = await client.get("/aurora/site/diep/map/bestand.txt")
        assert response.status_code == 200
        assert response.content == b"diep"


class TestPathValidation:
    @pytest.mark.parametrize(
        "raw_path",
        [
            "/aurora/site/..%2fx",
            "/aurora/site/%2e%2e/x",
            "/aurora/site/%2e%2e%2fx",
            "/aurora/site/a%5cb",
            "/aurora/site/a%00b",
            "/aurora/site/..%2f..%2fgeheim/index.html",
        ],
    )
    async def test_percent_encoded_traversal_is_neutral_404(self, environment, raw_path):
        status, _, body = await _raw_request(environment.app, raw_path)
        assert status == 404
        ref_status, _, ref_body = await _raw_request(environment.app, "/nergens/niks/")
        assert (status, body) == (ref_status, ref_body)

    async def test_traversal_does_not_touch_the_store(self, environment, monkeypatch):
        def boom(*args, **kwargs):
            raise AssertionError("store touched for an invalid path")

        monkeypatch.setattr(environment.store, "file_path", boom)
        status, _, _ = await _raw_request(environment.app, "/aurora/site/..%2fx")
        assert status == 404


class TestInconsistentDecision:
    """The gate always hands back a version_id that a live version, a preview
    or a _version lookup really found, so an ALLOW with a version or a site
    reference that resolves to nothing does not occur through the gate
    itself. These tests force it anyway (by replacing gate.decide with a
    fabricated decision) to pin the defensive behaviour: no crash, and the
    answer is the same neutral 404 as every other refusal, not a 500."""

    async def test_a_version_id_the_store_does_not_know_is_the_neutral_404(
        self, client, environment, monkeypatch
    ):
        async def fake_decide(db, group_slug, site_slug, visitor):
            return allow(uuid.uuid4(), AccessPolicy(AccessBase.PUBLIC))

        monkeypatch.setattr(gate, "decide", fake_decide)
        response = await client.get("/aurora/site/")
        assert response.status_code == 404
        assert response.content == b"Niet gevonden\n"
        rows = await _audit_rows(environment)
        assert rows[-1].reason_code == "UNKNOWN_STORAGE"

    async def test_a_key_selector_for_a_site_that_does_not_exist_sets_no_cookie(
        self, client, environment, monkeypatch
    ):
        # decision.key_selector set and a ?key= query present, but the group
        # and site the request names have no row of their own: the second,
        # independent site_id lookup inside _key_cookie_value finds nothing.
        async def fake_decide(db, group_slug, site_slug, visitor):
            return allow(uuid.uuid4(), AccessPolicy(AccessBase.PUBLIC), key_selector="AbCdEfGh")

        monkeypatch.setattr(gate, "decide", fake_decide)
        response = await client.get("/aurora/nietbestaand/?key=AbCdEfGh.dummy")
        assert response.status_code == 404
        assert response.content == b"Niet gevonden\n"
        assert "set-cookie" not in response.headers


class TestEtag304:
    async def test_if_none_match_gives_304(self, client, environment):
        etag = f'"{environment.world.site_live_id}"'
        response = await client.get("/aurora/site/", headers={"If-None-Match": etag})
        assert response.status_code == 304
        assert response.content == b""
        assert response.headers["etag"] == etag
        assert response.headers["cache-control"] == "no-cache, must-revalidate"

    async def test_no_304_for_a_path_without_a_file(self, client, environment):
        # The conditional answer comes after resolution; a 304 here would let
        # an intermediary treat a non-existent resource as fresh.
        response = await client.get("/aurora/zonder404/bestaat-niet", headers={"If-None-Match": "*"})
        assert response.status_code == 404
        assert response.content == b"Niet gevonden\n"

    async def test_no_304_for_a_path_that_falls_back_to_404_html(self, client, environment):
        response = await client.get("/aurora/site/bestaat-niet", headers={"If-None-Match": "*"})
        assert response.status_code == 404
        assert response.content == SITE_404

    async def test_no_304_for_a_directory_that_still_has_to_redirect(self, client, environment):
        response = await client.get("/aurora/site/docs", headers={"If-None-Match": "*"})
        assert response.status_code == 301
        assert response.headers["location"] == "/aurora/site/docs/"

    async def test_if_none_match_star_matches_any_existing_file(self, client, environment):
        response = await client.get("/aurora/site/", headers={"If-None-Match": "*"})
        assert response.status_code == 304
        assert response.content == b""

    async def test_weak_etag_matches_also(self, client, environment):
        response = await client.get(
            "/aurora/site/", headers={"If-None-Match": f'W/"{environment.world.site_live_id}"'}
        )
        assert response.status_code == 304

    async def test_wrong_etag_gives_200(self, client):
        response = await client.get("/aurora/site/", headers={"If-None-Match": f'"{uuid.uuid4()}"'})
        assert response.status_code == 200
        assert response.content == SITE_INDEX

    async def test_304_on_preview_keeps_noindex(self, client, environment):
        etag = f'"{environment.world.preview_version_id}"'
        response = await client.get("/aurora/site/_preview/pr-42/", headers={"If-None-Match": etag})
        assert response.status_code == 304
        assert response.headers["x-robots-tag"] == "noindex, nofollow"

    @staticmethod
    async def _assert_304_repeats_the_200(client, path: str) -> None:
        full = await client.get(path)
        assert full.status_code == 200, path
        revalidated = await client.get(path, headers={"If-None-Match": full.headers["etag"]})
        assert revalidated.status_code == 304, path
        assert {k for k in revalidated.headers if k != "content-length"} == {
            k for k in full.headers if k not in ("content-length", "content-type", "accept-ranges", "last-modified")
        }, path
        for name in revalidated.headers:
            if name != "content-length":
                assert revalidated.headers[name] == full.headers[name], (path, name)

    async def test_304_repeats_the_headers_of_the_200(self, client, environment):
        """A browser keeps the headers it stored with the 200 and overwrites
        only those a 304 repeats, so the 304 carries every one of them but
        Content-Type: each switch combination, a page and an asset, private
        content and a preview with its noindex."""
        for path in (
            "/aurora/site/",
            "/aurora/extern/",
            "/aurora/afgeschermd/",
            "/aurora/extern/stijl.css",
            "/aurora/site/_preview/pr-42/",
        ):
            await self._assert_304_repeats_the_200(client, path)
        async with environment.factory() as db:
            site = (await db.scalars(select(Site).where(Site.slug == "extern"))).one()
            site.sandbox = True
            await db.commit()
        await self._assert_304_repeats_the_200(client, "/aurora/extern/")
        set_content_session_cookie(client, environment.app, sub="willekeurige-kijker", sites=("/aurora/intern/",))
        await self._assert_304_repeats_the_200(client, "/aurora/intern/")

    async def test_a_switch_turned_after_caching_reaches_the_cached_page(self, client, environment):
        """The version and so the ETag stay the same when a site admin turns
        the shielding on; without the CSP on the 304 the visitor's cached
        page would keep running unshielded."""
        cached = await client.get("/aurora/site/")
        assert "sandbox" not in cached.headers["content-security-policy"]
        async with environment.factory() as db:
            site = (await db.scalars(select(Site).where(Site.slug == "site"))).one()
            site.sandbox = True
            await db.commit()
        revalidated = await client.get("/aurora/site/", headers={"If-None-Match": cached.headers["etag"]})
        assert revalidated.status_code == 304
        assert revalidated.headers["content-security-policy"] == SANDBOX_CSP

    async def test_304_of_a_secret_link_site_keeps_its_referrer_policy(self, client, environment):
        await client.get(f"/aurora/geheim/?key={environment.world.key_plain}")
        etag = (await client.get("/aurora/geheim/")).headers["etag"]
        revalidated = await client.get("/aurora/geheim/", headers={"If-None-Match": etag})
        assert revalidated.status_code == 304
        assert revalidated.headers["referrer-policy"] == "same-origin"



class TestEtag304Refused:
    """If-None-Match never gets past the access decision: whoever may not see
    a page gets the refusal they would get without it, never a 304 that
    confirms the version and carries the site's own policy."""

    async def test_anonymous_on_a_secret_link_site_gets_the_neutral_404(self, client):
        reference = await client.get("/aurora/bestaat-niet/")
        response = await client.get("/aurora/geheim/", headers={"If-None-Match": "*"})
        assert response.status_code == 404
        assert response.content == reference.content
        assert _header_list(response) == _header_list(reference)

    async def test_anonymous_on_a_group_site_goes_to_the_login(self, client):
        response = await client.get("/aurora/intern/", headers={"If-None-Match": "*"})
        assert response.status_code == 302
        assert response.headers["location"].startswith("/-/login?")

    async def test_a_foreign_subresource_gets_the_neutral_404(self, client):
        reference = await client.get("/aurora/bestaat-niet/")
        response = await client.get(
            "/aurora/intern/stijl.css",
            headers={
                "If-None-Match": "*",
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Dest": "empty",
                "Referer": f"{BASE_URL}/aurora/site/",
            },
        )
        assert response.status_code == 404
        assert response.content == reference.content
        assert _header_list(response) == _header_list(reference)

    async def test_a_version_view_for_a_non_member_gets_the_neutral_404(self, client, environment):
        reference = await client.get("/aurora/bestaat-niet/")
        set_content_session_cookie(client, environment.app, sub="buitenstaander", sites=("/aurora/site/",))
        response = await client.get(
            f"/aurora/site/_version/{environment.world.site_live_id}/",
            headers={"If-None-Match": f'"{environment.world.site_live_id}"'},
        )
        assert response.status_code == 404
        assert response.content == reference.content
        assert _header_list(response) == _header_list(reference)


class TestHead:
    """HEAD is GET without the body (RFC 9110 §9.3.2): the same decision and
    the same headers, so a link checker or a monitor sees what a browser
    would, and a refusal stays the one neutral 404 (head_requests.py).

    httpx drops a HEAD body itself, so the empty body is asserted where the
    raw ASGI messages are read: here in the invalid-path test, and in
    test_head_requests.py."""

    async def test_a_page_answers_with_the_headers_of_the_get(self, client):
        for path in ("/aurora/site/", "/aurora/extern/stijl.css", "/aurora/site/_preview/pr-42/"):
            get = await client.get(path)
            head = await client.head(path)
            assert head.status_code == 200, path
            assert _header_list(head) == _header_list(get), path

    async def test_a_range_head_answers_like_the_range_get(self, client):
        get = await client.get("/aurora/site/diep/map/bestand.txt", headers={"Range": "bytes=1-2"})
        head = await client.head("/aurora/site/diep/map/bestand.txt", headers={"Range": "bytes=1-2"})
        assert head.status_code == get.status_code == 206
        assert _header_list(head) == _header_list(get)

    async def test_every_refusal_is_the_neutral_404_of_the_get(self, client):
        reference = await client.get("/aurora/bestaat-niet/")
        for path in (
            "/nergens/niks/",
            "/aurora/bestaat-niet/",
            "/aurora/geheim/",
            "/aurora/site/_preview/pr-999/",
            "/aurora/site/_version/geen-uuid/",
            "/aurora/zonder404/bestaat-niet",
            "/robots.txt/onbekend",
            "/aurora",
            "/bestaat-niet",
        ):
            head = await client.head(path)
            assert head.status_code == 404, path
            assert _header_list(head) == _header_list(reference), path

    async def test_an_invalid_path_is_the_neutral_404_too(self, client, environment):
        reference = await client.get("/aurora/bestaat-niet/")
        status, headers, body = await _raw_request(environment.app, "/aurora/site/..%2fx", method="HEAD")
        assert status == 404
        assert body == b""
        assert headers == _header_list(reference)

    async def test_a_foreign_subresource_is_refused_like_the_get(self, client):
        headers = {
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": f"{BASE_URL}/aurora/site/",
        }
        reference = await client.get("/aurora/bestaat-niet/")
        head = await client.head("/aurora/intern/stijl.css", headers=headers)
        assert head.status_code == 404
        assert _header_list(head) == _header_list(reference)

    async def test_an_anonymous_navigation_to_a_preview_gets_the_login_redirect(self, client):
        navigation = {"Sec-Fetch-Dest": "document"}
        get = await client.get("/aurora/site/_preview/pr-besloten/", headers=navigation)
        head = await client.head("/aurora/site/_preview/pr-besloten/", headers=navigation)
        assert head.status_code == get.status_code == 302
        assert head.headers["location"] == get.headers["location"]

    async def test_the_slash_redirect_and_the_304_answer_head_too(self, client, environment):
        redirect = await client.head("/aurora/site")
        assert redirect.status_code == 301
        assert redirect.headers["location"] == "/aurora/site/"
        etag = f'"{environment.world.site_live_id}"'
        conditional = await client.head("/aurora/site/", headers={"If-None-Match": etag})
        assert conditional.status_code == 304

    async def test_the_code_page_answers_head_without_its_form(self, client, environment):
        get = await client.get(f"/aurora/geheim/?key={environment.world.key_plain.split('.')[0]}")
        head = await client.head(f"/aurora/geheim/?key={environment.world.key_plain.split('.')[0]}")
        assert head.status_code == get.status_code == 200
        assert _header_list(head) == _header_list(get)

    async def test_a_secret_link_redeemed_by_head_sets_the_cookie_like_the_get(self, client, environment):
        """Redemption only checks the key and sets a cookie; nothing is used
        up, so a HEAD from a link previewer does what the click would."""
        head = await client.head(f"/aurora/geheim/?key={environment.world.key_plain}")
        assert head.status_code == 302
        assert head.headers["location"] == "/aurora/geheim/"
        assert head.headers["set-cookie"].startswith(f"{KEY_COOKIE}=")

    async def test_a_head_on_protected_content_counts_as_a_look(self, client, environment):
        """The gate does not tell HEAD from GET, so neither does the audit: a
        HEAD on non-public content is one row, like the page itself."""
        set_content_session_cookie(client, environment.app, sub="willekeurige-kijker", sites=("/aurora/intern/",))
        assert (await client.head("/aurora/intern/")).status_code == 200
        rows = await _audit_rows(environment)
        assert [(row.result, row.refs["site"]) for row in rows] == [("allowed", "intern")]

    async def test_a_head_without_a_session_gets_the_refusal_of_the_get(self, client):
        get = await client.get("/aurora/intern/")
        head = await client.head("/aurora/intern/")
        assert head.status_code == get.status_code
        assert head.headers.get("location") == get.headers.get("location")


class TestKey:
    async def test_valid_key_becomes_redeemed_for_cookie_and_302_without_key(self, client, environment):
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_plain}")
        assert response.status_code == 302
        assert response.headers["location"] == "/aurora/geheim/"
        assert response.headers["cache-control"] == "no-store"
        assert response.content == b""
        set_cookie = response.headers["set-cookie"]
        cookie_value = set_cookie.split(";", 1)[0].removeprefix(f"{KEY_COOKIE}=")
        secret = environment.app.state.settings.session_secret
        assert check_signature(secret, cookie_value) == f"key:{environment.world.key_id}"
        assert "Path=/aurora/geheim/" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        # SameSite=none: a sandboxed page is cross-site with its own site.
        assert "SameSite=none" in set_cookie

        # The client took the cookie over: the follow-up request on the clean
        # URL yields the content with the private header set.
        followed = await client.get(response.headers["location"])
        assert followed.status_code == 200
        assert followed.content == SECRET_INDEX
        assert followed.headers["cache-control"] == "private, no-cache, must-revalidate"
        assert followed.headers["referrer-policy"] == "same-origin"
        assert "set-cookie" not in followed.headers

    async def test_redeem_keeps_other_query_parameters_and_deep_path(self, client, environment):
        response = await client.get(f"/aurora/geheim/map/?utm=x&key={environment.world.key_plain}&b=1")
        assert response.status_code == 302
        assert response.headers["location"] == "/aurora/geheim/map/?utm=x&b=1"
        assert "key" not in response.headers["location"]

    async def test_redeem_on_preview_sets_cookie_on_preview_path(self, client, environment):
        async with environment.factory() as db:
            site = await db.scalar(select(Site).where(Site.slug == "geheim"))
            version = await db.get(Version, environment.world.secret_live_id)
            db.add(Preview(site_id=site.id, ref="pr-7", version_id=version.id))
            await db.commit()
        response = await client.get(f"/aurora/geheim/_preview/pr-7/?key={environment.world.key_plain}")
        assert response.status_code == 302
        assert response.headers["location"] == "/aurora/geheim/_preview/pr-7/"
        assert "Path=/aurora/geheim/_preview/pr-7/" in response.headers["set-cookie"]

    async def test_redeem_goes_for_if_none_match(self, client, environment):
        etag = f'"{environment.world.secret_live_id}"'
        response = await client.get(
            f"/aurora/geheim/?key={environment.world.key_plain}", headers={"If-None-Match": etag}
        )
        assert response.status_code == 302
        assert "set-cookie" in response.headers

    async def test_cookie_only_gives_afterwards_access(self, client, environment):
        client.cookies.set(KEY_COOKIE, _signed_key_cookie(environment), domain="plak.example", path="/")
        response = await client.get("/aurora/geheim/")
        assert response.status_code == 200
        assert response.content == SECRET_INDEX
        # No fresh Set-Cookie without ?key=.
        assert "set-cookie" not in response.headers

    async def test_an_unverifiable_key_query_alongside_a_valid_cookie_sets_no_new_cookie(
        self, client, environment
    ):
        # Access already came in on the cookie; a garbage ?key= alongside it
        # must not crash and must not trigger a fresh Set-Cookie.
        client.cookies.set(KEY_COOKIE, _signed_key_cookie(environment), domain="plak.example", path="/")
        response = await client.get("/aurora/geheim/?key=onbruikbaar.waarde")
        assert response.status_code == 200
        assert response.content == SECRET_INDEX
        assert "set-cookie" not in response.headers

    async def test_bare_key_id_as_cookie_is_neutral_404(self, client, environment):
        """The key id alone is no credential: only a cookie Plak signed itself counts."""
        client.cookies.set(KEY_COOKIE, str(environment.world.key_id), domain="plak.example", path="/")
        response = await client.get("/aurora/geheim/")
        assert response.status_code == 404

    async def test_tampered_key_cookie_is_neutral_404(self, client, environment):
        tampered = _signed_key_cookie(environment)[:-2] + "xx"
        client.cookies.set(KEY_COOKIE, tampered, domain="plak.example", path="/")
        response = await client.get("/aurora/geheim/")
        assert response.status_code == 404

    async def test_a_signature_without_the_key_purpose_prefix_is_neutral_404(self, client, environment):
        """A validly signed value for another purpose (e.g. what an older
        cookie, or a session cookie, would look like) must not be accepted as
        a key cookie: the purpose prefix, not just the signature, decides."""
        secret = environment.app.state.settings.session_secret
        unprefixed = sign(secret, str(environment.world.key_id))
        client.cookies.set(KEY_COOKIE, unprefixed, domain="plak.example", path="/")
        response = await client.get("/aurora/geheim/")
        assert response.status_code == 404

    @pytest.mark.parametrize("cookie", [KEY_COOKIE, CONTENT_SESSION_COOKIE])
    async def test_a_non_ascii_cookie_is_the_neutral_404_not_a_500(self, client, cookie):
        """A cookie arrives latin-1 decoded; a non-ASCII value must end in the
        same refusal as an anonymous visitor, byte for byte."""
        anonymous = await client.get("/aurora/geheim/")
        response = await client.get("/aurora/geheim/", headers={"cookie": f"{cookie}=\xe9.x".encode("latin-1")})
        assert response.status_code == 404
        assert response.content == anonymous.content

    async def test_invalid_key_is_neutral_404(self, client):
        response = await client.get("/aurora/geheim/?key=verkeerd.sleutelwaarde")
        assert response.status_code == 404
        assert "set-cookie" not in response.headers

    async def test_revoked_key_stops_working_even_with_a_valid_cookie(self, client, environment):
        redeemed = await client.get(f"/aurora/geheim/?key={environment.world.key_plain}")
        assert redeemed.status_code == 302

        async with environment.factory() as db:
            assert await keys.revoke(db, environment.world.key_id)
            await db.commit()

        response = await client.get("/aurora/geheim/")
        assert response.status_code == 404

    async def test_asset_under_key_also_private(self, client, environment):
        client.cookies.set(KEY_COOKIE, _signed_key_cookie(environment), domain="plak.example", path="/")
        response = await client.get("/aurora/geheim/map/")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-cache, must-revalidate"


class TestPreview:
    async def test_preview_serves_preview_version_with_noindex(self, client, environment):
        response = await client.get("/aurora/site/_preview/pr-42/")
        assert response.status_code == 200
        assert response.content == PREVIEW_INDEX
        assert response.headers["etag"] == f'"{environment.world.preview_version_id}"'
        assert response.headers["x-robots-tag"] == "noindex, nofollow"
        # Public preview (inherits publiek): no private prefix.
        assert response.headers["cache-control"] == "no-cache, must-revalidate"

    async def test_login_gated_preview_anonymous_neutral_404(self, client):
        # Override sso: for an anonymous visitor no login redirect but a neutral 404.
        response = await client.get("/aurora/site/_preview/pr-besloten/")
        assert response.status_code == 404
        assert "location" not in response.headers

    async def test_login_gated_preview_with_session(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="willekeurige-kijker", sites=("/aurora/site/",))
        response = await client.get("/aurora/site/_preview/pr-besloten/")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-cache, must-revalidate"
        assert response.headers["x-robots-tag"] == "noindex, nofollow"

    async def test_unknown_preview_neutral_404(self, client):
        response = await client.get("/aurora/site/_preview/pr-999/")
        assert response.status_code == 404


class TestVersionView:
    async def test_active_group_member_sees_version(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="lid-actief", sites=("/aurora/site/",))
        response = await client.get(f"/aurora/site/_version/{environment.world.preview_version_id}/")
        assert response.status_code == 200
        assert response.content == PREVIEW_INDEX
        # Always private for _version views, plus noindex.
        assert response.headers["cache-control"].startswith("private, ")
        assert response.headers["x-robots-tag"] == "noindex, nofollow"

    async def test_anonymous_neutral_404(self, client, environment):
        response = await client.get(f"/aurora/site/_version/{environment.world.site_live_id}/")
        assert response.status_code == 404

    async def test_invalid_uuid_neutral_404(self, client):
        response = await client.get("/aurora/site/_version/geen-uuid/")
        assert response.status_code == 404

    async def test_not_member_neutral_404(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="buitenstaander", sites=("/aurora/site/",))
        response = await client.get(f"/aurora/site/_version/{environment.world.site_live_id}/")
        assert response.status_code == 404


class TestExternalSources:
    """The per-site "externe bronnen toestaan": on by default, and switching it
    off narrows the content CSP on every response that carries it."""

    async def test_a_site_that_switched_it_off_gets_the_strict_policy(self, client):
        response = await client.get("/aurora/site/")
        assert response.headers["content-security-policy"] == FULL_CSP

    async def test_live_page_and_asset_get_the_external_policy_by_default(self, client):
        for path in ("/aurora/extern/", "/aurora/extern/stijl.css"):
            response = await client.get(path)
            assert response.status_code == 200, path
            assert response.headers["content-security-policy"] == EXTERNAL_CSP, path

    async def test_preview_gets_the_external_policy(self, client):
        response = await client.get("/aurora/extern/_preview/pr-extern/")
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == EXTERNAL_CSP

    async def test_version_view_gets_the_external_policy(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="lid-actief", sites=("/aurora/extern/",))
        response = await client.get(f"/aurora/extern/_version/{environment.world.external_live_id}/")
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == EXTERNAL_CSP

    async def test_neutral_404_keeps_the_strict_policy(self, client):
        """The 404 stays byte-identical whatever a site allows; it may not
        become a way to tell which site a path belonged to."""
        response = await client.get("/aurora/extern/bestaat-niet/")
        assert response.status_code == 404
        assert response.headers["content-security-policy"] == PLATFORM_CSP


class TestSandbox:
    """The per-site shielding: on by default, and it adds the sandbox directive
    to every response that carries the content CSP."""

    async def test_a_site_that_switched_it_off_gets_the_policy_without_sandbox(self, client):
        response = await client.get("/aurora/site/")
        assert "sandbox" not in response.headers["content-security-policy"]

    async def test_live_page_and_asset_get_the_sandbox_by_default(self, client):
        for path in ("/aurora/afgeschermd/", "/aurora/afgeschermd/stijl.css"):
            response = await client.get(path)
            assert response.status_code == 200, path
            assert response.headers["content-security-policy"] == SANDBOX_CSP, path

    async def test_preview_gets_the_sandbox(self, client):
        response = await client.get("/aurora/afgeschermd/_preview/pr-afgeschermd/")
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == SANDBOX_CSP

    async def test_version_view_gets_the_sandbox(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="lid-actief", sites=("/aurora/afgeschermd/",))
        response = await client.get(
            f"/aurora/afgeschermd/_version/{environment.world.sandboxed_live_id}/"
        )
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == SANDBOX_CSP

    async def test_it_rides_along_with_external_sources(self, client, environment):
        """The two switches are independent, so a site with both on has to get
        both additions rather than whichever the code looked at last."""
        async with environment.factory() as db:
            site = (await db.scalars(select(Site).where(Site.slug == "afgeschermd"))).one()
            site.external_sources = True
            await db.commit()
        response = await client.get("/aurora/afgeschermd/")
        assert response.headers["content-security-policy"] == EXTERNAL_CSP + SANDBOX_DIRECTIVE

    async def test_neutral_404_keeps_the_policy_without_sandbox(self, client):
        """The 404 stays byte-identical whatever a site sets; it may not become
        a way to tell which site a path belonged to."""
        response = await client.get("/aurora/afgeschermd/bestaat-niet/")
        assert response.status_code == 404
        assert response.headers["content-security-policy"] == PLATFORM_CSP


class TestSandboxedOwnSubresource:
    """The sandbox gives the document an opaque origin, which makes it
    cross-site with its own site: its stylesheets, scripts and images arrive
    without a Referer, without an Origin and as `Sec-Fetch-Site: cross-site`.
    A non-public site has to serve them anyway, or it loses every asset it
    has.
    """

    # What a browser sends for a subresource of a sandboxed page. Measured,
    # not assumed: no Referer, and cross-site even though the URL is the
    # page's own site.
    OWN_SUBRESOURCE: ClassVar[dict[str, str]] = {
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "no-cors",
        "Sec-Fetch-Dest": "style",
    }

    @staticmethod
    async def _sandbox_on(environment, slug: str) -> None:
        async with environment.factory() as db:
            site = (await db.scalars(select(Site).where(Site.slug == slug))).one()
            site.sandbox = True
            await db.commit()

    async def test_a_member_gets_the_sites_own_asset(self, client, environment):
        await self._sandbox_on(environment, "intern")
        set_content_session_cookie(client, environment.app, sub="lid-actief", sites=("/aurora/intern/",))
        response = await client.get("/aurora/intern/stijl.css", headers=self.OWN_SUBRESOURCE)
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == SANDBOX_CSP

    async def test_a_secret_link_site_gets_its_own_asset(self, client, environment):
        await self._sandbox_on(environment, "geheim")
        await client.get(f"/aurora/geheim/?key={environment.world.key_plain}")
        response = await client.get("/aurora/geheim/stijl.css", headers=self.OWN_SUBRESOURCE)
        assert response.status_code == 200

    async def test_without_a_credential_it_is_still_refused(self, client, environment):
        """The shape is not a credential of its own: an anonymous request in
        exactly the same shape keeps the neutral 404."""
        await self._sandbox_on(environment, "geheim")
        response = await client.get("/aurora/geheim/stijl.css", headers=self.OWN_SUBRESOURCE)
        assert response.status_code == 404
        assert response.content == b"Niet gevonden\n"

    async def test_the_cookies_it_needs_are_ones_a_browser_sends_cross_site(
        self, client, environment
    ):
        """Whatever the serving layer hands a visitor of a non-public site has
        to survive the trip from an opaque origin, so SameSite=none with
        Secure. Under Lax the browser withholds it and the page loads without
        a single one of its own assets."""
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_plain}")
        set_cookie = response.headers["set-cookie"]
        assert "SameSite=none" in set_cookie
        assert "Secure" in set_cookie


class TestNeutral404ByteIdentical:
    async def test_all_refusal_variants_are_byte_identical(self, client, environment):
        paths = [
            "/nergens/niks/",  # unknown groep
            "/aurora/bestaat-niet/",  # unknown site
            "/aurora/leeg/",  # public site without a live versie
            "/aurora/geheim/",  # refusal (key, anonymous)
            "/aurora/geheim/?key=fout.fout",  # invalid key
            "/aurora/site/_preview/pr-999/",  # unknown preview
            "/aurora/site/_preview/pr-besloten/",  # login-gated preview, anonymous
            "/aurora/site/_version/geen-uuid/",  # invalid versie id
            f"/aurora/site/_version/{uuid.uuid4()}/",  # versie without membership
            "/aurora/zonder404/bestaat-niet",  # allowed, file missing, no 404.html
            "/robots.txt/onbekend",  # reserved slug
        ]
        responses_ = [await client.get(path) for path in paths]
        ref = responses_[0]
        assert ref.status_code == 404
        for path, response in zip(paths, responses_, strict=True):
            assert response.status_code == 404, path
            assert response.content == ref.content, path
            assert _header_list(response) == _header_list(ref), path

    async def test_also_path_validation_404_is_identical(self, client, environment):
        via_client = await client.get("/nergens/niks/")
        status, headers, body = await _raw_request(environment.app, "/aurora/site/..%2fx")
        assert status == 404
        assert body == via_client.content
        assert headers == _header_list(via_client)


class TestSiteScopedSession:
    """The content session cookie carries `Path=/{group}/{site}/`, so a
    request aimed at a site this browser has not opened carries no session and
    the gate sees an anonymous visitor. No header is read for this, and that
    is the point: nothing here can be shaped by the page that asks. It is not
    the whole site boundary, because a cookie path is matched against the
    requested URL and not against the asking page, so a site the visitor has
    opened does have a cookie that goes along; `_foreign_subresource` and the
    origin per site are what hold there.
    """

    async def test_the_site_it_was_issued_for_keeps_working(self, client, environment):
        set_content_session_cookie(
            client, environment.app, sub="willekeurige-kijker", sites=("/aurora/intern/",)
        )
        assert (await client.get("/aurora/intern/")).status_code == 200
        assert (await client.get("/aurora/intern/stijl.css")).status_code == 200

    async def test_a_session_for_one_site_is_anonymous_on_another(self, client, environment):
        set_content_session_cookie(
            client, environment.app, sub="willekeurige-kijker", sites=("/aurora/site/",)
        )
        response = await client.get("/aurora/intern/")
        assert response.status_code == 302
        assert response.headers["location"].startswith("/-/login?")

    async def test_the_refusal_is_the_missing_cookie_and_not_the_referer_guard(self, client, environment):
        """The same request as the fetch this fixes, but with no fetch
        metadata, so `_foreign_subresource` returns False and cannot be what
        refuses it. What is left is a visitor without a session."""
        set_content_session_cookie(
            client, environment.app, sub="willekeurige-kijker", sites=("/aurora/site/",)
        )
        response = await client.get("/aurora/intern/", headers={"Referer": f"{BASE_URL}/aurora/site/"})
        assert response.status_code == 302
        rows = await _audit_rows(environment)
        assert [(row.result, row.reason_code) for row in rows] == [("login_redirect", "LOGIN_REQUIRED")]


class TestFirstVisitToPreviewOrVersion:
    """Neither route hands an anonymous visitor a login redirect of its own,
    and a site without a live version has no live route that could, so a
    member's first request to a preview would be the neutral 404 although
    logging in would let them in. Every anonymous top-level navigation to one
    of these routes therefore goes to the login, whether or not anything
    exists behind the path."""

    NAVIGATION: ClassVar[dict[str, str]] = {"Sec-Fetch-Dest": "document"}

    async def test_a_member_without_the_site_cookie_is_sent_to_the_login(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="lid-actief")
        path = f"/aurora/site/_version/{environment.world.site_live_id}/"
        response = await client.get(path, headers=self.NAVIGATION)
        assert response.status_code == 302
        assert response.headers["location"] == f"/-/login?returnTo={quote(path, safe='')}"

    async def test_a_preview_likewise(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="willekeurige-kijker")
        response = await client.get("/aurora/site/_preview/pr-besloten/", headers=self.NAVIGATION)
        assert response.status_code == 302

    async def test_an_anonymous_visitor_is_sent_to_the_login(self, client, environment):
        """No content session anywhere, as after logging in on the admin host
        only: the case this redirect exists for."""
        for path in (
            f"/aurora/site/_version/{environment.world.site_live_id}/",
            "/aurora/site/_preview/pr-besloten/",
        ):
            response = await client.get(path, headers=self.NAVIGATION)
            assert response.status_code == 302, path
            assert response.headers["location"] == f"/-/login?returnTo={quote(path, safe='')}", path

    async def test_the_redirect_does_not_depend_on_what_exists(self, client, environment):
        """Guessing paths teaches nothing: an unknown group, site, preview or
        version gets the same answer as one that exists, byte for byte apart
        from the returnTo that echoes the requested path."""
        paths = [
            "/nergens/niks/_preview/pr-1/",  # unknown group
            "/aurora/bestaat-niet/_preview/pr-1/",  # unknown site
            "/aurora/leeg/_preview/pr-1/",  # site without a live version
            "/aurora/site/_preview/pr-999/",  # unknown preview
            "/aurora/site/_preview/pr-besloten/",  # existing login-gated preview
            "/aurora/site/_version/geen-uuid/",  # invalid version id
            f"/aurora/site/_version/{uuid.uuid4()}/",  # unknown version
            f"/aurora/site/_version/{environment.world.site_live_id}/",  # existing version
        ]
        responses_ = [await client.get(path, headers=self.NAVIGATION) for path in paths]
        ref = responses_[0]

        def without_location(response: httpx.Response) -> list[tuple[str, str]]:
            return [(k, v) for k, v in _header_list(response) if k != "location"]

        for path, response in zip(paths, responses_, strict=True):
            assert response.status_code == 302, path
            assert response.headers["location"] == f"/-/login?returnTo={quote(path, safe='')}", path
            assert response.content == ref.content, path
            assert without_location(response) == without_location(ref), path

    async def test_a_public_preview_is_served_to_an_anonymous_navigation(self, client, environment):
        """Only a refusal becomes the redirect: a visitor without an SSO
        account still opens a public preview."""
        response = await client.get("/aurora/site/_preview/pr-42/", headers=self.NAVIGATION)
        assert response.status_code == 200
        assert response.content == PREVIEW_INDEX
        assert "location" not in response.headers

    async def test_the_redirect_is_audited_with_the_reason_that_refused(self, client, environment):
        """Outward every path gets the same redirect; the audit log keeps what
        lay behind it, so probing for refs or version ids stays visible."""
        for path in (
            "/aurora/site/_preview/pr-besloten/",
            "/aurora/site/_preview/pr-999/",
            "/nergens/niks/_preview/pr-1/",
            f"/aurora/site/_version/{uuid.uuid4()}/",
        ):
            await client.get(path, headers=self.NAVIGATION)
        rows = await _audit_rows(environment)
        assert [(row.result, row.reason_code) for row in rows] == [
            ("login_redirect", "NO_ACCESS"),
            ("login_redirect", "UNKNOWN_PREVIEW"),
            ("login_redirect", "UNKNOWN_GROUP"),
            ("login_redirect", "NO_ACCESS"),
        ]

    @pytest.mark.parametrize(
        "headers",
        [
            {},  # no fetch metadata: curl, a link checker
            {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Dest": "empty"},
            {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Dest": "image"},
            {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Dest": "iframe"},
        ],
    )
    async def test_anything_but_a_top_level_navigation_keeps_the_neutral_404(
        self, client, environment, headers
    ):
        """Another site's page cannot walk a request through the login and
        collect a session on the way back, and nobody logs in because of an
        image."""
        response = await client.get("/aurora/site/_preview/pr-besloten/", headers=headers)
        assert response.status_code == 404
        assert "location" not in response.headers

    async def test_a_subresource_gets_no_redirect_to_chain(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="lid-actief")
        response = await client.get(
            f"/aurora/site/_version/{environment.world.site_live_id}/",
            headers={"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Dest": "empty"},
        )
        assert response.status_code == 404

    async def test_a_refused_key_in_the_query_keeps_the_neutral_404(self, client, environment):
        """Whoever came in on a secret link may have no SSO Rijk account at
        all; a login they cannot complete helps nobody."""
        response = await client.get(
            "/aurora/site/_preview/pr-besloten/?key=fout.fout", headers=self.NAVIGATION
        )
        assert response.status_code == 404
        assert "location" not in response.headers

    async def test_a_refused_key_cookie_keeps_the_neutral_404(self, client, environment):
        client.cookies.set(KEY_COOKIE, _signed_key_cookie(environment), domain="plak.example", path="/")
        response = await client.get("/aurora/site/_preview/pr-besloten/", headers=self.NAVIGATION)
        assert response.status_code == 404
        assert "location" not in response.headers

    async def test_a_path_the_login_cannot_scope_a_cookie_to_keeps_the_neutral_404(self, client, environment):
        """The login hands out a site cookie only for a `/{group}/{site}/` of
        slugs. For any other path it would send the visitor back without one,
        the gate would see them as anonymous again, and the redirect would
        loop."""
        response = await client.get("/Aurora/site/_preview/pr-besloten/", headers=self.NAVIGATION)
        assert response.status_code == 404
        assert "location" not in response.headers

    async def test_a_visitor_with_a_session_and_no_access_gets_the_neutral_404(self, client, environment):
        """Once logged in the refusal is the neutral 404 and not another round
        to the login, so a visitor without access does not loop either."""
        set_content_session_cookie(
            client, environment.app, sub="willekeurige-kijker", sites=("/aurora/site/",)
        )
        response = await client.get(
            f"/aurora/site/_version/{environment.world.site_live_id}/", headers=self.NAVIGATION
        )
        assert response.status_code == 404
        assert "location" not in response.headers

    async def test_live_content_needs_none_of_this(self, client, environment):
        """Wherever a session could grant anything on live content, the gate
        already answers an anonymous visitor with the login redirect; where it
        could not, the neutral 404 stays."""
        response = await client.get("/aurora/geheim/", headers=self.NAVIGATION)
        assert response.status_code == 404


class TestForeignSubresource:
    """Defence in depth beside the site-scoped session cookie, for a request
    that does carry a credential of its own: non-public content is served to a
    subresource request only when the Referer puts it inside the same site.
    """

    SUBRESOURCE: ClassVar[dict[str, str]] = {
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    }

    @staticmethod
    def _viewer(client, environment) -> None:
        # A cookie for every site in play, so this guard and not the scoped
        # cookie is what these tests measure.
        set_content_session_cookie(
            client,
            environment.app,
            sub="willekeurige-kijker",
            sites=("/aurora/site/", "/aurora/intern/", "/aurora/geheim/"),
        )

    async def test_a_fetch_from_another_site_is_refused(self, client, environment):
        self._viewer(client, environment)
        response = await client.get(
            "/aurora/intern/", headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/site/"}
        )
        assert response.status_code == 404
        assert response.content == b"Niet gevonden\n"

    async def test_the_sites_own_subresources_keep_loading(self, client, environment):
        self._viewer(client, environment)
        for dest in ("style", "script", "image", "font", "empty"):
            response = await client.get(
                "/aurora/intern/stijl.css",
                headers={
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Dest": dest,
                    "Referer": f"{BASE_URL}/aurora/intern/",
                },
            )
            assert response.status_code == 200, dest

    async def test_a_deeper_page_of_the_same_site_counts_as_its_own(self, client, environment):
        self._viewer(client, environment)
        response = await client.get(
            "/aurora/intern/stijl.css",
            headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/intern/diep/pagina.html"},
        )
        assert response.status_code == 200

    async def test_navigation_from_another_site_keeps_working(self, client, environment):
        self._viewer(client, environment)
        response = await client.get(
            "/aurora/intern/",
            headers={
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
                "Referer": f"{BASE_URL}/aurora/site/",
            },
        )
        assert response.status_code == 200
        assert response.content == b"<h1>intern</h1>"

    @pytest.mark.parametrize("dest", ["iframe", "frame"])
    async def test_an_embedded_document_keeps_working(self, client, environment, dest):
        self._viewer(client, environment)
        response = await client.get(
            "/aurora/intern/",
            headers={"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Dest": dest},
        )
        assert response.status_code == 200

    async def test_a_client_without_fetch_metadata_is_not_turned_away(self, client, environment):
        # curl, a link checker, a browser older than the header: no ambient
        # credentials, so nothing to abuse, and breaking them would cost more
        # than the guard wins.
        self._viewer(client, environment)
        response = await client.get("/aurora/intern/")
        assert response.status_code == 200

    async def test_a_request_the_visitor_started_themselves_is_not_turned_away(self, client, environment):
        self._viewer(client, environment)
        response = await client.get(
            "/aurora/intern/", headers={"Sec-Fetch-Site": "none", "Sec-Fetch-Dest": "document"}
        )
        assert response.status_code == 200

    async def test_an_anonymous_foreign_subresource_is_the_neutral_404_not_a_login_redirect(
        self, client, environment
    ):
        # Without a session the login redirect would otherwise win, and a 302
        # says that this site exists. Nobody logs in because of a stylesheet
        # fetch, so for this class the neutral 404 comes first.
        reference = await client.get("/aurora/bestaat-niet/")
        refused = await client.get(
            "/aurora/intern/", headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/site/"}
        )
        assert refused.status_code == 404
        assert "location" not in refused.headers
        assert refused.content == reference.content
        assert _header_list(refused) == _header_list(reference)

    async def test_an_anonymous_navigation_still_goes_to_the_login(self, client, environment):
        response = await client.get(
            "/aurora/intern/",
            headers={
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Dest": "document",
                "Referer": f"{BASE_URL}/aurora/site/",
            },
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/-/login?returnTo=%2Faurora%2Fintern%2F"

    async def test_an_anonymous_refusal_is_audited_as_a_foreign_subresource(self, client, environment):
        await client.get(
            "/aurora/intern/stijl.css",
            headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/site/"},
        )
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].result == "refused"
        assert rows[0].reason_code == "FOREIGN_SUBRESOURCE"
        assert rows[0].actor_pseudonym is None

    async def test_a_site_that_does_not_exist_keeps_its_own_refusal_reason(self, client, environment):
        # The neutral 404 is already the answer there, so the guard must not
        # take the reason over from the gate.
        await client.get(
            "/aurora/bestaat-niet/", headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/site/"}
        )
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].reason_code == "UNKNOWN_SITE"

    async def test_public_content_is_untouched(self, client, environment):
        response = await client.get(
            "/aurora/site/", headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/intern/"}
        )
        assert response.status_code == 200
        assert response.content == SITE_INDEX

    async def test_a_referer_on_another_host_does_not_count(self, client, environment):
        self._viewer(client, environment)
        response = await client.get(
            "/aurora/intern/",
            headers={**self.SUBRESOURCE, "Referer": "https://kwaad.example/aurora/intern/"},
        )
        assert response.status_code == 404

    async def test_a_malformed_referer_does_not_count_either(self, client, environment):
        # An IPv6 host with no closing bracket: urlsplit itself raises on
        # .hostname, not only on parsing, so the guard has to catch that too
        # and refuse rather than crash.
        self._viewer(client, environment)
        response = await client.get(
            "/aurora/intern/",
            headers={**self.SUBRESOURCE, "Referer": "http://[bad/aurora/intern/"},
        )
        assert response.status_code == 404
        assert response.content == b"Niet gevonden\n"

    async def test_a_referer_that_only_starts_the_same_does_not_count(self, client, environment):
        self._viewer(client, environment)
        response = await client.get(
            "/aurora/intern/", headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/internaat/"}
        )
        assert response.status_code == 404

    async def test_a_subresource_without_a_referer_is_refused(self, client, environment):
        # Our own answers carry a Referrer-Policy that sends the referrer
        # same-origin, so a site's own page always supplies one.
        self._viewer(client, environment)
        response = await client.get("/aurora/intern/stijl.css", headers=self.SUBRESOURCE)
        assert response.status_code == 404

    async def test_a_version_view_is_guarded_although_the_site_is_public(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="lid-actief", sites=("/aurora/site/",))
        path = f"/aurora/site/_version/{environment.world.site_live_id}/"
        response = await client.get(
            path, headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/intern/"}
        )
        assert response.status_code == 404

    async def test_the_boundary_is_the_site_so_a_preview_counts_as_its_own(self, client, environment):
        # Live, preview and _version of one site are one publishing team, so
        # the prefix that decides is `/{group}/{site}/` and not the subroute.
        response = await client.get(
            "/aurora/site/_preview/pr-42/",
            headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/site/"},
        )
        assert response.status_code == 200

    async def test_the_refusal_is_the_same_neutral_404_as_every_other(self, client, environment):
        reference = await client.get("/nergens/niks/")
        self._viewer(client, environment)
        refused = await client.get(
            "/aurora/intern/", headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/site/"}
        )
        assert refused.status_code == 404
        assert refused.content == reference.content
        assert _header_list(refused) == _header_list(reference)

    async def test_the_refusal_is_audited_with_the_fetch_metadata(self, client, environment):
        self._viewer(client, environment)
        await client.get(
            "/aurora/intern/stijl.css",
            headers={**self.SUBRESOURCE, "Referer": f"{BASE_URL}/aurora/site/"},
        )
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].result == "refused"
        assert rows[0].reason_code == "FOREIGN_SUBRESOURCE"
        assert rows[0].refs["fetch_dest"] == "empty"
        assert rows[0].refs["fetch_site"] == "same-origin"
        # The Referer is what decided, and it is a URL of a visitor; only its
        # presence is recorded, never the value.
        assert rows[0].refs["referer_present"] is True
        assert BASE_URL not in json.dumps(rows[0].refs)
        assert "aurora/site" not in json.dumps(rows[0].refs)

    async def test_an_allow_carries_the_fetch_metadata_too(self, client, environment):
        self._viewer(client, environment)
        response = await client.get(
            "/aurora/intern/",
            headers={
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Dest": "document",
                "Referer": f"{BASE_URL}/aurora/site/",
            },
        )
        assert response.status_code == 200
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].result == "allowed"
        assert rows[0].refs["fetch_dest"] == "document"

    async def test_without_the_headers_the_references_say_so(self, client, environment):
        self._viewer(client, environment)
        await client.get("/aurora/intern/")
        rows = await _audit_rows(environment)
        assert rows[0].refs["fetch_dest"] is None
        assert rows[0].refs["fetch_site"] is None
        assert rows[0].refs["referer_present"] is False

    async def test_a_site_that_suppresses_its_own_referrer_is_recognisable(self, client, environment):
        # Same refusal, different cause: with a Referer it is another site
        # reaching for this one, without one it is a site whose own pages
        # suppress the referrer and therefore break their own assets.
        self._viewer(client, environment)
        await client.get(
            "/aurora/intern/stijl.css",
            headers={"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Dest": "style"},
        )
        await client.get(
            "/aurora/intern/stijl.css",
            headers={
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Dest": "style",
                "Referer": f"{BASE_URL}/aurora/site/",
            },
        )
        rows = sorted(await _audit_rows(environment), key=lambda row: row.refs["referer_present"])
        assert [row.reason_code for row in rows] == ["FOREIGN_SUBRESOURCE", "FOREIGN_SUBRESOURCE"]
        assert [row.refs["referer_present"] for row in rows] == [False, True]
        # The Referer decided, but it is a visitor's URL and stays out.
        assert "aurora/site" not in json.dumps(rows[1].refs)

    async def test_an_allow_records_the_referer_presence_too(self, client, environment):
        # Refusals and allows have to be comparable, or the field says nothing
        # to whoever goes looking.
        self._viewer(client, environment)
        response = await client.get(
            "/aurora/intern/",
            headers={
                "Sec-Fetch-Site": "same-origin",
                "Sec-Fetch-Dest": "document",
                "Referer": f"{BASE_URL}/aurora/site/",
            },
        )
        assert response.status_code == 200
        rows = await _audit_rows(environment)
        assert rows[0].result == "allowed"
        assert rows[0].refs["referer_present"] is True

    async def test_an_empty_referer_counts_as_none(self, client, environment):
        self._viewer(client, environment)
        await client.get("/aurora/intern/", headers={**self.SUBRESOURCE, "Referer": ""})
        rows = await _audit_rows(environment)
        assert rows[0].refs["referer_present"] is False


class Test404Html:
    async def test_authorized_visitor_gets_root_404_html(self, client, environment):
        response = await client.get("/aurora/site/bestaat-niet")
        assert response.status_code == 404
        assert response.content == SITE_404
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["etag"] == f'"{environment.world.site_live_id}"'
        assert response.headers["content-security-policy"] == FULL_CSP

    async def test_unauthorized_visitor_gets_never_404_html(self, client):
        # geheim has a 404.html of its own, but anonymously everything stays neutral.
        response = await client.get("/aurora/geheim/bestaat-niet")
        assert response.status_code == 404
        neutral = await client.get("/nergens/niks/")
        assert response.content == neutral.content
        assert _header_list(response) == _header_list(neutral)

    async def test_without_404_html_falls_back_to_neutral(self, client):
        response = await client.get("/aurora/zonder404/bestaat-niet")
        neutral = await client.get("/nergens/niks/")
        assert response.status_code == 404
        assert response.content == neutral.content


class TestLoginRedirect:
    async def test_sso_site_anonymous_redirect_to_content_login(self, client):
        # On the same (content) host, not to /-/login: that path does
        # not exist on the content host (spec §4a).
        response = await client.get("/aurora/intern/")
        assert response.status_code == 302
        assert response.headers["location"] == "/-/login?returnTo=%2Faurora%2Fintern%2F"
        assert response.headers["cache-control"] == "no-store"

    async def test_deep_path_with_query_in_return_to(self, client):
        response = await client.get("/aurora/intern/docs/pagina.html?x=1")
        assert response.status_code == 302
        assert response.headers["location"] == "/-/login?returnTo=%2Faurora%2Fintern%2Fdocs%2Fpagina.html%3Fx%3D1"

    async def test_stray_key_stays_from_return_to(self, client):
        response = await client.get("/aurora/intern/?key=sel.verifier&x=1")
        assert response.status_code == 302
        assert response.headers["location"] == "/-/login?returnTo=%2Faurora%2Fintern%2F%3Fx%3D1"

    async def test_with_content_session_does_content(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="willekeurige-kijker", sites=("/aurora/intern/",))
        response = await client.get("/aurora/intern/")
        assert response.status_code == 200
        assert response.content == b"<h1>intern</h1>"
        assert response.headers["cache-control"] == "private, no-cache, must-revalidate"

    async def test_admin_session_counts_not_as_viewer(self, client, environment):
        # An admin session carries no viewing right on content: the serving
        # layer knows content sessions and nothing else (spec §4a, session
        # kinds).
        set_session_cookie(client, environment.app, sub="willekeurige-kijker")
        response = await client.get("/aurora/intern/")
        assert response.status_code == 302
        assert response.headers["location"].startswith("/-/login?")
        version = await client.get(f"/aurora/site/_version/{environment.world.site_live_id}/")
        assert version.status_code == 404

    async def test_platform_namespace_is_neutral_404_without_audit(self, client, environment):
        # /-/... is the platform namespace on the content host: never a groep,
        # so no DB lookup, no 301 and no audit row.
        for path in ("/-/onbekend", "/-/onbekend/", "/-/x/y/z"):
            response = await client.get(path)
            assert response.status_code == 404, path
            assert "location" not in response.headers, path
        assert await _audit_rows(environment) == []


class TestHeaderSet:
    # Added by FileResponse or by the server, not part of the contract.
    EXCLUDED: ClassVar[set[str]] = {"content-length", "last-modified", "accept-ranges", "date"}

    @pytest.mark.parametrize("path", ["/aurora/site/", "/aurora/site/stijl.css", "/aurora/site/app.mjs"])
    async def test_content_response_carries_exactly_the_fixed_header_set(self, client, path):
        response = await client.get(path)
        assert response.status_code == 200
        headers = {k.lower() for k in response.headers if k.lower() not in self.EXCLUDED}
        assert headers == {
            "content-type",
            "cache-control",
            "etag",
            "x-content-type-options",
            "content-security-policy",
            "referrer-policy",
        }

    async def test_preview_noindex(self, client, environment):
        response = await client.get("/aurora/site/_preview/pr-42/")
        assert response.status_code == 200
        assert response.content == PREVIEW_INDEX
        assert response.headers["x-robots-tag"] == "noindex, nofollow"

    async def test_range_request_gives_206_with_correct_bytes(self, client, environment):
        response = await client.get("/aurora/site/diep/map/bestand.txt", headers={"Range": "bytes=1-2"})
        assert response.status_code == 206
        assert response.content == b"ie"
        assert response.headers["content-range"] == "bytes 1-2/4"
        assert response.headers["etag"] == f'"{environment.world.site_live_id}"'
        assert response.headers["content-security-policy"] == FULL_CSP


class TestAudit:
    async def test_refusal_becomes_audited(self, client, environment):
        await client.get("/aurora/bestaat-niet/")
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        row = rows[0]
        assert row.result == "refused"
        assert row.reason_code == "UNKNOWN_SITE"
        assert row.refs["group"] == "aurora"
        assert row.refs["site"] == "bestaat-niet"

    async def test_public_allow_does_not_become_audited(self, client, environment):
        response = await client.get("/aurora/site/")
        assert response.status_code == 200
        assert await _audit_rows(environment) == []

    async def test_looking_at_protected_content_becomes_audited(self, client, environment):
        """Viewing non-public content is logged, with the short retention
        (docs/audit-log.md). Sleutel, sso and a login-gated preview each count."""
        set_content_session_cookie(
            client, environment.app, sub="willekeurige-kijker", sites=("/aurora/intern/", "/aurora/site/")
        )
        assert (await client.get("/aurora/intern/")).status_code == 200
        assert (await client.get("/aurora/site/_preview/pr-besloten/")).status_code == 200

        rows = await _audit_rows(environment)
        assert [(row.result, row.refs["site"], row.refs["kind"]) for row in rows] == [
            ("allowed", "intern", "live"),
            ("allowed", "site", "preview"),
        ]
        assert all(row.actor_pseudonym is not None for row in rows)

    async def test_a_secret_link_counts_once_not_twice(self, client, environment):
        """Redeeming ?key= answers with a redirect; the view is the request that
        follows it, so that is the one row."""
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_plain}")
        assert response.status_code == 302
        assert (await client.get("/aurora/geheim/")).status_code == 200

        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].result == "allowed"
        # A secret-link visitor is not signed in, so there is nobody to name;
        # the link itself is named by its selector, never by its verifier.
        assert rows[0].actor_pseudonym is None
        selector, verifier = environment.world.key_plain.split(".")
        assert rows[0].refs["selector"] == selector
        assert verifier not in str(rows[0].refs)

    async def test_a_page_counts_its_parts_do_not(self, client, environment):
        """A page with fifty parts is one look, not fifty."""
        await client.get(f"/aurora/geheim/?key={environment.world.key_plain}")
        assert (await client.get("/aurora/geheim/stijl.css")).status_code == 200
        assert (await client.get("/aurora/geheim/map/")).status_code == 200

        rows = await _audit_rows(environment)
        assert [row.refs["path"] for row in rows] == ["map/"]

    async def test_a_revisit_from_cache_still_counts(self, client, environment):
        await client.get(f"/aurora/geheim/?key={environment.world.key_plain}")
        etag = (await client.get("/aurora/geheim/")).headers["etag"]
        revisit = await client.get("/aurora/geheim/", headers={"If-None-Match": etag})
        assert revisit.status_code == 304

        assert len(await _audit_rows(environment)) == 2

    async def test_refs_contain_never_the_query_string(self, client, environment):
        # A refused key is audited, but without the key itself: the references
        # carry only the path, never the query.
        response = await client.get("/aurora/geheim/pagina.html?key=verkeerd.sleutelwaarde&x=1")
        assert response.status_code == 404
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].result == "refused"
        assert rows[0].reason_code == "KEY_INVALID"
        assert rows[0].refs["path"] == "pagina.html"
        row = json.dumps(rows[0].refs)
        assert "key=" not in row
        assert "sleutelwaarde" not in row

    async def test_version_access_becomes_audited(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="lid-actief", sites=("/aurora/site/",))
        response = await client.get(f"/aurora/site/_version/{environment.world.site_live_id}/")
        assert response.status_code == 200
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].result == "allowed"
        assert rows[0].refs["kind"] == "version"
        assert rows[0].actor_pseudonym is not None

    async def test_login_redirect_becomes_audited(self, client, environment):
        await client.get("/aurora/intern/")
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].result == "login_redirect"

    async def test_path_validation_refusal_becomes_audited(self, environment):
        await _raw_request(environment.app, "/aurora/site/..%2fx")
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].result == "refused"
        assert rows[0].reason_code == "PATH_INVALID"
