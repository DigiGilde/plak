"""Tracks SSO viewers of protected content.

A content-only viewer never gets a `members` row (that comes into being only
on a first admin visit, `auth/members.py`), so without this table there
would be nothing for `actor-identity` to resolve them to at all. Upserted on
every successful content-host SSO login (`last_seen_at` is therefore last
*login*, not last page view); purged by `audit/retention.py` after 90 days
without one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from plak.models.audit import ContentViewer


async def upsert_content_viewer(
    session_factory: async_sessionmaker, sub: str, email: str | None, email_verified: bool
) -> None:
    """Always upserts the sub, whether or not the IdP sent an email claim:
    the sub alone is what makes a viewer traceable at all."""
    now = datetime.now(UTC)
    normalised_email = email.lower() if email else None
    upsert = (
        pg_insert(ContentViewer)
        .values(
            id=uuid.uuid4(),
            sso_subject=sub,
            email=normalised_email,
            email_verified=email_verified,
            last_seen_at=now,
        )
        .on_conflict_do_update(
            index_elements=[ContentViewer.sso_subject],
            set_={"email": normalised_email, "email_verified": email_verified, "last_seen_at": now},
        )
    )
    async with session_factory() as db:
        await db.execute(upsert)
        await db.commit()
