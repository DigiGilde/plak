"""Back-channel logout endpoint (platform/backchannel.py).

Everything a logout token has to survive per OIDC Back-Channel Logout 1.0
section 2.6, and what happens to the sessions it points at.
"""

from __future__ import annotations

import time

import pytest
from authlib.jose import RSAKey
from helpers_audit import install_audit_recorder
from helpers_oidc import OMIT, MockIdP, make_app, make_settings, make_test_client

from plak.audit import vocabulary
from plak.auth.sessions import SessionStore
from plak.constants import PATH_BACKCHANNEL_LOGOUT
from plak.host_separation import belongs_to_content

pytestmark = pytest.mark.asyncio

PATH = PATH_BACKCHANNEL_LOGOUT


def _make():
    idp = MockIdP()
    app = make_app(make_settings(idp), idp)
    install_audit_recorder(app)
    return idp, app


def _session(app, *, sub="gebruiker-1", sid="sessie-bij-de-idp-1", kind=None):
    store: SessionStore = app.state.session_store
    extra = {"kind": kind} if kind is not None else {}
    return store.create_session(
        sub=sub, email=None, email_verified=False, acr="urn:acr:hoog", sid=sid, **extra
    )


async def _post(client, token: str | None):
    data = {"logout_token": token} if token is not None else {}
    return await client.post(PATH, data=data)


async def test_a_valid_sid_logout_drops_the_session() -> None:
    idp, app = _make()
    session = _session(app)
    recorder = app.state.audit_log
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid=idp.sid))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert app.state.session_store.get_session(session.id) is None
    record = recorder.only()
    assert record.action == vocabulary.IDP_SESSION_ENDED_ACTION
    assert record.reason_code == vocabulary.IDP_BACKCHANNEL_LOGOUT
    assert record.refs == {"kind": "admin"}


async def test_a_sid_logout_drops_both_session_kinds_of_that_login() -> None:
    from plak.auth.sessions import SessionKind

    idp, app = _make()
    admin = _session(app)
    content = _session(app, kind=SessionKind.CONTENT)
    other = _session(app, sid="een-andere-sessie")
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid=idp.sid))

    assert response.status_code == 200
    store: SessionStore = app.state.session_store
    assert store.get_session(admin.id) is None
    assert store.get_session(content.id) is None
    assert store.get_session(other.id) is not None


async def test_a_sub_logout_drops_every_session_of_that_person() -> None:
    idp, app = _make()
    first = _session(app, sid="sessie-a")
    second = _session(app, sid="sessie-b")
    somebody_else = _session(app, sub="gebruiker-2", sid="sessie-c")
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sub="gebruiker-1"))

    assert response.status_code == 200
    store: SessionStore = app.state.session_store
    assert store.get_session(first.id) is None
    assert store.get_session(second.id) is None
    assert store.get_session(somebody_else.id) is not None


async def test_a_token_without_a_matching_session_still_answers_200() -> None:
    """No oracle: whether anyone was logged in never shows."""
    idp, app = _make()
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid="niemand"))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


async def test_a_missing_token_is_refused() -> None:
    _, app = _make()
    async with make_test_client(app) as client:
        response = await _post(client, None)

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["error"] == "invalid_request"


async def test_a_forged_signature_is_refused() -> None:
    idp, app = _make()
    session = _session(app)
    foreign_key = RSAKey.generate_key(2048, is_private=True)
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid=idp.sid, key=foreign_key))

    assert response.status_code == 400
    assert app.state.session_store.get_session(session.id) is not None


async def test_an_expired_token_is_refused() -> None:
    idp, app = _make()
    session = _session(app)
    old = int(time.time()) - 3600
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid=idp.sid, iat=old, exp=old + 60))

    assert response.status_code == 400
    assert app.state.session_store.get_session(session.id) is not None


async def test_a_stale_iat_is_refused() -> None:
    idp, app = _make()
    async with make_test_client(app) as client:
        response = await _post(
            client, idp.make_logout_token(sid=idp.sid, iat=int(time.time()) - 3600)
        )

    assert response.status_code == 400


async def test_a_wrong_audience_is_refused() -> None:
    idp, app = _make()
    session = _session(app)
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid=idp.sid, aud="een-andere-client"))

    assert response.status_code == 400
    assert app.state.session_store.get_session(session.id) is not None


async def test_a_wrong_issuer_is_refused() -> None:
    idp, app = _make()
    async with make_test_client(app) as client:
        response = await _post(
            client, idp.make_logout_token(sid=idp.sid, iss="https://andere-idp.example")
        )

    assert response.status_code == 400


async def test_a_token_without_the_event_is_refused() -> None:
    idp, app = _make()
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid=idp.sid, with_event=False))

    assert response.status_code == 400


async def test_a_token_with_a_nonce_is_refused() -> None:
    """Section 2.4: a nonce marks an id token being replayed as a logout token."""
    idp, app = _make()
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid=idp.sid, nonce="n-1"))

    assert response.status_code == 400


async def test_a_token_without_sub_and_sid_is_refused() -> None:
    idp, app = _make()
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(jti="zonder-subject"))

    assert response.status_code == 400


async def test_a_replayed_token_is_refused() -> None:
    idp, app = _make()
    first = _session(app)
    token = idp.make_logout_token(sid=idp.sid, jti="eenmalig")
    async with make_test_client(app) as client:
        assert (await _post(client, token)).status_code == 200
        second = _session(app)
        response = await _post(client, token)

    assert response.status_code == 400
    assert app.state.session_store.get_session(first.id) is None
    assert app.state.session_store.get_session(second.id) is not None


async def test_a_token_without_jti_is_not_replay_checked() -> None:
    """The jti is optional in the spec; without one there is nothing to key a
    replay cache on, and refusing the token outright would break an OP that
    leaves it out."""
    idp, app = _make()
    token = idp.make_logout_token(sid=idp.sid, jti=OMIT)
    async with make_test_client(app) as client:
        assert (await _post(client, token)).status_code == 200
        assert (await _post(client, token)).status_code == 200


async def test_the_endpoint_does_not_exist_on_the_content_host() -> None:
    assert belongs_to_content(PATH) is False


async def test_the_token_never_shows_up_in_the_audit(caplog: pytest.LogCaptureFixture) -> None:
    idp, app = _make()
    _session(app)
    token = idp.make_logout_token(sid=idp.sid)
    recorder = app.state.audit_log
    async with make_test_client(app) as client:
        await _post(client, token)

    assert token not in repr(recorder.records)
    assert token not in caplog.text
    assert idp.sid not in repr(recorder.records)
