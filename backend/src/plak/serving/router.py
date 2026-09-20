"""Serving router: catch-all content routes plus the _preview and _version
subroutes.

Registered last in main.py, because the content routes are catch-alls; it
carries the final route of all too, where every path no route claimed gets
the neutral 404 instead of FastAPI's own JSON 404 (see unmatched_path). The
app has to supply on app.state: settings (Settings), session_store
(SessionStore), session_factory (async_sessionmaker), content_store
(ContentStore) and audit_log (AuditLog).

Order per request: path validation -> access decision -> audit -> key redeem
-> If-None-Match -> file resolution -> response. The lexical 301 (site or
subroute root without a slash) sits in separate routes before any existence
or access check and therefore leaks nothing.

Viewers are content sessions only; a management session never
counts here. A login redirect goes to `/-/login` on the same host. A valid
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
from urllib.parse import parse_qsl, quote, urlencode

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

AUDIT_ACTION = "content_access"
_AUDIT_PATH_MAX = 200

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

    refs: dict = {"kind": kind, "group": group, "site": site, "path": rest[:_AUDIT_PATH_MAX]}
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
        if decision.kind is DecisionKind.ALLOW:
            version = await db.get(Version, decision.version_id)
            storage_ref = version.storage_ref if version is not None else None
            if version is not None:
                external_sources = bool(
                    await db.scalar(select(Site.external_sources).where(Site.id == version.site_id))
                )
            key_cookie = await _key_cookie_value(request, db, group, site, decision, visitor)

    if code_selector is not None:
        refs["selector"] = code_selector
        await _audit(request, visitor, vocabulary.REFUSED, REASON_KEY_CODE_REQUIRED, refs)
        return code_page.code_page_response(request, code_selector, _path_without_key(request))

    if decision.kind is DecisionKind.NEUTRAL_404:
        await _audit(request, visitor, "refused", decision.reason_code, refs)
        return response.neutral_404_response()

    if decision.kind is DecisionKind.LOGIN_REDIRECT:
        await _audit(request, visitor, "login_redirect", decision.reason_code, refs)
        query = urlencode({"returnTo": _path_without_key(request)})
        return RedirectResponse(f"{PATH_CONTENT_LOGIN}?{query}", status_code=302)

    access = decision.effective_access
    version_id_allowed = decision.version_id
    if storage_ref is None or version_id_allowed is None or access is None:
        # Allowed but no (complete) version record: an inconsistent reference.
        await _audit(request, visitor, "refused", REASON_UNKNOWN_STORAGE, refs)
        return response.neutral_404_response()

    version_view = kind == "version"
    noindex = kind in ("preview", "version")

    if key_cookie is not None:
        return _redeem_key(request, key_cookie, group, site, kind, ref)

    # Looking at non-public content is logged, one row per page rather than
    # per stylesheet or image, and kept for the short term (docs/audit-log.md).
    # After the key redemption, whose follow-up request is the actual view.
    if version_view or (not access.is_public and _is_page(rel)):
        if decision.key_selector is not None:
            refs["selector"] = decision.key_selector
        await _audit(request, visitor, vocabulary.ALLOWED, decision.reason_code, refs)

    etag = response.etag_for(version_id_allowed)
    if response.if_none_match_matches(request.headers.get("if-none-match"), etag):
        return response.make_304(
            resolution.display_path(rel),
            version_id_allowed,
            access,
            version_view=version_view,
            noindex=noindex,
        )

    store: ContentStore = request.app.state.content_store
    outcome_ = resolution.resolve(store, storage_ref, rel)

    if outcome_.kind is resolution.ResolutionKind.DIRECTORY_REDIRECT:
        # Directory 301 inside a site: only after an allow decision.
        target = f"{request.url.path}/"
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(target, status_code=301)

    if (
        outcome_.kind is resolution.ResolutionKind.FILE
        and outcome_.rel_path is not None
        and outcome_.file_path is not None
    ):
        return response.make_content_response(
            rel_path=outcome_.rel_path,
            file_path=outcome_.file_path,
            version_id=version_id_allowed,
            access=access,
            version_view=version_view,
            noindex=noindex,
            external_sources=external_sources,
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
        status_code=404,
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
    follow-up request on the cookie route."""
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
        samesite="lax",
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
