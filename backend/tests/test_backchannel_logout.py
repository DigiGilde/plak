"""Back-channel logout endpoint (platform/backchannel.py).

Everything a logout token has to survive per OIDC Back-Channel Logout 1.0
section 2.6, and what happens to the sessions it points at.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from helpers_audit import install_audit_recorder
from helpers_oidc import OMIT, MockIdP, make_app, make_settings, make_test_client
from joserfc.jwk import RSAKey

from plak.audit import vocabulary
from plak.auth.sessions import SessionStore
from plak.constants import PATH_BACKCHANNEL_LOGOUT
from plak.host_separation import belongs_to_content
from plak.platform.backchannel import ReplayCache, replay_cache

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
    foreign_key = RSAKey.generate_key(2048)
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid=idp.sid, key=foreign_key))

    assert response.status_code == 400
    assert app.state.session_store.get_session(session.id) is not None


def _b64(obj) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


@pytest.mark.parametrize("crit", [5, None, True, [[1]], [{}]])
async def test_a_crit_header_of_the_wrong_type_is_refused_like_any_other(crit) -> None:
    """joserfc walks `crit` before it type-checks it and raises a bare
    TypeError, before any signature is checked, so no key is needed to send
    one. It has to end as the same refusal as a forged signature, not a 500."""
    idp, app = _make()
    now = int(time.time())
    header = {"alg": "RS256", "kid": idp.kid, "crit": crit}
    payload = {
        "iss": idp.issuer,
        "aud": idp.client_id,
        "iat": now,
        "exp": now + 120,
        "jti": f"crit-{crit!r}",
        "sid": idp.sid,
        "events": {"http://schemas.openid.net/event/backchannel-logout": {}},
    }
    forged = f"{_b64(header)}.{_b64(payload)}.{_b64('x' * 256)}"
    async with make_test_client(app) as client:
        response = await _post(client, forged)
        reference = await _post(client, idp.make_logout_token(sid=idp.sid, key=RSAKey.generate_key(2048)))

    assert response.status_code == 400
    assert response.content == reference.content
    assert response.headers["cache-control"] == "no-store"


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


async def test_a_token_without_jti_is_refused() -> None:
    """Section 2.4 makes the jti REQUIRED. Without one there is nothing to key
    the replay check on, so such a token could be replayed at will."""
    idp, app = _make()
    session = _session(app)
    token = idp.make_logout_token(sid=idp.sid, jti=OMIT)
    async with make_test_client(app) as client:
        response = await _post(client, token)

    assert response.status_code == 400
    assert app.state.session_store.get_session(session.id) is not None


async def test_a_token_without_exp_is_refused() -> None:
    """Section 2.4 makes exp REQUIRED as well."""
    idp, app = _make()
    session = _session(app)
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid=idp.sid, exp=OMIT))

    assert response.status_code == 400
    assert app.state.session_store.get_session(session.id) is not None


async def test_an_expired_entry_is_pruned_on_the_next_access() -> None:
    """The prune loop in seen_before() only ever deletes an entry that has
    already expired by the time some other jti is checked."""
    cache = ReplayCache()
    start = datetime.now(UTC)
    assert cache.seen_before("verlopen", now=start) is False

    later = start + timedelta(hours=1)
    assert cache.seen_before("een-andere", now=later) is False
    assert "verlopen" not in cache._seen


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


# ---------------------------------------------------------------------------
# The prefilter: an implausible token must not reach a signature verification
# ---------------------------------------------------------------------------


async def _post_refused_before_verification(app, client, token: str):
    """Posts a token with the verification path spied on; reaching it fails
    the test rather than the request."""
    with patch.object(
        app.state.oidc_client,
        "_fetch_keyset",
        new_callable=AsyncMock,
        side_effect=AssertionError("signature verification reached"),
    ):
        return await _post(client, token)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"iss": "https://evil.example/realms/x"},
        {"events": {"http://example.com/other-event": {}}},
        {"events": "not-a-dict"},
        {"jti": ""},
        {"nonce": "hoort-hier-niet"},
        {"iat": int(time.time()) - 999},
    ],
)
async def test_an_implausible_logout_token_never_reaches_verification(kwargs: dict) -> None:
    idp, app = _make()
    session = _session(app)
    token = idp.make_logout_token(sid=idp.sid, **kwargs)
    async with make_test_client(app) as client:
        response = await _post_refused_before_verification(app, client, token)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert app.state.session_store.get_session(session.id) is not None


@pytest.mark.parametrize("token", ["x" * 9000, "not-a-jwt", "aGVhZGVy.aGVhZGVy.sig"])
async def test_a_malformed_logout_token_never_reaches_verification(token: str) -> None:
    _, app = _make()
    async with make_test_client(app) as client:
        response = await _post_refused_before_verification(app, client, token)

    assert response.status_code == 400


async def test_a_logout_token_with_a_foreign_typ_or_alg_never_reaches_verification() -> None:
    idp, app = _make()
    token = idp.make_logout_token(sid=idp.sid)
    header_b64, payload_b64, signature_b64 = token.split(".")

    def _rewritten(**changes: object) -> str:
        decoded = json.loads(base64.urlsafe_b64decode(header_b64 + "=" * (-len(header_b64) % 4)))
        decoded.update(changes)
        raw = base64.urlsafe_b64encode(json.dumps(decoded).encode()).rstrip(b"=").decode()
        return f"{raw}.{payload_b64}.{signature_b64}"

    async with make_test_client(app) as client:
        typ_response = await _post_refused_before_verification(app, client, _rewritten(typ="at+jwt"))
        alg_response = await _post_refused_before_verification(app, client, _rewritten(alg="none"))

    assert typ_response.status_code == 400
    assert alg_response.status_code == 400


async def test_a_replayed_jti_is_refused_before_verification() -> None:
    """A jti already in the replay cache is refused on a read, before the
    JWKS fetch and signature verification that full validation would need."""
    idp, app = _make()
    token = idp.make_logout_token(sid=idp.sid, jti="jti-seen-before")
    replay_cache(app)._seen["jti-seen-before"] = datetime.now(UTC) + timedelta(seconds=60)

    async with make_test_client(app) as client:
        response = await _post_refused_before_verification(app, client, token)

    assert response.status_code == 400
    assert response.json()["error_description"] == "logout_token has already been used"


async def test_a_jti_lost_to_the_precheck_race_is_still_refused_after_validation() -> None:
    """Between the pre-check read and the full validation, another request for
    the same token may have completed. Blinding the pre-check forces every
    call through full validation, so the check that runs after it (the one
    that also records the jti) is what actually settles the race."""
    idp, app = _make()
    token = idp.make_logout_token(sid=idp.sid, jti="jti-race-1")

    with patch.object(ReplayCache, "already_seen", return_value=False):
        async with make_test_client(app) as client:
            first = await _post(client, token)
            second = await _post(client, token)

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.json()["error_description"] == "logout_token has already been used"


async def test_a_valid_token_still_ends_the_session_with_the_prefilter_in_place() -> None:
    idp, app = _make()
    session = _session(app)
    async with make_test_client(app) as client:
        response = await _post(client, idp.make_logout_token(sid=idp.sid))

    assert response.status_code == 200
    assert app.state.session_store.get_session(session.id) is None


async def test_the_prefilter_refusal_is_byte_identical_to_the_full_validation_refusal() -> None:
    """No oracle between the two: a caller must not be able to tell whether a
    token failed the cheap pre-check or the full, signature-verified check."""
    idp, app = _make()
    prefilter_token = idp.make_logout_token(sid=idp.sid, iss="https://evil.example/realms/x")
    forged_key = RSAKey.generate_key(2048)
    full_validation_token = idp.make_logout_token(sid=idp.sid, key=forged_key)

    async with make_test_client(app) as client:
        prefilter_response = await _post(client, prefilter_token)
        full_validation_response = await _post(client, full_validation_token)

    assert prefilter_response.status_code == full_validation_response.status_code == 400
    assert prefilter_response.content == full_validation_response.content
    assert prefilter_response.headers["cache-control"] == full_validation_response.headers["cache-control"]


async def test_the_prefilter_replay_refusal_is_byte_identical_to_the_post_validation_one() -> None:
    idp, app = _make()
    early_token = idp.make_logout_token(sid=idp.sid, jti="eenmalig-vroeg")
    late_token = idp.make_logout_token(sid=idp.sid, jti="jti-race-2")

    async with make_test_client(app) as client:
        assert (await _post(client, early_token)).status_code == 200
        prefilter_replay_response = await _post(client, early_token)

        with patch.object(ReplayCache, "already_seen", return_value=False):
            assert (await _post(client, late_token)).status_code == 200
            post_validation_replay_response = await _post(client, late_token)

    assert prefilter_replay_response.status_code == post_validation_replay_response.status_code == 400
    assert prefilter_replay_response.content == post_validation_replay_response.content
