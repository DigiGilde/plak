"""Tests for the ContentStore: atomic storage (streaming and dict variant),
spool files, containment and the tmp sweeper."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from plak.ingest.store import SPOOL_SUFFIX, ContentStore, StoreError


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "content"


@pytest.fixture
def store(root: Path) -> ContentStore:
    return ContentStore(root)


FILES = {"index.html": b"<html>", "assets/stijl.css": b"body{}"}


class TestStoreVersion:
    def test_writes_and_returns_ref(self, store: ContentStore, root: Path):
        version_id = uuid.uuid4()
        ref = store.store_version("group", "site", version_id, FILES)
        assert ref == f"group/site/{version_id}"
        assert (root / ref / "index.html").read_bytes() == b"<html>"
        assert (root / ref / "assets" / "stijl.css").read_bytes() == b"body{}"

    def test_tmp_empty_after_success(self, store: ContentStore, root: Path):
        store.store_version("g", "p", uuid.uuid4(), FILES)
        assert list((root / "_tmp").iterdir()) == []

    def test_tmp_cleaned_up_after_error(self, store: ContentStore, root: Path):
        with pytest.raises(StoreError):
            store.store_version("g", "p", uuid.uuid4(), {"../x": b"1"})
        assert list((root / "_tmp").iterdir()) == []
        assert not (root / "g").exists()

    def test_refuses_unsafe_relative_paths(self, store: ContentStore):
        for path in ("/abs", "a/../b", "", "a\\b", "a\x00b", "./a"):
            with pytest.raises(StoreError):
                store.store_version("g", "p", uuid.uuid4(), {path: b"1"})

    def test_refuses_ref_outside_root(self, store: ContentStore):
        with pytest.raises(StoreError):
            store.store_version("..", "p", uuid.uuid4(), FILES)


class TestRoot:
    def test_root_is_the_resolved_content_root(self, store: ContentStore, root: Path):
        assert store.root == root.resolve()


class TestWriteVersion:
    def test_streams_to_workdir_and_renames_atomic(self, store: ContentStore, root: Path):
        version_id = uuid.uuid4()
        with store.write_version("g", "p", version_id) as writer:
            with writer.open_file("index.html") as out:
                out.write(b"<h1>")
                out.write(b"hoi</h1>")
            with writer.open_file("assets/stijl.css") as out:
                out.write(b"body{}")
            # Nothing in the final place yet while the work dir is still open.
            assert not (root / "g" / "p").exists()
            assert len(list((root / "_tmp").iterdir())) == 1
        assert writer.storage_ref == f"g/p/{version_id}"
        assert (root / writer.storage_ref / "index.html").read_bytes() == b"<h1>hoi</h1>"
        assert (root / writer.storage_ref / "assets" / "stijl.css").read_bytes() == b"body{}"
        assert list((root / "_tmp").iterdir()) == []

    def test_exception_cleans_workdir_full_on(self, store: ContentStore, root: Path):
        with pytest.raises(RuntimeError), store.write_version("g", "p", uuid.uuid4()) as writer:
            with writer.open_file("a/b/c.txt") as out:
                out.write(b"deels geschreven")
            raise RuntimeError("uitpakken mislukt halverwege")
        assert list((root / "_tmp").iterdir()) == []
        assert not (root / "g").exists()

    def test_open_file_refuses_unsafe_paths(self, store: ContentStore):
        with pytest.raises(RuntimeError), store.write_version("g", "p", uuid.uuid4()) as writer:
            for path in ("/abs", "a/../b", "", "a\\b", "a\x00b", "./a", ".."):
                with pytest.raises(StoreError):
                    writer.open_file(path)
            raise RuntimeError("klaar")

    def test_open_file_refuses_collisions(self, store: ContentStore):
        with pytest.raises(RuntimeError), store.write_version("g", "p", uuid.uuid4()) as writer:
            with writer.open_file("a/b") as out:
                out.write(b"1")
            with pytest.raises(StoreError):
                writer.open_file("a/b")  # same file twice
            with pytest.raises(StoreError):
                writer.open_file("a")  # already exists as a directory
            with pytest.raises(StoreError):
                writer.open_file("a/b/c")  # using a file as a directory
            raise RuntimeError("klaar")

    def test_refuses_ref_outside_root(self, store: ContentStore, root: Path):
        with pytest.raises(StoreError), store.write_version("..", "p", uuid.uuid4()):
            pass
        assert list((root / "_tmp").iterdir()) == []


class TestSpoolFile:
    def test_lies_in_tmp_and_is_unique(self, store: ContentStore, root: Path):
        a = store.new_spool_file()
        b = store.new_spool_file()
        assert a != b
        assert a.parent == root / "_tmp"
        assert a.name.endswith(SPOOL_SUFFIX)
        assert not a.exists()

    def test_is_not_servable(self, store: ContentStore):
        spool = store.new_spool_file()
        spool.write_bytes(b"geheim")
        assert store.file_path("_tmp", spool.name) is None
        assert store.file_path(f"_tmp/{spool.name}", ".") is None
        assert store.file_path("..", f"content/_tmp/{spool.name}") is None

    def test_sweeper_cleans_old_spool_on(self, store: ContentStore):
        spool = store.new_spool_file()
        spool.write_bytes(b"x")
        old_time = 1_000_000
        os.utime(spool, (old_time, old_time))
        assert store.sweep_tmp(timedelta(hours=1)) == 1
        assert not spool.exists()


class TestFilePath:
    def test_finds_file(self, store: ContentStore, root: Path):
        ref = store.store_version("g", "p", uuid.uuid4(), FILES)
        path = store.file_path(ref, "assets/stijl.css")
        assert path == root / ref / "assets" / "stijl.css"

    def test_none_on_absent_file(self, store: ContentStore):
        ref = store.store_version("g", "p", uuid.uuid4(), FILES)
        assert store.file_path(ref, "bestaat-niet.html") is None

    def test_none_on_directory(self, store: ContentStore):
        ref = store.store_version("g", "p", uuid.uuid4(), FILES)
        assert store.file_path(ref, "assets") is None

    def test_none_on_traversal_in_rel_path(self, store: ContentStore, root: Path):
        ref = store.store_version("g", "p", uuid.uuid4(), FILES)
        (root / "g" / "geheim.txt").write_bytes(b"geheim")
        assert store.file_path(ref, "../../geheim.txt") is None
        assert store.file_path(ref, "/etc/passwd") is None
        assert store.file_path(ref, "a\x00b") is None

    def test_none_on_traversal_in_storage_ref(self, store: ContentStore, root: Path):
        (root.parent / "buiten.txt").write_bytes(b"x")
        assert store.file_path("../", "buiten.txt") is None
        assert store.file_path("..", "buiten.txt") is None

    def test_none_on_symlink_that_escapes(self, store: ContentStore, root: Path):
        ref = store.store_version("g", "p", uuid.uuid4(), FILES)
        outside = root.parent / "buiten.txt"
        outside.write_bytes(b"geheim")
        (root / ref / "ontsnap").symlink_to(outside)
        assert store.file_path(ref, "ontsnap") is None

    def test_none_on_ref_to_tmp(self, store: ContentStore, root: Path):
        (root / "_tmp" / "x").mkdir()
        (root / "_tmp" / "x" / "a.txt").write_bytes(b"1")
        assert store.file_path("_tmp/x", "a.txt") is None


class TestDeleteVersion:
    def test_deletes_tree(self, store: ContentStore, root: Path):
        ref = store.store_version("g", "p", uuid.uuid4(), FILES)
        store.delete_version(ref)
        assert not (root / ref).exists()

    def test_idempotent(self, store: ContentStore):
        ref = store.store_version("g", "p", uuid.uuid4(), FILES)
        store.delete_version(ref)
        store.delete_version(ref)

    def test_refuses_outside_root(self, store: ContentStore, root: Path):
        (root.parent / "extern").mkdir()
        for ref in ("..", "../extern", "_tmp", ""):
            with pytest.raises(StoreError):
                store.delete_version(ref)
        assert (root.parent / "extern").exists()

    def test_refuses_a_null_byte_in_the_storage_ref(self, store: ContentStore):
        # Guarded explicitly: a null byte would otherwise reach os.path
        # functions and come back as an unhandled ValueError.
        with pytest.raises(StoreError):
            store.delete_version("g/p/\x00")


class TestSweepTmp:
    def test_sweeps_only_old_entries(self, store: ContentStore, root: Path):
        old = root / "_tmp" / "oud"
        old.mkdir()
        (old / "a.txt").write_bytes(b"1")
        fresh = root / "_tmp" / "vers"
        fresh.mkdir()
        old_time = 1_000_000  # far in the past
        os.utime(old, (old_time, old_time))

        assert store.sweep_tmp(timedelta(hours=1)) == 1
        assert not old.exists()
        assert fresh.exists()

    def test_empty_is_zero(self, store: ContentStore):
        assert store.sweep_tmp(timedelta(hours=1)) == 0

    def test_entry_that_disappears_during_the_walk_is_skipped(
        self, store: ContentStore, root: Path, monkeypatch
    ):
        """The cleanup job or another sweep removing the same stale entry
        concurrently must not make this sweep fail."""
        gone = root / "_tmp" / "verdwenen"
        gone.mkdir()
        original_stat = Path.stat

        def flaky_stat(self: Path, *args, **kwargs):
            if self == gone:
                raise OSError("verdwenen tussen de iterdir en de stat")
            return original_stat(self, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", flaky_stat)
        assert store.sweep_tmp(timedelta(hours=1)) == 0
        monkeypatch.undo()
        assert gone.exists()


class TestMeasuring:
    def test_site_bytes_counts_every_version_and_is_zero_without_one(self, root: Path):
        store = ContentStore(root)
        assert store.site_bytes("groep", "site") == 0
        store.store_version("groep", "site", uuid.uuid4(), {"a.html": b"x" * 10})
        store.store_version("groep", "site", uuid.uuid4(), {"b.html": b"y" * 5})
        assert store.site_bytes("groep", "site") == 15
        # Another site on the same volume does not count towards it.
        store.store_version("groep", "andere", uuid.uuid4(), {"c.html": b"z" * 99})
        assert store.site_bytes("groep", "site") == 15

    def test_a_file_that_disappears_during_the_walk_is_not_counted(self, root: Path, monkeypatch):
        """The cleanup job removes an expired preview while a deploy is being
        measured: the race may not make the deploy fail."""
        store = ContentStore(root)
        store.store_version("groep", "site", uuid.uuid4(), {"a.html": b"x" * 10})

        def gone(_path):
            raise FileNotFoundError

        monkeypatch.setattr(os, "lstat", gone)
        assert store.site_bytes("groep", "site") == 0

    def test_free_bytes_reports_the_volume(self, root: Path):
        assert ContentStore(root).free_bytes() > 0
