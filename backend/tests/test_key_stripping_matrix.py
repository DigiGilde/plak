"""The access matrix over HTTP, for every visitor with a key in the query:
test_access_gate.py pins what the gate decides, this grid what serving answers
around it, on the same policies, keys and visitors. Each key value in the query
(the four links of the gate matrix and a selector alone) meets each credential
(none, every visitor with a session, a valid and a revoked key cookie) on the
live root, a preview and a `_version` view.

A link is redeemed where it is what lets the visitor in; the login redirect and
the code page stay what they were; a selector alone stays on a refusal; every
other answer takes the key out of the URL, with one and the same 302.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import set_content_session_cookie
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from test_access_gate import (
    POLICIES,
    VALID_KEY,
    VISITOR_NAMES,
    WITH_SESSION,
    World,
    expected,
    make_world,
    policy_id,
    visitors,
)
from test_serving import BASE_URL, _header_list, _make_app, make_settings

from plak.access.decision import DecisionKind
from plak.auth.sessions import KEY_COOKIE, sign_key_cookie
from plak.constants import AccessBase, AccessPolicy
from plak.ingest.store import ContentStore

SELECTOR = "selector"
KEY_VALUES = [*(name for name in VISITOR_NAMES if name.startswith("key_query")), SELECTOR]
KEY_COOKIES = ("key_cookie", "key_cookie_revoked")
CREDENTIALS = ["anonymous", *(name for name in VISITOR_NAMES if name in WITH_SESSION), *KEY_COOKIES]

# What a browser sends when it follows the link.
NAVIGATION = {"Sec-Fetch-Dest": "document"}

REDEEMED = "redeemed"
LOGIN = "login redirect"
CODE_PAGE = "code page"
NEUTRAL_404 = "neutral 404"
TAKEN_OUT = "taken out"


def _decision(policy: AccessPolicy, credential: str, key: str, route: str) -> DecisionKind:
    """What the gate decides for a credential and a key value together. Access
    is an OR, so either of the two letting the visitor in is enough; a refusal
    is the session's where there is one, and otherwise that of the key alone.
    A selector alone verifies nothing: to the gate it is a key handed in and
    refused."""
    if route == "version":
        return DecisionKind.ALLOW if credential == "group_member_active" else DecisionKind.NEUTRAL_404
    preview = route == "preview"
    by_credential, _ = expected(policy, credential, preview=preview)
    by_key, _ = expected(policy, "key_query_wrong" if key == SELECTOR else key, preview=preview)
    if DecisionKind.ALLOW in (by_credential, by_key):
        return DecisionKind.ALLOW
    return by_credential if credential in WITH_SESSION else by_key


def expected_answer(policy: AccessPolicy, credential: str, key: str, route: str) -> str:
    """What the visitor gets, following from what the gate decides. A public
    base lets everyone in before any link is looked at, and a `_version` view
    is the site team's, so neither redeems a link."""
    decision = _decision(policy, credential, key, route)
    if decision is DecisionKind.ALLOW:
        redeems = route != "version" and policy.keys and policy.base is not AccessBase.PUBLIC
        return REDEEMED if key in VALID_KEY and redeems else TAKEN_OUT
    if key == SELECTOR and route == "live" and policy.keys:
        return CODE_PAGE
    if decision is DecisionKind.LOGIN_REDIRECT:
        return LOGIN
    return NEUTRAL_404 if key == SELECTOR else TAKEN_OUT


@pytest_asyncio.fixture
async def factory(migrated_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_dsn)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                yield async_sessionmaker(
                    bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _get(app: FastAPI, world: World, credential: str, url: str) -> httpx.Response:
    """One request in a browser of its own, so that a cookie one answer sets
    never rides along with the next."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL, follow_redirects=False
    ) as client:
        if credential in WITH_SESSION:
            visitor = visitors(world)[credential]
            set_content_session_cookie(
                client,
                app,
                sub=visitor.sub,
                email=visitor.email,
                email_verified=visitor.email_verified,
                sites=("/aurora/site/",),
            )
        elif credential in KEY_COOKIES:
            key_id = world.key_id if credential == "key_cookie" else world.revoked_key_id
            signed = sign_key_cookie(app.state.settings.session_secret, str(key_id))
            client.cookies.set(KEY_COOKIE, signed, domain="plak.example", path="/")
        return await client.get(url, headers=NAVIGATION)


def _without_location(response: httpx.Response) -> list[tuple[str, str]]:
    return [(name, value) for name, value in _header_list(response) if name != "location"]


@pytest.mark.parametrize("credential", CREDENTIALS)
@pytest.mark.parametrize("policy", POLICIES, ids=policy_id)
async def test_matrix_key_in_the_query(factory, tmp_path: Path, policy, credential):
    async with factory() as db:
        world = await make_world(db, policy)
        await db.commit()
    app = _make_app(make_settings(tmp_path), factory, ContentStore(tmp_path))
    values = {name: visitors(world)[name].key_query for name in KEY_VALUES if name != SELECTOR}
    values[SELECTOR] = world.key_plain.split(".")[0]
    routes = {
        "live": "/aurora/site/",
        "preview": f"/aurora/site/_preview/{world.preview_ref}/",
        "version": f"/aurora/site/_version/{world.live_version_id}/",
    }
    neutral_404 = await _get(app, world, "anonymous", "/nergens/niks/")
    taken_out = await _get(app, world, "anonymous", f"/nergens/niks/?key={world.key_plain}")
    assert taken_out.status_code == 302

    for route, path in routes.items():
        for key, value in values.items():
            response = await _get(app, world, credential, f"{path}?x=1&key={value}")
            answer = expected_answer(policy, credential, key, route)
            cell = (route, key, answer)
            location = response.headers.get("location")
            if answer == CODE_PAGE:
                assert response.status_code == 200, cell
                assert f'value="{value}"' in response.text, cell
            elif answer == NEUTRAL_404:
                assert response.status_code == 404, cell
                assert response.content == neutral_404.content, cell
                assert _header_list(response) == _header_list(neutral_404), cell
            elif answer == LOGIN:
                assert response.status_code == 302, cell
                assert location == f"/-/login?returnTo={quote(f'{path}?x=1', safe='')}", cell
                assert "set-cookie" not in response.headers, cell
            elif answer == REDEEMED:
                assert response.status_code == 302, cell
                assert location == f"{path}?x=1", cell
                assert response.headers["set-cookie"].startswith(f"{KEY_COOKIE}="), cell
                assert f"Path={path}" in response.headers["set-cookie"], cell
            else:
                assert response.status_code == 302, cell
                assert location == f"{path}?x=1", cell
                assert response.content == taken_out.content, cell
                assert _without_location(response) == _without_location(taken_out), cell
