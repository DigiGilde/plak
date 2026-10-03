"""Tests for serving/resolution.py: path validation (behaviour requirement 1),
index rewrite and directory detection (behaviour requirement 2), 404.html
resolution."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from plak.ingest.store import ContentStore
from plak.serving import resolution


class TestNormaliseRest:
    @pytest.mark.parametrize(
        "rest",
        [
            "..",
            "../",
            "../x",
            "a/../b",
            "a/..",
            "a/../../b",
            "a\\b",
            "..\\x",
            "a\x00b",
            "\x00",
        ],
    )
    def test_invalid_path_yields_none(self, rest: str):
        assert resolution.normalise_rest(rest) is None

    @pytest.mark.parametrize(
        ("rest", "expected"),
        [
            ("", ""),
            ("index.html", "index.html"),
            ("docs/", "docs/"),
            ("docs/diep/", "docs/diep/"),
            ("docs", "docs"),
            ("a//b", "a/b"),
            ("./a", "a"),
            ("a/./b", "a/b"),
            (".", ""),
            ("docs/.", "docs/"),
            ("a%2e%2eb", "a%2e%2eb"),
        ],
    )
    def test_normalisation(self, rest: str, expected: str):
        assert resolution.normalise_rest(rest) == expected

    def test_three_dots_is_plain_a_name(self):
        assert resolution.normalise_rest("...") == "..."


class TestDisplayPath:
    @pytest.mark.parametrize(
        ("rel", "expected"),
        [
            ("", "index.html"),
            ("docs/", "docs/index.html"),
            ("app.css", "app.css"),
            ("docs/pagina.html", "docs/pagina.html"),
        ],
    )
    def test_index_rewrite(self, rel: str, expected: str):
        assert resolution.display_path(rel) == expected


@pytest.fixture
def store(tmp_path: Path) -> ContentStore:
    return ContentStore(tmp_path)


@pytest.fixture
def storage_ref(store: ContentStore) -> str:
    return store.store_version(
        uuid.uuid4(),
        uuid.uuid4(),
        {
            "index.html": b"<h1>start</h1>",
            "stijl.css": b"body{}",
            "docs/index.html": b"<h1>docs</h1>",
            "docs/diep/pagina.html": b"<p>diep</p>",
        },
    )


class TestResolve:
    def test_root_gets_index_rewrite(self, store: ContentStore, storage_ref: str):
        outcome_ = resolution.resolve(store, storage_ref, "")
        assert outcome_.kind is resolution.ResolutionKind.FILE
        assert outcome_.rel_path == "index.html"
        assert outcome_.file_path is not None and outcome_.file_path.read_bytes() == b"<h1>start</h1>"

    def test_directory_with_slash_gets_index_rewrite(self, store: ContentStore, storage_ref: str):
        outcome_ = resolution.resolve(store, storage_ref, "docs/")
        assert outcome_.kind is resolution.ResolutionKind.FILE
        assert outcome_.rel_path == "docs/index.html"

    def test_plain_file(self, store: ContentStore, storage_ref: str):
        outcome_ = resolution.resolve(store, storage_ref, "stijl.css")
        assert outcome_.kind is resolution.ResolutionKind.FILE
        assert outcome_.rel_path == "stijl.css"

    def test_directory_without_slash_becomes_redirect(self, store: ContentStore, storage_ref: str):
        outcome_ = resolution.resolve(store, storage_ref, "docs")
        assert outcome_.kind is resolution.ResolutionKind.DIRECTORY_REDIRECT

    def test_directory_without_index_is_not_found(self, store: ContentStore, storage_ref: str):
        # docs/diep does hold a file, but no index.html.
        assert resolution.resolve(store, storage_ref, "docs/diep").kind is resolution.ResolutionKind.NOT_FOUND
        assert resolution.resolve(store, storage_ref, "docs/diep/").kind is resolution.ResolutionKind.NOT_FOUND

    @pytest.mark.parametrize("rel", ["bestaat-niet", "bestaat-niet/", "docs/nee.html"])
    def test_unknown_path_is_not_found(self, store: ContentStore, storage_ref: str, rel: str):
        assert resolution.resolve(store, storage_ref, rel).kind is resolution.ResolutionKind.NOT_FOUND

    def test_unknown_storage_ref_is_not_found(self, store: ContentStore):
        assert resolution.resolve(store, "aurora/site/nergens", "").kind is resolution.ResolutionKind.NOT_FOUND


class TestFind404Page:
    def test_present(self, store: ContentStore):
        ref = store.store_version(uuid.uuid4(), uuid.uuid4(), {"404.html": b"<h1>oeps</h1>"})
        path = resolution.find_404_page(store, ref)
        assert path is not None and path.read_bytes() == b"<h1>oeps</h1>"

    def test_absent(self, store: ContentStore, storage_ref: str):
        assert resolution.find_404_page(store, storage_ref) is None
