"""Login, callback, logout and landing routes.

The app has to supply on app.state: settings (Settings), session_store
(SessionStore), oidc_client (OidcClient) and, for admin endpoints elsewhere,
session_factory (async_sessionmaker).

Two login flows share the same OIDC client and the same code, but produce a
different session kind:

- admin: `/-/login` and `/-/oauth2/callback` on the admin host, yielding an
  admin session (Strict cookie plus CSRF cookie);
- content: the same two paths on the content host, yielding a content session
  (Lax cookie, no admin authority, no member record).

Both hosts spell those two paths the same way, so the host decides which flow
a request enters (`_profile_for`). On top of that the login attempt carries
the kind, so a callback can never redeem an attempt from the other flow.

The root of the content host is answered here too, with the public front page
(see front_page_html). The root of the admin host is not: since the SPA moved
there the SPA middleware answers it, and this router never sees it.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from html import escape
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from starlette.responses import Response

from plak import i18n, net
from plak.api.errors import ApiError
from plak.api.origin_guard import REASON_OTHER_ORIGIN, admin_origin_ok
from plak.audit import vocabulary
from plak.audit.log import ANONYMOUS, Actor, AuditLog
from plak.auth.content_viewers import upsert_content_viewer
from plak.auth.oidc import OidcClient, OidcError
from plak.auth.sessions import (
    CONTENT_LOGIN_COOKIE,
    DEFAULT_CONTENT_RETURN_TO,
    DEFAULT_RETURN_TO,
    LOGIN_COOKIE,
    SessionKind,
    SessionStore,
    check_signature,
    clear_content_session_cookies,
    clear_session_cookies,
    content_anchor_session_from_request,
    content_site_prefix,
    session_from_request,
    set_content_anchor_cookies,
    set_content_session_cookie,
    set_session_cookies,
    sign,
    top_level_navigation,
    valid_return_to,
)
from plak.constants import (
    PATH_CONTENT_LOGOUT,
    PATH_CONTENT_OAUTH2_PREFIX,
    PATH_LOGIN,
    PATH_LOGOUT,
    PATH_OAUTH2_PREFIX,
)
from plak.models.audit import ActorKind
from plak.platform.security_txt import PATH_SECURITY_TXT, security_txt
from plak.serving.response import CONTENT_CSP, neutral_404_response

if TYPE_CHECKING:
    from plak.config import Settings

_logger = logging.getLogger(__name__)

router = APIRouter()

MESSAGE_LOGIN_FAILED = i18n.NL["login.failed"]


def _login_failed(request: Request) -> str:
    """The sentence a failed login shows, in the visitor's language.

    There is no session yet at this point, so `Accept-Language` is all there
    is to go on; the same source the front page reads.
    """
    return i18n.t(i18n.negotiate(request.headers.get("accept-language")), "login.failed")

# One route, two answers: on the admin host the SPA is everything,
# so nothing there is for a crawler; on the content host the published sites
# are the point. Since the SPA moved to the root, `Disallow: /admin` would
# leave the whole interface open to crawlers.
ROBOTS_TXT_ADMIN = "User-agent: *\nDisallow: /\n"
ROBOTS_TXT_CONTENT = "User-agent: *\nDisallow:\n"


@dataclass(frozen=True)
class _LoginProfile:
    kind: SessionKind
    login_cookie: str
    callback_path: str
    default_return_to: str


_ADMIN = _LoginProfile(
    kind=SessionKind.ADMIN,
    login_cookie=LOGIN_COOKIE,
    callback_path=PATH_OAUTH2_PREFIX + "callback",
    default_return_to=DEFAULT_RETURN_TO,
)
_CONTENT = _LoginProfile(
    kind=SessionKind.CONTENT,
    login_cookie=CONTENT_LOGIN_COOKIE,
    callback_path=PATH_CONTENT_OAUTH2_PREFIX + "callback",
    default_return_to=DEFAULT_CONTENT_RETURN_TO,
)


def on_content_host(request: Request) -> bool:
    """Whether this request arrived on the configured content host."""
    host = request.headers.get("host", "").split(":")[0].lower()
    return host == request.app.state.settings.content_host


def _profile_for(request: Request) -> _LoginProfile:
    """Which of the two flows a request on a shared path belongs to.

    `/-/login` and `/-/oauth2/callback` are spelled identically on both hosts,
    so the Host header is what tells them apart: the content flow exists only
    on the configured content host.
    """
    return _CONTENT if on_content_host(request) else _ADMIN


def _redirect_uri(request: Request, profile: _LoginProfile) -> str:
    # Production hardening: derive the redirect_uri from the configured base
    # URL of our own origin instead of the (client-controlled) Host header;
    # without that setting (dev) this falls back to request.base_url.
    settings = request.app.state.settings
    configured = (
        settings.content_base_url if profile.kind is SessionKind.CONTENT else settings.base_url
    )
    base = configured.rstrip("/") if configured else str(request.base_url).rstrip("/")
    return base + profile.callback_path


# --- Public front page on the root of the content host ---------------------

# The same sentence the admin environment carries above its pages, so someone
# who meets Plak on either side is told the same thing.
BETA_NOTICE = i18n.NL["beta.bar"]
#
# Without it, whoever hears the name of the service and types the content
# host into the address bar would land on the neutral 404. This page catches
# that guess and hands over the one thing that host cannot do itself: the way
# to the admin environment.
#
# It carries NO authority whatsoever. This is the content origin, the same
# origin as uploaded sites with their own JavaScript (see api/origin_guard.py:
# the admin API refuses that origin precisely because a browser counts it as
# same-site). So: plain server-rendered HTML, no script at all, no session
# read, no API call, no CSRF token, no SPA. The stylesheet is inline because
# the content CSP allows inline style and a file of its own would need a route
# under `/-/`, which host separation keeps off this host on purpose. The
# button to the admin environment is a plain link to the other origin.
#
# This is not a landing page for the ADMIN host: the SPA already carries one
# there (Start.vue picks between landing and overview based on the session),
# and a server-rendered page on that host would leave two, with logging out
# landing on the server-rendered one as a blank page. No SPA runs on the
# content host, so here this page is the only landing there is.

FRONT_PAGE_HEADERS = {
    "Cache-Control": "no-cache",
    # The body depends on Accept-Language, so a cache that keys on the URL
    # alone would hand the Dutch page to an English visitor and the other way
    # round.
    "Vary": "Accept-Language",
    "Content-Security-Policy": CONTENT_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}

# The name of each language, written in that language: an English reader has to
# recognise the way out without reading Dutch first.
_LANGUAGE_NAMES = {"nl": "Nederlands", "en": "English"}

# A parameter rather than the /en prefix NCSC.nl uses: this host owns
# /{group}/{site}, so a path prefix would cost a group slug.
LANG_PARAM = "lang"

# Path plus the catalogue key for its label: the same links in the same
# order as the SPA footer (frontend/src/App.vue). They are public SPA routes
# (`meta: { public: true }` in frontend/src/router.ts), so this footer is the
# first place where the accessibility statement - which has to be publicly
# reachable by law - can be found without knowing the address.
_FOOTER_LINKS = (
    ("/-/about", "footer.about"),
    ("/-/accessibility", "footer.accessibility"),
    ("/-/privacy", "footer.privacy"),
    ("/-/api/docs", "footer.api"),
)

# Own CSS in the style of the API docs shell (static/docs/docs.css): the
# wordmark with the block, Rijksblauw, and a dark variant that follows the
# reader's preference. Sizes in rem and em, so everything grows along with the
# text at 200%; nothing has a fixed width, so 320 CSS px needs no horizontal
# scrolling.
_FRONT_PAGE_CSS = """
:root {
  color-scheme: light;
  --plak-ground: #fff;
  --plak-text: #1c2022;
  --plak-muted: #46535a;
  --plak-brand: #154273;
  --plak-brand-contrast: #fff;
  --plak-line: #d4d4d4;
}

@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --plak-ground: #1c2022;
    --plak-text: #e7eaec;
    --plak-muted: #b3bcc1;
    /* Rijksblauw reaches only 3:1 on this ground; this lighter tint 6.5:1. */
    --plak-brand: #8fb8e0;
    --plak-brand-contrast: #1c2022;
    --plak-line: #3b4245;
  }
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  background: var(--plak-ground);
  color: var(--plak-text);
  font-family: "RO Sans", "Segoe UI", system-ui, sans-serif;
  line-height: 1.6;
  overflow-wrap: break-word;
}

.page {
  max-width: 42rem;
  margin: 0 auto;
  padding: 1.5rem 1rem 2rem;
}

.brand {
  display: flex;
  align-items: center;
  gap: 0.2em;
  margin: 0 0 1.5rem;
  color: var(--plak-brand);
  font-size: 2rem;
  line-height: 1.2;
}

.brand__block {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.4em;
  height: 1.4em;
  border-radius: 0.15em;
  background: var(--plak-brand);
  color: var(--plak-brand-contrast);
  font-size: 0.85em;
  font-weight: 700;
  line-height: 1;
}

/* The wordmark is drawn in two pieces; this gives a screen reader the name
   once and whole, instead of "P" and "lak" beside each other. */
.brand__name {
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}

.intro {
  font-size: 1.15rem;
}

/* The beta notice, hand-built because this page carries no design system: it is
   server-rendered HTML with its own small stylesheet. The measurements are
   nldd-status-bar's own, so the notice looks the same on both hosts: 24px high
   (--semantics-controls-xs-min-size), 8px inline padding, centred, one line
   cut off with an ellipsis, in body-xs-medium.

   #db6d00 with black text is what the warning variant resolves to, in light
   AND in dark mode: the background token takes oranje-400 in light and
   oranje-600 in dark, and those two swap, so both land on the same colour.
   Black on it measures 6.2:1.

   The outline rather than the fill alone keeps the bar visible in
   forced-colors mode, where background colours are dropped. */
.beta {
  display: flex;
  margin: 0;
  align-items: center;
  justify-content: center;
  min-height: 24px;
  padding-inline: 8px;
  outline: 1px solid transparent;
  background: #db6d00;
  color: #000;
  font-size: 0.889rem;
  font-weight: 500;
}

/* min-height rather than a fixed height, so the bar grows when the text does.
   The admin host reaches the same end through the component's own token
   (--components-status-bar-height in frontend/src/global.css); this page has
   no design system, so it says it in plain CSS.

   The truncation sits on the span, not on the bar: text-overflow does not
   reach the anonymous box that a flex container wraps bare text in. */
.beta > span {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

h2 {
  margin-top: 2.5rem;
  font-size: 1.25rem;
}

ol {
  padding-left: 1.3em;
}

ol li + li {
  margin-top: 0.5em;
}

a {
  color: var(--plak-brand);
}

.button {
  display: inline-block;
  padding: 0.6em 1.2em;
  border-radius: 0.25em;
  background: var(--plak-brand);
  color: var(--plak-brand-contrast);
  font-size: 1.05rem;
  font-weight: 700;
  text-decoration: none;
}

.button:hover {
  text-decoration: underline;
}

a:focus-visible {
  outline: 3px solid var(--plak-text);
  outline-offset: 2px;
}

.site-footer {
  margin-top: 3rem;
  border-top: 1px solid var(--plak-line);
  padding-top: 1rem;
  color: var(--plak-muted);
}

/* Under the footer nav rather than in it: switching language is not one of
   the pages that nav is labelled for. */
.language-switch {
  margin: 1rem 0 0;
}

.site-footer ul {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1.5rem;
  margin: 0;
  padding: 0;
  list-style: none;
}

/* A flex item gets min-width: auto, and `overflow-wrap: break-word` on the body
   does not lower that minimum: the item stays as wide as its longest word. At
   320px with the text enlarged and user spacing applied, "Toegankelijkheid"
   then runs past the right edge. Zero lets it shrink, after which break-word
   does its work. */
.site-footer li {
  min-width: 0;
}
"""

# The wordmark in two pieces, with the name once more for a screen reader
# (see .brand__name in the CSS). The pieces sit against each other without
# whitespace on purpose: the gap between them comes from the CSS.
_WORDMARK = (
    '<h1 class="brand"><span class="brand__block" aria-hidden="true">P</span>'
    '<span aria-hidden="true">lak</span><span class="brand__name">Plak</span></h1>'
)

FRONT_PAGE_INTRO = i18n.NL["front.intro"]

FRONT_PAGE_NAME_STORY = i18n.NL["front.name.story"]


def _language_links(content_origin: str, locale: str) -> tuple[str, str]:
    """The alternates for the head, and the switch for the footer.

    Both are empty without a content origin: an hreflang without an absolute
    URL is worse than none, and there is nothing to point the switch at.
    """
    if not content_origin:
        return "", ""
    root = escape(content_origin.rstrip("/"), quote=True)
    # x-default is the address that negotiates, which is the bare root.
    alternates = [f'<link rel="alternate" hreflang="x-default" href="{root}/">']
    alternates += [
        f'<link rel="alternate" hreflang="{code}" href="{root}/?{LANG_PARAM}={code}">'
        for code in i18n.SUPPORTED
    ]
    other = next(code for code in i18n.SUPPORTED if code != locale)
    switch = (
        f'<p class="language-switch"><a href="{root}/?{LANG_PARAM}={other}" hreflang="{other}" '
        f'lang="{other}">{_LANGUAGE_NAMES[other]}</a></p>'
    )
    return "\n".join(alternates), switch


def front_page_html(
    admin_origin: str,
    locale: str = i18n.DEFAULT,
    content_origin: str = "",
    *,
    version: str = "dev",
) -> str:
    """The front page, with every link to the admin host absolute.

    `admin_origin` is the configured admin origin (PLAK_BASE_URL) without a
    trailing slash, never the Host header: that one belongs to the content
    host here, and is client-controlled besides. `content_origin` is this
    host's own address, needed for the hreflang alternates.
    """
    origin = escape(admin_origin, quote=True)
    # The notes live in the SPA bundle, so there is no day to point at here.
    version_text = (
        i18n.t(locale, "footer.whatsNew")
        if version == "dev"
        else i18n.t(locale, "footer.version").replace("{version}", escape(version))
    )
    footer = "\n".join(
        [f'<li><a href="{origin}/-/whats-new">{version_text}</a></li>']
        + [f'<li><a href="{origin}{path}">{i18n.t(locale, key)}</a></li>' for path, key in _FOOTER_LINKS]
    )
    alternates, switch = _language_links(content_origin, locale)
    say = partial(i18n.t, locale)
    return f"""<!doctype html>
<html lang="{locale}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{say("page.title")}</title>
{alternates}
<style>{_FRONT_PAGE_CSS}</style>
</head>
<body>
<p class="beta"><span>{say("beta.bar")}</span></p>
<div class="page">
<main>
{_WORDMARK}
<p class="intro">{say("front.intro")}</p>
<p>{say("front.lead")}</p>
<p><a class="button" href="{origin}{PATH_LOGIN}">{say("front.login")}</a></p>
<h2>{say("front.steps.heading")}</h2>
<ol>
<li>{say("front.steps.1")}</li>
<li>{say("front.steps.2")}</li>
<li>{say("front.steps.3")}</li>
<li>{say("front.steps.4")}</li>
</ol>
<h2>{say("front.site.heading")}</h2>
<p>{say("front.site.body")}</p>
<h2>{say("front.name.heading")}</h2>
<p>{say("front.name.story")}</p>
</main>
<footer class="site-footer">
<nav aria-label="{say("footer.label")}">
<ul>
{footer}
</ul>
</nav>
{switch}
</footer>
</div>
</body>
</html>
"""


def front_page_response(settings: Settings, locale: str = i18n.DEFAULT) -> Response:
    """The front page, or the neutral 404 when there is nothing to point at.

    Without a configured admin origin the login button and the footer links
    have no address: the content host cannot derive the admin host, and
    guessing it from the Host header would hand a visitor a link they supplied
    themselves. The root then stays what it was.
    """
    origin = (settings.base_url or "").rstrip("/")
    if not origin:
        return neutral_404_response()
    return HTMLResponse(
        front_page_html(origin, locale, settings.content_base_url, version=settings.version),
        headers=FRONT_PAGE_HEADERS,
    )


@router.get("/", include_in_schema=False, response_model=None)
async def front_page(request: Request, lang: str | None = None) -> Response:
    """The public front page. Only the content host gets here: on the admin
    host the SPA middleware answers `/` itself, with the interface, where
    Start.vue picks between landing and overview based on the session.

    `?lang=` beats the header, so the switch in the footer works for someone
    whose browser asks for a language they cannot read. A value we do not
    have falls through to the header rather than being echoed anywhere.
    """
    # Our own constant rather than `lang`: the locale goes into the markup unescaped.
    chosen = next((code for code in i18n.SUPPORTED if code == lang), None)
    locale = chosen or i18n.negotiate(request.headers.get("accept-language"))
    return front_page_response(request.app.state.settings, locale)


@router.get("/robots.txt", include_in_schema=False)
async def robots(request: Request) -> PlainTextResponse:
    text = ROBOTS_TXT_CONTENT if on_content_host(request) else ROBOTS_TXT_ADMIN
    return PlainTextResponse(text)


# The same body on both hosts, so the retrieval URI is covered by a Canonical
# line either way. Everything else under /.well-known/ keeps the neutral 404.
@router.get(PATH_SECURITY_TXT, include_in_schema=False)
async def security_txt_file(request: Request) -> PlainTextResponse:
    return PlainTextResponse(security_txt(request.app.state.settings, datetime.now(UTC)))


async def _audit_auth(
    request: Request,
    action: str,
    actor: Actor,
    result: str,
    *,
    kind: SessionKind,
    reason_code: str | None = None,
) -> None:
    """A refused login has no verified subject: the actor stays anonymous and
    the truncated IP is what ties attempts together. The sub out of an id
    token that did not survive validation is supplied by the requester, so it
    is never pseudonymised into the log."""
    log: AuditLog | None = getattr(request.app.state, "audit_log", None)
    if log is None:
        return
    await log.write(
        action,
        actor,
        result,
        reason_code=reason_code,
        refs={"kind": kind.value},
        ip=net.client_ip_from_request(request),
    )


def _cookie_for_next_site(request: Request, target: str) -> RedirectResponse | None:
    """The content session cookie for the site the visitor is heading to, or
    None when this request gets no shortcut and has to go to the IdP.

    The content cookie is scoped to one `/{group}/{site}/`, so the first
    request to a second site arrives without one and the gate sees an
    anonymous visitor. With the anchor session still valid that costs no login
    and no round trip to the IdP: the same session, one cookie more.

    Only for a top-level navigation. A mint plus the redirect back would
    otherwise be a two-hop way for a page on one site to have the browser
    attach a session to a request aimed at another, which is the very thing
    the scoped cookie takes away.
    """
    if not top_level_navigation(request):
        return None
    session = content_anchor_session_from_request(request)
    if session is None:
        return None
    prefix = content_site_prefix(target)
    if prefix is None:
        return None
    store: SessionStore = request.app.state.session_store
    store.note_content_site(session.id, prefix)
    response = RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})
    set_content_session_cookie(response, session, request.app.state.settings.session_secret, path=prefix)
    return response


async def _start_login(request: Request, return_to: str | None, profile: _LoginProfile) -> RedirectResponse:
    settings = request.app.state.settings
    oidc: OidcClient = request.app.state.oidc_client
    store: SessionStore = request.app.state.session_store

    target = valid_return_to(return_to, profile.default_return_to)
    if profile.kind is SessionKind.CONTENT:
        shortcut = _cookie_for_next_site(request, target)
        if shortcut is not None:
            return shortcut
    try:
        start = await oidc.start_login(_redirect_uri(request, profile))
    except OidcError as error:
        await _audit_auth(
            request, vocabulary.LOGIN, ANONYMOUS, vocabulary.REFUSED, kind=profile.kind, reason_code=error.reason
        )
        raise HTTPException(status_code=502, detail=_login_failed(request)) from error

    attempt = store.create_attempt(
        state=start.state,
        nonce=start.nonce,
        code_verifier=start.code_verifier,
        return_to=target,
        kind=profile.kind,
        self_initiated=profile.kind is not SessionKind.ADMIN or admin_origin_ok(request),
    )
    response = RedirectResponse(start.authorization_url, status_code=302)
    response.set_cookie(
        profile.login_cookie,
        sign(settings.session_secret, attempt.id),
        max_age=600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    return response


async def _handle_callback(request: Request, profile: _LoginProfile) -> RedirectResponse:
    settings = request.app.state.settings
    oidc: OidcClient = request.app.state.oidc_client
    store: SessionStore = request.app.state.session_store
    params = request.query_params

    try:
        metadata = await oidc.metadata()
        # RFC 9207: check the iss parameter first, before any exchange.
        oidc.check_callback_iss(params, metadata)

        if params.get("error"):
            raise OidcError(f"IdP reported an error: {params['error']}", reason=vocabulary.LOGIN_IDP_ERROR)

        login_token = request.cookies.get(profile.login_cookie)
        attempt_id = check_signature(settings.session_secret, login_token) if login_token else None
        attempt = store.take_attempt(attempt_id) if attempt_id else None
        if attempt is None or attempt.kind is not profile.kind:
            raise OidcError("no valid login attempt for this callback", reason=vocabulary.LOGIN_ATTEMPT_MISSING)

        state = params.get("state")
        # Compare as bytes: compare_digest on str raises TypeError for non-ASCII.
        if not state or not hmac.compare_digest(state.encode("utf-8"), attempt.state.encode("utf-8")):
            raise OidcError("state does not match the login attempt", reason=vocabulary.LOGIN_STATE_MISMATCH)

        code = params.get("code")
        if not code:
            raise OidcError("code parameter is missing", reason=vocabulary.LOGIN_CODE_MISSING)

        tokens = await oidc.exchange_code(code, _redirect_uri(request, profile), attempt.code_verifier)
        claims = await oidc.validate_id_token(
            tokens["id_token"],
            nonce=attempt.nonce,
            access_token=tokens.get("access_token"),
        )
    except OidcError as error:
        await _audit_auth(
            request, vocabulary.LOGIN, ANONYMOUS, vocabulary.REFUSED, kind=profile.kind, reason_code=error.reason
        )
        raise HTTPException(status_code=400, detail=_login_failed(request)) from error

    # Session id rotation: an existing session of the same kind is dropped on
    # login.
    if profile.kind is SessionKind.CONTENT:
        old_session = content_anchor_session_from_request(request)
    else:
        old_session = session_from_request(request)
    if old_session is not None:
        store.delete_session(old_session.id)

    session = store.create_session(
        sub=claims["sub"],
        email=claims.get("email"),
        # Strict: bool("false") is True, and only a JSON true verifies.
        email_verified=claims.get("email_verified") is True,
        # Only a string: an IdP that sends a structured name would otherwise
        # put an object where the interface prints a person.
        name=claims["name"] if isinstance(claims.get("name"), str) else None,
        acr=claims["acr"],
        kind=profile.kind,
        id_token=tokens["id_token"],
        # For the periodic re-validation (auth/revalidation.py) and the
        # back-channel logout (platform/backchannel.py); neither ever leaves
        # the server.
        refresh_token=tokens.get("refresh_token"),
        sid=claims.get("sid"),
        self_initiated=attempt.self_initiated,
    )
    await _audit_auth(
        request, vocabulary.LOGIN, Actor(ActorKind.MEMBER, session.sub), vocabulary.ALLOWED, kind=profile.kind
    )
    if profile.kind is SessionKind.CONTENT:
        # Data minimisation: a content-only viewer never gets a
        # members row, so this is the only place their SSO id is traceable
        # from. Always upserted, even without an email claim: the sub is
        # what makes them traceable. Best-effort like the audit log's own
        # write(): a failure here must never block a successful login.
        try:
            await upsert_content_viewer(
                request.app.state.session_factory, session.sub, session.email, session.email_verified
            )
        except Exception:
            _logger.exception("Updating content_viewers skipped (fail-open)")

    target = valid_return_to(attempt.return_to, profile.default_return_to)
    response = RedirectResponse(target, status_code=303)
    if profile.kind is SessionKind.CONTENT:
        set_content_anchor_cookies(response, session, settings.session_secret)
        prefix = content_site_prefix(target)
        if prefix is not None:
            # The site the visitor was heading to, so the login lands them on
            # the page itself rather than on a second redirect.
            store.note_content_site(session.id, prefix)
            set_content_session_cookie(response, session, settings.session_secret, path=prefix)
    else:
        set_session_cookies(response, session, settings.session_secret)
    response.delete_cookie(profile.login_cookie, path="/", secure=True, httponly=True, samesite="lax")
    return response


@router.get(PATH_LOGIN, include_in_schema=False)
async def login(
    request: Request,
    return_to: Annotated[str | None, Query(alias="returnTo")] = None,
) -> RedirectResponse:
    return await _start_login(request, return_to, _profile_for(request))


@router.get(f"{PATH_OAUTH2_PREFIX}callback", include_in_schema=False)
async def callback(request: Request) -> RedirectResponse:
    return await _handle_callback(request, _profile_for(request))


# The value of ?from= that says the content logout is one leg of a logout that
# started on the admin host. A fixed token rather than a URL, so the parameter
# cannot be turned into an open redirect.
_FROM_ADMIN = "beheer"


@router.post(PATH_LOGOUT, include_in_schema=False)
async def logout(request: Request) -> Response:
    """Logging out of the admin host ends the content session as well.

    The browser is sent on to the content host, because that session lives in
    a cookie only that origin can clear. With RP-initiated logout on, the IdP
    comes first and returns to that same leg; its post_logout_redirect_uri is
    then the content logout, which is the one URL to register with the OP.
    """
    if on_content_host(request):
        return await _content_logout(request)

    # The SPA submits a real form from the admin origin, so it passes; a form
    # on the content host does not. POST alone is no guard here: the content
    # origin is same-site with the admin origin, so SameSite=Strict still
    # sends the session cookie along.
    if not admin_origin_ok(request):
        raise ApiError(403, REASON_OTHER_ORIGIN)

    settings = request.app.state.settings
    store: SessionStore = request.app.state.session_store
    session = session_from_request(request)
    if session is not None:
        store.delete_session(session.id)
        await _audit_auth(
            request, vocabulary.LOGOUT, Actor(ActorKind.MEMBER, session.sub), vocabulary.ALLOWED, kind=session.kind
        )

    content_leg = f"{settings.content_base_url.rstrip('/')}{PATH_CONTENT_LOGOUT}?from={_FROM_ADMIN}"
    target = content_leg
    if settings.oidc_rp_logout and session is not None:
        oidc: OidcClient = request.app.state.oidc_client
        target = await oidc.end_session_url(post_logout_redirect_uri=content_leg, id_token=session.id_token) or target

    response = RedirectResponse(target, status_code=303)
    clear_session_cookies(response)
    return response


@router.get(PATH_CONTENT_LOGOUT, include_in_schema=False)
async def content_logout(request: Request) -> Response:
    """GET, because the admin logout arrives here through a 303. Logging
    someone out by making them load this URL is the most harmless forgery
    there is, and the content session carries no CSRF cookie to check."""
    if not on_content_host(request):
        return neutral_404_response()
    return await _content_logout(request)


async def _content_logout(request: Request) -> Response:
    settings = request.app.state.settings
    store: SessionStore = request.app.state.session_store
    session = content_anchor_session_from_request(request)
    if session is not None:
        store.delete_session(session.id)
        await _audit_auth(
            request, vocabulary.LOGOUT, Actor(ActorKind.MEMBER, session.sub), vocabulary.ALLOWED, kind=session.kind
        )
    came_from_admin = request.query_params.get("from") == _FROM_ADMIN and settings.base_url
    target = f"{settings.base_url.rstrip('/')}/" if came_from_admin else DEFAULT_CONTENT_RETURN_TO
    response = RedirectResponse(target, status_code=303)
    clear_content_session_cookies(response, session.content_sites if session is not None else ())
    return response


__all__ = [
    "FRONT_PAGE_HEADERS",
    "FRONT_PAGE_INTRO",
    "FRONT_PAGE_NAME_STORY",
    "MESSAGE_LOGIN_FAILED",
    "front_page_html",
    "front_page_response",
    "on_content_host",
    "router",
]
