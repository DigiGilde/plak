"""Tests for api/origin_guard.py: the admin API refuses every request that does
not demonstrably come from its own admin origin (spec §4a, §7 origin
separation).

No database needed: the dependency only looks at headers and config.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, Depends, FastAPI

from plak.api.errors import register_error_handlers
from plak.api.origin_guard import normalise_origin, require_admin_origin
from plak.config import Settings

ADMIN_ORIGIN = "https://beheer.plak.example"
CONTENT_ORIGIN = "https://plak.example"
PATH = "/-/api/v1/ping"


def _settings(**overrides) -> Settings:
    base = {
        "db_url": "postgresql+asyncpg://plak:plak@localhost:5432/plak",
        "content_root": "/onbestaand/plak-content",
        "oidc_issuer": "https://idp.example",
        "oidc_client_id": "plak-client",
        "oidc_client_private_jwk": "{}",
        "oidc_required_acr": "urn:acr:hoog",
        "session_secret": "sessie-geheim-van-minstens-32-bytes!",
        "audit_pepper": "audit-pepper-van-minstens-32-bytes!!",
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "content_base_url": "https://plak.example",
    }
    base.update(overrides)
    return Settings(**base)


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    register_error_handlers(app)
    router = APIRouter(dependencies=[Depends(require_admin_origin)])

    @router.get(PATH)
    async def ping() -> dict:
        return {"ok": True}

    @router.post(PATH)
    async def ping_post() -> dict:
        return {"ok": True}

    app.include_router(router)
    return app


def _client(app: FastAPI, base: str = ADMIN_ORIGIN) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base)


@pytest.fixture
def app() -> FastAPI:
    return _app(_settings(base_url=ADMIN_ORIGIN))


@pytest_asyncio.fixture
async def client(app):
    async with _client(app) as client:
        yield client


def _is_problem_403(response: httpx.Response) -> None:
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 403
    assert body["title"]
    assert body["detail"]


class TestNormaliseOrigin:
    @pytest.mark.parametrize(
        ("input_", "expected"),
        [
            ("https://beheer.plak.example", "https://beheer.plak.example"),
            ("HTTPS://Beheer.Plak.Example", "https://beheer.plak.example"),
            ("https://beheer.plak.example:443", "https://beheer.plak.example"),
            ("http://localhost:80", "http://localhost"),
            ("http://localhost:5173", "http://localhost:5173"),
            ("https://host.example:8443", "https://host.example:8443"),
            ("https://host.example/", "https://host.example"),
        ],
    )
    def test_canonical_shape(self, input_, expected):
        assert normalise_origin(input_) == expected

    @pytest.mark.parametrize(
        "input_",
        ["", None, "null", "ftp://host.example", "host.example", "https://", "https://host:poort"],
    )
    def test_unusable_values(self, input_):
        assert normalise_origin(input_) is None


class TestWithOriginHeader:
    async def test_exact_admin_origin_allowed(self, client):
        response = await client.get(PATH, headers={"Origin": ADMIN_ORIGIN})
        assert response.status_code == 200

    async def test_admin_origin_with_default_port_allowed(self, client):
        response = await client.get(PATH, headers={"Origin": ADMIN_ORIGIN + ":443"})
        assert response.status_code == 200

    async def test_content_origin_refused(self, client):
        response = await client.post(PATH, headers={"Origin": CONTENT_ORIGIN})
        _is_problem_403(response)

    async def test_foreign_origin_refused(self, client):
        response = await client.get(PATH, headers={"Origin": "https://evil.example"})
        _is_problem_403(response)

    async def test_null_origin_refused(self, client):
        response = await client.post(PATH, headers={"Origin": "null"})
        _is_problem_403(response)

    async def test_refusal_sets_no_cors_headers(self, client):
        response = await client.get(PATH, headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers

    async def test_allow_sets_no_cors_headers(self, client):
        response = await client.get(PATH, headers={"Origin": ADMIN_ORIGIN})
        assert "access-control-allow-origin" not in response.headers


class TestWithoutOriginHeader:
    async def test_without_headers_allowed(self, client):
        # curl/CI: no browser, no ambient credentials; CSRF covers mutations.
        assert (await client.get(PATH)).status_code == 200

    async def test_sec_fetch_site_same_origin_allowed(self, client):
        response = await client.get(PATH, headers={"Sec-Fetch-Site": "same-origin"})
        assert response.status_code == 200

    async def test_sec_fetch_site_none_allowed(self, client):
        response = await client.get(PATH, headers={"Sec-Fetch-Site": "none"})
        assert response.status_code == 200

    async def test_sec_fetch_site_cross_site_refused(self, client):
        response = await client.get(PATH, headers={"Sec-Fetch-Site": "cross-site"})
        _is_problem_403(response)

    async def test_sec_fetch_site_same_site_refused(self, client):
        # The content origin is a sibling host and counts as same-site.
        response = await client.post(PATH, headers={"Sec-Fetch-Site": "same-site"})
        _is_problem_403(response)


class TestDevWithoutBaseUrl:
    @pytest.fixture
    def dev_app(self) -> FastAPI:
        return _app(_settings())

    async def test_localhost_origin_allowed(self, dev_app):
        async with _client(dev_app, "http://localhost:8000") as client:
            response = await client.get(PATH, headers={"Origin": "http://localhost:5173"})
            assert response.status_code == 200

    async def test_own_origin_allowed(self, dev_app):
        async with _client(dev_app, "https://dev.plak.example") as client:
            response = await client.get(PATH, headers={"Origin": "https://dev.plak.example"})
            assert response.status_code == 200

    async def test_foreign_origin_refused(self, dev_app):
        async with _client(dev_app, "http://localhost:8000") as client:
            response = await client.get(PATH, headers={"Origin": "https://evil.example"})
            _is_problem_403(response)
