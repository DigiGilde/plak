import re
from pathlib import Path

from plak.constants import (
    RESERVED_SEGMENTS,
    RESERVED_SLUGS,
    SLUG_RE,
    SPA_ROOT_PAGES,
    STANDARD_LOCATIONS,
    AccessBase,
    AccessPolicy,
)


def test_slug_re_accepts_valid_slug():
    assert SLUG_RE.match("nldd")
    assert SLUG_RE.match("moza-publiceer")
    assert SLUG_RE.match("a")
    assert SLUG_RE.match("a" * 63)


def test_slug_re_refuses_underscore_prefix():
    assert SLUG_RE.match("_x") is None


def test_slug_re_refuses_bare_hyphen():
    assert SLUG_RE.match("-") is None


def test_slug_re_refuses_uppercase():
    assert SLUG_RE.match("Beheer") is None


def test_slug_re_refuses_64_characters():
    assert SLUG_RE.match("a" * 64) is None


def test_reserved_slugs_holds_the_web_locations_and_the_root_spa_pages():
    # `admin` and `beheer` are deliberately not in here any more: the SPA
    # moved to the root of the admin host, so on the content host those two
    # are ordinary content and may be somebody's group.
    assert set(RESERVED_SLUGS) == {"robots.txt", "favicon.ico", ".well-known", "cli-koppelen"}


def test_reserved_slugs_mirrors_the_standard_locations_and_root_spa_pages():
    # A location the web pins down has to keep answering for itself, and a
    # root-level SPA page shadows /:group: neither can be a group.
    expected = {location.lstrip("/") for location in STANDARD_LOCATIONS} | SPA_ROOT_PAGES
    assert set(RESERVED_SLUGS) == expected


def test_every_root_level_spa_route_is_reserved():
    """Every route in the Vue router that sits at the root (not under /-/ and
    not a parameter) would shadow a group of that name."""
    router = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "router.ts").read_text()
    root_pages = set(re.findall(r"path: '/([a-z0-9][a-z0-9-]*)'", router))
    assert root_pages == set(SPA_ROOT_PAGES)
    assert root_pages <= RESERVED_SLUGS
    # Pages under /-/ need no reservation: '-' can never be a slug.
    assert SLUG_RE.match("-") is None


def test_reserved_segments_contains_preview_and_version():
    assert {"_preview", "_version"} == RESERVED_SEGMENTS


def test_access_base_contains_exactly_four_values():
    values = {member.value for member in AccessBase}
    assert values == {"public", "sso", "site_team", "nobody"}


def test_a_public_base_stays_public_whatever_the_extras_say():
    """The extras widen and never narrow, so they cannot make a public site
    non-public; the interface says as much and this is where it holds."""
    assert AccessPolicy(AccessBase.PUBLIC, keys=True, invitees=True).is_public is True
    assert AccessPolicy(AccessBase.SSO).is_public is False


def test_login_can_help_follows_the_base_and_the_invitee_extra():
    """The one thing that decides between a login redirect and a neutral 404
    for an anonymous visitor: secret links are handed in, not logged in for."""
    assert AccessPolicy(AccessBase.NOBODY).login_can_help is False
    assert AccessPolicy(AccessBase.NOBODY, keys=True).login_can_help is False
    assert AccessPolicy(AccessBase.NOBODY, invitees=True).login_can_help is True
    assert AccessPolicy(AccessBase.SSO).login_can_help is True
    assert AccessPolicy(AccessBase.SITE_TEAM).login_can_help is True
