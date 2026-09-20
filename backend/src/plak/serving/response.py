"""Response building for serving: headers, ETag/304 and FileResponse.

The app streams content itself through Starlette's FileResponse (sendfile
where the server offers it, Range requests); it answers conditional requests
without touching the store.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from starlette.responses import FileResponse, Response

from plak.constants import AccessPolicy
from plak.serving import mime

# Both content policies come out of this one table, so the strict one and the
# one with external sources cannot drift apart: the second is the first plus
# the hosts in EXTERNAL_SOURCES, directive by directive, and nothing else.
_CONTENT_DIRECTIVES: dict[str, tuple[str, ...]] = {
    "default-src": ("'self'",),
    "script-src": ("'self'", "'unsafe-inline'"),
    "style-src": ("'self'", "'unsafe-inline'"),
    "img-src": ("'self'", "data:", "blob:"),
    "font-src": ("'self'", "data:"),
    "connect-src": ("'self'",),
    "media-src": ("'self'",),
    "frame-ancestors": ("'none'",),
    "base-uri": ("'self'",),
    "form-action": ("'self'",),
    "object-src": ("'none'",),
}

_CDN_HOSTS = ("https://cdnjs.cloudflare.com", "https://cdn.jsdelivr.net", "https://unpkg.com")

# Tailwind's own CDN is a script that writes its styles into the page itself,
# so it needs script-src only; the inline <style> it injects is already covered
# by 'unsafe-inline' in style-src.
_TAILWIND_CDN = "https://cdn.tailwindcss.com"

# What "externe bronnen toestaan" adds, per directive. connect-src stays
# 'self': a page may load a library from these hosts, never send data to them.
EXTERNAL_SOURCES: dict[str, tuple[str, ...]] = {
    "script-src": (*_CDN_HOSTS, _TAILWIND_CDN),
    "style-src": (*_CDN_HOSTS, "https://fonts.googleapis.com"),
    "font-src": ("https://fonts.gstatic.com",),
}


def _serialise(directives: dict[str, tuple[str, ...]]) -> str:
    return "; ".join(f"{name} {' '.join(values)}" for name, values in directives.items())


def _with_external_sources() -> dict[str, tuple[str, ...]]:
    return {
        name: (*values, *EXTERNAL_SOURCES.get(name, ()))
        for name, values in _CONTENT_DIRECTIVES.items()
    }


CONTENT_CSP = _serialise(_CONTENT_DIRECTIVES)
CONTENT_CSP_EXTERNAL = _serialise(_with_external_sources())


def content_csp(*, external_sources: bool) -> str:
    return CONTENT_CSP_EXTERNAL if external_sources else CONTENT_CSP

NOINDEX = "noindex, nofollow"

NEUTRAL_404_BODY = b"Niet gevonden\n"


def neutral_404_response() -> Response:
    """The single construction point for every refusal and every non-existence:
    that is what keeps all neutral 404s byte-identical, headers included
    (anti-enumeration).

    It keeps the strict CONTENT_CSP whatever a site allows: a policy that
    followed the site's external_sources would say which site the refusal
    belonged to, which is what this response exists to hide."""
    return Response(
        content=NEUTRAL_404_BODY,
        status_code=404,
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": CONTENT_CSP,
            "Referrer-Policy": "no-referrer",
        },
    )


def etag_for(version_id: uuid.UUID) -> str:
    return f'"{version_id}"'


def if_none_match_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    for candidate in header.split(","):
        value = candidate.strip()
        if value.startswith("W/"):
            value = value[2:]
        if value == etag:
            return True
    return False


def cache_control(content_type: str, access: AccessPolicy, *, version_view: bool) -> str:
    is_html = content_type.startswith("text/html")
    base = "no-cache, must-revalidate" if is_html else "max-age=31536000, immutable"
    if version_view or not access.is_public:
        return f"private, {base}"
    return base


def _base_headers(
    content_type: str,
    version_id: uuid.UUID,
    access: AccessPolicy,
    *,
    version_view: bool,
    noindex: bool,
    external_sources: bool,
) -> dict[str, str]:
    headers = {
        "Content-Type": content_type,
        "Cache-Control": cache_control(content_type, access, version_view=version_view),
        "ETag": etag_for(version_id),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": content_csp(external_sources=external_sources),
        # no-referrer wherever a secret link can carry the visitor in,
        # because the URL itself is then the credential.
        "Referrer-Policy": "no-referrer" if access.keys else "strict-origin-when-cross-origin",
    }
    if noindex:
        headers["X-Robots-Tag"] = NOINDEX
    return headers


def make_304(
    rel_path: str,
    version_id: uuid.UUID,
    access: AccessPolicy,
    *,
    version_view: bool,
    noindex: bool,
) -> Response:
    """304 on If-None-Match, by the app itself, without touching the store: the
    headers follow purely from path and decision."""
    content_type = mime.determine(rel_path)
    headers = {
        "ETag": etag_for(version_id),
        "Cache-Control": cache_control(content_type, access, version_view=version_view),
        "X-Content-Type-Options": "nosniff",
    }
    if noindex:
        headers["X-Robots-Tag"] = NOINDEX
    return Response(status_code=304, headers=headers)


def make_content_response(
    *,
    rel_path: str,
    file_path: Path,
    version_id: uuid.UUID,
    access: AccessPolicy,
    version_view: bool,
    noindex: bool,
    external_sources: bool = False,
    status_code: int = 200,
) -> Response:
    content_type = mime.determine(rel_path)
    headers = _base_headers(
        content_type,
        version_id,
        access,
        version_view=version_view,
        noindex=noindex,
        external_sources=external_sources,
    )
    return FileResponse(file_path, status_code=status_code, headers=headers, media_type=content_type)
