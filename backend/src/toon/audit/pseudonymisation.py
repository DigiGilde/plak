"""Pseudonymisation for the audit log: actor identifiers are never
stored in readable form, IP addresses are truncated before they are stored.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress


def pseudonymise(pepper: str, identifier: str) -> str:
    """HMAC-SHA256(pepper, identifier) as a hex digest.

    Stable per pepper: the same identifier with the same pepper always
    yields the same pseudonym, so repeated behaviour by one actor stays
    recognisable without keeping the identifier itself.
    """
    return hmac.new(pepper.encode("utf-8"), identifier.encode("utf-8"), hashlib.sha256).hexdigest()


def truncate_ip(ip: str) -> str:
    """Truncate an IP address to its network: IPv4 /24, IPv6 /48.

    Returns the network notation (e.g. "203.0.113.0/24") so the
    granularity of the truncation stays visible and auditable. An
    IPv4-mapped IPv6 address (::ffff:a.b.c.d, what a dual-stack socket
    reports for an IPv4 peer) is an IPv4 address: as IPv6 its /48 would be
    ::/48 and keep nothing.
    """
    address = ipaddress.ip_address(ip)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    prefix_length = 24 if isinstance(address, ipaddress.IPv4Address) else 48
    network = ipaddress.ip_network(f"{address}/{prefix_length}", strict=False)
    return str(network)
