"""Server-side sessions with a signed __Host cookie (BFF).

The browser receives only a signed reference to a server-side session record;
claims never stand on their own in the cookie. Signing is HMAC-SHA256 with
PLAK_SESSION_SECRET (itsdangerous style, without the extra dependency). The
store is in-memory: Plak runs with replicas:1, sessions
deliberately do not survive a restart.

Two session kinds: the management session
(`__Host-plak-session`, SameSite=Strict, with a CSRF cookie)
on the management origin and the content session (without management
authority) on the content origin. One store holds both; the field
`kind` plus the separate cookie name keep them apart, also on a single shared
host (dev). `session_from_request` returns management sessions only,
`content_session_from_request` content sessions only.

The content session rides in three cookies, because every site of every group
is served from one hostname under a path prefix and uploaded content may run
its own JavaScript:

- `__Secure-plak-content`, `Path=/{group}/{site}/`: the session id for content
  requests, the same scoping the secret-link cookie already has. It narrows
  what one session reaches: a site this browser has not opened carries no
  cookie, so a request aimed at it is anonymous. It is not the site boundary
  itself, because a cookie path is matched against the requested URL and not
  against the page that asks (serving/router.py, `_foreign_subresource`, and
  the origin per site in serving/response.py). `__Secure-` and no longer
  `__Host-`, because that prefix requires `Path=/`. SameSite=None, see below.
- `__Secure-plak-content-anchor`, `Path=/-/`: the same session id where the
  login, the callback and the logout can see it. Never sent to a content path,
  so it grants nothing there. SameSite=Lax: the login, the callback and the
  logout are top-level navigations, which Lax covers, and this is the cookie
  that can mint a site cookie for the next site.
- `__Host-plak-content-present`, `Path=/`: a flag, no session id and no
  authority. It only tells the serving layer that this browser has a content
  session somewhere, which is what lets a preview or a `_version` view send a
  member to the login instead of the neutral 404 they would otherwise get on
  the first request to a site.

All three carry one server-side session: one lifetime, one `kind`, one
revocation.

SameSite=None on the site cookie (and on the secret-link cookie in
serving/) is what keeps a sandboxed site able to load its own stylesheets,
scripts and images. Published content is served with a CSP sandbox without
`allow-same-origin` (serving/response.py), so the document has an opaque
origin and is cross-site with everything, its own site included: it sends no
Referer, no Origin and `Sec-Fetch-Site: cross-site`, which leaves no request
signal that tells its subresource loads apart from a third party's. Under Lax
the browser withholds the cookie there and every non-public site loses its
assets. What None costs is written out in docs/security.md; the short version
is that a third-party page can then have private assets loaded with the
visitor's credentials, while the pages themselves stay unreadable to it (no
CORS headers anywhere, `frame-ancestors 'none'`). The durable fix is an origin
per site, which makes both the sandbox and this exception unnecessary.
"""

from __future__ import annotations

import base64
import enum
import hashlib
import hmac
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from plak.constants import PLATFORM_PREFIX, PLATFORM_SEGMENT, RESERVED_SLUGS

if TYPE_CHECKING:
    from fastapi import Request, Response

SESSION_COOKIE = "__Host-plak-session"
CSRF_COOKIE = "__Host-plak-csrf"
LOGIN_COOKIE = "__Host-plak-login"
CONTENT_SESSION_COOKIE = "__Secure-plak-content"
CONTENT_ANCHOR_COOKIE = "__Secure-plak-content-anchor"
CONTENT_PRESENCE_COOKIE = "__Host-plak-content-present"
CONTENT_LOGIN_COOKIE = "__Host-plak-content-login"
KEY_COOKIE = "__Secure-plak-key"
CSRF_HEADER = "X-CSRF-Token"

# Where the anchor cookie lives: the platform namespace, which per SLUG_RE can
# never be a group, so this path never overlaps a site.
CONTENT_ANCHOR_PATH = f"{PLATFORM_PREFIX}/"
CONTENT_PRESENT = "1"

MAX_SESSION_AGE = timedelta(hours=12)
MAX_LOGIN_ATTEMPT_AGE = timedelta(minutes=10)

# The root of the beheer host, where the SPA lives (platform/spa.py).
DEFAULT_RETURN_TO = "/"
# The root of the content host is the public front page (platform/pages.py);
# a content login without a valid returnTo (which the serving router always
# supplies) lands there.
DEFAULT_CONTENT_RETURN_TO = "/"


class SessionKind(enum.StrEnum):
    ADMIN = "admin"
    CONTENT = "content"

# Same pattern as InMemoryCounter in ratelimit.py: expired sessions/attempts
# that are never looked up again (e.g. abandoned logins) would otherwise linger
# forever, because get_session()/take_attempt() only clean up what is actually
# looked up.
_CLEANUP_INTERVAL = 128

# Ceiling on the site prefixes one content session remembers. Past it the
# cookie stays in the browser until it closes, and is useless from the moment
# the session is gone: the gate finds no session behind the id.
_MAX_CONTENT_SITES = 32


@dataclass(frozen=True)
class Visitor:
    """Derived from session + request, without a member lookup up front."""

    sub: str | None
    email: str | None
    email_verified: bool
    key_cookie: str | None
    key_query: str | None


@dataclass(frozen=True)
class Session:
    id: str
    sub: str
    email: str | None
    email_verified: bool
    acr: str
    csrf_token: str
    created_at: datetime
    kind: SessionKind = SessionKind.ADMIN
    # The `name` claim, the IdP's own display name. Unlike the email it has no
    # `_verified` counterpart in OIDC, so there is nothing to check it against;
    # it is shown, never matched on.
    name: str | None = None
    # For the id_token_hint of an RP-initiated logout; it never leaves the
    # server except back to the issuer that made it.
    id_token: str | None = None
    # For the periodic re-validation against the IdP (auth/revalidation.py).
    # Never logged, never in an audit ref, never sent to the browser; repr=False
    # keeps it out of a dataclass dump in a log line or a traceback.
    refresh_token: str | None = field(default=None, repr=False)
    # The `sid` claim of the id token: what a back-channel logout matches on.
    sid: str | None = None
    # When the IdP last confirmed this session; the recheck interval counts
    # from here. None means: not since it was created.
    checked_at: datetime | None = None
    # Backoff after a soft failure: no new check before this moment.
    recheck_not_before: datetime | None = None
    # The `/{group}/{site}/` prefixes this content session has handed out a
    # cookie for, so the logout can clear every one of them: a browser only
    # deletes a cookie when the path matches.
    content_sites: frozenset[str] = frozenset()
    # Whether the login that made this session was started from the beheer
    # origin itself. A login a third-party page navigated the browser into is
    # still a valid session, but does not count as fresh where freshness is
    # meant to prove deliberate intent (CLI device approval).
    self_initiated: bool = True

    @property
    def last_confirmed_at(self) -> datetime:
        return self.checked_at or self.created_at


@dataclass(frozen=True)
class LoginAttempt:
    id: str
    state: str
    nonce: str
    code_verifier: str
    return_to: str
    created_at: datetime
    kind: SessionKind = SessionKind.ADMIN
    self_initiated: bool = True


@dataclass
class SessionStore:
    _sessions: dict[str, Session] = field(default_factory=dict)
    _attempts: dict[str, LoginAttempt] = field(default_factory=dict)
    _calls: int = field(default=0, repr=False)

    def create_session(
        self,
        *,
        sub: str,
        email: str | None,
        email_verified: bool,
        acr: str,
        name: str | None = None,
        kind: SessionKind = SessionKind.ADMIN,
        id_token: str | None = None,
        refresh_token: str | None = None,
        sid: str | None = None,
        self_initiated: bool = True,
    ) -> Session:
        self._tick()
        session = Session(
            id=secrets.token_urlsafe(32),
            sub=sub,
            email=email,
            email_verified=email_verified,
            acr=acr,
            name=name,
            csrf_token=secrets.token_urlsafe(32),
            created_at=datetime.now(UTC),
            kind=kind,
            id_token=id_token,
            refresh_token=refresh_token,
            sid=sid,
            self_initiated=self_initiated,
        )
        self._sessions[session.id] = session
        return session

    def note_content_site(self, session_id: str, prefix: str) -> None:
        """Records a site prefix this content session got a cookie for."""
        session = self._sessions.get(session_id)
        if session is None or prefix in session.content_sites or len(session.content_sites) >= _MAX_CONTENT_SITES:
            return
        self._sessions[session_id] = replace(session, content_sites=session.content_sites | {prefix})

    def mark_checked(self, session_id: str, *, refresh_token: str | None, at: datetime) -> Session | None:
        """Records that the IdP confirmed this session, with the rotated
        refresh token when the IdP handed one out. A session that disappeared
        in the meantime stays gone."""
        session = self._sessions.get(session_id)
        if session is None:
            return None
        updated = replace(
            session,
            refresh_token=refresh_token or session.refresh_token,
            checked_at=at,
            recheck_not_before=None,
        )
        self._sessions[session_id] = updated
        return updated

    def defer_check(self, session_id: str, *, until: datetime) -> None:
        """Backoff after a check that could not be completed; the session itself
        stays as it is."""
        session = self._sessions.get(session_id)
        if session is not None:
            self._sessions[session_id] = replace(session, recheck_not_before=until)

    def sessions_for_logout(self, *, sid: str | None, sub: str | None) -> list[Session]:
        """The sessions a back-channel logout token points at: on `sid` when it
        carries one (one login at the OP), otherwise on `sub` (everything of
        this person). Both session kinds count."""
        if sid:
            return [session for session in self._sessions.values() if session.sid == sid]
        if sub:
            return [session for session in self._sessions.values() if session.sub == sub]
        return []

    def get_session(self, session_id: str) -> Session | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if datetime.now(UTC) - session.created_at > MAX_SESSION_AGE:
            self._sessions.pop(session_id, None)
            return None
        return session

    def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def create_attempt(
        self,
        *,
        state: str,
        nonce: str,
        code_verifier: str,
        return_to: str,
        kind: SessionKind = SessionKind.ADMIN,
        self_initiated: bool = True,
    ) -> LoginAttempt:
        self._tick()
        attempt = LoginAttempt(
            id=secrets.token_urlsafe(32),
            state=state,
            nonce=nonce,
            code_verifier=code_verifier,
            return_to=return_to,
            created_at=datetime.now(UTC),
            kind=kind,
            self_initiated=self_initiated,
        )
        self._attempts[attempt.id] = attempt
        return attempt

    def take_attempt(self, attempt_id: str) -> LoginAttempt | None:
        """Fetches a login attempt and removes it straight away (single-use)."""
        attempt = self._attempts.pop(attempt_id, None)
        if attempt is None:
            return None
        if datetime.now(UTC) - attempt.created_at > MAX_LOGIN_ATTEMPT_AGE:
            return None
        return attempt

    def _tick(self) -> None:
        self._calls += 1
        if self._calls % _CLEANUP_INTERVAL == 0:
            self.cleanup()

    def cleanup(self) -> int:
        """Removes expired sessions and login attempts that were never looked up
        again. Returns the number of items removed."""
        now_ = datetime.now(UTC)
        expired_sessions = [
            session_id for session_id, session in self._sessions.items() if now_ - session.created_at > MAX_SESSION_AGE
        ]
        for session_id in expired_sessions:
            del self._sessions[session_id]

        expired_attempts = [
            attempt_id
            for attempt_id, attempt in self._attempts.items()
            if now_ - attempt.created_at > MAX_LOGIN_ATTEMPT_AGE
        ]
        for attempt_id in expired_attempts:
            del self._attempts[attempt_id]

        return len(expired_sessions) + len(expired_attempts)


def sign(secret: str, value: str) -> str:
    signature = hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()
    short = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{value}.{short}"


# Session cookies and the key cookie share PLAK_SESSION_SECRET; a bare id
# would let a signature valid for one cookie be replayed as another (both are
# UUID strings). The prefix ties a signature to this one purpose.
_KEY_COOKIE_PURPOSE = "key:"


def sign_key_cookie(secret: str, key_id: str) -> str:
    """Signs a key id for the __Secure-plak-key cookie, purpose-tagged so its
    signature cannot be replayed as a session or CSRF cookie value."""
    return sign(secret, f"{_KEY_COOKIE_PURPOSE}{key_id}")


def check_signature(secret: str, token: str) -> str | None:
    """Returns the signed value, or None for an invalid token."""
    value, separation, _ = token.rpartition(".")
    if not separation or not value:
        return None
    if not hmac.compare_digest(sign(secret, value), token):
        return None
    return value


def valid_return_to(value: str | None, default: str = DEFAULT_RETURN_TO) -> str:
    """Paths within our own origin only; everything else falls back to `default`.

    The result has to hold up wherever it lands, not only in a `Location`
    header that the response layer happens to percent-encode. Hence the whole
    ASCII control range (C0 plus DEL) and not just CR/LF/NUL: a query
    parameter arrives percent-decoded, and a URL parser drops TAB, CR and LF
    before it works out the origin, so `/<TAB>/evil.example` would otherwise
    read as `//evil.example`. Nothing above DEL is refused: a path may carry
    non-ASCII.
    """
    if not value:
        return default
    if "\\" in value or any(char <= "\x1f" or char == "\x7f" for char in value):
        return default
    if not value.startswith("/") or value.startswith("//"):
        return default
    return value


def _session_from_cookie(request: Request, cookie: str, kind: SessionKind) -> Session | None:
    token = request.cookies.get(cookie)
    if not token:
        return None
    secret = request.app.state.settings.session_secret
    session_id = check_signature(secret, token)
    if session_id is None:
        return None
    store: SessionStore = request.app.state.session_store
    session = store.get_session(session_id)
    if session is None or session.kind is not kind:
        return None
    return session


def session_from_request(request: Request) -> Session | None:
    """Management session from `__Host-plak-session`; a content session never counts here."""
    return _session_from_cookie(request, SESSION_COOKIE, SessionKind.ADMIN)


def content_session_from_request(request: Request) -> Session | None:
    """Content session from the site-scoped cookie; a management session never
    counts here. A request aimed at a site this browser has not opened yet
    carries no such cookie, which is an anonymous visitor as far as the gate
    is concerned."""
    return _session_from_cookie(request, CONTENT_SESSION_COOKIE, SessionKind.CONTENT)


def content_anchor_session_from_request(request: Request) -> Session | None:
    """Content session from the anchor cookie, which only the paths under
    `/-/` receive. This is what lets the login hand out a cookie for the next
    site without a round trip to the IdP."""
    return _session_from_cookie(request, CONTENT_ANCHOR_COOKIE, SessionKind.CONTENT)


def content_presence(request: Request) -> bool:
    """Whether this browser says it has a content session somewhere. A flag,
    not a credential: it carries no session id and grants nothing."""
    return request.cookies.get(CONTENT_PRESENCE_COOKIE) == CONTENT_PRESENT


def top_level_navigation(request: Request) -> bool:
    """Whether the browser calls this request a top-level navigation.

    `Sec-Fetch-Dest` is a forbidden header name: page script cannot set or
    change it, so unlike `Referer` this is nothing an attacking page can
    shape. A request without the header does not count as a navigation, which
    costs a browser that sends no fetch metadata a round trip to the IdP per
    site and nothing else.
    """
    return request.headers.get("Sec-Fetch-Dest", "").strip().lower() == "document"


def parse_site_path(path: str) -> tuple[str, str] | None:
    """The (group, site) a content path addresses, or None when the path is
    not a content path of a site: fewer than two segments, an empty group or
    site, or a group reserved for the platform namespace.

    Percent-encoding is left exactly as it came in: a browser matches a cookie
    path against the encoded request path, so decoding here would hand out a
    cookie the browser never sends back, and the login redirect would loop.
    """
    segments = path.split("?", 1)[0].split("/")
    if len(segments) < 3:
        return None
    group, site = segments[1], segments[2]
    if not group or not site:
        return None
    if group in RESERVED_SLUGS or group == PLATFORM_SEGMENT:
        return None
    return group, site


def site_prefix(group: str, site: str) -> str:
    """The canonical `/{group}/{site}/` prefix for an already-known site."""
    return f"/{group}/{site}/"


def content_site_prefix(path: str) -> str | None:
    """The `/{group}/{site}/` a content path belongs to, or None."""
    site_path = parse_site_path(path)
    if site_path is None:
        return None
    return site_prefix(*site_path)


def _key_id_from_cookie(request: Request) -> str | None:
    """Key id from the signed key cookie. A cookie with a bad signature, or a
    validly signed value from another cookie's purpose, comes back as ""
    rather than None, so the gate refuses it as KEY_INVALID instead of
    treating the visitor as someone without a key."""
    token = request.cookies.get(KEY_COOKIE)
    if token is None:
        return None
    value = check_signature(request.app.state.settings.session_secret, token)
    if value is None or not value.startswith(_KEY_COOKIE_PURPOSE):
        return ""
    return value.removeprefix(_KEY_COOKIE_PURPOSE)


def visitor_from_request(request: Request) -> Visitor:
    """Viewer for the serving layer: based on the content session only."""
    session = content_session_from_request(request)
    return Visitor(
        sub=session.sub if session else None,
        email=session.email if session else None,
        email_verified=session.email_verified if session else False,
        key_cookie=_key_id_from_cookie(request),
        key_query=request.query_params.get("key"),
    )


def set_session_cookies(response: Response, session: Session, secret: str) -> None:
    # SameSite=Strict: the management origin has no cross-site
    # navigation need, and Strict turns away requests started from the content
    # origin.
    response.set_cookie(
        SESSION_COOKIE,
        sign(secret, session.id),
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    # Double submit: readable by the SPA, so deliberately not HttpOnly.
    response.set_cookie(
        CSRF_COOKIE,
        session.csrf_token,
        httponly=False,
        secure=True,
        samesite="strict",
        path="/",
    )


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=True, samesite="strict")


def clear_content_session_cookies(response: Response, paths: Iterable[str] = ()) -> None:
    """Clears the anchor, the presence flag and the site cookie at every path
    this session handed one out for. A site cookie past that list, or one from
    a session the store lost, survives in the browser until it closes and
    opens nothing: revocation is server-side."""
    response.delete_cookie(
        CONTENT_ANCHOR_COOKIE, path=CONTENT_ANCHOR_PATH, secure=True, httponly=True, samesite="lax"
    )
    response.delete_cookie(CONTENT_PRESENCE_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    for path in paths:
        response.delete_cookie(CONTENT_SESSION_COOKIE, path=path, secure=True, httponly=True, samesite="none")


def set_content_anchor_cookies(response: Response, session: Session, secret: str) -> None:
    # SameSite=Lax: a shared link to restricted content has to open
    # straight away on an existing content session, also from mail or chat.
    # No CSRF cookie: the content origin has no session-borne mutations.
    response.set_cookie(
        CONTENT_ANCHOR_COOKIE,
        sign(secret, session.id),
        httponly=True,
        secure=True,
        samesite="lax",
        path=CONTENT_ANCHOR_PATH,
    )
    response.set_cookie(
        CONTENT_PRESENCE_COOKIE,
        CONTENT_PRESENT,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def set_content_session_cookie(response: Response, session: Session, secret: str, *, path: str) -> None:
    """The session cookie for one site. `path` is a `/{group}/{site}/` prefix;
    anything wider would put this cookie on requests another site's page
    makes.

    SameSite=None because a sandboxed site's own document has an opaque origin
    and is therefore cross-site with itself; see the module docstring."""
    response.set_cookie(
        CONTENT_SESSION_COOKIE,
        sign(secret, session.id),
        httponly=True,
        secure=True,
        samesite="none",
        path=path,
    )


def csrf_valid(request: Request, session: Session) -> bool:
    """Double submit: header and cookie must both carry the session CSRF token."""
    header = request.headers.get(CSRF_HEADER)
    cookie = request.cookies.get(CSRF_COOKIE)
    if not header or not cookie:
        return False
    return hmac.compare_digest(header, session.csrf_token) and hmac.compare_digest(cookie, session.csrf_token)


__all__ = [
    "CONTENT_ANCHOR_COOKIE",
    "CONTENT_ANCHOR_PATH",
    "CONTENT_LOGIN_COOKIE",
    "CONTENT_PRESENCE_COOKIE",
    "CONTENT_PRESENT",
    "CONTENT_SESSION_COOKIE",
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "DEFAULT_CONTENT_RETURN_TO",
    "DEFAULT_RETURN_TO",
    "KEY_COOKIE",
    "LOGIN_COOKIE",
    "MAX_LOGIN_ATTEMPT_AGE",
    "MAX_SESSION_AGE",
    "SESSION_COOKIE",
    "LoginAttempt",
    "Session",
    "SessionKind",
    "SessionStore",
    "Visitor",
    "check_signature",
    "clear_content_session_cookies",
    "clear_session_cookies",
    "content_anchor_session_from_request",
    "content_presence",
    "content_session_from_request",
    "content_site_prefix",
    "csrf_valid",
    "parse_site_path",
    "session_from_request",
    "set_content_anchor_cookies",
    "set_content_session_cookie",
    "set_session_cookies",
    "sign",
    "sign_key_cookie",
    "site_prefix",
    "top_level_navigation",
    "valid_return_to",
    "visitor_from_request",
]
