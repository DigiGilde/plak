"""The public front page on the root of the content host (platform/pages.py).

Three properties carry this page, and each of them has its own test below:

1. it carries no authority - this is the content origin, where uploaded sites
   run their own JavaScript, so the page has no script, sets no cookie and
   every link leaves for the admin origin;
2. it changes nothing about the rest of the content host, where a refusal
   stays a byte-identical neutral 404;
3. the address of the admin host comes from the settings, never from the
   (client-controlled) Host header.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import make_client_jwk

from plak import i18n
from plak.config import Settings
from plak.main import create_app
from plak.platform.pages import (
    BETA_NOTICE,
    FRONT_PAGE_INTRO,
    FRONT_PAGE_NAME_STORY,
    front_page_html,
)
from plak.serving.response import CONTENT_CSP, NEUTRAL_404_BODY

ADMIN = "https://beheer.plak.example"
CONTENT = "https://plak.example"

APP_VUE = Path(__file__).resolve().parents[2] / "frontend" / "src" / "App.vue"
SPA_CATALOGUE_NL = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "nl.ts"


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


def _hrefs(body: str) -> list[str]:
    return re.findall(r'href="([^"]*)"', body)


def _anchor_hrefs(body: str) -> list[str]:
    """Only what a visitor can click. A <link rel="alternate"> is metadata."""
    return re.findall(r'<a [^>]*href="([^"]*)"', body)


class TestOnTheContentHost:
    async def test_root_says_what_plak_is_and_where_the_name_comes_from(self, two_hosts) -> None:
        _, content = two_hosts
        response = await content.get("/")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        body = response.text
        assert FRONT_PAGE_INTRO in body
        assert "De naam komt van plakkaat." in body
        assert FRONT_PAGE_NAME_STORY in body
        assert "<h1" in body and "Plak" in body

    async def test_login_button_leads_to_the_admin_host(self, two_hosts) -> None:
        _, content = two_hosts
        body = (await content.get("/")).text

        assert f'<a class="button" href="{ADMIN}/-/login">Inloggen</a>' in body

    async def test_footer_carries_the_public_pages_absolute(self, two_hosts) -> None:
        _, content = two_hosts
        body = (await content.get("/")).text

        # The accessibility statement has to be publicly reachable; this
        # footer is the first place where it can be found without knowing the
        # address.
        assert f'<a href="{ADMIN}/-/accessibility">Toegankelijkheid</a>' in body
        assert f'<a href="{ADMIN}/-/about">Over Plak</a>' in body
        assert f'<a href="{ADMIN}/-/privacy">Privacy</a>' in body
        assert f'<a href="{ADMIN}/-/api/docs">API-documentatie</a>' in body
        assert f'<a href="{ADMIN}/-/whats-new">Wat is er nieuw</a>' in body

    async def test_page_carries_no_authority(self, two_hosts) -> None:
        """The core requirement: nothing here can do anything on this origin.

        Uploaded sites run their own JavaScript on this same origin (see
        api/origin_guard.py). So no script of our own, no session or CSRF
        cookie, and not a single link or form that stays on this host.
        """
        _, content = two_hosts
        response = await content.get("/")
        body = response.text

        assert "<script" not in body.lower()
        assert "set-cookie" not in response.headers
        assert "<form" not in body.lower()

        anchors = _anchor_hrefs(body)
        assert anchors
        # One exception, and it has to stay exactly one. The language switch is
        # a plain GET to this same public page: no session, no script, no form,
        # nothing an uploaded site could borrow. Everything else leaves for the
        # admin origin. A second entry here means someone added a link that
        # keeps a visitor on the content origin, and that needs its own
        # argument.
        stays_here = [href for href in anchors if not href.startswith(ADMIN + "/")]
        assert stays_here == [f"{CONTENT}/?lang=en"]

    async def test_the_language_alternates_are_metadata_not_navigation(self, two_hosts) -> None:
        """hreflang needs absolute URLs on this host, so they are the only other
        place the content origin appears. They are <link> elements in the head,
        which no visitor can click."""
        _, content = two_hosts
        body = (await content.get("/")).text

        assert f'<link rel="alternate" hreflang="x-default" href="{CONTENT}/">' in body
        assert f'<link rel="alternate" hreflang="nl" href="{CONTENT}/?lang=nl">' in body
        assert f'<link rel="alternate" hreflang="en" href="{CONTENT}/?lang=en">' in body

    async def test_the_switch_beats_the_header(self, two_hosts) -> None:
        """Someone whose browser asks for a language they cannot read has to be
        able to get out of it."""
        _, content = two_hosts

        dutch_browser = await content.get("/", headers={"Accept-Language": "nl"})
        assert '<html lang="nl">' in dutch_browser.text
        assert f'href="{CONTENT}/?lang=en"' in dutch_browser.text

        switched = await content.get("/?lang=en", headers={"Accept-Language": "nl"})
        assert '<html lang="en">' in switched.text
        # And back again, so the switch is never a one-way door.
        assert f'href="{CONTENT}/?lang=nl"' in switched.text

    async def test_a_language_we_do_not_have_falls_through_to_the_header(self, two_hosts) -> None:
        """The value is never echoed into the page, so a bogus one has nothing
        to reflect; it simply does not count as a choice."""
        _, content = two_hosts

        response = await content.get("/?lang=de", headers={"Accept-Language": "nl"})
        assert '<html lang="nl">' in response.text

        # Only an exact code counts; a near miss is not a spelling of one.
        for near_miss in ("EN", "en%20", "en-GB", "e"):
            response = await content.get(f"/?lang={near_miss}", headers={"Accept-Language": "nl"})
            assert '<html lang="nl">' in response.text, near_miss

        injected = await content.get('/?lang=%22%3E%3Cscript%3E', headers={"Accept-Language": "en"})
        assert '<html lang="en">' in injected.text
        assert "<script" not in injected.text.lower()

    async def test_page_carries_the_content_headers(self, two_hosts) -> None:
        _, content = two_hosts
        response = await content.get("/")

        assert response.headers["content-security-policy"] == CONTENT_CSP
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
        assert response.headers["cache-control"] == "no-cache"
        # The page is meant to be found: whoever types the name has to land
        # here, so no X-Robots-Tag: noindex as under /admin.
        assert "x-robots-tag" not in response.headers

    async def test_the_page_tells_caches_that_the_language_varies(self, two_hosts) -> None:
        """Without this a shared cache keys on the URL alone and hands the
        Dutch page to an English visitor, or the other way round."""
        _, content = two_hosts
        response = await content.get("/", headers={"Accept-Language": "en"})

        assert response.headers["vary"] == "Accept-Language"
        assert '<html lang="en">' in response.text

    async def test_the_rest_of_the_host_keeps_its_neutral_404(self, two_hosts) -> None:
        """Anti-enumeration (behaviour requirement 6) is untouched.

        The front page exists always and at a fixed address, so it gives
        nothing away; every other refusal on this host stays byte-identical,
        headers included, whether host separation or the serving router
        answers it.
        """
        _, content = two_hosts
        router_404 = await content.get("/favicon.ico/x/")
        separation_404 = await content.get("/-/unknown")

        assert router_404.status_code == 404
        assert router_404.content == NEUTRAL_404_BODY
        assert separation_404.status_code == 404
        assert separation_404.content == NEUTRAL_404_BODY
        assert sorted(separation_404.headers.multi_items()) == sorted(router_404.headers.multi_items())

    async def test_a_path_beside_the_root_does_not_get_the_front_page(self, two_hosts) -> None:
        # A single segment matches no route (content starts at
        # /{group}/{site}), so this is FastAPI's own routing 404, not the
        # neutral one. That is how it was before this page existed; what is
        # pinned here is that the root did not widen into a catch-all.
        _, content = two_hosts
        response = await content.get("/iets-dat-niet-bestaat")

        assert response.status_code == 404
        assert "Plak is het platform" not in response.text


class TestElsewhere:
    async def test_the_admin_host_root_is_the_spa_and_not_this_page(self, two_hosts) -> None:
        # The front page belongs to the content host alone. On the admin host
        # the root is the SPA, which carries its own landing; this fixture has
        # no build, so it answers that the SPA is missing. Either way it is not
        # the front page, and that is what this guards.
        admin, _ = two_hosts
        response = await admin.get("/")

        assert response.status_code == 503
        assert FRONT_PAGE_INTRO not in response.text

    async def test_without_an_admin_origin_the_root_stays_the_neutral_404(self, tmp_path: Path) -> None:
        # Without PLAK_BASE_URL the login button and the footer have no
        # address, and the Host header here is the content host. Then there is
        # no page to show.
        app = create_app(_settings(tmp_path, base_url=None))
        async with app.router.lifespan_context(app), _client(app, CONTENT) as content:
            response = await content.get("/")

        assert response.status_code == 404
        assert response.content == NEUTRAL_404_BODY


class TestVersionLink:
    def test_a_dev_build_links_to_the_page_without_an_anchor(self) -> None:
        body = front_page_html(ADMIN)

        assert f'<li><a href="{ADMIN}/-/whats-new">Wat is er nieuw</a></li>' in body

    def test_a_dev_build_says_what_is_new_in_both_languages(self) -> None:
        english = front_page_html(ADMIN, i18n.negotiate("en"))

        assert f'<a href="{ADMIN}/-/whats-new">What\'s new</a>' in english

    def test_a_version_that_is_not_a_calver_links_to_the_page_itself(self) -> None:
        body = front_page_html(ADMIN, version="2026.9.30-5-g1a2b3c4")

        assert f'<a href="{ADMIN}/-/whats-new">Versie 2026.9.30-5-g1a2b3c4</a>' in body

    def test_a_release_links_to_its_own_notes_in_both_languages(self) -> None:
        dutch = front_page_html(ADMIN, version="2026.10.2.1")
        english = front_page_html(ADMIN, i18n.negotiate("en"), version="2026.10.2.1")

        assert f'<a href="{ADMIN}/-/whats-new#d2026-10-02">Versie 2026.10.2.1</a>' in dutch
        assert f'<a href="{ADMIN}/-/whats-new#d2026-10-02">Version 2026.10.2.1</a>' in english

    def test_the_version_comes_first_in_the_footer(self) -> None:
        body = front_page_html(ADMIN, version="2026.10.2")

        assert body.index("/-/whats-new#") < body.index("/-/about")

    def test_the_version_is_escaped(self) -> None:
        body = front_page_html(ADMIN, version='1"><script>')

        assert "<script>" not in body
        assert "/-/whats-new#" not in body

    async def test_the_settings_version_reaches_the_page(self, tmp_path: Path) -> None:
        app = create_app(_settings(tmp_path, version="2026.10.2"))
        async with app.router.lifespan_context(app), _client(app, CONTENT) as client:
            body = (await client.get("/")).text

        assert "#d2026-10-02" in body

    def test_the_version_defaults_to_dev(self, tmp_path: Path) -> None:
        assert _settings(tmp_path).version == "dev"


class TestRendering:
    def test_the_front_page_says_plak_is_still_in_development(self) -> None:
        """A visitor who lands here has to be told before they rely on it, not
        after something has gone wrong."""
        body = front_page_html("https://beheer.plak.example")

        assert BETA_NOTICE in body
        assert "Plak is in ontwikkeling" in BETA_NOTICE
        # Above everything, the way nldd-status-bar sits on the admin host:
        # outside the page wrapper, so before the wordmark rather than beside
        # the introduction.
        assert body.index(BETA_NOTICE) < body.index('<div class="page">')

    def test_the_notice_is_one_line_that_survives_being_cut_off(self) -> None:
        """It renders as the same bar as nldd-status-bar on the other host:
        24px, one line, ellipsis. So the first words have to carry the message,
        because on a narrow screen the rest is gone."""
        assert i18n.NL["beta.bar"].startswith("Bètaversie")
        assert i18n.EN["beta.bar"].startswith("Beta")
        for text in (i18n.NL["beta.bar"], i18n.EN["beta.bar"]):
            assert "\n" not in text
            assert len(text) < 80, text

        body = front_page_html("https://beheer.plak.example")
        assert "text-overflow: ellipsis" in body
        # min-height, not height: the bar has to grow with the text. The design
        # system's own status bar pins 24px against a 0.889rem font, so at 200
        # percent it clips its own glyphs; this copy does not.
        assert "min-height: 24px" in body
        assert "\n  height: 24px" not in body

    def test_the_front_page_follows_accept_language(self) -> None:
        """The content host has no session to read a preference from, and this
        page is public, so the header is all there is to go on."""
        english = front_page_html("https://beheer.plak.example", i18n.negotiate("en-GB,en;q=0.9"))
        dutch = front_page_html("https://beheer.plak.example", i18n.negotiate("nl-NL,nl;q=0.9"))

        assert '<html lang="en">' in english
        assert "Plak is under development" in english
        assert "Sign in" in english
        assert "How to share a page" in english
        assert '<html lang="nl">' in dutch
        assert "Plak is in ontwikkeling" in dutch
        assert "Zo deel je een pagina" in dutch

    def test_a_visitor_who_asks_for_nothing_gets_the_language_of_the_service(self) -> None:
        """No header is no signal, and Plak is a Dutch service. A lone `*` is
        not a request for a language either."""
        assert i18n.negotiate(None) == "nl"
        assert i18n.negotiate("") == "nl"
        assert i18n.negotiate("*") == "nl"
        assert i18n.negotiate(" ; q=0.5") == "nl"

    def test_the_weight_decides_which_language_wins(self) -> None:
        """Browsers write their list in preference order with descending
        weights, so this only shows up with a client that writes its own
        header. It was answering with Dutch there."""
        assert i18n.negotiate("nl;q=0.1, en;q=0.9") == "en"
        assert i18n.negotiate("en;q=0.1, nl;q=0.9") == "nl"
        # Equal weights keep the order they were written in.
        assert i18n.negotiate("en;q=0.5, nl;q=0.5") == "en"
        assert i18n.negotiate("nl, en") == "nl"

    def test_a_weight_of_zero_means_not_acceptable(self) -> None:
        """q=0 is a refusal, not a low preference. Dutch turned down and
        nothing else we have on the list leaves English."""
        assert i18n.negotiate("nl;q=0, fr") == "en"
        assert i18n.negotiate("nl;q=0") == "en"
        # English turned down leaves the language of the service.
        assert i18n.negotiate("en;q=0, fr") == "nl"
        # Both turned down: nothing is acceptable, so the service speaks its own.
        assert i18n.negotiate("nl;q=0, en;q=0") == "nl"
        # Refusing a language we do not have says nothing about ours.
        assert i18n.negotiate("fr;q=0") == "nl"

    def test_an_unreadable_header_falls_back_rather_than_fails(self) -> None:
        assert i18n.negotiate("nl;q=abc, en") == "en"
        assert i18n.negotiate(";;;") == "nl"
        assert i18n.negotiate("nl;q=9") == "nl"
        assert i18n.negotiate(",,nl,,") == "nl"

    def test_a_language_we_do_not_have_falls_back_to_english_not_dutch(self) -> None:
        """Someone asking for French has told us they do not read Dutch. Then
        the wider of the two languages we do have is the better guess than the
        one we already know they did not ask for."""
        assert i18n.negotiate("de-DE,de;q=0.9") == "en"
        assert i18n.negotiate("fr") == "en"
        assert i18n.negotiate("pl-PL,pl;q=0.9,ru;q=0.8") == "en"

        # But only when there is nothing we do have anywhere in the list.
        assert i18n.negotiate("fr-FR,fr;q=0.9,nl;q=0.5") == "nl"
        assert i18n.negotiate("de,en-GB;q=0.8") == "en"

    def test_a_trailing_slash_on_the_origin_does_not_double(self) -> None:
        body = front_page_html("https://beheer.plak.example")

        assert "https://beheer.plak.example//" not in body

    def test_the_origin_is_escaped(self) -> None:
        # The origin comes from the settings, not from a request; escaping is
        # what keeps a configuration mistake from becoming markup.
        body = front_page_html('https://x"onmouseover="alert(1)')

        assert 'onmouseover="alert(1)' not in body
        assert "&quot;onmouseover=&quot;alert(1)" in body

    def test_the_footer_matches_the_footer_of_the_spa(self) -> None:
        """Both footers name the same pages in the same order.

        A visitor meets one of the two depending on the host; a link that is
        added on one side and forgotten on the other makes the two say
        different things about what this platform offers.
        """
        source = APP_VUE.read_text(encoding="utf-8")
        legal_bar = re.search(
            r"<nldd-page-footer-legal-bar[^>]*>(.*?)</nldd-page-footer-legal-bar>", source, re.DOTALL
        )
        assert legal_bar is not None
        # The SPA is bilingual, so its footer names a catalogue key rather than
        # a word. The Dutch side of that catalogue is what this page renders.
        pattern = 'href="([^"]+)"' + r"\s+" + ':text="t\\(\'([^\']+)\'\\)"'
        spa_links = re.findall(pattern, legal_bar.group(1))
        catalogue = dict(
            re.findall(r"^  '([\w.]+)': '((?:[^'\\]|\\.)*)',$", SPA_CATALOGUE_NL.read_text(encoding="utf-8"), re.M)
        )

        body = front_page_html(ADMIN)
        front_page_links = re.findall(r"<li><a href=\"([^\"]+)\">([^<]+)</a></li>", body)

        # The version link sits in the SPA's start slot, before the legal
        # bar's own links, and has a dynamic href and text that the pattern
        # above does not match. The front page renders it first, as "Wat is er
        # nieuw" (no version known here means a dev build).
        assert ':text="versionText"' in source
        assert "t('footer.version', { version })" in source
        assert catalogue["footer.version"] == "Versie {version}"
        assert catalogue["footer.whatsNew"] == "Wat is er nieuw"

        assert len(spa_links) == 4
        assert front_page_links[0] == (f"{ADMIN}/-/whats-new", catalogue["footer.whatsNew"])
        assert front_page_links[1:] == [(ADMIN + href, catalogue[key]) for href, key in spa_links]
