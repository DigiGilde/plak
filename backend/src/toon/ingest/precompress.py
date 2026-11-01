"""Compressing a version's text files once, at ingest.

A file whose type compresses well is kept as gzip in place of itself, with a
brotli copy beside it where that is smaller still (ContentStore keeps both
under `_encoded/` in the version). Serving sends the copy a request accepts
and unpacks the gzip on the way out for a client that accepts neither: every
browser takes gzip, so that is a tool such as curl, and costs it a few
milliseconds per megabyte. What the volume holds, and what the quota counts,
is then about what goes over the wire.

A file stays as it is when it is small, of a type that is compressed already,
or when gzip does not make it smaller. Brotli's best quality runs at about
1 MB/s, so it is spent on the first BROTLI_BEST_BUDGET bytes of a version,
which holds a typical site whole, and the rest gets a quality that runs at
gzip's speed: compressing a deploy of the maximum size then takes seconds, not
minutes, and the request that carries it stays within a proxy's timeout.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Protocol

import brotli

from toon.serving import mime

BROTLI = "br"
GZIP = "gzip"

# Content-Encoding tokens in the server's order of preference, best first.
ENCODINGS = (BROTLI, GZIP)

# Below this a response fits in a packet or two either way.
MIN_SIZE = 1024

BROTLI_BEST_BUDGET = 4 * 1024 * 1024
BROTLI_BEST = 11
BROTLI_FAST = 9
GZIP_LEVEL = 6

_CHUNK = 512 * 1024

# Besides text/*, +json and +xml: types that are text in another wrapper or
# uncompressed binary formats. woff/woff2 and the image formats are compressed
# already and would only grow.
_COMPRESSIBLE = frozenset(
    {
        "application/json",
        "application/xml",
        "application/wasm",
        "image/svg+xml",
        "image/vnd.microsoft.icon",
        "font/ttf",
        "font/otf",
    }
)


def compressible(rel_path: str) -> bool:
    content_type = mime.determine(rel_path).split(";", 1)[0]
    return (
        content_type.startswith("text/")
        or content_type.endswith(("+json", "+xml"))
        or content_type in _COMPRESSIBLE
    )


class Writable(Protocol):
    def __enter__(self) -> Writable: ...

    def __exit__(self, *exc_info: object) -> None: ...

    def write(self, data: bytes, /) -> int: ...


class Version(Protocol):
    """A version being written (the store's VersionWriter, behind the ingest's
    room guard)."""

    def files(self) -> Iterable[tuple[str, Path]]: ...

    def open_encoded(self, rel_path: str, encoding: str) -> Writable: ...

    def discard_encoded(self, rel_path: str, encoding: str) -> None: ...

    def drop_plain(self, rel_path: str) -> None: ...


Compressor = tuple[Callable[[bytes], bytes], Callable[[], bytes]]


def _gzip() -> Compressor:
    # wbits 31: the gzip container, with a zero mtime in its header, so the
    # same file always gives the same bytes.
    compressor = zlib.compressobj(GZIP_LEVEL, zlib.DEFLATED, 31)
    return compressor.compress, compressor.flush


def _brotli(quality: int) -> Compressor:
    compressor = brotli.Compressor(quality=quality)
    return compressor.process, compressor.finish


def _write(version: Version, rel_path: str, path: Path, encoding: str, compressor: Compressor) -> int:
    compress, finish = compressor
    written = 0
    with path.open("rb") as source, version.open_encoded(rel_path, encoding) as target:
        while chunk := source.read(_CHUNK):
            written += target.write(compress(chunk))
        written += target.write(finish())
    return written


def encode_version(version: Version) -> None:
    budget = BROTLI_BEST_BUDGET
    for rel_path, path in version.files():
        size = path.stat().st_size
        if size < MIN_SIZE or not compressible(rel_path):
            continue
        gzipped = _write(version, rel_path, path, GZIP, _gzip())
        if gzipped >= size:
            version.discard_encoded(rel_path, GZIP)
            continue
        if size <= budget:
            quality = BROTLI_BEST
            budget -= size
        else:
            quality = BROTLI_FAST
        if _write(version, rel_path, path, BROTLI, _brotli(quality)) >= gzipped:
            version.discard_encoded(rel_path, BROTLI)
        version.drop_plain(rel_path)


def gunzip(path: Path) -> Iterator[bytes]:
    """The file a gzip copy holds, in pieces of at most _CHUNK bytes.

    Raises once the copy ends before its stream does: a reader that has
    nothing else to go by (a response without Content-Length, a file put back
    at its own path) would otherwise take a short file for the whole one."""
    # max_length bounds what one step holds in memory: a chunk of deflate can
    # unpack to a thousand times its size.
    decompressor = zlib.decompressobj(31)
    with path.open("rb") as source:
        while chunk := source.read(_CHUNK):
            data = decompressor.decompress(chunk, _CHUNK)
            while data:
                yield data
                data = decompressor.decompress(decompressor.unconsumed_tail, _CHUNK)
    if not decompressor.eof:
        raise OSError(f"gzip copy ends before its stream does: {path}")
