"""Puts every file a version keeps compressed only back at its own path.

The way back from design.md §5.4a: an image from before it looks for each file
at its own path and knows nothing of `_encoded/`. The container's entrypoint
runs this before the server, and it acts only with PLAK_STORE_COMPRESSED off,
so rolling back is: switch that off, let the pod start once, find the log line
that says everything is back, then roll the image back
(docs/deploying-on-zad.md §7).

A gzip copy whose file is missing is unpacked into `_encoded/` under a
temporary name, renamed into place, and then it and its brotli copy go. A file
already at its own path keeps whatever sits beside it: that file is what is
served, and the rest is not this module's to remove. A run that stops halfway
is finished by the next one.

Nothing is touched unless the volume holds the result above
storage_min_free_bytes. The server starts either way: a pod that does not start
is every site down, which a refusal in the log is not.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path

from toon.config import Settings, load_settings
from toon.constants import ENCODED_SEGMENT
from toon.ingest import precompress
from toon.ingest.store import _is_directory, _is_id

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Pending:
    copy: Path
    plain: Path
    encoded_root: Path


@dataclass(frozen=True)
class Outcome:
    restored: int
    failed: int
    # Set when there was no room: then nothing was restored.
    needed: int | None = None
    available: int | None = None


def _unpacked_size(copy: Path) -> int:
    # The gzip trailer's ISIZE: the size modulo 2**32, exact for any file the
    # ingest limits let through. A copy too short to have one is damaged, and
    # fails on its own when it is unpacked.
    try:
        with copy.open("rb") as handle:
            handle.seek(-4, os.SEEK_END)
            return int.from_bytes(handle.read(4), "little")
    except OSError:
        return 0


def _pending(root: Path) -> list[_Pending]:
    found = []
    for site_dir in sorted(root.iterdir()):
        if not _is_id(site_dir.name) or not _is_directory(site_dir):
            continue
        for version_dir in sorted(site_dir.iterdir()):
            encoded_root = version_dir / ENCODED_SEGMENT
            gzip_root = encoded_root / precompress.GZIP
            for directory, _, names in os.walk(gzip_root):
                for name in sorted(names):
                    copy = Path(directory) / name
                    plain = version_dir / copy.relative_to(gzip_root)
                    if not plain.exists():
                        found.append(_Pending(copy, plain, encoded_root))
    return found


def _restore(item: _Pending) -> None:
    temporary = item.encoded_root / f".unpacking-{uuid.uuid4()}"
    try:
        with temporary.open("xb") as target:
            for piece in precompress.gunzip(item.copy):
                target.write(piece)
        item.plain.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, item.plain)
    finally:
        temporary.unlink(missing_ok=True)
    rel_path = item.copy.relative_to(item.encoded_root / precompress.GZIP)
    (item.encoded_root / precompress.BROTLI / rel_path).unlink(missing_ok=True)
    item.copy.unlink()


def _prune(encoded_root: Path) -> None:
    for directory, _, _ in os.walk(encoded_root, topdown=False):
        try:
            Path(directory).rmdir()
        except OSError:
            continue


def restore_all(root: Path, min_free: int) -> Outcome:
    pending = _pending(root)
    needed = sum(_unpacked_size(item.copy) for item in pending)
    available = shutil.disk_usage(root).free - min_free
    if pending and needed > available:
        return Outcome(restored=0, failed=0, needed=needed, available=available)
    restored = failed = 0
    for item in pending:
        try:
            _restore(item)
        except (OSError, zlib.error) as error:
            # A damaged copy must not stop the others.
            _logger.error("Cannot put %s back: %s", item.plain, error)
            failed += 1
        else:
            restored += 1
    for encoded_root in sorted({item.encoded_root for item in pending}):
        _prune(encoded_root)
    return Outcome(restored=restored, failed=failed)


def run(settings: Settings) -> Outcome | None:
    if settings.store_compressed:
        return None
    outcome = restore_all(settings.content_root, settings.storage_min_free_bytes)
    if outcome.needed is not None:
        _logger.error(
            "Not putting files back: they need %d bytes and the volume has %d above its reserve. "
            "An image from before compression cannot serve them yet.",
            outcome.needed,
            outcome.available,
        )
    elif outcome.failed:
        _logger.error(
            "Put %d files back, %d could not be; an image from before compression cannot serve those.",
            outcome.restored,
            outcome.failed,
        )
    else:
        _logger.info(
            "Put %d files back at their own path; no version keeps a file compressed only.", outcome.restored
        )
    return outcome


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(load_settings())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
