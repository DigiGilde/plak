"""Tests for ci/tokens.py: JWT shape, RS256-only, issuer allowlist, discovery
and JWKS fetch with issuer/origin checks, key caching and kid refresh,
exp/iat/aud checks. Entirely without a network (httpx.MockTransport)."""

from __future__ import annotations

import asyncio
import base64
import json
import time

import httpx
import pytest
from authlib.jose import RSAKey
from helpers_ci import AUDIENCE, FORGEJO_HOST, FORGEJO_ISSUER, OMIT, MockCi

from plak.audit import vocabulary
from plak.ci.providers import GITHUB_HOST, GITHUB_ISSUER, Issuer
from plak.ci.tokens import (
    MAX_TOKEN_LENGTH,
    CiTokenError,
    CiTokenVerifier,
    VerifiedCiToken,
    looks_like_jwt,
)
from plak.config import Settings
from plak.models.ci import CiProvider

DB_URL = "postgresql+asyncpg://plak:plak@localhost:5432/plak"


def _settings(**overrides) -> Settings:
    base: dict[str, object] = {
        "db_url": DB_URL,
        "content_root": "/onbestaand/plak-content",
        "oidc_issuer": "https://idp.example",
        "oidc_client_id": "plak",
        "oidc_client_private_jwk": "{}",
        "session_secret": "sessie-geheim-van-minstens-32-bytes!",
        "audit_pepper": "audit-pepper-van-minstens-32-bytes!!",
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "environment": "dev",
        "content_base_url": AUDIENCE,
        "base_url": AUDIENCE,
        "ci_forgejo_hosts": FORGEJO_HOST,
    }
    base.update(overrides)
    return Settings(**base)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _verifier(ci: MockCi, *, settings: Settings | None = None, clock=time.monotonic) -> CiTokenVerifier:
    return CiTokenVerifier(settings or _settings(), ci.client(), clock=clock)


class TestLooksLikeJwt:
    def test_three_segments_accepted(self):
        assert looks_like_jwt("abc.def.ghi") is True

    def test_two_segments_refused(self):
        assert looks_like_jwt("abc.def") is False

    def test_empty_segment_refused(self):
        assert looks_like_jwt("abc..ghi") is False

    def test_bad_characters_refused(self):
        assert looks_like_jwt("abc.d f.ghi") is False


class TestValidTokens:
    async def test_valid_github_token_accepted(self):
        ci = MockCi()
        verified = await _verifier(ci).verify(ci.token("github"))
        assert verified.claim("repository") == "minbzk/website"
        assert verified.claim("repository_id") == "1001"

    async def test_valid_forgejo_token_accepted(self):
        ci = MockCi()
        verified = await _verifier(ci).verify(ci.token("forgejo"))
        assert verified.issuer.issuer == FORGEJO_ISSUER


class TestShape:
    async def test_too_long_refused(self):
        ci = MockCi()
        token = "a" * 20000
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID
        assert token not in str(exc.value)

    async def test_not_a_jwt_refused(self):
        ci = MockCi()
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify("not-a-jwt")
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID

    async def test_undecodable_header_refused(self):
        # "YWJj" is valid base64url (decodes to b"abc"), but not valid JSON:
        # this exercises the ValueError path in _b64_json, not looks_like_jwt.
        ci = MockCi()
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify("YWJj.YWJj.sig")
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID

    async def test_undecodable_payload_refused(self):
        ci = MockCi()
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(f"{header}.YWJj.sig")
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID

    async def test_deeply_nested_payload_refused(self):
        """json.loads answers deep nesting with RecursionError rather than
        ValueError, and such a payload fits well inside MAX_TOKEN_LENGTH."""
        ci = MockCi()
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(b"[" * 12000).rstrip(b"=").decode()
        token = f"{header}.{payload}.sig"
        assert len(token) < MAX_TOKEN_LENGTH
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID

    async def test_non_object_header_refused(self):
        ci = MockCi()
        header = base64.urlsafe_b64encode(json.dumps([1, 2]).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({"iss": "x"}).encode()).rstrip(b"=").decode()
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(f"{header}.{payload}.sig")
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID

    async def test_non_object_payload_refused(self):
        ci = MockCi()
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps([1, 2]).encode()).rstrip(b"=").decode()
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(f"{header}.{payload}.sig")
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID


class TestAlgorithm:
    async def test_alg_none_refused_before_key_lookup(self):
        ci = MockCi()
        header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
        now_ = int(time.time())
        payload = base64.urlsafe_b64encode(
            json.dumps({"iss": "https://token.actions.githubusercontent.com", "exp": now_ + 300, "iat": now_}).encode()
        ).rstrip(b"=").decode()
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(f"{header}.{payload}.")
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID
        assert ci.requests == []

    async def test_hs256_refused_before_key_lookup(self):
        ci = MockCi()
        token = ci.token("github", alg="HS256", key=b"x" * 48)
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID
        assert ci.requests == []

    async def test_ps256_refused_before_key_lookup(self):
        ci = MockCi()
        token = ci.token("github", alg="PS256", key=ci.github.key)
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID
        assert ci.requests == []

    async def test_rs512_refused_before_key_lookup(self):
        ci = MockCi()
        token = ci.token("github", alg="RS512", key=ci.github.key)
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID
        assert ci.requests == []


class TestIssuer:
    async def test_unknown_issuer_refused(self):
        ci = MockCi()
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        now_ = int(time.time())
        payload = base64.urlsafe_b64encode(
            json.dumps({"iss": "https://evil.example", "exp": now_ + 300, "iat": now_}).encode()
        ).rstrip(b"=").decode()
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(f"{header}.{payload}.sig")
        assert exc.value.reason == vocabulary.CI_ISSUER_UNKNOWN

    async def test_non_string_issuer_refused(self):
        ci = MockCi()
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        now_ = int(time.time())
        payload = base64.urlsafe_b64encode(json.dumps({"iss": 123, "exp": now_ + 300, "iat": now_}).encode()).rstrip(
            b"="
        ).decode()
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(f"{header}.{payload}.sig")
        assert exc.value.reason == vocabulary.CI_ISSUER_UNKNOWN

    async def test_forgejo_host_not_configured_refused(self):
        ci = MockCi()
        # No forgejo host configured at all: the issuer is unknown up front.
        settings = _settings(ci_forgejo_hosts="")
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci, settings=settings).verify(ci.token("forgejo"))
        assert exc.value.reason == vocabulary.CI_ISSUER_UNKNOWN


class TestSignature:
    async def test_bad_signature_refused(self):
        ci = MockCi()
        other_key = RSAKey.generate_key(2048, is_private=True)
        token = ci.token("github", key=other_key)  # signed with foreign key, right kid in header
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID


class TestTimeClaims:
    async def test_expired_refused(self):
        ci = MockCi()
        now_ = int(time.time())
        token = ci.token("github", exp=now_ - 120, iat=now_ - 300)
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID

    async def test_nbf_in_future_refused(self):
        ci = MockCi()
        now_ = int(time.time())
        token = ci.token("github", nbf=now_ + 300)
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID

    async def test_iat_far_in_future_refused(self):
        ci = MockCi()
        now_ = int(time.time())
        token = ci.token("github", iat=now_ + 300)
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID

    async def test_iat_within_leeway_accepted(self):
        ci = MockCi()
        now_ = int(time.time())
        token = ci.token("github", iat=now_ + 30)
        verified = await _verifier(ci).verify(token)
        assert verified.claim("repository") == "minbzk/website"

    async def test_missing_exp_refused(self):
        ci = MockCi()
        token = ci.token("github", exp=OMIT)
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID

    async def test_missing_iat_refused(self):
        ci = MockCi()
        token = ci.token("github", iat=OMIT)
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID


class TestAudience:
    async def test_mismatch_refused(self):
        ci = MockCi()
        token = ci.token("github", aud="https://elsewhere.example")
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_AUDIENCE_MISMATCH

    async def test_single_element_list_accepted(self):
        ci = MockCi()
        token = ci.token("github", aud=[AUDIENCE])
        verified = await _verifier(ci).verify(token)
        assert verified.claim("repository") == "minbzk/website"

    async def test_two_element_list_refused(self):
        ci = MockCi()
        token = ci.token("github", aud=[AUDIENCE, "https://elsewhere.example"])
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_AUDIENCE_MISMATCH

    async def test_base_url_unset_refused(self):
        ci = MockCi()
        settings = _settings(base_url=None)
        token = ci.token("github")
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci, settings=settings).verify(token)
        assert exc.value.reason == vocabulary.CI_AUDIENCE_MISMATCH


class TestKidSelection:
    async def test_no_kid_with_one_jwks_key_accepted(self):
        ci = MockCi()
        token = ci.token("github", kid=OMIT)
        verified = await _verifier(ci).verify(token)
        assert verified.claim("repository") == "minbzk/website"

    async def test_no_kid_with_two_jwks_keys_refused(self):
        ci = MockCi()
        second_key = RSAKey.generate_key(2048, is_private=True)
        second_public = second_key.as_dict(is_private=False)
        second_public["kid"] = "github-sleutel-2"
        jwks = {"keys": [*ci.github.jwks["keys"], second_public]}
        ci.failures[ci.github.jwks_uri] = httpx.Response(200, json=jwks)
        token = ci.token("github", kid=OMIT)
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID


class TestUnknownKidRefresh:
    async def test_unknown_kid_triggers_one_refetch_then_success(self):
        ci = MockCi()
        verifier = _verifier(ci)
        # Prime the cache with the current (single) key.
        await verifier.verify(ci.token("github"))
        assert ci.requests.count(ci.github.jwks_uri) == 1

        # Rotate: a new key becomes current, but the token still names it by
        # the old kid until we mint one for the new key.
        rotated_key = RSAKey.generate_key(2048, is_private=True)
        ci.github.key = rotated_key
        ci.github.kid = "github-sleutel-2"
        token = ci.token("github")  # signed with the rotated key, new kid
        verified = await verifier.verify(token)
        assert verified.claim("repository") == "minbzk/website"
        assert ci.requests.count(ci.github.jwks_uri) == 2

    async def test_cooldown_prevents_second_refetch(self):
        ci = MockCi()
        clock = FakeClock()
        verifier = _verifier(ci, clock=clock)
        await verifier.verify(ci.token("github"))
        assert ci.requests.count(ci.github.jwks_uri) == 1

        unknown_kid_token = ci.token("github", kid="onbekende-sleutel")
        with pytest.raises(CiTokenError) as exc:
            await verifier.verify(unknown_kid_token)
        assert exc.value.reason == vocabulary.CI_TOKEN_INVALID
        assert ci.requests.count(ci.github.jwks_uri) == 2

        clock.advance(30)
        with pytest.raises(CiTokenError):
            await verifier.verify(unknown_kid_token)
        # Still within the 60s cooldown: no further refetch.
        assert ci.requests.count(ci.github.jwks_uri) == 2

    async def test_refetch_allowed_again_after_cooldown(self):
        ci = MockCi()
        clock = FakeClock()
        verifier = _verifier(ci, clock=clock)
        await verifier.verify(ci.token("github"))
        assert ci.requests.count(ci.github.jwks_uri) == 1

        unknown_kid_token = ci.token("github", kid="onbekende-sleutel")
        with pytest.raises(CiTokenError):
            await verifier.verify(unknown_kid_token)
        assert ci.requests.count(ci.github.jwks_uri) == 2

        clock.advance(61)
        with pytest.raises(CiTokenError):
            await verifier.verify(unknown_kid_token)
        assert ci.requests.count(ci.github.jwks_uri) == 3


class TestKeyCacheTtl:
    async def test_no_refetch_before_ttl(self):
        ci = MockCi()
        clock = FakeClock()
        verifier = _verifier(ci, clock=clock)
        await verifier.verify(ci.token("github"))
        clock.advance(3599)
        await verifier.verify(ci.token("github"))
        assert ci.requests.count(ci.github.jwks_uri) == 1

    async def test_refetch_after_ttl(self):
        ci = MockCi()
        clock = FakeClock()
        verifier = _verifier(ci, clock=clock)
        await verifier.verify(ci.token("github"))
        clock.advance(3601)
        await verifier.verify(ci.token("github"))
        assert ci.requests.count(ci.github.jwks_uri) == 2


class TestDiscoveryUnreachable:
    async def _assert_unreachable(self, ci: MockCi) -> None:
        with pytest.raises(CiTokenError) as exc:
            await _verifier(ci).verify(ci.token("github"))
        assert exc.value.reason == vocabulary.CI_PROVIDER_UNREACHABLE
        assert exc.value.status == 503

    async def test_transport_exception(self):
        ci = MockCi()
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = httpx.ConnectError("verbroken")
        await self._assert_unreachable(ci)

    async def test_discovery_500(self):
        ci = MockCi()
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = 500
        await self._assert_unreachable(ci)

    async def test_discovery_oversize(self):
        ci = MockCi()
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        huge = {"issuer": ci.github.issuer, "jwks_uri": ci.github.jwks_uri, "padding": "x" * (70 * 1024)}
        ci.failures[discovery_url] = httpx.Response(200, json=huge)
        await self._assert_unreachable(ci)

    async def test_discovery_non_json(self):
        ci = MockCi()
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = httpx.Response(200, content=b"not json")
        await self._assert_unreachable(ci)

    async def test_discovery_issuer_mismatch(self):
        ci = MockCi()
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = httpx.Response(
            200, json={"issuer": "https://wrong.example", "jwks_uri": ci.github.jwks_uri}
        )
        await self._assert_unreachable(ci)

    async def test_jwks_uri_other_origin(self):
        ci = MockCi()
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = httpx.Response(
            200, json={"issuer": ci.github.issuer, "jwks_uri": "https://evil.example/jwks"}
        )
        await self._assert_unreachable(ci)

    async def test_jwks_uri_http(self):
        ci = MockCi()
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        http_jwks_uri = ci.github.jwks_uri.replace("https://", "http://")
        ci.failures[discovery_url] = httpx.Response(
            200, json={"issuer": ci.github.issuer, "jwks_uri": http_jwks_uri}
        )
        await self._assert_unreachable(ci)

    async def test_jwks_garbage(self):
        ci = MockCi()
        ci.failures[ci.github.jwks_uri] = httpx.Response(200, json={"keys": [{"kty": "onzin"}]})
        await self._assert_unreachable(ci)

    async def test_stale_keyset_used_when_ttl_refetch_fails(self):
        ci = MockCi()
        clock = FakeClock()
        verifier = _verifier(ci, clock=clock)
        token = ci.token("github")
        await verifier.verify(token)

        clock.advance(3601)
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = httpx.ConnectError("verbroken")
        # The TTL refetch fails, but the stale keyset still validates the
        # (still unexpired) token instead of refusing the deploy.
        stale_token = ci.token("github", exp=int(time.time()) + 300)
        verified = await verifier.verify(stale_token)
        assert verified.claim("repository") == "minbzk/website"


class TestVerifiedCiTokenClaim:
    _issuer = Issuer(CiProvider.GITHUB, GITHUB_HOST, GITHUB_ISSUER)

    def test_bool_claim_is_none(self):
        token = VerifiedCiToken(issuer=self._issuer, claims={"actor_id": True})
        assert token.claim("actor_id") is None

    def test_int_claim_stringified(self):
        token = VerifiedCiToken(issuer=self._issuer, claims={"run_id": 4242})
        assert token.claim("run_id") == "4242"

    def test_empty_string_claim_is_none(self):
        token = VerifiedCiToken(issuer=self._issuer, claims={"ref": ""})
        assert token.claim("ref") is None

    def test_missing_claim_is_none(self):
        token = VerifiedCiToken(issuer=self._issuer, claims={})
        assert token.claim("ref") is None

    def test_string_claim_returned(self):
        token = VerifiedCiToken(issuer=self._issuer, claims={"ref": "refs/heads/main"})
        assert token.claim("ref") == "refs/heads/main"


class TestFetchDiscipline:
    """Single flight per issuer, a negative cache after a failure, and no
    refetch storm while serving a stale keyset."""

    async def test_concurrent_cold_requests_share_one_fetch(self):
        ci = MockCi()

        async def slow(request: httpx.Request) -> httpx.Response:
            # Yield so the other requests really queue on the issuer lock.
            await asyncio.sleep(0.01)
            return ci.handler(request)

        http = httpx.AsyncClient(transport=httpx.MockTransport(slow))
        verifier = CiTokenVerifier(_settings(), http, clock=FakeClock())
        tokens = [ci.token("github") for _ in range(5)]
        results = await asyncio.gather(*(verifier.verify(token) for token in tokens))
        assert len(results) == 5
        assert ci.requests.count(ci.github.jwks_uri) == 1
        assert ci.requests.count(ci.github.issuer + "/.well-known/openid-configuration") == 1

    async def test_a_failed_fetch_is_not_retried_for_thirty_seconds(self):
        ci = MockCi()
        clock = FakeClock()
        verifier = _verifier(ci, clock=clock)
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = 500
        for _ in range(3):
            with pytest.raises(CiTokenError) as exc:
                await verifier.verify(ci.token("github"))
            assert exc.value.reason == vocabulary.CI_PROVIDER_UNREACHABLE
        assert ci.requests.count(discovery_url) == 1

        del ci.failures[discovery_url]
        clock.advance(29)
        with pytest.raises(CiTokenError):
            await verifier.verify(ci.token("github"))
        assert ci.requests.count(discovery_url) == 1
        clock.advance(2)
        await verifier.verify(ci.token("github"))
        assert ci.requests.count(discovery_url) == 2

    async def test_concurrent_requests_during_an_outage_share_one_failed_fetch(self):
        ci = MockCi()
        verifier = _verifier(ci, clock=FakeClock())
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = 503
        results = await asyncio.gather(
            *(verifier.verify(ci.token("github")) for _ in range(4)), return_exceptions=True
        )
        assert all(isinstance(result, CiTokenError) for result in results)
        assert ci.requests.count(discovery_url) == 1

    async def test_a_stale_keyset_is_served_without_a_refetch_per_request(self):
        ci = MockCi()
        clock = FakeClock()
        verifier = _verifier(ci, clock=clock)
        await verifier.verify(ci.token("github"))
        clock.advance(3601)
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = 500
        await verifier.verify(ci.token("github"))
        assert ci.requests.count(discovery_url) == 2
        # Well past the negative cache: the stale keyset got a fresh clock,
        # so nothing is fetched until the TTL runs out again.
        clock.advance(600)
        await verifier.verify(ci.token("github"))
        assert ci.requests.count(discovery_url) == 2

    async def test_an_unknown_kid_during_an_outage_is_503(self):
        ci = MockCi()
        clock = FakeClock()
        verifier = _verifier(ci, clock=clock)
        await verifier.verify(ci.token("github"))
        clock.advance(3601)
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = 500
        await verifier.verify(ci.token("github"))
        # Inside the negative cache: the forced refetch for the unknown kid is
        # not even attempted.
        with pytest.raises(CiTokenError) as exc:
            await verifier.verify(ci.token("github", kid="onbekende-sleutel"))
        assert exc.value.reason == vocabulary.CI_PROVIDER_UNREACHABLE
        assert ci.requests.count(discovery_url) == 2

    async def test_two_unknown_kid_refreshes_share_one_fetch(self):
        ci = MockCi()

        async def slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.01)
            return ci.handler(request)

        verifier = CiTokenVerifier(
            _settings(), httpx.AsyncClient(transport=httpx.MockTransport(slow)), clock=FakeClock()
        )
        await verifier.verify(ci.token("github"))
        ci.github.key = RSAKey.generate_key(2048, is_private=True)
        ci.github.kid = "github-sleutel-2"
        token = ci.token("github")
        issuer = next(iter(verifier._issuers.values()))
        # Both callers force a refresh at once; the second reuses the first's.
        results = await asyncio.gather(
            verifier._keyset(issuer, force=True), verifier._keyset(issuer, force=True)
        )
        assert results[0] is results[1]
        assert ci.requests.count(ci.github.jwks_uri) == 2
        assert (await verifier.verify(token)).claim("repository") == "minbzk/website"

    async def test_after_a_failed_forced_refresh_a_stale_keyset_is_served_from_the_negative_cache(self):
        ci = MockCi()
        clock = FakeClock()
        verifier = _verifier(ci, clock=clock)
        await verifier.verify(ci.token("github"))
        issuer = next(iter(verifier._issuers.values()))
        clock.advance(3601)
        discovery_url = ci.github.issuer + "/.well-known/openid-configuration"
        ci.failures[discovery_url] = 500
        with pytest.raises(CiTokenError):
            await verifier._keyset(issuer, force=True)
        assert ci.requests.count(discovery_url) == 2
        # Stale by TTL, but the failure is fresh: no new attempt, stale keys served.
        assert await verifier._keyset(issuer) is not None
        assert ci.requests.count(discovery_url) == 2
