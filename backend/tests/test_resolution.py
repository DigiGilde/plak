"""Tests for serving/resolution.py: path validation (behaviour requirement 1),
index rewrite and directory detection (behaviour requirement 2), 404.html
resolution."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from toon.ingest import precompress
from toon.ingest.store import ContentStore
from toon.serving import resolution


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
        assert outcome_.stored is not None and outcome_.stored.plain is not None
        assert outcome_.stored.plain.read_bytes() == b"<h1>start</h1>"

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
        stored = resolution.find_404_page(store, ref)
        assert stored is not None and stored.plain is not None
        assert stored.plain.read_bytes() == b"<h1>oeps</h1>"

    def test_absent(self, store: ContentStore, storage_ref: str):
        assert resolution.find_404_page(store, storage_ref) is None


class TestChooseEncoding:
    BOTH = ("br", "gzip")

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            (None, None),
            ("", None),
            ("gzip, deflate, br, zstd", "br"),
            ("gzip", "gzip"),
            ("br;q=0, gzip", "gzip"),
            ("gzip;q=1, br;q=0.5", "gzip"),
            ("gzip;q=0.5, br;q=1", "br"),
            ("gzip;q=0.8, br;q=0.8", "br"),
            ("*", "br"),
            ("*;q=0", None),
            ("*, br;q=0", "gzip"),
            ("*;q=0, gzip", "gzip"),
            ("identity", None),
            ("deflate", None),
            ("x-gzip", "gzip"),
            ("BR", "br"),
            ("br;Q=0, gzip", "gzip"),
            ("br; q = 0, gzip", "gzip"),
            (" gzip ; q=0.001 ", "gzip"),
            ("br;q=1.000", "br"),
            ("br;q=0.", None),
            ("br;q=1.", "br"),
            ("br;q=0.5", "br"),
            (",,gzip,", "gzip"),
            (",", None),
            ("gzip;level=1", "gzip"),
            ("gzip;level=1;q=0", None),
        ],
    )
    def test_negotiation(self, header: str | None, expected: str | None):
        assert resolution.choose_encoding(header, self.BOTH) == expected

    @pytest.mark.parametrize("value", ["2", "-1", "abc", "0.0001", "nan", "1.001", "", "inf", "1e0"])
    def test_invalid_qvalue_is_a_refusal(self, value: str):
        assert resolution.choose_encoding(f"br;q={value}, gzip;q=0.1", self.BOTH) == "gzip"
        assert resolution.choose_encoding(f"br;q={value}", self.BOTH) is None

    def test_only_available_codings_are_returned(self):
        assert resolution.choose_encoding("br, gzip", ("gzip",)) == "gzip"
        assert resolution.choose_encoding("br", ("gzip",)) is None
        assert resolution.choose_encoding("*", ("gzip",)) == "gzip"

    def test_nothing_available(self):
        assert resolution.choose_encoding("br, gzip", ()) is None
class TestRepresentation:
    def test_plain_file(self, tmp_path: Path):
        plain = tmp_path / "a.js"
        assert resolution.representation(resolution.Stored(plain=plain), "br, gzip") == resolution.Representation(plain)

    def test_brotli_when_accepted(self, tmp_path: Path):
        encoded = {"br": tmp_path / "b", "gzip": tmp_path / "g"}
        outcome_ = resolution.representation(resolution.Stored(encoded=encoded), "gzip, br")
        assert outcome_ == resolution.Representation(tmp_path / "b", "br", varies=True)

    def test_gzip_when_brotli_not_accepted(self, tmp_path: Path):
        encoded = {"br": tmp_path / "b", "gzip": tmp_path / "g"}
        outcome_ = resolution.representation(resolution.Stored(encoded=encoded), "gzip")
        assert outcome_ == resolution.Representation(tmp_path / "g", "gzip", varies=True)

    @pytest.mark.parametrize("header", [None, "", "identity", "br;q=0, gzip;q=0", "deflate"])
    def test_nothing_acceptable_is_gunzipped_from_the_gzip(self, tmp_path: Path, header: str | None):
        encoded = {"br": tmp_path / "b", "gzip": tmp_path / "g"}
        outcome_ = resolution.representation(resolution.Stored(encoded=encoded), header)
        assert outcome_ == resolution.Representation(tmp_path / "g", None, varies=True, gunzip=True)

    def test_gzip_only_with_a_brotli_only_client_is_gunzipped(self, tmp_path: Path):
        outcome_ = resolution.representation(resolution.Stored(encoded={"gzip": tmp_path / "g"}), "br")
        assert outcome_ == resolution.Representation(tmp_path / "g", None, varies=True, gunzip=True)

    def test_gzip_only_with_a_gzip_client(self, tmp_path: Path):
        outcome_ = resolution.representation(resolution.Stored(encoded={"gzip": tmp_path / "g"}), "br, gzip")
        assert outcome_ == resolution.Representation(tmp_path / "g", "gzip", varies=True)


def compressed_version(store: ContentStore, encoded: dict[str, tuple[str, ...]]) -> str:
    """A version whose files exist only as compressed copies: rel path to the
    encodings written, each holding its own name."""
    with store.write_version(uuid.uuid4(), uuid.uuid4()) as writer:
        for rel_path, encodings in encoded.items():
            for encoding in encodings:
                with writer.open_encoded(rel_path, encoding) as handle:
                    handle.write(encoding.encode())
    return writer.storage_ref


class TestLocate:
    def test_plain_file(self, store: ContentStore, storage_ref: str):
        stored = resolution.locate(store, storage_ref, "stijl.css")
        assert stored is not None and stored.encoded == {}
        assert stored.plain is not None and stored.plain.read_bytes() == b"body{}"

    def test_plain_file_wins_over_encoded_copies(self, store: ContentStore):
        with store.write_version(uuid.uuid4(), uuid.uuid4()) as writer:
            with writer.open_file("a.js") as handle:
                handle.write(b"plain")
            with writer.open_encoded("a.js", "gzip") as handle:
                handle.write(b"gz")
        stored = resolution.locate(store, writer.storage_ref, "a.js")
        assert stored is not None and stored.plain is not None and stored.encoded == {}

    def test_compressed_with_both_encodings(self, store: ContentStore):
        ref = compressed_version(store, {"a.js": ("gzip", "br")})
        stored = resolution.locate(store, ref, "a.js")
        assert stored is not None and stored.plain is None
        assert set(stored.encoded) == {"br", "gzip"}
        assert stored.encoded["gzip"].read_bytes() == b"gzip"
        assert stored.encoded["br"].read_bytes() == b"br"

    def test_compressed_with_gzip_only(self, store: ContentStore):
        ref = compressed_version(store, {"a.js": ("gzip",)})
        stored = resolution.locate(store, ref, "a.js")
        assert stored is not None and set(stored.encoded) == {"gzip"}

    def test_brotli_without_gzip_is_not_a_file(self, store: ContentStore):
        ref = compressed_version(store, {"a.js": ("br",)})
        assert resolution.locate(store, ref, "a.js") is None

    def test_the_version_decides_not_the_type_table(self, store: ContentStore, monkeypatch):
        """A copy is the only one there is: a later change to which types
        compress must not turn files already stored that way into a 404."""
        ref = compressed_version(store, {"font.ttf": ("gzip",), "x.png": ("gzip",)})
        monkeypatch.setattr(precompress, "_COMPRESSIBLE", frozenset())
        for rel_path in ("font.ttf", "x.png"):
            stored = resolution.locate(store, ref, rel_path)
            assert stored is not None, rel_path
            assert stored.plain is None
            assert set(stored.encoded) == {"gzip"}

    def test_missing(self, store: ContentStore, storage_ref: str):
        assert resolution.locate(store, storage_ref, "nergens.js") is None

    def test_encoded_tree_is_not_reachable_by_its_own_path(self, store: ContentStore):
        ref = compressed_version(store, {"a.js": ("gzip",)})
        assert resolution.locate(store, ref, "_encoded/gzip/a.js") is None


class TestResolveCompressed:
    @pytest.fixture
    def ref(self, store: ContentStore) -> str:
        return compressed_version(
            store,
            {"a.js": ("gzip", "br"), "index.html": ("gzip",), "docs/index.html": ("gzip",), "404.html": ("gzip",)},
        )

    def test_file(self, store: ContentStore, ref: str):
        outcome_ = resolution.resolve(store, ref, "a.js")
        assert outcome_.kind is resolution.ResolutionKind.FILE and outcome_.rel_path == "a.js"
        assert outcome_.stored is not None and outcome_.stored.plain is None
        assert set(outcome_.stored.encoded) == {"gzip", "br"}

    def test_root_index(self, store: ContentStore, ref: str):
        outcome_ = resolution.resolve(store, ref, "")
        assert outcome_.kind is resolution.ResolutionKind.FILE and outcome_.rel_path == "index.html"
        assert outcome_.stored is not None and set(outcome_.stored.encoded) == {"gzip"}

    def test_directory_with_slash_gets_its_index(self, store: ContentStore, ref: str):
        outcome_ = resolution.resolve(store, ref, "docs/")
        assert outcome_.kind is resolution.ResolutionKind.FILE and outcome_.rel_path == "docs/index.html"

    def test_directory_without_slash_redirects(self, store: ContentStore, ref: str):
        assert resolution.resolve(store, ref, "docs").kind is resolution.ResolutionKind.DIRECTORY_REDIRECT

    def test_directory_with_slash_without_index_is_not_found(self, store: ContentStore, ref: str):
        assert resolution.resolve(store, ref, "nergens/").kind is resolution.ResolutionKind.NOT_FOUND

    @pytest.mark.parametrize("rel", ["_encoded/gzip/a.js", "_encoded/br/a.js", "_encoded", "_encoded/"])
    def test_encoded_tree_is_not_found(self, store: ContentStore, ref: str, rel: str):
        assert resolution.resolve(store, ref, rel).kind is resolution.ResolutionKind.NOT_FOUND

    def test_find_404_page_compressed(self, store: ContentStore, ref: str):
        stored = resolution.find_404_page(store, ref)
        assert stored is not None and stored.plain is None and set(stored.encoded) == {"gzip"}
