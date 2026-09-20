"""Audit log: the write API for the access gate, serving and API layer.

Fail-open on the logging itself: a failed audit write must never
block the original action (allow/refuse/deploy/...). The caller therefore
always calls `write(...)` as a separate step after the actual decision,
never inside the same transaction as that decision.

`write_strict` is the deliberate exception: a handful of endpoints read out
who is behind a pseudonym, and there the audit row is the only record that
the de-anonymisation happened at all, so those callers must fail closed
instead.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plak.audit.ip_crypto import encrypt_ip
from plak.audit.pseudonymisation import pseudonymise, truncate_ip
from plak.models.audit import ActorKind, AuditLogEntry

_logger = logging.getLogger(__name__)


class LookupLimitReachedError(Exception):
    """Raised by `AuditLog.write_strict_limited` when the actor is at or
    over its daily cap; the transaction is rolled back, so no row for this
    attempt landed."""


# Advisory lock keyed to one actor: serialises concurrent write_strict_limited
# calls from the same admin so two requests cannot both count "under the cap"
# before either commits (TOCTOU). Transaction-scoped (_xact_lock), so it is
# released automatically on commit or rollback - no separate unlock needed.
_ADVISORY_LOCK = text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))")

_COUNT_RECENT_LOOKUPS = text(
    """
    SELECT count(*) FROM audit_log_entries
    WHERE actor_pseudonym = :pseudonym
      AND action IN :actions
      AND occurred_at >= now() - interval '24 hours'
    """
).bindparams(bindparam("actions", expanding=True))


@dataclass(frozen=True)
class Actor:
    """Actor of an audit record.

    `identifier` is the raw value (sso sub, email address or token prefix) and
    is always pseudonymised before storage; `None` for system or anonymous
    actions without an identifiable actor.
    """

    kind: ActorKind
    identifier: str | None = None


ANONYMOUS = Actor(kind=ActorKind.ANONYMOUS)
SYSTEM = Actor(kind=ActorKind.SYSTEM)


class AuditLog:
    """Helper the app instantiates once (e.g. in app.state) and that the serving
    and API layers call to write audit records."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], pepper: str, ip_key: bytes) -> None:
        self._session_factory = session_factory
        self._pepper = pepper
        self._ip_key = ip_key

    async def write(
        self,
        action: str,
        actor: Actor,
        result: str,
        reason_code: str | None = None,
        refs: dict | None = None,
        ip: str | None = None,
    ) -> None:
        """Writes an audit record; never raises an exception to the caller."""
        try:
            await self.write_strict(action, actor, result, reason_code=reason_code, refs=refs, ip=ip)
        except Exception:
            _logger.exception("Auditlog-schrijffout genegeerd (fail-open): actie=%s", action)

    async def write_strict(
        self,
        action: str,
        actor: Actor,
        result: str,
        reason_code: str | None = None,
        refs: dict | None = None,
        ip: str | None = None,
    ) -> None:
        """Same record as `write`, but propagates a failed write instead of
        swallowing it. For the handful of callers that must not answer at all
        when the audit row does not land."""
        actor_pseudonym = pseudonymise(self._pepper, actor.identifier) if actor.identifier else None
        ip_truncated = truncate_ip(ip) if ip else None
        # Generated up front (not left to IDMixin's default) because the
        # encryption below binds its ciphertext to this exact id.
        entry_id = uuid.uuid4()
        ip_encrypted = encrypt_ip(self._ip_key, entry_id, ip) if ip else None
        async with self._session_factory() as session:
            session.add(
                AuditLogEntry(
                    id=entry_id,
                    actor_kind=actor.kind,
                    actor_pseudonym=actor_pseudonym,
                    action=action,
                    result=result,
                    reason_code=reason_code,
                    refs=refs,
                    ip_truncated=ip_truncated,
                    ip_encrypted=ip_encrypted,
                )
            )
            await session.commit()

    async def write_strict_limited(
        self,
        action: str,
        actor: Actor,
        result: str,
        *,
        refs: dict | None,
        ip: str | None,
        limit: int,
        counted_actions: Sequence[str],
    ) -> None:
        """Same record as `write_strict`, but atomic with a daily cap on this
        actor: a Postgres advisory transaction lock keyed to the
        actor's pseudonym serialises concurrent calls for the same admin, the
        count and the insert run in the one transaction using database time,
        and a call that would push the actor over the cap raises
        `LookupLimitReachedError` instead of landing a row at all - so the count
        and the write can never race (TOCTOU): with a plain SELECT-then-INSERT
        split across two requests, both could read "under the cap" before
        either commits.

        `actor.identifier` is required (the cap is per actor).
        """
        if actor.identifier is None:
            raise ValueError("write_strict_limited vereist een actor met identifier")
        actor_pseudonym = pseudonymise(self._pepper, actor.identifier)
        ip_truncated = truncate_ip(ip) if ip else None
        entry_id = uuid.uuid4()
        ip_encrypted = encrypt_ip(self._ip_key, entry_id, ip) if ip else None
        async with self._session_factory() as session, session.begin():
            await session.execute(_ADVISORY_LOCK, {"lock_key": f"plak-lookup:{actor_pseudonym}"})
            count = (
                await session.execute(
                    _COUNT_RECENT_LOOKUPS,
                    {"pseudonym": actor_pseudonym, "actions": list(counted_actions)},
                )
            ).scalar()
            if count is not None and count >= limit:
                raise LookupLimitReachedError()
            session.add(
                AuditLogEntry(
                    id=entry_id,
                    actor_kind=actor.kind,
                    actor_pseudonym=actor_pseudonym,
                    action=action,
                    result=result,
                    refs=refs,
                    ip_truncated=ip_truncated,
                    ip_encrypted=ip_encrypted,
                )
            )
