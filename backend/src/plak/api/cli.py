"""CLI login API (`plak login`): the device authorization grant (RFC 8628)
that Plak brokers on top of its own admin SSO login.

The CLI side lives here and needs no session: asking for a device code,
polling the token endpoint, and logging out or asking who it is with its
access token. The member side (looking up, approving and denying a user code,
and the list of linked sessions) needs a fresh admin session with CSRF and
lives in api/admin.py.

The token endpoint answers RFC 8628's error codes as problem+json 400 with
`code` in SCREAMING_SNAKE (`AUTHORIZATION_PENDING`, `SLOW_DOWN`,
`EXPIRED_TOKEN`, `ACCESS_DENIED`, `INVALID_GRANT`), matching the rest of the
API rather than OAuth's own JSON error shape.
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Request
from pydantic import ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import Response

from plak import net
from plak.api.deploys import bearer_from_request, cli_member
from plak.api.docs import SECURITY_BEARER, TAG_CLI
from plak.api.errors import WWW_AUTHENTICATE_BEARER, ApiError, error_responses
from plak.api.schema import ApiModel, iso_utc
from plak.audit import request as audit_request
from plak.audit import vocabulary
from plak.audit.log import Actor
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
    """The start of `plak login`."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"clientName": "plak-cli 1.2 on macOS"}, {}]})

    client_name: str | None = Field(
        default=None,
        max_length=cli.CLIENT_NAME_MAX_LENGTH,
        description=(
            "How the CLI names itself; shown on the approval screen and in the list of linked CLI sessions. "
            f"At most {cli.CLIENT_NAME_MAX_LENGTH} characters; control and formatting characters are stripped."
        ),
        examples=["plak-cli 1.2 on macOS"],
    )


class DeviceAuthorizationOut(ApiModel):
    """What the CLI needs to finish signing in (RFC 8628 3.2)."""

    device_code: str = Field(
        description="Secret for the CLI itself: it uses this to request the tokens. Never show or log it."
    )
    user_code: str = Field(
        description=(
            "Code to show in the terminal and to compare or type in the admin interface: eight characters "
            "without 0, O, 1 and I, with a hyphen in the middle. Case-insensitive."
        ),
        examples=["WDJB-MJHT"],
    )
    verification_uri: str = Field(
        description="Page in the admin interface where the member enters the code.",
        examples=["https://beheer.plak.example/cli-link"],
    )
    verification_uri_complete: str = Field(
        description="The same page with the code already filled in; for opening in a browser.",
        examples=["https://beheer.plak.example/cli-link?code=WDJB-MJHT"],
    )
    expires_in: int = Field(description="Seconds until the codes expire.", examples=[600])
    interval: int = Field(description="Minimum number of seconds the CLI waits between attempts.", examples=[5])


class TokenRequest(ApiModel):
    """Exchange a device code, or a refresh token."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"grantType": "device_code", "deviceCode": "plakdc_..."},
                {"grantType": "refresh_token", "refreshToken": "plakclr_..."},
            ]
        }
    )

    grant_type: Literal["device_code", "refresh_token"] = Field(
        description="`device_code` after approval, `refresh_token` to refresh.",
        examples=["device_code"],
    )
    device_code: str | None = Field(
        default=None, max_length=200, description="The `deviceCode`, for `grantType` `device_code`."
    )
    refresh_token: str | None = Field(
        default=None, max_length=200, description="The latest refresh token, for `grantType` `refresh_token`."
    )


class CliMemberOut(ApiModel):
    """The member behind a CLI session."""

    email: str = Field(description="E-mail address of the member.", examples=["lid@example.nl"])
    name: str = Field(description="Display name; empty if the identity provider does not send one.", examples=["Sanne"])


class TokensOut(ApiModel):
    """A fresh set of tokens. The refresh token is invalid after use: always keep the new one."""

    access_token: str = Field(description="Access token `plakcli_...` for `Authorization: Bearer`.")
    refresh_token: str = Field(
        description=(
            "Refresh token `plakclr_...`. Single use: presenting a refresh token that has already been used "
            "revokes the entire CLI session."
        )
    )
    token_type: Literal["Bearer"] = Field(description="Always `Bearer`.", examples=["Bearer"])
    expires_in: int = Field(description="Seconds until the access token expires.", examples=[3600])
    member: CliMemberOut = Field(description="The member on whose behalf the CLI now acts.")


class WhoamiOut(ApiModel):
    """The CLI's member and session expiry."""

    member: CliMemberOut = Field(description="The member on whose behalf the CLI acts.")
    expires_at: str = Field(
        description=(
            "Time at which the CLI session expires if it is no longer refreshed: 30 days after the last "
            "refresh, and never later than 90 days after sign-in."
        ),
        json_schema_extra={"format": "date-time", "examples": ["2026-10-19T09:30:00Z"]},
    )


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
    per_ip = await counter.increment(f"ip:{net.rate_limit_key(ip or net.UNKNOWN)}", DEVICE_CREATE_WINDOW_S, now)
    if per_ip.count > DEVICE_CREATE_MAX_PER_IP:
        # Already refused on its own budget: do not also spend the shared
        # backstop, or a few over-budget IPs could exhaust it for everyone.
        return False
    backstop = await counter.increment("global", DEVICE_CREATE_WINDOW_S, now)
    return backstop.count <= DEVICE_CREATE_MAX_GLOBAL


def _factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


@router.post(
    "/cli/device-authorizations",
    tags=[TAG_CLI],
    openapi_extra=_NO_AUTH,
    summary="Start signing in with the CLI",
    response_description="The device code for the CLI and the user code for the member.",
    description=(
        "Step 1 of `plak login` (RFC 8628). The CLI receives a secret `deviceCode` and shows the `userCode` "
        "plus `verificationUri`; the member opens that page in the admin interface, compares the code and "
        "approves. Meanwhile the CLI calls `POST /cli/tokens` every `interval` seconds. The codes expire "
        "after ten minutes.\n\n"
        "**Who can call this:** anyone, without authentication. Each IP address has its own limit on the number "
        "of requests, on top of the general rate limit."
    ),
    responses=error_responses(
        {
            422: f"`clientName` is longer than {cli.CLIENT_NAME_MAX_LENGTH} characters.",
            429: "Too many requests from this address (`TOO_MANY_REQUESTS`); try again later.",
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
    summary="Fetch or refresh tokens",
    response_description="An access token and a new refresh token.",
    description=(
        "With `grantType` `device_code`: the CLI asks whether the member has approved yet. Until then, the "
        "response is 400 `AUTHORIZATION_PENDING`; polling faster than `interval` returns `SLOW_DOWN`, and the CLI "
        "must then wait five seconds longer between polls. After approval the tokens are returned once; after "
        "that the device code is spent.\n\n"
        "With `grantType` `refresh_token`: a new access token and a new refresh token. The old "
        "refresh token is invalid afterwards. If the refresh token that was just replaced is presented again "
        "within ten seconds (two refreshes at the same time), the response is `INVALID_GRANT` and the "
        "session remains valid. If a used refresh token is presented after that window, or an older one is "
        "presented, two parties "
        "hold the same session: Plak revokes the entire CLI session and writes `cli_refresh_reuse` to the "
        "audit log. A CLI session expires 30 days after the last refresh and "
        "in any case 90 days after sign-in.\n\n"
        "**Who can call this:** anyone with a valid device code or refresh token; there is no other "
        "authentication."
    ),
    responses=error_responses(
        {
            400: (
                "Not approved yet (`AUTHORIZATION_PENDING`), asked too fast (`SLOW_DOWN`), the "
                "device code has expired (`EXPIRED_TOKEN`), the member refused (`ACCESS_DENIED`), or the code "
                "or the refresh token is unknown, already used, expired or belongs to an inactive member "
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
        actor = Actor(ActorKind.MEMBER, error.member_sub) if error.member_sub else Actor(ActorKind.ANONYMOUS)
        await audit_request.write(
            request,
            vocabulary.CLI_REFRESH_REUSE,
            actor,
            vocabulary.REFUSED,
            reason_code=cli.INVALID_GRANT,
            refs={"via": vocabulary.VIA_CLI},
        )
        raise ApiError(400, error.message.key, params=error.message.params) from None
    except cli.GrantError as error:
        raise ApiError(400, error.message.key, params=error.message.params) from None
    if body.grant_type == "device_code":
        # The approval (cli_login) says who allowed it; this row says the
        # credential actually left Plak, and which session it belongs to.
        await audit_request.write(
            request,
            vocabulary.CLI_TOKEN_ISSUED,
            audit_request.member_actor(issued.member),
            vocabulary.ALLOWED,
            refs={"via": vocabulary.VIA_CLI, "cli_session": str(issued.session.id)},
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
        "Missing, invalid, expired or revoked access token (`TOKEN_INVALID`). The response carries "
        "`WWW-Authenticate: Bearer`."
    ),
}
_WHOAMI_ERRORS = {
    **_BEARER_ERRORS,
    403: "The member behind this CLI session is no longer active (`MEMBER_NOT_ACTIVE`).",
}


def _access_token(request: Request) -> str:
    plaintext = bearer_from_request(request)
    if not plaintext:
        raise ApiError(401, "TOKEN_INVALID.missing", headers=WWW_AUTHENTICATE_BEARER)
    return plaintext


class LogoutRequest(ApiModel):
    """Sign out with the refresh token, for when there is no (valid) access token any more."""

    model_config = ConfigDict(json_schema_extra={"examples": [{"refreshToken": "plakclr_..."}]})

    refresh_token: str = Field(
        max_length=200, description="A refresh token of the session, the current one or one that was already used."
    )


@router.delete(
    "/cli/session",
    status_code=204,
    tags=[TAG_CLI],
    openapi_extra={"security": [{SECURITY_BEARER: []}, {}]},
    summary="Sign out with the CLI",
    description=(
        "Revokes the CLI session (`plak logout`), with all its tokens; it disappears from the list of linked CLI "
        "sessions. Identify the session in one of two ways:\n\n"
        "* `Authorization: Bearer plakcli_...`: the access token, even if it has already expired, as long as "
        "it is genuine and the session still exists;\n"
        "* a JSON body `{\"refreshToken\": \"plakclr_...\"}` (as in RFC 7009), the current refresh token or "
        "one that was already used.\n\n"
        "The response is always 204, even for an unknown or already revoked token: this way it does not "
        "reveal which tokens exist. The audit log does distinguish the two cases (`cli_logout` with "
        "`allowed` or `refused`).\n\n"
        "**Who can call this:** anyone who holds a token of the session; also a member who has been deactivated "
        "in the meantime."
    ),
    responses={204: {"description": "The CLI session does not exist (any more). No content is returned."}}
    | error_responses({422: "The body is not an object with a `refreshToken` of at most 200 characters."}),
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
    if session is None:
        await audit_request.write(
            request,
            vocabulary.CLI_LOGOUT,
            Actor(ActorKind.ANONYMOUS),
            vocabulary.REFUSED,
            reason_code="TOKEN_INVALID",
            refs={"via": vocabulary.VIA_CLI},
        )
        return Response(status_code=204)
    await audit_request.write(
        request,
        vocabulary.CLI_LOGOUT,
        audit_request.member_actor(member) if member is not None else Actor(ActorKind.ANONYMOUS),
        vocabulary.ALLOWED,
        refs={"via": vocabulary.VIA_CLI, "cli_session": str(session.id)},
    )
    return Response(status_code=204)


@router.get(
    "/cli/whoami",
    tags=[TAG_CLI],
    openapi_extra=_BEARER,
    summary="Show the signed-in CLI member",
    response_description="The member behind this access token and when the session expires.",
    description=(
        "The member on whose behalf the CLI acts (`plak whoami`), and when the CLI session expires if it "
        "is not refreshed. The member must still be active, as with every deploy.\n\n"
        "**Who can call this:** the holder of a valid CLI access token (`Authorization: Bearer plakcli_...`)."
    ),
    responses=error_responses(_WHOAMI_ERRORS),
)
async def whoami(request: Request) -> WhoamiOut:
    plaintext = _access_token(request)
    async with _factory(request)() as db:
        session, member = await cli_member(request, db, plaintext)
    return WhoamiOut(member=_member_out(member), expires_at=iso_utc(min(session.expires_at, session.max_expires_at)))


__all__ = ["VERIFICATION_PATH", "router"]
