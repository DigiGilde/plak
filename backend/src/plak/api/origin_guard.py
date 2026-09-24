"""Origin guarding for the beheer API.

The beheer API only accepts requests from its own beheer origin: an Origin
header that is present must be exactly the beheer origin (PLAK_BASE_URL); if
Origin is absent, Sec-Fetch-Site may at most be `same-origin` or `none`.
`same-site` is refused on purpose: the content origin is a sibling host of
the beheer origin and counts as same-site to browsers. CORS headers are never
set, so another origin can never read a response either. Requests carrying
neither header (curl, CI) pass this gate: they carry no ambient credentials
sent along by the browser, and mutations stay covered by the CSRF double
submit.

Without a configured base_url (dev), an Origin equal to the origin of the
request itself or a localhost origin is allowed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlsplit

# Request must be importable at runtime: FastAPI resolves the annotation of
# the dependency parameter during registration.
from fastapi import Request

from plak.api.errors import ApiError

if TYPE_CHECKING:
    from plak.config import Settings

REASON_OTHER_ORIGIN = "ORIGIN_REFUSED"

_DEFAULT_PORTS = {"http": 80, "https": 443}
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ALLOWED_FETCH_SITES = frozenset({"same-origin", "none"})


def normalise_origin(value: str | None) -> str | None:
    """Canonical form `scheme://host[:port]` (lowercase, default port left
    out); None for anything that is not a usable origin (`null` included)."""
    if not value:
        return None
    try:
        parts = urlsplit(value.strip())
        scheme = (parts.scheme or "").lower()
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not host:
        return None
    # urlsplit.hostname strips the [] brackets off an IPv6 address; put them
    # back so the canonical form is itself a valid origin again.
    host_part = f"[{host}]" if ":" in host else host
    if port is None or port == _DEFAULT_PORTS[scheme]:
        return f"{scheme}://{host_part}"
    return f"{scheme}://{host_part}:{port}"


def admin_origin(settings: Settings) -> str | None:
    return normalise_origin(settings.base_url)


def _is_local_origin(origin: str) -> bool:
    try:
        host = urlsplit(origin).hostname
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


def _request_origin(request: Request) -> str | None:
    return normalise_origin(f"{request.url.scheme}://{request.url.netloc}")


def _refuse() -> ApiError:
    return ApiError(403, REASON_OTHER_ORIGIN)


def admin_origin_ok(request: Request) -> bool:
    """Whether this request comes from the beheer origin itself."""
    settings = request.app.state.settings
    target = admin_origin(settings)

    origin_header = request.headers.get("Origin")
    if origin_header is not None:
        origin = normalise_origin(origin_header)
        if origin is None:
            return False
        if target is not None:
            return origin == target
        return origin == _request_origin(request) or _is_local_origin(origin)

    fetch_site = request.headers.get("Sec-Fetch-Site")
    return fetch_site is None or fetch_site.strip().lower() in _ALLOWED_FETCH_SITES


async def require_admin_origin(request: Request) -> None:
    """FastAPI dependency for every beheer API route."""
    if not admin_origin_ok(request):
        raise _refuse()


__all__ = [
    "REASON_OTHER_ORIGIN",
    "admin_origin",
    "admin_origin_ok",
    "normalise_origin",
    "require_admin_origin",
]
