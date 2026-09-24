"""Verifying CI ID tokens (GitHub and Forgejo Actions).

What decides whether a token is accepted, in order:

- its `iss` must be one of the configured issuers (providers.known_issuers);
  the claim only selects from that list, so no URL ever comes from the token;
- the signing keys come from that issuer's discovery document and JWKS, both
  fetched over https from the issuer's own origin, cached for an hour and
  refetched (at most once a minute) when a token names an unknown `kid`; one
  fetch per issuer at a time, and none for 30 seconds after a failure;
- RS256 only: `none`, HS* and every other algorithm is refused before a key
  is looked at;
- `exp` and `iat` are required, `exp`/`nbf`/`iat` get 60 seconds of leeway;
- `aud` must be exactly PLAK_BASE_URL, as configured (no normalisation).

Whether the verified token may deploy to a given site is trust.py's question.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from authlib.jose import JsonWebKey, JsonWebToken
from authlib.jose.errors import JoseError

from plak import i18n, messages
from plak.audit import vocabulary
from plak.ci.providers import (
    MAX_JWKS_BYTES,
    MAX_METADATA_BYTES,
    FetchError,
    Issuer,
    fetch_json,
    known_issuers,
)
from plak.messages import Msg

if TYPE_CHECKING:
    import httpx

    from plak.config import Settings

ALGORITHM = "RS256"
LEEWAY_S = 60
KEYS_TTL_S = 3600
KID_REFRESH_COOLDOWN_S = 60
# After a failed fetch, the issuer is not asked again for this long: a
# provider outage must not turn every deploy attempt into outbound requests.
FETCH_FAILURE_TTL_S = 30
MAX_TOKEN_LENGTH = 16 * 1024

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def looks_like_jwt(value: str) -> bool:
    """Three non-empty base64url segments separated by dots."""
    parts = value.split(".")
    return len(parts) == 3 and all(_SEGMENT_RE.match(part) for part in parts)


class CiTokenError(Exception):
    """A refused CI token. `reason` is one of vocabulary.CI_REASONS; the
    message is a key from plak/messages.py, and safe to show whichever
    language it is rendered in: it never echoes the token or a claim."""

    def __init__(
        self, key: str, *, params: Mapping[str, object] | None = None, status: int = 401
    ) -> None:
        self.message = Msg(key, dict(params or {}))
        self.reason = messages.code_of(key)
        self.status = status
        super().__init__(messages.render(i18n.API_DEFAULT, self.message))


def _invalid(variant: str) -> CiTokenError:
    return CiTokenError(f"{vocabulary.CI_TOKEN_INVALID}.{variant}")


def _unreachable() -> CiTokenError:
    return CiTokenError(f"{vocabulary.CI_PROVIDER_UNREACHABLE}.keys", status=503)


def _b64_json(segment: str) -> Any:
    padded = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


@dataclass(frozen=True)
class VerifiedCiToken:
    issuer: Issuer
    claims: Mapping[str, Any]

    def claim(self, name: str) -> str | None:
        """A claim as a non-empty string, or None. Numbers are stringified
        (Forgejo and GitHub send ids as strings, but nothing promises that)."""
        value = self.claims.get(name)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str) and value:
            return value
        return None


@dataclass
class _Keys:
    keyset: Any
    fetched_at: float
    last_kid_refresh: float | None = None


class _UnknownKid(Exception):  # noqa: N818 - internal marker, never leaves this module
    pass


class CiTokenVerifier:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, *, clock=time.monotonic) -> None:
        self._settings = settings
        self._http = http
        self._clock = clock
        self._issuers = known_issuers(settings)
        self._jwt = JsonWebToken([ALGORITHM])
        # Bounded by construction: one entry per configured issuer at most,
        # in all three.
        self._keys: dict[str, _Keys] = {}
        self._failed_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._fetches: dict[str, int] = {}

    async def verify(self, token: str) -> VerifiedCiToken:
        if len(token) > MAX_TOKEN_LENGTH or not looks_like_jwt(token):
            raise _invalid("not_a_jwt")
        header_segment, payload_segment, _ = token.split(".")
        try:
            header = _b64_json(header_segment)
            payload = _b64_json(payload_segment)
        # A deeply nested payload makes json.loads raise RecursionError rather
        # than ValueError, and a few thousand brackets fit inside
        # MAX_TOKEN_LENGTH.
        except (ValueError, RecursionError) as error:
            raise _invalid("not_a_jwt") from error
        if not isinstance(header, dict) or not isinstance(payload, dict):
            raise _invalid("not_a_jwt")
        if header.get("alg") != ALGORITHM:
            raise _invalid("algorithm")

        issuer = self._issuers.get(payload.get("iss")) if isinstance(payload.get("iss"), str) else None
        if issuer is None:
            raise CiTokenError(vocabulary.CI_ISSUER_UNKNOWN)

        claims = await self._decode(issuer, token)
        self._check_claims(issuer, claims)
        return VerifiedCiToken(issuer=issuer, claims=dict(claims))

    async def _decode(self, issuer: Issuer, token: str):
        try:
            return await self._decode_with(issuer, token, await self._keyset(issuer))
        except _UnknownKid:
            keys = self._keys[issuer.issuer]
            now = self._clock()
            if keys.last_kid_refresh is not None and now - keys.last_kid_refresh < KID_REFRESH_COOLDOWN_S:
                raise _invalid("unknown_key") from None
            keys.last_kid_refresh = now
            keyset = await self._keyset(issuer, force=True)
            try:
                return await self._decode_with(issuer, token, keyset)
            except _UnknownKid:
                raise _invalid("unknown_key") from None

    async def _decode_with(self, issuer: Issuer, token: str, keyset):
        def _key(header: Mapping[str, Any], _payload: Any):
            kid = header.get("kid")
            if not kid:
                if len(keyset.keys) == 1:
                    return keyset.keys[0]
                raise _invalid("no_kid")
            try:
                return keyset.find_by_kid(kid)
            except ValueError as error:
                raise _UnknownKid() from error

        try:
            claims = self._jwt.decode(token, _key)
            claims.validate(now=int(time.time()), leeway=LEEWAY_S)
        except (_UnknownKid, CiTokenError):
            raise
        except (JoseError, ValueError, TypeError) as error:
            raise _invalid("expired") from error
        return claims

    def _check_claims(self, issuer: Issuer, claims: Mapping[str, Any]) -> None:
        if claims.get("iss") != issuer.issuer:  # pragma: no cover - defensive: selected from these same bytes
            raise CiTokenError(f"{vocabulary.CI_ISSUER_UNKNOWN}.mismatch")
        for name in ("exp", "iat"):
            if isinstance(claims.get(name), bool) or not isinstance(claims.get(name), int | float):
                raise _invalid("no_lifetime")
        audience = self._settings.base_url
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) == 1:
            aud = aud[0]
        if not audience or not isinstance(aud, str) or aud != audience:
            if audience:
                raise CiTokenError(
                    vocabulary.CI_AUDIENCE_MISMATCH, params={"audience": audience}
                )
            raise CiTokenError(f"{vocabulary.CI_AUDIENCE_MISMATCH}.no_base_url")

    async def _keyset(self, issuer: Issuer, *, force: bool = False):
        """The issuer's keyset: cached for KEYS_TTL_S, fetched by one request
        at a time per issuer (the others wait and share the result), and not
        refetched for FETCH_FAILURE_TTL_S after a failure."""
        cached = self._keys.get(issuer.issuer)
        if cached is not None and not force and self._clock() - cached.fetched_at < KEYS_TTL_S:
            return cached.keyset
        fetches_before = self._fetches.get(issuer.issuer, 0)
        lock = self._locks.setdefault(issuer.issuer, asyncio.Lock())
        async with lock:
            now = self._clock()
            cached = self._keys.get(issuer.issuer)
            if cached is not None:
                fresh = now - cached.fetched_at < KEYS_TTL_S
                # Someone else fetched while this request waited for the lock.
                if (not force and fresh) or (force and self._fetches.get(issuer.issuer, 0) > fetches_before):
                    return cached.keyset
            failed_at = self._failed_at.get(issuer.issuer)
            if failed_at is not None and now - failed_at < FETCH_FAILURE_TTL_S:
                if cached is not None and not force:
                    return cached.keyset
                raise _unreachable()
            try:
                keyset = await self._fetch_keyset(issuer)
            except (FetchError, JoseError, ValueError, TypeError, KeyError) as error:
                self._failed_at[issuer.issuer] = now
                if cached is not None and not force:
                    # A stale keyset beats refusing every deploy while the
                    # provider hiccups; the TTL is about rotation, not
                    # revocation. Restarting its clock keeps every request
                    # from retrying the fetch.
                    cached.fetched_at = now
                    return cached.keyset
                raise _unreachable() from error
            self._failed_at.pop(issuer.issuer, None)
            self._fetches[issuer.issuer] = self._fetches.get(issuer.issuer, 0) + 1
            last_kid_refresh = cached.last_kid_refresh if cached is not None else None
            self._keys[issuer.issuer] = _Keys(keyset=keyset, fetched_at=now, last_kid_refresh=last_kid_refresh)
            return keyset

    async def _fetch_keyset(self, issuer: Issuer):
        metadata = await fetch_json(
            self._http, issuer.issuer + "/.well-known/openid-configuration", max_bytes=MAX_METADATA_BYTES
        )
        if not isinstance(metadata, dict) or metadata.get("issuer") != issuer.issuer:
            raise FetchError("discovery-document hoort niet bij deze issuer")
        jwks_uri = metadata.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not _same_origin(jwks_uri, issuer.issuer):
            raise FetchError("jwks_uri ligt buiten de origin van de issuer")
        jwks = await fetch_json(self._http, jwks_uri, max_bytes=MAX_JWKS_BYTES)
        return JsonWebKey.import_key_set(jwks)


def _same_origin(url: str, issuer: str) -> bool:
    candidate = urlsplit(url)
    expected = urlsplit(issuer)
    return candidate.scheme == "https" and candidate.netloc.lower() == expected.netloc.lower()
