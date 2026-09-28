"""Pytest fixtures for DB tests: PostgreSQL 16 via testcontainers (Podman
socket) and a test database migrated once, with per-test isolation through a
transaction that is rolled back at the end.

Only tests that explicitly ask for the fixtures below (e.g. test_migrations.py)
start a container; test_config.py and test_constants.py stay untouched.
"""

from __future__ import annotations

import os
import platform
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from alembic.config import Config

from alembic import command

ALEMBIC_DIR = Path(__file__).resolve().parents[1] / "alembic"
DB_URL_VAR = "PLAK_DB_URL"


def _podman_socket() -> str | None:
    """Finds the Podman socket; respects a DOCKER_HOST that is already set (justfile)."""
    if os.environ.get("DOCKER_HOST"):
        return os.environ["DOCKER_HOST"]

    if platform.system() == "Darwin":
        # macOS has no native Podman daemon: the socket lives in the podman machine.
        path = ""
        try:
            # podman deliberately via PATH, like the justfile does
            result = subprocess.run(
                ["podman", "machine", "inspect", "--format", "{{.ConnectionInfo.PodmanSocket.Path}}"],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            path = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            path = ""
        if not path:
            path = os.path.expanduser("~/.local/share/containers/podman/machine/podman.sock")
        return f"unix://{path}" if os.path.exists(path) else None

    path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", ""), "podman", "podman.sock")
    return f"unix://{path}" if os.path.exists(path) else None


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[object]:
    socket = _podman_socket()
    if socket is None:
        pytest.skip(
            "Geen Podman-socket gevonden voor testcontainers. Start 'podman machine "
            "start' (macOS) of zet DOCKER_HOST naar de Podman-socket."
        )

    os.environ.setdefault("DOCKER_HOST", socket)
    # The ryuk cleanup container is not always available or needed under Podman.
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16", driver=None)
    try:
        container.start()
    except Exception as error:  # pragma: no cover - depends on the environment
        pytest.skip(f"Geen containerruntime bereikbaar via {socket}: {error}")
        return

    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session")
def migrated_dsn(postgres_container) -> str:
    """DSN (asyncpg scheme) of the testcontainer database, after a one-off
    'alembic upgrade head' onto revision 0001_base."""
    base_url = postgres_container.get_connection_url(driver=None)
    asyncpg_dsn = base_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    os.environ[DB_URL_VAR] = asyncpg_dsn
    try:
        cfg = Config()
        cfg.set_main_option("script_location", str(ALEMBIC_DIR))
        cfg.set_main_option("version_locations", str(ALEMBIC_DIR / "versions"))
        cfg.set_main_option("path_separator", "os")
        command.upgrade(cfg, "head")
    finally:
        os.environ.pop(DB_URL_VAR, None)

    return asyncpg_dsn


@pytest_asyncio.fixture
async def db_connection(migrated_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    """Asyncpg connection inside a transaction that is rolled back after the
    test, so tests cannot touch each other."""
    dsn = migrated_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(dsn)
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


# All data tables, child to parent (CASCADE covers the FK order anyway).
_DATA_TABLES = (
    "audit_log_entries, audit_log_chain_heads, content_viewers, previews, versions, access_keys, invitees, "
    "site_repositories, cli_refresh_tokens, cli_sessions, cli_device_authorizations, site_members, sites, "
    "group_members, groups, members"
)

# Fixtures that use the migrated test database; when a test asks for one of
# them we clean up beforehand. That way container-free tests (test_config,
# test_constants) still leave the container alone.
_DB_FIXTURES = {"migrated_dsn", "db_connection", "postgres_container"}


@pytest_asyncio.fixture(autouse=True)
async def _clean_db(request: pytest.FixtureRequest) -> AsyncIterator[None]:
    """Empties every data table before each DB test.

    Needed because some tests really commit (audit rows, member rows) through
    their own SQLAlchemy session factories in the session-scoped container;
    those commits would otherwise leak into other test files. TRUNCATE fires
    no ON DELETE triggers, so the append-only auditlog triggers do not block
    it.
    """
    if not (_DB_FIXTURES & set(request.fixturenames)):
        yield
        return
    dsn = request.getfixturevalue("migrated_dsn").replace(
        "postgresql+asyncpg://", "postgresql://", 1
    )
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(f"TRUNCATE {_DATA_TABLES} RESTART IDENTITY CASCADE")
    finally:
        await connection.close()
    yield
