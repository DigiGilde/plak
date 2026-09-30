"""Client IP determination behind trusted proxies.

One derivation point for both rate limiting and audit: walking
X-Forwarded-For from the right, trusted proxy hops are skipped and the first
untrusted address counts as the client. The leftmost value is the client's
own to set and is therefore never usable as an identity.

The derived value carries whether it can be vouched for; see `ClientAddress`.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from starlette.requests import Request

Network = ipaddress.IPv4Network | ipaddress.IPv6Network

UNKNOWN = "unknown"


class ClientAddress(str):
    """The derived address, plus whether we saw it ourselves.

    A plain `str` everywhere it is used (rate limit key, truncation,
    encryption); the extra attribute only travels along to the audit write,
    which is the one caller that has to say afterwards how the address was
    arrived at. That keeps the derivation in one place without every call site
    having to carry a second value.

    `vouched` is false for an address that came out of X-Forwarded-For with at
    least one trusted entry to its right. That entry was skipped because it
    falls inside PLAK_TRUSTED_PROXIES, and with a list as wide as all of
    RFC1918 that is not proof it was written by a proxy of ours: a client on a
    private address can write both values itself. The address we return is then
    a claim, not an observation. Every other outcome is one: the direct peer is
    what the socket says, and the rightmost X-Forwarded-For entry was appended
    by the peer, which we did see connect.
    """

    __slots__ = ("vouched",)

    vouched: bool

    def __new__(cls, value: str, *, vouched: bool) -> ClientAddress:
        address = super().__new__(cls, value)
        address.vouched = vouched
        return address


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


def client_ip(request: Request, trusted_networks: tuple[Network, ...]) -> ClientAddress:
    """Derives the client IP: rightmost-after-trusted.

    X-Forwarded-For is consulted only when the direct peer is a trusted proxy,
    and then from right to left: trusted hops are skipped, the first untrusted
    address is the client. When every entry is trusted (or there is no XFF),
    the peer itself counts.

    An entry that is not a parseable IP address ends the walk and the peer
    counts: the value is the client's own to write, and returning it would
    hand an unparseable "address" to callers that must truncate or encrypt it.

    Skipping is what costs the vouch: an entry only reached over a skipped one
    is as trustworthy as the assumption that the skipped hop is a proxy.
    """
    remote = request.client.host if request.client else UNKNOWN
    if not _is_trusted(remote, trusted_networks):
        return ClientAddress(remote, vouched=True)

    xff = request.headers.get("x-forwarded-for", "")
    skipped = False
    for part in reversed(xff.split(",")):
        candidate = part.strip()
        if not candidate:
            continue
        address = _parse(candidate)
        if address is None:
            return ClientAddress(remote, vouched=True)
        if any(address in network for network in trusted_networks):
            skipped = True
            continue
        return ClientAddress(candidate, vouched=not skipped)
    return ClientAddress(remote, vouched=True)


def rate_limit_key(address: str) -> str:
    """What a per-client limit counts on: an IPv4 address as it is, an IPv6
    address by its /64. A single subscriber is handed a whole /64, so keying on
    the full address would give every one of its 2**64 addresses a budget of
    its own. Anything unparseable is its own key."""
    parsed = _parse(address)
    if parsed is None or parsed.version == 4:
        return address
    if parsed.ipv4_mapped is not None:
        return str(parsed.ipv4_mapped)
    return str(ipaddress.IPv6Network((parsed, 64), strict=False))


def client_ip_from_request(request: Request) -> ClientAddress | None:
    """Convenience variant for audit call sites: reads the trusted proxies from
    `request.app.state.settings` and returns None without a connected peer."""
    if request.client is None:
        return None
    settings = request.app.state.settings
    return client_ip(request, parse_trusted_proxies(settings.trusted_proxies))
