"""Tests for the ContentStore: atomic storage (streaming and dict variant),
spool files, containment, the tmp sweeper and reclaiming directories that lost
their row."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

from plak.ingest.store import SPOOL_SUFFIX, ContentStore, StoreError

SITE = uuid.uuid4()
OLD = 1_000_000  # an mtime far in the past


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
        ref = store.store_version(SITE, version_id, FILES)
        assert ref == f"{SITE}/{version_id}"
        assert (root / ref / "index.html").read_bytes() == b"<html>"
        assert (root / ref / "assets" / "stijl.css").read_bytes() == b"body{}"

    def test_tmp_empty_after_success(self, store: ContentStore, root: Path):
        store.store_version(SITE, uuid.uuid4(), FILES)
        assert list((root / "_tmp").iterdir()) == []

    def test_tmp_cleaned_up_after_error(self, store: ContentStore, root: Path):
        with pytest.raises(StoreError):
            store.store_version(SITE, uuid.uuid4(), {"../x": b"1"})
        assert list((root / "_tmp").iterdir()) == []
        assert not (root / str(SITE)).exists()

    def test_refuses_unsafe_relative_paths(self, store: ContentStore):
        for path in ("/abs", "a/../b", "", "a\\b", "a\x00b", "./a"):
            with pytest.raises(StoreError):
                store.store_version(SITE, uuid.uuid4(), {path: b"1"})

    def test_refuses_ref_outside_root(self, store: ContentStore):
        with pytest.raises(StoreError):
            store.store_version("..", uuid.uuid4(), FILES)


class TestRoot:
    def test_root_is_the_resolved_content_root(self, store: ContentStore, root: Path):
        assert store.root == root.resolve()


class TestWriteVersion:
    def test_streams_to_workdir_and_renames_atomic(self, store: ContentStore, root: Path):
        version_id = uuid.uuid4()
        with store.write_version(SITE, version_id) as writer:
            with writer.open_file("index.html") as out:
                out.write(b"<h1>")
                out.write(b"hoi</h1>")
            with writer.open_file("assets/stijl.css") as out:
                out.write(b"body{}")
            # Nothing in the final place yet while the work dir is still open.
            assert not (root / str(SITE)).exists()
            assert len(list((root / "_tmp").iterdir())) == 1
        assert writer.storage_ref == f"{SITE}/{version_id}"
        assert (root / writer.storage_ref / "index.html").read_bytes() == b"<h1>hoi</h1>"
        assert (root / writer.storage_ref / "assets" / "stijl.css").read_bytes() == b"body{}"
        assert list((root / "_tmp").iterdir()) == []

    def test_exception_cleans_workdir_full_on(self, store: ContentStore, root: Path):
        with pytest.raises(RuntimeError), store.write_version(SITE, uuid.uuid4()) as writer:
            with writer.open_file("a/b/c.txt") as out:
                out.write(b"deels geschreven")
            raise RuntimeError("unpacking failed halfway")
        assert list((root / "_tmp").iterdir()) == []
        assert not (root / str(SITE)).exists()

    def test_open_file_refuses_unsafe_paths(self, store: ContentStore):
        with pytest.raises(RuntimeError), store.write_version(SITE, uuid.uuid4()) as writer:
            for path in ("/abs", "a/../b", "", "a\\b", "a\x00b", "./a", ".."):
                with pytest.raises(StoreError):
                    writer.open_file(path)
            raise RuntimeError("klaar")

    def test_open_file_refuses_collisions(self, store: ContentStore):
        with pytest.raises(RuntimeError), store.write_version(SITE, uuid.uuid4()) as writer:
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
        with pytest.raises(StoreError), store.write_version("..", uuid.uuid4()):
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
        ref = store.store_version(SITE, uuid.uuid4(), FILES)
        path = store.file_path(ref, "assets/stijl.css")
        assert path == root / ref / "assets" / "stijl.css"

    def test_none_on_absent_file(self, store: ContentStore):
        ref = store.store_version(SITE, uuid.uuid4(), FILES)
        assert store.file_path(ref, "bestaat-niet.html") is None

    def test_none_on_directory(self, store: ContentStore):
        ref = store.store_version(SITE, uuid.uuid4(), FILES)
        assert store.file_path(ref, "assets") is None

    def test_none_on_traversal_in_rel_path(self, store: ContentStore, root: Path):
        ref = store.store_version(SITE, uuid.uuid4(), FILES)
        (root / "geheim.txt").write_bytes(b"geheim")
        assert store.file_path(ref, "../../geheim.txt") is None
        assert store.file_path(ref, "/etc/passwd") is None
        assert store.file_path(ref, "a\x00b") is None

    def test_none_on_traversal_in_storage_ref(self, store: ContentStore, root: Path):
        (root.parent / "buiten.txt").write_bytes(b"x")
        assert store.file_path("../", "buiten.txt") is None
        assert store.file_path("..", "buiten.txt") is None

    def test_none_on_symlink_that_escapes(self, store: ContentStore, root: Path):
        ref = store.store_version(SITE, uuid.uuid4(), FILES)
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
        ref = store.store_version(SITE, uuid.uuid4(), FILES)
        store.delete_version(ref)
        assert not (root / ref).exists()

    def test_idempotent(self, store: ContentStore):
        ref = store.store_version(SITE, uuid.uuid4(), FILES)
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


class TestDeleteSite:
    def test_deletes_every_version_and_what_else_lies_there(self, store: ContentStore, root: Path):
        store.store_version(SITE, uuid.uuid4(), FILES)
        store.store_version(SITE, uuid.uuid4(), FILES)
        (root / str(SITE) / "zonder-rij").mkdir()
        other = store.store_version(uuid.uuid4(), uuid.uuid4(), FILES)
        store.delete_site(SITE)
        assert not (root / str(SITE)).exists()
        assert (root / other).is_dir()

    def test_idempotent(self, store: ContentStore):
        store.delete_site(SITE)
        store.store_version(SITE, uuid.uuid4(), FILES)
        store.delete_site(SITE)
        store.delete_site(SITE)


def _vanish(monkeypatch, gone: Path) -> None:
    """Makes `gone` disappear between the directory listing and its lstat."""
    original_lstat = Path.lstat

    def flaky_lstat(self: Path):
        if self == gone:
            raise OSError("verdwenen tussen de iterdir en de lstat")
        return original_lstat(self)

    monkeypatch.setattr(Path, "lstat", flaky_lstat)


def _age(path: Path) -> Path:
    os.utime(path, (OLD, OLD), follow_symlinks=False)
    return path


class TestReclaim:
    """`reclaim` gets the site ids and storage refs the database holds and
    moves what none of them points at into `_reclaimed`."""

    @staticmethod
    def _version(store: ContentStore, site_id: uuid.UUID, *, old: bool = True) -> str:
        ref = store.store_version(site_id, uuid.uuid4(), FILES)
        if old:
            _age(store.root / ref)
        return ref

    @staticmethod
    def _reclaimed(root: Path) -> dict[str, Path]:
        return {entry.name.split("-", 1)[1]: entry for entry in (root / "_reclaimed").iterdir()}

    def test_moves_a_version_directory_without_a_row(self, store: ContentStore, root: Path):
        kept = self._version(store, SITE)
        orphan = self._version(store, SITE)

        result = store.reclaim({str(SITE)}, {kept}, timedelta(hours=24))

        assert result.moved == [orphan]
        assert result.held == []
        assert (root / kept).is_dir()
        assert not (root / orphan).exists()
        moved = self._reclaimed(root)[orphan.replace("/", "_")]
        assert (moved / "index.html").read_bytes() == b"<html>"
        # The cooling-off period starts at the move, not at the version's age.
        assert moved.lstat().st_mtime > OLD

    def test_moves_the_directory_of_a_site_that_is_gone(self, store: ContentStore, root: Path):
        kept = self._version(store, SITE)
        gone_site = uuid.uuid4()
        self._version(store, gone_site)
        _age(root / str(gone_site))

        result = store.reclaim({str(SITE)}, {kept}, timedelta(hours=24))

        assert result.moved == [str(gone_site)]
        assert not (root / str(gone_site)).exists()
        assert set(self._reclaimed(root)) == {str(gone_site)}

    def test_leaves_what_is_younger_than_the_grace_period(self, store: ContentStore, root: Path):
        """A publish renames its directory into place before it inserts the
        row; in between, the directory has no row but is minutes old."""
        kept = self._version(store, SITE)
        in_flight = self._version(store, SITE, old=False)
        new_site = uuid.uuid4()
        self._version(store, new_site, old=False)

        result = store.reclaim({str(SITE)}, {kept}, timedelta(hours=24))

        assert result.moved == []
        assert (root / in_flight).is_dir()
        assert (root / str(new_site)).is_dir()
        assert not (root / "_reclaimed").exists()

    def test_never_touches_a_directory_with_a_row(self, store: ContentStore, root: Path):
        refs = {self._version(store, SITE) for _ in range(3)}

        result = store.reclaim({str(SITE)}, refs, timedelta(hours=24))

        assert result.moved == []
        assert result.held == []
        assert all((root / ref).is_dir() for ref in refs)

    def test_leaves_names_plak_never_writes(self, store: ContentStore, root: Path):
        """Only a directory named like a site id is Plak's: the volume's
        lost+found, a backup an operator put there or what the layout before
        site ids left behind are not, and neither is a file or a symlink."""
        kept = self._version(store, SITE)
        for name in ("lost+found", "_reclaimed", "backup", "team-aurora", ".verborgen"):
            _age(_mkdir(root / name))
        _age(root / "_tmp")
        stray = root / str(uuid.uuid4())
        stray.write_bytes(b"x")
        _age(stray)
        link = root / str(uuid.uuid4())
        link.symlink_to(root / kept)
        _age(link)
        upper = _age(_mkdir(root / str(uuid.uuid4()).upper()))

        result = store.reclaim({str(SITE)}, {kept}, timedelta(hours=24))

        assert result.moved == []
        assert {entry.name for entry in root.iterdir()} == {
            "_tmp", "_reclaimed", "lost+found", "backup", "team-aurora", ".verborgen",
            stray.name, link.name, upper.name, str(SITE),
        }

    def test_an_empty_directory_of_a_gone_site_weighs_nothing(self, store: ContentStore, root: Path):
        """The brake counts versions; an empty directory moves even when the
        database holds no version at all."""
        empty = _age(_mkdir(root / str(uuid.uuid4())))

        result = store.reclaim(set(), set(), timedelta(hours=24))

        assert result.moved == [empty.name]

    def test_holds_a_site_of_which_no_directory_has_a_row(self, store: ContentStore, root: Path):
        """The site exists, yet not one of its directories is a version the
        database knows: the two disagree about it, so it keeps everything."""
        kept = self._version(store, SITE)
        disputed = uuid.uuid4()
        refs = sorted(self._version(store, disputed) for _ in range(2))

        result = store.reclaim({str(SITE), str(disputed)}, {kept}, timedelta(hours=24))

        assert result.moved == []
        assert result.held == refs
        assert all((root / ref).is_dir() for ref in refs)

    def test_holds_everything_when_more_would_go_than_stay(self, store: ContentStore, root: Path):
        """What a wrong PLAK_DB_URL or a restored backup looks like: most of the
        volume suddenly has no row."""
        kept = self._version(store, SITE)
        orphans = [self._version(store, SITE) for _ in range(2)]

        result = store.reclaim({str(SITE)}, {kept}, timedelta(hours=24))

        assert result.moved == []
        assert sorted(result.held) == sorted(orphans)
        assert all((root / ref).is_dir() for ref in orphans)
        assert not (root / "_reclaimed").exists()

    def test_a_gone_site_weighs_its_versions(self, store: ContentStore, root: Path):
        kept = self._version(store, SITE)
        gone_site = uuid.uuid4()
        self._version(store, gone_site)
        self._version(store, gone_site)
        _age(root / str(gone_site))

        result = store.reclaim({str(SITE)}, {kept}, timedelta(hours=24))

        assert result.moved == []
        assert result.held == [str(gone_site)]

    def test_holds_everything_against_a_database_without_versions(self, store: ContentStore, root: Path):
        refs = [self._version(store, SITE) for _ in range(3)]
        _age(root / str(SITE))

        result = store.reclaim(set(), set(), timedelta(hours=24))

        assert result.moved == []
        assert result.held == [str(SITE)]
        assert all((root / ref).is_dir() for ref in refs)

    def test_as_many_going_as_staying_still_moves(self, store: ContentStore, root: Path):
        kept = self._version(store, SITE)
        orphan = self._version(store, SITE)

        assert store.reclaim({str(SITE)}, {kept}, timedelta(hours=24)).moved == [orphan]

    def test_a_directory_that_cannot_be_moved_is_not_reported_moved(
        self, store: ContentStore, root: Path, monkeypatch
    ):
        kept = self._version(store, SITE)
        orphan = self._version(store, SITE)

        def refuse(*_args):
            raise OSError("bezet")

        monkeypatch.setattr(os, "rename", refuse)
        result = store.reclaim({str(SITE)}, {kept}, timedelta(hours=24))
        monkeypatch.undo()

        assert result.moved == []
        assert (root / orphan).is_dir()

    def test_a_site_directory_that_disappears_during_the_walk_is_skipped(
        self, store: ContentStore, root: Path, monkeypatch
    ):
        kept = self._version(store, SITE)
        gone = _age(_mkdir(root / str(uuid.uuid4())))
        _vanish(monkeypatch, gone)

        assert store.reclaim({str(SITE)}, {kept}, timedelta(hours=24)).moved == []

    def test_a_version_directory_that_disappears_during_the_walk_is_skipped(
        self, store: ContentStore, root: Path, monkeypatch
    ):
        kept = self._version(store, SITE)
        orphan = self._version(store, SITE)
        _vanish(monkeypatch, root / orphan)

        assert store.reclaim({str(SITE)}, {kept}, timedelta(hours=24)).moved == []


def _mkdir(path: Path) -> Path:
    path.mkdir()
    return path


class TestSweepReclaimed:
    def test_nothing_reclaimed_yet_is_zero(self, store: ContentStore):
        assert store.sweep_reclaimed(timedelta(days=7)) == 0

    def test_removes_only_what_cooled_off(self, store: ContentStore, root: Path):
        kept = TestReclaim._version(store, SITE)
        orphan = TestReclaim._version(store, SITE)
        store.reclaim({str(SITE)}, {kept}, timedelta(hours=24))
        (fresh,) = (root / "_reclaimed").iterdir()
        cooled = _age(_mkdir(root / "_reclaimed" / "20260901T030000-oud"))

        assert store.sweep_reclaimed(timedelta(days=7)) == 1

        assert not cooled.exists()
        assert fresh.is_dir()
        assert fresh.name.endswith(orphan.replace("/", "_"))


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
        _vanish(monkeypatch, gone)
        assert store.sweep_tmp(timedelta(hours=1)) == 0
        monkeypatch.undo()
        assert gone.exists()


class TestMeasuring:
    def test_site_bytes_counts_every_version_and_is_zero_without_one(self, root: Path):
        store = ContentStore(root)
        assert store.site_bytes(SITE) == 0
        store.store_version(SITE, uuid.uuid4(), {"a.html": b"x" * 10})
        store.store_version(SITE, uuid.uuid4(), {"b.html": b"y" * 5})
        assert store.site_bytes(SITE) == 15
        # Another site on the same volume does not count towards it.
        store.store_version(uuid.uuid4(), uuid.uuid4(), {"c.html": b"z" * 99})
        assert store.site_bytes(SITE) == 15

    def test_a_file_that_disappears_during_the_walk_is_not_counted(self, root: Path, monkeypatch):
        """The cleanup job removes an expired preview while a deploy is being
        measured: the race may not make the deploy fail."""
        store = ContentStore(root)
        store.store_version(SITE, uuid.uuid4(), {"a.html": b"x" * 10})

        def gone(_path):
            raise FileNotFoundError

        monkeypatch.setattr(os, "lstat", gone)
        assert store.site_bytes(SITE) == 0

    def test_free_bytes_reports_the_volume(self, root: Path):
        assert ContentStore(root).free_bytes() > 0
