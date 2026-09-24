"""Tests for ci/providers.py (fetch_json, ProviderClient, valid_name,
known_issuers, host_label) and the PLAK_CI_FORGEJO_HOSTS config validation."""

from __future__ import annotations

import json

import httpx
import pytest
from helpers_ci import FORGEJO_HOST, FORGEJO_ISSUER, MockCi

from plak.ci.providers import (
    GITHUB_API,
    GITHUB_HOST,
    GITHUB_ISSUER,
    FetchError,
    ProviderClient,
    ProviderUnavailableError,
    RepositoryNotFoundError,
    fetch_json,
    host_label,
    known_issuers,
    repository_api_url,
    valid_name,
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
        "content_base_url": "https://plak.example",
    }
    base.update(overrides)
    return Settings(**base)


class TestFetchJson:
    async def test_http_url_refused(self):
        ci = MockCi()
        with pytest.raises(FetchError) as exc:
            await fetch_json(ci.client(), "http://example.com/data.json", max_bytes=1000)
        assert exc.value.status is None

    async def test_redirect_not_followed(self):
        ci = MockCi()
        url = "https://example.com/data.json"
        ci.failures[url] = httpx.Response(301, headers={"location": "https://example.com/elsewhere"})
        with pytest.raises(FetchError) as exc:
            await fetch_json(ci.client(), url, max_bytes=1000)
        assert exc.value.status == 301

    async def test_429_flagged_rate_limited(self):
        ci = MockCi()
        url = "https://example.com/data.json"
        ci.failures[url] = 429
        with pytest.raises(FetchError) as exc:
            await fetch_json(ci.client(), url, max_bytes=1000)
        assert exc.value.rate_limited is True

    async def test_403_with_ratelimit_header_flagged(self):
        ci = MockCi()
        url = "https://example.com/data.json"
        ci.failures[url] = httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
        with pytest.raises(FetchError) as exc:
            await fetch_json(ci.client(), url, max_bytes=1000)
        assert exc.value.rate_limited is True

    async def test_plain_403_not_rate_limited(self):
        ci = MockCi()
        url = "https://example.com/data.json"
        ci.failures[url] = 403
        with pytest.raises(FetchError) as exc:
            await fetch_json(ci.client(), url, max_bytes=1000)
        assert exc.value.rate_limited is False

    async def test_size_cap(self):
        ci = MockCi()
        url = "https://example.com/data.json"
        ci.failures[url] = httpx.Response(200, content=json.dumps({"x": "y" * 5000}).encode())
        with pytest.raises(FetchError):
            await fetch_json(ci.client(), url, max_bytes=100)

    async def test_invalid_json(self):
        ci = MockCi()
        url = "https://example.com/data.json"
        ci.failures[url] = httpx.Response(200, content=b"not json")
        with pytest.raises(FetchError):
            await fetch_json(ci.client(), url, max_bytes=1000)

    async def test_transport_exception_wrapped(self):
        ci = MockCi()
        url = "https://example.com/data.json"
        ci.failures[url] = httpx.ConnectError("verbroken")
        with pytest.raises(FetchError):
            await fetch_json(ci.client(), url, max_bytes=1000)


class TestRepositoryApiUrl:
    def test_github_url(self):
        assert repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website") == (
            GITHUB_API + "/repos/minbzk/website"
        )

    def test_forgejo_url(self):
        assert repository_api_url(CiProvider.FORGEJO, FORGEJO_HOST, "minbzk", "website") == (
            FORGEJO_HOST + "/api/v1/repos/minbzk/website"
        )

    def test_names_percent_quoted(self):
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "my org", "my/repo")
        assert "my%20org" in url
        assert "my%2Frepo" in url


class TestResolve:
    async def test_canonical_casing_returned(self):
        ci = MockCi()
        ci.add_github("MinBZK", "Website", 1001, 2002)
        client = ProviderClient(ci.client())
        resolved = await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        assert resolved.owner == "MinBZK"
        assert resolved.repo == "Website"

    async def test_404_not_found(self):
        ci = MockCi()
        client = ProviderClient(ci.client())
        with pytest.raises(RepositoryNotFoundError):
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "onbestaand")

    async def test_301_not_found(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = 301
        client = ProviderClient(ci.client())
        with pytest.raises(RepositoryNotFoundError):
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")

    async def test_401_not_found(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = 401
        client = ProviderClient(ci.client())
        with pytest.raises(RepositoryNotFoundError):
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")

    async def test_403_not_found(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = 403
        client = ProviderClient(ci.client())
        with pytest.raises(RepositoryNotFoundError):
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")

    async def test_403_rate_limited_unavailable(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={})
        client = ProviderClient(ci.client())
        with pytest.raises(ProviderUnavailableError) as exc:
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        assert exc.value.rate_limited is True

    async def test_429_unavailable(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = 429
        client = ProviderClient(ci.client())
        with pytest.raises(ProviderUnavailableError) as exc:
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        assert exc.value.rate_limited is True

    async def test_500_unavailable(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = 500
        client = ProviderClient(ci.client())
        with pytest.raises(ProviderUnavailableError) as exc:
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        assert exc.value.rate_limited is False

    async def test_transport_exception_unavailable(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = httpx.ConnectError("verbroken")
        client = ProviderClient(ci.client())
        with pytest.raises(ProviderUnavailableError):
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")

    async def test_missing_owner_unavailable(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = httpx.Response(200, json={"id": 1, "name": "website"})
        client = ProviderClient(ci.client())
        with pytest.raises(ProviderUnavailableError):
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")

    async def test_bool_id_unavailable(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = httpx.Response(
            200, json={"id": True, "name": "website", "owner": {"id": 2002, "login": "minbzk"}}
        )
        client = ProviderClient(ci.client())
        with pytest.raises(ProviderUnavailableError):
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")

    async def test_zero_id_unavailable(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = httpx.Response(
            200, json={"id": 0, "name": "website", "owner": {"id": 2002, "login": "minbzk"}}
        )
        client = ProviderClient(ci.client())
        with pytest.raises(ProviderUnavailableError):
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")

    async def test_invalid_owner_name_unavailable(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = httpx.Response(
            200, json={"id": 1001, "name": "website", "owner": {"id": 2002, "login": "min/bzk"}}
        )
        client = ProviderClient(ci.client())
        with pytest.raises(ProviderUnavailableError):
            await client.resolve(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")


class TestStillHasIds:
    async def test_true_and_cached(self):
        ci = MockCi()
        ci.add_github("minbzk", "website", 1001, 2002)
        client = ProviderClient(ci.client())
        assert await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website", 1001, 2002) is True
        request_count = len(ci.requests)
        assert await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website", 1001, 2002) is True
        assert len(ci.requests) == request_count  # served from cache, no new request

    async def test_reasks_after_ttl(self):
        ci = MockCi()
        ci.add_github("minbzk", "website", 1001, 2002)
        clock_value = [0.0]
        client = ProviderClient(ci.client(), clock=lambda: clock_value[0])
        assert await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website", 1001, 2002) is True
        request_count = len(ci.requests)
        clock_value[0] = 301.0
        assert await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website", 1001, 2002) is True
        assert len(ci.requests) == request_count + 1

    async def test_mismatched_repository_id_false_not_cached(self):
        ci = MockCi()
        ci.add_github("minbzk", "website", 9999, 2002)
        client = ProviderClient(ci.client())
        assert await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website", 1001, 2002) is False
        request_count = len(ci.requests)
        # Not cached: asking again makes another request.
        assert await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website", 1001, 2002) is False
        assert len(ci.requests) == request_count + 1

    async def test_mismatched_owner_id_false(self):
        ci = MockCi()
        ci.add_github("minbzk", "website", 1001, 9999)
        client = ProviderClient(ci.client())
        assert await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website", 1001, 2002) is False

    async def test_404_false(self):
        ci = MockCi()
        client = ProviderClient(ci.client())
        assert await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "onbestaand", 1001, 2002) is False

    async def test_unavailable_propagates(self):
        ci = MockCi()
        url = repository_api_url(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website")
        ci.failures[url] = 500
        client = ProviderClient(ci.client())
        with pytest.raises(ProviderUnavailableError):
            await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, "minbzk", "website", 1001, 2002)

    async def test_cache_clears_at_max_entries(self):
        ci = MockCi()
        client = ProviderClient(ci.client())
        for index in range(1024):
            owner = f"owner{index}"
            ci.add_github(owner, "repo", index + 1, 9000)
            assert await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, owner, "repo", index + 1, 9000) is True
        assert len(client._confirmed) <= 1024
        # One more entry: the cache had reached the cap, so it was cleared
        # first and now holds only the new entry.
        ci.add_github("final-owner", "repo", 5000, 9000)
        await client.still_has_ids(CiProvider.GITHUB, GITHUB_HOST, "final-owner", "repo", 5000, 9000)
        assert len(client._confirmed) == 1


class TestValidName:
    @pytest.mark.parametrize("name", ["website", "my-repo_1.x", "a", "A" * 100])
    def test_valid(self, name):
        assert valid_name(name) is True

    @pytest.mark.parametrize("name", ["", "A" * 101, ".", "..", "my/repo", "my repo", "repo?"])
    def test_invalid(self, name):
        assert valid_name(name) is False


class TestKnownIssuers:
    def test_github_always_present(self):
        issuers = known_issuers(_settings())
        assert GITHUB_ISSUER in issuers
        assert issuers[GITHUB_ISSUER].provider == CiProvider.GITHUB

    def test_configured_forgejo_host_present(self):
        settings = _settings(ci_forgejo_hosts=FORGEJO_HOST)
        issuers = known_issuers(settings)
        assert FORGEJO_ISSUER in issuers
        assert issuers[FORGEJO_ISSUER].provider == CiProvider.FORGEJO
        assert issuers[FORGEJO_ISSUER].host == FORGEJO_HOST

    def test_no_forgejo_hosts_configured(self):
        settings = _settings(ci_forgejo_hosts="")
        issuers = known_issuers(settings)
        assert list(issuers) == [GITHUB_ISSUER]


class TestHostLabel:
    def test_strips_scheme(self):
        assert host_label("https://github.com") == "github.com"

    def test_strips_scheme_with_port(self):
        assert host_label("https://code.overheid.nl:8443") == "code.overheid.nl:8443"


class TestCiForgejoHostsConfig:
    def test_default(self):
        settings = _settings()
        assert settings.forgejo_hosts == ("https://code.overheid.nl",)

    def test_trailing_slash_stripped(self):
        settings = _settings(ci_forgejo_hosts="https://code.overheid.nl/")
        assert settings.forgejo_hosts == ("https://code.overheid.nl",)

    def test_uppercase_lowercased(self):
        settings = _settings(ci_forgejo_hosts="https://CODE.OVERHEID.NL")
        assert settings.forgejo_hosts == ("https://code.overheid.nl",)

    def test_port_443_dropped(self):
        settings = _settings(ci_forgejo_hosts="https://code.overheid.nl:443")
        assert settings.forgejo_hosts == ("https://code.overheid.nl",)

    def test_other_port_kept(self):
        settings = _settings(ci_forgejo_hosts="https://code.overheid.nl:8443")
        assert settings.forgejo_hosts == ("https://code.overheid.nl:8443",)

    def test_duplicates_removed(self):
        settings = _settings(ci_forgejo_hosts="https://code.overheid.nl,https://code.overheid.nl/")
        assert settings.forgejo_hosts == ("https://code.overheid.nl",)

    def test_empty_entries_skipped(self):
        settings = _settings(ci_forgejo_hosts="https://code.overheid.nl,,")
        assert settings.forgejo_hosts == ("https://code.overheid.nl",)

    def test_all_empty_no_hosts(self):
        settings = _settings(ci_forgejo_hosts="")
        assert settings.forgejo_hosts == ()

    def test_http_refused(self):
        with pytest.raises(Exception, match="https"):
            _settings(ci_forgejo_hosts="http://code.overheid.nl")

    def test_path_refused(self):
        with pytest.raises(Exception, match="schema en host"):
            _settings(ci_forgejo_hosts="https://code.overheid.nl/pad")

    def test_query_refused(self):
        with pytest.raises(Exception, match="schema en host"):
            _settings(ci_forgejo_hosts="https://code.overheid.nl?x=1")

    def test_userinfo_refused(self):
        with pytest.raises(Exception, match="schema en host"):
            _settings(ci_forgejo_hosts="https://user:pass@code.overheid.nl")

    def test_invalid_port_refused(self):
        with pytest.raises(Exception, match="poort"):
            _settings(ci_forgejo_hosts="https://code.overheid.nl:notaport")
