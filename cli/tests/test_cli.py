"""Tests voor de plak-CLI tegen een lokale stub-server.

Draai met:
    just test-cli
of rechtstreeks:
    cd cli && uv run pytest tests -q
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import keyring
import keyring.backend
import keyring.backends.fail
import keyring.backends.null
import keyring.errors

# The package installed in this project's environment, the same code the
# `plak` command runs.
import plak_cli as cli
import pytest
import yaml

# Mode bits and a uid mean nothing on NTFS (chmod there only toggles the
# read-only flag), so what rests on them only exists outside Windows.
posix_only = pytest.mark.skipif(cli.WINDOWS, reason="POSIX mode bits and uid")


def _parse_multipart(content_type: str, body: bytes) -> dict[str, dict[str, Any]]:
    boundary = None
    for piece in content_type.split(";"):
        piece = piece.strip()
        if piece.startswith("boundary="):
            boundary = piece[len("boundary=") :].strip('"')
    if boundary is None:
        raise ValueError(f"geen boundary in content-type: {content_type!r}")

    boundary_bytes = ("--" + boundary).encode()
    fields: dict[str, dict[str, Any]] = {}
    for part in body.split(boundary_bytes):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header_blob, _, content = part.partition(b"\r\n\r\n")
        headers: dict[str, str] = {}
        for line in header_blob.split(b"\r\n"):
            line = line.strip()
            if b":" in line:
                key, value = line.split(b":", 1)
                headers[key.strip().lower().decode()] = value.strip().decode()
        content = content.rstrip(b"\r\n")

        name = None
        file_name = None
        for piece in headers.get("content-disposition", "").split(";"):
            piece = piece.strip()
            if piece.startswith("name="):
                name = piece[len("name=") :].strip('"')
            elif piece.startswith("filename="):
                file_name = piece[len("filename=") :].strip('"')

        if name:
            fields[name] = {
                "file_name": file_name,
                "content": content,
                "content_type": headers.get("content-type"),
            }
    return fields


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _handle(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        record = {
            "method": self.command,
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body": body,
        }
        self.server.requests.append(record)  # type: ignore[attr-defined]
        status, payload, content_type = self.server.responder(record)  # type: ignore[attr-defined]
        self.send_response(status)
        for name, value in self.server.extra_headers.items():  # type: ignore[attr-defined]
            self.send_header(name, value)
        if payload is not None:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload is not None:
            self.wfile.write(payload)

    def do_POST(self) -> None:
        self._handle()

    def do_GET(self) -> None:
        self._handle()

    def do_DELETE(self) -> None:
        self._handle()

    def do_PUT(self) -> None:
        self._handle()


def _json_responder(status: int, data: dict[str, Any]):
    payload = json.dumps(data).encode()

    def responder(_record):
        return status, payload, "application/json"

    return responder


def _empty_responder(status: int):
    def responder(_record):
        return status, None, "text/plain"

    return responder


def _sequence_responder(by_path: dict[str, list[Callable[[dict], tuple[int, bytes | None, str]]]]):
    """Antwoordt op een pad met de volgende functie in zijn lijst, elke keer opnieuw
    aangeroepen; de laatste blijft gelden zodra de lijst leeg is."""

    def responder(record: dict) -> tuple[int, bytes | None, str]:
        path = record["path"].split("?", 1)[0]
        queue = by_path[path]
        step = queue.pop(0) if len(queue) > 1 else queue[0]
        return step(record)

    return responder


def _json_step(status: int, data: dict[str, Any]) -> Callable[[dict], tuple[int, bytes | None, str]]:
    payload = json.dumps(data).encode()
    return lambda _record: (status, payload, "application/json")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """The login poll waits `interval` seconds between polls; the stub server
    answers at once, so waiting for real only slows the suite. A test that
    checks the waits patches sleep again with its own recorder."""
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)


@pytest.fixture
def stub_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    server.requests = []  # type: ignore[attr-defined]
    server.extra_headers = {}  # type: ignore[attr-defined]
    server.responder = _json_responder(  # type: ignore[attr-defined]
        201, {"versionId": "00000000-0000-0000-0000-000000000000"}
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
    # Every request of every test that goes through the stub carries the
    # User-Agent, so a call that bypasses the shared helper fails here.
    for record in server.requests:  # type: ignore[attr-defined]
        assert record["headers"].get("User-Agent") == f"plak-cli/{cli.VERSION}"


@pytest.fixture
def host(stub_server) -> str:
    return f"http://{stub_server.server_address[0]}:{stub_server.server_address[1]}"


@pytest.fixture
def token_env(monkeypatch) -> str:
    """De meeste deploy-tests hebben alleen om het even welk token nodig."""
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "tok")
    return "tok"


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """Tests that look at the working directory never run in the checkout itself."""
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


class _MemoryKeyring(keyring.backend.KeyringBackend):
    """A system keyring in memory. `error` set: every call raises it."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self.secrets: dict[tuple[str, str], str] = {}
        self.error: Exception | None = None
        self.get_error: Exception | None = None
        self.set_error: Exception | None = None
        self.delete_error: Exception | None = None

    def get_password(self, service: str, username: str) -> str | None:
        if self.error or self.get_error:
            raise self.error or self.get_error
        return self.secrets.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        if self.error or self.set_error:
            raise self.error or self.set_error
        self.secrets[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.error or self.delete_error:
            raise self.error or self.delete_error
        if (service, username) not in self.secrets:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.secrets[(service, username)]


TEST_DEFAULT_HOST = "https://default.plak.invalid"


@pytest.fixture(autouse=True)
def memory_keyring(tmp_path, monkeypatch):
    """No test ever touches the real keyring, the real ~/.config/plak or the
    real default host."""
    monkeypatch.setenv("PLAK_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("PLAK_HOST", raising=False)
    monkeypatch.setattr(cli, "DEFAULT_HOST", TEST_DEFAULT_HOST)
    backend = _MemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)


def _store_session(
    host: str,
    access: str = "access-1",
    refresh: str = "refresh-1",
    expires_at: float = 9999999999.0,
    *,
    insecure: bool = False,
) -> None:
    cli._save_session(host, access, refresh, expires_at, insecure=insecure, make_default=True)


def _stored_tokens(host: str) -> tuple[str | None, str | None]:
    return cli._session_tokens(host, cli._host_entry(cli._read_hosts(), host))


def _set_entry_field(host: str, key: str, value: Any) -> None:
    config = cli._read_hosts()
    if value is _MISSING:
        del config["hosts"][host][key]
    else:
        config["hosts"][host][key] = value
    cli._write_hosts(config)


_MISSING = object()


@pytest.fixture
def dist_folder(tmp_path: Path) -> Path:
    folder_path = tmp_path / "dist"
    (folder_path / "assets").mkdir(parents=True)
    (folder_path / "index.html").write_text("<html>hoofdpagina</html>")
    (folder_path / "assets" / "stijl.css").write_text("body { color: black; }")
    (folder_path / "assets" / "diep" / "nog-dieper").mkdir(parents=True)
    (folder_path / "assets" / "diep" / "nog-dieper" / "bestand.js").write_text(
        "console.log(1);"
    )
    return folder_path


def test_publish_packs_a_folder_as_tar_gz_with_the_right_relative_paths(
    stub_server, host, dist_folder, token_env, capsys
):
    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 0
    output = capsys.readouterr().out.strip()
    assert output == "00000000-0000-0000-0000-000000000000"

    assert len(stub_server.requests) == 1
    record = stub_server.requests[0]
    assert record["method"] == "POST"
    assert record["path"] == "/-/api/v1/sites/team-aurora/website/deploys"
    assert record["headers"]["Authorization"] == "Bearer tok"

    fields = _parse_multipart(record["headers"]["Content-Type"], record["body"])
    assert "file" in fields
    assert "preview" not in fields

    tar_bytes = fields["file"]["content"]
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        names = sorted(tar.getnames())

    assert names == sorted(
        [
            "index.html",
            "assets/stijl.css",
            "assets/diep/nog-dieper/bestand.js",
        ]
    )
    for name in names:
        assert not name.startswith("/")
        assert not name.startswith("dist/")
        assert ".." not in name


def test_publish_sends_a_single_html_file_unchanged(
    stub_server, host, tmp_path, token_env, capsys
):
    html_path = tmp_path / "pagina.html"
    html_bytes = b"<html>los bestand</html>"
    html_path.write_bytes(html_bytes)

    code = cli.main(
        ["publish", str(html_path), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 0
    record = stub_server.requests[0]
    fields = _parse_multipart(record["headers"]["Content-Type"], record["body"])
    assert fields["file"]["content"] == html_bytes
    assert fields["file"]["content_type"] == "text/html"


def test_publish_with_the_preview_field(stub_server, host, dist_folder, token_env):
    code = cli.main(
        [
            "publish",
            str(dist_folder),
            "--host",
            host,
            "--site",
            "team-aurora/website",
            "--preview",
            "pr-42",
        ]
    )

    assert code == 0
    record = stub_server.requests[0]
    fields = _parse_multipart(record["headers"]["Content-Type"], record["body"])
    assert fields["preview"]["content"] == b"pr-42"


def test_publish_with_base_path(stub_server, host, dist_folder, token_env):
    code = cli.main(
        [
            "publish",
            str(dist_folder),
            "--host",
            host,
            "--site",
            "team-aurora/website",
            "--base-path",
            "./dist/",
        ]
    )

    assert code == 0
    record = stub_server.requests[0]
    fields = _parse_multipart(record["headers"]["Content-Type"], record["body"])
    assert fields["basePath"]["content"] == b"dist"


def test_publish_without_base_path_does_not_send_the_field(
    stub_server, host, dist_folder, token_env
):
    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 0
    record = stub_server.requests[0]
    fields = _parse_multipart(record["headers"]["Content-Type"], record["body"])
    assert "basePath" not in fields


@pytest.mark.parametrize("base_path", ["../geheim", "/etc", "dist/../..", "", "a\x00b"])
def test_publish_invalid_base_path_gives_exit_2_without_a_request(
    stub_server, host, dist_folder, token_env, base_path
):
    code = cli.main(
        [
            "publish",
            str(dist_folder),
            "--host",
            host,
            "--site",
            "team-aurora/website",
            "--base-path",
            base_path,
        ]
    )

    assert code == 2
    assert stub_server.requests == []


def test_publish_refused_bundle_shows_the_suggestion_and_the_flag(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = _json_responder(
        422,
        {
            "type": "about:blank",
            "title": "Onverwerkbare invoer",
            "status": 422,
            "detail": "geen index.html in de wortel van de bundel",
            "code": "NO_INDEX",
            "indexCandidates": ["dist/index.html", "docs/site/index.html"],
        },
    )

    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 1
    error_output = capsys.readouterr().err
    assert "dist/index.html" in error_output
    assert "docs/site/index.html" in error_output
    assert "--base-path dist" in error_output
    # Whoever reads this in an action log sees a workflow input, not a flag.
    assert "base-path: dist" in error_output


def test_publish_suggestion_in_the_root_advises_dropping_the_flag(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = _json_responder(
        422,
        {
            "type": "about:blank",
            "title": "Onverwerkbare invoer",
            "status": 422,
            "detail": "basispad 'dist' bevat geen index.html",
            "code": "BASE_PATH_WITHOUT_INDEX",
            "indexCandidates": ["index.html"],
        },
    )

    code = cli.main(
        [
            "publish",
            str(dist_folder),
            "--host",
            host,
            "--site",
            "team-aurora/website",
            "--base-path",
            "dist",
        ]
    )

    assert code == 1
    error_output = capsys.readouterr().err
    assert "without --base-path" in error_output
    assert "without the base-path input" in error_output


def test_publish_suggestion_with_odd_characters_is_not_shown(
    stub_server, host, dist_folder, token_env, capsys
):
    """Het antwoord komt van de server: niets uit dat voorstel mag ongezien op
    de terminal of in een CI-logregel belanden."""
    stub_server.responder = _json_responder(
        422,
        {
            "type": "about:blank",
            "title": "Onverwerkbare invoer",
            "status": 422,
            "detail": "geen index.html in de wortel van de bundel",
            "code": "NO_INDEX",
            "indexCandidates": ["dist/index.html\nversion-id=kwaad", 42, "/etc/passwd"],
        },
    )

    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 1
    error_output = capsys.readouterr().err
    assert "kwaad" not in error_output
    assert "passwd" not in error_output
    assert "--base-path" not in error_output


def test_publish_detail_with_control_characters_is_cleaned(
    stub_server, host, dist_folder, token_env, capsys
):
    """Het detail is vrije tekst van de server en draagt tegenwoordig
    archiefpaden; het mag net zomin als een voorstel een eigen regel of een
    ANSI-escape in de CI-uitvoer schuiven."""
    stub_server.responder = _json_responder(
        422,
        {
            "type": "about:blank",
            "title": "Onverwerkbare invoer",
            "status": 422,
            "detail": "geen index.html\n::error::gekaapt\n\x1b[31mrood\x1b[0m",
            "code": "NO_INDEX",
        },
    )

    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 1
    error_output = capsys.readouterr().err
    assert error_output.count("\n") == 1
    assert "\x1b" not in error_output
    assert error_output.startswith("Error: geen index.html")
    assert "::error::gekaapt" in error_output


def test_publish_detail_is_truncated(stub_server, host, dist_folder, token_env, capsys):
    stub_server.responder = _json_responder(
        422,
        {"title": "Onverwerkbare invoer", "status": 422, "detail": "x" * 5000},
    )

    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 1
    assert len(capsys.readouterr().err) < 2100


def test_publish_client_error_prints_the_dutch_detail_and_gives_exit_1(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = _json_responder(
        422,
        {
            "type": "about:blank",
            "title": "Unprocessable Entity",
            "status": 422,
            "detail": "Ongeldig archief: bevat een verboden segment '_preview'",
        },
    )

    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 1
    error_output = capsys.readouterr().err
    assert "Error: Ongeldig archief: bevat een verboden segment '_preview'" in error_output


def test_publish_unknown_file_type_gives_exit_2_without_a_request(
    stub_server, host, tmp_path, token_env, capsys
):
    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF-1.4")

    code = cli.main(
        ["publish", str(pdf_path), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 2
    assert stub_server.requests == []
    assert "Error" in capsys.readouterr().err


def test_publish_invalid_site_gives_exit_2(stub_server, host, dist_folder, token_env):
    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "zonder-slash"]
    )

    assert code == 2
    assert stub_server.requests == []


def test_publish_missing_path_gives_exit_2(stub_server, host, tmp_path, token_env):
    code = cli.main(
        [
            "publish",
            str(tmp_path / "bestaat-niet"),
            "--host",
            host,
            "--site",
            "team-aurora/website",
        ]
    )

    assert code == 2
    assert stub_server.requests == []


def test_publish_missing_required_arguments_gives_exit_2(stub_server):
    code = cli.main(["publish", "/tmp/iets"])
    assert code == 2


def test_preview_remove_succeeds(stub_server, host, token_env, capsys):
    stub_server.responder = _empty_responder(204)

    code = cli.main(["preview-remove", "pr-42", "--host", host, "--site", "team-aurora/website"])

    assert code == 0
    record = stub_server.requests[0]
    assert record["method"] == "DELETE"
    assert record["path"] == "/-/api/v1/sites/team-aurora/website/previews/pr-42"
    assert record["headers"]["Authorization"] == "Bearer tok"


def test_preview_remove_is_idempotent(stub_server, host, token_env):
    stub_server.responder = _empty_responder(204)

    args = ["preview-remove", "pr-42", "--host", host, "--site", "team-aurora/website"]

    first_code = cli.main(args)
    second_code = cli.main(args)

    assert first_code == 0
    assert second_code == 0
    assert len(stub_server.requests) == 2


def test_preview_remove_client_error_gives_exit_1(stub_server, host, monkeypatch, capsys):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "onjuist")
    stub_server.responder = _json_responder(
        401,
        {
            "type": "about:blank",
            "title": "Unauthorized",
            "status": 401,
            "detail": "Token is ongeldig of ingetrokken",
        },
    )

    code = cli.main(["preview-remove", "pr-42", "--host", host, "--site", "team-aurora/website"])

    assert code == 1
    assert "Token is ongeldig of ingetrokken" in capsys.readouterr().err


def test_publish_symlink_in_the_dist_folder_gives_exit_2_without_a_request(
    stub_server, host, dist_folder, tmp_path, token_env, capsys
):
    outside_file = tmp_path / "buiten-de-dist-map.txt"
    outside_file.write_text("niet bedoeld om mee te gaan")
    (dist_folder / "kwaadaardige-link").symlink_to(outside_file)

    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 2
    assert stub_server.requests == []
    assert "Error" in capsys.readouterr().err


def test_publish_server_sending_a_version_id_with_a_newline_gives_exit_1(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = _json_responder(201, {"versionId": "not-a-uuid\nevil=1"})

    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 1
    assert "unexpected versionId format" in capsys.readouterr().err


def test_preview_remove_invalid_ref_gives_exit_2_without_a_request(stub_server, host, token_env):
    code = cli.main(["preview-remove", "../x", "--host", host, "--site", "a/b"])

    assert code == 2
    assert stub_server.requests == []


def test_publish_invalid_site_part_with_dotdot_gives_exit_2_without_a_request(
    stub_server, host, dist_folder, token_env
):
    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "a/.."])

    assert code == 2
    assert stub_server.requests == []


def test_publish_http_not_localhost_gives_exit_2_without_a_request(
    stub_server, dist_folder, token_env, capsys
):
    code = cli.main(
        [
            "publish",
            str(dist_folder),
            "--host",
            "http://plak.example.nl",
            "--site",
            "team-aurora/website",
        ]
    )

    assert code == 2
    assert stub_server.requests == []
    assert "https" in capsys.readouterr().err.lower()


@pytest.mark.parametrize(
    "host",
    [
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://127.1.2.3",
        "http://[::1]:8080",
        # Plak's own dev stack; `.localhost` is loopback per RFC 6761 6.3.
        "http://beheer.plak.localhost:8080",
        "http://PLAK.LOCALHOST",
    ],
)
def test_http_is_allowed_to_loopback(host):
    cli._require_https(host)


@pytest.mark.parametrize(
    "host",
    [
        "http://plak.example.nl",
        # Not loopback: `localhost` does not sit at the end of the name here.
        "http://localhost.example.nl",
        "http://notlocalhost",
        "http://128.0.0.1",
        "http://[::2]",
    ],
)
def test_http_is_not_allowed_to_the_network(host):
    with pytest.raises(cli.UsageError):
        cli._require_https(host)


@pytest.mark.parametrize(
    "host",
    [
        "https://user:token@beheer.example.nl",
        "https://user@beheer.example.nl",
        "http://user:token@beheer.plak.localhost:8080",
    ],
)
def test_credentials_in_the_host_are_refused(host):
    """httpx would send them, and every message that names the host puts them
    in a terminal or a CI log afterwards."""
    with pytest.raises(cli.UsageError):
        cli._require_https(host)


def test_publish_token_via_environment_variable(
    stub_server, host, dist_folder, monkeypatch, capsys
):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "geheim-uit-omgeving")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 0
    record = stub_server.requests[0]
    assert record["headers"]["Authorization"] == "Bearer geheim-uit-omgeving"


def test_publish_client_error_does_not_leak_the_token_in_the_output(
    stub_server, host, dist_folder, monkeypatch, capsys
):
    token = "supergeheim-token-mag-nergens-verschijnen"
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", token)
    stub_server.responder = _json_responder(
        422,
        {
            "type": "about:blank",
            "title": "Unprocessable Entity",
            "status": 422,
            "detail": "Ongeldig archief: bevat een verboden segment '_preview'",
        },
    )

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 1
    output = capsys.readouterr()
    assert token not in output.out
    assert token not in output.err


def test_publish_without_any_token_source_asks_to_log_in(
    stub_server, host, dist_folder, isolated_cwd, capsys
):
    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 2
    assert stub_server.requests == []
    assert "plak login" in capsys.readouterr().err


# --- plak login: device flow ------------------------------------------------


def test_login_device_flow_success_stores_the_tokens_in_the_keyring(
    stub_server, host, isolated_cwd, memory_keyring, capsys
):
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [
                _json_step(
                    200,
                    {
                        "deviceCode": "devicecode-1",
                        "userCode": "ABCD-1234",
                        "verificationUri": f"{host}/-/device",
                        "verificationUriComplete": f"{host}/-/device?user_code=ABCD-1234",
                        "expiresIn": 60,
                        "interval": 0,
                    },
                )
            ],
            "/-/api/v1/cli/tokens": [
                _json_step(
                    400,
                    {
                        "type": "about:blank",
                        "title": "Bad Request",
                        "status": 400,
                        "code": "AUTHORIZATION_PENDING",
                        "detail": "nog niet goedgekeurd",
                    },
                ),
                _json_step(
                    200,
                    {
                        "accessToken": "access-1",
                        "refreshToken": "refresh-1",
                        "tokenType": "Bearer",
                        "expiresIn": 3600,
                        "member": {"email": "iemand@example.nl", "name": "Iemand"},
                    },
                ),
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    out = capsys.readouterr()
    assert "Logged in as iemand@example.nl." in out.out
    # The userCode and the sign-in URL may show on screen, the token never.
    assert "access-1" not in out.out
    assert "access-1" not in out.err
    assert "Session stored in the system keyring." in out.err

    assert json.loads(memory_keyring.secrets[(f"plak:{host}", "session")]) == {
        "access_token": "access-1",
        "refresh_token": "refresh-1",
    }
    hosts_path = cli._hosts_path()
    if not cli.WINDOWS:
        assert stat.S_IMODE(hosts_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(hosts_path.parent.stat().st_mode) == 0o700
    # hosts.json holds where the session lives, never the tokens themselves.
    assert "access-1" not in hosts_path.read_text()
    assert "refresh-1" not in hosts_path.read_text()
    config = cli._read_hosts()
    assert config["default_host"] == host
    assert config["hosts"][host]["storage"] == "keyring"
    # Nothing lands in the working directory any more.
    assert list(isolated_cwd.iterdir()) == []


def test_login_device_flow_handles_slow_down(stub_server, host, isolated_cwd, capsys):
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [
                _json_step(
                    200,
                    {
                        "deviceCode": "devicecode-1",
                        "userCode": "ABCD-1234",
                        "verificationUri": f"{host}/-/device",
                        "expiresIn": 60,
                        "interval": 0,
                    },
                )
            ],
            "/-/api/v1/cli/tokens": [
                _json_step(
                    400,
                    {"status": 400, "code": "SLOW_DOWN", "detail": "rustiger aan"},
                ),
                _json_step(
                    200,
                    {
                        "accessToken": "access-2",
                        "refreshToken": "refresh-2",
                        "expiresIn": 3600,
                        "member": {"email": "iemand@example.nl"},
                    },
                ),
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    assert _stored_tokens(host) == ("access-2", "refresh-2")


@pytest.mark.parametrize(
    "code,expected_words",
    [
        ("ACCESS_DENIED", "refused"),
        ("EXPIRED_TOKEN", "expired"),
        ("INVALID_GRANT", "Invalid or revoked"),
    ],
)
def test_login_device_flow_stops_on_denial(
    stub_server, host, isolated_cwd, capsys, code, expected_words
):
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [
                _json_step(
                    200,
                    {
                        "deviceCode": "devicecode-1",
                        "userCode": "ABCD-1234",
                        "verificationUri": f"{host}/-/device",
                        "expiresIn": 60,
                        "interval": 0,
                    },
                )
            ],
            "/-/api/v1/cli/tokens": [
                _json_step(400, {"status": 400, "code": code, "detail": "afgewezen"})
            ],
        }
    )

    exit_code = cli.main(["login", "--host", host, "--no-open"])

    assert exit_code == 1
    error_output = capsys.readouterr().err
    assert expected_words in error_output
    assert not cli._hosts_path().exists()


def test_login_requires_https(stub_server, isolated_cwd, capsys):
    code = cli.main(["login", "--host", "http://plak.example.nl", "--no-open"])

    assert code == 2
    assert "https" in capsys.readouterr().err.lower()


# --- refresh, whoami, logout --------------------------------------------------


def test_stored_session_is_refreshed_transparently_when_expired(
    stub_server, host, isolated_cwd, capsys
):
    _store_session(host, access="expired-access", refresh="refresh-old", expires_at=1.0)
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/tokens": [
                _json_step(
                    200,
                    {
                        "accessToken": "fresh-access",
                        "refreshToken": "refresh-new",
                        "expiresIn": 3600,
                    },
                )
            ],
            "/-/api/v1/cli/whoami": [
                _json_step(
                    200,
                    {
                        "member": {"email": "iemand@example.nl"},
                        "expiresAt": "2030-01-01T00:00:00Z",
                    },
                )
            ],
        }
    )

    code = cli.main(["whoami", "--host", host])

    assert code == 0
    out = capsys.readouterr().out
    assert "iemand@example.nl" in out
    assert "fresh-access" not in out

    tokens_requests = [r for r in stub_server.requests if r["path"] == "/-/api/v1/cli/tokens"]
    assert len(tokens_requests) == 1
    whoami_requests = [r for r in stub_server.requests if r["path"] == "/-/api/v1/cli/whoami"]
    assert whoami_requests[0]["headers"]["Authorization"] == "Bearer fresh-access"

    assert _stored_tokens(host) == ("fresh-access", "refresh-new")
    entry = cli._read_hosts()["hosts"][host]
    assert entry["storage"] == "keyring"
    assert entry["access_expires_at"] > cli.time.time()


def test_refresh_failure_asks_to_log_in_again(stub_server, host, isolated_cwd, capsys):
    _store_session(host, access="expired-access", refresh="spent-refresh", expires_at=1.0)
    stub_server.responder = _json_responder(
        400, {"status": 400, "code": "INVALID_GRANT", "detail": "verlopen"}
    )

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "plak login" in capsys.readouterr().err


def test_whoami_without_a_session_asks_to_log_in(stub_server, host, isolated_cwd, capsys):
    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "plak login" in capsys.readouterr().err
    assert stub_server.requests == []


def test_version_flag_prints_the_installed_version(capsys):
    code = cli.main(["--version"])

    assert code == 0
    assert capsys.readouterr().out == f"plak {importlib.metadata.version('plak')}\n"


def test_every_http_call_goes_through_the_shared_helper():
    tree = ast.parse(Path(cli.__file__).read_text())
    helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_http"
    )
    inside_helper = {id(node) for node in ast.walk(helper)}
    direct = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "httpx"
        and node.attr in {"get", "post", "put", "patch", "delete", "request", "stream", "Client"}
        and id(node) not in inside_helper
    ]
    assert direct == []


def test_requests_carry_the_user_agent(stub_server, host, isolated_cwd, monkeypatch):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "tok")
    stub_server.responder = _json_responder(200, {"member": {"email": "a@b.nl"}})

    assert cli.main(["whoami", "--host", host]) == 0

    agent = stub_server.requests[0]["headers"]["User-Agent"]
    assert agent == f"plak-cli/{importlib.metadata.version('plak')}"


@pytest.mark.parametrize(
    ("header", "code"),
    [
        (None, 0),
        ("1.0.0", 0),
        ("1.7.3", 0),
        ("0.9.0", 0),
        ("2", 0),
        ("two.0.0", 0),
        ("", 0),
        ("2.0.0", 1),
        ("3.1.4", 1),
    ],
)
def test_api_major_check(stub_server, host, isolated_cwd, monkeypatch, capsys, header, code):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "tok")
    if header is not None:
        stub_server.extra_headers["API-Version"] = header
    stub_server.responder = _json_responder(200, {"member": {"email": "a@b.nl"}})

    assert cli.main(["whoami", "--host", host]) == code

    captured = capsys.readouterr()
    if code == 0:
        assert "a@b.nl" in captured.out
        assert captured.err == ""
    else:
        assert captured.out == ""
        major = header.split(".")[0]
        assert f"Error: this server speaks API {major}.x" in captured.err
        assert f"supports API {cli.SUPPORTED_API_MAJOR}.x" in captured.err
        assert "<tag>#subdirectory=cli" in captured.err


def test_a_newer_api_major_is_refused_before_the_body_is_read(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.extra_headers["API-Version"] = "2.0.0"
    stub_server.responder = _json_responder(
        201, {"versionId": "00000000-0000-0000-0000-000000000000"}
    )

    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"]
    )

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "API 2.x" in captured.err


def test_logout_against_a_newer_api_major_still_forgets_the_session(
    stub_server, host, isolated_cwd, memory_keyring, capsys
):
    _store_session(host)
    stub_server.extra_headers["API-Version"] = "2.0.0"
    stub_server.responder = _empty_responder(204)

    code = cli.main(["logout", "--host", host])

    assert code == 1
    captured = capsys.readouterr()
    assert "Logged out locally." in captured.out
    assert "API 2.x" in captured.err
    assert memory_keyring.secrets == {}
    assert host not in cli._read_hosts()["hosts"]


def test_logout_clears_the_stored_session(stub_server, host, isolated_cwd, memory_keyring, capsys):
    _store_session(host)
    stub_server.responder = _empty_responder(204)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    assert "Logged out." in capsys.readouterr().out

    record = stub_server.requests[0]
    assert record["method"] == "DELETE"
    assert record["path"] == "/-/api/v1/cli/session"
    assert record["headers"]["Authorization"] == "Bearer access-1"

    assert memory_keyring.secrets == {}
    config = cli._read_hosts()
    assert host not in config["hosts"]
    # The default host stays: a next 'plak login' needs no --host.
    assert config["default_host"] == host


def test_logout_without_a_session_still_clears_and_succeeds(stub_server, host, isolated_cwd):
    code = cli.main(["logout", "--host", host])

    assert code == 0
    assert stub_server.requests == []


# --- OIDC in CI --------------------------------------------------------------


def test_publish_fetches_and_masks_an_oidc_token_in_ci(
    stub_server, host, dist_folder, isolated_cwd, monkeypatch, capsys
):
    stub_server.responder = _sequence_responder(
        {
            "/oidc-token": [_json_step(200, {"value": "oidc-jwt-token", "count": 0})],
            "/-/api/v1/sites/team-aurora/website/deploys": [
                _json_step(201, {"versionId": "00000000-0000-0000-0000-000000000000"})
            ],
        }
    )
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", f"{host}/oidc-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-bearer")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 0
    out = capsys.readouterr()
    assert "::add-mask::oidc-jwt-token" in out.out

    oidc_requests = [r for r in stub_server.requests if r["path"].startswith("/oidc-token")]
    assert len(oidc_requests) == 1
    assert oidc_requests[0]["headers"]["Authorization"] == "bearer runner-bearer"
    assert "audience=" in oidc_requests[0]["path"]

    deploy_requests = [
        r for r in stub_server.requests if r["path"] == "/-/api/v1/sites/team-aurora/website/deploys"
    ]
    assert deploy_requests[0]["headers"]["Authorization"] == "Bearer oidc-jwt-token"

    # OIDC mode does not write a session: the token only lives for this run.
    assert not cli._hosts_path().exists()


def test_explicit_plak_access_token_takes_priority_over_oidc(
    stub_server, host, dist_folder, isolated_cwd, monkeypatch
):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "expliciet-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", f"{host}/oidc-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-bearer")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 0
    assert all(not r["path"].startswith("/oidc-token") for r in stub_server.requests)
    deploy_requests = [
        r for r in stub_server.requests if r["path"] == "/-/api/v1/sites/team-aurora/website/deploys"
    ]
    assert deploy_requests[0]["headers"]["Authorization"] == "Bearer expliciet-token"


@posix_only
def test_hosts_file_is_never_readable_by_others_even_if_it_was(capsys):
    """An existing world-readable hosts.json is replaced, never written in
    place, and what it said is not carried over."""
    hosts_path = cli._hosts_path()
    hosts_path.parent.mkdir(parents=True)
    hosts_path.write_text(json.dumps({"default_host": "https://kwaad.example"}))
    hosts_path.chmod(0o644)

    _store_session("https://beheer.example", insecure=True)

    assert stat.S_IMODE(hosts_path.stat().st_mode) == 0o600
    assert cli._read_hosts()["default_host"] == "https://beheer.example"
    assert [p.name for p in hosts_path.parent.iterdir()] == ["hosts.json"]
    assert "ignoring" in capsys.readouterr().err


# --- action.yml: no stdout capture around publish, --output-file instead ----

ACTION_YML_PATH = Path(__file__).resolve().parents[2] / "actions" / "publish" / "action.yml"


def test_action_yml_does_not_capture_publish_stdout():
    """A $(...) capture around the publish call would swallow the OIDC
    '::add-mask::' workflow command that 'plak publish' also prints on
    stdout, and any newline-guard on the captured value never sees it."""
    content = ACTION_YML_PATH.read_text()
    assert "VERSION_ID=$(" not in content
    assert "--output-file" in content


def test_action_yml_pins_every_nested_action_to_a_commit_sha():
    """A tag can be moved, a commit cannot; the same rule as for the
    workflows in .github/ (backend/tests/test_workflows.py)."""
    content = ACTION_YML_PATH.read_text()
    uses = re.findall(r"^\s*(?:- )?uses:\s*(\S+)", content, re.MULTILINE)
    assert uses, "the action installs uv through a nested uses:"
    for reference in uses:
        ref = reference.split("@")[1]
        assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), reference


def test_action_yml_runs_the_cli_from_the_project_two_levels_up():
    """github.action_path is actions/publish/ in this repository, so the
    reference to the CLI project in cli/ has to climb out of it."""
    content = ACTION_YML_PATH.read_text()
    assert content.count('--project "${{ github.action_path }}/../../cli" plak') == 2
    project = (ACTION_YML_PATH.parent / ".." / ".." / "cli").resolve()
    assert (project / "pyproject.toml").is_file()
    assert (project / "plak_cli" / "__init__.py").is_file()


def test_publish_step_writes_version_id_to_output_file_and_masks_the_oidc_token(
    stub_server, host, dist_folder, isolated_cwd
):
    """Simulates the action's 'Publish' step as a real subprocess: the CLI
    called the way action.yml calls it (--output-file, output not captured
    by the shell) in CI mode (OIDC env vars set, no stored session). The
    '::add-mask::' line must reach real stdout, and the OIDC token must
    never land in the output file."""
    stub_server.responder = _sequence_responder(
        {
            "/oidc-token": [_json_step(200, {"value": "super-secret-oidc-jwt"})],
            "/-/api/v1/sites/team-aurora/website/deploys": [
                _json_step(201, {"versionId": "00000000-0000-0000-0000-000000000000"})
            ],
        }
    )
    github_output = isolated_cwd / "github_output.txt"
    github_output.write_text("")

    env = os.environ.copy()
    env.pop("PLAK_ACCESS_TOKEN", None)
    env["ACTIONS_ID_TOKEN_REQUEST_URL"] = f"{host}/oidc-token"
    env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"] = "runner-bearer"

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "plak_cli",
            "publish",
            str(dist_folder),
            "--host",
            host,
            "--site",
            "team-aurora/website",
            "--output-file",
            str(github_output),
        ],
        env=env,
        cwd=str(isolated_cwd),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "::add-mask::super-secret-oidc-jwt" in result.stdout

    output_content = github_output.read_text()
    assert output_content.strip() == "version-id=00000000-0000-0000-0000-000000000000"
    assert "super-secret-oidc-jwt" not in output_content


# --- action.yml: the composite action's steps, driven against the stub -----
#
# No workflow in this repository uses the action, so nothing else ever runs
# what a publisher's workflow runs. These tests take the shell steps out of
# action.yml, fill in the ${{ }} expressions the way a runner would, and
# execute them against the stub server, so the control flow (which step runs
# for which input combination, which arguments the CLI is handed, which exit
# code comes out) is exercised rather than read.

REPO_ROOT = ACTION_YML_PATH.resolve().parents[2]

_EXPRESSION = re.compile(r"\$\{\{\s*(.+?)\s*\}\}")
_COMPARISON = re.compile(r"^(\S+)\s*(==|!=)\s*'([^']*)'$")


def _action_definition() -> dict:
    return yaml.safe_load(ACTION_YML_PATH.read_text(encoding="utf-8"))


def _expression_values(inputs: dict[str, str], github: dict[str, str] | None = None) -> dict[str, str]:
    declared = _action_definition()["inputs"]
    unknown = set(inputs) - set(declared)
    assert not unknown, f"not an input of the action: {sorted(unknown)}"
    values = {
        f"inputs.{name}": inputs.get(name, str(spec.get("default", "")))
        for name, spec in declared.items()
    }
    values["github.action_path"] = str(ACTION_YML_PATH.parent)
    # A push by a person, unless the test says otherwise.
    values["github.event_name"] = "push"
    values["github.event.pull_request.user.login"] = ""
    values["github.event.pull_request.user.type"] = ""
    values.update({f"github.{key}": value for key, value in (github or {}).items()})
    return values


def _render(text: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        reference = match.group(1)
        if reference.startswith("steps.") and reference not in values:
            # A runner renders the output of a step that did not run as empty.
            return ""
        assert reference in values, f"unhandled expression in action.yml: {reference}"
        return values[reference]

    return _EXPRESSION.sub(replace, text)


def _step_runs(condition: str, values: dict[str, str]) -> bool:
    expression = _EXPRESSION.fullmatch(condition.strip())
    assert expression, f"unhandled if: in action.yml: {condition}"
    for part in expression.group(1).split(" && "):
        comparison = _COMPARISON.match(part.strip())
        assert comparison, f"unhandled if: in action.yml: {condition}"
        left, operator, right = comparison.groups()
        if left.startswith("steps."):
            value = values.get(left, "")
        else:
            assert left in values, f"unhandled expression in action.yml: {left}"
            value = values[left]
        if (value == right) != (operator == "=="):
            return False
    return True


class _ActionRun:
    def __init__(self) -> None:
        self.returncode = 0
        self.failed_step: str | None = None
        self.steps: list[str] = []
        self.stdout = ""
        self.stderr = ""


def _run_action(
    inputs: dict[str, str], *, env: dict[str, str], cwd: Path, github: dict[str, str] | None = None
) -> _ActionRun:
    """Runs the shell steps of action.yml the way a runner would: the ${{ }}
    expressions filled in, each step's own env applied, the `if:` conditions
    honoured, and the run stopping at the first step that fails."""
    values = _expression_values(inputs, github)
    run = _ActionRun()
    for step in _action_definition()["runs"]["steps"]:
        if "run" not in step:
            # A `uses:` step (setup-uv); installing uv is the runner's job and
            # test_action_yml_pins_every_nested_action_to_a_commit_sha covers it.
            continue
        if "if" in step and not _step_runs(step["if"], values):
            continue
        step_env = dict(os.environ)
        step_env.pop("PLAK_ACCESS_TOKEN", None)
        step_env.update({k: _render(str(v), values) for k, v in step.get("env", {}).items()})
        step_env.update(env)
        result = subprocess.run(
            ["bash", "-c", _render(step["run"], values)],
            env=step_env,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        run.steps.append(step["name"])
        if "id" in step and "GITHUB_OUTPUT" in env:
            for line in Path(env["GITHUB_OUTPUT"]).read_text().splitlines():
                key, _, value = line.partition("=")
                values[f"steps.{step['id']}.outputs.{key}"] = value
        run.stdout += result.stdout
        run.stderr += result.stderr
        if result.returncode != 0:
            run.returncode = result.returncode
            run.failed_step = step["name"]
            break
    return run


@pytest.fixture
def action_env(host, isolated_cwd) -> dict[str, str]:
    """A runner with `id-token: write`: the CLI mints its own token per run."""
    output_path = isolated_cwd / "github_output.txt"
    output_path.write_text("")
    return {
        "ACTIONS_ID_TOKEN_REQUEST_URL": f"{host}/oidc-token",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "runner-bearer",
        "GITHUB_OUTPUT": str(output_path),
    }


def _action_responder(stub_server, *, deploy=None, remove=None) -> None:
    stub_server.responder = _sequence_responder(
        {
            "/oidc-token": [_json_step(200, {"value": "oidc-jwt-token"})],
            "/-/api/v1/sites/team-aurora/website/deploys": [
                deploy or _json_step(201, {"versionId": "11111111-2222-3333-4444-555555555555"})
            ],
            "/-/api/v1/sites/team-aurora/website/previews/pr-42": [
                remove or (lambda _record: (204, None, "text/plain"))
            ],
        }
    )


def _deploy_requests(stub_server) -> list[dict]:
    return [
        r for r in stub_server.requests if r["path"] == "/-/api/v1/sites/team-aurora/website/deploys"
    ]


def test_action_publishes_live_and_reports_the_version_id(
    stub_server, host, dist_folder, isolated_cwd, action_env
):
    _action_responder(stub_server)

    run = _run_action(
        {"host": host, "site": "team-aurora/website", "dist-path": str(dist_folder)},
        env=action_env,
        cwd=isolated_cwd,
    )

    assert run.returncode == 0, run.stderr
    assert run.steps == [
        "Check pull request author",
        "Check OIDC access",
        "Check GitHub options",
        "Publish",
        "Report to GitHub",
    ]

    deploys = _deploy_requests(stub_server)
    assert len(deploys) == 1
    assert deploys[0]["method"] == "POST"
    assert deploys[0]["headers"]["Authorization"] == "Bearer oidc-jwt-token"

    fields = _parse_multipart(deploys[0]["headers"]["Content-Type"], deploys[0]["body"])
    assert sorted(fields) == ["file"]
    with tarfile.open(fileobj=io.BytesIO(fields["file"]["content"]), mode="r:gz") as tar:
        assert "index.html" in tar.getnames()

    output_path = Path(action_env["GITHUB_OUTPUT"])
    assert output_path.read_text().strip() == "version-id=11111111-2222-3333-4444-555555555555"
    assert "oidc-jwt-token" not in output_path.read_text()
    assert "::add-mask::oidc-jwt-token" in run.stdout


def test_action_passes_preview_ref_and_base_path_on_to_the_server(
    stub_server, host, dist_folder, isolated_cwd, action_env
):
    """The preview and base-path inputs are optional flags the step only adds
    when they are filled, so their presence in the request is the proof."""
    _action_responder(stub_server)

    run = _run_action(
        {
            "host": host,
            "site": "team-aurora/website",
            "dist-path": str(dist_folder),
            "preview-ref": "pr-42",
            "base-path": "assets",
        },
        env=action_env,
        cwd=isolated_cwd,
    )

    assert run.returncode == 0, run.stderr
    fields = _parse_multipart(
        _deploy_requests(stub_server)[0]["headers"]["Content-Type"],
        _deploy_requests(stub_server)[0]["body"],
    )
    assert fields["preview"]["content"] == b"pr-42"
    assert fields["basePath"]["content"] == b"assets"


def test_action_teardown_removes_the_preview_and_publishes_nothing(
    stub_server, host, dist_folder, isolated_cwd, action_env
):
    """teardown 'true' switches steps: the Publish step is skipped even with a
    dist-path filled in, so a closed pull request never deploys once more."""
    _action_responder(stub_server)

    run = _run_action(
        {
            "host": host,
            "site": "team-aurora/website",
            "dist-path": str(dist_folder),
            "preview-ref": "pr-42",
            "teardown": "true",
        },
        env=action_env,
        cwd=isolated_cwd,
    )

    assert run.returncode == 0, run.stderr
    assert run.steps == [
        "Check pull request author",
        "Check OIDC access",
        "Check GitHub options",
        "Remove preview",
        "Report to GitHub",
    ]
    assert _deploy_requests(stub_server) == []

    removals = [r for r in stub_server.requests if "/previews/" in r["path"]]
    assert len(removals) == 1
    assert removals[0]["method"] == "DELETE"
    assert removals[0]["path"] == "/-/api/v1/sites/team-aurora/website/previews/pr-42"
    assert removals[0]["headers"]["Authorization"] == "Bearer oidc-jwt-token"
    assert Path(action_env["GITHUB_OUTPUT"]).read_text() == ""


def test_action_refuses_a_teardown_without_a_preview_ref(
    stub_server, host, isolated_cwd, action_env
):
    """The description promises preview-ref becomes required with teardown;
    without the guard the step would remove nothing and go green."""
    _action_responder(stub_server)

    run = _run_action(
        {"host": host, "site": "team-aurora/website", "teardown": "true"},
        env=action_env,
        cwd=isolated_cwd,
    )

    assert run.returncode == 2
    assert run.failed_step == "Remove preview"
    assert "preview-ref is required when teardown is 'true'" in run.stderr
    assert stub_server.requests == []


def test_action_refuses_a_publish_without_a_dist_path(
    stub_server, host, isolated_cwd, action_env
):
    _action_responder(stub_server)

    run = _run_action(
        {"host": host, "site": "team-aurora/website"}, env=action_env, cwd=isolated_cwd
    )

    assert run.returncode == 2
    assert run.failed_step == "Publish"
    assert "dist-path is required unless teardown is 'true'" in run.stderr
    assert stub_server.requests == []


def test_action_stops_before_publishing_when_the_runner_offers_no_oidc(
    stub_server, host, dist_folder, isolated_cwd, action_env
):
    """Without `permissions: id-token: write` the two request variables are
    not in the environment at all. The step has to name that itself: the CLI
    would otherwise fall through to 'log in with plak login', which is no
    advice on a runner."""
    _action_responder(stub_server)

    run = _run_action(
        {"host": host, "site": "team-aurora/website", "dist-path": str(dist_folder)},
        env={"GITHUB_OUTPUT": action_env["GITHUB_OUTPUT"]},
        cwd=isolated_cwd,
    )

    assert run.returncode == 2
    assert run.failed_step == "Check OIDC access"
    assert run.steps == ["Check pull request author", "Check OIDC access"]
    assert "no OIDC token available" in run.stderr
    assert "id-token: write" in run.stderr
    assert "enable-openid-connect: true" in run.stderr
    assert stub_server.requests == []


def test_action_fails_the_step_when_the_server_refuses_the_deploy(
    stub_server, host, dist_folder, isolated_cwd, action_env
):
    """A refusal from Plak (no trust relation, quota, a broken bundle) has to
    come out as a failed step. `set -e` plus a non-zero CLI is what carries
    that; a silent pass would let a workflow report a deploy that never was."""
    _action_responder(
        stub_server,
        deploy=_json_step(
            403, {"title": "Geen toegang", "detail": "This repository may not publish here."}
        ),
    )

    run = _run_action(
        {"host": host, "site": "team-aurora/website", "dist-path": str(dist_folder)},
        env=action_env,
        cwd=isolated_cwd,
    )

    assert run.returncode != 0
    assert run.failed_step == "Publish"
    assert "This repository may not publish here." in run.stderr
    assert Path(action_env["GITHUB_OUTPUT"]).read_text() == ""


def test_action_fails_the_teardown_step_when_the_server_refuses_it(
    stub_server, host, isolated_cwd, action_env
):
    _action_responder(
        stub_server,
        remove=_json_step(403, {"title": "Geen toegang", "detail": "Not your preview."}),
    )

    run = _run_action(
        {"host": host, "site": "team-aurora/website", "preview-ref": "pr-42", "teardown": "true"},
        env=action_env,
        cwd=isolated_cwd,
    )

    assert run.returncode != 0
    assert run.failed_step == "Remove preview"
    assert "Not your preview." in run.stderr


def test_action_without_host_leaves_the_host_to_the_cli(
    stub_server, host, dist_folder, isolated_cwd, action_env
):
    """An empty host input passes no --host at all, so PLAK_HOST set on the
    job reaches the CLI; on a runner without it the CLI's DEFAULT_HOST
    applies (test_default_host_*)."""
    _action_responder(stub_server)
    env = {**action_env, "PLAK_HOST": host}

    publish = _run_action(
        {"site": "team-aurora/website", "dist-path": str(dist_folder), "preview-ref": "pr-42"},
        env=env,
        cwd=isolated_cwd,
    )
    teardown = _run_action(
        {"site": "team-aurora/website", "preview-ref": "pr-42", "teardown": "true"},
        env=env,
        cwd=isolated_cwd,
    )

    assert publish.returncode == 0, publish.stderr
    assert teardown.returncode == 0, teardown.stderr
    assert [r["method"] for r in stub_server.requests if "/-/api/" in r["path"]] == ["POST", "DELETE"]


def test_action_refuses_a_comment_input_that_is_not_true_or_false(
    stub_server, host, dist_folder, isolated_cwd, action_env
):
    _action_responder(stub_server)

    run = _run_action(
        {"host": host, "site": "team-aurora/website", "dist-path": str(dist_folder), "comment-on-pr": "yes"},
        env=action_env,
        cwd=isolated_cwd,
    )

    assert run.returncode == 2
    assert run.failed_step == "Check GitHub options"
    assert "comment-on-pr must be 'true' or 'false'" in run.stderr
    assert stub_server.requests == []


@pytest.mark.parametrize("option", [{"environment": "preview"}, {"comment-on-pr": "true"}])
def test_action_refuses_the_github_options_on_forgejo_before_publishing(
    stub_server, host, dist_folder, isolated_cwd, action_env, option
):
    _action_responder(stub_server)

    run = _run_action(
        {"host": host, "site": "team-aurora/website", "dist-path": str(dist_folder), **option},
        env={**action_env, "FORGEJO_ACTIONS": "true"},
        cwd=isolated_cwd,
    )

    assert run.returncode == 2
    assert run.failed_step == "Check GitHub options"
    assert "work on GitHub Actions only" in run.stderr
    assert stub_server.requests == []


def test_action_on_forgejo_without_github_options_publishes(
    stub_server, host, dist_folder, isolated_cwd, action_env
):
    _action_responder(stub_server)

    run = _run_action(
        {"host": host, "site": "team-aurora/website", "dist-path": str(dist_folder)},
        env={**action_env, "FORGEJO_ACTIONS": "true"},
        cwd=isolated_cwd,
    )

    assert run.returncode == 0, run.stderr
    assert len(_deploy_requests(stub_server)) == 1


def _bot_pull_request(login: str, user_type: str, event: str = "pull_request") -> dict[str, str]:
    return {
        "event_name": event,
        "event.pull_request.user.login": login,
        "event.pull_request.user.type": user_type,
    }


@pytest.mark.parametrize(
    ("login", "user_type", "event"),
    [
        ("some-app[bot]", "Bot", "pull_request"),
        # Forgejo has no user type; the known logins still count.
        ("renovate[bot]", "", "pull_request"),
        ("dependabot[bot]", "Bot", "pull_request_target"),
    ],
)
@pytest.mark.parametrize("teardown", ["false", "true"])
def test_action_leaves_a_bots_pull_request_alone(
    stub_server, host, dist_folder, isolated_cwd, action_env, login, user_type, event, teardown
):
    """A Dependabot pull request gets no OIDC token: without the skip the
    run would go red on 'no OIDC token available'. With it, nothing runs
    after the check, the teardown neither, and the skipped output says so."""
    _action_responder(stub_server)

    run = _run_action(
        {
            "host": host,
            "site": "team-aurora/website",
            "dist-path": str(dist_folder),
            "preview-ref": "pr-42",
            "teardown": teardown,
        },
        env={"GITHUB_OUTPUT": action_env["GITHUB_OUTPUT"]},
        cwd=isolated_cwd,
        github=_bot_pull_request(login, user_type, event),
    )

    assert run.returncode == 0, run.stderr
    assert run.steps == ["Check pull request author"]
    assert f"::notice::Skipping: pull request author '{login}' is a bot" in run.stdout
    assert Path(action_env["GITHUB_OUTPUT"]).read_text() == "skipped=true\n"
    assert stub_server.requests == []


@pytest.mark.parametrize(
    "github",
    [
        _bot_pull_request("robbert", "User"),
        # A push carries no pull request author, whoever pushed.
        _bot_pull_request("dependabot[bot]", "Bot", event="push"),
    ],
)
def test_action_publishes_for_a_person_and_for_a_push(
    stub_server, host, dist_folder, isolated_cwd, action_env, github
):
    _action_responder(stub_server)

    run = _run_action(
        {"host": host, "site": "team-aurora/website", "dist-path": str(dist_folder)},
        env=action_env,
        cwd=isolated_cwd,
        github=github,
    )

    assert run.returncode == 0, run.stderr
    assert len(_deploy_requests(stub_server)) == 1
    assert "skipped=" not in Path(action_env["GITHUB_OUTPUT"]).read_text()


def test_action_with_skip_bot_prs_false_publishes_for_a_bot(
    stub_server, host, dist_folder, isolated_cwd, action_env
):
    _action_responder(stub_server)

    run = _run_action(
        {
            "host": host,
            "site": "team-aurora/website",
            "dist-path": str(dist_folder),
            "preview-ref": "pr-42",
            "skip-bot-prs": "false",
        },
        env=action_env,
        cwd=isolated_cwd,
        github=_bot_pull_request("dependabot[bot]", "Bot"),
    )

    assert run.returncode == 0, run.stderr
    assert len(_deploy_requests(stub_server)) == 1


def test_action_refuses_a_skip_bot_prs_input_that_is_not_true_or_false(
    stub_server, host, dist_folder, isolated_cwd, action_env
):
    _action_responder(stub_server)

    run = _run_action(
        {"host": host, "site": "team-aurora/website", "dist-path": str(dist_folder), "skip-bot-prs": "nee"},
        env=action_env,
        cwd=isolated_cwd,
    )

    assert run.returncode == 2
    assert run.failed_step == "Check pull request author"
    assert "skip-bot-prs must be 'true' or 'false'" in run.stderr
    assert stub_server.requests == []


PREVIEW_URL = "https://plak.example/team-aurora/website/_preview/pr-42/"


@pytest.fixture
def github_runner(host, isolated_cwd, action_env) -> dict[str, str]:
    """action_env on a pull_request event for PR 42, with the stub server
    standing in for the GitHub API as well."""
    event = isolated_cwd / "event.json"
    event.write_text(json.dumps({"pull_request": {"number": 42, "head": {"sha": "abcdef0123456789"}}}))
    return {
        **action_env,
        "GITHUB_API_URL": host,
        "GITHUB_SERVER_URL": "https://github.test",
        "GITHUB_REPOSITORY": "digigilde/website",
        "GITHUB_RUN_ID": "987",
        "GITHUB_SHA": "merge0000000000",
        "GITHUB_EVENT_PATH": str(event),
    }


def test_action_reports_a_preview_to_github_as_deployment_and_comment(
    stub_server, host, dist_folder, isolated_cwd, github_runner
):
    """The url the deploy returns travels through the Publish step's output
    into the deployment status and the comment."""
    repo = "/repos/digigilde/website"
    stub_server.responder = _sequence_responder(
        {
            "/oidc-token": [_json_step(200, {"value": "oidc-jwt-token"})],
            "/-/api/v1/sites/team-aurora/website/deploys": [
                _json_step(201, {"versionId": "11111111-2222-3333-4444-555555555555", "url": PREVIEW_URL})
            ],
            f"{repo}/deployments": [_json_step(201, {"id": 7}), _json_step(200, [{"id": 7}])],
            f"{repo}/deployments/7/statuses": [_json_step(201, {})],
            f"{repo}/issues/42/comments": [_json_step(200, []), _json_step(201, {"id": 1})],
        }
    )

    run = _run_action(
        {
            "host": host,
            "site": "team-aurora/website",
            "dist-path": str(dist_folder),
            "preview-ref": "pr-42",
            "environment": "preview",
            "comment-on-pr": "true",
            "github-token": "ghs-token",
        },
        env=github_runner,
        cwd=isolated_cwd,
    )

    assert run.returncode == 0, run.stderr
    github = [r for r in stub_server.requests if r["path"].startswith(repo)]
    assert [(r["method"], r["path"].split("?")[0]) for r in github] == [
        ("POST", f"{repo}/deployments"),
        ("POST", f"{repo}/deployments/7/statuses"),
        ("GET", f"{repo}/deployments"),
        ("GET", f"{repo}/issues/42/comments"),
        ("POST", f"{repo}/issues/42/comments"),
    ]
    assert all(r["headers"]["Authorization"] == "Bearer ghs-token" for r in github)
    assert json.loads(github[1]["body"])["environment_url"] == PREVIEW_URL
    assert PREVIEW_URL in json.loads(github[4]["body"])["body"]
    assert f"url={PREVIEW_URL}" in Path(github_runner["GITHUB_OUTPUT"]).read_text()


def test_action_fails_the_report_when_the_server_returns_no_url(
    stub_server, host, dist_folder, isolated_cwd, github_runner
):
    """A Plak from before the url field: the deploy stands, but a comment
    without a link is no comment, so the step says why it cannot."""
    _action_responder(stub_server)

    run = _run_action(
        {
            "host": host,
            "site": "team-aurora/website",
            "dist-path": str(dist_folder),
            "preview-ref": "pr-42",
            "comment-on-pr": "true",
            "github-token": "ghs-token",
        },
        env=github_runner,
        cwd=isolated_cwd,
    )

    assert run.returncode == 1
    assert run.failed_step == "Report to GitHub"
    assert "returned no url" in run.stderr
    assert len(_deploy_requests(stub_server)) == 1


def test_action_teardown_reports_the_removal_to_github(
    stub_server, host, isolated_cwd, github_runner
):
    repo = "/repos/digigilde/website"
    stub_server.responder = _sequence_responder(
        {
            "/oidc-token": [_json_step(200, {"value": "oidc-jwt-token"})],
            "/-/api/v1/sites/team-aurora/website/previews/pr-42": [lambda _record: (204, None, "text/plain")],
            f"{repo}/deployments": [_json_step(200, [])],
            f"{repo}/issues/42/comments": [_json_step(200, [])],
        }
    )

    run = _run_action(
        {
            "host": host,
            "site": "team-aurora/website",
            "preview-ref": "pr-42",
            "teardown": "true",
            "environment": "preview",
            "comment-on-pr": "true",
            "github-token": "ghs-token",
        },
        env=github_runner,
        cwd=isolated_cwd,
    )

    assert run.returncode == 0, run.stderr
    assert [r["method"] + " " + r["path"].split("?")[0] for r in stub_server.requests] == [
        "GET /oidc-token",
        "DELETE /-/api/v1/sites/team-aurora/website/previews/pr-42",
        f"GET {repo}/deployments",
        f"GET {repo}/issues/42/comments",
    ]


# --- action.yml: one repository reference, in every place that names it ----


def _repository_reference() -> str:
    """publiccode.yml is where this repository declares its own address, and
    it is the one place kept current for the Standard for Public Code."""
    url = yaml.safe_load((REPO_ROOT / "publiccode.yml").read_text(encoding="utf-8"))["url"]
    prefix = "https://github.com/"
    assert url.startswith(prefix), url
    return url[len(prefix) :].rstrip("/")


# Everything that tells a publisher where the action lives. The generated
# snippet in the Deploy tab referenced an organization that never existed for
# months, because nothing read these four files together.
_REPOSITORY_MENTION = re.compile(
    r"(?:github\.com/|uses:\s*|marketplace add\s+|['\"])([A-Za-z0-9][\w.-]*/plak)(?=[/@\s'\"`#])"
)

ACTION_REFERENCE_FILES = (
    "frontend/src/components/site/TabDeploy.vue",
    "frontend/src/components/site/TabDeploy.test.ts",
    "docs/publishing.md",
    "plugin/skills/plak-publish/SKILL.md",
    "README.md",
)


def test_every_place_that_names_the_action_names_the_same_repository():
    reference = _repository_reference()
    found = {}
    for name in ACTION_REFERENCE_FILES:
        content = (REPO_ROOT / name).read_text(encoding="utf-8")
        owners = set(re.findall(_REPOSITORY_MENTION, content))
        assert owners, f"{name} names the plak repository nowhere any more"
        found[name] = owners
    for name, owners in found.items():
        assert owners == {reference}, f"{name} points at {sorted(owners)}, not {reference}"


def test_the_deploy_tab_snippet_and_the_docs_point_at_the_action_path():
    """github.com/<owner>/<repo>/<path>@<ref>: the action sits in a
    subdirectory, so the path has to be part of the reference."""
    reference = _repository_reference()
    for name in ("frontend/src/components/site/TabDeploy.vue", "docs/publishing.md"):
        content = (REPO_ROOT / name).read_text(encoding="utf-8")
        uses = re.findall(r"uses:\s*(\S*/plak/\S+)", content)
        assert uses, f"{name} has no `uses:` for the action"
        for value in uses:
            assert value.startswith(
                (f"{reference}/actions/publish@", f"https://github.com/{reference}/actions/publish@")
            ), f"{name}: {value}"


def test_the_generated_snippet_only_uses_inputs_the_action_declares():
    """A `with:` key the action does not know is silently ignored by the
    runner, so a renamed input shows up as a deploy that quietly does
    something else."""
    declared = set(_action_definition()["inputs"])
    snippet = (REPO_ROOT / "frontend/src/components/site/TabDeploy.vue").read_text(
        encoding="utf-8"
    )
    blocks = re.findall(
        r"uses: \S*/plak/actions/publish@\S+\n {8}with:\n((?: {10}[\w-]+:.*\n)+)", snippet
    )
    assert len(blocks) == 6, "two snippets, three action steps each"
    for block in blocks:
        keys = set(re.findall(r"^\s+([\w-]+):", block, re.MULTILINE))
        assert keys <= declared, sorted(keys - declared)
        assert {"host", "site"} <= keys


# --- plak login: only same-origin URLs, server strings cleaned -------------


def test_login_rejects_a_verification_uri_that_is_not_same_origin(
    stub_server, host, isolated_cwd, capsys
):
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [
                _json_step(
                    200,
                    {
                        "deviceCode": "devicecode-1",
                        "userCode": "ABCD-1234",
                        "verificationUri": "file:///etc/passwd",
                        "verificationUriComplete": (
                            "http://evil.example/device?user_code=ABCD-1234"
                        ),
                        "expiresIn": 60,
                        "interval": 0,
                    },
                )
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 1
    error_output = capsys.readouterr().err
    assert host in error_output
    assert "evil.example" not in error_output
    assert "/etc/passwd" not in error_output
    assert not cli._hosts_path().exists()


def test_login_accepts_verification_uri_when_only_the_complete_variant_matches(
    stub_server, host, isolated_cwd, capsys
):
    """verificationUri may be foreign as long as verificationUriComplete is
    same-origin: each candidate is checked on its own, not as a pair."""
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [
                _json_step(
                    200,
                    {
                        "deviceCode": "devicecode-1",
                        "userCode": "ABCD-1234",
                        "verificationUri": "http://evil.example/device",
                        "verificationUriComplete": f"{host}/-/device?user_code=ABCD-1234",
                        "expiresIn": 60,
                        "interval": 0,
                    },
                )
            ],
            "/-/api/v1/cli/tokens": [
                _json_step(
                    200,
                    {
                        "accessToken": "access-1",
                        "refreshToken": "refresh-1",
                        "expiresIn": 3600,
                        "member": {"email": "iemand@example.nl"},
                    },
                )
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    out = capsys.readouterr()
    assert "evil.example" not in out.err
    assert f"{host}/-/device" in out.err


def test_login_cleans_control_characters_from_server_strings(
    stub_server, host, isolated_cwd, capsys
):
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [
                _json_step(
                    200,
                    {
                        "deviceCode": "devicecode-1",
                        "userCode": "ABCD\x1b[31m-1234",
                        "verificationUri": f"{host}/-/device",
                        "expiresIn": 60,
                        "interval": 0,
                    },
                )
            ],
            "/-/api/v1/cli/tokens": [
                _json_step(
                    200,
                    {
                        "accessToken": "access-1",
                        "refreshToken": "refresh-1",
                        "expiresIn": 3600,
                        "member": {"email": "ie\x1b[31mmand@example.nl"},
                    },
                )
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    out = capsys.readouterr()
    assert "\x1b" not in out.out
    assert "\x1b" not in out.err


def test_whoami_cleans_control_characters_from_server_strings(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "tok")
    stub_server.responder = _json_responder(
        200,
        {
            "member": {"email": "ie\x1b[31mmand@example.nl"},
            "expiresAt": "2030\x1b[31m-01-01",
        },
    )

    code = cli.main(["whoami", "--host", host])

    assert code == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out


# --- hosts.json: the default host, and when it is trusted --------------------


def _write_raw_hosts(content: str | bytes, mode: int = 0o600) -> Path:
    hosts_path = cli._hosts_path()
    hosts_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if isinstance(content, bytes):
        hosts_path.write_bytes(content)
        hosts_path.chmod(mode)
        return hosts_path
    hosts_path.write_text(content)
    hosts_path.chmod(mode)
    return hosts_path


def test_default_host_is_used_when_no_host_flag_given(stub_server, host, monkeypatch):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "tok")
    _store_session(host)
    stub_server.responder = _json_responder(200, {"member": {"email": "iemand@example.nl"}})

    code = cli.main(["whoami"])

    assert code == 0
    assert stub_server.requests[0]["path"] == "/-/api/v1/cli/whoami"


def test_the_host_comes_from_the_flag_then_plak_host_then_the_last_login_then_the_default(
    monkeypatch,
):
    assert _resolved_host() == TEST_DEFAULT_HOST

    _store_session("https://laatst.example")
    assert _resolved_host() == "https://laatst.example"

    monkeypatch.setenv("PLAK_HOST", "https://omgeving.example/")
    assert _resolved_host() == "https://omgeving.example"

    assert cli._resolve_host(argparse.Namespace(host="https://vlag.example/")) == "https://vlag.example"


@pytest.mark.parametrize(
    "command",
    [["publish", "DIST", "--site", "team-aurora/website"], ["preview-remove", "pr-1", "--site", "team-aurora/website"]],
    ids=["publish", "preview-remove"],
)
def test_publish_and_preview_remove_need_no_host_flag(stub_server, host, dist_folder, command):
    _store_session(host, access="stored")
    stub_server.responder = lambda record: (
        (201, json.dumps({"versionId": "00000000-0000-0000-0000-000000000000"}).encode(), "application/json")
        if record["method"] == "POST"
        else (204, None, "text/plain")
    )

    code = cli.main([str(dist_folder) if part == "DIST" else part for part in command])

    assert code == 0
    assert stub_server.requests[0]["headers"]["Authorization"] == "Bearer stored"


def test_the_built_in_default_host_is_used_without_anything_else(monkeypatch, stub_server, host):
    """Without --host, PLAK_HOST or a login, a command goes to DEFAULT_HOST."""
    monkeypatch.setattr(cli, "DEFAULT_HOST", host)
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "tok")
    stub_server.responder = _json_responder(200, {"member": {"email": "iemand@example.nl"}})

    code = cli.main(["whoami"])

    assert code == 0
    assert [r["path"] for r in stub_server.requests] == ["/-/api/v1/cli/whoami"]


def test_the_session_holds_in_any_directory(stub_server, host, tmp_path, monkeypatch, capsys):
    """The point of the whole design: one login, every project."""
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [_device_start_step(host)],
            "/-/api/v1/cli/tokens": [_json_step(200, {"accessToken": "access-1", "expiresIn": 3600})],
            "/-/api/v1/cli/whoami": [_json_step(200, {"member": {"email": "iemand@example.nl"}})],
        }
    )
    first, second = tmp_path / "project-a", tmp_path / "project-b"
    first.mkdir()
    second.mkdir()
    monkeypatch.chdir(first)
    assert cli.main(["login", "--host", host, "--no-open"]) == 0

    monkeypatch.chdir(second)
    code = cli.main(["whoami"])

    assert code == 0
    assert stub_server.requests[-1]["headers"]["Authorization"] == "Bearer access-1"
    assert list(first.iterdir()) == [] and list(second.iterdir()) == []


def _resolved_host() -> str:
    return cli._resolve_host(argparse.Namespace())


@posix_only
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_hosts_file_readable_or_writable_by_others_is_ignored(mode, capsys):
    hosts_path = _write_raw_hosts(json.dumps({"default_host": "https://kwaad.example"}), mode)

    assert _resolved_host() == TEST_DEFAULT_HOST
    assert f"ignoring {hosts_path}" in capsys.readouterr().err


@posix_only
def test_hosts_file_owned_by_someone_else_is_ignored(monkeypatch, capsys):
    _write_raw_hosts(json.dumps({"default_host": "https://kwaad.example"}))
    real_uid = os.getuid()
    monkeypatch.setattr(cli.os, "getuid", lambda: real_uid + 1)

    assert _resolved_host() == TEST_DEFAULT_HOST
    assert "it must be a file of yours with mode 0600" in capsys.readouterr().err


@posix_only
def test_hosts_file_behind_a_symlink_is_ignored(tmp_path, capsys):
    """A symlink could point hosts.json at another 0600 file of the user's."""
    elsewhere = tmp_path / "elders.json"
    elsewhere.write_text(json.dumps({"default_host": "https://kwaad.example"}))
    elsewhere.chmod(0o600)
    hosts_path = cli._hosts_path()
    hosts_path.parent.mkdir(mode=0o700, parents=True)
    hosts_path.symlink_to(elsewhere)

    assert _resolved_host() == TEST_DEFAULT_HOST
    assert f"ignoring {hosts_path}" in capsys.readouterr().err


@posix_only
@pytest.mark.parametrize("mode", [0o770, 0o707, 0o777])
def test_hosts_file_in_a_directory_others_can_write_to_is_ignored(mode, capsys):
    """Whoever can write the directory can swap the file for one of their own."""
    hosts_path = _write_raw_hosts(json.dumps({"default_host": "https://kwaad.example"}))
    hosts_path.parent.chmod(mode)

    assert _resolved_host() == TEST_DEFAULT_HOST
    assert "in a directory only you can write to" in capsys.readouterr().err


@posix_only
def test_hosts_file_in_a_directory_of_someone_else_is_ignored(monkeypatch, capsys):
    hosts_path = _write_raw_hosts(json.dumps({"default_host": "https://kwaad.example"}))
    real_stat = Path.stat
    directory_uid = os.getuid() + 1

    def stat_with_foreign_directory(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self == hosts_path.parent:
            fields = list(result)
            fields[stat.ST_UID] = directory_uid
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(Path, "stat", stat_with_foreign_directory)

    assert _resolved_host() == TEST_DEFAULT_HOST
    assert "in a directory only you can write to" in capsys.readouterr().err


@posix_only
def test_hosts_path_that_cannot_be_opened_is_ignored(capsys):
    hosts_path = _write_raw_hosts("{}", 0o000)

    host = _resolved_host()

    hosts_path.chmod(0o600)
    assert host == TEST_DEFAULT_HOST
    assert f"ignoring {hosts_path}" in capsys.readouterr().err


@posix_only
def test_plain_text_session_in_an_untrusted_hosts_file_is_never_sent(
    stub_server, host, capsys
):
    """Whoever could write the file could have planted a session there, or
    read the one that was in it: neither gets used."""
    _write_raw_hosts(
        json.dumps(
            {
                "default_host": host,
                "hosts": {host: {"storage": "file", "access_token": "geplant", "refresh_token": "r"}},
            }
        ),
        0o644,
    )

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "plak login" in capsys.readouterr().err
    assert stub_server.requests == []


@pytest.mark.parametrize("content", ["{niet json", "[]", '"tekst"'])
def test_hosts_file_that_is_not_a_json_object_is_ignored(content):
    _write_raw_hosts(content)

    assert _resolved_host() == TEST_DEFAULT_HOST


def test_hosts_file_that_is_not_utf8_is_ignored_with_a_warning(capsys):
    _write_raw_hosts(b'{"default_host": "\xff\xfe"}')

    assert _resolved_host() == TEST_DEFAULT_HOST
    assert "not readable JSON" in capsys.readouterr().err


@posix_only
def test_a_directory_in_place_of_the_hosts_file_is_ignored(capsys):
    hosts_path = cli._hosts_path()
    hosts_path.mkdir(parents=True, mode=0o700)

    assert _resolved_host() == TEST_DEFAULT_HOST
    assert f"ignoring {hosts_path}: it must be a file of yours" in capsys.readouterr().err


@pytest.mark.parametrize("default_host", [42, "", None, ["https://kwaad.example"]])
def test_a_default_host_that_is_not_a_string_falls_back_to_the_built_in_one(default_host):
    _write_raw_hosts(json.dumps({"default_host": default_host}))

    assert _resolved_host() == TEST_DEFAULT_HOST


@pytest.mark.parametrize(
    "config",
    [
        {"hosts": ["niet", "een", "object"]},
        {"hosts": {"HOST": "geen object"}},
    ],
)
def test_odd_shapes_in_the_hosts_file_count_as_no_session(stub_server, host, config, capsys):
    raw = json.dumps(config).replace("HOST", host)
    _write_raw_hosts(raw)

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    error_output = capsys.readouterr().err
    assert "plak login" in error_output
    assert stub_server.requests == []


def test_config_dir_follows_plak_config_dir_then_xdg_then_home(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "WINDOWS", False)
    monkeypatch.setenv("PLAK_CONFIG_DIR", str(tmp_path / "eigen"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    # Path.home() reads USERPROFILE on Windows, HOME elsewhere.
    monkeypatch.setenv("HOME", str(tmp_path / "thuis"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "thuis"))
    assert cli._hosts_path() == tmp_path / "eigen" / "hosts.json"

    monkeypatch.delenv("PLAK_CONFIG_DIR")
    assert cli._hosts_path() == tmp_path / "xdg" / "plak" / "hosts.json"

    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert cli._hosts_path() == tmp_path / "thuis" / ".config" / "plak" / "hosts.json"


def test_config_dir_on_windows_follows_plak_config_dir_then_xdg_then_appdata(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cli, "WINDOWS", True)
    monkeypatch.setenv("PLAK_CONFIG_DIR", str(tmp_path / "eigen"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("HOME", str(tmp_path / "thuis"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "thuis"))
    assert cli._hosts_path() == tmp_path / "eigen" / "hosts.json"

    monkeypatch.delenv("PLAK_CONFIG_DIR")
    assert cli._hosts_path() == tmp_path / "xdg" / "plak" / "hosts.json"

    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert cli._hosts_path() == tmp_path / "roaming" / "plak" / "hosts.json"

    # A service account can run without APPDATA; it points here by default.
    monkeypatch.delenv("APPDATA")
    assert cli._hosts_path() == tmp_path / "thuis" / "AppData" / "Roaming" / "plak" / "hosts.json"


@pytest.fixture
def windows(monkeypatch):
    """The Windows path on any platform: without the POSIX-only parts of os
    that broke the CLI there (#65), so touching one fails here too."""
    monkeypatch.setattr(cli, "WINDOWS", True)
    monkeypatch.delattr(cli.os, "O_NOFOLLOW", raising=False)
    monkeypatch.delattr(cli.os, "getuid", raising=False)


def test_on_windows_the_hosts_file_is_trusted_on_its_location(windows, capsys):
    """NTFS has no mode bits or uid to check; the profile ACL protects
    %APPDATA%. What a POSIX check would refuse is read."""
    hosts_path = _write_raw_hosts(json.dumps({"default_host": "https://laatst.example"}), 0o644)
    hosts_path.parent.chmod(0o777)

    assert _resolved_host() == "https://laatst.example"
    assert capsys.readouterr().err == ""


def test_on_windows_a_session_round_trips_through_the_hosts_file(windows):
    _store_session("https://beheer.example", insecure=True)

    assert _stored_tokens("https://beheer.example") == ("access-1", "refresh-1")
    assert _resolved_host() == "https://beheer.example"


def test_on_windows_a_directory_in_place_of_the_hosts_file_is_ignored(windows, capsys):
    hosts_path = cli._hosts_path()
    hosts_path.mkdir(parents=True)

    assert _resolved_host() == TEST_DEFAULT_HOST
    error_output = capsys.readouterr().err
    assert f"ignoring {hosts_path}: it must be a regular file you can read" in error_output
    assert "mode 0600" not in error_output


def test_sessions_for_two_hosts_live_side_by_side(memory_keyring):
    _store_session("https://een.example", access="access-een")
    _store_session("https://twee.example", access="access-twee")

    assert _stored_tokens("https://een.example") == ("access-een", "refresh-1")
    assert _stored_tokens("https://twee.example") == ("access-twee", "refresh-1")
    # The host logged in to last is the default.
    assert cli._read_hosts()["default_host"] == "https://twee.example"
    assert set(memory_keyring.secrets) == {
        ("plak:https://een.example", "session"),
        ("plak:https://twee.example", "session"),
    }


# --- the system keyring: missing, refusing, emptied ------------------------------


def _login_answers(stub_server, host: str) -> None:
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [_device_start_step(host)],
            "/-/api/v1/cli/tokens": [
                _json_step(
                    200, {"accessToken": "access-1", "refreshToken": "refresh-1", "expiresIn": 3600}
                )
            ],
        }
    )


def test_login_without_a_system_keyring_falls_back_to_the_file_and_says_so(
    stub_server, host, memory_keyring, capsys
):
    memory_keyring.error = keyring.errors.NoKeyringError("install keyrings.alt")
    _login_answers(stub_server, host)

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    error_output = capsys.readouterr().err
    assert (
        f"Warning: no system keyring found; the session is stored in plain text in "
        f"{cli._hosts_path()}." in error_output
    )
    # Its advice to install keyrings.alt is not passed on.
    assert "keyrings.alt" not in error_output
    entry = cli._read_hosts()["hosts"][host]
    assert entry["storage"] == "file"
    if not cli.WINDOWS:
        assert stat.S_IMODE(cli._hosts_path().stat().st_mode) == 0o600
    assert _stored_tokens(host) == ("access-1", "refresh-1")


def test_login_with_a_refusing_keyring_falls_back_to_the_file_and_names_why(
    stub_server, host, memory_keyring, capsys
):
    memory_keyring.error = keyring.errors.KeyringLocked("keychain is vergrendeld")
    _login_answers(stub_server, host)

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    assert (
        "Warning: the system keyring refused: keychain is vergrendeld; the session is "
        "stored in plain text" in capsys.readouterr().err
    )
    assert cli._read_hosts()["hosts"][host]["storage"] == "file"


def test_login_with_insecure_storage_skips_the_keyring(stub_server, host, memory_keyring, capsys):
    _login_answers(stub_server, host)

    code = cli.main(["login", "--host", host, "--no-open", "--insecure-storage"])

    assert code == 0
    error_output = capsys.readouterr().err
    assert f"Session stored in plain text in {cli._hosts_path()}." in error_output
    assert "Warning" not in error_output
    assert memory_keyring.secrets == {}
    assert _stored_tokens(host) == ("access-1", "refresh-1")


def test_a_plain_text_session_is_refreshed_into_the_file_again(stub_server, host, memory_keyring):
    _store_session(host, access="expired-access", refresh="refresh-old", expires_at=1.0, insecure=True)
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/tokens": [
                _json_step(200, {"accessToken": "fresh-access", "refreshToken": "refresh-new", "expiresIn": 3600})
            ],
            "/-/api/v1/cli/whoami": [_json_step(200, {"member": {"email": "iemand@example.nl"}})],
        }
    )

    code = cli.main(["whoami", "--host", host])

    assert code == 0
    assert memory_keyring.secrets == {}
    assert cli._read_hosts()["hosts"][host]["storage"] == "file"
    assert _stored_tokens(host) == ("fresh-access", "refresh-new")


def test_a_refresh_the_keyring_refuses_to_store_warns_that_it_went_to_the_file(
    stub_server, host, memory_keyring, capsys
):
    """The server has already rotated the refresh token, so the new pair is
    kept in the file; the move out of the keyring is never silent, and the
    stale keyring entry does not stay behind."""
    _store_session(host, access="expired-access", refresh="refresh-old", expires_at=1.0)
    memory_keyring.set_error = keyring.errors.KeyringLocked("keychain is vergrendeld")
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/tokens": [
                _json_step(200, {"accessToken": "fresh-access", "refreshToken": "refresh-new", "expiresIn": 3600})
            ],
            "/-/api/v1/cli/whoami": [_json_step(200, {"member": {"email": "iemand@example.nl"}})],
        }
    )

    code = cli.main(["whoami", "--host", host])

    assert code == 0
    assert (
        "Warning: the system keyring refused: keychain is vergrendeld; the session is "
        f"stored in plain text in {cli._hosts_path()}." in capsys.readouterr().err
    )
    assert cli._read_hosts()["hosts"][host]["storage"] == "file"
    assert _stored_tokens(host) == ("fresh-access", "refresh-new")
    assert memory_keyring.secrets == {}


def test_login_with_insecure_storage_removes_an_earlier_keyring_entry(
    stub_server, host, memory_keyring
):
    _store_session(host, access="oud-access", refresh="oud-refresh")
    _login_answers(stub_server, host)

    code = cli.main(["login", "--host", host, "--no-open", "--insecure-storage"])

    assert code == 0
    assert memory_keyring.secrets == {}
    assert _stored_tokens(host) == ("access-1", "refresh-1")


@pytest.mark.parametrize(
    "backend",
    [keyring.backends.null.Keyring, keyring.backends.fail.Keyring],
    ids=["null", "fail"],
)
def test_a_backend_that_is_no_system_keyring_is_not_trusted_with_the_session(
    stub_server, host, backend, capsys
):
    """PYTHON_KEYRING_BACKEND can select the null backend, which drops a
    secret without an error: 'stored in the keyring' would be a lie."""
    keyring.set_keyring(backend())
    _login_answers(stub_server, host)

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    error_output = capsys.readouterr().err
    assert "Warning: no system keyring found; the session is stored in plain text" in error_output
    assert "system keyring." not in error_output
    assert _stored_tokens(host) == ("access-1", "refresh-1")


def test_a_session_that_fell_back_to_the_file_returns_to_the_keyring_on_refresh(
    stub_server, host, memory_keyring, capsys
):
    memory_keyring.set_error = keyring.errors.KeyringLocked("keychain is vergrendeld")
    _store_session(host, access="expired-access", refresh="refresh-old", expires_at=1.0)
    assert cli._read_hosts()["hosts"][host]["storage"] == "file"
    memory_keyring.set_error = None
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/tokens": [
                _json_step(200, {"accessToken": "fresh-access", "refreshToken": "refresh-new", "expiresIn": 3600})
            ],
            "/-/api/v1/cli/whoami": [_json_step(200, {"member": {"email": "iemand@example.nl"}})],
        }
    )

    code = cli.main(["whoami", "--host", host])

    assert code == 0
    assert "Warning" not in capsys.readouterr().err
    assert cli._read_hosts()["hosts"][host]["storage"] == "keyring"
    assert "fresh-access" not in cli._hosts_path().read_text()
    assert _stored_tokens(host) == ("fresh-access", "refresh-new")


def test_a_file_session_whose_refresh_still_finds_no_keyring_stays_quiet(
    stub_server, host, memory_keyring, capsys
):
    """Not a move out of the keyring, so no warning on every hourly refresh."""
    memory_keyring.set_error = keyring.errors.NoKeyringError("geen")
    _store_session(host, access="expired-access", refresh="refresh-old", expires_at=1.0)
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/tokens": [
                _json_step(200, {"accessToken": "fresh-access", "refreshToken": "refresh-new", "expiresIn": 3600})
            ],
            "/-/api/v1/cli/whoami": [_json_step(200, {"member": {"email": "iemand@example.nl"}})],
        }
    )

    code = cli.main(["whoami", "--host", host])

    assert code == 0
    assert "Warning" not in capsys.readouterr().err
    assert _stored_tokens(host) == ("fresh-access", "refresh-new")


def test_logout_warns_when_the_keyring_refuses_the_delete_and_keeps_the_entry(
    stub_server, host, memory_keyring, capsys
):
    """The macOS backend raises PasswordDeleteError for a refused delete as
    well as for a missing entry; an entry that is still there is a failure."""
    _store_session(host)
    memory_keyring.delete_error = keyring.errors.PasswordDeleteError("User canceled the operation")
    stub_server.responder = _empty_responder(204)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    assert (
        "Warning: could not remove the session from the system keyring: User canceled the operation"
        in capsys.readouterr().err
    )


def test_logout_warns_when_a_refused_delete_cannot_be_checked_either(
    stub_server, host, memory_keyring, capsys
):
    _store_session(host)
    memory_keyring.delete_error = keyring.errors.PasswordDeleteError("User canceled the operation")
    memory_keyring.get_error = keyring.errors.KeyringLocked("keychain is vergrendeld")

    code = cli.main(["logout", "--host", host])

    assert code == 0
    assert "could not remove the session from the system keyring" in capsys.readouterr().err
    assert stub_server.requests == []


@pytest.mark.parametrize(
    "command",
    [
        ["whoami"],
        ["publish", "DIST", "--site", "team-aurora/website"],
        ["preview-remove", "pr-1", "--site", "team-aurora/website"],
        ["group", "create", "team", "--name", "Team"],
        ["site", "create", "team/docs", "--title", "Docs"],
    ],
    ids=["whoami", "publish", "preview-remove", "group-create", "site-create"],
)
def test_a_session_is_never_sent_to_another_host(stub_server, host, dist_folder, command, capsys):
    """localhost and 127.0.0.1 reach the same stub, but are different hosts:
    the session stored for one never goes to the other."""
    _store_session(host)
    other_host = host.replace("127.0.0.1", "localhost")
    argv = [str(dist_folder) if part == "DIST" else part for part in command]

    code = cli.main([*argv, "--host", other_host])

    assert code == 2
    assert "plak login" in capsys.readouterr().err
    assert stub_server.requests == []


def test_a_trailing_slash_on_the_host_finds_the_same_session(stub_server, host):
    _login_answers(stub_server, host)
    assert cli.main(["login", "--host", f"{host}/", "--no-open"]) == 0
    stub_server.responder = _json_responder(200, {"member": {"email": "iemand@example.nl"}})

    assert cli.main(["whoami", "--host", host]) == 0
    assert cli.main(["whoami", "--host", f"{host}/"]) == 0
    assert cli._read_hosts()["default_host"] == host
    assert [r["headers"]["Authorization"] for r in stub_server.requests[-2:]] == [
        "Bearer access-1",
        "Bearer access-1",
    ]


def test_a_keyring_that_fails_on_reading_gives_exit_2_and_sends_nothing(
    stub_server, host, memory_keyring, capsys
):
    _store_session(host)
    memory_keyring.error = keyring.errors.KeyringLocked("keychain is vergrendeld")

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert (
        "Could not read the session from the system keyring: keychain is vergrendeld"
        in capsys.readouterr().err
    )
    assert stub_server.requests == []


@pytest.mark.parametrize("secret", [None, "{kapot", '["geen", "object"]', '{"access_token": 42}'])
def test_a_keyring_entry_that_is_gone_or_garbled_counts_as_no_session(
    stub_server, host, memory_keyring, secret, capsys
):
    _store_session(host)
    service = (f"plak:{host}", "session")
    if secret is None:
        del memory_keyring.secrets[service]
    else:
        memory_keyring.secrets[service] = secret

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "plak login" in capsys.readouterr().err
    assert stub_server.requests == []


def test_logout_with_an_unreadable_keyring_still_forgets_locally(
    stub_server, host, memory_keyring, capsys
):
    _store_session(host)
    memory_keyring.error = keyring.errors.KeyringLocked("keychain is vergrendeld")

    code = cli.main(["logout", "--host", host])

    assert code == 0
    out = capsys.readouterr()
    assert "Warning: Could not read the session from the system keyring" in out.err
    assert "Warning: could not remove the session from the system keyring" in out.err
    assert "Logged out." in out.out
    assert stub_server.requests == []
    assert host not in cli._read_hosts()["hosts"]


def test_logout_when_the_keyring_entry_is_already_gone_stays_quiet(
    stub_server, host, memory_keyring, capsys
):
    _store_session(host)
    memory_keyring.secrets.clear()

    code = cli.main(["logout", "--host", host])

    assert code == 0
    assert "Warning" not in capsys.readouterr().err
    assert stub_server.requests == []
    assert host not in cli._read_hosts()["hosts"]


def test_logout_when_the_keyring_errors_on_delete_warns_and_forgets_locally(
    stub_server, host, memory_keyring, capsys
):
    _store_session(host)
    memory_keyring.delete_error = keyring.errors.KeyringLocked("keychain is vergrendeld")
    stub_server.responder = _empty_responder(204)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    assert (
        "Warning: could not remove the session from the system keyring: keychain is vergrendeld"
        in capsys.readouterr().err
    )
    assert host not in cli._read_hosts()["hosts"]


def test_logout_of_a_plain_text_session_removes_the_tokens_from_the_file(stub_server, host):
    _store_session(host, insecure=True)
    stub_server.responder = _empty_responder(204)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    assert stub_server.requests[0]["headers"]["Authorization"] == "Bearer access-1"
    assert "access-1" not in cli._hosts_path().read_text()


# --- a leftover .env.plak -----------------------------------------------------------


LEGACY_NOTE = "plak no longer reads .env.plak in this directory"


def test_login_points_at_a_leftover_env_file(stub_server, host, isolated_cwd, capsys):
    (isolated_cwd / ".env.plak").write_text("PLAK_ACCESS_TOKEN=oud\n")
    _login_answers(stub_server, host)

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    assert LEGACY_NOTE in capsys.readouterr().err


def test_a_leftover_env_file_never_supplies_the_host(stub_server, isolated_cwd, capsys):
    (isolated_cwd / ".env.plak").write_text("PLAK_HOST=https://kwaad.example\n")

    assert _resolved_host() == TEST_DEFAULT_HOST
    code = cli.main(["whoami"])

    assert code == 2
    assert LEGACY_NOTE in capsys.readouterr().err
    assert stub_server.requests == []


def test_a_leftover_env_file_is_named_when_there_is_no_token(
    stub_server, host, isolated_cwd, capsys
):
    (isolated_cwd / ".env.plak").write_text(f"PLAK_HOST={host}\nPLAK_ACCESS_TOKEN=oud\n")

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert LEGACY_NOTE in capsys.readouterr().err
    assert stub_server.requests == []


def test_a_leftover_env_file_is_named_when_there_is_no_member_session(
    stub_server, host, isolated_cwd, capsys
):
    (isolated_cwd / ".env.plak").write_text(f"PLAK_HOST={host}\nPLAK_ACCESS_TOKEN=oud\n")

    code = cli.main(["group", "create", "team", "--name", "Team", "--host", host])

    assert code == 2
    assert LEGACY_NOTE in capsys.readouterr().err
    assert stub_server.requests == []


def test_without_a_leftover_env_file_there_is_no_note(stub_server, host, isolated_cwd, capsys):
    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert LEGACY_NOTE not in capsys.readouterr().err


# --- plak logout: refresh token in the body, clear reporting ----------------


def test_logout_sends_the_refresh_token_in_the_body(stub_server, host, isolated_cwd):
    _store_session(host, access="", refresh="refresh-only", expires_at=1.0)
    stub_server.responder = _empty_responder(204)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    record = stub_server.requests[0]
    assert record["method"] == "DELETE"
    assert "Authorization" not in record["headers"]
    body = json.loads(record["body"])
    assert body == {"refreshToken": "refresh-only"}


def test_logout_sends_both_the_bearer_and_the_refresh_token_when_both_are_present(
    stub_server, host, isolated_cwd
):
    _store_session(host)
    stub_server.responder = _empty_responder(204)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    record = stub_server.requests[0]
    assert record["headers"]["Authorization"] == "Bearer access-1"
    assert json.loads(record["body"]) == {"refreshToken": "refresh-1"}


def test_logout_reports_a_failed_server_revoke_but_still_clears_locally(
    stub_server, host, isolated_cwd, capsys
):
    _store_session(host)
    stub_server.responder = _json_responder(401, {"status": 401, "detail": "ongeldig"})

    code = cli.main(["logout", "--host", host])

    assert code == 0
    out = capsys.readouterr()
    assert "could not revoke the session" in out.err
    assert "Logged out." in out.out
    assert _stored_tokens(host) == (None, None)


def test_logout_of_another_host_leaves_this_session_alone_and_sends_nothing(
    stub_server, host, isolated_cwd, memory_keyring, capsys
):
    """A session is only ever sent to the host it was issued for: logging out
    of another host never sends this host's tokens there."""
    _store_session(host)

    code = cli.main(["logout", "--host", "https://evil.example"])

    assert code == 0
    assert stub_server.requests == []
    assert "Logged out." in capsys.readouterr().out
    assert _stored_tokens(host) == ("access-1", "refresh-1")


@posix_only
def test_logout_with_an_untrusted_hosts_file_sends_nothing(stub_server, host, capsys):
    _write_raw_hosts(
        json.dumps(
            {
                "default_host": host,
                "hosts": {host: {"storage": "file", "access_token": "a", "refresh_token": "r"}},
            }
        ),
        0o644,
    )

    code = cli.main(["logout", "--host", host])

    assert code == 0
    assert stub_server.requests == []
    assert "ignoring" in capsys.readouterr().err


# --- error paths: network failures, odd server answers, refusals ------------


def _raise_connect_error(*_args, **_kwargs):
    raise httpx.ConnectError("verbinding geweigerd")


def _device_start_step(host: str, **overrides: Any):
    data: dict[str, Any] = {
        "deviceCode": "devicecode-1",
        "userCode": "ABCD-1234",
        "verificationUri": f"{host}/-/device",
        "expiresIn": 60,
        "interval": 0,
    }
    data.update(overrides)
    return _json_step(200, data)


def test_https_host_is_accepted_without_complaint():
    cli._require_https("https://plak.example.nl")


def test_python_dash_m_entry_point_uses_the_same_main():
    import plak_cli.__main__ as entry

    assert entry.main is cli.main


def test_publish_connection_failure_gives_exit_1(
    stub_server, host, dist_folder, token_env, monkeypatch, capsys
):
    monkeypatch.setattr(cli.httpx, "post", _raise_connect_error)

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 1
    assert "could not connect to" in capsys.readouterr().err
    assert stub_server.requests == []


def test_publish_201_without_version_id_gives_exit_1(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = _json_responder(201, {"iets": "anders"})

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 1
    out = capsys.readouterr()
    assert "unexpected answer without versionId" in out.err
    assert out.out == ""


def test_publish_writes_the_version_id_to_the_output_file_instead_of_stdout(
    stub_server, host, dist_folder, token_env, tmp_path, capsys
):
    output_file = tmp_path / "github_output.txt"
    output_file.write_text("eerder=1\n")

    code = cli.main(
        [
            "publish",
            str(dist_folder),
            "--host",
            host,
            "--site",
            "team-aurora/website",
            "--output-file",
            str(output_file),
        ]
    )

    assert code == 0
    assert capsys.readouterr().out == ""
    # Appended, never truncated: $GITHUB_OUTPUT carries other steps' outputs.
    assert output_file.read_text() == (
        "eerder=1\nversion-id=00000000-0000-0000-0000-000000000000\n"
    )


def test_publish_writes_the_url_beside_the_version_id(
    stub_server, host, dist_folder, token_env, tmp_path
):
    stub_server.responder = _json_responder(
        201,
        {
            "versionId": "00000000-0000-0000-0000-000000000000",
            "url": "https://plak.example/team-aurora/website/_preview/pr-42/",
        },
    )
    output_file = tmp_path / "github_output.txt"

    code = cli.main(
        [
            "publish",
            str(dist_folder),
            "--host",
            host,
            "--site",
            "team-aurora/website",
            "--preview",
            "pr-42",
            "--output-file",
            str(output_file),
        ]
    )

    assert code == 0
    assert output_file.read_text() == (
        "version-id=00000000-0000-0000-0000-000000000000\n"
        "url=https://plak.example/team-aurora/website/_preview/pr-42/\n"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://plak.example/\nversion-id=evil",
        "https://plak.example/a b/",
        "javascript:alert(1)",
        42,
    ],
)
def test_publish_refuses_a_url_that_could_break_out_of_the_output_file(
    stub_server, host, dist_folder, token_env, tmp_path, capsys, url
):
    stub_server.responder = _json_responder(
        201, {"versionId": "00000000-0000-0000-0000-000000000000", "url": url}
    )
    output_file = tmp_path / "github_output.txt"

    code = cli.main(
        [
            "publish",
            str(dist_folder),
            "--host",
            host,
            "--site",
            "team-aurora/website",
            "--output-file",
            str(output_file),
        ]
    )

    assert code == 1
    assert "unexpected url format" in capsys.readouterr().err
    assert not output_file.exists()


def test_publish_reports_an_unwritable_output_file_after_a_successful_publish(
    stub_server, host, dist_folder, token_env, tmp_path, capsys
):
    """The version already exists on the server by the time the write fails:
    losing it silently, or a raw traceback with an undocumented exit code, is
    worse than a clear exit 1 naming the version."""
    code = cli.main(
        [
            "publish",
            str(dist_folder),
            "--host",
            host,
            "--site",
            "team-aurora/website",
            "--output-file",
            str(tmp_path / "geen" / "zo'n-map" / "output.txt"),
        ]
    )

    assert code == 1
    error_output = capsys.readouterr().err
    assert "00000000-0000-0000-0000-000000000000" in error_output
    assert "publish succeeded" in error_output


def test_publish_non_json_error_body_is_shown_as_the_detail(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = lambda _record: (502, b"Bad gateway van de proxy", "text/plain")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 1
    assert "Error: Bad gateway van de proxy" in capsys.readouterr().err


def test_publish_empty_error_body_falls_back_to_the_http_status(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = _empty_responder(500)

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 1
    assert "Error: HTTP 500" in capsys.readouterr().err


def test_publish_skips_a_git_folder_inside_the_dist_folder(
    stub_server, host, dist_folder, token_env
):
    (dist_folder / ".git").mkdir()
    (dist_folder / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 0
    fields = _parse_multipart(
        stub_server.requests[0]["headers"]["Content-Type"], stub_server.requests[0]["body"]
    )
    with tarfile.open(fileobj=io.BytesIO(fields["file"]["content"]), mode="r:gz") as tar:
        names = tar.getnames()
    assert not any(name.startswith(".git") for name in names)
    assert "index.html" in names


def test_publish_empty_folder_gives_exit_2_without_a_request(
    stub_server, host, tmp_path, token_env, capsys
):
    empty = tmp_path / "leeg"
    (empty / "alleen-een-submap").mkdir(parents=True)

    code = cli.main(["publish", str(empty), "--host", host, "--site", "team-aurora/website"])

    assert code == 2
    assert stub_server.requests == []
    assert "Folder is empty" in capsys.readouterr().err


def test_preview_remove_connection_failure_gives_exit_1(
    stub_server, host, token_env, monkeypatch, capsys
):
    monkeypatch.setattr(cli.httpx, "delete", _raise_connect_error)

    code = cli.main(["preview-remove", "pr-42", "--host", host, "--site", "team-aurora/website"])

    assert code == 1
    assert "could not connect to" in capsys.readouterr().err
    assert stub_server.requests == []


def test_whoami_connection_failure_gives_exit_1(
    stub_server, host, token_env, monkeypatch, capsys
):
    monkeypatch.setattr(cli.httpx, "get", _raise_connect_error)

    code = cli.main(["whoami", "--host", host])

    assert code == 1
    assert "could not connect to" in capsys.readouterr().err


def test_whoami_with_a_rejected_token_shows_the_detail_and_gives_exit_1(
    stub_server, host, token_env, capsys
):
    stub_server.responder = _json_responder(
        401, {"status": 401, "detail": "Token is ongeldig of ingetrokken"}
    )

    code = cli.main(["whoami", "--host", host])

    assert code == 1
    assert "Token is ongeldig of ingetrokken" in capsys.readouterr().err


# --- stored session: missing token, odd expiry, refresh failures ------------


def test_stored_host_without_a_token_asks_to_log_in(stub_server, host, isolated_cwd, capsys):
    _store_session(host, access="", refresh="")

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "plak login" in capsys.readouterr().err
    assert stub_server.requests == []


@pytest.mark.parametrize("expiry", ["onbekend", True, None, _MISSING])
def test_stored_session_with_an_expiry_that_is_not_a_number_is_used_as_is(
    stub_server, host, isolated_cwd, expiry, capsys
):
    _store_session(host)
    _set_entry_field(host, "access_expires_at", expiry)
    stub_server.responder = _json_responder(200, {"member": {"email": "iemand@example.nl"}})

    code = cli.main(["whoami", "--host", host])

    assert code == 0
    assert [r["path"] for r in stub_server.requests] == ["/-/api/v1/cli/whoami"]
    assert stub_server.requests[0]["headers"]["Authorization"] == "Bearer access-1"


def test_expired_session_without_a_refresh_token_asks_to_log_in(
    stub_server, host, isolated_cwd, capsys
):
    _store_session(host, access="expired-access", refresh="", expires_at=1.0)

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "Session expired" in capsys.readouterr().err
    assert stub_server.requests == []


def test_refresh_connection_failure_gives_exit_2(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    _store_session(host, access="expired-access", refresh="refresh-old", expires_at=1.0)
    monkeypatch.setattr(cli.httpx, "post", _raise_connect_error)

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "Could not refresh the session" in capsys.readouterr().err
    assert stub_server.requests == []
    # The old session stays put: nothing was exchanged, nothing gets clobbered.
    assert _stored_tokens(host) == ("expired-access", "refresh-old")


def test_refresh_with_a_non_json_answer_gives_exit_2(stub_server, host, isolated_cwd, capsys):
    _store_session(host, access="expired-access", refresh="refresh-old", expires_at=1.0)
    stub_server.responder = lambda _record: (200, b"<html>geen json</html>", "text/html")

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "Unexpected answer while refreshing" in capsys.readouterr().err
    assert [r["path"] for r in stub_server.requests] == ["/-/api/v1/cli/tokens"]
    assert _stored_tokens(host) == ("expired-access", "refresh-old")


# --- OIDC in CI: the runner endpoint misbehaves ------------------------------


def test_oidc_connection_failure_gives_exit_2_without_a_deploy(
    stub_server, host, dist_folder, isolated_cwd, monkeypatch, capsys
):
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", f"{host}/oidc-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-bearer")
    monkeypatch.setattr(cli.httpx, "get", _raise_connect_error)

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 2
    assert "Could not fetch an OIDC token" in capsys.readouterr().err
    assert stub_server.requests == []


def test_oidc_endpoint_refusal_explains_the_missing_permission(
    stub_server, host, dist_folder, isolated_cwd, monkeypatch, capsys
):
    stub_server.responder = _json_responder(403, {"message": "Resource not accessible"})
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", f"{host}/oidc-token?api-version=2")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-bearer")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 2
    error_output = capsys.readouterr().err
    assert "HTTP 403" in error_output
    assert "id-token: write" in error_output
    assert "enable-openid-connect" in error_output
    # An existing query string is extended, not replaced.
    assert stub_server.requests[0]["path"].startswith("/oidc-token?api-version=2&audience=")
    assert len(stub_server.requests) == 1


def test_oidc_answer_without_a_value_gives_exit_2(
    stub_server, host, dist_folder, isolated_cwd, monkeypatch, capsys
):
    stub_server.responder = _json_responder(200, {"count": 0})
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", f"{host}/oidc-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-bearer")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "team-aurora/website"])

    assert code == 2
    out = capsys.readouterr()
    assert "Unexpected answer while fetching the OIDC token" in out.err
    assert "::add-mask::" not in out.out
    assert len(stub_server.requests) == 1


# --- plak login: the start call and the polling loop go wrong ---------------


def test_login_connection_failure_at_the_start_gives_exit_1(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    monkeypatch.setattr(cli.httpx, "post", _raise_connect_error)

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 1
    assert "could not connect to" in capsys.readouterr().err
    assert not cli._hosts_path().exists()


def test_login_refused_start_shows_the_detail_and_gives_exit_1(
    stub_server, host, isolated_cwd, capsys
):
    stub_server.responder = _json_responder(
        503, {"status": 503, "detail": "Aanmelden is tijdelijk uitgeschakeld"}
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 1
    assert "Aanmelden is tijdelijk uitgeschakeld" in capsys.readouterr().err
    assert len(stub_server.requests) == 1


def test_login_start_without_a_device_code_gives_exit_1(
    stub_server, host, isolated_cwd, capsys
):
    stub_server.responder = _json_responder(
        200, {"userCode": "ABCD-1234", "verificationUri": f"{host}/-/device"}
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 1
    assert "unexpected answer while starting" in capsys.readouterr().err
    assert len(stub_server.requests) == 1


def test_login_rejects_a_verification_uri_that_does_not_even_parse(
    stub_server, host, isolated_cwd, capsys
):
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [
                _device_start_step(host, verificationUri="http://[::1/-/device")
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 1
    error_output = capsys.readouterr().err
    assert "does not belong to" in error_output
    assert "[::1/-/device" not in error_output


def test_login_unreadable_interval_falls_back_to_five_seconds(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    sleeps: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [
                _device_start_step(host, interval="traag", expiresIn="lang")
            ],
            "/-/api/v1/cli/tokens": [
                _json_step(200, {"accessToken": "access-1", "expiresIn": 3600})
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    assert sleeps == [5.0]


def test_login_gives_up_when_the_device_code_expires(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    # One poll fits inside the window; the clock then jumps past the deadline.
    clock = iter([0.0, 0.0, 100.0])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [
                _device_start_step(host, expiresIn=30, interval=7)
            ],
            "/-/api/v1/cli/tokens": [
                _json_step(400, {"status": 400, "code": "AUTHORIZATION_PENDING"})
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 1
    assert "expired before it was approved" in capsys.readouterr().err
    tokens_requests = [r for r in stub_server.requests if r["path"] == "/-/api/v1/cli/tokens"]
    assert len(tokens_requests) == 1
    assert not cli._hosts_path().exists()


def test_login_connection_failure_while_polling_gives_exit_1(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    real_post = cli.httpx.post

    def flaky_post(url, *args, **kwargs):
        if url.endswith("/-/api/v1/cli/tokens"):
            _raise_connect_error()
        return real_post(url, *args, **kwargs)

    monkeypatch.setattr(cli.httpx, "post", flaky_post)
    stub_server.responder = _sequence_responder(
        {"/-/api/v1/cli/device-authorizations": [_device_start_step(host)]}
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 1
    assert "could not connect to" in capsys.readouterr().err
    assert not cli._hosts_path().exists()


def test_login_token_answer_without_an_access_token_gives_exit_1(
    stub_server, host, isolated_cwd, capsys
):
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [_device_start_step(host)],
            "/-/api/v1/cli/tokens": [_json_step(200, {"tokenType": "Bearer"})],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 1
    assert "unexpected answer while fetching the token" in capsys.readouterr().err
    assert not cli._hosts_path().exists()


def test_login_unreadable_expires_in_stores_a_session_that_is_already_due(
    stub_server, host, isolated_cwd, capsys
):
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [_device_start_step(host)],
            "/-/api/v1/cli/tokens": [
                _json_step(
                    200,
                    {"accessToken": "access-1", "refreshToken": "refresh-1", "expiresIn": "lang"},
                )
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    assert "Logged in." in capsys.readouterr().out
    assert _stored_tokens(host) == ("access-1", "refresh-1")
    assert cli._read_hosts()["hosts"][host]["access_expires_at"] <= cli.time.time()


def test_login_opens_the_browser_on_a_terminal(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    opened: list[str] = []
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [_device_start_step(host)],
            "/-/api/v1/cli/tokens": [
                _json_step(200, {"accessToken": "access-1", "expiresIn": 3600})
            ],
        }
    )

    code = cli.main(["login", "--host", host])

    assert code == 0
    assert opened == [f"{host}/-/device"]
    assert "Browser opened" in capsys.readouterr().err


def test_login_reports_when_no_browser_can_be_opened(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: False)
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [_device_start_step(host)],
            "/-/api/v1/cli/tokens": [
                _json_step(200, {"accessToken": "access-1", "expiresIn": 3600})
            ],
        }
    )

    code = cli.main(["login", "--host", host])

    assert code == 0
    error_output = capsys.readouterr().err
    assert "Could not open a browser" in error_output
    assert f"{host}/-/device" in error_output


def test_login_does_not_open_a_browser_with_no_open_even_on_a_terminal(
    stub_server, host, isolated_cwd, monkeypatch
):
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(
        cli.webbrowser, "open", lambda url: pytest.fail("browser mag niet geopend worden")
    )
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [_device_start_step(host)],
            "/-/api/v1/cli/tokens": [
                _json_step(200, {"accessToken": "access-1", "expiresIn": 3600})
            ],
        }
    )

    assert cli.main(["login", "--host", host, "--no-open"]) == 0


# --- hosts.json: writing it --------------------------------------------------


def test_hosts_file_write_failure_leaves_the_old_file_and_no_temp_file(monkeypatch):
    _store_session("https://beheer.example")
    hosts_path = cli._hosts_path()
    before = hosts_path.read_text()

    def failing_replace(_src, _dst):
        raise OSError("schijf vol")

    monkeypatch.setattr(cli.os, "replace", failing_replace)

    with pytest.raises(OSError, match="schijf vol"):
        _store_session("https://ander.example")

    assert hosts_path.read_text() == before
    assert [p.name for p in hosts_path.parent.iterdir()] == ["hosts.json"]


def test_a_token_with_a_line_break_cannot_change_the_default_host():
    """A server chooses the tokens. Stored as JSON, a line break or quote in
    one stays inside its own value and never becomes a key of its own."""
    token = 'geldig\n"default_host": "https://kwaad.example",\r'
    _store_session("https://beheer.example", access=token, refresh=token, insecure=True)

    assert cli._read_hosts()["default_host"] == "https://beheer.example"
    assert _stored_tokens("https://beheer.example") == (token, token)


# --- plak logout: no host, server unreachable --------------------------------


def test_logout_refuses_http_to_the_network(stub_server, capsys):
    code = cli.main(["logout", "--host", "http://plak.example.nl"])

    assert code == 2
    assert "Use https://" in capsys.readouterr().err
    assert stub_server.requests == []


def test_logout_without_a_host_or_session_sends_nothing(stub_server, isolated_cwd, capsys):
    code = cli.main(["logout"])

    assert code == 0
    assert "Logged out." in capsys.readouterr().out
    assert stub_server.requests == []
    assert not cli._hosts_path().exists()


def test_logout_with_an_unreachable_server_still_clears_locally(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    _store_session(host)
    monkeypatch.setattr(cli.httpx, "request", _raise_connect_error)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    out = capsys.readouterr()
    assert "could not revoke the session at the server" in out.err
    assert "Logged out." in out.out
    assert _stored_tokens(host) == (None, None)
    assert cli._read_hosts()["default_host"] == host


def test_the_mask_is_skipped_when_stdout_is_a_file(tmp_path, capfd):
    """Redirected to a file (`> log.txt`) the runner never reads the workflow
    command, so printing it would only write the token into that file."""
    import os as _os
    import sys as _sys

    target = tmp_path / "log.txt"
    saved = _os.dup(1)
    try:
        with target.open("w") as handle:
            _os.dup2(handle.fileno(), 1)
            stdout, _sys.stdout = _sys.stdout, _os.fdopen(_os.dup(1), "w")
            try:
                cli._mask_in_ci_log("super-geheim-token")
                _sys.stdout.flush()
            finally:
                _sys.stdout.close()
                _sys.stdout = stdout
    finally:
        _os.dup2(saved, 1)
        _os.close(saved)
    assert "super-geheim-token" not in target.read_text()


# --- group create / site create ---------------------------------------------


def _group_answer(base: str = "site_team", keys: bool = False, invitees: bool = False, **extra: Any) -> dict:
    access = {"base": base, "keys": keys, "invitees": invitees}
    return {"slug": "team", "name": "Team", "defaultAccess": access} | extra


def _site_answer(base: str = "site_team", keys: bool = False, invitees: bool = False, **extra: Any) -> dict:
    return {
        "groupSlug": "team",
        "slug": "docs",
        "title": "Docs",
        "access": {"base": base, "keys": keys, "invitees": invitees},
    } | extra


def test_group_create_sends_only_slug_and_name_without_access_flags(stub_server, host, token_env, capsys):
    stub_server.responder = _json_responder(201, _group_answer())

    code = cli.main(["group", "create", "team", "--name", "Team", "--host", host])

    assert code == 0
    record = stub_server.requests[0]
    assert record["method"] == "POST"
    assert record["path"] == "/-/api/v1/groups"
    assert record["headers"]["Authorization"] == "Bearer tok"
    assert json.loads(record["body"]) == {"slug": "team", "name": "Team"}
    assert capsys.readouterr().out.splitlines() == [
        "Created group 'team' (Team). You are its admin.",
        (
            "Default access for new sites: site_team (members of the site and its group), "
            "secret links off, invitees off."
        ),
        "Who can see it: members of the site and its group.",
        f"Change it at: {host}/team/-/settings",
    ]


def test_group_create_sends_the_access_flags_that_were_given(stub_server, host, token_env, capsys):
    stub_server.responder = _json_responder(201, _group_answer("public", invitees=True))

    code = cli.main(
        [
            "group", "create", "team", "--name", "Team",
            "--access", "public", "--no-secret-links", "--invitees", "--host", host,
        ]
    )

    assert code == 0
    assert json.loads(stub_server.requests[0]["body"])["defaultAccess"] == {
        "base": "public",
        "keys": False,
        "invitees": True,
    }
    # Public needs no "who" and no pointer to where it changes.
    assert capsys.readouterr().out.splitlines() == [
        "Created group 'team' (Team). You are its admin.",
        "Default access for new sites: public (anyone), secret links off, invitees on.",
    ]


def test_group_create_uses_the_stored_session_and_host(stub_server, host, isolated_cwd, capsys):
    _store_session(host, access="stored")
    stub_server.responder = _json_responder(201, _group_answer())

    code = cli.main(["group", "create", "team", "--name", "Team"])

    assert code == 0
    assert stub_server.requests[0]["headers"]["Authorization"] == "Bearer stored"


def test_group_create_without_a_session_asks_to_log_in_and_sends_nothing(
    stub_server, host, isolated_cwd, capsys
):
    code = cli.main(["group", "create", "team", "--name", "Team", "--host", host])

    assert code == 2
    assert "plak login" in capsys.readouterr().err
    assert stub_server.requests == []


def test_group_create_never_trades_for_a_ci_oidc_token(stub_server, host, isolated_cwd, monkeypatch, capsys):
    """The server takes only a member's CLI token here, so the CLI does not
    fetch an OIDC token it would be refused with."""
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", f"{host}/oidc-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-token")

    code = cli.main(["group", "create", "team", "--name", "Team", "--host", host])

    assert code == 2
    assert stub_server.requests == []


def test_group_create_refuses_an_invalid_slug(stub_server, host, token_env, capsys):
    code = cli.main(["group", "create", "Team Aurora", "--name", "Team", "--host", host])

    assert code == 2
    assert "group must be a valid slug" in capsys.readouterr().err
    assert stub_server.requests == []


def test_group_create_refuses_plain_http_to_another_host(stub_server, token_env, capsys):
    code = cli.main(["group", "create", "team", "--name", "Team", "--host", "http://plak.example"])

    assert code == 2
    assert "https://" in capsys.readouterr().err


def test_group_create_refuses_an_unknown_access_base(stub_server, host, token_env, capsys):
    code = cli.main(["group", "create", "team", "--name", "Team", "--access", "everyone", "--host", host])

    assert code == 2
    assert stub_server.requests == []


def test_group_create_shows_the_problem_detail_and_gives_exit_1(stub_server, host, token_env, capsys):
    stub_server.responder = _json_responder(
        409, {"status": 409, "code": "SLUG_EXISTS", "detail": "A group with slug 'team' already exists."}
    )

    code = cli.main(["group", "create", "team", "--name", "Team", "--host", host])

    assert code == 1
    assert capsys.readouterr().err.strip() == "Error: A group with slug 'team' already exists."


def test_group_create_connection_failure_gives_exit_1(stub_server, host, token_env, monkeypatch, capsys):
    monkeypatch.setattr(cli.httpx, "post", _raise_connect_error)

    code = cli.main(["group", "create", "team", "--name", "Team", "--host", host])

    assert code == 1
    assert "could not connect to" in capsys.readouterr().err


@pytest.mark.parametrize(
    "default_access",
    [None, "public", {"base": "everyone", "keys": False, "invitees": False}, {"base": "sso", "keys": "yes"}],
)
def test_group_create_refuses_an_answer_without_a_usable_access(
    stub_server, host, token_env, capsys, default_access
):
    stub_server.responder = _json_responder(
        201, {"slug": "team", "name": "Team", "defaultAccess": default_access}
    )

    code = cli.main(["group", "create", "team", "--name", "Team", "--host", host])

    assert code == 1
    assert "unexpected answer" in capsys.readouterr().err


def test_group_create_cleans_the_name_from_the_server_and_falls_back_to_the_slug(
    stub_server, host, token_env, capsys
):
    stub_server.responder = _json_responder(201, _group_answer(name="Team\x1b[31m\nred"))
    cli.main(["group", "create", "team", "--name", "Team", "--host", host])
    assert capsys.readouterr().out.splitlines()[0] == "Created group 'team' (Team [31m red). You are its admin."

    stub_server.responder = _json_responder(201, _group_answer(name=None))
    cli.main(["group", "create", "team", "--name", "Team", "--host", host])
    assert capsys.readouterr().out.splitlines()[0] == "Created group 'team' (team). You are its admin."


def test_site_create_without_access_flags_lets_the_site_inherit(stub_server, host, token_env, capsys):
    stub_server.responder = _json_responder(201, _site_answer("public"))

    code = cli.main(["site", "create", "team/docs", "--title", "Docs", "--host", host])

    assert code == 0
    record = stub_server.requests[0]
    assert record["path"] == "/-/api/v1/groups/team/sites"
    assert record["headers"]["Authorization"] == "Bearer tok"
    assert json.loads(record["body"]) == {"slug": "docs", "title": "Docs"}
    assert capsys.readouterr().out.splitlines() == [
        "Created site 'team/docs' (Docs). You are its admin.",
        "Access: public (anyone), secret links off, invitees off.",
        f"Publish to it with: plak publish <dist> --host {host} --site team/docs",
    ]


def test_site_create_sends_only_the_given_flags_and_says_who_can_see_it(stub_server, host, token_env, capsys):
    """Only --secret-links given: base and invitees come from the group's
    default on the server."""
    stub_server.responder = _json_responder(201, _site_answer("nobody", keys=True))

    code = cli.main(["site", "create", "team/docs", "--title", "Docs", "--secret-links", "--host", host])

    assert code == 0
    assert json.loads(stub_server.requests[0]["body"]) == {
        "slug": "docs",
        "title": "Docs",
        "access": {"keys": True},
    }
    assert capsys.readouterr().out.splitlines() == [
        "Created site 'team/docs' (Docs). You are its admin.",
        "Access: nobody (nobody by default), secret links on, invitees off.",
        "Who can see it: anyone with a secret link.",
        f"Change it at: {host}/team/docs/access",
        f"Publish to it with: plak publish <dist> --host {host} --site team/docs",
    ]


def test_site_create_names_every_way_in(stub_server, host, token_env, capsys):
    stub_server.responder = _json_responder(201, _site_answer("sso", keys=True, invitees=True))

    cli.main(
        [
            "site", "create", "team/docs", "--title", "Docs",
            "--access", "sso", "--secret-links", "--invitees", "--host", host,
        ]
    )

    assert json.loads(stub_server.requests[0]["body"])["access"] == {
        "base": "sso",
        "keys": True,
        "invitees": True,
    }
    assert (
        "Who can see it: anyone who signs in with SSO Rijk, anyone with a secret link, invitees, once signed in."
        in capsys.readouterr().out.splitlines()
    )


def test_site_create_that_nobody_can_see_says_so(stub_server, host, token_env, capsys):
    stub_server.responder = _json_responder(201, _site_answer("nobody"))

    cli.main(
        ["site", "create", "team/docs", "--title", "Docs", "--access", "nobody", "--no-invitees", "--host", host]
    )

    assert json.loads(stub_server.requests[0]["body"])["access"] == {"base": "nobody", "invitees": False}
    assert "Who can see it: nobody yet." in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize("site", ["docs", "team/", "/docs", "team/docs/extra"])
def test_site_create_refuses_a_site_that_is_not_group_slash_site(stub_server, host, token_env, capsys, site):
    code = cli.main(["site", "create", site, "--title", "Docs", "--host", host])

    assert code == 2
    assert "The site must have the form 'group/site'" in capsys.readouterr().err
    assert stub_server.requests == []


def test_site_create_without_a_session_asks_to_log_in(stub_server, host, isolated_cwd, capsys):
    code = cli.main(["site", "create", "team/docs", "--title", "Docs", "--host", host])

    assert code == 2
    assert "plak login" in capsys.readouterr().err
    assert stub_server.requests == []


def test_site_create_shows_the_refusal_and_gives_exit_1(stub_server, host, token_env, capsys):
    stub_server.responder = _json_responder(
        403, {"status": 403, "code": "INSUFFICIENT_ROLE", "detail": "This needs at least the role editor."}
    )

    code = cli.main(["site", "create", "team/docs", "--title", "Docs", "--host", host])

    assert code == 1
    assert capsys.readouterr().err.strip() == "Error: This needs at least the role editor."


def test_site_create_refuses_an_answer_without_a_usable_access(stub_server, host, token_env, capsys):
    stub_server.responder = _json_responder(201, {"slug": "docs", "title": "Docs"})

    code = cli.main(["site", "create", "team/docs", "--title", "Docs", "--host", host])

    assert code == 1
    assert "unexpected answer" in capsys.readouterr().err


def test_site_create_falls_back_to_the_slug_without_a_title(stub_server, host, token_env, capsys):
    stub_server.responder = _json_responder(201, _site_answer("public", title=7))

    cli.main(["site", "create", "team/docs", "--title", "Docs", "--host", host])

    assert capsys.readouterr().out.splitlines()[0] == "Created site 'team/docs' (docs). You are its admin."


def test_group_and_site_need_a_subcommand_and_their_required_flags(capsys):
    assert cli.main(["group"]) == 2
    assert cli.main(["site"]) == 2
    assert cli.main(["site", "create", "team/docs"]) == 2


_STORED_ACCESS = "stored-access-token-must-never-be-printed"
_STORED_REFRESH = "stored-refresh-token-must-never-be-printed"


@pytest.mark.parametrize(
    ("argv", "answer", "unreachable", "expected"),
    [
        (["whoami"], {"member": {"email": "iemand@example.nl"}}, None, "Logged in as iemand@example.nl on {host}."),
        (["whoami"], None, "get", "Error: could not connect to {host}"),
        (["group", "create", "team", "--name", "Team"], _group_answer(), None, "Change it at: {host}/team/-/settings"),
        (["group", "create", "team", "--name", "Team"], None, "post", "Error: could not connect to {host}"),
        (
            ["site", "create", "team/docs", "--title", "Docs"],
            _site_answer("public"),
            None,
            "Publish to it with: plak publish <dist> --host {host} --site team/docs",
        ),
        (["login", "--no-open"], None, "post", "Logging in at {host}"),
    ],
    ids=["whoami", "whoami-unreachable", "group-create", "group-create-unreachable", "site-create", "login"],
)
def test_a_host_taken_from_the_stored_session_is_printed_but_its_tokens_never_are(
    stub_server, host, isolated_cwd, monkeypatch, capsys, argv, answer, unreachable, expected
):
    """With --insecure-storage the host and the tokens share hosts.json; a
    message naming the host must carry the host alone."""
    for name in ("PLAK_HOST", "PLAK_ACCESS_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL"):
        monkeypatch.delenv(name, raising=False)
    _store_session(host, access=_STORED_ACCESS, refresh=_STORED_REFRESH, insecure=True)
    if answer is not None:
        stub_server.responder = _json_responder(200 if argv[0] == "whoami" else 201, answer)
    if unreachable is not None:
        monkeypatch.setattr(cli.httpx, unreachable, _raise_connect_error)

    cli.main(argv)

    output = capsys.readouterr()
    printed = output.out + output.err
    assert expected.format(host=host) in printed
    assert _STORED_ACCESS not in printed
    assert _STORED_REFRESH not in printed


# -- plak site link ---------------------------------------------------------------


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _gh_repository(owner: str = "MinBZK", repo: str = "Prive", repository_id: Any = 5005, owner_id: Any = 6006) -> str:
    return json.dumps({"id": repository_id, "name": repo, "owner": {"id": owner_id, "login": owner}})


class _FakeRun:
    """Stands in for subprocess.run: per program (git, gh) a result or an
    exception to raise; every call is recorded."""

    def __init__(self, **outcomes: Any) -> None:
        self.outcomes = outcomes
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        outcome = self.outcomes[argv[0]]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def programs(self) -> list[str]:
        return [argv[0] for argv in self.calls]


def _link_answer(**extra: Any) -> dict:
    return {
        "groupSlug": "team",
        "siteSlug": "docs",
        "provider": "github",
        "host": "https://github.com",
        "owner": "MinBZK",
        "repo": "Prive",
        "repositoryId": 5005,
        "ownerId": 6006,
        "liveBranch": "main",
    } | extra


@pytest.fixture
def fake_run(monkeypatch) -> _FakeRun:
    fake = _FakeRun(gh=_completed(_gh_repository()), git=_completed("git@github.com:minbzk/prive.git\n"))
    monkeypatch.setattr(cli.subprocess, "run", fake)
    return fake


def test_site_link_asks_gh_for_the_ids_so_a_private_repository_links(
    stub_server, host, token_env, fake_run, capsys
):
    stub_server.responder = _json_responder(200, _link_answer())

    code = cli.main(["site", "link", "team/docs", "minbzk/prive", "--live-branch", "main", "--host", host])

    assert code == 0
    assert fake_run.calls == [["gh", "api", "--hostname", "github.com", "repos/minbzk/prive"]]
    record = stub_server.requests[0]
    assert (record["method"], record["path"]) == ("PUT", "/-/api/v1/sites/team/docs/repository")
    assert record["headers"]["Authorization"] == "Bearer tok"
    assert json.loads(record["body"]) == {
        "provider": "github",
        "owner": "MinBZK",
        "repo": "Prive",
        "liveBranch": "main",
        "repositoryId": 5005,
        "ownerId": 6006,
    }
    assert capsys.readouterr().out.splitlines() == [
        "Linked github.com/MinBZK/Prive to team/docs.",
        "Live: only from 'main', on a push, a manual run or a schedule. Previews: from any branch.",
        "IDs from gh: repository 5005, owner 6006.",
        f"Set up the workflow: {host}/team/docs/deploy",
    ]


def test_site_link_takes_the_origin_of_the_checkout_without_a_repository(
    stub_server, host, token_env, fake_run, capsys
):
    stub_server.responder = _json_responder(200, _link_answer(liveBranch=None))

    code = cli.main(["site", "link", "team/docs", "--host", host])

    assert code == 0
    assert fake_run.calls[0] == ["git", "remote", "get-url", "origin"]
    assert fake_run.calls[1][-1] == "repos/minbzk/prive"
    assert json.loads(stub_server.requests[0]["body"])["liveBranch"] is None
    assert (
        "Live: from any branch, on a push, a manual run or a schedule. Previews: from any branch."
        in capsys.readouterr().out.splitlines()
    )


def test_site_link_sends_a_forgejo_repository_with_its_host_and_does_not_ask_gh(
    stub_server, host, token_env, fake_run, capsys
):
    fake_run.outcomes["git"] = _completed("https://Code.Overheid.nl/minbzk/website.git\n")
    stub_server.responder = _json_responder(200, _link_answer(owner="minbzk", repo="website"))

    code = cli.main(["site", "link", "team/docs", "--live-branch", "main", "--host", host])

    assert code == 0
    assert fake_run.programs() == ["git"]
    assert json.loads(stub_server.requests[0]["body"]) == {
        "provider": "forgejo",
        "host": "https://code.overheid.nl",
        "owner": "minbzk",
        "repo": "website",
        "liveBranch": "main",
    }
    output = capsys.readouterr().out
    assert "Linked code.overheid.nl/minbzk/website to team/docs." in output
    assert "IDs" not in output


def test_site_link_sends_given_ids_without_asking_gh(stub_server, host, token_env, fake_run, capsys):
    stub_server.responder = _json_responder(200, _link_answer(owner="minbzk", repo="prive"))

    code = cli.main(
        [
            "site", "link", "team/docs", "minbzk/prive", "--live-branch", "main",
            "--repository-id", "7007", "--owner-id", "8008", "--host", host,
        ]
    )

    assert code == 0
    assert fake_run.calls == []
    body = json.loads(stub_server.requests[0]["body"])
    assert (body["owner"], body["repo"], body["repositoryId"], body["ownerId"]) == ("minbzk", "prive", 7007, 8008)
    assert "IDs as given: repository 7007, owner 8008." in capsys.readouterr().out


def test_site_link_with_no_gh_leaves_the_lookup_to_plak(stub_server, host, token_env, fake_run, capsys):
    stub_server.responder = _json_responder(200, _link_answer())

    code = cli.main(["site", "link", "team/docs", "minbzk/website", "--no-gh", "--host", host])

    assert code == 0
    assert fake_run.calls == []
    assert "repositoryId" not in json.loads(stub_server.requests[0]["body"])


@pytest.mark.parametrize(
    "gh",
    [
        FileNotFoundError("gh"),
        subprocess.TimeoutExpired(["gh"], 30),
        _completed("", returncode=1),
        _completed("geen json"),
        _completed("[]"),
        _completed(json.dumps({"id": 5005, "name": "Prive", "owner": "MinBZK"})),
        _completed(_gh_repository(repository_id=True)),
        _completed(_gh_repository(repository_id=0)),
        _completed(_gh_repository(owner_id="6006")),
        _completed(_gh_repository(owner_id=2**63)),
        _completed(_gh_repository(owner="min bzk")),
        _completed(_gh_repository(repo="..")),
    ],
    ids=[
        "missing", "timeout", "failed", "not-json", "not-an-object", "owner-not-an-object", "bool-id",
        "zero-id", "string-id", "id-too-large", "bad-owner", "bad-repo",
    ],
)
def test_site_link_without_a_usable_gh_answer_sends_no_ids(stub_server, host, token_env, fake_run, capsys, gh):
    fake_run.outcomes["gh"] = gh
    stub_server.responder = _json_responder(200, _link_answer(owner="minbzk", repo="website"))

    code = cli.main(["site", "link", "team/docs", "minbzk/website", "--host", host])

    assert code == 0
    body = json.loads(stub_server.requests[0]["body"])
    assert "repositoryId" not in body and "ownerId" not in body
    assert (body["owner"], body["repo"]) == ("minbzk", "website")
    assert "IDs" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("o/r", ("github.com", "o", "r")),
        ("https://github.com/O/R.git", ("github.com", "O", "R")),
        ("https://github.com/o/r/tree/main", ("github.com", "o", "r")),
        ("ssh://git@github.com/o/r.git", ("github.com", "o", "r")),
        ("git@code.overheid.nl:o/r.git", ("code.overheid.nl", "o", "r")),
        ("https://Code.Overheid.nl/o/r", ("code.overheid.nl", "o", "r")),
        ("https://forge.example:3000/o/r", ("forge.example:3000", "o", "r")),
        ("ssh://forge.example:2222/o/r.tar", ("forge.example", "o", "r.tar")),
    ],
)
def test_site_link_understands_the_usual_ways_to_name_a_repository(reference, expected):
    assert cli._parse_repository(reference) == expected


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("o/r/x", "must be 'owner/repo' or a URL"),
        ("websiteonly", "must be 'owner/repo' or a URL"),
        ("http://github.com/o/r", "Not a repository URL"),
        ("https:///o/r", "Not a repository URL"),
        ("https://forge.example:poort/o/r", "Not a repository URL"),
        ("x@bad\x1bhost:o/r", "Not a valid host"),
        ("https://-forge.example/o/r", "Not a valid host"),
        ("https://github.com/o", "names no owner and repository"),
        ("o/..", "Not a valid owner or repository name"),
        ("git@github.com:o/re po", "Not a valid owner or repository name"),
    ],
)
def test_site_link_refuses_what_is_not_a_repository(
    stub_server, host, token_env, fake_run, capsys, reference, message
):
    code = cli.main(["site", "link", "team/docs", reference, "--host", host])

    assert code == 2
    assert message in capsys.readouterr().err
    assert stub_server.requests == []


@pytest.mark.parametrize(
    "origin",
    [
        _completed("", returncode=128),
        _completed("\n"),
        FileNotFoundError("git"),
        subprocess.TimeoutExpired(["git"], 30),
    ],
    ids=["no-remote", "empty", "not-installed", "timeout"],
)
def test_site_link_without_a_repository_and_no_origin_says_what_to_pass(
    stub_server, host, token_env, fake_run, capsys, origin
):
    fake_run.outcomes["git"] = origin

    code = cli.main(["site", "link", "team/docs", "--host", host])

    assert code == 2
    assert "no git remote 'origin' here" in capsys.readouterr().err
    assert stub_server.requests == []


@pytest.mark.parametrize(
    ("ids", "message"),
    [
        (["--repository-id", "7007"], "together, or neither"),
        (["--owner-id", "8008"], "together, or neither"),
        (["--repository-id", "0", "--owner-id", "8008"], "positive whole numbers"),
        (["--repository-id", "7007", "--owner-id", "-1"], "positive whole numbers"),
    ],
)
def test_site_link_refuses_half_or_impossible_ids(stub_server, host, token_env, fake_run, capsys, ids, message):
    code = cli.main(["site", "link", "team/docs", "o/r", *ids, "--host", host])

    assert code == 2
    assert message in capsys.readouterr().err
    assert stub_server.requests == []
    assert fake_run.calls == []


def test_site_link_without_a_live_branch_lets_every_branch_publish_live(
    stub_server, host, token_env, fake_run, capsys
):
    stub_server.responder = _json_responder(200, _link_answer(liveBranch=None))

    code = cli.main(["site", "link", "team/docs", "o/r", "--host", host])

    assert code == 0
    assert json.loads(stub_server.requests[0]["body"])["liveBranch"] is None
    assert (
        "Live: from any branch, on a push, a manual run or a schedule. Previews: from any branch."
        in capsys.readouterr().out.splitlines()
    )


def test_site_link_refuses_a_site_that_is_not_group_slash_site(stub_server, host, token_env, fake_run, capsys):
    code = cli.main(["site", "link", "docs", "o/r", "--host", host])

    assert code == 2
    assert "The site must have the form 'group/site'" in capsys.readouterr().err


def test_site_link_without_a_session_asks_to_log_in(stub_server, host, isolated_cwd, fake_run, capsys):
    code = cli.main(["site", "link", "team/docs", "o/r", "--host", host])

    assert code == 2
    assert "plak login" in capsys.readouterr().err
    assert stub_server.requests == []


def test_site_link_explains_a_private_repository_plak_could_not_find(
    stub_server, host, token_env, fake_run, capsys
):
    fake_run.outcomes["gh"] = _completed("", returncode=1)
    stub_server.responder = _json_responder(
        422, {"status": 422, "code": "REPOSITORY_NOT_FOUND", "detail": "Repository o/r not found on github.com."}
    )

    code = cli.main(["site", "link", "team/docs", "o/r", "--host", host])

    assert code == 1
    assert capsys.readouterr().err.splitlines() == [
        "Error: Repository o/r not found on github.com.",
        (
            "A private repository: log in to GitHub with 'gh auth login' and run this again, "
            "or pass --repository-id and --owner-id."
        ),
    ]


def test_site_link_shows_any_other_refusal_on_its_own(stub_server, host, token_env, fake_run, capsys):
    stub_server.responder = _json_responder(
        403, {"status": 403, "code": "INSUFFICIENT_ROLE", "detail": "This needs at least the role admin."}
    )

    code = cli.main(["site", "link", "team/docs", "o/r", "--host", host])

    assert code == 1
    assert capsys.readouterr().err.strip() == "Error: This needs at least the role admin."


def test_site_link_connection_failure_gives_exit_1(stub_server, host, token_env, fake_run, monkeypatch, capsys):
    monkeypatch.setattr(cli.httpx, "put", _raise_connect_error)

    code = cli.main(["site", "link", "team/docs", "o/r", "--host", host])

    assert code == 1
    assert "could not connect to" in capsys.readouterr().err


def test_site_link_names_the_repository_as_sent_without_one_in_the_answer_and_cleans_the_one_there(
    stub_server, host, token_env, fake_run, capsys
):
    stub_server.responder = _json_responder(200, {"owner": 7})
    cli.main(["site", "link", "team/docs", "o/r", "--host", host])
    assert capsys.readouterr().out.splitlines()[0] == "Linked github.com/MinBZK/Prive to team/docs."

    stub_server.responder = _json_responder(200, _link_answer(repo="Prive\x1b[31m"))
    cli.main(["site", "link", "team/docs", "o/r", "--host", host])
    assert capsys.readouterr().out.splitlines()[0] == "Linked github.com/MinBZK/Prive [31m to team/docs."
