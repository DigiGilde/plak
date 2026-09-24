"""Tests for auth/sessions.py and platform/pages.py: cookies, rotation,
returnTo validation, logging out, the landing and the content-origin login
with its own session kind (spec §4a). Without a network and without a DB."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from helpers_audit import install_audit_recorder
from helpers_oidc import (
    APP_BASE_URL,
    CONTENT_BASE_URL,
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
    CONTENT_LOGIN_COOKIE,
    CONTENT_SESSION_COOKIE,
    CSRF_COOKIE,
    LOGIN_COOKIE,
    MAX_LOGIN_ATTEMPT_AGE,
    MAX_SESSION_AGE,
    SESSION_COOKIE,
    SessionKind,
    SessionStore,
    check_signature,
    content_session_from_request,
    session_from_request,
    sign,
    valid_return_to,
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
        ],
    )
    def test_invalid_falls_back_to_the_root(self, value):
        # The root of the admin host is where the SPA lives; a returnTo that
        # does not pass validation lands there, never on a foreign origin.
        assert valid_return_to(value) == "/"

    @pytest.mark.parametrize("value", ["/fin/rapport/", "/aurora", "/", "/fin/rapport/index.html?x=1"])
    def test_valid_path_stays(self, value):
        assert valid_return_to(value) == value

    @pytest.mark.parametrize("value", ["//evil.example", "https://evil", "", None, "fin/rapport/"])
    def test_invalid_falls_back_to_given_default(self, value):
        assert valid_return_to(value, "/") == "/"


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
        there is to go on: the same source the front page reads."""
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

    async def test_the_spa_form_from_the_beheer_origin_logs_out(self, client, app, idp):
        """What the browser really sends for the hidden form in App.vue."""
        await complete_login(client, idp)
        session_id = check_signature(app.state.settings.session_secret, client.cookies.get(SESSION_COOKIE))

        response = await client.post(
            "/-/logout", headers={"Origin": APP_BASE_URL, "Sec-Fetch-Site": "same-origin"}
        )
        assert response.status_code == 303
        assert app.state.session_store.get_session(session_id) is None

    async def test_a_form_on_the_content_host_cannot_end_the_beheer_session(self, client, app, idp):
        """The content origin is same-site with the beheer origin, so
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
                app.state.settings.session_secret, content_client.cookies.get(CONTENT_SESSION_COOKIE)
            )

            response = await content_client.get("/-/logout?from=beheer")

        assert response.status_code == 303
        assert response.headers["location"] == "https://beheer.plak.example/"
        assert app.state.session_store.get_session(session_id) is None
        assert CONTENT_SESSION_COOKIE + "=" in " ".join(response.headers.get_list("set-cookie"))

    async def test_a_direct_content_logout_stays_on_the_content_host(self, content_client, idp):
        await _complete_content_login(content_client, idp)
        response = await content_client.get("/-/logout")
        assert response.status_code == 303
        assert response.headers["location"] == "/"

    async def test_from_is_a_token_not_an_address(self, content_client):
        """Anything but the fixed value lands on the content front page, so the
        parameter cannot be turned into an open redirect."""
        for value in ("https://evil.example/", "//evil.example", "beheer.evil"):
            response = await content_client.get("/-/logout", params={"from": value})
            assert response.headers["location"] == "/", value

    async def test_logging_out_of_admin_leaves_no_content_session_behind(self, client, content_client, app, idp):
        """The whole chain, both legs: after it, neither session exists."""
        await complete_login(client, idp)
        await _complete_content_login(content_client, idp)
        content_id = check_signature(
            app.state.settings.session_secret, content_client.cookies.get(CONTENT_SESSION_COOKIE)
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
        assert record.refs == {"kind": vocabulary.SESSION_ADMIN}
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
        assert record.refs == {"kind": vocabulary.SESSION_CONTENT}

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
        assert "samesite=lax" in header
        assert "path=/;" in header or header.endswith("path=/")
        assert "domain=" not in header
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
            session = set_content_session_cookie(client, app)
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
            "path": "/",
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
        first = check_signature(secret, content_client.cookies.get(CONTENT_SESSION_COOKIE))
        await _complete_content_login(content_client, idp, return_to="/fin/")
        second_one = check_signature(secret, content_client.cookies.get(CONTENT_SESSION_COOKIE))
        assert first != second_one
        assert app.state.session_store.get_session(first) is None
        assert app.state.session_store.get_session(second_one) is not None

    async def test_admin_login_lets_content_session_stand(self, client, content_client, app, idp):
        # Rotation is per kind: an admin login does not touch the content session.
        await _complete_content_login(content_client, idp, return_to="/fin/")
        content_id = check_signature(
            app.state.settings.session_secret, content_client.cookies.get(CONTENT_SESSION_COOKIE)
        )
        await complete_login(client, idp)
        assert app.state.session_store.get_session(content_id) is not None


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
            raise RuntimeError("content_viewers kapot")

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
