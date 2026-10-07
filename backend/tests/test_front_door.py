"""The root of the content host (platform/pages.py): a redirect to the admin
host's public landing page.

Three properties carry it, and each of them has its own test below:

1. it carries no authority - this is the content origin, where uploaded sites
   run their own JavaScript, so the answer has no body to speak of, sets no
   cookie and only ever points at the admin origin;
2. it changes nothing about the rest of the content host, where a refusal
   stays a byte-identical neutral 404;
3. the address of the admin host comes from the settings, never from the
   (client-controlled) Host header, and nothing from the query is echoed into
   it beyond a language code of our own.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import make_client_jwk

from plak.config import Settings
from plak.main import create_app
from plak.serving.response import NEUTRAL_404_BODY, PLATFORM_CSP

ADMIN = "https://beheer.plak.example"
CONTENT = "https://plak.example"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "db_url": "postgresql+asyncpg://unused:unused@localhost:5432/unused",
        "content_root": tmp_path / "content",
        "oidc_issuer": "https://idp.example",
        "oidc_client_id": "plak-client",
        "oidc_client_private_jwk": make_client_jwk(),
        "session_secret": "sessie-geheim-van-minstens-32-bytes!",
        "audit_pepper": "audit-pepper-van-minstens-32-bytes!!",
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "environment": "dev",
        "base_url": ADMIN,
        "content_base_url": CONTENT,
        "spa_path": tmp_path / "dist",
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


class TestOnTheContentHost:
    async def test_the_root_sends_a_visitor_to_the_admin_landing_page(self, two_hosts) -> None:
        _, content = two_hosts
        response = await content.get("/")

        assert response.status_code == 302
        assert response.headers["location"] == f"{ADMIN}/"

    async def test_the_redirect_carries_no_authority(self, two_hosts) -> None:
        """Uploaded sites run their own JavaScript on this same origin (see
        api/origin_guard.py), so the root sets nothing and renders nothing
        that could act here."""
        _, content = two_hosts
        response = await content.get("/")

        assert "set-cookie" not in response.headers
        assert "<script" not in response.text.lower()
        assert not response.headers["location"].startswith(CONTENT)

    async def test_the_redirect_carries_the_platform_headers(self, two_hosts) -> None:
        _, content = two_hosts
        response = await content.get("/")

        assert response.headers["content-security-policy"] == PLATFORM_CSP
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        # Not a permanent move: if the root ever carries a page again, no
        # browser should still hold on to this redirect.
        assert response.headers["cache-control"] == "no-cache"

    async def test_head_answers_with_the_headers_of_the_get(self, two_hosts) -> None:
        """A link checker or a monitor asks with HEAD; it gets what a browser
        would, without the body, here and on robots.txt and security.txt."""
        _, content = two_hosts
        for path, status in (("/", 302), ("/robots.txt", 200), ("/.well-known/security.txt", 200)):
            get = await content.get(path)
            head = await content.head(path)
            assert head.status_code == get.status_code == status, path
            assert head.headers.get("location") == get.headers.get("location"), path
            assert head.headers.get("content-type") == get.headers.get("content-type"), path
            assert head.headers["content-length"] == get.headers["content-length"], path

    @pytest.mark.parametrize("lang", ["nl", "en"])
    async def test_a_language_of_ours_travels_along(self, two_hosts, lang: str) -> None:
        """An old link to `/?lang=en` keeps opening in English; the SPA reads
        the same parameter."""
        _, content = two_hosts
        response = await content.get(f"/?lang={lang}")

        assert response.status_code == 302
        assert response.headers["location"] == f"{ADMIN}/?lang={lang}"

    @pytest.mark.parametrize(
        "lang",
        ["de", "EN", "en%20", "en-GB", "e", "", "%22%3E%3Cscript%3E", "en%0D%0ASet-Cookie:%20x=1"],
    )
    async def test_anything_else_in_lang_is_dropped_not_echoed(self, two_hosts, lang: str) -> None:
        """Only an exact code of our own goes into the Location header; a near
        miss is not a spelling of one, and an injection attempt has nothing to
        reflect."""
        _, content = two_hosts
        response = await content.get(f"/?lang={lang}")

        assert response.status_code == 302
        assert response.headers["location"] == f"{ADMIN}/"
        assert "set-cookie" not in response.headers

    async def test_the_host_header_does_not_choose_the_target(self, tmp_path: Path) -> None:
        """The admin origin comes from PLAK_BASE_URL. A request that names
        another admin host in its own headers still goes to the configured one."""
        app = create_app(_settings(tmp_path))
        async with app.router.lifespan_context(app), _client(app, CONTENT) as content:
            response = await content.get(
                "/", headers={"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "https"}
            )

        assert response.headers["location"] == f"{ADMIN}/"

    async def test_a_trailing_slash_on_the_origin_does_not_double(self, tmp_path: Path) -> None:
        app = create_app(_settings(tmp_path, base_url=f"{ADMIN}/"))
        async with app.router.lifespan_context(app), _client(app, CONTENT) as content:
            response = await content.get("/")

        assert response.headers["location"] == f"{ADMIN}/"

    async def test_the_rest_of_the_host_keeps_its_neutral_404(self, two_hosts) -> None:
        """Anti-enumeration (behaviour requirement 6) is untouched.

        The root answers the same way always and at a fixed address, so it
        gives nothing away; every other refusal on this host stays
        byte-identical, headers included, whether host separation or the
        serving router answers it.
        """
        _, content = two_hosts
        router_404 = await content.get("/favicon.ico/x/")
        separation_404 = await content.get("/-/unknown")

        assert router_404.status_code == 404
        assert router_404.content == NEUTRAL_404_BODY
        assert separation_404.status_code == 404
        assert separation_404.content == NEUTRAL_404_BODY
        assert sorted(separation_404.headers.multi_items()) == sorted(router_404.headers.multi_items())

    async def test_a_path_beside_the_root_is_not_redirected(self, two_hosts) -> None:
        # A single segment matches no route (content starts at
        # /{group}/{site}), so this is FastAPI's own routing 404, not the
        # neutral one. What is pinned here is that the root did not widen into
        # a catch-all.
        _, content = two_hosts
        response = await content.get("/iets-dat-niet-bestaat")

        assert response.status_code == 404
        assert "location" not in response.headers


class TestElsewhere:
    async def test_the_admin_host_root_is_the_spa_and_not_this_redirect(self, two_hosts) -> None:
        # On the admin host the root is the SPA, which carries the landing page
        # itself; this fixture has no build, so it answers that the SPA is
        # missing. Either way it does not redirect, which would loop.
        admin, _ = two_hosts
        response = await admin.get("/")

        assert response.status_code == 503
        assert "location" not in response.headers

    async def test_without_an_admin_origin_the_root_stays_the_neutral_404(self, tmp_path: Path) -> None:
        # Without PLAK_BASE_URL there is no address to send anyone to, and the
        # Host header here is the content host.
        app = create_app(_settings(tmp_path, base_url=None))
        async with app.router.lifespan_context(app), _client(app, CONTENT) as content:
            response = await content.get("/")

        assert response.status_code == 404
        assert response.content == NEUTRAL_404_BODY
