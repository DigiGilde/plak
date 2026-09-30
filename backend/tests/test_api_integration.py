"""Integration tests for the Phase 3 wiring in create_app() (main.py): the
deploy API (bearer), the session API with origin guarding, the problem+json
handlers, the API docs and the cleanup background task, all through the full
app stack (rate limit, TrustedHost, middleware, routers in registration order).
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import APP_BASE_URL, CONTENT_BASE_URL, make_client_jwk

from plak.api.origin_guard import REASON_OTHER_ORIGIN
from plak.cli import service as cli
from plak.config import Settings
from plak.constants import AccessBase, Role
from plak.main import create_app
from plak.models.identity import Group, GroupMember, Member, MemberStatus
from plak.models.publication import Site
from plak.serving.response import NEUTRAL_404_BODY

BASE = "/-/api/v1"
PROBLEM = "application/problem+json"
OTHER_ORIGIN = "https://content.example"


def _settings(tmp_path: Path, dsn: str) -> Settings:
    return Settings(
        db_url=dsn,
        content_root=tmp_path / "content",
        oidc_issuer="https://idp.example",
        oidc_client_id="plak-client",
        oidc_client_private_jwk=make_client_jwk(),
        oidc_required_acr="urn:acr:hoog",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper="audit-pepper-van-minstens-32-bytes!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        base_url=APP_BASE_URL,
        content_base_url=CONTENT_BASE_URL,
        environment="dev",
    )


@pytest_asyncio.fixture
async def app(tmp_path: Path, migrated_dsn: str) -> AsyncIterator[FastAPI]:
    app = create_app(_settings(tmp_path, migrated_dsn))
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=APP_BASE_URL, follow_redirects=False
    ) as client:
        yield client


async def _seed_site_with_token(app: FastAPI) -> str:
    """Active lid, groep team-aurora, site website; returns a CLI access token of that lid."""
    factory = app.state.session_factory
    async with factory() as db:
        member = Member(sso_subject="sub-ci", email="ci@example.org", status=MemberStatus.ACTIVE)
        group = Group(slug="team-aurora", name="Team Aurora", default_access_base=AccessBase.PUBLIC)
        db.add_all([member, group])
        await db.flush()
        db.add(GroupMember(group_id=group.id, member_id=member.id, role=Role.ADMIN))
        site = Site(
            group_id=group.id, slug="website", title="Website", access_base=AccessBase.PUBLIC
        )
        db.add(site)
        await db.commit()
    async with factory() as db:
        created = await cli.create_device_authorization(db, client_name="plak-cli", ip_truncated=None)
    async with factory() as db:
        await cli.decide(db, created.user_code, await db.get(Member, member.id), approve=True)
    async with factory() as db:
        return (await cli.exchange_device_code(db, created.device_code)).access_token


def _zip_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("index.html", b"<h1>hoi</h1>")
    return buffer.getvalue()


async def test_session_mutation_with_other_origin_gives_403(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        f"{BASE}/groups",
        json={"name": "Team", "slug": "team"},
        headers={"Origin": OTHER_ORIGIN},
    )

    assert resp.status_code == 403
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert resp.json()["code"] == REASON_OTHER_ORIGIN


async def test_session_mutation_without_origin_but_cross_site_gives_403(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        f"{BASE}/groups",
        json={"name": "Team", "slug": "team"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == REASON_OTHER_ORIGIN


async def test_session_api_with_admin_origin_passes_the_origin_port(
    client: httpx.AsyncClient,
) -> None:
    """Check: with the right Origin the request fails not at the origin gate
    but on the missing session (401), so the 403 above demonstrably comes from
    the origin guard."""
    resp = await client.post(
        f"{BASE}/groups",
        json={"name": "Team", "slug": "team"},
        headers={"Origin": APP_BASE_URL},
    )

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_bearer_deploy_without_origin_header_works(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    token = await _seed_site_with_token(app)

    resp = await client.post(
        f"{BASE}/sites/team-aurora/website/deploys",
        files={"file": ("site.zip", _zip_bytes(), "application/zip")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 201, resp.text
    assert "versionId" in resp.json()


async def test_bearer_preview_teardown_without_origin_is_idempotent_204(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    token = await _seed_site_with_token(app)

    resp = await client.delete(
        f"{BASE}/sites/team-aurora/website/previews/pr-1",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 204


async def test_deploy_with_content_origin_refused(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """The deploy endpoints sit behind the origin guard too: a request with a
    deviating (content) Origin is refused before auth gets a turn, so same-site
    content JS cannot abuse deploy/teardown."""
    token = await _seed_site_with_token(app)

    resp = await client.post(
        f"{BASE}/sites/team-aurora/website/deploys",
        files={"file": ("site.zip", _zip_bytes(), "application/zip")},
        headers={"Authorization": f"Bearer {token}", "Origin": OTHER_ORIGIN},
    )

    assert resp.status_code == 403, resp.text
    assert resp.headers["content-type"].startswith(PROBLEM)


async def test_bearer_outside_deploy_endpoints_gives_401(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    token = await _seed_site_with_token(app)

    resp = await client.get(
        f"{BASE}/overview", headers={"Authorization": f"Bearer {token}"}
    )

    assert resp.status_code == 401
    assert resp.headers["content-type"].startswith(PROBLEM)
    assert "WWW-Authenticate" in resp.headers


async def test_bearer_creates_a_group_and_a_site_through_the_full_stack(
    app: FastAPI, client: httpx.AsyncClient
) -> None:
    """The CLI sends no Origin and no CSRF header: the origin guard lets it
    through, as on the deploy router, and the token stands in for the session."""
    token = await _seed_site_with_token(app)

    group = await client.post(
        f"{BASE}/groups",
        json={"name": "Team", "slug": "team", "defaultAccess": {"base": "nobody"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    site = await client.post(
        f"{BASE}/groups/team/sites",
        json={"title": "Docs", "slug": "docs", "access": {"keys": True}},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert group.status_code == 201, group.text
    assert site.status_code == 201, site.text
    assert site.json()["access"] == {"base": "nobody", "keys": True, "invitees": False}


async def test_bearer_creation_with_a_content_origin_is_refused(app: FastAPI, client: httpx.AsyncClient) -> None:
    """Content JS on the sibling host that got hold of a token still meets the
    origin guard."""
    token = await _seed_site_with_token(app)

    resp = await client.post(
        f"{BASE}/groups",
        json={"name": "Team", "slug": "team"},
        headers={"Authorization": f"Bearer {token}", "Origin": OTHER_ORIGIN},
    )

    assert resp.status_code == 403
    assert resp.json()["code"] == REASON_OTHER_ORIGIN


async def test_bearer_on_deleting_a_group_is_still_401(app: FastAPI, client: httpx.AsyncClient) -> None:
    token = await _seed_site_with_token(app)

    resp = await client.delete(f"{BASE}/groups/team-aurora", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert resp.json()["code"] == "BEARER_NOT_ACCEPTED"


async def test_docs_gives_200_without_external_origins(client: httpx.AsyncClient) -> None:
    resp = await client.get("/-/api/docs")

    assert resp.status_code == 200
    html = resp.text
    assert "http://" not in html
    assert "https://" not in html
    assert "/-/api/docs/assets/" in html


async def test_docs_assets_are_served_with_their_own_media_type(client: httpx.AsyncClient) -> None:
    for name, mediatype in (
        ("swagger-ui-bundle.js", "text/javascript"),
        ("swagger-ui.css", "text/css"),
        ("docs-init.js", "text/javascript"),
        ("docs.css", "text/css"),
    ):
        resp = await client.get(f"/-/api/docs/assets/{name}")

        assert resp.status_code == 200, name
        assert resp.headers["content-type"].startswith(mediatype), name
        assert resp.headers["cache-control"] == "no-cache", name
        assert resp.content, name


async def test_asset_outside_the_allowlist_does_not_exist(client: httpx.AsyncClient) -> None:
    # The allowlist is the path validation: there is no StaticFiles mount that
    # could follow a ../ path. docs.js was the old home-grown renderer.
    for name in ("onbekend.js", "docs.js"):
        resp = await client.get(f"/-/api/docs/assets/{name}")

        assert resp.status_code == 404, name
        assert resp.json()["code"] == "UNKNOWN_ASSET", name


async def test_path_traversal_does_not_reach_the_asset_route(client: httpx.AsyncClient) -> None:
    resp = await client.get("/-/api/docs/assets/..%2F..%2Fhoofd.py")

    # The path is normalised before routing, so this never even reaches the
    # handler; what counts is that no source code comes out of it.
    assert resp.status_code == 404
    assert "create_app" not in resp.text


async def test_openapi_schema_reachable(client: httpx.AsyncClient) -> None:
    resp = await client.get("/-/api/openapi.json")

    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert f"{BASE}/sites/{{group_slug}}/{{site_slug}}/deploys" in paths
    assert f"{BASE}/overview" in paths


async def test_fastapi_default_docs_disabled(client: httpx.AsyncClient) -> None:
    # A 404 does not prove this: the SPA sits on the root of the admin host,
    # so every unknown path there falls through to index.html with a 200, and
    # "not found" and "handed to the SPA" look the same from outside. What the
    # test can still prove is that FastAPI's own pages are not served: neither
    # the Swagger shell nor a schema comes back. Our own docs live at
    # /-/api/docs and are covered separately.
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = await client.get(path)
        body = response.text.lower()
        assert "swagger-ui" not in body, path
        assert "redoc" not in body, path
        assert '"openapi"' not in body, path


async def test_api_version_header_on_every_response(client: httpx.AsyncClient) -> None:
    for path in ("/healthz", f"{BASE}/overview", "/onbekend/site/"):
        resp = await client.get(path)
        assert resp.headers.get("api-version") == "1.0.0", path


async def test_serving_catch_all_stays_last(client: httpx.AsyncClient) -> None:
    """An unknown site falls through every router onto the serving router's
    neutral 404; the API routers do not intercept the path.

    Measured on the content host, because on the admin host the SPA now catches
    every unknown path and would answer with index.html instead."""
    host = CONTENT_BASE_URL.split("//", 1)[1]
    resp = await client.get("/onbekend/site/", headers={"host": host})

    assert resp.status_code == 404
    assert resp.content == NEUTRAL_404_BODY


async def test_cleanup_job_runs_inside_the_lifespan(app: FastAPI) -> None:
    task = app.state.cleanup_task
    assert not task.done()


async def test_content_host_allowed_alongside_admin_host(
    tmp_path: Path, migrated_dsn: str
) -> None:
    """Two origins share the app (spec §4a/§11): with PLAK_CONTENT_BASE_URL
    configured TrustedHostMiddleware lets the content host through as well;
    without it the app rejects all content traffic that nginx forwards over
    that second origin."""
    settings = _settings(tmp_path, migrated_dsn).model_copy(
        update={"content_base_url": "https://content.plak.example"}
    )
    content_app = create_app(settings)
    async with content_app.router.lifespan_context(content_app):
        transport = httpx.ASGITransport(app=content_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://content.plak.example"
        ) as content_client:
            resp = await content_client.get("/onbekend/site/")

    assert resp.status_code == 404
    assert resp.content == NEUTRAL_404_BODY


async def test_unknown_host_refused_by_trustedhost(
    tmp_path: Path, migrated_dsn: str
) -> None:
    settings = _settings(tmp_path, migrated_dsn)
    unknown_app = create_app(settings)
    async with unknown_app.router.lifespan_context(unknown_app):
        transport = httpx.ASGITransport(app=unknown_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="https://onbekende-host.example"
        ) as unknown_client:
            resp = await unknown_client.get("/healthz")

    assert resp.status_code == 400


async def test_the_cli_login_runs_through_the_full_stack(app: FastAPI, client: httpx.AsyncClient) -> None:
    token = await _seed_site_with_token(app)

    whoami = await client.get(f"{BASE}/cli/whoami", headers={"Authorization": f"Bearer {token}"})
    assert whoami.status_code == 200
    assert whoami.json()["member"]["email"] == "ci@example.org"

    started = await client.post(f"{BASE}/cli/device-authorizations", json={"clientName": "plak-cli"})
    assert started.status_code == 200
    assert started.json()["verificationUri"] == f"{APP_BASE_URL}/cli-link"

    logout = await client.delete(f"{BASE}/cli/session", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 204
    again = await client.get(f"{BASE}/cli/whoami", headers={"Authorization": f"Bearer {token}"})
    assert again.status_code == 401


async def test_the_cli_login_start_refuses_a_content_origin(client: httpx.AsyncClient) -> None:
    resp = await client.post(f"{BASE}/cli/device-authorizations", json={}, headers={"Origin": OTHER_ORIGIN})
    assert resp.status_code == 403
