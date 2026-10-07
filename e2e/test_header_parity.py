"""The app's own header set (spec §5, §9).

At the builder level, always active, no containers needed: calls the header
builders of `serving/response.py` (content) and `platform/spa.py` (admin SPA)
directly and checks the header set the app puts together itself. The app
serves everything itself, so there is no second path (nginx/X-Accel) to
compare against; what remains is the contract: the fixed header set per
response kind, the 304 without touching the store, the byte-identical neutral
404 and the SPA headers.

Running:

    cd backend && uv run pytest ../e2e/test_header_parity.py -v
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from plak.constants import AccessBase, AccessPolicy
from plak.platform import spa
from plak.serving import response

# The SPA middleware steps aside on the content host, so it has to know
# which host that is; the value only has to differ from the base_url below.
CONTENT_HOST = "plak.localhost"

# Added by FileResponse/the server, not part of the contract.
EXTRA = {"content-length", "last-modified", "accept-ranges"}

# Headers the spec names explicitly as the contract for content (spec §5).
CONTENT_HEADERS = (
    "ETag",
    "Content-Type",
    "Cache-Control",
    "X-Content-Type-Options",
    "Content-Security-Policy",
    "Referrer-Policy",
)

# Headers the spec demands for everything the SPA serves (spec §9, §5.9).
SPA_HEADERS = {
    "Content-Security-Policy": spa.ADMIN_CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Robots-Tag": "noindex, nofollow",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def _header(headers: dict[str, str], name: str) -> str | None:
    return headers.get(name.lower())


class TestContentHeaderset:
    def _response(self, tmp_path: Path, version_id: uuid.UUID, *, noindex: bool) -> tuple[int, dict[str, str]]:
        tmp_path.mkdir(parents=True, exist_ok=True)
        file_path = tmp_path / "index.html"
        file_path.write_text("<html></html>", encoding="utf-8")
        resp = response.make_content_response(
            rel_path="index.html",
            file_path=file_path,
            version_id=version_id,
            access=AccessPolicy(AccessBase.PUBLIC),
            version_view=False,
            noindex=noindex,
            external_sources=False,
            sandbox=False,
        )
        # Starlette lowercases header names while iterating.
        return resp.status_code, dict(resp.headers)

    def test_content_response_carries_exactly_the_fixed_header_set(self, tmp_path: Path) -> None:
        version_id = uuid.uuid4()
        status, headers = self._response(tmp_path, version_id, noindex=False)
        assert status == 200
        assert set(headers) - EXTRA == {name.lower() for name in CONTENT_HEADERS}
        assert _header(headers, "ETag") == response.etag_for(version_id)
        assert _header(headers, "Content-Type") == "text/html; charset=utf-8"
        assert _header(headers, "Cache-Control") == "no-cache, must-revalidate"
        assert _header(headers, "X-Content-Type-Options") == "nosniff"
        assert _header(headers, "Content-Security-Policy") == response.CONTENT_CSP
        assert _header(headers, "Referrer-Policy") == "strict-origin-when-cross-origin"

    def test_noindex_adds_only_x_robots_tag_to(self, tmp_path: Path) -> None:
        version_id = uuid.uuid4()
        _, without = self._response(tmp_path, version_id, noindex=False)
        _, with_noindex = self._response(tmp_path / "noindex", version_id, noindex=True)
        assert set(with_noindex) - set(without) == {"x-robots-tag"}
        assert _header(with_noindex, "X-Robots-Tag") == response.NOINDEX
        for name in CONTENT_HEADERS:
            assert _header(with_noindex, name) == _header(without, name), name

    def test_304_headers_follow_from_path_and_decision(self) -> None:
        # Behaviour requirement 4 (spec §5): the app answers If-None-Match itself,
        # without touching the store; the headers follow purely from path and decision.
        version_id = uuid.uuid4()
        resp_304 = response.make_304(
            "index.html",
            version_id,
            AccessPolicy(AccessBase.PUBLIC),
            version_view=False,
            noindex=False,
            external_sources=False,
            sandbox=False,
        )
        assert resp_304.status_code == 304
        headers_304 = dict(resp_304.headers)
        assert _header(headers_304, "ETag") == response.etag_for(version_id)
        assert _header(headers_304, "Cache-Control") == "no-cache, must-revalidate"
        assert _header(headers_304, "X-Content-Type-Options") == "nosniff"
        # A browser keeps the stored CSP unless the 304 repeats it.
        assert _header(headers_304, "Content-Security-Policy") == response.CONTENT_CSP
        assert _header(headers_304, "Referrer-Policy") == "strict-origin-when-cross-origin"
        assert _header(headers_304, "Content-Type") is None

    def test_neutral_404_is_byte_identical_regardless_of_reason(self) -> None:
        r1 = response.neutral_404_response()
        r2 = response.neutral_404_response()
        assert r1.body == r2.body == response.NEUTRAL_404_BODY
        assert dict(r1.headers) == dict(r2.headers)


async def _inner(scope: Scope, receive: Receive, send: Send) -> None:
    await PlainTextResponse("app")(scope, receive, send)


class TestSpaHeaderset:
    def _client(self, tmp_path: Path) -> TestClient:
        # No `with`: that would send the lifespan protocol through the bare inner
        # app; the middleware has no startup work.
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<!doctype html><div id=app></div>", encoding="utf-8")
        (dist / "assets" / "index-abc123.js").write_text("export {}", encoding="utf-8")
        return TestClient(
            spa.SpaMiddleware(_inner, spa_path=dist, content_host=CONTENT_HOST),
            base_url="http://beheer.plak.localhost",
        )

    def test_header_builder_per_kind(self) -> None:
        index = spa.spa_headers("index.html")
        asset = spa.spa_headers("assets/index-abc123.js")
        assert index["Cache-Control"] == "no-cache"
        assert asset["Cache-Control"] == "max-age=31536000, immutable"
        for name, value in SPA_HEADERS.items():
            assert index[name] == value, name
            assert asset[name] == value, name

    def test_index_html_via_the_middleware(self, tmp_path: Path) -> None:
        resp = self._client(tmp_path).get("/aurora/site")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/html; charset=utf-8"
        assert resp.headers["cache-control"] == "no-cache"
        for name, value in SPA_HEADERS.items():
            assert resp.headers[name] == value, name

    def test_asset_via_the_middleware(self, tmp_path: Path) -> None:
        resp = self._client(tmp_path).get("/assets/index-abc123.js")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "text/javascript; charset=utf-8"
        assert resp.headers["cache-control"] == "max-age=31536000, immutable"
        for name, value in SPA_HEADERS.items():
            assert resp.headers[name] == value, name

    def test_app_paths_carry_no_spa_headers(self, tmp_path: Path) -> None:
        resp = self._client(tmp_path).get("/-/api/v1/overview")
        assert resp.text == "app"
        assert "content-security-policy" not in resp.headers
