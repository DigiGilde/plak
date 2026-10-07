"""HEAD on the content host: the GET, without the body (RFC 9110 §9.3.2).

The routes take GET only. Giving them HEAD as well cannot be done per host:
a content route such as `/{group}/{site}/{rest:path}` also matches `/-/...` on
the admin host, so HEAD on a GET-only admin route would end there as the
neutral 404 instead of its 405. This middleware leaves the admin host alone
and, on the content host, hands the app a GET and the client no body, so a
link checker or a monitor sees what a browser would: the same gate, the same
audit, the same headers, and a refusal that is the same neutral 404. A file
response sees HEAD_SCOPE_KEY and sends its headers without reading the file
(serving/response.py), so a HEAD costs no more than its answer.

Not under `/-/`: the content login, callback and logout act on a GET, and a
HEAD must never start a login. Those keep their 405, and anything else there
stays host separation's neutral 404.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from plak.constants import HEAD_SCOPE_KEY, PLATFORM_PREFIX, path_under
from plak.host_separation import host_from_scope


class ContentHeadMiddleware:
    def __init__(self, app: ASGIApp, *, content_host: str) -> None:
        self.app = app
        self.content_host = content_host.lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "HEAD"
            or host_from_scope(scope) != self.content_host
            or path_under(scope["path"], PLATFORM_PREFIX)
        ):
            await self.app(scope, receive, send)
            return

        async def send_without_body(message: Message) -> None:
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET", HEAD_SCOPE_KEY: True}, receive, send_without_body)
