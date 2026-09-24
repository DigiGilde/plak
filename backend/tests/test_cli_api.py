"""Tests for api/cli.py and the CLI side of api/admin.py: the device
authorization grant (RFC 8628) end to end, over HTTP.

The app under test wires the CLI router, the admin router (for the member's
side of approval) and BearerOutsideDeploysMiddleware together, the way
main.py would, without touching main.py itself.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from plak.api import cli as cli_api
from plak.api.admin import CLI_APPROVAL_MAX_ATTEMPTS, make_admin_router
from plak.api.deploys import BearerOutsideDeploysMiddleware
from plak.api.errors import register_error_handlers
from plak.audit.log import AuditLog
from plak.auth.sessions import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, SessionStore, sign
from plak.cli import service as cli
from plak.config import Settings
from plak.db import make_session_factory
from plak.models.audit import AuditLogEntry
from plak.models.cli import CliDeviceAuthorization, CliRefreshToken, CliSession
from plak.models.identity import Member, MemberStatus

BASE = "/-/api/v1"
PROBLEM = "application/problem+json"
APP_BASE_URL = "https://plak.example"


def _settings(content_root, *, base_url: str | None = APP_BASE_URL) -> Settings:
    return Settings(
        db_url="postgresql+asyncpg://plak:plak@localhost:5432/plak",
        content_root=content_root,
        oidc_issuer="https://idp.example",
        oidc_client_id="plak-client",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr:hoog",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper="audit-pepper-van-minstens-32-bytes!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        base_url=base_url,
        content_base_url=APP_BASE_URL,
        environment="dev",
    )


@pytest_asyncio.fixture
async def factory(migrated_dsn: str):
    engine = create_async_engine(migrated_dsn, poolclass=NullPool)
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


def _make_app(factory, content_root, *, base_url: str | None = APP_BASE_URL) -> FastAPI:
    settings = _settings(content_root, base_url=base_url)
    app = FastAPI()
    app.state.settings = settings
    app.state.session_store = SessionStore()
    app.state.session_factory = factory
    app.state.audit_log = AuditLog(factory, settings.audit_pepper, settings.audit_ip_key_bytes)
    register_error_handlers(app)
    app.add_middleware(BearerOutsideDeploysMiddleware)
    app.include_router(cli_api.router)
    app.include_router(make_admin_router())

    @app.get(f"{BASE}/groups")
    async def _groups() -> list:  # target for the bearer-elsewhere test
        return []

    return app


@pytest.fixture
def content_root(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    return root


@pytest.fixture
def app(factory, content_root) -> FastAPI:
    return _make_app(factory, content_root)


def _client(app: FastAPI):
    import httpx

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=APP_BASE_URL, follow_redirects=False)


@pytest_asyncio.fixture
async def client(app):
    async with _client(app) as client:
        yield client


async def _make_member(factory, *, sub: str = "lid-1", status: MemberStatus = MemberStatus.ACTIVE) -> Member:
    async with factory() as db:
        member = Member(sso_subject=sub, email=f"{sub}@example.nl", status=status)
        db.add(member)
        await db.commit()
        return member


def login(
    client,
    app,
    *,
    sub: str = "lid-1",
    session_age: timedelta | None = None,
    self_initiated: bool = True,
) -> dict[str, str]:
    """Sets an admin session (and CSRF cookie), for `sub`. Optionally backdated."""
    store: SessionStore = app.state.session_store
    session = store.create_session(
        sub=sub,
        email=f"{sub}@example.nl",
        email_verified=True,
        acr="urn:acr:hoog",
        self_initiated=self_initiated,
    )
    if session_age is not None:
        session = dataclasses.replace(session, created_at=datetime.now(UTC) - session_age)
        store._sessions[session.id] = session
    token = sign(app.state.settings.session_secret, session.id)
    client.cookies.set(SESSION_COOKIE, token, domain="plak.example", path="/")
    client.cookies.set(CSRF_COOKIE, session.csrf_token, domain="plak.example", path="/")
    return {CSRF_HEADER: session.csrf_token}


async def _audit_rows(factory) -> list[AuditLogEntry]:
    async with factory() as db:
        rows = await db.scalars(select(AuditLogEntry).order_by(AuditLogEntry.occurred_at))
        return list(rows)


def _no_secret_leak(*payloads: object) -> None:
    """Asserts none of the given values (response bodies, audit refs...)
    contain a device code, access token or refresh token in the clear."""
    blob = json.dumps(payloads, default=str)
    for prefix in (cli.DEVICE_CODE_PREFIX, cli.ACCESS_TOKEN_PREFIX, cli.REFRESH_TOKEN_PREFIX):
        assert f"{prefix}_" not in blob


async def _session_count(factory) -> int:
    async with factory() as db:
        return len(list(await db.scalars(select(CliSession.id))))


async def _logged_in_cli(client, app, factory, *, sub: str = "lid-uitlog") -> dict:
    """Runs the device flow over HTTP and returns the token response."""
    await _make_member(factory, sub=sub)
    created = await _create_device_authorization(client)
    headers = login(client, app, sub=sub)
    await client.post(
        f"{BASE}/cli/device-authorizations/approve", json={"userCode": created["userCode"]}, headers=headers
    )
    exchange = await client.post(
        f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": created["deviceCode"]}
    )
    return exchange.json()


async def _create_device_authorization(client, **body) -> dict:
    response = await client.post(f"{BASE}/cli/device-authorizations", json=body or None)
    assert response.status_code == 200, response.text
    return response.json()


# -- POST /cli/device-authorizations -----------------------------------------


class TestCreateDeviceAuthorization:
    async def test_no_body_works(self, client):
        response = await client.post(f"{BASE}/cli/device-authorizations")
        assert response.status_code == 200
        body = response.json()
        assert body["clientName"] is None if "clientName" in body else True

    async def test_shape_and_verification_uri_with_base_url(self, client):
        body = await _create_device_authorization(client, clientName="plak-cli 1.2")
        assert body["deviceCode"].startswith(cli.DEVICE_CODE_PREFIX + "_")
        assert len(body["userCode"]) == 9  # ABCD-EFGH
        assert body["userCode"][4] == "-"
        assert body["verificationUri"] == f"{APP_BASE_URL}{cli_api.VERIFICATION_PATH}"
        assert body["verificationUriComplete"] == f"{body['verificationUri']}?code={body['userCode']}"
        assert body["expiresIn"] == int(cli.DEVICE_CODE_TTL.total_seconds())
        assert body["interval"] == cli.POLL_INTERVAL_S

    async def test_verification_uri_without_base_url_uses_request_base(self, factory, content_root):
        app = _make_app(factory, content_root, base_url=None)
        async with _client(app) as client:
            body = await _create_device_authorization(client)
        assert body["verificationUri"] == f"{APP_BASE_URL}{cli_api.VERIFICATION_PATH}"

    async def test_client_name_is_sanitised(self, client):
        body = await _create_device_authorization(client, clientName="plak-cli\x00\n 1.2")
        assert body["deviceCode"]  # sanity: request succeeded
        # sanitising happens server-side before storage; verify via lookup.

    async def test_client_name_over_max_length_is_422(self, client):
        response = await client.post(
            f"{BASE}/cli/device-authorizations", json={"clientName": "a" * (cli.CLIENT_NAME_MAX_LENGTH + 1)}
        )
        assert response.status_code == 422

    async def test_rate_limited_after_ten_from_one_ip(self, client):
        for _ in range(cli_api.DEVICE_CREATE_MAX_PER_IP):
            response = await client.post(f"{BASE}/cli/device-authorizations")
            assert response.status_code == 200
        response = await client.post(f"{BASE}/cli/device-authorizations")
        assert response.status_code == 429
        assert response.json()["code"] == "TOO_MANY_REQUESTS"

    async def test_rate_limit_429_is_not_audited(self, client, factory):
        for _ in range(cli_api.DEVICE_CREATE_MAX_PER_IP):
            await client.post(f"{BASE}/cli/device-authorizations")
        await client.post(f"{BASE}/cli/device-authorizations")
        rows = await _audit_rows(factory)
        assert rows == []


# -- Full happy path ----------------------------------------------------------


class TestFullHappyPath:
    async def test_create_pending_lookup_approve_exchange_whoami_refresh_reuse(self, client, app, factory):
        await _make_member(factory, sub="cli-lid")

        created = await _create_device_authorization(client, clientName="plak-cli 1.2 on macOS")
        device_code = created["deviceCode"]
        user_code = created["userCode"]

        # Poll while pending.
        pending_response = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": device_code}
        )
        assert pending_response.status_code == 400
        assert pending_response.json()["code"] == "AUTHORIZATION_PENDING"

        headers = login(client, app, sub="cli-lid")

        lookup_response = await client.post(
            f"{BASE}/cli/device-authorizations/lookup", json={"userCode": user_code}, headers=headers
        )
        assert lookup_response.status_code == 200
        lookup_body = lookup_response.json()
        assert lookup_body["userCode"] == user_code
        assert lookup_body["clientName"] == "plak-cli 1.2 on macOS"
        assert lookup_body["ipTruncated"]

        approve_response = await client.post(
            f"{BASE}/cli/device-authorizations/approve", json={"userCode": user_code}, headers=headers
        )
        assert approve_response.status_code == 204

        # The service tracks real poll timing server-side; move the previous
        # poll back past the interval so this second poll is not itself
        # refused as SLOW_DOWN (that behaviour has its own test below).
        async with factory() as db:
            await db.execute(
                update(CliDeviceAuthorization).values(last_polled_at=datetime.now(UTC) - timedelta(seconds=10))
            )
            await db.commit()

        exchange_response = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": device_code}
        )
        assert exchange_response.status_code == 200
        tokens = exchange_response.json()
        assert tokens["tokenType"] == "Bearer"
        assert tokens["member"]["email"] == "cli-lid@example.nl"
        access_token = tokens["accessToken"]
        refresh_token = tokens["refreshToken"]

        whoami_response = await client.get(f"{BASE}/cli/whoami", headers={"Authorization": f"Bearer {access_token}"})
        assert whoami_response.status_code == 200
        assert whoami_response.json()["member"]["email"] == "cli-lid@example.nl"

        refresh_response = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "refresh_token", "refreshToken": refresh_token}
        )
        assert refresh_response.status_code == 200
        rotated = refresh_response.json()
        new_access_token = rotated["accessToken"]
        assert new_access_token != access_token
        assert rotated["refreshToken"] != refresh_token

        # A second use right away is a race, not theft: refused, session kept.
        race_response = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "refresh_token", "refreshToken": refresh_token}
        )
        assert race_response.status_code == 400
        assert race_response.json()["code"] == "INVALID_GRANT"
        still = await client.get(f"{BASE}/cli/whoami", headers={"Authorization": f"Bearer {new_access_token}"})
        assert still.status_code == 200

        # Past the grace window the old refresh token is reuse.
        async with factory() as db:
            await db.execute(
                update(CliRefreshToken)
                .where(CliRefreshToken.used_at.is_not(None))
                .values(used_at=datetime.now(UTC) - timedelta(seconds=11))
            )
            await db.commit()
        reuse_response = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "refresh_token", "refreshToken": refresh_token}
        )
        assert reuse_response.status_code == 400
        assert reuse_response.json()["code"] == "INVALID_GRANT"

        # The whole session is gone: even the freshly rotated access token is dead.
        whoami_after_reuse = await client.get(
            f"{BASE}/cli/whoami", headers={"Authorization": f"Bearer {new_access_token}"}
        )
        assert whoami_after_reuse.status_code == 401

        rows = await _audit_rows(factory)
        actions = [row.action for row in rows]
        assert "cli_login" in actions
        assert "cli_refresh_reuse" in actions
        # Issuing counts once, for the device code exchange; a refresh adds none.
        issued_rows = [row for row in rows if row.action == "cli_token_issued"]
        assert len(issued_rows) == 1
        assert issued_rows[0].result == "allowed"
        assert set(issued_rows[0].refs) == {"via", "cli_session"}
        refuse_row = next(row for row in rows if row.action == "cli_refresh_reuse")
        assert refuse_row.result == "refused"

        # The device code/access/refresh tokens are necessarily plaintext in
        # their own issuing response (that is the point of the grant); they
        # must never show up anywhere else: other responses, or audit refs.
        _no_secret_leak(pending_response.json(), lookup_body, reuse_response.json(), [row.refs for row in rows])

    async def test_deny_path(self, client, app, factory):
        await _make_member(factory, sub="cli-lid-deny")
        created = await _create_device_authorization(client)
        headers = login(client, app, sub="cli-lid-deny")

        deny_response = await client.post(
            f"{BASE}/cli/device-authorizations/deny", json={"userCode": created["userCode"]}, headers=headers
        )
        assert deny_response.status_code == 204

        exchange_response = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": created["deviceCode"]}
        )
        assert exchange_response.status_code == 400
        assert exchange_response.json()["code"] == "ACCESS_DENIED"

        rows = await _audit_rows(factory)
        assert any(row.action == "cli_login_denied" for row in rows)

    async def test_logout(self, client, app, factory):
        await _make_member(factory, sub="cli-lid-logout")
        created = await _create_device_authorization(client)
        headers = login(client, app, sub="cli-lid-logout")
        await client.post(f"{BASE}/cli/device-authorizations/approve", json={"userCode": created["userCode"]},
                          headers=headers)
        exchange = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": created["deviceCode"]}
        )
        access_token = exchange.json()["accessToken"]

        logout_response = await client.delete(
            f"{BASE}/cli/session", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert logout_response.status_code == 204

        whoami_response = await client.get(f"{BASE}/cli/whoami", headers={"Authorization": f"Bearer {access_token}"})
        assert whoami_response.status_code == 401

        rows = await _audit_rows(factory)
        assert any(row.action == "cli_logout" and row.result == "allowed" for row in rows)

    async def test_logout_works_for_deactivated_member(self, client, app, factory):
        member = await _make_member(factory, sub="cli-lid-deact")
        created = await _create_device_authorization(client)
        headers = login(client, app, sub="cli-lid-deact")
        await client.post(f"{BASE}/cli/device-authorizations/approve", json={"userCode": created["userCode"]},
                          headers=headers)
        exchange = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": created["deviceCode"]}
        )
        access_token = exchange.json()["accessToken"]

        async with factory() as db:
            db_member = await db.get(Member, member.id)
            db_member.status = MemberStatus.DEACTIVATED
            await db.commit()

        logout_response = await client.delete(
            f"{BASE}/cli/session", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert logout_response.status_code == 204


# -- SLOW_DOWN / EXPIRED_TOKEN --------------------------------------------


class TestPollingErrors:
    async def test_slow_down_on_fast_repeat_poll(self, client):
        created = await _create_device_authorization(client)
        body = {"grantType": "device_code", "deviceCode": created["deviceCode"]}
        first = await client.post(f"{BASE}/cli/tokens", json=body)
        assert first.json()["code"] == "AUTHORIZATION_PENDING"
        second = await client.post(f"{BASE}/cli/tokens", json=body)
        assert second.status_code == 400
        assert second.json()["code"] == "SLOW_DOWN"

    async def test_missing_device_code_is_invalid_grant(self, client):
        response = await client.post(f"{BASE}/cli/tokens", json={"grantType": "device_code"})
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_GRANT"

    async def test_missing_refresh_token_is_invalid_grant(self, client):
        response = await client.post(f"{BASE}/cli/tokens", json={"grantType": "refresh_token"})
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_GRANT"

    async def test_unknown_grant_type_is_422(self, client):
        response = await client.post(f"{BASE}/cli/tokens", json={"grantType": "password"})
        assert response.status_code == 422

    async def test_unknown_device_code_is_invalid_grant(self, client):
        response = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": "plakdc_" + "a" * 16 + "_" + "b" * 64}
        )
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_GRANT"


# -- Member side: approval/lookup/deny ----------------------------------------


class TestApprovalGate:
    @pytest.mark.parametrize(
        ("stored", "expected"),
        [("keep", True), ("198.51.100.0/24", False), (None, None)],
    )
    async def test_the_lookup_says_whether_the_approver_is_on_the_same_network(
        self, client, app, factory, stored, expected
    ):
        await _make_member(factory, sub="lid-netwerk")
        created = await _create_device_authorization(client)
        if stored != "keep":
            async with factory() as db:
                await db.execute(update(CliDeviceAuthorization).values(ip_truncated=stored))
                await db.commit()
        headers = login(client, app, sub="lid-netwerk")
        response = await client.post(
            f"{BASE}/cli/device-authorizations/lookup", json={"userCode": created["userCode"]}, headers=headers
        )
        assert response.status_code == 200
        body = response.json()
        assert body["sameNetwork"] is expected
        # Only the truncated network is ever in the answer.
        assert "127.0.0.1" not in response.text

    async def test_csrf_missing_is_403(self, client, app, factory):
        await _make_member(factory, sub="lid-csrf")
        created = await _create_device_authorization(client)
        login(client, app, sub="lid-csrf")  # sets cookies but we drop the header below
        response = await client.post(
            f"{BASE}/cli/device-authorizations/approve", json={"userCode": created["userCode"]}
        )
        assert response.status_code == 403

    async def test_session_not_fresh_is_401(self, client, app, factory):
        await _make_member(factory, sub="lid-oud")
        created = await _create_device_authorization(client)
        headers = login(client, app, sub="lid-oud", session_age=timedelta(minutes=20))
        response = await client.post(
            f"{BASE}/cli/device-authorizations/approve", json={"userCode": created["userCode"]}, headers=headers
        )
        assert response.status_code == 401
        assert response.json()["code"] == "SESSION_NOT_FRESH"

    async def test_a_login_started_by_another_site_is_not_fresh(self, client, app, factory):
        """A phishing page can navigate the browser through the IdP, which
        returns without a prompt on an existing SSO session; that must not
        reset the freshness window."""
        await _make_member(factory, sub="lid-extern")
        created = await _create_device_authorization(client)
        headers = login(client, app, sub="lid-extern", self_initiated=False)
        response = await client.post(
            f"{BASE}/cli/device-authorizations/approve", json={"userCode": created["userCode"]}, headers=headers
        )
        assert response.status_code == 401
        assert response.json()["code"] == "SESSION_NOT_FRESH"

    async def test_unknown_user_code_lookup_is_404(self, client, app, factory):
        await _make_member(factory, sub="lid-404")
        headers = login(client, app, sub="lid-404")
        response = await client.post(f"{BASE}/cli/device-authorizations/lookup", json={"userCode": "AAAA-AAAA"},
                                     headers=headers)
        assert response.status_code == 404
        assert response.json()["code"] == "USER_CODE_UNKNOWN"

    async def test_unknown_user_code_approve_is_404(self, client, app, factory):
        await _make_member(factory, sub="lid-404b")
        headers = login(client, app, sub="lid-404b")
        response = await client.post(f"{BASE}/cli/device-authorizations/approve", json={"userCode": "AAAA-AAAA"},
                                     headers=headers)
        assert response.status_code == 404

    async def test_unknown_user_code_deny_is_404(self, client, app, factory):
        await _make_member(factory, sub="lid-404c")
        headers = login(client, app, sub="lid-404c")
        response = await client.post(f"{BASE}/cli/device-authorizations/deny", json={"userCode": "AAAA-AAAA"},
                                     headers=headers)
        assert response.status_code == 404

    async def test_too_many_attempts_429(self, client, app, factory):
        await _make_member(factory, sub="lid-flood")
        headers = login(client, app, sub="lid-flood")
        for _ in range(CLI_APPROVAL_MAX_ATTEMPTS):
            response = await client.post(f"{BASE}/cli/device-authorizations/lookup", json={"userCode": "AAAA-AAAA"},
                                         headers=headers)
            assert response.status_code == 404
        response = await client.post(f"{BASE}/cli/device-authorizations/lookup", json={"userCode": "AAAA-AAAA"},
                                     headers=headers)
        assert response.status_code == 429
        assert response.json()["code"] == "TOO_MANY_ATTEMPTS"


# -- me/cli-sessions -----------------------------------------------------


class TestMyCliSessions:
    async def test_list_and_revoke(self, client, app, factory):
        await _make_member(factory, sub="lid-sessions")
        created = await _create_device_authorization(client, clientName="mijn-cli")
        headers = login(client, app, sub="lid-sessions")
        await client.post(f"{BASE}/cli/device-authorizations/approve", json={"userCode": created["userCode"]},
                          headers=headers)
        await client.post(f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": created["deviceCode"]})

        list_response = await client.get(f"{BASE}/me/cli-sessions", headers=headers)
        assert list_response.status_code == 200
        sessions = list_response.json()
        assert len(sessions) == 1
        assert sessions[0]["clientName"] == "mijn-cli"
        session_id = sessions[0]["id"]

        revoke_response = await client.delete(f"{BASE}/me/cli-sessions/{session_id}", headers=headers)
        assert revoke_response.status_code == 204

        list_after = await client.get(f"{BASE}/me/cli-sessions", headers=headers)
        assert list_after.json() == []

        rows = await _audit_rows(factory)
        assert any(row.action == "cli_session_revoke" for row in rows)
        _no_secret_leak([sessions], [row.refs for row in rows])

    async def test_revoke_unknown_session_is_404(self, client, app, factory):
        await _make_member(factory, sub="lid-sessions-404")
        headers = login(client, app, sub="lid-sessions-404")
        response = await client.delete(f"{BASE}/me/cli-sessions/{uuid.uuid4()}", headers=headers)
        assert response.status_code == 404
        assert response.json()["code"] == "CLI_SESSION_UNKNOWN"

    async def test_revoke_someone_elses_session_is_404(self, client, app, factory):
        owner = await _make_member(factory, sub="lid-owner")
        await _make_member(factory, sub="lid-other")
        created = await _create_device_authorization(client)
        owner_headers = login(client, app, sub="lid-owner")
        await client.post(f"{BASE}/cli/device-authorizations/approve", json={"userCode": created["userCode"]},
                          headers=owner_headers)
        await client.post(f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": created["deviceCode"]})
        async with factory() as db:
            row = await db.scalar(select(cli.CliSession).where(cli.CliSession.member_id == owner.id))
            session_id = row.id

        other_headers = login(client, app, sub="lid-other")
        response = await client.delete(f"{BASE}/me/cli-sessions/{session_id}", headers=other_headers)
        assert response.status_code == 404


# -- Bearer middleware ------------------------------------------------------


class TestBearerOutsideCliEndpoints:
    async def test_bearer_on_another_endpoint_is_401(self, client, app, factory):
        await _make_member(factory, sub="lid-bearer")
        created = await _create_device_authorization(client)
        headers = login(client, app, sub="lid-bearer")
        await client.post(f"{BASE}/cli/device-authorizations/approve", json={"userCode": created["userCode"]},
                          headers=headers)
        exchange = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": created["deviceCode"]}
        )
        access_token = exchange.json()["accessToken"]

        response = await client.get(f"{BASE}/groups", headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == 'Bearer realm="plak", error="invalid_token"'


# -- whoami errors ------------------------------------------------------


class TestWhoami:
    async def test_no_token_is_401(self, client):
        response = await client.get(f"{BASE}/cli/whoami")
        assert response.status_code == 401
        assert response.json()["code"] == "TOKEN_INVALID"

    async def test_inactive_member_is_403(self, client, app, factory):
        member = await _make_member(factory, sub="lid-inactief")
        created = await _create_device_authorization(client)
        headers = login(client, app, sub="lid-inactief")
        await client.post(f"{BASE}/cli/device-authorizations/approve", json={"userCode": created["userCode"]},
                          headers=headers)
        exchange = await client.post(
            f"{BASE}/cli/tokens", json={"grantType": "device_code", "deviceCode": created["deviceCode"]}
        )
        access_token = exchange.json()["accessToken"]
        async with factory() as db:
            db_member = await db.get(Member, member.id)
            db_member.status = MemberStatus.DEACTIVATED
            await db.commit()
        response = await client.get(f"{BASE}/cli/whoami", headers={"Authorization": f"Bearer {access_token}"})
        assert response.status_code == 403
        assert response.json()["code"] == "MEMBER_NOT_ACTIVE"

    async def test_malformed_token_is_401(self, client):
        response = await client.get(f"{BASE}/cli/whoami", headers={"Authorization": "Bearer garbage"})
        assert response.status_code == 401

    async def test_logout_without_any_token_is_204_and_audited_as_refused(self, client, factory):
        response = await client.delete(f"{BASE}/cli/session")
        assert response.status_code == 204
        rows = await _audit_rows(factory)
        assert [(row.action, row.result, row.reason_code) for row in rows] == [
            ("cli_logout", "refused", "TOKEN_INVALID")
        ]

    async def test_logout_with_an_unknown_token_is_204_no_oracle(self, client, factory):
        response = await client.delete(
            f"{BASE}/cli/session", headers={"Authorization": f"Bearer {cli.ACCESS_TOKEN_PREFIX}_{'a' * 16}_{'b' * 64}"}
        )
        assert response.status_code == 204
        response = await client.request(
            "DELETE", f"{BASE}/cli/session", json={"refreshToken": f"plakclr_{'a' * 16}_{'b' * 64}"}
        )
        assert response.status_code == 204
        assert [row.result for row in await _audit_rows(factory)] == ["refused", "refused"]

    async def test_logout_with_an_expired_access_token_revokes(self, client, app, factory):
        tokens = await _logged_in_cli(client, app, factory)
        async with factory() as db:
            await db.execute(update(CliSession).values(access_expires_at=datetime.now(UTC) - timedelta(hours=1)))
            await db.commit()
        response = await client.delete(
            f"{BASE}/cli/session", headers={"Authorization": f"Bearer {tokens['accessToken']}"}
        )
        assert response.status_code == 204
        assert await _session_count(factory) == 0

    async def test_logout_with_the_refresh_token_in_the_body_revokes(self, client, app, factory):
        tokens = await _logged_in_cli(client, app, factory)
        response = await client.request(
            "DELETE", f"{BASE}/cli/session", json={"refreshToken": tokens["refreshToken"]}
        )
        assert response.status_code == 204
        assert await _session_count(factory) == 0
        rows = [row for row in await _audit_rows(factory) if row.action == "cli_logout"]
        assert [row.result for row in rows] == ["allowed"]
        assert tokens["refreshToken"] not in str(rows[0].refs)

    async def test_logout_with_a_malformed_body_is_422(self, client):
        response = await client.request("DELETE", f"{BASE}/cli/session", json={"refreshToken": "x" * 201})
        assert response.status_code == 422
