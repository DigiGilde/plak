"""Deploy API: live/preview deploys and preview teardown.

Auth is one of three:

- a CI ID token (`Authorization: Bearer <JWT>` from GitHub or Forgejo
  Actions), verified by ci/tokens.py and matched against the site's linked
  repository by ci/trust.py;
- a CLI access token (`Authorization: Bearer plakcli_...` from `plak login`),
  which acts as its member with exactly that member's roles;
- a beheer session with CSRF, for the upload in the SPA.

Bearer is accepted on these two endpoints and the two CLI session endpoints
only: `BearerOutsideDeploysMiddleware` rejects any other request carrying a
Bearer Authorization header with a 401.
main.py registers router and middleware; the app supplies on app.state:
settings, session_factory, session_store, content_store and audit_log.

The upload is never read into memory: the multipart body streams chunk by
chunk into a spool file in `{content_root}/_tmp/` (outside any servable
path), with the body limit checked incrementally; the ingest then unpacks
from that file.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from fastapi import APIRouter, Request
from pydantic import Field
from python_multipart import MultipartParser
from python_multipart.exceptions import FormParserError
from python_multipart.multipart import parse_options_header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from plak import messages, net
from plak.api.authorization import require_site_role
from plak.api.docs import TAG_DEPLOYS
from plak.api.errors import (
    WWW_AUTHENTICATE_BEARER,
    ApiError,
    error_responses,
    locale_from_header,
    problem_response,
)
from plak.api.schema import ApiModel
from plak.audit import vocabulary
from plak.audit.log import ANONYMOUS, Actor, AuditLog
from plak.auth import sessions
from plak.ci import trust
from plak.ci.providers import ProviderClient
from plak.ci.tokens import CiTokenError, CiTokenVerifier, VerifiedCiToken, looks_like_jwt
from plak.cli import service as cli
from plak.constants import SLUG_RE, Role
from plak.ingest.service import Deployer, IngestError, IngestService
from plak.ingest.store import ContentStore
from plak.ingest.unpacker import BundleError
from plak.messages import Msg
from plak.models.audit import ActorKind
from plak.models.identity import Group, Member, MemberStatus
from plak.models.publication import Site

AUDIT_ACTION_DEPLOY = "deploy"
AUDIT_ACTION_PREVIEW_TEARDOWN = "preview_teardown"
# reason_code for a deploy that failed on something other than a refusal, for
# instance a full disk. The client sees the generic 500; the log keeps the row.
AUDIT_REASON_INTERNAL = "INTERNAL_ERROR"

_logger = logging.getLogger(__name__)

_DEPLOY_PATH_RE = re.compile(r"^/-/api/v1/sites/[^/]+/[^/]+/deploys$")
_PREVIEW_PATH_RE = re.compile(r"^/-/api/v1/sites/[^/]+/[^/]+/previews/[^/]+$")
# The CLI's own session endpoints (api/cli.py) authenticate with the access
# token as well.
CLI_SESSION_PATH = "/-/api/v1/cli/session"
CLI_WHOAMI_PATH = "/-/api/v1/cli/whoami"

router = APIRouter(prefix="/-/api/v1")

FILE_FIELD = "file"
PREVIEW_FIELD = "preview"
BASE_PATH_FIELD = "basePath"
# Non-file fields stay in memory: keep them small (a preview ref is a slug of
# at most 63 characters).
MAX_FIELD_BYTES = 4096
MAX_FIELDS = 16

_DEPLOY_OPENAPI = {
    "requestBody": {
        "required": True,
        "description": (
            "De te publiceren bundel, als `multipart/form-data`. Het lichaam wordt streamend verwerkt, dus een "
            "grote bundel hoeft nergens in geheugen te passen."
        ),
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": [FILE_FIELD],
                    "properties": {
                        FILE_FIELD: {
                            "type": "string",
                            "format": "binary",
                            "description": (
                                "Het bestandsveld, verplicht en precies een keer. Geaccepteerd worden een los "
                                "`.html`-bestand (dat wordt de `index.html` van de site) of een archief "
                                "`.zip`, `.tar.gz` of `.tgz`. De vorm wordt aan de bestandsnaam herkend; iets "
                                "anders levert 422 `UNKNOWN_FORMAT`. Paden in het archief moeten relatief en "
                                "veilig zijn: absolute paden, `..`, symlinks en de gereserveerde topsegmenten "
                                "`_preview` en `_version` worden geweigerd."
                            ),
                        },
                        PREVIEW_FIELD: {
                            "type": "string",
                            "pattern": SLUG_RE.pattern,
                            "examples": ["pr-42"],
                            "description": (
                                "Optioneel. Met dit veld wordt de bundel een preview onder deze ref, in plaats "
                                "van de live site. De ref is een slug (kleine letters, cijfers, koppeltekens, "
                                "hoogstens 63 tekens) en is meestal het pull-requestnummer. Bestaat de preview "
                                "al, dan wordt hij vervangen en schuift zijn vervaltijd vooruit. Weglaten voor "
                                "een live-deploy."
                            ),
                        },
                        BASE_PATH_FIELD: {
                            "type": "string",
                            "examples": ["dist"],
                            "description": (
                                "Optioneel. De map binnen het archief die de wortel van de site wordt; alles "
                                "wat ernaast staat wordt niet gepubliceerd. Het pad is relatief aan de wortel "
                                "na het afpellen van omhullende mappen en moet een bestaande map met een "
                                "`index.html` erin zijn. Bedoeld als bevestiging van het voorstel dat een "
                                "geweigerde deploy meegeeft in `indexCandidates`; zonder dit veld bepaalt "
                                "Plak de wortel zelf."
                            ),
                        },
                    },
                },
                "encoding": {
                    FILE_FIELD: {"contentType": "text/html, application/zip, application/gzip"},
                },
            }
        },
    }
}


class DeployResult(ApiModel):
    """Wat CI terugkrijgt na een geslaagde deploy."""

    version_id: uuid.UUID = Field(
        description=(
            "Id van de zojuist aangemaakte versie. Hiermee is de deploy terug te vinden in de versielijst van "
            "de site, en hiernaar is later terug te rollen."
        )
    )


# Authorization is identical for both deploy endpoints; only the occasion
# differs, so they share these error descriptions.
_DEPLOY_ERRORS = {
    401: (
        "Er is noch een bearer-token noch een geldige beheersessie meegestuurd (`NO_AUTHENTICATION`), het "
        "CLI-token is ongeldig, ingetrokken of verlopen (`TOKEN_INVALID`), of het CI-token wordt geweigerd: "
        "het komt niet van GitHub of een geconfigureerde Forgejo (`CI_ISSUER_UNKNOWN`), de handtekening of "
        "geldigheid klopt niet (`CI_TOKEN_INVALID`), of de audience is niet precies de beheer-URL "
        "(`CI_AUDIENCE_MISMATCH`). Het antwoord draagt dan `WWW-Authenticate: Bearer`."
    ),
    403: (
        "Bij een CI-token: de repository is niet aan deze site gekoppeld (`CI_REPOSITORY_NOT_TRUSTED`), of een "
        "live-deploy komt niet uit `push`, `workflow_dispatch` of `schedule`, of niet van de live-branch "
        "(`CI_BRANCH_NOT_ALLOWED`). Bij een CLI-token of sessie: het "
        "lid heeft op deze site niet minimaal de rol `editor` (`INSUFFICIENT_ROLE`) of is niet actief "
        "(`MEMBER_NOT_ACTIVE`); bij een sessie ook: de CSRF-header ontbreekt of klopt niet (`CSRF_INVALID`). "
        "Een verzoek van een andere origin dan de beheer-host wordt eveneens geweigerd; CI en de CLI sturen "
        "geen `Origin` en passeren die bewaking."
    ),
    404: "Onbekende groep of onbekend site (`UNKNOWN_SITE`).",
    429: "Het ratelimit-budget is op; probeer het later opnieuw.",
    503: (
        "De CI-provider is niet bereikbaar om de sleutels op te halen of de repository te controleren "
        "(`CI_PROVIDER_UNREACHABLE`); probeer het later opnieuw."
    ),
}


@dataclass(frozen=True)
class _CiPrincipal:
    token: VerifiedCiToken
    repository: trust.TrustedRepository | None = None


@dataclass(frozen=True)
class _DeployAuth:
    member: Member | None
    ci: _CiPrincipal | None
    actor: Actor
    via_cli: bool = False
    cli_session_id: uuid.UUID | None = None

    @property
    def member_id(self) -> uuid.UUID | None:
        return self.member.id if self.member is not None else None


def is_deploy_endpoint(method_: str, path: str) -> bool:
    if method_ == "POST" and _DEPLOY_PATH_RE.match(path):
        return True
    return bool(method_ == "DELETE" and _PREVIEW_PATH_RE.match(path))


def accepts_bearer(method_: str, path: str) -> bool:
    """The deploy endpoints plus the CLI's logout and whoami."""
    if is_deploy_endpoint(method_, path):
        return True
    return (method_, path) in (("DELETE", CLI_SESSION_PATH), ("GET", CLI_WHOAMI_PATH))


def _bearer_plaintext(authorization: str | None) -> str | None:
    """Returns the token value when the scheme (case-insensitive) is Bearer;
    None when there is no Bearer header."""
    if not authorization:
        return None
    schema, _, rest = authorization.partition(" ")
    if schema.lower() != "bearer":
        return None
    return rest.strip()


def bearer_from_request(request: Request) -> str | None:
    return _bearer_plaintext(request.headers.get("Authorization"))


class BearerOutsideDeploysMiddleware:
    """Rejects Bearer requests outside the endpoints that accept one."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            authorization = Headers(scope=scope).get("authorization")
            if _bearer_plaintext(authorization) is not None and not accepts_bearer(
                scope["method"], scope["path"]
            ):
                locale = locale_from_header(Headers(scope=scope).get("accept-language"))
                response = problem_response(
                    401,
                    messages.render(locale, Msg("BEARER_NOT_ACCEPTED")),
                    locale=locale,
                    code="BEARER_NOT_ACCEPTED",
                    headers=WWW_AUTHENTICATE_BEARER,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _decode(value: bytes) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        return value.decode("latin-1")


@dataclass(frozen=True)
class _Upload:
    filename: str
    spool: Path
    fields: dict[str, str]


class _MultipartSpooler:
    """Callbacks for python-multipart: the data of the `file` part is
    collected chunk by chunk in `to_write` (the loop flushes it to the spool
    file), the other fields land bounded in `fields`."""

    def __init__(self) -> None:
        self.filename: str | None = None
        self.file_complete = False
        self.end_seen = False
        self.fields: dict[str, str] = {}
        self.to_write: list[bytes] = []
        self._header_name = b""
        self._header_value = b""
        self._disposition: bytes | None = None
        self._field_name = ""
        self._is_file = False
        self._value = bytearray()
        self._field_count = 0

    def on_part_begin(self) -> None:
        self._disposition = None
        self._field_name = ""
        self._is_file = False
        self._value = bytearray()

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._header_name += data[start:end]

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._header_value += data[start:end]

    def on_header_end(self) -> None:
        if self._header_name.lower() == b"content-disposition":
            self._disposition = self._header_value
        self._header_name = b""
        self._header_value = b""

    def on_headers_finished(self) -> None:
        _, options = parse_options_header(self._disposition)
        name = options.get(b"name")
        if name is None:
            raise ApiError(422, "MULTIPART_INVALID.no_field_name")
        self._field_name = _decode(name)
        if b"filename" in options:
            if self._field_name != FILE_FIELD:
                raise ApiError(
                    422,
                    "MULTIPART_INVALID.unexpected_file_field",
                    params={"field": self._field_name},
                )
            if self.filename is not None:
                raise ApiError(422, "MULTIPART_INVALID.more_than_one_file")
            self.filename = _decode(options[b"filename"])
            self._is_file = True
            return
        self._field_count += 1
        if self._field_count > MAX_FIELDS:
            raise ApiError(422, "MULTIPART_INVALID.too_many_fields")

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self._is_file:
            self.to_write.append(data[start:end])
            return
        if len(self._value) + (end - start) > MAX_FIELD_BYTES:
            raise ApiError(
                422, "MULTIPART_INVALID.field_too_large", params={"field": self._field_name}
            )
        self._value += data[start:end]

    def on_part_end(self) -> None:
        if self._is_file:
            self.file_complete = True
        else:
            self.fields[self._field_name] = _decode(bytes(self._value))

    def on_end(self) -> None:
        self.end_seen = True


def _too_large(max_body: int) -> ApiError:
    return ApiError(413, "BODY_TOO_LARGE", params={"max_body": max_body})


def _invalid_multipart(error: Exception) -> ApiError:
    # The parser's own wording stays in the log: `detail` never carries
    # internal details (api/errors.py).
    _logger.info("multipart body refused: %s", error)
    return ApiError(422, "MULTIPART_INVALID")


async def _spool_upload(request: Request, store: ContentStore, max_body: int) -> _Upload:
    """Streams the request body into a spool file in the store's tempdir. A
    Content-Length above the limit is rejected without reading; without (or
    with a matching) Content-Length, reading stops at the first chunk that
    crosses the limit. On any error the spool file is gone."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > max_body:
        raise _too_large(max_body)
    kind, options = parse_options_header(request.headers.get("content-type"))
    boundary = options.get(b"boundary")
    if kind != b"multipart/form-data" or not boundary:
        raise ApiError(422, "NOT_MULTIPART", params={"field": FILE_FIELD})

    spooler = _MultipartSpooler()
    try:
        parser = MultipartParser(
            boundary,
            {
                "on_part_begin": spooler.on_part_begin,
                "on_part_data": spooler.on_part_data,
                "on_part_end": spooler.on_part_end,
                "on_header_field": spooler.on_header_field,
                "on_header_value": spooler.on_header_value,
                "on_header_end": spooler.on_header_end,
                "on_headers_finished": spooler.on_headers_finished,
                "on_end": spooler.on_end,
            },
        )
    except FormParserError as error:
        raise _invalid_multipart(error) from error

    spool_path = store.new_spool_file()
    spool: IO[bytes] = await run_in_threadpool(spool_path.open, "xb")
    read_bytes_count = 0
    succeeded = False
    try:
        async for chunk in request.stream():
            read_bytes_count += len(chunk)
            if read_bytes_count > max_body:
                raise _too_large(max_body)
            try:
                parser.write(chunk)
            except FormParserError as error:
                raise _invalid_multipart(error) from error
            if spooler.to_write:
                await run_in_threadpool(spool.writelines, spooler.to_write)
                spooler.to_write = []
        parser.finalize()
        await run_in_threadpool(spool.close)
        if not spooler.filename or not spooler.file_complete:
            raise ApiError(422, "FILE_MISSING", params={"field": FILE_FIELD})
        if not spooler.end_seen:
            raise ApiError(422, "MULTIPART_INVALID.incomplete")
        succeeded = True
    except ClientDisconnect as error:
        raise ApiError(400, "CLIENT_ABORTED") from error
    finally:
        # On success the spool stays until the ingest is done with it.
        if not succeeded:
            spool.close()
            spool_path.unlink(missing_ok=True)
    return _Upload(filename=spooler.filename, spool=spool_path, fields=spooler.fields)


async def _audit(
    request: Request,
    actor: Actor,
    action: str,
    result: str,
    reason_code: str | None,
    refs: dict,
) -> None:
    # The ApiError handler audits refusals generically; this endpoint writes a
    # richer record of its own (CI or member actor, group, site), so it says so.
    request.state.audit_written = True
    log: AuditLog = request.app.state.audit_log
    ip = net.client_ip_from_request(request)
    await log.write(action, actor, result, reason_code=reason_code, refs=refs, ip=ip)


async def _audit_best_effort(
    request: Request,
    actor: Actor,
    action: str,
    result: str,
    reason_code: str | None,
    refs: dict,
) -> None:
    """`_audit` for use inside an exception handler. AuditLog.write is
    fail-open, but the steps around it are not, and a raise here would bury
    the exception that is already on its way out."""
    try:
        await _audit(request, actor, action, result, reason_code, refs)
    except Exception:
        _logger.exception("audit row for the failed deploy could not be written")


def _token_invalid() -> ApiError:
    return ApiError(401, "TOKEN_INVALID", headers=WWW_AUTHENTICATE_BEARER)


def ci_error(error: CiTokenError) -> ApiError:
    headers = WWW_AUTHENTICATE_BEARER if error.status == 401 else None
    return ApiError(error.status, error.message.key, params=error.message.params, headers=headers)


async def cli_member(request: Request, db: AsyncSession, plaintext: str) -> tuple[cli.CliSession, Member]:
    """The active member behind a CLI access token; the same 401/403 as a
    session would get. Records the use best-effort."""
    session = await cli.session_for_access_token(db, plaintext)
    if session is None:
        raise _token_invalid()
    member = await db.get(Member, session.member_id)
    if member is None or member.status != MemberStatus.ACTIVE:
        raise ApiError(403, "MEMBER_NOT_ACTIVE")
    # A session of its own: a failure here must leave `db` and the instances
    # it loaded untouched, since a rollback would expire them.
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    try:
        async with factory() as bookkeeping_db:
            await cli.mark_used(bookkeeping_db, session.id)
    except Exception:
        _logger.warning("last use of CLI session could not be recorded", exc_info=True)
    return session, member


async def _authenticate(request: Request, db: AsyncSession) -> _DeployAuth:
    plaintext = bearer_from_request(request)
    if plaintext is not None:
        if plaintext.startswith(cli.ACCESS_TOKEN_PREFIX + "_"):
            session, member = await cli_member(request, db, plaintext)
            return _DeployAuth(
                member=member,
                ci=None,
                actor=Actor(ActorKind.MEMBER, member.sso_subject),
                via_cli=True,
                cli_session_id=session.id,
            )
        if looks_like_jwt(plaintext):
            verifier: CiTokenVerifier = request.app.state.ci_verifier
            try:
                token = await verifier.verify(plaintext)
            except CiTokenError as error:
                raise ci_error(error) from None
            return _DeployAuth(
                member=None,
                ci=_CiPrincipal(token=token),
                actor=Actor(ActorKind.CI, trust.refused_actor_identifier(token)),
            )
        raise _token_invalid()

    session = sessions.session_from_request(request)
    if session is None:
        raise ApiError(401, "NO_AUTHENTICATION")
    if not sessions.csrf_valid(request, session):
        raise ApiError(403, "CSRF_INVALID")
    member = await db.scalar(select(Member).where(Member.sso_subject == session.sub))
    if member is None or member.status != MemberStatus.ACTIVE:
        raise ApiError(403, "MEMBER_NOT_ACTIVE")
    return _DeployAuth(member=member, ci=None, actor=Actor(ActorKind.MEMBER, session.sub))


async def _find_group_and_site(
    db: AsyncSession, group_slug: str, site_slug: str
) -> tuple[Group, Site]:
    row = (
        await db.execute(
            select(Group, Site)
            .join(Site, Site.group_id == Group.id)
            .where(Group.slug == group_slug, Site.slug == site_slug)
        )
    ).one_or_none()
    if row is None:
        raise ApiError(404, "UNKNOWN_SITE.deploy")
    return row.Group, row.Site


async def _authorize(request: Request, db: AsyncSession, auth: _DeployAuth, site: Site) -> _DeployAuth:
    """Returns the auth to carry on with: for CI, with the matched repository
    and its actor pseudonymised on the stored repository id."""
    if auth.ci is None:
        await require_site_role(db, auth.member, site, Role.EDITOR)
        return auth
    providers: ProviderClient = request.app.state.ci_providers
    try:
        repository = await trust.trusted_repository(db, auth.ci.token, site, providers)
    except CiTokenError as error:
        raise ci_error(error) from None
    return _DeployAuth(
        member=None,
        ci=_CiPrincipal(token=auth.ci.token, repository=repository),
        actor=Actor(ActorKind.CI, repository.actor_identifier),
    )


def _auth_refs(auth: _DeployAuth | None) -> dict:
    if auth is None:
        return {}
    if auth.ci is not None:
        return trust.audit_refs(auth.ci.token)
    if auth.via_cli:
        return {"via": vocabulary.VIA_CLI, "cli_session": str(auth.cli_session_id)}
    return {}


def _deployer(auth: _DeployAuth) -> Deployer:
    if auth.ci is not None and auth.ci.repository is not None:
        return Deployer(ci_repository=auth.ci.repository.origin)
    return Deployer(member_id=auth.member_id)


@router.post(
    "/sites/{group_slug}/{site_slug}/deploys",
    status_code=201,
    openapi_extra=_DEPLOY_OPENAPI,
    tags=[TAG_DEPLOYS],
    summary="Een bundel publiceren, live of als preview",
    response_description="De bundel is uitgepakt en gepubliceerd; de id van de nieuwe versie komt terug.",
    description=(
        "Het endpoint waar CI op bouwt. Stuur de gebouwde site als `multipart/form-data` met het veld "
        "`file`; zonder het veld `preview` vervangt de bundel de live site, met `preview` komt hij onder "
        "`/{groupSlug}/{siteSlug}/_preview/{ref}/` te staan.\n\n"
        "**Mag:** drie manieren. (1) Een CI-ID-token van GitHub of Forgejo Actions "
        "(`Authorization: Bearer <JWT>`) met als audience precies de beheer-URL van Plak, uit de repository "
        "die aan deze site gekoppeld is; een live-deploy moet dan uit een `push`, `workflow_dispatch` of "
        "`schedule` komen, en van de live-branch als die is ingesteld; een preview mag vanaf elke branch. "
        "(2) Een CLI-token uit `plak login` "
        "(`Authorization: Bearer plakcli_...`): dat handelt als het lid dat inlogde, met precies diens "
        "rollen. (3) Een beheersessie plus CSRF-header. Bij (2) en (3) moet het lid actief zijn en op deze "
        "site minstens de rol `editor` hebben. Dit endpoint en de preview-teardown zijn samen met de "
        "CLI-sessie-endpoints de enige die een Bearer-token accepteren; elders levert die header 401.\n\n"
        "**Verloop:** eerst wordt geautoriseerd, pas daarna wordt het lichaam gelezen, zodat een geweigerd "
        "verzoek geen upload kost. De upload streamt naar schijf en wordt uitgepakt tegen de limieten "
        "hieronder. Elke deploy, geslaagd of geweigerd, komt in het auditlogboek.\n\n"
        "**Wortel van de site:** omhullende mappen worden afgepeld zolang de wortel precies een map bevat "
        "en verder niets, dus een archief met alleen `mijnsite/dist/index.html` landt gewoon op de "
        "siteroot. Metadata van het besturingssysteem telt daarbij niet mee en wordt ook niet "
        "gepubliceerd: de map `__MACOSX`, `.DS_Store` en de AppleDouble-bestanden die met `._` "
        "beginnen. Een zip die je met rechtsklik in de Finder maakt werkt daardoor gewoon. "
        "Daarna moet er een `index.html` in de wortel staan; zo niet, dan volgt 422 "
        "`NO_INDEX` met de gevonden index-paden in `indexCandidates`. Staat de site in een map naast "
        "andere dingen (een gezipte projectmap), stuur die map dan als veld `basePath` mee: dat is de "
        "bevestiging van het voorstel. Plak kiest nooit zelf een van meerdere kandidaten, want dan zou het "
        "stilzwijgend bestanden weglaten die je dacht te publiceren. Het `basePath` staat relatief aan de "
        "wortel ná het afpellen, maar de spelling mét het afgepelde voorvoegsel werkt net zo goed, en een "
        "vaste waarde blijft werken als het afpellen die map al weggenomen heeft.\n\n"
        "**Limieten** (instelbaar; dit zijn de standaardwaarden): het verzoeklichaam is hoogstens 550 MB "
        "(`PLAK_INGEST_MAX_BODY`), een los uitgepakt bestand 100 MB (`PLAK_INGEST_MAX_FILE`), de hele "
        "uitgepakte site 500 MB (`PLAK_INGEST_MAX_TOTAL`), met hoogstens 1000 entries "
        "(`PLAK_INGEST_MAX_FILES`) en 10 niveaus mapdiepte (`PLAK_INGEST_MAX_DEPTH`). Die gelden op "
        "wat gepubliceerd wordt: wat buiten het `basePath` valt telt niet mee. Het archief als geheel mag "
        "hoogstens vijftig keer zoveel entries bevatten, en bij een `.tar.gz` telt de uitgepakte omvang van "
        "alle leden mee, ook de niet-gepubliceerde: een tar heeft geen index, dus bij de volgende header "
        "komen betekent alles ertussen decomprimeren. De limieten worden tijdens het uitpakken bewaakt en "
        "de headers worden vooraf al tegen dezelfde grenzen gehouden, dus ook een zip- of tar-bom komt er "
        "niet langs.\n\n"
        "**Voorbeeld** (`$PLAK_TOKEN` is een CI-ID-token of een CLI-token):\n\n"
        "```\n"
        "curl --fail --silent --show-error \\\n"
        '  --header "Authorization: Bearer $PLAK_TOKEN" \\\n'
        "  --form file=@dist.zip \\\n"
        "  --form preview=pr-42 \\\n"
        '  "$PLAK_ADMIN_URL/-/api/v1/sites/aurora/docs/deploys"\n'
        "```\n\n"
        'Antwoord: `201` met `{"versionId": "..."}`. Laat `--form preview=...` weg voor een live-deploy.'
    ),
    responses=error_responses(
        {
            **_DEPLOY_ERRORS,
            400: "De client brak de upload af voordat het lichaam compleet was (`CLIENT_ABORTED`).",
            413: (
                "De upload is groter dan de bodylimiet (`BODY_TOO_LARGE`), of de bundel wordt uitgepakt te "
                "groot: `FILE_TOO_LARGE`, `TOTAL_TOO_LARGE` of `TOO_MANY_FILES` (die laatste ook "
                "wanneer het archief als geheel te veel entries heeft; de melding zegt welke van de twee, "
                "en draagt waar mogelijk `indexCandidates`, want een gezipte projectmap loopt hier als "
                "eerste op vast)."
            ),
            422: (
                "Het verzoek is geen multipart/form-data (`NOT_MULTIPART`), het veld `file` ontbreekt "
                "(`FILE_MISSING`), de multipart-vorm klopt niet (`MULTIPART_INVALID`), `preview` is "
                "geen geldige slug (`PREVIEW_REF_INVALID`), of de bundel wordt geweigerd: onbekende vorm "
                "(`UNKNOWN_FORMAT`), onleesbaar of leeg archief (`INVALID_ARCHIVE`, `EMPTY_ARCHIVE`), "
                "een onveilig pad erin (`PATH_TRAVERSAL`, `ABSOLUTE_PATH`, `SYMLINK_REFUSED`, "
                "`HARDLINK_REFUSED`, `SPECIAL_FILE`, `RESERVED_SEGMENT`, `TOO_DEEP`, `DUPLICATE_PATH`, "
                "`NULL_BYTE`, `EMPTY_PATH`), een bestand dat niet op een website hoort (`SECRET_FILE`: "
                "een `.git`-map of een `.env`-bestand, die met een gezipte projectmap vanzelf "
                "meekomen), geen `index.html` in de wortel (`NO_INDEX`, met de gevonden "
                "paden in `indexCandidates`), of een `basePath` dat niet deugt (`BASE_PATH_INVALID`), geen "
                "map in de bundel is (`BASE_PATH_UNKNOWN`, ook wanneer het pad naar een bestand wijst) of "
                "geen `index.html` bevat (`BASE_PATH_WITHOUT_INDEX`, ook wanneer die map helemaal geen "
                "bestanden bevat)."
            ),
        }
    ),
)
async def deploy(request: Request, group_slug: str, site_slug: str) -> DeployResult:
    settings = request.app.state.settings
    store: ContentStore = request.app.state.content_store
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    refs: dict = {"group": group_slug, "site": site_slug}
    actor = ANONYMOUS
    upload: _Upload | None = None
    try:
        # Decide first, read the body only after: a rejected request costs no
        # disk and no reading.
        async with factory() as db:
            auth = await _authenticate(request, db)
            actor = auth.actor
            refs.update(_auth_refs(auth))
            group, site = await _find_group_and_site(db, group_slug, site_slug)
            auth = await _authorize(request, db, auth, site)
            actor = auth.actor

        upload = await _spool_upload(request, store, settings.ingest_max_body)
        preview = upload.fields.get(PREVIEW_FIELD)
        base_path = upload.fields.get(BASE_PATH_FIELD)
        # Both fields are attacker-controlled up to MAX_FIELD_BYTES, so they
        # are capped like a CI claim before they reach the log.
        if base_path is not None:
            refs["base_path"] = base_path[: trust.MAX_CLAIM_LENGTH]
        if preview is not None:
            if not SLUG_RE.match(preview):
                raise ApiError(422, "PREVIEW_REF_INVALID")
            refs["preview"] = preview[: trust.MAX_CLAIM_LENGTH]
        elif auth.ci is not None and auth.ci.repository is not None:
            try:
                trust.check_live_deploy(auth.ci.repository, auth.ci.token)
            except CiTokenError as error:
                raise ci_error(error) from None
    except ApiError as error:
        if upload is not None:
            upload.spool.unlink(missing_ok=True)
        await _audit(request, actor, AUDIT_ACTION_DEPLOY, "refused", error.reason, refs)
        raise
    except Exception:
        if upload is not None:
            upload.spool.unlink(missing_ok=True)
        _logger.exception("deploy failed before the ingest")
        await _audit_best_effort(request, actor, AUDIT_ACTION_DEPLOY, "refused", AUDIT_REASON_INTERNAL, refs)
        raise

    service = IngestService(store, settings)
    deployer = _deployer(auth)
    try:
        async with factory() as db:
            if preview is not None:
                version_id = await service.preview_deploy(
                    db, group, site, preview, upload.filename, upload.spool, deployer, base_path
                )
            else:
                version_id = await service.deploy(
                    db, group, site, upload.filename, upload.spool, deployer, base_path
                )
    except (BundleError, IngestError) as error:
        await _audit(request, actor, AUDIT_ACTION_DEPLOY, "refused", error.reason, refs)
        raise
    except Exception:
        _logger.exception("deploy failed during the ingest")
        await _audit_best_effort(request, actor, AUDIT_ACTION_DEPLOY, "refused", AUDIT_REASON_INTERNAL, refs)
        raise
    finally:
        upload.spool.unlink(missing_ok=True)

    await _audit(
        request,
        actor,
        AUDIT_ACTION_DEPLOY,
        "allowed",
        None,
        {**refs, "version_id": str(version_id)},
    )
    return DeployResult(version_id=version_id)


@router.delete(
    "/sites/{group_slug}/{site_slug}/previews/{ref}",
    status_code=204,
    tags=[TAG_DEPLOYS],
    summary="Een preview opruimen",
    description=(
        "Haalt de preview met deze ref weg, inclusief haar bestanden. Bedoeld voor de CI-stap die draait als "
        "een pull request sluit. De live site en de versiehistorie blijven ongemoeid.\n\n"
        "**Mag:** hetzelfde als de deploy: een CI-ID-token uit de gekoppelde repository (vanaf elke branch), "
        "een CLI-token of een beheersessie met CSRF-header van een actief lid met effectieve siterol "
        "`editor` of ruimer.\n\n"
        "**Idempotent:** een ref die niet (meer) bestaat levert ook 204, zodat een herhaalde opruimstap in CI "
        "niet alsnog rood wordt.\n\n"
        "**Voorbeeld:**\n\n"
        "```\n"
        "curl --fail --silent --show-error --request DELETE \\\n"
        '  --header "Authorization: Bearer $PLAK_TOKEN" \\\n'
        '  "$PLAK_ADMIN_URL/-/api/v1/sites/aurora/docs/previews/pr-42"\n'
        "```"
    ),
    responses={204: {"description": "De preview bestaat niet meer. Er komt geen inhoud terug."}}
    | error_responses(
        {**_DEPLOY_ERRORS, 422: "De ref in het pad is geen geldige slug (`PREVIEW_REF_INVALID`)."}
    ),
)
async def delete_preview(request: Request, group_slug: str, site_slug: str, ref: str) -> Response:
    settings = request.app.state.settings
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    refs = {"group": group_slug, "site": site_slug, "preview": ref}
    actor = ANONYMOUS
    try:
        async with factory() as db:
            auth = await _authenticate(request, db)
            actor = auth.actor
            refs.update(_auth_refs(auth))
            if not SLUG_RE.match(ref):
                raise ApiError(422, "PREVIEW_REF_INVALID")
            _, site = await _find_group_and_site(db, group_slug, site_slug)
            auth = await _authorize(request, db, auth, site)
            actor = auth.actor
    except ApiError as error:
        await _audit(request, actor, AUDIT_ACTION_PREVIEW_TEARDOWN, "refused", error.reason, refs)
        raise

    service = IngestService(request.app.state.content_store, settings)
    async with factory() as db:
        await service.delete_preview(db, site, ref)

    await _audit(request, actor, AUDIT_ACTION_PREVIEW_TEARDOWN, "allowed", None, refs)
    return Response(status_code=204)
