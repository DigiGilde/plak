"""The CI providers Plak trusts, and the outbound HTTP it does on their behalf.

Every URL fetched here is built from configuration (GitHub's fixed hosts and
PLAK_CI_FORGEJO_HOSTS), never from a token or a request: that is the whole
SSRF defence, together with https only, no redirects, a timeout and a cap on
the response size.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlsplit

from plak.models.ci import CiProvider

if TYPE_CHECKING:
    import httpx

    from plak.config import Settings

GITHUB_HOST = "https://github.com"
GITHUB_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_API = "https://api.github.com"
FORGEJO_ISSUER_PATH = "/api/actions"

TIMEOUT_S = 5.0
MAX_METADATA_BYTES = 64 * 1024
MAX_JWKS_BYTES = 256 * 1024
MAX_REPOSITORY_BYTES = 256 * 1024

# GitHub and Forgejo owner and repository names: letters, digits, `-`, `_`
# and `.`, at most 100 characters. Stricter than either provider needs, which
# is the point: these land in a URL path.
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
MAX_NAME_LENGTH = 100


def valid_name(value: str) -> bool:
    return (
        0 < len(value) <= MAX_NAME_LENGTH
        and value not in (".", "..")
        and all(char in _NAME_CHARS for char in value)
    )


@dataclass(frozen=True)
class Issuer:
    provider: CiProvider
    host: str
    issuer: str


def known_issuers(settings: Settings) -> dict[str, Issuer]:
    """Issuer URL to provider: GitHub always, plus every configured Forgejo host."""
    issuers = {GITHUB_ISSUER: Issuer(CiProvider.GITHUB, GITHUB_HOST, GITHUB_ISSUER)}
    for host in settings.forgejo_hosts:
        issuer = host + FORGEJO_ISSUER_PATH
        issuers[issuer] = Issuer(CiProvider.FORGEJO, host, issuer)
    return issuers


def host_label(host: str) -> str:
    """`https://github.com` -> `github.com`, for display and for version history."""
    return urlsplit(host).netloc


class FetchError(Exception):
    """The provider could not be asked: unreachable, timed out, not https,
    a redirect, or an answer too large or not JSON. `status` is set when an
    HTTP answer did arrive."""

    def __init__(self, message: str, *, status: int | None = None, rate_limited: bool = False) -> None:
        self.status = status
        self.rate_limited = rate_limited
        super().__init__(message)


async def fetch_json(http: httpx.AsyncClient, url: str, *, max_bytes: int) -> Any:
    """GET a JSON document over https, bounded in time and size.

    Any non-200 answer raises FetchError carrying its status; a 429, or a 403
    with `x-ratelimit-remaining: 0` (GitHub's unauthenticated limit), is
    flagged as rate limited.
    """
    if urlsplit(url).scheme != "https":
        raise FetchError("alleen https wordt opgehaald")
    try:
        async with http.stream(
            "GET",
            url,
            headers={"Accept": "application/json"},
            timeout=TIMEOUT_S,
            follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                limited = response.status_code == 429 or (
                    response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
                )
                raise FetchError(
                    f"antwoord {response.status_code}", status=response.status_code, rate_limited=limited
                )
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > max_bytes:
                    raise FetchError("antwoord te groot")
    except FetchError:
        raise
    except Exception as error:
        raise FetchError(f"niet bereikbaar: {type(error).__name__}") from error
    try:
        return json.loads(bytes(body))
    except ValueError as error:
        raise FetchError("antwoord is geen JSON") from error


@dataclass(frozen=True)
class ResolvedRepository:
    owner: str
    repo: str
    repository_id: int
    owner_id: int


class RepositoryNotFoundError(Exception):
    pass


class ProviderUnavailableError(Exception):
    def __init__(self, *, rate_limited: bool) -> None:
        self.rate_limited = rate_limited
        super().__init__("provider rate limited" if rate_limited else "provider unreachable")


def repository_api_url(provider: CiProvider, host: str, owner: str, repo: str) -> str:
    path = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    if provider == CiProvider.GITHUB:
        return GITHUB_API + path
    return host + "/api/v1" + path


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _parse_repository(data: Any) -> ResolvedRepository | None:
    if not isinstance(data, dict) or not isinstance(data.get("owner"), dict):
        return None
    repository_id = _positive_int(data.get("id"))
    owner_id = _positive_int(data["owner"].get("id"))
    owner = data["owner"].get("login")
    repo = data.get("name")
    if repository_id is None or owner_id is None or not isinstance(owner, str) or not isinstance(repo, str):
        return None
    if not valid_name(owner) or not valid_name(repo):
        return None
    return ResolvedRepository(owner=owner, repo=repo, repository_id=repository_id, owner_id=owner_id)


# A positive answer from the Forgejo re-check stays valid this long. Short on
# purpose: it only saves a lookup per deploy burst, and a repository that is
# deleted and recreated under the same name has to lose its trust quickly.
RECHECK_TTL_S = 300
_MAX_CACHE_ENTRIES = 1024


class ProviderClient:
    """Resolves `owner/repo` to the numeric ids through the provider's REST API."""

    def __init__(self, http: httpx.AsyncClient, *, clock=time.monotonic) -> None:
        self._http = http
        self._clock = clock
        self._confirmed: dict[tuple[str, str, int, int], float] = {}

    async def resolve(self, provider: CiProvider, host: str, owner: str, repo: str) -> ResolvedRepository:
        """Raises RepositoryNotFoundError (unknown, private, moved) or
        ProviderUnavailableError (unreachable, broken answer, rate limited)."""
        url = repository_api_url(provider, host, owner, repo)
        try:
            data = await fetch_json(self._http, url, max_bytes=MAX_REPOSITORY_BYTES)
        except FetchError as error:
            if error.rate_limited:
                raise ProviderUnavailableError(rate_limited=True) from error
            if error.status is not None and (error.status in (301, 302, 307, 308) or 400 <= error.status < 500):
                raise RepositoryNotFoundError() from error
            raise ProviderUnavailableError(rate_limited=False) from error
        resolved = _parse_repository(data)
        if resolved is None:
            raise ProviderUnavailableError(rate_limited=False)
        return resolved

    async def still_has_ids(
        self, provider: CiProvider, host: str, owner: str, repo: str, repository_id: int, owner_id: int
    ) -> bool:
        """Whether `owner/repo` still carries these ids; positive answers are
        cached for RECHECK_TTL_S. Raises ProviderUnavailableError when the
        provider cannot answer."""
        key = (host, f"{owner}/{repo}".lower(), repository_id, owner_id)
        now = self._clock()
        confirmed_at = self._confirmed.get(key)
        if confirmed_at is not None and now - confirmed_at < RECHECK_TTL_S:
            return True
        try:
            resolved = await self.resolve(provider, host, owner, repo)
        except RepositoryNotFoundError:
            self._confirmed.pop(key, None)
            return False
        if resolved.repository_id != repository_id or resolved.owner_id != owner_id:
            self._confirmed.pop(key, None)
            return False
        if len(self._confirmed) >= _MAX_CACHE_ENTRIES:
            self._confirmed.clear()
        self._confirmed[key] = now
        return True
