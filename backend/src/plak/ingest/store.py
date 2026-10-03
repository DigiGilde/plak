"""ContentStore: immutable version storage on the content volume.

Writing goes to {root}/_tmp/{uuid} in full first, and from there to
{site_id}/{version_id} with an atomic os.rename. The tempdir sits on
the same filesystem as the final directory, so the rename is atomic and has
no EXDEV/copy fallback. The spooled upload lives in {root}/_tmp as well:
outside every servable path, on the volume instead of in memory.
"""

from __future__ import annotations

import os
import shutil
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

TMP_DIRNAME = "_tmp"
RECLAIMED_DIRNAME = "_reclaimed"
SPOOL_SUFFIX = ".upload"


class StoreError(Exception):
    """Invalid call into the ContentStore (e.g. a path outside the root)."""


def _check_rel_path(rel_path: str) -> None:
    # Defence in depth: the unpacker has validated paths already, but the
    # store independently refuses anything that could reach outside the
    # version root.
    if not rel_path or "\x00" in rel_path or "\\" in rel_path:
        raise StoreError(f"ongeldig relatief pad: {rel_path!r}")
    parts = rel_path.split("/")
    if any(part in ("", ".", "..") for part in parts) or rel_path.startswith("/"):
        raise StoreError(f"ongeldig relatief pad: {rel_path!r}")


class VersionWriter:
    """Write handle for a version in the making, in the store's work directory."""

    def __init__(self, workdir: Path, storage_ref: str) -> None:
        self._workdir = workdir
        self.storage_ref = storage_ref

    def open_file(self, rel_path: str) -> IO[bytes]:
        """Opens `rel_path` exclusively for writing; a collision with an earlier
        written file or directory is a StoreError."""
        _check_rel_path(rel_path)
        target = self._workdir / rel_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            return target.open("xb")
        except (FileExistsError, NotADirectoryError, IsADirectoryError) as error:
            raise StoreError(f"pad botst met een eerder geschreven pad: {rel_path!r}") from error

    def total_bytes(self) -> int:
        """What has been written so far, measured on disk rather than counted
        along the way: this is the number the volume actually fills up with."""
        return _tree_bytes(self._workdir)


def _tree_bytes(root: Path) -> int:
    """Apparent size of every file under `root`; a missing tree is 0.

    st_size and not st_blocks: the quota is about what was published, not
    about the block size the volume happens to round it up to.
    """
    total = 0
    for directory, _, names in os.walk(root, onerror=None):
        for name in names:
            try:
                total += os.lstat(os.path.join(directory, name)).st_size
            except OSError:
                # Gone between the walk and the stat: the cleanup job removing
                # an expired preview alongside us. Not counting it is the safe
                # side of the quota.
                continue
    return total


class ContentStore:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._tmp = self._root / TMP_DIRNAME
        self._tmp.mkdir(parents=True, exist_ok=True)
        self._reclaimed = self._root / RECLAIMED_DIRNAME

    @property
    def root(self) -> Path:
        return self._root

    def _version_root(self, storage_ref: str) -> Path:
        """Resolved path of a version root, with a containment guarantee."""
        if "\x00" in storage_ref:
            raise StoreError("storage_ref bevat een null-byte")
        path = (self._root / storage_ref).resolve()
        if path == self._root or not path.is_relative_to(self._root):
            raise StoreError("storage_ref wijst buiten de contentroot")
        if path == self._tmp or path.is_relative_to(self._tmp):
            raise StoreError("storage_ref wijst naar de tempdirectory")
        return path

    def free_bytes(self) -> int:
        """Free space on the content volume."""
        return shutil.disk_usage(self._root).free

    def site_bytes(self, site_id: uuid.UUID) -> int:
        """What every version of one site together occupies.

        Measured on disk rather than kept in a column: a version's size is
        never revised, but the set of versions is (the cleanup job removes
        expired previews), and the volume is the only place where the two are
        always in step.
        """
        return _tree_bytes(self._version_root(str(site_id)))

    def new_spool_file(self) -> Path:
        """Unique path in the tempdir for an upload still to be received."""
        return self._tmp / f"{uuid.uuid4()}{SPOOL_SUFFIX}"

    @contextmanager
    def write_version(self, site_id: uuid.UUID, version_id: uuid.UUID) -> Iterator[VersionWriter]:
        """Work directory for a new version; on a normal exit atomically renamed
        to its final place, on an exception cleaned up entirely."""
        storage_ref = f"{site_id}/{version_id}"
        final_target = self._version_root(storage_ref)

        workdir = self._tmp / str(uuid.uuid4())
        workdir.mkdir(parents=True)
        try:
            yield VersionWriter(workdir, storage_ref)
            final_target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(workdir, final_target)
        except BaseException:
            shutil.rmtree(workdir, ignore_errors=True)
            raise

    def store_version(self, site_id: uuid.UUID, version_id: uuid.UUID, files: dict[str, bytes]) -> str:
        with self.write_version(site_id, version_id) as writer:
            for rel_path, content in files.items():
                with writer.open_file(rel_path) as out:
                    out.write(content)
        return writer.storage_ref

    def file_path(self, storage_ref: str, rel_path: str) -> Path | None:
        try:
            base = self._version_root(storage_ref)
            candidate = (base / rel_path).resolve()
        except (StoreError, ValueError, OSError):
            return None
        if candidate == base or not candidate.is_relative_to(base):
            return None
        if not candidate.is_file():
            return None
        return candidate

    def delete_version(self, storage_ref: str) -> None:
        path = self._version_root(storage_ref)
        if path.exists():
            shutil.rmtree(path)

    def delete_site(self, site_id: uuid.UUID) -> None:
        """Every version of one site, and whatever else lies in its directory.

        Never raises on the removal itself: the rows are gone by now, and what
        stays behind is reclaimed by the nightly cleanup."""
        shutil.rmtree(self._version_root(str(site_id)), ignore_errors=True)

    def sweep_tmp(self, older_than: timedelta) -> int:
        boundary = datetime.now(tz=UTC) - older_than
        swept = 0
        for entry in self._tmp.iterdir():
            if _remove_if_older(entry, boundary):
                swept += 1
        return swept

    def reclaim(self, site_ids: set[str], storage_refs: set[str], older_than: timedelta) -> Reclaimed:
        """Moves the directories no row points at into `_reclaimed/`.

        `site_ids` and `storage_refs` are what the database holds, read before
        this call: a site or version made after that read is younger than
        `older_than` and left alone. Ids never recur, so a directory that lost
        its row can never be claimed by a new one. At the top only directories
        named like a site id are considered: `lost+found` on a fresh volume or
        a backup an operator put there is not ours.

        Two brakes against a database that does not belong to this volume (a
        wrong PLAK_DB_URL, a restored backup): a site of which no directory has
        a row keeps all of them, and nothing moves at all when more versions
        would go than stay.
        """
        boundary = datetime.now(tz=UTC) - older_than
        kept = 0
        orphans: list[tuple[str, int]] = []
        held: list[str] = []
        for site_dir in sorted(self._root.iterdir()):
            if not _is_id(site_dir.name) or not _is_directory(site_dir):
                continue
            if site_dir.name not in site_ids:
                if _older(site_dir, boundary):
                    orphans.append((site_dir.name, sum(1 for _ in site_dir.iterdir())))
                continue
            versions = sorted(site_dir.iterdir())
            refs = [f"{site_dir.name}/{version_dir.name}" for version_dir in versions]
            with_row = sum(ref in storage_refs for ref in refs)
            without_row = [
                ref
                for ref, version_dir in zip(refs, versions, strict=True)
                if ref not in storage_refs and _older(version_dir, boundary)
            ]
            if with_row == 0:
                held.extend(without_row)
                continue
            kept += with_row
            orphans.extend((ref, 1) for ref in without_row)
        if sum(weight for _, weight in orphans) > kept:
            return Reclaimed(moved=[], held=held + [ref for ref, _ in orphans])
        moved = [ref for ref, _ in orphans if self._quarantine(ref)]
        return Reclaimed(moved=moved, held=held)

    def _quarantine(self, ref: str) -> bool:
        """Renames `ref` into `_reclaimed/` with a fresh mtime, so the sweep
        counts the cooling-off period from the move rather than from its age."""
        self._reclaimed.mkdir(exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
        target = self._reclaimed / f"{stamp}-{ref.replace('/', '_')}"
        try:
            os.rename(self._root / ref, target)
        except OSError:
            return False
        os.utime(target)
        return True

    def sweep_reclaimed(self, older_than: timedelta) -> int:
        """Removes for good what `reclaim` moved aside longer than `older_than` ago."""
        if not self._reclaimed.is_dir():
            return 0
        boundary = datetime.now(tz=UTC) - older_than
        return sum(_remove_if_older(entry, boundary) for entry in self._reclaimed.iterdir())


@dataclass(frozen=True)
class Reclaimed:
    """Paths relative to the content root: what `reclaim` moved aside, and what
    it found without a row but left in place because a brake held."""

    moved: list[str]
    held: list[str]


def _older(entry: Path, boundary: datetime) -> bool:
    try:
        return datetime.fromtimestamp(entry.lstat().st_mtime, tz=UTC) < boundary
    except OSError:
        return False


def _is_id(name: str) -> bool:
    try:
        return str(uuid.UUID(name)) == name
    except ValueError:
        return False


def _is_directory(entry: Path) -> bool:
    """A directory itself, not a symlink to one."""
    try:
        return stat.S_ISDIR(entry.lstat().st_mode)
    except OSError:
        return False


def _remove_if_older(entry: Path, boundary: datetime) -> bool:
    if not _older(entry, boundary):
        return False
    if _is_directory(entry):
        shutil.rmtree(entry, ignore_errors=True)
    else:
        entry.unlink(missing_ok=True)
    return True
