"""Client IP determination behind trusted proxies.

One derivation point for both rate limiting and audit: walking
X-Forwarded-For from the right, trusted proxy hops are skipped and the first
untrusted address counts as the client. The leftmost value is the client's
own to set and is therefore never usable as an identity.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from starlette.requests import Request

Network = ipaddress.IPv4Network | ipaddress.IPv6Network

UNKNOWN = "unknown"


@lru_cache(maxsize=16)
def parse_trusted_proxies(value: str) -> tuple[Network, ...]:
    """Parses a comma-separated list of CIDRs (PLAK_TRUSTED_PROXIES)."""
    networks = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        networks.append(ipaddress.ip_network(part, strict=False))
    return tuple(networks)


def _parse(ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(ip)
    except ValueError:
        return None


def _is_trusted(ip: str, networks: tuple[Network, ...]) -> bool:
    address = _parse(ip)
    if address is None:
        return False
    return any(address in network for network in networks)


def client_ip(request: Request, trusted_networks: tuple[Network, ...]) -> str:
    """Derives the client IP: rightmost-after-trusted.

    X-Forwarded-For is consulted only when the direct peer is a trusted proxy,
    and then from right to left: trusted hops are skipped, the first untrusted
    address is the client. When every entry is trusted (or there is no XFF),
    the peer itself counts.

    An entry that is not a parseable IP address ends the walk and the peer
    counts: the value is the client's own to write, and returning it would
    hand an unparseable "address" to callers that must truncate or encrypt it.
    """
    remote = request.client.host if request.client else UNKNOWN
    if not _is_trusted(remote, trusted_networks):
        return remote

    xff = request.headers.get("x-forwarded-for", "")
    for part in reversed(xff.split(",")):
        candidate = part.strip()
        if not candidate:
            continue
        address = _parse(candidate)
        if address is None:
            return remote
        if any(address in network for network in trusted_networks):
            continue
        return candidate
    return remote


def client_ip_from_request(request: Request) -> str | None:
    """Convenience variant for audit call sites: reads the trusted proxies from
    `request.app.state.settings` and returns None without a connected peer."""
    if request.client is None:
        return None
    settings = request.app.state.settings
    return client_ip(request, parse_trusted_proxies(settings.trusted_proxies))
