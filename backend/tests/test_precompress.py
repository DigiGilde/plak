"""Tests for ingest/precompress.py: which files get precompressed variants and
what those variants hold."""

from __future__ import annotations

import gzip
import os
import uuid
from pathlib import Path

import brotli
import pytest

from plak.ingest import precompress
from plak.ingest.store import ContentStore


def text(size: int) -> bytes:
    return (b"lorem ipsum dolor " * (size // 18 + 1))[:size]


def encode(store: ContentStore, files: dict[str, bytes]) -> str:
    """Writes `files` as a version, precompresses it, returns the storage_ref."""
    with store.write_version(uuid.uuid4(), uuid.uuid4()) as writer:
        for rel_path, data in files.items():
            with writer.open_file(rel_path) as handle:
                handle.write(data)
        precompress.encode_version(writer)
    return writer.storage_ref


def variant(store: ContentStore, ref: str, rel_path: str, encoding: str) -> bytes | None:
    path = store.encoded_path(ref, rel_path, encoding)
    return None if path is None else path.read_bytes()


@pytest.fixture
def store(tmp_path: Path) -> ContentStore:
    return ContentStore(tmp_path)


@pytest.fixture
def qualities(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The brotli quality of every brotli compressor the encoder asks for."""
    seen: list[int] = []
    real = precompress._brotli

    def recording(quality: int):
        seen.append(quality)
        return real(quality)

    monkeypatch.setattr(precompress, "_brotli", recording)
    return seen


class TestCompressible:
    @pytest.mark.parametrize(
        "rel_path",
        [
            "index.html",
            "a/b.css",
            "app.js",
            "notes.txt",
            "readme.md",
            "site.webmanifest",
            "data.json",
            "feed.xml",
            "app.wasm",
            "logo.svg",
            "favicon.ico",
            "font.ttf",
            "font.otf",
        ],
    )
    def test_compresses_well(self, rel_path: str):
        assert precompress.compressible(rel_path)

    @pytest.mark.parametrize(
        "rel_path",
        ["a.png", "a.woff2", "a.woff", "a.pdf", "a.unknownext", "noextension", "a.gz"],
    )
    def test_already_compressed_or_unknown(self, rel_path: str):
        assert not precompress.compressible(rel_path)

    def test_xml_suffix_type_is_compressible(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(precompress.mime, "determine", lambda _: "application/atom+xml; charset=utf-8")
        assert precompress.compressible("feed.atom")


class TestEncodeVersion:
    def test_compressible_file_gets_both_variants_that_round_trip(self, store: ContentStore):
        data = text(5000)
        ref = encode(store, {"a.html": data})
        assert brotli.decompress(variant(store, ref, "a.html", "br") or b"") == data
        assert gzip.decompress(variant(store, ref, "a.html", "gzip") or b"") == data

    def test_variants_are_smaller_than_the_file(self, store: ContentStore):
        ref = encode(store, {"a.html": text(5000)})
        for encoding in precompress.ENCODINGS:
            assert 0 < len(variant(store, ref, "a.html", encoding) or b"") < 5000

    def test_plain_file_is_dropped_when_compressed(self, store: ContentStore):
        ref = encode(store, {"a.html": text(5000), "sub/b.css": text(5000)})
        assert store.file_path(ref, "a.html") is None
        assert store.file_path(ref, "sub/b.css") is None
        assert variant(store, ref, "sub/b.css", "gzip") is not None

    def test_files_left_alone_stay_plain(self, store: ContentStore):
        small = text(precompress.MIN_SIZE - 1)
        ref = encode(store, {"small.html": small, "a.png": text(5000)})
        for rel_path in ("small.html", "a.png"):
            path = store.file_path(ref, rel_path)
            assert path is not None
            assert variant(store, ref, rel_path, "gzip") is None

    def test_brotli_that_is_not_smaller_than_gzip_is_discarded(
        self, store: ContentStore, monkeypatch: pytest.MonkeyPatch
    ):
        data = text(5000)
        monkeypatch.setattr(precompress, "_brotli", lambda quality: (lambda chunk: chunk, lambda: b""))
        ref = encode(store, {"a.html": data})
        assert variant(store, ref, "a.html", "br") is None
        assert gzip.decompress(variant(store, ref, "a.html", "gzip") or b"") == data
        assert store.file_path(ref, "a.html") is None

    def test_brotli_equal_to_gzip_in_size_is_discarded(self, store: ContentStore, monkeypatch: pytest.MonkeyPatch):
        data = text(5000)
        gzipped = len(gzip.compress(data))
        monkeypatch.setattr(precompress, "_brotli", lambda quality: (lambda chunk: b"x" * gzipped, lambda: b""))
        ref = encode(store, {"a.html": data})
        assert variant(store, ref, "a.html", "br") is None
        assert variant(store, ref, "a.html", "gzip") is not None

    def test_gzip_that_is_not_smaller_keeps_the_plain_file_and_skips_brotli(
        self, store: ContentStore, qualities: list[int]
    ):
        data = os.urandom(5000)
        ref = encode(store, {"noise.json": data})
        path = store.file_path(ref, "noise.json")
        assert path is not None and path.read_bytes() == data
        assert variant(store, ref, "noise.json", "gzip") is None
        assert variant(store, ref, "noise.json", "br") is None
        assert qualities == []

    def test_small_file_gets_no_variant(self, store: ContentStore):
        ref = encode(store, {"a.html": text(precompress.MIN_SIZE - 1)})
        assert variant(store, ref, "a.html", "br") is None
        assert variant(store, ref, "a.html", "gzip") is None

    def test_file_of_exactly_min_size_is_compressed(self, store: ContentStore):
        ref = encode(store, {"a.html": text(precompress.MIN_SIZE)})
        assert variant(store, ref, "a.html", "br") is not None
        assert variant(store, ref, "a.html", "gzip") is not None

    def test_incompressible_type_gets_no_variant(self, store: ContentStore):
        ref = encode(store, {"a.png": text(5000), "a.woff2": text(5000)})
        for rel_path in ("a.png", "a.woff2"):
            assert variant(store, ref, rel_path, "br") is None
            assert variant(store, ref, rel_path, "gzip") is None

    def test_gzip_is_deterministic(self, store: ContentStore):
        data = text(5000)
        first = encode(store, {"a.html": data})
        second = encode(store, {"a.html": data})
        assert variant(store, first, "a.html", "gzip") == variant(store, second, "a.html", "gzip")

    def test_multi_chunk_file_round_trips(self, store: ContentStore, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(precompress, "_CHUNK", 1000)
        data = text(5500) + bytes(range(256)) * 4
        ref = encode(store, {"a.txt": data})
        assert brotli.decompress(variant(store, ref, "a.txt", "br") or b"") == data
        assert gzip.decompress(variant(store, ref, "a.txt", "gzip") or b"") == data

    def test_file_larger_than_default_chunk_round_trips(self, store: ContentStore):
        data = text(precompress._CHUNK * 2 + 123)
        ref = encode(store, {"big.txt": data})
        assert gzip.decompress(variant(store, ref, "big.txt", "gzip") or b"") == data
        assert brotli.decompress(variant(store, ref, "big.txt", "br") or b"") == data

    def test_brotli_budget_is_spent_in_path_order(
        self, store: ContentStore, monkeypatch: pytest.MonkeyPatch, qualities: list[int]
    ):
        monkeypatch.setattr(precompress, "BROTLI_BEST_BUDGET", 5000)
        # Sorted: a (3000, fits, 2000 left), b (4000, does not fit), c (1500, fits the rest).
        encode(store, {"a.txt": text(3000), "b.txt": text(4000), "c.txt": text(1500)})
        assert qualities == [precompress.BROTLI_BEST, precompress.BROTLI_FAST, precompress.BROTLI_BEST]

    def test_file_exactly_filling_the_budget_gets_best(
        self, store: ContentStore, monkeypatch: pytest.MonkeyPatch, qualities: list[int]
    ):
        monkeypatch.setattr(precompress, "BROTLI_BEST_BUDGET", 3000)
        encode(store, {"a.txt": text(3000), "b.txt": text(2000)})
        assert qualities == [precompress.BROTLI_BEST, precompress.BROTLI_FAST]

    def test_incompressible_content_does_not_use_the_budget(
        self, store: ContentStore, monkeypatch: pytest.MonkeyPatch, qualities: list[int]
    ):
        monkeypatch.setattr(precompress, "BROTLI_BEST_BUDGET", 3000)
        encode(store, {"a.json": os.urandom(2500), "b.txt": text(3000)})
        assert qualities == [precompress.BROTLI_BEST]

    def test_skipped_files_do_not_use_the_budget(
        self, store: ContentStore, monkeypatch: pytest.MonkeyPatch, qualities: list[int]
    ):
        monkeypatch.setattr(precompress, "BROTLI_BEST_BUDGET", 3000)
        encode(store, {"a.png": text(9000), "b.txt": text(100), "c.txt": text(3000)})
        assert qualities == [precompress.BROTLI_BEST]


class TestGunzip:
    def test_pieces_are_bounded_and_join_to_the_original(self, tmp_path: Path):
        data = os.urandom(1000) * 3000 + text(2 * precompress._CHUNK + 5)
        copy = tmp_path / "copy.gz"
        copy.write_bytes(gzip.compress(data))

        pieces = list(precompress.gunzip(copy))

        assert len(pieces) > 1
        assert all(0 < len(piece) <= precompress._CHUNK for piece in pieces)
        assert b"".join(pieces) == data

    def test_a_truncated_copy_raises_after_yielding_what_it_holds(self, tmp_path: Path):
        data = os.urandom(200_000)
        whole = gzip.compress(data)
        copy = tmp_path / "copy.gz"
        copy.write_bytes(whole[: len(whole) // 2])

        yielded = []
        with pytest.raises(OSError, match="ends before its stream does"):
            for piece in precompress.gunzip(copy):
                yielded.append(piece)

        held = b"".join(yielded)
        assert held
        assert data.startswith(held)
        assert len(held) < len(data)
