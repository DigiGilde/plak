"""Path validation and file resolution for serving.

Validation happens serving-side, independent of what ingest already refused:
null bytes, backslashes and `..` segments yield None (neutral 404).
Containment within the version root is then enforced once more by
ContentStore.file_path.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

from plak.constants import INDEX_FILE
from plak.ingest.store import ContentStore

NOT_FOUND_FILE = "404.html"


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
class Resolution:
    kind: ResolutionKind
    rel_path: str | None = None
    file_path: Path | None = None


def resolve(store: ContentStore, storage_ref: str, rel: str) -> Resolution:
    """Finds the file to serve for a normalised relative path.

    Directory requests (trailing slash or site root) get the index rewrite; a
    path without a slash that turns out to be a directory holding an
    index.html yields DIRECTORY_REDIRECT so the router can answer 301, only
    after an allow decision.
    """
    if rel == "" or rel.endswith("/"):
        target = display_path(rel)
        path = store.file_path(storage_ref, target)
        if path is not None:
            return Resolution(ResolutionKind.FILE, target, path)
        return Resolution(ResolutionKind.NOT_FOUND)
    path = store.file_path(storage_ref, rel)
    if path is not None:
        return Resolution(ResolutionKind.FILE, rel, path)
    if store.file_path(storage_ref, f"{rel}/{INDEX_FILE}") is not None:
        return Resolution(ResolutionKind.DIRECTORY_REDIRECT)
    return Resolution(ResolutionKind.NOT_FOUND)


def find_404_page(store: ContentStore, storage_ref: str) -> Path | None:
    """The version's root 404.html; to be shown to authorised visitors only."""
    return store.file_path(storage_ref, NOT_FOUND_FILE)
