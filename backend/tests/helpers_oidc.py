"""Test helpers for the OIDC, session and lid tests: a mock IdP without a
network.

The IdP runs entirely in-process through httpx.MockTransport; discovery, JWKS
and the token endpoint are served out of this object. No test touches the
network.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Sequence
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey

from plak.api.errors import register_error_handlers
from plak.auth.oidc import OidcClient
from plak.auth.sessions import (
    CONTENT_ANCHOR_COOKIE,
    CONTENT_ANCHOR_PATH,
    CONTENT_SESSION_COOKIE,
    SESSION_COOKIE,
    SessionKind,
    SessionStore,
    sign,
)
from plak.config import Settings
from plak.main import SessionRecheckMiddleware
from plak.platform import backchannel, pages

APP_BASE_URL = "https://plak.example"
# The content host of the same app: /-/login and /-/oauth2/callback have one
# spelling on both hosts, so only the Host header tells the two flows apart.
CONTENT_BASE_URL = "https://content.plak.example"

# Sentinel to leave a claim out of the token.
OMIT = object()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_client_jwk() -> str:
    key = RSAKey.generate_key(2048)
    data_ = key.as_dict(private=True)
    data_["kid"] = "client-sleutel-1"
    return json.dumps(data_)


class MockIdP:
    def __init__(self, issuer: str = "https://idp.example", client_id: str = "plak-client") -> None:
        self.issuer = issuer
        self.client_id = client_id
        self.private_key = RSAKey.generate_key(2048)
        self.kid = "idp-sleutel-1"
        public = self.private_key.as_dict(private=False)
        public["kid"] = self.kid
        self.jwks = {"keys": [public]}

        self.access_token = "toegangstoken-123"
        # Refresh grant (helpers for test_session_revalidation.py): the token
        # the mock hands out, what it hands out next, and how it may refuse.
        self.refresh_token = "ververstoken-1"
        self.next_refresh_token: str | None = None
        self.refresh_error: str | None = None
        self.refresh_status = 200
        self.refresh_id_token: bool = True
        self.refresh_claim_overrides: dict = {}
        self.refresh_requests: list[dict[str, list[str]]] = []
        # Override for a token endpoint that answers 200 with a body that is
        # not the JSON object RFC 6749 expects (a proxy's maintenance page).
        self.refresh_response_override: httpx.Response | None = None
        self.sid = "sessie-bij-de-idp-1"
        self.next_nonce: str | None = None
        self.iss_param_supported = False
        self.end_session_supported = False
        self.token_claim_overrides: dict = {}
        self.token_alg = "RS256"
        self.token_key = None  # override to sign with a foreign key
        self.token_requests: list[dict[str, list[str]]] = []
        self.token_request_headers: list[httpx.Headers] = []
        self.jwks_requests = 0

    @property
    def metadata(self) -> dict:
        data_ = {
            "issuer": self.issuer,
            "authorization_endpoint": self.issuer + "/authorize",
            "token_endpoint": self.issuer + "/token",
            "jwks_uri": self.issuer + "/jwks",
            "authorization_response_iss_parameter_supported": self.iss_param_supported,
            "id_token_signing_alg_values_supported": ["RS256", "HS256"],
        }
        if self.end_session_supported:
            data_["end_session_endpoint"] = self.issuer + "/endsession"
        return data_

    def make_id_token(
        self,
        *,
        nonce: str | None,
        alg: str | None = None,
        key=None,
        kid_override: str | None = None,
        with_at_hash: bool = True,
        **overrides,
    ) -> str:
        now_ = int(time.time())
        claims: dict = {
            "iss": self.issuer,
            "sub": "gebruiker-1",
            "aud": self.client_id,
            "exp": now_ + 600,
            "iat": now_,
            "acr": "urn:acr:hoog",
            "email": "gebruiker@example.nl",
            "email_verified": True,
            "sid": self.sid,
        }
        if nonce is not None:
            claims["nonce"] = nonce
        if with_at_hash:
            claims["at_hash"] = _b64url(hashlib.sha256(self.access_token.encode("ascii")).digest()[:16])
        for name, value in overrides.items():
            if value is OMIT:
                claims.pop(name, None)
            else:
                claims[name] = value

        alg = alg or self.token_alg
        key = key or self.token_key or self.private_key
        header = {"alg": alg}
        if kid_override is not None:
            header["kid"] = kid_override
        elif alg != "HS256" and key is self.private_key:
            header["kid"] = self.kid
        if isinstance(key, str | bytes):
            key = OctKey.import_key(key)
        return jwt.encode(header, claims, key, algorithms=[alg])

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json=self.metadata)
        if path == "/jwks":
            self.jwks_requests += 1
            return httpx.Response(200, json=self.jwks)
        if path == "/token" and request.method == "POST":
            form = parse_qs(request.content.decode("ascii"))
            self.token_requests.append(form)
            self.token_request_headers.append(request.headers)
            if form.get("grant_type") == ["refresh_token"]:
                return self._refresh_response(form)
            id_token = self.make_id_token(
                nonce=self.next_nonce, **self.token_claim_overrides
            )
            return httpx.Response(
                200,
                json={
                    "access_token": self.access_token,
                    "token_type": "Bearer",
                    "id_token": id_token,
                    "refresh_token": self.refresh_token,
                },
            )
        return httpx.Response(404)

    def _refresh_response(self, form: dict[str, list[str]]) -> httpx.Response:
        self.refresh_requests.append(form)
        if self.refresh_response_override is not None:
            return self.refresh_response_override
        if self.refresh_error is not None:
            return httpx.Response(self.refresh_status, json={"error": self.refresh_error})
        if self.refresh_status != 200:
            return httpx.Response(self.refresh_status, json={"error": "server_error"})
        body: dict = {"access_token": self.access_token, "token_type": "Bearer"}
        if self.refresh_id_token:
            body["id_token"] = self.make_id_token(
                nonce=None, with_at_hash=False, **self.refresh_claim_overrides
            )
        if self.next_refresh_token is not None:
            body["refresh_token"] = self.next_refresh_token
        return httpx.Response(200, json=body)

    def make_logout_token(
        self,
        *,
        sid: str | None = None,
        sub: str | None = None,
        jti: object = "logout-token-1",
        key=None,
        with_event: bool = True,
        **overrides,
    ) -> str:
        """Logout token per OIDC Back-Channel Logout 1.0 section 2.4."""
        now_ = int(time.time())
        claims: dict = {
            "iss": self.issuer,
            "aud": self.client_id,
            "iat": now_,
            # Two minutes out, as Keycloak sets it.
            "exp": now_ + 120,
        }
        if isinstance(jti, str):
            claims["jti"] = jti
        if sid is not None:
            claims["sid"] = sid
        if sub is not None:
            claims["sub"] = sub
        if with_event:
            claims["events"] = {"http://schemas.openid.net/event/backchannel-logout": {}}
        for name, value in overrides.items():
            if value is OMIT:
                claims.pop(name, None)
            else:
                claims[name] = value
        key = key or self.private_key
        header = {"alg": "RS256"}
        if key is self.private_key:
            header["kid"] = self.kid
        return jwt.encode(header, claims, key, algorithms=["RS256"])


CLIENT_SECRET = "client-geheim-van-de-mock-idp"


def make_settings(idp: MockIdP, **overrides) -> Settings:
    """private_key_jwt by default; with oidc_client_auth=client_secret_* a
    client secret takes the place of the JWK."""
    default = {
        "db_url": "postgresql+asyncpg://plak:plak@localhost:5432/plak",
        # Dummy: no test in this module touches the content store.
        "content_root": "/onbestaand/plak-content",
        "oidc_issuer": idp.issuer,
        "oidc_client_id": idp.client_id,
        "oidc_client_private_jwk": make_client_jwk(),
        "oidc_required_acr": "urn:acr:hoog,urn:acr:substantieel",
        "session_secret": "sessie-geheim-van-minstens-32-bytes!",
        "audit_pepper": "audit-pepper-van-minstens-32-bytes!!",
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "environment": "dev",
        "content_base_url": CONTENT_BASE_URL,
    }
    if overrides.get("oidc_client_auth", "private_key_jwt") != "private_key_jwt":
        default["oidc_client_private_jwk"] = ""
        default["oidc_client_secret"] = CLIENT_SECRET
    default.update(overrides)
    return Settings(**default)


def make_oidc_client(settings: Settings, idp: MockIdP) -> OidcClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(idp.handler))
    return OidcClient(settings, http)


def make_app(settings: Settings, idp: MockIdP) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    app.state.session_store = SessionStore()
    app.state.oidc_client = make_oidc_client(settings, idp)
    register_error_handlers(app)
    app.include_router(pages.router)
    app.include_router(backchannel.router)
    app.add_middleware(SessionRecheckMiddleware)
    return app


def make_test_client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=APP_BASE_URL,
        follow_redirects=False,
    )


def make_content_test_client(app: FastAPI) -> httpx.AsyncClient:
    """Same app, but on the content host: that is what puts a request on
    /-/login into the content flow."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=CONTENT_BASE_URL,
        follow_redirects=False,
    )


def set_session_cookie(
    client: httpx.AsyncClient,
    app: FastAPI,
    *,
    sub: str = "gebruiker-1",
    email: str | None = "gebruiker@example.nl",
    email_verified: bool = True,
    acr: str = "urn:acr:hoog",
    refresh_token: str | None = None,
    sid: str | None = None,
):
    """Creates a server-side admin session directly and sets the signed cookie."""
    store: SessionStore = app.state.session_store
    session = store.create_session(
        sub=sub, email=email, email_verified=email_verified, acr=acr, refresh_token=refresh_token, sid=sid
    )
    token = sign(app.state.settings.session_secret, session.id)
    client.cookies.set(SESSION_COOKIE, token, domain="plak.example", path="/")
    return session


def set_content_session_cookie(
    client: httpx.AsyncClient,
    app: FastAPI,
    *,
    sub: str = "gebruiker-1",
    email: str | None = "gebruiker@example.nl",
    email_verified: bool = True,
    acr: str = "urn:acr:hoog",
    refresh_token: str | None = None,
    sid: str | None = None,
    sites: Sequence[str] = (),
):
    """Creates a server-side content session (viewer) directly and sets its
    cookies: the anchor, plus the site cookie for every
    `/{group}/{site}/` in `sites`. Like a browser, the client sends a site
    cookie only to the site it belongs to, so a test that leaves `sites` empty
    is a visitor who has not opened that site yet."""
    store: SessionStore = app.state.session_store
    session = store.create_session(
        sub=sub,
        email=email,
        email_verified=email_verified,
        acr=acr,
        kind=SessionKind.CONTENT,
        refresh_token=refresh_token,
        sid=sid,
    )
    token = sign(app.state.settings.session_secret, session.id)
    client.cookies.set(CONTENT_ANCHOR_COOKIE, token, domain="plak.example", path=CONTENT_ANCHOR_PATH)
    for prefix in sites:
        store.note_content_site(session.id, prefix)
        client.cookies.set(CONTENT_SESSION_COOKIE, token, domain="plak.example", path=prefix)
    return store.get_session(session.id) or session


async def start_login(
    client: httpx.AsyncClient,
    idp: MockIdP,
    *,
    return_to: str | None = None,
    path_login: str = "/-/login",
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """GET on the login route; returns the query parameters of the authorization URL."""
    params = {"returnTo": return_to} if return_to is not None else None
    response = await client.get(path_login, params=params, headers=headers)
    assert response.status_code == 302, response.text
    authorization_url = httpx.URL(response.headers["location"])
    assert str(authorization_url).startswith(idp.issuer + "/authorize?")
    q = dict(authorization_url.params)
    idp.next_nonce = q["nonce"]
    return q


async def complete_login(
    client: httpx.AsyncClient,
    idp: MockIdP,
    *,
    return_to: str | None = None,
    send_iss: bool = True,
    iss_value: str | None = None,
    path_login: str = "/-/login",
    path_callback: str = "/-/oauth2/callback",
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Full login flow through the mock IdP; returns the callback response."""
    q = await start_login(client, idp, return_to=return_to, path_login=path_login, headers=headers)
    callback_params = {"code": "code-123", "state": q["state"]}
    if send_iss:
        callback_params["iss"] = iss_value if iss_value is not None else idp.issuer
    return await client.get(path_callback, params=callback_params)
