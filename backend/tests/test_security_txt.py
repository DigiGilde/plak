"""security.txt under /.well-known/ (RFC 9116, BIO2 5.24.08).

This file rests on three properties, and each has its own test below:

1. it is valid according to the standard, and stays valid over time: Expires is computed
   per request, because a fixed date makes the file invalid on some day
   instead of merely old;
2. it is served on both hosts with the same document, so the fetch URL is
   always covered by a Canonical line;
3. it opens exactly one path; the rest of /.well-known/ stays the neutral 404.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from helpers_oidc import make_client_jwk

from plak.config import Settings
from plak.main import create_app
from plak.platform.security_txt import PATH_SECURITY_TXT, VALIDITY, expires_at, security_txt
from plak.serving.response import NEUTRAL_404_BODY

ADMIN = "https://beheer.plak.example"
CONTENT = "https://plak.example"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "db_url": "postgresql+asyncpg://unused:unused@localhost:5432/unused",
        "content_root": tmp_path / "content",
        "oidc_issuer": "https://idp.example",
        "oidc_client_id": "plak-client",
        "oidc_client_private_jwk": make_client_jwk(),
        "session_secret": "sessie-geheim-van-minstens-32-bytes!",
        "audit_pepper": "audit-pepper-van-minstens-32-bytes!!",
        "audit_ip_key": "a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        "environment": "dev",
        "base_url": ADMIN,
        "content_base_url": CONTENT,
        "spa_path": tmp_path / "dist",
    }
    base.update(overrides)
    return Settings(**base)


def _client(app: FastAPI, base: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base, follow_redirects=False
    )


@pytest_asyncio.fixture
async def two_hosts(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, httpx.AsyncClient]]:
    app = create_app(_settings(tmp_path))
    async with (
        app.router.lifespan_context(app),
        _client(app, ADMIN) as admin,
        _client(app, CONTENT) as content,
    ):
        yield admin, content


def _fields(body: str, name: str) -> list[str]:
    prefix = f"{name}: "
    return [line[len(prefix) :] for line in body.splitlines() if line.startswith(prefix)]


class TestServing:
    async def test_both_hosts_serve_the_same_document(self, two_hosts) -> None:
        admin, content = two_hosts
        on_admin = await admin.get(PATH_SECURITY_TXT)
        on_content = await content.get(PATH_SECURITY_TXT)

        assert on_admin.status_code == 200
        assert on_content.status_code == 200
        assert on_admin.text == on_content.text

    async def test_the_content_type_is_the_one_the_rfc_demands(self, two_hosts) -> None:
        # RFC 9116 section 3: text/plain with charset utf-8, not a guess.
        _, content = two_hosts
        response = await content.get(PATH_SECURITY_TXT)

        assert response.headers["content-type"] == "text/plain; charset=utf-8"

    async def test_the_rest_of_well_known_keeps_the_neutral_404(self, two_hosts) -> None:
        """Opening one fixed path gives nothing away, the way /robots.txt and
        the front page already do not. Everything around it has to stay the
        byte-identical refusal, or this becomes an enumeration channel."""
        _, content = two_hosts

        for path in (
            "/.well-known/",
            "/.well-known/onbekend",
            "/.well-known/security.txt/x",
            "/.well-known/change-password",
        ):
            response = await content.get(path)
            assert response.status_code == 404, path
            assert response.content == NEUTRAL_404_BODY, path


class TestContent:
    def test_the_two_required_fields_are_there(self, tmp_path: Path) -> None:
        body = security_txt(_settings(tmp_path), datetime.now(UTC))

        # RFC 9116 section 2.5.3 and 2.5.5: both MUST be present, and Expires
        # MUST NOT appear more than once.
        assert len(_fields(body, "Expires")) == 1
        assert _fields(body, "Contact")
        # Section 2.5.4: the order is the order of preference, and the private
        # advisory is the route a reporter should see first.
        assert _fields(body, "Contact")[0] == (
            "https://github.com/DigiGilde/plak/security/advisories/new"
        )
        assert "mailto:digigilde@rijksoverheid.nl" in _fields(body, "Contact")
        assert _fields(body, "Policy")[0] == (
            "https://github.com/DigiGilde/plak/blob/beta/SECURITY.md"
        )

    def test_no_encryption_key_is_offered(self, tmp_path: Path) -> None:
        """An Encryption field names the key researchers should use (RFC 9116
        section 2.5.4), tied to no Contact in particular, so NCSC's key would
        read as the key for the Plak team's address. The NCSC routes stay as
        contacts; they publish their own key."""
        body = security_txt(_settings(tmp_path), datetime.now(UTC))

        assert _fields(body, "Encryption") == []
        assert "mailto:security@ncsc.nl" in _fields(body, "Contact")
        assert _fields(body, "Preferred-Languages") == ["nl, en"]

    def test_the_canonical_lines_cover_both_retrieval_urls(self, tmp_path: Path) -> None:
        """A file whose retrieval URI appears in none of its Canonical fields
        should not be trusted (RFC 9116 section 2.5.2), and Plak answers on two
        hosts."""
        body = security_txt(_settings(tmp_path), datetime.now(UTC))

        assert _fields(body, "Canonical") == [
            f"{CONTENT}{PATH_SECURITY_TXT}",
            f"{ADMIN}{PATH_SECURITY_TXT}",
        ]

    def test_an_http_origin_yields_no_canonical_rather_than_an_invalid_one(
        self, tmp_path: Path
    ) -> None:
        """A Canonical web URI must be https, so the dev and e2e stacks drop
        the field. It is optional; Contact and Expires are not."""
        settings = _settings(
            tmp_path, base_url="http://beheer.plak.localhost:8080", content_base_url="http://plak.localhost:8080"
        )
        body = security_txt(settings, datetime.now(UTC))

        assert _fields(body, "Canonical") == []
        assert _fields(body, "Contact")
        assert len(_fields(body, "Expires")) == 1

    def test_expires_is_never_in_the_past_and_never_a_year_out(self, tmp_path: Path) -> None:
        """An expired security.txt is invalid, not merely old, so the date is
        computed instead of written down."""
        settings = _settings(tmp_path)

        for moment in (
            datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            datetime(2030, 6, 30, 23, 59, 59, tzinfo=UTC),
        ):
            body = security_txt(settings, moment)
            stamp = datetime.strptime(_fields(body, "Expires")[0], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
            assert moment < stamp
            assert stamp - moment < timedelta(days=365)

    def test_the_body_changes_once_a_day_not_once_a_request(self, tmp_path: Path) -> None:
        """Counted from the start of the UTC day, so two requests in the same
        second do not disagree and a cache has something stable to hold."""
        early = datetime(2026, 9, 19, 0, 0, 1, tzinfo=UTC)
        late = datetime(2026, 9, 19, 23, 59, 59, tzinfo=UTC)
        next_day = datetime(2026, 9, 20, 0, 0, 1, tzinfo=UTC)

        assert expires_at(early) == expires_at(late)
        assert expires_at(next_day) == expires_at(early) + timedelta(days=1)
        assert expires_at(early) == early.replace(hour=0, minute=0, second=0) + VALIDITY


class TestAgainstTheParser:
    def test_the_document_passes_the_dtc_parser(self, tmp_path: Path) -> None:
        """sectxt is the parser behind the Digital Trust Center's own check.
        Signing and an Encryption key are the two things it recommends that we
        deliberately leave out; see SECURITY.md."""
        sectxt = pytest.importorskip("sectxt")

        body = security_txt(_settings(tmp_path), datetime.now(UTC))
        parser = sectxt.Parser(body.encode(), urls=[f"{CONTENT}{PATH_SECURITY_TXT}"])

        assert parser.errors == []
        assert parser.is_valid()
        assert sorted(item["code"] for item in parser.recommendations) == ["no_encryption", "not_signed"]
