"""Deploy API: live/preview deploys and preview teardown.

Auth is one of three:

- a CI ID token (`Authorization: Bearer <JWT>` from GitHub or Forgejo
  Actions), verified by ci/tokens.py and matched against the site's linked
  repository by ci/trust.py;
- a CLI access token (`Authorization: Bearer plakcli_...` from `plak login`),
  which acts as its member with exactly that member's roles;
- an admin session with CSRF, for the upload in the SPA.

Bearer is accepted on these two endpoints, the two CLI session endpoints and
the two creation endpoints of the admin API (`POST /groups`, `POST
/groups/{group}/sites`, CLI token only) and nowhere else:
`BearerOutsideDeploysMiddleware` rejects any other request carrying a Bearer
Authorization header with a 401.
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
from urllib.parse import quote

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
from plak.access.gate import effective_access
from plak.api.authorization import require_site_role
from plak.api.docs import TAG_DEPLOYS
from plak.api.errors import (
    WWW_AUTHENTICATE_BEARER,
    ApiError,
    error_responses,
    locale_from_header,
    problem_response,
)
from plak.api.schema import AccessOut, ApiModel
from plak.audit import vocabulary
from plak.audit.log import ANONYMOUS, Actor, AuditLog
from plak.auth import sessions
from plak.ci import trust
from plak.ci.providers import ProviderClient
from plak.ci.tokens import CiTokenError, CiTokenVerifier, VerifiedCiToken, looks_like_jwt
from plak.cli import service as cli
from plak.constants import SLUG_RE, Role
from plak.ingest.service import Deployer, IngestError, IngestService, RoomGuard
from plak.ingest.store import ContentStore
from plak.ingest.unpacker import BundleError
from plak.messages import Msg
from plak.models.audit import ActorKind
from plak.models.identity import Group, Member, MemberStatus
from plak.models.publication import Preview, Site

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
# Creating a group or a site and linking a repository (api/admin.py) also
# take the CLI token; nothing else in the admin API does.
GROUP_CREATE_PATH = "/-/api/v1/groups"
_SITE_CREATE_PATH_RE = re.compile(r"^/-/api/v1/groups/[^/]+/sites$")
_REPOSITORY_LINK_PATH_RE = re.compile(r"^/-/api/v1/sites/[^/]+/[^/]+/repository$")

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
            "The bundle to publish, as `multipart/form-data`. The body is processed as a stream, so a large "
            "bundle never has to fit in memory."
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
                                "The `file` field, required and exactly once. Either a single "
                                "`.html` file (which becomes the site's `index.html`) or a `.zip`, "
                                "`.tar.gz` or `.tgz` archive. The format is recognised by the file name; anything "
                                "else yields 422 `UNKNOWN_FORMAT`. Paths in the archive must be relative and "
                                "safe: absolute paths, `..`, symlinks and the reserved top-level segments "
                                "`_preview` and `_version` are refused."
                            ),
                        },
                        PREVIEW_FIELD: {
                            "type": "string",
                            "pattern": SLUG_RE.pattern,
                            "examples": ["pr-42"],
                            "description": (
                                "Optional. With this field the bundle becomes a preview under this ref, instead "
                                "of the live site. The ref is a slug (lowercase letters, digits, hyphens, "
                                "at most 63 characters) and is usually the pull request number. If the preview "
                                "already exists, it is replaced and its expiry moves forward. Omit it for "
                                "a live deploy."
                            ),
                        },
                        BASE_PATH_FIELD: {
                            "type": "string",
                            "examples": ["dist"],
                            "description": (
                                "Optional. The directory inside the archive that becomes the root of the site; "
                                "everything next to it is not published. The path is relative to the root "
                                "directory after unwrapping enclosing directories and must be an existing directory "
                                "containing an `index.html`. Meant to confirm one of the suggestions a "
                                "refused deploy returns in `indexCandidates`; without this field "
                                "Plak determines the root directory itself."
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
    """What CI gets back after a successful deploy."""

    version_id: uuid.UUID = Field(
        description=(
            "ID of the version that was just created. It identifies the deploy in the site's version list, "
            "and it is what you roll back to later."
        )
    )
    url: str = Field(
        description=(
            "Where the deploy can be viewed: the live site, or for a preview the preview URL. CI can post a "
            "link to it, in a pull request for example."
        ),
        examples=["https://plak.example/team-aurora/website/_preview/pr-42/"],
    )
    access: AccessOut = Field(
        description=(
            "Who may see the deploy: for a preview the preview's own access if one is set, "
            "otherwise the site's. CI can use this to tell, alongside the link, whether signing in is needed."
        )
    )


# Authorization is identical for both deploy endpoints; only the occasion
# differs, so they share these error descriptions.
_DEPLOY_ERRORS = {
    401: (
        "Neither a bearer token nor a valid admin session was sent (`NO_AUTHENTICATION`), the "
        "CLI token is invalid, revoked or expired (`TOKEN_INVALID`), or the CI token is refused: "
        "it does not come from GitHub or a configured Forgejo (`CI_ISSUER_UNKNOWN`), the signature is "
        "invalid or the token is expired or not yet valid (`CI_TOKEN_INVALID`), or the audience is not exactly "
        "the admin URL (`CI_AUDIENCE_MISMATCH`). The response then carries `WWW-Authenticate: Bearer`."
    ),
    403: (
        "With a CI token: the repository is not linked to this site (`CI_REPOSITORY_NOT_TRUSTED`), or a "
        "live deploy does not come from `push`, `workflow_dispatch` or `schedule`, or not from the live branch "
        "(`CI_BRANCH_NOT_ALLOWED`). With a CLI token or session: the "
        "member does not have at least the `editor` role on this site (`INSUFFICIENT_ROLE`) or is not active "
        "(`MEMBER_NOT_ACTIVE`); with a session also: the CSRF header is missing or wrong (`CSRF_INVALID`). "
        "A request from an origin other than the admin host is refused as well; CI and the CLI send "
        "no `Origin` and pass that check."
    ),
    404: "Unknown group or unknown site (`UNKNOWN_SITE`).",
    429: "The rate limit budget is used up; try again later.",
    503: (
        "The CI provider cannot be reached to fetch the keys or to check the repository "
        "(`CI_PROVIDER_UNREACHABLE`); try again later."
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


def is_creation_endpoint(method_: str, path: str) -> bool:
    if method_ != "POST":
        return False
    return path == GROUP_CREATE_PATH or bool(_SITE_CREATE_PATH_RE.match(path))


def is_repository_link_endpoint(method_: str, path: str) -> bool:
    return method_ == "PUT" and bool(_REPOSITORY_LINK_PATH_RE.match(path))


def accepts_bearer(method_: str, path: str) -> bool:
    """The deploy endpoints, the CLI's logout and whoami, group and site
    creation, and linking a repository."""
    if (
        is_deploy_endpoint(method_, path)
        or is_creation_endpoint(method_, path)
        or is_repository_link_endpoint(method_, path)
    ):
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


async def _spool_upload(
    request: Request, store: ContentStore, max_body: int, room: RoomGuard
) -> _Upload:
    """Streams the request body into a spool file in the store's tempdir. A
    Content-Length above the limit is rejected without reading; without (or
    with a matching) Content-Length, reading stops at the first chunk that
    crosses the limit, or that `room` refuses. On any error the spool file is
    gone."""
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
                room.reserve(sum(map(len, spooler.to_write)))
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


async def _authorize(
    request: Request, db: AsyncSession, auth: _DeployAuth, group_slug: str, site: Site
) -> _DeployAuth:
    """Returns the auth to carry on with: for CI, with the matched repository
    and its actor pseudonymised on the stored repository id. A trusted token
    that names the repository differently renames the link."""
    if auth.ci is None:
        await require_site_role(db, auth.member, site, Role.EDITOR)
        return auth
    providers: ProviderClient = request.app.state.ci_providers
    try:
        repository = await trust.trusted_repository(db, auth.ci.token, site, providers)
    except CiTokenError as error:
        raise ci_error(error) from None
    trusted = _DeployAuth(
        member=None,
        ci=_CiPrincipal(token=auth.ci.token, repository=repository),
        actor=Actor(ActorKind.CI, repository.actor_identifier),
    )
    previous = await trust.follow_token(db, site, repository)
    if previous is not None:
        await _audit(
            request,
            trusted.actor,
            vocabulary.SITE_REPOSITORY_RENAME,
            vocabulary.ALLOWED,
            None,
            {
                "group": group_slug,
                "site": site.slug,
                "provider": str(repository.provider),
                "host": repository.host,
                "repository": f"{repository.owner}/{repository.repo}",
                "previous_repository": previous,
                "repository_id": repository.repository_id,
            },
        )
    return trusted


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
    summary="Publish a bundle, live or as a preview",
    response_description="The bundle was unpacked and published; the ID of the new version is returned.",
    description=(
        "The endpoint CI uses. Send the built site as `multipart/form-data` with the "
        "`file` field; without the `preview` field the bundle replaces the live site, with `preview` it ends up under "
        "`/{groupSlug}/{siteSlug}/_preview/{ref}/`.\n\n"
        "**Who can call this:** any of three callers. (1) A CI ID token from GitHub or Forgejo Actions "
        "(`Authorization: Bearer <JWT>`) whose audience is exactly Plak's admin URL, from the repository "
        "linked to this site; a live deploy must then come from a `push`, `workflow_dispatch` or "
        "`schedule`, and from the live branch if one is set; a preview may come from any branch. "
        "(2) A CLI token from `plak login` "
        "(`Authorization: Bearer plakcli_...`): it acts as the member who signed in, with exactly their "
        "roles. (3) An admin session plus CSRF header. With (2) and (3) the member must be active and have at "
        "least the `editor` role on this site. This endpoint and the preview teardown are, together with the "
        "CLI session endpoints, the creation of a group or site and the linking of a repository, the only ones that "
        "accept a Bearer token; elsewhere that header yields 401.\n\n"
        "**Flow:** authorization comes first, only then is the body read, so a refused "
        "request costs no upload. The upload streams to disk and is unpacked against the limits "
        "below. Every deploy, whether it succeeds or is refused, ends up in the audit log.\n\n"
        "**Site root:** enclosing directories are repeatedly unwrapped while the root directory contains exactly "
        "one directory and nothing else, so an archive with only `mysite/dist/index.html` simply lands on the "
        "site root. Operating system metadata does not count and is not published either: "
        "the `__MACOSX` directory, `.DS_Store` and the AppleDouble files that start with `._`. "
        "A zip you make with a right-click in Finder therefore just works. "
        "After that there must be an `index.html` in the root directory; otherwise the response is 422 `NO_INDEX`, "
        "with the index paths found in `indexCandidates`. If the site is in a directory "
        "next to other things (a zipped project directory), send that directory as the `basePath` field: this "
        "confirms one of the suggestions. Plak never picks one of several candidates itself, because it "
        "would silently leave out files you thought you were publishing. The `basePath` is relative to the "
        "root directory after unwrapping, but a path that includes the unwrapped prefix works too, and "
        "a fixed value keeps working if unwrapping has already taken that directory away.\n\n"
        "**Limits** (configurable; these are the defaults): the request body is at most 100 MB "
        "(`PLAK_INGEST_MAX_BODY`), a single unpacked file 50 MB (`PLAK_INGEST_MAX_FILE`), the entire "
        "unpacked site 200 MB (`PLAK_INGEST_MAX_TOTAL`), with at most 1000 entries "
        "(`PLAK_INGEST_MAX_FILES`) and 10 levels of directory depth (`PLAK_INGEST_MAX_DEPTH`). These apply to "
        "what is published: what falls outside the `basePath` does not count. The archive as a whole may "
        "contain at most fifty times as many entries, and for a `.tar.gz` the unpacked size of "
        "all members counts, including the unpublished ones: a tar has no index, so getting to the next "
        "header means decompressing everything in between. The limits are enforced during unpacking and "
        "the headers are checked against the same limits beforehand, so a zip or tar bomb does not get "
        "past either.\n\n"
        "**Example** (`$PLAK_TOKEN` is a CI ID token or a CLI token):\n\n"
        "```\n"
        "curl --fail --silent --show-error \\\n"
        '  --header "Authorization: Bearer $PLAK_TOKEN" \\\n'
        "  --form file=@dist.zip \\\n"
        "  --form preview=pr-42 \\\n"
        '  "$PLAK_ADMIN_URL/-/api/v1/sites/aurora/docs/deploys"\n'
        "```\n\n"
        'Response: `201` with `{"versionId": "..."}`. Omit `--form preview=...` for a live deploy.'
    ),
    responses=error_responses(
        {
            **_DEPLOY_ERRORS,
            400: "The client aborted the upload before the body was complete (`CLIENT_ABORTED`).",
            503: (
                f"{_DEPLOY_ERRORS[503]} Or the content volume has too little free space for this upload "
                "(`STORAGE_UNAVAILABLE`): checked in advance against the declared `Content-Length`, and again while "
                "receiving and unpacking, so the volume never drops below the configured margin of free "
                "space. Whatever was already written is then cleaned up; try again later."
            ),
            413: (
                "The upload is larger than the body limit (`BODY_TOO_LARGE`), or the bundle unpacks too "
                "large: `FILE_TOO_LARGE`, `TOTAL_TOO_LARGE` or `TOO_MANY_FILES` (the last one also "
                "when the archive as a whole has too many entries; the message says which of the two, "
                "and carries `indexCandidates` where possible, because a zipped project directory runs into "
                "this first)."
            ),
            422: (
                "The request is not multipart/form-data (`NOT_MULTIPART`), the `file` field is missing "
                "(`FILE_MISSING`), the multipart structure is wrong (`MULTIPART_INVALID`), `preview` is "
                "not a valid slug (`PREVIEW_REF_INVALID`), or the bundle is refused: unknown format "
                "(`UNKNOWN_FORMAT`), unreadable or empty archive (`INVALID_ARCHIVE`, `EMPTY_ARCHIVE`), "
                "an unsafe path in it (`PATH_TRAVERSAL`, `ABSOLUTE_PATH`, `SYMLINK_REFUSED`, "
                "`HARDLINK_REFUSED`, `SPECIAL_FILE`, `RESERVED_SEGMENT`, `TOO_DEEP`, `DUPLICATE_PATH`, "
                "`NULL_BYTE`, `EMPTY_PATH`), a file that does not belong on a website (`SECRET_FILE`: "
                "a `.git` directory or an `.env` file, which come along automatically with a zipped project "
                "directory), no `index.html` in the root directory (`NO_INDEX`, with the paths found "
                "in `indexCandidates`), or a `basePath` that is not valid (`BASE_PATH_INVALID`), is not a "
                "directory in the bundle (`BASE_PATH_UNKNOWN`, also when the path points to a file) or "
                "contains no `index.html` (`BASE_PATH_WITHOUT_INDEX`, also when that directory contains no "
                "files at all)."
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
            auth = await _authorize(request, db, auth, group_slug, site)
            actor = auth.actor

        service = IngestService(store, settings)
        # Before the body is read: a volume without room costs no upload. A
        # declared size above the body limit is the spool's 413 to give.
        length = request.headers.get("content-length", "")
        declared = int(length) if length.isdigit() else 0
        service.check_room(min(declared, settings.ingest_max_body))

        upload = await _spool_upload(request, store, settings.ingest_max_body, service.room_guard())
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
    except (ApiError, IngestError) as error:
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
            preview_row = (
                await db.scalar(select(Preview).where(Preview.site_id == site.id, Preview.ref == preview))
                if preview is not None
                else None
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
    url = f"{settings.content_base_url.rstrip('/')}/{quote(group.slug)}/{quote(site.slug)}/"
    if preview is not None:
        url += f"_preview/{quote(preview)}/"
    access = effective_access(site, preview_row)
    return DeployResult(
        version_id=version_id,
        url=url,
        access=AccessOut(base=access.base, keys=access.keys, invitees=access.invitees),
    )


@router.delete(
    "/sites/{group_slug}/{site_slug}/previews/{ref}",
    status_code=204,
    tags=[TAG_DEPLOYS],
    summary="Clean up a preview",
    description=(
        "Removes the preview with this ref, including its files. Meant for the CI step that runs when "
        "a pull request closes. The live site and the version history are left alone.\n\n"
        "**Who can call this:** the same as the deploy: a CI ID token from the linked repository (from any branch), "
        "a CLI token or an admin session with CSRF header of an active member with effective site role "
        "`editor` or higher.\n\n"
        "**Idempotent:** a ref that does not (any more) exist also yields 204, so a repeated cleanup step in CI "
        "does not fail on a rerun.\n\n"
        "**Example:**\n\n"
        "```\n"
        "curl --fail --silent --show-error --request DELETE \\\n"
        '  --header "Authorization: Bearer $PLAK_TOKEN" \\\n'
        '  "$PLAK_ADMIN_URL/-/api/v1/sites/aurora/docs/previews/pr-42"\n'
        "```"
    ),
    responses={204: {"description": "The preview no longer exists. No content is returned."}}
    | error_responses(
        {**_DEPLOY_ERRORS, 422: "The ref in the path is not a valid slug (`PREVIEW_REF_INVALID`)."}
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
            auth = await _authorize(request, db, auth, group_slug, site)
            actor = auth.actor
    except ApiError as error:
        await _audit(request, actor, AUDIT_ACTION_PREVIEW_TEARDOWN, "refused", error.reason, refs)
        raise

    service = IngestService(request.app.state.content_store, settings)
    try:
        async with factory() as db:
            await service.delete_preview(db, site, ref)
    except Exception:
        _logger.exception("preview teardown failed")
        await _audit_best_effort(
            request, actor, AUDIT_ACTION_PREVIEW_TEARDOWN, "refused", AUDIT_REASON_INTERNAL, refs
        )
        raise

    await _audit(request, actor, AUDIT_ACTION_PREVIEW_TEARDOWN, "allowed", None, refs)
    return Response(status_code=204)
