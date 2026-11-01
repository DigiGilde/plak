"""Path validation and file resolution for serving.

Validation happens serving-side, independent of what ingest already refused:
null bytes, backslashes and `..` segments yield None (neutral 404).
Containment within the version root is then enforced once more by
ContentStore.file_path.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from pathlib import Path

from toon.constants import INDEX_FILE
from toon.ingest import precompress
from toon.ingest.store import ContentStore

NOT_FOUND_FILE = "404.html"

_QVALUE = re.compile(r"0(\.[0-9]{0,3})?|1(\.0{0,3})?")


def normalise_rest(rest: str) -> str | None:
    """Validates and normalises the path behind /{group}/{site}/.

    Returns the normalised relative path (empty string for the site root; a
    trailing slash marks a directory request) or None when the path is
    invalid. The ASGI server has already URL-decoded the path once;
    deliberately no second decode here, otherwise %252e%252e would still
    collapse into '..'.
    """
    if "\x00" in rest or "\\" in rest:
        return None
    parts = rest.split("/")
    if ".." in parts:
        return None
    clean = [part for part in parts if part not in ("", ".")]
    rel = "/".join(clean)
    if rel and (rest.endswith("/") or parts[-1] in ("", ".")):
        return rel + "/"
    return rel


def display_path(rel: str) -> str:
    """The path of the representation a normalised path yields: directory
    requests are rewritten to their index.html."""
    if rel == "" or rel.endswith("/"):
        return rel + INDEX_FILE
    return rel


class ResolutionKind(enum.Enum):
    FILE = "file"
    DIRECTORY_REDIRECT = "directory_redirect"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class Stored:
    """One file of a version as the volume holds it: the file itself, or
    compressed only, with gzip always among `encoded` (ingest/precompress.py)."""

    plain: Path | None = None
    encoded: dict[str, Path] = field(default_factory=dict)


def locate(store: ContentStore, storage_ref: str, rel_path: str) -> Stored | None:
    path = store.file_path(storage_ref, rel_path)
    if path is not None:
        return Stored(plain=path)
    # What the version holds decides, not precompress.compressible: the copy is
    # the only one, and the type table may change after it was written.
    encoded = {
        encoding: path
        for encoding in precompress.ENCODINGS
        if (path := store.encoded_path(storage_ref, rel_path, encoding)) is not None
    }
    if precompress.GZIP not in encoded:
        return None
    return Stored(encoded=encoded)


@dataclass(frozen=True)
class Resolution:
    kind: ResolutionKind
    rel_path: str | None = None
    stored: Stored | None = None


def resolve(store: ContentStore, storage_ref: str, rel: str) -> Resolution:
    """Finds the file to serve for a normalised relative path.

    Directory requests (trailing slash or site root) get the index rewrite; a
    path without a slash that turns out to be a directory holding an
    index.html yields DIRECTORY_REDIRECT so the router can answer 301, only
    after an allow decision.
    """
    if rel == "" or rel.endswith("/"):
        target = display_path(rel)
        stored = locate(store, storage_ref, target)
        if stored is not None:
            return Resolution(ResolutionKind.FILE, target, stored)
        return Resolution(ResolutionKind.NOT_FOUND)
    stored = locate(store, storage_ref, rel)
    if stored is not None:
        return Resolution(ResolutionKind.FILE, rel, stored)
    if locate(store, storage_ref, f"{rel}/{INDEX_FILE}") is not None:
        return Resolution(ResolutionKind.DIRECTORY_REDIRECT)
    return Resolution(ResolutionKind.NOT_FOUND)


def find_404_page(store: ContentStore, storage_ref: str) -> Stored | None:
    """The version's root 404.html; to be shown to authorised visitors only."""
    return locate(store, storage_ref, NOT_FOUND_FILE)


@dataclass(frozen=True)
class Representation:
    """What answers for one file. `encoding` is the Content-Encoding sent, or
    None for the file as published; `gunzip` says that the file as published
    has to be unpacked from the gzip at `file_path` on the way out. `varies`
    says whether the choice depended on Accept-Encoding, which is so for every
    file kept compressed."""

    file_path: Path
    encoding: str | None = None
    varies: bool = False
    gunzip: bool = False


def _weights(accept_encoding: str) -> dict[str, float]:
    weights: dict[str, float] = {}
    for item in accept_encoding.split(","):
        coding, *params = (part.strip() for part in item.split(";"))
        coding = coding.lower()
        if not coding:
            continue
        weight = 1.0
        for param in params:
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                value = value.strip()
                # Anything but a qvalue (RFC 9110 §12.4.2) counts as a refusal.
                weight = float(value) if _QVALUE.fullmatch(value) else 0.0
        # RFC 9110 §8.4.1.3: x-gzip is gzip.
        weights["gzip" if coding == "x-gzip" else coding] = weight
    return weights


def choose_encoding(accept_encoding: str | None, available: tuple[str, ...]) -> str | None:
    """The coding of `available` (in the server's order of preference) the
    request weighs highest, or None for the file itself.

    No header means the file itself: RFC 9110 lets a server pick any coding
    then, and a client that sent nothing is one we know least about."""
    if not accept_encoding:
        return None
    weights = _weights(accept_encoding)
    wildcard = weights.get("*", 0.0)
    best: str | None = None
    best_weight = 0.0
    for coding in available:
        weight = weights.get(coding, wildcard)
        if weight > best_weight:
            best, best_weight = coding, weight
    return best


def representation(stored: Stored, accept_encoding: str | None) -> Representation:
    if stored.plain is not None:
        return Representation(stored.plain)
    encoding = choose_encoding(accept_encoding, tuple(stored.encoded))
    if encoding is None:
        return Representation(stored.encoded[precompress.GZIP], varies=True, gunzip=True)
    return Representation(stored.encoded[encoding], encoding, varies=True)
