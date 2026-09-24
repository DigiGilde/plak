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

# Every content policy comes out of this one table, so the variants cannot
# drift apart: each is this base plus the additions of the site switches that
# are on, directive by directive, and nothing else.
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


# What the sandbox adds. All sites share one hostname, so without this a page
# runs on the same origin as every other site's pages. Leaving out
# allow-same-origin gives the document an opaque origin: it can read no other
# document on this host, its own requests carry no cookies and it can write
# none. Its stylesheets, scripts, images and fonts are ordinary subresource
# loads and keep working; browser storage does not.
SANDBOX: dict[str, tuple[str, ...]] = {
    "sandbox": ("allow-scripts", "allow-forms", "allow-popups"),
}


def _serialise(directives: dict[str, tuple[str, ...]]) -> str:
    return "; ".join(f"{name} {' '.join(values)}" for name, values in directives.items())


def _with(*additions: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    directives = dict(_CONTENT_DIRECTIVES)
    for addition in additions:
        for name, values in addition.items():
            directives[name] = (*directives.get(name, ()), *values)
    return directives


CONTENT_CSP = _serialise(_CONTENT_DIRECTIVES)
CONTENT_CSP_EXTERNAL = _serialise(_with(EXTERNAL_SOURCES))
CONTENT_CSP_SANDBOX = _serialise(_with(SANDBOX))
CONTENT_CSP_EXTERNAL_SANDBOX = _serialise(_with(EXTERNAL_SOURCES, SANDBOX))

_CONTENT_POLICIES: dict[tuple[bool, bool], str] = {
    (False, False): CONTENT_CSP,
    (True, False): CONTENT_CSP_EXTERNAL,
    (False, True): CONTENT_CSP_SANDBOX,
    (True, True): CONTENT_CSP_EXTERNAL_SANDBOX,
}


def content_csp(*, external_sources: bool, sandbox: bool) -> str:
    return _CONTENT_POLICIES[(external_sources, sandbox)]

NOINDEX = "noindex, nofollow"

NEUTRAL_404_BODY = b"Niet gevonden\n"


def neutral_404_response() -> Response:
    """The single construction point for every refusal and every non-existence:
    that is what keeps all neutral 404s byte-identical, headers included
    (anti-enumeration).

    It keeps the strict CONTENT_CSP whatever a site allows: a policy that
    followed the site's own switches would say which site the refusal
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
    private = version_view or not access.is_public
    if content_type.startswith("text/html"):
        base = "no-cache, must-revalidate"
    elif private:
        # An asset URL is not content-addressed: `/{group}/{site}/assets/x.css`
        # survives a redeploy, so `immutable` keeps a new version out of sight
        # even on a reload. Public content keeps it for the reach a shared
        # cache gives it; private content has no shared cache to gain from.
        base = "max-age=31536000"
    else:
        base = "max-age=31536000, immutable"
    return f"private, {base}" if private else base


def _base_headers(
    content_type: str,
    version_id: uuid.UUID,
    access: AccessPolicy,
    *,
    version_view: bool,
    noindex: bool,
    external_sources: bool,
    sandbox: bool,
) -> dict[str, str]:
    headers = {
        "Content-Type": content_type,
        "Cache-Control": cache_control(content_type, access, version_view=version_view),
        "ETag": etag_for(version_id),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": content_csp(
            external_sources=external_sources, sandbox=sandbox
        ),
        # same-origin wherever a secret link can carry the visitor in: nothing
        # goes to another origin, and the page URL never holds the verifier
        # (a `?key=` is redeemed with a 302 that strips it). Not no-referrer,
        # because the router needs a Referer of its own to tell a site's own
        # subresources from another site's (see _foreign_subresource).
        "Referrer-Policy": "same-origin" if access.keys else "strict-origin-when-cross-origin",
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
    sandbox: bool = False,
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
        sandbox=sandbox,
    )
    return FileResponse(file_path, status_code=status_code, headers=headers, media_type=content_type)
