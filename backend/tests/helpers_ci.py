"""Test helpers for CI trust: a mock GitHub and Forgejo without a network.

Discovery, JWKS and the repository REST endpoints of both providers are
served out of one httpx.MockTransport; ID tokens are signed with a generated
RSA key, the same pattern as helpers_oidc.MockIdP.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field

import httpx
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey

from plak.ci.providers import GITHUB_API, GITHUB_ISSUER

FORGEJO_HOST = "https://code.overheid.nl"
FORGEJO_ISSUER = FORGEJO_HOST + "/api/actions"
AUDIENCE = "https://plak.example"

# Sentinel to leave a claim out of the token.
OMIT = object()


@dataclass
class RepositoryRecord:
    owner: str
    repo: str
    repository_id: int
    owner_id: int

    def json(self) -> dict:
        return {
            "id": self.repository_id,
            "name": self.repo,
            "full_name": f"{self.owner}/{self.repo}",
            "owner": {"id": self.owner_id, "login": self.owner},
        }


@dataclass
class _Issuer:
    issuer: str
    jwks_uri: str
    key: RSAKey
    kid: str

    @property
    def jwks(self) -> dict:
        public = self.key.as_dict(private=False)
        public["kid"] = self.kid
        return {"keys": [public]}


@dataclass
class MockCi:
    """GitHub (always) plus one Forgejo host, with repositories registered per host."""

    github: _Issuer = field(
        default_factory=lambda: _Issuer(
            GITHUB_ISSUER,
            GITHUB_ISSUER + "/.well-known/jwks",
            RSAKey.generate_key(2048),
            "github-sleutel-1",
        )
    )
    forgejo: _Issuer = field(
        default_factory=lambda: _Issuer(
            FORGEJO_ISSUER,
            FORGEJO_ISSUER + "/.well-known/keys",
            RSAKey.generate_key(2048),
            "forgejo-sleutel-1",
        )
    )
    github_repositories: dict[str, RepositoryRecord] = field(default_factory=dict)
    forgejo_repositories: dict[str, RepositoryRecord] = field(default_factory=dict)
    # Per URL: a status code (or an exception instance) to answer instead.
    failures: dict[str, object] = field(default_factory=dict)
    requests: list[str] = field(default_factory=list)

    def add_github(self, owner: str, repo: str, repository_id: int, owner_id: int) -> RepositoryRecord:
        record = RepositoryRecord(owner, repo, repository_id, owner_id)
        self.github_repositories[f"{owner}/{repo}".lower()] = record
        return record

    def add_forgejo(self, owner: str, repo: str, repository_id: int, owner_id: int) -> RepositoryRecord:
        record = RepositoryRecord(owner, repo, repository_id, owner_id)
        self.forgejo_repositories[f"{owner}/{repo}".lower()] = record
        return record

    def _discovery(self, issuer: _Issuer) -> dict:
        return {
            "issuer": issuer.issuer,
            "jwks_uri": issuer.jwks_uri,
            "id_token_signing_alg_values_supported": ["RS256"],
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        failure = self.failures.get(url)
        if isinstance(failure, Exception):
            raise failure
        if isinstance(failure, int):
            return httpx.Response(failure, json={"message": "fout"})
        if isinstance(failure, httpx.Response):
            return failure
        for issuer in (self.github, self.forgejo):
            if url == issuer.issuer + "/.well-known/openid-configuration":
                return httpx.Response(200, json=self._discovery(issuer))
            if url == issuer.jwks_uri:
                return httpx.Response(200, json=issuer.jwks)
        for prefix, repositories in (
            (GITHUB_API + "/repos/", self.github_repositories),
            (FORGEJO_HOST + "/api/v1/repos/", self.forgejo_repositories),
        ):
            if url.startswith(prefix):
                record = repositories.get(url.removeprefix(prefix).lower())
                if record is None:
                    return httpx.Response(404, json={"message": "Not Found"})
                return httpx.Response(200, content=json.dumps(record.json()).encode())
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def token(
        self,
        provider: str = "github",
        *,
        alg: str = "RS256",
        key=None,
        kid: str | object | None = None,
        **overrides,
    ) -> str:
        """A signed ID token; `overrides` replace claims, OMIT drops one."""
        issuer = self.github if provider == "github" else self.forgejo
        now = int(time.time())
        claims: dict = {
            "iss": issuer.issuer,
            "aud": AUDIENCE,
            "sub": "repo:minbzk/website:ref:refs/heads/main",
            "exp": now + 300,
            "iat": now,
            "nbf": now,
            "repository": "minbzk/website",
            "repository_owner": "minbzk",
            "ref": "refs/heads/main",
            "ref_type": "branch",
            "sha": "0123456789abcdef0123456789abcdef01234567",
            "run_id": "4242",
            "workflow": "Publiceer",
            "event_name": "push",
            "actor": "ontwikkelaar",
        }
        if provider == "github":
            claims["repository_id"] = "1001"
            claims["repository_owner_id"] = "2002"
            # GitHub gives every token its own jti; Forgejo sends none.
            claims["jti"] = str(uuid.uuid4())
        for name, value in overrides.items():
            if value is OMIT:
                claims.pop(name, None)
            else:
                claims[name] = value
        header: dict = {"alg": alg}
        if kid is None:
            header["kid"] = issuer.kid
        elif kid is not OMIT:
            header["kid"] = kid
        signing_key = key if key is not None else issuer.key
        if isinstance(signing_key, str | bytes):
            signing_key = OctKey.import_key(signing_key)
        return jwt.encode(header, claims, signing_key, algorithms=[alg])
