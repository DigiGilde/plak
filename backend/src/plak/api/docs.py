"""API documentation: OpenAPI schema and self-hosted docs UI.

`/-/api/docs` is publicly readable and refers only to our own assets
(`/-/api/docs/assets/...`); no CDN scripts or stylesheets, so the page
keeps working under the admin CSP. The assets are plain files under
`static/docs/` and are served here from an explicit allowlist of file
names (no open path traversal through StaticFiles needed for this handful of
fixed files).

This module also supplies the descriptive layer of the schema itself: the API
description and the tag order that main.py passes to `FastAPI(...)`, plus
`register_openapi(app)` for the shared `Problem` error schema.

Both the page and the schema come in English and Dutch (api/openapi_i18n.py).
The language is, in this order: `?lang=` (the switch on the page), the
language a signed-in member set on their profile, `Accept-Language`, and
otherwise English. The schema settles on its language the same way as the
page around it, so only a `?lang=` on the page is passed on to the schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from sqlalchemy import select
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from plak import i18n, messages
from plak.api import openapi_i18n
from plak.api.errors import (
    PROBLEM_CONTENT_TYPE,
    PROBLEM_SCHEMA_NAME,
    PROBLEM_SCHEMA_REF,
    ApiError,
    Problem,
    locale_from_request,
)
from plak.api.schema import ApiModel
from plak.auth.sessions import CSRF_HEADER, SESSION_COOKIE, session_from_request
from plak.models.identity import Member, MemberStatus

STATIC_DOCS_DIR = Path(__file__).resolve().parent.parent / "static" / "docs"

TAG_SESSION = "Session"
TAG_OVERVIEW = "Overview"
TAG_GROUPS = "Groups"
TAG_GROUP_MEMBERS = "Group members"
TAG_SITES = "Sites"
TAG_SITE_MEMBERS = "Site members"
TAG_DEPLOYS = "Deploys"
TAG_VERSIONS = "Versions"
TAG_PREVIEWS = "Previews"
TAG_INVITEES = "Invitees"
TAG_KEYS = "Secret links"
TAG_AUDIT = "Audit log"
TAG_CI = "Publishing from CI"
TAG_CLI = "CLI login"
TAG_PLATFORM = "Platform administration"

API_DESCRIPTION = """
Plak publishes static sites per group and site, with a preview for every pull request.
This API serves two kinds of client: the admin SPA on the admin host, and the CI that runs deploys.

## Authentication

There are two ways to identify yourself, and every endpoint accepts exactly one or two of them:

* **Admin session** - a `Secure`/`HttpOnly`/`SameSite=Strict` cookie you get after SSO sign-in on the admin
  host. Every session route also requires an **active** member: a new or deactivated member gets 403.
  Mutations (POST, PUT, DELETE) additionally require the CSRF double-submit header `X-CSRF-Token`, with the
  same value as the CSRF cookie.
* **Bearer token** - `Authorization: Bearer <token>`, only on the two deploy endpoints, on
  `DELETE /cli/session` and `GET /cli/whoami`, and, with a CLI token only, on creating a group or a site
  and on linking a repository. The token is one of two kinds:
  * a **CI ID token** (JWT) from GitHub Actions or Forgejo Actions, whose audience is exactly the admin
    URL of Plak (`PLAK_BASE_URL`). It is valid only for the sites the repository of that workflow is linked
    to (`PUT /sites/{groupSlug}/{siteSlug}/repository`); there is no secret to keep;
  * a **CLI token** `plakcli_...` from `plak login` (see the CLI login endpoints). It acts as the member who
    signed in, with exactly their roles.

  A Bearer header on any other endpoint yields 401, even when the token is valid.

All endpoints live on the admin origin and guard where a request comes from: an `Origin` or
`Sec-Fetch-Site` that does not belong to the admin origin yields 403. CI and the CLI send neither header
and so pass that guard unhindered.

## Authorization

Who may do what depends on the group: **group members** manage the sites of their own group according to
their role, and any active member may create a group. A **platform administrator** also activates and
deactivates members and reads the audit log. With a CI ID token what counts is the repository linked to the
site, and for a live deploy the live branch.

## Errors

Errors are `application/problem+json` following RFC 9457: `{type, title, status, detail}`, extended with
the extension member `code` holding a stable, machine-readable error code (`NOT_GROUP_MEMBER`, `SLUG_EXISTS`,
`BODY_TOO_LARGE`, ...). Each endpoint below lists the status codes it can return. Codes are the contract
for clients; the `detail` text is meant for people and may change.

## Language

This document and the `title` and `detail` of every error come in English and in Dutch. Send
`Accept-Language: nl` for Dutch; a client that asks for nothing gets English. For this document `?lang=nl`
or `?lang=en` overrides the header.

## Conventions

Fields on the wire are lowerCamelCase. Timestamps are RFC 3339 in UTC with a `Z` suffix
(`2026-09-12T09:30:00Z`). The full version of this API is in the `API-Version` header of every response;
the major version is in the path (`/-/api/v1`). Requests are rate limited: exceeding the limit yields 429.
"""

OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": TAG_SESSION, "description": "Who am I, and where does my content live."},
    {"name": TAG_OVERVIEW, "description": "Home screen of the SPA: your own groups with their sites."},
    {
        "name": TAG_GROUPS,
        "description": "Create, view and delete groups, and set their default access.",
    },
    {"name": TAG_GROUP_MEMBERS, "description": "Who may manage the sites of a group."},
    {"name": TAG_SITES, "description": "Sites within a group, including who may view them."},
    {
        "name": TAG_SITE_MEMBERS,
        "description": (
            "Who may manage this one site: the group members who reach it through the group, plus the "
            "site roles granted on top of that."
        ),
    },
    {
        "name": TAG_DEPLOYS,
        "description": "Publishing from CI or from the SPA: live deploy, preview deploy and teardown.",
    },
    {"name": TAG_VERSIONS, "description": "The deploy history of a site, and rolling back to an earlier version."},
    {"name": TAG_PREVIEWS, "description": "Previews per pull request and their own access."},
    {
        "name": TAG_INVITEES,
        "description": "Individual addresses that may view a site with the 'invitees' exception.",
    },
    {"name": TAG_KEYS, "description": "Secret links through which a site can be viewed without signing in."},
    {
        "name": TAG_CI,
        "description": (
            "Linking the repository from which CI may publish to a site with an OIDC ID token "
            "(GitHub or Forgejo Actions), without a secret."
        ),
    },
    {
        "name": TAG_CLI,
        "description": (
            "Signing in with the CLI (`plak login`) through the device flow: a code in the terminal, approval "
            "in the admin interface, and then tokens the CLI refreshes by itself."
        ),
    },
    {
        "name": TAG_PLATFORM,
        "description": "Platform administration: activating or deactivating members. Platform administrators only.",
    },
    {
        "name": TAG_AUDIT,
        "description": (
            "Reading the audit log: who did what, when, and with what outcome. Platform administrators "
            "only."
        ),
    },
]

# Swagger UI 5.32.15, verbatim from the npm package swagger-ui-dist; refresh
# with `just ververs-swagger-ui`. Served ourselves because the admin CSP
# allows no CDN.
SWAGGER_UI_VERSION = "5.32.15"

# The requested filename only picks an entry; the path on disk comes from
# this table, never from the request.
_ASSETS: dict[str, tuple[Path, str]] = {
    name: (STATIC_DOCS_DIR / name, media_type)
    for name, media_type in (
        ("docs.css", "text/css"),
        ("docs-init.js", "text/javascript"),
        ("docs-theme.js", "text/javascript"),
        ("swagger-ui-bundle.js", "text/javascript"),
        ("swagger-ui.css", "text/css"),
    )
}

# Swagger UI sets its own `style` attributes on elements; with just
# `style-src 'self'` the page stays blank. The relaxation applies here only:
# script-src stays 'self' (the init lives in docs-init.js, not inline) and the
# page shows no user-supplied content.
DOCS_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
    "object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)

# no-cache, not no-store: FileResponse sets ETag and Last-Modified, so a
# repeat visit gets a 304 instead of 1.5 MB of bundle again. With a real
# max-age a new docs-init.js would stay invisible for days.
_CACHE_ASSET = "no-cache"

router = APIRouter()

_DOCS_HTML = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="/-/api/docs/assets/swagger-ui.css">
<link rel="stylesheet" href="/-/api/docs/assets/docs.css">
<!-- No defer, and before the body: this sets the dark class before anything
     is painted, otherwise the page flashes white first. -->
<script src="/-/api/docs/assets/docs-theme.js"></script>
</head>
<body>
<header class="docs-header">
<span class="docs-brand"><span class="docs-brand__block">P</span>lak</span>
<span class="docs-header__divider"></span>
<a href="/">{back}</a>
<a href="/-/api/openapi.json{schema_query}">{schema}</a>
<a href="/-/api/docs?lang={other}" lang="{other}" hreflang="{other}">{switch}</a>
</header>
<div id="swagger-ui"></div>
<script src="/-/api/docs/assets/swagger-ui-bundle.js"></script>
<script src="/-/api/docs/assets/docs-init.js"></script>
</body>
</html>
"""

# Both answers depend on the language header and on the session cookie.
_VARY = "Accept-Language, Cookie"


async def _member_language(request: Request) -> str | None:
    session = session_from_request(request)
    if session is None:
        return None
    async with request.app.state.session_factory() as db:
        language = await db.scalar(
            select(Member.language).where(
                Member.sso_subject == session.sub, Member.status == MemberStatus.ACTIVE
            )
        )
    return language.value if language is not None else None


async def docs_locale(request: Request) -> str:
    """The language of the docs page and the schema; see the module docstring."""
    asked = request.query_params.get("lang")
    if asked in i18n.SUPPORTED:
        return asked
    return await _member_language(request) or locale_from_request(request)


@router.get("/-/api/docs", include_in_schema=False)
async def docs_ui(request: Request) -> HTMLResponse:
    locale = await docs_locale(request)
    other = next(language for language in i18n.SUPPORTED if language != locale)
    asked = request.query_params.get("lang") in i18n.SUPPORTED
    page = _DOCS_HTML.format(
        lang=locale,
        schema_query=f"?lang={locale}" if asked else "",
        other=other,
        title=i18n.t(locale, "docs.title"),
        back=i18n.t(locale, "docs.back"),
        schema=i18n.t(locale, "docs.schema"),
        switch=i18n.t(other, "docs.language"),
    )
    return HTMLResponse(
        page,
        headers={"Content-Security-Policy": DOCS_CSP, "Content-Language": locale, "Vary": _VARY},
    )


@router.get("/-/api/docs/assets/{filename}", include_in_schema=False)
async def docs_asset(filename: str) -> Response:
    asset = _ASSETS.get(filename)
    if asset is None:
        raise ApiError(404, "UNKNOWN_ASSET")
    path, media_type = asset
    if not path.is_file():
        raise ApiError(404, "UNKNOWN_ASSET")
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": _CACHE_ASSET})


@router.get("/-/api/openapi.json", include_in_schema=False)
async def openapi_schema(request: Request) -> JSONResponse:
    locale = await docs_locale(request)
    return JSONResponse(
        localised_openapi(request.app, locale), headers={"Content-Language": locale, "Vary": _VARY}
    )


def localised_openapi(app: FastAPI, locale: str) -> dict[str, Any]:
    """The schema in `locale`, translated once per language and then kept,
    like FastAPI keeps the English one in `app.openapi_schema`."""
    cache: dict[str, dict[str, Any]] = app.state.localised_openapi
    if locale not in cache:
        cache[locale] = openapi_i18n.localise(app.openapi(), locale)
    return cache[locale]


SECURITY_SESSION = "adminSession"
SECURITY_BEARER = "bearerToken"

_SECURITY_SCHEMES: dict[str, dict[str, Any]] = {
    SECURITY_SESSION: {
        "type": "apiKey",
        "in": "cookie",
        "name": SESSION_COOKIE,
        "description": (
            "The admin session you get after SSO sign-in on the admin host. The browser sends the cookie by "
            "itself, so 'Try it out' works here once you are signed in; the Authorize button is not needed. "
            f"Mutations also require the `{CSRF_HEADER}` header with the value of the CSRF cookie, which this "
            "page sends along automatically."
        ),
    },
    SECURITY_BEARER: {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "A CI ID token (JWT from GitHub or Forgejo Actions, with the admin URL as audience) or a CLI token "
            "`plakcli_...` from `plak login`. Paste the whole token under Authorize. Only the deploy endpoints "
            "and the CLI session endpoints accept it, and, with a CLI token only, creating a group, creating a "
            "site and linking a repository; anywhere else a Bearer header yields 401, even when the token is "
            "valid."
        ),
    },
}

# The endpoints that accept a bearer token besides the session, recognised by
# the tail of their path template (see deploys.accepts_bearer). The CLI
# endpoints declare their own security in api/cli.py.
_TOKEN_ENDPOINTS = (
    ("post", "/deploys"),
    ("delete", "/previews/{ref}"),
    ("post", "/-/api/v1/groups"),
    ("post", "/groups/{group_slug}/sites"),
    ("put", "/repository"),
)


def _set_security(schema: dict[str, Any]) -> None:
    """Records per operation which authentication it accepts.

    Without this Swagger UI shows no Authorize button, and no endpoint states
    whether it wants a session or a token. The list is an OR: an operation
    listing both accepts either of the two.
    """
    for path, path_part in schema.get("paths", {}).items():
        for method_, operation in path_part.items():
            if not isinstance(operation, dict) or "security" in operation:
                continue
            with_token = any(method_ == m and path.endswith(end) for m, end in _TOKEN_ENDPOINTS)
            operation["security"] = (
                [{SECURITY_BEARER: []}, {SECURITY_SESSION: []}] if with_token else [{SECURITY_SESSION: []}]
            )


_VALIDATION_RESPONSE: dict[str, Any] = {
    "description": (
        "Unprocessable input. A field in the path or in the body does not have the shape the schema "
        "prescribes; `detail` names the fields."
    ),
    "content": {
        PROBLEM_CONTENT_TYPE: {
            "schema": {"$ref": PROBLEM_SCHEMA_REF},
            "example": {
                "type": "about:blank",
                "title": messages.title(i18n.API_DEFAULT, 422),
                "status": 422,
                "detail": "slug: String should match pattern '^[a-z0-9-]+$'",
            },
        }
    },
}

_FASTAPI_VALIDATION_SCHEMAS = ("HTTPValidationError", "ValidationError")

# Path parameters come in only a handful of shapes in this API and mean the
# same thing everywhere; describing them once here saves a Path(...) annotation
# on every route handler.
PATH_PARAMETERS = {
    "group_slug": "Slug of the group; the first path segment of every site URL.",
    "site_slug": "Slug of the site within that group.",
    "ref": "Name of the preview, usually the pull request number or the branch name as a slug.",
    "selector": "The non-secret first part of a key value, before the dot.",
    "session_id": "ID of the CLI session, from `GET /me/cli-sessions`.",
    "invitee_id": "ID of the invitee, from the invitee list of the site.",
    "member_id": "ID of the platform member.",
    "version_id": "ID of the version.",
}


def _describe_path_parameters(schema: dict[str, Any]) -> None:
    for path_part in schema.get("paths", {}).values():
        for operation in path_part.values():
            if not isinstance(operation, dict):
                continue
            for parameter in operation.get("parameters", []):
                if parameter.get("description"):
                    continue
                hint_text = PATH_PARAMETERS.get(parameter.get("name", ""))
                if hint_text:
                    parameter["description"] = hint_text


def _fix_validation_errors(schema: dict[str, Any]) -> None:
    """Replaces FastAPI's own 422 with the problem+json contract.

    FastAPI attaches a 422 with `HTTPValidationError` under
    `application/json` to every route with parameters, but the validation
    handler in api/errors.py turns every validation error under
    `/-/api/` into problem+json. Without this correction the docs would
    promise a response shape the app never sends.
    """
    for path_part in schema.get("paths", {}).values():
        for operation in path_part.values():
            if not isinstance(operation, dict):
                continue
            responses_ = operation.get("responses", {})
            if "application/json" in responses_.get("422", {}).get("content", {}):
                responses_["422"] = dict(_VALIDATION_RESPONSE)


def _submodels(base: type[ApiModel]) -> list[type[ApiModel]]:
    direct = base.__subclasses__()
    return direct + [small for kind in direct for small in _submodels(kind)]


def _restore_examples(schema: dict[str, Any]) -> None:
    """Puts back null values that dropped out of the examples.

    FastAPI runs the generated schema through `jsonable_encoder(...,
    exclude_none=True)`, so an example like `{"access": null}` ends up
    in the docs as `{}`: a body the API would in fact reject. The models hold
    on to the intended examples themselves.
    """
    schemas = schema.get("components", {}).get("schemas", {})
    for model in _submodels(ApiModel):
        extra = model.model_config.get("json_schema_extra")
        if not isinstance(extra, dict) or "examples" not in extra:
            continue
        target = schemas.get(model.__name__)
        if target is not None:
            target["examples"] = extra["examples"]


def register_openapi(app: FastAPI) -> None:
    """Adds the shared error contract to the generated schema.

    `Problem` lands in components here because no route declares it as its
    `response_model`: that would make FastAPI hang the error schema under
    `application/json` instead of `application/problem+json`, so the routes
    point at it with a `$ref` instead. FastAPI caches the schema in
    `app.openapi_schema`, so this is one-time work.
    """
    original = app.openapi

    def openapi() -> dict[str, Any]:
        schema = original()
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        schemas.setdefault(PROBLEM_SCHEMA_NAME, Problem.model_json_schema())
        schema["components"].setdefault("securitySchemes", _SECURITY_SCHEMES)
        _set_security(schema)
        _restore_examples(schema)
        _fix_validation_errors(schema)
        _describe_path_parameters(schema)
        for name in _FASTAPI_VALIDATION_SCHEMAS:
            schemas.pop(name, None)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
    app.state.localised_openapi = {}
