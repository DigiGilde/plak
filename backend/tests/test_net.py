"""Tests for client IP derivation behind trusted proxies (`plak.net`).

The rate limiter exercises the same code through the middleware
(`test_ratelimit.py`); here the derivation itself is under test, including the
values a client can write into X-Forwarded-For.
"""

from __future__ import annotations

from starlette.requests import Request

from plak.audit.pseudonymisation import truncate_ip
from plak.net import UNKNOWN, client_ip, parse_trusted_proxies

# What production carries today: every RFC1918 range counts as a proxy.
_ALL_PRIVATE = parse_trusted_proxies("10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
# What the setting should be: only the network the routers themselves sit in.
_ROUTERS_ONLY = parse_trusted_proxies("10.128.0.0/16")


def _request(peer: str | None, xff: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff is not None else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234) if peer else None})


def test_untrusted_peer_ignores_the_header() -> None:
    request = _request("198.51.100.5", "203.0.113.9")
    assert client_ip(request, _ALL_PRIVATE) == "198.51.100.5"


def test_public_client_behind_a_trusted_proxy() -> None:
    # The router appends the address it sees, so the client's own value sits
    # to the left of it and never wins.
    request = _request("10.128.0.5", "203.0.113.9, 198.51.100.5")
    assert client_ip(request, _ALL_PRIVATE) == "198.51.100.5"


def test_private_client_cannot_forge_when_only_the_routers_are_trusted() -> None:
    request = _request("10.128.0.5", "203.0.113.9, 10.42.0.7")
    assert client_ip(request, _ROUTERS_ONLY) == "10.42.0.7"


def test_unparseable_entry_falls_back_to_the_peer() -> None:
    request = _request("10.128.0.5", "not-an-ip, 10.42.0.7")
    assert client_ip(request, _ALL_PRIVATE) == "10.128.0.5"


def test_derived_ip_stays_storable_whatever_the_client_writes() -> None:
    # A value that truncate_ip cannot parse would raise inside the audit write
    # and take the whole row with it, so no client-written value may survive
    # the derivation unvalidated.
    for forged in ("not-an-ip", "203.0.113.9; DROP", "", "999.999.999.999", "10.0.0.1/8"):
        derived = client_ip(_request("10.128.0.5", f"{forged}, 10.42.0.7"), _ALL_PRIVATE)
        assert truncate_ip(derived)


def test_empty_entries_are_skipped() -> None:
    request = _request("10.128.0.5", " , 198.51.100.5")
    assert client_ip(request, _ALL_PRIVATE) == "198.51.100.5"


def test_only_trusted_hops_falls_back_to_the_peer() -> None:
    request = _request("10.128.0.5", "10.0.0.2, 10.0.0.3")
    assert client_ip(request, _ALL_PRIVATE) == "10.128.0.5"


def test_without_a_peer_the_address_is_unknown() -> None:
    assert client_ip(_request(None, "203.0.113.9"), _ALL_PRIVATE) == UNKNOWN
