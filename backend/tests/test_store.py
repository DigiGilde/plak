"""Tests for the ContentStore: atomic storage (streaming and dict variant),
spool files, containment, the tmp sweeper and reclaiming directories that lost
their row."""

from __future__ import annotations

import asyncio
import os
import shutil
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from toon.ingest.store import SPOOL_SUFFIX, ContentStore, StoreError, version_ref

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
        assert ref == version_ref(SITE, version_id) == f"{SITE}/{version_id}"
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


def _set_age(path: Path, age: timedelta) -> Path:
    moment = (datetime.now(tz=UTC) - age).timestamp()
    os.utime(path, (moment, moment))
    return path


def _held(*refs: str, sites: tuple[uuid.UUID, ...] = (SITE,)) -> dict[str, set[str]]:
    """What the database holds, as `reclaim` gets it: the sites in `sites`,
    and the versions `refs` name."""
    held: dict[str, set[str]] = {str(site): set() for site in sites}
    for ref in refs:
        site, version = ref.split("/")
        held.setdefault(site, set()).add(version)
    return held


class TestReclaim:
    """`reclaim` gets the site ids and version ids the database holds and
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

        result = store.reclaim(_held(kept), timedelta(hours=24))

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

        result = store.reclaim(_held(kept), timedelta(hours=24))

        assert result.moved == [str(gone_site)]
        assert not (root / str(gone_site)).exists()
        assert set(self._reclaimed(root)) == {str(gone_site)}

    @pytest.mark.parametrize("delete", ["delete_site", "delete_version"])
    def test_waits_for_a_delete_in_progress(
        self, store: ContentStore, root: Path, monkeypatch: pytest.MonkeyPatch, delete: str
    ):
        """Deletes run in worker threads; the walk starts only once a delete
        has finished, so it never sees a directory half removed."""
        kept = self._version(store, SITE)
        deleted_site = uuid.uuid4()
        deleted = self._version(store, deleted_site)
        _age(root / str(deleted_site))
        removing, release = threading.Event(), threading.Event()
        rmtree = shutil.rmtree

        def held_rmtree(path: Path, *args: Any, **kwargs: Any) -> None:
            removing.set()
            assert release.wait(5)
            rmtree(path, *args, **kwargs)

        monkeypatch.setattr(shutil, "rmtree", held_rmtree)
        target = deleted_site if delete == "delete_site" else deleted
        deleter = threading.Thread(target=getattr(store, delete), args=(target,))
        deleter.start()
        assert removing.wait(5)
        results: list[Any] = []
        walker = threading.Thread(
            target=lambda: results.append(store.reclaim(_held(kept), timedelta(hours=24)))
        )
        walker.start()

        walker.join(0.2)
        assert walker.is_alive()
        release.set()
        deleter.join(5)
        walker.join(5)

        assert not walker.is_alive()
        assert results[0].moved == []
        assert (root / kept).is_dir()
        assert not (root / deleted).exists()

    def test_leaves_what_is_younger_than_the_grace_period(self, store: ContentStore, root: Path):
        """A publish renames its directory into place before it inserts the
        row; in between, the directory has no row but is minutes old."""
        kept = self._version(store, SITE)
        in_flight = self._version(store, SITE, old=False)
        new_site = uuid.uuid4()
        self._version(store, new_site, old=False)

        result = store.reclaim(_held(kept), timedelta(hours=24))

        assert result.moved == []
        assert (root / in_flight).is_dir()
        assert (root / str(new_site)).is_dir()
        assert not (root / "_reclaimed").exists()

    def test_never_touches_a_directory_with_a_row(self, store: ContentStore, root: Path):
        refs = {self._version(store, SITE) for _ in range(3)}

        result = store.reclaim(_held(*refs), timedelta(hours=24))

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

        result = store.reclaim(_held(kept), timedelta(hours=24))

        assert result.moved == []
        assert {entry.name for entry in root.iterdir()} == {
            "_tmp", "_reclaimed", "lost+found", "backup", "team-aurora", ".verborgen",
            stray.name, link.name, upper.name, str(SITE),
        }

    def test_an_empty_directory_of_a_gone_site_weighs_nothing(self, store: ContentStore, root: Path):
        """The brake counts versions; an empty directory moves even when the
        database holds no version at all."""
        empty = _age(_mkdir(root / str(uuid.uuid4())))

        result = store.reclaim({}, timedelta(hours=24))

        assert result.moved == [empty.name]

    def test_holds_a_site_whose_version_rows_match_none_of_its_directories(self, store: ContentStore, root: Path):
        """The database has versions for the site, yet not one of them is a
        directory on the volume: the two disagree about it, so it keeps
        everything, even though the global brake would let one directory go."""
        kept = self._version(store, SITE)
        disputed = uuid.uuid4()
        leftover = self._version(store, disputed)
        elsewhere = f"{disputed}/{uuid.uuid4()}"

        result = store.reclaim(_held(kept, elsewhere), timedelta(hours=24))

        assert result.moved == []
        assert result.held == [leftover]
        assert (root / leftover).is_dir()

    def test_moves_the_leftover_of_a_site_without_any_version_row(self, store: ContentStore, root: Path):
        """A first publish that stopped between its rename and its insert, or a
        site whose versions were all deleted: the database has nothing on the
        site to disagree with, so the leftover is an ordinary orphan and does
        not count against the site's quota forever."""
        kept = self._version(store, SITE)
        started = uuid.uuid4()
        leftover = self._version(store, started)

        result = store.reclaim(_held(kept, sites=(SITE, started)), timedelta(hours=24))

        assert result.moved == [leftover]
        assert result.held == []
        assert not (root / leftover).exists()
        assert (root / str(started)).is_dir()
        assert set(self._reclaimed(root)) == {leftover.replace("/", "_")}

    def test_the_global_brake_still_holds_a_site_without_any_version_row(self, store: ContentStore, root: Path):
        kept = self._version(store, SITE)
        started = uuid.uuid4()
        leftovers = sorted(self._version(store, started) for _ in range(2))

        result = store.reclaim(_held(kept, sites=(SITE, started)), timedelta(hours=24))

        assert result.moved == []
        assert result.held == leftovers
        assert all((root / ref).is_dir() for ref in leftovers)
        assert not (root / "_reclaimed").exists()

    def test_the_grace_period_is_a_boundary_on_the_mtime(self, store: ContentStore, root: Path):
        kept = self._version(store, SITE)
        younger = self._version(store, SITE, old=False)
        _set_age(root / younger, timedelta(hours=23, minutes=59))
        older = self._version(store, SITE, old=False)
        _set_age(root / older, timedelta(hours=24, minutes=1))

        result = store.reclaim(_held(kept), timedelta(hours=24))

        assert result.moved == [older]
        assert result.held == []
        assert (root / younger).is_dir()
        assert not (root / older).exists()

    def test_holds_everything_when_more_would_go_than_stay(self, store: ContentStore, root: Path):
        """What a wrong PLAK_DB_URL or a restored backup looks like: most of the
        volume suddenly has no row."""
        kept = self._version(store, SITE)
        orphans = [self._version(store, SITE) for _ in range(2)]

        result = store.reclaim(_held(kept), timedelta(hours=24))

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

        result = store.reclaim(_held(kept), timedelta(hours=24))

        assert result.moved == []
        assert result.held == [str(gone_site)]

    def test_holds_everything_against_a_database_without_versions(self, store: ContentStore, root: Path):
        refs = [self._version(store, SITE) for _ in range(3)]
        _age(root / str(SITE))

        result = store.reclaim({}, timedelta(hours=24))

        assert result.moved == []
        assert result.held == [str(SITE)]
        assert all((root / ref).is_dir() for ref in refs)

    def test_as_many_going_as_staying_still_moves(self, store: ContentStore, root: Path):
        kept = self._version(store, SITE)
        orphan = self._version(store, SITE)

        assert store.reclaim(_held(kept), timedelta(hours=24)).moved == [orphan]

    def test_a_directory_that_cannot_be_moved_is_not_reported_moved(
        self, store: ContentStore, root: Path, monkeypatch
    ):
        kept = self._version(store, SITE)
        orphan = self._version(store, SITE)

        def refuse(*_args):
            raise OSError("bezet")

        monkeypatch.setattr(os, "rename", refuse)
        result = store.reclaim(_held(kept), timedelta(hours=24))
        monkeypatch.undo()

        assert result.moved == []
        assert (root / orphan).is_dir()

    def test_a_site_directory_that_disappears_during_the_walk_is_skipped(
        self, store: ContentStore, root: Path, monkeypatch
    ):
        kept = self._version(store, SITE)
        gone = _age(_mkdir(root / str(uuid.uuid4())))
        _vanish(monkeypatch, gone)

        assert store.reclaim(_held(kept), timedelta(hours=24)).moved == []

    def test_a_version_directory_that_disappears_during_the_walk_is_skipped(
        self, store: ContentStore, root: Path, monkeypatch
    ):
        kept = self._version(store, SITE)
        orphan = self._version(store, SITE)
        _vanish(monkeypatch, root / orphan)

        assert store.reclaim(_held(kept), timedelta(hours=24)).moved == []


def _mkdir(path: Path) -> Path:
    path.mkdir()
    return path


class TestSweepReclaimed:
    def test_nothing_reclaimed_yet_is_zero(self, store: ContentStore):
        assert store.sweep_reclaimed(timedelta(days=7)) == 0

    def test_removes_only_what_cooled_off(self, store: ContentStore, root: Path):
        kept = TestReclaim._version(store, SITE)
        orphan = TestReclaim._version(store, SITE)
        store.reclaim(_held(kept), timedelta(hours=24))
        (fresh,) = (root / "_reclaimed").iterdir()
        cooled = _age(_mkdir(root / "_reclaimed" / "20260901T030000-oud"))

        assert store.sweep_reclaimed(timedelta(days=7)) == 1

        assert not cooled.exists()
        assert fresh.is_dir()
        assert fresh.name.endswith(orphan.replace("/", "_"))

    def test_the_cooling_off_period_is_a_boundary_on_the_mtime(self, store: ContentStore, root: Path):
        reclaimed = _mkdir(root / "_reclaimed")
        younger = _set_age(_mkdir(reclaimed / "20260901T030000-jonger"), timedelta(days=6, hours=23, minutes=59))
        older = _set_age(_mkdir(reclaimed / "20260901T030000-ouder"), timedelta(days=7, minutes=1))

        assert store.sweep_reclaimed(timedelta(days=7)) == 1

        assert younger.is_dir()
        assert not older.exists()


class TestDeleteVersions:
    async def test_deletes_every_tree(self, store: ContentStore, root: Path):
        refs = [store.store_version(SITE, uuid.uuid4(), FILES) for _ in range(3)]
        await store.delete_versions(refs)
        assert not any((root / ref).exists() for ref in refs)

    async def test_runs_off_the_event_loop_thread(self, store: ContentStore, monkeypatch):
        ref = store.store_version(SITE, uuid.uuid4(), FILES)
        threads = []
        real = store.delete_version

        def recording(storage_ref: str) -> None:
            threads.append(threading.current_thread())
            real(storage_ref)

        monkeypatch.setattr(store, "delete_version", recording)
        await store.delete_versions(iter([ref]))
        assert threads and threading.main_thread() not in threads

    async def test_nothing_to_delete_starts_no_thread(self, store: ContentStore, monkeypatch):
        def refuse(*_args, **_kwargs):
            raise AssertionError("no thread for an empty list")

        monkeypatch.setattr(asyncio, "to_thread", refuse)
        await store.delete_versions([])

    async def test_a_refused_ref_surfaces(self, store: ContentStore):
        with pytest.raises(StoreError):
            await store.delete_versions(["../extern"])


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


def _version_with_copies(store: ContentStore) -> str:
    """A version whose `a/b.css` is kept compressed only."""
    version_id = uuid.uuid4()
    with store.write_version(SITE, version_id) as writer:
        with writer.open_file("a/b.css") as out:
            out.write(b"plain" * 20)
        with writer.open_file("index.html") as out:
            out.write(b"<html>")
        with writer.open_encoded("a/b.css", "gzip") as out:
            out.write(b"GZ")
        with writer.open_encoded("a/b.css", "br") as out:
            out.write(b"BR")
        writer.drop_plain("a/b.css")
    return f"{SITE}/{version_id}"


class TestVersionWriterFiles:
    def test_lists_every_written_file_sorted_with_posix_paths(self, store: ContentStore):
        names = ["z.html", "assets/stijl.css", "één map/bestand met spatie.txt", "a/b/c.js"]
        with store.write_version(SITE, uuid.uuid4()) as writer:
            for name in names:
                with writer.open_file(name) as out:
                    out.write(name.encode())
            found = writer.files()
            assert [rel for rel, _ in found] == sorted(names)
            for rel, path in found:
                assert path.read_bytes() == rel.encode()

    def test_is_empty_before_anything_is_written(self, store: ContentStore):
        with store.write_version(SITE, uuid.uuid4()) as writer:
            assert writer.files() == []

    def test_leaves_out_the_compressed_copies_in_the_root(self, store: ContentStore):
        with store.write_version(SITE, uuid.uuid4()) as writer:
            with writer.open_file("index.html") as out:
                out.write(b"x")
            with writer.open_encoded("index.html", "br") as out:
                out.write(b"y")
            assert [rel for rel, _ in writer.files()] == ["index.html"]

    def test_lists_a_deeper_directory_named_encoded_as_site_files(self, store: ContentStore):
        with store.write_version(SITE, uuid.uuid4()) as writer:
            with writer.open_file("assets/_encoded/x.css") as out:
                out.write(b"x")
            with writer.open_file("index.html") as out:
                out.write(b"x")
            with writer.open_encoded("index.html", "br") as out:
                out.write(b"y")
            assert [rel for rel, _ in writer.files()] == ["assets/_encoded/x.css", "index.html"]


class TestVersionWriterEncoded:
    def test_writes_under_encoded_in_the_workdir(self, store: ContentStore, root: Path):
        with store.write_version(SITE, uuid.uuid4()) as writer:
            with writer.open_encoded("a/b.css", "br") as out:
                out.write(b"variant")
            found = [p for p in (root / "_tmp").rglob("*") if p.is_file()]
            assert len(found) == 1
            assert found[0].as_posix().endswith("/_encoded/br/a/b.css")

    def test_refuses_the_same_copy_twice(self, store: ContentStore):
        with store.write_version(SITE, uuid.uuid4()) as writer:
            with writer.open_encoded("a.css", "gzip") as out:
                out.write(b"1")
            with pytest.raises(FileExistsError):
                writer.open_encoded("a.css", "gzip")
            # Another encoding of the same file is a different copy.
            with writer.open_encoded("a.css", "br") as out:
                out.write(b"2")

    @pytest.mark.parametrize("rel_path", ["../x", "/abs", "a\\b", "", "./a", "a/../b", "a\x00b"])
    def test_refuses_an_unsafe_rel_path(self, store: ContentStore, rel_path: str):
        with store.write_version(SITE, uuid.uuid4()) as writer:
            with pytest.raises(StoreError):
                writer.open_encoded(rel_path, "br")
            with pytest.raises(StoreError):
                writer.discard_encoded(rel_path, "br")
            with pytest.raises(StoreError):
                writer.drop_plain(rel_path)
            assert writer.total_bytes() == 0

    @pytest.mark.parametrize("encoding", ["..", "/br", "br\\x", "", "a/../b"])
    def test_refuses_an_unsafe_encoding(self, store: ContentStore, encoding: str):
        with store.write_version(SITE, uuid.uuid4()) as writer:
            with pytest.raises(StoreError):
                writer.open_encoded("a.css", encoding)
            with pytest.raises(StoreError):
                writer.discard_encoded("a.css", encoding)

    def test_discard_removes_the_copy_and_it_can_be_written_again(self, store: ContentStore):
        with store.write_version(SITE, uuid.uuid4()) as writer:
            with writer.open_encoded("a.css", "br") as out:
                out.write(b"1")
            with writer.open_encoded("a.css", "gzip") as out:
                out.write(b"22")
            writer.discard_encoded("a.css", "br")
            assert writer.total_bytes() == 2
            with writer.open_encoded("a.css", "br") as out:
                out.write(b"333")
            assert writer.total_bytes() == 5

    def test_discarding_a_copy_that_is_not_there_raises(self, store: ContentStore):
        with store.write_version(SITE, uuid.uuid4()) as writer, pytest.raises(FileNotFoundError):
            writer.discard_encoded("a.css", "br")

    def test_drop_plain_removes_only_that_file(self, store: ContentStore):
        with store.write_version(SITE, uuid.uuid4()) as writer:
            for name in ("a.css", "b.css"):
                with writer.open_file(name) as out:
                    out.write(b"x" * 10)
            writer.drop_plain("a.css")
            assert [rel for rel, _ in writer.files()] == ["b.css"]
            assert writer.total_bytes() == 10

    def test_drop_plain_of_a_missing_file_raises(self, store: ContentStore):
        with store.write_version(SITE, uuid.uuid4()) as writer, pytest.raises(FileNotFoundError):
            writer.drop_plain("niet-aanwezig.css")


class TestEncodedInTheVersion:
    def test_copies_sit_inside_the_version_next_to_the_content(self, store: ContentStore, root: Path):
        ref = _version_with_copies(store)
        version = root / ref
        assert (version / "_encoded" / "gzip" / "a" / "b.css").read_bytes() == b"GZ"
        assert (version / "_encoded" / "br" / "a" / "b.css").read_bytes() == b"BR"
        assert not (version / "a" / "b.css").exists()
        assert (version / "index.html").read_bytes() == b"<html>"
        assert not (root / "_encoded").exists()
        assert list((root / "_tmp").iterdir()) == []

    def test_an_error_leaves_nothing_behind(self, store: ContentStore, root: Path):
        version_id = uuid.uuid4()
        with pytest.raises(RuntimeError), store.write_version(SITE, version_id) as writer:
            with writer.open_encoded("index.html", "br") as out:
                out.write(b"y")
            raise RuntimeError("boom")
        assert not (root / str(SITE) / str(version_id)).exists()
        assert list((root / "_tmp").iterdir()) == []

    def test_total_bytes_counts_the_copies_and_not_the_dropped_plain_file(self, store: ContentStore):
        with store.write_version(SITE, uuid.uuid4()) as writer:
            with writer.open_file("a.css") as out:
                out.write(b"x" * 100)
            with writer.open_file("b.html") as out:
                out.write(b"y" * 10)
            with writer.open_encoded("a.css", "br") as out:
                out.write(b"z" * 7)
            with writer.open_encoded("a.css", "gzip") as out:
                out.write(b"w" * 8)
            assert writer.total_bytes() == 125
            writer.drop_plain("a.css")
            assert writer.total_bytes() == 25

    def test_site_bytes_counts_the_copies(self, store: ContentStore):
        _version_with_copies(store)
        assert store.site_bytes(SITE) == len(b"<html>") + 2 + 2

    def test_delete_version_takes_the_copies_along(self, store: ContentStore, root: Path):
        ref = _version_with_copies(store)
        store.delete_version(ref)
        assert not (root / ref).exists()
        assert store.encoded_path(ref, "a/b.css", "gzip") is None

    def test_reclaim_quarantines_the_copies_with_their_version(self, store: ContentStore, root: Path):
        kept = _version_with_copies(store)
        orphan = _version_with_copies(store)
        _age(root / orphan)

        result = store.reclaim(_held(kept), timedelta(hours=24))

        assert result.moved == [orphan]
        assert not (root / orphan).exists()
        (moved,) = (root / "_reclaimed").iterdir()
        assert moved.name.endswith("-" + orphan.replace("/", "_"))
        assert (moved / "_encoded" / "gzip" / "a" / "b.css").is_file()
        assert store.encoded_path(kept, "a/b.css", "gzip") is not None


class TestEncodedPath:
    def test_returns_the_copy(self, store: ContentStore, root: Path):
        ref = _version_with_copies(store)
        path = store.encoded_path(ref, "a/b.css", "br")
        assert path == (root / ref / "_encoded" / "br" / "a" / "b.css").resolve()
        assert path is not None and path.read_bytes() == b"BR"

    def test_none_for_a_missing_copy(self, store: ContentStore):
        ref = _version_with_copies(store)
        assert store.encoded_path(ref, "a/other.css", "br") is None
        assert store.encoded_path(ref, "index.html", "br") is None

    def test_none_for_a_missing_encoding(self, store: ContentStore):
        ref = _version_with_copies(store)
        assert store.encoded_path(ref, "a/b.css", "zstd") is None

    def test_none_for_a_version_without_copies(self, store: ContentStore):
        ref = store.store_version(SITE, uuid.uuid4(), FILES)
        assert store.encoded_path(ref, "index.html", "br") is None

    def test_none_on_traversal(self, store: ContentStore, root: Path):
        ref = _version_with_copies(store)
        (root / "geheim.txt").write_bytes(b"geheim")
        assert store.encoded_path(ref, "../../../../geheim.txt", "br") is None
        assert store.encoded_path(ref, "../gzip/a/b.css", "br") is None
        assert store.encoded_path(ref, "../../index.html", "br") is None
        assert store.encoded_path(ref, "/etc/passwd", "br") is None
        assert store.encoded_path(ref, "a\x00b", "br") is None
        assert store.encoded_path(ref, "a/b.css", "../gzip") is None
        assert store.encoded_path(ref, "index.html", "..") is None

    def test_none_for_the_encoding_base_itself(self, store: ContentStore):
        ref = _version_with_copies(store)
        assert store.encoded_path(ref, "", "br") is None
        assert store.encoded_path(ref, ".", "br") is None

    def test_none_for_a_directory(self, store: ContentStore):
        ref = _version_with_copies(store)
        assert store.encoded_path(ref, "a", "br") is None

    def test_none_on_a_symlink_that_escapes(self, store: ContentStore, root: Path):
        ref = _version_with_copies(store)
        outside = root.parent / "buiten.txt"
        outside.write_bytes(b"geheim")
        (root / ref / "_encoded" / "br" / "ontsnap").symlink_to(outside)
        assert store.encoded_path(ref, "ontsnap", "br") is None

    def test_none_on_an_invalid_storage_ref(self, store: ContentStore, root: Path):
        (root / "_tmp" / "x" / "_encoded" / "br").mkdir(parents=True)
        (root / "_tmp" / "x" / "_encoded" / "br" / "a.css").write_bytes(b"x")
        (root.parent / "buiten" / "_encoded" / "br").mkdir(parents=True)
        (root.parent / "buiten" / "_encoded" / "br" / "a.css").write_bytes(b"x")
        for ref in ("..", "../buiten", "_tmp/x", "a/\x00"):
            assert store.encoded_path(ref, "a.css", "br") is None


class TestFilePathHidesEncoded:
    @pytest.fixture
    def ref(self, store: ContentStore, root: Path) -> str:
        ref = _version_with_copies(store)
        deeper = root / ref / "assets" / "_encoded"
        deeper.mkdir(parents=True)
        (deeper / "x.css").write_bytes(b"site file")
        return ref

    @pytest.mark.parametrize(
        "rel_path",
        [
            "_encoded/gzip/a/b.css",
            "_encoded/br/a/b.css",
            "./_encoded/br/a/b.css",
            "_encoded",
            "_encoded/",
            "a/../_encoded/gzip/a/b.css",
        ],
    )
    def test_refuses_anything_under_the_root_encoded_directory(self, store: ContentStore, ref: str, rel_path: str):
        assert store.file_path(ref, rel_path) is None

    def test_refuses_a_symlink_that_points_into_encoded(self, store: ContentStore, root: Path, ref: str):
        (root / ref / "doorgang.css").symlink_to(root / ref / "_encoded" / "gzip" / "a" / "b.css")
        assert store.file_path(ref, "doorgang.css") is None

    def test_serves_a_deeper_directory_named_encoded(self, store: ContentStore, root: Path, ref: str):
        assert store.file_path(ref, "assets/_encoded/x.css") == root / ref / "assets" / "_encoded" / "x.css"

    def test_still_serves_the_plain_files(self, store: ContentStore, root: Path, ref: str):
        assert store.file_path(ref, "index.html") == root / ref / "index.html"
