"""Unpacker: validates and unpacks an uploaded bundle.

Fail-closed: anything that is not explicitly a regular file with a safe
relative path is refused. The source is a file on disk (the spooled upload)
and every entry streams in 8KB chunks straight to the destination; nothing is
held in memory as a whole.

The root of the site is settled in three steps:

1. A chain of enclosing directories is peeled off: as long as the root holds
   exactly one entry and that entry is a directory, that directory becomes the
   new root. That never loses a file, because by definition nothing sits
   beside it, and it makes an archive holding only
   `mijnsite/dist/index.html` work too.
2. A `basispad` from the caller then wins: that directory becomes the root;
   whatever sits beside it is not published. That is the user confirming the
   proposal from step 3. The path is relative to the root after the peeling,
   but the spelling including the peeled prefix counts just as well, and so
   does a basispad that the peeling already took away: a fixed
   `basispad: dist` in a workflow must not depend on whether a LEESMIJ.md
   happened to sit beside `dist/` that time.
3. If that does not yield an `index.html` in the root, the bundle is refused,
   with the index.html paths found as a proposal.

Deliberately not: automatically picking the shortest or deepest index.html as
the root when something sits beside that directory in the root. That would
silently throw away files the user thought they were publishing (think of a
zipped site directory holding both `dist/` and `docs/`). The machine
proposes (step 3), the human confirms (step 2).

Capitalisation counts: only `index.html` is an index. `Index.html` does work
on the builder's case-insensitive Mac and not on the server, because both the
serving layer and the filesystem there look for exactly `index.html`. Such a
bundle is therefore refused, with a message that names the capitals instead
of a site that only works for the builder.

Reserved segments (`_preview`, `_version`) are about the root of the published
tree, not about the path as it sits in the archive: an enclosing directory by
that name is simply peeled off, and whatever ends up in the root after the
peeling and the basispad is refused.

Decompression is checked incrementally against the limits, so a zip/tar bomb
never gets past them: the sizes from the archive headers are deliberately not
trusted (a header that already claims more than the limit is an early
refusal, though). That also holds for the pre-pass a tar needs to determine
its root: reaching the next header means decompressing everything in between
in a gzip stream, so that pass refuses on those same claimed sizes and adds
them up against `max_total`.

Entries that are walked cost memory even when they write nothing, and are
therefore bounded separately: `max_files` applies to what lands in the
site (directory entries included), `max_archive_entries` to everything the
archive contains, so also to what falls outside the chosen root.
"""

from __future__ import annotations

import gzip
import logging
import re
import stat
import tarfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from plak import i18n, messages
from plak.config import Settings
from plak.constants import INDEX_FILE, RESERVED_SEGMENTS
from plak.messages import Msg

_logger = logging.getLogger(__name__)

READ_CHUNK = 8192

# Upper bound on the proposal in the error response: enough to recognise the
# right directory, short enough to fit in an error message and in constant
# memory.
MAX_INDEX_SUGGESTIONS = 5

# Entries outside the chosen root do not end up in the site, but walking past
# them still costs memory (tarfile holds on to every member it walks, roughly
# half a KB). They therefore get their own, wider limit than the site itself: a
# node_modules beside your dist must not hold up a deploy, an archive with
# hundreds of thousands of entries must.
ARCHIVE_ENTRY_FACTOR = 50

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class BundleError(Exception):
    """Refused or invalid bundle, with a machine-readable reason code.

    It names a message key from plak/messages.py rather than a text, so the
    same refusal reaches the client in the language it asked for; `reason` is
    the code that key carries.

    On a refusal about the root, `index_candidates` carries the index.html
    paths found, shortest first, relative to the root after the peeling:
    exactly the values that can be sent back as `basePath`.
    """

    def __init__(
        self,
        key: str,
        *,
        params: Mapping[str, object] | None = None,
        index_candidates: tuple[str, ...] = (),
    ) -> None:
        self.message = Msg(key, dict(params or {}))
        self.reason = messages.code_of(key)
        self.index_candidates = index_candidates
        super().__init__(messages.render(i18n.API_DEFAULT, self.message))


def _unreadable(error: Exception) -> str:
    """Logs the unreadable archive and returns what the client may be told.

    Never `str(error)`: an OSError names the absolute path of the spool file
    on the server, and a problem+json `detail` carries no internal details.
    """
    _logger.warning("Archief onleesbaar", exc_info=error)
    return type(error).__name__


class Destination(Protocol):
    """Where unpacked files go (the store's VersionWriter)."""

    def open_file(self, rel_path: str) -> IO[bytes]: ...


@dataclass(frozen=True)
class Limits:
    max_file: int
    max_total: int
    max_files: int
    max_depth: int

    @classmethod
    def from_settings(cls, settings: Settings) -> Limits:
        return cls(
            max_file=settings.ingest_max_file,
            max_total=settings.ingest_max_total,
            max_files=settings.ingest_max_files,
            max_depth=settings.ingest_max_depth,
        )

    @property
    def max_archive_entries(self) -> int:
        """Limit on the number of archive entries walked, published or not.
        Derived from `max_files`, so an instance that allows bigger sites
        allows roomier bundles as a matter of course."""
        return self.max_files * ARCHIVE_ENTRY_FACTOR


class _EntryCounter:
    """Bounds how many entries one pass over the archive touches.

    Separate from the tree, because this also counts what falls outside the
    root and therefore writes nothing; a counter of its own per pass, so the
    pre-pass and the unpacking do not add up against each other.
    """

    def __init__(self, limits: Limits) -> None:
        self._limit = limits.max_archive_entries
        self._entries = 0

    def count(self) -> None:
        self._entries += 1
        if self._entries > self._limit:
            raise BundleError(
                "TOO_MANY_FILES.archive_entries", params={"limit": self._limit}
            )


# Metadata the Finder bakes into a zip. Without this the most common route of
# all strands: directory in Finder, right-click, "Compress". The archive then
# holds `mijn-site/` beside `__MACOSX/`, two things in the root, so the
# enclosure is not peeled off and there is no index.html in the root. These
# entries are ignored: they do not count towards the root, they are not
# proposed as an index and they are not published.
_JUNK_DIR = "__MACOSX"
_JUNK_FILES = frozenset({".DS_Store"})
# AppleDouble: the Finder puts the resource fork of `x.html` in `._x.html`.
_JUNK_PREFIX = "._"


def is_archive_junk(segments: Sequence[str]) -> bool:
    """Whether this path is OS metadata that belongs nowhere in the site.

    Only to be called after the structural path check has run: a path pointing
    outside the destination has to be refused, not silently skipped.
    """
    if any(segment == _JUNK_DIR for segment in segments):
        return True
    if not segments:
        return False
    name = segments[-1]
    return name in _JUNK_FILES or name.startswith(_JUNK_PREFIX)


def _safe_segments(raw_value: str) -> list[str]:
    """Structural path check: whatever gets through here cannot point outside
    the destination. Applies to every entry, including one that ends up
    outside the chosen root and is therefore not published."""
    if "\x00" in raw_value:
        raise BundleError("NULL_BYTE", params={"path": raw_value})
    path = raw_value.replace("\\", "/")
    if path.startswith("/") or _WINDOWS_DRIVE_RE.match(path):
        raise BundleError("ABSOLUTE_PATH", params={"path": raw_value})

    segments: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise BundleError("PATH_TRAVERSAL", params={"path": raw_value})
        segments.append(segment)

    if not segments:
        raise BundleError("EMPTY_PATH", params={"path": raw_value})
    return segments


# Files you never want on a website and that come along by themselves with a
# zipped site directory. Refuse, do not skip: whoever sends these along
# packed something other than what they thought, and that is worth saying out
# loud. The CLI already skips `.git` while packing; this is about the browser
# upload, where the archive is already made.
SECRETS_DIR = ".git"
SECRETS_FILE_PREFIX = ".env"


def _secrets_segment(segments: Sequence[str]) -> str | None:
    """The first segment that must not be published, or None.

    No blanket ban on dotfiles: `.well-known/` is precisely a path a site
    ought to be able to serve.
    """
    for segment in segments:
        if segment == SECRETS_DIR:
            return segment
    name = segments[-1] if segments else ""
    # `.env`, `.env.local`, `.env.production`; `.env.example` too, because the
    # price of that false alarm is an error message and the price of the other
    # way round is a leaked token.
    if name == SECRETS_FILE_PREFIX or name.startswith(SECRETS_FILE_PREFIX + "."):
        return name
    return None


def _check_policy(segments: list[str], raw_value: str, limits: Limits) -> None:
    """Limits that are about the published tree, on the path as it sits in the
    archive (so an enclosing directory counts towards the depth)."""
    if len(segments) > limits.max_depth:
        raise BundleError(
            "TOO_DEEP", params={"max_depth": limits.max_depth, "path": raw_value}
        )
    secret = _secrets_segment(segments)
    if secret is not None:
        raise BundleError("SECRET_FILE", params={"path": raw_value, "secret": secret})


def _scannable_segments(raw_value: str) -> list[str] | None:
    """Segments for determining the root, or None as soon as the path is going
    to be refused anyway. Refusing does not happen here: the unpacking does
    that, with the precise reason and on the entry where it goes wrong."""
    try:
        return _safe_segments(raw_value)
    except BundleError:
        return None


def _common(links: Sequence[str], right: Sequence[str]) -> int:
    count = 0
    for one, two in zip(links, right, strict=False):
        if one != two:
            break
        count += 1
    return count


@dataclass(frozen=True)
class _Root:
    """The chosen root of the site, split by origin: what the peeling took away
    and what the caller chose as basispad.

    The split exists for the error messages: the user recognises their own
    basispad, not the prefix the peeling put in front of it.
    """

    peeled: tuple[str, ...] = ()
    base: tuple[str, ...] = ()

    @property
    def segments(self) -> tuple[str, ...]:
        return self.peeled + self.base


class _Scan:
    """Reads only the names from the archive index, never the contents.

    Delivers three things: the chain of enclosing directories, the depths at
    which the basispad occurs in the archive, and (in case the root turns out
    to have no index.html) the shortest index.html paths as a proposal. Memory
    is constant: a single segment list for the chain, at most
    MAX_INDEX_SUGGESTIONS proposals, a single path for the capitalisation
    variant and a set of depths that never grows beyond the deepest path.

    The chain is the longest prefix every entry agrees on, cut short by two
    things: a file at level n stops the peeling at level n-1 (deeper would
    swallow the file itself), and two entries that diverge at level j mean two
    things sit beside each other there. A directory entry above the chain is
    merely an intermediate directory and constrains nothing; `dist/` being in
    the archive does not say that something sits beside `dist/sub/`.
    """

    def __init__(self, base: Sequence[str] = ()) -> None:
        self._chain: list[str] | None = None
        self._bound_at: int | None = None
        self._invalid = False
        self._candidates: list[list[str]] = []
        self._variant: list[str] | None = None
        self._base = list(base)
        self._base_lower = [segment.lower() for segment in base]
        self._base_depths: set[int] = set()
        self._base_variant: list[str] | None = None

    def see(self, raw_path_value: str, is_map: bool) -> None:
        segments = _scannable_segments(raw_path_value)
        if segments is None:
            self._invalid = True
            return
        if is_archive_junk(segments):
            return
        self._note_base(segments)
        if not is_map:
            self._note_index(segments)
            self._bound(len(segments) - 1)
        if self._chain is None:
            self._chain = segments
            return
        common = _common(self._chain, segments)
        if common < min(len(self._chain), len(segments)):
            self._bound(common)
        elif len(segments) > len(self._chain):
            self._chain = segments

    def _bound(self, level: int) -> None:
        self._bound_at = level if self._bound_at is None else min(self._bound_at, level)

    def _note_base(self, segments: list[str]) -> None:
        """Records at which depths the basispad occurs in this archive path.

        That way the scan settles which reading of the basispad really exists:
        the documented one (relative to the root after the peeling) or a
        shallower one, in which the user typed the peeled prefix along
        themselves or the peeling already took their directory away.
        """
        if not self._base:
            return
        for i in range(len(segments) - len(self._base) + 1):
            part = segments[i : i + len(self._base)]
            if part == self._base:
                self._base_depths.add(i)
            elif self._base_variant is None and [s.lower() for s in part] == self._base_lower:
                self._base_variant = part

    def _note_index(self, segments: list[str]) -> None:
        if segments[-1] == INDEX_FILE:
            self._candidates.append(segments)
            self._candidates.sort(key=lambda path: (len(path), path))
            del self._candidates[MAX_INDEX_SUGGESTIONS:]
        elif segments[-1].lower() == INDEX_FILE and self._variant is None:
            self._variant = segments

    @property
    def root(self) -> list[str]:
        """The chain to peel off. Empty as soon as a path is going to be refused
        anyway: shifting the root on an archive we do not trust makes no
        sense."""
        if self._invalid or self._chain is None:
            return []
        if self._bound_at is None:
            # Only directory entries seen: the whole archive is enclosure.
            return list(self._chain)
        return self._chain[: self._bound_at]

    def pick_root(self) -> _Root:
        """The root of the site: the peeled prefix, and behind it the basispad at
        the deepest place where it really occurs in the archive.

        That deepest place is the documented reading as soon as it exists; if
        the basispad occurs only shallower, that is the only reading the user
        can have meant. If it occurs nowhere, the documented reading stands and
        the unpacking refuses with BASE_PATH_UNKNOWN."""
        peeled = self.root
        if not self._base:
            return _Root(peeled=tuple(peeled))
        depths = [i for i in self._base_depths if i <= len(peeled)]
        depth = max(depths) if depths else len(peeled)
        return _Root(peeled=tuple(peeled[:depth]), base=tuple(self._base))

    def base_case_variant(self) -> str | None:
        """The path that looks like the basispad but is spelled differently, or
        None as soon as the basispad itself occurs exactly somewhere."""
        if self._base_depths or self._base_variant is None:
            return None
        return "/".join(self._base_variant)

    def suggestions(self, root: Sequence[str]) -> tuple[str, ...]:
        return tuple("/".join(path[len(root) :]) for path in self._candidates)

    def case_variant(self, root: Sequence[str]) -> str | None:
        if self._variant is None:
            return None
        return "/".join(self._variant[len(root) :])


@dataclass(frozen=True)
class _Placement:
    """Where an archive entry lands in the site.

    `inside` says whether the entry falls under the chosen root (or is that
    directory itself), `path` is the path within the site, or None when there
    is nothing to write.
    """

    inside: bool
    path: str | None


def _reserved_error(segment: str, root: _Root) -> BundleError:
    """`_preview` and `_version` are platform names and must not sit in the root
    of the site. The message says where that root comes from, so the user is
    not served a prefix they never typed."""
    if root.base:
        where = Msg("where.base_path", {"base": "/".join(root.base)})
    elif root.peeled:
        where = Msg("where.peeled", {"peeled": "/".join(root.peeled)})
    else:
        where = Msg("where.bundle_root")
    return BundleError("RESERVED_SEGMENT", params={"segment": segment, "where": where})


def _place(raw_value: str, root: _Root, limits: Limits) -> _Placement:
    """Determines where an entry is placed and refuses an unsafe path.

    The structural check applies to every entry; the policy limits (reserved
    segment in the root, directory depth) apply only to what gets published,
    so a `basispad` does not trip over a deep node_modules that the user
    deliberately kept out of the publication.
    """
    segments = _safe_segments(raw_value)
    if is_archive_junk(segments):
        return _Placement(inside=False, path=None)
    root_segments = list(root.segments)
    if len(segments) < len(root_segments) or segments[: len(root_segments)] != root_segments:
        return _Placement(inside=False, path=None)
    rest = segments[len(root_segments) :]
    if not rest:
        # The root itself: proves the basispad exists, writes nothing.
        return _Placement(inside=True, path=None)
    _check_policy(segments, raw_value, limits)
    if rest[0] in RESERVED_SEGMENTS:
        raise _reserved_error(rest[0], root)
    return _Placement(inside=True, path="/".join(rest))


def _validate_base_path(base_path: str | None, limits: Limits) -> tuple[str, ...]:
    """The same path validation as for an archive entry; an invalid basispad is
    a refusal, never a silent fallback to the archive root."""
    if base_path is None:
        return ()
    try:
        segments = _safe_segments(base_path)
        _check_policy(segments, base_path, limits)
        if segments[0] in RESERVED_SEGMENTS:
            # Not because something reserved would be published (the
            # contents of that directory land in the root), but because a
            # platform name as input ought to be refused fail-closed.
            raise BundleError("RESERVED_SEGMENT.base_path", params={"segment": segments[0]})
    except BundleError as error:
        raise BundleError("BASE_PATH_INVALID", params={"cause": error.message}) from error
    return tuple(segments)


@dataclass(frozen=True)
class _Outcome:
    """What the unpacking established about the bundle as a whole."""

    count: int
    root_seen: bool
    index_seen: bool
    suggestions: tuple[str, ...] = ()
    case_variant: str | None = None
    # The root does exist, but is a file: only possible with a basispad, and
    # the difference between "does not exist" and "is not a directory".
    root_is_file: bool = False
    # What the peeling took away, and the path that looks like the basispad
    # but is spelled differently: both only for the error message.
    peeled: str = ""
    base_variant: str | None = None


class _Tree:
    """Tracks the paths only (no contents): collisions and counts."""

    def __init__(self, limits: Limits, suggestions: tuple[str, ...] = ()) -> None:
        self._limits = limits
        # Only to pass along at the entry limit: whoever zips their whole
        # site directory hits that first, and then the directory holding
        # the index.html is exactly the answer they need.
        self._suggestions = suggestions
        self._files: set[str] = set()
        self._dirs: set[str] = set()
        self._entries = 0
        self.total = 0

    @property
    def count(self) -> int:
        return len(self._files)

    def count_entry(self) -> None:
        """Counts one archive entry, a directory entry included: it writes
        nothing, but both tarfile and our own bookkeeping hold memory per
        entry walked, so counting files alone does not bound that."""
        self._entries += 1
        if self._entries > self._limits.max_files:
            raise BundleError(
                "TOO_MANY_FILES",
                params={"limit": self._limits.max_files},
                index_candidates=self._suggestions,
            )

    def check_upfront(self, path: str, claimed: int) -> None:
        """Every check that can be done before the target file is opened."""
        self.count_entry()
        if path in self._files or path in self._dirs:
            raise BundleError("DUPLICATE_PATH", params={"path": path})
        parts = path.split("/")
        for i in range(1, len(parts)):
            prefix = "/".join(parts[:i])
            if prefix in self._files:
                raise BundleError("DUPLICATE_PATH", params={"path": prefix})
        if claimed > self._limits.max_file:
            raise BundleError(
                "FILE_TOO_LARGE", params={"path": path, "max_file": self._limits.max_file}
            )
        if self.total + claimed > self._limits.max_total:
            raise BundleError(
                "TOTAL_TOO_LARGE", params={"max_total": self._limits.max_total}
            )

    def register(self, path: str, size: int) -> None:
        parts = path.split("/")
        for i in range(1, len(parts)):
            self._dirs.add("/".join(parts[:i]))
        self._files.add(path)
        self.total += size


def _copy_bounded(source: IO[bytes], target: IO[bytes], path: str, tree: _Tree, limits: Limits) -> int:
    """Copies in chunks and counts the real unpacked bytes against the limits."""
    read_bytes_count = 0
    while True:
        part_item = source.read(READ_CHUNK)
        if not part_item:
            return read_bytes_count
        read_bytes_count += len(part_item)
        if read_bytes_count > limits.max_file:
            raise BundleError(
                "FILE_TOO_LARGE", params={"path": path, "max_file": limits.max_file}
            )
        if tree.total + read_bytes_count > limits.max_total:
            raise BundleError("TOTAL_TOO_LARGE", params={"max_total": limits.max_total})
        target.write(part_item)


def _write_entry(
    source: IO[bytes], path: str, claimed: int, tree: _Tree, destination: Destination, limits: Limits
) -> None:
    tree.check_upfront(path, claimed)
    with destination.open_file(path) as target:
        size = _copy_bounded(source, target, path, tree, limits)
    tree.register(path, size)


@dataclass
class _Loop:
    """Bookkeeping over one pass through an archive."""

    tree: _Tree
    root: _Root
    root_seen: bool
    index_seen: bool = False
    root_is_file: bool = False
    suggestions: tuple[str, ...] = ()
    case_variant: str | None = None
    peeled: str = ""
    base_variant: str | None = None

    def see_entry(self, placement: _Placement, is_map: bool) -> None:
        """Tracks whether the root has been seen and counts what lands in the
        site. What falls outside the root does not count towards that; the
        number of entries walked was bounded in the pre-pass already."""
        if not placement.inside:
            return
        self.root_seen = True
        if placement.path is None:
            self.root_is_file = self.root_is_file or not is_map
        if is_map or placement.path is None:
            self.tree.count_entry()

    def outcome(self) -> _Outcome:
        return _Outcome(
            count=self.tree.count,
            root_seen=self.root_seen,
            index_seen=self.index_seen,
            suggestions=self.suggestions,
            case_variant=self.case_variant,
            root_is_file=self.root_is_file,
            peeled=self.peeled,
            base_variant=self.base_variant,
        )


def _start_loop(scan: _Scan, limits: Limits) -> _Loop:
    peeled = scan.root
    root = scan.pick_root()
    suggestions = scan.suggestions(peeled)
    return _Loop(
        tree=_Tree(limits, suggestions),
        root=root,
        # Without a basispad the chosen root is by construction the root of
        # the archive; only a supplied basispad can turn out to be unfindable.
        root_seen=not root.base,
        # The proposals are relative to the root after the peeling: exactly
        # the form in which they can be sent back as a basispad.
        suggestions=suggestions,
        case_variant=scan.case_variant(peeled),
        peeled="/".join(peeled),
        base_variant=scan.base_case_variant(),
    )


# The tail of a zip: the end-of-central-directory record, behind it a comment
# of at most 65535 bytes, and in a zip64 the locator that points at the record
# holding the real totals.
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_SIZE = 22
_MAX_ZIP_COMMENT = 65535
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE = 20
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_EOCD_SIZE = 56
# One record in the central directory: the fixed fields, with the name, the
# extra field and the comment behind them.
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_CENTRAL_RECORD_SIZE = 46


def _eocd_start(tail: bytes, tail_offset: int, size: int) -> int | None:
    """Where the end-of-central-directory record begins in `tail`, or None.

    Scanned from the back, because a comment of up to 65535 bytes may follow
    the record and those bytes can hold the signature themselves. A candidate
    counts only when its comment length reaches exactly the end of the file.
    """
    end = len(tail)
    while True:
        start = tail.rfind(_EOCD_SIGNATURE, 0, end)
        if start < 0:
            return None
        if len(tail) - start >= _EOCD_SIZE:
            comment = int.from_bytes(tail[start + 20 : start + 22], "little")
            if tail_offset + start + _EOCD_SIZE + comment == size:
                return start
        end = start + len(_EOCD_SIGNATURE) - 1


def _zip64_directory_size(handle: IO[bytes], tail: bytes, start: int, size: int) -> int | None:
    """The size of the central directory out of the zip64 record, which the
    classic record refers to as soon as a total does not fit in its own
    fields. None as soon as that trail does not hold up."""
    locator = start - _ZIP64_LOCATOR_SIZE
    if locator < 0 or tail[locator : locator + 4] != _ZIP64_LOCATOR_SIGNATURE:
        return None
    offset = int.from_bytes(tail[locator + 8 : locator + 16], "little")
    if offset + _ZIP64_EOCD_SIZE > size:
        return None
    handle.seek(offset)
    record = handle.read(_ZIP64_EOCD_SIZE)
    if record[:4] != _ZIP64_EOCD_SIGNATURE:
        return None
    return int.from_bytes(record[40:48], "little")


def _count_central_records(handle: IO[bytes], start: int, limit: int) -> int:
    """The number of records in the central directory, counted to at most
    `limit` + 1: enough to decide, cheap for an archive that claims millions.

    Counting stops at the first record that does not parse. zipfile stumbles
    over that same record, so it never builds more objects than were counted
    here; the claimed number of entries is deliberately not trusted, because
    zipfile reads to the end of the directory and not to that number.
    """
    handle.seek(start)
    count = 0
    while count <= limit:
        record = handle.read(_CENTRAL_RECORD_SIZE)
        if len(record) < _CENTRAL_RECORD_SIZE or record[:4] != _CENTRAL_SIGNATURE:
            return count
        # The name, the extra field and the comment sit behind the record.
        behind = sum(int.from_bytes(record[at : at + 2], "little") for at in (28, 30, 32))
        handle.seek(behind, 1)
        count += 1
    return count


def _zip_entry_bound(handle: IO[bytes], limit: int) -> int | None:
    """How many entries the zip holds, counted to at most `limit` + 1, or None
    when the tail does not parse (zipfile then refuses the archive).

    zipfile parses the whole central directory into memory the moment the
    archive opens, hundreds of bytes per entry, so an entry counter over
    `infolist()` comes too late: an archive that ships millions of entries has
    then already cost gigabytes. Walking the directory itself costs nothing
    beyond the records walked, and stops at the limit.
    """
    handle.seek(0, 2)
    size = handle.tell()
    tail_size = min(size, _EOCD_SIZE + _MAX_ZIP_COMMENT)
    handle.seek(size - tail_size)
    tail = handle.read(tail_size)
    start = _eocd_start(tail, size - tail_size, size)
    if start is None:
        return None
    directory_size = int.from_bytes(tail[start + 12 : start + 16], "little")
    total = int.from_bytes(tail[start + 10 : start + 12], "little")
    zip64_size = 0
    if total == 0xFFFF or directory_size == 0xFFFFFFFF:
        found = _zip64_directory_size(handle, tail, start, size)
        if found is None:
            return None
        directory_size = found
        zip64_size = _ZIP64_EOCD_SIZE + _ZIP64_LOCATOR_SIZE
    # The directory ends where its own records end: right in front of the
    # zip64 records, if any, and the classic record. Reckoning back from there
    # rather than from the claimed offset keeps a zip with something in front
    # of it (a self-extracting header) working, exactly as zipfile does.
    directory_start = size - tail_size + start - zip64_size - directory_size
    if directory_start < 0:
        return None
    return _count_central_records(handle, directory_start, limit)


def _check_zip_entry_bound(source: Path, limits: Limits) -> None:
    try:
        with source.open("rb") as handle:
            found = _zip_entry_bound(handle, limits.max_archive_entries)
    except OSError as error:
        raise BundleError("INVALID_ARCHIVE.zip", params={"error": _unreadable(error)}) from error
    if found is not None and found > limits.max_archive_entries:
        raise BundleError(
            "TOO_MANY_FILES.archive_entries", params={"limit": limits.max_archive_entries}
        )


def _unpack_zip(source: Path, destination: Destination, limits: Limits, base: tuple[str, ...]) -> _Outcome:
    # zipfile reads the whole central directory into memory as soon as the
    # archive opens, so the entry counter below does not bound that first
    # peak: the tail of the archive says beforehand how many entries that
    # directory holds at most.
    _check_zip_entry_bound(source, limits)
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as error:
        raise BundleError("INVALID_ARCHIVE.zip", params={"error": _unreadable(error)}) from error

    with archive:
        # The central directory is in memory already; this extra pass over
        # the names costs no archive access. The counter cuts it off just as
        # hard as the unpacking itself, so an archive with a million entries
        # is not scanned in full first.
        scan = _Scan(base)
        counter = _EntryCounter(limits)
        for info in archive.infolist():
            counter.count()
            # zipfile truncates a name at the first NUL byte, so an entry
            # declared `index.html\0.bak` arrives as `index.html` and the
            # archive listing disagrees with what lands on disk.
            # `orig_filename` is the only place where that byte is still
            # visible.
            if "\x00" in info.orig_filename:
                raise BundleError("NULL_BYTE", params={"path": info.orig_filename})
            scan.see(info.filename, info.is_dir())
        loop = _start_loop(scan, limits)

        for info in archive.infolist():
            mode_ = info.external_attr >> 16
            if stat.S_ISLNK(mode_):
                raise BundleError("SYMLINK_REFUSED", params={"name": info.filename})
            is_map = info.is_dir()
            # Only the file type bits count: many zippers set permission
            # bits only (type 0), which is a regular file.
            if not is_map and stat.S_IFMT(mode_) not in (0, stat.S_IFREG):
                raise BundleError("SPECIAL_FILE", params={"name": info.filename})
            placement = _place(info.filename, loop.root, limits)
            loop.see_entry(placement, is_map)
            if is_map or placement.path is None:
                continue
            try:
                with archive.open(info) as stream:
                    _write_entry(stream, placement.path, info.file_size, loop.tree, destination, limits)
            except BundleError:
                raise
            except (zipfile.BadZipFile, OSError, RuntimeError) as error:
                raise BundleError(
                    "INVALID_ARCHIVE.zip_entry", params={"error": _unreadable(error)}
                ) from error
            loop.index_seen = loop.index_seen or placement.path == INDEX_FILE
    return loop.outcome()


def _tar_scan(source: Path, limits: Limits, base: tuple[str, ...]) -> _Scan:
    """Pre-pass over the tar headers only, to determine the root.

    A tar has no central directory the way a zip does, so the names come from
    a pass of our own over the spool file. That pass holds nothing more than
    the chain and at most five proposals, and the entry counter bounds the
    number of headers walked here just as hard as during the unpacking.

    Reaching the next header is not a free jump: in a gzip stream everything
    in between has to be decompressed. The claimed sizes from the headers are
    therefore held against the limits here already, so this pass never
    decompresses more than a bundle is allowed to be under those same limits.
    Otherwise an archive that ought to be refused on its first header would
    have to be read through in full first.
    """
    scan = _Scan(base)
    counter = _EntryCounter(limits)
    claimed_total = 0
    with tarfile.open(source, mode="r:gz") as archive:
        for member in archive:
            counter.count()
            if member.isfile():
                if member.size > limits.max_file:
                    raise BundleError(
                        "FILE_TOO_LARGE",
                        params={"path": member.name, "max_file": limits.max_file},
                    )
                claimed_total += member.size
                if claimed_total > limits.max_total:
                    raise BundleError(
                        "TOTAL_TOO_LARGE.archive", params={"max_total": limits.max_total}
                    )
            scan.see(member.name, member.isdir())
    return scan


class _TarSource(gzip.GzipFile):
    """The gzip stream under the tar, with the raw header blocks within reach.

    tarfile decodes a member name only up to the first NUL byte, so an entry
    declared `index.html\\0.bak` arrives as `index.html` and the archive
    listing disagrees with what lands on disk. The raw header is the only
    place where that byte is still visible.

    Blocks read since the previous member was handed over are kept: that is
    the header of the member being handed over now, and in front of it the
    long-name blocks that belong to it.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(filename=str(path), mode="rb")
        self._blocks: dict[int, bytes] = {}

    def read(self, size: int = -1) -> bytes:
        start = self.tell()
        data = super().read(size)
        if len(data) == tarfile.BLOCKSIZE:
            self._blocks[start] = data
        return data

    def raw_name_of(self, member: tarfile.TarInfo) -> bytes:
        """The name bytes this member was declared under, NUL padding removed.

        A name too long for the header sits in a block of its own: the GNU
        variant puts it behind its own header, the pax variant in a record
        tarfile reads NUL and all, so `_safe_segments` refuses that one on the
        name itself. The bookkeeping is dropped along the way: a member is
        handed over once.
        """
        header = self._blocks.get(member.offset, b"")
        if header[156:157] == tarfile.GNUTYPE_LONGNAME:
            raw_name = self._blocks.get(member.offset + tarfile.BLOCKSIZE, b"")
        else:
            raw_name = header[0:100]
        self._blocks.clear()
        return raw_name.rstrip(b"\0")


def _unpack_tar(source: Path, destination: Destination, limits: Limits, base: tuple[str, ...]) -> _Outcome:
    try:
        loop = _start_loop(_tar_scan(source, limits, base), limits)
        # Deliberately a GzipFile and not the stream mode r|gz: GzipFile
        # decompresses per read with max_length (8KB), the stream mode per
        # whole gzip block, which for highly compressible data can be tens of
        # MB per block. Members are read in order, so GzipFile never has to
        # seek back.
        with _TarSource(source) as stream_source, tarfile.open(fileobj=stream_source, mode="r:") as archive:
            for member in archive:
                raw_name = stream_source.raw_name_of(member)
                if b"\0" in raw_name:
                    raise BundleError(
                        "NULL_BYTE",
                        params={"path": raw_name.decode(archive.encoding, "surrogateescape")},
                    )
                if member.issym():
                    raise BundleError("SYMLINK_REFUSED", params={"name": member.name})
                if member.islnk():
                    raise BundleError("HARDLINK_REFUSED", params={"name": member.name})
                if not member.isdir() and not member.isfile():
                    raise BundleError("SPECIAL_FILE", params={"name": member.name})
                placement = _place(member.name, loop.root, limits)
                loop.see_entry(placement, member.isdir())
                if member.isdir() or placement.path is None:
                    continue
                stream = archive.extractfile(member)
                if stream is None:  # pragma: no cover - extractfile returns None
                    # only for a directory, a special file or a link, and those
                    # three are caught or skipped above already. Kept because
                    # tarfile gives no guarantee and None would turn into an
                    # AttributeError here.
                    raise BundleError("INVALID_ARCHIVE.tar_entry", params={"name": member.name})
                with stream:
                    _write_entry(stream, placement.path, member.size, loop.tree, destination, limits)
                loop.index_seen = loop.index_seen or placement.path == INDEX_FILE
    except BundleError:
        raise
    except (tarfile.TarError, OSError, EOFError) as error:
        raise BundleError("INVALID_ARCHIVE.tar", params={"error": _unreadable(error)}) from error
    return loop.outcome()


def _unpack_html(source: Path, destination: Destination, limits: Limits) -> _Outcome:
    tree = _Tree(limits)
    try:
        with source.open("rb") as stream:
            _write_entry(stream, INDEX_FILE, source.stat().st_size, tree, destination, limits)
    except OSError as error:
        raise BundleError("INVALID_ARCHIVE.html", params={"error": _unreadable(error)}) from error
    return _Outcome(count=tree.count, root_seen=True, index_seen=True)


def _base_path_unknown_error(base_path: str | None, outcome: _Outcome) -> BundleError:
    """The basispad occurs nowhere in the archive. The message names the two
    things the user cannot see in their own bundle: a directory that differs
    in capitalisation only, and the prefix the peeling took away."""
    params: dict[str, object] = {"base_path": base_path}
    if outcome.base_variant is not None:
        key = "BASE_PATH_UNKNOWN.case_variant"
        params["variant"] = outcome.base_variant
    elif outcome.peeled:
        key = "BASE_PATH_UNKNOWN.peeled"
        params["peeled"] = outcome.peeled
    else:
        key = "BASE_PATH_UNKNOWN"
    return BundleError(key, params=params, index_candidates=outcome.suggestions)


def _suggestion_sentence(shortest: str) -> Msg:
    """What can be done with the nearest index.html. If it already sits in the
    root there is no directory to point at, and the answer is to leave the
    basispad out."""
    if "/" not in shortest:
        return Msg("suggestion.in_root", {"index": INDEX_FILE})
    return Msg("suggestion.nearest", {"path": shortest})


def _no_index_error(base_path: str | None, outcome: _Outcome) -> BundleError:
    if base_path is not None:
        # A basispad with no files in it (an empty directory) is the same
        # problem as a basispad without an index: the directory is no good as
        # a root, and the candidates point the way to one that is.
        what = "empty" if outcome.count == 0 else "no_index"
        params: dict[str, object] = {"base_path": base_path, "index": INDEX_FILE}
        if outcome.suggestions:
            key = f"BASE_PATH_WITHOUT_INDEX.{what}_with_suggestion"
            params["suggestion"] = _suggestion_sentence(outcome.suggestions[0])
        else:
            key = f"BASE_PATH_WITHOUT_INDEX.{what}"
        return BundleError(key, params=params, index_candidates=outcome.suggestions)

    if not outcome.suggestions:
        if outcome.case_variant is not None:
            return BundleError(
                "NO_INDEX.case_variant",
                params={"index": INDEX_FILE, "variant": outcome.case_variant},
            )
        return BundleError("NO_INDEX", params={"index": INDEX_FILE})

    # The shortest candidate always sits in a subdirectory: were it in the
    # root, there would be an index and we would not get here.
    shortest = outcome.suggestions[0]
    dir_name = shortest.rsplit("/", 1)[0]
    return BundleError(
        "NO_INDEX.nearest",
        params={"index": INDEX_FILE, "shortest": shortest, "directory": dir_name},
        index_candidates=outcome.suggestions,
    )


def unpack(
    filename: str,
    source: Path,
    destination: Destination,
    limits: Limits,
    base_path: str | None = None,
) -> int:
    """Unpacks the upload in `source` into `destination`; returns the number of
    files written and raises BundleError on a refusal. The caller cleans up the
    destination on an error (the store does that in write_version).

    The refusal over a missing index deliberately comes after the unpacking:
    an unsafe or oversized path keeps its own, more precise reason that way,
    and nothing is left behind regardless because the work directory
    disappears on every error.
    """
    base = _validate_base_path(base_path, limits)
    name = filename.lower()
    if name.endswith(".html"):
        if base:
            raise BundleError("BASE_PATH_UNKNOWN.html_file", params={"base_path": base_path})
        outcome = _unpack_html(source, destination, limits)
    elif name.endswith(".zip"):
        outcome = _unpack_zip(source, destination, limits, base)
    elif name.endswith((".tar.gz", ".tgz")):
        outcome = _unpack_tar(source, destination, limits, base)
    else:
        raise BundleError("UNKNOWN_FORMAT")

    if not outcome.root_seen:
        raise _base_path_unknown_error(base_path, outcome)
    if outcome.root_is_file:
        raise BundleError(
            "BASE_PATH_UNKNOWN.file",
            params={"base_path": base_path},
            index_candidates=outcome.suggestions,
        )
    if outcome.count == 0:
        # With a basispad, "empty" is a statement about that one directory,
        # not about the bundle: that one is usually full of files.
        if base_path is not None:
            raise _no_index_error(base_path, outcome)
        raise BundleError(
            "EMPTY_ARCHIVE", params={"junk_dir": _JUNK_DIR, "junk_prefix": _JUNK_PREFIX}
        )
    if not outcome.index_seen:
        raise _no_index_error(base_path, outcome)
    return outcome.count
