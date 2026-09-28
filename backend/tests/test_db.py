"""Tests for plak.db: the FastAPI session dependency built on the session
factory. make_engine/make_session_factory are exercised indirectly by every
other DB test in this suite; session_dependency has no caller anywhere in
the app (no route depends on it), so it needs a test of its own."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from plak.db import make_session_factory, session_dependency


async def test_session_dependency_yields_a_usable_session(migrated_dsn: str) -> None:
    engine = create_async_engine(migrated_dsn)
    session_factory = make_session_factory(engine)
    try:
        generator = session_dependency(session_factory)
        session = await anext(generator)
        result = await session.execute(text("SELECT 1"))
        assert result.scalar() == 1

        # Draining the generator runs the `async with` block to its end,
        # which closes the session; nothing left to yield after that.
        rest = [item async for item in generator]
        assert rest == []
    finally:
        await engine.dispose()
