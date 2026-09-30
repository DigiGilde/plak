"""Security headers as app middleware: there is no nginx in
front of the app to set them.

- `Permissions-Policy` on every response.
- HSTS on every response, only when the admin origin is https.
- On the admin host: `Cross-Origin-Opener-Policy: same-origin` and
  `X-Content-Type-Options: nosniff` on every response. When the response does
  not carry a CSP yet, HTML (such as the API docs) gets the full admin CSP
  plus `X-Robots-Tag: noindex`, and everything else (JSON API) gets
  `frame-ancestors 'none'`. The SPA and the neutral 404 carry their own full
  CSP; content keeps the content CSP from serving/response.py.

The regime follows the HOST, not the path. Following the path would make a
refusal on the content host tell tales: `/admin/x` refused with
`Cross-Origin-Opener-Policy` and `/bestaat/niet/` refused without it is one
neutral 404 in two shapes, which gives away that there is an admin world
behind this origin. On the content host every response, refusal included,
carries the two general headers and nothing more.

Existing headers are never overwritten.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from plak.host_separation import host_from_scope
from plak.platform.spa import ADMIN_CSP

HSTS = "max-age=31536000; includeSubDomains"
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"
COOP = "same-origin"
FRAME_ANCESTORS_CSP = "frame-ancestors 'none'"
NOSNIFF = "nosniff"
NOINDEX = "noindex, nofollow"


def is_https(base_url: str | None) -> bool:
    return bool(base_url) and base_url.lower().startswith("https://")


def is_html(headers: MutableHeaders) -> bool:
    return headers.get("content-type", "").lower().startswith("text/html")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool, content_host: str, admin_csp: str = ADMIN_CSP) -> None:
        self.app = app
        self.hsts = hsts
        self.admin_csp = admin_csp
        self.content_host = content_host.lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        admin = host_from_scope(scope) != self.content_host

        async def send_met_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "permissions-policy" not in headers:
                    headers["Permissions-Policy"] = PERMISSIONS_POLICY
                if self.hsts and "strict-transport-security" not in headers:
                    headers["Strict-Transport-Security"] = HSTS
                if admin:
                    html = is_html(headers)
                    if "cross-origin-opener-policy" not in headers:
                        headers["Cross-Origin-Opener-Policy"] = COOP
                    if "x-content-type-options" not in headers:
                        headers["X-Content-Type-Options"] = NOSNIFF
                    if "content-security-policy" not in headers:
                        headers["Content-Security-Policy"] = self.admin_csp if html else FRAME_ANCESTORS_CSP
                    if html and "x-robots-tag" not in headers:
                        headers["X-Robots-Tag"] = NOINDEX
            await send(message)

        await self.app(scope, receive, send_met_headers)


__all__ = [
    "COOP",
    "FRAME_ANCESTORS_CSP",
    "HSTS",
    "NOINDEX",
    "NOSNIFF",
    "PERMISSIONS_POLICY",
    "SecurityHeadersMiddleware",
    "is_html",
    "is_https",
]
