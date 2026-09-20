"""Tests voor de plak-CLI tegen een lokale stub-server.

Draai met:
    just test-cli
of rechtstreeks:
    cd cli && uv run pytest tests -q
"""

from __future__ import annotations

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
import pytest

# The package installed in this project's environment, the same code the
# `plak` command runs.
import plak_cli as cli


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


@pytest.fixture
def stub_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    server.requests = []  # type: ignore[attr-defined]
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
    """.env.plak-tests mogen nooit in de repo-checkout zelf schrijven."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


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
        ["publish", str(dist_folder), "--host", host, "--site", "nldd/website"]
    )

    assert code == 0
    output = capsys.readouterr().out.strip()
    assert output == "00000000-0000-0000-0000-000000000000"

    assert len(stub_server.requests) == 1
    record = stub_server.requests[0]
    assert record["method"] == "POST"
    assert record["path"] == "/-/api/v1/sites/nldd/website/deploys"
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
        ["publish", str(html_path), "--host", host, "--site", "nldd/website"]
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
            "nldd/website",
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
            "nldd/website",
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
        ["publish", str(dist_folder), "--host", host, "--site", "nldd/website"]
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
            "nldd/website",
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
        ["publish", str(dist_folder), "--host", host, "--site", "nldd/website"]
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
            "nldd/website",
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
        ["publish", str(dist_folder), "--host", host, "--site", "nldd/website"]
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
        ["publish", str(dist_folder), "--host", host, "--site", "nldd/website"]
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
        ["publish", str(dist_folder), "--host", host, "--site", "nldd/website"]
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
        ["publish", str(dist_folder), "--host", host, "--site", "nldd/website"]
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
        ["publish", str(pdf_path), "--host", host, "--site", "nldd/website"]
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
            "nldd/website",
        ]
    )

    assert code == 2
    assert stub_server.requests == []


def test_publish_missing_required_arguments_gives_exit_2(stub_server):
    code = cli.main(["publish", "/tmp/iets"])
    assert code == 2


def test_preview_remove_succeeds(stub_server, host, token_env, capsys):
    stub_server.responder = _empty_responder(204)

    code = cli.main(["preview-remove", "pr-42", "--host", host, "--site", "nldd/website"])

    assert code == 0
    record = stub_server.requests[0]
    assert record["method"] == "DELETE"
    assert record["path"] == "/-/api/v1/sites/nldd/website/previews/pr-42"
    assert record["headers"]["Authorization"] == "Bearer tok"


def test_preview_remove_is_idempotent(stub_server, host, token_env):
    stub_server.responder = _empty_responder(204)

    args = ["preview-remove", "pr-42", "--host", host, "--site", "nldd/website"]

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

    code = cli.main(["preview-remove", "pr-42", "--host", host, "--site", "nldd/website"])

    assert code == 1
    assert "Token is ongeldig of ingetrokken" in capsys.readouterr().err


def test_publish_symlink_in_the_dist_folder_gives_exit_2_without_a_request(
    stub_server, host, dist_folder, tmp_path, token_env, capsys
):
    outside_file = tmp_path / "buiten-de-dist-map.txt"
    outside_file.write_text("niet bedoeld om mee te gaan")
    (dist_folder / "kwaadaardige-link").symlink_to(outside_file)

    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "nldd/website"]
    )

    assert code == 2
    assert stub_server.requests == []
    assert "Error" in capsys.readouterr().err


def test_publish_server_sending_a_version_id_with_a_newline_gives_exit_1(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = _json_responder(201, {"versionId": "not-a-uuid\nevil=1"})

    code = cli.main(
        ["publish", str(dist_folder), "--host", host, "--site", "nldd/website"]
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
            "nldd/website",
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


def test_publish_token_via_environment_variable(
    stub_server, host, dist_folder, monkeypatch, capsys
):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "geheim-uit-omgeving")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

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

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

    assert code == 1
    output = capsys.readouterr()
    assert token not in output.out
    assert token not in output.err


def test_publish_without_any_token_source_asks_to_log_in(
    stub_server, host, dist_folder, isolated_cwd, capsys
):
    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

    assert code == 2
    assert stub_server.requests == []
    assert "plak login" in capsys.readouterr().err


# --- plak login: device flow ------------------------------------------------


def test_login_device_flow_success_stores_the_session_with_0600(
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

    env_path = isolated_cwd / cli.ENV_FILENAME
    assert env_path.exists()
    mode = stat.S_IMODE(env_path.stat().st_mode)
    assert mode == 0o600

    data = cli._read_env_file()
    assert data["PLAK_HOST"] == host
    assert data["PLAK_ACCESS_TOKEN"] == "access-1"
    assert data["PLAK_REFRESH_TOKEN"] == "refresh-1"


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
    assert cli._read_env_file()["PLAK_ACCESS_TOKEN"] == "access-2"


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
    assert not (isolated_cwd / cli.ENV_FILENAME).exists()


def test_login_requires_https(stub_server, isolated_cwd, capsys):
    code = cli.main(["login", "--host", "http://plak.example.nl", "--no-open"])

    assert code == 2
    assert "https" in capsys.readouterr().err.lower()


# --- refresh, whoami, logout --------------------------------------------------


def test_stored_session_is_refreshed_transparently_when_expired(
    stub_server, host, isolated_cwd, capsys
):
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": "expired-access",
            "PLAK_REFRESH_TOKEN": "refresh-old",
            "PLAK_ACCESS_EXPIRES_AT": "1",
        }
    )
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

    data = cli._read_env_file()
    assert data["PLAK_ACCESS_TOKEN"] == "fresh-access"
    assert data["PLAK_REFRESH_TOKEN"] == "refresh-new"


def test_refresh_failure_asks_to_log_in_again(stub_server, host, isolated_cwd, capsys):
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": "expired-access",
            "PLAK_REFRESH_TOKEN": "spent-refresh",
            "PLAK_ACCESS_EXPIRES_AT": "1",
        }
    )
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


def test_logout_clears_the_stored_session(stub_server, host, isolated_cwd, capsys):
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": "access-1",
            "PLAK_REFRESH_TOKEN": "refresh-1",
            "PLAK_ACCESS_EXPIRES_AT": "9999999999",
        }
    )
    stub_server.responder = _empty_responder(204)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    assert "Logged out." in capsys.readouterr().out

    record = stub_server.requests[0]
    assert record["method"] == "DELETE"
    assert record["path"] == "/-/api/v1/cli/session"
    assert record["headers"]["Authorization"] == "Bearer access-1"

    data = cli._read_env_file()
    assert "PLAK_ACCESS_TOKEN" not in data
    assert "PLAK_REFRESH_TOKEN" not in data
    # The host stays: handy for the next 'plak login --host ...'.
    assert data["PLAK_HOST"] == host


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
            "/-/api/v1/sites/nldd/website/deploys": [
                _json_step(201, {"versionId": "00000000-0000-0000-0000-000000000000"})
            ],
        }
    )
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", f"{host}/oidc-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-bearer")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

    assert code == 0
    out = capsys.readouterr()
    assert "::add-mask::oidc-jwt-token" in out.out

    oidc_requests = [r for r in stub_server.requests if r["path"].startswith("/oidc-token")]
    assert len(oidc_requests) == 1
    assert oidc_requests[0]["headers"]["Authorization"] == "bearer runner-bearer"
    assert "audience=" in oidc_requests[0]["path"]

    deploy_requests = [
        r for r in stub_server.requests if r["path"] == "/-/api/v1/sites/nldd/website/deploys"
    ]
    assert deploy_requests[0]["headers"]["Authorization"] == "Bearer oidc-jwt-token"

    # OIDC mode does not write a session: the token only lives for this run.
    assert not (isolated_cwd / cli.ENV_FILENAME).exists()


def test_explicit_plak_access_token_takes_priority_over_oidc(
    stub_server, host, dist_folder, isolated_cwd, monkeypatch
):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "expliciet-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", f"{host}/oidc-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-bearer")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

    assert code == 0
    assert all(not r["path"].startswith("/oidc-token") for r in stub_server.requests)
    deploy_requests = [
        r for r in stub_server.requests if r["path"] == "/-/api/v1/sites/nldd/website/deploys"
    ]
    assert deploy_requests[0]["headers"]["Authorization"] == "Bearer expliciet-token"


def test_env_file_is_never_readable_by_others_even_if_it_was(isolated_cwd):
    """An existing world-readable .env.plak is replaced, never written in place."""
    env_path = isolated_cwd / cli.ENV_FILENAME
    env_path.write_text("PLAK_HOST=https://beheer.example\n")
    env_path.chmod(0o644)

    cli._write_env_file({"PLAK_ACCESS_TOKEN": "geheim"})

    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert cli._read_env_file() == {
        "PLAK_HOST": "https://beheer.example",
        "PLAK_ACCESS_TOKEN": "geheim",
    }
    assert [p.name for p in isolated_cwd.iterdir() if p.name.startswith(".env.plak.")] == []


# --- action.yml: no stdout capture around publish, --output-file instead ----

ACTION_YML_PATH = Path(__file__).resolve().parents[2] / "actions" / "publiceer" / "action.yml"


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
    """github.action_path is actions/publiceer/ in this repository, so the
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
            "/-/api/v1/sites/nldd/website/deploys": [
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
            "nldd/website",
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
    assert not (isolated_cwd / cli.ENV_FILENAME).exists()


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


# --- .env.plak as a trusted source for PLAK_HOST ----------------------------


def test_untrusted_env_file_host_is_ignored_when_world_readable(
    isolated_cwd, monkeypatch, capsys
):
    monkeypatch.delenv("PLAK_HOST", raising=False)
    env_path = isolated_cwd / cli.ENV_FILENAME
    env_path.write_text("PLAK_HOST=https://beheer.example\n")
    env_path.chmod(0o644)

    code = cli.main(["whoami"])

    assert code == 2
    error_output = capsys.readouterr().err
    assert "not trusted" in error_output
    assert "No host" in error_output


def test_untrusted_env_file_host_is_ignored_when_git_tracked(
    isolated_cwd, monkeypatch, capsys
):
    monkeypatch.delenv("PLAK_HOST", raising=False)
    env_path = isolated_cwd / cli.ENV_FILENAME
    env_path.write_text("PLAK_HOST=https://beheer.example\n")
    env_path.chmod(0o600)
    monkeypatch.setattr(cli, "_is_git_tracked", lambda path: True)

    code = cli.main(["whoami"])

    assert code == 2
    assert "not trusted" in capsys.readouterr().err


def test_trusted_env_file_host_is_used_when_no_host_flag_given(
    stub_server, host, isolated_cwd, monkeypatch
):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "tok")
    cli._write_env_file({"PLAK_HOST": host})
    stub_server.responder = _json_responder(200, {"member": {"email": "iemand@example.nl"}})

    code = cli.main(["whoami"])

    assert code == 0
    assert stub_server.requests[0]["path"] == "/-/api/v1/cli/whoami"


# --- plak logout: refresh token in the body, clear reporting ----------------


def test_logout_sends_the_refresh_token_in_the_body(stub_server, host, isolated_cwd):
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_REFRESH_TOKEN": "refresh-only",
            "PLAK_ACCESS_EXPIRES_AT": "1",
        }
    )
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
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": "access-1",
            "PLAK_REFRESH_TOKEN": "refresh-1",
            "PLAK_ACCESS_EXPIRES_AT": "9999999999",
        }
    )
    stub_server.responder = _empty_responder(204)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    record = stub_server.requests[0]
    assert record["headers"]["Authorization"] == "Bearer access-1"
    assert json.loads(record["body"]) == {"refreshToken": "refresh-1"}


def test_logout_reports_a_failed_server_revoke_but_still_clears_locally(
    stub_server, host, isolated_cwd, capsys
):
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": "access-1",
            "PLAK_REFRESH_TOKEN": "refresh-1",
            "PLAK_ACCESS_EXPIRES_AT": "9999999999",
        }
    )
    stub_server.responder = _json_responder(401, {"status": 401, "detail": "ongeldig"})

    code = cli.main(["logout", "--host", host])

    assert code == 0
    out = capsys.readouterr()
    assert "could not revoke the session" in out.err
    assert "Logged out." in out.out
    data = cli._read_env_file()
    assert "PLAK_ACCESS_TOKEN" not in data
    assert "PLAK_REFRESH_TOKEN" not in data


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

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

    assert code == 1
    assert "could not connect to" in capsys.readouterr().err
    assert stub_server.requests == []


def test_publish_201_without_version_id_gives_exit_1(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = _json_responder(201, {"iets": "anders"})

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

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
            "nldd/website",
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


def test_publish_non_json_error_body_is_shown_as_the_detail(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = lambda _record: (502, b"Bad gateway van de proxy", "text/plain")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

    assert code == 1
    assert "Error: Bad gateway van de proxy" in capsys.readouterr().err


def test_publish_empty_error_body_falls_back_to_the_http_status(
    stub_server, host, dist_folder, token_env, capsys
):
    stub_server.responder = _empty_responder(500)

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

    assert code == 1
    assert "Error: HTTP 500" in capsys.readouterr().err


def test_publish_skips_a_git_folder_inside_the_dist_folder(
    stub_server, host, dist_folder, token_env
):
    (dist_folder / ".git").mkdir()
    (dist_folder / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

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

    code = cli.main(["publish", str(empty), "--host", host, "--site", "nldd/website"])

    assert code == 2
    assert stub_server.requests == []
    assert "Folder is empty" in capsys.readouterr().err


def test_preview_remove_connection_failure_gives_exit_1(
    stub_server, host, token_env, monkeypatch, capsys
):
    monkeypatch.setattr(cli.httpx, "delete", _raise_connect_error)

    code = cli.main(["preview-remove", "pr-42", "--host", host, "--site", "nldd/website"])

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
    cli._write_env_file({"PLAK_HOST": host})

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "plak login" in capsys.readouterr().err
    assert stub_server.requests == []


def test_stored_session_with_unreadable_expiry_is_used_as_is(
    stub_server, host, isolated_cwd, capsys
):
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": "access-1",
            "PLAK_REFRESH_TOKEN": "refresh-1",
            "PLAK_ACCESS_EXPIRES_AT": "onbekend",
        }
    )
    stub_server.responder = _json_responder(200, {"member": {"email": "iemand@example.nl"}})

    code = cli.main(["whoami", "--host", host])

    assert code == 0
    assert [r["path"] for r in stub_server.requests] == ["/-/api/v1/cli/whoami"]
    assert stub_server.requests[0]["headers"]["Authorization"] == "Bearer access-1"


def test_expired_session_without_a_refresh_token_asks_to_log_in(
    stub_server, host, isolated_cwd, capsys
):
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": "expired-access",
            "PLAK_ACCESS_EXPIRES_AT": "1",
        }
    )

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "Session expired" in capsys.readouterr().err
    assert stub_server.requests == []


def test_refresh_connection_failure_gives_exit_2(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": "expired-access",
            "PLAK_REFRESH_TOKEN": "refresh-old",
            "PLAK_ACCESS_EXPIRES_AT": "1",
        }
    )
    monkeypatch.setattr(cli.httpx, "post", _raise_connect_error)

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "Could not refresh the session" in capsys.readouterr().err
    assert stub_server.requests == []
    # The old session stays put: nothing was exchanged, nothing gets clobbered.
    assert cli._read_env_file()["PLAK_ACCESS_TOKEN"] == "expired-access"


def test_refresh_with_a_non_json_answer_gives_exit_2(stub_server, host, isolated_cwd, capsys):
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": "expired-access",
            "PLAK_REFRESH_TOKEN": "refresh-old",
            "PLAK_ACCESS_EXPIRES_AT": "1",
        }
    )
    stub_server.responder = lambda _record: (200, b"<html>geen json</html>", "text/html")

    code = cli.main(["whoami", "--host", host])

    assert code == 2
    assert "Unexpected answer while refreshing" in capsys.readouterr().err
    assert [r["path"] for r in stub_server.requests] == ["/-/api/v1/cli/tokens"]
    assert cli._read_env_file()["PLAK_ACCESS_TOKEN"] == "expired-access"


# --- OIDC in CI: the runner endpoint misbehaves ------------------------------


def test_oidc_connection_failure_gives_exit_2_without_a_deploy(
    stub_server, host, dist_folder, isolated_cwd, monkeypatch, capsys
):
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", f"{host}/oidc-token")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-bearer")
    monkeypatch.setattr(cli.httpx, "get", _raise_connect_error)

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

    assert code == 2
    assert "Could not fetch an OIDC token" in capsys.readouterr().err
    assert stub_server.requests == []


def test_oidc_endpoint_refusal_explains_the_missing_permission(
    stub_server, host, dist_folder, isolated_cwd, monkeypatch, capsys
):
    stub_server.responder = _json_responder(403, {"message": "Resource not accessible"})
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_URL", f"{host}/oidc-token?api-version=2")
    monkeypatch.setenv("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "runner-bearer")

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

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

    code = cli.main(["publish", str(dist_folder), "--host", host, "--site", "nldd/website"])

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
    assert not (isolated_cwd / cli.ENV_FILENAME).exists()


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
    assert not (isolated_cwd / cli.ENV_FILENAME).exists()


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
    assert not (isolated_cwd / cli.ENV_FILENAME).exists()


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
    assert not (isolated_cwd / cli.ENV_FILENAME).exists()


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
    data = cli._read_env_file()
    assert data["PLAK_ACCESS_TOKEN"] == "access-1"
    assert float(data["PLAK_ACCESS_EXPIRES_AT"]) <= cli.time.time()


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


def test_login_warns_when_the_env_file_is_not_gitignored(
    stub_server, host, isolated_cwd, capsys
):
    subprocess.run(["git", "init", "-q", str(isolated_cwd)], check=True, timeout=30)
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [_device_start_step(host)],
            "/-/api/v1/cli/tokens": [
                _json_step(200, {"accessToken": "access-1", "expiresIn": 3600})
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    assert "is not in .gitignore" in capsys.readouterr().err


def test_login_stays_quiet_about_gitignore_when_git_is_missing(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    def no_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(cli.subprocess, "run", no_git)
    stub_server.responder = _sequence_responder(
        {
            "/-/api/v1/cli/device-authorizations": [_device_start_step(host)],
            "/-/api/v1/cli/tokens": [
                _json_step(200, {"accessToken": "access-1", "expiresIn": 3600})
            ],
        }
    )

    code = cli.main(["login", "--host", host, "--no-open"])

    assert code == 0
    assert "gitignore" not in capsys.readouterr().err
    assert cli._read_env_file()["PLAK_ACCESS_TOKEN"] == "access-1"


# --- .env.plak: reading, writing and trusting it -----------------------------


def test_env_file_comments_and_odd_lines_survive_a_logout(stub_server, host, isolated_cwd):
    env_path = isolated_cwd / cli.ENV_FILENAME
    env_path.write_text(
        "# sessie van plak\n"
        "\n"
        "regel-zonder-gelijkteken\n"
        "PLAK_ACCESS_TOKEN = 'access-1'\n"
        "PLAK_REFRESH_TOKEN=\"refresh-1\"\n"
    )
    stub_server.responder = _empty_responder(204)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    record = stub_server.requests[0]
    assert record["headers"]["Authorization"] == "Bearer access-1"
    assert json.loads(record["body"]) == {"refreshToken": "refresh-1"}
    assert env_path.read_text() == "# sessie van plak\n\nregel-zonder-gelijkteken\n"


def test_env_file_write_failure_leaves_the_old_file_and_no_temp_file(
    isolated_cwd, monkeypatch
):
    env_path = isolated_cwd / cli.ENV_FILENAME
    env_path.write_text("PLAK_HOST=https://beheer.example\n")

    def failing_replace(_src, _dst):
        raise OSError("schijf vol")

    monkeypatch.setattr(cli.os, "replace", failing_replace)

    with pytest.raises(OSError, match="schijf vol"):
        cli._write_env_file({"PLAK_ACCESS_TOKEN": "geheim"})

    assert env_path.read_text() == "PLAK_HOST=https://beheer.example\n"
    assert [p.name for p in isolated_cwd.iterdir()] == [cli.ENV_FILENAME]


def test_env_file_host_is_still_trusted_when_git_is_missing(
    stub_server, host, isolated_cwd, monkeypatch
):
    monkeypatch.setenv("PLAK_ACCESS_TOKEN", "tok")
    monkeypatch.delenv("PLAK_HOST", raising=False)
    cli._write_env_file({"PLAK_HOST": host})

    def no_git(*_args, **_kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(cli.subprocess, "run", no_git)
    stub_server.responder = _json_responder(200, {"member": {"email": "iemand@example.nl"}})

    code = cli.main(["whoami"])

    assert code == 0
    assert stub_server.requests[0]["path"] == "/-/api/v1/cli/whoami"


# --- plak logout: no host, server unreachable --------------------------------


def test_logout_without_any_host_gives_exit_2(stub_server, isolated_cwd, monkeypatch, capsys):
    monkeypatch.delenv("PLAK_HOST", raising=False)

    code = cli.main(["logout"])

    assert code == 2
    assert "No host" in capsys.readouterr().err
    assert stub_server.requests == []


def test_logout_with_an_unreachable_server_still_clears_locally(
    stub_server, host, isolated_cwd, monkeypatch, capsys
):
    cli._write_env_file(
        {
            "PLAK_HOST": host,
            "PLAK_ACCESS_TOKEN": "access-1",
            "PLAK_REFRESH_TOKEN": "refresh-1",
            "PLAK_ACCESS_EXPIRES_AT": "9999999999",
        }
    )
    monkeypatch.setattr(cli.httpx, "request", _raise_connect_error)

    code = cli.main(["logout", "--host", host])

    assert code == 0
    out = capsys.readouterr()
    assert "could not revoke the session at the server" in out.err
    assert "Logged out." in out.out
    data = cli._read_env_file()
    assert "PLAK_ACCESS_TOKEN" not in data
    assert "PLAK_REFRESH_TOKEN" not in data
    assert data["PLAK_HOST"] == host
