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
    "Naast de basis staan twee uitzonderingen die er los van elkaar bij kunnen: `keys` laat "
    "iedereen met een geldige geheime link binnen, ook zonder inloggen, en `invitees` laat "
    "ingelogde adressen van de genodigdenlijst binnen. Ze verbreden de basis en versmallen hem "
    "nooit, dus bij basis `public` veranderen ze niets."
)

ROLE_HINT = (
    "`reader` (leest mee), `editor` (publiceert) of `admin` (bepaalt "
    "zichtbaarheid, genodigden, sleutels en wie er in de groep zit). Een ruimere rol kan alles wat een "
    "smallere rol kan."
)

MEMBER_IDENTIFIER_HINT = (
    "E-mailadres, SSO-subject, of de volledige naam zoals die op het beheer bekend is (hoofdletters en "
    "spaties aan het begin of eind genegeerd). Een naam werkt alleen als hij precies overeenkomt met "
    "één actief platformlid; komt hij bij meer dan één lid voor, dan volgt een 409 en zoek je op het "
    "e-mailadres. Hoofdletters in een e-mailadres worden genegeerd."
)


def _timestamp_schema() -> dict[str, Any]:
    """OpenAPI extras for a timestamp field: the API writes RFC 3339 in UTC with a Z."""
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
            "Zoekterm van minstens twee tekens. Zoekt hoofdletterongevoelig op naam en op "
            "e-mailadres, ergens in de tekst."
        ),
        examples=["jansen"],
    ),
]
SiteRolesOnRemoval = Annotated[
    Literal["keep", "remove"],
    Query(
        alias="siteRoles",
        description=(
            "Wat er gebeurt met de eigen siterollen die dit lid heeft op sites in deze groep. "
            "`keep` laat ze staan, zodat diegene bij die sites blijft kunnen; `remove` haalt ze in "
            "dezelfde handeling weg. Weggelaten betekent `keep`: meer weghalen dan gevraagd is een "
            "bewuste keuze.\n\n"
            "Alleen sites in deze groep. Een siterol in een andere groep blijft buiten beeld en "
            "buiten schot."
        ),
        examples=["remove"],
    ),
]


# -- Error contract per route -----------------------------------------------

# Every route in this router carries these three; the routes extend them with
# whatever they can refuse themselves.
_ERROR_SESSION = {
    401: "Er is geen geldige beheersessie: het cookie ontbreekt, is ongeldig of is verlopen (`NO_SESSION`).",
    403: (
        "Het verzoek komt van een andere origin dan de beheer-host, of het lid is niet (meer) actief "
        "(`ORIGIN_REFUSED`, `MEMBER_NOT_ACTIVE`)."
    ),
    429: "Het ratelimit-budget voor deze sessie is op; probeer het later opnieuw.",
}
_ERROR_CSRF = {403: "De header `X-CSRF-Token` ontbreekt of komt niet overeen met het CSRF-cookie (`CSRF_INVALID`)."}
_ERROR_ADMIN = {403: "Alleen een platformbeheerder mag dit (`NOT_ADMIN`)."}
_ERROR_GROUP_ROLE = {403: "Je rol in deze groep is te smal voor deze handeling (`INSUFFICIENT_ROLE`)."}
_ERROR_SITE_ROLE = {403: "Je rol op deze site is te smal voor deze handeling (`INSUFFICIENT_ROLE`)."}
_ERROR_GROUP = {404: "Onbekende groep (`UNKNOWN_GROUP`)."}
_ERROR_SITE = {404: "Onbekende groep (`UNKNOWN_GROUP`) of onbekende site (`UNKNOWN_SITE`)."}
_ERROR_SEARCH = {422: "De zoekterm is korter dan twee tekens (`SEARCH_TOO_SHORT`)."}
_ERROR_AUDIT_FILTER = {
    422: (
        "Een filter deugt niet: de cursor is onleesbaar (`CURSOR_INVALID`), `actorPseudonym` is geen "
        "hexadecimale HMAC-waarde (`ACTOR_PSEUDONYM_INVALID`), of `limit` valt buiten 1 tot 200."
    )
}
_ERROR_AUDIT_UNAVAILABLE = {
    503: (
        "De handeling zelf schrijft een auditrij, en die schrijfactie faalde (`AUDIT_UNAVAILABLE`); "
        "er is niets teruggegeven. Probeer het later opnieuw."
    )
}
_ERROR_IDENTIFIER_AMBIGUOUS = {
    409: (
        "Het e-mailadres of de naam hoort bij meer dan één lid (`IDENTIFIER_AMBIGUOUS`); zoek bij een "
        "meerduidige naam op het e-mailadres, bij een meerduidig e-mailadres op het SSO-subject."
    )
}
_ERROR_REASON = {
    422: (
        "`reason` ontbreekt, is korter dan 10 of langer dan 500 tekens (na spaties strippen), bevat een "
        "stuur- of opmaakteken, of bevat een `@` (geen e-mailadres; noem een zaak- of ticketnummer)."
    )
}
_ERROR_LOOKUP_LIMIT = {
    429: (
        "De dagelijkse limiet voor herleidingen door deze platformbeheerder is bereikt "
        "(`LOOKUP_LIMIT_REACHED`); probeer het morgen opnieuw."
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
    return {204: {"description": f"{what} Er komt geen inhoud terug."}}


# -- Request bodies ---------------------------------------------------------


def _require_aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class AccessChoice(ApiModel):
    """Toegang bij het aanmaken: basis plus uitzonderingen. Elk veld mag weg; wat er dan geldt,
    staat bij het veld dat dit object draagt."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"base": "nobody", "keys": True}, {"base": "public"}]}
    )

    base: AccessBase | None = Field(default=None, description=f"De basis, precies één van: {ACCESS_BASE_HINT}")
    keys: bool | None = Field(
        default=None, description="Of geheime links toegang geven. " + ACCESS_EXTRAS_HINT
    )
    invitees: bool | None = Field(
        default=None, description="Of genodigden toegang geven na inloggen met SSO Rijk."
    )


class GroupCreate(ApiModel):
    """Een nieuwe groep."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"name": "Team Aurora", "slug": "aurora"},
                {"name": "Team Aurora", "slug": "aurora", "defaultAccess": {"base": "sso"}},
            ]
        }
    )

    name: str = Field(description="Weergavenaam van de groep.", examples=["Team Aurora"])
    slug: str = Field(
        description=(
            "Slug van de groep: kleine letters, cijfers en koppeltekens, hoogstens 63 tekens, en niet "
            "gereserveerd (`robots.txt`, `favicon.ico`, `.well-known`). Dit wordt het eerste "
            "padsegment van elke site-URL van de groep."
        ),
        examples=["aurora"],
    )
    default_access: AccessChoice | None = Field(
        default=None,
        description=(
            "Optioneel: de standaardtoegang waarmee de groep begint. Elk weggelaten veld, of het hele "
            "object weggelaten, krijgt de standaard: basis `site_team`, geen geheime links, geen "
            "genodigden."
        ),
    )


class SiteCreate(ApiModel):
    """Een nieuwe site binnen een groep."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"title": "Documentatie", "slug": "docs"},
                {"title": "Documentatie", "slug": "docs", "access": {"base": "public"}},
            ]
        }
    )

    title: str = Field(description="Weergavenaam van de site.", examples=["Documentatie"])
    slug: str = Field(
        description="Slug van de site: kleine letters, cijfers en koppeltekens, uniek binnen de groep.",
        examples=["docs"],
    )
    access: AccessChoice | None = Field(
        default=None,
        description=(
            "Optioneel: de toegang waarmee de site begint. Elk weggelaten veld, of het hele object "
            "weggelaten, neemt de standaardtoegang van de groep over, dus `{\"keys\": true}` zet alleen "
            "geheime links aan bovenop wat de groep al voorschrijft."
        ),
    )


class AccessBody(ApiModel):
    """Wie de content mag zien: een basis plus twee uitzonderingen."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"base": "nobody", "keys": True, "invitees": False}]}
    )

    base: AccessBase = Field(description=f"De basis, precies één van: {ACCESS_BASE_HINT}")
    keys: bool = Field(
        default=False, description="Of geheime links toegang geven. " + ACCESS_EXTRAS_HINT
    )
    invitees: bool = Field(
        default=False, description="Of genodigden toegang geven na inloggen met SSO Rijk."
    )


class ExternalSourcesBody(ApiModel):
    """Of de content van deze site externe bronnen mag laden."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"externalSources": True}]})

    external_sources: bool = Field(
        description=(
            "`true` (de standaard) laat de pagina scripts en stijlen laden van cdnjs, jsDelivr en "
            "unpkg, en lettertypen van Google Fonts. `false` laat alleen bronnen uit de site zelf "
            "toe, en is de veiligere keuze voor een vertrouwelijke pagina. Wat in beide standen "
            "geblokkeerd blijft: gegevens ophalen bij of sturen naar andere hosts, afbeeldingen "
            "van elders, een iframe, en een formulier dat elders post."
        )
    )


class SandboxBody(ApiModel):
    """Of de content van deze site afgeschermd wordt van de andere sites."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"sandbox": True}]})

    sandbox: bool = Field(
        description=(
            "`true` (de standaard) serveert de content met een CSP-sandbox zonder "
            "`allow-same-origin`, waardoor de pagina een eigen, lege herkomst krijgt: hij kan "
            "geen enkele andere site op deze hostnaam lezen, krijgt geen cookies mee en kan "
            "niets in de browser bewaren. Eigen stijlen, scripts, afbeeldingen en lettertypen "
            "laden gewoon. `false` zet de pagina terug op de gedeelde herkomst, nodig voor een "
            "site die `localStorage`, `sessionStorage` of een cookie gebruikt."
        )
    )


class LiveVersionsKeptBody(ApiModel):
    """Hoeveel vorige live-versies deze site bewaart."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"liveVersionsKept": 3}]})

    live_versions_kept: int | None = Field(
        strict=True,
        description=(
            "Het aantal vorige live-versies dat de nachtelijke opschoning naast de huidige laat "
            "staan: een geheel getal van 0 of meer. `0` bewaart alle live-versies van deze site; "
            "`null` laat de site de standaard van het platform volgen."
        ),
        json_schema_extra={"minimum": 0},
    )


class PreviewAccessBody(ApiModel):
    """Een afwijkende toegang voor een preview, of `null` om die af te zetten."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"access": {"base": "sso", "keys": False, "invitees": False}}, {"access": None}]
        }
    )

    access: AccessBody | None = Field(
        description=(
            "Toegang die alleen voor deze preview geldt: basis plus uitzonderingen in hun geheel. "
            "`null` haalt de uitzondering weg, waarna de preview de site weer volgt."
        )
    )


class IdentifierBody(ApiModel):
    """Een persoon, aangeduid met e-mailadres of SSO-subject."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"identifier": "genodigde@example.nl"}]}
    )

    identifier: str = Field(
        description="E-mailadres of SSO-subject. Hoofdletters in een e-mailadres worden genegeerd.",
        examples=["genodigde@example.nl"],
    )


class GroupMemberAdd(IdentifierBody):
    """Een persoon die aan de groep wordt toegevoegd, met de rol die hij daar krijgt."""

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
        description=f"Rol die dit lid in de groep krijgt: {ROLE_HINT} Weggelaten betekent `reader`.",
        examples=["reader"],
    )


class SiteMemberAdd(IdentifierBody):
    """Een persoon die een rol op deze ene site krijgt, naast wat een groepsrol al geeft."""

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
            f"Rol die dit lid op deze site krijgt: {ROLE_HINT} Weggelaten betekent `reader`. De rol "
            "verbreedt alleen; iemand met een ruimere groepsrol houdt die."
        ),
        examples=["reader"],
    )


class KeyCreate(ApiModel):
    """Een nieuwe geheime link."""

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
            "Waar deze link voor is; alleen voor de beheerder zelf, hij komt niet in de URL. Laat weg "
            "of leeg voor een naam met de datum van vandaag."
        ),
        examples=["reviewers"],
    )
    expires_at: datetime | None = Field(
        default=None,
        description=(
            "Tijdstip waarna de link niet meer werkt (RFC 3339). Laat weg of `null` voor de "
            f"standaardtermijn van 90 dagen; hoogstens {MAX_VALIDITY.days} dagen vooruit."
        ),
        json_schema_extra=_timestamp_schema(),
    )

    @field_validator("label")
    @classmethod
    def _plain_label(cls, value: str | None) -> str | None:
        if value is not None and _has_forbidden_characters(value):
            raise ValueError("label mag geen stuur- of opmaaktekens bevatten")
        return value

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value)


# -- Response models --------------------------------------------------------


class LanguageUpdate(ApiModel):
    """De taalkeuze van het ingelogde lid."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"language": "en"}, {"language": None}]}
    )

    language: MemberLanguage | None = Field(
        description=(
            "`nl` of `en`, of `null` om de taal weer door de browser te laten bepalen "
            "(`Accept-Language`, met Engels als het daar niet uitkomt). De keuze hangt aan het "
            "account, dus hij reist mee naar elk apparaat."
        ),
        examples=["en"],
    )


class PlatformRoleUpdate(ApiModel):
    """De nieuwe platformrol van een lid."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"platformRole": "admin"}]})

    platform_role: PlatformRole = Field(
        description="`admin` maakt het lid platformbeheerder, `member` haalt die rol er weer af.",
        examples=["admin"],
    )


class GroupRoleUpdate(ApiModel):
    """De nieuwe rol van een lid binnen een groep."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"role": "editor"}]})

    role: Role = Field(description=f"Rol die dit lid in de groep krijgt: {ROLE_HINT}", examples=["editor"])


class SiteRoleUpdate(ApiModel):
    """De nieuwe siterol van een lid."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"role": "editor"}]})

    role: Role = Field(
        description=(
            f"Rol die dit lid op deze site krijgt: {ROLE_HINT} Een smallere rol dan de groepsrol "
            "verandert niets: de ruimste van de twee blijft gelden."
        ),
        examples=["editor"],
    )


class MemberOut(ApiModel):
    """Een platformlid."""

    id: uuid.UUID = Field(description="Interne id van het lid.")
    sso_subject: str = Field(description="De `sub` uit het SSO-token; daarop hangt een sessie aan een lid.")
    email: str = Field(description="E-mailadres uit het SSO-profiel; tevens de identifier bij groepslidmaatschap.")
    name: str = Field(description="Weergavenaam uit het SSO-profiel; leeg als de identity provider die niet stuurt.")
    platform_role: PlatformRole = Field(
        description="`admin` mag platformbreed inrichten, `member` alleen binnen de eigen groepen."
    )
    status: MemberStatus = Field(
        description=(
            "`active` (mag de API gebruiken) of `deactivated` (wordt geweigerd). Alleen `active` komt "
            "langs de API."
        )
    )
    is_bootstrap: bool = Field(
        default=False,
        description=(
            "Of dit het account uit `PLAK_BOOTSTRAP_ADMIN_SUB` is. Dat account wordt bij elke login "
            "hersteld naar beheerder-en-actief, dus status en platformrol zijn er niet te wijzigen. De "
            "SPA gebruikt dit om die handelingen niet aan te bieden."
        ),
        examples=[False],
    )
    created_at: str = Field(
        description="Moment waarop het lid ontstond: het eerste bezoek aan de beheeromgeving.",
        json_schema_extra=_timestamp_schema(),
    )
    last_login_at: str | None = Field(
        default=None,
        description="Laatste succesvolle login, of `null` als die er nog niet was.",
        json_schema_extra=_timestamp_schema(),
    )


class VolumeOut(ApiModel):
    """Hoe vol het contentvolume is."""

    total_bytes: int = Field(description="Grootte van het contentvolume in bytes.", examples=[1073741824])
    used_bytes: int = Field(description="Bytes in gebruik op het volume.", examples=[536870912])
    free_bytes: int = Field(description="Bytes die nog vrij zijn op het volume.", examples=[536870912])
    reserve_bytes: int = Field(
        description=(
            "Vrije ruimte die het volume moet houden (`PLAK_STORAGE_MIN_FREE_BYTES`); daaronder wordt een "
            "deploy geweigerd. `0` betekent dat die controle uit staat."
        ),
        examples=[104857600],
    )
    max_deploy_bytes: int = Field(
        description=(
            "Grootste uitgepakte omvang van één deploy (`PLAK_INGEST_MAX_TOTAL`). Is het vrije volume kleiner "
            "dan `reserveBytes` plus dit getal, dan kan een deploy van maximale omvang niet meer."
        ),
        examples=[209715200],
    )


class MyGroupRole(ApiModel):
    """Een groep waarin het ingelogde lid een rol heeft."""

    group_slug: str = Field(description="Slug van de groep.", examples=["aurora"])
    role: Role = Field(description=f"Rol van het lid in deze groep: {ROLE_HINT}", examples=["editor"])


class MySiteRole(ApiModel):
    """Een site waarop het ingelogde lid een eigen siterol heeft."""

    group_slug: str = Field(description="Slug van de groep waar deze site in zit.", examples=["aurora"])
    site_slug: str = Field(description="Slug van de site.", examples=["docs"])
    role: Role = Field(
        description=f"De siterol zelf, los van de groepsrol: {ROLE_HINT}", examples=["editor"]
    )
    effective_role: Role = Field(
        description=(
            "Wat het lid op deze site werkelijk mag: de ruimste van zijn groepsrol en deze siterol."
        ),
        examples=["editor"],
    )


class MyProfile(MemberOut):
    """Het ingelogde lid, aangevuld met wat de SPA nodig heeft om links te bouwen."""

    content_base_url: str = Field(
        description=(
            "Origin waarop de gepubliceerde content staat. De SPA bouwt hier publieke URL's, preview-links "
            "en geheime links op. Altijd een andere host dan het beheer: content en beheer delen nooit een origin."
        ),
        examples=["https://sites.plak.example"],
    )
    group_roles: list[MyGroupRole] = Field(
        default_factory=list,
        description=(
            "Groepen waarin dit lid een rol heeft, op slug gesorteerd. Leeg als het lid nergens groepslid is."
        ),
    )
    site_roles: list[MySiteRole] = Field(
        default_factory=list,
        description=(
            "Sites waarop dit lid een eigen siterol heeft, op groep en site gesorteerd. Alleen de sites "
            "met zo'n eigen rol staan erin: op elke andere site van een groep geldt gewoon de groepsrol "
            "uit `groupRoles`."
        ),
    )
    ci_forgejo_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "De Forgejo-instanties waarvan Plak CI-ID-tokens accepteert (`PLAK_CI_FORGEJO_HOSTS`), als "
            "basis-URL. GitHub wordt altijd geaccepteerd en staat hier niet in."
        ),
        examples=[["https://code.overheid.nl"]],
    )
    ci_audience: str = Field(
        default="",
        description=(
            "De audience die een CI-workflow voor zijn ID-token moet aanvragen: precies `PLAK_BASE_URL`. Dat "
            "is ook de waarde voor de invoer `host` van de plak-action."
        ),
        examples=["https://beheer.plak.example"],
    )
    language: MemberLanguage | None = Field(
        default=None,
        description=(
            "De taal die dit lid zelf koos voor het beheer: `nl` of `en`. `null` betekent dat het lid "
            "geen keuze maakte en de SPA de taal uit de browser haalt. De keuze staat op het account en "
            "geldt dus op elk apparaat."
        ),
        examples=["en"],
    )


class GroupOut(ApiModel):
    """Een groep: eigenaar van sites en de eenheid waarop lidmaatschap telt."""

    slug: str = Field(description="Slug van de groep; het eerste padsegment van elke site-URL.", examples=["aurora"])
    name: str = Field(description="Weergavenaam van de groep.", examples=["Team Aurora"])
    default_access: AccessOut = Field(
        description=(
            "Toegang die een nieuwe site in deze groep meekrijgt. Bestaande sites veranderen "
            "niet mee."
        )
    )


class SiteOut(ApiModel):
    """Een site met de samenvatting die de SPA in lijsten toont."""

    group_slug: str = Field(description="Slug van de groep waar deze site in zit.", examples=["aurora"])
    slug: str = Field(description="Slug van de site; het tweede padsegment van de site-URL.", examples=["docs"])
    title: str = Field(description="Weergavenaam van de site.", examples=["Documentatie"])
    access: AccessOut = Field(description="Wie de live content mag zien: basis plus uitzonderingen.")
    external_sources: bool = Field(
        description=(
            "Of de content van deze site scripts, stijlen en lettertypen van een vaste lijst "
            "externe hosts mag laden. Standaard `true`; uitzetten is een extra beperking."
        )
    )
    sandbox: bool = Field(
        description=(
            "Of de content van deze site afgeschermd wordt van de andere sites op dezelfde "
            "hostnaam. Standaard `true`; uitzetten is nodig voor een site die iets in de "
            "browser bewaart."
        )
    )
    live_version_id: uuid.UUID | None = Field(
        default=None, description="Versie die nu op de publieke URL staat, of `null` als er nog niets live is."
    )
    live_versions_kept: int | None = Field(
        default=None,
        description=(
            "Eigen aantal vorige live-versies dat deze site bewaart, of `null` als de site de "
            "standaard van het platform volgt. `0` bewaart alle live-versies. Het aantal dat nu "
            "geldt staat op `GET /sites/{groupSlug}/{siteSlug}/storage`."
        ),
        examples=[3],
    )
    created_by: str = Field(
        description="Id van het lid dat de site aanmaakte; leeg als dat lid inmiddels verwijderd is."
    )
    has_live_version: bool = Field(description="Kortere vorm van `liveVersionId is not null`, handig in lijsten.")
    last_published_at: str | None = Field(
        default=None,
        description="Tijdstip van de meest recente deploy, live of preview; `null` als er nog niets is gedeployd.",
        json_schema_extra=_timestamp_schema(),
    )
    preview_count: int = Field(description="Aantal previews dat nu voor deze site bestaat.", examples=[2])


class VersionOut(ApiModel):
    """Een gedeployde versie: een uitgepakte bundel die live kan staan of aan een preview kan hangen."""

    id: uuid.UUID = Field(description="Interne id van de versie; hiermee rol je terug.")
    site_slug: str = Field(description="Slug van de site.", examples=["docs"])
    group_slug: str = Field(description="Slug van de groep.", examples=["aurora"])
    target: VersionTarget = Field(description="`live` voor de publieke URL, `preview` voor een preview-ref.")
    storage_ref: str = Field(description="Interne verwijzing naar de uitgepakte bestandsboom op schijf.")
    origin: Literal["upload", "action"] = Field(
        description=(
            "`upload` als een lid deze versie zelf publiceerde (in het beheer of met de CLI), `action` als "
            "CI hem publiceerde vanuit de gekoppelde repository."
        )
    )
    created_by_member: uuid.UUID | None = Field(
        default=None, description="Lid dat deployde, of `null` bij een deploy vanuit CI."
    )
    created_by_name: str | None = Field(
        default=None,
        description=(
            "Naam van het lid dat deployde, of zijn e-mailadres als de identity provider geen naam "
            "stuurde. `null` bij een deploy vanuit CI. Zonder dit veld heeft een lijst alleen het "
            "interne id om mee te tonen, en dat zegt een lezer niets."
        ),
        examples=["Sanne Jansen"],
    )
    created_by_repository: str | None = Field(
        default=None,
        description=(
            "Repository waaruit CI deze versie publiceerde, als host plus `eigenaar/repo`; `null` bij een "
            "deploy door een lid."
        ),
        examples=["github.com/minbzk/website"],
    )
    created_at: str | None = Field(
        default=None, description="Tijdstip van de deploy.", json_schema_extra=_timestamp_schema()
    )
    is_live: bool = Field(description="Of deze versie op dit moment de live versie van de site is.")


class SiteStorageOut(ApiModel):
    """Wat een site op het contentvolume inneemt, en hoeveel live-versies er bewaard blijven."""

    used_bytes: int = Field(
        description="Wat alle versies van deze site samen innemen op het contentvolume, live en preview, in bytes.",
        examples=[77594624],
    )
    max_bytes: int = Field(
        description=(
            "Hoeveel alle versies van een site samen mogen innemen, in bytes. Een deploy die daar "
            "overheen zou gaan krijgt 413 (`SITE_QUOTA_EXCEEDED`). `0` betekent geen limiet."
        ),
        examples=[524288000],
    )
    live_versions_kept: int = Field(
        description=(
            "Hoeveel vorige live-versies van deze site naast de huidige bewaard blijven: het eigen "
            "aantal van de site, of anders de standaard van het platform. Oudere live-versies ruimt "
            "de nachtelijke opschoning op, rij en bestanden. `0` betekent dat alle versies blijven."
        ),
        examples=[5],
    )
    live_versions_kept_is_default: bool = Field(
        description=(
            "`true` als de site de standaard van het platform volgt, `false` als een sitebeheerder "
            "een eigen aantal instelde."
        ),
        examples=[True],
    )
    default_live_versions_kept: int = Field(
        description=(
            "De standaard van het platform: hoeveel vorige live-versies een site zonder eigen "
            "aantal bewaart. `0` betekent dat zulke sites alle versies bewaren."
        ),
        examples=[5],
    )


class PreviewOut(ApiModel):
    """Een preview: een tweede, tijdelijke uitgave van een site naast de live versie."""

    site_slug: str = Field(description="Slug van de site.", examples=["docs"])
    group_slug: str = Field(description="Slug van de groep.", examples=["aurora"])
    ref: str = Field(
        description="Naam van de preview, meestal het pull-requestnummer of de branchnaam als slug.",
        examples=["pr-42"],
    )
    version_id: uuid.UUID = Field(description="Versie die op deze preview staat.")
    access_override: AccessOut | None = Field(
        default=None,
        description=(
            "Toegang die alleen voor deze preview geldt; `null` betekent: volgt de site."
        ),
    )
    last_updated_at: str | None = Field(
        default=None,
        description="Tijdstip van de laatste deploy naar deze preview.",
        json_schema_extra=_timestamp_schema(),
    )
    expires_at: str | None = Field(
        default=None,
        description=(
            "Tijdstip waarop de opruimjob deze preview weggooit. Elke nieuwe deploy naar dezelfde ref schuift "
            "het vooruit; `null` betekent dat hij niet automatisch verloopt."
        ),
        json_schema_extra=_timestamp_schema(),
    )
    url: str = Field(
        description="Pad van de preview op de content-origin uit `contentBaseUrl`.",
        examples=["/aurora/docs/_preview/pr-42/"],
    )


class InviteeOut(ApiModel):
    """Een adres op de genodigdenlijst van een site."""

    id: str = Field(
        description="Id van deze genodigde; hiermee haal je hem van de lijst.",
        examples=["3f2a1c6e-9b4d-4f2a-8c1e-7d5b2a9f4c31"],
    )
    site_slug: str = Field(description="Slug van de site.", examples=["docs"])
    group_slug: str = Field(description="Slug van de groep.", examples=["aurora"])
    identifier: str = Field(
        description="E-mailadres of SSO-subject van de genodigde, genormaliseerd naar kleine letters.",
        examples=["genodigde@example.nl"],
    )
    added_by: str = Field(
        description="Id van het lid dat de genodigde toevoegde; leeg als dat lid inmiddels verwijderd is."
    )
    added_at: str | None = Field(
        default=None, description="Tijdstip van toevoegen.", json_schema_extra=_timestamp_schema()
    )


class KeyOut(ApiModel):
    """Een geheime link, zonder het geheim zelf."""

    site_slug: str = Field(description="Slug van de site.", examples=["docs"])
    group_slug: str = Field(description="Slug van de groep.", examples=["aurora"])
    label: str = Field(description="Waar deze link voor is; alleen voor de beheerder.", examples=["reviewers"])
    selector: str = Field(
        description="Eerste helft van de sleutelwaarde: de niet-geheime helft, waarmee je de sleutel opzoekt.",
        examples=["a1b2c3d4"],
    )
    status: KeyStatus = Field(description="`active` (werkt) of `revoked` (werkt niet meer).")
    created_at: str | None = Field(
        default=None, description="Tijdstip van aanmaken.", json_schema_extra=_timestamp_schema()
    )
    expires_at: str | None = Field(
        default=None,
        description="Tijdstip waarna de link niet meer werkt.",
        json_schema_extra=_timestamp_schema(),
    )


class KeyCreated(ApiModel):
    """De verse sleutel plus haar waarde. Dit is het enige moment waarop de waarde te zien is."""

    key: KeyOut = Field(description="De aangemaakte sleutel.")
    value: str = Field(
        description=(
            "De volledige sleutelwaarde `<selector>.<geheim>` voor in de link. Plak bewaart alleen een hash, "
            "dus deze waarde is hierna nergens meer op te vragen."
        ),
        examples=["a1b2c3d4.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"],
    )


class GroupSiteRole(ApiModel):
    """Een eigen rol op één site, altijd een site in de groep waar je naar kijkt."""

    site_slug: str = Field(description="Slug van de site binnen deze groep.", examples=["jaarverslag"])
    site_title: str = Field(description="Titel van de site, zoals die in het beheer staat.")
    role: Role = Field(
        description=f"Rol die alleen op deze site geldt: {ROLE_HINT}",
        examples=["editor"],
    )


class GroupMemberOut(ApiModel):
    """Een lid van een groep, zoals de ledenlijst van die groep het toont."""

    group_slug: str = Field(description="Slug van de groep.", examples=["aurora"])
    member_id: str = Field(
        description="Id van het platformlid; hiermee haal je het uit de groep.",
        examples=["3f2a1c6e-9b4d-4f2a-8c1e-7d5b2a9f4c31"],
    )
    identifier: str = Field(
        description="Waarmee je dit lid aan de groep toevoegt of zijn rol wijzigt: het e-mailadres.",
        examples=["lid@example.nl"],
    )
    name: str = Field(description="Weergavenaam uit het SSO-profiel; leeg als die ontbreekt.")
    email: str = Field(description="E-mailadres uit het SSO-profiel.", examples=["lid@example.nl"])
    role: Role = Field(
        description=f"Rol van dit lid in deze groep: {ROLE_HINT}",
        examples=["reader"],
    )
    site_roles: list[GroupSiteRole] = Field(
        description=(
            "De sites in déze groep waarop dit lid een eigen rol heeft, op slug gesorteerd. Zo'n rol "
            "staat los van de groepsrol en blijft gelden als het lid uit de groep gaat, tenzij je hem "
            "meeneemt (`siteRoles=remove` bij het verwijderen).\n\n"
            "Wat dit lid in een andere groep heeft staat er niet bij: dat hoort bij die groep."
        ),
    )


class SiteMemberOut(ApiModel):
    """Een rij van de ledenlijst van een site: iedereen die bij deze site kan."""

    group_slug: str = Field(description="Slug van de groep.", examples=["aurora"])
    site_slug: str = Field(description="Slug van de site.", examples=["docs"])
    member_id: str = Field(
        description="Id van het platformlid; hiermee haal je zijn siterol weg.",
        examples=["3f2a1c6e-9b4d-4f2a-8c1e-7d5b2a9f4c31"],
    )
    identifier: str = Field(
        description="Waarmee je de siterol van dit lid zet: het e-mailadres.",
        examples=["lid@example.nl"],
    )
    name: str = Field(description="Weergavenaam uit het SSO-profiel; leeg als die ontbreekt.")
    email: str = Field(description="E-mailadres uit het SSO-profiel.", examples=["lid@example.nl"])
    group_role: Role | None = Field(
        default=None,
        description=(
            f"Rol in de groep van deze site, of `null` als dit lid geen groepslid is: {ROLE_HINT} "
            "Deze rol wijzig je bij de groep, niet hier."
        ),
        examples=["reader"],
    )
    site_role: Role | None = Field(
        default=None,
        description=(
            "Rol die alleen op deze site geldt, of `null` als dit lid er geen heeft. Dit is het enige "
            "veld dat de siteroutes wijzigen."
        ),
        examples=["editor"],
    )
    effective_role: Role = Field(
        description="Wat dit lid hier werkelijk mag: de ruimste van `groupRole` en `siteRole`.",
        examples=["editor"],
    )


class MemberSearchOut(ApiModel):
    """Een gevonden platformlid, zoals het zoekveld bij 'lid toevoegen' het toont."""

    identifier: str = Field(
        description="Waarmee je dit lid toevoegt: het e-mailadres.",
        examples=["lid@example.nl"],
    )
    name: str = Field(description="Weergavenaam uit het SSO-profiel; leeg als die ontbreekt.")
    email: str = Field(description="E-mailadres uit het SSO-profiel.", examples=["lid@example.nl"])
    already_member: bool = Field(
        description=(
            "Of dit lid hier al een eigen rol heeft: een groepsrol bij het zoekveld van een groep, "
            "een siterol bij dat van een site. Toevoegen doe je dan niet meer; de rol wijzig je in "
            "de ledenlijst."
        ),
        examples=[False],
    )
    group_role: Role | None = Field(
        default=None,
        description=(
            "Alleen bij het zoekveld van een site: de rol waarmee dit lid deze site nu al via de "
            f"groep bereikt, of `null` als het geen groepslid is: {ROLE_HINT} Een siterol verruimt "
            "alleen, dus een even smalle of smallere siterol verandert hier niets aan. Bij het "
            "zoekveld van een groep is dit veld altijd `null`."
        ),
        examples=["reader"],
    )


class GroupRow(ApiModel):
    """Een groep met haar sites, zoals het overzicht die toont."""

    group: GroupOut = Field(description="De groep zelf.")
    sites: list[SiteOut] = Field(
        description=(
            "De sites van de groep die dit lid mag zien, op slug gesorteerd. Dat zijn ze alle, tenzij het "
            "lid de groep alleen via een siterol bereikt: dan staan alleen die sites erin."
        )
    )


class Overview(ApiModel):
    """Het startscherm van de beheer-SPA."""

    groups: list[GroupRow] = Field(
        description=(
            "Groepen die dit lid mag zien, op slug gesorteerd: waar het een groepsrol heeft, en waar het "
            "een rol op een site heeft. Leeg als het lid nergens een rol heeft."
        )
    )


class GroupDetail(ApiModel):
    """Alles wat de groepspagina van de SPA in één keer nodig heeft."""

    group: GroupOut = Field(description="De groep zelf.")
    sites: list[SiteOut] = Field(description="Sites van de groep, op slug gesorteerd.")
    members: list[GroupMemberOut] = Field(description="Leden van de groep, op e-mailadres gesorteerd.")


LIVE_BRANCH_MAX_LENGTH = 255


class SiteRepositoryBody(ApiModel):
    """De repository waaruit CI naar deze site mag publiceren."""

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

    provider: CiProvider = Field(description="`github` of `forgejo`.", examples=["github"])
    host: str | None = Field(
        default=None,
        description=(
            "Basis-URL van de Forgejo-instantie, een van `ciForgejoHosts` uit `GET /me`. Verplicht bij "
            "`forgejo`; bij `github` weglaten (of `https://github.com`)."
        ),
        examples=["https://code.overheid.nl"],
    )
    owner: str = Field(description="Eigenaar van de repository: gebruiker of organisatie.", examples=["minbzk"])
    repo: str = Field(description="Naam van de repository.", examples=["website"])
    live_branch: str | None = Field(
        description=(
            "De enige branch die live mag publiceren, zonder `refs/heads/`. `null` of leeg: elke branch mag "
            "live. Live gaat hoe dan ook alleen vanuit `push`, `workflow_dispatch` of `schedule`. Previews "
            "en het opruimen ervan mogen altijd vanaf elke branch."
        ),
        examples=["main"],
    )
    repository_id: int | None = Field(
        default=None,
        description=(
            "Numeriek id van de repository, alleen nodig als Plak haar niet kan opzoeken omdat ze privé "
            "is. Samen met `ownerId`, of allebei weglaten. Op te vragen met "
            "`gh api repos/{owner}/{repo} --jq '.id, .owner.id'`."
        ),
        examples=[123456],
    )
    owner_id: int | None = Field(
        default=None,
        description="Numeriek id van de eigenaar, samen met `repositoryId`.",
        examples=[7890],
    )


class SiteRepositoryOut(ApiModel):
    """De gekoppelde repository van een site."""

    group_slug: str = Field(description="Slug van de groep.", examples=["aurora"])
    site_slug: str = Field(description="Slug van de site.", examples=["docs"])
    provider: CiProvider = Field(description="`github` of `forgejo`.")
    host: str = Field(description="Basis-URL van de provider.", examples=["https://github.com"])
    owner: str = Field(description="Eigenaar zoals de provider hem spelt.", examples=["minbzk"])
    repo: str = Field(description="Repository zoals de provider haar spelt.", examples=["website"])
    repository_id: int = Field(
        description="Numeriek id van de repository bij de provider; overleeft een hernoeming.", examples=[123456]
    )
    owner_id: int = Field(description="Numeriek id van de eigenaar bij de provider.", examples=[7890])
    live_branch: str | None = Field(
        default=None, description="De enige branch die live mag publiceren, of `null`: elke branch.", examples=["main"]
    )
    ids_confirmed: bool = Field(
        description=(
            "Of de ids bevestigd zijn: door de provider bij het koppelen, of door een CI-ID-token dat ze allebei "
            "droeg. `false` zolang ze alleen zijn zoals ze zijn ingevuld; de naam kan dan ook nog afwijken."
        ),
        examples=[True],
    )
    created_by: str = Field(
        description="Naam of e-mailadres van wie de koppeling maakte; leeg als dat lid verwijderd is."
    )
    created_at: str | None = Field(
        default=None, description="Tijdstip van koppelen.", json_schema_extra=_timestamp_schema()
    )


class UserCodeBody(ApiModel):
    """De code uit de terminal van `plak login`."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"userCode": "WDJB-MJHT"}]})

    user_code: str = Field(
        max_length=32,
        description="De gebruikerscode, met of zonder koppelteken, hoofdletterongevoelig.",
        examples=["WDJB-MJHT"],
    )


class DeviceAuthorizationPendingOut(ApiModel):
    """Een openstaande CLI-login, zoals het goedkeuringsscherm hem toont."""

    user_code: str = Field(description="De gebruikerscode, genormaliseerd.", examples=["WDJB-MJHT"])
    client_name: str | None = Field(
        default=None, description="Hoe de CLI zichzelf noemde; `null` als hij niets opgaf.", examples=["plak-cli 1.2"]
    )
    ip_truncated: str | None = Field(
        default=None,
        description="Het netwerk waarvandaan de CLI de login begon (IPv4 /24, IPv6 /48).",
        examples=["203.0.113.0/24"],
    )
    created_at: str | None = Field(
        default=None, description="Wanneer de CLI de login begon.", json_schema_extra=_timestamp_schema()
    )
    expires_at: str | None = Field(
        default=None, description="Wanneer de code verloopt.", json_schema_extra=_timestamp_schema()
    )
    same_network: bool | None = Field(
        default=None,
        description=(
            "Of de CLI de login begon vanaf hetzelfde afgekapte netwerk (IPv4 /24, IPv6 /48) als waarvandaan "
            "het lid nu goedkeurt. `false` is een reden om extra op te letten: iemand anders kan de code "
            "gestuurd hebben. `null` als een van beide adressen onbekend is. Er komt geen volledig IP-adres "
            "in het antwoord."
        ),
        examples=[True],
    )


class CliSessionOut(ApiModel):
    """Een gekoppelde sessie: een goedgekeurde `plak login` van het ingelogde lid."""

    id: uuid.UUID = Field(description="Id van de CLI-sessie; hiermee trek je hem in.")
    client_name: str | None = Field(
        default=None, description="Hoe de CLI zichzelf noemde bij het koppelen.", examples=["plak-cli 1.2 on macOS"]
    )
    created_at: str | None = Field(
        default=None, description="Tijdstip van koppelen.", json_schema_extra=_timestamp_schema()
    )
    last_used_at: str | None = Field(
        default=None,
        description="Laatste keer dat de CLI iets deed of verversde; `null` als dat nog niet gebeurde.",
        json_schema_extra=_timestamp_schema(),
    )
    expires_at: str | None = Field(
        default=None,
        description="Wanneer de sessie verloopt zonder verder gebruik.",
        json_schema_extra=_timestamp_schema(),
    )


AUDIT_PAGE_DEFAULT = 50
AUDIT_PAGE_MAX = 200
AUDIT_PSEUDONYM_LENGTH = 64
REASON_MIN_LENGTH = 10
REASON_MAX_LENGTH = 500


class AuditFilters(ApiModel):
    """Filters op het auditlog. Alles is optioneel en alles combineert met EN."""

    limit: int = Field(
        default=AUDIT_PAGE_DEFAULT,
        ge=1,
        le=AUDIT_PAGE_MAX,
        description=f"Aantal regels per pagina, 1 tot {AUDIT_PAGE_MAX}.",
        examples=[50],
    )
    cursor: str | None = Field(
        default=None,
        description=(
            "De `nextCursor` uit het vorige antwoord, ongewijzigd overgenomen. Laat hem weg voor de "
            "eerste pagina."
        ),
    )
    since: datetime | None = Field(
        default=None,
        description="Alleen regels vanaf dit tijdstip (RFC 3339, inclusief). Zonder tijdzone geldt UTC.",
        examples=["2026-09-01T00:00:00Z"],
    )
    until: datetime | None = Field(
        default=None,
        description="Alleen regels van vóór dit tijdstip (RFC 3339, exclusief). Zonder tijdzone geldt UTC.",
        examples=["2026-09-19T00:00:00Z"],
    )
    action: str | None = Field(
        default=None,
        description="Exacte handeling, bijvoorbeeld `content_access`, `deploy` of `site_create`.",
        examples=["content_access"],
    )
    result: str | None = Field(
        default=None,
        description="Exacte uitkomst: `allowed`, `refused` of `login_redirect`.",
        examples=["refused"],
    )
    reason_code: str | None = Field(
        default=None,
        description="Exacte redencode achter de uitkomst, bijvoorbeeld `UNKNOWN_SITE`.",
        examples=["UNKNOWN_SITE"],
    )
    group: str | None = Field(
        default=None, description="Slug van de groep waar de regel over gaat.", examples=["aurora"]
    )
    site: str | None = Field(
        default=None, description="Slug van de site waar de regel over gaat.", examples=["docs"]
    )
    actor_pseudonym: str | None = Field(
        default=None,
        description=(
            "Pseudoniem van één actor, 64 hexadecimale tekens. Haal het op met "
            "`POST /platform/audit/actor-pseudonym`. Een e-mailadres hoort hier niet: een queryparameter "
            "belandt in proxylogs."
        ),
    )


class AuditEntryOut(ApiModel):
    """Eén regel uit het auditlog: een handeling, wie hem deed en hoe hij afliep."""

    id: uuid.UUID = Field(description="Id van de auditregel; ook de tweede sorteersleutel achter `occurredAt`.")
    occurred_at: str | None = Field(
        default=None,
        description="Tijdstip van de handeling.",
        json_schema_extra=_timestamp_schema(),
    )
    actor_kind: ActorKind = Field(
        description=(
            "Soort actor: `member` (een ingelogd lid, ook via de CLI), `ci` (een CI-workflow met een ID-token), "
            "`system` (de applicatie zelf) "
            "of `anonymous` (een bezoeker zonder sessie)."
        )
    )
    actor_pseudonym: str | None = Field(
        default=None,
        description=(
            "Pseudoniem van de actor: HMAC-SHA256 van zijn identifier onder de audit-pepper. Gelijke "
            "pseudoniemen betekenen dezelfde actor, zolang de pepper niet gewisseld is. `null` bij "
            "`system` en `anonymous`."
        ),
    )
    action: str = Field(
        description="Wat er gebeurde, bijvoorbeeld `content_access` of `site_create`.",
        examples=["content_access"],
    )
    result: str = Field(
        description="Hoe het afliep: `allowed`, `refused` of `login_redirect`.", examples=["refused"]
    )
    reason_code: str | None = Field(
        default=None,
        description="Reden achter de uitkomst, bijvoorbeeld `UNKNOWN_SITE`.",
        examples=["UNKNOWN_SITE"],
    )
    refs: dict[str, Any] | None = Field(
        default=None,
        description="Waar de handeling over ging: sleutels als `group`, `site`, `preview` en `path`.",
        examples=[{"group": "aurora", "site": "docs"}],
    )
    ip_truncated: str | None = Field(
        default=None,
        description="Netwerk van de bezoeker, afgeknot op /24 (IPv4) of /48 (IPv6); nooit het hele adres.",
        examples=["203.0.113.0/24"],
    )


class AuditPage(ApiModel):
    """Eén pagina uit het auditlog, nieuwste regel eerst."""

    entries: list[AuditEntryOut] = Field(description="De regels van deze pagina, nieuwste eerst.")
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Ondoorzichtige verwijzing naar de volgende pagina; geef hem ongewijzigd terug als `cursor`. "
            "`null` betekent dat dit de laatste pagina was."
        ),
    )


class ReasonField(ApiModel):
    """Verplichte motivatie bij het herleiden van een pseudoniem of IP-adres (spec §12): komt ongewijzigd
    in de auditrij van die herleiding te staan (zichtbaar voor elke platformbeheerder die het auditlog
    leest), en telt mee voor de dagelijkse limiet op zulke herleidingen."""

    reason: str = Field(
        description=(
            f"Waarom deze herleiding nodig is, {REASON_MIN_LENGTH} tot {REASON_MAX_LENGTH} tekens. Noem "
            "een zaak- of ticketnummer, geen e-mailadres of andere persoonsgegevens: dit veld komt "
            "leesbaar in het auditlog te staan, voor elke platformbeheerder."
        ),
        examples=["onderzoek naar melding 2026-091"],
    )

    @field_validator("reason")
    @classmethod
    def _reason_length(cls, value: str) -> str:
        normalised = value.strip()
        if not (REASON_MIN_LENGTH <= len(normalised) <= REASON_MAX_LENGTH):
            raise ValueError(
                f"reason moet, na spaties strippen, {REASON_MIN_LENGTH} tot {REASON_MAX_LENGTH} tekens zijn"
            )
        if _has_forbidden_characters(normalised):
            raise ValueError("reason mag geen stuur- of opmaaktekens bevatten")
        if "@" in normalised:
            raise ValueError("noem een zaak- of ticketnummer in reason, geen e-mailadres")
        return normalised


class ActorLookup(ReasonField):
    """De identifier van een actor, om zijn auditpseudoniem mee op te zoeken."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"identifier": "lid@example.nl", "reason": "onderzoek naar melding 2026-091"}]
        }
    )

    identifier: str = Field(
        description=(
            "E-mailadres of SSO-subject van een lid of content-viewer, of een gekoppelde repository als "
            "`eigenaar/repo`, `github.com/eigenaar/repo` of `https://code.overheid.nl/eigenaar/repo`. "
            "Een e-mailadres wordt eerst naar het SSO-subject van dat lid of die viewer vertaald, want "
            "daarop is geaudit."
        ),
        examples=["lid@example.nl"],
    )


class ActorPseudonymOut(ApiModel):
    """Het auditpseudoniem dat bij een identifier hoort."""

    actor_pseudonym: str = Field(
        description="Waarde om als `actorPseudonym` mee te filteren op `GET /platform/audit`."
    )
    resolved_as: Literal["member", "content_viewer", "ci", "unknown"] = Field(
        description=(
            "Waar de identifier bij hoorde: `member` (vertaald naar zijn SSO-subject), `content_viewer` "
            "(een SSO-viewer van afgeschermde content, gezien in de laatste 90 dagen), `ci` "
            "(een repository die aan een site gekoppeld is) of `unknown` (letterlijk gepseudonimiseerd, alleen bij een "
            "niet-e-mailadres: een e-mailadres dat nergens bij hoort geeft een 404)."
        )
    )


class ActorIdentityLookup(ReasonField):
    """Een auditpseudoniem, om de actor erachter mee op te zoeken."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"actorPseudonym": "a" * AUDIT_PSEUDONYM_LENGTH, "reason": "onderzoek naar melding 2026-091"}
            ]
        }
    )

    actor_pseudonym: str = Field(
        description=f"Het pseudoniem uit het auditlog: {AUDIT_PSEUDONYM_LENGTH} hexadecimale tekens."
    )


class ActorIdentityOut(ApiModel):
    """De actor achter een auditpseudoniem."""

    kind: Literal["member", "content_viewer", "ci"] = Field(
        description="Of het pseudoniem bij een lid, een content-viewer of een CI-repository hoort."
    )
    member_id: uuid.UUID | None = Field(default=None, description="Interne id van het lid, bij kind `member`.")
    email: str | None = Field(
        default=None,
        description="E-mailadres, bij kind `member` of `content_viewer`; `null` als er geen bekend is.",
    )
    email_verified: bool | None = Field(
        default=None,
        description=(
            "Of `email` een geverifieerde claim van de identity provider was, bij kind `content_viewer`. "
            "De voorwaartse zoekslag (`actor-pseudonym`) matcht alleen op een geverifieerd e-mailadres."
        ),
    )
    name: str | None = Field(
        default=None,
        description="Weergavenaam van het lid, bij kind `member`; leeg als de identity provider die niet stuurt.",
    )
    member_status: MemberStatus | None = Field(default=None, description="Status van het lid, bij kind `member`.")
    last_seen_at: str | None = Field(
        default=None,
        description="Laatste keer inloggen op de content-host, bij kind `content_viewer`.",
        json_schema_extra=_timestamp_schema(),
    )
    provider: Literal["github", "forgejo"] | None = Field(
        default=None, description="CI-provider van de repository, bij kind `ci`."
    )
    host: str | None = Field(
        default=None,
        description="Basis-URL van de provider, bij kind `ci`.",
        examples=["https://github.com"],
    )
    repository: str | None = Field(
        default=None, description="De repository als `eigenaar/repo`, bij kind `ci`.", examples=["minbzk/website"]
    )
    sites: list[str] | None = Field(
        default=None,
        description=(
            "De sites waaraan deze repository nu gekoppeld is, als `groep/site`, bij kind `ci`. Een "
            "ontkoppelde repository is niet meer te herleiden."
        ),
        examples=[["aurora/docs"]],
    )


class IpRevealBody(ReasonField):
    """Motivatie om het volledige IP-adres bij één auditregel te ontsleutelen."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"reason": "onderzoek naar melding 2026-091"}]}
    )


class IpRevealOut(ApiModel):
    """Het volledige IP-adres achter één auditregel."""

    ip: str = Field(description="Het volledige IP-adres zoals opgeslagen bij deze auditregel.")


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
    """Only the sites with a site role of their own; everywhere else the group role stands on its own."""
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
        super().__init__(f"{matches} verschillende subjecten voor deze identifier")


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


_ERROR_REPOSITORY_NOT_SET = {404: "Aan deze site is geen repository gekoppeld (`REPOSITORY_NOT_SET`)."}
_ERROR_APPROVAL = {
    401: (
        "De beheersessie is ouder dan een kwartier (`SESSION_NOT_FRESH`): log opnieuw in en probeer "
        "het nog eens."
    ),
    404: "De code is onbekend, verlopen of al afgehandeld (`USER_CODE_UNKNOWN`).",
    429: "Te veel pogingen door dit lid in korte tijd (`TOO_MANY_ATTEMPTS`).",
}
_ERROR_CLI_TOKEN = {
    401: (
        "Met een Bearer-header: het is geen CLI-token uit `plak login`, of het is ongeldig, ingetrokken of "
        "verlopen (`TOKEN_INVALID`); het antwoord draagt dan `WWW-Authenticate: Bearer`."
    ),
    403: "Met een CLI-token: het lid is niet (meer) actief (`MEMBER_NOT_ACTIVE`).",
}
_ERROR_CREATIONS = {
    429: (
        f"Dit lid heeft in het afgelopen uur al {CREATION_MAX_PER_WINDOW} groepen en sites samen aangemaakt "
        "(`TOO_MANY_CREATIONS`); de header `Retry-After` zegt na hoeveel seconden het weer kan."
    )
}
_CREATION_RULE = (
    "Behalve met de beheersessie en een geldige CSRF-header mag dit ook met een CLI-token uit "
    "`plak login` (`Authorization: Bearer plakcli_...`), met precies dezelfde rolcontrole; de CSRF-header "
    "vervalt dan, want een token gaat niet vanzelf mee zoals een cookie. Een CI-ID-token mag het niet. "
    f"Per lid geldt een limiet van {CREATION_MAX_PER_WINDOW} nieuwe groepen en sites samen per "
    f"{CREATION_WINDOW_S // 60} minuten, via beheer en CLI samen."
)
_APPROVAL_RULE = (
    "**Mag:** elk actief lid, met een beheersessie van hoogstens een kwartier oud en een geldige "
    "CSRF-header. Per lid tellen opzoeken, goedkeuren en weigeren samen voor een limiet van "
    f"{CLI_APPROVAL_MAX_ATTEMPTS} per {CLI_APPROVAL_WINDOW_S // 60} minuten."
)


def make_admin_router() -> APIRouter:
    router = APIRouter(prefix="/-/api/v1", dependencies=[Depends(require_admin_origin)])

    # -- Session --

    @router.get(
        "/me",
        tags=[TAG_SESSION],
        summary="Het ingelogde lid",
        response_description="Het lid achter de huidige sessie, met de content-origin.",
        description=(
            "Geeft het lid achter de huidige beheersessie, plus de origin waarop de SPA content-, preview- "
            "en sleutel-links bouwt. Dit is meteen de goedkoopste manier om te controleren of de sessie nog "
            "geldig is.\n\n"
            "`groupRoles` en `siteRoles` zeggen waar dit lid iets mag, zodat de SPA weet wat ze aanbiedt. "
            "`siteRoles` bevat alleen de sites met een eigen siterol; op elke andere site van een groep "
            "geldt de groepsrol uit `groupRoles`. Een platformbeheerder kan beide lijsten leeg hebben: hij "
            "beheert mensen en groepen, en geeft zichzelf voor content een zichtbare groepsrol.\n\n"
            "**Mag:** elk actief lid met een geldige beheersessie. Een lid met status `deactivated` "
            "krijgt 403, net als op elk ander endpoint."
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
        summary="Mijn taal instellen",
        description=(
            "Legt vast in welke taal dit lid het beheer wil lezen: `nl`, `en`, of `null` om de taal "
            "weer aan de browser over te laten. De keuze staat op het account, niet op het apparaat, "
            "dus hij geldt overal waar dit lid inlogt.\n\n"
            "Wat de SPA erna doet is de taal meesturen in `Accept-Language`, zodat ook een "
            "problem+json-melding in die taal terugkomt.\n\n"
            "**Mag:** elk actief lid, voor zichzelf, met een geldige CSRF-header."
        ),
        responses=_deleted("De taalkeuze is vastgelegd.")
        | _errors(_ERROR_CSRF, {422: "`language` is geen ondersteunde taal en niet `null`."}),
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
        summary="Alle zichtbare groepen met hun sites",
        response_description="De groepen die dit lid mag zien, elk met hun sites.",
        description=(
            "Het startscherm van de beheer-SPA: per groep de sites met hun zichtbaarheid, of er iets live "
            "staat, wanneer er voor het laatst is gedeployd en hoeveel previews er openstaan.\n\n"
            "**Mag:** elk actief lid. Het lid ziet de groepen waar het een groepsrol in heeft, elk met al "
            "hun sites, plus de groepen "
            "waar het alleen een siterol heeft: daarvan verschijnen uitsluitend die sites, want een "
            "siterol geeft niets op groepsniveau. Een platformbeheerder ziet net zo alleen zijn eigen "
            "groepen. Wie nergens een rol heeft krijgt een lege lijst, geen 403."
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
        summary="Groep aanmaken",
        response_description="De aangemaakte groep.",
        description=(
            "Maakt een groep aan en maakt de aanmaker meteen groepsbeheerder (`admin`), zodat hij er sites "
            "in kan zetten. De nieuwe groep begint met basis `site_team` en geen uitzonderingen, tenzij "
            "`defaultAccess` iets anders vraagt; dat is daarna te wijzigen. Omdat de aanmaker groepsbeheerder "
            "wordt, is de standaardtoegang meteen kiezen niets meer dan hij daarna zelf ook mag.\n\n"
            "**Mag:** ieder actief lid, met een geldige CSRF-header. Een groep aanmaken is geen "
            "voorbehouden handeling: wie iets wil publiceren moet daar zelf een plek voor kunnen maken. "
            + _CREATION_RULE
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_CLI_TOKEN,
            _ERROR_CREATIONS,
            {409: "Er bestaat al een groep met deze slug (`SLUG_EXISTS`)."},
            {
                422: (
                    "De slug is ongeldig of gereserveerd (`SLUG_INVALID`), of de naam is leeg "
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
        summary="Groep met sites en leden",
        response_description="De groep met haar sites en leden.",
        description=(
            "Alles wat de groepspagina van de SPA in één keer nodig heeft.\n\n"
            "**Mag:** groepsrol `reader` of ruimer, of een platformbeheerder."
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
        summary="Groep verwijderen",
        description=(
            "Verwijdert de groep met al haar sites, en per site alles wat `DELETE /sites/{group}/{site}` "
            "ook weghaalt: versies, previews, genodigden, geheime links, de gekoppelde repository en de "
            "uitgepakte bestanden op schijf. Onomkeerbaar; elke URL van de groep geeft daarna 404.\n\n"
            "**Mag:** groepsrol `admin`, met een geldige CSRF-header. Een platformbeheerder die geen "
            "groepsrol heeft mag het niet."
        ),
        responses=_deleted("De groep en al haar sites zijn verwijderd.")
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
        summary="Standaardtoegang van een groep zetten",
        response_description="De groep met haar nieuwe standaardtoegang.",
        description=(
            "Zet de toegang die sites meekrijgen die hierna in deze groep worden aangemaakt: de "
            "basis en de twee uitzonderingen in één keer. Bestaande sites veranderen niet mee; "
            "die zet je per site.\n\n"
            "**Mag:** groepsrol `admin`, met een geldige CSRF-header. Dit is beleid over content, dus een "
            "platformbeheerder die geen groepsrol heeft mag het niet."
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
        summary="Site aanmaken",
        response_description="Het aangemaakte site.",
        description=(
            "Maakt een site binnen een groep. De site krijgt de standaardzichtbaarheid van de groep, of wat "
            "`access` daarvan afwijkend vraagt, en staat op `/{groupSlug}/{siteSlug}/` op de content-origin, "
            "zodra er iets naartoe is gedeployd.\n\n"
            "**Mag:** groepsrol `editor` of ruimer, met een geldige CSRF-header; de maker wordt `admin` van "
            "de site die hij aanmaakt, en mag de toegang dus meteen kiezen: dat is niets meer dan hij daarna "
            "zelf ook mag. Een platformbeheerder die geen groepsrol heeft, mag dit niet. " + _CREATION_RULE
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_CLI_TOKEN,
            _ERROR_CREATIONS,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            {409: "Er bestaat al een site met deze slug in deze groep (`SLUG_EXISTS`)."},
            {422: "De slug is ongeldig (`SLUG_INVALID`), of de titel is leeg (`FIELD_EMPTY`)."},
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
        summary="Site verwijderen",
        description=(
            "Verwijdert de site met alles eraan: versies, previews, genodigden, geheime links, "
            "de gekoppelde repository en de uitgepakte bestanden op schijf. Onomkeerbaar; de URL geeft daarna 404.\n\n"
            "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
        ),
        responses=_deleted("De site en alle content zijn verwijderd.")
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
        summary="Toegang tot een site zetten",
        response_description="De site met zijn nieuwe toegang.",
        description=(
            "Bepaalt wie de live content van deze site mag zien: de basis en de twee "
            "uitzonderingen in één keer, want ze horen bij elkaar en een bezoeker komt binnen "
            "zodra een van de drie hem binnenlaat. De wijziging geldt onmiddellijk voor elke "
            "volgende aanvraag van de content. Previews met een eigen `accessOverride` volgen "
            "deze waarde niet.\n\n"
            "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
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
        summary="Externe bronnen toestaan of blokkeren",
        response_description="De site met zijn nieuwe instelling.",
        description=(
            "Bepaalt of de content van deze site scripts en stijlen mag laden van cdnjs, jsDelivr "
            "en unpkg, en lettertypen van Google Fonts. Staat standaard aan; uitzetten is een "
            "extra beperking en de veiligere keuze voor een vertrouwelijke pagina. De wijziging "
            "geldt onmiddellijk voor elke volgende aanvraag van de content, voor de live site, "
            "previews en versieweergaven. In beide standen geblokkeerd: gegevens ophalen bij of "
            "sturen naar andere hosts, afbeeldingen van elders, een iframe, en een formulier dat "
            "elders post.\n\n"
            "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
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
        summary="Afscherming van andere sites aan- of uitzetten",
        response_description="De site met zijn nieuwe instelling.",
        description=(
            "Alle sites delen een hostnaam. Staat deze afscherming aan, de standaard, dan wordt "
            "de content geserveerd met een CSP-sandbox zonder `allow-same-origin`: de pagina "
            "krijgt een eigen, lege herkomst en kan geen enkele andere site op die hostnaam "
            "lezen, krijgt geen cookies mee en kan niets in de browser bewaren. Eigen stijlen, "
            "scripts, afbeeldingen en lettertypen laden gewoon. Uitzetten is nodig voor een site "
            "die `localStorage`, `sessionStorage` of een cookie gebruikt, en zet die site terug "
            "op de herkomst die hij met alle andere sites deelt. De wijziging geldt onmiddellijk "
            "voor elke volgende aanvraag van de content, voor de live site, previews en "
            "versieweergaven.\n\n"
            "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
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
        summary="Aantal bewaarde vorige versies zetten",
        response_description="De site met zijn nieuwe instelling.",
        description=(
            "Bepaalt hoeveel vorige live-versies de nachtelijke opschoning van deze site laat staan, "
            "naast de huidige live-versie. Oudere live-versies gaan weg, rij en bestanden, en daar "
            "kan daarna niet meer naar teruggerold worden. `0` bewaart alle live-versies, `null` "
            "zet de site terug op de standaard van het platform. De wijziging geldt vanaf de "
            "volgende nachtelijke opschoning.\n\n"
            "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {
                422: (
                    "Geen geheel getal van 0 of meer (`LIVE_VERSIONS_KEPT_INVALID`), of een getal "
                    "dat te groot is om op te slaan (`LIVE_VERSIONS_KEPT_TOO_LARGE`)."
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
        summary="Genodigden van een site",
        response_description="De genodigden, op identifier gesorteerd.",
        description=(
            "De adressen die deze site mogen zien zolang de zichtbaarheid `invitees` is. Bij een andere "
            "zichtbaarheid blijft de lijst bestaan maar doet hij niets.\n\n"
            "**Mag:** effectieve siterol `editor` of ruimer. Deze lijst draagt e-mailadressen van externen "
            "en ligt daarom hoger dan `reader`."
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
        summary="Genodigde toevoegen",
        response_description="De toegevoegde genodigde.",
        description=(
            "Zet een adres op de genodigdenlijst. De identifier wordt naar kleine letters genormaliseerd; "
            "de genodigde hoeft nog geen account te hebben, maar moet wel via SSO kunnen inloggen.\n\n"
            "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {409: "Dit adres staat al op de genodigdenlijst (`INVITEE_EXISTS`)."},
            {422: "De identifier is leeg (`FIELD_EMPTY`)."},
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
        summary="Genodigde verwijderen",
        description=(
            "Haalt een adres van de genodigdenlijst. Een id dat niet bij deze site hoort, levert 404. In "
            "het pad staat het `id` uit de genodigdenlijst, niet het adres zelf: een adres in een URL "
            "belandt in de logregels van elke proxy ertussen.\n\n"
            "**Mag:** effectieve siterol `editor` of ruimer, met een geldige CSRF-header."
        ),
        responses=_deleted("De genodigde is van de lijst.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {404: "Deze genodigde staat niet op de lijst van deze site (`UNKNOWN_INVITEE`)."},
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
        summary="Geheime links van een site",
        response_description="De geheime links, nieuwste eerst.",
        description=(
            "De geheime links van deze site, nieuwste eerst, inclusief de ingetrokken links. De geheime "
            "waarde staat er niet bij: die is alleen bij het aanmaken te zien.\n\n"
            "**Mag:** effectieve siterol `editor` of ruimer."
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
        summary="Geheime link aanmaken",
        response_description="De aangemaakte sleutel, met eenmalig haar volledige waarde.",
        description=(
            "Maakt een geheime link waarmee de site zonder inloggen te zien is, zolang de zichtbaarheid "
            "`key` is.\n\n"
            "Het antwoord bevat eenmalig `value`: de volledige sleutel `<selector>.<geheim>`. Plak bewaart "
            "alleen een hash, dus wie de waarde kwijt is, maakt een nieuwe sleutel aan.\n\n"
            "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {
                422: (
                    "`expiresAt` is geen geldig tijdstip, ligt in het verleden (`EXPIRY_IN_PAST`) of "
                    "verder vooruit dan toegestaan (`EXPIRY_TOO_FAR`)."
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
        summary="Geheime link intrekken",
        description=(
            "Zet de sleutel op `revoked`; de link werkt daarna niet meer. De sleutel blijft in de lijst "
            "staan, zodat zichtbaar blijft dat hij bestond. Het pad gebruikt de `selector`, niet de volledige "
            "sleutelwaarde.\n\n"
            "**Mag:** effectieve siterol `editor` of ruimer, met een geldige CSRF-header."
        ),
        responses=_deleted("De sleutel is ingetrokken.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {404: "Deze site heeft geen sleutel met deze selector (`UNKNOWN_KEY`)."},
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
        summary="Gekoppelde repository van een site",
        response_description="De repository waaruit CI naar deze site mag publiceren.",
        description=(
            "De repository waarvan GitHub- of Forgejo-workflows met een OIDC-ID-token naar deze site mogen "
            "publiceren, zonder geheim. Een 404 `REPOSITORY_NOT_SET` betekent dat er nog niets gekoppeld "
            "is.\n\n"
            "**Mag:** effectieve siterol `editor` of ruimer: wie publiceert, moet de workflow kunnen "
            "inrichten."
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
        summary="Repository aan een site koppelen",
        response_description="De gekoppelde repository, met de ids die de provider teruggaf.",
        description=(
            "Koppelt een GitHub- of Forgejo-repository aan deze site, of vervangt de koppeling. Plak zoekt de "
            "repository op bij de provider (`GET /repos/{owner}/{repo}`) en bewaart haar numerieke ids: "
            "die blijven gelijk bij een hernoeming, en een nieuwe repository onder dezelfde naam krijgt ze "
            "niet. Een CI-ID-token uit deze repository mag daarna publiceren: een preview (en het opruimen "
            "ervan) vanaf elke branch, live alleen vanuit `push`, `workflow_dispatch` of `schedule` en, als "
            "die is ingesteld, alleen vanaf `liveBranch`.\n\n"
            "Plak zoekt zonder inloggegevens, dus een privé repository vindt het niet. Geef dan zelf "
            "`repositoryId` en `ownerId` mee (`gh api repos/{owner}/{repo} --jq '.id, .owner.id'`): Plak "
            "bewaart ze zonder opzoeking als die faalt. Een verkeerd id koppelt niets anders, het weigert "
            "alleen elke deploy. Vindt Plak de repository wel, dan moeten de ids kloppen.\n\n"
            "Ook met het CLI-token uit `plak login` (`plak site link`), dan zonder CSRF-header. Een "
            "CI-ID-token koppelt niets.\n\n"
            "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header of het CLI-token."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_CLI_TOKEN,
            _ERROR_SITE_ROLE,
            _ERROR_SITE,
            {
                422: (
                    "Eigenaar of repository is geen geldige naam (`REPOSITORY_INVALID`), de host hoort niet "
                    "bij de provider of staat niet in de toegestane Forgejo-instanties (`HOST_NOT_ALLOWED`), de "
                    "live-branch is geen geldige branchnaam (`LIVE_BRANCH_INVALID`), de provider kent de "
                    "repository niet, of niet openbaar, en er zijn geen ids meegegeven (`REPOSITORY_NOT_FOUND`), "
                    "de ids zijn niet allebei een positief geheel getal (`REPOSITORY_IDS_INVALID`), of de "
                    "provider geeft de repository andere ids (`REPOSITORY_IDS_MISMATCH`)."
                )
            },
            {
                503: (
                    "De provider is niet bereikbaar (`CI_PROVIDER_UNREACHABLE`) of zijn limiet voor "
                    "anonieme verzoeken is op (`CI_PROVIDER_RATE_LIMITED`), en er zijn geen ids meegegeven; "
                    "probeer het later opnieuw."
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
        summary="Repository ontkoppelen",
        description=(
            "Haalt de koppeling weg: CI-ID-tokens uit die repository worden daarna geweigerd. Versies die "
            "eerder vanuit CI zijn gepubliceerd blijven staan.\n\n"
            "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
        ),
        responses=_deleted("De koppeling is weg.")
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
        summary="Openstaande CLI-login opzoeken",
        response_description="Wat het goedkeuringsscherm toont.",
        description=(
            "Zoekt de openstaande CLI-login achter een gebruikerscode op, zodat het beheer kan laten zien "
            "welk programma wanneer en vanaf welk netwerk wil koppelen. Een POST en geen queryparameter, "
            "zodat de code niet in logregels belandt.\n\n" + _APPROVAL_RULE
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
        summary="CLI-login goedkeuren",
        description=(
            "Keurt de CLI-login achter deze gebruikerscode goed voor het ingelogde lid. De CLI haalt daarna "
            "zelf zijn tokens op en handelt voortaan als dit lid, met precies diens rollen. Alleen doen als "
            "je zelf zojuist `plak login` startte.\n\n" + _APPROVAL_RULE
        ),
        responses=_deleted("Goedgekeurd.") | _errors(_ERROR_CSRF, _ERROR_APPROVAL),
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
        summary="CLI-login weigeren",
        description=(
            "Weigert de CLI-login achter deze gebruikerscode; de CLI krijgt `ACCESS_DENIED`.\n\n"
            + _APPROVAL_RULE
        ),
        responses=_deleted("Geweigerd.") | _errors(_ERROR_CSRF, _ERROR_APPROVAL),
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
        summary="Mijn gekoppelde sessies",
        response_description="De CLI-sessies van het ingelogde lid, nieuwste eerst.",
        description=(
            "Elke `plak login` die nog loopt, nieuwste eerst. Verlopen sessies staan er niet meer bij.\n\n"
            "**Mag:** elk actief lid, voor zijn eigen sessies."
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
        summary="Gekoppelde sessie intrekken",
        description=(
            "Trekt een CLI-sessie in; wie hem gebruikte moet daarna opnieuw `plak login` doen.\n\n"
            "**Mag:** elk actief lid, voor zijn eigen sessies, met een geldige CSRF-header."
        ),
        responses=_deleted("De CLI-sessie is ingetrokken.")
        | _errors(_ERROR_CSRF, {404: "Je hebt geen CLI-sessie met dit id (`CLI_SESSION_UNKNOWN`)."}),
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
        summary="Leden van een groep",
        response_description="De leden van de groep, op e-mailadres gesorteerd.",
        description=(
            "Wie de sites van deze groep mag beheren, op e-mailadres gesorteerd, elk met zijn rol.\n\n"
            "**Mag:** groepsrol `reader` of ruimer, of een platformbeheerder."
        ),
        responses=_errors(_ERROR_GROUP_ROLE, _ERROR_GROUP),
    )
    async def group_members(group_slug: str, member: ActiveMember, db: Db) -> list[GroupMemberOut]:
        group = await _group_with_role(db, member, group_slug, Role.READER, platform_admin=True)
        return await _group_members_json(db, group)

    @router.get(
        "/groups/{group_slug}/members/search",
        tags=[TAG_GROUP_MEMBERS],
        summary="Iemand zoeken om aan de groep toe te voegen",
        response_description=(
            "Hoogstens tien actieve platformleden, op naam en daarbinnen op e-mailadres gesorteerd."
        ),
        description=(
            "Zoekt in de platformleden op naam of e-mailadres, zodat je iemand kunt toevoegen zonder "
            "zijn adres uit het hoofd te kennen. De `identifier` uit een treffer is precies wat "
            "`POST /groups/{group_slug}/members` verwacht.\n\n"
            "Alleen actieve leden komen terug: wie buitengesloten is, voeg je "
            "niet toe. Het antwoord is een shortlist van hoogstens tien namen, geen uitdraai van de "
            "hele organisatie; wie er al in de groep zit staat er wel bij, met `alreadyMember` op "
            "`true`.\n\n"
            "Zoeken mag precies wie ook mag toevoegen, en Plak maakt hier net zomin een account aan: "
            "iemand verschijnt pas zodra hij zelf op het beheer heeft ingelogd.\n\n"
            "**Mag:** groepsrol `admin`, of een platformbeheerder."
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
        summary="Lid aan een groep toevoegen",
        response_description="Het toegevoegde groepslid.",
        description=(
            "Voegt een bestaand platformlid aan deze groep toe, gezocht op e-mailadres of SSO-subject. Het "
            "lid moet al eens zelf op het beheer ingelogd hebben; Plak maakt hier geen account aan.\n\n"
            "Zonder `role` wordt het lid `reader`: wie erbij komt kijkt eerst mee, en de rol die hij nodig "
            "heeft geef je bewust.\n\n"
            "**Mag:** groepsrol `admin`, of een platformbeheerder, met een geldige CSRF-header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            _ERROR_IDENTIFIER_AMBIGUOUS,
            {404: "Er is geen lid met deze identifier; diegene moet eerst zelf inloggen (`UNKNOWN_MEMBER`)."},
            {409: "Dit lid zit al in de groep (`ALREADY_GROUP_MEMBER`)."},
            {422: "De identifier is leeg (`FIELD_EMPTY`), of `role` is geen bestaande rol."},
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
        summary="Lid uit een groep halen",
        description=(
            "Haalt iemand uit de groep. Het platformlid zelf blijft bestaan, net als zijn eventuele andere "
            "groepslidmaatschappen. In het pad staat `memberId` uit de ledenlijst, niet het e-mailadres: "
            "een adres in een URL belandt in de logregels van elke proxy ertussen.\n\n"
            "Een eigen rol op een losse site staat los van de groep en blijft standaard gelden. Met "
            "`siteRoles=remove` haal je die rollen in dezelfde handeling weg, maar alleen op sites in "
            "deze groep; wat dit lid elders heeft blijft onaangeroerd. Alles gebeurt in één transactie, "
            "dus het is allemaal weg of er verandert niets. Elke weggehaalde siterol levert dezelfde "
            "auditregel op als weghalen vanaf het sitescherm (`site_member_remove`).\n\n"
            "Het laatste lid van een groep kan er niet uit: een groep zonder leden is niet meer te beheren, "
            "want iemand toevoegen mag alleen wie er zelf in zit. Om dezelfde reden kan de laatste "
            "`admin` er niet uit.\n\n"
            "**Mag:** groepsrol `admin`, of een platformbeheerder, met een geldige CSRF-header. Wie de "
            "groep mag beheren, mag elke siterol erin al weghalen bij de site zelf."
        ),
        responses=_deleted("Het lid zit niet meer in de groep.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            {
                409: (
                    "Dit is het laatste lid van de groep (`LAST_GROUP_MEMBER`), of de laatste beheerder "
                    "ervan (`LAST_GROUP_ADMIN`)."
                ),
                404: (
                    "Er is geen lid met dit id (`UNKNOWN_MEMBER`), of dat lid zit niet in deze groep "
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
        summary="Groepsrol van een lid wijzigen",
        response_description="Het groepslid met zijn nieuwe rol.",
        description=(
            "Geeft een lid van deze groep een andere rol. De rol bepaalt wat diegene in de hele groep mag: "
            f"{ROLE_HINT}\n\n"
            "Een siterol komt hier nooit uit: die zet je per site, en hij verbreedt alleen wat iemand op die "
            "ene site mag.\n\n"
            "De laatste `admin` van een groep kan niet gedegradeerd worden; een groep zonder beheerder is "
            "niet meer te beheren. Jezelf degraderen kan wel: zolang er een andere beheerder is, kan die je "
            "terugzetten.\n\n"
            "In het pad staat `memberId` uit de ledenlijst, niet het e-mailadres: een adres in een URL "
            "belandt in de logregels van elke proxy ertussen.\n\n"
            "**Mag:** groepsrol `admin`, of een platformbeheerder, met een geldige CSRF-header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_GROUP_ROLE,
            _ERROR_GROUP,
            {
                404: (
                    "Er is geen lid met dit id (`UNKNOWN_MEMBER`), of dat lid zit niet in deze groep "
                    "(`NOT_GROUP_MEMBER`)."
                )
            },
            {409: "Dit is de laatste beheerder van de groep (`LAST_GROUP_ADMIN`)."},
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
        summary="Alle platformleden",
        response_description="Alle platformleden, op e-mailadres gesorteerd.",
        description=(
            "Iedereen die ooit op het beheer heeft ingelogd, op e-mailadres gesorteerd, met hun rol en status. "
            "Hier zie je ook wie de toegang is ontzegd.\n\n"
            "**Mag:** alleen een platformbeheerder."
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
        summary="Vulling van het contentvolume",
        response_description="Totaal, gebruikt en vrij op het contentvolume, met de reserve.",
        description=(
            "Het hele contentvolume in één blik: grootte, gebruikt, vrij en de reserve waaronder een deploy "
            "wordt geweigerd. Er staat geen site of groep in: de platformbeheerder beheert mensen en groepen "
            "en kijkt niet in de sites.\n\n"
            "**Mag:** alleen een platformbeheerder."
        ),
        responses=_errors(
            _ERROR_ADMIN,
            {503: "Het volume is niet te meten, bijvoorbeeld omdat de contentroot ontbreekt (`VOLUME_UNMEASURABLE`)."},
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
        summary="Platformlid heractiveren",
        response_description="Het lid met zijn nieuwe status.",
        description=(
            "Zet de status van een lid op `active`, waarmee het de beheer-API weer mag gebruiken. Voor een "
            "lid van wie de toegang is ingetrokken (status `deactivated`).\n\n"
            "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_ADMIN, {404: "Onbekend lid (`UNKNOWN_MEMBER`)."}),
    )
    async def activate_platform_member(
        request: Request, member_id: uuid.UUID, _csrf: Csrf, member: ActiveMember, db: Db
    ) -> MemberOut:
        return await _set_member_status(request, member_id, MemberStatus.ACTIVE, "member_activate", member, db)

    @router.post(
        "/platform/members/{member_id}/_deactivate",
        tags=[TAG_PLATFORM],
        summary="Platformlid deactiveren",
        response_description="Het lid met zijn nieuwe status.",
        description=(
            "Zet de status van een lid op `deactivated`. Elk volgend API-verzoek van dat lid krijgt 403, "
            "ook met een sessie die nog geldig is. Groepslidmaatschappen blijven staan, zodat activeren het "
            "lid terugbrengt zoals het was. Alle CLI-sessies (`plak login`) van het lid worden wel "
            "ingetrokken: na heractiveren moet het opnieuw koppelen.\n\n"
            "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header."
        ),
        responses=_errors(_ERROR_CSRF, _ERROR_ADMIN, {404: "Onbekend lid (`UNKNOWN_MEMBER`)."}),
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
        summary="Platformrol van een lid wijzigen",
        response_description="Het lid met zijn nieuwe platformrol.",
        description=(
            "Maakt een lid platformbeheerder of haalt die rol er weer af. Een platformbeheerder beheert "
            "mensen en groepen: leden activeren en deactiveren, beheerders aanwijzen, en de ledenlijst "
            "lezen. Hij beheert geen sites; daarvoor kent hij zichzelf een groepsrol toe.\n\n"
            "Drie dingen kunnen niet, allemaal omdat ze het platform onbeheerbaar zouden maken: je eigen "
            "rol afnemen, de laatste actieve beheerder degraderen, en het bootstrap-account wijzigen.\n\n"
            "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            {404: "Onbekend lid (`UNKNOWN_MEMBER`)."},
            {
                409: (
                    "Je eigen rol afnemen (`SELF_NOT_ALLOWED`), de laatste actieve beheerder "
                    "(`LAST_PLATFORM_ADMIN`), of het bootstrap-account (`BOOTSTRAP_MEMBER`)."
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
        summary="Auditlog teruglezen",
        response_description="Eén pagina auditregels, nieuwste eerst, met de cursor naar de volgende.",
        description=(
            "Leest het auditlog terug, nieuwste eerst: weigeringen, inloggen en uitloggen, kijken op "
            "niet-publieke content, deploys en beheerhandelingen. Woordenschat en bewaartermijnen staan in "
            "`docs/audit-log.md`.\n\n"
            "**Bladeren gaat met `cursor`, niet met een paginanummer.** Het log groeit terwijl je leest, en "
            "een verschuivende offset zou regels overslaan of dubbel tonen. Neem `nextCursor` uit het "
            "antwoord ongewijzigd over; is die `null`, dan was dit de laatste pagina.\n\n"
            "**Actoren staan er gepseudonimiseerd in** en worden hier niet teruggevertaald. Zoek je iemand "
            "in het bijzonder, haal dan eerst zijn pseudoniem op met "
            "`POST /platform/audit/actor-pseudonym` en filter daarmee op `actorPseudonym`.\n\n"
            "**Mag:** alleen een platformbeheerder. Een groepsbeheerder ziet ook zijn eigen groep niet: de "
            "groep van een regel staat alleen als vrije sleutel in `refs`, en daar is geen "
            "autorisatiegrens op te bouwen. Het teruglezen zelf wordt geaudit; lukt die auditrij niet, "
            "dan komt er geen pagina: zie de `503`."
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
        summary="Pseudoniem van een actor opzoeken",
        response_description="Het auditpseudoniem dat bij deze identifier hoort.",
        description=(
            "Vertaalt een identifier naar het pseudoniem waaronder hij in het auditlog staat, zodat je op "
            "`actorPseudonym` kunt filteren. Wie je zoekt moet je dus al bij naam kennen: het log geeft "
            "geen namenlijst prijs. Ken je juist alleen het pseudoniem uit een logregel, gebruik dan "
            "`POST /platform/audit/actor-identity` voor de omgekeerde richting.\n\n"
            "Dat dit een POST is en geen queryparameter, is met opzet: een e-mailadres in een URL belandt "
            "in de logregels van elke proxy ertussen.\n\n"
            "Het pseudoniem hangt aan `PLAK_AUDIT_PEPPER`. Na een rotatie van die pepper vind je alleen nog "
            "regels van ná de rotatie.\n\n"
            "Een e-mailadres dat bij geen lid en geen geverifieerd e-mailadres van een content-viewer "
            "hoort, wordt niet gepseudonimiseerd: dat is een 404. Een niet-e-mailadres dat nergens bij "
            "hoort (bijvoorbeeld een los SSO-subject) wordt wel letterlijk gepseudonimiseerd, als "
            "`unknown`. Hoort een e-mailadres bij meer dan één subject (kan alleen via e-mail, nooit via "
            "een SSO-subject), dan is het antwoord een 409: zoek in dat geval op het SSO-subject.\n\n"
            "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header en een `reason` van "
            f"{REASON_MIN_LENGTH} tot {REASON_MAX_LENGTH} tekens. Het opzoeken zelf wordt geaudit, met het "
            "pseudoniem, `resolved_as` en de reden, nooit de identifier. Lukt die auditrij niet, dan komt "
            "er geen antwoord: zie de `503`. Telt mee voor de dagelijkse limiet op zulke opzoekingen, ook "
            "bij een 404."
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
        summary="Actor achter een pseudoniem opzoeken",
        response_description="Het lid, de content-viewer of de CI-repository achter dit pseudoniem.",
        description=(
            "De omgekeerde richting van `POST /platform/audit/actor-pseudonym`: geef een pseudoniem uit "
            "een logregel, krijg terug wie erachter zit. Elk lid, elke content-viewer en elke gekoppelde repository "
            "wordt met de actuele `PLAK_AUDIT_PEPPER` opnieuw gepseudonimiseerd en vergeleken met het "
            "opgegeven pseudoniem; er wordt niets bijgehouden om deze zoekslag te versnellen, dus reken op "
            "een volledige doorloop van alle drie.\n\n"
            "Levert de zoekslag niets op, dan is er geen lid, content-viewer of repository dat tot dit "
            "pseudoniem pseudonimiseert. Meerdere oorzaken komen daarvoor in aanmerking: de pepper is "
            "geroteerd sinds die regel geschreven werd; de actor bezocht alleen ooit content via de "
            "content-host-SSO en dat bezoek ligt al langer dan 90 dagen terug (`content_viewers` wordt dan "
            "net als de bijbehorende `content_access`-regels opgeruimd); of de repository is inmiddels "
            "ontkoppeld, of met haar site of groep verwijderd (`ON DELETE CASCADE`).\n\n"
            "Dat dit een POST is en geen queryparameter, is met opzet, net als bij de andere kant van deze "
            "zoekslag.\n\n"
            "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header en een `reason` van "
            f"{REASON_MIN_LENGTH} tot {REASON_MAX_LENGTH} tekens. Het opzoeken zelf wordt geaudit, met het "
            "opgegeven pseudoniem, `resolved_as` en de reden, nooit de gevonden identifier. Lukt die "
            "auditrij niet, dan komt er geen antwoord: zie de `503`. Telt mee voor de dagelijkse limiet op "
            "zulke opzoekingen, ook bij een 404."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            _ERROR_AUDIT_UNAVAILABLE,
            _ERROR_REASON,
            _ERROR_LOOKUP_LIMIT,
            {422: f"Geen {AUDIT_PSEUDONYM_LENGTH} hexadecimale tekens (`ACTOR_PSEUDONYM_INVALID`)."},
            {
                404: (
                    "Geen lid, content-viewer of gekoppelde repository pseudonimiseert tot deze waarde "
                    "(`PSEUDONYM_UNKNOWN`): de pepper is geroteerd, de content-viewer is opgeruimd na 90 "
                    "dagen zonder bezoek, of de repository is ontkoppeld of met haar site verwijderd."
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
        summary="Volledig IP-adres van een auditregel achterhalen",
        response_description="Het volledige IP-adres bij deze auditregel.",
        description=(
            "`GET /platform/audit` toont per regel alleen het afgeknotte netwerk (`ipTruncated`); dit "
            "endpoint ontsleutelt het volledige adres dat er versleuteld naast staat (`PLAK_AUDIT_IP_KEY`, "
            "een andere sleutel dan de auditpepper). Bedoeld voor het uiterste geval waarin het netwerk "
            "niet volstaat, bijvoorbeeld bij een incidentonderzoek.\n\n"
            "**Mag:** alleen een platformbeheerder, met een geldige CSRF-header en een `reason` van "
            f"{REASON_MIN_LENGTH} tot {REASON_MAX_LENGTH} tekens. Het ontsleutelen zelf wordt geaudit, met "
            "het regel-id en de reden, nooit het IP-adres. Lukt die auditrij niet, dan komt er geen "
            "antwoord: zie de `503`. Telt mee voor de dagelijkse limiet op zulke opzoekingen, ook bij een "
            "404."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_ADMIN,
            _ERROR_AUDIT_UNAVAILABLE,
            _ERROR_REASON,
            _ERROR_LOOKUP_LIMIT,
            {404: "Geen auditregel met dit id, of er staat geen versleuteld IP-adres bij (`AUDIT_IP_UNKNOWN`)."},
        ),
    )
    async def audit_ip_reveal(
        request: Request,
        entry_id: Annotated[uuid.UUID, Path(description="Id van de auditregel (`AuditEntryOut.id`).")],
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
        summary="Leden van een site",
        response_description="Iedereen die bij deze site kan, met de rol waarmee.",
        description=(
            "Iedereen die bij deze site kan, niet alleen wie hier een eigen rol heeft. Per regel staat "
            "de groepsrol, de siterol en de rol die daaruit volgt: de ruimste van de twee wint, want een "
            "siterol verbreedt alleen en neemt nooit iets af.\n\n"
            "Een lijst met alleen de siterollen zou de groepsleden weglaten en lezen alsof veel minder "
            "mensen erbij kunnen dan werkelijk het geval is.\n\n"
            "**Mag:** effectieve siterol `lezer`."
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
        summary="Iemand zoeken om een rol op deze site te geven",
        response_description=(
            "Hoogstens tien actieve platformleden, op naam en daarbinnen op e-mailadres gesorteerd."
        ),
        description=(
            "Zoekt in de platformleden op naam of e-mailadres, zodat je iemand een rol op deze site "
            "kunt geven zonder zijn adres uit het hoofd te kennen. De `identifier` uit een treffer is "
            "precies wat `POST /sites/{group_slug}/{site_slug}/members` verwacht.\n\n"
            "Alleen actieve leden komen terug: wie buitengesloten is, voeg je "
            "niet toe. Het antwoord is een shortlist van hoogstens tien namen, geen uitdraai van de "
            "hele organisatie; wie hier al een eigen siterol heeft staat er wel bij, met "
            "`alreadyMember` op `true`.\n\n"
            "Groepsleden kun je hier gewoon kiezen: een siterol verruimt wat zij via de groep al "
            "mogen. Wat dat is staat in `groupRole`, zodat je ziet of een siterol iets toevoegt.\n\n"
            "Zoeken mag precies wie ook mag toevoegen, en Plak maakt hier net zomin een account aan: "
            "iemand verschijnt pas zodra hij zelf op het beheer heeft ingelogd.\n\n"
            "**Mag:** effectieve siterol `beheerder`."
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
        summary="Lid een rol op deze site geven",
        response_description="Het lid met zijn nieuwe siterol.",
        description=(
            "Geeft een bestaand platformlid een rol op deze ene site. Dat kan een ruimere rol zijn dan "
            "zijn groepsrol; smaller heeft geen effect, want de ruimste van de twee blijft gelden. Iemand "
            "hoeft geen groepslid te zijn: zo geef je een buitenstaander toegang tot precies deze site.\n\n"
            "Een groepsrol wijzig je niet hier maar bij de groep.\n\n"
            "**Mag:** effectieve siterol `beheerder`, met een geldige CSRF-header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE,
            _ERROR_IDENTIFIER_AMBIGUOUS,
            {404: "Er is geen lid met deze identifier; diegene moet eerst zelf inloggen (`UNKNOWN_MEMBER`)."},
            {409: "Dit lid heeft al een rol op deze site (`ALREADY_SITE_MEMBER`)."},
            {422: "De identifier is leeg (`FIELD_EMPTY`)."},
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
        summary="Siterol van een lid wijzigen",
        response_description="Het lid met zijn nieuwe siterol.",
        description=(
            "Geeft een lid een andere rol op deze ene site. Werkt alleen op een siterol; wie hier staat "
            "omdat hij groepslid is, wijzig je bij de groep.\n\n"
            "In het pad staat `memberId` uit de ledenlijst, niet het e-mailadres: een adres in een URL "
            "belandt in de logregels van elke proxy ertussen.\n\n"
            "**Mag:** effectieve siterol `beheerder`, met een geldige CSRF-header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE,
            {
                404: (
                    "Er is geen lid met dit id (`UNKNOWN_MEMBER`), of dat lid heeft geen eigen "
                    "rol op deze site (`NOT_SITE_MEMBER`)."
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
        summary="Siterol van een lid weghalen",
        description=(
            "Haalt de rol weg die alleen op deze site gold. Een groepsrol blijft staan, dus wie via de "
            "groep bij deze site kan, kan dat daarna nog steeds.\n\n"
            "In het pad staat `memberId` uit de ledenlijst, niet het e-mailadres: een adres in een URL "
            "belandt in de logregels van elke proxy ertussen.\n\n"
            "Er is hier geen laatste-beheerder-bescherming zoals bij een groep: de beheerders van de "
            "groep kunnen altijd bij deze site, dus een site zonder eigen beheerder is niet onbeheerbaar."
            "\n\n**Mag:** effectieve siterol `beheerder`, met een geldige CSRF-header."
        ),
        responses=_deleted("Het lid heeft geen eigen rol meer op deze site.")
        | _errors(
            _ERROR_CSRF,
            _ERROR_SITE,
            {
                404: (
                    "Er is geen lid met dit id (`UNKNOWN_MEMBER`), of dat lid heeft geen eigen "
                    "rol op deze site (`NOT_SITE_MEMBER`)."
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
        summary="Deployhistorie van een site",
        response_description="De versies van de site, nieuwste eerst.",
        description=(
            "Alle versies van deze site, nieuwste eerst, met hun doel (`live` of `preview`) en herkomst "
            "(`upload` door een lid of `action` door CI). Precies een versie heeft `isLive`, tenzij er nog "
            "niets live staat.\n\n"
            "**Mag:** effectieve siterol `reader` of ruimer."
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
        summary="Opslag en bewaarregel van een site",
        response_description="Het gebruik, de limiet en het aantal bewaarde live-versies.",
        description=(
            "Hoeveel ruimte de versies van deze site nu innemen, hoeveel ze samen mogen innemen, en "
            "hoeveel vorige live-versies de nachtelijke opschoning laat staan. De huidige live-versie "
            "blijft altijd, ook na terugrollen naar een oudere versie. De limiet geldt voor het hele "
            "platform; het aantal bewaarde versies is de standaard van het platform, tenzij een "
            "sitebeheerder voor deze site een eigen aantal instelde.\n\n"
            "**Mag:** effectieve siterol `reader` of ruimer."
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
        summary="Terugrollen naar een eerdere versie",
        response_description="De site met de nieuwe live versie.",
        description=(
            "Zet een bestaande versie (terug) op de publieke URL. Er wordt niets opnieuw geüpload: de "
            "uitgepakte bestanden van die versie staan er al. De wissel geldt onmiddellijk.\n\n"
            "Alleen versies met doel `live` kunnen live staan; een preview-versie levert 422.\n\n"
            "**Mag:** effectieve siterol `editor` of ruimer, met een geldige CSRF-header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            {
                404: (
                    "Onbekende groep (`UNKNOWN_GROUP`), onbekende site (`UNKNOWN_SITE`), of de versie "
                    "bestaat niet of hoort bij een ander site (`UNKNOWN_VERSION`, `VERSION_OTHER_SITE`)."
                )
            },
            {422: "Deze versie kan niet live: het is bijvoorbeeld een preview-versie."},
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
        summary="Previews van een site",
        response_description="De previews van de site, op ref gesorteerd.",
        description=(
            "De previews die nu voor deze site bestaan, op ref gesorteerd, met hun URL op de content-origin "
            "en het tijdstip waarop de opruimjob ze weggooit.\n\n"
            "**Mag:** effectieve siterol `reader` of ruimer."
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
        summary="Toegang tot een preview zetten",
        response_description="De preview met haar nieuwe toegang.",
        description=(
            "Geeft deze ene preview een eigen toegang, los van de site: zo kan een preview ruimer "
            "of juist strenger staan dan de live site. Het is basis plus uitzonderingen in hun "
            "geheel, nooit een basis van de preview met uitzonderingen van de site. "
            "`access: null` haalt de uitzondering weg, waarna de preview de site weer volgt.\n\n"
            "**Mag:** effectieve siterol `admin`, met een geldige CSRF-header."
        ),
        responses=_errors(
            _ERROR_CSRF,
            _ERROR_SITE_ROLE,
            {
                404: (
                    "Onbekende groep (`UNKNOWN_GROUP`), onbekende site (`UNKNOWN_SITE`), of dit "
                    "site heeft geen preview met deze ref (`UNKNOWN_PREVIEW`)."
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
