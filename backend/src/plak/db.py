"""Async database engine and session factory built from PLAK_DB_URL."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from plak.config import Settings


def make_engine(settings: Settings) -> AsyncEngine:
    # hide_parameters: a failed statement's log line never shows bound values
    # (sub, email, reason, ...), only the SQL shape.
    return create_async_engine(settings.db_url, pool_pre_ping=True, hide_parameters=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_dependency(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
