"""The encrypted full IP address next to `ip_truncated` (spec §12), and the
reveal endpoint that decrypts it: platform admins only, its own audit action,
and it counts toward the daily lookup limit like the actor lookups.
"""

# Fixtures imported from test_admin_api are found by name, which ruff reads as
# a parameter shadowing the import.
# ruff: noqa: F811

from __future__ import annotations

import uuid

from sqlalchemy import select
from test_admin_api import (  # noqa: F401 - fixtures are found by name
    BASE,
    _count,
    app,
    client,
    content_root,
    data,
    factory,
    login,
)

from plak.audit import vocabulary
from plak.audit.log import ANONYMOUS, Actor
from plak.models.audit import ActorKind, AuditLogEntry

AUDIT = f"{BASE}/platform/audit"
REASON = "onderzoek naar een melding over misbruik"


def _reveal_url(entry_id) -> str:
    return f"{BASE}/platform/audit/entries/{entry_id}/ip"


def _as_admin(client, app) -> dict[str, str]:
    return login(client, app, sub="admin-sub", email="admin@example.nl")


async def _write_with_ip(app, *, action: str = "content_access", ip: str = "203.0.113.42"):
    await app.state.audit_log.write(action, ANONYMOUS, "allowed", ip=ip)


async def test_the_read_never_exposes_the_encrypted_ip(client, app, data):
    await _write_with_ip(app)
    _as_admin(client, app)

    response = await client.get(AUDIT)
    assert "ipEncrypted" not in response.text


async def test_a_platform_admin_can_reveal_the_full_ip(client, app, factory, data):
    await _write_with_ip(app, ip="203.0.113.42")
    headers = _as_admin(client, app)

    async with factory() as db:
        entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "content_access"))

    response = await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)
    assert response.status_code == 200
    assert response.json()["ip"] == "203.0.113.42"


async def test_the_reveal_is_audited_with_the_entry_id_and_reason_never_the_ip(client, app, factory, data):
    await _write_with_ip(app, ip="203.0.113.42")
    headers = _as_admin(client, app)

    async with factory() as db:
        entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "content_access"))

    await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)

    async with factory() as db:
        row = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.AUDIT_IP_REVEAL))
    assert row is not None
    assert row.refs == {"entry": str(entry.id), "reason": REASON, "revealed": True}
    assert "203.0.113.42" not in str(row.refs)


async def test_reveal_demands_platform_admin_and_csrf(client, app, factory, data):
    await _write_with_ip(app, ip="203.0.113.42")
    async with factory() as db:
        entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "content_access"))

    headers = login(client, app, sub="lid-a", email="a@example.nl")
    refused = await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)
    assert refused.status_code == 403
    assert refused.json()["code"] == "NOT_ADMIN"

    _as_admin(client, app)
    without_csrf = await client.post(_reveal_url(entry.id), json={"reason": REASON})
    assert without_csrf.status_code == 403
    assert without_csrf.json()["code"] == "CSRF_INVALID"


async def test_reveal_requires_a_reason(client, app, factory, data):
    await _write_with_ip(app, ip="203.0.113.42")
    async with factory() as db:
        entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "content_access"))
    headers = _as_admin(client, app)

    response = await client.post(_reveal_url(entry.id), json={}, headers=headers)
    assert response.status_code == 422


async def test_an_unknown_entry_is_404(client, app, data):
    headers = _as_admin(client, app)
    response = await client.post(_reveal_url(uuid.uuid4()), json={"reason": REASON}, headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == "AUDIT_IP_UNKNOWN"


async def test_an_entry_without_ip_is_404(client, app, factory, data):
    await app.state.audit_log.write("test_zonder_ip", Actor(ActorKind.MEMBER, "lid-a"), "allowed")
    async with factory() as db:
        entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "test_zonder_ip"))
    headers = _as_admin(client, app)

    response = await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == "AUDIT_IP_UNKNOWN"


async def test_a_404_reveal_is_still_audited_with_revealed_false(client, app, factory, data):
    await app.state.audit_log.write("test_zonder_ip", Actor(ActorKind.MEMBER, "lid-a"), "allowed")
    async with factory() as db:
        entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "test_zonder_ip"))
    headers = _as_admin(client, app)

    await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)

    async with factory() as db:
        row = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == vocabulary.AUDIT_IP_REVEAL))
    assert row is not None
    assert row.refs == {"entry": str(entry.id), "reason": REASON, "revealed": False}


async def test_a_404_reveal_still_counts_toward_the_daily_lookup_limit(client, app, factory, data):
    await app.state.audit_log.write("test_zonder_ip", Actor(ActorKind.MEMBER, "lid-a"), "allowed")
    async with factory() as db:
        entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "test_zonder_ip"))

    app.state.settings.audit_lookup_daily_limit = 1
    headers = _as_admin(client, app)

    first = await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)
    assert first.status_code == 404
    over_limit = await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)
    assert over_limit.status_code == 429
    assert over_limit.json()["code"] == "LOOKUP_LIMIT_REACHED"


async def test_a_failed_audit_write_gives_503_and_no_ip(client, app, factory, data, monkeypatch):
    await _write_with_ip(app, ip="203.0.113.42")
    async with factory() as db:
        entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "content_access"))
    headers = _as_admin(client, app)

    async def _boom(*args, **kwargs):
        raise RuntimeError("audit log write error")

    monkeypatch.setattr(app.state.audit_log, "write_strict_limited", _boom)
    response = await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)
    assert response.status_code == 503
    assert response.json()["code"] == "AUDIT_UNAVAILABLE"
    assert "203.0.113.42" not in response.text
    assert await _count(factory, AuditLogEntry, action=vocabulary.AUDIT_IP_REVEAL) == 0


async def test_the_previous_key_decrypts_a_row_from_before_rotation(client, app, factory, data):
    """The key-rotation shape: PLAK_AUDIT_IP_KEY_PREVIOUS holds the old key,
    so a row written under it stays revealable after the rotation."""
    old_key = app.state.settings.audit_ip_key
    await _write_with_ip(app, ip="203.0.113.42")
    async with factory() as db:
        entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "content_access"))

    app.state.settings.audit_ip_key = "bm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm5ubm4="
    app.state.settings.audit_ip_key_previous = old_key
    app.state.audit_log._ip_key = app.state.settings.audit_ip_key_bytes  # simulate a rotated deploy

    headers = _as_admin(client, app)
    response = await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)
    assert response.status_code == 200
    assert response.json()["ip"] == "203.0.113.42"


async def test_reveal_counts_toward_the_daily_lookup_limit(client, app, factory, data):
    await _write_with_ip(app, ip="203.0.113.42")
    async with factory() as db:
        entry = await db.scalar(select(AuditLogEntry).where(AuditLogEntry.action == "content_access"))

    app.state.settings.audit_lookup_daily_limit = 1
    headers = _as_admin(client, app)

    assert (await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)).status_code == 200
    over_limit = await client.post(_reveal_url(entry.id), json={"reason": REASON}, headers=headers)
    assert over_limit.status_code == 429
    assert over_limit.json()["code"] == "LOOKUP_LIMIT_REACHED"

    assert await _count(factory, AuditLogEntry, action=vocabulary.AUDIT_IP_REVEAL) == 1
