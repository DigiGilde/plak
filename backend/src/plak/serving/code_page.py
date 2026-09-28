"""The code page: a secret link shared without its code.

A secret link is `?key=selector.verifier`. Sharing it whole puts link and code
in one message; whoever has the message is in. This is the other way of
sharing the same link: `?key=selector` goes through one channel and the
verifier (the code) through another. The selector alone shows this page, the
code opens the same door, and the visitor ends up with the same
`__Secure-plak-key` cookie as a full link would have set.

The page says as little as it can: no site title, no group name, nothing but
that a code is needed. It only ever appears for a selector that belongs to a
usable key of a live site that has secret links on (access/gate.py,
`code_page_needed`); every other case keeps the neutral 404, or the page
would tell an outsider which selectors exist.

The one POST on the content host lives here. There is no session and no CSRF
token for an anonymous visitor, so what guards it instead is: same-origin only
(`Origin`/`Sec-Fetch-Site`), a hard limit per selector on top of the per-IP
limit of the `code` rate-limit class, and the fact that a successful POST only
sets a cookie for a key whose code the caller has just proved to know. The
code travels in the body, never in the query string, so it does not land in a
log, in the history or in a `Referer`.

The app has to supply on app.state: settings, session_factory, audit_log and
code_attempts (an `InMemoryCounter` for the limit per selector).
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from functools import partial
from html import escape
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Request
from sqlalchemy import select
from starlette.datastructures import FormData
from starlette.formparsers import FormParser
from starlette.responses import RedirectResponse, Response

from plak import i18n, net
from plak.access import keys
from plak.access.decision import REASON_KEY_CODE_INVALID, REASON_KEY_CODE_THROTTLED
from plak.audit import vocabulary
from plak.audit.log import ANONYMOUS, AuditLog
from plak.auth import sessions
from plak.constants import PATH_CONTENT_CODE
from plak.host_separation import host_from_scope
from plak.models.identity import Group
from plak.models.publication import Site
from plak.ratelimit import RateLimitClass, limit_for
from plak.serving.response import CONTENT_CSP, NOINDEX, neutral_404_response

router = APIRouter()

AUDIT_ACTION = vocabulary.CONTENT_ACCESS
AUDIT_KIND = "code"
_AUDIT_PATH_MAX = 200

# A form with three fields; anything larger is not this form.
MAX_BODY_BYTES = 4096
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"

# The same header set as protected content, plus no-store: the page carries a
# form whose answer opens a site, and nothing about it may be kept.
#
# Referrer-Policy is same-origin rather than the no-referrer of content: under
# no-referrer Chrome posts this form with `Origin: null`, which costs the
# origin guard its teeth. The page's own URL carries only the selector, never
# the verifier, so a Referer to our own POST target leaks nothing.
CODE_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Vary": "Accept-Language",
    "Content-Security-Policy": CONTENT_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Robots-Tag": NOINDEX,
}

# Its own small stylesheet, like the front page has: the content host carries
# no design system and a stylesheet of its own would need a route under `/-/`.
# Sizes in rem and em, so everything grows with the text at 200 percent, and
# nothing has a fixed width, so 320 CSS px needs no horizontal scrolling.
_CODE_PAGE_CSS = """
:root {
  color-scheme: light;
  --plak-ground: #fff;
  --plak-text: #1c2022;
  --plak-muted: #46535a;
  --plak-brand: #154273;
  --plak-brand-contrast: #fff;
  --plak-line: #767676;
  --plak-critical: #a90000;
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
    --plak-line: #9aa4a9;
    --plak-critical: #ff8a80;
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
  max-width: 32rem;
  margin: 0 auto;
  padding: 1.5rem 1rem 2rem;
}

h1 {
  margin: 0 0 0.5rem;
  color: var(--plak-brand);
  font-size: 1.75rem;
  line-height: 1.2;
}

.intro {
  color: var(--plak-muted);
}

label {
  display: block;
  font-weight: 700;
}

input[type="text"] {
  width: 100%;
  margin-top: 0.25rem;
  padding: 0.5em;
  border: 1px solid var(--plak-line);
  border-radius: 0.25em;
  background: var(--plak-ground);
  color: var(--plak-text);
  font: inherit;
}

button {
  margin-top: 1rem;
  padding: 0.6em 1.2em;
  border: 0;
  border-radius: 0.25em;
  background: var(--plak-brand);
  color: var(--plak-brand-contrast);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}

button:hover {
  text-decoration: underline;
}

:focus-visible {
  outline: 3px solid var(--plak-text);
  outline-offset: 2px;
}

.error {
  margin: 0.5rem 0 0;
  border-left: 4px solid var(--plak-critical);
  padding-left: 0.75rem;
  color: var(--plak-critical);
}
"""


def code_page_html(selector: str, path: str, error: str = "", locale: str = i18n.DEFAULT) -> str:
    """The code page. `selector` and `path` ride along in the form, because the
    POST lands on `/-/code` and no longer knows which address the visitor came
    for."""
    say = partial(i18n.t, locale)
    error_html = (
        f'<p class="error" id="code-fout" role="alert">{escape(error)}</p>' if error else ""
    )
    described = ' aria-describedby="code-fout"' if error else ""
    invalid = ' aria-invalid="true"' if error else ""
    return f"""<!doctype html>
<html lang="{locale}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{say("code.title")}</title>
<style>{_CODE_PAGE_CSS}</style>
</head>
<body>
<div class="page">
<main>
<h1>{say("code.heading")}</h1>
<p class="intro">{say("code.intro")}</p>
<form method="post" action="{PATH_CONTENT_CODE}">
<input type="hidden" name="selector" value="{escape(selector, quote=True)}">
<input type="hidden" name="path" value="{escape(path, quote=True)}">
<label for="code">{say("code.label")}</label>
<input type="text" id="code" name="code" autocomplete="off" autocapitalize="off"
 autocorrect="off" spellcheck="false" autofocus{described}{invalid}>
{error_html}
<p><button type="submit">{say("code.submit")}</button></p>
</form>
</main>
</div>
</body>
</html>
"""


def code_page_response(request: Request, selector: str, path: str, error: str = "") -> Response:
    """The code page in the visitor's language; `Accept-Language` is all there
    is to go on, the same source the front page reads."""
    locale = i18n.negotiate(request.headers.get("accept-language"))
    return Response(
        content=code_page_html(selector, path, error, locale),
        status_code=200,
        media_type="text/html; charset=utf-8",
        headers=CODE_PAGE_HEADERS,
    )


def valid_target(value: str) -> str | None:
    """A path within our own origin, or None.

    The same rule as a returnTo (auth/sessions.py): no scheme, no host, no
    backslashes or control characters. A Location built from the body of an
    anonymous POST is otherwise an open redirect.
    """
    target = sessions.valid_return_to(value, "")
    return target or None


def _same_origin(request: Request) -> bool:
    """Whether this POST comes from a page on the content host itself.

    There is no session and no CSRF token for an anonymous visitor, so this and
    the rate limit are what a form posted from another site runs into. The
    scheme is deliberately not compared: dev serves the same host over http.

    `Origin: null` is not another site. Chrome anonymises the Origin of a form
    navigation when the document was served with `Referrer-Policy:
    no-referrer`, so a literal `null` says nothing about where the form came
    from; the decision falls through to `Sec-Fetch-Site`, which Chrome does
    send as `same-origin` for that navigation. A real cross-site Origin still
    fails.

    A request carrying neither header (curl) passes: it brings no ambient
    credentials, and its answer is a cookie for a code it supplied itself. The
    limit per selector applies to it like to everything else.
    """
    origin = request.headers.get("Origin")
    if origin is not None and origin.strip().lower() != "null":
        try:
            host = urlsplit(origin.strip()).hostname
        except ValueError:
            return False
        return host == request.app.state.settings.content_host
    fetch_site = request.headers.get("Sec-Fetch-Site")
    return fetch_site is None or fetch_site.strip().lower() in ("same-origin", "none")


async def _audit(request: Request, reason_code: str, refs: dict) -> None:
    log: AuditLog = request.app.state.audit_log
    await log.write(
        AUDIT_ACTION,
        ANONYMOUS,
        vocabulary.REFUSED,
        reason_code=reason_code,
        refs=refs,
        ip=net.client_ip_from_request(request),
    )


async def _over_the_limit(request: Request, selector: str) -> bool:
    """Counts this attempt against the limit per selector.

    Unknown selectors are counted too, and by the value that was submitted: a
    locked-out selector then looks exactly like one that never existed, so the
    lockout is not an existence oracle of its own.
    """
    limit = limit_for(request.app.state.settings, RateLimitClass.CODE)
    counter = request.app.state.code_attempts
    result = await counter.increment(f"selector:{selector}", limit.window_s, time.monotonic())
    return result.count > limit.max


class _BodyTooLargeError(Exception):
    """Raised mid-stream once the body read so far exceeds MAX_BODY_BYTES,
    for a chunked request that carries no Content-Length to check upfront."""


async def _bounded_form(request: Request) -> FormData:
    total = 0

    async def bounded_stream() -> AsyncGenerator[bytes, None]:
        nonlocal total
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_BODY_BYTES:
                raise _BodyTooLargeError
            yield chunk

    parser = FormParser(request.headers, bounded_stream())
    return await parser.parse()


@router.post(PATH_CONTENT_CODE, include_in_schema=False)
async def submit_code(request: Request) -> Response:
    """Hands in the code of a secret link. Everything that is not this form
    coming from this host is the neutral 404, the same answer as every other
    refusal on the content host."""
    settings = request.app.state.settings
    if host_from_scope(request.scope) != settings.content_host or not _same_origin(request):
        return neutral_404_response()
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > MAX_BODY_BYTES:
        return neutral_404_response()
    # The form this page posts, and nothing else: a multipart body would need
    # a parser this host has no other use for.
    if not request.headers.get("content-type", "").startswith(FORM_CONTENT_TYPE):
        return neutral_404_response()

    try:
        form = await _bounded_form(request)
    except _BodyTooLargeError:
        return neutral_404_response()
    selector = str(form.get("selector") or "")[: keys.SELECTOR_LENGTH]
    code = str(form.get("code") or "")
    target = valid_target(str(form.get("path") or ""))
    site_path = sessions.parse_site_path(target) if target is not None else None
    if target is None or site_path is None:
        return neutral_404_response()
    group, site = site_path

    refs = {
        "kind": AUDIT_KIND,
        "group": group,
        "site": site,
        "path": target[:_AUDIT_PATH_MAX],
        "selector": selector,
    }

    if await _over_the_limit(request, selector):
        # The same answer as a wrong code, with one line more: waiting is the
        # only thing that helps, and saying so costs nothing that the limit
        # itself does not already say.
        keys.compare_dummy(code)
        await _audit(request, REASON_KEY_CODE_THROTTLED, refs)
        locale = i18n.negotiate(request.headers.get("accept-language"))
        message = f"{i18n.t(locale, 'code.wrong')} {i18n.t(locale, 'code.later')}"
        return code_page_response(request, selector, target, message)

    session_factory = request.app.state.session_factory
    async with session_factory() as db:
        site_id = await db.scalar(
            select(Site.id)
            .join(Group, Site.group_id == Group.id)
            .where(
                Group.slug == group,
                Site.slug == site,
                Site.access_keys.is_(True),
                Site.live_version_id.is_not(None),
            )
        )
        key = await keys.verify_parts(db, site_id, selector, code)
        key_id = str(key.id) if key is not None else None

    if key_id is None:
        await _audit(request, REASON_KEY_CODE_INVALID, refs)
        locale = i18n.negotiate(request.headers.get("accept-language"))
        return code_page_response(request, selector, target, i18n.t(locale, "code.wrong"))

    # From here on exactly what a full `?key=` link does (serving/router.py):
    # the signed key cookie, scoped to this site, and on to the page itself.
    # The audit row for the view is written there, on the request that follows.
    response = RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        sessions.KEY_COOKIE,
        sessions.sign_key_cookie(settings.session_secret, key_id),
        path=f"/{quote(group)}/{quote(site)}/",
        httponly=True,
        secure=True,
        samesite="none",
    )
    return response


__all__ = ["CODE_PAGE_HEADERS", "code_page_html", "code_page_response", "router"]
