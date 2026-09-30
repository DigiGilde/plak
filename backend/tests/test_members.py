"""Tests for auth/members.py: lid upsert only on a /admin visit, active from
the first login, deactivation, reactivation and bootstrap promotion. Runs
against the testcontainers PostgreSQL from conftest.py; the mock IdP comes
from helpers_oidc."""

from __future__ import annotations

from typing import Annotated
from unittest.mock import AsyncMock

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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from plak.api.errors import register_error_handlers
from plak.auth.members import REASON_DEACTIVATED, get_or_create_member, require_active_member
from plak.auth.sessions import SessionStore
from plak.db import make_session_factory
from plak.models.identity import Member, MemberStatus, PlatformRole

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


    async def test_an_unverified_email_is_not_stored(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            set_session_cookie(client, app, email="alice@example.nl", email_verified=False)
            assert (await client.get("/admin")).status_code == 200
        members = await _members(factory)
        assert members[0].email == ""

    async def test_a_later_verified_email_fills_the_gap(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            set_session_cookie(client, app, email="alice@example.nl", email_verified=False)
            await client.get("/admin")
            set_session_cookie(client, app, email="Gebruiker@Example.NL", email_verified=True)
            await client.get("/admin")
        members = await _members(factory)
        assert [member.email for member in members] == ["gebruiker@example.nl"]

    async def test_a_stored_email_is_not_replaced_by_a_later_login(self, idp, db_environment):
        _, factory = db_environment
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            set_session_cookie(client, app, email="gebruiker@example.nl", email_verified=True)
            await client.get("/admin")
            set_session_cookie(client, app, email="ander@example.nl", email_verified=True)
            await client.get("/admin")
            set_session_cookie(client, app, email="alice@example.nl", email_verified=False)
            await client.get("/admin")
        members = await _members(factory)
        assert [member.email for member in members] == ["gebruiker@example.nl"]


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


class TestConcurrentFirstVisit:
    async def test_concurrent_insert_falls_back_to_the_existing_row(self, idp, db_environment):
        """Two concurrent first visits: the unique constraint on sso_subject
        wins one of them; the loser adopts the record the winner created,
        instead of raising or creating a duplicate."""
        _, factory = db_environment
        store = SessionStore()
        session = store.create_session(
            sub="race-sub", email="race@example.nl", email_verified=True, acr="urn:acr:hoog"
        )

        async with factory() as db:
            async def racing_commit() -> None:
                # Simulates a second, concurrent request whose insert commits
                # first: our own db.commit() below fails on the unique
                # constraint instead.
                async with factory() as other:
                    other.add(
                        Member(
                            sso_subject="race-sub",
                            email="",
                            platform_role=PlatformRole.MEMBER,
                            status=MemberStatus.ACTIVE,
                        )
                    )
                    await other.commit()
                raise IntegrityError("insert", {}, Exception("duplicate key value violates unique constraint"))

            db.commit = AsyncMock(side_effect=racing_commit)
            member = await get_or_create_member(db, session, "")

        assert member.sso_subject == "race-sub"
        members = await _members(factory)
        assert len(members) == 1


class TestTheDisplayName:
    """The `name` claim, the one field the IdP fills and nothing else writes."""

    async def test_the_name_from_the_claim_lands_on_a_new_member(self, idp, db_environment):
        _, factory = db_environment
        store = SessionStore()
        session = store.create_session(
            sub="naam-sub",
            email="naam@example.nl",
            email_verified=True,
            acr="urn:acr:hoog",
            name="Stéphanie Anne-marie de Vries",
        )

        async with factory() as db:
            member = await get_or_create_member(db, session, "")

        assert member.name == "Stéphanie Anne-marie de Vries"

    async def test_a_changed_name_follows_on_the_next_visit(self, idp, db_environment):
        """Unlike the email, which is only filled where it is missing, the name
        follows the IdP: nothing else writes it, so a correction has to show."""
        _, factory = db_environment
        store = SessionStore()
        first = store.create_session(
            sub="hernoemd", email="h@example.nl", email_verified=True, acr="urn:acr:hoog", name="R. Bos"
        )
        async with factory() as db:
            await get_or_create_member(db, first, "")

        later = store.create_session(
            sub="hernoemd", email="h@example.nl", email_verified=True, acr="urn:acr:hoog", name="Robbert Bos"
        )
        async with factory() as db:
            member = await get_or_create_member(db, later, "")

        assert member.name == "Robbert Bos"

    async def test_a_login_without_the_claim_leaves_the_name_alone(self, idp, db_environment):
        """An IdP that stops sending the claim must not erase what it told us
        before: an empty name would read as "Onbekend" in the interface."""
        _, factory = db_environment
        store = SessionStore()
        named = store.create_session(
            sub="stil", email="s@example.nl", email_verified=True, acr="urn:acr:hoog", name="Wim Wever"
        )
        async with factory() as db:
            await get_or_create_member(db, named, "")

        silent = store.create_session(
            sub="stil", email="s@example.nl", email_verified=True, acr="urn:acr:hoog"
        )
        async with factory() as db:
            member = await get_or_create_member(db, silent, "")

        assert member.name == "Wim Wever"

    async def test_the_claim_travels_the_whole_login_into_the_record(self, idp, db_environment):
        """The mapping in platform/pages.py, not just the member upsert: the
        claim has to survive the token exchange and the session to reach the
        row the interface reads."""
        _, factory = db_environment
        idp.token_claim_overrides = {"name": "Robbert Bos"}
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            await complete_login(client, idp)
            assert (await client.get("/admin")).status_code == 200

        members = await _members(factory)
        assert members[0].name == "Robbert Bos"

    async def test_a_structured_name_is_refused(self, idp, db_environment):
        """An IdP that sends an object where OIDC says string would otherwise
        put that object where the interface prints a person."""
        _, factory = db_environment
        idp.token_claim_overrides = {"name": {"given": "Robbert", "family": "Bos"}}
        app = _make_admin_app(idp, factory)
        async with make_test_client(app) as client:
            await complete_login(client, idp)
            assert (await client.get("/admin")).status_code == 200

        members = await _members(factory)
        assert members[0].name is None
