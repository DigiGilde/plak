"""Tests for auth/oidc.py: the alg allowlist, claim checks, RFC 9207,
private_key_jwt and client_secret_post/basic, the optional acr requirement.
Entirely without a network (httpx.MockTransport)."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time

import httpx
import pytest
from authlib.jose import JsonWebKey, JsonWebToken, RSAKey
from helpers_oidc import CLIENT_SECRET, OMIT, MockIdP, make_oidc_client, make_settings

from plak.audit import vocabulary
from plak.auth.oidc import (
    BACKCHANNEL_LOGOUT_EVENT,
    CLIENT_ASSERTION_TYPE,
    ClientRejectedError,
    IdpUnavailableError,
    OidcClient,
    OidcError,
    RefreshRejectedError,
)
from plak.config import ConfigurationError


@pytest.fixture
def idp() -> MockIdP:
    return MockIdP()


@pytest.fixture
def oidc(idp: MockIdP) -> OidcClient:
    return make_oidc_client(make_settings(idp), idp)


async def validate(oidc: OidcClient, idp: MockIdP, token: str, *, nonce: str = "nonce-1", access_token=None):
    if access_token is None:
        access_token = idp.access_token
    return await oidc.validate_id_token(token, nonce=nonce, access_token=access_token)


class TestIdTokenValidation:
    async def test_valid_token_accepted(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1")
        claims = await validate(oidc, idp, token)
        assert claims["sub"] == "gebruiker-1"
        assert claims["email_verified"] is True

    async def test_hs256_token_refused(self, oidc, idp):
        # A symmetric alg is never allowed, even when the metadata advertises it.
        token = idp.make_id_token(nonce="nonce-1", alg="HS256", key="x" * 48)
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_alg_none_refused(self, oidc, idp):
        header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
        now_ = int(time.time())
        content = {"iss": idp.issuer, "sub": "s", "aud": idp.client_id, "exp": now_ + 600, "iat": now_}
        payload = base64.urlsafe_b64encode(json.dumps(content).encode()).rstrip(b"=").decode()
        with pytest.raises(OidcError):
            await validate(oidc, idp, f"{header}.{payload}.")

    async def test_foreign_key_refused(self, oidc, idp):
        other_one = RSAKey.generate_key(2048, is_private=True)
        token = idp.make_id_token(nonce="nonce-1", key=other_one)
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_missing_acr_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", acr=OMIT)
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_acr_outside_list_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", acr="urn:acr:laag")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_second_acr_from_list_accepted(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", acr="urn:acr:substantieel")
        claims = await validate(oidc, idp, token)
        assert claims["acr"] == "urn:acr:substantieel"

    async def test_wrong_issuer_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", iss="https://kwaadaardig.example")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_wrong_aud_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", aud="andere-client")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_azp_incorrect_on_single_aud_refused(self, oidc, idp):
        # OIDC Core: when present, azp SHOULD match the client_id with a
        # single audience too, not only with multiple audiences.
        token = idp.make_id_token(nonce="nonce-1", azp="andere-client")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_azp_equal_on_client_id_on_single_aud_accepted(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", azp=idp.client_id)
        claims = await validate(oidc, idp, token)
        assert claims["azp"] == idp.client_id

    async def test_wrong_nonce_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="andere-nonce")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_missing_nonce_refused(self, oidc, idp):
        token = idp.make_id_token(nonce=None)
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_expired_token_refused(self, oidc, idp):
        now_ = int(time.time())
        token = idp.make_id_token(nonce="nonce-1", exp=now_ - 3600, iat=now_ - 7200)
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_at_hash_mismatch_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token, access_token="ander-access-token")

    async def test_at_hash_absent_accepted(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", with_at_hash=False)
        claims = await validate(oidc, idp, token)
        assert "at_hash" not in claims

    async def test_at_hash_correct_accepted(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", with_at_hash=True)
        claims = await validate(oidc, idp, token)
        assert "at_hash" in claims

    async def test_at_hash_present_without_access_token_refused(self, oidc, idp):
        # validate() defaults a missing access_token to idp.access_token, so
        # this calls validate_id_token directly to pass a genuine None.
        token = idp.make_id_token(nonce="nonce-1", with_at_hash=True)
        with pytest.raises(OidcError):
            await oidc.validate_id_token(token, nonce="nonce-1", access_token=None)

    async def test_missing_sub_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", sub=OMIT)
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_missing_exp_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", exp=OMIT)
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_missing_iat_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", iat=OMIT)
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_aud_as_list_with_correct_azp_accepted(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", aud=[idp.client_id, "andere-audience"], azp=idp.client_id)
        claims = await validate(oidc, idp, token)
        assert claims["aud"] == [idp.client_id, "andere-audience"]

    async def test_aud_as_list_with_wrong_azp_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", aud=[idp.client_id, "andere-audience"], azp="verkeerde-azp")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)


class TestCallbackIss:
    """RFC 9207: the iss check on the callback query parameters."""

    async def test_mismatch_always_refused(self, oidc, idp):
        metadata = await oidc.metadata()
        with pytest.raises(OidcError):
            oidc.check_callback_iss({"iss": "https://kwaadaardig.example"}, metadata)

    async def test_mismatch_also_refused_without_flag(self, idp):
        oidc = make_oidc_client(make_settings(idp, oidc_iss_required=False), idp)
        metadata = await oidc.metadata()
        with pytest.raises(OidcError):
            oidc.check_callback_iss({"iss": "https://kwaadaardig.example"}, metadata)

    async def test_missing_refused_when_flag_on(self, idp):
        oidc = make_oidc_client(make_settings(idp, oidc_iss_required=True), idp)
        metadata = await oidc.metadata()
        with pytest.raises(OidcError):
            oidc.check_callback_iss({"code": "x", "state": "y"}, metadata)

    async def test_missing_refused_when_metadata_support_advertises(self, idp):
        idp.iss_param_supported = True
        oidc = make_oidc_client(make_settings(idp, oidc_iss_required=False), idp)
        metadata = await oidc.metadata()
        with pytest.raises(OidcError):
            oidc.check_callback_iss({"code": "x", "state": "y"}, metadata)

    async def test_missing_allowed_without_flag_and_without_support(self, oidc, idp):
        metadata = await oidc.metadata()
        oidc.check_callback_iss({"code": "x", "state": "y"}, metadata)

    async def test_correct_iss_accepted(self, idp):
        oidc = make_oidc_client(make_settings(idp, oidc_iss_required=True), idp)
        metadata = await oidc.metadata()
        oidc.check_callback_iss({"iss": idp.issuer}, metadata)


class TestRefusalReasons:
    def test_default_reason_says_only_that_the_id_token_layer_refused(self):
        """A raise-plek without its own reason falls into the neutral bucket,
        not into a code that suggests a cause it did not check."""
        assert OidcError("iets").reason == vocabulary.LOGIN_TOKEN_INVALID


class TestClientConfiguration:
    def test_symmetric_jwk_refused(self, idp):
        jwk = json.dumps({"kty": "oct", "k": base64.urlsafe_b64encode(b"x" * 32).decode()})
        with pytest.raises(ConfigurationError):
            make_oidc_client(make_settings(idp, oidc_client_private_jwk=jwk), idp)

    def test_unparsable_jwk_refused(self, idp):
        with pytest.raises(ConfigurationError):
            make_oidc_client(make_settings(idp, oidc_client_private_jwk="dit-is-geen-json"), idp)

    async def test_metadata_issuer_mismatch_refused(self, idp):
        settings = make_settings(idp, oidc_issuer=idp.issuer)
        idp.issuer = "https://andere-issuer.example"  # metadata now deviates
        # the discovery URL stays the configured issuer; the handler matches on path
        oidc = make_oidc_client(settings, idp)
        with pytest.raises(OidcError):
            await oidc.metadata()

    async def test_metadata_fetch_network_error_refused(self, idp):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        oidc = OidcClient(make_settings(idp), http)
        with pytest.raises(OidcError):
            await oidc.metadata()


PRODUCTION_OVERRIDES = {
    "environment": "productie",
    "trusted_proxies": "10.0.0.0/8",
    "oidc_iss_required": True,
    "base_url": "https://beheer.plak.example",
    "content_base_url": "https://plak.example",
}


class TestEmptyRequiredAcr:
    """An empty PLAK_OIDC_REQUIRED_ACR: no acr check, no acr_values, but a
    startup message (a warning in production)."""

    @pytest.fixture
    def oidc_without_acr(self, idp):
        return make_oidc_client(make_settings(idp, oidc_required_acr=" , "), idp)

    async def test_token_without_acr_accepted_with_empty_acr_in_claims(self, oidc_without_acr, idp):
        token = idp.make_id_token(nonce="nonce-1", acr=OMIT)
        claims = await validate(oidc_without_acr, idp, token)
        assert claims["sub"] == "gebruiker-1"
        assert claims["acr"] == ""

    async def test_token_with_arbitrary_acr_accepted(self, oidc_without_acr, idp):
        token = idp.make_id_token(nonce="nonce-1", acr="0")
        claims = await validate(oidc_without_acr, idp, token)
        assert claims["acr"] == "0"

    async def test_no_acr_values_in_authorization_request(self, oidc_without_acr):
        start = await oidc_without_acr.start_login("https://plak.example/-/oauth2/callback")
        assert "acr_values" not in dict(httpx.URL(start.authorization_url).params)

    async def test_acr_values_does_sent_along_with_list(self, oidc):
        start = await oidc.start_login("https://plak.example/-/oauth2/callback")
        q = dict(httpx.URL(start.authorization_url).params)
        assert q["acr_values"] == "urn:acr:hoog urn:acr:substantieel"

    def test_warning_in_production(self, idp, caplog):
        with caplog.at_level(logging.INFO, logger="plak.auth.oidc"):
            make_oidc_client(
                make_settings(idp, oidc_required_acr="", **PRODUCTION_OVERRIDES), idp
            )
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "PLAK_OIDC_REQUIRED_ACR is leeg" in warnings[0].getMessage()

    def test_info_in_dev(self, idp, caplog):
        with caplog.at_level(logging.INFO, logger="plak.auth.oidc"):
            make_oidc_client(make_settings(idp, oidc_required_acr=""), idp)
        levels = [r.levelno for r in caplog.records if "PLAK_OIDC_REQUIRED_ACR" in r.getMessage()]
        assert levels == [logging.INFO]

    def test_no_message_with_list(self, idp, caplog):
        with caplog.at_level(logging.INFO, logger="plak.auth.oidc"):
            make_oidc_client(make_settings(idp), idp)
        assert not [r for r in caplog.records if "PLAK_OIDC_REQUIRED_ACR" in r.getMessage()]


class TestClientSecret:
    """client_secret_post and client_secret_basic (RFC 6749 §2.3.1) for the
    ZAD Keycloak, which only creates client-secret clients."""

    CALLBACK = "https://plak.example/-/oauth2/callback"

    async def test_client_secret_post_sends_secret_in_body(self, idp):
        oidc = make_oidc_client(make_settings(idp, oidc_client_auth="client_secret_post"), idp)
        idp.next_nonce = "nonce-1"
        tokens = await oidc.exchange_code("code-123", self.CALLBACK, "verifier")
        assert tokens["id_token"]

        request = idp.token_requests[0]
        assert request["grant_type"] == ["authorization_code"]
        assert request["client_id"] == [idp.client_id]
        assert request["client_secret"] == [CLIENT_SECRET]
        assert request["code_verifier"] == ["verifier"]
        assert "client_assertion" not in request
        assert "client_assertion_type" not in request
        assert "authorization" not in idp.token_request_headers[0]

    async def test_client_secret_basic_sends_basic_header(self, idp):
        oidc = make_oidc_client(make_settings(idp, oidc_client_auth="client_secret_basic"), idp)
        idp.next_nonce = "nonce-1"
        tokens = await oidc.exchange_code("code-123", self.CALLBACK, "verifier")
        assert tokens["id_token"]

        request = idp.token_requests[0]
        assert "client_secret" not in request
        assert "client_assertion" not in request
        assert request["code_verifier"] == ["verifier"]

        authorization = idp.token_request_headers[0]["authorization"]
        schema, _, credentials = authorization.partition(" ")
        assert schema == "Basic"
        assert base64.b64decode(credentials).decode("ascii") == f"{idp.client_id}:{CLIENT_SECRET}"

    async def test_client_secret_basic_form_url_encodes_credentials(self):
        idp = MockIdP(client_id="plak client:1")
        settings = make_settings(
            idp, oidc_client_auth="client_secret_basic", oidc_client_secret="ge heim/&=+"
        )
        oidc = make_oidc_client(settings, idp)
        idp.next_nonce = "nonce-1"
        await oidc.exchange_code("code-123", self.CALLBACK, "verifier")

        credentials = idp.token_request_headers[0]["authorization"].removeprefix("Basic ")
        assert base64.b64decode(credentials).decode("ascii") == "plak+client%3A1:ge+heim%2F%26%3D%2B"

    async def test_private_key_jwt_sends_no_secret(self, idp):
        oidc = make_oidc_client(make_settings(idp), idp)
        idp.next_nonce = "nonce-1"
        await oidc.exchange_code("code-123", self.CALLBACK, "verifier")
        request = idp.token_requests[0]
        assert "client_secret" not in request
        assert "authorization" not in idp.token_request_headers[0]

    def test_client_secret_method_loads_no_jwk(self, idp):
        oidc = make_oidc_client(make_settings(idp, oidc_client_auth="client_secret_post"), idp)
        assert oidc.client_auth == "client_secret_post"
        assert oidc._private_jwk is None

    async def test_client_secret_changes_nothing_on_id_token_validation(self, idp):
        oidc = make_oidc_client(make_settings(idp, oidc_client_auth="client_secret_basic"), idp)
        token = idp.make_id_token(nonce="nonce-1", acr="urn:acr:laag")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)


class TestPrivateKeyJwt:
    async def test_exchange_code_sends_valid_client_assertion(self, idp):
        settings = make_settings(idp)
        oidc = make_oidc_client(settings, idp)
        idp.next_nonce = "nonce-1"

        tokens = await oidc.exchange_code("code-123", "https://plak.example/-/oauth2/callback", "verifier")
        assert tokens["id_token"]

        assert len(idp.token_requests) == 1
        request = idp.token_requests[0]
        assert request["grant_type"] == ["authorization_code"]
        assert request["client_assertion_type"] == [CLIENT_ASSERTION_TYPE]
        assert request["code_verifier"] == ["verifier"]

        assertion = request["client_assertion"][0]
        client_jwk = json.loads(settings.oidc_client_private_jwk)
        public_key = JsonWebKey.import_key({k: v for k, v in client_jwk.items() if k in ("kty", "n", "e", "kid")})
        claims = JsonWebToken(["RS256"]).decode(assertion, public_key)
        claims.validate(leeway=60)
        assert claims["iss"] == settings.oidc_client_id
        assert claims["sub"] == settings.oidc_client_id
        assert claims["aud"] == idp.metadata["token_endpoint"]
        assert claims["jti"]

    async def test_pkce_challenge_is_s256_of_verifier(self, idp):
        oidc = make_oidc_client(make_settings(idp), idp)
        start = await oidc.start_login("https://plak.example/-/oauth2/callback")
        q = dict(httpx.URL(start.authorization_url).params)
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(start.code_verifier.encode("ascii")).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        assert q["code_challenge"] == expected
        assert q["code_challenge_method"] == "S256"
        assert q["scope"] == "openid profile email"
        assert q["nonce"] == start.nonce
        assert q["state"] == start.state


class TestTokenResponseNotJson:
    """A 200 whose body is not the JSON object RFC 6749 prescribes (a proxy's
    maintenance page, for instance) must never reach `.json()` unguarded."""

    async def test_exchange_code_refuses_a_non_json_200(self, idp):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=idp.metadata)
            return httpx.Response(200, text="<html>onderhoud</html>")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        oidc = OidcClient(make_settings(idp), http)
        with pytest.raises(OidcError):
            await oidc.exchange_code("code-123", "https://plak.example/-/oauth2/callback", "verifier")

    async def test_exchange_code_refuses_a_200_json_array(self, idp):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=idp.metadata)
            return httpx.Response(200, json=["niet-een-object"])

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        oidc = OidcClient(make_settings(idp), http)
        with pytest.raises(OidcError):
            await oidc.exchange_code("code-123", "https://plak.example/-/oauth2/callback", "verifier")

    async def test_refresh_tokens_treats_a_non_json_200_as_idp_unavailable(self, idp):
        idp.refresh_response_override = httpx.Response(200, text="<html>onderhoud</html>")
        oidc = make_oidc_client(make_settings(idp), idp)
        with pytest.raises(IdpUnavailableError):
            await oidc.refresh_tokens("ververstoken-1")

    async def test_refresh_tokens_treats_a_200_json_array_as_idp_unavailable(self, idp):
        idp.refresh_response_override = httpx.Response(200, json=["niet-een-object"])
        oidc = make_oidc_client(make_settings(idp), idp)
        with pytest.raises(IdpUnavailableError):
            await oidc.refresh_tokens("ververstoken-1")


class TestJwksRefreshOnUnknownKid:
    """The JWKS is refreshed once (with a cooldown) when an id token carries a
    kid that does not appear in the cached JWKS."""

    async def test_key_rotation_succeeds_after_once_refresh(self, oidc, idp):
        await oidc._fetch_keyset()  # caches the (old) JWKS

        new_key = RSAKey.generate_key(2048, is_private=True)
        idp.kid = "idp-sleutel-2"
        idp.private_key = new_key
        public = new_key.as_dict(is_private=False)
        public["kid"] = idp.kid
        idp.jwks = {"keys": [public]}

        token = idp.make_id_token(nonce="nonce-1")
        claims = await validate(oidc, idp, token)
        assert claims["sub"] == "gebruiker-1"

    async def test_permanent_unknown_kid_refused(self, oidc, idp):
        token = idp.make_id_token(nonce="nonce-1", kid_override="nooit-bestaande-kid")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)

    async def test_cooldown_prevents_repeated_jwks_fetch(self, oidc, idp):
        await oidc._fetch_keyset()
        requests_for = idp.jwks_requests

        token = idp.make_id_token(nonce="nonce-1", kid_override="nooit-bestaande-kid")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)
        after_first_attempt = idp.jwks_requests
        assert after_first_attempt == requests_for + 1  # one refresh attempt

        with pytest.raises(OidcError):
            await validate(oidc, idp, token)
        assert idp.jwks_requests == after_first_attempt  # cooldown: no second fetch

    async def test_no_kid_with_multiple_keys_in_jwks_refused(self, idp):
        second_key = RSAKey.generate_key(2048, is_private=True)
        second_public = second_key.as_dict(is_private=False)
        second_public["kid"] = "idp-sleutel-2"
        idp.jwks = {"keys": [*idp.jwks["keys"], second_public]}
        oidc = make_oidc_client(make_settings(idp), idp)

        now_ = int(time.time())
        claims = {
            "iss": idp.issuer,
            "sub": "gebruiker-1",
            "aud": idp.client_id,
            "exp": now_ + 600,
            "iat": now_,
            "acr": "urn:acr:hoog",
            "nonce": "nonce-1",
        }
        # No "kid" in the header: with a single key in the JWKS that key is
        # used regardless, but with two keys the token is ambiguous.
        token = JsonWebToken(["RS256"]).encode({"alg": "RS256"}, claims, idp.private_key).decode("ascii")
        with pytest.raises(OidcError):
            await validate(oidc, idp, token)


class TestEndSessionUrl:
    """RP-initiated logout (OIDC RP-Initiated Logout 1.0 section 2)."""

    REDIRECT = "https://plak.example/"

    async def test_no_end_session_endpoint_returns_none(self, oidc):
        url = await oidc.end_session_url(post_logout_redirect_uri=self.REDIRECT, id_token=None)
        assert url is None

    async def test_endpoint_present_without_id_token(self, idp):
        idp.end_session_supported = True
        oidc = make_oidc_client(make_settings(idp), idp)
        url = await oidc.end_session_url(post_logout_redirect_uri=self.REDIRECT, id_token=None)
        assert url.startswith(idp.issuer + "/endsession?")
        assert "id_token_hint" not in url

    async def test_endpoint_present_with_id_token_hint(self, idp):
        idp.end_session_supported = True
        oidc = make_oidc_client(make_settings(idp), idp)
        url = await oidc.end_session_url(post_logout_redirect_uri=self.REDIRECT, id_token="een-id-token")
        assert "id_token_hint=een-id-token" in url

    async def test_metadata_failure_returns_none(self, idp):
        settings = make_settings(idp, oidc_issuer=idp.issuer)
        idp.issuer = "https://andere-issuer.example"  # metadata now deviates
        oidc = make_oidc_client(settings, idp)
        url = await oidc.end_session_url(post_logout_redirect_uri=self.REDIRECT, id_token=None)
        assert url is None

    async def test_separator_is_ampersand_when_endpoint_already_has_a_query(self, oidc):
        await oidc.metadata()
        oidc._metadata["end_session_endpoint"] = "https://idp.example/endsession?foo=bar"
        url = await oidc.end_session_url(post_logout_redirect_uri=self.REDIRECT, id_token=None)
        assert url.startswith("https://idp.example/endsession?foo=bar&")


class TestJwksFetchFailure:
    async def test_jwks_endpoint_failure_is_oidc_error(self, idp):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=idp.metadata)
            if request.url.path == "/jwks":
                return httpx.Response(500)
            return httpx.Response(404)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        oidc = OidcClient(make_settings(idp), http)
        with pytest.raises(OidcError):
            await oidc._fetch_keyset()


class TestClientAssertionGuard:
    def test_missing_private_jwk_refused(self, idp):
        # Reachable only defensively: __init__ already refuses a
        # private_key_jwt client with no valid JWK, so this calls the
        # private method directly on a client_secret_post client, which
        # never receives a private_jwk.
        oidc = make_oidc_client(make_settings(idp, oidc_client_auth="client_secret_post"), idp)
        with pytest.raises(OidcError):
            oidc._make_client_assertion("https://idp.example/token")

    async def test_assertion_header_omits_kid_when_jwk_has_none(self, idp):
        key = RSAKey.generate_key(2048, is_private=True)
        jwk = key.as_dict(is_private=True)
        jwk.pop("kid", None)  # as_dict adds a thumbprint kid; this test wants none
        settings = make_settings(idp, oidc_client_private_jwk=json.dumps(jwk))
        oidc = make_oidc_client(settings, idp)
        idp.next_nonce = "nonce-1"

        await oidc.exchange_code("code-123", "https://plak.example/-/oauth2/callback", "verifier")

        assertion = idp.token_requests[0]["client_assertion"][0]
        header_b64 = assertion.split(".")[0]
        header_b64 += "=" * (-len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64))
        assert "kid" not in header


class TestExchangeCodeFailures:
    CALLBACK = "https://plak.example/-/oauth2/callback"

    async def test_network_error_is_oidc_error(self, idp):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=idp.metadata)
            raise httpx.ConnectError("boom")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        oidc = OidcClient(make_settings(idp), http)
        with pytest.raises(OidcError):
            await oidc.exchange_code("code-123", self.CALLBACK, "verifier")

    async def test_non_200_status_is_refused(self, idp):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=idp.metadata)
            return httpx.Response(400, json={"error": "invalid_grant"})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        oidc = OidcClient(make_settings(idp), http)
        with pytest.raises(OidcError):
            await oidc.exchange_code("code-123", self.CALLBACK, "verifier")

    async def test_missing_id_token_is_refused(self, idp):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=idp.metadata)
            return httpx.Response(200, json={"access_token": "x"})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        oidc = OidcClient(make_settings(idp), http)
        with pytest.raises(OidcError):
            await oidc.exchange_code("code-123", self.CALLBACK, "verifier")


class TestRefreshTokens:
    async def test_successful_refresh_returns_tokens(self, oidc, idp):
        tokens = await oidc.refresh_tokens("ververstoken-1")
        assert tokens["access_token"] == idp.access_token
        assert "id_token" in tokens


class TestRefreshTokensFailures:
    async def test_metadata_failure_is_idp_unavailable(self, idp):
        settings = make_settings(idp, oidc_issuer=idp.issuer)
        idp.issuer = "https://andere-issuer.example"  # metadata now deviates
        oidc = make_oidc_client(settings, idp)
        with pytest.raises(IdpUnavailableError):
            await oidc.refresh_tokens("ververstoken-1")

    async def test_network_error_is_idp_unavailable(self, idp):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/.well-known/openid-configuration":
                return httpx.Response(200, json=idp.metadata)
            raise httpx.ConnectError("boom")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        oidc = OidcClient(make_settings(idp), http)
        with pytest.raises(IdpUnavailableError):
            await oidc.refresh_tokens("ververstoken-1")

    async def test_invalid_grant_is_hard_failure(self, idp):
        idp.refresh_error = "invalid_grant"
        idp.refresh_status = 400
        oidc = make_oidc_client(make_settings(idp), idp)
        with pytest.raises(RefreshRejectedError):
            await oidc.refresh_tokens("ververstoken-1")

    async def test_client_fault_error_is_client_rejected(self, idp):
        idp.refresh_error = "invalid_client"
        idp.refresh_status = 401
        oidc = make_oidc_client(make_settings(idp), idp)
        with pytest.raises(ClientRejectedError) as excinfo:
            await oidc.refresh_tokens("ververstoken-1")
        assert excinfo.value.code == "invalid_client"

    async def test_unknown_error_is_idp_unavailable(self, idp):
        idp.refresh_error = "server_error"
        idp.refresh_status = 500
        oidc = make_oidc_client(make_settings(idp), idp)
        with pytest.raises(IdpUnavailableError):
            await oidc.refresh_tokens("ververstoken-1")

    async def test_non_json_error_body_is_idp_unavailable(self, idp):
        # response.json() raises inside _error_code; that must not propagate
        # unguarded either.
        idp.refresh_response_override = httpx.Response(500, text="<html>onderhoud</html>")
        oidc = make_oidc_client(make_settings(idp), idp)
        with pytest.raises(IdpUnavailableError):
            await oidc.refresh_tokens("ververstoken-1")

    async def test_error_body_without_error_field_is_idp_unavailable(self, idp):
        idp.refresh_response_override = httpx.Response(400, json={"foo": "bar"})
        oidc = make_oidc_client(make_settings(idp), idp)
        with pytest.raises(IdpUnavailableError):
            await oidc.refresh_tokens("ververstoken-1")


class TestValidateRefreshedIdToken:
    """No nonce, no acr check: only signature, issuer, audience and sub."""

    async def test_valid_token_accepted(self, oidc, idp):
        token = idp.make_id_token(nonce=None, with_at_hash=False)
        claims = await oidc.validate_refreshed_id_token(token)
        assert claims["sub"] == "gebruiker-1"

    async def test_wrong_issuer_refused(self, oidc, idp):
        token = idp.make_id_token(nonce=None, with_at_hash=False, iss="https://kwaadaardig.example")
        with pytest.raises(OidcError):
            await oidc.validate_refreshed_id_token(token)

    async def test_missing_sub_refused(self, oidc, idp):
        token = idp.make_id_token(nonce=None, with_at_hash=False, sub=OMIT)
        with pytest.raises(OidcError):
            await oidc.validate_refreshed_id_token(token)


class TestValidateLogoutToken:
    """OIDC Back-Channel Logout 1.0 section 2.6."""

    async def test_valid_token_with_sid_accepted(self, oidc, idp):
        token = idp.make_logout_token(sid=idp.sid)
        result = await oidc.validate_logout_token(token)
        assert result.sid == idp.sid
        assert result.jti == "logout-token-1"

    async def test_valid_token_with_sub_only_accepted(self, oidc, idp):
        token = idp.make_logout_token(sub="gebruiker-1")
        result = await oidc.validate_logout_token(token)
        assert result.sub == "gebruiker-1"
        assert result.sid is None

    async def test_stale_iat_refused(self, oidc, idp):
        token = idp.make_logout_token(sid=idp.sid, iat=int(time.time()) - 999)
        with pytest.raises(OidcError):
            await oidc.validate_logout_token(token)

    async def test_missing_iat_refused(self, oidc, idp):
        token = idp.make_logout_token(sid=idp.sid, iat=OMIT)
        with pytest.raises(OidcError):
            await oidc.validate_logout_token(token)

    async def test_missing_event_refused(self, oidc, idp):
        token = idp.make_logout_token(sid=idp.sid, with_event=False)
        with pytest.raises(OidcError):
            await oidc.validate_logout_token(token)

    async def test_nonce_present_refused(self, oidc, idp):
        token = idp.make_logout_token(sid=idp.sid, nonce="hoort-hier-niet")
        with pytest.raises(OidcError):
            await oidc.validate_logout_token(token)

    async def test_missing_sub_and_sid_refused(self, oidc, idp):
        token = idp.make_logout_token()
        with pytest.raises(OidcError):
            await oidc.validate_logout_token(token)

    async def test_non_string_jti_becomes_none(self, oidc, idp):
        # make_logout_token's own jti param only ever sets a string claim, so
        # this token is built by hand to get a non-string jti past the IdP.
        now_ = int(time.time())
        claims = {
            "iss": idp.issuer,
            "aud": idp.client_id,
            "iat": now_,
            "sid": idp.sid,
            "jti": 12345,
            "events": {BACKCHANNEL_LOGOUT_EVENT: {}},
        }
        header = {"alg": "RS256", "kid": idp.kid}
        token = JsonWebToken(["RS256"]).encode(header, claims, idp.private_key).decode("ascii")
        result = await oidc.validate_logout_token(token)
        assert result.jti is None
