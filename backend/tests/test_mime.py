"""Tests for serving/mime.py: broad MIME table with explicit additions
(spec §5, behaviour requirement 3)."""

from __future__ import annotations

import pytest

from plak.serving import mime


@pytest.mark.parametrize(
    ("rel_path", "expected"),
    [
        ("index.html", "text/html; charset=utf-8"),
        ("pagina.htm", "text/html; charset=utf-8"),
        ("stijl.css", "text/css; charset=utf-8"),
        ("app.js", "text/javascript; charset=utf-8"),
        ("app.mjs", "text/javascript; charset=utf-8"),
        ("app.js.map", "application/json"),
        ("data.json", "application/json"),
        ("module.wasm", "application/wasm"),
        ("site.webmanifest", "application/manifest+json"),
        ("foto.avif", "image/avif"),
        ("logo.svg", "image/svg+xml"),
        ("letter.woff", "font/woff"),
        ("letter.woff2", "font/woff2"),
        ("letter.ttf", "font/ttf"),
        ("letter.otf", "font/otf"),
        ("leesmij.txt", "text/plain; charset=utf-8"),
        ("leesmij.md", "text/markdown; charset=utf-8"),
        ("favicon.ico", "image/vnd.microsoft.icon"),
        ("rapport.pdf", "application/pdf"),
    ],
)
def test_explicit_table(rel_path: str, expected: str):
    assert mime.determine(rel_path) == expected


@pytest.mark.parametrize("rel_path", ["diep/map/onder/app.mjs", "docs/index.html"])
def test_path_with_subdirs(rel_path: str):
    assert mime.determine(rel_path) in ("text/javascript; charset=utf-8", "text/html; charset=utf-8")


def test_uppercase_in_extension():
    assert mime.determine("APP.MJS") == "text/javascript; charset=utf-8"
    assert mime.determine("Index.HTML") == "text/html; charset=utf-8"


@pytest.mark.parametrize("rel_path", ["bestand.xyz123abc", "zonder-extensie", "raar."])
def test_unknown_becomes_octet_stream(rel_path: str):
    assert mime.determine(rel_path) == mime.DEFAULT_MIME


def test_mimetypes_fallback_for_common_types():
    assert mime.determine("foto.png") == "image/png"
    assert mime.determine("foto.jpg") == "image/jpeg"


def test_mimetypes_fallback_for_text_gets_a_charset():
    # Not in the explicit table, but mimetypes still guesses text/*: the
    # charset applies to every text type, not only the ones listed above.
    assert mime.determine("data.csv") == "text/csv; charset=utf-8"
