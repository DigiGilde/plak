"""Core constants for Plak: slug validation, the path vocabulary both hosts
share, access levels and roles."""

import enum
import re
from dataclasses import dataclass

SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def path_under(path: str, prefix: str) -> bool:
    """Whether `path` is `prefix` itself or something below it.

    Compares on a segment boundary, so `/beheerder` is not under `/beheer`.
    Shared by the host separation and the SPA (platform/spa.py), which have to
    read a path the same way or a path would belong to both or to neither.
    """
    return path == prefix or path.startswith(prefix + "/")


# Locations the web pins down. They exist on both hosts, can never be a group,
# and the SPA fallback never swallows them: a crawler, a browser or an ACME
# client has to get the real answer or a 404 there, never an HTML page with
# status 200.
STANDARD_LOCATIONS = ("/robots.txt", "/favicon.ico", "/.well-known")

# Internal only: the probe reaches the pod directly, so this
# path exists on neither public host.
INTERNAL_ONLY_PATHS = frozenset({"/healthz"})


# SPA pages at the root of the beheer host rather than under /-/, because a
# client opens them by URL (the CLI's device flow opens /cli-koppelen). The
# Vue router matches them before /:group, so a group of that name would be
# unreachable in the SPA. frontend/src/router.ts holds the same list.
SPA_ROOT_PAGES = frozenset({"cli-koppelen"})

# Group slugs the application refuses, and the source of a CHECK constraint on
# `groups.slug` (models/identity.py, migration 0001). The locations the web
# prescribes have to keep answering for themselves on the content host, so
# they can never be somebody's group; nor can a root-level SPA page.
RESERVED_SLUGS = frozenset(location.lstrip("/") for location in STANDARD_LOCATIONS) | SPA_ROOT_PAGES

# Refused as a top-level segment inside dists (unpacked content): these are
# the platform's own names under a site, so a bundle that carries one would
# shadow a preview or a version view.
RESERVED_SEGMENTS = frozenset({"_preview", "_version"})

# The name both the ingest (which requires it in the root) and the serving
# (which looks it up for a directory request) key on. Case-sensitive: on the
# content volume `Index.html` is a different file.
INDEX_FILE = "index.html"

# Platform namespace: per SLUG_RE the segment "-" can never be a
# group, so /-/... is never content.
# Every app endpoint lives under it, on both hosts, which is what keeps the
# root of the beheer host free for the SPA.
PLATFORM_SEGMENT = "-"
PLATFORM_PREFIX = f"/{PLATFORM_SEGMENT}"

# Login path prefixes: shared between platform/pages.py (route
# registration) and ratelimit.py (class assignment), so a route change can
# never silently shift the rate-limit class of the login routes.
#
# The admin flow and the content flow land on the same spelling but stay two
# constants: they belong to two flows, and host separation and rate limiting
# both read them. A shared value is not a shared concept.
PATH_LOGIN = f"/{PLATFORM_SEGMENT}/login"
PATH_OAUTH2_PREFIX = f"/{PLATFORM_SEGMENT}/oauth2/"
PATH_LOGOUT = f"/{PLATFORM_SEGMENT}/logout"

# Where the OP delivers a back-channel logout token (OIDC Back-Channel Logout
# 1.0, platform/backchannel.py). Beheer host only: belongs_to_content() refuses
# everything under /-/ that is not one of the four content paths.
PATH_BACKCHANNEL_LOGOUT = f"/{PLATFORM_SEGMENT}/oidc/backchannel-logout"

PATH_CONTENT_LOGIN = f"/{PLATFORM_SEGMENT}/login"
PATH_CONTENT_OAUTH2_PREFIX = f"/{PLATFORM_SEGMENT}/oauth2/"
PATH_CONTENT_LOGOUT = f"/{PLATFORM_SEGMENT}/logout"

# Where the code of a secret link shared without it is handed in
# (serving/code_page.py). The only POST the content host has, so host
# separation and rate limiting both single this path out.
PATH_CONTENT_CODE = f"/{PLATFORM_SEGMENT}/code"


class AccessBase(enum.StrEnum):
    """The base answer to "who can look at this site": exactly one per site,
    and never the whole story.

    NOBODY grants nothing by itself. A site on that base is unreachable unless
    one of the two extras beside it (secret links, invitees) is on, which is
    what makes "alleen via de uitzonderingen hieronder" expressible at all.
    """

    PUBLIC = "public"
    SSO = "sso"
    SITE_TEAM = "site_team"
    NOBODY = "nobody"


@dataclass(frozen=True)
class AccessPolicy:
    """A base plus the extras that widen it: who may look, in one value.

    A site, a group default and a preview override all carry these same three
    fields, so the gate never has to know which of the three it is holding.
    """

    base: AccessBase
    keys: bool = False
    invitees: bool = False

    @property
    def is_public(self) -> bool:
        """Whether anyone gets in without a credential of any kind.

        The extras cannot narrow a public base, so they do not enter into it.
        Serving reads this for private caching and for the noindex decision.
        """
        return self.base is AccessBase.PUBLIC

    @property
    def login_can_help(self) -> bool:
        """Whether having a session can grant what anonymity cannot.

        This, not the base alone, decides the login redirect: base `nobody`
        plus genodigden still has to send an anonymous visitor to the login,
        while base `nobody` with only secret links must stay a neutral 404.
        """
        return self.base in (AccessBase.SSO, AccessBase.SITE_TEAM) or self.invitees


class Role(enum.StrEnum):
    """Role within a group or within a site.

    A reader looks on, an editor changes content, an admin changes policy: who
    may look, who may join in, and what disappears for good. The interface
    labels these lezer, redacteur and beheerder.
    """

    READER = "reader"
    EDITOR = "editor"
    ADMIN = "admin"


# The ranking is spelled out here instead of sitting implicitly in the
# declaration order, because "the widest wins" counts on it. A
# StrEnum compares as text, and that ordering is demonstrably wrong:
# 'editor' < 'admin' is false. So never compare two roles with < or >, always
# through this rank (access/roles.py).
ROLE_RANK: dict[Role, int] = {Role.READER: 1, Role.EDITOR: 2, Role.ADMIN: 3}
