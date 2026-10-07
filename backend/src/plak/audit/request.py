"""Audit rows written on behalf of the request at hand: the app's AuditLog and
the client address come from the request, a member becomes the actor by sub."""

from __future__ import annotations

from typing import TYPE_CHECKING

from plak import net
from plak.audit.log import Actor, AuditLog
from plak.models.audit import ActorKind

if TYPE_CHECKING:
    from fastapi import Request

    from plak.models.identity import Member


def member_actor(member: Member) -> Actor:
    return Actor(ActorKind.MEMBER, member.sso_subject)


def audit_log(request: Request) -> AuditLog | None:
    return getattr(request.app.state, "audit_log", None)


async def write(
    request: Request,
    action: str,
    actor: Actor,
    result: str,
    *,
    reason_code: str | None = None,
    refs: dict | None = None,
) -> None:
    """Fail-open, like AuditLog.write; an app without an AuditLog writes
    nothing."""
    log = audit_log(request)
    if log is None:
        return
    await log.write(action, actor, result, reason_code=reason_code, refs=refs, ip=net.client_ip_from_request(request))


__all__ = ["audit_log", "member_actor", "write"]
