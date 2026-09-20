"""Tests for auth/members.py: lid upsert only on a /admin visit, active from
the first login, deactivation, reactivation and bootstrap promotion. Runs
against the testcontainers PostgreSQL from conftest.py; the mock IdP comes
from helpers_oidc."""

from __future__ import annotations

from typing import Annotated

import pytest
import pytest_asyncio
from fastapi import Depends
from helpers_oidc import (
    MockIdP,
    complete_login,
    make_app,
    make_settings,
    make_test_client,
    set_session_cookie,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from plak.api.errors import register_error_handlers
from plak.auth.members import REASON_DEACTIVATED, require_active_member
from plak.db import make_session_factory
from plak.models.identity import Member

BOOTSTRAP_SUB = "bootstrap-beheerder-sub"


@pytest.fixture
def idp() -> MockIdP:
    return MockIdP()


@pytest_asyncio.fixture
async def db_environment(migrated_dsn: str):
    engine = create_async_engine(migrated_dsn, poolclass=NullPool)
    factory = make_session_factory(engine)
    try:
        yield engine, factory
    finally:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM group_members"))
            await connection.execute(text("DELETE FROM members"))
        await engine.dispose()


def _make_admin_app(idp: MockIdP, factory, **settings_overrides):
    app = make_app(make_settings(idp, **settings_overrides), idp)
    app.state.session_factory = factory
    # require_active_member refuses with an ApiError, which only becomes a 403
    # once the handlers are registered; main.py does that for the real app.
    register_error_handlers(app)

    @app.get("/admin")
    async def admin_start(member: Annotated[Member, Depends(require_active_member)]):
        return {"member_id": str(member.id), "status": member.status.value, "role": member.platform_role.value}

    return app


async def _members(factory) -> list[Member]:
    async with factory() as db:
        return list((await db.execute(select(Member))).scalars())


class TestLoginCreatesNoMember:
    async def test_full_login_flow_does_not_touch_the_members_table(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            response = await complete_login(client, idp)
            assert response.status_code == 303
        assert await _members(factory) == []

    async def test_first_admin_visit_creates_an_active_member(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            await complete_login(client, idp)
            assert await _members(factory) == []

            response = await client.get("/admin")
            assert response.status_code == 200

        members = await _members(factory)
        assert len(members) == 1
        assert members[0].sso_subject == "gebruiker-1"
        assert members[0].status.value == "active"
        assert members[0].platform_role.value == "member"
        assert members[0].email == "gebruiker@example.nl"


class TestRequireActiveMember:
    async def test_without_session_401_and_no_member(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            response = await client.get("/admin")
            assert response.status_code == 401
        assert await _members(factory) == []

    async def test_repeated_visit_creates_no_duplicate(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            set_session_cookie(client, app)
            assert (await client.get("/admin")).status_code == 200
            assert (await client.get("/admin")).status_code == 200
        assert len(await _members(factory)) == 1

    async def test_reactivated_member_gets_access_back(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            set_session_cookie(client, app)
            assert (await client.get("/admin")).status_code == 200

            async with factory() as db:
                await db.execute(
                    text("UPDATE members SET status = 'deactivated' WHERE sso_subject = 'gebruiker-1'")
                )
                await db.commit()
            assert (await client.get("/admin")).status_code == 403

            async with factory() as db:
                await db.execute(text("UPDATE members SET status = 'active' WHERE sso_subject = 'gebruiker-1'"))
                await db.commit()

            response = await client.get("/admin")
            assert response.status_code == 200
            assert response.json()["status"] == "active"

    async def test_deactivated_member_403_with_own_message(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            set_session_cookie(client, app)
            await client.get("/admin")

            async with factory() as db:
                await db.execute(
                    text("UPDATE members SET status = 'deactivated' WHERE sso_subject = 'gebruiker-1'")
                )
                await db.commit()

            response = await client.get("/admin")
            assert response.status_code == 403
            assert response.json()["code"] == REASON_DEACTIVATED

    async def test_email_is_stored_lowercase(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            set_session_cookie(client, app, email="Gebruiker@Example.NL")
            await client.get("/admin")
        members = await _members(factory)
        assert members[0].email == "gebruiker@example.nl"


class TestBootstrap:
    async def test_bootstrap_sub_becomes_direct_admin_and_active(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory, bootstrap_admin_sub=BOOTSTRAP_SUB)
        async with make_test_client(app) as client:
            set_session_cookie(client, app, sub=BOOTSTRAP_SUB)
            response = await client.get("/admin")
            assert response.status_code == 200
            assert response.json() == {
                "member_id": response.json()["member_id"],
                "status": "active",
                "role": "admin",
            }

    async def test_existing_member_with_bootstrap_sub_becomes_promoted(self, idp, db_environment):
        _, factory = db_environment
        # First without bootstrap: the lid is created as an ordinary member.
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            set_session_cookie(client, app, sub=BOOTSTRAP_SUB)
            assert (await client.get("/admin")).status_code == 200

        # Then with bootstrap configured: the existing record is promoted.
        app_met_bootstrap = _make_admin_app(idp, factory, bootstrap_admin_sub=BOOTSTRAP_SUB)
        async with make_test_client(app_met_bootstrap) as client:
            set_session_cookie(client, app_met_bootstrap, sub=BOOTSTRAP_SUB)
            response = await client.get("/admin")
            assert response.status_code == 200
            assert response.json()["role"] == "admin"
            assert response.json()["status"] == "active"

        members = await _members(factory)
        assert len(members) == 1

    async def test_other_sub_does_not_become_promoted(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory, bootstrap_admin_sub=BOOTSTRAP_SUB)
        async with make_test_client(app) as client:
            set_session_cookie(client, app, sub="gewoon-lid-sub")
            response = await client.get("/admin")
            assert response.status_code == 200
        members = await _members(factory)
        assert members[0].platform_role.value == "member"
        assert members[0].status.value == "active"
