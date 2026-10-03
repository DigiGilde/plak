"""GET /-/healthz: the public liveness answer on the admin host.

Anonymous, so it names only the checks that complain, never a message, a
number or an error code; those are in the log and on the platform
administration page. Semantics follow draft-inadarei-api-health-check: ok and
degraded are 2xx, fail is 5xx.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from plak.auth.revalidation import revalidation_status
from plak.ingest.storage_health import storage_complaint, storage_watch

if TYPE_CHECKING:
    from fastapi import FastAPI

_logger = logging.getLogger(__name__)

DATABASE_TIMEOUT_SECONDS = 2.0


async def _database_reachable(app: FastAPI) -> bool:
    async def ping() -> None:
        async with app.state.session_factory() as session:
            await session.execute(text("SELECT 1"))

    try:
        await asyncio.wait_for(ping(), timeout=DATABASE_TIMEOUT_SECONDS)
    except (TimeoutError, SQLAlchemyError, OSError):
        _logger.warning("Health check: the database did not answer", exc_info=True)
        return False
    return True


async def health_response(app: FastAPI) -> JSONResponse:
    checks: list[str] = []
    if not await _database_reachable(app):
        checks.append("database")
    try:
        storage_trouble = storage_complaint(app.state.content_store, app.state.settings) is not None
        storage_unmeasurable = False
    except OSError:
        _logger.warning("Health check: the free space of the content volume cannot be measured", exc_info=True)
        storage_trouble = storage_unmeasurable = True
    if storage_trouble:
        checks.append("storage")
    if storage_watch(app).content_root_message:
        checks.append("content_root")
    if revalidation_status(app):
        checks.append("idp_revalidation")

    headers = {"Cache-Control": "no-store"}
    if "database" in checks or storage_unmeasurable:
        return JSONResponse({"status": "fail", "checks": checks}, status_code=503, headers=headers)
    if checks:
        return JSONResponse({"status": "degraded", "checks": checks}, headers=headers)
    return JSONResponse({"status": "ok"}, headers=headers)
