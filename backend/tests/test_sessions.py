"""Tests for auth/sessions.py and platform/pages.py: cookies, rotation,
returnTo validation, logging out, the landing and the content-origin login
with its own session kind (spec §4a). Without a network and without a DB."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import httpx
import pytest
import pytest_asyncio
from helpers_audit import install_audit_recorder
from helpers_oidc import (
    APP_BASE_URL,
    CONTENT_BASE_URL,
    OMIT,
    MockIdP,
    complete_login,
    make_app,
    make_content_test_client,
    make_settings,
    make_test_client,
    set_content_session_cookie,
    set_session_cookie,
    start_login,
)

import plak.auth.sessions as sessions_mod
from plak import i18n
from plak.audit import vocabulary
from plak.auth.sessions import (
    CONTENT_ANCHOR_COOKIE,
    CONTENT_LOGIN_COOKIE,
    CONTENT_SESSION_COOKIE,
    CSRF_COOKIE,
    CSRF_HEADER,
    KEY_COOKIE,
    LOGIN_COOKIE,
    MAX_LOGIN_ATTEMPT_AGE,
    MAX_SESSION_AGE,
    SESSION_COOKIE,
    SessionKind,
    SessionStore,
    check_signature,
    content_anchor_session_from_request,
    content_session_from_request,
    content_site_prefix,
    csrf_valid,
    parse_site_path,
    session_from_request,
    sign,
    sign_key_cookie,
    site_prefix,
    valid_return_to,
    visitor_from_request,
)
from plak.constants import PATH_CONTENT_LOGIN, PATH_CONTENT_OAUTH2_PREFIX
from plak.models.audit import ActorKind
from plak.platform import pages

CONTENT_CALLBACK = PATH_CONTENT_OAUTH2_PREFIX + "callback"

SECRET = "sessie-geheim-van-minstens-32-bytes!"


class TestSigning:
    def test_roundtrip(self):
        token = sign(SECRET, "waarde-1")
        assert check_signature(SECRET, token) == "waarde-1"

    def test_forged_signature_refused(self):
        token = sign(SECRET, "waarde-1")
        assert check_signature(SECRET, token[:-2] + "xx") is None

    def test_other_secret_refused(self):
        token = sign(SECRET, "waarde-1")
        assert check_signature("ander-geheim-van-minstens-32-bytes!", token) is None

    def test_bare_value_without_signature_refused(self):
        assert check_signature(SECRET, "waarde-zonder-punt") is None
        assert check_signature(SECRET, "") is None

    @pytest.mark.parametrize("token", ["é.x", "waarde-1.é", "wåarde.abcdef"])
    def test_non_ascii_token_refused_not_raised(self, token):
        # compare_digest on str raises TypeError for non-ASCII; a cookie can
        # carry any latin-1 byte.
        assert check_signature(SECRET, token) is None


class TestReturnTo:
    @pytest.mark.parametrize(
        "value",
        [
            "//evil.example",
            "//evil.example/pad",
            "https://evil",
            "http://evil.example/",
            "/admin\\x",
            "\\\\evil.example",
            "admin",
            "",
            None,
            "/pad\r\nSet-Cookie: x=y",
            # A URL parser strips TAB, CR and LF before it works out the
            # origin, so this one reads as //evil.example wherever the result
            # is used unencoded.
            "/\t/evil.example",
            # The rest of the control range has no business in a path either,
            # up to and including DEL.
            "/pad\x0b",
            "/pad\x7f",
        ],
    )
    def test_invalid_falls_back_to_the_root(self, value):
        # The root of the admin host is where the SPA lives; a returnTo that
        # does not pass validation lands there, never on a foreign origin.
        assert valid_return_to(value) == "/"

    @pytest.mark.parametrize(
        "value",
        ["/fin/rapport/", "/aurora", "/", "/fin/rapport/index.html?x=1", "/fin/café.html"],
    )
    def test_valid_path_stays(self, value):
        assert valid_return_to(value) == value

    @pytest.mark.parametrize("value", ["//evil.example", "https://evil", "", None, "fin/rapport/"])
    def test_invalid_falls_back_to_given_default(self, value):
        assert valid_return_to(value, "/") == "/"


class TestParseSitePath:
    """The one shared rule for a site's own path, used by the session layer,
    the code page and the serving router."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("/fin/rapport/index.html", ("fin", "rapport")),
            ("/fin/rapport/", ("fin", "rapport")),
            ("/fin/rapport", ("fin", "rapport")),
            # Query string stripped before segments are read.
            ("/fin/rapport/index.html?x=1", ("fin", "rapport")),
            # Percent-encoding left exactly as it came in: an encoded slash
            # does not split, and what stays in the segment is not a slug.
            ("/fin/si%2Fte/index.html", None),
            # The result becomes a cookie Path, written unquoted: a `;` or
            # `=` in a segment would add attributes of its own.
            ("/x;Path=/;Domain=example.org/y/", None),
            ("/fin/rapport;Path=/", None),
            ("/fin=x/rapport/", None),
            ("/fin/rap=port/", None),
            # Not a slug in any other way either.
            ("/Fin/rapport/", None),
            ("/fin/-rapport/", None),
            ("/fin/rapport\n/", None),
            # Too few segments.
            ("/", None),
            ("/fin", None),
            ("/fin/", None),
            ("", None),
            # Empty group or site.
            ("//rapport/index.html", None),
            ("/fin//index.html", None),
            # A reserved slug, or the platform namespace, as the group.
            ("/robots.txt/rapport/index.html", None),
            ("/-/sessions", None),
            # The one reserved slug that is slug-shaped.
            ("/cli-link/rapport/", None),
        ],
    )
    def test_table(self, path, expected):
        assert parse_site_path(path) == expected

    def test_content_site_prefix_formats_a_match(self):
        assert content_site_prefix("/fin/rapport/index.html") == "/fin/rapport/"

    def test_content_site_prefix_passes_through_none(self):
        assert content_site_prefix("/fin") is None

    def test_site_prefix_formats_an_already_known_site(self):
        assert site_prefix("fin", "rapport") == "/fin/rapport/"


@pytest.fixture
def idp() -> MockIdP:
    return MockIdP()


@pytest.fixture
def app(idp: MockIdP):
    return make_app(make_settings(idp), idp)


@pytest_asyncio.fixture
async def client(app):
    async with make_test_client(app) as client:
        yield client


@pytest_asyncio.fixture
async def content_client(app):
    """The same app on the content host: /-/login and /-/oauth2/callback are
    spelled the same on both hosts, so the Host header picks the flow."""
    async with make_content_test_client(app) as client:
        yield client


def _cookie_value(response, name: str) -> str:
    return _set_cookie_header(response, name).split("=", 1)[1].split(";", 1)[0]


def _set_cookie_header(response, name: str) -> str:
    headers_ = [k for k in response.headers.get_list("set-cookie") if k.startswith(name + "=")]
    assert len(headers_) == 1, f"verwachtte precies een Set-Cookie voor {name}: {response.headers}"
    return headers_[0]


class TestLoginFlow:
    async def test_callback_sets_session_and_csrf_cookies_with_correct_attributes(self, client, idp):
        response = await complete_login(client, idp)
        assert response.status_code == 303
        assert response.headers["location"] == "/"

        session_header = _set_cookie_header(response, SESSION_COOKIE).lower()
        assert "httponly" in session_header
        assert "secure" in session_header
        assert "path=/;" in session_header or session_header.endswith("path=/")
        assert "samesite=strict" in session_header

        csrf_header = _set_cookie_header(response, CSRF_COOKIE).lower()
        assert "httponly" not in csrf_header  # double-submit: the SPA has to read it
        assert "secure" in csrf_header
        assert "path=/" in csrf_header
        assert "samesite=strict" in csrf_header

    async def test_session_cookie_is_signed_reference(self, client, app, idp):
        await complete_login(client, idp)
        token = client.cookies.get(SESSION_COOKIE)
        session_id = check_signature(app.state.settings.session_secret, token)
        assert session_id is not None
        session = app.state.session_store.get_session(session_id)
        assert session is not None
        assert session.sub == "gebruiker-1"
        assert session.email == "gebruiker@example.nl"
        assert session.email_verified is True
        assert session.acr == "urn:acr:hoog"

    @pytest.mark.parametrize(
        "claim", ["false", "true", 1, None, OMIT], ids=["string-false", "string-true", "one", "null", "missing"]
    )
    async def test_only_a_boolean_true_email_verified_counts(self, client, app, idp, claim):
        idp.token_claim_overrides = {"email_verified": claim}
        await complete_login(client, idp)
        session_id = check_signature(app.state.settings.session_secret, client.cookies.get(SESSION_COOKIE))
        assert app.state.session_store.get_session(session_id).email_verified is False

    async def test_session_id_rotates_on_new_login(self, client, app, idp):
        await complete_login(client, idp)
        first_token = client.cookies.get(SESSION_COOKIE)
        first_id = check_signature(app.state.settings.session_secret, first_token)

        await complete_login(client, idp)
        second_token = client.cookies.get(SESSION_COOKIE)
        second_id = check_signature(app.state.settings.session_secret, second_token)

        assert first_id != second_id
        # The old session is revoked server-side, not merely replaced.
        assert app.state.session_store.get_session(first_id) is None
        assert app.state.session_store.get_session(second_id) is not None

    async def test_returnto_valid_path_becomes_followed(self, client, idp):
        response = await complete_login(client, idp, return_to="/fin/rapport/")
        assert response.status_code == 303
        assert response.headers["location"] == "/fin/rapport/"

    @pytest.mark.parametrize("malicious", ["//evil.example", "https://evil", "/aurora\\x"])
    async def test_returnto_malicious_falls_back_to_the_root(self, client, idp, malicious):
        response = await complete_login(client, idp, return_to=malicious)
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    async def test_state_mismatch_refused(self, client, idp):
        await start_login(client, idp)
        response = await client.get(
            "/-/oauth2/callback", params={"code": "code-123", "state": "vervalste-state"}
        )
        assert response.status_code == 400
        assert client.cookies.get(SESSION_COOKIE) is None

    async def test_a_refusal_speaks_the_language_of_the_browser(self, client, idp):
        """There is no session yet at this point, so Accept-Language is all
        there is to go on."""
        await start_login(client, idp)
        response = await client.get(
            "/-/oauth2/callback",
            params={"code": "code-123", "state": "vervalste-state"},
            headers={"Accept-Language": "en-GB,en;q=0.9"},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == i18n.EN["login.failed"]

    async def test_not_ascii_state_refused_with_400(self, client, idp):
        await start_login(client, idp)
        response = await client.get(
            "/-/oauth2/callback", params={"code": "code-123", "state": "stäte-ünïcode"}
        )
        assert response.status_code == 400
        assert client.cookies.get(SESSION_COOKIE) is None

    async def test_idp_error_on_callback_refused(self, client, idp, audit):
        q = await start_login(client, idp)
        response = await client.get(
            "/-/oauth2/callback", params={"error": "access_denied", "state": q["state"]}
        )
        assert response.status_code == 400
        record = audit.only()
        assert record.reason_code == vocabulary.LOGIN_IDP_ERROR

    async def test_callback_without_code_is_refused(self, client, idp, audit):
        q = await start_login(client, idp)
        response = await client.get("/-/oauth2/callback", params={"state": q["state"]})
        assert response.status_code == 400
        record = audit.only()
        assert record.reason_code == vocabulary.LOGIN_CODE_MISSING

    async def test_callback_without_login_cookie_refused(self, client, idp):
        q = await start_login(client, idp)
        client.cookies.clear()
        response = await client.get(
            "/-/oauth2/callback", params={"code": "code-123", "state": q["state"]}
        )
        assert response.status_code == 400

    async def test_login_attempt_is_once(self, client, idp):
        response = await complete_login(client, idp)
        assert response.status_code == 303
        # Replaying the same callback: the attempt has been spent.
        repeated = await client.get(str(response.request.url))
        assert repeated.status_code == 400


class TestCallbackIssEnforcement:
    async def test_iss_mismatch_refuses_for_token_exchange(self, idp):
        app = make_app(make_settings(idp, oidc_iss_required=True), idp)
        async with make_test_client(app) as client:
            response = await complete_login(client, idp, iss_value="https://kwaadaardig.example")
            assert response.status_code == 400
            # The exchange never started: not one token request was made.
            assert idp.token_requests == []
            assert client.cookies.get(SESSION_COOKIE) is None

    async def test_missing_iss_refused_when_flag_on(self, idp):
        app = make_app(make_settings(idp, oidc_iss_required=True), idp)
        async with make_test_client(app) as client:
            response = await complete_login(client, idp, send_iss=False)
            assert response.status_code == 400
            assert idp.token_requests == []

    async def test_missing_iss_allowed_when_flag_from_and_no_support(self, idp):
        app = make_app(make_settings(idp, oidc_iss_required=False), idp)
        async with make_test_client(app) as client:
            response = await complete_login(client, idp, send_iss=False)
            assert response.status_code == 303

    async def test_missing_iss_refused_when_metadata_support_advertises(self, idp):
        idp.iss_param_supported = True
        app = make_app(make_settings(idp, oidc_iss_required=False), idp)
        async with make_test_client(app) as client:
            response = await complete_login(client, idp, send_iss=False)
            assert response.status_code == 400


class TestLoginOrigin:
    """A login another site navigated the browser into is a valid session, but
    not a deliberate one: it must not re-arm the freshness window that guards
    CLI device approval."""

    @pytest.mark.parametrize(
        ("headers", "expected"),
        [
            ({}, True),
            ({"Sec-Fetch-Site": "none"}, True),
            ({"Sec-Fetch-Site": "same-origin"}, True),
            ({"Origin": APP_BASE_URL, "Sec-Fetch-Site": "same-origin"}, True),
            ({"Sec-Fetch-Site": "same-site"}, False),
            ({"Sec-Fetch-Site": "cross-site"}, False),
            ({"Origin": CONTENT_BASE_URL}, False),
        ],
    )
    async def test_the_session_records_where_the_login_was_started(self, client, app, idp, headers, expected):
        response = await complete_login(client, idp, headers=headers)
        assert response.status_code == 303
        session_id = check_signature(app.state.settings.session_secret, client.cookies.get(SESSION_COOKIE))
        assert app.state.session_store.get_session(session_id).self_initiated is expected

    async def test_a_same_origin_navigation_without_user_activation_still_counts(self, client, app, idp):
        """What Chromium and Firefox send for `location.href = '/-/login'` from
        an admin page, and what Safari sends even for a real click: no
        Sec-Fetch-User. Requiring it would lock every Safari member out of
        CLI approval (WebKit bug 247697), so this counts as self-initiated and
        the SPA never navigates to the login by script instead
        (frontend/tests/login-navigation.test.ts)."""
        headers = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        response = await complete_login(client, idp, headers=headers)
        assert response.status_code == 303
        session_id = check_signature(app.state.settings.session_secret, client.cookies.get(SESSION_COOKIE))
        assert app.state.session_store.get_session(session_id).self_initiated is True

    async def test_a_cross_site_login_still_yields_a_usable_session(self, client, app, idp):
        """Only freshness is affected: the login itself works as before, so a
        link from an e-mail or another site logs someone in as usual."""
        response = await complete_login(client, idp, headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 303
        session_id = check_signature(app.state.settings.session_secret, client.cookies.get(SESSION_COOKIE))
        assert app.state.session_store.get_session(session_id) is not None


CONTENT_LOGOUT_LEG = f"{CONTENT_BASE_URL}/-/logout?from=beheer"


class TestLogout:
    async def test_logout_via_get_is_no_logout_on_the_admin_host(self, client, app, idp):
        """GET is only there for the content leg of the chain. On the admin
        host it is a miss, and the session stays."""
        await complete_login(client, idp)
        response = await client.get("/-/logout")
        assert response.status_code == 404
        session_id = check_signature(app.state.settings.session_secret, client.cookies.get(SESSION_COOKIE))
        assert app.state.session_store.get_session(session_id) is not None

    async def test_logout_post_ends_session_and_goes_on_to_the_content_host(self, client, app, idp):
        """The content session lives in a cookie only the content origin can
        clear, so the admin logout hands over to it."""
        await complete_login(client, idp)
        token = client.cookies.get(SESSION_COOKIE)
        session_id = check_signature(app.state.settings.session_secret, token)

        response = await client.post("/-/logout")
        assert response.status_code == 303
        assert response.headers["location"] == CONTENT_LOGOUT_LEG
        # Gone server-side, not only the cookie.
        assert app.state.session_store.get_session(session_id) is None

        headers_ = " ".join(response.headers.get_list("set-cookie"))
        assert SESSION_COOKIE + "=" in headers_
        assert CSRF_COOKIE + "=" in headers_

    async def test_logout_without_session_is_harmless(self, client):
        response = await client.post("/-/logout")
        assert response.status_code == 303

    async def test_the_spa_form_from_the_admin_origin_logs_out(self, client, app, idp):
        """What the browser really sends for the hidden form in App.vue."""
        await complete_login(client, idp)
        session_id = check_signature(app.state.settings.session_secret, client.cookies.get(SESSION_COOKIE))

        response = await client.post(
            "/-/logout", headers={"Origin": APP_BASE_URL, "Sec-Fetch-Site": "same-origin"}
        )
        assert response.status_code == 303
        assert app.state.session_store.get_session(session_id) is None

    async def test_a_form_on_the_content_host_cannot_end_the_admin_session(self, client, app, idp):
        """The content origin is same-site with the admin origin, so
        SameSite=Strict sends the session cookie along and POST alone is no
        guard."""
        await complete_login(client, idp)
        session_id = check_signature(app.state.settings.session_secret, client.cookies.get(SESSION_COOKIE))

        response = await client.post(
            "/-/logout", headers={"Origin": CONTENT_BASE_URL, "Sec-Fetch-Site": "same-site"}
        )
        assert response.status_code == 403
        assert app.state.session_store.get_session(session_id) is not None


class TestContentLogout:
    async def test_the_content_leg_ends_that_session_and_returns_to_admin(self, idp):
        app = make_app(make_settings(idp, base_url="https://beheer.plak.example"), idp)
        async with make_content_test_client(app) as content_client:
            await _complete_content_login(content_client, idp)
            session_id = check_signature(
                app.state.settings.session_secret, content_client.cookies.get(CONTENT_ANCHOR_COOKIE)
            )

            response = await content_client.get("/-/logout?from=beheer")

        assert response.status_code == 303
        assert response.headers["location"] == "https://beheer.plak.example/"
        assert app.state.session_store.get_session(session_id) is None
        assert CONTENT_ANCHOR_COOKIE + "=" in " ".join(response.headers.get_list("set-cookie"))

    async def test_a_post_logout_on_the_content_host_is_the_content_logout(self, content_client, app, idp):
        """The logout route is shared between hosts; on the content host a POST
        takes the same branch as the GET the content leg normally arrives on."""
        await _complete_content_login(content_client, idp)
        session_id = check_signature(
            app.state.settings.session_secret, content_client.cookies.get(CONTENT_ANCHOR_COOKIE)
        )

        response = await content_client.post("/-/logout")

        assert response.status_code == 303
        assert response.headers["location"] == "/"
        assert app.state.session_store.get_session(session_id) is None

    async def test_a_direct_content_logout_stays_on_the_content_host(self, content_client, idp):
        await _complete_content_login(content_client, idp)
        response = await content_client.get("/-/logout")
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    async def test_from_is_a_token_not_an_address(self, content_client):
        """Anything but the fixed value lands on the content host's root, so the
        parameter cannot be turned into an open redirect."""
        for value in ("https://evil.example/", "//evil.example", "beheer.evil"):
            response = await content_client.get("/-/logout", params={"from": value})
            assert response.headers["location"] == "/", value

    async def test_logging_out_of_admin_leaves_no_content_session_behind(self, client, content_client, app, idp):
        """The whole chain, both legs: after it, neither session exists."""
        await complete_login(client, idp)
        await _complete_content_login(content_client, idp)
        content_id = check_signature(
            app.state.settings.session_secret, content_client.cookies.get(CONTENT_ANCHOR_COOKIE)
        )

        first = await client.post("/-/logout")
        assert first.headers["location"] == CONTENT_LOGOUT_LEG
        await content_client.get("/-/logout?from=beheer")

        assert app.state.session_store.get_session(content_id) is None


class TestRpInitiatedLogout:
    @pytest_asyncio.fixture
    async def rp_client(self, idp):
        idp.end_session_supported = True
        app = make_app(make_settings(idp, oidc_rp_logout=True), idp)
        async with make_test_client(app) as client:
            yield client

    async def test_with_the_flag_on_the_idp_comes_first_and_returns_to_the_content_leg(self, rp_client, idp):
        await complete_login(rp_client, idp)
        response = await rp_client.post("/-/logout")

        location = httpx.URL(response.headers["location"])
        assert str(location).startswith(idp.issuer + "/endsession?")
        params = dict(location.params)
        assert params["post_logout_redirect_uri"] == CONTENT_LOGOUT_LEG
        assert params["client_id"] == idp.client_id
        assert params["id_token_hint"]

    async def test_without_an_end_session_endpoint_the_chain_skips_the_idp(self, idp):
        app = make_app(make_settings(idp, oidc_rp_logout=True), idp)
        async with make_test_client(app) as client:
            await complete_login(client, idp)
            response = await client.post("/-/logout")
        assert response.headers["location"] == CONTENT_LOGOUT_LEG

    async def test_the_flag_is_off_by_default(self, client, idp):
        idp.end_session_supported = True
        await complete_login(client, idp)
        response = await client.post("/-/logout")
        assert response.headers["location"] == CONTENT_LOGOUT_LEG


@pytest.fixture
def audit(app):
    return install_audit_recorder(app)


class TestAuthenticationAudit:
    """login and logout as they land in the audit log (BIO2 5.17.01)."""

    async def test_successful_login_records_the_member(self, client, idp, audit):
        await complete_login(client, idp)
        record = audit.only()
        assert record.action == vocabulary.LOGIN
        assert record.result == vocabulary.ALLOWED
        assert record.actor.kind is ActorKind.MEMBER
        assert record.actor.identifier == "gebruiker-1"
        assert record.refs == {"kind": "admin"}
        assert record.reason_code is None
        assert record.ip

    async def test_refused_callback_records_without_an_actor(self, client, idp, audit):
        await start_login(client, idp)
        response = await client.get(
            "/-/oauth2/callback", params={"code": "code-123", "state": "vervalste-state"}
        )
        assert response.status_code == 400
        record = audit.only()
        assert record.result == vocabulary.REFUSED
        assert record.reason_code == vocabulary.LOGIN_STATE_MISMATCH
        assert record.actor.kind is ActorKind.ANONYMOUS
        assert record.actor.identifier is None

    async def test_refused_callback_records_no_value_that_tripped_it(self, client, idp, audit):
        """BIO2 8.15.02: the value that tripped the check never goes into the record."""
        await start_login(client, idp)
        response = await client.get(
            "/-/oauth2/callback",
            params={"code": "code-xyz", "state": "vervalste-state-abc"},
        )
        assert response.status_code == 400
        record = audit.only()
        assert "vervalste-state-abc" not in repr(record)
        assert "code-xyz" not in repr(record)

    async def test_a_nonce_mismatch_is_recorded_as_token_invalid(self, client, idp, audit):
        """There is no dedicated NONCE_MISMATCH code: the id-token layer keeps
        the coarse default reason for every claim check but iss, acr and
        at_hash."""
        q = await start_login(client, idp)
        idp.next_nonce = "een-andere-nonce-dan-de-poging"
        response = await client.get(
            "/-/oauth2/callback", params={"code": "code-123", "state": q["state"], "iss": idp.issuer}
        )
        assert response.status_code == 400
        record = audit.only()
        assert record.reason_code == vocabulary.LOGIN_TOKEN_INVALID

    async def test_missing_iss_has_its_own_reason(self, idp):
        app = make_app(make_settings(idp, oidc_iss_required=True), idp)
        recorder = install_audit_recorder(app)
        async with make_test_client(app) as client:
            await complete_login(client, idp, send_iss=False)
        record = recorder.only()
        assert record.reason_code == vocabulary.LOGIN_ISS_MISMATCH

    async def test_content_login_records_the_content_flow(self, content_client, idp, audit):
        await _complete_content_login(content_client, idp)
        record = audit.only()
        assert record.refs == {"kind": "content"}

    async def test_logout_records_the_member(self, client, idp, audit):
        await complete_login(client, idp)
        audit.records.clear()
        await client.post("/-/logout")
        record = audit.only()
        assert record.action == vocabulary.LOGOUT
        assert record.result == vocabulary.ALLOWED
        assert record.actor.identifier == "gebruiker-1"

    async def test_logout_without_session_records_nothing(self, client, audit):
        await client.post("/-/logout")
        assert audit.records == []

    async def test_unreachable_idp_on_login_start_is_recorded(self, idp):
        app = make_app(make_settings(idp, oidc_issuer="https://idp.example/anders"), idp)
        recorder = install_audit_recorder(app)
        async with make_test_client(app) as client:
            response = await client.get("/-/login")
        assert response.status_code == 502
        record = recorder.only()
        assert record.result == vocabulary.REFUSED
        assert record.reason_code == vocabulary.LOGIN_IDP_UNREACHABLE


def _request_with_cookies(app, cookie_header: str):
    """A bare Request with these cookies, to exercise the session derivation
    itself. The landing does not give the difference away: it forwards the
    logged-in and the anonymous visitor alike to the SPA."""
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", cookie_header.encode())],
            "app": app,
            "query_string": b"",
        }
    )


class TestSession:
    """The root of the admin host has no page of its own: the SPA sits
    there and Start.vue picks between the landing and the overview. What the
    root serves is tested in test_spa.py, which has a built SPA to serve."""

    async def test_forged_session_cookie_counts_not_as_logged_in(self, app):
        request = _request_with_cookies(app, f"{SESSION_COOKIE}=vervalst.abcdef")
        assert session_from_request(request) is None

    @pytest.mark.parametrize("cookie", [SESSION_COOKIE, CONTENT_SESSION_COOKIE, CONTENT_ANCHOR_COOKIE])
    async def test_a_non_ascii_session_cookie_counts_as_no_session(self, app, cookie):
        request = _request_with_cookies(app, f"{cookie}=é.x")
        assert session_from_request(request) is None
        assert content_session_from_request(request) is None
        assert content_anchor_session_from_request(request) is None


class TestRobots:
    async def test_robots_txt_shuts_out_the_whole_admin_host(self, client):
        # The SPA sits at the root now, so there is no prefix left to name:
        # the host as a whole is off limits to crawlers.
        response = await client.get("/robots.txt")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert response.text == "User-agent: *\nDisallow: /\n"


class TestRedirectUri:
    async def test_falls_back_to_request_when_base_url_missing(self, client, idp):
        q = await start_login(client, idp)
        assert q["redirect_uri"] == "https://plak.example/-/oauth2/callback"

    async def test_used_base_url_instead_of_the_host_header(self, idp):
        settings = make_settings(idp, base_url="https://prod.plak.nl")
        app = make_app(settings, idp)
        async with make_test_client(app) as client:
            q = await start_login(client, idp)
        assert q["redirect_uri"] == "https://prod.plak.nl/-/oauth2/callback"

    async def test_content_login_used_content_base_url(self, idp):
        settings = make_settings(idp, base_url="https://beheer.plak.nl")
        app = make_app(settings, idp)
        async with make_content_test_client(app) as content_client:
            q = await start_login(content_client, idp, path_login=PATH_CONTENT_LOGIN)
        assert q["redirect_uri"] == CONTENT_BASE_URL + "/-/oauth2/callback"


async def _complete_content_login(client, idp, **kwargs):
    return await complete_login(
        client, idp, path_login=PATH_CONTENT_LOGIN, path_callback=CONTENT_CALLBACK, **kwargs
    )


class TestContentLogin:
    """Content-origin login (spec §4a): the same OIDC flow, but the outcome is
    a content session without admin authority. The app in these tests has no
    DB: that a login succeeds proves no lid record is created (spec §7)."""

    async def test_login_sets_own_login_cookie_and_pkce_state_nonce(self, content_client, idp):
        response = await content_client.get(PATH_CONTENT_LOGIN, params={"returnTo": "/fin/rapport/"})
        assert response.status_code == 302
        q = dict(httpx.URL(response.headers["location"]).params)
        assert q["code_challenge_method"] == "S256"
        assert q["state"] and q["nonce"] and q["code_challenge"]
        login_header = _set_cookie_header(response, CONTENT_LOGIN_COOKIE).lower()
        assert "httponly" in login_header
        assert "secure" in login_header
        assert "samesite=lax" in login_header
        assert "path=/" in login_header
        assert content_client.cookies.get(LOGIN_COOKIE) is None

    async def test_callback_sets_content_session_cookie_with_correct_attributes(self, content_client, app, idp):
        response = await _complete_content_login(content_client, idp, return_to="/fin/rapport/index.html?x=1")
        assert response.status_code == 303
        assert response.headers["location"] == "/fin/rapport/index.html?x=1"

        header = _set_cookie_header(response, CONTENT_SESSION_COOKIE).lower()
        assert "httponly" in header
        assert "secure" in header
        # SameSite=none, because a sandboxed page has an opaque origin and is
        # cross-site with its own site; Secure comes with it.
        assert "samesite=none" in header
        # Scoped to the site the visitor was heading to, never wider.
        assert "path=/fin/rapport/" in header
        assert "domain=" not in header
        anchor = _set_cookie_header(response, CONTENT_ANCHOR_COOKIE).lower()
        assert "httponly" in anchor
        assert "secure" in anchor
        # The anchor stays Lax: login, callback and logout are top-level
        # navigations, and this is the cookie that mints site cookies.
        assert "samesite=lax" in anchor
        assert "path=/-/" in anchor
        # No admin session and no CSRF cookie: the content origin has no
        # session-borne mutations.
        headers_ = response.headers.get_list("set-cookie")
        assert not any(k.startswith(SESSION_COOKIE + "=") for k in headers_)
        assert not any(k.startswith(CSRF_COOKIE + "=") for k in headers_)
        # The login cookie has been cleaned up.
        assert any(k.startswith(CONTENT_LOGIN_COOKIE + "=") and "max-age=0" in k.lower() for k in headers_)

        token = content_client.cookies.get(CONTENT_SESSION_COOKIE)
        session_id = check_signature(app.state.settings.session_secret, token)
        session = app.state.session_store.get_session(session_id)
        assert session is not None
        assert session.kind is SessionKind.CONTENT
        assert session.sub == "gebruiker-1"
        assert session.email_verified is True

    @pytest.mark.parametrize("claim", ["false", "true", OMIT], ids=["string-false", "string-true", "missing"])
    async def test_only_a_boolean_true_email_verified_counts(self, content_client, app, idp, claim):
        idp.token_claim_overrides = {"email_verified": claim}
        await _complete_content_login(content_client, idp)
        token = content_client.cookies.get(CONTENT_ANCHOR_COOKIE)
        session_id = check_signature(app.state.settings.session_secret, token)
        assert app.state.session_store.get_session(session_id).email_verified is False

    async def test_a_return_to_that_is_not_a_site_path_gets_no_site_cookie(self, content_client, idp):
        """A `;` in the returnTo would otherwise reach the cookie Path and widen
        the SameSite=None session cookie to the whole host."""
        response = await _complete_content_login(
            content_client, idp, return_to="/x;Path=/;Domain=example.org/y/"
        )
        assert response.status_code == 303
        headers_ = response.headers.get_list("set-cookie")
        assert not any(k.startswith(CONTENT_SESSION_COOKIE + "=") for k in headers_)
        assert not any("domain=" in k.lower() for k in headers_)
        # The anchor still arrives: the login itself succeeded.
        assert "path=/-/" in _set_cookie_header(response, CONTENT_ANCHOR_COOKIE).lower()

    async def test_returnto_invalid_falls_back_to_root(self, content_client, idp):
        response = await _complete_content_login(content_client, idp, return_to="//evil.example")
        assert response.status_code == 303
        assert response.headers["location"] == "/"
        response = await _complete_content_login(content_client, idp)
        assert response.headers["location"] == "/"

    async def test_content_session_counts_not_as_admin_session(self, content_client, app, idp):
        await _complete_content_login(content_client, idp, return_to="/fin/rapport/")
        # To the admin side a content session is anonymous.
        cookie_header = "; ".join(f"{name}={value}" for name, value in content_client.cookies.items())
        assert session_from_request(_request_with_cookies(app, cookie_header)) is None

    async def test_admin_session_counts_not_as_content_session(self, app):
        from starlette.requests import Request

        async with make_test_client(app) as client:
            session = set_session_cookie(client, app)
            # The same signed id in the content cookie: the kind does not match.
            client.cookies.set(
                CONTENT_SESSION_COOKIE,
                sign(app.state.settings.session_secret, session.id),
                domain="plak.example",
                path="/",
            )
            cookie_header = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", cookie_header.encode())],
            "app": app,
            "query_string": b"",
        }
        request = Request(scope)
        assert session_from_request(request) is session
        assert content_session_from_request(request) is None

    async def test_content_session_only_via_content_cookie(self, app):
        from starlette.requests import Request

        async with make_test_client(app) as client:
            session = set_content_session_cookie(client, app, sites=("/fin/rapport/",))
            client.cookies.set(
                SESSION_COOKIE,
                sign(app.state.settings.session_secret, session.id),
                domain="plak.example",
                path="/",
            )
            cookie_header = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/fin/rapport/",
            "headers": [(b"cookie", cookie_header.encode())],
            "app": app,
            "query_string": b"",
        }
        request = Request(scope)
        assert content_session_from_request(request) is session
        assert session_from_request(request) is None

    async def test_admin_attempt_cannot_on_content_callback_are_redeemed(self, client, content_client, idp):
        q = await start_login(client, idp)
        login_token = client.cookies.get(LOGIN_COOKIE)
        # The same attempt, offered as the content login cookie on the content callback.
        content_client.cookies.set(CONTENT_LOGIN_COOKIE, login_token, domain="content.plak.example", path="/")
        response = await content_client.get(
            CONTENT_CALLBACK, params={"code": "code-123", "state": q["state"], "iss": idp.issuer}
        )
        assert response.status_code == 400
        assert idp.token_requests == []
        assert content_client.cookies.get(CONTENT_SESSION_COOKIE) is None

    async def test_content_attempt_cannot_on_admin_callback_are_redeemed(self, client, content_client, idp):
        q = await start_login(content_client, idp, path_login=PATH_CONTENT_LOGIN)
        login_token = content_client.cookies.get(CONTENT_LOGIN_COOKIE)
        client.cookies.set(LOGIN_COOKIE, login_token, domain="plak.example", path="/")
        response = await client.get(
            "/-/oauth2/callback", params={"code": "code-123", "state": q["state"], "iss": idp.issuer}
        )
        assert response.status_code == 400
        assert idp.token_requests == []
        assert client.cookies.get(SESSION_COOKIE) is None

    async def test_content_session_rotates_on_new_login(self, content_client, app, idp):
        secret = app.state.settings.session_secret
        await _complete_content_login(content_client, idp, return_to="/fin/")
        first = check_signature(secret, content_client.cookies.get(CONTENT_ANCHOR_COOKIE))
        await _complete_content_login(content_client, idp, return_to="/fin/")
        second_one = check_signature(secret, content_client.cookies.get(CONTENT_ANCHOR_COOKIE))
        assert first != second_one
        assert app.state.session_store.get_session(first) is None
        assert app.state.session_store.get_session(second_one) is not None

    async def test_admin_login_lets_content_session_stand(self, client, content_client, app, idp):
        # Rotation is per kind: an admin login does not touch the content session.
        await _complete_content_login(content_client, idp, return_to="/fin/")
        content_id = check_signature(
            app.state.settings.session_secret, content_client.cookies.get(CONTENT_ANCHOR_COOKIE)
        )
        await complete_login(client, idp)
        assert app.state.session_store.get_session(content_id) is not None


class TestContentCookiePerSite:
    """The content session cookie is scoped to one `/{group}/{site}/`, so
    opening a second site arrives at the login without one. With the anchor
    session still valid that costs a redirect and no login."""

    NAVIGATION: ClassVar[dict[str, str]] = {"Sec-Fetch-Dest": "document"}

    async def test_a_second_site_gets_a_cookie_without_touching_the_idp(self, content_client, app, idp):
        await _complete_content_login(content_client, idp, return_to="/fin/rapport/")
        first = check_signature(
            app.state.settings.session_secret, content_client.cookies.get(CONTENT_ANCHOR_COOKIE)
        )
        idp.token_requests.clear()

        response = await content_client.get(
            PATH_CONTENT_LOGIN, params={"returnTo": "/fin/jaarverslag/"}, headers=self.NAVIGATION
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/fin/jaarverslag/"
        assert idp.token_requests == []
        header = _set_cookie_header(response, CONTENT_SESSION_COOKIE).lower()
        assert "path=/fin/jaarverslag/" in header
        # The same session, one cookie more: nothing rotated, nothing revoked.
        minted = _cookie_value(response, CONTENT_SESSION_COOKIE)
        assert check_signature(app.state.settings.session_secret, minted) == first
        assert app.state.session_store.get_session(first) is not None

    async def test_a_subresource_gets_no_cookie_out_of_the_login(self, content_client, idp):
        """The mint plus the redirect back would otherwise be a two-hop way
        for a page on one site to have the browser attach a session to a
        request aimed at another."""
        await _complete_content_login(content_client, idp, return_to="/fin/rapport/")
        response = await content_client.get(
            PATH_CONTENT_LOGIN,
            params={"returnTo": "/fin/jaarverslag/"},
            headers={"Sec-Fetch-Dest": "empty", "Sec-Fetch-Site": "same-origin"},
        )
        assert response.status_code == 302
        assert response.headers["location"].startswith(idp.issuer + "/authorize?")

    async def test_without_a_session_the_login_is_the_ordinary_one(self, content_client, idp):
        response = await content_client.get(
            PATH_CONTENT_LOGIN, params={"returnTo": "/fin/jaarverslag/"}, headers=self.NAVIGATION
        )
        assert response.status_code == 302
        assert response.headers["location"].startswith(idp.issuer + "/authorize?")

    async def test_a_return_to_outside_a_site_mints_nothing(self, content_client, idp):
        await _complete_content_login(content_client, idp, return_to="/fin/rapport/")
        response = await content_client.get(PATH_CONTENT_LOGIN, params={"returnTo": "/"}, headers=self.NAVIGATION)
        assert response.status_code == 302
        assert response.headers["location"].startswith(idp.issuer + "/authorize?")

    async def test_a_return_to_with_cookie_attributes_mints_nothing(self, content_client, idp):
        await _complete_content_login(content_client, idp, return_to="/fin/rapport/")
        response = await content_client.get(
            PATH_CONTENT_LOGIN,
            params={"returnTo": "/x;Path=/;Domain=example.org/y/"},
            headers=self.NAVIGATION,
        )
        assert response.status_code == 302
        assert response.headers["location"].startswith(idp.issuer + "/authorize?")
        assert not any(
            k.startswith(CONTENT_SESSION_COOKIE + "=") for k in response.headers.get_list("set-cookie")
        )

    async def test_the_logout_clears_every_site_cookie_it_handed_out(self, content_client, app, idp):
        await _complete_content_login(content_client, idp, return_to="/fin/rapport/")
        await content_client.get(
            PATH_CONTENT_LOGIN, params={"returnTo": "/fin/jaarverslag/"}, headers=self.NAVIGATION
        )
        session_id = check_signature(
            app.state.settings.session_secret, content_client.cookies.get(CONTENT_ANCHOR_COOKIE)
        )

        response = await content_client.get("/-/logout")

        cleared = {
            (k.split("=", 1)[0], k.lower().split("path=", 1)[1].split(";", 1)[0])
            for k in response.headers.get_list("set-cookie")
            if "max-age=0" in k.lower()
        }
        assert (CONTENT_ANCHOR_COOKIE, "/-/") in cleared
        assert (CONTENT_SESSION_COOKIE, "/fin/rapport/") in cleared
        assert (CONTENT_SESSION_COOKIE, "/fin/jaarverslag/") in cleared
        assert app.state.session_store.get_session(session_id) is None
        assert content_client.cookies.get(CONTENT_SESSION_COOKIE) is None

    async def test_a_site_cookie_that_outlives_the_logout_opens_nothing(self, content_client, app, idp):
        """Whatever a browser does with the deletions, the session behind the
        id is gone, and a cookie without a session is an anonymous visitor."""
        await _complete_content_login(content_client, idp, return_to="/fin/rapport/")
        token = content_client.cookies.get(CONTENT_SESSION_COOKIE)
        await content_client.get("/-/logout")

        content_client.cookies.set(
            CONTENT_SESSION_COOKIE, token, domain="content.plak.example", path="/fin/rapport/"
        )
        cookie_header = f"{CONTENT_SESSION_COOKIE}={token}"
        assert content_session_from_request(_request_with_cookies(app, cookie_header)) is None


class TestContentViewerUpsert:
    """The content_viewers upsert (auth/content_viewers.py) is called on a
    content-host login and not on an admin login. The apps in this module
    have no DB, so the wiring is checked with a monkeypatched upsert rather
    than a real row (test_content_viewers.py covers the upsert itself)."""

    async def test_content_login_upserts_the_viewer(self, content_client, app, idp, monkeypatch):
        calls = []

        async def _record(session_factory, sub, email, email_verified):
            calls.append((sub, email, email_verified))

        app.state.session_factory = object()
        monkeypatch.setattr(pages, "upsert_content_viewer", _record)
        await _complete_content_login(content_client, idp)
        assert calls == [("gebruiker-1", "gebruiker@example.nl", True)]

    async def test_admin_login_does_not_upsert_a_viewer(self, client, app, idp, monkeypatch):
        calls = []

        async def _record(session_factory, sub, email, email_verified):
            calls.append((sub, email, email_verified))

        app.state.session_factory = object()
        monkeypatch.setattr(pages, "upsert_content_viewer", _record)
        await complete_login(client, idp)
        assert calls == []

    async def test_a_failed_upsert_does_not_block_the_login(self, content_client, app, idp, monkeypatch, caplog):
        async def _boom(session_factory, sub, email, email_verified):
            raise RuntimeError("content_viewers broken")

        app.state.session_factory = object()
        monkeypatch.setattr(pages, "upsert_content_viewer", _boom)
        with caplog.at_level("ERROR"):
            response = await _complete_content_login(content_client, idp)
        assert response.status_code == 303
        assert "content_viewers" in caplog.text


class TestCleanup:
    def test_expired_session_and_attempt_are_deleted(self):
        store = SessionStore()
        session = store.create_session(sub="s", email=None, email_verified=False, acr="acr")
        attempt = store.create_attempt(state="s", nonce="n", code_verifier="v", return_to="/admin")

        # Frozen dataclasses: age them artificially by replacing the stored
        # instance instead of mutating an attribute.
        store._sessions[session.id] = dataclasses.replace(
            session, created_at=datetime.now(UTC) - MAX_SESSION_AGE - timedelta(seconds=1)
        )
        store._attempts[attempt.id] = dataclasses.replace(
            attempt, created_at=datetime.now(UTC) - MAX_LOGIN_ATTEMPT_AGE - timedelta(seconds=1)
        )

        deleted = store.cleanup()

        assert deleted == 2
        assert session.id not in store._sessions
        assert attempt.id not in store._attempts

    def test_not_expired_entities_stay_stand(self):
        store = SessionStore()
        session = store.create_session(sub="s", email=None, email_verified=False, acr="acr")

        deleted = store.cleanup()

        assert deleted == 0
        assert session.id in store._sessions

    def test_periodic_cleanup_via_make_attempt(self):
        store = SessionStore()
        session = store.create_session(sub="s", email=None, email_verified=False, acr="acr")
        store._sessions[session.id] = dataclasses.replace(
            session, created_at=datetime.now(UTC) - MAX_SESSION_AGE - timedelta(seconds=1)
        )

        interval = sessions_mod._CLEANUP_INTERVAL
        for _ in range(interval - 2):  # store._aanroepen is already at 1 from maak_sessie above
            store.create_attempt(state="s", nonce="n", code_verifier="v", return_to="/admin")
        assert session.id in store._sessions  # the cleanup threshold is not reached yet

        store.create_attempt(state="s", nonce="n", code_verifier="v", return_to="/admin")
        assert session.id not in store._sessions  # cleanup was triggered automatically


class TestSessionStoreEdgeCases:
    """The store methods on a lookup that misses: a gone or never-existing id
    must be a no-op or a plain None, never an exception."""

    def _store_with_session(self) -> tuple[SessionStore, object]:
        store = SessionStore()
        session = store.create_session(sub="s", email=None, email_verified=False, acr="acr")
        return store, session

    def test_note_content_site_on_an_unknown_session_is_a_no_op(self):
        store = SessionStore()
        store.note_content_site("onbestaande-sessie", "/fin/rapport/")
        assert store._sessions == {}

    def test_note_content_site_does_not_duplicate_an_already_recorded_prefix(self):
        store, session = self._store_with_session()
        store.note_content_site(session.id, "/fin/rapport/")
        store.note_content_site(session.id, "/fin/rapport/")
        assert store.get_session(session.id).content_sites == frozenset({"/fin/rapport/"})

    def test_note_content_site_stops_at_the_ceiling(self):
        """Past _MAX_CONTENT_SITES a further site is not recorded: its cookie
        would only outlive the session, useless from the moment it is gone."""
        store, session = self._store_with_session()
        for i in range(sessions_mod._MAX_CONTENT_SITES):
            store.note_content_site(session.id, f"/groep{i}/site/")
        store.note_content_site(session.id, "/een-teveel/site/")
        recorded = store.get_session(session.id).content_sites
        assert len(recorded) == sessions_mod._MAX_CONTENT_SITES
        assert "/een-teveel/site/" not in recorded

    def test_mark_checked_on_an_unknown_session_returns_none(self):
        store = SessionStore()
        assert store.mark_checked("onbestaande-sessie", refresh_token=None, at=datetime.now(UTC)) is None

    def test_defer_check_on_an_unknown_session_is_a_no_op(self):
        store = SessionStore()
        store.defer_check("onbestaande-sessie", until=datetime.now(UTC))
        assert store._sessions == {}

    def test_sessions_for_logout_without_sid_or_sub_finds_nothing(self):
        """Neither claim on the logout token: nothing to match on, so no
        session is dropped by mistake."""
        store, _ = self._store_with_session()
        assert store.sessions_for_logout(sid=None, sub=None) == []

    def test_get_session_past_max_age_is_gone_and_swept_away(self):
        store, session = self._store_with_session()
        store._sessions[session.id] = dataclasses.replace(
            session, created_at=datetime.now(UTC) - MAX_SESSION_AGE - timedelta(seconds=1)
        )
        assert store.get_session(session.id) is None
        assert session.id not in store._sessions

    def test_take_attempt_unknown_id_returns_none(self):
        store = SessionStore()
        assert store.take_attempt("onbestaande-poging") is None

    def test_take_attempt_past_max_age_returns_none_and_stays_spent(self):
        store = SessionStore()
        attempt = store.create_attempt(state="s", nonce="n", code_verifier="v", return_to="/admin")
        store._attempts[attempt.id] = dataclasses.replace(
            attempt, created_at=datetime.now(UTC) - MAX_LOGIN_ATTEMPT_AGE - timedelta(seconds=1)
        )
        assert store.take_attempt(attempt.id) is None
        assert attempt.id not in store._attempts


def _request_with_headers(app, headers: dict[str, str], cookie_header: str = ""):
    from starlette.requests import Request

    all_headers = dict(headers)
    if cookie_header:
        all_headers["cookie"] = cookie_header
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(k.lower().encode(), v.encode()) for k, v in all_headers.items()],
            "app": app,
            "query_string": b"",
        }
    )


class TestSignKeyCookie:
    def test_roundtrips_with_the_purpose_prefix(self):
        token = sign_key_cookie(SECRET, "sleutel-1")
        # Purpose-tagged, so this signature cannot be replayed as a session
        # or CSRF cookie value even though it uses the same secret.
        assert check_signature(SECRET, token) == "key:sleutel-1"


class TestVisitorFromRequest:
    """The key cookie half of the visitor: content_session_from_request is
    covered elsewhere, this is about _key_id_from_cookie's three outcomes."""

    async def test_no_key_cookie_reports_no_key(self, app):
        visitor = visitor_from_request(_request_with_cookies(app, ""))
        assert visitor.key_cookie is None
        assert visitor.sub is None

    async def test_a_validly_signed_key_cookie_reports_the_key_id(self, app):
        secret = app.state.settings.session_secret
        token = sign_key_cookie(secret, "sleutel-1")
        visitor = visitor_from_request(_request_with_cookies(app, f"{KEY_COOKIE}={token}"))
        assert visitor.key_cookie == "sleutel-1"

    async def test_a_forged_key_cookie_is_reported_invalid_not_absent(self, app):
        """"" (KEY_INVALID) and None (no key at all) are different refusals for
        the gate; a tampered signature must not be read as "no key"."""
        secret = app.state.settings.session_secret
        token = sign_key_cookie(secret, "sleutel-1")
        tampered = token[:-2] + "xx"
        visitor = visitor_from_request(_request_with_cookies(app, f"{KEY_COOKIE}={tampered}"))
        assert visitor.key_cookie == ""

    async def test_a_non_ascii_key_cookie_is_reported_invalid(self, app):
        visitor = visitor_from_request(_request_with_cookies(app, f"{KEY_COOKIE}=é.x"))
        assert visitor.key_cookie == ""

    async def test_a_validly_signed_value_from_another_purpose_is_reported_invalid(self, app):
        """A session cookie value, replayed as the key cookie: the signature
        checks out, but the purpose prefix does not match."""
        secret = app.state.settings.session_secret
        foreign = sign(secret, "een-sessie-id")
        visitor = visitor_from_request(_request_with_cookies(app, f"{KEY_COOKIE}={foreign}"))
        assert visitor.key_cookie == ""


class TestCsrfValid:
    def _session(self):
        store = SessionStore()
        return store.create_session(sub="s", email=None, email_verified=False, acr="acr")

    async def test_missing_header_is_refused(self, app):
        session = self._session()
        request = _request_with_headers(app, {}, cookie_header=f"{CSRF_COOKIE}={session.csrf_token}")
        assert csrf_valid(request, session) is False

    async def test_missing_cookie_is_refused(self, app):
        session = self._session()
        request = _request_with_headers(app, {CSRF_HEADER: session.csrf_token})
        assert csrf_valid(request, session) is False

    async def test_header_and_cookie_disagreeing_is_refused(self, app):
        session = self._session()
        request = _request_with_headers(
            app, {CSRF_HEADER: session.csrf_token}, cookie_header=f"{CSRF_COOKIE}=een-ander-token"
        )
        assert csrf_valid(request, session) is False

    async def test_a_non_ascii_header_is_refused(self, app):
        session = self._session()
        request = _request_with_headers(
            app, {CSRF_HEADER: "é" + session.csrf_token}, cookie_header=f"{CSRF_COOKIE}={session.csrf_token}"
        )
        assert csrf_valid(request, session) is False

    async def test_a_non_ascii_cookie_is_refused(self, app):
        session = self._session()
        request = _request_with_headers(
            app, {CSRF_HEADER: session.csrf_token}, cookie_header=f"{CSRF_COOKIE}=é{session.csrf_token}"
        )
        assert csrf_valid(request, session) is False

    async def test_matching_header_and_cookie_are_accepted(self, app):
        session = self._session()
        request = _request_with_headers(
            app, {CSRF_HEADER: session.csrf_token}, cookie_header=f"{CSRF_COOKIE}={session.csrf_token}"
        )
        assert csrf_valid(request, session) is True
