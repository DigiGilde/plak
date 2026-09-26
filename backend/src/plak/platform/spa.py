"""Beheer SPA: the built Vue app (Vite base `/`) from PLAK_SPA_PATH, served by
the app itself on the root of the beheer host.

Pure ASGI middleware, outside the rate limit (SPA assets are unlimited) and
inside the host separation. It answers on the beheer host only; on
the content host it steps aside completely, because the fallback below would
otherwise hand out the interface for every content path.

Where the boundary runs. The SPA answers every path on that host except the
ones the app claims itself:

- the platform namespace `/-/...`, where every app endpoint lives (API, login,
  callback, logout). The SPA has pages there too, but only the ones named in
  SPA_PAGE_PATHS; everything else under `/-/` belongs to the app, so a typo in
  an API path (`/-/apx/v1/overview`) stays a 404 instead of coming back as the
  whole interface with status 200;
- the locations the web pins down (`/robots.txt`, `/favicon.ico`,
  `/.well-known/...`): a crawler, a browser or an ACME client has to get the
  real answer or a 404 there, never an HTML page with status 200;
- `/healthz`, which is internal only.

Everything else is the SPA: an existing file is served statically, every other
path gets index.html (client-side routing). Path validation is Starlette's
StaticFiles.lookup_path: a path pointing outside the SPA directory does not
exist and falls back to index.html.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import anyio
from starlette.datastructures import Headers
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from plak.constants import (
    INTERNAL_ONLY_PATHS,
    PLATFORM_PREFIX,
    STANDARD_LOCATIONS,
    path_under,
)
from plak.host_separation import host_from_scope
from plak.serving import mime

# The SPA's own pages inside the platform namespace, from
# frontend/src/router.ts. They are named one by one because the app owns `/-/`
# in full: anything under it that is not an app endpoint and not one of these
# is a 404. test_spa.py holds this list and the router's together, so the two
# cannot drift apart.
SPA_PAGE_PATHS = tuple(
    f"{PLATFORM_PREFIX}/{page}"
    for page in ("about", "accessibility", "groups", "members", "privacy", "profile", "sessions")
)


def admin_csp(*form_targets: str) -> str:
    """The strict beheer regime, alongside the content CSP from
    serving/response.py.

    form-action has no fallback to default-src, so without it a form injected
    into this page could post a session elsewhere. `form_targets` widens it for
    the logout form only: Chrome checks every redirect of a form navigation
    against form-action, and logout passes the content host (to end that
    session too) and, with RP-initiated logout on, the IdP.
    """
    form_action = " ".join(("form-action 'self'", *form_targets))
    return (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        f"object-src 'none'; frame-ancestors 'none'; base-uri 'self'; {form_action}"
    )


ADMIN_CSP = admin_csp()

CACHE_ASSETS = "max-age=31536000, immutable"
CACHE_OTHER = "no-cache"

INDEX = "index.html"

MESSAGE_SPA_MISSING = (
    "De beheeromgeving is niet beschikbaar: de gebouwde SPA ontbreekt. "
    "Bouw de frontend (npm run build) of wijs PLAK_SPA_PATH naar de dist-map.\n"
)


def is_spa_path(path: str) -> bool:
    """Whether the SPA answers this path (on the beheer host; the middleware
    decides about the host itself).

    The rule is a negative one, and that is the point: since the move to the
    root the SPA is the fallback for the whole host, so what the app claims has
    to be named here rather than the other way around.
    """
    if path in INTERNAL_ONLY_PATHS:
        return False
    if any(path_under(path, location) for location in STANDARD_LOCATIONS):
        return False
    if path_under(path, PLATFORM_PREFIX):
        return any(path_under(path, page) for page in SPA_PAGE_PATHS)
    return True


def spa_headers(rel_path: str, csp: str = ADMIN_CSP) -> dict[str, str]:
    """Fixed header set for every SPA response."""
    return {
        "Cache-Control": CACHE_ASSETS if rel_path.startswith("assets/") else CACHE_OTHER,
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "X-Robots-Tag": "noindex, nofollow",
        "Cross-Origin-Opener-Policy": "same-origin",
    }


def spa_available(spa_path: Path) -> bool:
    return (spa_path / INDEX).is_file()


def _unavailable(csp: str = ADMIN_CSP) -> Response:
    headers = spa_headers("", csp)
    headers["Cache-Control"] = "no-store"
    return PlainTextResponse(MESSAGE_SPA_MISSING, status_code=503, headers=headers)


def _with_query(path: str, scope: Scope) -> str:
    query = scope.get("query_string", b"").decode("latin-1")
    return f"{path}?{query}" if query else path


class SpaMiddleware:
    def __init__(self, app: ASGIApp, *, spa_path: Path, content_host: str, csp: str = ADMIN_CSP) -> None:
        self.app = app
        self.spa_path = spa_path
        self.csp = csp
        self.content_host = content_host.lower()
        # check_dir=False: a missing directory is a 503 per request, not a
        # crash at startup (in dev the directory can be built later).
        self._static = StaticFiles(directory=str(spa_path), check_dir=False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or host_from_scope(scope) == self.content_host:
            await self.app(scope, receive, send)
            return
        response = await self._make_response(scope)
        if response is None:
            await self.app(scope, receive, send)
            return
        await response(scope, receive, send)

    async def _make_response(self, scope: Scope) -> Response | None:
        """The response for this path, or None when the app answers it itself."""
        path: str = scope["path"]
        if not is_spa_path(path):
            return None
        if scope["method"] not in ("GET", "HEAD"):
            return Response(status_code=405, headers={"Allow": "GET, HEAD"})
        if not spa_available(self.spa_path):
            return _unavailable(self.csp)

        rel = path.lstrip("/")
        try:
            full_path, stat_result = await anyio.to_thread.run_sync(self._static.lookup_path, rel)
        except (OSError, ValueError):
            # Name too long, null bytes and the like: no file, so an SPA route.
            stat_result = None
        if stat_result is None or not stat.S_ISREG(stat_result.st_mode):
            rel = INDEX
            full_path, stat_result = await anyio.to_thread.run_sync(self._static.lookup_path, rel)
            if stat_result is None:
                return _unavailable(self.csp)
        return self._file(full_path, stat_result, rel, scope)

    def _file(self, full_path: str, stat_result: os.stat_result, rel: str, scope: Scope) -> Response:
        response = FileResponse(
            full_path, stat_result=stat_result, headers=spa_headers(rel, self.csp), media_type=mime.determine(rel)
        )
        if self._static.is_not_modified(response.headers, Headers(scope=scope)):
            return NotModifiedResponse(response.headers)
        return response


__all__ = [
    "ADMIN_CSP",
    "CACHE_ASSETS",
    "CACHE_OTHER",
    "MESSAGE_SPA_MISSING",
    "SPA_PAGE_PATHS",
    "SpaMiddleware",
    "admin_csp",
    "is_spa_path",
    "spa_available",
    "spa_headers",
]
