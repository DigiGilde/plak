"""Client IP determination behind one proxy.

One derivation point for both rate limiting and audit. PLAK_BEHIND_PROXY says
whether a proxy stands in front of the pod. With one, the last X-Forwarded-For
entry is the one that proxy wrote about the peer it accepted, and everything
to the left of it is the client's to invent. Without one, the socket decides
and the header is not read at all -- which is what the dev stack and the e2e
suite run on, and what keeps a client from naming itself there.

Position rather than recognition, because the alternative does not work here.
Skipping entries that fall inside a list of trusted ranges needs that list to
name the proxies; on ZAD the router pods sit on the cluster pod network, whose
range the platform cannot hand out, so the list had to be all of RFC1918 --
wide enough that a client on a private address could write an entry that the
walk would skip.

One proxy, not a number of them: ZAD puts exactly the HAProxy router in front
of the pod. A second layer (a CDN, an nginx in the pod) would move the entry
and this setting would have to grow a count with it.

Two X-Forwarded-For header LINES, not one value: the OpenShift router runs
`option forwardfor` under its Append policy, which adds a line of its own
after the client's rather than extending it. `headers.get` returns the first
line, which is the client's, so every entry has to be gathered with
`getlist` first.

The derived value carries whether it can be vouched for; see `ClientAddress`.
"""

from __future__ import annotations

import ipaddress

from starlette.requests import Request

UNKNOWN = "unknown"


class ClientAddress(str):
    """The derived address, plus whether we saw it ourselves.

    A plain `str` everywhere it is used (rate limit key, truncation,
    encryption); the extra attribute only travels along to the audit write,
    which is the one caller that has to say afterwards how the address was
    arrived at. That keeps the derivation in one place without every call site
    having to carry a second value.

    `vouched` is false where the proxy the deployment promises did not write
    anything usable: no X-Forwarded-For arrived, or its last entry is not an
    address. What stands in front of the pod is then not what the deployment
    says, and the peer we fall back to is a proxy's address rather than a
    client's. Every other outcome is an observation: the peer is what the
    socket says, and the last entry was written by the proxy about the
    connection it accepted.
    """

    __slots__ = ("vouched",)

    vouched: bool

    def __new__(cls, value: str, *, vouched: bool) -> ClientAddress:
        address = super().__new__(cls, value)
        address.vouched = vouched
        return address


def _parse(ip: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(ip)
    except ValueError:
        return None


def forwarded_entries(request: Request) -> list[str]:
    """Every X-Forwarded-For entry, in order, across all header lines."""
    entries = []
    for line in request.headers.getlist("x-forwarded-for"):
        for part in line.split(","):
            candidate = part.strip()
            if candidate:
                entries.append(candidate)
    return entries


def client_ip(request: Request, behind_proxy: bool) -> ClientAddress:
    """Derives the client IP: the last X-Forwarded-For entry, or the socket.

    A client can put anything in X-Forwarded-For, and does not have to send one
    at all. Neither moves the entry we read: the proxy appends its own to
    whatever arrived, so the last one is always the proxy's and everything
    before it is the client's to invent.
    """
    remote = request.client.host if request.client else UNKNOWN
    if not behind_proxy:
        return ClientAddress(remote, vouched=True)

    entries = forwarded_entries(request)
    if not entries:
        # A proxy that writes no X-Forwarded-For is not the proxy this
        # deployment says it has, so the peer is all we have seen ourselves.
        return ClientAddress(remote, vouched=False)

    candidate = entries[-1]
    if _parse(candidate) is None:
        # A proxy writes an address. Anything else means the entry came from
        # somewhere else, and handing it on would give callers that must
        # truncate or encrypt it something that is not an address.
        return ClientAddress(remote, vouched=False)
    return ClientAddress(candidate, vouched=True)


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
    """Convenience variant for audit call sites: reads the setting from
    `request.app.state.settings` and returns None without a connected peer."""
    if request.client is None:
        return None
    settings = request.app.state.settings
    return client_ip(request, bool(settings.behind_proxy))
