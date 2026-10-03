"""Session API for the admin SPA on `/-/api/v1`.

Every route runs behind `require_admin_origin` (origin separation) and
`require_active_member`; mutations additionally demand the CSRF double-submit
header. Errors are problem+json through the handlers from `api/errors.py`.
The paths mirror `frontend/src/api/plak.ts`.

Every route declares its response as a pydantic model on the ApiModel base
(lowerCamelCase on the wire), a `summary`/`description`, and through
`error_responses()` the problem+json errors it can return; that is what the
API documentation on `/-/api/docs` lives off.

The two deploy endpoints (`POST .../deploys` and `DELETE .../previews/{ref}`)
deliberately do NOT live in this router: `api/deploys.py` serves those paths
for bearer and session at once. main.py puts `require_admin_origin` on that
router too (bearer CI sends no Origin/Sec-Fetch-Site and passes it
unhindered).

Three routes here do take a bearer as well: creating a group, creating a
site and linking a repository accept the CLI token from `plak login`
(`require_creator`), with the same role checks as the session. A bearer
request carries no ambient credentials, so it skips the CSRF check; the
origin guard stays on it, as on the deploy router. Every other route in this router is session only, which
`BearerOutsideDeploysMiddleware` enforces before the request gets here.
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import math
import shutil
import time
import unicodedata
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select, tuple_, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from plak import net
from plak.access import keys as access_keys
from plak.access import roles
from plak.api.authorization import require_group_role, require_site_role
from plak.api.deploys import bearer_from_request, cli_member
from plak.api.docs import (
    TAG_AUDIT,
    TAG_CI,
    TAG_CLI,
    TAG_GROUP_MEMBERS,
    TAG_GROUPS,
    TAG_INVITEES,
    TAG_KEYS,
    TAG_OVERVIEW,
    TAG_PLATFORM,
    TAG_PREVIEWS,
    TAG_SESSION,
    TAG_SITE_MEMBERS,
    TAG_SITES,
    TAG_VERSIONS,
)
from plak.api.errors import WWW_AUTHENTICATE_BEARER, ApiError, error_responses
from plak.api.origin_guard import require_admin_origin
from plak.api.schema import ACCESS_BASE_HINT, AccessOut, ApiModel
from plak.audit import vocabulary
from plak.audit.ip_crypto import IpDecryptError, decrypt_ip
from plak.audit.log import Actor, AuditLog, LookupLimitReachedError
from plak.audit.pseudonymisation import pseudonymise, truncate_ip
from plak.auth import sessions
from plak.auth.members import require_active_member
from plak.ci.providers import (
    GITHUB_HOST,
    ProviderClient,
    ProviderUnavailableError,
    RepositoryNotFoundError,
    ResolvedRepository,
    host_label,
    valid_name,
)
from plak.ci.trust import ci_actor_identifier
from plak.cli import service as cli
from plak.config import normalise_https_base_url
from plak.constants import RESERVED_SLUGS, ROLE_RANK, SLUG_RE, AccessBase, Role
from plak.expiry import MAX_VALIDITY, ExpiryError
from plak.ingest.service import IngestError, IngestService
from plak.models.audit import ActorKind, AuditLogEntry, ContentViewer
from plak.models.ci import CiProvider, SiteRepository
from plak.models.identity import (
    Group,
    GroupMember,
    Member,
    MemberLanguage,
    MemberStatus,
    PlatformRole,
    SiteMember,
)
from plak.models.publication import (
    AccessKey,
    Invitee,
    KeyStatus,
    Preview,
    Site,
    Version,
    VersionTarget,
)
from plak.ratelimit import InMemoryCounter

# Message keys in plak/messages.py; the code the SPA branches on is what
# stands before the dot.
KEY_NO_SESSION = "NO_SESSION"
KEY_CSRF_INVALID = "CSRF_INVALID"
KEY_ADMIN_ONLY = "NOT_ADMIN"
KEY_NOT_GROUP_MEMBER = "NOT_GROUP_MEMBER.you"

# PostgreSQL code for a PL/pgSQL `RAISE EXCEPTION` without a code of its own.
_SQLSTATE_RAISE_EXCEPTION = "P0001"

# Unicode categories no text a member types may contain: Cc (control, e.g.
# NUL, tab, newline) and Cf (format, e.g. U+202E right-to-left override, which
# reverses the reading order of everything after it) - a plain ASCII space is
# category Zs, not Cc/Cf, so ordinary spacing is unaffected.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf"})


def _has_forbidden_characters(value: str) -> bool:
    return any(unicodedata.category(char) in _FORBIDDEN_CATEGORIES for char in value)

ACCESS_EXTRAS_HINT = (
    "Besides the base there are two exceptions that can be added independently of each other: `keys` "
    "lets in anyone with a valid secret link, even without signing in, and `invitees` lets in "
    "signed-in addresses on the invitee list. They widen the base and never narrow it, so with "
    "base `public` they change nothing."
)

ROLE_HINT = (
    "`reader` (views), `editor` (publishes) or `admin` (determines "
    "access, invitees, secret links and who is in the group). A higher role can do everything a "
    "lower role can."
)

SITE_ROLE_HINT = (
    "`reader` (views), `editor` (publishes) or `admin` (determines "
    "access, invitees, secret links and who has a role on the site). A higher role can do everything a "
    "lower role can."
)

MEMBER_IDENTIFIER_HINT = (
    "E-mail address, SSO subject, or the full name as known in the admin interface (case and "
    "leading or trailing spaces ignored). A name only works if it matches exactly "
    "one active platform member; if it matches more than one, the response is 409; use the "
    "e-mail address instead. Case in an e-mail address is ignored."
)


def _timestamp_schema() -> dict[str, Any]:
    """OpenAPI exceptions for a timestamp field: the API writes RFC 3339 in UTC with a Z."""
    return {"format": "date-time", "examples": ["2026-09-12T09:30:00Z"]}


# -- Dependencies -----------------------------------------------------------


async def _db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


async def require_csrf(request: Request) -> None:
    """Double-submit check for mutations; runs before the member upsert so a
    forged request never creates a member record."""
    session = sessions.session_from_request(request)
    if session is None:
        raise ApiError(401, KEY_NO_SESSION)
    if not sessions.csrf_valid(request, session):
        raise ApiError(403, KEY_CSRF_INVALID)


Db = Annotated[AsyncSession, Depends(_db)]
ActiveMember = Annotated[Member, Depends(require_active_member)]


async def require_platform_admin(member: ActiveMember) -> Member:
    """Same check as `_require_admin`, but as a dependency: FastAPI resolves
    dependencies before it parses the body, so a non-admin is refused before
    a malformed body would be (previously a 422 could beat the 403 on these
    routes, and a non-admin's probing request went unaudited)."""
    if member.platform_role != PlatformRole.ADMIN:
        raise ApiError(403, KEY_ADMIN_ONLY)
    return member


PlatformAdmin = Annotated[Member, Depends(require_platform_admin)]
Csrf = Annotated[None, Depends(require_csrf)]
SearchTerm = Annotated[
    str,
    Query(
        description=(
            "Search term of at least two characters. Matches name and e-mail address, case-insensitively, anywhere "
            "in the text."
        ),
        examples=["jansen"],
    ),
]
SiteRolesOnRemoval = Annotated[
    Literal["keep", "remove"],
    Query(
        alias="siteRoles",
        description=(
            "What happens to the direct site roles that this member has on sites in this group. "
            "`keep` leaves them in place, so they keep access to those sites; `remove` removes them in "
            "the same action. Omitted means `keep`: removing more than was asked is a "
            "deliberate choice.\n\n"
            "Only sites in this group. A site role in another group stays out of view and "
            "untouched."
        ),
        examples=["remove"],
    ),
]


# -- Error contract per route -----------------------------------------------

# Every route in this router carries these three; the routes extend them with
# whatever they can refuse themselves.
_ERROR_SESSION = {
    401: "There is no valid admin session: the cookie is missing, invalid or expired (`NO_SESSION`).",
    403: (
        "The request comes from an origin other than the admin host, or the member is not (or no "
        "longer) active (`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`)."
    ),
    429: "The rate limit budget for this session is used up; try again later.",
}
_ERROR_CSRF = {403: "The `X-CSRF-Token` header is missing or does not match the CSRF cookie (`CSRF_INVALID`)."}
_ERROR_ADMIN = {403: "Only a platform administrator is allowed to do this (`NOT_ADMIN`)."}
_ERROR_GROUP_ROLE = {403: "The member's role in this group is insufficient for this action (`INSUFFICIENT_ROLE`)."}
_ERROR_SITE_ROLE = {403: "The member's role on this site is insufficient for this action (`INSUFFICIENT_ROLE`)."}
_ERROR_GROUP = {404: "Unknown group (`UNKNOWN_GROUP`)."}
_ERROR_SITE = {404: "Unknown group (`UNKNOWN_GROUP`) or unknown site (`UNKNOWN_SITE`)."}
_ERROR_SEARCH = {422: "The search term is shorter than two characters (`SEARCH_TOO_SHORT`)."}
_ERROR_AUDIT_FILTER = {
    422: (
        "A filter is invalid: the cursor is unreadable (`CURSOR_INVALID`), `actorPseudonym` is not a "
        "hexadecimal HMAC value (`ACTOR_PSEUDONYM_INVALID`), or `limit` is outside 1 to 200."
    )
}
_ERROR_AUDIT_UNAVAILABLE = {
    503: (
        "The action itself writes an audit row, and that write failed (`AUDIT_UNAVAILABLE`); "
        "nothing was returned. Try again later."
    )
}
_ERROR_IDENTIFIER_AMBIGUOUS = {
    409: (
        "The e-mail address or name belongs to more than one member (`IDENTIFIER_AMBIGUOUS`); if the name is "
        "ambiguous, use the e-mail address; if the e-mail address is ambiguous, use the SSO subject."
    )
}
_ERROR_REASON = {
    422: (
        "`reason` is missing, is shorter than 10 or longer than 500 characters (after stripping spaces), "
        "contains a control or formatting character, or contains an `@` (not an e-mail address; give a "
        "case or ticket number)."
    )
}
_ERROR_LOOKUP_LIMIT = {
    429: (
        "The daily limit on audit lookups by this platform administrator has been reached "
        "(`LOOKUP_LIMIT_REACHED`); try again tomorrow."
    )
}


def _errors(*additions: dict[int, str]) -> dict[int | str, dict[str, Any]]:
    """The fixed session errors plus the route's own, as OpenAPI `responses`."""
    descriptions = dict(_ERROR_SESSION)
    for addition in additions:
        for status, text in addition.items():
            existing = descriptions.get(status)
            descriptions[status] = f"{existing} {text}" if existing else text
    return error_responses(descriptions)


def _deleted(what: str) -> dict[int | str, dict[str, Any]]:
    return {204: {"description": f"{what} No content is returned."}}


# -- Request bodies ---------------------------------------------------------


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class AccessChoice(ApiModel):
    """Access when creating: base plus exceptions. Every field may be omitted; what then applies
    is described on the field that uses this object."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"base": "nobody", "keys": True}, {"base": "public"}]}
    )

    base: AccessBase | None = Field(default=None, description=f"The base, exactly one of: {ACCESS_BASE_HINT}")
    keys: bool | None = Field(
        default=None, description="Whether secret links grant access. " + ACCESS_EXTRAS_HINT
    )
    invitees: bool | None = Field(
        default=None, description="Whether to give invitees access after signing in with SSO Rijk."
    )


class GroupCreate(ApiModel):
    """A new group."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"name": "Team Aurora", "slug": "aurora"},
                {"name": "Team Aurora", "slug": "aurora", "defaultAccess": {"base": "sso"}},
            ]
        }
    )

    name: str = Field(description="Display name of the group.", examples=["Team Aurora"])
    slug: str = Field(
        description=(
            "Slug of the group: lowercase letters, digits and hyphens, at most 63 characters, and not "
            "reserved (`robots.txt`, `favicon.ico`, `.well-known`). This becomes the first "
            "path segment of every site URL of the group."
        ),
        examples=["aurora"],
    )
    default_access: AccessChoice | None = Field(
        default=None,
        description=(
            "Optional: the default access the group starts with. Every omitted field, or the whole "
            "object omitted, gets the default: base `site_team`, no secret links, no "
            "invitees."
        ),
    )


class SiteCreate(ApiModel):
    """A new site within a group."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"title": "Documentatie", "slug": "docs"},
                {"title": "Documentatie", "slug": "docs", "access": {"base": "public"}},
            ]
        }
    )

    title: str = Field(description="Display name of the site.", examples=["Documentatie"])
    slug: str = Field(
        description="Slug of the site: lowercase letters, digits and hyphens, unique within the group.",
        examples=["docs"],
    )
    access: AccessChoice | None = Field(
        default=None,
        description=(
            "Optional: the access the site starts with. Every omitted field, or the whole object "
            "omitted, inherits the default access of the group, so `{\"keys\": true}` only turns on "
            "secret links on top of what the group already prescribes."
        ),
    )


class AccessBody(ApiModel):
    """Who may see the content: a base plus two exceptions."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"base": "nobody", "keys": True, "invitees": False}]}
    )

    base: AccessBase = Field(description=f"The base, exactly one of: {ACCESS_BASE_HINT}")
    keys: bool = Field(
        default=False, description="Whether secret links grant access. " + ACCESS_EXTRAS_HINT
    )
    invitees: bool = Field(
        default=False, description="Whether to give invitees access after signing in with SSO Rijk."
    )


class ExternalSourcesBody(ApiModel):
    """Whether the content of this site may load external sources."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"externalSources": True}]})

    external_sources: bool = Field(
        description=(
            "`true` (the default) lets the page load scripts and styles from cdnjs, jsDelivr and "
            "unpkg, and fonts from Google Fonts. `false` only allows sources from the site itself, "
            "and is the safer choice for a confidential page. What stays blocked in both modes: "
            "fetching data from or sending data to other hosts, images from elsewhere, an iframe, "
            "and a form that posts elsewhere."
        )
    )


class SandboxBody(ApiModel):
    """Whether the content of this site is isolated from the other sites."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"sandbox": True}]})

    sandbox: bool = Field(
        description=(
            "`true` (the default) serves the content with a CSP sandbox without "
            "`allow-same-origin`, so the page gets an opaque origin: it cannot "
            "read any other site on this hostname, receives no cookies and cannot "
            "store anything in the browser. Its own styles, scripts, images and fonts "
            "load as usual. `false` puts the page back on the shared origin, needed for a "
            "site that uses `localStorage`, `sessionStorage` or a cookie."
        )
    )


class LiveVersionsKeptBody(ApiModel):
    """How many previous live versions this site keeps."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"liveVersionsKept": 3}]})

    live_versions_kept: int | None = Field(
        strict=True,
        description=(
            "The number of previous live versions that the nightly cleanup leaves in place in addition to the "
            "current one: an integer of 0 or more. `0` keeps all live versions of this site; "
            "`null` makes the site follow the platform default."
        ),
        json_schema_extra={"minimum": 0},
    )


class PreviewAccessBody(ApiModel):
    """Separate access settings for a preview, or `null` to turn it off."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"access": {"base": "sso", "keys": False, "invitees": False}}, {"access": None}]
        }
    )

    access: AccessBody | None = Field(
        description=(
            "Access that applies only to this preview: base plus exceptions as a whole. "
            "`null` removes the override, after which the preview follows the site again."
        )
    )


class IdentifierBody(ApiModel):
    """A person, identified by e-mail address or SSO subject."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"identifier": "genodigde@example.nl"}]}
    )

    identifier: str = Field(
        description="E-mail address or SSO subject. Case in an e-mail address is ignored.",
        examples=["genodigde@example.nl"],
    )


class GroupMemberAdd(IdentifierBody):
    """A person who is added to the group, with the role they get there."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"identifier": "lid@example.nl"},
                {"identifier": "lid@example.nl", "role": "editor"},
            ]
        }
    )

    identifier: str = Field(description=MEMBER_IDENTIFIER_HINT, examples=["lid@example.nl"])
    role: Role = Field(
        default=Role.READER,
        description=f"Role this member gets in the group: {ROLE_HINT} Omitted means `reader`.",
        examples=["reader"],
    )


class SiteMemberAdd(IdentifierBody):
    """A person who gets a role on this one site, in addition to what a group role already gives."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"identifier": "lid@example.nl"},
                {"identifier": "lid@example.nl", "role": "editor"},
            ]
        }
    )

    identifier: str = Field(description=MEMBER_IDENTIFIER_HINT, examples=["lid@example.nl"])
    role: Role = Field(
        default=Role.READER,
        description=(
            f"Role this member gets on this site: {SITE_ROLE_HINT} Omitted means `reader`. The role "
            "only widens; someone with a higher group role keeps that."
        ),
        examples=["reader"],
    )


class KeyCreate(ApiModel):
    """A new secret link."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"label": "reviewers", "expiresAt": "2026-12-31T23:59:59Z"},
                {"label": None, "expiresAt": None},
            ]
        }
    )

    label: str | None = Field(
        default=None,
        description=(
            "What this link is for; only for the admin's own use, it does not appear in the URL. Omit "
            "or leave empty for a name with today's date."
        ),
        examples=["reviewers"],
    )
    expires_at: datetime | None = Field(
        default=None,
        description=(
            "Time after which the link stops working (RFC 3339). Omit or `null` for the "
            f"default validity of 90 days; at most {MAX_VALIDITY.days} days ahead."
        ),
        json_schema_extra=_timestamp_schema(),
    )

    @field_validator("label")
    @classmethod
    def _plain_label(cls, value: str | None) -> str | None:
        if value is not None and _has_forbidden_characters(value):
            raise ValueError("label must not contain control or formatting characters")
        return value

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)


# -- Response models --------------------------------------------------------


class LanguageUpdate(ApiModel):
    """The language choice of the signed-in member."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"language": "en"}, {"language": None}]}
    )

    language: MemberLanguage | None = Field(
        description=(
            "`nl` or `en`, or `null` to let the browser decide again "
            "(`Accept-Language`, with English if that does not settle it). The choice is tied to the "
            "account, so it applies on every device."
        ),
        examples=["en"],
    )


class PlatformRoleUpdate(ApiModel):
    """The new platform role of a member."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"platformRole": "admin"}]})

    platform_role: PlatformRole = Field(
        description="`admin` makes the member platform administrator, `member` removes that role again.",
        examples=["admin"],
    )


class GroupRoleUpdate(ApiModel):
    """The new role of a member within a group."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"role": "editor"}]})

    role: Role = Field(description=f"Role this member gets in the group: {ROLE_HINT}", examples=["editor"])


class SiteRoleUpdate(ApiModel):
    """The new site role of a member."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"role": "editor"}]})

    role: Role = Field(
        description=(
            f"Role this member gets on this site: {SITE_ROLE_HINT} A role lower than the group role "
            "changes nothing: the higher of the two applies."
        ),
        examples=["editor"],
    )


class MemberOut(ApiModel):
    """A platform member."""

    id: uuid.UUID = Field(description="Internal ID of the member.")
    sso_subject: str = Field(description="The `sub` from the SSO token; a session is tied to a member through it.")
    email: str = Field(description="E-mail address from the SSO profile; also the identifier for group membership.")
    name: str = Field(description="Display name from the SSO profile; empty if the identity provider does not send it.")
    platform_role: PlatformRole = Field(
        description="`admin` may configure platform-wide, `member` only within their own groups."
    )
    status: MemberStatus = Field(
        description=(
            "`active` (may use the API) or `deactivated` (is refused). Only `active` gets "
            "through to the API."
        )
    )
    is_bootstrap: bool = Field(
        default=False,
        description=(
            "Whether this is the account from `PLAK_BOOTSTRAP_ADMIN_SUB`. That account is restored to "
            "administrator and active at every sign-in, so its status and platform role cannot be changed. The "
            "SPA uses this to not offer those actions."
        ),
        examples=[False],
    )
    created_at: str = Field(
        description="When the member was created: the first visit to the admin interface.",
        json_schema_extra=_timestamp_schema(),
    )
    last_login_at: str | None = Field(
        default=None,
        description="Last successful sign-in, or `null` if there was none yet.",
        json_schema_extra=_timestamp_schema(),
    )


class VolumeOut(ApiModel):
    """How full the content volume is."""

    total_bytes: int = Field(description="Size of the content volume in bytes.", examples=[1073741824])
    used_bytes: int = Field(description="Bytes in use on the volume.", examples=[536870912])
    free_bytes: int = Field(description="Bytes still free on the volume.", examples=[536870912])
    reserve_bytes: int = Field(
        description=(
            "Free space the volume must keep (`PLAK_STORAGE_MIN_FREE_BYTES`); below that a "
            "deploy is refused. `0` means that check is off."
        ),
        examples=[104857600],
    )
    max_deploy_bytes: int = Field(
        description=(
            "Largest unpacked size of one deploy (`PLAK_INGEST_MAX_TOTAL`). If free space on the volume is less "
            "than `reserveBytes` plus this number, a deploy of maximum size is no longer possible."
        ),
        examples=[209715200],
    )


class MyGroupRole(ApiModel):
    """A group in which the signed-in member has a role."""

    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    role: Role = Field(description=f"Role of the member in this group: {ROLE_HINT}", examples=["editor"])


class MySiteRole(ApiModel):
    """A site on which the signed-in member has a direct site role."""

    group_slug: str = Field(description="Slug of the group this site is in.", examples=["aurora"])
    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    role: Role = Field(
        description=f"The site role itself, independent of the group role: {SITE_ROLE_HINT}", examples=["editor"]
    )
    effective_role: Role = Field(
        description=(
            "What the member is actually allowed to do on this site: the higher of their group role "
            "and this site role."
        ),
        examples=["editor"],
    )


class MyProfile(MemberOut):
    """The signed-in member, extended with what the SPA needs to build links."""

    content_base_url: str = Field(
        description=(
            "Origin on which the published content lives. The SPA builds public URLs, preview links "
            "and secret links on it. Always a different host than the admin interface: content and admin "
            "never share an origin."
        ),
        examples=["https://sites.plak.example"],
    )
    group_roles: list[MyGroupRole] = Field(
        default_factory=list,
        description=(
            "Groups in which this member has a role, sorted by slug. Empty if the member is not a group "
            "member anywhere."
        ),
    )
    site_roles: list[MySiteRole] = Field(
        default_factory=list,
        description=(
            "Sites on which this member has a direct site role, sorted by group and site. Only the sites "
            "with a direct site role are listed: on every other site of a group the group role "
            "from `groupRoles` simply applies."
        ),
    )
    ci_forgejo_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "The Forgejo instances whose CI ID tokens Plak accepts (`PLAK_CI_FORGEJO_HOSTS`), as "
            "base URL. GitHub is always accepted and is not listed here."
        ),
        examples=[["https://code.overheid.nl"]],
    )
    ci_audience: str = Field(
        default="",
        description=(
            "The audience a CI workflow must request for its ID token: exactly `PLAK_BASE_URL`. That "
            "is also the value for the `host` input of the plak action."
        ),
        examples=["https://beheer.plak.example"],
    )
    language: MemberLanguage | None = Field(
        default=None,
        description=(
            "The language this member chose for the admin interface: `nl` or `en`. `null` means the member "
            "made no choice and the SPA takes the language from the browser. The choice is stored on the account "
            "and so applies on every device."
        ),
        examples=["en"],
    )


class GroupOut(ApiModel):
    """A group: owner of sites and the unit of membership."""

    slug: str = Field(description="Slug of the group; the first path segment of every site URL.", examples=["aurora"])
    name: str = Field(description="Display name of the group.", examples=["Team Aurora"])
    default_access: AccessOut = Field(
        description=(
            "Access that a new site in this group starts with. Existing sites are not affected."
        )
    )


class SiteOut(ApiModel):
    """A site with the summary the SPA shows in lists."""

    group_slug: str = Field(description="Slug of the group this site is in.", examples=["aurora"])
    slug: str = Field(description="Slug of the site; the second path segment of the site URL.", examples=["docs"])
    title: str = Field(description="Display name of the site.", examples=["Documentatie"])
    access: AccessOut = Field(description="Who may see the live content: base plus exceptions.")
    external_sources: bool = Field(
        description=(
            "Whether the content of this site may load scripts, styles and fonts from a fixed list of "
            "external hosts. `true` by default; turning it off is an extra restriction."
        )
    )
    sandbox: bool = Field(
        description=(
            "Whether the content of this site is isolated from the other sites on the same "
            "hostname. `true` by default; turning it off is needed for a site that stores something in the "
            "browser."
        )
    )
    live_version_id: uuid.UUID | None = Field(
        default=None, description="Version that is currently on the public URL, or `null` if nothing is live yet."
    )
    live_versions_kept: int | None = Field(
        default=None,
        description=(
            "Site-specific number of previous live versions that this site keeps, or `null` if the site follows the "
            "platform default. `0` keeps all live versions. The number that currently "
            "applies is at `GET /sites/{groupSlug}/{siteSlug}/storage`."
        ),
        examples=[3],
    )
    created_by: str = Field(
        description="ID of the member who created the site; empty if that member has since been deleted."
    )
    has_live_version: bool = Field(description="Shorter form of `liveVersionId is not null`, handy in lists.")
    last_published_at: str | None = Field(
        default=None,
        description="Time of the most recent deploy, live or preview; `null` if nothing has been deployed yet.",
        json_schema_extra=_timestamp_schema(),
    )
    preview_count: int = Field(description="Number of previews that currently exist for this site.", examples=[2])


class VersionOut(ApiModel):
    """A deployed version: an unpacked bundle that can be live or attached to a preview."""

    id: uuid.UUID = Field(description="Internal ID of the version; you use it to roll back.")
    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    target: VersionTarget = Field(description="`live` for the public URL, `preview` for a preview ref.")
    storage_ref: str = Field(description="Internal reference to the unpacked file tree on disk.")
    origin: Literal["upload", "action"] = Field(
        description=(
            "`upload` if a member published this version themselves (in the admin interface or with the CLI), "
            "`action` if CI published it from the linked repository."
        )
    )
    created_by_member: uuid.UUID | None = Field(
        default=None, description="Member who deployed, or `null` for a deploy from CI."
    )
    created_by_name: str | None = Field(
        default=None,
        description=(
            "Name of the member who deployed, or their e-mail address if the identity provider sent no "
            "name. `null` for a deploy from CI. Without this field a list only has the "
            "internal ID to show, and that tells a reader nothing."
        ),
        examples=["Sanne Jansen"],
    )
    created_by_repository: str | None = Field(
        default=None,
        description=(
            "Repository from which CI published this version, as host plus `owner/repo`; `null` for a "
            "deploy by a member."
        ),
        examples=["github.com/minbzk/website"],
    )
    created_at: str | None = Field(
        default=None, description="Time of the deploy.", json_schema_extra=_timestamp_schema()
    )
    is_live: bool = Field(description="Whether this version is currently the live version of the site.")


class SiteStorageOut(ApiModel):
    """What a site takes up on the content volume, and how many live versions are kept."""

    used_bytes: int = Field(
        description=(
            "What all versions of this site together take up on the content volume, live and preview, in bytes."
        ),
        examples=[77594624],
    )
    max_bytes: int = Field(
        description=(
            "How much all versions of a site together may take up, in bytes. A deploy that would go "
            "over that gets 413 (`SITE_QUOTA_EXCEEDED`). `0` means no limit."
        ),
        examples=[524288000],
    )
    live_versions_kept: int = Field(
        description=(
            "How many previous live versions of this site are kept in addition to the current one: the site's own "
            "setting, or else the platform default. Older live versions are removed by "
            "the nightly cleanup, row and files. `0` means all versions are kept."
        ),
        examples=[5],
    )
    live_versions_kept_is_default: bool = Field(
        description=(
            "`true` if the site follows the platform default, `false` if a site admin "
            "set a custom number."
        ),
        examples=[True],
    )
    default_live_versions_kept: int = Field(
        description=(
            "The platform default: how many previous live versions a site without a custom number "
            "keeps. `0` means such sites keep all versions."
        ),
        examples=[5],
    )


class PreviewOut(ApiModel):
    """A preview: a temporary copy of a site alongside the live version."""

    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    ref: str = Field(
        description="Name of the preview, usually the pull request number or the branch name as a slug.",
        examples=["pr-42"],
    )
    version_id: uuid.UUID = Field(description="Version that is on this preview.")
    access_override: AccessOut | None = Field(
        default=None,
        description=(
            "Access that applies only to this preview; `null` means: follows the site."
        ),
    )
    last_updated_at: str | None = Field(
        default=None,
        description="Time of the last deploy to this preview.",
        json_schema_extra=_timestamp_schema(),
    )
    expires_at: str | None = Field(
        default=None,
        description=(
            "Time at which the cleanup job discards this preview. Every new deploy to the same ref pushes "
            "it forward; `null` means it does not expire automatically."
        ),
        json_schema_extra=_timestamp_schema(),
    )
    url: str = Field(
        description="Path of the preview on the content origin from `contentBaseUrl`.",
        examples=["/aurora/docs/_preview/pr-42/"],
    )


class InviteeOut(ApiModel):
    """An address on the invitee list of a site."""

    id: str = Field(
        description="ID of this invitee; you use it to remove them from the list.",
        examples=["3f2a1c6e-9b4d-4f2a-8c1e-7d5b2a9f4c31"],
    )
    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    identifier: str = Field(
        description="E-mail address or SSO subject of the invitee, normalised to lowercase.",
        examples=["genodigde@example.nl"],
    )
    added_by: str = Field(
        description="ID of the member who added the invitee; empty if that member has since been deleted."
    )
    added_at: str | None = Field(
        default=None, description="When the invitee was added.", json_schema_extra=_timestamp_schema()
    )


class KeyOut(ApiModel):
    """A secret link, without the secret itself."""

    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    label: str = Field(description="What this link is for; only for the admin.", examples=["reviewers"])
    selector: str = Field(
        description="First half of the key value: the non-secret half, with which you look up the key.",
        examples=["a1b2c3d4"],
    )
    status: KeyStatus = Field(description="`active` (works) or `revoked` (no longer works).")
    created_at: str | None = Field(
        default=None, description="Time of creation.", json_schema_extra=_timestamp_schema()
    )
    expires_at: str | None = Field(
        default=None,
        description="Time after which the link stops working.",
        json_schema_extra=_timestamp_schema(),
    )


class KeyCreated(ApiModel):
    """The fresh key plus its value. This is the only moment the value can be seen."""

    key: KeyOut = Field(description="The created key.")
    value: str = Field(
        description=(
            "The full key value `<selector>.<secret>` for use in the link. Plak only stores a hash, "
            "so this value cannot be retrieved anywhere afterwards."
        ),
        examples=["a1b2c3d4.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"],
    )


class GroupSiteRole(ApiModel):
    """A role of its own on one site, always a site in the group you are looking at."""

    site_slug: str = Field(description="Slug of the site within this group.", examples=["jaarverslag"])
    site_title: str = Field(description="Title of the site, as shown in the admin interface.")
    role: Role = Field(
        description=f"Role that applies only on this site: {SITE_ROLE_HINT}",
        examples=["editor"],
    )


class GroupMemberOut(ApiModel):
    """A member of a group, as the member list of that group shows it."""

    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    member_id: str = Field(
        description="ID of the platform member; you use it to remove them from the group.",
        examples=["3f2a1c6e-9b4d-4f2a-8c1e-7d5b2a9f4c31"],
    )
    identifier: str = Field(
        description="What you use to add this member to the group or change their role: the e-mail address.",
        examples=["lid@example.nl"],
    )
    name: str = Field(description="Display name from the SSO profile; empty if missing.")
    email: str = Field(description="E-mail address from the SSO profile.", examples=["lid@example.nl"])
    role: Role = Field(
        description=f"Role of this member in this group: {ROLE_HINT}",
        examples=["reader"],
    )
    site_roles: list[GroupSiteRole] = Field(
        description=(
            "The sites in this group on which this member has a direct site role, sorted by slug. Such a "
            "role is independent of the group role and stays in force if the member leaves the group, unless you "
            "remove it at the same time (`siteRoles=remove` when removing).\n\n"
            "What this member has in another group is not listed: that belongs to that group."
        ),
    )


class SiteMemberOut(ApiModel):
    """A row of the member list of a site: everyone who can reach this site."""

    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    member_id: str = Field(
        description="ID of the platform member; you use it to remove their site role.",
        examples=["3f2a1c6e-9b4d-4f2a-8c1e-7d5b2a9f4c31"],
    )
    identifier: str = Field(
        description="What you use to set the site role of this member: the e-mail address.",
        examples=["lid@example.nl"],
    )
    name: str = Field(description="Display name from the SSO profile; empty if missing.")
    email: str = Field(description="E-mail address from the SSO profile.", examples=["lid@example.nl"])
    group_role: Role | None = Field(
        default=None,
        description=(
            f"Role in the group of this site, or `null` if this member is not a group member: {ROLE_HINT} "
            "You change this role on the group, not here."
        ),
        examples=["reader"],
    )
    site_role: Role | None = Field(
        default=None,
        description=(
            "Role that applies only on this site, or `null` if this member has none. This is the only "
            "field that the site routes change."
        ),
        examples=["editor"],
    )
    effective_role: Role = Field(
        description="What this member is actually allowed to do here: the higher of `groupRole` and `siteRole`.",
        examples=["editor"],
    )


class MemberSearchOut(ApiModel):
    """A platform member that was found, as the search field of 'add member' shows it."""

    identifier: str = Field(
        description="What you use to add this member: the e-mail address.",
        examples=["lid@example.nl"],
    )
    name: str = Field(description="Display name from the SSO profile; empty if missing.")
    email: str = Field(description="E-mail address from the SSO profile.", examples=["lid@example.nl"])
    already_member: bool = Field(
        description=(
            "Whether this member already has a direct role here: a group role in the search field of a "
            "group, a site role in that of a site. You do not add them then; you change the role in "
            "the member list."
        ),
        examples=[False],
    )
    group_role: Role | None = Field(
        default=None,
        description=(
            "Only in the search field of a site: the role with which this member already reaches this site "
            f"through the group, or `null` if they are not a group member: {ROLE_HINT} A site role only "
            "widens, so an equally low or lower site role changes nothing here. In the "
            "search field of a group this field is always `null`."
        ),
        examples=["reader"],
    )


class GroupRow(ApiModel):
    """A group with its sites, as the overview shows it."""

    group: GroupOut = Field(description="The group itself.")
    sites: list[SiteOut] = Field(
        description=(
            "The sites of the group that this member may see, sorted by slug. That is all of them, unless "
            "the member only reaches the group through a site role: then only those sites are listed."
        )
    )


class Overview(ApiModel):
    """The home screen of the admin SPA."""

    groups: list[GroupRow] = Field(
        description=(
            "Groups this member may see, sorted by slug: where they have a group role, and where they have "
            "a role on a site. Empty if the member has no role anywhere."
        )
    )


class GroupDetail(ApiModel):
    """Everything the group page of the SPA needs in one go."""

    group: GroupOut = Field(description="The group itself.")
    sites: list[SiteOut] = Field(description="Sites of the group, sorted by slug.")
    members: list[GroupMemberOut] = Field(description="Members of the group, sorted by e-mail address.")


LIVE_BRANCH_MAX_LENGTH = 255


class SiteRepositoryBody(ApiModel):
    """The repository from which CI may publish to this site."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"provider": "github", "owner": "minbzk", "repo": "website", "liveBranch": "main"},
                {
                    "provider": "forgejo",
                    "host": "https://code.overheid.nl",
                    "owner": "minbzk",
                    "repo": "website",
                    "liveBranch": None,
                },
            ]
        }
    )

    provider: CiProvider = Field(description="`github` or `forgejo`.", examples=["github"])
    host: str | None = Field(
        default=None,
        description=(
            "Base URL of the Forgejo instance, one of `ciForgejoHosts` from `GET /me`. Required for "
            "`forgejo`; omit for `github` (or `https://github.com`)."
        ),
        examples=["https://code.overheid.nl"],
    )
    owner: str = Field(description="Owner of the repository: user or organisation.", examples=["minbzk"])
    repo: str = Field(description="Name of the repository.", examples=["website"])
    live_branch: str | None = Field(
        description=(
            "The only branch allowed to publish live, without `refs/heads/`. `null` or empty: any branch may "
            "go live. Live is in any case only possible from `push`, `workflow_dispatch` or `schedule`. "
            "Previews and their cleanup are always allowed from any branch."
        ),
        examples=["main"],
    )
    repository_id: int | None = Field(
        default=None,
        description=(
            "Numeric ID of the repository, only needed if Plak cannot look it up because it is private. "
            "Together with `ownerId`, or omit both. Can be retrieved with "
            "`gh api repos/{owner}/{repo} --jq '.id, .owner.id'`."
        ),
        examples=[123456],
    )
    owner_id: int | None = Field(
        default=None,
        description="Numeric ID of the owner, together with `repositoryId`.",
        examples=[7890],
    )


class SiteRepositoryOut(ApiModel):
    """The linked repository of a site."""

    group_slug: str = Field(description="Slug of the group.", examples=["aurora"])
    site_slug: str = Field(description="Slug of the site.", examples=["docs"])
    provider: CiProvider = Field(description="`github` or `forgejo`.")
    host: str = Field(description="Base URL of the provider.", examples=["https://github.com"])
    owner: str = Field(description="Owner as the provider spells it.", examples=["minbzk"])
    repo: str = Field(description="Repository as the provider spells it.", examples=["website"])
    repository_id: int = Field(
        description="Numeric ID of the repository at the provider; survives a rename.", examples=[123456]
    )
    owner_id: int = Field(description="Numeric ID of the owner at the provider.", examples=[7890])
    live_branch: str | None = Field(
        default=None, description="The only branch allowed to publish live, or `null`: any branch.", examples=["main"]
    )
    ids_confirmed: bool = Field(
        description=(
            "Whether the IDs are confirmed: by the provider when linking, or by a CI ID token that carried both "
            "of them. `false` while they have only been entered by hand; the name may then also still differ."
        ),
        examples=[True],
    )
    created_by: str = Field(
        description=(
            "Name or e-mail address of the member who linked the repository; empty if that member has been "
            "deleted."
        )
    )
    created_at: str | None = Field(
        default=None, description="Time of linking.", json_schema_extra=_timestamp_schema()
    )


class UserCodeBody(ApiModel):
    """The code from the terminal of `plak login`."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"userCode": "WDJB-MJHT"}]})

    user_code: str = Field(
        max_length=32,
        description="The user code, with or without hyphen, case-insensitive.",
        examples=["WDJB-MJHT"],
    )


class DeviceAuthorizationPendingOut(ApiModel):
    """A pending CLI login, as the approval screen shows it."""

    user_code: str = Field(description="The user code, normalised.", examples=["WDJB-MJHT"])
    client_name: str | None = Field(
        default=None, description="How the CLI named itself; `null` if it gave nothing.", examples=["plak-cli 1.2"]
    )
    ip_truncated: str | None = Field(
        default=None,
        description="The network from which the CLI started the login (IPv4 /24, IPv6 /48).",
        examples=["203.0.113.0/24"],
    )
    created_at: str | None = Field(
        default=None, description="When the CLI started the login.", json_schema_extra=_timestamp_schema()
    )
    expires_at: str | None = Field(
        default=None, description="When the code expires.", json_schema_extra=_timestamp_schema()
    )
    same_network: bool | None = Field(
        default=None,
        description=(
            "Whether the CLI started the login from the same truncated network (IPv4 /24, IPv6 /48) as the one "
            "from which the member is approving now. `false` is a reason to be extra careful: someone else may "
            "have sent the code. `null` if one of the two addresses is unknown. No full IP address "
            "is in the response."
        ),
        examples=[True],
    )


class CliSessionOut(ApiModel):
    """A linked CLI session: an approved `plak login` of the signed-in member."""

    id: uuid.UUID = Field(description="ID of the CLI session; you use it to revoke it.")
    client_name: str | None = Field(
        default=None, description="How the CLI named itself when signing in.", examples=["plak-cli 1.2 on macOS"]
    )
    created_at: str | None = Field(
        default=None, description="Time of sign-in.", json_schema_extra=_timestamp_schema()
    )
    last_used_at: str | None = Field(
        default=None,
        description="Last time the CLI did something or refreshed; `null` if that has not happened yet.",
        json_schema_extra=_timestamp_schema(),
    )
    expires_at: str | None = Field(
        default=None,
        description="When the session expires without further use.",
        json_schema_extra=_timestamp_schema(),
    )


AUDIT_PAGE_DEFAULT = 50
AUDIT_PAGE_MAX = 200
AUDIT_PSEUDONYM_LENGTH = 64
REASON_MIN_LENGTH = 10
REASON_MAX_LENGTH = 500


class AuditFilters(ApiModel):
    """Filters on the audit log. Everything is optional and everything combines with AND."""

    limit: int = Field(
        default=AUDIT_PAGE_DEFAULT,
        ge=1,
        le=AUDIT_PAGE_MAX,
        description=f"Number of rows per page, 1 to {AUDIT_PAGE_MAX}.",
        examples=[50],
    )
    cursor: str | None = Field(
        default=None,
        description=(
            "The `nextCursor` from the previous response, copied unchanged. Omit it for the "
            "first page."
        ),
    )
    since: datetime | None = Field(
        default=None,
        description="Only rows from this time onwards (RFC 3339, inclusive). Without a time zone, UTC applies.",
        examples=["2026-09-01T00:00:00Z"],
    )
    until: datetime | None = Field(
        default=None,
        description="Only rows from before this time (RFC 3339, exclusive). Without a time zone, UTC applies.",
        examples=["2026-09-19T00:00:00Z"],
    )
    action: str | None = Field(
        default=None,
        description="Exact action, for example `content_access`, `deploy` or `site_create`.",
        examples=["content_access"],
    )
    result: str | None = Field(
        default=None,
        description="Exact outcome: `allowed`, `refused` or `login_redirect`.",
        examples=["refused"],
    )
    reason_code: str | None = Field(
        default=None,
        description="Exact reason code behind the outcome, for example `UNKNOWN_SITE`.",
        examples=["UNKNOWN_SITE"],
    )
    group: str | None = Field(
        default=None, description="Slug of the group the row is about.", examples=["aurora"]
    )
    site: str | None = Field(
        default=None, description="Slug of the site the row is about.", examples=["docs"]
    )
    actor_pseudonym: str | None = Field(
        default=None,
        description=(
            "Pseudonym of one actor, 64 hexadecimal characters. Fetch it with "
            "`POST /platform/audit/actor-pseudonym`. An e-mail address does not belong here: a query "
            "parameter ends up in proxy logs."
        ),
    )


class AuditEntryOut(ApiModel):
    """One row of the audit log: an action, who did it and how it went."""

    id: uuid.UUID = Field(description="ID of the audit row; also the second sort key after `occurredAt`.")
    occurred_at: str | None = Field(
        default=None,
        description="Time of the action.",
        json_schema_extra=_timestamp_schema(),
    )
    actor_kind: ActorKind = Field(
        description=(
            "Kind of actor: `member` (a signed-in member, including via the CLI), `ci` (a CI workflow with an ID "
            "token), "
            "`system` (the application itself) "
            "or `anonymous` (a visitor without a session)."
        )
    )
    actor_pseudonym: str | None = Field(
        default=None,
        description=(
            "Pseudonym of the actor: HMAC-SHA256 of their identifier under the audit pepper. Equal "
            "pseudonyms mean the same actor, as long as the pepper has not been changed. `null` for "
            "`system` and `anonymous`."
        ),
    )
    action: str = Field(
        description="What happened, for example `content_access` or `site_create`.",
        examples=["content_access"],
    )
    result: str = Field(
        description="How it went: `allowed`, `refused` or `login_redirect`.", examples=["refused"]
    )
    reason_code: str | None = Field(
        default=None,
        description="Reason behind the outcome, for example `UNKNOWN_SITE`.",
        examples=["UNKNOWN_SITE"],
    )
    refs: dict[str, Any] | None = Field(
        default=None,
        description="What the action was about: keys such as `group`, `site`, `preview` and `path`.",
        examples=[{"group": "aurora", "site": "docs"}],
    )
    ip_truncated: str | None = Field(
        default=None,
        description="Network of the visitor, truncated to /24 (IPv4) or /48 (IPv6); never the whole address.",
        examples=["203.0.113.0/24"],
    )


class AuditPage(ApiModel):
    """One page of the audit log, newest row first."""

    entries: list[AuditEntryOut] = Field(description="The rows of this page, newest first.")
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Opaque reference to the next page; pass it back unchanged as `cursor`. "
            "`null` means this was the last page."
        ),
    )


class ReasonField(ApiModel):
    """Mandatory justification when re-identifying a pseudonym or IP address (spec §12): ends up unchanged
    in the audit row of that re-identification (visible to every platform administrator who reads the
    audit log), and counts towards the daily limit on such re-identifications."""

    reason: str = Field(
        description=(
            f"Why this re-identification is needed, {REASON_MIN_LENGTH} to {REASON_MAX_LENGTH} characters. Give "
            "a case or ticket number, not an e-mail address or other personal data: this field ends up "
            "readable in the audit log, for every platform administrator."
        ),
        examples=["onderzoek naar melding 2026-091"],
    )

    @field_validator("reason")
    @classmethod
    def _reason_length(cls, value: str) -> str:
        normalised = value.strip()
        if not (REASON_MIN_LENGTH <= len(normalised) <= REASON_MAX_LENGTH):
            raise ValueError(
                f"reason must be {REASON_MIN_LENGTH} to {REASON_MAX_LENGTH} characters after stripping whitespace"
            )
        if _has_forbidden_characters(normalised):
            raise ValueError("reason must not contain control or formatting characters")
        if "@" in normalised:
            raise ValueError("give a case or ticket number in reason, not an email address")
        return normalised


class ActorLookup(ReasonField):
    """The identifier of an actor, to look up their audit pseudonym with."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"identifier": "lid@example.nl", "reason": "onderzoek naar melding 2026-091"}]
        }
    )

    identifier: str = Field(
        description=(
            "E-mail address or SSO subject of a member or content viewer, or a linked repository as "
            "`owner/repo`, `github.com/owner/repo` or `https://code.overheid.nl/owner/repo`. "
            "An e-mail address is first translated to the SSO subject of that member or viewer, because "
            "that is what is audited."
        ),
        examples=["lid@example.nl"],
    )


class ActorPseudonymOut(ApiModel):
    """The audit pseudonym that belongs to an identifier."""

    actor_pseudonym: str = Field(
        description="Value to use as `actorPseudonym` filter on `GET /platform/audit`."
    )
    resolved_as: Literal["member", "content_viewer", "ci", "unknown"] = Field(
        description=(
            "What the identifier belonged to: `member` (translated to their SSO subject), `content_viewer` "
            "(an SSO viewer of protected content, seen in the last 90 days), `ci` "
            "(a repository linked to a site) or `unknown` (pseudonymised as given, only for an "
            "identifier that is not an e-mail address: an e-mail address that belongs to nothing gives a 404)."
        )
    )


class ActorIdentityLookup(ReasonField):
    """An audit pseudonym, to look up the actor behind it."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"actorPseudonym": "a" * AUDIT_PSEUDONYM_LENGTH, "reason": "onderzoek naar melding 2026-091"}
            ]
        }
    )

    actor_pseudonym: str = Field(
        description=f"The pseudonym from the audit log: {AUDIT_PSEUDONYM_LENGTH} hexadecimal characters."
    )


class ActorIdentityOut(ApiModel):
    """The actor behind an audit pseudonym."""

    kind: Literal["member", "content_viewer", "ci"] = Field(
        description="Whether the pseudonym belongs to a member, a content viewer or a CI repository."
    )
    member_id: uuid.UUID | None = Field(default=None, description="Internal ID of the member, for kind `member`.")
    email: str | None = Field(
        default=None,
        description="E-mail address, for kind `member` or `content_viewer`; `null` if none is known.",
    )
    email_verified: bool | None = Field(
        default=None,
        description=(
            "Whether `email` was a verified claim of the identity provider, for kind `content_viewer`. "
            "The forward lookup (`actor-pseudonym`) only matches on a verified e-mail address."
        ),
    )
    name: str | None = Field(
        default=None,
        description="Display name of the member, for kind `member`; empty if the identity provider does not send it.",
    )
    member_status: MemberStatus | None = Field(default=None, description="Status of the member, for kind `member`.")
    last_seen_at: str | None = Field(
        default=None,
        description="Last sign-in on the content host, for kind `content_viewer`.",
        json_schema_extra=_timestamp_schema(),
    )
    provider: Literal["github", "forgejo"] | None = Field(
        default=None, description="CI provider of the repository, for kind `ci`."
    )
    host: str | None = Field(
        default=None,
        description="Base URL of the provider, for kind `ci`.",
        examples=["https://github.com"],
    )
    repository: str | None = Field(
        default=None, description="The repository as `owner/repo`, for kind `ci`.", examples=["minbzk/website"]
    )
    sites: list[str] | None = Field(
        default=None,
        description=(
            "The sites to which this repository is currently linked, as `group/site`, for kind `ci`. An "
            "unlinked repository can no longer be traced."
        ),
        examples=[["aurora/docs"]],
    )


class IpRevealBody(ReasonField):
    """Justification for decrypting the full IP address of one audit row."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"reason": "onderzoek naar melding 2026-091"}]}
    )


class IpRevealOut(ApiModel):
    """The full IP address behind one audit row."""

    ip: str = Field(description="The full IP address as stored with this audit row.")


# -- Serialization ----------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _member_json(member: Member, *, bootstrap_sub: str = "") -> MemberOut:
    return MemberOut(
        id=member.id,
        sso_subject=member.sso_subject,
        email=member.email,
        name=member.name or "",
        platform_role=member.platform_role,
        status=member.status,
        is_bootstrap=bool(bootstrap_sub) and member.sso_subject == bootstrap_sub,
        created_at=_iso(member.created_at) or "",
        last_login_at=_iso(member.last_login_at),
    )


def _group_json(group: Group) -> GroupOut:
    return GroupOut(
        slug=group.slug,
        name=group.name,
        default_access=AccessOut(
            base=group.default_access_base,
            keys=group.default_access_keys,
            invitees=group.default_access_invitees,
        ),
    )


def _site_json(
    site: Site,
    group_slug: str,
    last_published_at: datetime | None,
    preview_count: int,
) -> SiteOut:
    return SiteOut(
        group_slug=group_slug,
        slug=site.slug,
        title=site.title,
        access=AccessOut(base=site.access_base, keys=site.access_keys, invitees=site.access_invitees),
        external_sources=site.external_sources,
        sandbox=site.sandbox,
        live_versions_kept=site.live_versions_kept,
        live_version_id=site.live_version_id,
        created_by=str(site.created_by) if site.created_by else "",
        has_live_version=site.live_version_id is not None,
        last_published_at=_iso(last_published_at),
        preview_count=preview_count,
    )


def _version_json(
    version: Version, group_slug: str, site: Site, *, deployer: Member | None = None
) -> VersionOut:
    return VersionOut(
        id=version.id,
        site_slug=site.slug,
        group_slug=group_slug,
        target=version.target,
        storage_ref=version.storage_ref,
        origin="upload" if version.member_id is not None else "action",
        created_by_member=version.member_id,
        created_by_name=(deployer.name or deployer.email) if deployer is not None else None,
        created_by_repository=version.ci_repository,
        created_at=_iso(version.created_at),
        is_live=version.id == site.live_version_id,
    )


def _preview_json(preview: Preview, group_slug: str, site_slug: str) -> PreviewOut:
    return PreviewOut(
        site_slug=site_slug,
        group_slug=group_slug,
        ref=preview.ref,
        version_id=preview.version_id,
        access_override=(
            None
            if preview.access_base_override is None
            else AccessOut(
                base=preview.access_base_override,
                keys=bool(preview.access_keys_override),
                invitees=bool(preview.access_invitees_override),
            )
        ),
        last_updated_at=_iso(preview.last_updated_at),
        expires_at=_iso(preview.expires_at),
        url=f"/{group_slug}/{site_slug}/_preview/{preview.ref}/",
    )


def _invitee_json(invitee: Invitee, group_slug: str, site_slug: str) -> InviteeOut:
    return InviteeOut(
        id=str(invitee.id),
        site_slug=site_slug,
        group_slug=group_slug,
        identifier=invitee.identifier,
        added_by=str(invitee.added_by) if invitee.added_by else "",
        added_at=_iso(invitee.added_at),
    )


def _key_json(key: AccessKey, group_slug: str, site_slug: str) -> KeyOut:
    return KeyOut(
        site_slug=site_slug,
        group_slug=group_slug,
        label=key.label,
        selector=key.selector,
        status=key.status,
        created_at=_iso(key.created_at),
        expires_at=_iso(key.expires_at),
    )


def _group_member_json(
    group_slug: str, member: Member, role: Role, site_roles: list[GroupSiteRole]
) -> GroupMemberOut:
    return GroupMemberOut(
        group_slug=group_slug,
        member_id=str(member.id),
        identifier=member.email,
        name=member.name or "",
        email=member.email,
        role=role,
        site_roles=site_roles,
    )


def _site_member_json(
    group_slug: str,
    site_slug: str,
    member: Member,
    group_role: Role | None,
    site_role: Role | None,
) -> SiteMemberOut:
    """One row of a site member list.

    `group_role` and `site_role` are never both None: a row exists only for
    someone who reaches this site through at least one of the two.
    """
    return SiteMemberOut(
        group_slug=group_slug,
        site_slug=site_slug,
        member_id=str(member.id),
        identifier=member.email,
        name=member.name or "",
        email=member.email,
        group_role=group_role,
        site_role=site_role,
        effective_role=roles.widest(group_role, site_role),
    )


# -- Lookup and authorization helpers ---------------------------------------


def _audit_entry_json(entry: AuditLogEntry) -> AuditEntryOut:
    return AuditEntryOut(
        id=entry.id,
        occurred_at=_iso(entry.occurred_at),
        actor_kind=entry.actor_kind,
        actor_pseudonym=entry.actor_pseudonym,
        action=entry.action,
        result=entry.result,
        reason_code=entry.reason_code,
        refs=entry.refs,
        ip_truncated=entry.ip_truncated,
    )


async def _group_or_404(db: AsyncSession, slug: str) -> Group:
    group = await db.scalar(select(Group).where(Group.slug == slug))
    if group is None:
        raise ApiError(404, "UNKNOWN_GROUP")
    return group


async def _site_or_404(db: AsyncSession, group: Group, slug: str) -> Site:
    site = await db.scalar(
        select(Site).where(Site.group_id == group.id, Site.slug == slug)
    )
    if site is None:
        raise ApiError(404, "UNKNOWN_SITE")
    return site


async def _group_with_role(
    db: AsyncSession,
    member: Member,
    slug: str,
    minimum: Role,
    *,
    platform_admin: bool = False,
) -> Group:
    """Looks up the group and demands a group role of at least `minimum`.

    `platform_admin` lets a platformbeheerder through without a group role; it
    is set only on the actions about people and groups, never about content.
    """
    group = await _group_or_404(db, slug)
    if platform_admin and member.platform_role == PlatformRole.ADMIN:
        return group
    await require_group_role(db, member, group.id, minimum)
    return group


async def _site_with_role(
    db: AsyncSession, member: Member, group_slug: str, site_slug: str, minimum: Role
) -> tuple[Group, Site]:
    """Looks up group and site and demands an effective site role of at least
    `minimum`: the widest of group role and site role.

    The role cannot be computed without the site, so the lookup comes first.
    That would tell anyone with a session which sites exist in a group they
    have nothing to do with, so someone with no role at all gets the same 404
    as for a site that is not there.

    Two people are spared that: someone who does have a role and only a
    narrower one than this action needs, and a platformbeheerder. Both can
    already see that the site exists, the first from within the group and the
    second from the detail of any group, so hiding it from them protects
    nothing and only sends them looking for a mistake
    they did not make.
    """
    group = await _group_or_404(db, group_slug)
    site = await _site_or_404(db, group, site_slug)
    if (
        member.platform_role != PlatformRole.ADMIN
        and await roles.effective_site_role(db, site, member.id) is None
    ):
        raise ApiError(404, "UNKNOWN_SITE")
    await require_site_role(db, member, site, minimum)
    return group, site


def _require_admin(member: Member) -> None:
    if member.platform_role != PlatformRole.ADMIN:
        raise ApiError(403, KEY_ADMIN_ONLY)


def _refuse_self(target: Member, member: Member, what: str) -> None:
    """You cannot take your own platform rights away.

    Not paternalism but a lockout guard: the admin API is the only way back
    in, and a member who has just switched themselves off can no longer reach
    it. Someone else with the role does it, or you change the bootstrap
    setting.
    """
    if target.id == member.id:
        raise ApiError(409, f"SELF_NOT_ALLOWED.{what}")


def _refuse_bootstrap(request: Request, target: Member) -> None:
    """The bootstrap account is restored on every login (auth/members.py), so
    changing it here would look like it worked and be undone on the next
    request. Refusing says out loud what the honest fix is."""
    bootstrap_sub = request.app.state.settings.bootstrap_admin_sub
    if bootstrap_sub and target.sso_subject == bootstrap_sub:
        raise ApiError(409, "BOOTSTRAP_MEMBER")


async def _refuse_last_admin(db: AsyncSession, target: Member) -> None:
    """A platform without an active beheerder can never let anyone in again:
    activating a member is itself a beheerder action. So the last one stays."""
    if target.platform_role != PlatformRole.ADMIN or target.status != MemberStatus.ACTIVE:
        return
    remaining = await db.scalar(
        select(func.count())
        .select_from(Member)
        .where(
            Member.platform_role == PlatformRole.ADMIN,
            Member.status == MemberStatus.ACTIVE,
            Member.id != target.id,
        )
    )
    # _refuse_self already blocks target == caller, and the caller is always a
    # distinct active admin, so remaining never reaches zero over the API.
    if not remaining:  # pragma: no cover - unreachable, see above
        raise ApiError(409, "LAST_PLATFORM_ADMIN")


async def _is_last_group_member(db: AsyncSession, group_id: uuid.UUID, member_id: uuid.UUID) -> bool:
    """True when this member is the only one in the group. A group that empties
    out is unmanageable: adding someone requires membership yourself."""
    members = list(await db.scalars(select(GroupMember.member_id).where(GroupMember.group_id == group_id)))
    return members == [member_id]


@asynccontextmanager
async def _group_keeps_an_admin(db: AsyncSession) -> AsyncIterator[None]:
    """Turns the trigger ck_groups_keep_one_admin into a 409 instead of a 500.

    The trigger (migration 0001) raises a bare PL/pgSQL exception, so the
    sqlstate is all there is to recognise it by; nothing else these routes
    touch can raise that code. It is a constraint trigger INITIALLY IMMEDIATE
    and therefore fires at the end of the statement, not at commit, so both
    have to happen inside this block.
    """
    try:
        yield
    except DBAPIError as error:
        if getattr(error.orig, "sqlstate", None) != _SQLSTATE_RAISE_EXCEPTION:
            raise
        await db.rollback()
        raise ApiError(409, "LAST_GROUP_ADMIN") from None


def _validate_slug(slug: str, *, reserved_refused: bool = False) -> str:
    normalised = slug.strip()
    if not SLUG_RE.match(normalised) or (
        reserved_refused and normalised in RESERVED_SLUGS
    ):
        raise ApiError(422, "SLUG_INVALID", params={"slug": normalised})
    return normalised


def _validate_text(value: str, field: str) -> str:
    normalised = value.strip()
    if not normalised:
        raise ApiError(422, "FIELD_EMPTY", params={"field": field})
    if _has_forbidden_characters(normalised):
        raise ApiError(422, "FIELD_CONTROL_CHARACTERS", params={"field": field})
    return normalised


async def _audit(request: Request, member: Member, action: str, refs: dict) -> None:
    log: AuditLog | None = getattr(request.app.state, "audit_log", None)
    if log is None:
        return
    ip = net.client_ip_from_request(request)
    await log.write(
        action,
        Actor(ActorKind.MEMBER, member.sso_subject),
        "allowed",
        refs=refs,
        ip=ip,
    )


async def _audit_strict(request: Request, member: Member, action: str, refs: dict) -> None:
    """Fail-closed variant of `_audit`, for the de-anonymisation reads
    (actor-pseudonym, actor-identity, the audit log itself): the audit row is
    the only record that the read happened, so a failed write must not let
    the response through. Marks the request as already audited, so a later
    ApiError (e.g. an unknown pseudonym) does not also pick up a generic
    admin_access row from api/errors.py::_audit_refusal.
    """
    log: AuditLog | None = getattr(request.app.state, "audit_log", None)
    request.state.audit_written = True
    if log is None:
        raise ApiError(503, "AUDIT_UNAVAILABLE")
    ip = net.client_ip_from_request(request)
    try:
        await log.write_strict(
            action,
            Actor(ActorKind.MEMBER, member.sso_subject),
            "allowed",
            refs=refs,
            ip=ip,
        )
    except Exception as error:
        raise ApiError(503, "AUDIT_UNAVAILABLE") from error


async def _sites_json(db: AsyncSession, group: Group, sites: list[Site]) -> list[SiteOut]:
    ids = [site.id for site in sites]
    last: dict[uuid.UUID, datetime] = {}
    counts: dict[uuid.UUID, int] = {}
    if ids:
        last = dict(
            (
                await db.execute(
                    select(Version.site_id, func.max(Version.created_at))
                    .where(Version.site_id.in_(ids))
                    .group_by(Version.site_id)
                )
            ).all()
        )
        counts = dict(
            (
                await db.execute(
                    select(Preview.site_id, func.count())
                    .where(Preview.site_id.in_(ids))
                    .group_by(Preview.site_id)
                )
            ).all()
        )
    return [
        _site_json(site, group.slug, last.get(site.id), counts.get(site.id, 0))
        for site in sites
    ]


async def _group_sites(db: AsyncSession, group: Group) -> list[Site]:
    return list(
        await db.scalars(
            select(Site).where(Site.group_id == group.id).order_by(Site.slug)
        )
    )


async def _group_site_roles(
    db: AsyncSession, group: Group, member_id: uuid.UUID | None = None
) -> dict[uuid.UUID, list[GroupSiteRole]]:
    """Per member, the sites in this group they hold a role of their own on.

    `Site.group_id == group.id` is the whole point of this query and not an
    optimisation: a site role in another group would say where else in the
    organisation this person works, and an admin of this group has no business
    reading that here.
    """
    query = (
        select(SiteMember.member_id, Site.slug, Site.title, SiteMember.role)
        .join(Site, Site.id == SiteMember.site_id)
        .where(Site.group_id == group.id)
        .order_by(Site.slug)
    )
    if member_id is not None:
        query = query.where(SiteMember.member_id == member_id)
    per_member: dict[uuid.UUID, list[GroupSiteRole]] = {}
    for row_member_id, site_slug, site_title, role in await db.execute(query):
        per_member.setdefault(row_member_id, []).append(
            GroupSiteRole(site_slug=site_slug, site_title=site_title, role=role)
        )
    return per_member


async def _group_members_json(db: AsyncSession, group: Group) -> list[GroupMemberOut]:
    rows = await db.execute(
        select(Member, GroupMember.role)
        .join(GroupMember, GroupMember.member_id == Member.id)
        .where(GroupMember.group_id == group.id)
        .order_by(Member.email)
    )
    site_roles = await _group_site_roles(db, group)
    return [
        _group_member_json(group.slug, row.Member, row.role, site_roles.get(row.Member.id, []))
        for row in rows
    ]


async def _remove_site_roles_in_group(db: AsyncSession, group: Group, target: Member) -> list[str]:
    """Take away every site role this member holds on a site in this group, and
    report the slugs of the sites it really came off.

    Part of the caller's transaction and committed by the caller, so the group
    role and the site roles go together or not at all.

    `Site.group_id == group.id` is the boundary: a site role in another group is
    neither read nor written here.
    """
    in_group = dict(
        (
            await db.execute(
                select(SiteMember.site_id, Site.slug)
                .join(Site, Site.id == SiteMember.site_id)
                .where(Site.group_id == group.id, SiteMember.member_id == target.id)
            )
        ).all()
    )
    if not in_group:
        return []
    gone = await db.execute(
        delete(SiteMember)
        .where(SiteMember.member_id == target.id, SiteMember.site_id.in_(in_group))
        .returning(SiteMember.site_id)
    )
    # RETURNING rather than the list read above: a role somebody else took away
    # in the meantime is not ours to write an audit row about.
    return sorted(in_group[site_id] for site_id in gone.scalars())


async def _one_group_member(
    db: AsyncSession, group: Group, target: Member, role: Role
) -> GroupMemberOut:
    """The row for one member, built the same way the listing builds it, so an
    answer after a change can never disagree with the list."""
    site_roles = await _group_site_roles(db, group, target.id)
    return _group_member_json(group.slug, target, role, site_roles.get(target.id, []))


async def _one_site_member(
    db: AsyncSession, group: Group, site: Site, target: Member
) -> SiteMemberOut:
    """The row for one member, taken from the same listing the GET returns, so
    an answer after a change can never disagree with the list."""
    rows = await _site_members_json(db, group, site)
    return next(row for row in rows if row.identifier == target.email)


async def _site_members_json(db: AsyncSession, group: Group, site: Site) -> list[SiteMemberOut]:
    """Everyone who can reach this site, not just the rows in `site_members`.

    A list that showed only the site roles would leave out the group members
    who reach this site through the group, and read as if far fewer people had
    access than really do.

    The order is widest effective role first, alphabetically by e-mail within
    a role: the list gets opened to see who decides here, and inside a band a
    name stays findable.
    """
    group_roles = dict(
        (
            await db.execute(
                select(GroupMember.member_id, GroupMember.role).where(GroupMember.group_id == group.id)
            )
        ).all()
    )
    site_roles = dict(
        (
            await db.execute(
                select(SiteMember.member_id, SiteMember.role).where(SiteMember.site_id == site.id)
            )
        ).all()
    )
    identifiers = set(group_roles) | set(site_roles)
    # Both callers only reach here through require_site_role, which already
    # demands the caller be one of these identifiers.
    if not identifiers:  # pragma: no cover - unreachable, see above
        return []
    members = await db.scalars(select(Member).where(Member.id.in_(identifiers)))
    rows = [
        _site_member_json(
            group.slug, site.slug, row, group_roles.get(row.id), site_roles.get(row.id)
        )
        for row in members
    ]
    rows.sort(key=lambda row: (-ROLE_RANK[row.effective_role], row.email))
    return rows


async def _my_group_roles(db: AsyncSession, member: Member) -> list[MyGroupRole]:
    rows = await db.execute(
        select(Group.slug, GroupMember.role)
        .select_from(GroupMember)
        .join(Group, Group.id == GroupMember.group_id)
        .where(GroupMember.member_id == member.id)
        .order_by(Group.slug)
    )
    return [MyGroupRole(group_slug=slug, role=role) for slug, role in rows]


async def _my_site_roles(db: AsyncSession, member: Member) -> list[MySiteRole]:
    """Only the sites with a direct site role; everywhere else the group role stands on its own."""
    rows = await db.execute(
        select(Group.slug, Site.slug, SiteMember.role, GroupMember.role)
        .select_from(SiteMember)
        .join(Site, Site.id == SiteMember.site_id)
        .join(Group, Group.id == Site.group_id)
        .outerjoin(
            GroupMember,
            (GroupMember.group_id == Group.id) & (GroupMember.member_id == member.id),
        )
        .where(SiteMember.member_id == member.id)
        .order_by(Group.slug, Site.slug)
    )
    return [
        MySiteRole(
            group_slug=group_slug,
            site_slug=site_slug,
            role=site_role,
            effective_role=roles.widest(group_role, site_role),
        )
        for group_slug, site_slug, site_role, group_role in rows
    ]


async def _groups_for_member(
    db: AsyncSession, member: Member
) -> tuple[list[Group], dict[uuid.UUID, list[Site]]]:
    """The groups this member sees in the overview, with the sites each one shows.

    A group role shows the whole group. A site role alone shows that group with
    only the sites the member has a role on: a site role grants nothing at
    group level, so the rest of the group stays hidden.
    """
    group_ids = set(
        await db.scalars(select(GroupMember.group_id).where(GroupMember.member_id == member.id))
    )
    own_sites = await db.scalars(
        select(Site)
        .join(SiteMember, SiteMember.site_id == Site.id)
        .where(SiteMember.member_id == member.id)
        .order_by(Site.slug)
    )
    sites_per_group: dict[uuid.UUID, list[Site]] = {}
    for site in own_sites:
        if site.group_id not in group_ids:
            sites_per_group.setdefault(site.group_id, []).append(site)
    groups = list(
        await db.scalars(
            select(Group).where(Group.id.in_(group_ids | set(sites_per_group))).order_by(Group.slug)
        )
    )
    for group in groups:
        if group.id in group_ids:
            sites_per_group[group.id] = await _group_sites(db, group)
    return groups, sites_per_group


class IdentifierAmbiguousError(Exception):
    """Raised when an identifier matches more than one distinct subject.

    `sso_subject` carries a unique constraint, so an exact match on it is
    never ambiguous; neither `email` nor `name` does (on `members`, nor on
    `content_viewers` for email), so a lookup that falls back to either can
    genuinely hit more than one person. `via` says which step it was, so the
    409 can point at a way out that actually resolves it: the SSO-subject for
    an ambiguous email, the email for an ambiguous name.
    """

    def __init__(self, matches: int, *, via: Literal["email", "name"] = "email") -> None:
        self.matches = matches
        self.via = via
        super().__init__(f"{matches} different subjects for this identifier")


async def _find_member_by_identifier(db: AsyncSession, identifier: str) -> Member | None:
    """Exact sso_subject match wins; only when there is none does an email
    match count; only when there is neither does an exact, case-insensitive
    name match on exactly one ACTIVE member count. Only the email or the name
    step can be ambiguous (`IdentifierAmbiguousError`).

    Not used for the audit de-anonymisation lookup (`_resolve_lookup_identifier`):
    a name is not an identifier you should be able to guess your way through an
    actor pseudonym with, unlike a group or site membership where the person
    doing the adding already has to know who they mean.
    """
    normalised = identifier.strip()
    exact = await db.scalar(select(Member).where(Member.sso_subject == normalised))
    if exact is not None:
        return exact
    matches = list(await db.scalars(select(Member).where(Member.email == normalised.lower())))
    if len(matches) > 1:
        raise IdentifierAmbiguousError(len(matches), via="email")
    if matches:
        return matches[0]
    name_matches = list(
        await db.scalars(
            select(Member).where(
                Member.status == MemberStatus.ACTIVE,
                Member.name.isnot(None),
                func.lower(Member.name) == normalised.lower(),
            )
        )
    )
    if len(name_matches) > 1:
        raise IdentifierAmbiguousError(len(name_matches), via="name")
    return name_matches[0] if name_matches else None


async def _find_member_or_409(db: AsyncSession, identifier: str) -> Member | None:
    """Same resolution as `_find_member_by_identifier`, turning an ambiguous
    email or name match into a 409 so callers do not each have to handle it."""
    try:
        return await _find_member_by_identifier(db, identifier)
    except IdentifierAmbiguousError as error:
        key = (
            "IDENTIFIER_AMBIGUOUS.member_email"
            if error.via == "email"
            else "IDENTIFIER_AMBIGUOUS.member_name"
        )
        raise ApiError(409, key) from error


async def _match_member_by_pseudonym(db: AsyncSession, pepper: str, pseudonym: str) -> Member | None:
    """Linear scan for the reverse actor lookup: only id and sso_subject are
    pulled for the comparison, so the full row is fetched once, for the one
    id that actually matches."""
    rows = await db.execute(select(Member.id, Member.sso_subject))
    for member_id, sso_subject in rows:
        if hmac.compare_digest(pseudonymise(pepper, sso_subject), pseudonym):
            return await db.get(Member, member_id)
    return None


async def _match_content_viewer_by_pseudonym(db: AsyncSession, pepper: str, pseudonym: str) -> ContentViewer | None:
    """Same shape as `_match_member_by_pseudonym`, for content viewers."""
    rows = await db.execute(select(ContentViewer.id, ContentViewer.sso_subject))
    for viewer_id, sso_subject in rows:
        if hmac.compare_digest(pseudonymise(pepper, sso_subject), pseudonym):
            return await db.get(ContentViewer, viewer_id)
    return None


def _ci_subjects(repository: SiteRepository) -> tuple[str, str]:
    """Both identifiers a CI actor for this repository can have been audited
    under: the stored id (every accepted deploy) and `owner/repo` in lowercase
    (a refused token without ids, see ci/trust.py)."""
    return (
        ci_actor_identifier(repository.provider, repository.host, repository.repository_id),
        ci_actor_identifier(repository.provider, repository.host, f"{repository.owner}/{repository.repo}".lower()),
    )


async def _match_ci_by_pseudonym(
    db: AsyncSession, pepper: str, pseudonym: str
) -> tuple[SiteRepository, list[str]] | None:
    """Same shape as `_match_member_by_pseudonym`, over the linked
    repositories; returns the first match plus every site it is linked to."""
    rows = list(
        (
            await db.execute(
                select(SiteRepository, Group.slug, Site.slug)
                .join(Site, Site.id == SiteRepository.site_id)
                .join(Group, Group.id == Site.group_id)
                .order_by(Group.slug, Site.slug)
            )
        ).all()
    )
    for repository, _, _ in rows:
        if any(hmac.compare_digest(pseudonymise(pepper, subject), pseudonym) for subject in _ci_subjects(repository)):
            sites = [
                f"{group_slug}/{site_slug}"
                for other, group_slug, site_slug in rows
                if (other.provider, other.host, other.repository_id)
                == (repository.provider, repository.host, repository.repository_id)
            ]
            return repository, sites
    return None


def _parse_repository_identifier(identifier: str) -> tuple[str | None, str] | None:
    """`owner/repo`, `host/owner/repo` or `https://host/owner/repo` into
    (host or None, `owner/repo` in lowercase)."""
    value = identifier.strip().removeprefix("https://").removeprefix("http://").strip("/")
    parts = value.split("/")
    if len(parts) == 2 and all(parts):
        return None, "/".join(parts).lower()
    if len(parts) == 3 and all(parts) and "." in parts[0]:
        return parts[0].lower(), "/".join(parts[1:]).lower()
    return None


async def _resolve_ci_identifier(db: AsyncSession, identifier: str) -> str | None:
    """The CI subject of a linked repository named by `identifier`, or None.
    More than one distinct repository (same name on two providers) is
    `IdentifierAmbiguousError`."""
    parsed = _parse_repository_identifier(identifier)
    if parsed is None:
        return None
    host, name = parsed
    subjects = {
        _ci_subjects(repository)[0]
        for repository in await db.scalars(select(SiteRepository))
        if f"{repository.owner}/{repository.repo}".lower() == name
        and (host is None or host_label(repository.host).lower() == host)
    }
    if len(subjects) > 1:
        raise IdentifierAmbiguousError(len(subjects))
    return next(iter(subjects), None)


async def _resolve_lookup_identifier(db: AsyncSession, identifier: str) -> tuple[str, str] | None:
    """Resolves an identifier for `actor-pseudonym`: an exact sso_subject
    match (member or content viewer) wins outright - the same sub in both
    tables is one person, not an ambiguity, member takes priority for the
    reported kind. Only without one does an email match count, across
    members and verified content viewers combined and deduplicated by
    subject (an unverified content-viewer email is never a match):
    more than one distinct subject there is `IdentifierAmbiguousError`.

    Returns `(resolved_as, subject)`, or `None` when nothing at all matched
    (the caller then tries a linked repository, or treats it as unknown).
    """
    normalised = identifier.strip()

    exact_member = await db.scalar(select(Member).where(Member.sso_subject == normalised))
    if exact_member is not None:
        return "member", exact_member.sso_subject
    exact_viewer = await db.scalar(select(ContentViewer).where(ContentViewer.sso_subject == normalised))
    if exact_viewer is not None:
        return "content_viewer", exact_viewer.sso_subject

    email = normalised.lower()
    member_matches = list(await db.scalars(select(Member.sso_subject).where(Member.email == email)))
    viewer_matches = list(
        await db.scalars(
            select(ContentViewer.sso_subject).where(
                ContentViewer.email == email, ContentViewer.email_verified.is_(True)
            )
        )
    )
    distinct_subjects = set(member_matches) | set(viewer_matches)
    if len(distinct_subjects) > 1:
        raise IdentifierAmbiguousError(len(distinct_subjects))
    if member_matches:
        return "member", member_matches[0]
    if viewer_matches:
        return "content_viewer", viewer_matches[0]
    return None


# Actions that a de-anonymisation counts against the daily lookup limit
# (PLAK_AUDIT_LOOKUP_DAILY_LIMIT): reading the audit log itself does not.
_LOOKUP_ACTIONS = (vocabulary.AUDIT_ACTOR_LOOKUP, vocabulary.AUDIT_ACTOR_IDENTITY, vocabulary.AUDIT_IP_REVEAL)


async def _audit_strict_limited(request: Request, member: Member, action: str, refs: dict) -> None:
    """Fail-closed and atomically capped variant of `_audit_strict`, for the
    three de-anonymisation actions: writes exactly one row for this call, or
    raises 429 `LOOKUP_LIMIT_REACHED` without writing one (the generic
    admin_access/refused row from `api/errors.py` covers that refusal
    instead), or 503 if the write itself fails. See
    `AuditLog.write_strict_limited` for how the count and the write are kept
    from racing (TOCTOU)."""
    request.state.audit_written = True
    log: AuditLog | None = getattr(request.app.state, "audit_log", None)
    if log is None:
        raise ApiError(503, "AUDIT_UNAVAILABLE")
    settings = request.app.state.settings
    ip = net.client_ip_from_request(request)
    try:
        await log.write_strict_limited(
            action,
            Actor(ActorKind.MEMBER, member.sso_subject),
            "allowed",
            refs=refs,
            ip=ip,
            limit=settings.audit_lookup_daily_limit,
            counted_actions=_LOOKUP_ACTIONS,
        )
    except LookupLimitReachedError as error:
        # No row landed for this attempt: let the generic admin_access/refused
        # handler in api/errors.py audit the refusal instead.
        request.state.audit_written = False
        raise ApiError(429, "LOOKUP_LIMIT_REACHED") from error
    except Exception as error:
        raise ApiError(503, "AUDIT_UNAVAILABLE") from error


SEARCH_MINIMUM = 2
SEARCH_LIMIT = 10


async def _search_members(
    db: AsyncSession,
    term: str,
    already: set[uuid.UUID],
    group_roles: Mapping[uuid.UUID, Role] | None = None,
) -> list[MemberSearchOut]:
    """Active members whose name or e-mail contains `term`, as a shortlist.

    The floor under the term keeps an empty query from handing out the whole
    directory, and the cap keeps the answer a shortlist instead of an export.
    Whoever already has a role here stays in the answer: saying so beats
    leaving the row out, which reads as if the search were broken.

    `already` holds whoever has a role at this very level, and is the only
    thing that rules someone out. `group_roles` is what the site search adds
    on top: the role a member already reaches this site with through the
    group, which narrows or widens nothing but tells the admin whether a site
    role would add anything.
    """
    normalised = term.strip()
    if len(normalised) < SEARCH_MINIMUM:
        raise ApiError(422, "SEARCH_TOO_SHORT", params={"minimum": SEARCH_MINIMUM})
    # name is nullable and goes over the wire as "", so the sort uses the same
    # substitution; otherwise a nameless member lands after every named one.
    rows = await db.scalars(
        select(Member)
        .where(
            Member.status == MemberStatus.ACTIVE,
            Member.name.icontains(normalised, autoescape=True)
            | Member.email.icontains(normalised, autoescape=True),
        )
        .order_by(func.coalesce(Member.name, ""), Member.email)
        .limit(SEARCH_LIMIT)
    )
    return [
        MemberSearchOut(
            identifier=row.email,
            name=row.name or "",
            email=row.email,
            already_member=row.id in already,
            group_role=(group_roles or {}).get(row.id),
        )
        for row in rows
    ]


# Newest first, with the id behind it, so two rows from the same transaction
# time keep a fixed order and the cursor can neither skip nor repeat one.
_AUDIT_ORDER = (AuditLogEntry.occurred_at.desc(), AuditLogEntry.id.desc())

_HEX = "0123456789abcdef"


def _audit_cursor(entry: AuditLogEntry) -> str:
    raw = f"{_iso(entry.occurred_at)}|{entry.id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _audit_cursor_read(cursor: str) -> tuple[datetime, uuid.UUID]:
    # binascii.Error and UnicodeDecodeError are both ValueError, as are
    # fromisoformat and UUID(), so one except catches every unreadable cursor.
    try:
        raw = base64.urlsafe_b64decode(cursor).decode("utf-8")
        timestamp, _, identifier = raw.partition("|")
        moment = _require_aware(datetime.fromisoformat(timestamp))
        return moment, uuid.UUID(identifier)
    except ValueError as error:
        raise ApiError(422, "CURSOR_INVALID") from error


def _audit_pseudonym(value: str) -> str:
    normalised = value.strip().lower()
    if len(normalised) != AUDIT_PSEUDONYM_LENGTH or not all(char in _HEX for char in normalised):
        raise ApiError(
            422, "ACTOR_PSEUDONYM_INVALID", params={"length": AUDIT_PSEUDONYM_LENGTH}
        )
    return normalised


def _ingest_service(request: Request) -> IngestService:
    return IngestService(request.app.state.content_store, request.app.state.settings)


def _content_base(request: Request) -> str:
    """Origin the SPA builds content URLs on (public URL, secret and preview
    links). PLAK_CONTENT_BASE_URL is required everywhere, so there is one host
    to name here and no fallback to guess at."""
    return request.app.state.settings.content_base_url.rstrip("/")


def _ci_audience(request: Request) -> str:
    """What a CI workflow asks its ID token for; PLAK_BASE_URL exactly, or the
    request origin in dev, where CI tokens are refused anyway."""
    base_url = request.app.state.settings.base_url
    return base_url or str(request.base_url).rstrip("/")


def _live_branch(value: str | None) -> str | None:
    """A branch name as git allows it, minus the edge cases that would only
    ever be a mistake here; empty means every branch."""
    if value is None or not value.strip():
        return None
    branch = value.strip()
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-")
    if (
        len(branch) > LIVE_BRANCH_MAX_LENGTH
        or not set(branch) <= allowed
        or branch.startswith(("/", "-", ".", "refs/"))
        or branch.endswith(("/", ".", ".lock"))
        or ".." in branch
        or "//" in branch
    ):
        raise ApiError(422, "LIVE_BRANCH_INVALID")
    return branch


# site_repositories stores both ids as a BigInteger.
_MAX_PROVIDER_ID = 2**63 - 1


def _entered_ids(body: SiteRepositoryBody) -> tuple[int, int] | None:
    """The repository and owner id the admin entered, both or neither."""
    repository_id, owner_id = body.repository_id, body.owner_id
    if repository_id is None and owner_id is None:
        return None
    if (
        repository_id is None
        or owner_id is None
        or not 0 < repository_id <= _MAX_PROVIDER_ID
        or not 0 < owner_id <= _MAX_PROVIDER_ID
    ):
        raise ApiError(422, "REPOSITORY_IDS_INVALID")
    return repository_id, owner_id


def _lookup_failed(error: RepositoryNotFoundError | ProviderUnavailableError, where: dict[str, str]) -> ApiError:
    if isinstance(error, RepositoryNotFoundError):
        return ApiError(422, "REPOSITORY_NOT_FOUND", params=where)
    if error.rate_limited:
        return ApiError(503, "CI_PROVIDER_RATE_LIMITED", params={"host": where["host"]})
    return ApiError(503, "CI_PROVIDER_UNREACHABLE.lookup", params={"host": where["host"]})


def _repository_host(request: Request, provider: CiProvider, host: str | None) -> str:
    if provider == CiProvider.GITHUB:
        if host is not None and host.strip().rstrip("/").lower() != GITHUB_HOST:
            raise ApiError(422, "HOST_NOT_ALLOWED.github")
        return GITHUB_HOST
    try:
        normalised = normalise_https_base_url(host or "", "host")
    except ValueError:
        normalised = None
    if normalised is None or normalised not in request.app.state.settings.forgejo_hosts:
        raise ApiError(422, "HOST_NOT_ALLOWED")
    return normalised


async def _repository_json(db: AsyncSession, repository: SiteRepository, group_slug: str, site_slug: str):
    creator = await db.get(Member, repository.created_by) if repository.created_by else None
    return SiteRepositoryOut(
        group_slug=group_slug,
        site_slug=site_slug,
        provider=repository.provider,
        host=repository.host,
        owner=repository.owner,
        repo=repository.repo,
        repository_id=repository.repository_id,
        owner_id=repository.owner_id,
        live_branch=repository.live_branch,
        ids_confirmed=repository.ids_confirmed,
        created_by=(creator.name or creator.email) if creator is not None else "",
        created_at=_iso(repository.created_at),
    )


# A fresh login, not just a valid one, may approve a CLI: the approval hands
# out a 90-day credential, so a session left open on a shared machine is not
# enough.
CLI_APPROVAL_MAX_SESSION_AGE_S = 15 * 60
# Per member, every lookup, approval and denial counts; guessing user codes
# means many lookups.
CLI_APPROVAL_MAX_ATTEMPTS = 20
CLI_APPROVAL_WINDOW_S = 600


async def _require_fresh_approval(request: Request, member: Member) -> None:
    session = sessions.session_from_request(request)
    if session is None:  # pragma: no cover - require_active_member has already refused this
        raise ApiError(401, KEY_NO_SESSION)
    # A login another site navigated the browser into does not count as fresh:
    # the IdP returns without a prompt on an existing SSO session, so a
    # phishing page could otherwise re-arm this window itself.
    if not session.self_initiated:
        raise ApiError(401, "SESSION_NOT_FRESH")
    if (datetime.now(UTC) - session.created_at).total_seconds() > CLI_APPROVAL_MAX_SESSION_AGE_S:
        raise ApiError(401, "SESSION_NOT_FRESH")
    counter = getattr(request.app.state, "cli_approval_counter", None)
    if counter is None:
        counter = InMemoryCounter()
        request.app.state.cli_approval_counter = counter
    result = await counter.increment(f"member:{member.id}", CLI_APPROVAL_WINDOW_S, time.monotonic())
    if result.count > CLI_APPROVAL_MAX_ATTEMPTS:
        raise ApiError(429, "TOO_MANY_ATTEMPTS")


def _unknown_user_code() -> ApiError:
    return ApiError(404, "USER_CODE_UNKNOWN")


# Per member, groups and sites together; every attempt that gets past the
# role check and the input validation counts, a slug collision included.
CREATION_MAX_PER_WINDOW = 20
CREATION_WINDOW_S = 3600


@dataclass(frozen=True)
class Creator:
    """Who creates a group or a site, or links a repository: the member, plus
    the CLI session when the request came with a CLI token instead of an admin
    session."""

    member: Member
    cli_session_id: uuid.UUID | None = None

    def audit_refs(self) -> dict:
        if self.cli_session_id is None:
            return {}
        return {"via": vocabulary.VIA_CLI, "cli_session": str(self.cli_session_id)}


async def require_creator(request: Request) -> Creator:
    """The two creation routes and the repository link take an admin session
    with CSRF, exactly as before, or a CLI access token. A bearer header
    decides: with one, the session cookie is not looked at, and neither is
    CSRF, since nothing ambient came along. A CI ID token is site-bound and
    creates or links nothing."""
    plaintext = bearer_from_request(request)
    if plaintext is None:
        await require_csrf(request)
        return Creator(member=await require_active_member(request))
    if not plaintext.startswith(cli.ACCESS_TOKEN_PREFIX + "_"):
        raise ApiError(401, "TOKEN_INVALID.cli_only", headers=WWW_AUTHENTICATE_BEARER)
    async with request.app.state.session_factory() as db:
        session, member = await cli_member(request, db, plaintext)
    creator = Creator(member=member, cli_session_id=session.id)
    # A refusal after this point (role, rate limit) is audited by
    # api/errors.py, which cannot see this member through a session cookie.
    request.state.audit_actor = Actor(ActorKind.MEMBER, member.sso_subject)
    request.state.audit_refs = creator.audit_refs()
    return creator


async def _require_creation_budget(request: Request, member: Member) -> None:
    counter = getattr(request.app.state, "creation_counter", None)
    if counter is None:
        counter = InMemoryCounter()
        request.app.state.creation_counter = counter
    result = await counter.increment(f"member:{member.id}", CREATION_WINDOW_S, time.monotonic())
    if result.count > CREATION_MAX_PER_WINDOW:
        raise ApiError(
            429,
            "TOO_MANY_CREATIONS",
            params={"limit": CREATION_MAX_PER_WINDOW},
            headers={"Retry-After": str(max(1, math.ceil(result.remaining_s)))},
        )


def _chosen_access(
    choice: AccessChoice | None, base: AccessBase, keys: bool, invitees: bool
) -> tuple[AccessBase, bool, bool]:
    """The access asked for, with every field left out taken from the default."""
    if choice is None:
        return base, keys, invitees
    return (
        choice.base if choice.base is not None else base,
        choice.keys if choice.keys is not None else keys,
        choice.invitees if choice.invitees is not None else invitees,
    )


def _access_refs(base: AccessBase, keys: bool, invitees: bool) -> dict:
    return {"base": str(base), "keys": keys, "invitees": invitees}


# -- Router -----------------------------------------------------------------


_ERROR_REPOSITORY_NOT_SET = {404: "No repository is linked to this site (`REPOSITORY_NOT_SET`)."}
_ERROR_APPROVAL = {
    401: (
        "The admin session is more than fifteen minutes old (`SESSION_NOT_FRESH`): sign in again and try "
        "again."
    ),
    404: "The code is unknown, expired or already handled (`USER_CODE_UNKNOWN`).",
    429: "Too many attempts by this member in a short time (`TOO_MANY_ATTEMPTS`).",
}
_ERROR_CLI_TOKEN = {
    401: (
        "With a Bearer header: it is not a CLI token from `plak login`, or it is invalid, revoked or "
        "expired (`TOKEN_INVALID`); the response then carries `WWW-Authenticate: Bearer`."
    ),
    403: "With a CLI token: the member is not (or no longer) active (`MEMBER_NOT_ACTIVE`).",
}
_ERROR_CREATIONS = {
    429: (
        f"This member has already created {CREATION_MAX_PER_WINDOW} groups and sites combined in the past "
        "hour (`TOO_MANY_CREATIONS`); the `Retry-After` header says after how many seconds it is allowed "
        "again."
    )
}
_CREATION_RULE = (
    "Besides the admin session with a valid CSRF header, this is also allowed with a CLI token from "
    "`plak login` (`Authorization: Bearer plakcli_...`), with exactly the same role check; the CSRF header "
    "is then not needed, because a token is not sent along automatically the way a cookie is. A CI ID "
    "token is not allowed. "
    f"Each member has a limit of {CREATION_MAX_PER_WINDOW} new groups and sites combined per "
    f"{CREATION_WINDOW_S // 60} minutes, across the admin interface and the CLI together."
)
_APPROVAL_RULE = (
    "**Who can call this:** any active member, with an admin session no older than fifteen minutes and a "
    "valid CSRF header. Per member, looking up, approving and denying together count towards a limit of "
    f"{CLI_APPROVAL_MAX_ATTEMPTS} per {CLI_APPROVAL_WINDOW_S // 60} minutes."
)


def make_admin_router() -> APIRouter:
    router = APIRouter(prefix="/-/api/v1", dependencies=[Depends(require_admin_origin)])

    # -- Session --

    @router.get(
        "/me",
        tags=[TAG_SESSION],
        summary="The signed-in member",
        response_description="The member behind the current session, with the content origin.",
        description=(
            "Returns the member behind the current admin session, plus the origin on which the SPA builds "
            "content links, preview links and secret links. This is also the cheapest way to check whether the "
            "session is still valid.\n\n"
            "`groupRoles` and `siteRoles` say where this member is allowed to do something, so the SPA knows "
            "what to offer. `siteRoles` only contains the sites with a direct site role; on every other "
            "site of a group the group role from `groupRoles` applies. A platform administrator can have both "
            "lists empty: they manage people and groups, and give themselves a group role to make content "
            "visible to them.\n\n"
            "**Who can call this:** any active member with a valid admin session. A member with status "
            "`deactivated` gets 403, as on every other endpoint."
        ),
        responses=_errors(),
    )
    async def me(request: Request, member: ActiveMember, db: Db) -> MyProfile:
        return MyProfile(
            **_member_json(member).model_dump(),
            content_base_url=_content_base(request),
            group_roles=await _my_group_roles(db, member),
            site_roles=await _my_site_roles(db, member),
            ci_forgejo_hosts=list(request.app.state.settings.forgejo_hosts),
            ci_audience=_ci_audience(request),
            language=member.language,
        )

    @router.put(
        "/me/language",
        status_code=204,
        tags=[TAG_SESSION],
        summary="Set my language",
        description=(
            "Records the language in which this member wants to read the admin interface: `nl`, `en`, or "
            "`null` to leave the language to the browser again. The choice is stored on the account, not on "
            "the device, so it applies everywhere this member signs in.\n\n"
            "The SPA then sends this language in `Accept-Language`, so problem+json messages "
            "come back in it too.\n\n"
            "**Who can call this:** any active member, for themselves, with a valid CSRF header."
        ),
        responses=_deleted("The language choice has been recorded.")
        | _errors(_ERROR_CSRF, {422: "`language` is not a supported language and not `null`."}),
    )
    async def set_my_language(
        request: Request, body: LanguageUpdate, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        # `member` comes from require_active_member, whose session is already
        # closed, so it is detached here: a statement rather than an attribute
        # assignment is what reaches the row from this session.
        await db.execute(update(Member).where(Member.id == member.id).values(language=body.language))
        await db.commit()
        await _audit(
            request,
            member,
            vocabulary.MEMBER_LANGUAGE,
            {"language": body.language.value if body.language else None},
        )
        return Response(status_code=204)

    # -- Overview and groups --

    @router.get(
        "/overview",
        tags=[TAG_OVERVIEW],
        summary="All visible groups with their sites",
        response_description="The groups this member is allowed to see, each with its sites.",
        description=(
            "The home screen of the admin SPA: per group the sites with their access, whether anything "
            "is live, when something was last deployed and how many previews are open.\n\n"
            "**Who can call this:** any active member. The member sees the groups in which they have a group role, "
            "each with all its sites, plus the groups "
            "in which they only have a site role: of those only the sites in question appear, because a "
            "site role grants nothing at group level. A platform administrator likewise only sees their own "
            "groups. Anyone without a role anywhere gets an empty list, not a 403."
        ),
        responses=_errors(),
    )
    async def overview(member: ActiveMember, db: Db) -> Overview:
        groups, sites_per_group = await _groups_for_member(db, member)
        rows = [
            GroupRow(
                group=_group_json(group),
                sites=await _sites_json(db, group, sites_per_group[group.id]),
            )
            for group in groups
        ]
        return Overview(groups=rows)

    @router.post(
        "/groups",
        status_code=201,
        tags=[TAG_GROUPS],
        summary="Create a group",
        response_description="The created group.",
        description=(
            "Creates a group and makes the creator group admin (`admin`) right away, so they can put sites "
            "in it. The new group starts with base `site_team` and no exceptions, unless "
            "`defaultAccess` asks for something else; that can be changed afterwards. Because the creator "
            "becomes group admin, choosing the default access right away is no more than they may do "
            "afterwards anyway.\n\n"
            "**Who can call this:** any active member, with a valid CSRF header. Creating a group is not a "
            "reserved action: anyone who wants to publish something must be able to make a place for it "
            "themselves. "
            + _CREATION_RULE
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_CLI_TOKEN,
            _ERROR_CREATIONS,
            {409: "A group with this slug already exists (`SLUG_EXISTS`)."},
            {
                422: (
                    "The slug is invalid or reserved (`SLUG_INVALID`), or the name is empty "
                    "(`FIELD_EMPTY`)."
                )
            },
        ),
    )
    async def create_group(
        request: Request, body: GroupCreate, creator: Annotated[Creator, Depends(require_creator)], db: Db
    ) -> GroupOut:
        member = creator.member
        slug = _validate_slug(body.slug, reserved_refused=True)
        name = _validate_text(body.name, "name")
        base, keys, invitees = _chosen_access(body.default_access, AccessBase.SITE_TEAM, False, False)
        await _require_creation_budget(request, member)
        group = Group(
            slug=slug,
            name=name,
            default_access_base=base,
            default_access_keys=keys,
            default_access_invitees=invitees,
        )
        db.add(group)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise ApiError(409, "SLUG_EXISTS.group", params={"slug": slug}) from None
        # The creator becomes group beheerder in the same transaction, otherwise
        # they cannot create a site in their own fresh group (403).
        db.add(GroupMember(group_id=group.id, member_id=member.id, role=Role.ADMIN))
        await db.commit()
        await _audit(
            request,
            member,
            "group_create",
            {"group": slug, **_access_refs(base, keys, invitees), **creator.audit_refs()},
        )
        return _group_json(group)

    @router.get(
        "/groups/{group_slug}",
        tags=[TAG_GROUPS],
        summary="Group with sites and members",
        response_description="The group with its sites and members.",
        description=(
            "Everything the group page of the SPA needs in one go.\n\n"
            "**Who can call this:** group role `reader` or higher, or a platform administrator."
        ),
        responses=_errors(_ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def group_detail(group_slug: str, member: ActiveMember, db: Db) -> GroupDetail:
        group = await _group_with_role(db, member, group_slug, Role.READER, platform_admin=True)
        sites = await _group_sites(db, group)
        return GroupDetail(
            group=_group_json(group),
            sites=await _sites_json(db, group, sites),
            members=await _group_members_json(db, group),
        )

    @router.delete(
        "/groups/{group_slug}",
        status_code=204,
        tags=[TAG_GROUPS],
        summary="Delete a group",
        description=(
            "Deletes the group with all its sites, and per site everything `DELETE /sites/{group}/{site}` "
            "also removes: versions, previews, invitees, secret links, the linked repository and the "
            "unpacked files on disk. Irreversible; every URL of the group returns 404 afterwards.\n\n"
            "**Who can call this:** group role `admin`, with a valid CSRF header. A platform administrator without "
            "a group role is not allowed."
        ),
        responses=_deleted("The group and all its sites have been deleted.")
        | _errors(_ERROR_CSRF, _ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def delete_group(
        request: Request, group_slug: str, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN)
        site_slugs = list(
            await db.scalars(select(Site.slug).where(Site.group_id == group.id).order_by(Site.slug))
        )
        storage_refs = list(
            await db.scalars(
                select(Version.storage_ref)
                .join(Site, Version.site_id == Site.id)
                .where(Site.group_id == group.id)
            )
        )
        await db.delete(group)
        await db.commit()
        store = request.app.state.content_store
        for storage_ref in storage_refs:
            store.delete_version(storage_ref)
        await _audit(request, member, "group_delete", {"group": group_slug, "sites": site_slugs})
        return Response(status_code=204)

    @router.put(
        "/groups/{group_slug}/default-access",
        tags=[TAG_GROUPS],
        summary="Set the default access of a group",
        response_description="The group with its new default access.",
        description=(
            "Sets the access that new sites in this group start with: the "
            "base and the two exceptions in one go. Existing sites are not affected; "
            "you set those per site.\n\n"
            "**Who can call this:** group role `admin`, with a valid CSRF header. This is policy about content, so "
            "a platform administrator without a group role is not allowed."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def set_default_access(
        request: Request, group_slug: str, body: AccessBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> GroupOut:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN)
        group.default_access_base = body.base
        group.default_access_keys = body.keys
        group.default_access_invitees = body.invitees
        await db.commit()
        await _audit(
            request,
            member,
            "group_default_visibility",
            {
                "group": group_slug,
                "base": str(body.base),
                "keys": body.keys,
                "invitees": body.invitees,
            },
        )
        return _group_json(group)

    # -- Sites --

    @router.post(
        "/groups/{group_slug}/sites",
        status_code=201,
        tags=[TAG_SITES],
        summary="Create a site",
        response_description="The created site.",
        description=(
            "Creates a site within a group. The site gets the default access of the group, or whatever "
            "`access` asks for instead, and is served at `/{groupSlug}/{siteSlug}/` on the content origin, "
            "as soon as something has been deployed to it.\n\n"
            "**Who can call this:** group role `editor` or higher, with a valid CSRF header; the creator becomes "
            "`admin` of the site they create, and may therefore choose the access right away: that is no "
            "more than they may do afterwards anyway. A platform administrator without a group role is not "
            "allowed. " + _CREATION_RULE
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_CLI_TOKEN,
            _ERROR_CREATIONS,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            {409: "A site with this slug already exists in this group (`SLUG_EXISTS`)."},
            {422: "The slug is invalid (`SLUG_INVALID`), or the title is empty (`FIELD_EMPTY`)."},
        ),
    )
    async def create_site(
        request: Request,
        group_slug: str,
        body: SiteCreate,
        creator: Annotated[Creator, Depends(require_creator)],
        db: Db,
    ) -> SiteOut:
        member = creator.member
        group = await _group_with_role(db, member, group_slug, Role.EDITOR)
        slug = _validate_slug(body.slug)
        title = _validate_text(body.title, "title")
        base, keys, invitees = _chosen_access(
            body.access, group.default_access_base, group.default_access_keys, group.default_access_invitees
        )
        await _require_creation_budget(request, member)
        site = Site(
            group_id=group.id,
            slug=slug,
            title=title,
            access_base=base,
            access_keys=keys,
            access_invitees=invitees,
            created_by=member.id,
        )
        db.add(site)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise ApiError(409, "SLUG_EXISTS.site", params={"slug": slug}) from None
        # The creator becomes site beheerder of what they create.
        db.add(
            SiteMember(
                site_id=site.id, member_id=member.id, role=Role.ADMIN, added_by=member.id
            )
        )
        await db.commit()
        await _audit(
            request,
            member,
            "site_create",
            {"group": group_slug, "site": slug, **_access_refs(base, keys, invitees), **creator.audit_refs()},
        )
        return _site_json(site, group.slug, None, 0)

    @router.delete(
        "/sites/{group_slug}/{site_slug}",
        status_code=204,
        tags=[TAG_SITES],
        summary="Delete a site",
        description=(
            "Deletes the site with everything attached to it: versions, previews, invitees, secret links, "
            "the linked repository and the unpacked files on disk. Irreversible; the URL returns 404 afterwards.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_deleted("The site and all its content have been deleted.")
        | _errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def delete_site(
        request: Request, group_slug: str, site_slug: str, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        storage_refs = list(
            await db.scalars(select(Version.storage_ref).where(Version.site_id == site.id))
        )
        await db.delete(site)
        await db.commit()
        store = request.app.state.content_store
        for storage_ref in storage_refs:
            store.delete_version(storage_ref)
        await _audit(request, member, "site_delete", {"group": group_slug, "site": site_slug})
        return Response(status_code=204)

    @router.put(
        "/sites/{group_slug}/{site_slug}/access",
        tags=[TAG_SITES],
        summary="Set the access to a site",
        response_description="The site with its new access.",
        description=(
            "Determines who may see the live content of this site: the base and the two "
            "exceptions in one go, because they belong together and a visitor gets in "
            "as soon as one of the three lets them in. The change applies immediately to every "
            "subsequent request for the content. Previews with their own `accessOverride` do not follow "
            "this value.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def set_access(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: AccessBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        site.access_base = body.base
        site.access_keys = body.keys
        site.access_invitees = body.invitees
        await db.commit()
        await _audit(
            request,
            member,
            "site_visibility",
            {
                "group": group_slug,
                "site": site_slug,
                "base": str(body.base),
                "keys": body.keys,
                "invitees": body.invitees,
            },
        )
        return (await _sites_json(db, group, [site]))[0]

    @router.put(
        "/sites/{group_slug}/{site_slug}/external-sources",
        tags=[TAG_SITES],
        summary="Allow or block external sources",
        response_description="The site with its new setting.",
        description=(
            "Determines whether the content of this site may load scripts and styles from cdnjs, jsDelivr "
            "and unpkg, and fonts from Google Fonts. On by default; turning it off is an "
            "extra restriction and the safer choice for a confidential page. The change "
            "applies immediately to every subsequent request for the content, for the live site, "
            "previews and version views. Blocked in both modes: fetching data from or "
            "sending data to other hosts, images from elsewhere, an iframe, and a form that "
            "posts elsewhere.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def set_external_sources(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: ExternalSourcesBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        site.external_sources = body.external_sources
        await db.commit()
        await _audit(
            request,
            member,
            "site_external_sources",
            {"group": group_slug, "site": site_slug, "external_sources": body.external_sources},
        )
        return (await _sites_json(db, group, [site]))[0]

    @router.put(
        "/sites/{group_slug}/{site_slug}/sandbox",
        tags=[TAG_SITES],
        summary="Turn isolation from other sites on or off",
        response_description="The site with its new setting.",
        description=(
            "All sites share one hostname. When this isolation is on, the default, the content is served "
            "with a CSP sandbox without `allow-same-origin`: the page gets an opaque origin and "
            "cannot read any other site on that hostname, receives no cookies and cannot store "
            "anything in the browser. Its own styles, scripts, images and fonts load as usual. "
            "Turning it off is needed for a site that uses `localStorage`, `sessionStorage` or a cookie, "
            "and puts that site back on the origin it shares with all other sites. The change "
            "applies immediately to every subsequent request for the content, for the live site, "
            "previews and version views.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def set_sandbox(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: SandboxBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        site.sandbox = body.sandbox
        await db.commit()
        await _audit(
            request,
            member,
            "site_sandbox",
            {"group": group_slug, "site": site_slug, "sandbox": body.sandbox},
        )
        return (await _sites_json(db, group, [site]))[0]

    @router.put(
        "/sites/{group_slug}/{site_slug}/live-versions-kept",
        tags=[TAG_SITES],
        summary="Set the number of previous versions kept",
        response_description="The site with its new setting.",
        description=(
            "Determines how many previous live versions the nightly cleanup of this site leaves in place, "
            "in addition to the current live version. Older live versions are removed, row and files, and "
            "cannot be rolled back to afterwards. `0` keeps all live versions, `null` "
            "puts the site back on the platform default. The change takes effect at the "
            "next nightly cleanup.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {
                422: (
                    "Not an integer of 0 or more (`LIVE_VERSIONS_KEPT_INVALID`), or a number "
                    "that is too large to store (`LIVE_VERSIONS_KEPT_TOO_LARGE`)."
                )
            },
        ),
    )
    async def set_live_versions_kept(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: LiveVersionsKeptBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        kept = body.live_versions_kept
        if kept is not None and kept < 0:
            raise ApiError(422, "LIVE_VERSIONS_KEPT_INVALID")
        site.live_versions_kept = kept
        try:
            await db.commit()
        except DBAPIError as error:
            # The column's integer type is the only upper bound. A number it
            # cannot hold comes back as SQLSTATE class 22 (data exception):
            # 22000 when asyncpg cannot encode the parameter, 22003 should the
            # server be the one to say so.
            if not str(getattr(error.orig, "sqlstate", "")).startswith("22"):
                raise
            await db.rollback()
            raise ApiError(422, "LIVE_VERSIONS_KEPT_TOO_LARGE") from None
        await _audit(
            request,
            member,
            "site_live_versions_kept",
            {"group": group_slug, "site": site_slug, "live_versions_kept": kept},
        )
        return (await _sites_json(db, group, [site]))[0]

    # -- Invitees --

    @router.get(
        "/sites/{group_slug}/{site_slug}/invitees",
        tags=[TAG_INVITEES],
        summary="Invitees of a site",
        response_description="The invitees, sorted by identifier.",
        description=(
            "The addresses that may see this site while the `invitees` exception is on. While it is off, "
            "the list is kept but has no effect.\n\n"
            "**Who can call this:** effective site role `editor` or higher. This list holds e-mail addresses of "
            "external people and therefore sits above `reader`."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def invitees(group_slug: str, site_slug: str, member: ActiveMember, db: Db) -> list[InviteeOut]:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        rows = await db.scalars(
            select(Invitee).where(Invitee.site_id == site.id).order_by(Invitee.identifier)
        )
        return [_invitee_json(invitee, group_slug, site_slug) for invitee in rows]

    @router.post(
        "/sites/{group_slug}/{site_slug}/invitees",
        status_code=201,
        tags=[TAG_INVITEES],
        summary="Add an invitee",
        response_description="The added invitee.",
        description=(
            "Puts an address on the invitee list. The identifier is normalised to lowercase; "
            "the invitee does not need to have an account yet, but must be able to sign in through SSO.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {409: "This address is already on the invitee list (`INVITEE_EXISTS`)."},
            {422: "The identifier is empty (`FIELD_EMPTY`)."},
        ),
    )
    async def add_invitee(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: IdentifierBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> InviteeOut:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        identifier = _validate_text(body.identifier, "identifier").lower()
        invitee = Invitee(site_id=site.id, identifier=identifier, added_by=member.id)
        db.add(invitee)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise ApiError(
                409, "INVITEE_EXISTS", params={"identifier": identifier}
            ) from None
        await _audit(
            request, member, "invitee_add", {"group": group_slug, "site": site_slug}
        )
        return _invitee_json(invitee, group_slug, site_slug)

    @router.delete(
        "/sites/{group_slug}/{site_slug}/invitees/{invitee_id}",
        status_code=204,
        tags=[TAG_INVITEES],
        summary="Remove an invitee",
        description=(
            "Removes an address from the invitee list. An ID that does not belong to this site returns 404. The "
            "path takes the invitee's `id` from the list, not the address itself: an address in a URL "
            "ends up in the log lines of every proxy in between.\n\n"
            "**Who can call this:** effective site role `editor` or higher, with a valid CSRF header."
        ),
        responses=_deleted("The invitee has been removed from the list.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {404: "This invitee is not on the list of this site (`UNKNOWN_INVITEE`)."},
        ),
    )
    async def remove_invitee(
        request: Request,
        group_slug: str,
        site_slug: str,
        invitee_id: uuid.UUID,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> Response:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        result = await db.execute(
            delete(Invitee).where(
                Invitee.site_id == site.id,
                Invitee.id == invitee_id,
            )
        )
        await db.commit()
        if result.rowcount == 0:
            raise ApiError(404, "UNKNOWN_INVITEE")
        await _audit(
            request, member, "invitee_remove", {"group": group_slug, "site": site_slug}
        )
        return Response(status_code=204)

    # -- Keys (secret links) --

    @router.get(
        "/sites/{group_slug}/{site_slug}/keys",
        tags=[TAG_KEYS],
        summary="Secret links of a site",
        response_description="The secret links, newest first.",
        description=(
            "The secret links of this site, newest first, including the revoked links. The secret "
            "value is not included: it can only be seen when the link is created.\n\n"
            "**Who can call this:** effective site role `editor` or higher."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def keys(group_slug: str, site_slug: str, member: ActiveMember, db: Db) -> list[KeyOut]:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        rows = await db.scalars(
            select(AccessKey)
            .where(AccessKey.site_id == site.id)
            .order_by(AccessKey.created_at.desc())
        )
        return [_key_json(key, group_slug, site_slug) for key in rows]

    @router.post(
        "/sites/{group_slug}/{site_slug}/keys",
        status_code=201,
        tags=[TAG_KEYS],
        summary="Create a secret link",
        response_description="The created key, with its full value shown once.",
        description=(
            "Creates a secret link with which the site can be seen without signing in, while the `keys` exception is "
            "on.\n\n"
            "The response contains `value` once: the full key `<selector>.<secret>`. Plak only stores "
            "a hash, so if you lose the value, create a new key.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {
                422: (
                    "`expiresAt` is not a valid time, lies in the past (`EXPIRY_IN_PAST`) or "
                    "further ahead than allowed (`EXPIRY_TOO_FAR`)."
                )
            },
        ),
    )
    async def create_key(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: KeyCreate,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> KeyCreated:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        try:
            key, value = await access_keys.create_key(db, site.id, body.label, body.expires_at)
        except ExpiryError as error:
            raise ApiError(422, error.message.key, params=error.message.params) from None
        await db.commit()
        await _audit(
            request,
            member,
            "key_create",
            {"group": group_slug, "site": site_slug, "selector": key.selector},
        )
        return KeyCreated(key=_key_json(key, group_slug, site_slug), value=value)

    @router.delete(
        "/sites/{group_slug}/{site_slug}/keys/{selector}",
        status_code=204,
        tags=[TAG_KEYS],
        summary="Revoke a secret link",
        description=(
            "Sets the key to `revoked`; the link no longer works afterwards. The key stays in the list, "
            "so there is a record that it existed. The path uses the `selector`, not the full "
            "key value.\n\n"
            "**Who can call this:** effective site role `editor` or higher, with a valid CSRF header."
        ),
        responses=_deleted("The key has been revoked.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {404: "This site has no key with this selector (`UNKNOWN_KEY`)."},
        ),
    )
    async def revoke_key(
        request: Request,
        group_slug: str,
        site_slug: str,
        selector: str,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> Response:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        key = await db.scalar(
            select(AccessKey).where(AccessKey.site_id == site.id, AccessKey.selector == selector)
        )
        if key is None:
            raise ApiError(404, "UNKNOWN_KEY")
        key.status = KeyStatus.REVOKED
        await db.commit()
        await _audit(
            request,
            member,
            "key_revoke",
            {"group": group_slug, "site": site_slug, "selector": selector},
        )
        return Response(status_code=204)

    # -- Linked repository (CI trust) --

    @router.get(
        "/sites/{group_slug}/{site_slug}/repository",
        tags=[TAG_CI],
        summary="Linked repository of a site",
        response_description="The repository from which CI may publish to this site.",
        description=(
            "The repository from which GitHub or Forgejo workflows with an OIDC ID token may publish to "
            "this site, without a secret. A 404 `REPOSITORY_NOT_SET` means nothing has been linked "
            "yet.\n\n"
            "**Who can call this:** effective site role `editor` or higher: whoever publishes must be able to "
            "set up the workflow."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE, _ERROR_REPOSITORY_NOT_SET),
    )
    async def site_repository(
        group_slug: str, site_slug: str, member: ActiveMember, db: Db
    ) -> SiteRepositoryOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        repository = await db.scalar(select(SiteRepository).where(SiteRepository.site_id == site.id))
        if repository is None:
            raise ApiError(404, "REPOSITORY_NOT_SET")
        return await _repository_json(db, repository, group.slug, site.slug)

    @router.put(
        "/sites/{group_slug}/{site_slug}/repository",
        tags=[TAG_CI],
        summary="Link a repository to a site",
        response_description="The linked repository, with the IDs the provider returned.",
        description=(
            "Links a GitHub or Forgejo repository to this site, or replaces the link. Plak looks the "
            "repository up at the provider (`GET /repos/{owner}/{repo}`) and stores its numeric IDs: "
            "these stay the same when the repository is renamed, and a new repository under the same name "
            "does not get them. A CI ID token from this repository may publish afterwards: a preview (and "
            "its cleanup) from any branch, live only from `push`, `workflow_dispatch` or `schedule` and, if "
            "one is set, only from `liveBranch`.\n\n"
            "Plak does the lookup without credentials, so it cannot see a private repository. In that case pass "
            "`repositoryId` and `ownerId` yourself (`gh api repos/{owner}/{repo} --jq '.id, .owner.id'`): If the "
            "lookup fails, Plak stores them as given. A wrong ID does not link anything else, it only causes "
            "every deploy to be refused. If Plak does find the repository, the IDs must match.\n\n"
            "Also allowed with the CLI token from `plak login` (`plak site link`), then without a CSRF header. "
            "A CI ID token links nothing.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header or the CLI token."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_CLI_TOKEN,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {
                422: (
                    "Owner or repository is not a valid name (`REPOSITORY_INVALID`), the host does not belong "
                    "to the provider or is not among the allowed Forgejo instances (`HOST_NOT_ALLOWED`), the "
                    "live branch is not a valid branch name (`LIVE_BRANCH_INVALID`), the provider does not know "
                    "the repository, or it is not public, and no IDs were passed (`REPOSITORY_NOT_FOUND`), "
                    "the IDs are not both a positive integer (`REPOSITORY_IDS_INVALID`), or the "
                    "provider gives the repository different IDs (`REPOSITORY_IDS_MISMATCH`)."
                )
            },
            {
                503: (
                    "The provider is unreachable (`CI_PROVIDER_UNREACHABLE`) or its limit for "
                    "anonymous requests is used up (`CI_PROVIDER_RATE_LIMITED`), and no IDs were passed; "
                    "try again later."
                )
            },
        ),
    )
    async def set_site_repository(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: SiteRepositoryBody,
        creator: Annotated[Creator, Depends(require_creator)],
        db: Db,
    ) -> SiteRepositoryOut:
        member = creator.member
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        host = _repository_host(request, body.provider, body.host)
        owner, repo = body.owner.strip(), body.repo.strip()
        if not valid_name(owner) or not valid_name(repo):
            raise ApiError(422, "REPOSITORY_INVALID")
        live_branch = _live_branch(body.live_branch)
        entered = _entered_ids(body)
        where = {"owner": owner, "repo": repo, "host": host_label(host)}
        providers: ProviderClient = request.app.state.ci_providers
        # Entered ids are safe without the lookup: a token only matches the
        # repository that really carries them, so a wrong id means every
        # deploy is refused. The lookup still catches a typo in a public one.
        try:
            resolved = await providers.resolve(body.provider, host, owner, repo)
        except (RepositoryNotFoundError, ProviderUnavailableError) as error:
            if entered is None:
                raise _lookup_failed(error, where) from None
            resolved = ResolvedRepository(owner=owner, repo=repo, repository_id=entered[0], owner_id=entered[1])
            ids_confirmed = False
        else:
            if entered is not None and entered != (resolved.repository_id, resolved.owner_id):
                raise ApiError(422, "REPOSITORY_IDS_MISMATCH", params=where)
            ids_confirmed = True

        repository = await db.scalar(select(SiteRepository).where(SiteRepository.site_id == site.id))
        if repository is None:
            repository = SiteRepository(site_id=site.id)
            db.add(repository)
        elif repository.ids_confirmed and (
            repository.provider,
            repository.host,
            f"{repository.owner}/{repository.repo}".lower(),
            repository.repository_id,
            repository.owner_id,
        ) == (
            body.provider,
            host,
            f"{resolved.owner}/{resolved.repo}".lower(),
            resolved.repository_id,
            resolved.owner_id,
        ):
            # Changing the live branch of a private repository keeps what a
            # token or an earlier lookup confirmed about the same repository.
            ids_confirmed = True
        repository.ids_confirmed = ids_confirmed
        repository.provider = body.provider
        repository.host = host
        repository.owner = resolved.owner
        repository.repo = resolved.repo
        repository.repository_id = resolved.repository_id
        repository.owner_id = resolved.owner_id
        repository.live_branch = live_branch
        repository.created_by = member.id
        repository.created_at = datetime.now(UTC)
        await db.commit()
        await _audit(
            request,
            member,
            "site_repository_set",
            {
                "group": group_slug,
                "site": site_slug,
                "provider": str(body.provider),
                "host": host,
                "repository": f"{resolved.owner}/{resolved.repo}",
                "repository_id": resolved.repository_id,
                "ids_confirmed": ids_confirmed,
                "live_branch": live_branch,
                **creator.audit_refs(),
            },
        )
        return await _repository_json(db, repository, group.slug, site.slug)

    @router.delete(
        "/sites/{group_slug}/{site_slug}/repository",
        status_code=204,
        tags=[TAG_CI],
        summary="Unlink a repository",
        description=(
            "Removes the link: CI ID tokens from that repository are refused afterwards. Versions that "
            "were published from CI earlier remain.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_deleted("The link has been removed.")
        | _errors(_ERROR_CSRF, _ERROR_SITE_ROLE, _ERROR_SITE, _ERROR_REPOSITORY_NOT_SET),
    )
    async def delete_site_repository(
        request: Request, group_slug: str, site_slug: str, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        result = await db.execute(
            delete(SiteRepository).where(SiteRepository.site_id == site.id).returning(SiteRepository.id)
        )
        if result.scalar() is None:
            await db.rollback()
            raise ApiError(404, "REPOSITORY_NOT_SET")
        await db.commit()
        await _audit(request, member, "site_repository_remove", {"group": group_slug, "site": site_slug})
        return Response(status_code=204)

    # -- CLI login: the member's side --

    @router.post(
        "/cli/device-authorizations/lookup",
        tags=[TAG_CLI],
        summary="Look up a pending CLI login",
        response_description="What the approval screen shows.",
        description=(
            "Looks up the pending CLI login behind a user code, so the admin interface can show "
            "which program is requesting access, when and from which network. A POST and not a query parameter, "
            "so the code does not end up in log lines.\n\n" + _APPROVAL_RULE
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_APPROVAL),
    )
    async def lookup_device_authorization(
        request: Request, body: UserCodeBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> DeviceAuthorizationPendingOut:
        await _require_fresh_approval(request, member)
        authorization = await cli.pending_by_user_code(db, body.user_code)
        if authorization is None:
            raise _unknown_user_code()
        approver_ip = net.client_ip_from_request(request)
        same_network = None
        if authorization.ip_truncated and approver_ip:
            same_network = truncate_ip(approver_ip) == authorization.ip_truncated
        return DeviceAuthorizationPendingOut(
            same_network=same_network,
            user_code=cli.format_user_code(cli.normalise_user_code(body.user_code) or ""),
            client_name=authorization.client_name,
            ip_truncated=authorization.ip_truncated,
            created_at=_iso(authorization.created_at),
            expires_at=_iso(authorization.expires_at),
        )

    @router.post(
        "/cli/device-authorizations/approve",
        status_code=204,
        tags=[TAG_CLI],
        summary="Approve a CLI login",
        description=(
            "Approves the CLI login behind this user code for the signed-in member. The CLI then fetches "
            "its tokens itself and from now on acts as this member, with exactly their roles. Only do this if "
            "you just started `plak login` yourself.\n\n" + _APPROVAL_RULE
        ),
        responses=_deleted("Approved.") | _errors(_ERROR_CSRF, _ERROR_APPROVAL),
    )
    async def approve_device_authorization(
        request: Request, body: UserCodeBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        await _require_fresh_approval(request, member)
        authorization = await cli.decide(db, body.user_code, member, approve=True)
        if authorization is None:
            raise _unknown_user_code()
        await _audit(
            request,
            member,
            vocabulary.CLI_LOGIN,
            {"via": vocabulary.VIA_CLI, "device_authorization": str(authorization.id)},
        )
        return Response(status_code=204)

    @router.post(
        "/cli/device-authorizations/deny",
        status_code=204,
        tags=[TAG_CLI],
        summary="Deny a CLI login",
        description=(
            "Denies the CLI login behind this user code; the CLI gets `ACCESS_DENIED`.\n\n"
            + _APPROVAL_RULE
        ),
        responses=_deleted("Denied.") | _errors(_ERROR_CSRF, _ERROR_APPROVAL),
    )
    async def deny_device_authorization(
        request: Request, body: UserCodeBody, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        await _require_fresh_approval(request, member)
        authorization = await cli.decide(db, body.user_code, member, approve=False)
        if authorization is None:
            raise _unknown_user_code()
        await _audit(
            request,
            member,
            vocabulary.CLI_LOGIN_DENIED,
            {"via": vocabulary.VIA_CLI, "device_authorization": str(authorization.id)},
        )
        return Response(status_code=204)

    @router.get(
        "/me/cli-sessions",
        tags=[TAG_CLI],
        summary="My linked CLI sessions",
        response_description="The CLI sessions of the signed-in member, newest first.",
        description=(
            "Every `plak login` that is still active, newest first. Expired sessions are no longer listed.\n\n"
            "**Who can call this:** any active member, for their own sessions."
        ),
        responses=_errors(),
    )
    async def my_cli_sessions(member: ActiveMember, db: Db) -> list[CliSessionOut]:
        return [
            CliSessionOut(
                id=session.id,
                client_name=session.client_name,
                created_at=_iso(session.created_at),
                last_used_at=_iso(session.last_used_at),
                expires_at=_iso(min(session.expires_at, session.max_expires_at)),
            )
            for session in await cli.sessions_of(db, member.id)
        ]

    @router.delete(
        "/me/cli-sessions/{session_id}",
        status_code=204,
        tags=[TAG_CLI],
        summary="Revoke a linked CLI session",
        description=(
            "Revokes a CLI session; whoever used it has to run `plak login` again afterwards.\n\n"
            "**Who can call this:** any active member, for their own sessions, with a valid CSRF header."
        ),
        responses=_deleted("The CLI session has been revoked.")
        | _errors(_ERROR_CSRF, {404: "You have no CLI session with this ID (`CLI_SESSION_UNKNOWN`)."}),
    )
    async def revoke_my_cli_session(
        request: Request, session_id: uuid.UUID, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> Response:
        if not await cli.revoke(db, session_id, member_id=member.id):
            raise ApiError(404, "CLI_SESSION_UNKNOWN")
        await _audit(
            request, member, vocabulary.CLI_SESSION_REVOKE, {"via": vocabulary.VIA_CLI, "cli_session": str(session_id)}
        )
        return Response(status_code=204)

    # -- Group members --

    @router.get(
        "/groups/{group_slug}/members",
        tags=[TAG_GROUP_MEMBERS],
        summary="Members of a group",
        response_description="The members of the group, sorted by e-mail address.",
        description=(
            "Who may manage the sites of this group, sorted by e-mail address, each with their role.\n\n"
            "**Who can call this:** group role `reader` or higher, or a platform administrator."
        ),
        responses=_errors(_ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def group_members(group_slug: str, member: ActiveMember, db: Db) -> list[GroupMemberOut]:
        group = await _group_with_role(db, member, group_slug, Role.READER, platform_admin=True)
        return await _group_members_json(db, group)

    @router.get(
        "/groups/{group_slug}/members/search",
        tags=[TAG_GROUP_MEMBERS],
        summary="Find someone to add to the group",
        response_description=(
            "At most ten active platform members, sorted by name and then by e-mail address."
        ),
        description=(
            "Searches the platform members by name or e-mail address, so you can add someone without "
            "knowing their address by heart. The `identifier` of a hit is exactly what "
            "`POST /groups/{group_slug}/members` expects.\n\n"
            "Only active members are returned: you do not add someone who has been "
            "deactivated. The answer is a shortlist of at most ten names, not a dump of the "
            "whole organisation; someone who is already in the group is included, with `alreadyMember` set "
            "to `true`.\n\n"
            "Only those who may add members may search, and Plak does not create an account here "
            "either: someone only appears once they have signed in to the admin interface themselves.\n\n"
            "**Who can call this:** group role `admin`, or a platform administrator."
        ),
        responses=_errors(_ERROR_GROUP_ROLE, _ERROR_GROUP, _ERROR_SEARCH),
    )
    async def search_group_members(
        group_slug: str, q: SearchTerm, member: ActiveMember, db: Db
    ) -> list[MemberSearchOut]:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN, platform_admin=True)
        already = set(
            await db.scalars(select(GroupMember.member_id).where(GroupMember.group_id == group.id))
        )
        return await _search_members(db, q, already)

    @router.post(
        "/groups/{group_slug}/members",
        status_code=201,
        tags=[TAG_GROUP_MEMBERS],
        summary="Add a member to a group",
        response_description="The added group member.",
        description=(
            "Adds an existing platform member to this group, looked up by e-mail address or SSO subject. The "
            "member must already have signed in to the admin interface once themselves; Plak does not "
            "create an account here.\n\n"
            "Without `role` the member becomes `reader`: new members start with read-only access, and you "
            "give the role they need deliberately.\n\n"
            "**Who can call this:** group role `admin`, or a platform administrator, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            _ERROR_IDENTIFIER_AMBIGUOUS,
            {404: "There is no member with this identifier; they must sign in themselves first (`UNKNOWN_MEMBER`)."},
            {409: "This member is already in the group (`ALREADY_GROUP_MEMBER`)."},
            {422: "The identifier is empty (`FIELD_EMPTY`), or `role` is not an existing role."},
        ),
    )
    async def add_group_member(
        request: Request, group_slug: str, body: GroupMemberAdd, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> GroupMemberOut:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN, platform_admin=True)
        target = await _find_member_or_409(db, _validate_text(body.identifier, "identifier"))
        if target is None:
            raise ApiError(404, "UNKNOWN_MEMBER.must_sign_in")
        db.add(GroupMember(group_id=group.id, member_id=target.id, role=body.role))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise ApiError(409, "ALREADY_GROUP_MEMBER") from None
        await _audit(
            request,
            member,
            "group_member_add",
            {"group": group_slug, "member_id": str(target.id), "role": body.role.value},
        )
        return await _one_group_member(db, group, target, body.role)

    @router.delete(
        "/groups/{group_slug}/members/{member_id}",
        status_code=204,
        tags=[TAG_GROUP_MEMBERS],
        summary="Remove a member from a group",
        description=(
            "Removes someone from the group. The member's platform account remains, as do any other group "
            "memberships. The path holds `memberId` from the member list, not the e-mail address: "
            "an address in a URL ends up in the log lines of every proxy in between.\n\n"
            "A direct site role on an individual site is independent of the group and by default "
            "stays in place. With `siteRoles=remove` you remove those roles in the same action, but only on "
            "sites in this group; whatever this member has elsewhere is left untouched. Everything happens in "
            "one transaction, so it is all gone or nothing changes. Each removed site role produces the same "
            "audit row as removing it from the site screen (`site_member_remove`).\n\n"
            "The last member of a group cannot be removed: a group without members can no longer be managed, "
            "because only someone who is in it may add someone. For the same reason the last "
            "`admin` cannot be removed.\n\n"
            "**Who can call this:** group role `admin`, or a platform administrator, with a valid CSRF header. "
            "Whoever may manage the group may already remove any site role in it at the site itself."
        ),
        responses=_deleted("The member is no longer in the group.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            {
                409: (
                    "This is the last member of the group (`LAST_GROUP_MEMBER`), or its last admin "
                    "(`LAST_GROUP_ADMIN`)."
                ),
                404: (
                    "There is no member with this ID (`UNKNOWN_MEMBER`), or that member is not in this group "
                    "(`NOT_GROUP_MEMBER`)."
                )
            },
        ),
    )
    async def remove_group_member(
        request: Request,
        group_slug: str,
        member_id: uuid.UUID,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
        site_roles: SiteRolesOnRemoval = "keep",
    ) -> Response:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN, platform_admin=True)
        target = await db.get(Member, member_id)
        if target is None:
            raise ApiError(404, "UNKNOWN_MEMBER")
        # Read before anything is deleted: the two deletes share one
        # transaction, so a rowcount afterwards can no longer be the thing that
        # decides whether this member was in the group at all.
        membership = await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group.id, GroupMember.member_id == target.id
            )
        )
        if membership is None:
            raise ApiError(404, "NOT_GROUP_MEMBER")
        # A group without members can no longer be managed: there is no route to
        # add someone who is not a member themselves. The database trigger
        # ck_groups_keep_one_admin guards the neighbouring case, a group left
        # without an admin.
        if await _is_last_group_member(db, group.id, target.id):
            raise ApiError(409, "LAST_GROUP_MEMBER")
        removed_sites: list[str] = []
        async with _group_keeps_an_admin(db):
            if site_roles == "remove":
                removed_sites = await _remove_site_roles_in_group(db, group, target)
            await db.execute(
                delete(GroupMember).where(GroupMember.group_id == group.id, GroupMember.member_id == target.id)
            )
            await db.commit()
        await _audit(
            request, member, "group_member_remove", {"group": group_slug, "member_id": str(target.id)}
        )
        for site_slug in removed_sites:
            await _audit(
                request,
                member,
                "site_member_remove",
                {"group": group_slug, "site": site_slug, "member_id": str(target.id)},
            )
        return Response(status_code=204)

    @router.put(
        "/groups/{group_slug}/members/{member_id}/role",
        tags=[TAG_GROUP_MEMBERS],
        summary="Change the group role of a member",
        response_description="The group member with their new role.",
        description=(
            "Gives a member of this group a different role. The role determines what they may do in the whole "
            f"group: {ROLE_HINT}\n\n"
            "This never changes a site role: you set that per site, and it only widens what someone may "
            "do on that one site.\n\n"
            "The last `admin` of a group cannot be demoted; a group without an admin can no longer be "
            "managed. Demoting yourself is allowed: as long as there is another admin, they can restore "
            "you.\n\n"
            "The path holds `memberId` from the member list, not the e-mail address: an address in a URL "
            "ends up in the log lines of every proxy in between.\n\n"
            "**Who can call this:** group role `admin`, or a platform administrator, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            {
                404: (
                    "There is no member with this ID (`UNKNOWN_MEMBER`), or that member is not in this group "
                    "(`NOT_GROUP_MEMBER`)."
                )
            },
            {409: "This is the last admin of the group (`LAST_GROUP_ADMIN`)."},
        ),
    )
    async def set_group_member_role(
        request: Request,
        group_slug: str,
        member_id: uuid.UUID,
        body: GroupRoleUpdate,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> GroupMemberOut:
        group = await _group_with_role(db, member, group_slug, Role.ADMIN, platform_admin=True)
        target = await db.get(Member, member_id)
        if target is None:
            raise ApiError(404, "UNKNOWN_MEMBER")
        membership = await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group.id, GroupMember.member_id == target.id
            )
        )
        if membership is None:
            raise ApiError(404, "NOT_GROUP_MEMBER")
        membership.role = body.role
        async with _group_keeps_an_admin(db):
            await db.commit()
        await _audit(
            request,
            member,
            "group_member_role",
            {"group": group_slug, "member_id": str(target.id), "role": body.role.value},
        )
        return await _one_group_member(db, group, target, body.role)

    # -- Platform members --

    @router.get(
        "/platform/members",
        tags=[TAG_PLATFORM],
        summary="All platform members",
        response_description="All platform members, sorted by e-mail address.",
        description=(
            "Everyone who has ever signed in to the admin interface, sorted by e-mail address, with their "
            "role and status. This is also where you see who has been denied access.\n\n"
            "**Who can call this:** only a platform administrator."
        ),
        responses=_errors(_ERROR_ADMIN),
    )
    async def platform_members(request: Request, member: ActiveMember, db: Db) -> list[MemberOut]:
        _require_admin(member)
        bootstrap_sub = request.app.state.settings.bootstrap_admin_sub
        members = await db.scalars(select(Member).order_by(Member.email))
        return [_member_json(row, bootstrap_sub=bootstrap_sub) for row in members]

    # -- Content volume --

    @router.get(
        "/platform/storage",
        tags=[TAG_PLATFORM],
        summary="Disk usage of the content volume",
        response_description="Total, used and free space on the content volume, with the reserve.",
        description=(
            "The whole content volume at a glance: size, used, free and the reserve below which a deploy "
            "is refused. It lists no site or group: the platform administrator manages people and groups "
            "and does not look into the sites.\n\n"
            "**Who can call this:** only a platform administrator."
        ),
        responses=_errors(
            _ERROR_ADMIN,
            {
                503: (
                    "The volume cannot be measured, for example because the content root is missing "
                    "(`VOLUME_UNMEASURABLE`)."
                )
            },
        ),
    )
    async def platform_storage(request: Request, member: PlatformAdmin) -> VolumeOut:
        settings = request.app.state.settings
        try:
            usage = await asyncio.to_thread(shutil.disk_usage, request.app.state.content_store.root)
        except OSError as error:
            raise ApiError(503, "VOLUME_UNMEASURABLE") from error
        return VolumeOut(
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            reserve_bytes=settings.storage_min_free_bytes,
            max_deploy_bytes=settings.ingest_max_total,
        )

    async def _set_member_status(
        request: Request, member_id: uuid.UUID, status: MemberStatus, action: str, member: Member, db: AsyncSession
    ) -> MemberOut:
        _require_admin(member)
        target = await db.get(Member, member_id)
        if target is None:
            raise ApiError(404, "UNKNOWN_MEMBER")
        if status != MemberStatus.ACTIVE:
            _refuse_self(target, member, "deactivate")
            _refuse_bootstrap(request, target)
            await _refuse_last_admin(db, target)
        target.status = status
        refs: dict[str, Any] = {"member_id": str(member_id)}
        if status == MemberStatus.DEACTIVATED:
            # Gone, not suspended: reactivating the member must not bring a
            # CLI login back that was made before the deactivation.
            refs["cli_sessions_revoked"] = await cli.revoke_all_of(db, target.id)
        await db.commit()
        await _audit(request, member, action, refs)
        return _member_json(target, bootstrap_sub=request.app.state.settings.bootstrap_admin_sub)

    @router.post(
        "/platform/members/{member_id}/_activate",
        tags=[TAG_PLATFORM],
        summary="Reactivate a platform member",
        response_description="The member with their new status.",
        description=(
            "Sets the status of a member to `active`, so they may use the admin API again. For a "
            "member whose access has been revoked (status `deactivated`).\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_ADMIN, {404: "Unknown member (`UNKNOWN_MEMBER`)."}),
    )
    async def activate_platform_member(
        request: Request, member_id: uuid.UUID, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> MemberOut:
        return await _set_member_status(request, member_id, MemberStatus.ACTIVE, "member_activate", member, db)

    @router.post(
        "/platform/members/{member_id}/_deactivate",
        tags=[TAG_PLATFORM],
        summary="Deactivate a platform member",
        response_description="The member with their new status.",
        description=(
            "Sets the status of a member to `deactivated`. Every subsequent API request from that member gets "
            "403, even with a session that is still valid. Group memberships remain, so activating brings "
            "the member back as they were. All CLI sessions (`plak login`) of the member are revoked, "
            "though: after reactivation they have to run `plak login` again.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_ADMIN, {404: "Unknown member (`UNKNOWN_MEMBER`)."}),
    )
    async def deactivate_platform_member(
        request: Request, member_id: uuid.UUID, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> MemberOut:
        return await _set_member_status(
            request, member_id, MemberStatus.DEACTIVATED, "member_deactivate", member, db
        )

    @router.put(
        "/platform/members/{member_id}/platform-role",
        tags=[TAG_PLATFORM],
        summary="Change the platform role of a member",
        response_description="The member with their new platform role.",
        description=(
            "Makes a member platform administrator or removes that role. A platform administrator manages "
            "people and groups: activating and deactivating members, appointing administrators, and reading "
            "the member list. They do not manage sites; for that they give themselves a group role.\n\n"
            "Three things are not possible, all because they would make the platform unmanageable: taking "
            "away your own role, demoting the last active administrator, and changing the bootstrap "
            "account.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            {404: "Unknown member (`UNKNOWN_MEMBER`)."},
            {
                409: (
                    "Taking away your own role (`SELF_NOT_ALLOWED`), the last active administrator "
                    "(`LAST_PLATFORM_ADMIN`), or the bootstrap account (`BOOTSTRAP_MEMBER`)."
                )
            },
        ),
    )
    async def set_platform_role(
        request: Request,
        member_id: uuid.UUID,
        body: PlatformRoleUpdate,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> MemberOut:
        _require_admin(member)
        target = await db.get(Member, member_id)
        if target is None:
            raise ApiError(404, "UNKNOWN_MEMBER")
        if body.platform_role != PlatformRole.ADMIN:
            _refuse_self(target, member, "demote")
            _refuse_bootstrap(request, target)
            await _refuse_last_admin(db, target)
        target.platform_role = body.platform_role
        await db.commit()
        await _audit(
            request, member, "member_platform_role", {"member_id": str(member_id), "role": body.platform_role.value}
        )
        return _member_json(target, bootstrap_sub=request.app.state.settings.bootstrap_admin_sub)

    # -- Auditlog --

    @router.get(
        "/platform/audit",
        tags=[TAG_AUDIT],
        summary="Read the audit log",
        response_description="One page of audit rows, newest first, with the cursor to the next.",
        description=(
            "Reads the audit log, newest first: refusals, signing in and out, viewing "
            "non-public content, deploys and admin actions. Vocabulary and retention periods are in "
            "`docs/audit-log.md`.\n\n"
            "**Paging works with `cursor`, not with a page number.** The log grows while you read, and "
            "a shifting offset would skip rows or show them twice. Take `nextCursor` from the "
            "response unchanged; if it is `null`, this was the last page.\n\n"
            "**Actors appear pseudonymised** and are not translated back here. If you are looking for "
            "someone in particular, first fetch their pseudonym with "
            "`POST /platform/audit/actor-pseudonym` and filter on `actorPseudonym` with it.\n\n"
            "**Who can call this:** only a platform administrator. A group admin does not see even their own group: "
            "the group of a row is only a free-form key in `refs`, and no "
            "authorization boundary can be built on that. Reading the log is itself audited; if writing that "
            "audit row fails, no page is returned: see the `503`."
        ),
        responses=_errors(_ERROR_ADMIN, _ERROR_AUDIT_FILTER, _ERROR_AUDIT_UNAVAILABLE),
    )
    async def audit_log(
        request: Request,
        member: PlatformAdmin,
        db: Db,
        filters: Annotated[AuditFilters, Query()],
    ) -> AuditPage:
        statement = select(AuditLogEntry).order_by(*_AUDIT_ORDER).limit(filters.limit + 1)
        if filters.since is not None:
            statement = statement.where(AuditLogEntry.occurred_at >= _require_aware(filters.since))
        if filters.until is not None:
            statement = statement.where(AuditLogEntry.occurred_at < _require_aware(filters.until))
        if filters.action:
            statement = statement.where(AuditLogEntry.action == filters.action)
        if filters.result:
            statement = statement.where(AuditLogEntry.result == filters.result)
        if filters.reason_code:
            statement = statement.where(AuditLogEntry.reason_code == filters.reason_code)
        if filters.group:
            statement = statement.where(AuditLogEntry.refs["group"].astext == filters.group)
        if filters.site:
            statement = statement.where(AuditLogEntry.refs["site"].astext == filters.site)
        if filters.actor_pseudonym:
            statement = statement.where(
                AuditLogEntry.actor_pseudonym == _audit_pseudonym(filters.actor_pseudonym)
            )
        if filters.cursor:
            occurred_at, last_id = _audit_cursor_read(filters.cursor)
            statement = statement.where(
                tuple_(AuditLogEntry.occurred_at, AuditLogEntry.id) < tuple_(occurred_at, last_id)
            )

        rows = list(await db.scalars(statement))
        entries = rows[: filters.limit]
        used = filters.model_dump(mode="json", exclude_none=True, by_alias=True)
        used.pop("cursor", None)
        # Written before the response is built: this read is itself an
        # actor-pseudonym-bearing disclosure, so a failed audit write must
        # not let the page through.
        await _audit_strict(request, member, vocabulary.AUDIT_READ, {"filters": used, "returned": len(entries)})
        return AuditPage(
            entries=[_audit_entry_json(entry) for entry in entries],
            next_cursor=_audit_cursor(entries[-1]) if len(rows) > filters.limit else None,
        )

    @router.post(
        "/platform/audit/actor-pseudonym",
        tags=[TAG_AUDIT],
        summary="Look up the pseudonym of an actor",
        response_description="The audit pseudonym that belongs to this identifier.",
        description=(
            "Translates an identifier into the pseudonym under which it appears in the audit log, so you can "
            "filter on `actorPseudonym`. So you must already know who you are looking for: the log does not give "
            "away a list of names. If you only know the pseudonym from a log row, "
            "use `POST /platform/audit/actor-identity` for the opposite direction.\n\n"
            "That this is a POST and not a query parameter is deliberate: an e-mail address in a URL ends up "
            "in the log lines of every proxy in between.\n\n"
            "The pseudonym depends on `PLAK_AUDIT_PEPPER`. After a rotation of that pepper you only find "
            "rows from after the rotation.\n\n"
            "An e-mail address that matches neither a member nor the verified address of a "
            "content viewer is not pseudonymised: that is a 404. An identifier that is not an e-mail address "
            "and belongs to nothing (for example a bare SSO subject) is pseudonymised as given, as "
            "`unknown`. If an e-mail address belongs to more than one subject (only possible via e-mail, never via "
            "an SSO subject), the response is a 409: in that case search by the SSO subject.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason` of "
            f"{REASON_MIN_LENGTH} to {REASON_MAX_LENGTH} characters. The lookup itself is audited, with the "
            "pseudonym, `resolved_as` and the reason, never the identifier. If that audit row fails, "
            "no response is returned: see the `503`. Counts towards the daily limit on such lookups, even "
            "on a 404."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            _ERROR_AUDIT_UNAVAILABLE,
            _ERROR_REASON,
            _ERROR_LOOKUP_LIMIT,
            _ERROR_IDENTIFIER_AMBIGUOUS,
        ),
    )
    async def audit_actor_pseudonym(
        request: Request, body: ActorLookup, _csrf: Csrf, member: PlatformAdmin, db: Db
    ) -> ActorPseudonymOut:
        identifier = _validate_text(body.identifier, "identifier")

        try:
            resolution = await _resolve_lookup_identifier(db, identifier)
        except IdentifierAmbiguousError as error:
            await _audit_strict_limited(
                request,
                member,
                vocabulary.AUDIT_ACTOR_LOOKUP,
                {"resolved_as": "ambiguous", "matches": error.matches, "reason": body.reason},
            )
            raise ApiError(409, "IDENTIFIER_AMBIGUOUS.email") from error

        ci_subject = None
        if resolution is None and "@" not in identifier:
            try:
                ci_subject = await _resolve_ci_identifier(db, identifier)
            except IdentifierAmbiguousError as error:
                await _audit_strict_limited(
                    request,
                    member,
                    vocabulary.AUDIT_ACTOR_LOOKUP,
                    {"resolved_as": "ambiguous", "matches": error.matches, "reason": body.reason},
                )
                raise ApiError(409, "IDENTIFIER_AMBIGUOUS.repository") from error

        if resolution is not None:
            resolved, subject = resolution
        elif ci_subject is not None:
            resolved, subject = "ci", ci_subject
        elif "@" in identifier:
            # An e-mail address that matches nothing must not be hashed:
            # nothing to gain from a pseudonym for an address that will
            # never appear in the log.
            await _audit_strict_limited(
                request,
                member,
                vocabulary.AUDIT_ACTOR_LOOKUP,
                {"resolved_as": "unknown", "reason": body.reason},
            )
            raise ApiError(404, "IDENTIFIER_UNKNOWN")
        else:
            resolved, subject = "unknown", identifier
        pseudonym = pseudonymise(request.app.state.settings.audit_pepper, subject)
        await _audit_strict_limited(
            request,
            member,
            vocabulary.AUDIT_ACTOR_LOOKUP,
            {"pseudonym": pseudonym, "resolved_as": resolved, "reason": body.reason},
        )
        return ActorPseudonymOut(actor_pseudonym=pseudonym, resolved_as=resolved)

    @router.post(
        "/platform/audit/actor-identity",
        tags=[TAG_AUDIT],
        summary="Look up the actor behind a pseudonym",
        response_description="The member, content viewer or CI repository behind this pseudonym.",
        description=(
            "The opposite direction of `POST /platform/audit/actor-pseudonym`: give a pseudonym from "
            "a log row, get back who is behind it. Every member, every content viewer and every linked "
            "repository is pseudonymised again with the current `PLAK_AUDIT_PEPPER` and compared with the "
            "given pseudonym; nothing is kept to speed up this lookup, so expect a "
            "full pass over all three.\n\n"
            "If the lookup finds nothing, there is no member, content viewer or repository that pseudonymises "
            "to this pseudonym. Several causes are possible: the pepper has been rotated since that row was "
            "written; the actor only ever visited content through the content-host SSO and that visit was "
            "more than 90 days ago (`content_viewers` is then cleaned up, like the related "
            "`content_access` rows); or the repository has since been unlinked, or deleted along with its "
            "site or group (`ON DELETE CASCADE`).\n\n"
            "That this is a POST and not a query parameter is deliberate, as with the other side of this "
            "lookup.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason` of "
            f"{REASON_MIN_LENGTH} to {REASON_MAX_LENGTH} characters. The lookup itself is audited, with the "
            "given pseudonym, `resolved_as` and the reason, never the identifier found. If writing that "
            "audit row fails, no response is returned: see the `503`. Counts towards the daily limit on "
            "such lookups, even on a 404."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            _ERROR_AUDIT_UNAVAILABLE,
            _ERROR_REASON,
            _ERROR_LOOKUP_LIMIT,
            {422: f"Not {AUDIT_PSEUDONYM_LENGTH} hexadecimal characters (`ACTOR_PSEUDONYM_INVALID`)."},
            {
                404: (
                    "No member, content viewer or linked repository pseudonymises to this value "
                    "(`PSEUDONYM_UNKNOWN`): the pepper has been rotated, the content viewer was cleaned up after 90 "
                    "days without a visit, or the repository was unlinked or deleted along with its site."
                )
            },
        ),
    )
    async def audit_actor_identity(
        request: Request, body: ActorIdentityLookup, _csrf: Csrf, member: PlatformAdmin, db: Db
    ) -> ActorIdentityOut:
        pseudonym = _audit_pseudonym(body.actor_pseudonym)
        pepper = request.app.state.settings.audit_pepper

        result: ActorIdentityOut | None = None
        resolved = "unknown"

        matched_member = await _match_member_by_pseudonym(db, pepper, pseudonym)
        if matched_member is not None:
            resolved = "member"
            result = ActorIdentityOut(
                kind="member",
                member_id=matched_member.id,
                email=matched_member.email,
                name=matched_member.name or "",
                member_status=matched_member.status,
            )
        else:
            matched_viewer = await _match_content_viewer_by_pseudonym(db, pepper, pseudonym)
            if matched_viewer is not None:
                resolved = "content_viewer"
                result = ActorIdentityOut(
                    kind="content_viewer",
                    email=matched_viewer.email,
                    email_verified=matched_viewer.email_verified,
                    last_seen_at=_iso(matched_viewer.last_seen_at),
                )
            else:
                matched_ci = await _match_ci_by_pseudonym(db, pepper, pseudonym)
                if matched_ci is not None:
                    repository, linked_sites = matched_ci
                    resolved = "ci"
                    result = ActorIdentityOut(
                        kind="ci",
                        provider=str(repository.provider),
                        host=repository.host,
                        repository=f"{repository.owner}/{repository.repo}",
                        sites=linked_sites,
                    )

        # Written before the 404: the audit row is the only record that this
        # de-anonymisation was attempted, whatever the outcome.
        await _audit_strict_limited(
            request,
            member,
            vocabulary.AUDIT_ACTOR_IDENTITY,
            {"pseudonym": pseudonym, "resolved_as": resolved, "reason": body.reason},
        )
        if result is None:
            raise ApiError(404, "PSEUDONYM_UNKNOWN")
        return result

    @router.post(
        "/platform/audit/entries/{entry_id}/ip",
        tags=[TAG_AUDIT],
        summary="Reveal the full IP address of an audit row",
        response_description="The full IP address for this audit row.",
        description=(
            "`GET /platform/audit` only shows the truncated network per row (`ipTruncated`); this "
            "endpoint decrypts the full address stored encrypted next to it (`PLAK_AUDIT_IP_KEY`, "
            "a different key from the audit pepper). Intended as a last resort, when the truncated network "
            "is not enough, for example in an incident investigation.\n\n"
            "**Who can call this:** only a platform administrator, with a valid CSRF header and a `reason` of "
            f"{REASON_MIN_LENGTH} to {REASON_MAX_LENGTH} characters. The decryption itself is audited, with "
            "the row ID and the reason, never the IP address. If that audit row fails, no "
            "response is returned: see the `503`. Counts towards the daily limit on such lookups, even on a "
            "404."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            _ERROR_AUDIT_UNAVAILABLE,
            _ERROR_REASON,
            _ERROR_LOOKUP_LIMIT,
            {404: "No audit row with this ID, or it has no encrypted IP address (`AUDIT_IP_UNKNOWN`)."},
        ),
    )
    async def audit_ip_reveal(
        request: Request,
        entry_id: Annotated[uuid.UUID, Path(description="ID of the audit row (`AuditEntryOut.id`).")],
        body: IpRevealBody,
        _csrf: Csrf,
        member: PlatformAdmin,
        db: Db,
    ) -> IpRevealOut:
        entry = await db.get(AuditLogEntry, entry_id)
        ip: str | None = None
        if entry is not None and entry.ip_encrypted:
            try:
                ip = decrypt_ip(
                    request.app.state.settings.audit_ip_key_bytes,
                    entry.id,
                    entry.ip_encrypted,
                    previous_key=request.app.state.settings.audit_ip_key_previous_bytes,
                )
            except IpDecryptError:
                # No key configured (current or previous) decrypts this row,
                # or the blob predates the AAD-binding format: indistinguishable
                # from "no encrypted IP" to the caller (docs/security.md).
                ip = None

        # Written before the 404: see the same audit call above.
        await _audit_strict_limited(
            request,
            member,
            vocabulary.AUDIT_IP_REVEAL,
            {"entry": str(entry_id), "reason": body.reason, "revealed": ip is not None},
        )
        if ip is None:
            raise ApiError(404, "AUDIT_IP_UNKNOWN")
        return IpRevealOut(ip=ip)

    # -- Site members ---------------------------------------------------------

    @router.get(
        "/sites/{group_slug}/{site_slug}/members",
        tags=[TAG_SITE_MEMBERS],
        summary="Members of a site",
        response_description="Everyone who can reach this site, each with the role that grants access.",
        description=(
            "Everyone who can reach this site, not only those with a direct role here. Each row shows "
            "the group role, the site role and the resulting role: the higher of the two "
            "wins, because a site role only widens and never takes anything away.\n\n"
            "A list with only the site roles would leave out the group members and read as if far fewer "
            "people can reach the site than is actually the case.\n\n"
            "**Who can call this:** effective site role `reader`."
        ),
        responses=_errors(_ERROR_SITE),
    )
    async def site_members(
        group_slug: str, site_slug: str, member: ActiveMember, db: Db
    ) -> list[SiteMemberOut]:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.READER)
        return await _site_members_json(db, group, site)

    @router.get(
        "/sites/{group_slug}/{site_slug}/members/search",
        tags=[TAG_SITE_MEMBERS],
        summary="Find someone to give a role on this site",
        response_description=(
            "At most ten active platform members, sorted by name and then by e-mail address."
        ),
        description=(
            "Searches the platform members by name or e-mail address, so you can give someone a role on this "
            "site without knowing their address by heart. The `identifier` of a hit is "
            "exactly what `POST /sites/{group_slug}/{site_slug}/members` expects.\n\n"
            "Only active members are returned: you do not add someone who has been "
            "deactivated. The answer is a shortlist of at most ten names, not a dump of the "
            "whole organisation; someone who already has a direct site role here is included, with "
            "`alreadyMember` set to `true`.\n\n"
            "You can simply pick group members here: a site role widens what they may already do "
            "through the group. What that is appears in `groupRole`, so you can see whether a site role "
            "adds anything.\n\n"
            "Only those who may add members may search, and Plak does not create an account here "
            "either: someone only appears once they have signed in to the admin interface themselves.\n\n"
            "**Who can call this:** effective site role `admin`."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE, _ERROR_SEARCH),
    )
    async def search_site_members(
        group_slug: str, site_slug: str, q: SearchTerm, member: ActiveMember, db: Db
    ) -> list[MemberSearchOut]:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        already = set(
            await db.scalars(select(SiteMember.member_id).where(SiteMember.site_id == site.id))
        )
        group_rows = await db.execute(
            select(GroupMember.member_id, GroupMember.role).where(GroupMember.group_id == group.id)
        )
        group_roles = dict(group_rows.all())
        return await _search_members(db, q, already, group_roles)

    @router.post(
        "/sites/{group_slug}/{site_slug}/members",
        status_code=201,
        tags=[TAG_SITE_MEMBERS],
        summary="Give a member a role on this site",
        response_description="The member with their new site role.",
        description=(
            "Gives an existing platform member a role on this one site. That can be a higher role than "
            "their group role; lower has no effect, because the higher of the two applies. Someone "
            "does not need to be a group member: this is how you give an outsider access to exactly this "
            "site.\n\n"
            "A group role is changed on the group, not here.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE,
            _ERROR_IDENTIFIER_AMBIGUOUS,
            {404: "There is no member with this identifier; they must sign in themselves first (`UNKNOWN_MEMBER`)."},
            {409: "This member already has a role on this site (`ALREADY_SITE_MEMBER`)."},
            {422: "The identifier is empty (`FIELD_EMPTY`)."},
        ),
    )
    async def add_site_member(
        request: Request,
        group_slug: str,
        site_slug: str,
        body: SiteMemberAdd,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteMemberOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        target = await _find_member_or_409(db, _validate_text(body.identifier, "identifier"))
        if target is None:
            raise ApiError(404, "UNKNOWN_MEMBER.must_sign_in")
        db.add(SiteMember(site_id=site.id, member_id=target.id, role=body.role))
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise ApiError(409, "ALREADY_SITE_MEMBER") from None
        await _audit(
            request,
            member,
            "site_member_add",
            {"group": group_slug, "site": site_slug, "member_id": str(target.id), "role": body.role.value},
        )
        return await _one_site_member(db, group, site, target)

    @router.put(
        "/sites/{group_slug}/{site_slug}/members/{member_id}/role",
        tags=[TAG_SITE_MEMBERS],
        summary="Change the site role of a member",
        response_description="The member with their new site role.",
        description=(
            "Gives a member a different role on this one site. Only applies to a direct site role; for someone listed "
            "through their group membership, change the role on the group.\n\n"
            "The path holds `memberId` from the member list, not the e-mail address: an address in a URL "
            "ends up in the log lines of every proxy in between.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE,
            {
                404: (
                    "There is no member with this ID (`UNKNOWN_MEMBER`), or that member has no direct "
                    "site role on this site (`NOT_SITE_MEMBER`)."
                )
            },
        ),
    )
    async def set_site_role(
        request: Request,
        group_slug: str,
        site_slug: str,
        member_id: uuid.UUID,
        body: SiteRoleUpdate,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteMemberOut:
        # The site check first, so a refusal tells an outsider nothing about
        # what exists here.
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        target = await db.get(Member, member_id)
        if target is None:
            raise ApiError(404, "UNKNOWN_MEMBER")
        row = await db.scalar(
            select(SiteMember).where(SiteMember.site_id == site.id, SiteMember.member_id == target.id)
        )
        if row is None:
            raise ApiError(404, "NOT_SITE_MEMBER")
        row.role = body.role
        await db.commit()
        await _audit(
            request,
            member,
            "site_member_role",
            {"group": group_slug, "site": site_slug, "member_id": str(target.id), "role": body.role.value},
        )
        return await _one_site_member(db, group, site, target)

    @router.delete(
        "/sites/{group_slug}/{site_slug}/members/{member_id}",
        status_code=204,
        tags=[TAG_SITE_MEMBERS],
        summary="Remove the site role of a member",
        description=(
            "Removes the role that applied only to this site. A group role remains, so whoever can reach "
            "this site through the group still can afterwards.\n\n"
            "The path holds `memberId` from the member list, not the e-mail address: an address in a URL "
            "ends up in the log lines of every proxy in between.\n\n"
            "There is no last-admin protection here as there is for a group: the admins of the "
            "group can always reach this site, so a site without an admin of its own is not unmanageable."
            "\n\n**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_deleted("The member no longer has a direct site role on this site.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_SITE,
            {
                404: (
                    "There is no member with this ID (`UNKNOWN_MEMBER`), or that member has no direct "
                    "site role on this site (`NOT_SITE_MEMBER`)."
                )
            },
        ),
    )
    async def remove_site_member(
        request: Request,
        group_slug: str,
        site_slug: str,
        member_id: uuid.UUID,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> Response:
        # The site check first, so a refusal tells an outsider nothing about
        # what exists here.
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        target = await db.get(Member, member_id)
        if target is None:
            raise ApiError(404, "UNKNOWN_MEMBER")
        result = await db.execute(
            delete(SiteMember).where(SiteMember.site_id == site.id, SiteMember.member_id == target.id)
        )
        await db.commit()
        if result.rowcount == 0:
            raise ApiError(404, "NOT_SITE_MEMBER")
        await _audit(
            request,
            member,
            "site_member_remove",
            {"group": group_slug, "site": site_slug, "member_id": str(target.id)},
        )
        return Response(status_code=204)

    # -- Versions and rollback --

    @router.get(
        "/sites/{group_slug}/{site_slug}/versions",
        tags=[TAG_VERSIONS],
        summary="Deploy history of a site",
        response_description="The versions of the site, newest first.",
        description=(
            "All versions of this site, newest first, with their target (`live` or `preview`) and origin "
            "(`upload` by a member or `action` by CI). Exactly one version has `isLive`, unless "
            "nothing is live yet.\n\n"
            "**Who can call this:** effective site role `reader` or higher."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def versions(group_slug: str, site_slug: str, member: ActiveMember, db: Db) -> list[VersionOut]:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.READER)
        # The deployer comes along in the same query: a list that only carried
        # the member id would leave the interface with nothing to show but a
        # UUID.
        rows = await db.execute(
            select(Version, Member)
            .outerjoin(Member, Member.id == Version.member_id)
            .where(Version.site_id == site.id)
            .order_by(Version.created_at.desc(), Version.id)
        )
        return [
            _version_json(version, group_slug, site, deployer=deployer) for version, deployer in rows
        ]

    @router.get(
        "/sites/{group_slug}/{site_slug}/storage",
        tags=[TAG_VERSIONS],
        summary="Storage and retention rule of a site",
        response_description="The usage, the limit and the number of live versions kept.",
        description=(
            "How much space the versions of this site currently take up, how much they may take up together, "
            "and how many previous live versions the nightly cleanup leaves in place. The current live version "
            "always stays, even after rolling back to an older version. The limit applies to the whole "
            "platform; the number of versions kept is the platform default, unless a "
            "site admin set a custom number for this site.\n\n"
            "**Who can call this:** effective site role `reader` or higher."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def site_storage(
        request: Request, group_slug: str, site_slug: str, member: ActiveMember, db: Db
    ) -> SiteStorageOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.READER)
        settings = request.app.state.settings
        used = await asyncio.to_thread(request.app.state.content_store.site_bytes, group.slug, site.slug)
        own = site.live_versions_kept
        return SiteStorageOut(
            used_bytes=used,
            max_bytes=settings.site_max_bytes,
            live_versions_kept=settings.live_versions_kept if own is None else own,
            live_versions_kept_is_default=own is None,
            default_live_versions_kept=settings.live_versions_kept,
        )

    @router.post(
        "/sites/{group_slug}/{site_slug}/versions/{version_id}/_set-live",
        tags=[TAG_VERSIONS],
        summary="Roll back to an earlier version",
        response_description="The site with the new live version.",
        description=(
            "Puts an existing version (back) on the public URL. Nothing is uploaded again: the "
            "unpacked files of that version are already there. The switch applies immediately.\n\n"
            "Only versions with target `live` can be live; a preview version returns 422.\n\n"
            "**Who can call this:** effective site role `editor` or higher, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            {
                404: (
                    "Unknown group (`UNKNOWN_GROUP`), unknown site (`UNKNOWN_SITE`), or the version "
                    "does not exist or belongs to another site (`UNKNOWN_VERSION`, `VERSION_OTHER_SITE`)."
                )
            },
            {422: "This version cannot go live, for example because it is a preview version."},
        ),
    )
    async def set_version_live(
        request: Request,
        group_slug: str,
        site_slug: str,
        version_id: uuid.UUID,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> SiteOut:
        group, site = await _site_with_role(db, member, group_slug, site_slug, Role.EDITOR)
        # Close the read transaction: rollback_to opens `db.begin()` itself.
        await db.commit()
        try:
            await _ingest_service(request).rollback_to(db, site, version_id)
        except IngestError as error:
            if error.reason in ("UNKNOWN_VERSION", "VERSION_OTHER_SITE"):
                raise ApiError(404, "UNKNOWN_VERSION.site") from None
            raise ApiError(422, error.message.key, params=error.message.params) from None
        refreshed = await _site_or_404(db, group, site_slug)
        await _audit(
            request,
            member,
            "version_set_live",
            {"group": group_slug, "site": site_slug, "version_id": str(version_id)},
        )
        return (await _sites_json(db, group, [refreshed]))[0]

    # -- Previews --

    @router.get(
        "/sites/{group_slug}/{site_slug}/previews",
        tags=[TAG_PREVIEWS],
        summary="Previews of a site",
        response_description="The previews of the site, sorted by ref.",
        description=(
            "The previews that currently exist for this site, sorted by ref, with their URL on the content "
            "origin and the time at which the cleanup job discards them.\n\n"
            "**Who can call this:** effective site role `reader` or higher."
        ),
        responses=_errors(_ERROR_SITE_ROLE, _ERROR_SITE),
    )
    async def previews(group_slug: str, site_slug: str, member: ActiveMember, db: Db) -> list[PreviewOut]:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.READER)
        rows = await db.scalars(
            select(Preview).where(Preview.site_id == site.id).order_by(Preview.ref)
        )
        return [_preview_json(preview, group_slug, site_slug) for preview in rows]

    @router.put(
        "/sites/{group_slug}/{site_slug}/previews/{ref}/access",
        tags=[TAG_PREVIEWS],
        summary="Set the access to a preview",
        response_description="The preview with its new access.",
        description=(
            "Gives this preview its own access settings, independent of the site: so a preview can be "
            "broader or stricter than the live site. It is base plus exceptions as a whole, "
            "never a base of the preview with exceptions of the site. "
            "`access: null` removes the override, after which the preview follows the site again.\n\n"
            "**Who can call this:** effective site role `admin`, with a valid CSRF header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            {
                404: (
                    "Unknown group (`UNKNOWN_GROUP`), unknown site (`UNKNOWN_SITE`), or this "
                    "site has no preview with this ref (`UNKNOWN_PREVIEW`)."
                )
            },
        ),
    )
    async def set_preview_access(
        request: Request,
        group_slug: str,
        site_slug: str,
        ref: str,
        body: PreviewAccessBody,
        _csrf: Csrf,
        member: ActiveMember,
        db: Db,
    ) -> PreviewOut:
        _, site = await _site_with_role(db, member, group_slug, site_slug, Role.ADMIN)
        preview = await db.scalar(
            select(Preview).where(Preview.site_id == site.id, Preview.ref == ref)
        )
        if preview is None:
            raise ApiError(404, "UNKNOWN_PREVIEW")
        preview.access_base_override = body.access.base if body.access else None
        preview.access_keys_override = body.access.keys if body.access else None
        preview.access_invitees_override = body.access.invitees if body.access else None
        await db.commit()
        await db.refresh(preview)
        await _audit(
            request,
            member,
            "preview_visibility",
            {
                "group": group_slug,
                "site": site_slug,
                "preview": ref,
                "base": str(body.access.base) if body.access else None,
                "keys": body.access.keys if body.access else None,
                "invitees": body.access.invitees if body.access else None,
            },
        )
        return _preview_json(preview, group_slug, site_slug)

    # NB: POST .../deploys and DELETE .../previews/{ref} live in api/deploys.py
    # (bearer as well as session); see the module docstring.

    return router


__all__ = [
    "KEY_ADMIN_ONLY",
    "KEY_CSRF_INVALID",
    "KEY_NOT_GROUP_MEMBER",
    "KEY_NO_SESSION",
    "make_admin_router",
    "require_csrf",
]
