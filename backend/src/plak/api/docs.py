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
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from plak.api.errors import (
    PROBLEM_CONTENT_TYPE,
    PROBLEM_SCHEMA_NAME,
    PROBLEM_SCHEMA_REF,
    ApiError,
    Problem,
)
from plak.api.schema import ApiModel
from plak.auth.sessions import CSRF_HEADER, SESSION_COOKIE

STATIC_DOCS_DIR = Path(__file__).resolve().parent.parent / "static" / "docs"

TAG_SESSION = "Sessie"
TAG_OVERVIEW = "Overzicht"
TAG_GROUPS = "Groepen"
TAG_GROUP_MEMBERS = "Groepsleden"
TAG_SITES = "Sites"
TAG_SITE_MEMBERS = "Siteleden"
TAG_DEPLOYS = "Deploys"
TAG_VERSIONS = "Versies"
TAG_PREVIEWS = "Previews"
TAG_INVITEES = "Genodigden"
TAG_KEYS = "Geheime links"
TAG_AUDIT = "Auditlog"
TAG_CI = "Publiceren vanuit CI"
TAG_CLI = "CLI-login"
TAG_PLATFORM = "Platformbeheer"

API_DESCRIPTION = """
Plak publiceert statische sites per groep en site, met previews per pull request.
Deze API bedient twee soorten clients: de beheer-SPA op de beheer-host en de CI die deploys uitvoert.

## Authenticatie

Er zijn twee manieren om je te identificeren, en elk endpoint accepteert er precies één of twee van:

* **Beheersessie** - een `Secure`/`HttpOnly`/`SameSite=Strict`-cookie die je na SSO-login op de beheer-host
  krijgt. Elke sessieroute eist daarnaast een **actief** lid: een nieuw of gedeactiveerd lid krijgt 403.
  Mutaties (POST, PUT, DELETE) eisen bovendien de CSRF-double-submit-header `X-CSRF-Token`, met dezelfde
  waarde als het CSRF-cookie.
* **Bearer-token** - `Authorization: Bearer <token>`, uitsluitend op de twee deploy-endpoints en op
  `DELETE /cli/session` en `GET /cli/whoami`. Het token is een van twee soorten:
  * een **CI-ID-token** (JWT) van GitHub Actions of Forgejo Actions, met als audience precies de
    beheer-URL van Plak (`PLAK_BASE_URL`). Het geldt alleen voor de sites waaraan de repository van die
    workflow gekoppeld is (`PUT /sites/{groupSlug}/{siteSlug}/repository`); er is geen geheim om te bewaren;
  * een **CLI-token** `plakcli_...` uit `plak login` (zie de CLI-login-endpoints). Dat handelt als het lid
    dat inlogde, met precies diens rollen.

  Een Bearer-header op elk ander endpoint levert 401, ook als het token geldig is.

Alle endpoints staan op de beheer-origin en bewaken de herkomst van het verzoek: een `Origin` of
`Sec-Fetch-Site` die niet bij de beheer-origin hoort levert 403. CI en de CLI sturen geen van beide
headers en passeren die bewaking dus ongehinderd.

## Autorisatie

Wie wat mag, hangt af van de groep: **groepsleden** beheren de sites van hun eigen groep, een
**platformbeheerder** mag daarnaast groepen aanmaken of verwijderen en leden activeren. Bij een
CI-ID-token telt de gekoppelde repository van de site, en voor een live-deploy de live-branch.

## Fouten

Fouten zijn `application/problem+json` volgens RFC 9457: `{type, title, status, detail}`, aangevuld met
het extensielid `code` met een stabiele, machineleesbare foutcode (`NOT_GROUP_MEMBER`, `SLUG_EXISTS`,
`BODY_TOO_LARGE`, ...). Per endpoint staat hieronder welke statuscodes kunnen voorkomen. Codes zijn het
contract voor clients; de `detail`-tekst is voor mensen en kan wijzigen.

## Conventies

Velden op de draad zijn lowerCamelCase. Tijdstippen zijn RFC 3339 in UTC met een `Z`-achtervoegsel
(`2026-09-12T09:30:00Z`). De volledige versie van deze API staat op elk antwoord in de `API-Version`-header;
de majorversie staat in het pad (`/-/api/v1`). Verzoeken zijn ratelimited: bij overschrijding volgt 429.
"""

OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": TAG_SESSION, "description": "Wie ben ik, en waar staat mijn content."},
    {"name": TAG_OVERVIEW, "description": "Startscherm van de SPA: de eigen groepen met hun sites."},
    {
        "name": TAG_GROUPS,
        "description": "Groepen aanmaken, bekijken, verwijderen en hun standaardtoegang zetten.",
    },
    {"name": TAG_GROUP_MEMBERS, "description": "Wie mag de sites van een groep beheren."},
    {"name": TAG_SITES, "description": "Sites binnen een groep, inclusief wie ze mag bekijken."},
    {
        "name": TAG_SITE_MEMBERS,
        "description": (
            "Wie deze ene site mag beheren: de groepsleden die er via de groep bij kunnen, plus de "
            "siterollen die daar bovenop gegeven zijn."
        ),
    },
    {
        "name": TAG_DEPLOYS,
        "description": "Publiceren vanuit CI of vanuit de SPA: live-deploy, preview-deploy en teardown.",
    },
    {"name": TAG_VERSIONS, "description": "De deployhistorie van een site en terugrollen naar een eerdere versie."},
    {"name": TAG_PREVIEWS, "description": "Previews per pull request en hun afwijkende toegang."},
    {
        "name": TAG_INVITEES,
        "description": "Individuele adressen die een site met de uitzondering 'genodigden' mogen zien.",
    },
    {"name": TAG_KEYS, "description": "Geheime links waarmee een site zonder inloggen te zien is."},
    {
        "name": TAG_CI,
        "description": (
            "De repository koppelen waaruit CI met een OIDC-ID-token naar een site mag publiceren "
            "(GitHub of Forgejo Actions), zonder geheim."
        ),
    },
    {
        "name": TAG_CLI,
        "description": (
            "Inloggen met de CLI (`plak login`) via de device-flow: een code in de terminal, goedkeuren in "
            "het beheer, en daarna tokens die de CLI zelf ververst."
        ),
    },
    {
        "name": TAG_PLATFORM,
        "description": "Platformbeheer: leden activeren of deactiveren. Alleen voor platformbeheerders.",
    },
    {
        "name": TAG_AUDIT,
        "description": (
            "Het auditlog teruglezen: wie deed wat, wanneer en met welke uitkomst. Alleen voor "
            "platformbeheerders."
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
<html lang="nl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Plak API-documentatie</title>
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
<a href="/">Naar het beheer</a>
<a href="/-/api/openapi.json">OpenAPI-schema</a>
</header>
<div id="swagger-ui"></div>
<script src="/-/api/docs/assets/swagger-ui-bundle.js"></script>
<script src="/-/api/docs/assets/docs-init.js"></script>
</body>
</html>
"""


@router.get("/-/api/docs", include_in_schema=False)
async def docs_ui() -> HTMLResponse:
    return HTMLResponse(_DOCS_HTML, headers={"Content-Security-Policy": DOCS_CSP})


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
    return JSONResponse(request.app.openapi())


SECURITY_SESSION = "adminSession"
SECURITY_BEARER = "bearerToken"

_SECURITY_SCHEMES: dict[str, dict[str, Any]] = {
    SECURITY_SESSION: {
        "type": "apiKey",
        "in": "cookie",
        "name": SESSION_COOKIE,
        "description": (
            "De beheersessie die je na SSO-login op de beheer-host krijgt. De browser stuurt het cookie zelf mee, "
            "dus 'Try it out' werkt hier zodra je bent ingelogd; de knop Authorize hoeft niet. Mutaties "
            f"eisen daarnaast de header `{CSRF_HEADER}` met de waarde van het CSRF-cookie, die deze pagina "
            "automatisch meestuurt."
        ),
    },
    SECURITY_BEARER: {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "Een CI-ID-token (JWT van GitHub of Forgejo Actions, audience de beheer-URL) of een CLI-token "
            "`plakcli_...` uit `plak login`. Plak het hele token achter Authorize. Alleen de deploy-endpoints, "
            "de CLI-sessie-endpoints, het aanmaken van een groep of site en het koppelen van een repository "
            "(die laatste drie alleen met het CLI-token) accepteren het; elders levert een Bearer-header 401, "
            "ook als het token geldig is."
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
        "Onverwerkbare invoer. Een veld in het pad of in het lichaam heeft niet de vorm die het schema "
        "voorschrijft; `detail` noemt de velden."
    ),
    "content": {
        PROBLEM_CONTENT_TYPE: {
            "schema": {"$ref": PROBLEM_SCHEMA_REF},
            "example": {
                "type": "about:blank",
                "title": "Onverwerkbare invoer",
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
    "group_slug": "Slug van de groep; het eerste padsegment van elke site-URL.",
    "site_slug": "Slug van de site binnen die groep.",
    "ref": "Naam van de preview, meestal het pull-requestnummer als slug.",
    "selector": "Het niet-geheime eerste deel van een sleutelwaarde, voor de punt.",
    "session_id": "Id van de CLI-sessie, uit `GET /me/cli-sessions`.",
    "invitee_id": "Id van de genodigde, uit de genodigdenlijst van de site.",
    "member_id": "Id van het platformlid.",
    "version_id": "Id van de versie.",
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
