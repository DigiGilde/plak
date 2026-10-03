"""Tests for ingest/storage_health.py and what /-/healthz and the log make of it:
the low-space complaint with both sides of its threshold, the once-per-hour
ERROR line, the periodic check, and the startup check on the content root,
which only runs in production.

/-/healthz is asked through the whole app, on the admin host."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from helpers_oidc import MockIdP, make_settings

from plak.auth.revalidation import RECHECK_BACKOFF, idp_fault
from plak.ingest.storage_health import (
    LOG_WINDOW,
    MIB,
    StorageWatch,
    content_root_complaint,
    storage_check_job,
    storage_complaint,
    storage_watch,
)
from plak.ingest.store import ContentStore
from plak.main import create_app

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
LOGGER = "plak.ingest.storage_health"


def _settings(**overrides):
    return make_settings(MockIdP(), **overrides)


def _store(free_mib: float) -> SimpleNamespace:
    return SimpleNamespace(free_bytes=lambda: int(free_mib * MIB))


def _app(tmp_path: Path, **overrides):
    return create_app(_settings(content_root=tmp_path / "content", **overrides))


async def _healthz(app, host: str = "plak.example") -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=f"https://{host}") as client:
        return await client.get("/-/healthz")


@pytest.fixture(autouse=True)
def _database_up(monkeypatch):
    """These tests are about the storage checks; the database check has its own
    tests in test_health.py."""

    async def reachable(app) -> bool:
        return True

    monkeypatch.setattr("plak.platform.health._database_reachable", reachable)


def _fake_free(monkeypatch, free_mib: float) -> None:
    monkeypatch.setattr(ContentStore, "free_bytes", lambda self: int(free_mib * MIB))


class TestStorageComplaint:
    def test_below_the_reserve_plus_the_largest_deploy_is_a_complaint(self) -> None:
        message = storage_complaint(_store(240), _settings())
        assert message == (
            "Content volume has 240 MiB free; a deploy of the maximum size (200 MiB) "
            "would take it below the 100 MiB reserve."
        )

    def test_exactly_enough_room_is_no_complaint(self) -> None:
        assert storage_complaint(_store(300), _settings()) is None

    def test_one_byte_short_is_a_complaint(self) -> None:
        store = SimpleNamespace(free_bytes=lambda: 300 * MIB - 1)
        assert storage_complaint(store, _settings()) is not None

    def test_plenty_of_room_is_no_complaint(self) -> None:
        assert storage_complaint(_store(900), _settings()) is None

    def test_follows_the_configured_limits(self) -> None:
        settings = _settings(storage_min_free_bytes=10 * MIB, ingest_max_total=20 * MIB)
        assert storage_complaint(_store(31), settings) is None
        assert "the 10 MiB reserve" in storage_complaint(_store(29), settings)

    def test_reserve_switched_off_never_complains(self) -> None:
        assert storage_complaint(_store(0), _settings(storage_min_free_bytes=0)) is None


class TestContentRootComplaint:
    def test_a_plain_directory_is_not_a_mount_point(self, tmp_path: Path) -> None:
        root = tmp_path / "content"
        root.mkdir()
        assert content_root_complaint(root) == (
            f"{root} is not a mount point; what is published there is lost when the container restarts."
        )

    def test_a_directory_on_its_own_device_is_a_mount_point(self, tmp_path: Path, monkeypatch) -> None:
        root = tmp_path / "content"
        root.mkdir()
        real_stat = os.stat

        def fake_stat(path, *args, **kwargs):
            result = real_stat(path, *args, **kwargs)
            if Path(path) == root:
                return SimpleNamespace(st_dev=result.st_dev + 1)
            return result

        monkeypatch.setattr(os, "stat", fake_stat)
        assert content_root_complaint(root) is None


class TestWatch:
    def test_logs_error_once_per_window(self, caplog) -> None:
        watch = StorageWatch()
        store, settings = _store(10), _settings()
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            first = watch.check(store, settings, NOW)
            again = watch.check(store, settings, NOW + LOG_WINDOW - timedelta(seconds=1))
        assert first == again is not None
        assert len(caplog.records) == 1
        assert "Content volume has 10 MiB free" in caplog.text

    def test_logs_again_when_the_window_is_over(self, caplog) -> None:
        watch = StorageWatch()
        store, settings = _store(10), _settings()
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            watch.check(store, settings, NOW)
            watch.check(store, settings, NOW + LOG_WINDOW)
        assert len(caplog.records) == 2

    def test_a_recovery_resets_the_window(self, caplog) -> None:
        watch = StorageWatch()
        settings = _settings()
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            watch.check(_store(10), settings, NOW)
            assert watch.check(_store(900), settings, NOW + timedelta(minutes=5)) is None
            watch.check(_store(10), settings, NOW + timedelta(minutes=10))
        assert len(caplog.records) == 2

    def test_no_complaint_logs_nothing(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            assert StorageWatch().check(_store(900), _settings(), NOW) is None
        assert not caplog.records

    def test_the_state_hangs_on_the_app_once(self) -> None:
        app = SimpleNamespace(state=SimpleNamespace())
        assert storage_watch(app) is storage_watch(app)


class TestPeriodicCheck:
    async def test_the_job_logs_without_anyone_calling_healthz(self, caplog) -> None:
        app = SimpleNamespace(state=SimpleNamespace())
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            async with storage_check_job(app, _store(10), _settings(), interval=0.01):
                await asyncio.sleep(0.1)
        assert len(caplog.records) == 1

    async def test_a_failing_measurement_does_not_end_the_loop(self, caplog) -> None:
        calls = 0

        def free_bytes() -> int:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("statvfs failed")
            return 10 * MIB

        app = SimpleNamespace(state=SimpleNamespace())
        store = SimpleNamespace(free_bytes=free_bytes)
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            async with storage_check_job(app, store, _settings(), interval=0.01):
                await asyncio.sleep(0.1)
        assert "Checking the free space of the content volume failed" in caplog.text
        assert "Content volume has 10 MiB free" in caplog.text

    async def test_the_lifespan_runs_and_stops_the_job(self, tmp_path: Path) -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            task = app.state.storage_task
            assert not task.done()
        assert task.done()


class TestHealthz:
    async def test_ok_with_room_to_spare(self, tmp_path: Path, monkeypatch) -> None:
        _fake_free(monkeypatch, 900)
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            response = await _healthz(app)
        assert (response.status_code, response.json()) == (200, {"status": "ok"})

    async def test_low_storage_is_degraded_and_names_only_the_check(self, tmp_path: Path, monkeypatch) -> None:
        _fake_free(monkeypatch, 240)
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            response = await _healthz(app)
        assert (response.status_code, response.json()) == (200, {"status": "degraded", "checks": ["storage"]})
        assert "MiB" not in response.text

    async def test_the_reserve_switched_off_reports_no_storage(self, tmp_path: Path, monkeypatch) -> None:
        _fake_free(monkeypatch, 0)
        app = _app(tmp_path, storage_min_free_bytes=0)
        async with app.router.lifespan_context(app):
            response = await _healthz(app)
        assert response.json() == {"status": "ok"}

    async def test_storage_combines_with_the_idp_complaint(self, tmp_path: Path, monkeypatch) -> None:
        _fake_free(monkeypatch, 240)
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            idp_fault(app).record(
                code="invalid_client", issuer="https://idp.example", now=NOW, window=RECHECK_BACKOFF
            )
            response = await _healthz(app)
        assert response.status_code == 200
        assert response.json() == {"status": "degraded", "checks": ["storage", "idp_revalidation"]}
        assert "invalid_client" not in response.text

    async def test_recovers_when_space_is_freed(self, tmp_path: Path, monkeypatch) -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app):
            _fake_free(monkeypatch, 240)
            assert (await _healthz(app)).json()["status"] == "degraded"
            _fake_free(monkeypatch, 900)
            assert (await _healthz(app)).json() == {"status": "ok"}


def _productie(tmp_path: Path):
    return _app(
        tmp_path,
        environment="productie",
        behind_proxy=False,
        oidc_iss_required=True,
        base_url="https://beheer.plak.example",
        oidc_issuer="https://idp.example",
    )


class TestContentRootAtStartup:
    async def test_production_without_a_mount_is_reported_once_and_logged(
        self, tmp_path: Path, monkeypatch, caplog
    ) -> None:
        _fake_free(monkeypatch, 900)
        app = _productie(tmp_path)
        root = (tmp_path / "content").resolve()
        with caplog.at_level(logging.ERROR, logger="plak.main"):
            async with app.router.lifespan_context(app):
                response = await _healthz(app, "beheer.plak.example")
        assert response.json() == {"status": "degraded", "checks": ["content_root"]}
        assert str(root) not in response.text
        assert f"{root} is not a mount point" in caplog.text
        assert "PLAK_CONTENT_ROOT" in caplog.text

    async def test_production_with_a_mount_is_fine(self, tmp_path: Path, monkeypatch, caplog) -> None:
        _fake_free(monkeypatch, 900)
        monkeypatch.setattr("plak.main.content_root_complaint", lambda root: None)
        app = _productie(tmp_path)
        with caplog.at_level(logging.ERROR, logger="plak.main"):
            async with app.router.lifespan_context(app):
                response = await _healthz(app, "beheer.plak.example")
        assert response.json() == {"status": "ok"}
        assert "is not a mount point" not in caplog.text

    async def test_outside_production_the_mount_is_not_checked(
        self, tmp_path: Path, monkeypatch, caplog
    ) -> None:
        _fake_free(monkeypatch, 900)
        app = _app(tmp_path)
        with caplog.at_level(logging.ERROR, logger="plak.main"):
            async with app.router.lifespan_context(app):
                response = await _healthz(app)
        assert response.json() == {"status": "ok"}
        assert "is not a mount point" not in caplog.text

    async def test_every_complaint_is_listed_in_a_fixed_order(self, tmp_path: Path, monkeypatch) -> None:
        _fake_free(monkeypatch, 240)
        app = _productie(tmp_path)
        async with app.router.lifespan_context(app):
            idp_fault(app).record(
                code="invalid_client", issuer="https://idp.example", now=NOW, window=RECHECK_BACKOFF
            )
            response = await _healthz(app, "beheer.plak.example")
        assert response.json() == {
            "status": "degraded",
            "checks": ["storage", "content_root", "idp_revalidation"],
        }
