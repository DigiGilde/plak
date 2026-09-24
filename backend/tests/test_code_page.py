"""Tests for the secret link shared without its code (serving/code_page.py):
when the code page appears at all, what stays the neutral 404, the POST that
hands in the code, its origin checks, the limit per selector and the audit
trail. Against the real PostgreSQL test container, like the serving tests.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import set_content_session_cookie
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plak.access import keys
from plak.audit.log import AuditLog
from plak.auth.sessions import KEY_COOKIE, SessionStore, check_signature
from plak.config import Settings
from plak.constants import PATH_CONTENT_CODE, AccessBase
from plak.ingest.store import ContentStore
from plak.models.audit import AuditLogEntry
from plak.models.identity import Group, Member, MemberStatus
from plak.models.publication import AccessKey, KeyStatus, Preview, Site, Version, VersionTarget
from plak.ratelimit import InMemoryCounter
from plak.serving.code_page import router as code_router
from plak.serving.response import NEUTRAL_404_BODY
from plak.serving.router import router as serving_router

BASE_URL = "https://plak.example"
ADMIN_URL = "https://beheer.plak.example"

SECRET_INDEX = b"<h1>geheim</h1>"

FORM_HEADERS = {
    "content-type": "application/x-www-form-urlencoded",
    "origin": BASE_URL,
    "sec-fetch-site": "same-origin",
}


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
        base_url=ADMIN_URL,
        content_base_url=BASE_URL,
        environment="dev",
    )


@dataclass
class World:
    key_plain: str
    key_selector: str
    key_verifier: str
    key_id: uuid.UUID
    revoked_selector: str
    expired_selector: str
    other_site_selector: str
    public_site_selector: str
    without_live_selector: str
    preview_ref: str


@dataclass
class Environment:
    factory: async_sessionmaker[AsyncSession]
    app: FastAPI
    world: World


def _make_app(settings: Settings, factory, store: ContentStore) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    app.state.session_store = SessionStore()
    app.state.session_factory = factory
    app.state.content_store = store
    app.state.audit_log = AuditLog(factory, settings.audit_pepper, settings.audit_ip_key_bytes)
    app.state.code_attempts = InMemoryCounter()
    app.include_router(code_router)
    app.include_router(serving_router)
    return app


async def _seed(factory, store: ContentStore) -> World:
    async with factory() as db:
        group = Group(slug="aurora", name="Aurora", default_access_base=AccessBase.PUBLIC)
        db.add(group)
        await db.flush()
        member = Member(sso_subject="lid", email="lid@example.org", status=MemberStatus.ACTIVE)
        db.add(member)
        await db.flush()

        secret = Site(group_id=group.id, slug="geheim", title="Geheim", access_base=AccessBase.NOBODY, access_keys=True)
        public = Site(group_id=group.id, slug="open", title="Open", access_base=AccessBase.PUBLIC)
        other = Site(group_id=group.id, slug="ander", title="Ander", access_base=AccessBase.NOBODY, access_keys=True)
        without_live = Site(
            group_id=group.id, slug="leeg", title="Leeg", access_base=AccessBase.NOBODY, access_keys=True
        )
        db.add_all([secret, public, other, without_live])
        await db.flush()

        def new_version(site: Site, body: bytes, target=VersionTarget.LIVE) -> Version:
            version_id = uuid.uuid4()
            storage_ref = store.store_version(group.slug, site.slug, version_id, {"index.html": body})
            return Version(
                id=version_id, site_id=site.id, target=target, storage_ref=storage_ref, member_id=member.id
            )

        secret_live = new_version(secret, SECRET_INDEX)
        public_live = new_version(public, b"<h1>open</h1>")
        other_live = new_version(other, b"<h1>ander</h1>")
        preview_version = new_version(secret, b"<h1>preview</h1>", target=VersionTarget.PREVIEW)
        db.add_all([secret_live, public_live, other_live, preview_version])
        await db.flush()
        secret.live_version_id = secret_live.id
        public.live_version_id = public_live.id
        other.live_version_id = other_live.id
        db.add(Preview(site_id=secret.id, ref="pr-1", version_id=preview_version.id))

        key, plain = await keys.create_key(db, secret.id, "testsleutel")
        revoked, _ = await keys.create_key(db, secret.id, "ingetrokken")
        revoked.status = KeyStatus.REVOKED
        expired, _ = await keys.create_key(db, secret.id, "expired")
        expired.expires_at = datetime.now(UTC) - timedelta(days=1)
        other_key, _ = await keys.create_key(db, other.id, "andere site")
        public_key, _ = await keys.create_key(db, public.id, "publieke site")
        empty_key, _ = await keys.create_key(db, without_live.id, "zonder live")
        await db.flush()

        selector, _, verifier = plain.partition(".")
        world = World(
            key_plain=plain,
            key_selector=selector,
            key_verifier=verifier,
            key_id=key.id,
            revoked_selector=revoked.selector,
            expired_selector=expired.selector,
            other_site_selector=other_key.selector,
            public_site_selector=public_key.selector,
            without_live_selector=empty_key.selector,
            preview_ref="pr-1",
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
                yield Environment(factory=factory, app=app, world=world)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(environment: Environment) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=environment.app), base_url=BASE_URL, follow_redirects=False
    ) as c:
        yield c


async def _audit_rows(environment: Environment) -> list[AuditLogEntry]:
    async with environment.factory() as db:
        result = await db.execute(select(AuditLogEntry).where(AuditLogEntry.action == "content_access"))
        return list(result.scalars().all())


def _is_neutral_404(response: httpx.Response) -> bool:
    return response.status_code == 404 and response.content == NEUTRAL_404_BODY


class TestTheCodePageAppears:
    async def test_selector_alone_asks_for_the_code(self, client, environment):
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_selector}")
        assert response.status_code == 200
        body = response.text
        assert f'action="{PATH_CONTENT_CODE}"' in body
        assert 'name="code"' in body
        assert f'value="{environment.world.key_selector}"' in body
        assert 'value="/aurora/geheim/"' in body

    async def test_the_page_names_neither_site_nor_group(self, client, environment):
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_selector}")
        assert "Geheim" not in response.text
        assert "Aurora" not in response.text
        # Not the code either, and not the full link.
        assert environment.world.key_verifier not in response.text

    async def test_headers_are_those_of_protected_content(self, client, environment):
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_selector}")
        assert response.headers["cache-control"] == "no-store"
        # Not no-referrer: that makes Chrome post the form with `Origin: null`.
        assert response.headers["referrer-policy"] == "same-origin"
        assert response.headers["x-robots-tag"] == "noindex, nofollow"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert response.headers["vary"] == "Accept-Language"

    async def test_english_accept_language_gets_the_english_page(self, client, environment):
        response = await client.get(
            f"/aurora/geheim/?key={environment.world.key_selector}",
            headers={"accept-language": "en-GB,en;q=0.9"},
        )
        assert 'lang="en"' in response.text
        assert "Enter the code" in response.text

    async def test_the_page_is_audited_as_a_refusal_with_the_selector(self, client, environment):
        await client.get(f"/aurora/geheim/?key={environment.world.key_selector}")
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].result == "refused"
        assert rows[0].reason_code == "KEY_CODE_REQUIRED"
        assert rows[0].refs["selector"] == environment.world.key_selector

    async def test_a_deeper_page_keeps_its_own_path_in_the_form(self, client, environment):
        response = await client.get(f"/aurora/geheim/map/?key={environment.world.key_selector}&x=1")
        assert response.status_code == 200
        assert 'value="/aurora/geheim/map/?x=1"' in response.text


class TestEverythingElseStaysTheNeutral404:
    @pytest.mark.parametrize(
        "selector_name",
        ["revoked_selector", "expired_selector", "other_site_selector", "without_live_selector"],
    )
    async def test_unusable_selectors(self, client, environment, selector_name):
        selector = getattr(environment.world, selector_name)
        response = await client.get(f"/aurora/geheim/?key={selector}")
        assert _is_neutral_404(response)

    async def test_unknown_selector(self, client):
        response = await client.get("/aurora/geheim/?key=AaBbCcDd")
        assert _is_neutral_404(response)

    async def test_a_selector_of_the_wrong_length_is_no_selector(self, client, environment):
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_selector[:4]}")
        assert _is_neutral_404(response)

    async def test_a_public_site_never_asks_for_a_code(self, client, environment):
        response = await client.get(f"/aurora/open/?key={environment.world.public_site_selector}")
        # Public content is simply served; the key plays no part there.
        assert response.status_code == 200
        assert b"open" in response.content

    async def test_unknown_site(self, client, environment):
        response = await client.get(f"/aurora/bestaat-niet/?key={environment.world.key_selector}")
        assert _is_neutral_404(response)

    async def test_unknown_group(self, client, environment):
        response = await client.get(f"/nergens/geheim/?key={environment.world.key_selector}")
        assert _is_neutral_404(response)

    async def test_a_preview_gets_no_code_page(self, client, environment):
        response = await client.get(
            f"/aurora/geheim/_preview/{environment.world.preview_ref}/?key={environment.world.key_selector}"
        )
        assert _is_neutral_404(response)

    async def test_get_on_the_code_path_itself(self, client):
        response = await client.get(PATH_CONTENT_CODE)
        assert _is_neutral_404(response)


async def _set_access(
    environment: Environment,
    base: AccessBase,
    *,
    access_keys: bool = True,
    invitees: bool = False,
) -> None:
    async with environment.factory() as db:
        site = await db.scalar(select(Site).where(Site.slug == "geheim"))
        site.access_base = base
        site.access_keys = access_keys
        site.access_invitees = invitees
        await db.commit()


def _is_code_page(response: httpx.Response) -> bool:
    return response.status_code == 200 and f'action="{PATH_CONTENT_CODE}"' in response.text


def _is_login_redirect(response: httpx.Response) -> bool:
    return response.status_code == 302 and response.headers["location"].startswith("/-/login")


class TestTheCodePageBeatsTheLoginRedirect:
    """A selector alone asks for the code before anything sends the visitor to
    an IdP; every combination of base and extras, anonymous and logged in."""

    @pytest.mark.parametrize("base", [AccessBase.NOBODY, AccessBase.SSO, AccessBase.SITE_TEAM])
    @pytest.mark.parametrize("invitees", [False, True])
    async def test_anonymous_with_a_valid_selector_gets_the_code_page(
        self, client, environment, base, invitees
    ):
        await _set_access(environment, base, invitees=invitees)
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_selector}")
        assert _is_code_page(response)

    @pytest.mark.parametrize(
        ("base", "invitees", "login_helps"),
        [
            (AccessBase.NOBODY, False, False),
            (AccessBase.NOBODY, True, True),
            (AccessBase.SSO, False, True),
            (AccessBase.SITE_TEAM, False, True),
        ],
    )
    async def test_an_unknown_selector_keeps_the_old_order(
        self, client, environment, base, invitees, login_helps
    ):
        await _set_access(environment, base, invitees=invitees)
        response = await client.get("/aurora/geheim/?key=AaBbCcDd")
        if login_helps:
            assert _is_login_redirect(response)
        else:
            assert _is_neutral_404(response)

    @pytest.mark.parametrize("invitees", [False, True])
    async def test_a_logged_in_outsider_with_a_valid_selector_gets_the_code_page(
        self, client, environment, invitees
    ):
        await _set_access(environment, AccessBase.NOBODY, invitees=invitees)
        set_content_session_cookie(client, environment.app, sub="buitenstaander")
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_selector}")
        assert _is_code_page(response)

    async def test_somebody_the_base_already_lets_in_simply_sees_the_site(self, client, environment):
        """A session on base sso: the allow comes first and the selector never
        reaches the code page."""
        await _set_access(environment, AccessBase.SSO)
        set_content_session_cookie(client, environment.app, sub="willekeurige-kijker")
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_selector}")
        assert response.status_code == 200
        assert response.content == SECRET_INDEX

    @pytest.mark.parametrize(
        ("base", "invitees", "login_helps"),
        [
            (AccessBase.NOBODY, False, False),
            (AccessBase.NOBODY, True, True),
            (AccessBase.SSO, False, True),
        ],
    )
    async def test_without_the_secret_link_extra_no_selector_helps(
        self, client, environment, base, invitees, login_helps
    ):
        await _set_access(environment, base, access_keys=False, invitees=invitees)
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_selector}")
        if login_helps:
            assert _is_login_redirect(response)
        else:
            assert _is_neutral_404(response)

    @pytest.mark.parametrize("invitees", [False, True])
    async def test_the_full_link_still_redeems_straight_away(self, client, environment, invitees):
        await _set_access(environment, AccessBase.NOBODY, invitees=invitees)
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_plain}")
        assert response.status_code == 302
        assert response.headers["location"] == "/aurora/geheim/"
        assert KEY_COOKIE in response.cookies

    @pytest.mark.parametrize("invitees", [False, True])
    async def test_a_preview_still_never_shows_the_code_page(self, client, environment, invitees):
        await _set_access(environment, AccessBase.NOBODY, invitees=invitees)
        response = await client.get(
            f"/aurora/geheim/_preview/{environment.world.preview_ref}/?key={environment.world.key_selector}"
        )
        assert _is_neutral_404(response)


class TestTheFullLinkKeepsWorking:
    async def test_full_key_is_still_redeemed(self, client, environment):
        response = await client.get(f"/aurora/geheim/?key={environment.world.key_plain}")
        assert response.status_code == 302
        assert response.headers["location"] == "/aurora/geheim/"
        assert KEY_COOKIE in response.cookies


class TestHandingInTheCode:
    @staticmethod
    def _body(environment) -> dict[str, str]:
        return {
            "selector": environment.world.key_selector,
            "code": environment.world.key_verifier,
            "path": "/aurora/geheim/",
        }

    async def _post(self, client, environment, **overrides) -> httpx.Response:
        data = {
            "selector": environment.world.key_selector,
            "code": environment.world.key_verifier,
            "path": "/aurora/geheim/",
        }
        data.update(overrides)
        return await client.post(PATH_CONTENT_CODE, data=data, headers=FORM_HEADERS)

    async def test_the_right_code_sets_the_same_cookie_as_a_full_link(self, client, environment):
        response = await self._post(client, environment)
        assert response.status_code == 303
        assert response.headers["location"] == "/aurora/geheim/"
        assert response.headers["cache-control"] == "no-store"
        cookie = response.cookies[KEY_COOKIE]
        value = check_signature(environment.app.state.settings.session_secret, cookie)
        assert value == f"key:{environment.world.key_id}"
        assert "Path=/aurora/geheim/" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]

    async def test_after_the_code_the_page_itself_follows(self, client, environment):
        await self._post(client, environment)
        # The client keeps the cookie the redirect set, exactly as a browser does.
        page = await client.get("/aurora/geheim/")
        assert page.status_code == 200
        assert page.content == SECRET_INDEX

    async def test_a_successful_code_writes_no_refusal(self, client, environment):
        await self._post(client, environment)
        assert await _audit_rows(environment) == []

    async def test_a_wrong_code_returns_the_page_with_an_error(self, client, environment):
        response = await self._post(client, environment, code="fout")
        assert response.status_code == 200
        assert "De code klopt niet" in response.text
        assert KEY_COOKIE not in response.cookies

    async def test_a_wrong_code_is_audited_without_the_code(self, client, environment):
        await self._post(client, environment, code="fout")
        rows = await _audit_rows(environment)
        assert len(rows) == 1
        assert rows[0].reason_code == "KEY_CODE_INVALID"
        assert rows[0].refs["selector"] == environment.world.key_selector
        assert "fout" not in str(rows[0].refs)

    async def test_an_unknown_selector_answers_exactly_like_a_wrong_code(self, client, environment):
        wrong_code = await self._post(client, environment, code="fout")
        unknown = await self._post(client, environment, selector="AaBbCcDd", code="fout")
        assert unknown.status_code == wrong_code.status_code
        assert "De code klopt niet" in unknown.text
        assert KEY_COOKIE not in unknown.cookies

    @pytest.mark.parametrize("selector_name", ["revoked_selector", "expired_selector"])
    async def test_an_unusable_key_never_opens(self, client, environment, selector_name):
        response = await self._post(
            client, environment, selector=getattr(environment.world, selector_name), code="fout"
        )
        assert KEY_COOKIE not in response.cookies

    async def test_a_key_of_a_site_that_is_not_on_the_secret_link_never_opens(self, client, environment):
        response = await self._post(
            client, environment, selector=environment.world.public_site_selector, path="/aurora/open/"
        )
        assert response.status_code == 200
        assert KEY_COOKIE not in response.cookies

    @pytest.mark.parametrize(
        "path",
        [
            "https://kwaad.example/pad",
            "//kwaad.example/pad",
            "/aurora\\geheim/",
            "/aurora",
            "/-/code",
            "",
        ],
    )
    async def test_a_path_that_is_not_ours_is_the_neutral_404(self, client, environment, path):
        response = await self._post(client, environment, path=path)
        assert _is_neutral_404(response)

    async def test_another_origin_is_the_neutral_404(self, client, environment):
        response = await client.post(
            PATH_CONTENT_CODE,
            data=self._body(environment),
            headers={**FORM_HEADERS, "origin": "https://kwaad.example"},
        )
        assert _is_neutral_404(response)

    async def test_a_null_origin_from_this_page_still_redeems(self, client, environment):
        # Chrome anonymises the Origin of a form navigation from a no-referrer
        # document; Sec-Fetch-Site is what says where it really came from.
        response = await client.post(
            PATH_CONTENT_CODE,
            data=self._body(environment),
            headers={**FORM_HEADERS, "origin": "null", "sec-fetch-site": "same-origin"},
        )
        assert response.status_code == 303
        assert KEY_COOKIE in response.cookies

    async def test_a_null_origin_from_another_site_is_the_neutral_404(self, client, environment):
        response = await client.post(
            PATH_CONTENT_CODE,
            data=self._body(environment),
            headers={**FORM_HEADERS, "origin": "null", "sec-fetch-site": "cross-site"},
        )
        assert _is_neutral_404(response)

    async def test_the_code_page_and_the_content_keep_the_same_referrer_policy(self, client, environment):
        page = await client.get(f"/aurora/geheim/?key={environment.world.key_selector}")
        assert page.headers["referrer-policy"] == "same-origin"
        await self._post(client, environment)
        content = await client.get("/aurora/geheim/")
        assert content.status_code == 200
        assert content.headers["referrer-policy"] == "same-origin"

    async def test_a_cross_site_fetch_without_origin_is_the_neutral_404(self, client, environment):
        headers = {"content-type": "application/x-www-form-urlencoded", "sec-fetch-site": "cross-site"}
        response = await client.post(
            PATH_CONTENT_CODE,
            data=self._body(environment),
            headers=headers,
        )
        assert _is_neutral_404(response)

    async def test_without_any_of_the_two_headers_it_still_works(self, client, environment):
        response = await client.post(
            PATH_CONTENT_CODE,
            data=self._body(environment),
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        assert response.status_code == 303

    async def test_another_content_type_is_the_neutral_404(self, client, environment):
        response = await client.post(
            PATH_CONTENT_CODE,
            content=b"{}",
            headers={"content-type": "application/json", "origin": BASE_URL},
        )
        assert _is_neutral_404(response)

    async def test_an_oversized_body_is_the_neutral_404(self, client, environment):
        response = await client.post(
            PATH_CONTENT_CODE,
            data={"selector": environment.world.key_selector, "code": "x" * 5000, "path": "/aurora/geheim/"},
            headers=FORM_HEADERS,
        )
        assert _is_neutral_404(response)

    async def test_the_admin_host_has_no_code_endpoint(self, environment):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=environment.app), base_url=ADMIN_URL, follow_redirects=False
        ) as admin:
            response = await admin.post(
                PATH_CONTENT_CODE,
                data=self._body(environment),
                headers={**FORM_HEADERS, "origin": ADMIN_URL},
            )
        assert _is_neutral_404(response)


class TestTheLimitPerSelector:
    async def _post(self, client, environment, code: str) -> httpx.Response:
        return await client.post(
            PATH_CONTENT_CODE,
            data={"selector": environment.world.key_selector, "code": code, "path": "/aurora/geheim/"},
            headers=FORM_HEADERS,
        )

    async def test_the_eleventh_attempt_is_turned_away(self, client, environment):
        for _ in range(10):
            response = await self._post(client, environment, "fout")
            assert "Probeer het later opnieuw" not in response.text
        response = await self._post(client, environment, "fout")
        assert response.status_code == 200
        assert "De code klopt niet" in response.text
        assert "Probeer het later opnieuw" in response.text

    async def test_the_right_code_no_longer_opens_once_the_limit_is_reached(self, client, environment):
        for _ in range(10):
            await self._post(client, environment, "fout")
        response = await self._post(client, environment, environment.world.key_verifier)
        assert response.status_code == 200
        assert KEY_COOKIE not in response.cookies

    async def test_being_locked_out_is_audited_apart(self, client, environment):
        for _ in range(11):
            await self._post(client, environment, "fout")
        rows = await _audit_rows(environment)
        assert [row.reason_code for row in rows].count("KEY_CODE_THROTTLED") == 1

    async def test_an_unknown_selector_counts_too(self, client, environment):
        for _ in range(11):
            await client.post(
                PATH_CONTENT_CODE,
                data={"selector": "AaBbCcDd", "code": "fout", "path": "/aurora/geheim/"},
                headers=FORM_HEADERS,
            )
        response = await client.post(
            PATH_CONTENT_CODE,
            data={"selector": "AaBbCcDd", "code": "fout", "path": "/aurora/geheim/"},
            headers=FORM_HEADERS,
        )
        # A selector that never existed locks out exactly like one that does,
        # so the lockout says nothing about existence.
        assert "Probeer het later opnieuw" in response.text

    async def test_another_selector_keeps_its_own_budget(self, client, environment):
        for _ in range(11):
            await self._post(client, environment, "fout")
        response = await client.post(
            PATH_CONTENT_CODE,
            data={
                "selector": environment.world.other_site_selector,
                "code": "fout",
                "path": "/aurora/ander/",
            },
            headers=FORM_HEADERS,
        )
        assert "Probeer het later opnieuw" not in response.text


class TestKeysHelpers:
    async def test_selector_usable_holds_the_selector_to_its_own_site(self, environment):
        async with environment.factory() as db:
            access_key = await db.scalar(
                select(AccessKey).where(AccessKey.selector == environment.world.key_selector)
            )
            assert await keys.selector_usable(db, access_key.site_id, access_key.selector)
            assert not await keys.selector_usable(db, uuid.uuid4(), access_key.selector)

    @pytest.mark.parametrize("value", [None, "", "te-kort", "AaBbCcDd.verifier", "Aa Bb Cc!"])
    def test_bare_selector_refuses_everything_that_is_no_selector(self, value):
        assert keys.bare_selector(value) is None

    def test_bare_selector_accepts_a_selector(self, environment):
        assert keys.bare_selector(environment.world.key_selector) == environment.world.key_selector

    async def test_verify_parts_without_a_site_still_refuses(self, environment):
        async with environment.factory() as db:
            assert (
                await keys.verify_parts(
                    db, None, environment.world.key_selector, environment.world.key_verifier
                )
                is None
            )

    async def test_verify_parts_refuses_an_empty_code(self, environment):
        async with environment.factory() as db:
            assert await keys.verify_parts(db, uuid.uuid4(), environment.world.key_selector, "") is None
