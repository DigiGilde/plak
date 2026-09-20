"""Integration tests for create_app() (main.py): the full wiring of the rate
limit middleware, session handling, the platform/auth routes and the serving
router as catch-all.

Only `test_unknown_site_gives_neutral_404_by_full_stack` touches a
real PostgreSQL test container (through the conftest fixture
`migrated_dsn`); the other tests need no reachable DB, because the routes
involved never touch it (lexical redirect, /healthz, /-/login).
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from helpers_oidc import MockIdP, make_client_jwk, make_oidc_client, set_content_session_cookie
from sqlalchemy.ext.asyncio import async_sessionmaker

from plak.audit.log import AuditLog
from plak.auth.oidc import OidcClient
from plak.auth.sessions import SessionStore
from plak.config import Settings
from plak.ingest.store import ContentStore
from plak.main import create_app
from plak.serving.response import NEUTRAL_404_BODY

BASE_URL = "https://plak.example"


def _make_settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "db_url": "postgresql+asyncpg://ongebruikt:ongebruikt@localhost:5432/ongebruikt",
        "content_root": tmp_path / "content",
        "oidc_issuer": "https://idp.example",
        "oidc_client_id": "plak-client",
        "oidc_client_private_jwk": make_client_jwk(),
        "oidc_required_acr": "urn:acr:hoog",
        "session_secret": "sessie-geheim-van-minstens-32-bytes!",
        "audit_pepper": "audit-pepper-van-minstens-32-bytes!!",
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "content_base_url": BASE_URL,
    }
    base.update(overrides)
    return Settings(**base)


def _client_for(app: FastAPI, base: str = BASE_URL) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base, follow_redirects=False
    )


async def test_app_boots_with_test_settings(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        assert app.state.settings is settings
        assert isinstance(app.state.session_store, SessionStore)
        assert isinstance(app.state.content_store, ContentStore)
        assert isinstance(app.state.oidc_client, OidcClient)
        assert app.state.session_factory is not None
        assert app.state.audit_log is not None


async def test_startup_logs_the_idp_coupling_without_any_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """One INFO line naming the issuer and the client id, so a wrong coupling
    shows up straight away. The private key and the client secret never do."""
    private_jwk = make_client_jwk()
    settings = _make_settings(
        tmp_path,
        oidc_issuer="https://idp.example/realms/plak",
        oidc_client_id="plak-client",
        oidc_client_private_jwk=private_jwk,
    )
    app = create_app(settings)

    with caplog.at_level(logging.INFO, logger="plak.main"):
        async with app.router.lifespan_context(app):
            pass

    lines = [row.getMessage() for row in caplog.records if row.name == "plak.main"]
    coupling = [line for line in lines if "OIDC-koppeling" in line]
    assert len(coupling) == 1
    assert "https://idp.example/realms/plak" in coupling[0]
    assert "plak-client" in coupling[0]
    assert private_jwk not in caplog.text
    assert settings.session_secret not in caplog.text
    assert settings.audit_pepper not in caplog.text


async def test_startup_log_of_the_idp_coupling_carries_no_client_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _make_settings(
        tmp_path,
        oidc_client_auth="client_secret_post",
        oidc_client_private_jwk="",
        oidc_client_secret="geheim-van-de-client",
    )
    app = create_app(settings)

    with caplog.at_level(logging.INFO, logger="plak.main"):
        async with app.router.lifespan_context(app):
            pass

    assert "OIDC-koppeling" in caplog.text
    assert "geheim-van-de-client" not in caplog.text


async def test_healthz_does_not_exist_on_a_public_host(tmp_path: Path) -> None:
    """Spec §4a/§11: the probe hits the pod directly, so `/healthz` exists on
    neither public host, and both of them refuse it with the neutral 404. A
    probe therefore still has to reach the route around the host separation
    (its own port); the route itself answers, which is what the second half
    checks - through the endpoint, because no request can reach it."""
    settings = _make_settings(tmp_path, base_url="https://beheer.plak.example")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        async with _client_for(app) as content:
            on_content = await content.get("/healthz")
        async with _client_for(app, base="https://beheer.plak.example") as admin:
            on_admin = await admin.get("/healthz")

    for resp in (on_content, on_admin):
        assert resp.status_code == 404
        assert resp.content == NEUTRAL_404_BODY

    route = next(route for route in app.routes if getattr(route, "path", None) == "/healthz")
    assert await route.endpoint() == {"status": "ok"}


async def test_robots_txt_not_shadowed_by_the_content_catch_all(tmp_path: Path) -> None:
    """The platform routes (pages.router) sit in main.py ahead of the
    serving catch-all; this pins that /robots.txt keeps being served by that
    route and is not read as a groep/site pair."""
    settings = _make_settings(tmp_path)
    app = create_app(settings)

    async with app.router.lifespan_context(app), _client_for(app) as client:
        resp = await client.get("/robots.txt")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    # On the content host: the published sites are exactly what may be
    # indexed. The admin host gets `Disallow: /` (test_host_separation.py).
    assert resp.text == "User-agent: *\nDisallow:\n"


async def test_unknown_site_gives_neutral_404_by_full_stack(
    tmp_path: Path, migrated_dsn: str
) -> None:
    settings = _make_settings(tmp_path, db_url=migrated_dsn)
    app = create_app(settings)

    # create_app() binds its own session factory straight to the shared test
    # container, and the refusal below really does write an audit row
    # (fail-open, its own commit). Without this rollback transaction (same
    # pattern as test_serving.py) that row would stay visible to other test
    # files sharing the same container (READ COMMITTED sees each other's
    # commits).
    async with app.router.lifespan_context(app), app.state.engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        app.state.session_factory = factory
        app.state.audit_log = AuditLog(factory, settings.audit_pepper, settings.audit_ip_key_bytes)
        try:
            async with _client_for(app) as client:
                resp = await client.get("/onbekende-groep/onbekend-site/")
                # A refusal that went through the access gate and the audit
                # log, next to one that never got past the host separation:
                # same body, same headers. That is the whole promise
                # (docs/security.md), and the header set is the half most
                # likely to drift.
                elsewhere = await client.get("/-/onbekend")
        finally:
            await transaction.rollback()

    assert resp.status_code == 404
    assert resp.content == NEUTRAL_404_BODY
    assert resp.headers["cache-control"] == "no-store"
    assert sorted(resp.headers.multi_items()) == sorted(elsewhere.headers.multi_items())


async def test_login_redirect_to_idp(tmp_path: Path) -> None:
    idp = MockIdP()
    settings = _make_settings(tmp_path, oidc_issuer=idp.issuer)
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        # create_app() puts an OidcClient with a real httpx client on
        # app.state itself; here we replace only the underlying transport with
        # an in-process mock IdP, so this test touches no network.
        app.state.oidc_client = make_oidc_client(settings, idp)
        async with _client_for(app) as client:
            resp = await client.get("/-/login")

    assert resp.status_code == 302
    assert resp.headers["location"].startswith(idp.issuer + "/authorize?")


async def test_ratelimit_headers_on_429_on_content_path(tmp_path: Path) -> None:
    settings = _make_settings(
        tmp_path, ratelimit_content_max=2, ratelimit_content_window_s=60
    )
    app = create_app(settings)

    async with app.router.lifespan_context(app), _client_for(app) as client:
        # Purely lexical slash redirect (spec §5, behaviour requirement 2):
        # no DB call, so it is suited to hammering the rate limit layer alone.
        for _ in range(2):
            resp = await client.get("/nldd/website")
            assert resp.status_code == 301

        resp = await client.get("/nldd/website")

    assert resp.status_code == 429
    assert "retry-after" in resp.headers
    assert int(resp.headers["retry-after"]) > 0


async def test_ratelimit_counts_content_session_per_viewer_not_per_ip(tmp_path: Path) -> None:
    settings = _make_settings(
        tmp_path, ratelimit_content_max=2, ratelimit_content_window_s=60
    )
    app = create_app(settings)

    # Both clients share the ASGI transport's IP; only the session tells them
    # apart.
    async with app.router.lifespan_context(app):
        async with _client_for(app) as anonymous:
            for _ in range(2):
                assert (await anonymous.get("/nldd/website")).status_code == 301
            assert (await anonymous.get("/nldd/website")).status_code == 429

        async with _client_for(app) as watcher:
            set_content_session_cookie(watcher, app, sub="kijker-1")
            for _ in range(2):
                assert (await watcher.get("/nldd/website")).status_code == 301
            resp = await watcher.get("/nldd/website")

    assert resp.status_code == 429


async def test_trustedhost_middleware_refused_on_wrong_host(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, base_url="https://beheer.plak.example")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://kwaadaardig.example", follow_redirects=False
        ) as client:
            resp = await client.get("/robots.txt")

    assert resp.status_code == 400


async def test_trustedhost_middleware_allows_both_own_hosts(tmp_path: Path) -> None:
    # Two origins share this one app (spec §4a), so both are on the allowlist.
    settings = _make_settings(tmp_path, base_url="https://beheer.plak.example")
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        async with _client_for(app) as content:
            on_content = await content.get("/robots.txt")
        async with _client_for(app, base="https://beheer.plak.example") as admin:
            on_admin = await admin.get("/robots.txt")

    assert on_content.status_code == 200
    assert on_admin.status_code == 200


async def test_no_trustedhost_middleware_without_base_url(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    app = create_app(settings)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://willekeurige-host.example", follow_redirects=False
        ) as client:
            resp = await client.get("/robots.txt")

    assert resp.status_code == 200
