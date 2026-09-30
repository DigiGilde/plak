"""Tests for client IP derivation in front of a proxy (`plak.net`).

The rate limiter exercises the same code through the middleware
(`test_ratelimit.py`); here the derivation itself is under test, including the
values a client can write into X-Forwarded-For.

The measurement these are built on: on 2026-09-30 two requests were sent to
the production deployment, one plain and one carrying
`X-Forwarded-For: 203.0.113.99`. The audit log recorded the real address for
the first and the invented one for the second, because the OpenShift router
adds its entry as a second header LINE and the code read only the first.
`test_the_router_line_wins_over_the_clients_line` is that measurement.
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from plak.audit.pseudonymisation import truncate_ip
from plak.net import UNKNOWN, client_ip, client_ip_from_request, rate_limit_key


def _request(peer: str | None, *xff_lines: str) -> Request:
    """A request whose X-Forwarded-For arrives as one line per argument, which
    is how a proxy delivers it: it adds its own line behind the client's."""
    headers = [(b"x-forwarded-for", line.encode()) for line in xff_lines]
    return Request({"type": "http", "headers": headers, "client": (peer, 1234) if peer else None})


# --- No proxy in front -------------------------------------------------------


def test_without_a_proxy_the_socket_decides() -> None:
    request = _request("198.51.100.5", "203.0.113.9")
    assert client_ip(request, False) == "198.51.100.5"


def test_without_a_proxy_the_address_is_vouched_for() -> None:
    assert client_ip(_request("198.51.100.5"), False).vouched


# --- A proxy in front ------------------------------------------------------


def test_the_router_entry_is_the_client() -> None:
    request = _request("10.128.0.5", "203.0.113.9, 198.51.100.5")
    assert client_ip(request, True) == "198.51.100.5"


def test_the_router_line_wins_over_the_clients_line() -> None:
    # The real shape on ZAD: the client's header arrives untouched and the
    # router adds a line of its own behind it. Reading only the first line is
    # what let an invented address into the audit log.
    request = _request("10.128.0.5", "203.0.113.99", "62.131.59.199")
    address = client_ip(request, True)
    assert address == "62.131.59.199"
    assert address.vouched


def test_a_client_on_a_private_address_cannot_forge_either() -> None:
    # What the trusted-ranges walk could not do: a private entry used to be
    # skipped as a proxy hop, and the walk continued into the client's own
    # values. Position does not care what the address looks like.
    request = _request("10.128.0.5", "203.0.113.9, 10.42.0.7", "198.51.100.5")
    assert client_ip(request, True) == "198.51.100.5"


def test_one_line_with_both_entries_reads_the_same() -> None:
    # A proxy may extend the existing line instead of adding one. The entries
    # are in the same order either way, so the position is the same.
    request = _request("10.128.0.5", "203.0.113.9, 198.51.100.5")
    assert client_ip(request, True) == "198.51.100.5"


# --- When there is no proxy writing what the deployment promises -----------------------------------


def test_a_missing_header_falls_back_to_the_peer() -> None:
    address = client_ip(_request("10.128.0.5"), True)
    assert address == "10.128.0.5"
    assert not address.vouched


def test_something_that_is_not_an_address_falls_back_to_the_peer() -> None:
    address = client_ip(_request("10.128.0.5", "203.0.113.9, geen-adres"), True)
    assert address == "10.128.0.5"
    assert not address.vouched


def test_empty_entries_do_not_shift_the_position() -> None:
    request = _request("10.128.0.5", "203.0.113.9, , ", "198.51.100.5")
    assert client_ip(request, True) == "198.51.100.5"


def test_without_a_peer_the_address_is_unknown() -> None:
    assert client_ip(_request(None), False) == UNKNOWN


# --- What the derived address is used for ---------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("198.51.100.5", "198.51.100.5"),
        ("2001:db8::1", "2001:db8::/64"),
        ("::ffff:198.51.100.5", "198.51.100.5"),
        (UNKNOWN, UNKNOWN),
    ],
)
def test_the_rate_limit_key(address: str, expected: str) -> None:
    assert rate_limit_key(address) == expected


def test_the_audit_truncation_of_a_derived_address() -> None:
    request = _request("10.128.0.5", "203.0.113.9", "198.51.100.5")
    assert truncate_ip(client_ip(request, True)) == "198.51.100.0/24"


# --- The settings variant -------------------------------------------------------------


class _App:
    def __init__(self, behind_proxy: bool | None) -> None:
        self.state = type("S", (), {"settings": type("C", (), {"behind_proxy": behind_proxy})})


def _with_settings(peer: str | None, behind_proxy: bool | None, *xff_lines: str) -> Request:
    request = _request(peer, *xff_lines)
    request.scope["app"] = _App(behind_proxy)
    return request


def test_the_settings_variant_reads_the_setting() -> None:
    request = _with_settings("10.128.0.5", True, "203.0.113.9", "198.51.100.5")
    assert client_ip_from_request(request) == "198.51.100.5"


def test_an_unset_setting_counts_as_no_proxy() -> None:
    # Production refuses None (config.py); everywhere else it means the same
    # as false, so a dev run without the setting reads the socket.
    assert client_ip_from_request(_with_settings("10.128.0.5", None, "203.0.113.9")) == "10.128.0.5"


def test_the_settings_variant_without_a_peer_returns_nothing() -> None:
    assert client_ip_from_request(_with_settings(None, True)) is None
