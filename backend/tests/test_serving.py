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
from urllib.parse import unquote

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import set_content_session_cookie, set_session_cookie
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plak.access import keys
from plak.audit.log import AuditLog
from plak.auth.sessions import KEY_COOKIE, SessionStore, check_signature, sign, sign_key_cookie
from plak.config import Settings
from plak.constants import AccessBase, Role
from plak.ingest.store import ContentStore
from plak.models.audit import AuditLogEntry
from plak.models.identity import Group, GroupMember, Member, MemberStatus
from plak.models.publication import Preview, Site, Version, VersionTarget
from plak.serving.router import router as serving_router

BASE_URL = "https://plak.example"

FULL_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
    "font-src 'self' data:; connect-src 'self'; media-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
    "object-src 'none'"
)

EXTERNAL_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net "
    "https://unpkg.com https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net "
    "https://unpkg.com https://fonts.googleapis.com; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data: https://fonts.gstatic.com; connect-src 'self'; media-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; "
    "object-src 'none'"
)

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

        # External sources are on by default, so the sites that must show
        # the strict CSP turn it off here; `extern` leaves the default as
        # is.
        site = Site(
            group_id=group.id,
            slug="site",
            title="Site",
            access_base=AccessBase.PUBLIC,
            external_sources=False,
        )
        secret = Site(
            group_id=group.id,
            slug="geheim",
            title="Geheim",
            access_base=AccessBase.NOBODY,
            access_keys=True,
            external_sources=False,
        )
        internal = Site(
            group_id=group.id,
            slug="intern",
            title="Intern",
            access_base=AccessBase.SSO,
            external_sources=False,
        )
        empty = Site(
            group_id=group.id,
            slug="leeg",
            title="Leeg",
            access_base=AccessBase.PUBLIC,
            external_sources=False,
        )
        without_404 = Site(
            group_id=group.id,
            slug="zonder404",
            title="Zonder",
            access_base=AccessBase.PUBLIC,
            external_sources=False,
        )
        external = Site(group_id=group.id, slug="extern", title="Extern", access_base=AccessBase.PUBLIC)
        db.add_all([site, secret, internal, empty, without_404, external])
        await db.flush()

        def new_version(site: Site, files: dict[str, bytes], target=VersionTarget.LIVE) -> Version:
            version_id = uuid.uuid4()
            storage_ref = store.store_version(group.slug, site.slug, version_id, files)
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
        internal_live = new_version(internal, {"index.html": b"<h1>intern</h1>"})
        without_404_live = new_version(without_404, {"index.html": b"<h1>kaal</h1>"})
        external_live = new_version(external, {"index.html": b"<h1>extern</h1>", "stijl.css": b"body{}"})
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
            ]
        )
        await db.flush()

        site.live_version_id = site_live.id
        secret.live_version_id = secret_live.id
        internal.live_version_id = internal_live.id
        without_404.live_version_id = without_404_live.id
        external.live_version_id = external_live.id

        db.add(Preview(site_id=site.id, ref="pr-42", version_id=preview_version.id))
        db.add(Preview(site_id=external.id, ref="pr-extern", version_id=external_preview.id))
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


async def _raw_request(app: FastAPI, raw_path: str) -> tuple[int, list[tuple[str, str]], bytes]:
    """Sends a request with an exactly given raw path, the way a real ASGI
    server delivers it (path URL-decoded once). Needed because httpx can
    normalise percent-encoded dot segments away by itself."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
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
        assert response.headers["content-type"] == "text/css"

    async def test_mjs_gets_text_javascript(self, client):
        response = await client.get("/aurora/site/app.mjs")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/javascript"

    async def test_directory_301_after_allow(self, client):
        response = await client.get("/aurora/site/docs")
        assert response.status_code == 301
        assert response.headers["location"] == "/aurora/site/docs/"
        followed = await client.get("/aurora/site/docs/")
        assert followed.status_code == 200
        assert followed.content == b"<h1>docs</h1>"

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
            raise AssertionError("store aangeraakt bij ongeldig pad")

        monkeypatch.setattr(environment.store, "file_path", boom)
        status, _, _ = await _raw_request(environment.app, "/aurora/site/..%2fx")
        assert status == 404


class TestEtag304:
    async def test_if_none_match_gives_304_without_store(self, client, environment, monkeypatch):
        etag = f'"{environment.world.site_live_id}"'

        def boom(*args, **kwargs):
            raise AssertionError("store aangeraakt bij 304")

        monkeypatch.setattr(environment.store, "file_path", boom)
        response = await client.get("/aurora/site/", headers={"If-None-Match": etag})
        assert response.status_code == 304
        assert response.content == b""
        assert response.headers["etag"] == etag
        assert response.headers["cache-control"] == "no-cache, must-revalidate"

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
        assert "SameSite=lax" in set_cookie

        # The client took the cookie over: the follow-up request on the clean
        # URL yields the content with the private header set.
        followed = await client.get(response.headers["location"])
        assert followed.status_code == 200
        assert followed.content == SECRET_INDEX
        assert followed.headers["cache-control"] == "private, no-cache, must-revalidate"
        assert followed.headers["referrer-policy"] == "no-referrer"
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
        set_content_session_cookie(client, environment.app, sub="willekeurige-kijker")
        response = await client.get("/aurora/site/_preview/pr-besloten/")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-cache, must-revalidate"
        assert response.headers["x-robots-tag"] == "noindex, nofollow"

    async def test_unknown_preview_neutral_404(self, client):
        response = await client.get("/aurora/site/_preview/pr-999/")
        assert response.status_code == 404


class TestVersionView:
    async def test_active_group_member_sees_version(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="lid-actief")
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
        set_content_session_cookie(client, environment.app, sub="buitenstaander")
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
        set_content_session_cookie(client, environment.app, sub="lid-actief")
        response = await client.get(f"/aurora/extern/_version/{environment.world.external_live_id}/")
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == EXTERNAL_CSP

    async def test_neutral_404_keeps_the_strict_policy(self, client):
        """The 404 stays byte-identical whatever a site allows; it may not
        become a way to tell which site a path belonged to."""
        response = await client.get("/aurora/extern/bestaat-niet/")
        assert response.status_code == 404
        assert response.headers["content-security-policy"] == FULL_CSP


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

    async def test_deep_path_with_query_in_return_to(self, client):
        response = await client.get("/aurora/intern/docs/pagina.html?x=1")
        assert response.status_code == 302
        assert response.headers["location"] == "/-/login?returnTo=%2Faurora%2Fintern%2Fdocs%2Fpagina.html%3Fx%3D1"

    async def test_stray_key_stays_from_return_to(self, client):
        response = await client.get("/aurora/intern/?key=sel.verifier&x=1")
        assert response.status_code == 302
        assert response.headers["location"] == "/-/login?returnTo=%2Faurora%2Fintern%2F%3Fx%3D1"

    async def test_with_content_session_does_content(self, client, environment):
        set_content_session_cookie(client, environment.app, sub="willekeurige-kijker")
        response = await client.get("/aurora/intern/")
        assert response.status_code == 200
        assert response.content == b"<h1>intern</h1>"
        assert response.headers["cache-control"] == "private, no-cache, must-revalidate"

    async def test_admin_session_counts_not_as_viewer(self, client, environment):
        # A admin session carries no viewing right on content: the serving
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
        set_content_session_cookie(client, environment.app, sub="willekeurige-kijker")
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
        set_content_session_cookie(client, environment.app, sub="lid-actief")
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
