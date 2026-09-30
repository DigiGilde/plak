"""The container's entrypoint, pinned down here rather than in a rollout.

ZAD has no Job object and no `command` field on a component, so the image
itself carries "migrate before serving" (docs/deploying-on-zad.md §7). That
makes four properties of this one shell script load-bearing, and none of
them fails loudly: a missing `exec` only shows up as a rollout that hangs
for its full grace period, and a missing `set -e` only as an app serving
against a half-migrated schema.
"""

from __future__ import annotations

from pathlib import Path

import pytest

CONTAINER = Path(__file__).resolve().parents[2] / "containers" / "plak"


@pytest.fixture(scope="module")
def entrypoint() -> str:
    return (CONTAINER / "entrypoint.sh").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def containerfile() -> str:
    return (CONTAINER / "Containerfile").read_text(encoding="utf-8")


def test_the_image_starts_the_entrypoint(containerfile: str) -> None:
    assert 'CMD ["./entrypoint.sh"]' in containerfile


def test_the_entrypoint_is_copied_into_the_image(containerfile: str) -> None:
    assert "containers/plak/entrypoint.sh ./entrypoint.sh" in containerfile


def test_a_failing_step_stops_the_script(entrypoint: str) -> None:
    # Without `set -e` a failed migration is followed by a server that
    # answers requests against whatever schema it found.
    assert "set -eu" in entrypoint


def test_the_migration_runs_before_the_server(entrypoint: str) -> None:
    migration = entrypoint.index("alembic upgrade head")
    server = entrypoint.index("exec uvicorn")
    assert migration < server


def test_the_server_replaces_the_shell(entrypoint: str) -> None:
    # Without exec, uvicorn is a child of this shell and the SIGTERM that
    # ends a rollout reaches the shell, which ignores it; the pod then waits
    # out its termination grace period on every deploy.
    assert "exec uvicorn" in entrypoint


def test_the_access_log_stays_off(entrypoint: str) -> None:
    # uvicorn's access log writes path plus querystring, so every secret
    # link (?key=...) would land in the log stack (docs/security.md).
    assert "--no-access-log" in entrypoint


def test_waiting_for_the_database_is_bounded(entrypoint: str) -> None:
    # An unbounded wait turns an unreachable database into a pod that looks
    # alive and never serves.
    assert "-ge 30" in entrypoint
    assert "exit 1" in entrypoint


def test_uvicorn_does_not_derive_the_client_itself(entrypoint: str) -> None:
    # With proxy headers on, uvicorn rewrites request.client from
    # X-Forwarded-For as soon as the peer is in --forwarded-allow-ips, whose
    # default reads the FORWARDED_ALLOW_IPS environment variable. net.py would
    # then walk the header starting from an address that already came out of
    # it, and `vouched` would stop meaning anything.
    assert "--no-proxy-headers" in entrypoint
