"""The admin SPA served by the app itself (platform/spa.py; spec §4, §9):
static files on the root of the admin host, the index.html fallback, the
header set, path validation and the 503 when no SPA has been built. No database
needed: the middleware answers ahead of every router.

The class that carries this file is TestBoundary. Since the SPA moved to the
root of the admin host, its fallback is the answer for the whole host, and
that makes the question "what is NOT the SPA" a security question: an app path
that comes back as the interface with status 200 misleads a human and a script
alike. Both directions are pinned there.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from helpers_oidc import make_client_jwk
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from plak.config import Settings
from plak.main import create_app
from plak.platform.spa import (
    ADMIN_CSP,
    CACHE_ASSETS,
    CACHE_OTHER,
    MESSAGE_SPA_MISSING,
    SPA_PAGE_PATHS,
    SpaMiddleware,
    admin_csp,
    is_spa_path,
    spa_headers,
)
from plak.serving.response import NEUTRAL_404_BODY

ADMIN_URL = "https://beheer.plak.example"
CONTENT_URL = "https://plak.example"
CONTENT_HOST = "plak.example"

INDEX_HTML = b"<!doctype html><div id=app></div>"
APP_JS = b"console.log('plak')"
SECRET = b"buiten de spa-map"

EXPECTED_SPA_HEADERS = {
    "content-security-policy": ADMIN_CSP,
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "x-robots-tag": "noindex, nofollow",
    "cross-origin-opener-policy": "same-origin",
}

ROUTER_TS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "router.ts"
# Top-level platform routes of the Vue router: `path: '/-/members'`. The tabs
# of a group are spelled without a leading slash (`path: '-/members'`), so they
# stay out of this on purpose.
_ROUTER_PLATFORM_PATH = re.compile(r"path: '(/-/[a-z0-9-]+)'")


@pytest.fixture
def spa_dir(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_bytes(INDEX_HTML)
    (dist / "assets" / "app-abc123.js").write_bytes(APP_JS)
    (dist / "assets" / "font.woff2").write_bytes(b"\x00" * 64)
    (dist / "manifest.webmanifest").write_bytes(b"{}")
    (tmp_path / "geheim.txt").write_bytes(SECRET)
    # Symlink inside the dist pointing outward: must never be followed.
    os.symlink(tmp_path / "geheim.txt", dist / "link.txt")
    return dist


async def _inner(scope: Scope, receive: Receive, send: Send) -> None:
    await PlainTextResponse(f"binnenste:{scope['path']}")(scope, receive, send)


def _client(spa_path: Path, base: str = ADMIN_URL) -> httpx.AsyncClient:
    app = SpaMiddleware(_inner, spa_path=spa_path, content_host=CONTENT_HOST)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base, follow_redirects=False
    )


@pytest_asyncio.fixture
async def client(spa_dir: Path) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(spa_dir) as c:
        yield c


def _raw_scope(raw_path: str, method: str = "GET") -> Scope:
    """Scope as an ASGI server hands it over: path decoded once, dot segments
    left unnormalised (httpx normalises those away itself)."""
    from urllib.parse import unquote

    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": unquote(raw_path),
        "raw_path": raw_path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"beheer.plak.example")],
        "client": ("203.0.113.5", 4711),
        "server": ("beheer.plak.example", 443),
    }


async def _raw(spa_path: Path, raw_path: str) -> tuple[int, bytes]:
    app = SpaMiddleware(_inner, spa_path=spa_path, content_host=CONTENT_HOST)
    messages: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        messages.append(message)

    await app(_raw_scope(raw_path), receive, send)
    status = next(b["status"] for b in messages if b["type"] == "http.response.start")
    body = b"".join(b.get("body", b"") for b in messages if b["type"] == "http.response.body")
    return status, body


class TestBoundary:
    """Where the SPA stops and the app begins."""

    @pytest.mark.parametrize(
        "path",
        [
            "/",
            "/aurora",
            "/aurora/site",
            "/aurora/site/toegang",
            "/aurora/-/members",
            "/assets/app-abc123.js",
            "/apix",
            # The SPA's own platform pages, the only ones it owns under `/-/`.
            "/-/members",
            "/-/about",
        ],
    )
    def test_spa_paths(self, path: str) -> None:
        assert is_spa_path(path)

    @pytest.mark.parametrize(
        "path",
        [
            # Everything the app claims under the platform namespace. The typo
            # in the middle is the case this boundary exists for: it has to be
            # a 404, not the interface with status 200.
            "/-/api",
            "/-/api/v1/overview",
            "/-/apx/v1/overview",
            "/-/login",
            "/-/oauth2/callback",
            "/-/logout",
            "/-/onbekend",
            "/-/",
            # The locations the web pins down.
            "/robots.txt",
            "/favicon.ico",
            "/.well-known/acme-challenge/x",
            # Internal only.
            "/healthz",
        ],
    )
    def test_no_spa_paths(self, path: str) -> None:
        assert not is_spa_path(path)

    @pytest.mark.parametrize(
        "path", ["/-/apx/v1/overview", "/-/onbekend", "/robots.txt", "/favicon.ico", "/.well-known/x", "/healthz"]
    )
    async def test_what_the_app_claims_reaches_the_app(self, client: httpx.AsyncClient, path: str) -> None:
        # The other direction: these paths pass through the middleware, so
        # whatever the app makes of them (a route or a 404) is what comes out,
        # never index.html with status 200.
        response = await client.get(path)
        assert response.status_code == 200
        assert response.text == f"binnenste:{path}"

    async def test_a_group_path_that_looks_like_a_platform_path_stays_spa(
        self, client: httpx.AsyncClient
    ) -> None:
        # `/-/` is the app's, but a group named `apix` is not: the boundary
        # runs at the platform segment, not at what a path resembles.
        response = await client.get("/apix/v1/overview")
        assert response.status_code == 200
        assert response.content == INDEX_HTML

    def test_the_pages_under_the_platform_segment_match_the_vue_router(self) -> None:
        """The app owns `/-/` in full, so the SPA's pages there are named in
        SPA_PAGE_PATHS. A page added to the Vue router without being added
        there would work while clicking and 404 on a reload, which is exactly
        the kind of break nobody notices; this comparison turns it into a red
        test."""
        declared = set(_ROUTER_PLATFORM_PATH.findall(ROUTER_TS.read_text(encoding="utf-8")))
        assert declared == set(SPA_PAGE_PATHS)

    def test_header_set_per_kind(self) -> None:
        assert spa_headers("index.html")["Cache-Control"] == CACHE_OTHER
        assert spa_headers("assets/app.js")["Cache-Control"] == CACHE_ASSETS
        assert spa_headers("manifest.webmanifest")["Cache-Control"] == CACHE_OTHER
        headers = {name.lower(): value for name, value in spa_headers("index.html").items()}
        for name, value in EXPECTED_SPA_HEADERS.items():
            assert headers[name] == value, name


class TestNothingIsReservedForTheApp:
    """Nothing is reserved here: `admin` and `beheer` are ordinary SPA routes
    like any other path, so a group with either name stays reachable."""

    @pytest.mark.parametrize("path", ["/admin", "/admin/", "/beheer", "/beheer/", "/api"])
    async def test_a_former_platform_path_is_now_an_ordinary_spa_route(
        self, client: httpx.AsyncClient, path: str
    ) -> None:
        response = await client.get(path)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")


class TestContentHost:
    async def test_the_spa_steps_aside_completely(self, spa_dir: Path) -> None:
        # Without this the fallback would answer every content path with the
        # interface, on the origin where uploaded sites run.
        async with _client(spa_dir, base=CONTENT_URL) as content:
            for path in ("/", "/aurora/site/", "/assets/app-abc123.js", "/admin/aurora"):
                response = await content.get(path)
                assert response.status_code == 200, path
                assert response.text == f"binnenste:{path}", path


class TestServing:
    async def test_index_with_full_header_set(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/")
        assert response.status_code == 200
        assert response.content == INDEX_HTML
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["cache-control"] == CACHE_OTHER
        for name, value in EXPECTED_SPA_HEADERS.items():
            assert response.headers[name] == value, name
        assert "etag" in response.headers

    async def test_asset_immutable(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/assets/app-abc123.js")
        assert response.status_code == 200
        assert response.content == APP_JS
        assert response.headers["content-type"] == "text/javascript; charset=utf-8"
        assert response.headers["cache-control"] == CACHE_ASSETS
        for name, value in EXPECTED_SPA_HEADERS.items():
            assert response.headers[name] == value, name

    async def test_font_mime_from_own_table(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/assets/font.woff2")
        assert response.status_code == 200
        assert response.headers["content-type"] == "font/woff2"

    @pytest.mark.parametrize(
        "path", ["/aurora", "/aurora/site/toegang", "/-/members", "/assets/ontbreekt.js"]
    )
    async def test_unknown_path_gets_index_html(self, client: httpx.AsyncClient, path: str) -> None:
        response = await client.get(path)
        assert response.status_code == 200
        assert response.content == INDEX_HTML
        assert response.headers["content-type"] == "text/html; charset=utf-8"
        assert response.headers["cache-control"] == CACHE_OTHER

    async def test_other_method_than_get_head_is_405(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/")
        assert response.status_code == 405
        assert response.headers["allow"] == "GET, HEAD"

    async def test_a_post_to_an_app_path_reaches_the_app(self, client: httpx.AsyncClient) -> None:
        # The 405 above belongs to the SPA alone; the app keeps every method it
        # has routes for.
        response = await client.post("/-/logout")
        assert response.status_code == 200
        assert response.text == "binnenste:/-/logout"

    async def test_head_without_body(self, client: httpx.AsyncClient) -> None:
        response = await client.head("/assets/app-abc123.js")
        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["cache-control"] == CACHE_ASSETS

    async def test_304_on_if_none_match(self, client: httpx.AsyncClient) -> None:
        first = await client.get("/")
        response = await client.get("/", headers={"If-None-Match": first.headers["etag"]})
        assert response.status_code == 304
        assert response.content == b""
        assert response.headers["cache-control"] == CACHE_OTHER

    async def test_range_on_asset(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/assets/app-abc123.js", headers={"Range": "bytes=0-6"})
        assert response.status_code == 206
        assert response.content == APP_JS[:7]
        assert response.headers["content-range"] == f"bytes 0-6/{len(APP_JS)}"


class TestPathValidation:
    @pytest.mark.parametrize(
        "raw_path",
        [
            "/../geheim.txt",
            "/%2e%2e/geheim.txt",
            "/assets/../../geheim.txt",
            "/assets/..%2f..%2fgeheim.txt",
            "/..%5cgeheim.txt",
        ],
    )
    async def test_never_outside_the_spa_dir(self, spa_dir: Path, raw_path: str) -> None:
        status, body = await _raw(spa_dir, raw_path)
        assert status == 200
        assert body == INDEX_HTML
        assert SECRET not in body

    async def test_symlink_to_outside_does_not_become_followed(self, spa_dir: Path) -> None:
        status, body = await _raw(spa_dir, "/link.txt")
        assert status == 200
        assert body == INDEX_HTML

    async def test_null_byte_in_path_falls_back_to_index(self, spa_dir: Path) -> None:
        status, body = await _raw(spa_dir, "/assets/app%00.js")
        assert status == 200
        assert body == INDEX_HTML


class TestMissingSpa:
    async def test_missing_dir_gives_503_text(self, tmp_path: Path) -> None:
        async with _client(tmp_path / "bestaat-niet") as client:
            response = await client.get("/")
        assert response.status_code == 503
        assert response.text == MESSAGE_SPA_MISSING
        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-security-policy"] == ADMIN_CSP

    async def test_dir_without_index_gives_503(self, tmp_path: Path) -> None:
        (tmp_path / "leeg").mkdir()
        async with _client(tmp_path / "leeg") as client:
            response = await client.get("/assets/x.js")
        assert response.status_code == 503

    async def test_app_paths_stay_reachable_without_spa(self, tmp_path: Path) -> None:
        async with _client(tmp_path / "bestaat-niet") as client:
            response = await client.get("/-/api/v1/overview")
        assert response.status_code == 200
        assert response.text == "binnenste:/-/api/v1/overview"

    async def test_spa_that_appears_later_is_picked_up(self, tmp_path: Path) -> None:
        dist = tmp_path / "later"
        async with _client(dist) as client:
            assert (await client.get("/")).status_code == 503
            dist.mkdir()
            (dist / "index.html").write_bytes(INDEX_HTML)
            response = await client.get("/")
        assert response.status_code == 200
        assert response.content == INDEX_HTML


def _settings(tmp_path: Path, spa_path: Path) -> Settings:
    return Settings(
        db_url="postgresql+asyncpg://ongebruikt:ongebruikt@localhost:5432/ongebruikt",
        content_root=tmp_path / "content",
        oidc_issuer="https://idp.example",
        oidc_client_id="plak-client",
        oidc_client_private_jwk=make_client_jwk(),
        oidc_required_acr="urn:acr:hoog",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper="audit-pepper-van-minstens-32-bytes!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        base_url=ADMIN_URL,
        content_base_url=CONTENT_URL,
        spa_path=spa_path,
    )


class TestFullApp:
    async def test_spa_via_create_app(self, tmp_path: Path, spa_dir: Path) -> None:
        app = create_app(_settings(tmp_path, spa_dir))
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ADMIN_URL) as client,
        ):
            index = await client.get("/aurora/site")
            asset = await client.get("/assets/app-abc123.js")
            unknown_api = await client.get("/-/api/v1/bestaat-niet")
            typo_api = await client.get("/-/apx/v1/overview")

        assert index.status_code == 200
        assert index.content == INDEX_HTML
        # The full app lets the logout form pass the content host
        # (platform/pages.py, logout); everything else is the strict regime.
        assert index.headers["content-security-policy"] == admin_csp(CONTENT_URL)
        assert index.headers["api-version"] == "1.0.0"
        assert asset.headers["cache-control"] == CACHE_ASSETS
        # An unknown or mistyped app path never gets index.html: it falls
        # through to the app, where nothing claims it and the neutral 404 is
        # the answer.
        for response in (unknown_api, typo_api):
            assert response.status_code == 404
            assert response.content == NEUTRAL_404_BODY

    async def test_missing_spa_logs_on_startup_and_does_not_crash(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        app = create_app(_settings(tmp_path, tmp_path / "geen-dist"))
        with caplog.at_level(logging.WARNING, logger="plak.main"):
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ADMIN_URL) as client,
            ):
                response = await client.get("/")
                app_path = await client.get("/-/api/v1/me")

        assert response.status_code == 503
        # Without a built SPA the app itself keeps answering.
        assert app_path.status_code == 401
        assert any("PLAK_SPA_PATH" in row.getMessage() for row in caplog.records)
