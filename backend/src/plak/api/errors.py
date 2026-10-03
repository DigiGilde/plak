"""Problem+json error handling for the API layer (NL API Design Rules).

Every API error is `application/problem+json` with `{type, title, status,
detail}` and the fixed status codes 401/403/404/409/413/422/429/503. The same
shape appears as `Problem` in OpenAPI; routes attach it to their `responses`
with `error_responses()`, so the docs show per endpoint which errors exist.
main.py registers `register_error_handlers(app)`; the
handlers for FastAPI's own exceptions (validation, HTTPException) only
rewrite requests under the API path prefix, so platform and content routes
keep their own response shapes (HTML, neutral 404).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, Response

from plak import i18n, messages, net
from plak.api.schema import ApiModel
from plak.audit import vocabulary
from plak.audit.log import ANONYMOUS, Actor, AuditLog
from plak.auth.sessions import session_from_request
from plak.ingest.service import IngestError
from plak.ingest.unpacker import BundleError
from plak.messages import Msg
from plak.models.audit import ActorKind

PROBLEM_CONTENT_TYPE = "application/problem+json"
API_PATH_PREFIX = "/-/api/"

WWW_AUTHENTICATE_BEARER = {"WWW-Authenticate": 'Bearer realm="plak", error="invalid_token"'}

# A 403 is a refusal whatever the method, and so is a 429 (only ApiError(429)
# raises one, from the audit lookup daily limit; the ratelimit middleware's
# 429 goes around ApiError, see docs/audit-log.md "Bekende gaten"). A 401 and
# a 404 count only on a mutation: on a read, a 401 is "not signed in" (the
# SPA asks /me on every load to choose between landing page and overview) and
# a 404 an ordinary miss, while on a mutation the 404 stands in for a 403
# that would give away whether the thing exists.
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# BundleError reason codes that mark an exceeded limit (413); every other
# bundle error is an invalid archive (422).
_LIMIT_REASONS = frozenset({"FILE_TOO_LARGE", "TOTAL_TOO_LARGE", "TOO_MANY_FILES"})

# IngestError reason codes that are not a 422: a full site quota is an
# exceeded limit like the ones above, and a volume without room is a state of
# the platform the client can retry out of.
_INGEST_STATUS = {"SITE_QUOTA_EXCEEDED": 413, "STORAGE_UNAVAILABLE": 503}

PROBLEM_SCHEMA_NAME = "Problem"
PROBLEM_SCHEMA_REF = f"#/components/schemas/{PROBLEM_SCHEMA_NAME}"

# Extension member through which a rejected bundle passes back its suggestion;
# the name is the field alias of Problem.index_candidates.
FIELD_INDEX_CANDIDATES = "indexCandidates"


class Problem(ApiModel):
    """Error message following RFC 9457 (`application/problem+json`), with `code` as an extension."""

    type: str = Field(
        default="about:blank",
        description="Error type URI. Plak always uses `about:blank`; `code` carries the machine-readable meaning.",
    )
    title: str = Field(
        description="Short, fixed description of the status code, for example 'Forbidden'.",
        examples=["Forbidden"],
    )
    status: int = Field(description="The HTTP status code, repeated in the message.", examples=[403])
    detail: str = Field(
        description="Explanation of this one occurrence, meant for a human. Never contains internal details.",
        examples=["You are not a member of this group."],
    )
    code: str | None = Field(
        default=None,
        description=(
            "Extension member (RFC 9457): stable, machine-readable error code such as `NOT_GROUP_MEMBER` or "
            "`SLUG_EXISTS`. Absent for errors that FastAPI handles itself, such as a schema validation."
        ),
        examples=["NOT_GROUP_MEMBER"],
    )
    index_candidates: list[str] | None = Field(
        default=None,
        description=(
            "Extension member (RFC 9457): with `NO_INDEX`, `BASE_PATH_WITHOUT_INDEX`, `BASE_PATH_UNKNOWN` and "
            "`TOO_MANY_FILES`, the "
            "`index.html` paths found in the bundle, shortest first and at most five. The paths "
            "are relative to the root directory after unwrapping, so the directory of such a path is exactly the "
            "value that can be sent back as the `basePath` form field. If a path contains no `/`, that "
            "`index.html` is already in the root directory: there is no directory to send back, and the fix is "
            "to omit `basePath`."
        ),
        examples=[["dist/index.html", "docs/site/index.html"]],
    )


# Every error code is SCREAMING_SNAKE with at least one underscore; requiring
# that underscore keeps bare words like `POST` or `RFC` out of the match.
_CODE_RE = re.compile(r"`([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)`")

# The OpenAPI document is generated in English, the language a client that
# asks for nothing gets; api/openapi_i18n.py swaps these examples for Dutch.
_DOCS_LOCALE = i18n.API_DEFAULT
_EXAMPLE_STATUSES = frozenset({401, 403, 404, 409, 413, 422, 429})


def _example_detail(status: int) -> str:
    if status in _EXAMPLE_STATUSES:
        return messages.render(_DOCS_LOCALE, Msg(f"example.{status}"))
    return messages.render(_DOCS_LOCALE, Msg("example.other"))


def error_example(status: int, description_: str) -> dict[str, Any]:
    """An example body that belongs to this one status code.

    Without it Swagger UI derives the example from the field examples of
    `Problem` and shows the same 403 body for every status code. The `code`
    comes out of the description itself, so example and text cannot drift
    apart.
    """
    example: dict[str, Any] = {
        "type": "about:blank",
        "title": messages.title(_DOCS_LOCALE, status),
        "status": status,
        "detail": _example_detail(status),
    }
    codes = _CODE_RE.findall(description_)
    if codes:
        example["code"] = codes[0]
    return example


def error_responses(descriptions: dict[int, str]) -> dict[int | str, dict[str, Any]]:
    """Builds the OpenAPI `responses` for a route's problem+json errors.

    The schema sits in the response as a `$ref`; `register_openapi` in
    api/docs.py adds the component, because no route declares `Problem` as
    its response_model.
    """
    return {
        status: {
            "description": f"{messages.title(_DOCS_LOCALE, status)}. {description_}",
            "content": {
                PROBLEM_CONTENT_TYPE: {
                    "schema": {"$ref": PROBLEM_SCHEMA_REF},
                    "example": error_example(status, description_),
                }
            },
        }
        for status, description_ in sorted(descriptions.items())
    }


class ApiError(Exception):
    """API error that the handlers turn into problem+json.

    It names a message key rather than a text: `reason` (the code the client
    branches on) is what stands before the dot in that key, and the wording
    is settled by the handler, in the language the request asked for. See
    plak/messages.py.
    """

    def __init__(
        self,
        status: int,
        key: str,
        *,
        params: Mapping[str, object] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.message = Msg(key, dict(params or {}))
        self.reason = messages.code_of(key)
        self.headers = headers or {}
        # English in the exception itself, so a traceback or a log line reads
        # as the developer-facing half of the same message.
        super().__init__(messages.render(i18n.API_DEFAULT, self.message))


def locale_from_request(request: Request) -> str:
    return locale_from_header(request.headers.get("accept-language"))


def locale_from_header(accept_language: str | None) -> str:
    """The language an API answer is written in: English unless the client
    asks for something else."""
    return i18n.negotiate(accept_language, default=i18n.API_DEFAULT)


def problem_response(
    status: int,
    detail: str,
    *,
    locale: str = i18n.API_DEFAULT,
    code: str | None = None,
    headers: dict[str, str] | None = None,
    extra: dict[str, object] | None = None,
) -> JSONResponse:
    content: dict[str, object] = {
        "type": "about:blank",
        "title": messages.title(locale, status),
        "status": status,
        "detail": detail,
    }
    if code is not None:
        # Extension member (RFC 9457): stable machine-readable error code.
        content["code"] = code
    content.update(extra or {})
    return JSONResponse(
        status_code=status,
        media_type=PROBLEM_CONTENT_TYPE,
        content=content,
        headers=headers,
    )


def _is_api_path(request: Request) -> bool:
    return request.url.path.startswith(API_PATH_PREFIX)


def _is_audited_refusal(request: Request, status: int) -> bool:
    if not _is_api_path(request):
        return False
    if status in (403, 429):
        return True
    return status in (401, 404) and request.method in _MUTATING_METHODS


async def _audit_refusal(request: Request, error: ApiError) -> None:
    """One place for every refused admin action, including endpoints that do
    not exist yet (BIO2 5.18.01).

    Only the route template goes into refs, never the path: a path parameter
    can be an e-mail address, and the audit log keeps no readable identifiers.
    The response itself is untouched, so the neutral 404 stays byte-identical,
    and both a missing and a forbidden thing cost exactly one row, so timing
    tells them apart no better than the body does.
    """
    log: AuditLog | None = getattr(request.app.state, "audit_log", None)
    if log is None or getattr(request.state, "audit_written", False):
        return
    if not _is_audited_refusal(request, error.status):
        return
    # A route that authenticated its member some other way than the session
    # cookie (a CLI token, api/admin.py:require_creator) leaves it here.
    actor = getattr(request.state, "audit_actor", None)
    if actor is None:
        session = session_from_request(request)
        actor = Actor(ActorKind.MEMBER, session.sub) if session is not None else ANONYMOUS
    await log.write(
        vocabulary.ADMIN_ACCESS,
        actor,
        vocabulary.REFUSED,
        reason_code=error.reason,
        refs={
            "method": request.method,
            "route": getattr(request.scope.get("route"), "path", None),
            "status": error.status,
            **getattr(request.state, "audit_refs", {}),
        },
        ip=net.client_ip_from_request(request),
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, error: ApiError) -> JSONResponse:
        await _audit_refusal(request, error)
        locale = locale_from_request(request)
        return problem_response(
            error.status,
            messages.render(locale, error.message),
            locale=locale,
            code=error.reason,
            headers=error.headers,
        )

    @app.exception_handler(BundleError)
    async def _bundle_error(request: Request, error: BundleError) -> JSONResponse:
        status = 413 if error.reason in _LIMIT_REASONS else 422
        extra = {FIELD_INDEX_CANDIDATES: list(error.index_candidates)} if error.index_candidates else None
        locale = locale_from_request(request)
        return problem_response(
            status,
            messages.render(locale, error.message),
            locale=locale,
            code=error.reason,
            extra=extra,
        )

    @app.exception_handler(IngestError)
    async def _ingest_error(request: Request, error: IngestError) -> JSONResponse:
        locale = locale_from_request(request)
        return problem_response(
            _INGEST_STATUS.get(error.reason, 422),
            messages.render(locale, error.message),
            locale=locale,
            code=error.reason,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, error: RequestValidationError) -> Response:
        if not _is_api_path(request):
            return await request_validation_exception_handler(request, error)
        fields = ", ".join(
            ".".join(str(part) for part in part_.get("loc", ()) if part != "body")
            for part_ in error.errors()
        )
        # No code: this is FastAPI's own schema validation, which has none of
        # its own (see the `code` field of Problem).
        message = Msg("INVALID_INPUT", {"fields": fields}) if fields else Msg("INVALID_INPUT.request")
        locale = locale_from_request(request)
        return problem_response(422, messages.render(locale, message), locale=locale)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, error: StarletteHTTPException) -> Response:
        if not _is_api_path(request):
            return await http_exception_handler(request, error)
        return problem_response(
            error.status_code,
            str(error.detail),
            locale=locale_from_request(request),
            headers=error.headers,
        )
