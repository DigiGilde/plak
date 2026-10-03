"""Tests for GET /-/healthz (platform/health.py): the public answer on the admin
host. Status and body per state, the database check and its timeout, what the
answer must not reveal, the neutral 404 on the content host and the rate limit.

The storage and content-root checks behind it are in test_storage_health.py."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
from helpers_oidc import APP_BASE_URL, CONTENT_BASE_URL, MockIdP, make_settings

from plak.ingest.store import ContentStore
from plak.main import create_app
from plak.serving.response import NEUTRAL_404_BODY

UNREACHABLE_DB = "postgresql+asyncpg://plak:plak@127.0.0.1:1/plak"


def _app(tmp_path: Path, db_url: str, **overrides):
    return create_app(make_settings(MockIdP(), content_root=tmp_path / "content", db_url=db_url, **overrides))


def _client(app, base: str = APP_BASE_URL) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base)


@pytest.fixture(autouse=True)
def _room(monkeypatch):
    monkeypatch.setattr(ContentStore, "free_bytes", lambda self: 900 * 1024 * 1024)


class TestStatus:
    async def test_healthy_is_200_ok_and_never_cached(self, tmp_path: Path, migrated_dsn: str) -> None:
        app = _app(tmp_path, migrated_dsn)
        async with app.router.lifespan_context(app), _client(app) as client:
            response = await client.get("/-/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert response.headers["cache-control"] == "no-store"

    async def test_needs_no_session_csrf_or_token(self, tmp_path: Path, migrated_dsn: str) -> None:
        app = _app(tmp_path, migrated_dsn)
        async with app.router.lifespan_context(app), _client(app) as client:
            assert not client.cookies
            response = await client.get("/-/healthz")

        assert response.status_code == 200

    async def test_an_unreachable_database_is_503_fail(self, tmp_path: Path, caplog) -> None:
        app = _app(tmp_path, UNREACHABLE_DB)
        with caplog.at_level(logging.WARNING, logger="plak.platform.health"):
            async with app.router.lifespan_context(app), _client(app) as client:
                response = await client.get("/-/healthz")

        assert response.status_code == 503
        assert response.json() == {"status": "fail", "checks": ["database"]}
        assert response.headers["cache-control"] == "no-store"
        assert "the database did not answer" in caplog.text

    async def test_a_database_that_hangs_is_503_within_the_timeout(
        self, tmp_path: Path, migrated_dsn: str, monkeypatch
    ) -> None:
        class Hanging:
            async def __aenter__(self):
                await asyncio.sleep(60)

            async def __aexit__(self, *exc_info) -> None:  # pragma: no cover - never entered
                return None

        app = _app(tmp_path, migrated_dsn)
        monkeypatch.setattr("plak.platform.health.DATABASE_TIMEOUT_SECONDS", 0.05)
        async with app.router.lifespan_context(app):
            app.state.session_factory = Hanging
            async with _client(app) as client:
                started = asyncio.get_running_loop().time()
                response = await client.get("/-/healthz")
                elapsed = asyncio.get_running_loop().time() - started

        assert response.status_code == 503
        assert response.json() == {"status": "fail", "checks": ["database"]}
        assert elapsed < 5

    async def test_fail_carries_the_degraded_checks_too(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(ContentStore, "free_bytes", lambda self: 240 * 1024 * 1024)
        app = _app(tmp_path, UNREACHABLE_DB)
        async with app.router.lifespan_context(app), _client(app) as client:
            response = await client.get("/-/healthz")

        assert response.status_code == 503
        assert response.json() == {"status": "fail", "checks": ["database", "storage"]}

    async def test_an_unmeasurable_volume_is_503_fail_and_never_a_500(
        self, tmp_path: Path, migrated_dsn: str, monkeypatch, caplog
    ) -> None:
        def gone(self) -> int:
            raise FileNotFoundError("content root is gone")

        monkeypatch.setattr(ContentStore, "free_bytes", gone)
        app = _app(tmp_path, migrated_dsn)
        with caplog.at_level(logging.WARNING, logger="plak.platform.health"):
            async with app.router.lifespan_context(app), _client(app) as client:
                response = await client.get("/-/healthz")

        assert response.status_code == 503
        assert response.json() == {"status": "fail", "checks": ["storage"]}
        assert "cannot be measured" in caplog.text
        assert "content root is gone" not in response.text

    async def test_database_and_volume_failing_together_list_database_first(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        def gone(self) -> int:
            raise OSError("statvfs failed")

        monkeypatch.setattr(ContentStore, "free_bytes", gone)
        app = _app(tmp_path, UNREACHABLE_DB)
        async with app.router.lifespan_context(app), _client(app) as client:
            response = await client.get("/-/healthz")

        assert response.status_code == 503
        assert response.json() == {"status": "fail", "checks": ["database", "storage"]}

    async def test_degraded_is_still_200(self, tmp_path: Path, migrated_dsn: str, monkeypatch) -> None:
        monkeypatch.setattr(ContentStore, "free_bytes", lambda self: 240 * 1024 * 1024)
        app = _app(tmp_path, migrated_dsn)
        async with app.router.lifespan_context(app), _client(app) as client:
            response = await client.get("/-/healthz")

        assert response.status_code == 200
        assert response.json() == {"status": "degraded", "checks": ["storage"]}

    async def test_the_answer_has_exactly_the_documented_keys(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(ContentStore, "free_bytes", lambda self: 240 * 1024 * 1024)
        app = _app(tmp_path, UNREACHABLE_DB)
        async with app.router.lifespan_context(app), _client(app) as client:
            response = await client.get("/-/healthz")

        body = response.json()
        assert set(body) == {"status", "checks"}
        assert all(isinstance(check, str) and " " not in check for check in body["checks"])
        assert "127.0.0.1" not in response.text
        assert "postgresql" not in response.text

    async def test_it_is_not_in_the_openapi_schema(self, tmp_path: Path, migrated_dsn: str) -> None:
        app = _app(tmp_path, migrated_dsn)
        assert "healthz" not in json.dumps(app.openapi())


class TestHosts:
    async def test_the_content_host_answers_the_neutral_404(self, tmp_path: Path, migrated_dsn: str) -> None:
        app = _app(tmp_path, migrated_dsn)
        async with app.router.lifespan_context(app), _client(app, CONTENT_BASE_URL) as client:
            healthz = await client.get("/-/healthz")
            other = await client.get("/-/onbekend")

        assert healthz.status_code == 404
        assert healthz.content == NEUTRAL_404_BODY
        assert healthz.content == other.content
        assert dict(healthz.headers) == dict(other.headers)

    async def test_the_old_internal_path_answers_no_health(self, tmp_path: Path, migrated_dsn: str) -> None:
        app = _app(tmp_path, migrated_dsn)
        async with app.router.lifespan_context(app):
            async with _client(app, CONTENT_BASE_URL) as client:
                on_content = await client.get("/healthz")
            async with _client(app) as client:
                on_admin = await client.get("/healthz")

        assert on_content.content == NEUTRAL_404_BODY
        assert '"status"' not in on_admin.text


class TestRateLimit:
    async def test_it_counts_against_the_content_budget(self, tmp_path: Path, migrated_dsn: str) -> None:
        app = _app(tmp_path, migrated_dsn, ratelimit_content_max=2, ratelimit_content_window_s=60)
        async with app.router.lifespan_context(app), _client(app) as client:
            statuses = [(await client.get("/-/healthz")).status_code for _ in range(3)]

        assert statuses == [200, 200, 429]
