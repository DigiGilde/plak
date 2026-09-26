"""CLI login API (`plak login`): the device authorization grant (RFC 8628)
that Plak brokers on top of its own beheer SSO login.

The CLI side lives here and needs no session: asking for a device code,
polling the token endpoint, and logging out or asking who it is with its
access token. The member side (looking up, approving and denying a user code,
and the list of linked devices) needs a fresh beheer session with CSRF and
lives in api/admin.py.

The token endpoint answers RFC 8628's error codes as problem+json 400 with
`code` in SCREAMING_SNAKE (`AUTHORIZATION_PENDING`, `SLOW_DOWN`,
`EXPIRED_TOKEN`, `ACCESS_DENIED`, `INVALID_GRANT`), matching the rest of the
API rather than OAuth's own JSON error shape.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Request
from pydantic import ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import Response

from plak import net
from plak.api.deploys import bearer_from_request, cli_member
from plak.api.docs import SECURITY_BEARER, TAG_CLI
from plak.api.errors import WWW_AUTHENTICATE_BEARER, ApiError, error_responses
from plak.api.schema import ApiModel
from plak.audit import vocabulary
from plak.audit.log import Actor, AuditLog
from plak.audit.pseudonymisation import truncate_ip
from plak.cli import service as cli
from plak.models.audit import ActorKind
from plak.models.identity import Member
from plak.ratelimit import InMemoryCounter

router = APIRouter(prefix="/-/api/v1")

VERIFICATION_PATH = "/cli-link"

# Creating a device authorization costs a row and a code someone might phish
# with, so it gets a budget of its own on top of the general API rate limit:
# per client IP, and a global backstop against a distributed flood.
DEVICE_CREATE_MAX_PER_IP = 10
DEVICE_CREATE_MAX_GLOBAL = 1000
DEVICE_CREATE_WINDOW_S = 600

_NO_AUTH = {"security": [{}]}
_BEARER = {"security": [{SECURITY_BEARER: []}]}


class DeviceAuthorizationRequest(ApiModel):
    """Het begin van `plak login`."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"clientName": "plak-cli 1.2 on macOS"}, {}]})

    client_name: str | None = Field(
        default=None,
        max_length=cli.CLIENT_NAME_MAX_LENGTH,
        description=(
            "Hoe de CLI zichzelf noemt; staat op het goedkeuringsscherm en in de lijst gekoppelde apparaten. "
            f"Hoogstens {cli.CLIENT_NAME_MAX_LENGTH} tekens; stuur- en opmaaktekens worden eruit gehaald."
        ),
        examples=["plak-cli 1.2 on macOS"],
    )


class DeviceAuthorizationOut(ApiModel):
    """Wat de CLI nodig heeft om het inloggen af te maken (RFC 8628 3.2)."""

    device_code: str = Field(
        description="Geheim voor de CLI zelf: hiermee vraagt hij de tokens op. Nooit tonen of loggen."
    )
    user_code: str = Field(
        description=(
            "Code om in de terminal te tonen en in het beheer te vergelijken of in te tikken: acht tekens "
            "zonder 0, O, 1 en I, met een koppelteken in het midden. Hoofdletterongevoelig."
        ),
        examples=["WDJB-MJHT"],
    )
    verification_uri: str = Field(
        description="Pagina in het beheer waar het lid de code invoert.",
        examples=["https://beheer.plak.example/cli-link"],
    )
    verification_uri_complete: str = Field(
        description="Dezelfde pagina met de code al ingevuld; om in de browser te openen.",
        examples=["https://beheer.plak.example/cli-link?code=WDJB-MJHT"],
    )
    expires_in: int = Field(description="Seconden tot de codes verlopen.", examples=[600])
    interval: int = Field(description="Seconden die de CLI minstens tussen twee pogingen wacht.", examples=[5])


class TokenRequest(ApiModel):
    """Een apparaatcode inwisselen, of een verversingstoken."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"grantType": "device_code", "deviceCode": "plakdc_..."},
                {"grantType": "refresh_token", "refreshToken": "plakclr_..."},
            ]
        }
    )

    grant_type: Literal["device_code", "refresh_token"] = Field(
        description="`device_code` na het goedkeuren, `refresh_token` om te verversen.",
        examples=["device_code"],
    )
    device_code: str | None = Field(
        default=None, max_length=200, description="De `deviceCode`, bij `grantType` `device_code`."
    )
    refresh_token: str | None = Field(
        default=None, max_length=200, description="Het laatste verversingstoken, bij `grantType` `refresh_token`."
    )


class CliMemberOut(ApiModel):
    """Het lid achter een CLI-sessie."""

    email: str = Field(description="E-mailadres van het lid.", examples=["lid@example.nl"])
    name: str = Field(description="Weergavenaam; leeg als de identity provider die niet stuurt.", examples=["Sanne"])


class TokensOut(ApiModel):
    """Een verse set tokens. Het verversingstoken is na gebruik ongeldig: bewaar steeds het nieuwe."""

    access_token: str = Field(description="Toegangstoken `plakcli_...` voor `Authorization: Bearer`.")
    refresh_token: str = Field(
        description=(
            "Verversingstoken `plakclr_...`. Eenmalig: wie een al gebruikt verversingstoken nog eens "
            "aanbiedt, trekt daarmee de hele CLI-sessie in."
        )
    )
    token_type: Literal["Bearer"] = Field(description="Altijd `Bearer`.", examples=["Bearer"])
    expires_in: int = Field(description="Seconden tot het toegangstoken verloopt.", examples=[3600])
    member: CliMemberOut = Field(description="Het lid namens wie de CLI nu handelt.")


class WhoamiOut(ApiModel):
    """Wie de CLI is, en tot wanneer."""

    member: CliMemberOut = Field(description="Het lid namens wie de CLI handelt.")
    expires_at: str = Field(
        description=(
            "Tijdstip waarop de CLI-sessie verloopt als hij niet meer ververst wordt: 30 dagen na het laatste "
            "verversen, en nooit later dan 90 dagen na het koppelen."
        ),
        json_schema_extra={"format": "date-time", "examples": ["2026-10-19T09:30:00Z"]},
    )


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _member_out(member: Member) -> CliMemberOut:
    return CliMemberOut(email=member.email, name=member.name or "")


def _verification_base(request: Request) -> str:
    base_url = request.app.state.settings.base_url
    return (base_url or str(request.base_url)).rstrip("/")


def _creation_counter(request: Request) -> InMemoryCounter:
    counter = getattr(request.app.state, "cli_device_counter", None)
    if counter is None:
        counter = InMemoryCounter()
        request.app.state.cli_device_counter = counter
    return counter


async def _within_creation_budget(request: Request, ip: str | None) -> bool:
    counter = _creation_counter(request)
    now = time.monotonic()
    per_ip = await counter.increment(f"ip:{ip}", DEVICE_CREATE_WINDOW_S, now)
    backstop = await counter.increment("global", DEVICE_CREATE_WINDOW_S, now)
    return per_ip.count <= DEVICE_CREATE_MAX_PER_IP and backstop.count <= DEVICE_CREATE_MAX_GLOBAL


def _factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


@router.post(
    "/cli/device-authorizations",
    tags=[TAG_CLI],
    openapi_extra=_NO_AUTH,
    summary="Inloggen met de CLI beginnen",
    response_description="De apparaatcode voor de CLI en de gebruikerscode voor het lid.",
    description=(
        "Stap 1 van `plak login` (RFC 8628). De CLI krijgt een geheime `deviceCode` en toont de `userCode` "
        "plus `verificationUri`; het lid opent die pagina in het beheer, vergelijkt de code en keurt goed. "
        "Intussen vraagt de CLI elke `interval` seconden `POST /cli/tokens`. De codes verlopen na tien "
        "minuten.\n\n"
        "**Mag:** iedereen, zonder authenticatie. Per IP-adres geldt een eigen limiet op het aantal "
        "aanvragen, naast de algemene ratelimit."
    ),
    responses=error_responses(
        {
            422: f"`clientName` is langer dan {cli.CLIENT_NAME_MAX_LENGTH} tekens.",
            429: "Te veel aanvragen vanaf dit adres (`TOO_MANY_REQUESTS`); probeer het later opnieuw.",
        }
    ),
)
async def create_device_authorization(
    request: Request, body: Annotated[DeviceAuthorizationRequest | None, Body()] = None
) -> DeviceAuthorizationOut:
    ip = net.client_ip_from_request(request)
    if not await _within_creation_budget(request, ip):
        # Not audited per request, like the general ratelimit's 429: a flood
        # would otherwise become a flood of audit rows.
        request.state.audit_written = True
        raise ApiError(429, "TOO_MANY_REQUESTS.cli_login")
    client_name = cli.sanitise_client_name(body.client_name if body is not None else None)
    async with _factory(request)() as db:
        created = await cli.create_device_authorization(
            db, client_name=client_name, ip_truncated=truncate_ip(ip) if ip else None
        )
    user_code = cli.format_user_code(created.user_code)
    verification_uri = _verification_base(request) + VERIFICATION_PATH
    return DeviceAuthorizationOut(
        device_code=created.device_code,
        user_code=user_code,
        verification_uri=verification_uri,
        verification_uri_complete=f"{verification_uri}?code={user_code}",
        expires_in=int(cli.DEVICE_CODE_TTL.total_seconds()),
        interval=cli.POLL_INTERVAL_S,
    )


@router.post(
    "/cli/tokens",
    tags=[TAG_CLI],
    openapi_extra=_NO_AUTH,
    summary="Tokens ophalen of verversen",
    response_description="Een toegangstoken en een nieuw verversingstoken.",
    description=(
        "Met `grantType` `device_code`: de CLI vraagt of het lid al heeft goedgekeurd. Zolang dat niet zo is "
        "volgt 400 `AUTHORIZATION_PENDING`; wie sneller vraagt dan `interval` krijgt `SLOW_DOWN` en wacht "
        "voortaan vijf seconden langer. Na goedkeuring komen er eenmalig tokens terug; daarna is de "
        "apparaatcode op.\n\n"
        "Met `grantType` `refresh_token`: een nieuw toegangstoken en een nieuw verversingstoken. Het oude "
        "verversingstoken is daarna ongeldig. Wordt het zojuist vervangen verversingstoken binnen tien "
        "seconden nog eens aangeboden (twee verversingen tegelijk), dan volgt `INVALID_GRANT` en blijft de "
        "sessie staan. Komt een al gebruikt verversingstoken later of ouder terug, dan hebben twee partijen "
        "dezelfde sessie in handen: Plak trekt de hele CLI-sessie in en schrijft `cli_refresh_reuse` in het "
        "auditlog. Een CLI-sessie verloopt 30 dagen na het laatste verversen en "
        "hoe dan ook 90 dagen na het koppelen.\n\n"
        "**Mag:** iedereen met een geldige apparaatcode of verversingstoken; er is verder geen "
        "authenticatie."
    ),
    responses=error_responses(
        {
            400: (
                "Nog niet goedgekeurd (`AUTHORIZATION_PENDING`), te snel gevraagd (`SLOW_DOWN`), de "
                "apparaatcode is verlopen (`EXPIRED_TOKEN`), het lid weigerde (`ACCESS_DENIED`), of de code "
                "of het verversingstoken is onbekend, al gebruikt, verlopen of hoort bij een niet-actief lid "
                "(`INVALID_GRANT`)."
            ),
        }
    ),
)
async def tokens(request: Request, body: TokenRequest) -> TokensOut:
    try:
        async with _factory(request)() as db:
            if body.grant_type == "device_code":
                if not body.device_code:
                    raise cli.GrantError(f"{cli.INVALID_GRANT}.device_code_missing")
                issued = await cli.exchange_device_code(db, body.device_code)
            else:
                if not body.refresh_token:
                    raise cli.GrantError(f"{cli.INVALID_GRANT}.refresh_token_missing")
                issued = await cli.refresh(db, body.refresh_token)
    except cli.RefreshReuseError as error:
        log: AuditLog = request.app.state.audit_log
        actor = Actor(ActorKind.MEMBER, error.member_sub) if error.member_sub else Actor(ActorKind.ANONYMOUS)
        await log.write(
            vocabulary.CLI_REFRESH_REUSE,
            actor,
            vocabulary.REFUSED,
            reason_code=cli.INVALID_GRANT,
            refs={"via": vocabulary.VIA_CLI},
            ip=net.client_ip_from_request(request),
        )
        raise ApiError(400, error.message.key, params=error.message.params) from None
    except cli.GrantError as error:
        raise ApiError(400, error.message.key, params=error.message.params) from None
    if body.grant_type == "device_code":
        # The approval (cli_login) says who allowed it; this row says the
        # credential actually left Plak, and which session it belongs to.
        log: AuditLog = request.app.state.audit_log
        await log.write(
            vocabulary.CLI_TOKEN_ISSUED,
            Actor(ActorKind.MEMBER, issued.member.sso_subject),
            vocabulary.ALLOWED,
            refs={"via": vocabulary.VIA_CLI, "cli_session": str(issued.session.id)},
            ip=net.client_ip_from_request(request),
        )
    return TokensOut(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        token_type="Bearer",  # noqa: S106 - the OAuth token type, not a secret
        expires_in=int(cli.ACCESS_TOKEN_TTL.total_seconds()),
        member=_member_out(issued.member),
    )


_BEARER_ERRORS = {
    401: (
        "Geen of een ongeldig, verlopen of ingetrokken toegangstoken (`TOKEN_INVALID`). Het antwoord draagt "
        "`WWW-Authenticate: Bearer`."
    ),
}
_WHOAMI_ERRORS = {
    **_BEARER_ERRORS,
    403: "Het lid achter deze CLI-sessie is niet (meer) actief (`MEMBER_NOT_ACTIVE`).",
}


def _access_token(request: Request) -> str:
    plaintext = bearer_from_request(request)
    if not plaintext:
        raise ApiError(401, "TOKEN_INVALID.missing", headers=WWW_AUTHENTICATE_BEARER)
    return plaintext


class LogoutRequest(ApiModel):
    """Uitloggen met het verversingstoken, voor als er geen (geldig) toegangstoken meer is."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"refreshToken": "plakclr_..."}]})

    refresh_token: str = Field(
        max_length=200, description="Een verversingstoken van de sessie, het huidige of een al gebruikt."
    )


@router.delete(
    "/cli/session",
    status_code=204,
    tags=[TAG_CLI],
    openapi_extra={"security": [{SECURITY_BEARER: []}, {}]},
    summary="Uitloggen met de CLI",
    description=(
        "Trekt de CLI-sessie in (`plak logout`), met al haar tokens; het apparaat verdwijnt uit de lijst "
        "gekoppelde apparaten. Noem de sessie op een van twee manieren:\n\n"
        "* `Authorization: Bearer plakcli_...`: het toegangstoken, ook als het al verlopen is, zolang het "
        "echt is en de sessie nog bestaat;\n"
        "* een JSON-lichaam `{\"refreshToken\": \"plakclr_...\"}` (zoals RFC 7009), het huidige of een al "
        "gebruikt verversingstoken.\n\n"
        "Het antwoord is altijd 204, ook voor een onbekend of al ingetrokken token: zo verraadt het niet "
        "welke tokens bestaan. Het auditlog onderscheidt de twee gevallen wel (`cli_logout` met `allowed` "
        "of `refused`).\n\n"
        "**Mag:** iedereen die een token van de sessie heeft; ook een lid dat inmiddels gedeactiveerd is."
    ),
    responses={204: {"description": "De CLI-sessie bestaat niet (meer). Er komt geen inhoud terug."}}
    | error_responses({422: "Het lichaam is geen object met een `refreshToken` van hoogstens 200 tekens."}),
)
async def logout(request: Request, body: Annotated[LogoutRequest | None, Body()] = None) -> Response:
    access_token = bearer_from_request(request) or None
    refresh_token = body.refresh_token if body is not None else None
    async with _factory(request)() as db:
        # Deliberately no active-member check: logging out has to work for a
        # deactivated member too.
        session = await cli.session_for_logout(db, access_token=access_token, refresh_token=refresh_token)
        member = await db.get(Member, session.member_id) if session is not None else None
        if session is not None:
            await cli.revoke(db, session.id)
    log: AuditLog = request.app.state.audit_log
    ip = net.client_ip_from_request(request)
    if session is None:
        await log.write(
            vocabulary.CLI_LOGOUT,
            Actor(ActorKind.ANONYMOUS),
            vocabulary.REFUSED,
            reason_code="TOKEN_INVALID",
            refs={"via": vocabulary.VIA_CLI},
            ip=ip,
        )
        return Response(status_code=204)
    await log.write(
        vocabulary.CLI_LOGOUT,
        Actor(ActorKind.MEMBER, member.sso_subject) if member is not None else Actor(ActorKind.ANONYMOUS),
        vocabulary.ALLOWED,
        refs={"via": vocabulary.VIA_CLI, "cli_session": str(session.id)},
        ip=ip,
    )
    return Response(status_code=204)


@router.get(
    "/cli/whoami",
    tags=[TAG_CLI],
    openapi_extra=_BEARER,
    summary="Wie is de CLI",
    response_description="Het lid achter dit toegangstoken en wanneer de sessie verloopt.",
    description=(
        "Het lid namens wie de CLI handelt (`plak whoami`), en tot wanneer de CLI-sessie loopt als hij niet "
        "meer ververst wordt. Het lid moet nog actief zijn, net als bij elke deploy.\n\n"
        "**Mag:** de houder van een geldig CLI-toegangstoken (`Authorization: Bearer plakcli_...`)."
    ),
    responses=error_responses(_WHOAMI_ERRORS),
)
async def whoami(request: Request) -> WhoamiOut:
    plaintext = _access_token(request)
    async with _factory(request)() as db:
        session, member = await cli_member(request, db, plaintext)
    return WhoamiOut(member=_member_out(member), expires_at=_iso(min(session.expires_at, session.max_expires_at)))


__all__ = ["VERIFICATION_PATH", "router"]
