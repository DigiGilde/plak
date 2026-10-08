"""Async database engine and session factory built from PLAK_DB_URL."""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from plak.config import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    # hide_parameters: a failed statement's log line never shows bound values
    # (sub, email, reason, ...), only the SQL shape.
    return create_async_engine(settings.db_url, pool_pre_ping=True, hide_parameters=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def violated_constraint(error: DBAPIError) -> str | None:
    """The constraint a failed statement ran into, or None when it names none.

    asyncpg carries the name on its own exception, which SQLAlchemy's adapter
    keeps as the cause of `error.orig`; the message text is not a contract."""
    cause: BaseException | None = error.orig
    while cause is not None:
        name = getattr(cause, "constraint_name", None)
        if name:
            return name
        cause = cause.__cause__
    return None
