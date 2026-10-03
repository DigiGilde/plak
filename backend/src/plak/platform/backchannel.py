"""OIDC Back-Channel Logout 1.0: the endpoint the OP calls when a session at
the OP ends.

Lives on the admin host only (host_separation.py keeps everything under `/-/`
off the content host), takes the `logout_token` as a form field, verifies it
per section 2.6 and then drops every Plak session that token points at. Both
session kinds go: one login at the OP produced them both, so the `sid` claim
matches both.

The answer is always a bare status with `Cache-Control: no-store`, 200 on a
valid token, 400 on anything else. Whether a session was actually found never
shows: that would make this endpoint an oracle for who is logged in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, Response

from plak.audit import vocabulary
from plak.auth.oidc import (
    LOGOUT_TOKEN_MAX_AGE_S,
    OidcError,
    logout_token_prefilter,
    unverified_logout_token_jti,
)
from plak.auth.revalidation import audit_session_ended
from plak.constants import PATH_BACKCHANNEL_LOGOUT

if TYPE_CHECKING:
    from fastapi import FastAPI

    from plak.auth.oidc import OidcClient
    from plak.auth.sessions import SessionStore
    from plak.config import Settings

_logger = logging.getLogger(__name__)

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store"}

# Section 2.6 point 12: a logout token may be refused when one with the same
# jti came by recently. "Recently" is as long as a token can be valid at all.
REPLAY_TTL = timedelta(seconds=LOGOUT_TOKEN_MAX_AGE_S * 2)


@dataclass
class ReplayCache:
    """jti values already accepted, with the moment they may be forgotten."""

    _seen: dict[str, datetime] = field(default_factory=dict)

    def _prune(self, *, now: datetime) -> None:
        for key, expiry in list(self._seen.items()):
            if expiry <= now:
                del self._seen[key]

    def already_seen(self, jti: str, *, now: datetime) -> bool:
        """Read-only: called before signature verification, so it must not
        record anything - storing an unverified jti would let a caller
        pre-burn the id of a logout token the IdP has yet to send."""
        self._prune(now=now)
        return jti in self._seen

    def seen_before(self, jti: str, *, now: datetime) -> bool:
        self._prune(now=now)
        if jti in self._seen:
            return True
        self._seen[jti] = now + REPLAY_TTL
        return False


def replay_cache(app: FastAPI) -> ReplayCache:
    cache = getattr(app.state, "logout_replays", None)
    if cache is None:
        cache = ReplayCache()
        app.state.logout_replays = cache
    return cache


def _refused(message: str) -> JSONResponse:
    """Section 2.8: a refusal is a 400 with the OAuth error body. The
    description stays generic; which check tripped is for our own log."""
    return JSONResponse(
        {"error": "invalid_request", "error_description": message},
        status_code=400,
        headers=NO_STORE,
    )


@router.post(PATH_BACKCHANNEL_LOGOUT, include_in_schema=False)
async def backchannel_logout(
    request: Request, logout_token: Annotated[str | None, Form()] = None
) -> Response:
    if not logout_token:
        return _refused("logout_token is missing")

    # Cheap refusals first: this endpoint is unauthenticated and its rate
    # limit keys on a client-controlled address (see docs/security.md), so
    # nothing but this bounds how often a caller can ask for a signature
    # verification.
    settings: Settings = request.app.state.settings
    prefilter_reason = logout_token_prefilter(logout_token, settings)
    if prefilter_reason is not None:
        _logger.warning("Back-channel logout refused (prefilter): %s", prefilter_reason)
        return _refused("logout_token is not valid")

    unverified_jti = unverified_logout_token_jti(logout_token)
    assert unverified_jti is not None  # noqa: S101 - the prefilter above requires a jti
    if replay_cache(request.app).already_seen(unverified_jti, now=datetime.now(UTC)):
        _logger.warning("Back-channel logout refused: logout_token already used")
        return _refused("logout_token has already been used")

    oidc: OidcClient = request.app.state.oidc_client
    try:
        token = await oidc.validate_logout_token(logout_token)
    except OidcError as error:
        _logger.warning("Back-channel logout refused: %s", error)
        return _refused("logout_token is not valid")

    if replay_cache(request.app).seen_before(token.jti, now=datetime.now(UTC)):
        _logger.warning("Back-channel logout refused: logout_token already used")
        return _refused("logout_token has already been used")

    store: SessionStore = request.app.state.session_store
    for session in store.sessions_for_logout(sid=token.sid, sub=token.sub):
        store.delete_session(session.id)
        await audit_session_ended(request, session, vocabulary.IDP_BACKCHANNEL_LOGOUT)

    return Response(status_code=200, headers=NO_STORE)


__all__ = ["NO_STORE", "ReplayCache", "router"]
