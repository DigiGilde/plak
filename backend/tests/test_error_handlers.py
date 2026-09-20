"""The two error handlers that have no route of their own in the app.

`IngestError` is raised deep inside the publishing chain, and a
`RequestValidationError` outside `/-/api/` comes from the platform and
content routes. Both branches are driven here from a mini app of their own,
because no request travelling the real routes ever reaches them.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from helpers_audit import install_audit_recorder

from plak import messages
from plak.api.errors import PROBLEM_CONTENT_TYPE, ApiError, register_error_handlers
from plak.audit import vocabulary
from plak.ingest.service import IngestError
from plak.messages import Msg
from plak.models.audit import ActorKind

pytestmark = pytest.mark.asyncio


def _app() -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/-/api/v1/crashes")
    async def crashes() -> None:
        raise IngestError("ROLLBACK_TARGET_PREVIEW")

    @app.get("/{group}/counts")
    async def counts(group: str, count: int) -> dict[str, int]:
        return {"count": count, "group": len(group)}

    return app


async def _fetch(path: str, headers: dict[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="https://beheer.plak.example") as client:
        return await client.get(path, headers=headers)


async def test_ingest_error_becomes_a_422_with_are_own_code() -> None:
    response = await _fetch("/-/api/v1/crashes")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert response.json()["code"] == "ROLLBACK_TARGET_PREVIEW"


# -- The language of an answer (plak/messages.py) ---------------------------


async def test_a_refusal_is_english_when_the_client_asks_for_nothing() -> None:
    body = (await _fetch("/-/api/v1/crashes")).json()

    assert body["detail"] == messages.render("en", Msg("ROLLBACK_TARGET_PREVIEW"))
    assert body["title"] == messages.TITLES_EN[422]


async def test_a_refusal_is_dutch_when_the_client_asks_for_dutch() -> None:
    """What the beheer SPA does: it sends `nl`, so its users keep reading
    Dutch while the interface itself is still Dutch."""
    body = (await _fetch("/-/api/v1/crashes", {"Accept-Language": "nl"})).json()

    assert body["detail"] == messages.render("nl", Msg("ROLLBACK_TARGET_PREVIEW"))
    assert body["title"] == messages.TITLES_NL[422]


async def test_the_code_is_the_same_in_both_languages() -> None:
    english = (await _fetch("/-/api/v1/crashes")).json()
    dutch = (await _fetch("/-/api/v1/crashes", {"Accept-Language": "nl"})).json()

    assert english["code"] == dutch["code"] == "ROLLBACK_TARGET_PREVIEW"
    assert english["detail"] != dutch["detail"]


async def test_a_language_plak_does_not_have_falls_back_to_english() -> None:
    body = (await _fetch("/-/api/v1/crashes", {"Accept-Language": "fr-BE,fr;q=0.9"})).json()

    assert body["detail"] == messages.render("en", Msg("ROLLBACK_TARGET_PREVIEW"))


async def test_validation_error_outside_the_api_keeps_fastapis_own_shape() -> None:
    # Under /-/api/ the contract is problem+json; outside it (platform and
    # content routes) the shape must not silently change along with it.
    response = await _fetch("/demo/counts?count=not-a-number")

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert "detail" in response.json()


# -- Audited refusals (api/errors.py::_audit_refusal) ------------------------
#
# A mini app of its own, because no real route reaches every branch: a 401/403
# on a read, a 404 on a mutation, a route outside /-/api/ and a route that
# already audited itself all need a status this app can hand out on demand.


# Any key from the catalogue; what is under test is the audit row, not the
# wording.
REFUSAL_KEY = "NOT_ADMIN"


def _refusal_app(status: int, *, mark_audited: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.settings = SimpleNamespace(trusted_proxies="")
    register_error_handlers(app)

    @app.api_route("/-/api/v1/things/{slug}", methods=["GET", "POST", "DELETE"])
    async def _things(slug: str, request: Request) -> None:
        if mark_audited:
            request.state.audit_written = True
        raise ApiError(status, REFUSAL_KEY)

    @app.post("/outside/{slug}")
    async def _outside(slug: str) -> None:
        raise ApiError(status, REFUSAL_KEY)

    return app


async def _fetch_from(app: FastAPI, method: str, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://beheer.plak.example") as client:
        return await client.request(method, path)


async def test_refused_api_action_is_recorded() -> None:
    app = _refusal_app(403)
    recorder = install_audit_recorder(app)
    response = await _fetch_from(app, "POST", "/-/api/v1/things/someone")
    assert response.status_code == 403

    record = recorder.only()
    assert record.action == vocabulary.ADMIN_ACCESS
    assert record.result == vocabulary.REFUSED
    assert record.reason_code == REFUSAL_KEY
    assert record.refs == {"method": "POST", "route": "/-/api/v1/things/{slug}", "status": 403}
    assert record.ip


async def test_route_template_is_recorded_not_the_supplied_value() -> None:
    app = _refusal_app(403)
    recorder = install_audit_recorder(app)
    response = await _fetch_from(app, "POST", "/-/api/v1/things/someone%40example.nl")
    assert response.status_code == 403

    record = recorder.only()
    assert "someone@example.nl" not in repr(record)


async def test_401_is_recorded_without_an_actor() -> None:
    app = _refusal_app(401)
    recorder = install_audit_recorder(app)
    response = await _fetch_from(app, "POST", "/-/api/v1/things/someone")
    assert response.status_code == 401

    record = recorder.only()
    assert record.actor.kind is ActorKind.ANONYMOUS
    assert record.actor.identifier is None


async def test_not_being_signed_in_on_a_read_is_not_recorded() -> None:
    """The interface asks /me on every load; without this, every anonymous
    visit to the admin host would leave a three-year refusal behind."""
    app = _refusal_app(401)
    recorder = install_audit_recorder(app)
    response = await _fetch_from(app, "GET", "/-/api/v1/things/someone")
    assert response.status_code == 401
    assert recorder.records == []


async def test_read_miss_is_not_recorded() -> None:
    app = _refusal_app(404)
    recorder = install_audit_recorder(app)
    response = await _fetch_from(app, "GET", "/-/api/v1/things/someone")
    assert response.status_code == 404
    assert recorder.records == []


async def test_mutation_404_is_recorded() -> None:
    """The neutral 404 on a mutation stands in for a 403 that would give away
    whether the thing exists, so it counts as a refusal too."""
    app = _refusal_app(404)
    recorder = install_audit_recorder(app)
    response = await _fetch_from(app, "DELETE", "/-/api/v1/things/someone")
    assert response.status_code == 404

    record = recorder.only()
    assert record.refs["status"] == 404


async def test_conflict_is_not_recorded() -> None:
    app = _refusal_app(409)
    recorder = install_audit_recorder(app)
    response = await _fetch_from(app, "POST", "/-/api/v1/things/someone")
    assert response.status_code == 409
    assert recorder.records == []


async def test_refusal_outside_the_api_is_not_recorded() -> None:
    app = _refusal_app(403)
    recorder = install_audit_recorder(app)
    response = await _fetch_from(app, "POST", "/outside/x")
    assert response.status_code == 403
    assert recorder.records == []


async def test_endpoint_that_audited_itself_is_not_recorded_twice() -> None:
    app = _refusal_app(403, mark_audited=True)
    recorder = install_audit_recorder(app)
    response = await _fetch_from(app, "POST", "/-/api/v1/things/someone")
    assert response.status_code == 403
    assert recorder.records == []


async def test_the_record_leaves_the_response_untouched() -> None:
    """The enumeration guarantee: whether the audit log gets to see the
    request changes nothing about the response it gets back."""
    audited_app = _refusal_app(403)
    install_audit_recorder(audited_app)
    unaudited_app = _refusal_app(403)

    audited_response = await _fetch_from(audited_app, "POST", "/-/api/v1/things/someone")
    unaudited_response = await _fetch_from(unaudited_app, "POST", "/-/api/v1/things/someone")

    assert audited_response.status_code == unaudited_response.status_code
    assert audited_response.content == unaudited_response.content
    assert audited_response.headers["content-type"] == unaudited_response.headers["content-type"]
