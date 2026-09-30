"""Serving router: catch-all content routes plus the _preview and _version
subroutes.

Registered last in main.py, because the content routes are catch-alls; it
carries the final route of all too, where every path no route claimed gets
the neutral 404 instead of FastAPI's own JSON 404 (see unmatched_path). The
app has to supply on app.state: settings (Settings), session_store
(SessionStore), session_factory (async_sessionmaker), content_store
(ContentStore) and audit_log (AuditLog).

Order per request: path validation -> access decision -> fetch-metadata guard
-> audit -> key redeem -> file resolution -> If-None-Match -> response. The
lexical 301 (site or subroute root without a slash) sits in separate routes
before any existence or access check and therefore leaks nothing.

Viewers are content sessions only; a management session never
counts here. The content session cookie is scoped to `/{group}/{site}/`, so a
request aimed at another site carries no session and the gate sees an
anonymous visitor. A login redirect goes to `/-/login` on the same host. A valid
`?key=` is redeemed: the key cookie rides along on a 302 to the same URL
without `key`, so the key is not left behind in the address bar, the history
or logs. A `?key=` that carries the selector alone (a link shared without its
code) gets the code page instead of the neutral 404 and instead of the login
redirect, but only for live content and only when that selector belongs to a
usable key of this site (serving/code_page.py). An allow is audited for
`_version` views only (AVG).
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse, Response

from plak import net
from plak.access import gate, keys
from plak.access.decision import (
    REASON_KEY_CODE_REQUIRED,
    AccessDecision,
    DecisionKind,
    neutral_404,
)
from plak.audit import vocabulary
from plak.audit.log import ANONYMOUS, Actor, AuditLog
from plak.auth import sessions
from plak.constants import PATH_CONTENT_LOGIN, PLATFORM_SEGMENT, RESERVED_SLUGS
from plak.ingest.store import ContentStore
from plak.models.audit import ActorKind
from plak.models.identity import Group
from plak.models.publication import Site, Version
from plak.serving import code_page, resolution, response

REASON_PATH_INVALID = "PATH_INVALID"
REASON_UNKNOWN_VERSION = "UNKNOWN_VERSION"
REASON_UNKNOWN_STORAGE = "UNKNOWN_STORAGE"
REASON_FOREIGN_SUBRESOURCE = "FOREIGN_SUBRESOURCE"

AUDIT_ACTION = "content_access"
_AUDIT_PATH_MAX = 200
_FETCH_TOKEN_MAX = 20

# What a browser reports for a top-level navigation and for an embedded
# document. Everything else is a subresource of some page.
_NAVIGATION_DESTS = frozenset({"document", "iframe", "frame"})

router = APIRouter()


def _is_platform_namespace(group: str) -> bool:
    return group in RESERVED_SLUGS or group == PLATFORM_SEGMENT


def _visitor(request: Request) -> gate.Visitor:
    # The content session only; sessions.visitor_from_request deliberately
    # ignores the management session cookie.
    session_visitor = sessions.visitor_from_request(request)
    return gate.Visitor(
        sub=session_visitor.sub,
        email=session_visitor.email,
        email_verified=session_visitor.email_verified,
        key_cookie=session_visitor.key_cookie,
        key_query=session_visitor.key_query,
    )


def _path_without_key(request: Request) -> str:
    """Current URL (path plus query) without any key parameter: it must never
    end up in a returnTo or a Location."""
    path = request.url.path
    query = [(name, value) for name, value in parse_qsl(request.url.query, keep_blank_values=True) if name != "key"]
    if query:
        path = f"{path}?{urlencode(query)}"
    return path


def _lexical_slash_redirect(request: Request, group: str) -> Response:
    # Purely lexical, before any existence or access check; only the platform
    # namespace (reserved slugs and /-/) drops out.
    if _is_platform_namespace(group):
        return response.neutral_404_response()
    target = f"{request.url.path}/"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(target, status_code=301)


def _is_page(rel: str) -> bool:
    return resolution.display_path(rel).lower().endswith((".html", ".htm"))


def _fetch_token(request: Request, header: str) -> str | None:
    """A `Sec-Fetch-*` value, normalised to the small set of tokens the spec
    allows; anything longer is not one of them and is cut off."""
    value = request.headers.get(header)
    if value is None:
        return None
    return value.strip().lower()[:_FETCH_TOKEN_MAX]


def _referer_within_site(request: Request, group: str, site: str) -> bool:
    referer = request.headers.get("Referer")
    if not referer:
        return False
    try:
        parts = urlsplit(referer)
    except ValueError:
        return False
    if parts.hostname is not None and parts.hostname != request.url.hostname:
        return False
    return parts.path.startswith(sessions.site_prefix(group, site))


def _foreign_subresource(request: Request, group: str, site: str) -> bool:
    """Whether this is a subresource request made by another site's page.

    Every site of every group is served from one hostname under a path prefix,
    so a page that runs its own JavaScript can `fetch()` any other site on the
    same origin. The site-scoped session cookie is what takes the visitor's
    credentials out of such a request; this check stays beside it as a second
    line, and holds where a request carries a credential of its own: non-public
    content goes to a subresource request only when `Referer` puts it inside
    the same `/{group}/{site}/`. The durable fix is an origin per site.

    Top-level navigation is untouched: following a link from one site to
    another is something the visitor does and sees. So is a request that
    carries no `Sec-Fetch-Site` at all (curl, a link checker, a browser older
    than the header): it brings no ambient credentials the way a page-driven
    request does, the same reasoning as `_same_origin` in serving/code_page.py.
    """
    if _fetch_token(request, "Sec-Fetch-Site") != "same-origin":
        return False
    if _fetch_token(request, "Sec-Fetch-Dest") in _NAVIGATION_DESTS:
        return False
    return not _referer_within_site(request, group, site)


async def _audit(
    request: Request,
    visitor: gate.Visitor,
    result: str,
    reason_code: str | None,
    refs: dict,
) -> None:
    log: AuditLog = request.app.state.audit_log
    actor = Actor(ActorKind.MEMBER, visitor.sub) if visitor.sub is not None else ANONYMOUS
    ip = net.client_ip_from_request(request)
    await log.write(
        AUDIT_ACTION, actor, result, reason_code=reason_code, refs=refs, ip=ip
    )


async def _key_cookie_value(
    request: Request,
    db: AsyncSession,
    group: str,
    site: str,
    decision: AccessDecision,
    visitor: gate.Visitor,
) -> str | None:
    """Signed key id for the __Secure cookie after a successful ?key= access;
    None when no cookie needs to be set. Signed, because the key id
    alone would otherwise be a bearer credential for the site."""
    if decision.key_selector is None or not visitor.key_query:
        return None
    site_id = await db.scalar(
        select(Site.id)
        .join(Group, Site.group_id == Group.id)
        .where(Group.slug == group, Site.slug == site)
    )
    if site_id is None:
        return None
    key = await keys.verify(db, site_id, visitor.key_query)
    if key is None:
        # Access then came in on an already-set cookie; nothing to do.
        return None
    return sessions.sign_key_cookie(request.app.state.settings.session_secret, str(key.id))


async def _serve(
    request: Request,
    group: str,
    site: str,
    rest: str,
    *,
    kind: str,
    ref: str | None = None,
    version_str: str | None = None,
) -> Response:
    if _is_platform_namespace(group):
        # Platform namespace (robots.txt, favicon.ico, /-/...): not a content
        # route and no audit; these are routing misses, not access refusals.
        return response.neutral_404_response()

    refs: dict = {
        "kind": kind,
        "group": group,
        "site": site,
        "path": rest[:_AUDIT_PATH_MAX],
        # Fetch metadata, so a request made by another site's page can be told
        # apart afterwards. Fixed tokens from the browser, no personal data.
        "fetch_dest": _fetch_token(request, "Sec-Fetch-Dest"),
        "fetch_site": _fetch_token(request, "Sec-Fetch-Site"),
        # Whether a Referer came along at all, never which one. A refusal on a
        # subresource dest without one is the signature of a page that
        # suppressed its own referrer and so broke its own assets, which reads
        # the same as another site's fetch and is not the same thing.
        "referer_present": bool(request.headers.get("Referer")),
    }
    if ref is not None:
        refs["ref"] = ref
    if version_str is not None:
        refs["version"] = version_str[:_AUDIT_PATH_MAX]

    visitor = _visitor(request)

    rel = resolution.normalise_rest(rest)
    if rel is None:
        await _audit(request, visitor, "refused", REASON_PATH_INVALID, refs)
        return response.neutral_404_response()

    session_factory = request.app.state.session_factory
    async with session_factory() as db:
        if kind == "live":
            decision = await gate.decide(db, group, site, visitor)
        elif kind == "preview" and ref is not None:
            decision = await gate.decide_preview(db, group, site, ref, visitor)
        else:
            try:
                version_id = uuid.UUID(version_str)
            except (TypeError, ValueError):
                decision = neutral_404(REASON_UNKNOWN_VERSION)
            else:
                decision = await gate.decide_version(db, group, site, version_id, visitor)

        code_selector: str | None = None
        if decision.kind in (DecisionKind.NEUTRAL_404, DecisionKind.LOGIN_REDIRECT) and kind == "live":
            # A `?key=` carrying the selector alone: the link was shared
            # without its code. Only for a selector that really belongs to
            # this site does the code page appear, and then it wins over both
            # the neutral 404 and the login redirect: whoever was handed a
            # link is asked for its code, not sent to an IdP.
            selector = keys.bare_selector(visitor.key_query)
            if selector is not None and await gate.code_page_needed(db, group, site, selector):
                code_selector = selector

        storage_ref: str | None = None
        key_cookie: str | None = None
        external_sources = False
        sandbox = False
        if decision.kind is DecisionKind.ALLOW:
            version = await db.get(Version, decision.version_id)
            storage_ref = version.storage_ref if version is not None else None
            if version is not None:
                row = (
                    await db.execute(
                        select(Site.external_sources, Site.sandbox).where(Site.id == version.site_id)
                    )
                ).one_or_none()
                if row is not None:  # pragma: no cover - version.site_id is FK-bound to a Site row
                    external_sources, sandbox = bool(row.external_sources), bool(row.sandbox)
            key_cookie = await _key_cookie_value(request, db, group, site, decision, visitor)

    if code_selector is not None:
        refs["selector"] = code_selector
        await _audit(request, visitor, vocabulary.REFUSED, REASON_KEY_CODE_REQUIRED, refs)
        return code_page.code_page_response(request, code_selector, _path_without_key(request))

    version_view = kind == "version"
    noindex = kind in ("preview", "version")

    # Before the login redirect, and therefore whether or not the visitor has
    # a session: a subresource of another site's page is never a navigation,
    # so nobody logs in because of it, and a 302 would say that this site
    # exists. Not before the neutral 404, which is this answer already and
    # keeps the reason that really refused.
    decided_access = decision.effective_access
    if (
        decision.kind is not DecisionKind.NEUTRAL_404
        and decided_access is not None
        and (version_view or not decided_access.is_public)
        and _foreign_subresource(request, group, site)
    ):
        await _audit(request, visitor, vocabulary.REFUSED, REASON_FOREIGN_SUBRESOURCE, refs)
        return response.neutral_404_response()

    # A preview or a _version view is often the first thing a member opens of
    # a site, and the content session cookie is scoped per site, so that first
    # request carries none; a site without a live version has no live route
    # that could have handed one out. The gate never gives these routes a
    # login redirect of its own, so every anonymous refusal here becomes one,
    # whatever the reason: the answer is then the same for a path with
    # nothing behind it, and guessing paths teaches nothing. Only for a
    # top-level navigation, so another site's page cannot chain this into a
    # session for a request of its own. Not with a key in play, whose holder
    # may have no SSO account at all, and not for a path the login cannot
    # scope a site cookie to, where the visitor would come back anonymous and
    # loop. Live content needs none of this: wherever a session could grant
    # anything there, the gate already answers with the login redirect.
    if (
        decision.kind is DecisionKind.NEUTRAL_404
        and kind in ("preview", "version")
        and visitor.sub is None
        and visitor.key_query is None
        and visitor.key_cookie is None
        and sessions.content_site_prefix(request.url.path) is not None
        and sessions.top_level_navigation(request)
    ):
        # The reason that really refused, so probing for refs or version ids
        # stays visible in the audit log although the answer is the same.
        await _audit(request, visitor, "login_redirect", decision.reason_code, refs)
        return _login_redirect(request)

    if decision.kind is DecisionKind.NEUTRAL_404:
        await _audit(request, visitor, "refused", decision.reason_code, refs)
        return response.neutral_404_response()

    if decision.kind is DecisionKind.LOGIN_REDIRECT:
        await _audit(request, visitor, "login_redirect", decision.reason_code, refs)
        return _login_redirect(request)

    access = decided_access
    version_id_allowed = decision.version_id
    if storage_ref is None or version_id_allowed is None or access is None:
        # Allowed but no (complete) version record: an inconsistent reference.
        await _audit(request, visitor, "refused", REASON_UNKNOWN_STORAGE, refs)
        return response.neutral_404_response()

    if key_cookie is not None:
        return _redeem_key(request, key_cookie, group, site, kind, ref)

    # Looking at non-public content is logged, one row per page rather than
    # per stylesheet or image, and kept for the short term (docs/audit-log.md).
    # After the key redemption, whose follow-up request is the actual view.
    if version_view or (not access.is_public and _is_page(rel)):
        if decision.key_selector is not None:
            refs["selector"] = decision.key_selector
        await _audit(request, visitor, vocabulary.ALLOWED, decision.reason_code, refs)

    store: ContentStore = request.app.state.content_store
    outcome_ = resolution.resolve(store, storage_ref, rel)

    if outcome_.kind is resolution.ResolutionKind.DIRECTORY_REDIRECT:
        # Directory 301 inside a site: only after an allow decision.
        target = f"{request.url.path}/"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(target, status_code=301, headers={"Cache-Control": "no-store"})

    if (
        outcome_.kind is resolution.ResolutionKind.FILE
        and outcome_.rel_path is not None
        and outcome_.file_path is not None
    ):
        # The conditional answer comes after resolution, not before: a 304 for
        # a path that has no file would tell an intermediary that a
        # non-existent resource is fresh.
        etag = response.etag_for(version_id_allowed)
        if response.if_none_match_matches(request.headers.get("if-none-match"), etag):
            return response.make_304(
                outcome_.rel_path,
                version_id_allowed,
                access,
                version_view=version_view,
                noindex=noindex,
            )
        return response.make_content_response(
            rel_path=outcome_.rel_path,
            file_path=outcome_.file_path,
            version_id=version_id_allowed,
            access=access,
            version_view=version_view,
            noindex=noindex,
            external_sources=external_sources,
            sandbox=sandbox,
        )

    path_404 = resolution.find_404_page(store, storage_ref)
    if path_404 is None:
        return response.neutral_404_response()
    # The version's root 404.html, for authorised visitors only.
    return response.make_content_response(
        rel_path=resolution.NOT_FOUND_FILE,
        file_path=path_404,
        version_id=version_id_allowed,
        access=access,
        version_view=version_view,
        noindex=noindex,
        external_sources=external_sources,
        sandbox=sandbox,
        status_code=404,
    )


def _login_redirect(request: Request) -> Response:
    query = urlencode({"returnTo": _path_without_key(request)})
    return RedirectResponse(
        f"{PATH_CONTENT_LOGIN}?{query}", status_code=302, headers={"Cache-Control": "no-store"}
    )


def _redeem_key(
    request: Request,
    key_cookie: str,
    group: str,
    site: str,
    kind: str,
    ref: str | None,
) -> Response:
    """Redeems a valid ?key=: set the cookie and 302 to the same URL without
    key (other query parameters stay). The actual response comes from the
    follow-up request on the cookie route.

    SameSite=None for the same reason as the content session cookie
    (auth/sessions.py): a sandboxed page is cross-site with its own site, so
    under Lax the key would reach the page and none of its assets."""
    if kind == "preview":
        path = f"/{quote(group)}/{quote(site)}/_preview/{quote(ref or '')}/"
    else:
        path = f"/{quote(group)}/{quote(site)}/"
    response = RedirectResponse(_path_without_key(request), status_code=302, headers={"Cache-Control": "no-store"})
    response.set_cookie(
        sessions.KEY_COOKIE,
        key_cookie,
        path=path,
        httponly=True,
        secure=True,
        samesite="none",
    )
    return response


@router.get("/{group}/{site}", include_in_schema=False)
async def site_root_without_slash(request: Request, group: str, site: str) -> Response:
    return _lexical_slash_redirect(request, group)


@router.get("/{group}/{site}/_preview/{ref}", include_in_schema=False)
async def preview_root_without_slash(request: Request, group: str, site: str, ref: str) -> Response:
    return _lexical_slash_redirect(request, group)


@router.get("/{group}/{site}/_preview/{ref}/{rest:path}", include_in_schema=False)
async def preview_content(request: Request, group: str, site: str, ref: str, rest: str) -> Response:
    return await _serve(request, group, site, rest, kind="preview", ref=ref)


@router.get("/{group}/{site}/_version/{version_id}", include_in_schema=False)
async def version_root_without_slash(request: Request, group: str, site: str, version_id: str) -> Response:
    return _lexical_slash_redirect(request, group)


@router.get("/{group}/{site}/_version/{version_id}/{rest:path}", include_in_schema=False)
async def version_content(request: Request, group: str, site: str, version_id: str, rest: str) -> Response:
    return await _serve(request, group, site, rest, kind="version", version_str=version_id)


@router.get("/{group}/{site}/{rest:path}", include_in_schema=False)
async def live_content(request: Request, group: str, site: str, rest: str) -> Response:
    return await _serve(request, group, site, rest, kind="live")


@router.get("/{rest:path}", include_in_schema=False)
async def unmatched_path(rest: str) -> Response:
    """Registered last, behind every route in the app: a path no route claims
    is the neutral 404 as well.

    Without it a miss came in two shapes. `/aurora/bestaat-niet/` reached the
    content routes above and got the neutral 404, while a path that matched no
    route at all (`/onbekend`, `/favicon.ico`, `/-/`) got FastAPI's own
    `{"detail":"Not Found"}`: another body, another header set, another
    content type. On the content host that difference is exactly the
    enumeration signal the neutral 404 exists to remove, and on the beheer host
    it is what keeps a mistyped app path from being told apart from a real one.

    Only GET (and HEAD): a path that does have a route but not for this method
    keeps its 405.
    """
    return response.neutral_404_response()
