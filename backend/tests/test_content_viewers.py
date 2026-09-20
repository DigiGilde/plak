"""Tests for auth/content_viewers.py: the upsert that keeps a content-only
SSO viewer traceable for the audit log (spec §7 "Kijken"), without giving
them a `members` row."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from plak.auth.content_viewers import upsert_content_viewer
from plak.db import make_session_factory
from plak.models.audit import ContentViewer


@pytest_asyncio.fixture
async def session_factory(migrated_dsn: str) -> AsyncIterator:
    engine = create_async_engine(migrated_dsn)
    try:
        yield make_session_factory(engine)
    finally:
        await engine.dispose()


async def test_a_first_login_creates_the_row(session_factory):
    await upsert_content_viewer(session_factory, "sub-nieuw", "Viewer@Example.NL", True)

    async with session_factory() as db:
        viewer = await db.scalar(select(ContentViewer).where(ContentViewer.sso_subject == "sub-nieuw"))
    assert viewer is not None
    assert viewer.email == "viewer@example.nl"
    assert viewer.email_verified is True


async def test_a_second_login_updates_last_seen_at_and_email_not_a_second_row(session_factory):
    await upsert_content_viewer(session_factory, "sub-terugkerend", "oud@example.nl", True)
    async with session_factory() as db:
        first = await db.scalar(select(ContentViewer).where(ContentViewer.sso_subject == "sub-terugkerend"))

    await upsert_content_viewer(session_factory, "sub-terugkerend", "nieuw@example.nl", True)

    async with session_factory() as db:
        rows = list(
            await db.scalars(select(ContentViewer).where(ContentViewer.sso_subject == "sub-terugkerend"))
        )
    assert len(rows) == 1
    assert rows[0].id == first.id
    assert rows[0].email == "nieuw@example.nl"
    assert rows[0].last_seen_at >= first.last_seen_at


async def test_a_login_without_an_email_claim_still_upserts_the_sub(session_factory):
    await upsert_content_viewer(session_factory, "sub-zonder-email", None, False)

    async with session_factory() as db:
        viewer = await db.scalar(select(ContentViewer).where(ContentViewer.sso_subject == "sub-zonder-email"))
    assert viewer is not None
    assert viewer.email is None
    assert viewer.email_verified is False


async def test_an_unverified_email_is_stored_as_unverified(session_factory):
    await upsert_content_viewer(session_factory, "sub-ongeverifieerd", "viewer@example.nl", False)

    async with session_factory() as db:
        viewer = await db.scalar(
            select(ContentViewer).where(ContentViewer.sso_subject == "sub-ongeverifieerd")
        )
    assert viewer is not None
    assert viewer.email_verified is False
