"""Periodic re-validation of a session against the IdP (auth/revalidation.py).

The mock IdP answers the refresh grant, and can refuse it the way a real OP
does: hard (`invalid_grant`, the session there is over) or soft (a 5xx, an
unreachable endpoint).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from helpers_audit import install_audit_recorder
from helpers_oidc import (
    OMIT,
    MockIdP,
    make_app,
    make_content_test_client,
    make_settings,
    make_test_client,
    set_content_session_cookie,
    set_session_cookie,
)

from plak.audit import vocabulary
from plak.auth.revalidation import RECHECK_BACKOFF, idp_fault, revalidation_status
from plak.auth.sessions import SessionStore
from plak.models.audit import ActorKind

pytestmark = pytest.mark.asyncio

RECHECK_S = 900


def _make(idp: MockIdP | None = None, **overrides):
    idp = idp or MockIdP()
    settings = make_settings(idp, **{"idp_recheck_seconds": RECHECK_S, **overrides})
    app = make_app(settings, idp)
    return idp, app


def _age(app, session, *, seconds: int) -> None:
    """Puts the last confirmation that many seconds into the past."""
    store: SessionStore = app.state.session_store
    aged = replace(session, checked_at=datetime.now(UTC) - timedelta(seconds=seconds))
    store._sessions[session.id] = aged


def _refresh_grants(idp: MockIdP) -> list[dict]:
    return [form for form in idp.token_requests if form.get("grant_type") == ["refresh_token"]]


async def _visit(client) -> None:
    """Any request through the middleware; the route itself does not matter."""
    await client.get("/-/logout")


async def test_no_recheck_before_the_interval() -> None:
    idp, app = _make()
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S - 60)

        await _visit(client)

        assert _refresh_grants(idp) == []
        assert app.state.session_store.get_session(session.id) is not None


async def test_recheck_after_the_interval() -> None:
    idp, app = _make()
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        await _visit(client)

        grants = _refresh_grants(idp)
        assert len(grants) == 1
        assert grants[0]["refresh_token"] == ["ververstoken-1"]
        kept = app.state.session_store.get_session(session.id)
        assert kept is not None
        assert kept.checked_at is not None


async def test_a_second_request_right_after_a_check_does_not_check_again() -> None:
    idp, app = _make()
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        await _visit(client)
        await _visit(client)

        assert len(_refresh_grants(idp)) == 1


async def test_a_rotated_refresh_token_is_stored() -> None:
    idp, app = _make()
    idp.next_refresh_token = "ververstoken-2"
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        await _visit(client)

        assert app.state.session_store.get_session(session.id).refresh_token == "ververstoken-2"


async def test_a_hard_failure_logs_out_and_audits() -> None:
    idp, app = _make()
    idp.refresh_error = "invalid_grant"
    idp.refresh_status = 400
    recorder = install_audit_recorder(app)
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        await _visit(client)

        assert app.state.session_store.get_session(session.id) is None
        record = recorder.only()
        assert record.action == vocabulary.IDP_SESSION_ENDED_ACTION
        assert record.result == vocabulary.ALLOWED
        assert record.reason_code == vocabulary.IDP_SESSION_ENDED
        assert record.actor.kind is ActorKind.MEMBER
        assert record.refs == {"kind": "admin"}


async def test_a_dropped_session_answers_as_not_logged_in() -> None:
    idp, app = _make()
    idp.refresh_error = "invalid_grant"
    idp.refresh_status = 400
    install_audit_recorder(app)
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        # The logout route writes an audit record only when it still sees a
        # session; with the session dropped it just redirects.
        response = await client.post("/-/logout")

        assert response.status_code == 303
        assert app.state.session_store.get_session(session.id) is None


async def test_a_soft_failure_keeps_the_session_and_backs_off() -> None:
    idp, app = _make()
    idp.refresh_status = 503
    recorder = install_audit_recorder(app)
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        await _visit(client)

        kept = app.state.session_store.get_session(session.id)
        assert kept is not None
        assert kept.recheck_not_before is not None
        assert recorder.records == []

        # Within the backoff no second attempt is made.
        await _visit(client)
        assert len(_refresh_grants(idp)) == 1


async def test_after_the_backoff_the_check_is_tried_again() -> None:
    idp, app = _make()
    idp.refresh_status = 503
    install_audit_recorder(app)
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)
        await _visit(client)

        store: SessionStore = app.state.session_store
        store.defer_check(session.id, until=datetime.now(UTC) - RECHECK_BACKOFF)
        idp.refresh_status = 200
        await _visit(client)

        assert len(_refresh_grants(idp)) == 2
        assert store.get_session(session.id) is not None


async def test_a_sub_mismatch_drops_the_session() -> None:
    idp, app = _make()
    idp.refresh_claim_overrides = {"sub": "iemand-anders"}
    recorder = install_audit_recorder(app)
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        await _visit(client)

        assert app.state.session_store.get_session(session.id) is None
        assert recorder.only().reason_code == vocabulary.IDP_SUB_MISMATCH


async def test_a_refreshed_id_token_from_another_issuer_drops_the_session() -> None:
    idp, app = _make()
    idp.refresh_claim_overrides = {"iss": "https://andere-idp.example"}
    recorder = install_audit_recorder(app)
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        await _visit(client)

        assert app.state.session_store.get_session(session.id) is None
        assert recorder.only().reason_code == vocabulary.IDP_TOKEN_INVALID


@pytest.mark.parametrize("claim", ["exp", "iat"])
async def test_a_refreshed_id_token_without_exp_or_iat_drops_the_session(claim: str) -> None:
    """authlib treats both as optional; the refresh path demands them like the
    login path does."""
    idp, app = _make()
    idp.refresh_claim_overrides = {claim: OMIT}
    recorder = install_audit_recorder(app)
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        await _visit(client)

        assert app.state.session_store.get_session(session.id) is None
        assert recorder.only().reason_code == vocabulary.IDP_TOKEN_INVALID


async def test_a_response_without_an_id_token_keeps_the_session() -> None:
    """RFC 6749 does not require one; the refresh itself is then the proof."""
    idp, app = _make()
    idp.refresh_id_token = False
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        await _visit(client)

        assert app.state.session_store.get_session(session.id) is not None


async def test_a_session_without_a_refresh_token_is_kept_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    idp, app = _make()
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app)
        _age(app, session, seconds=RECHECK_S + 1)

        with caplog.at_level(logging.WARNING):
            await _visit(client)

        assert _refresh_grants(idp) == []
        assert app.state.session_store.get_session(session.id) is not None
        assert "verversingstoken" in caplog.text


async def test_recheck_off_never_calls_the_idp() -> None:
    idp, app = _make(idp_recheck_seconds=0)
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=10 * RECHECK_S)

        await _visit(client)

        assert _refresh_grants(idp) == []
        assert app.state.session_store.get_session(session.id) is not None


async def test_a_content_session_is_rechecked_too() -> None:
    idp, app = _make()
    idp.refresh_error = "invalid_grant"
    idp.refresh_status = 400
    recorder = install_audit_recorder(app)
    async with make_content_test_client(app) as client:
        session = set_content_session_cookie(client, app, refresh_token="ververstoken-1")
        _age(app, session, seconds=RECHECK_S + 1)

        await client.get("/-/logout")

        assert app.state.session_store.get_session(session.id) is None
        assert recorder.only().refs == {"kind": "content"}


async def test_no_token_material_in_logs_or_audit_refs(caplog: pytest.LogCaptureFixture) -> None:
    idp, app = _make()
    idp.refresh_error = "invalid_grant"
    idp.refresh_status = 400
    recorder = install_audit_recorder(app)
    async with make_test_client(app) as client:
        session = set_session_cookie(client, app, refresh_token="geheim-ververstoken")
        _age(app, session, seconds=RECHECK_S + 1)

        with caplog.at_level(logging.DEBUG):
            await _visit(client)

        assert "geheim-ververstoken" not in caplog.text
        assert "geheim-ververstoken" not in repr(recorder.records)
        # The session dataclass itself keeps it out of a dump as well.
        assert "geheim-ververstoken" not in repr(session)


async def test_the_login_stores_the_refresh_token_and_sid() -> None:
    from helpers_oidc import complete_login

    idp, app = _make()
    recorder = install_audit_recorder(app)
    assert recorder is not None
    async with make_test_client(app) as client:
        response = await complete_login(client, idp)
        assert response.status_code == 303

    sessions = list(app.state.session_store._sessions.values())
    assert len(sessions) == 1
    assert sessions[0].refresh_token == idp.refresh_token
    assert sessions[0].sid == idp.sid


class TestBrokenCoupling:
    """A token endpoint that refuses our client, not the session: nobody is
    logged out, but it is loud (ERROR once per window, visible on /healthz)."""

    @staticmethod
    def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
        return [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR and "IdP re-validation is failing" in record.getMessage()
        ]

    async def test_the_session_is_kept_and_the_error_is_logged_once_per_window(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        idp, app = _make()
        idp.refresh_error = "invalid_client"
        idp.refresh_status = 401
        recorder = install_audit_recorder(app)
        async with make_test_client(app) as client:
            session = set_session_cookie(client, app, refresh_token="ververstoken-1")
            _age(app, session, seconds=RECHECK_S + 1)

            with caplog.at_level(logging.DEBUG):
                await _visit(client)
                # Within the backoff window: no second attempt, no second line.
                await _visit(client)

        assert app.state.session_store.get_session(session.id) is not None
        assert recorder.records == []
        errors = self._errors(caplog)
        assert len(errors) == 1, errors
        assert "invalid_client" in errors[0]
        assert idp.issuer in errors[0]

    async def test_after_the_window_it_complains_again(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        idp, app = _make()
        idp.refresh_error = "invalid_client"
        idp.refresh_status = 401
        install_audit_recorder(app)
        async with make_test_client(app) as client:
            session = set_session_cookie(client, app, refresh_token="ververstoken-1")
            _age(app, session, seconds=RECHECK_S + 1)
            with caplog.at_level(logging.DEBUG):
                await _visit(client)

                store: SessionStore = app.state.session_store
                store.defer_check(session.id, until=datetime.now(UTC) - RECHECK_BACKOFF)
                fault = idp_fault(app)
                fault.log_again_at = datetime.now(UTC) - RECHECK_BACKOFF
                await _visit(client)

        assert len(self._errors(caplog)) == 2

    async def test_healthz_reports_the_fault_and_recovers(self) -> None:
        idp, app = _make()
        idp.refresh_error = "invalid_client"
        idp.refresh_status = 401
        install_audit_recorder(app)
        assert revalidation_status(app) is None
        async with make_test_client(app) as client:
            session = set_session_cookie(client, app, refresh_token="ververstoken-1")
            _age(app, session, seconds=RECHECK_S + 1)
            await _visit(client)

            assert revalidation_status(app) == "IdP re-validation is failing: invalid_client"

            idp.refresh_error = None
            idp.refresh_status = 200
            store: SessionStore = app.state.session_store
            store.defer_check(session.id, until=datetime.now(UTC) - RECHECK_BACKOFF)
            await _visit(client)

        assert revalidation_status(app) is None

    async def test_an_unknown_error_code_stays_a_quiet_soft_failure(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        idp, app = _make()
        idp.refresh_error = "temporarily_unavailable"
        idp.refresh_status = 503
        install_audit_recorder(app)
        async with make_test_client(app) as client:
            session = set_session_cookie(client, app, refresh_token="ververstoken-1")
            _age(app, session, seconds=RECHECK_S + 1)
            with caplog.at_level(logging.DEBUG):
                await _visit(client)

        assert app.state.session_store.get_session(session.id) is not None
        assert self._errors(caplog) == []
        assert revalidation_status(app) is None

    async def test_invalid_grant_still_drops_the_session(self) -> None:
        idp, app = _make()
        idp.refresh_error = "invalid_grant"
        idp.refresh_status = 400
        recorder = install_audit_recorder(app)
        async with make_test_client(app) as client:
            session = set_session_cookie(client, app, refresh_token="ververstoken-1")
            _age(app, session, seconds=RECHECK_S + 1)
            await _visit(client)

        assert app.state.session_store.get_session(session.id) is None
        assert recorder.only().reason_code == vocabulary.IDP_SESSION_ENDED
        assert revalidation_status(app) is None


async def test_healthz_carries_the_idp_complaint(tmp_path) -> None:
    """The route is internal only (host separation answers a neutral 404 on
    both public hosts), so the handler is called directly, the way a probe
    inside the pod reaches it."""
    from fastapi.routing import APIRoute

    from plak.auth.revalidation import idp_fault
    from plak.main import create_app

    idp = MockIdP()
    app = create_app(make_settings(idp, content_root=tmp_path, base_url="https://beheer.plak.example"))
    route = next(r for r in app.routes if isinstance(r, APIRoute) and r.path == "/healthz")

    assert await route.endpoint() == {"status": "ok"}

    idp_fault(app).record(
        code="invalid_client", issuer=idp.issuer, now=datetime.now(UTC), window=RECHECK_BACKOFF
    )
    assert await route.endpoint() == {
        "status": "degraded",
        "idp_revalidation": "IdP re-validation is failing: invalid_client",
    }
