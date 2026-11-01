"""Tests for api/operator.py: who runs this installation, for anyone to read.

The app under test includes the router behind the origin guard, the way
main.py does, without a database or a session store: the endpoint needs
neither. test_api_integration.py runs it through the full stack.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import Depends, FastAPI

from toon.api import operator
from toon.api.errors import register_error_handlers
from toon.api.origin_guard import REASON_OTHER_ORIGIN, require_admin_origin
from toon.config import Settings

URL = "/-/api/v1/operator"
APP_BASE_URL = "https://beheer.plak.example"
CONTENT_ORIGIN = "https://plak.example"


def _settings(tmp_path: Path, **operator_settings: str) -> Settings:
    return Settings(
        db_url="postgresql+asyncpg://plak:plak@localhost:5432/plak",
        content_root=tmp_path,
        oidc_issuer="https://idp.example",
        oidc_client_id="plak-client",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr:hoog",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper="audit-pepper-van-minstens-32-bytes!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        base_url=APP_BASE_URL,
        content_base_url=CONTENT_ORIGIN,
        environment="dev",
        **operator_settings,
    )


def _app(settings: Settings) -> FastAPI:
    app = FastAPI()
    app.state.settings = settings
    register_error_handlers(app)
    app.include_router(operator.router, dependencies=[Depends(require_admin_origin)])
    return app


async def _get(settings: Settings, headers: dict[str, str] | None = None) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(settings)), base_url=APP_BASE_URL
    ) as client:
        return await client.get(URL, headers=headers)


async def test_both_settings_come_back_as_configured(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, operator_name="Digi Gilde", operator_email="digigilde@rijksoverheid.nl"
    )

    resp = await _get(settings)

    assert resp.status_code == 200
    assert resp.json() == {"name": "Digi Gilde", "email": "digigilde@rijksoverheid.nl"}


async def test_without_settings_both_fields_are_null_not_empty(tmp_path: Path) -> None:
    resp = await _get(_settings(tmp_path))

    assert resp.status_code == 200
    assert resp.json() == {"name": None, "email": None}


@pytest.mark.parametrize(
    ("operator_settings", "expected"),
    [
        ({"operator_name": "Digi Gilde"}, {"name": "Digi Gilde", "email": None}),
        (
            {"operator_email": "digigilde@rijksoverheid.nl"},
            {"name": None, "email": "digigilde@rijksoverheid.nl"},
        ),
    ],
)
async def test_one_setting_alone_leaves_the_other_null(
    tmp_path: Path, operator_settings: dict[str, str], expected: dict[str, str | None]
) -> None:
    resp = await _get(_settings(tmp_path, **operator_settings))

    assert resp.json() == expected


async def test_answers_the_stripped_values(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, operator_name="  Digi Gilde \n", operator_email=" digigilde@rijksoverheid.nl\t"
    )

    resp = await _get(settings)

    assert resp.json() == {"name": "Digi Gilde", "email": "digigilde@rijksoverheid.nl"}


async def test_whitespace_only_settings_count_as_not_configured(tmp_path: Path) -> None:
    resp = await _get(_settings(tmp_path, operator_name="   ", operator_email="   "))

    assert resp.json() == {"name": None, "email": None}


async def test_needs_no_session_and_sets_no_cookie(tmp_path: Path) -> None:
    resp = await _get(_settings(tmp_path, operator_name="Digi Gilde"), headers={"Origin": APP_BASE_URL})

    assert resp.status_code == 200
    assert "set-cookie" not in resp.headers


async def test_a_content_origin_is_refused(tmp_path: Path) -> None:
    resp = await _get(_settings(tmp_path, operator_name="Digi Gilde"), headers={"Origin": CONTENT_ORIGIN})

    assert resp.status_code == 403
    assert resp.json()["code"] == REASON_OTHER_ORIGIN
