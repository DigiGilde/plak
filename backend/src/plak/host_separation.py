"""Host separation inside the app.

Without nginx in front, the app decides per Host which world a path belongs
to. The SPA sits on the root of the admin host, so the two worlds do not
differ in which paths exist there, but in what answers them: on the
admin host the SPA is the fallback for the whole path space, with the app's
own paths carved out of it (platform/spa.py). On the content host only content exists
(`/{group}/{site}/...`), robots.txt, favicon.ico, .well-known, the redirect
to the admin landing page on `/` (platform/pages.py) and of the platform namespace exactly
four paths: the content login, its callback, the content logout and the code
of a secret link shared without it (the segment `-` is never a slug).
Everything else under `/-/` is refused there, the API included - without that
rule moving the API from /beheer/api to /-/api would silently publish it on
the content host; that includes `/-/healthz`, which only the admin host answers
(platform/health.py). Every refusal is byte-identical to every other
neutral 404, headers included (anti-enumeration: SecurityHeadersMiddleware sits
outside this one and reads the host, not the path), and leaves ratelimit, audit
and router untouched.

`/admin` and `/beheer` are not carved out here: on the admin host they fall to
the SPA like any other path (platform/spa.py), and on the content host they
are ordinary content paths, because neither is a reserved group slug.
"""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from plak.constants import (
    PATH_CONTENT_CODE,
    PATH_CONTENT_LOGIN,
    PATH_CONTENT_LOGOUT,
    PATH_CONTENT_OAUTH2_PREFIX,
    PLATFORM_PREFIX,
    path_under,
)
from plak.serving.response import neutral_404_response


def host_from_scope(scope: Scope) -> str:
    """Hostname of the request, without port and lowercase.

    The same derivation as TrustedHostMiddleware, which sits further out and
    has already refused unknown hosts. Shared with the SPA and the security
    headers, which both have to read the host exactly like this one.
    """
    return Headers(scope=scope).get("host", "").split(":")[0].lower()


def belongs_to_content(path: str) -> bool:
    # `/` is not carved out: the root of the content host redirects to the
    # admin landing page (platform/pages.py). Everything the content world does not
    # own is carved out below; what is left over is content.
    if path_under(path, PLATFORM_PREFIX):
        # Only the content login, its callback, logout and the code of a
        # secret link live here. Everything else under /-/ belongs to the
        # admin host alone; the API above all, which this branch is what
        # keeps off the content host.
        return path in (PATH_CONTENT_LOGIN, PATH_CONTENT_LOGOUT, PATH_CONTENT_CODE) or path.startswith(
            PATH_CONTENT_OAUTH2_PREFIX
        )
    return True


class HostSeparationMiddleware:
    def __init__(self, app: ASGIApp, *, content_host: str) -> None:
        self.app = app
        self.content_host = content_host.lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        host = host_from_scope(scope)
        path: str = scope["path"]
        # Every path on the admin host is an app or an SPA path.
        if host != self.content_host or belongs_to_content(path):
            await self.app(scope, receive, send)
            return
        await neutral_404_response()(scope, receive, send)


__all__ = [
    "HostSeparationMiddleware",
    "belongs_to_content",
    "host_from_scope",
]
