"""OIDC client: authorization code + PKCE, client authentication per
PLAK_OIDC_CLIENT_AUTH.

Client authentication: `private_key_jwt` (RFC 7523, the default) or, for the
ZAD Keycloak that only creates client-secret clients, `client_secret_post` or
`client_secret_basic` (RFC 6749 §2.3.1).

Security code of our own on top of joserfc, deliberately not metadata-driven:

- the alg allowlist for id tokens is a fixed JWSRegistry of our own
  (RS256/PS256/ES256); what the provider metadata advertises does not matter;
- the RFC 9207 iss check runs on the callback query parameters before the
  token exchange;
- claim checks (issuer exact, aud, nonce, acr when a list is configured,
  at_hash when present) are explicit checks of our own.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus, urlencode

from joserfc import jwt
from joserfc.errors import InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet, import_key
from joserfc.jws import JWSRegistry
from joserfc.jwt import JWTClaimsRegistry

from plak.audit import vocabulary
from plak.config import ConfigurationError

if TYPE_CHECKING:
    import httpx

    from plak.config import Settings

ALG_ALLOWLIST = ("RS256", "PS256", "ES256")
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
SCOPES = "openid profile email"

# OIDC Back-Channel Logout 1.0 section 2.4: the event key a logout token must
# carry, and how old its iat may be.
BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
LOGOUT_TOKEN_MAX_AGE_S = 300

# A Keycloak logout token sits well under 2 KB; the cap only keeps an
# unauthenticated caller from making us base64-decode an arbitrary body.
LOGOUT_TOKEN_MAX_CHARS = 8192
# Section 2.4 asks for "logout+jwt"; Keycloak still mints plain "JWT"
# (github.com/keycloak/keycloak#28939 tracks the gap).
_LOGOUT_TOKEN_TYPES = frozenset({"jwt", "logout+jwt"})

# RFC 6749 §5.2 error codes that say the deployment is broken rather than that
# a session ended: the client, its credentials or the grant it is allowed to
# use. Refusing these keeps everyone logged in, but loudly (ClientRejectedError).
CLIENT_FAULT_ERRORS = frozenset(
    {
        "invalid_client",
        "unauthorized_client",
        "unsupported_grant_type",
        "invalid_request",
        "invalid_scope",
    }
)

_logger = logging.getLogger(__name__)


class OidcError(Exception):
    """Login is refused; the message is for logging, never for the visitor.

    `reason` is the coarse cause that goes into the audit record, from the set
    in audit/vocabulary.py. Never the value that tripped the check (state,
    nonce, code, token): a log line may not carry anything that helps break
    the security (BIO2 8.15.02).
    """

    def __init__(self, message: str, *, reason: str = vocabulary.LOGIN_TOKEN_INVALID) -> None:
        self.reason = reason
        super().__init__(message)


class IdpUnavailableError(OidcError):
    """Soft failure of a re-validation: the IdP could not answer (network, a
    timeout, a 5xx, or a refusal that is about our client rather than about
    this session). The session stays; the caller retries later."""

    def __init__(self, message: str) -> None:
        super().__init__(message, reason=vocabulary.LOGIN_IDP_UNREACHABLE)


class ClientRejectedError(IdpUnavailableError):
    """Soft failure of the loud kind: the token endpoint refused our client
    rather than the session (wrong secret, the refresh grant switched off, the
    client gone). Nobody is logged out over our own broken coupling, but an
    operator has to hear about it. `code` is the OAuth error code."""

    def __init__(self, message: str, *, code: str) -> None:
        self.code = code
        super().__init__(message)


class RefreshRejectedError(OidcError):
    """Hard failure of a re-validation: the IdP refuses to renew this session
    (invalid_grant), so the session at the IdP is over."""


class _UnknownKid(Exception):  # noqa: N818 - internal marker, never surfaces as an error
    """Internal marker: the id token points at a kid that is not in the
    (cached) JWKS. Never leak it to the caller; always translate it into an
    OidcError."""


# Cooldown between two JWKS refresh attempts triggered by an unknown kid, so
# that a stream of invalid tokens does not turn into a fetch storm against the
# IdP.
_JWKS_REFRESH_COOLDOWN_S = 60


@dataclass(frozen=True)
class LoginStart:
    authorization_url: str
    state: str
    nonce: str
    code_verifier: str


@dataclass(frozen=True)
class LogoutToken:
    """What a verified back-channel logout token points at."""

    sub: str | None
    sid: str | None
    jti: str


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unverified_jwt_segment(token: str, index: int) -> dict[str, Any] | None:
    """Decodes one segment of a JWT without checking its signature."""
    try:
        segment = token.split(".")[index]
        padded = segment + "=" * (-len(segment) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError, IndexError):
        return None
    return decoded if isinstance(decoded, dict) else None


def logout_token_prefilter(token: str, settings: Settings) -> str | None:
    """A refusal reason for a logout token not worth a signature verification, else None.

    Reads the JWT without checking its signature, so it may only narrow what
    reaches `OidcClient.validate_logout_token` - never stand in for it. The
    back-channel logout endpoint is unauthenticated and its rate limit keys on
    a client-controlled address (see docs/security.md), so this is what bounds
    how often a caller can make us fetch the JWKS and verify a signature.
    """
    if len(token) > LOGOUT_TOKEN_MAX_CHARS:
        return "oversized"
    if token.count(".") != 2:
        return "malformed"
    header = _unverified_jwt_segment(token, 0)
    claims = _unverified_jwt_segment(token, 1)
    if header is None or claims is None:
        return "malformed"

    typ = header.get("typ")
    if typ is not None and (not isinstance(typ, str) or typ.lower() not in _LOGOUT_TOKEN_TYPES):
        return "typ"
    if header.get("alg") not in ALG_ALLOWLIST:
        return "alg"

    if claims.get("iss") != settings.oidc_issuer:
        return "iss"
    iat = claims.get("iat")
    if not isinstance(iat, int) or abs(time.time() - iat) > LOGOUT_TOKEN_MAX_AGE_S:
        return "iat"
    jti = claims.get("jti")
    if not isinstance(jti, str) or not jti:
        return "jti"
    if "nonce" in claims:
        return "nonce"
    events = claims.get("events")
    if not isinstance(events, dict) or not isinstance(events.get(BACKCHANNEL_LOGOUT_EVENT), dict):
        return "events"
    return None


def unverified_logout_token_jti(token: str) -> str | None:
    """The `jti` as the token claims it, for the replay check before verification."""
    claims = _unverified_jwt_segment(token, 1)
    if claims is None:
        return None
    jti = claims.get("jti")
    return jti if isinstance(jti, str) and jti else None


def _check_sub_exp_iat(claims: Mapping[str, Any]) -> None:
    """joserfc treats `exp` and `iat` as optional, so demand them ourselves."""
    if not claims.get("sub"):
        raise OidcError("id-token sub ontbreekt")
    if "exp" not in claims or "iat" not in claims:
        raise OidcError("id-token exp of iat ontbreekt")


def _error_code(response: httpx.Response) -> str | None:
    """The OAuth error code of a refused token response; None when the body is
    not the JSON object RFC 6749 §5.2 prescribes."""
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("error") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def _json_object(response: httpx.Response) -> dict[str, Any] | None:
    """The parsed JSON body of a successful token response; None when it is
    not the JSON object RFC 6749 §5.1 prescribes (a proxy's maintenance page,
    for instance, still answers 200)."""
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


class OidcClient:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http
        self._registry = JWSRegistry(algorithms=list(ALG_ALLOWLIST))
        self._metadata: dict[str, Any] | None = None
        self._keyset = None
        self._last_kid_refresh: float | None = None

        self.required_acr = tuple(
            acr.strip() for acr in settings.oidc_required_acr.split(",") if acr.strip()
        )
        if not self.required_acr:
            message = (
                "PLAK_OIDC_REQUIRED_ACR is leeg: de acr-claim van het id-token wordt niet "
                "gecontroleerd en er wordt geen acr_values meegestuurd; het "
                "authenticatieniveau hangt dan volledig af van de OP (%s)"
            )
            if settings.environment == "productie":
                _logger.warning(message, settings.oidc_issuer)
            else:
                _logger.info(message, settings.oidc_issuer)

        self.client_auth = settings.oidc_client_auth
        self._private_jwk: dict[str, Any] | None = None
        self._private_key = None
        self._assertion_alg: str | None = None
        if self.client_auth == "private_key_jwt":
            try:
                self._private_jwk = json.loads(settings.oidc_client_private_jwk)
                self._private_key = import_key(self._private_jwk)
            except (ValueError, JoseError) as error:
                raise ConfigurationError(
                    "PLAK_OIDC_CLIENT_PRIVATE_JWK is geen geldige private JWK"
                ) from error
            self._assertion_alg = {"RSA": "RS256", "EC": "ES256"}.get(self._private_jwk.get("kty"))
            if self._assertion_alg is None:
                raise ConfigurationError(
                    "PLAK_OIDC_CLIENT_PRIVATE_JWK moet een RSA- of EC-sleutel zijn"
                )

    async def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            url = self._settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
            try:
                response = await self._http.get(url)
                response.raise_for_status()
                data_ = response.json()
            except Exception as error:
                raise OidcError(
                    f"discovery-metadata niet op te halen: {error}", reason=vocabulary.LOGIN_IDP_UNREACHABLE
                ) from error
            if data_.get("issuer") != self._settings.oidc_issuer:
                raise OidcError(
                    "issuer in discovery-metadata wijkt af van de configuratie",
                    reason=vocabulary.LOGIN_IDP_UNREACHABLE,
                )
            self._metadata = data_
        return self._metadata

    async def _fetch_keyset(self, *, force: bool = False):
        if self._keyset is not None and not force:
            return self._keyset
        meta = await self.metadata()
        try:
            response = await self._http.get(meta["jwks_uri"])
            response.raise_for_status()
            self._keyset = KeySet.import_key_set(response.json())
        except OidcError:  # pragma: no cover - nothing above raises OidcError, only httpx/joserfc errors
            raise
        except Exception as error:
            raise OidcError(f"JWKS niet op te halen: {error}", reason=vocabulary.LOGIN_IDP_UNREACHABLE) from error
        return self._keyset

    async def start_login(self, redirect_uri: str) -> LoginStart:
        meta = await self.metadata()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        code_verifier = secrets.token_urlsafe(48)
        code_challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
        params = {
            "response_type": "code",
            "client_id": self._settings.oidc_client_id,
            "redirect_uri": redirect_uri,
            "scope": SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if self.required_acr:
            params["acr_values"] = " ".join(self.required_acr)
        url = meta["authorization_endpoint"] + "?" + urlencode(params)
        return LoginStart(authorization_url=url, state=state, nonce=nonce, code_verifier=code_verifier)

    async def end_session_url(self, *, post_logout_redirect_uri: str, id_token: str | None) -> str | None:
        """RP-initiated logout (OIDC RP-Initiated Logout 1.0 section 2), or None
        when the OP advertises no end_session_endpoint.

        client_id goes along because the OP demands either that or an
        id_token_hint once a post_logout_redirect_uri is sent, and that URI has
        to be registered with the OP or the OP refuses it.
        """
        try:
            meta = await self.metadata()
        except OidcError:
            return None
        endpoint = meta.get("end_session_endpoint")
        if not endpoint:
            return None
        params = {
            "client_id": self._settings.oidc_client_id,
            "post_logout_redirect_uri": post_logout_redirect_uri,
        }
        if id_token:
            params["id_token_hint"] = id_token
        separator = "&" if "?" in endpoint else "?"
        return f"{endpoint}{separator}{urlencode(params)}"

    def check_callback_iss(self, params: Mapping[str, str], metadata: Mapping[str, Any]) -> None:
        """RFC 9207: runs on the callback query parameters, before the token exchange."""
        iss = params.get("iss")
        if iss is not None:
            if iss != self._settings.oidc_issuer:
                raise OidcError(
                    "iss-parameter komt niet overeen met de geconfigureerde issuer",
                    reason=vocabulary.LOGIN_ISS_MISMATCH,
                )
            return
        required = self._settings.oidc_iss_required or bool(
            metadata.get("authorization_response_iss_parameter_supported")
        )
        if required:
            raise OidcError("iss-parameter ontbreekt in de callback", reason=vocabulary.LOGIN_ISS_MISMATCH)

    def _make_client_assertion(self, token_endpoint: str) -> str:
        private_jwk = self._private_jwk
        if private_jwk is None:
            raise OidcError(
                "client_assertion vereist PLAK_OIDC_CLIENT_AUTH=private_key_jwt",
                reason=vocabulary.LOGIN_IDP_UNREACHABLE,
            )
        now_ = int(time.time())
        client_id = self._settings.oidc_client_id
        header: dict[str, Any] = {"alg": self._assertion_alg}
        if private_jwk.get("kid"):
            header["kid"] = private_jwk["kid"]
        claims = {
            "iss": client_id,
            "sub": client_id,
            "aud": token_endpoint,
            "jti": secrets.token_urlsafe(16),
            "iat": now_,
            "exp": now_ + 300,
        }
        return jwt.encode(header, claims, self._private_key, registry=self._registry)

    def _client_authentication(self, token_endpoint: str) -> tuple[dict[str, str], dict[str, str]]:
        """Returns (extra form fields, extra headers) for the token exchange."""
        client_id = self._settings.oidc_client_id
        if self.client_auth == "private_key_jwt":
            return {
                "client_id": client_id,
                "client_assertion_type": CLIENT_ASSERTION_TYPE,
                "client_assertion": self._make_client_assertion(token_endpoint),
            }, {}
        client_secret = self._settings.oidc_client_secret
        if self.client_auth == "client_secret_post":
            return {"client_id": client_id, "client_secret": client_secret}, {}
        # client_secret_basic: RFC 6749 §2.3.1 prescribes form-url-encoding of
        # username and password before the Basic encoding.
        credentials = f"{quote_plus(client_id)}:{quote_plus(client_secret)}"
        basic = base64.b64encode(credentials.encode("ascii")).decode("ascii")
        return {}, {"Authorization": f"Basic {basic}"}

    async def exchange_code(self, code: str, redirect_uri: str, code_verifier: str) -> dict[str, Any]:
        meta = await self.metadata()
        token_endpoint = meta["token_endpoint"]
        fields, headers = self._client_authentication(token_endpoint)
        try:
            response = await self._http.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "code_verifier": code_verifier,
                    **fields,
                },
                headers=headers,
            )
        except Exception as error:
            raise OidcError(
                f"token-endpoint niet bereikbaar: {error}", reason=vocabulary.LOGIN_IDP_UNREACHABLE
            ) from error
        if response.status_code != 200:
            raise OidcError(f"token-endpoint weigerde de code (status {response.status_code})")
        data_ = _json_object(response)
        if data_ is None:
            raise OidcError("token-endpoint gaf status 200 zonder JSON-object terug")
        if not data_.get("id_token"):
            raise OidcError("token-antwoord bevat geen id_token")
        return data_

    async def refresh_tokens(self, refresh_token: str) -> dict[str, Any]:
        """RFC 6749 §6 against the token endpoint, for the periodic
        re-validation of a session (auth/revalidation.py).

        Only `invalid_grant` is a hard failure: the IdP says this session is
        over. Every other refusal (invalid_client, an unreachable endpoint, a
        5xx) is about us or about the connection, and logging everybody out
        over our own misconfiguration would be worse than the window it closes.
        """
        try:
            meta = await self.metadata()
            token_endpoint = meta["token_endpoint"]
            fields, headers = self._client_authentication(token_endpoint)
        except OidcError as error:
            raise IdpUnavailableError(str(error)) from error
        try:
            response = await self._http.post(
                token_endpoint,
                data={"grant_type": "refresh_token", "refresh_token": refresh_token, **fields},
                headers=headers,
            )
        except Exception as error:
            raise IdpUnavailableError(f"token-endpoint niet bereikbaar: {error}") from error
        if response.status_code == 200:
            data_ = _json_object(response)
            if data_ is None:
                raise IdpUnavailableError("token-endpoint gaf status 200 zonder JSON-object terug")
            return data_
        code = _error_code(response)
        if response.status_code in (400, 401) and code == "invalid_grant":
            raise RefreshRejectedError("de IdP verwierp het verversingstoken")
        if code in CLIENT_FAULT_ERRORS:
            raise ClientRejectedError(
                f"token-endpoint weigerde onze client met '{code}'", code=code
            )
        raise IdpUnavailableError(f"token-endpoint gaf status {response.status_code}")

    async def validate_refreshed_id_token(self, id_token: str) -> dict[str, Any]:
        """The id token of a refresh: signature, issuer, audience and sub.

        No nonce check (a refresh has no authorization request to bind one to)
        and no acr check (an OP may report the level of the renewal rather than
        of the original authentication); the session already carries the acr it
        was created with.
        """
        claims = await self._decode_with_kid_refresh(id_token)
        self._check_issuer_and_audience(claims)
        _check_sub_exp_iat(claims)
        return dict(claims)

    async def validate_logout_token(self, logout_token: str) -> LogoutToken:
        """OIDC Back-Channel Logout 1.0 §2.6: signature, iss, aud, a recent
        iat, an exp, a jti, the backchannel-logout event, sub or sid, and no
        nonce."""
        claims = await self._decode_with_kid_refresh(logout_token)
        self._check_issuer_and_audience(claims)

        iat = claims.get("iat")
        if not isinstance(iat, int) or abs(time.time() - iat) > LOGOUT_TOKEN_MAX_AGE_S:
            raise OidcError("logout-token heeft geen recente iat")

        # §2.4 makes both REQUIRED. joserfc checks exp only when it is there,
        # and the replay check in platform/backchannel.py keys on the jti.
        if "exp" not in claims:
            raise OidcError("logout-token mist exp")
        jti = claims.get("jti")
        if not isinstance(jti, str) or not jti:
            raise OidcError("logout-token mist een jti")

        events = claims.get("events")
        if not isinstance(events, dict) or not isinstance(events.get(BACKCHANNEL_LOGOUT_EVENT), dict):
            raise OidcError("logout-token mist de backchannel-logout-event")

        # §2.4: a nonce is what an id token carries, so its presence marks a
        # token that was meant as one and is being replayed here.
        if "nonce" in claims:
            raise OidcError("logout-token bevat een nonce")

        sub = claims.get("sub") or None
        sid = claims.get("sid") or None
        if not sub and not sid:
            raise OidcError("logout-token bevat sub noch sid")
        return LogoutToken(sub=sub, sid=sid, jti=jti)

    async def validate_id_token(
        self, id_token: str, *, nonce: str, access_token: str | None
    ) -> dict[str, Any]:
        claims = await self._decode_with_kid_refresh(id_token)
        self._check_claims(claims, nonce=nonce, access_token=access_token)
        result = dict(claims)
        if not self.required_acr:
            # Without an acr requirement the OP may omit the claim; consumers
            # (the session) count on a string.
            result.setdefault("acr", "")
        return result

    async def _decode_with_kid_refresh(self, token: str):
        """Decodes a token signed by the OP, refreshing the JWKS once when it
        names a kid we do not know yet (key rollover)."""
        try:
            return await self._decode_id_token(token)
        except _UnknownKid as error:
            if not self._may_refresh_kid():
                raise OidcError(
                    "token verwijst naar een onbekende sleutel (kid); JWKS-ververscooldown actief"
                ) from error
            self._last_kid_refresh = time.monotonic()
            await self._fetch_keyset(force=True)
            try:
                return await self._decode_id_token(token)
            except _UnknownKid as inner:
                raise OidcError(f"token ongeldig: {inner}") from inner

    def _may_refresh_kid(self) -> bool:
        if self._last_kid_refresh is None:
            return True
        return (time.monotonic() - self._last_kid_refresh) >= _JWKS_REFRESH_COOLDOWN_S

    async def _decode_id_token(self, id_token: str):
        keyset = await self._fetch_keyset()

        def _key(obj: Any):
            kid = obj.headers().get("kid")
            if kid:
                try:
                    return keyset.get_by_kid(kid)
                except InvalidKeyIdError as error:
                    raise _UnknownKid(str(error)) from error
            keys = keyset.keys
            if len(keys) == 1:
                return keys[0]
            raise OidcError("id-token zonder kid terwijl de JWKS meerdere sleutels bevat")

        try:
            claims = jwt.decode(id_token, _key, registry=self._registry).claims
            # jwt.decode only checks that the payload is JSON; a signed array
            # or scalar would otherwise reach the claim checks as a non-dict.
            if not isinstance(claims, dict):
                raise OidcError("id-token payload is geen JSON-object")
            JWTClaimsRegistry(leeway=60).validate(claims)
        except _UnknownKid:
            raise
        except OidcError:
            raise
        except (JoseError, ValueError) as error:
            raise OidcError(f"id-token ongeldig: {error}") from error
        return claims

    def _check_issuer_and_audience(self, claims: Mapping[str, Any]) -> None:
        """Shared by the id token of a login, the id token of a refresh and a
        back-channel logout token: all three come from our OP and are meant for
        our client."""
        settings = self._settings
        if claims.get("iss") != settings.oidc_issuer:
            raise OidcError("token issuer onjuist")

        aud = claims.get("aud")
        if isinstance(aud, str):
            aud = [aud]
        if not aud or settings.oidc_client_id not in aud:
            raise OidcError("token aud onjuist")
        if len(aud) > 1 and claims.get("azp") != settings.oidc_client_id:
            raise OidcError("token azp onjuist bij meerdere audiences")

        azp = claims.get("azp")
        if azp is not None and azp != settings.oidc_client_id:
            raise OidcError("token azp onjuist")

    def _check_claims(
        self, claims: Mapping[str, Any], *, nonce: str, access_token: str | None
    ) -> None:
        self._check_issuer_and_audience(claims)
        _check_sub_exp_iat(claims)

        if not nonce or claims.get("nonce") != nonce:
            raise OidcError("id-token nonce onjuist")

        if self.required_acr and claims.get("acr") not in self.required_acr:
            raise OidcError(
                "id-token acr ontbreekt of staat niet in de vereiste lijst",
                reason=vocabulary.LOGIN_ACR_INSUFFICIENT,
            )

        at_hash = claims.get("at_hash")
        if at_hash is not None:
            # RS256/PS256/ES256 all three use SHA-256 for at_hash.
            if not access_token:
                raise OidcError("at_hash aanwezig maar geen access_token om te controleren")
            expected = _b64url(hashlib.sha256(access_token.encode("ascii")).digest()[:16])
            # Compare as bytes: compare_digest raises TypeError for non-ASCII
            # str and for anything that is not a str at all.
            if not isinstance(at_hash, str) or not hmac.compare_digest(
                expected.encode("ascii"), at_hash.encode("utf-8")
            ):
                raise OidcError("at_hash komt niet overeen met het access_token")


__all__ = [
    "ALG_ALLOWLIST",
    "BACKCHANNEL_LOGOUT_EVENT",
    "CLIENT_ASSERTION_TYPE",
    "LOGOUT_TOKEN_MAX_AGE_S",
    "LOGOUT_TOKEN_MAX_CHARS",
    "SCOPES",
    "IdpUnavailableError",
    "LoginStart",
    "LogoutToken",
    "OidcClient",
    "OidcError",
    "RefreshRejectedError",
    "logout_token_prefilter",
    "unverified_logout_token_jti",
]
