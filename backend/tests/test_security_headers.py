"""Security headers as app middleware (security_headers.py; spec §9, §13):
HSTS only on an https admin origin, Permissions-Policy everywhere, and the
strict admin regime (COOP, nosniff, the full admin CSP plus noindex on HTML
without a CSP of its own, frame-ancestors on the rest) on the admin host and
nowhere else.

The regime follows the host, not the path, and the second class below is what
that is for: on the content host every refusal has to carry the same headers,
whatever it is refusing. While the regime followed the path, `/admin/x` was
refused there with `Cross-Origin-Opener-Policy` and `/bestaat/niet/` without
it, so the one neutral 404 came in two shapes and gave away that an admin world
sits behind this origin.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from helpers_oidc import make_client_jwk
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.types import Receive, Scope, Send

from plak.api.docs import DOCS_CSP
from plak.api.origin_guard import normalise_origin
from plak.config import Settings
from plak.main import create_app
from plak.platform.spa import ADMIN_CSP, admin_csp
from plak.security_headers import (
    COOP,
    FRAME_ANCESTORS_CSP,
    HSTS,
    NOINDEX,
    NOSNIFF,
    PERMISSIONS_POLICY,
    SecurityHeadersMiddleware,
    is_html,
    is_https,
)
from plak.serving.response import (
    CONTENT_CSP,
    CONTENT_CSP_EXTERNAL,
    CONTENT_CSP_EXTERNAL_SANDBOX,
    CONTENT_CSP_SANDBOX,
    EXTERNAL_SOURCES,
    NEUTRAL_404_BODY,
    SANDBOX,
    content_csp,
)


def _directives(policy: str) -> dict[str, list[str]]:
    parts = [directive.strip().split() for directive in policy.split(";")]
    return {part[0]: part[1:] for part in parts}


class TestContentCspComposition:
    """The two content policies come out of one table, so they cannot drift:
    the policy with external sources is the strict one, directive for
    directive, with exactly the hosts of EXTERNAL_SOURCES appended.
    """

    def test_both_policies_carry_the_same_directives(self) -> None:
        assert list(_directives(CONTENT_CSP)) == list(_directives(CONTENT_CSP_EXTERNAL))

    def test_external_adds_the_listed_hosts_and_nothing_else(self) -> None:
        strict = _directives(CONTENT_CSP)
        external = _directives(CONTENT_CSP_EXTERNAL)
        for name, values in strict.items():
            assert external[name] == values + list(EXTERNAL_SOURCES.get(name, ())), name

    def test_only_script_style_and_font_widen(self) -> None:
        assert set(EXTERNAL_SOURCES) == {"script-src", "style-src", "font-src"}

    def test_what_may_not_widen_stays_where_it_was(self) -> None:
        external = _directives(CONTENT_CSP_EXTERNAL)
        assert external["connect-src"] == ["'self'"]
        assert external["img-src"] == ["'self'", "data:", "blob:"]
        assert external["frame-ancestors"] == ["'none'"]
        assert external["form-action"] == ["'self'"]
        assert external["object-src"] == ["'none'"]
        assert external["base-uri"] == ["'self'"]

    def test_the_switch_picks_the_policy(self) -> None:
        assert content_csp(external_sources=False, sandbox=False) == CONTENT_CSP
        assert content_csp(external_sources=True, sandbox=False) == CONTENT_CSP_EXTERNAL
        assert content_csp(external_sources=False, sandbox=True) == CONTENT_CSP_SANDBOX
        assert content_csp(external_sources=True, sandbox=True) == CONTENT_CSP_EXTERNAL_SANDBOX

    def test_the_sandbox_withholds_allow_same_origin(self) -> None:
        """The whole point: with allow-same-origin the document would be back
        on the origin it shares with every other site, and the directive would
        buy nothing."""
        tokens = _directives(CONTENT_CSP_SANDBOX)["sandbox"]
        assert tokens == ["allow-scripts", "allow-forms", "allow-popups"]
        assert "allow-same-origin" not in tokens

    def test_the_sandbox_adds_only_its_own_directive(self) -> None:
        strict = _directives(CONTENT_CSP)
        sandboxed = _directives(CONTENT_CSP_SANDBOX)
        assert set(sandboxed) - set(strict) == {"sandbox"}
        for name, values in strict.items():
            assert sandboxed[name] == values, name

    def test_the_two_switches_are_independent(self) -> None:
        """Both on is both additions and nothing more, so no combination can
        quietly lose a directive the other one brought."""
        both = _directives(CONTENT_CSP_EXTERNAL_SANDBOX)
        assert both["sandbox"] == list(SANDBOX["sandbox"])
        for name, values in _directives(CONTENT_CSP_EXTERNAL).items():
            assert both[name] == values, name


ADMIN = "https://beheer.plak.example"
CONTENT = "https://plak.example"
CONTENT_HOST = "plak.example"


async def _inner(scope: Scope, receive: Receive, send: Send) -> None:
    path = scope["path"]
    if path == "/eigen-csp":
        response = PlainTextResponse("x", headers={"Content-Security-Policy": "default-src 'none'"})
    elif path == "/html":
        response = HTMLResponse("<p>x</p>")
    elif path == "/eigen-html":
        response = HTMLResponse(
            "<p>x</p>",
            headers={"Content-Security-Policy": "default-src 'none'", "X-Robots-Tag": "all"},
        )
    elif path == "/eigen-alles":
        response = PlainTextResponse(
            "x",
            headers={
                "Permissions-Policy": "camera=(self)",
                "Strict-Transport-Security": "max-age=1",
                "Cross-Origin-Opener-Policy": "unsafe-none",
            },
        )
    else:
        response = JSONResponse({"path": path})
    await response(scope, receive, send)


def _client(*, hsts: bool, base: str = ADMIN) -> httpx.AsyncClient:
    app = SecurityHeadersMiddleware(_inner, hsts=hsts, content_host=CONTENT_HOST)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base)


class TestMiddleware:
    def test_is_https(self) -> None:
        assert is_https("https://beheer.plak.example")
        assert is_https("HTTPS://beheer.plak.example")
        assert not is_https("http://beheer.plak.localhost:8080")
        assert not is_https(None)

    def test_is_html_reads_the_content_type(self) -> None:
        from starlette.datastructures import MutableHeaders

        assert is_html(MutableHeaders({"content-type": "TEXT/HTML; charset=utf-8"}))
        assert not is_html(MutableHeaders({"content-type": "application/json"}))
        assert not is_html(MutableHeaders({}))

    async def test_permissions_policy_everywhere_and_hsts_on_https(self) -> None:
        async with _client(hsts=True) as admin, _client(hsts=True, base=CONTENT) as content:
            admin_response = await admin.get("/-/api/v1/me")
            content_response = await content.get("/aurora/site/")
        for response in (admin_response, content_response):
            assert response.headers["permissions-policy"] == PERMISSIONS_POLICY
            assert response.headers["strict-transport-security"] == HSTS

    async def test_no_hsts_without_https(self) -> None:
        async with _client(hsts=False) as client:
            response = await client.get("/-/api/v1/me")
        assert "strict-transport-security" not in response.headers
        assert response.headers["permissions-policy"] == PERMISSIONS_POLICY

    @pytest.mark.parametrize("path", ["/-/api/v1/me", "/", "/aurora/site/", "/admin/x"])
    async def test_the_admin_regime_follows_the_host_not_the_path(self, path: str) -> None:
        # Every one of these paths gets the regime on the admin host and none
        # of it on the content host; the path plays no part.
        async with _client(hsts=True) as admin, _client(hsts=True, base=CONTENT) as content:
            on_admin = await admin.get(path)
            on_content = await content.get(path)

        assert on_admin.headers["cross-origin-opener-policy"] == COOP, path
        assert on_admin.headers["x-content-type-options"] == NOSNIFF, path
        assert on_admin.headers["content-security-policy"] == FRAME_ANCESTORS_CSP, path
        assert "x-robots-tag" not in on_admin.headers, path

        assert "cross-origin-opener-policy" not in on_content.headers, path
        assert "x-content-type-options" not in on_content.headers, path
        assert "content-security-policy" not in on_content.headers, path

    async def test_html_on_the_admin_host_gets_full_admin_csp_and_noindex(self) -> None:
        async with _client(hsts=True) as client:
            response = await client.get("/html")
        assert response.headers["content-security-policy"] == ADMIN_CSP
        assert response.headers["x-content-type-options"] == NOSNIFF
        assert response.headers["x-robots-tag"] == NOINDEX
        assert response.headers["cross-origin-opener-policy"] == COOP

    def test_form_action_widens_only_for_the_logout_chain(self, tmp_path: Path) -> None:
        """Chrome checks each redirect of a form navigation against
        form-action, and logout passes the content host and, with RP-initiated
        logout on, the IdP. Nothing else gets in."""
        from plak.main import create_app

        settings_on = _settings(tmp_path, oidc_rp_logout=True)
        off = create_app(_settings(tmp_path))
        on = create_app(settings_on)
        csp_off = next(m.kwargs["admin_csp"] for m in off.user_middleware if "admin_csp" in m.kwargs)
        csp_on = next(m.kwargs["admin_csp"] for m in on.user_middleware if "admin_csp" in m.kwargs)

        assert csp_off.endswith(f"form-action 'self' {CONTENT}")
        assert csp_on.endswith(f"form-action 'self' {CONTENT} {normalise_origin(settings_on.oidc_issuer)}")

    def test_the_admin_csp_pins_where_a_form_may_post(self) -> None:
        """form-action does not fall back to default-src, so leaving it out
        lets an injected form send a session to another origin. The API docs
        carry the same clamp."""
        for csp in (ADMIN_CSP, DOCS_CSP):
            assert "form-action 'self'" in csp
            assert "object-src 'none'" in csp
            assert "frame-ancestors 'none'" in csp
            assert "base-uri 'self'" in csp

    async def test_existing_headers_stay_stand(self) -> None:
        async with _client(hsts=True) as client:
            own_csp = await client.get("/eigen-csp")
            own_everything = await client.get("/eigen-alles")
            own_html = await client.get("/eigen-html")
        assert own_csp.headers["content-security-policy"] == "default-src 'none'"
        assert own_html.headers["content-security-policy"] == "default-src 'none'"
        assert own_html.headers["x-robots-tag"] == "all"
        assert own_html.headers["x-content-type-options"] == NOSNIFF
        assert own_everything.headers["permissions-policy"] == "camera=(self)"
        assert own_everything.headers["strict-transport-security"] == "max-age=1"
        assert own_everything.headers["cross-origin-opener-policy"] == "unsafe-none"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    dist = tmp_path / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_bytes(b"<!doctype html>")
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


@pytest_asyncio.fixture
async def app_clients(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, httpx.AsyncClient]]:
    app = create_app(_settings(tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ADMIN, follow_redirects=False) as admin,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=CONTENT, follow_redirects=False) as content,
    ):
        yield admin, content


def _header_set(response: httpx.Response) -> list[tuple[str, str]]:
    return sorted(response.headers.multi_items())


class TestFullApp:
    async def test_spa_keeps_full_admin_csp(self, app_clients) -> None:
        admin, _ = app_clients
        response = await admin.get("/")
        assert response.headers["content-security-policy"] == admin_csp(CONTENT)
        assert response.headers["cross-origin-opener-policy"] == COOP
        assert response.headers["strict-transport-security"] == HSTS
        assert response.headers["permissions-policy"] == PERMISSIONS_POLICY

    async def test_api_response_gets_coop_nosniff_and_frame_ancestors(self, app_clients) -> None:
        admin, _ = app_clients
        response = await admin.get("/-/api/v1/me")
        assert response.status_code == 401
        assert response.headers["content-security-policy"] == FRAME_ANCESTORS_CSP
        assert response.headers["x-content-type-options"] == NOSNIFF
        assert response.headers["cross-origin-opener-policy"] == COOP
        assert response.headers["strict-transport-security"] == HSTS

    async def test_api_docs_gets_the_full_admin_regime(self, app_clients) -> None:
        admin, _ = app_clients
        response = await admin.get("/-/api/docs")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        # The docs page carries its own, slightly wider CSP (style-src for
        # Swagger UI); the middleware leaves an existing header in place.
        assert response.headers["content-security-policy"] == DOCS_CSP
        assert response.headers["x-content-type-options"] == NOSNIFF
        assert response.headers["x-robots-tag"] == NOINDEX
        assert response.headers["cross-origin-opener-policy"] == COOP
        assert response.headers["strict-transport-security"] == HSTS
        assert response.headers["permissions-policy"] == PERMISSIONS_POLICY

    async def test_every_refusal_on_the_content_host_carries_the_same_header_set(self, app_clients) -> None:
        """Four kinds of refusal, four different pieces of code, one answer.

        Measured on the running stack before this fix: `/admin/x` came back
        with `cross-origin-opener-policy: same-origin` and
        `/nietbestaand/nietbestaand/` with the same body without that header.
        The comparison below is on the full header set, not on the body: the
        body was identical all along, and that is exactly why nobody saw it.
        """
        _, content = app_clients
        paths = [
            "/-/onbekend",  # host separation: the platform namespace of the admin host
            "/-/healthz",  # host separation: admin host only
            "/favicon.ico/x/",  # serving router: reserved slug, before the DB
            "/.well-known/x",  # serving router: reserved slug, lexical branch
            "/onbekend",  # the catch-all: a path no route claims
            "/admin",  # an ordinary miss, no admin regime here
        ]
        responses = [await content.get(path) for path in paths]
        reference = responses[0]
        assert reference.status_code == 404
        assert reference.content == NEUTRAL_404_BODY
        assert reference.headers["content-security-policy"] == CONTENT_CSP
        assert "cross-origin-opener-policy" not in reference.headers
        for path, response in zip(paths, responses, strict=True):
            assert response.status_code == 404, path
            assert response.content == NEUTRAL_404_BODY, path
            assert _header_set(response) == _header_set(reference), path

    async def test_the_same_refusal_on_the_admin_host_carries_the_admin_regime(self, app_clients) -> None:
        # The other side of the rule: weakening the content host may not
        # weaken the admin host, where every answer keeps COOP and nosniff.
        admin, _ = app_clients
        response = await admin.get("/-/onbekend")
        assert response.status_code == 404
        assert response.content == NEUTRAL_404_BODY
        assert response.headers["cross-origin-opener-policy"] == COOP
        assert response.headers["x-content-type-options"] == NOSNIFF
        assert response.headers["content-security-policy"] == CONTENT_CSP

    async def test_front_page_robots_and_security_txt_get_only_the_general_headers(self, app_clients) -> None:
        _, content = app_clients
        for path in ("/", "/robots.txt", "/.well-known/security.txt"):
            response = await content.get(path)
            assert response.status_code == 200, path
            assert response.headers["permissions-policy"] == PERMISSIONS_POLICY
            assert response.headers["strict-transport-security"] == HSTS
            assert "cross-origin-opener-policy" not in response.headers

    async def test_non_http_scope_is_passed_through_untouched(self) -> None:
        """A websocket (or lifespan) scope carries no response to add headers
        to; the middleware must step aside rather than read a host or touch
        `send`."""
        calls: list[Scope] = []

        async def inner(scope: Scope, receive: Receive, send: Send) -> None:
            calls.append(scope)

        middleware = SecurityHeadersMiddleware(inner, hsts=True, content_host=CONTENT_HOST)
        scope: Scope = {"type": "websocket", "path": "/ws"}

        async def receive() -> None:
            raise AssertionError("receive should not be called")

        async def send(message) -> None:
            raise AssertionError("send should not be called")

        await middleware(scope, receive, send)
        assert calls == [scope]

    async def test_no_hsts_with_http_base_url(self, tmp_path: Path) -> None:
        app = create_app(
            _settings(
                tmp_path, base_url="http://beheer.plak.localhost:8080", content_base_url="http://plak.localhost:8080"
            )
        )
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://beheer.plak.localhost:8080") as client,
        ):
            response = await client.get("/")
        assert response.status_code == 200
        assert "strict-transport-security" not in response.headers
        assert response.headers["permissions-policy"] == PERMISSIONS_POLICY
