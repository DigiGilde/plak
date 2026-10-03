"""Periodic re-validation of a browser session against the IdP.

After login Plak keeps its own session for up to MAX_SESSION_AGE and never
asks the IdP anything again. Someone blocked or removed at Keycloak or SSO
Rijk therefore keeps their Plak session until it expires. This module bounds
that window to PLAK_IDP_RECHECK_SECONDS: on the first request after that
interval the session's refresh token goes to the token endpoint
(grant_type=refresh_token).

- success: the session stays, the rotated refresh token is stored and the
  clock starts again. The sub of a new id token has to match the session's, or
  the session goes;
- hard failure (invalid_grant: the session at the OP is over, the user is
  gone): the session is dropped and the request continues as not logged in;
- soft failure (network, timeout, 5xx): the session stays, the request is not
  blocked, and the check is retried after RECHECK_BACKOFF;
- a refusal of our client itself (invalid_client and the rest of
  CLIENT_FAULT_ERRORS) is the same for the visitor, but loud: an ERROR line
  once per backoff window and a standing complaint that /-/healthz reports.

No token material is ever logged or written to an audit ref.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from plak import net
from plak.audit import vocabulary
from plak.audit.log import Actor
from plak.auth.oidc import ClientRejectedError, OidcError, RefreshRejectedError
from plak.auth.sessions import (
    content_anchor_session_from_request,
    content_session_from_request,
    session_from_request,
)
from plak.models.audit import ActorKind

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

    from plak.auth.oidc import OidcClient
    from plak.auth.sessions import Session, SessionStore

_logger = logging.getLogger(__name__)

# After a soft failure: no new attempt for this long. Keeps an unreachable IdP
# from turning every request into a failing token call.
RECHECK_BACKOFF = timedelta(seconds=60)


@dataclass
class IdpRevalidationFault:
    """The recorded fault for a broken connection to the IdP.

    Set when the token endpoint refuses our client itself, cleared by the first
    re-validation that succeeds. Two readers: the ERROR log line (at most one
    per backoff window, so a busy site does not flood the log) and /-/healthz,
    where an operator sees it without reading logs at all.
    """

    code: str | None = None
    issuer: str | None = None
    log_again_at: datetime | None = None

    def record(self, *, code: str, issuer: str, now: datetime, window: timedelta) -> bool:
        """Registers the fault; returns whether this one is due to be logged."""
        same = code == self.code and issuer == self.issuer
        self.code = code
        self.issuer = issuer
        if same and self.log_again_at is not None and now < self.log_again_at:
            return False
        self.log_again_at = now + window
        return True

    def clear(self) -> None:
        self.code = None
        self.issuer = None
        self.log_again_at = None

    @property
    def message(self) -> str | None:
        return f"IdP revalidation is failing: {self.code}" if self.code else None


def idp_fault(app: FastAPI) -> IdpRevalidationFault:
    fault = getattr(app.state, "idp_revalidation_fault", None)
    if fault is None:
        fault = IdpRevalidationFault()
        app.state.idp_revalidation_fault = fault
    return fault


def revalidation_status(app: FastAPI) -> str | None:
    """What /-/healthz reports about the connection to the IdP; None while it is
    healthy."""
    fault = getattr(app.state, "idp_revalidation_fault", None)
    return fault.message if fault is not None else None


async def revalidate_sessions(request: Request) -> None:
    """Re-validates the sessions this request carries, when they are due.

    Called from the middleware, before any route reads a session: a session
    dropped here is simply gone from the store, so everything downstream sees
    a visitor who is not logged in.
    """
    settings = request.app.state.settings
    if settings.idp_recheck_seconds <= 0:
        return
    interval = timedelta(seconds=settings.idp_recheck_seconds)
    # The anchor as well: on the paths under `/-/` it is the only content
    # cookie there is, and the login hands out a site cookie from it.
    for session in (
        session_from_request(request),
        content_session_from_request(request) or content_anchor_session_from_request(request),
    ):
        if session is not None:
            await _revalidate(request, session, interval)


def _due(session: Session, now: datetime, interval: timedelta) -> bool:
    if now - session.last_confirmed_at < interval:
        return False
    return session.recheck_not_before is None or now >= session.recheck_not_before


def _session_lock(app: FastAPI, session_id: str) -> asyncio.Lock:
    locks = getattr(app.state, "revalidation_locks", None)
    if locks is None:
        locks = weakref.WeakValueDictionary()
        app.state.revalidation_locks = locks
    lock = locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[session_id] = lock
    return lock


async def _revalidate(request: Request, session: Session, interval: timedelta) -> None:
    if not _due(session, datetime.now(UTC), interval):
        return
    # Concurrent requests of one session would each redeem the same refresh
    # token, and an OP that makes them single-use answers all but the first
    # with invalid_grant, which drops the session. The store lives in this
    # process, so a lock per session is enough; whoever waited re-reads the
    # session the winner left behind.
    async with _session_lock(request.app, session.id):
        store: SessionStore = request.app.state.session_store
        current = store.get_session(session.id)
        now = datetime.now(UTC)
        if current is not None and _due(current, now, interval):
            await _redeem(request, current, now)


async def _redeem(request: Request, session: Session, now: datetime) -> None:
    store: SessionStore = request.app.state.session_store
    if not session.refresh_token:
        # Nothing to check with: the OP handed out no refresh token. Keeping
        # the session is the lesser evil; the log line says the window is not
        # closed for this one.
        _logger.warning("Session without refresh token: revalidation with the IdP skipped")
        store.defer_check(session.id, until=now + RECHECK_BACKOFF)
        return

    oidc: OidcClient = request.app.state.oidc_client
    try:
        tokens = await oidc.refresh_tokens(session.refresh_token)
    except RefreshRejectedError:
        await _drop(request, session, vocabulary.IDP_SESSION_ENDED)
        return
    except ClientRejectedError as error:
        # Our own connection is broken, not this session: keep everyone logged in
        # and complain where an operator looks.
        issuer = request.app.state.settings.oidc_issuer
        if idp_fault(request.app).record(
            code=error.code, issuer=issuer, now=now, window=RECHECK_BACKOFF
        ):
            _logger.error(
                "IdP revalidation is failing: the token endpoint refused our client with '%s' "
                "at issuer %s. Sessions are kept; fix the client configuration",
                error.code,
                issuer,
            )
        store.defer_check(session.id, until=now + RECHECK_BACKOFF)
        return
    except OidcError as error:
        _logger.warning("Re-validation at the IdP failed, session is kept: %s", error)
        store.defer_check(session.id, until=now + RECHECK_BACKOFF)
        return

    id_token = tokens.get("id_token")
    if id_token:
        try:
            claims = await oidc.validate_refreshed_id_token(id_token)
        except OidcError as error:
            _logger.warning("ID token from the revalidation is invalid: %s", error)
            await _drop(request, session, vocabulary.IDP_TOKEN_INVALID)
            return
        if claims.get("sub") != session.sub:
            await _drop(request, session, vocabulary.IDP_SUB_MISMATCH)
            return

    idp_fault(request.app).clear()
    store.mark_checked(session.id, refresh_token=tokens.get("refresh_token"), at=now)


async def _drop(request: Request, session: Session, reason: str) -> None:
    store: SessionStore = request.app.state.session_store
    store.delete_session(session.id)
    await audit_session_ended(request, session, reason)


async def audit_session_ended(request: Request, session: Session, reason: str) -> None:
    """One row per session the IdP ended for us, shared with the back-channel
    logout endpoint. Never a token, never a sid: a pseudonymised sub says who
    it was."""
    audit = getattr(request.app.state, "audit_log", None)
    if audit is None:
        return
    await audit.write(
        vocabulary.IDP_SESSION_ENDED_ACTION,
        Actor(ActorKind.MEMBER, session.sub),
        vocabulary.ALLOWED,
        reason_code=reason,
        refs={"kind": session.kind.value},
        ip=net.client_ip_from_request(request),
    )


__all__ = [
    "RECHECK_BACKOFF",
    "IdpRevalidationFault",
    "audit_session_ended",
    "idp_fault",
    "revalidate_sessions",
    "revalidation_status",
]
