"""What /-/healthz and the log say about the content volume.

Two complaints with different lifetimes. Low free space comes and goes, so it
is measured on every call and its ERROR line is rate-limited. A content root
that is not a mount point is decided once at startup: it only changes with a
restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from plak.config import Settings
    from plak.ingest.store import ContentStore

_logger = logging.getLogger(__name__)

MIB = 1024 * 1024
LOG_WINDOW = timedelta(hours=1)
CHECK_INTERVAL_SECONDS = 300.0


def storage_complaint(store: ContentStore, settings: Settings) -> str | None:
    """Set when a deploy of the maximum size would be refused for lack of room;
    None while there is room, and always None with the reserve switched off."""
    reserve = settings.storage_min_free_bytes
    if not reserve:
        return None
    free = store.free_bytes()
    if free >= reserve + settings.ingest_max_total:
        return None
    return (
        f"Content volume has {free // MIB} MiB free; a deploy of the maximum size "
        f"({settings.ingest_max_total // MIB} MiB) would take it below the {reserve // MIB} MiB reserve."
    )


def content_root_complaint(root: Path) -> str | None:
    """Set when `root` sits on the same device as its parent: nothing is mounted
    there, so what is published lands in the container's writable layer."""
    if os.stat(root).st_dev != os.stat(root.parent).st_dev:
        return None
    return f"{root} is not a mount point; what is published there is lost when the container restarts."


@dataclass
class StorageWatch:
    """Standing state of the content volume checks, kept on `app.state`."""

    content_root_message: str | None = None
    log_again_at: datetime | None = None

    def check(self, store: ContentStore, settings: Settings, now: datetime) -> str | None:
        """Returns the storage complaint and logs it, at most once per LOG_WINDOW."""
        message = storage_complaint(store, settings)
        if message is None:
            self.log_again_at = None
            return None
        if self.log_again_at is None or now >= self.log_again_at:
            self.log_again_at = now + LOG_WINDOW
            _logger.error("%s Deploys will be refused soon; free up space or enlarge the volume.", message)
        return message


def storage_watch(app: FastAPI) -> StorageWatch:
    watch = getattr(app.state, "storage_watch", None)
    if watch is None:
        watch = StorageWatch()
        app.state.storage_watch = watch
    return watch


async def _run_checks(app: FastAPI, store: ContentStore, settings: Settings, interval: float) -> None:
    watch = storage_watch(app)
    while True:
        try:
            watch.check(store, settings, datetime.now(tz=UTC))
        except OSError:
            _logger.exception("Checking the free space of the content volume failed")
        await asyncio.sleep(interval)


@asynccontextmanager
async def storage_check_job(
    app: FastAPI, store: ContentStore, settings: Settings, *, interval: float = CHECK_INTERVAL_SECONDS
) -> AsyncIterator[asyncio.Task]:
    """Background task that measures the volume every `interval` seconds, so the
    ERROR line appears whether or not anyone calls /-/healthz."""
    task = asyncio.create_task(_run_checks(app, store, settings, interval))
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
