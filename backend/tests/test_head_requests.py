"""ContentHeadMiddleware: on the content host HEAD reaches the app as GET and
leaves without a body; everywhere else the request passes as it came."""

from __future__ import annotations

import uuid
from pathlib import Path

from starlette.types import Message, Receive, Scope, Send

from plak.constants import HEAD_SCOPE_KEY, AccessBase, AccessPolicy
from plak.head_requests import ContentHeadMiddleware
from plak.serving.response import make_content_response

CONTENT_HOST = "plak.example"
ADMIN_HOST = "beheer.plak.example"


def _scope(method: str, host: str, path: str = "/aurora/site/", **extra) -> Scope:
    return {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(b"host", host.encode())],
        **extra,
    }


async def _run(scope: Scope) -> tuple[list[Scope], list[Message]]:
    seen: list[Scope] = []
    sent: list[Message] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"4")]})
        await send({"type": "http.response.body", "body": b"body", "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await ContentHeadMiddleware(app, content_host=CONTENT_HOST)(scope, receive, send)
    return seen, sent


async def test_head_on_the_content_host_reaches_the_app_as_get_and_leaves_without_a_body() -> None:
    seen, sent = await _run(_scope("HEAD", CONTENT_HOST))
    assert seen[0]["method"] == "GET"
    assert sent[0] == {"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"4")]}
    assert [message["body"] for message in sent[1:]] == [b"", b""]
    assert sent[1]["more_body"] is True


async def test_the_app_is_told_it_is_answering_a_head() -> None:
    seen, _ = await _run(_scope("HEAD", CONTENT_HOST))
    assert seen[0][HEAD_SCOPE_KEY] is True


async def test_a_file_answers_a_head_without_reading_the_file(tmp_path: Path) -> None:
    """A GET streams a large file in chunks; the HEAD the middleware turned
    into GET gets the same headers and one empty body, so repeating it costs
    the server no more than the answer."""
    big = tmp_path / "groot.bin"
    big.write_bytes(b"x" * (5 * 64 * 1024))
    version_id = uuid.uuid4()

    async def sent_for(scope: Scope) -> list[Message]:
        response = make_content_response(
            rel_path="groot.bin",
            file_path=big,
            version_id=version_id,
            access=AccessPolicy(AccessBase.PUBLIC),
            version_view=False,
            noindex=False,
            external_sources=False,
            sandbox=False,
        )
        sent: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        await response(scope, receive, send)
        return sent

    get = await sent_for(_scope("GET", CONTENT_HOST))
    head = await sent_for({**_scope("GET", CONTENT_HOST), HEAD_SCOPE_KEY: True})
    assert len([m for m in get if m["type"] == "http.response.body"]) > 1
    assert head[0] == get[0]
    assert head[1:] == [{"type": "http.response.body", "body": b"", "more_body": False}]


async def test_the_host_is_compared_without_case() -> None:
    seen, _ = await _run(_scope("HEAD", "PLAK.example"))
    assert seen[0]["method"] == "GET"


async def test_head_on_the_admin_host_passes_untouched() -> None:
    """A content route such as /{group}/{site}/{rest} also matches /-/... on
    the admin host; GET there would turn the 405 of a GET-only API route into
    the neutral 404. A path outside /-/ too, so the host is what decides."""
    for path in ("/aurora/site/", "/robots.txt", "/-/healthz"):
        scope = _scope("HEAD", ADMIN_HOST, path)
        seen, sent = await _run(scope)
        assert seen[0] is scope, path
        assert sent[1]["body"] == b"body", path


async def test_head_under_the_platform_segment_passes_untouched() -> None:
    """The content login, callback and logout act on a GET: a HEAD may never
    start a login."""
    for path in ("/-/login", "/-/oauth2/callback", "/-/logout", "/-"):
        scope = _scope("HEAD", CONTENT_HOST, path)
        seen, _ = await _run(scope)
        assert seen[0] is scope, path


async def test_other_methods_pass_untouched() -> None:
    for method in ("GET", "POST", "DELETE"):
        scope = _scope(method, CONTENT_HOST)
        seen, sent = await _run(scope)
        assert seen[0] is scope, method
        assert sent[1]["body"] == b"body", method


async def test_a_non_http_scope_passes_untouched() -> None:
    scope: Scope = {"type": "lifespan"}
    seen: list[Scope] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)

    async def receive() -> Message:
        return {"type": "lifespan.startup"}

    async def send(message: Message) -> None:
        pass

    await ContentHeadMiddleware(app, content_host=CONTENT_HOST)(scope, receive, send)
    assert seen == [scope]
