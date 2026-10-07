"""ContentStore: immutable version storage on the content volume.

Writing goes to {root}/_tmp/{uuid} in full first, and from there to
{group}/{site}/{version_id} with an atomic os.rename. The tempdir sits on
the same filesystem as the final directory, so the rename is atomic and has
no EXDEV/copy fallback. The spooled upload lives in {root}/_tmp as well:
outside every servable path, on the volume instead of in memory.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO

TMP_DIRNAME = "_tmp"
SPOOL_SUFFIX = ".upload"


class StoreError(Exception):
    """Invalid call into the ContentStore (e.g. a path outside the root)."""


def _check_rel_path(rel_path: str) -> None:
    # Defence in depth: the unpacker has validated paths already, but the
    # store independently refuses anything that could reach outside the
    # version root.
    if not rel_path or "\x00" in rel_path or "\\" in rel_path:
        raise StoreError(f"invalid relative path: {rel_path!r}")
    parts = rel_path.split("/")
    if any(part in ("", ".", "..") for part in parts) or rel_path.startswith("/"):
        raise StoreError(f"invalid relative path: {rel_path!r}")


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
            raise StoreError(f"path collides with a previously written path: {rel_path!r}") from error

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

    @property
    def root(self) -> Path:
        return self._root

    def _version_root(self, storage_ref: str) -> Path:
        """Resolved path of a version root, with a containment guarantee."""
        if "\x00" in storage_ref:
            raise StoreError("storage_ref contains a null byte")
        path = (self._root / storage_ref).resolve()
        if path == self._root or not path.is_relative_to(self._root):
            raise StoreError("storage_ref points outside the content root")
        if path == self._tmp or path.is_relative_to(self._tmp):
            raise StoreError("storage_ref points to the temp directory")
        return path

    def free_bytes(self) -> int:
        """Free space on the content volume."""
        return shutil.disk_usage(self._root).free

    def site_bytes(self, group: str, site: str) -> int:
        """What every version of one site together occupies.

        Measured on disk rather than kept in a column: a version's size is
        never revised, but the set of versions is (the cleanup job removes
        expired previews), and the volume is the only place where the two are
        always in step.
        """
        return _tree_bytes(self._version_root(f"{group}/{site}"))

    def new_spool_file(self) -> Path:
        """Unique path in the tempdir for an upload still to be received."""
        return self._tmp / f"{uuid.uuid4()}{SPOOL_SUFFIX}"

    @contextmanager
    def write_version(self, group: str, site: str, version_id: uuid.UUID) -> Iterator[VersionWriter]:
        """Work directory for a new version; on a normal exit atomically renamed
        to its final place, on an exception cleaned up entirely."""
        storage_ref = f"{group}/{site}/{version_id}"
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

    async def delete_versions(self, storage_refs: Iterable[str]) -> None:
        """Removes versions from disk in a worker thread: the app serves from
        one event loop, and an rmtree of a version with thousands of files
        would hold up every page view meanwhile."""
        refs = list(storage_refs)
        if refs:
            await asyncio.to_thread(self._delete_all, refs)

    def _delete_all(self, storage_refs: list[str]) -> None:
        for storage_ref in storage_refs:
            self.delete_version(storage_ref)

    def sweep_tmp(self, older_than: timedelta) -> int:
        boundary = datetime.now(tz=UTC) - older_than
        swept = 0
        for entry in self._tmp.iterdir():
            try:
                mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=UTC)
            except OSError:
                continue
            if mtime < boundary:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
                swept += 1
        return swept
