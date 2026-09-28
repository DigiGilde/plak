"""Host separation in the app (host_separation.py; spec §4/§4a): content paths
exist only on the content host, the API only on the admin host, the login and
its callback on both (they share one spelling and the host picks the flow), and
on the admin host the SPA answers everything the app does not claim itself.
No database needed: every path used here is answered before the DB (lexical
301, SPA, front page, robots, healthz, login redirect, neutral 404).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import MockIdP, make_client_jwk, make_oidc_client
from starlette.types import Receive, Scope, Send

from plak.config import Settings
from plak.host_separation import HostSeparationMiddleware, belongs_to_admin, belongs_to_content
from plak.main import create_app
from plak.serving.response import NEUTRAL_404_BODY

ADMIN = "https://beheer.plak.example"
CONTENT = "https://plak.example"

INDEX_HTML = b"<!doctype html><div id=app></div>"


class TestClassification:
    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/-/api/v1/me",
            "/-/login",
            "/-/oauth2/callback",
            "/-/logout",
            "/robots.txt",
            "/favicon.ico",
            "/.well-known/x",
            # Since the SPA moved to the root these are its routes, not content:
            # on this host they exist, and platform/spa.py decides what answers.
            "/aurora",
            "/aurora/site/",
            "/admin",
            "/beheer/aurora",
        ],
    )
    def test_admin_paths(self, path: str) -> None:
        assert belongs_to_admin(path)

    @pytest.mark.parametrize(
        "path",
        [
            "/aurora/site/",
            "/aurora/site",
            "/aurora",
            "/robots.txt",
            "/favicon.ico",
            "/.well-known/x",
            "/-/login",
            "/-/oauth2/callback",
            # The code of a secret link shared without it (serving/code_page.py).
            "/-/code",
            # The public front page (platform/pages.py) lives here.
            "/",
            # `admin` and `beheer` are not reserved slugs, so on this
            # host they are ordinary content paths.
            "/admin/site/",
            "/beheer/site/",
        ],
    )
    def test_content_paths(self, path: str) -> None:
        assert belongs_to_content(path)

    @pytest.mark.parametrize("path", ["/-/api/v1/x", "/-/onbekend"])
    def test_no_content_paths(self, path: str) -> None:
        assert not belongs_to_content(path)

    def test_healthz_exists_on_neither_public_host(self) -> None:
        # Spec §4a/§11: internal only, the probe hits the pod directly.
        assert not belongs_to_admin("/healthz")
        assert not belongs_to_content("/healthz")

    async def test_non_http_scope_is_passed_through_untouched(self) -> None:
        """A websocket (or lifespan) scope carries no host-worthy path
        classification; the middleware must step aside rather than read the
        host or answer the neutral 404 for it."""
        calls: list[Scope] = []

        async def inner(scope: Scope, receive: Receive, send: Send) -> None:
            calls.append(scope)

        middleware = HostSeparationMiddleware(inner, content_host="plak.example")
        scope: Scope = {"type": "websocket", "path": "/-/onbekend"}

        async def receive() -> None:
            raise AssertionError("receive should not be called")

        async def send(message) -> None:
            raise AssertionError("send should not be called")

        await middleware(scope, receive, send)
        assert calls == [scope]


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_bytes(INDEX_HTML)
    base: dict[str, object] = {
        "db_url": "postgresql+asyncpg://ongebruikt:ongebruikt@localhost:5432/ongebruikt",
        "content_root": tmp_path / "content",
        "oidc_issuer": "https://idp.example",
        "oidc_client_id": "plak-client",
        "oidc_client_private_jwk": make_client_jwk(),
        "oidc_required_acr": "urn:acr:hoog",
        "session_secret": "sessie-geheim-van-minstens-32-bytes!",
        "audit_pepper": "audit-pepper-van-minstens-32-bytes!!",
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "environment": "dev",
        "base_url": ADMIN,
        "content_base_url": CONTENT,
        "spa_path": dist,
    }
    base.update(overrides)
    return Settings(**base)


def _client(app: FastAPI, base: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base, follow_redirects=False)


@pytest_asyncio.fixture
async def two_hosts(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, httpx.AsyncClient]]:
    app = create_app(_settings(tmp_path))
    async with app.router.lifespan_context(app), _client(app, ADMIN) as admin, _client(app, CONTENT) as content:
        yield admin, content


def _header_set(response: httpx.Response) -> list[tuple[str, str]]:
    return sorted(response.headers.multi_items())


async def _router_404(client: httpx.AsyncClient) -> httpx.Response:
    """Neutral 404 from the serving router itself, on the same host: a path
    under a reserved slug, so ahead of the DB. The general headers have to be
    on it, otherwise the equality in _is_neutral_404 compares nothing."""
    response = await client.get("/favicon.ico/x/")
    assert response.status_code == 404 and response.content == NEUTRAL_404_BODY
    assert "permissions-policy" in response.headers
    assert "strict-transport-security" in response.headers
    return response


async def _is_neutral_404(client: httpx.AsyncClient, path: str) -> bool:
    """A 404 that is neutral and carries exactly the header set of the router
    404 on the same host. The header set follows from the host, never from the
    path (security_headers.py), so this comparison holds for every kind of
    refusal on that host."""
    response = await client.get(path)
    if response.status_code != 404 or response.content != NEUTRAL_404_BODY:
        return False
    return _header_set(response) == _header_set(await _router_404(client))


class TestAdminHost:
    @pytest.mark.parametrize("path", ["/", "/aurora", "/aurora/site", "/aurora/site/toegang", "/-/members"])
    async def test_the_spa_answers_every_path_it_owns(self, two_hosts, path: str) -> None:
        # A group or site path here is an SPA route, not the neutral 404, and
        # the platform pages of the SPA come along under `/-/`.
        admin, _ = two_hosts
        response = await admin.get(path)
        assert response.status_code == 200, path
        assert response.content == INDEX_HTML, path

    async def test_platform_routes_exist(self, two_hosts) -> None:
        admin, _ = two_hosts
        robots = await admin.get("/robots.txt")
        assert robots.status_code == 200
        assert robots.text == "User-agent: *\nDisallow: /\n"

    async def test_healthz_is_neutral_404(self, two_hosts) -> None:
        # Internal only (spec §4a/§11): through the public admin host the
        # probe does not exist, not even rate-limit-free.
        admin, _ = two_hosts
        assert await _is_neutral_404(admin, "/healthz")

    @pytest.mark.parametrize("path", ["/-/login", "/-/login?returnTo=%2Faurora%2Fsite%2F", "/-/oauth2/callback"])
    async def test_login_exists_also_on_the_admin_host(self, two_hosts, path: str) -> None:
        # Both hosts spell these two paths the same way and the host picks
        # the flow, so here they exist rather than being the neutral 404.
        admin, _ = two_hosts
        assert (await admin.get(path)).content != NEUTRAL_404_BODY

    @pytest.mark.parametrize("path", ["/admin", "/admin/", "/beheer", "/beheer/aurora/site"])
    async def test_nothing_is_reserved_for_the_app_any_more(self, two_hosts, path: str) -> None:
        """Nothing is reserved here: `admin` and `beheer` are ordinary SPA
        routes like any other path, so a group with either name stays
        reachable."""
        admin, _ = two_hosts
        response = await admin.get(path)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


class TestContentHost:
    @pytest.mark.parametrize(
        "path",
        ["/-/api/v1/x", "/-/onbekend", "/-/onbekend/diep/", "/healthz"],
    )
    async def test_platform_paths_are_neutral_404(self, two_hosts, path: str) -> None:
        _, content = two_hosts
        assert await _is_neutral_404(content, path)

    async def test_the_old_admin_prefixes_are_content_here(self, two_hosts) -> None:
        # `admin` and `beheer` are not reserved slugs: on this host they
        # are an ordinary group, so the path gets the lexical 301 that every
        # other site root gets and no refusal of its own.
        _, content = two_hosts
        response = await content.get("/admin/site")
        assert response.status_code == 301
        assert response.headers["location"] == "/admin/site/"
        assert await _is_neutral_404(content, "/admin")

    async def test_root_is_the_public_front_page(self, two_hosts) -> None:
        # The one path on this host that is not content and not the neutral
        # 404; test_front_page.py holds it to its content (spec §4a).
        _, content = two_hosts
        response = await content.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")

    async def test_content_path_reaches_the_serving_router(self, two_hosts) -> None:
        _, content = two_hosts
        response = await content.get("/aurora/site")
        assert response.status_code == 301
        assert response.headers["location"] == "/aurora/site/"

    async def test_standard_locations_exist(self, two_hosts) -> None:
        _, content = two_hosts
        robots = await content.get("/robots.txt")
        assert robots.status_code == 200
        assert robots.text == "User-agent: *\nDisallow:\n"

    async def test_content_login_exists_with_callback_on_the_content_host(self, tmp_path: Path) -> None:
        idp = MockIdP()
        settings = _settings(tmp_path, oidc_issuer=idp.issuer)
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            app.state.oidc_client = make_oidc_client(settings, idp)
            async with _client(app, CONTENT) as content:
                response = await content.get("/-/login?returnTo=%2Faurora%2Fsite%2F")
        assert response.status_code == 302
        authorization = httpx.URL(response.headers["location"])
        assert str(authorization).startswith(idp.issuer + "/authorize?")
        assert dict(authorization.params)["redirect_uri"] == CONTENT + "/-/oauth2/callback"

    async def test_host_comparison_ignores_gate_and_uppercase(self, tmp_path: Path) -> None:
        app = create_app(_settings(tmp_path))
        async with app.router.lifespan_context(app), _client(app, "https://PLAK.example:8443") as content:
            # The SPA steps aside here, so this is content, not the interface.
            assert await _is_neutral_404(content, "/-/onbekend")
            assert (await content.get("/aurora/site")).status_code == 301
