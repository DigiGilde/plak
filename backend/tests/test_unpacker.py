"""Tests for the unpacker: dispatch, fail-closed path validation, the bomb
guard and streaming (the archive is never held in memory as a whole)."""

from __future__ import annotations

import gzip
import io
import stat
import struct
import tarfile
import tracemalloc
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import IO, ClassVar

import pytest

from plak.config import Settings
from plak.ingest.unpacker import (
    BundleError,
    Limits,
    _safe_segments,
    _Tree,
    _write_entry,
    is_archive_junk,
)
from plak.ingest.unpacker import (
    unpack as unpack_archive,
)

LIMITS = Limits(max_file=100_000, max_total=200_000, max_files=10, max_depth=5)

UnpackFn = Callable[..., dict[str, bytes]]


class DirDestination:
    """Minimal destination: writes under a directory (in production the store does this)."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def open_file(self, rel_path: str) -> IO[bytes]:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path.open("xb")


def read_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()
    }


def make_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buf.getvalue()


def make_tar(
    entries: dict[str, bytes],
    links: tuple[tuple[str, str, bytes], ...] = (),
    dirs: tuple[str, ...] = (),
    tar_format: int = tarfile.DEFAULT_FORMAT,
) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tar_format) as archive:
        for name in dirs:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name, content in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        for name, target, kind in links:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = target
            archive.addfile(info)
    return buf.getvalue()


def make_tar_header_claim(real: tuple[int, ...], claim: int) -> bytes:
    """tar.gz with members of the given real sizes, followed by a member of
    which only the header is in the archive: it claims `claim` bytes that are
    not behind it.

    That makes visible what the pre-pass does. If it skips over the claimed
    data (which in a gzip stream amounts to decompressing all of it), it runs
    into the end of the archive and INVALID_ARCHIVE follows; if it refuses on
    the claimed size, the limit error follows.
    """
    raw_value = b""
    for i, size in enumerate(real):
        info = tarfile.TarInfo(f"b{i}.bin")
        info.size = size
        raw_value += info.tobuf() + b"\0" * (size + -size % tarfile.BLOCKSIZE)
    large = tarfile.TarInfo("groot.bin")
    large.size = claim
    return gzip.compress(raw_value + large.tobuf())


def reason_of(excinfo: pytest.ExceptionInfo[BundleError]) -> str:
    return excinfo.value.reason


@pytest.fixture
def unpack(tmp_path: Path) -> UnpackFn:
    """Writes `data` to disk as an upload, unpacks into a fresh directory and
    returns the written tree as {relative path: content}."""

    def _pak(
        filename: str,
        data: bytes,
        limits: Limits = LIMITS,
        base_path: str | None = None,
    ) -> dict[str, bytes]:
        source = tmp_path / "upload" / f"{uuid.uuid4().hex}-{filename}"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(data)
        target = tmp_path / "target" / uuid.uuid4().hex
        target.mkdir(parents=True)
        count = unpack_archive(filename, source, DirDestination(target), limits, base_path=base_path)
        tree = read_tree(target)
        assert count == len(tree)
        return tree

    return _pak


class TestDispatch:
    def test_single_html_becomes_index_html(self, unpack: UnpackFn):
        assert unpack("rapport.html", b"<h1>hoi</h1>") == {"index.html": b"<h1>hoi</h1>"}

    def test_html_uppercase_in_name(self, unpack: UnpackFn):
        assert unpack("Rapport.HTML", b"x") == {"index.html": b"x"}

    def test_zip_with_subdirs(self, unpack: UnpackFn):
        data = make_zip({"index.html": b"<html>", "assets/stijl.css": b"body{}"})
        assert unpack("site.zip", data) == {"index.html": b"<html>", "assets/stijl.css": b"body{}"}

    def test_tar_gz_and_tgz(self, unpack: UnpackFn):
        data = make_tar({"index.html": b"<html>", "docs/a.txt": b"a"})
        expected = {"index.html": b"<html>", "docs/a.txt": b"a"}
        assert unpack("site.tar.gz", data) == expected
        assert unpack("site.tgz", data) == expected

    def test_unknown_format_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.rar", b"x")
        assert reason_of(error) == "UNKNOWN_FORMAT"

    def test_broken_zip_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", b"geen zip")
        assert reason_of(error) == "INVALID_ARCHIVE"

    def test_broken_targz_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", b"geen tar")
        assert reason_of(error) == "INVALID_ARCHIVE"

    def test_truncated_targz_refused(self, unpack: UnpackFn):
        data = make_tar({"index.html": b"x" * 50_000})
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data[: len(data) // 2])
        assert reason_of(error) == "INVALID_ARCHIVE"

    def test_empty_archive_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({}))
        assert reason_of(error) == "EMPTY_ARCHIVE"

    def test_missing_source_refused(self, tmp_path: Path):
        with pytest.raises(BundleError) as error:
            unpack_archive("site.zip", tmp_path / "bestaat-niet.zip", DirDestination(tmp_path / "d"), LIMITS)
        assert reason_of(error) == "INVALID_ARCHIVE"


class TestPathValidation:
    def test_traversal_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"../x": b"1"}))
        assert reason_of(error) == "PATH_TRAVERSAL"

    def test_nested_traversal_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"a/../../x": b"1"}))
        assert reason_of(error) == "PATH_TRAVERSAL"

    def test_absolute_path_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"/etc/passwd": b"1"}))
        assert reason_of(error) == "ABSOLUTE_PATH"

    def test_windows_drive_path_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"C:\\x": b"1"}))
        assert reason_of(error) == "ABSOLUTE_PATH"

    def test_null_byte_refused(self):
        with pytest.raises(BundleError) as error:
            _safe_segments("a\x00b")
        assert reason_of(error) == "NULL_BYTE"

    def test_zip_null_byte_in_the_name_refused(self, unpack: UnpackFn):
        """zipfile cuts an entry name off at the null byte too, in both the
        writer and the reader, so the archive has to be patched by hand to
        carry one. `orig_filename` still holds it, and that is what is refused
        on."""
        marker = "\x7f"
        data = make_zip({"index.html": b"<h1>hoi</h1>", f"index.html{marker}.bak": b"kwaad"})
        data = data.replace(f"index.html{marker}.bak".encode(), b"index.html\x00.bak")
        with pytest.raises(BundleError) as error:
            unpack("site.zip", data)
        assert reason_of(error) == "NULL_BYTE"
        assert "index.html\\x00.bak" in str(error.value)

    def test_tar_null_byte_in_the_name_refused(self, unpack: UnpackFn):
        """tarfile cuts a member name off at the null byte before the unpacker
        sees it, so an entry declared `index.html\\0.bak` would quietly become
        the index.html of the site: the archive listing and what the server
        serves then say different things. The raw header still shows the byte,
        and that is what is refused on."""
        data = make_tar({"index.html": b"<h1>hoi</h1>", "index.html\x00.bak": b"kwaad"})
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "NULL_BYTE"
        assert "index.html\\x00.bak" in str(error.value)

    def test_tar_null_byte_in_a_gnu_long_name_refused(self, unpack: UnpackFn):
        # A name too long for the header sits in a block of its own; the GNU
        # format truncates it at the null byte just the same.
        name = "n" * 120 + "\x00.bak"
        data = make_tar({"index.html": b"h", name: b"kwaad"}, tar_format=tarfile.GNU_FORMAT)
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "NULL_BYTE"

    def test_tar_null_byte_past_512_bytes_in_a_gnu_long_name_refused(self, unpack: UnpackFn):
        # CPython reads a GNU long name in one call sized to the name, not to
        # BLOCKSIZE: past 512 bytes that read no longer looks like a header
        # block by its size, which is what the fix has to see past.
        name = "n" * 600 + "\x00.bak"
        data = make_tar({"index.html": b"h", name: b"kwaad"}, tar_format=tarfile.GNU_FORMAT)
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "NULL_BYTE"

    def test_tar_null_byte_before_512_bytes_in_a_long_gnu_name_refused(self, unpack: UnpackFn):
        # Same long-name payload, but the null byte sits in the first 512
        # bytes of it: the counter-case to the one above, so the fix is not
        # one that only looks past byte 512.
        name = "n" * 100 + "\x00" + "y" * 600
        data = make_tar({"index.html": b"h", name: b"kwaad"}, tar_format=tarfile.GNU_FORMAT)
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "NULL_BYTE"

    def test_tar_long_gnu_name_past_512_bytes_without_a_null_byte_is_unpacked(self, unpack: UnpackFn):
        # The counter-test to the two above: a long name past 512 bytes is
        # not itself a reason to refuse. Split over directories so no single
        # segment trips a filesystem's own filename length limit; the raw
        # long-name payload itself is still well past 512 bytes.
        name = "/".join(["a" * 150, "b" * 150, "c" * 150, "d" * 60 + ".html"])
        assert len(name) > 512
        data = make_tar({"index.html": b"h", name: b"x"}, tar_format=tarfile.GNU_FORMAT)
        assert unpack("site.tar.gz", data) == {"index.html": b"h", name: b"x"}

    def test_tar_null_byte_in_a_pax_long_name_refused(self, unpack: UnpackFn):
        # The pax format hands the null byte over unharmed, so there the path
        # check itself refuses. The counter-test to the two above: both routes
        # end in the same refusal.
        name = "n" * 120 + "\x00.bak"
        data = make_tar({"index.html": b"h", name: b"kwaad"}, tar_format=tarfile.PAX_FORMAT)
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "NULL_BYTE"

    def test_tar_long_names_without_a_null_byte_are_unpacked(self, unpack: UnpackFn):
        # The counter-test to the refusals above: a long name is not itself a
        # reason to refuse, in either format.
        name = "n" * 120 + ".html"
        for tar_format in (tarfile.GNU_FORMAT, tarfile.PAX_FORMAT):
            data = make_tar({"index.html": b"h", name: b"x"}, tar_format=tar_format)
            assert unpack("site.tar.gz", data) == {"index.html": b"h", name: b"x"}

    def test_empty_path_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({".": b"1"}))
        assert reason_of(error) == "EMPTY_PATH"

    def test_backslash_normalised(self, unpack: UnpackFn):
        data = make_zip({"index.html": b"h", "sub\\a.txt": b"1"})
        assert unpack("site.zip", data) == {"index.html": b"h", "sub/a.txt": b"1"}

    def test_preview_in_the_root_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"index.html": b"h", "_preview/x.html": b"1"}))
        assert reason_of(error) == "RESERVED_SEGMENT"
        assert "in the root of the bundle" in str(error.value)

    def test_version_in_the_root_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"_version": b"1"}))
        assert reason_of(error) == "RESERVED_SEGMENT"

    def test_nested_reserved_segment_allowed(self, unpack: UnpackFn):
        # The entry in the root keeps 'docs' an ordinary directory: without
        # that entry 'docs' would be stripped off (see TestOmhullendeMap).
        data = make_zip({"index.html": b"h", "docs/_preview/x.html": b"1"})
        assert unpack("site.zip", data) == {"index.html": b"h", "docs/_preview/x.html": b"1"}

    def test_duplicate_path_refused(self, unpack: UnpackFn):
        buf = io.BytesIO()
        with (
            pytest.warns(UserWarning, match="Duplicate name"),
            zipfile.ZipFile(buf, "w") as archive,
        ):
            archive.writestr("a.txt", b"1")
            archive.writestr("a.txt", b"2")
        with pytest.raises(BundleError) as error:
            unpack("site.zip", buf.getvalue())
        assert reason_of(error) == "DUPLICATE_PATH"

    def test_file_and_dir_conflict_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"a": b"1", "a/b": b"2"}))
        assert reason_of(error) == "DUPLICATE_PATH"

    def test_dir_and_file_conflict_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"a/b": b"2", "a": b"1"}))
        assert reason_of(error) == "DUPLICATE_PATH"

    def test_refused_entry_does_not_become_written(self, tmp_path: Path):
        """Checks come before the target file is opened: a refused entry leaves
        no (partial) file behind, earlier good entries do (the store cleans up
        the whole work dir)."""
        source = tmp_path / "site.zip"
        source.write_bytes(make_zip({"ok.html": b"x", "../kwaad": b"y", "later.html": b"z"}))
        target = tmp_path / "target"
        target.mkdir()
        with pytest.raises(BundleError):
            unpack_archive("site.zip", source, DirDestination(target), LIMITS)
        assert read_tree(target) == {"ok.html": b"x"}


class TestWrappingDirectory:
    """Compressing the folder `dist` in Finder gives an archive in which
    everything sits under `dist/`. If that one directory is alone in the root
    it becomes the new top level, and that repeats as long as the root holds
    exactly one directory; if anything sits beside it, nothing changes."""

    SITE: ClassVar[dict[str, bytes]] = {"index.html": b"<h1>hoi</h1>", "assets/stijl.css": b"body{}"}

    def test_zip_with_dir_entries_peeled(self, unpack: UnpackFn):
        entries = {"dist/": b"", "dist/assets/": b""} | {f"dist/{p}": i for p, i in self.SITE.items()}
        assert unpack("site.zip", make_zip(entries)) == self.SITE

    def test_zip_without_dir_entries_peeled(self, unpack: UnpackFn):
        # Many zippers put only file entries in the archive; the directory
        # structure then shows from the file paths alone.
        entries = {f"dist/{path}": content for path, content in self.SITE.items()}
        assert unpack("site.zip", make_zip(entries)) == self.SITE

    def test_tar_with_dir_entries_peeled(self, unpack: UnpackFn):
        entries = {f"dist/{path}": content for path, content in self.SITE.items()}
        data = make_tar(entries, dirs=("dist", "dist/assets"))
        assert unpack("site.tar.gz", data) == self.SITE

    def test_tar_without_dir_entries_peeled(self, unpack: UnpackFn):
        entries = {f"dist/{path}": content for path, content in self.SITE.items()}
        assert unpack("site.tar.gz", make_tar(entries)) == self.SITE

    def test_whole_chain_peeled(self, unpack: UnpackFn):
        """A directory in a directory in a directory: all of it comes off,
        because by definition nothing sits beside it."""
        entries = {"mijnproject/build/dist/index.html": b"<h1>hoi</h1>"}
        expected = {"index.html": b"<h1>hoi</h1>"}
        assert unpack("site.zip", make_zip(entries)) == expected
        assert unpack("site.tar.gz", make_tar(entries)) == expected

    def test_chain_with_dir_entries_peeled(self, unpack: UnpackFn):
        """Directory entries along the way are intermediate directories, not
        neighbours: `dist/` does not say anything lies beside `dist/sub/`."""
        entries = {"dist/sub/index.html": b"<h1>hoi</h1>"}
        zip_data = make_zip({"dist/": b"", "dist/sub/": b""} | entries)
        assert unpack("site.zip", zip_data) == {"index.html": b"<h1>hoi</h1>"}
        tar_data = make_tar(entries, dirs=("dist", "dist/sub"))
        assert unpack("site.tar.gz", tar_data) == {"index.html": b"<h1>hoi</h1>"}

    def test_peeling_stops_on_the_first_file(self, unpack: UnpackFn):
        entries = {"dist/index.html": b"<h1>hoi</h1>", "dist/binnen/a.txt": b"a"}
        expected = {"index.html": b"<h1>hoi</h1>", "binnen/a.txt": b"a"}
        assert unpack("site.zip", make_zip(entries)) == expected
        assert unpack("site.tar.gz", make_tar(entries)) == expected

    def test_empty_sibling_dir_keeps_the_peeling_against(self, unpack: UnpackFn):
        """A directory entry beside the wrapper is a second entry in the root;
        without an index in the root that is a refusal."""
        entries = {"dist/index.html": b"<h1>hoi</h1>"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"leeg/": b""} | entries))
        assert reason_of(error) == "NO_INDEX"
        assert error.value.index_candidates == ("dist/index.html",)

    def test_dir_alongside_loose_file_not_peeled(self, unpack: UnpackFn):
        """The realistic case: someone zips their whole project folder. One
        level is stripped off and then there is no index in the root any more."""
        entries = {"mijnproject/dist/index.html": b"<h1>hoi</h1>", "mijnproject/README.md": b"x"}
        for filename, data in (("site.zip", make_zip(entries)), ("site.tar.gz", make_tar(entries))):
            with pytest.raises(BundleError) as error:
                unpack(filename, data)
            assert reason_of(error) == "NO_INDEX"
            assert error.value.index_candidates == ("dist/index.html",)
            assert "dist/index.html" in str(error.value)
            assert "base path" in str(error.value)

    def test_loose_file_after_the_dir_not_peeled(self, unpack: UnpackFn):
        """The decision is made over the whole archive, not on the first entry."""
        entries = {"dist/index.html": b"<h1>hoi</h1>", "dist/a.txt": b"a", "zzz.txt": b"z"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries))
        assert reason_of(error) == "NO_INDEX"
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", make_tar(entries))
        assert reason_of(error) == "NO_INDEX"

    def test_two_dirs_not_peeled(self, unpack: UnpackFn):
        entries = {"index.html": b"w", "a/index.html": b"1", "b/index.html": b"2"}
        assert unpack("site.zip", make_zip(entries)) == entries
        assert unpack("site.tar.gz", make_tar(entries)) == entries

    def test_only_dir_entries_stays_empty_archive(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"dist/": b"", "dist/leeg/": b""}))
        assert reason_of(error) == "EMPTY_ARCHIVE"
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", make_tar({}, dirs=("dist", "dist/leeg")))
        assert reason_of(error) == "EMPTY_ARCHIVE"

    def test_reserved_wrapping_dir_peeled(self, unpack: UnpackFn):
        """The name belongs to the platform in the root of the site, and that
        is exactly where a stripped wrapper does not land: `_preview/index.html`
        simply becomes `index.html`. Someone whose outermost directory happens
        to carry that name need not get stuck on it."""
        entries = {"_preview/index.html": b"1", "_preview/a.css": b"2"}
        expected = {"index.html": b"1", "a.css": b"2"}
        assert unpack("site.zip", make_zip(entries)) == expected
        assert unpack("site.tar.gz", make_tar(entries)) == expected

    def test_reserved_segment_under_the_wrapper_refused(self, unpack: UnpackFn):
        """Stripping shifts level two to the root: `site/_preview/x.html` would
        then land in the platform namespace, and is refused."""
        entries = {"site/_preview/x.html": b"1", "site/index.html": b"2"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries))
        assert reason_of(error) == "RESERVED_SEGMENT"
        assert "after peeling off 'site'" in str(error.value)
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", make_tar(entries))
        assert reason_of(error) == "RESERVED_SEGMENT"

    def test_depth_counts_the_wrapping_dir_along(self, unpack: UnpackFn):
        """The limits apply to the path as it stands in the archive, even when
        it would fall within the depth after stripping."""
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"dist/a/b/c/d/e.txt": b"x"}))
        assert reason_of(error) == "TOO_DEEP"

    def test_traversal_in_wrapped_archive_refused(self, unpack: UnpackFn):
        entries = {"dist/index.html": b"1", "dist/../../kwaad": b"2"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries))
        assert reason_of(error) == "PATH_TRAVERSAL"
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", make_tar(entries))
        assert reason_of(error) == "PATH_TRAVERSAL"

    def test_backslash_path_peeled(self, unpack: UnpackFn):
        assert unpack("site.zip", make_zip({"dist\\index.html": b"1"})) == {"index.html": b"1"}


class TestIndexRequirement:
    """Without an index.html in the root the site is a 404 nobody notices: the
    deploy is refused, with the index paths found as a suggestion."""

    def test_bundle_without_index_refused(self, unpack: UnpackFn):
        """`dist/rapport.html` on its own: stripped, and then there is no index."""
        for filename, data in (
            ("site.zip", make_zip({"dist/rapport.html": b"x"})),
            ("site.tar.gz", make_tar({"dist/rapport.html": b"x"})),
        ):
            with pytest.raises(BundleError) as error:
                unpack(filename, data)
            assert reason_of(error) == "NO_INDEX"
            assert error.value.index_candidates == ()
            assert "no index.html anywhere" in str(error.value)

    def test_archive_without_only_index(self, unpack: UnpackFn):
        entries = {"leesmij.txt": b"x", "docs/a.html": b"y"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries))
        assert reason_of(error) == "NO_INDEX"
        assert error.value.index_candidates == ()

    def test_suggestion_shortest_first(self, unpack: UnpackFn):
        entries = {"leesmij.txt": b"x", "diep/er/index.html": b"1", "dist/index.html": b"2"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries))
        assert error.value.index_candidates == ("dist/index.html", "diep/er/index.html")
        assert "'dist/index.html'" in str(error.value)
        assert "'dist'" in str(error.value)

    def test_two_indexes_on_equal_depth_both_suggested(self, unpack: UnpackFn):
        """Neither of the two is chosen: that would silently throw the other
        directory away. Alphabetical, so the suggestion does not vary per
        archive."""
        entries = {"leesmij.txt": b"x", "b/index.html": b"2", "a/index.html": b"1"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries))
        assert error.value.index_candidates == ("a/index.html", "b/index.html")

    def test_at_most_five_suggestions(self, unpack: UnpackFn):
        entries: dict[str, bytes] = {"leesmij.txt": b"x"}
        entries |= {f"m{i}/index.html": b"1" for i in range(7)}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries))
        assert error.value.index_candidates == tuple(f"m{i}/index.html" for i in range(5))

    def test_uppercase_index_counts_not_along(self, unpack: UnpackFn):
        """`Index.html` works on a Mac and not on the server; the message says
        so, instead of a site that only works for the person who built it."""
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"Index.html": b"<h1>hoi</h1>"}))
        assert reason_of(error) == "NO_INDEX"
        assert error.value.index_candidates == ()
        assert "Index.html" in str(error.value)
        assert "case sensitive" in str(error.value)

    def test_loose_html_file_becomes_always_the_index(self, unpack: UnpackFn):
        """A single .html file becomes the site's index.html, so the index
        requirement cannot fire there."""
        assert unpack("rapport.html", b"<h1>hoi</h1>") == {"index.html": b"<h1>hoi</h1>"}


class TestBasePath:
    """The user confirms the suggestion: this directory inside the archive
    becomes the root, the rest stays unpublished."""

    PROJECT: ClassVar[dict[str, bytes]] = {
        "mijnproject/README.md": b"leesmij",
        "mijnproject/dist/index.html": b"<h1>hoi</h1>",
        "mijnproject/dist/assets/stijl.css": b"body{}",
    }
    SITE: ClassVar[dict[str, bytes]] = {
        "index.html": b"<h1>hoi</h1>",
        "assets/stijl.css": b"body{}",
    }

    def test_valid_base_path_zip(self, unpack: UnpackFn):
        dirs = {"mijnproject/": b"", "mijnproject/dist/": b"", "mijnproject/dist/assets/": b""}
        assert unpack("site.zip", make_zip(dirs | self.PROJECT), base_path="dist") == self.SITE

    def test_valid_base_path_tar(self, unpack: UnpackFn):
        data = make_tar(self.PROJECT, dirs=("mijnproject", "mijnproject/dist"))
        assert unpack("site.tar.gz", data, base_path="dist") == self.SITE

    def test_base_path_with_more_segments(self, unpack: UnpackFn):
        entries = {"leesmij.txt": b"x", "docs/site/index.html": b"<h1>hoi</h1>"}
        assert unpack("site.zip", make_zip(entries), base_path="docs/site") == {
            "index.html": b"<h1>hoi</h1>"
        }

    def test_base_path_may_the_peeled_prefix_carry_along(self, unpack: UnpackFn):
        """The suggestion is relative to the root after stripping, but someone
        who sees `mijnproject/dist` in their zip and types that over gets the
        same site: that reading exists too."""
        assert unpack("site.zip", make_zip(self.PROJECT), base_path="mijnproject/dist") == self.SITE

    def test_base_path_that_the_peeling_already_removed_has(self, unpack: UnpackFn):
        """Without a neighbouring file the chain strips `dist` off by itself. A
        fixed `base_path` of `dist` in a workflow must not depend on whether a
        LEESMIJ.md happened to sit beside it that time."""
        entries = {f"dist/{path}": content for path, content in self.SITE.items()}
        assert unpack("site.zip", make_zip(entries), base_path="dist") == self.SITE
        assert unpack("site.tar.gz", make_tar(entries), base_path="dist") == self.SITE

    def test_deepest_read_of_the_base_path_wins(self, unpack: UnpackFn):
        """`dist` occurs twice: as the stripped wrapper and inside it. The
        reading faithful to the documentation (relative to the root after
        stripping) is the deeper one and keeps precedence."""
        entries = {"dist/dist/index.html": b"binnen", "dist/a.txt": b"naast"}
        assert unpack("site.zip", make_zip(entries), base_path="dist") == {"index.html": b"binnen"}

    def test_base_path_does_not_exist(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(self.PROJECT), base_path="build")
        assert reason_of(error) == "BASE_PATH_UNKNOWN"
        assert "build" in str(error.value)
        # The stripped prefix appears nowhere in the user's own input; without
        # that sentence the message does not match what they see.
        assert "'mijnproject' has been peeled off already" in str(error.value)

    def test_base_path_exists_not_without_peeling(self, unpack: UnpackFn):
        entries = {"leesmij.txt": b"x", "dist/index.html": b"1"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries), base_path="build")
        assert reason_of(error) == "BASE_PATH_UNKNOWN"
        assert "peeled off" not in str(error.value)
        assert error.value.index_candidates == ("dist/index.html",)

    def test_base_path_with_wrong_uppercase(self, unpack: UnpackFn):
        """The same pitfall as `Index.html`, and so the same explanation: the
        bundle does contain the directory, only spelled differently."""
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(self.PROJECT), base_path="Dist")
        assert reason_of(error) == "BASE_PATH_UNKNOWN"
        assert "'dist'" in str(error.value)
        assert "capitals" in str(error.value)

    def test_base_path_to_a_file(self, unpack: UnpackFn):
        """Sending the value from `indexCandidates` back literally is the most
        obvious slip; that deserves a message about the path, not about the
        bundle."""
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(self.PROJECT), base_path="dist/index.html")
        assert reason_of(error) == "BASE_PATH_UNKNOWN"
        assert "points at a file" in str(error.value)
        assert error.value.index_candidates == ("dist/index.html",)

    def test_base_path_to_dir_with_only_a_subdir(self, unpack: UnpackFn):
        entries = dict(self.PROJECT) | {"mijnproject/leeg/diep/": b""}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries), base_path="leeg")
        assert reason_of(error) == "BASE_PATH_WITHOUT_INDEX"
        assert "holds no files" in str(error.value)
        assert error.value.index_candidates == ("dist/index.html",)

    def test_base_path_without_index(self, unpack: UnpackFn):
        entries = {"leesmij.txt": b"x", "dist/stijl.css": b"body{}", "docs/index.html": b"1"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries), base_path="dist")
        assert reason_of(error) == "BASE_PATH_WITHOUT_INDEX"
        assert error.value.index_candidates == ("docs/index.html",)

    def test_base_path_without_index_and_without_suggestion(self, unpack: UnpackFn):
        entries = {"leesmij.txt": b"x", "dist/stijl.css": b"body{}"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries), base_path="dist")
        assert reason_of(error) == "BASE_PATH_WITHOUT_INDEX"
        assert error.value.index_candidates == ()

    def test_base_path_to_empty_dir(self, unpack: UnpackFn):
        """"Empty" is about that one directory, not about the bundle: that is full."""
        entries = {"mijnproject/leeg/": b"", "mijnproject/README.md": b"x"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries), base_path="leeg")
        assert reason_of(error) == "BASE_PATH_WITHOUT_INDEX"
        assert "Base path 'leeg' holds no files" in str(error.value)

    def test_base_path_without_index_points_to_the_root(self, unpack: UnpackFn):
        """The nearest index.html is already in the root: then there is no
        directory to send back as basispad and the field has to go."""
        entries = {"index.html": b"h", "dist/stijl.css": b"body{}"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries), base_path="dist")
        assert reason_of(error) == "BASE_PATH_WITHOUT_INDEX"
        assert error.value.index_candidates == ("index.html",)
        assert "leave the base path field out" in str(error.value)

    @pytest.mark.parametrize(
        "base_path", ["../geheim", "/etc", "dist/../../kwaad", "", ".", "a\x00b", "_preview"]
    )
    def test_invalid_base_path_refused(self, unpack: UnpackFn, base_path: str):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(self.PROJECT), base_path=base_path)
        assert reason_of(error) == "BASE_PATH_INVALID"

    def test_base_path_on_loose_html_file_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("rapport.html", b"<h1>hoi</h1>", base_path="dist")
        assert reason_of(error) == "BASE_PATH_UNKNOWN"

    def test_unsafe_path_outside_the_base_path_still_refused(self, unpack: UnpackFn):
        """The structural path check applies to every entry, including one that
        is not published."""
        entries = dict(self.PROJECT) | {"mijnproject/../kwaad": b"x"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries), base_path="dist")
        assert reason_of(error) == "PATH_TRAVERSAL"

    def test_deep_sibling_dir_does_not_block_the_base_path(self, unpack: UnpackFn):
        """The depth and name limits apply over the published tree; a
        node_modules that stays outside the publication does not count."""
        entries = dict(self.PROJECT) | {"mijnproject/node_modules/a/b/c/d/e/f.js": b"x"}
        assert unpack("site.zip", make_zip(entries), base_path="dist") == self.SITE

    def test_reserved_segment_under_base_path_refused(self, unpack: UnpackFn):
        entries = dict(self.PROJECT) | {"mijnproject/dist/_version/x.html": b"x"}
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries), base_path="dist")
        assert reason_of(error) == "RESERVED_SEGMENT"
        # The user wrote 'dist', not 'mijnproject/dist': the message names
        # their own basispad and not the stripped prefix.
        assert "in the root of base path 'dist'" in str(error.value)


class TestLinks:
    def test_tar_symlink_refused(self, unpack: UnpackFn):
        data = make_tar({"index.html": b"x"}, links=(("link", "/etc/passwd", tarfile.SYMTYPE),))
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "SYMLINK_REFUSED"

    def test_tar_hardlink_refused(self, unpack: UnpackFn):
        data = make_tar({"index.html": b"x"}, links=(("link", "index.html", tarfile.LNKTYPE),))
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "HARDLINK_REFUSED"

    def test_tar_special_file_refused(self, unpack: UnpackFn):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as archive:
            info = tarfile.TarInfo("fifo")
            info.type = tarfile.FIFOTYPE
            archive.addfile(info)
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", buf.getvalue())
        assert reason_of(error) == "SPECIAL_FILE"

    def test_zip_symlink_refused(self, unpack: UnpackFn):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            info = zipfile.ZipInfo("link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "target")
        with pytest.raises(BundleError) as error:
            unpack("site.zip", buf.getvalue())
        assert reason_of(error) == "SYMLINK_REFUSED"


class TestLimits:
    def test_html_too_large(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("groot.html", b"x" * (LIMITS.max_file + 1))
        assert reason_of(error) == "FILE_TOO_LARGE"

    def test_bomb_refused_for_materialisation(self, unpack: UnpackFn):
        # Small compressed (zeros), far above the limit unpacked: refused
        # without 1 MB landing on disk.
        data = make_zip({"boem.bin": b"\0" * 1_000_000})
        assert len(data) < 10_000
        tight = Limits(max_file=10_000, max_total=200_000, max_files=10, max_depth=5)
        with pytest.raises(BundleError) as error:
            unpack("site.zip", data, tight)
        assert reason_of(error) == "FILE_TOO_LARGE"

    def test_counter_does_not_trust_the_header_claim(self, tmp_path: Path):
        """An entry that claims a small size but delivers more runs into the
        incremental counter (not into the header) and is aborted."""
        source = io.BytesIO(b"\0" * 50_000)
        tree = _Tree(LIMITS)
        tight = Limits(max_file=10_000, max_total=200_000, max_files=10, max_depth=5)
        with pytest.raises(BundleError) as error:
            _write_entry(source, "leugen.bin", 100, tree, DirDestination(tmp_path), tight)
        assert reason_of(error) == "FILE_TOO_LARGE"
        # Counted before each write: nothing past the limit reaches the disk.
        assert (tmp_path / "leugen.bin").stat().st_size <= 10_000
        assert tree.count == 0

    def test_cumulative_limit(self, unpack: UnpackFn):
        data = make_zip({f"b{i}.bin": b"\0" * 90_000 for i in range(3)})
        with pytest.raises(BundleError) as error:
            unpack("site.zip", data)
        assert reason_of(error) == "TOTAL_TOO_LARGE"

    def test_cumulative_limit_tar(self, unpack: UnpackFn):
        data = make_tar({f"b{i}.bin": b"\0" * 90_000 for i in range(3)})
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "TOTAL_TOO_LARGE"

    def test_entries_outside_the_root_count_not_along_for_the_site(self, unpack: UnpackFn):
        """The promise from the documentation: a node_modules beside your dist
        does not hold the deploy up, not even when that directory on its own has
        more entries than the site is allowed."""
        entries = {"mijnproject/dist/index.html": b"h", "mijnproject/README.md": b"x"}
        entries |= {
            f"mijnproject/node_modules/p{i}/a.js": b"x" for i in range(LIMITS.max_files + 5)
        }
        expected = {"index.html": b"h"}
        assert unpack("site.zip", make_zip(entries), base_path="dist") == expected
        assert unpack("site.tar.gz", make_tar(entries), base_path="dist") == expected

    def test_archive_bound_counts_also_what_outside_the_root_falls(self, unpack: UnpackFn):
        """Walking is not free: every entry costs memory, including one that
        publishes nothing. The message says so, otherwise the user hunts the
        fault in a site of one file."""
        entries = {"mijnproject/dist/index.html": b"h"}
        entries |= {
            f"mijnproject/node_modules/p{i}/a.js": b"x"
            for i in range(LIMITS.max_archive_entries)
        }
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries), base_path="dist")
        assert reason_of(error) == "TOO_MANY_FILES"
        assert "outside the root" in str(error.value)

    def test_too_many_files_carries_the_base_path_suggestion(self, unpack: UnpackFn):
        """Someone who zips their whole project folder hits this first, ahead
        of the index requirement. Without the candidates there is no way back;
        with them the answer points at the directory that was meant as the
        site."""
        entries = {"mijnproject/dist/index.html": b"h", "mijnproject/README.md": b"x"}
        entries |= {
            f"mijnproject/node_modules/p{i}/a.js": b"x" for i in range(LIMITS.max_files)
        }
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries))
        assert reason_of(error) == "TOO_MANY_FILES"
        assert error.value.index_candidates == ("dist/index.html",)

    def test_too_many_files(self, unpack: UnpackFn):
        data = make_zip({f"b{i}.txt": b"x" for i in range(LIMITS.max_files + 1)})
        with pytest.raises(BundleError) as error:
            unpack("site.zip", data)
        assert reason_of(error) == "TOO_MANY_FILES"

    def test_dir_entries_count_along_zip(self, unpack: UnpackFn):
        entries: dict[str, bytes] = {f"m{i}/": b"" for i in range(LIMITS.max_files)}
        entries["index.html"] = b"<h1>hoi</h1>"
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip(entries))
        assert reason_of(error) == "TOO_MANY_FILES"

    def test_dir_entries_count_along_tar(self, unpack: UnpackFn):
        data = make_tar(
            {"index.html": b"<h1>hoi</h1>"},
            dirs=tuple(f"m{i}" for i in range(LIMITS.max_files)),
        )
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "TOO_MANY_FILES"

    def test_dir_entries_inside_the_limit_allowed(self, unpack: UnpackFn):
        # `zip -r` and `tar -cz` put directory entries in the archive; those
        # write nothing and must not push an ordinary bundle over the limit.
        expected = {"index.html": b"x", "assets/stijl.css": b"body{}"}
        zip_data = make_zip({"index.html": b"x", "assets/": b"", "assets/stijl.css": b"body{}"})
        assert unpack("site.zip", zip_data) == expected
        tar_data = make_tar({"index.html": b"x", "assets/stijl.css": b"body{}"}, dirs=("assets",))
        assert unpack("site.tar.gz", tar_data) == expected

    def test_too_deep(self, unpack: UnpackFn):
        data = make_zip({"a/b/c/d/e/f.txt": b"x"})
        with pytest.raises(BundleError) as error:
            unpack("site.zip", data)
        assert reason_of(error) == "TOO_DEEP"

    def test_exactly_on_the_limits_allowed(self, unpack: UnpackFn):
        data = make_zip({"index.html": b"h", "a/b/c/d/e.txt": b"\0" * 100_000})
        result = unpack("site.zip", data)
        assert len(result["a/b/c/d/e.txt"]) == 100_000


def make_eocd(total: int, directory_size: int = 0, comment: bytes = b"") -> bytes:
    """The end-of-central-directory record a zip ends with, without a central
    directory in front of it: the tail the unpacker reads first."""
    fields = struct.pack(
        "<4sHHHHLLH",
        b"PK\x05\x06",
        0,
        0,
        min(total, 0xFFFF),
        min(total, 0xFFFF),
        directory_size,
        0,
        len(comment),
    )
    return fields + comment


def make_zip64_tail(signature: bytes = b"PK\x06\x06", record_offset: int = 0) -> bytes:
    """A zip64 tail without an archive in front of it: the record with the real
    totals, the locator pointing at it, and a classic record whose own fields
    are full."""
    record = struct.pack("<4sQHHLLQQQQ", signature, 44, 45, 45, 0, 0, 7, 7, 0, 0)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, record_offset, 1)
    return record + locator + make_eocd(0xFFFF)


def as_zip64(data: bytes) -> bytes:
    """The same archive with a zip64 tail: above 65535 entries the classic
    record holds no totals any more and refers to the zip64 record, which is
    the shape a bundle that really has too many entries arrives in."""
    eocd = data.rindex(b"PK\x05\x06")
    directory_size, directory_offset = struct.unpack("<LL", data[eocd + 12 : eocd + 20])
    record = struct.pack(
        "<4sQHHLLQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, 0, 0, directory_size, directory_offset
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, eocd, 1)
    tail = struct.pack("<4sHHHHLLH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0)
    return data[:eocd] + record + locator + tail


def make_crowded_zip(entries: int, comment: bytes = b"") -> bytes:
    """A zip with more entries than `max_archive_entries` allows, each of them
    empty: what the count costs is the walk over the central directory, not
    the contents."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.comment = comment
        for i in range(entries):
            archive.writestr(f"d{i}/x", b"")
    return buf.getvalue()


class TestZipEntryCount:
    """zipfile parses the whole central directory into memory the moment the
    archive opens: hundreds of bytes per entry, before a single entry has been
    counted. The entries are therefore counted in that directory itself first,
    up to the limit and no further; otherwise one upload of a few hundred
    megabytes costs gigabytes of memory in the worker and so takes the backend
    down for every group and every site."""

    def test_more_entries_than_the_limit_refused(self, unpack: UnpackFn):
        data = make_crowded_zip(LIMITS.max_archive_entries + 1)
        with pytest.raises(BundleError) as error:
            unpack("site.zip", data)
        assert reason_of(error) == "TOO_MANY_FILES"
        assert "entries" in str(error.value)

    def test_the_count_stops_at_the_limit(self, tmp_path: Path):
        """The point of the whole check: an archive that ships far more entries
        than are allowed costs no memory per entry, because the count stops at
        the limit and zipfile never gets to open it."""
        source = tmp_path / "veel.zip"
        source.write_bytes(make_crowded_zip(50 * LIMITS.max_archive_entries))
        target = tmp_path / "target"
        target.mkdir()

        tracemalloc.start()
        with pytest.raises(BundleError) as error:
            unpack_archive("veel.zip", source, DirDestination(target), LIMITS)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()

        assert reason_of(error) == "TOO_MANY_FILES"
        # zipfile holds roughly half a KB per entry; the walk holds one record.
        assert peak < LIMITS.max_archive_entries * 500

    def test_a_directory_that_holds_more_than_it_claims(self, unpack: UnpackFn):
        # zipfile reads to the end of the central directory and not to the
        # number of entries the tail claims, so that number is not trusted.
        data = bytearray(make_crowded_zip(LIMITS.max_archive_entries + 1))
        eocd = data.rindex(b"PK\x05\x06")
        data[eocd + 8 : eocd + 12] = struct.pack("<HH", 1, 1)
        with pytest.raises(BundleError) as error:
            unpack("site.zip", bytes(data))
        assert reason_of(error) == "TOO_MANY_FILES"

    def test_zip64_entries_are_counted_too(self, unpack: UnpackFn):
        data = as_zip64(make_crowded_zip(LIMITS.max_archive_entries + 1))
        with pytest.raises(BundleError) as error:
            unpack("site.zip", data)
        assert reason_of(error) == "TOO_MANY_FILES"

    def test_a_directory_that_stops_making_sense_is_left_to_zipfile(self, unpack: UnpackFn):
        """Counting stops at the first record that does not parse; zipfile
        stumbles over that same record and refuses the archive."""
        data = bytearray(make_crowded_zip(LIMITS.max_archive_entries + 1))
        directory = data.index(b"PK\x01\x02")
        data[directory : directory + 4] = b"PK\x01\x03"
        with pytest.raises(BundleError) as error:
            unpack("site.zip", bytes(data))
        assert reason_of(error) == "INVALID_ARCHIVE"

    @pytest.mark.parametrize(
        ("what", "tail"),
        [
            ("no record at all", b"dit is geen zip"),
            ("record cut short", b"PK\x05\x06" + b"\0" * 5),
            ("comment length does not add up", make_eocd(1, comment=b"x")[:-1]),
            ("directory in front of the start of the file", make_eocd(1, directory_size=1 << 20)),
            ("no directory at all", make_eocd(1)),
            ("zip64 locator missing", make_eocd(0xFFFF)),
            ("zip64 record not where the locator says", make_zip64_tail(record_offset=1 << 40)),
            ("zip64 record is not one", make_zip64_tail(signature=b"PK\x06\x08")),
        ],
    )
    def test_a_tail_that_does_not_parse_is_left_to_zipfile(
        self, unpack: UnpackFn, what: str, tail: bytes
    ):
        """A tail nothing can be read out of is not a count of its own: the
        archive goes to zipfile, which refuses it on its own terms (as an
        unreadable archive, or as one holding nothing)."""
        with pytest.raises(BundleError) as error:
            unpack("site.zip", tail)
        assert reason_of(error) in ("INVALID_ARCHIVE", "EMPTY_ARCHIVE"), what

    def test_a_signature_in_the_comment_does_not_hide_the_real_record(self, unpack: UnpackFn):
        # The record may be followed by a comment of up to 65535 bytes, and
        # those bytes can hold the signature themselves. Only the record whose
        # comment length reaches the end of the file counts; were the fake one
        # taken, nothing would be counted and this bundle would go through.
        data = make_crowded_zip(
            LIMITS.max_archive_entries + 1, comment=b"PK\x05\x06" + b"\0" * 30
        )
        with pytest.raises(BundleError) as error:
            unpack("site.zip", data)
        assert reason_of(error) == "TOO_MANY_FILES"

    def test_an_ordinary_comment_leaves_the_bundle_alone(self, unpack: UnpackFn):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.comment = b"gemaakt met plak" * 10
            archive.writestr("index.html", b"<h1>hoi</h1>")
        assert unpack("site.zip", buf.getvalue()) == {"index.html": b"<h1>hoi</h1>"}


class TestTarPrepass:
    """A tar has no index, so determining the root takes a pass of its own over
    the headers. Jumping to the next header means decompressing everything in
    between in a gzip stream, so that pass counts against the same limits as
    unpacking: otherwise an archive that ought to be refused on its first header
    would have to be read all the way through first."""

    def test_too_large_file_refused_on_the_header(self, unpack: UnpackFn):
        data = make_tar_header_claim((), LIMITS.max_file + 1)
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "FILE_TOO_LARGE"

    def test_cumulative_too_large_refused_on_the_header(self, unpack: UnpackFn):
        # Two members of 90,000 fit individually and together; the third tips
        # over max_total, and that already shows from its header.
        data = make_tar_header_claim((90_000, 90_000), 90_000)
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "TOTAL_TOO_LARGE"

    def test_inside_the_limits_reads_the_prepass_does_by(self, unpack: UnpackFn):
        """The counter-check: a claim within the limits is not refused, so the
        pass skips over the (missing) data and runs into the end of the archive.
        Without this test the two tests above do not show that the refusal comes
        from the header."""
        data = make_tar_header_claim((), 12)
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", data)
        assert reason_of(error) == "INVALID_ARCHIVE"


LARGE = 200 * 1024 * 1024
GENEROUS = Limits(max_file=LARGE, max_total=LARGE + 4096, max_files=10, max_depth=5)


def _write_large_zip(path: Path, chunk: bytes, count: int) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", b"<h1>hoi</h1>")
        with archive.open("nullen.bin", "w") as out:
            for _ in range(count):
                out.write(chunk)


def _write_large_targz(path: Path, chunk: bytes, count: int, name: str = "nullen.bin") -> None:
    class Zeros(io.RawIOBase):
        def __init__(self) -> None:
            self.rest = len(chunk) * count

        def readinto(self, buf) -> int:  # type: ignore[override]
            n = min(len(buf), self.rest)
            buf[:n] = b"\0" * n
            self.rest -= n
            return n

        def readable(self) -> bool:
            return True

    index = b"<h1>hoi</h1>"
    with tarfile.open(path, "w:gz") as archive:
        indexinfo = tarfile.TarInfo(f"{name.rpartition('/')[0]}/index.html".lstrip("/"))
        indexinfo.size = len(index)
        archive.addfile(indexinfo, io.BytesIO(index))
        info = tarfile.TarInfo(name)
        info.size = len(chunk) * count
        archive.addfile(info, io.BufferedReader(Zeros()))


def _write_large_targz_wrapped(path: Path, chunk: bytes, count: int) -> None:
    """Everything under a wrapping directory: the tar is then walked twice
    (first the headers to determine the root, then the contents)."""
    _write_large_targz(path, chunk, count, name="dist/nullen.bin")


@pytest.mark.parametrize(
    ("filename", "write"),
    [
        ("groot.zip", _write_large_zip),
        ("groot.tar.gz", _write_large_targz),
        ("groot.tar.gz", _write_large_targz_wrapped),
    ],
    ids=["zip", "tar", "tar-omhuld"],
)
def test_peak_memory_does_not_grow_with_the_archive(tmp_path: Path, filename: str, write) -> None:
    """200 MB unpacked: peak memory during unpack stays orders of magnitude
    below the archive (streaming in 64 KiB chunks, no dict[str, bytes])."""
    chunk = b"\0" * (1024 * 1024)
    source = tmp_path / filename
    write(source, chunk, LARGE // len(chunk))
    del chunk
    target = tmp_path / "target"
    target.mkdir()

    tracemalloc.start()
    try:
        count = unpack_archive(filename, source, DirDestination(target), GENEROUS)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert count == 2
    assert (target / "nullen.bin").stat().st_size == LARGE
    assert (target / "index.html").is_file()
    assert peak < 8 * 1024 * 1024, f"piek {peak} bytes voor een archief van {LARGE} bytes"


def test_peak_memory_grows_not_along_with_the_count_dir_entries(tmp_path: Path) -> None:
    """100,000 empty directories are small on disk and expensive in memory as
    soon as every entry is walked (tarfile holds on to each member); the entry
    counter cuts off as soon as the limit is full."""
    source = tmp_path / "veel.tar.gz"
    source.write_bytes(make_tar({"index.html": b"x"}, dirs=tuple(f"m{i}" for i in range(100_000))))
    assert source.stat().st_size < 1024 * 1024
    target = tmp_path / "target"
    target.mkdir()

    tracemalloc.start()
    try:
        with pytest.raises(BundleError) as error:
            unpack_archive("site.tar.gz", source, DirDestination(target), LIMITS)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert reason_of(error) == "TOO_MANY_FILES"
    assert peak < 1024 * 1024, f"piek {peak} bytes voor 100.000 map-entries"


def test_limits_from_settings(tmp_path):
    settings = Settings(
        db_url="postgresql+asyncpg://x/y",
        content_root=tmp_path,
        oidc_issuer="https://idp.example",
        oidc_client_id="plak",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr",
        session_secret="s" * 32,
        audit_pepper="p" * 32,
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        content_base_url="https://plak.example",
        environment="dev",
        ingest_max_file=1,
        ingest_max_total=2,
        ingest_max_files=3,
        ingest_max_depth=4,
    )
    limits = Limits.from_settings(settings)
    assert limits == Limits(max_file=1, max_total=2, max_files=3, max_depth=4)


class TestFinderJunk:
    """The route a colleague without a terminal takes: folder in Finder, right
    click, "Compress". Such a zip holds `__MACOSX/` beside the folder, with an
    AppleDouble file per real file, and often a `.DS_Store`."""

    def _finder_zip(self) -> bytes:
        return make_zip(
            {
                "mijn-site/index.html": b"<h1>hoi</h1>",
                "mijn-site/stijl.css": b"body{}",
                "mijn-site/.DS_Store": b"rommel",
                "__MACOSX/._mijn-site": b"rommel",
                "__MACOSX/mijn-site/._index.html": b"rommel",
                "__MACOSX/mijn-site/._stijl.css": b"rommel",
            }
        )

    def test_the_wrapping_dir_becomes_still_peeled(self, unpack: UnpackFn):
        # Without the filter `mijn-site/` and `__MACOSX/` sit side by side in
        # the root, the wrapper stays and there is no index.html.
        assert unpack("site.zip", self._finder_zip()) == {
            "index.html": b"<h1>hoi</h1>",
            "stijl.css": b"body{}",
        }

    def test_appledouble_inside_the_site_does_not_become_published(self, unpack: UnpackFn):
        tree = unpack(
            "site.zip",
            make_zip({"index.html": b"<h1>hoi</h1>", "._index.html": b"rommel", ".DS_Store": b"rommel"}),
        )
        assert tree == {"index.html": b"<h1>hoi</h1>"}

    def test_a_archive_with_only_junk_has_nothing_too_publish(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"__MACOSX/._x": b"rommel", ".DS_Store": b"rommel"}))
        assert reason_of(error) == "EMPTY_ARCHIVE"
        # Not GEEN_INDEX: adding an index.html does not solve this, because the
        # message has to say that it held nothing but metadata.
        assert "OS metadata" in str(error.value)

    def test_junk_escapes_not_on_the_path_check(self, unpack: UnpackFn):
        # Skipping happens only after the structural check, otherwise a `..`
        # behind `__MACOSX/` would pass unseen.
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"index.html": b"<h1>hoi</h1>", "__MACOSX/../../weg": b"1"}))
        assert reason_of(error) == "PATH_TRAVERSAL"

    def test_a_path_that_until_nothing_reduces_is_no_junk(self):
        # `./` and `/` leave zero segments after the structural check; that is
        # not metadata, so ordinary handling has to take it.
        assert is_archive_junk(()) is False

    def test_junk_in_a_tar_goes_net_so(self, unpack: UnpackFn):
        tree = unpack(
            "site.tar.gz",
            make_tar({"mijn-site/index.html": b"<h1>hoi</h1>", "__MACOSX/._mijn-site": b"rommel"}),
        )
        assert tree == {"index.html": b"<h1>hoi</h1>"}


class TestBrokenArchive:
    """What happens when the archive is not what the index promises.

    These paths never come past with a healthy archive, but they do decide
    whether a broken or hostile bundle gives a clean refusal instead of a 500.
    """

    def test_cumulative_limit_falls_also_on_a_lying_header(self, tmp_path: Path):
        # Counterpart of the per-file test above: the header claims little, the
        # stream delivers more, and the bundle thereby overruns the total.
        tree = _Tree(LIMITS)
        tight = Limits(max_file=200_000, max_total=20_000, max_files=10, max_depth=5)
        with pytest.raises(BundleError) as error:
            _write_entry(io.BytesIO(b"\0" * 50_000), "leugen.bin", 100, tree, DirDestination(tmp_path), tight)
        assert reason_of(error) == "TOTAL_TOO_LARGE"
        assert tree.count == 0

    def test_zip_entry_that_no_plain_file_is(self, unpack: UnpackFn):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("index.html", b"<h1>hoi</h1>")
            info = zipfile.ZipInfo("pijp")
            info.external_attr = (stat.S_IFIFO | 0o644) << 16
            archive.writestr(info, b"")
        with pytest.raises(BundleError) as error:
            unpack("site.zip", buf.getvalue())
        assert reason_of(error) == "SPECIAL_FILE"

    def test_zip_with_a_corrupt_entry(self, unpack: UnpackFn):
        # Only the compressed bytes are broken: the archive opens, the index is
        # right, and only on reading does the CRC fall over.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("index.html", b"<h1>hoi</h1>")
            archive.writestr("stuk.bin", bytes(range(256)) * 40)
        raw = bytearray(buf.getvalue())
        header = raw.find(b"PK\x03\x04", raw.find(b"PK\x03\x04") + 4)
        name_length = int.from_bytes(raw[header + 26 : header + 28], "little")
        extra = int.from_bytes(raw[header + 28 : header + 30], "little")
        raw[header + 30 + name_length + extra + 20] ^= 0xFF

        with pytest.raises(BundleError) as error:
            unpack("site.zip", bytes(raw))
        assert reason_of(error) == "INVALID_ARCHIVE"
        assert "Zip entry unreadable" in str(error.value)

    def test_an_unreadable_archive_names_no_path_on_the_server(self, tmp_path: Path):
        """The `detail` of a refusal carries no internal details (api/errors.py).
        A name the filesystem will not take makes the store's own spool path
        part of the OSError, and that must not travel along to the deployer."""
        long_name = "a" * 300 + ".html"
        for filename, data in (
            ("site.tar.gz", make_tar({"index.html": b"h", long_name: b"x"})),
            ("site.zip", make_zip({"index.html": b"h", long_name: b"x"})),
        ):
            source = tmp_path / f"upload-{filename}"
            source.write_bytes(data)
            target = tmp_path / f"target-{filename}"
            target.mkdir()
            with pytest.raises(BundleError) as error:
                unpack_archive(filename, source, DirDestination(target), LIMITS)
            assert reason_of(error) == "INVALID_ARCHIVE"
            assert str(tmp_path) not in str(error.value)
            assert "OSError" in str(error.value)

    def test_html_that_not_too_open_is(self, tmp_path: Path):
        # The store normally hands over a file; if that turns out not to be
        # there, it is a refused bundle and not an uncaught OSError.
        source = tmp_path / "rapport.html"
        source.mkdir()
        with pytest.raises(BundleError) as error:
            unpack_archive("rapport.html", source, DirDestination(tmp_path / "target"), LIMITS)
        assert reason_of(error) == "INVALID_ARCHIVE"
        assert "Html file unreadable" in str(error.value)


class TestSecretsAreRefused:
    """A zipped project folder often carries `.git` and `.env` along.

    Those do not belong on a website: `.env` can hold the deploy token itself,
    with which someone else can publish to the same site, and `.git` gives
    the full history away. The serving side does not stop dot files, so it has
    to happen here. Refuse rather than skip, because whoever sends this along
    packed something other than what they thought.
    """

    def test_env_in_the_bundle_becomes_refused(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"index.html": b"<h1>hoi</h1>", ".env": b"PLAK_TOKEN=geheim"}))
        assert reason_of(error) == "SECRET_FILE"
        # The message has to say what you do instead.
        assert "dist" in str(error.value)

    @pytest.mark.parametrize("name", [".env", ".env.local", ".env.production", ".env.example"])
    def test_every_env_variant_counts(self, unpack: UnpackFn, name: str):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"index.html": b"x", name: b"y"}))
        assert reason_of(error) == "SECRET_FILE"

    def test_a_name_that_only_with_env_begins_may_does(self, unpack: UnpackFn):
        # `.environment.html` is not an environment file; the boundary is the dot.
        tree = unpack("site.zip", make_zip({"index.html": b"x", ".environment.html": b"y"}))
        assert ".environment.html" in tree

    def test_git_dir_becomes_refused_on_every_depth(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.zip", make_zip({"index.html": b"x", "docs/.git/config": b"y"}))
        assert reason_of(error) == "SECRET_FILE"

    def test_gitignore_is_no_git_dir(self, unpack: UnpackFn):
        tree = unpack("site.zip", make_zip({"index.html": b"x", ".gitignore": b"node_modules"}))
        assert ".gitignore" in tree

    def test_well_known_stays_plain_publishable(self, unpack: UnpackFn):
        # No blanket ban on dot files: a site is precisely meant to be able to have this.
        tree = unpack("site.zip", make_zip({"index.html": b"x", ".well-known/security.txt": b"y"}))
        assert ".well-known/security.txt" in tree

    def test_outside_the_chosen_root_it_does_not_block(self, unpack: UnpackFn):
        # Whoever zips their project folder and points at `dist` does not
        # publish the secrets; then they need not hold the publication up.
        tree = unpack(
            "site.zip",
            make_zip(
                {
                    "project/dist/index.html": b"<h1>hoi</h1>",
                    "project/.env": b"PLAK_TOKEN=geheim",
                    "project/.git/config": b"x",
                }
            ),
            base_path="dist",
        )
        assert tree == {"index.html": b"<h1>hoi</h1>"}

    def test_also_in_a_tar(self, unpack: UnpackFn):
        with pytest.raises(BundleError) as error:
            unpack("site.tar.gz", make_tar({"index.html": b"x", ".env": b"y"}))
        assert reason_of(error) == "SECRET_FILE"
