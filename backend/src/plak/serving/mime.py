"""MIME table for serving.

Python's `mimetypes` module is the base, with explicit additions pinned down
independently of platform and system (mimetypes reads /etc/mime.types among
others and is therefore not identical everywhere). Unknown becomes
application/octet-stream; the caller always sets X-Content-Type-Options:
nosniff.
"""

from __future__ import annotations

import mimetypes
import posixpath

DEFAULT_MIME = "application/octet-stream"

_EXPLICIT = {
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".map": "application/json",
    ".json": "application/json",
    ".wasm": "application/wasm",
    ".webmanifest": "application/manifest+json",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".xml": "application/xml",
    ".pdf": "application/pdf",
    ".ico": "image/vnd.microsoft.icon",
}


def determine(rel_path: str) -> str:
    """Determines the Content-Type for a relative file path."""
    _, extension = posixpath.splitext(rel_path)
    mediatype = _EXPLICIT.get(extension.lower())
    if mediatype is None:
        mediatype, _ = mimetypes.guess_type(rel_path, strict=False)
    if mediatype is None:
        return DEFAULT_MIME
    if mediatype == "text/html":
        return "text/html; charset=utf-8"
    return mediatype
