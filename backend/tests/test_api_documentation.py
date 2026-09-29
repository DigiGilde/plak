"""Tests for the generated OpenAPI schema (spec §8, NL API Design Rules).

These tests guard that the API keeps documenting itself: every endpoint keeps
a response model, a summary, a description and a tag, every field keeps a
description, and every error stays problem+json with the shared `Problem`
schema. A new route returning `-> dict` without a `summary` falls over here.

No database is needed for it: `create_app()` builds the routes without
connecting, so `app.openapi()` works without a container.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from plak import messages
from plak.api.deploys import accepts_bearer
from plak.api.docs import (
    _ASSETS,
    PATH_PARAMETERS,
    SECURITY_BEARER,
    SECURITY_SESSION,
    STATIC_DOCS_DIR,
    _describe_path_parameters,
    _fix_validation_errors,
    _restore_examples,
    _set_security,
    docs_asset,
)
from plak.api.errors import (
    _CODE_RE,
    FIELD_INDEX_CANDIDATES,
    PROBLEM_CONTENT_TYPE,
    PROBLEM_SCHEMA_NAME,
    PROBLEM_SCHEMA_REF,
    ApiError,
    error_example,
)
from plak.api.schema import ApiModel
from plak.auth.sessions import SESSION_COOKIE
from plak.config import Settings
from plak.main import create_app

# Status codes where HTTP forbids a body: precisely there no schema belongs.
WITHOUT_BODY = {"204", "304"}


@pytest.fixture(scope="module")
def app(tmp_path_factory) -> FastAPI:
    settings = Settings(
        db_url="postgresql+asyncpg://plak:plak@localhost:5432/plak",
        content_root=tmp_path_factory.mktemp("content"),
        oidc_issuer="https://idp.example",
        oidc_client_id="plak-client",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr:hoog",
        session_secret="sessie-geheim-van-minstens-32-bytes!",
        audit_pepper="audit-pepper-van-minstens-32-bytes!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        content_base_url="https://plak.example",
        environment="dev",
        base_url="https://beheer.plak.example",
    )
    return create_app(settings)


@pytest.fixture(scope="module")
def schema(app: FastAPI) -> dict[str, Any]:
    return app.openapi()


def _routes(app: FastAPI) -> list[APIRoute]:
    return [
        route for route in app.routes if isinstance(route, APIRoute) and route.include_in_schema
    ]


def _is_api_model(annotation: Any) -> bool:
    """Whether this is a pydantic model on the ApiModel base, possibly in a list.

    A bare `dict` is not: exactly the declaration that yields an empty schema
    and that this test has to block.
    """
    if get_origin(annotation) in (list, tuple, set):
        arguments = get_args(annotation)
        return bool(arguments) and _is_api_model(arguments[0])
    return isinstance(annotation, type) and issubclass(annotation, ApiModel)


def _operations(schema: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method_, path, operation)
        for path, path_part in schema["paths"].items()
        for method_, operation in path_part.items()
        if isinstance(operation, dict)
    ]


class TestEveryEndpointIsDescribed:
    def test_there_are_endpoints(self, schema) -> None:
        assert len(_operations(schema)) >= 20

    def test_every_route_declares_itself_a_summary(self, app) -> None:
        """On the route, not on the schema: otherwise FastAPI invents a
        `summary` from the function name, so the schema shows a summary that
        nobody wrote."""
        without = [
            f"{sorted(route.methods)} {route.path}" for route in _routes(app) if not route.summary
        ]
        assert without == [], f"routes zonder summary=: {without}"

    def test_every_route_declares_a_response_model(self, app) -> None:
        """A bare `-> dict` yields an empty schema; only a 204 may go without."""
        error = []
        for route in _routes(app):
            if route.status_code in (204, 304):
                if route.response_model is not None:
                    error.append(f"{sorted(route.methods)} {route.path}: 204 met responsemodel")
                continue
            if not _is_api_model(route.response_model):
                error.append(f"{sorted(route.methods)} {route.path}: {route.response_model!r}")
        assert error == [], f"routes zonder pydantic-responsemodel: {error}"

    def test_every_endpoint_has_a_summary(self, schema) -> None:
        without = [f"{m.upper()} {p}" for m, p, op in _operations(schema) if not op.get("summary")]
        assert without == [], f"endpoints zonder summary: {without}"

    def test_every_endpoint_has_a_hint(self, schema) -> None:
        without = [f"{m.upper()} {p}" for m, p, op in _operations(schema) if not op.get("description")]
        assert without == [], f"endpoints zonder description: {without}"

    def test_every_endpoint_has_a_known_tag(self, schema) -> None:
        known = {tag["name"] for tag in schema.get("tags", [])}
        assert known, "de API declareert geen tags"
        error = [
            f"{m.upper()} {p}"
            for m, p, op in _operations(schema)
            if not op.get("tags") or not set(op["tags"]) <= known
        ]
        assert error == [], f"endpoints zonder (bekende) tag: {error}"

    def test_every_endpoint_has_a_response_model(self, schema) -> None:
        """A successful response points at a schema, or by its status code has
        no body (204)."""
        error = []
        for method_, path, operation in _operations(schema):
            succeeded = {
                code: response
                for code, response in operation.get("responses", {}).items()
                if code.startswith("2")
            }
            if not succeeded:
                error.append(f"{method_.upper()} {path}: geen 2xx-antwoord")
                continue
            for code, response in succeeded.items():
                content = response.get("content", {})
                if code in WITHOUT_BODY:
                    if content:
                        error.append(f"{method_.upper()} {path}: {code} hoort geen lichaam te hebben")
                    continue
                schema_of_response = content.get("application/json", {}).get("schema")
                if not schema_of_response:
                    error.append(f"{method_.upper()} {path}: {code} heeft geen responsemodel")
                elif not (schema_of_response.get("$ref") or schema_of_response.get("items")):
                    error.append(f"{method_.upper()} {path}: {code} heeft een leeg schema")
        assert error == [], error

    def test_successful_responses_are_in_the_dutch_described(self, schema) -> None:
        """FastAPI's own text is 'Successful Response'; the docs are Dutch."""
        error = []
        for method_, path, operation in _operations(schema):
            for code, response in operation.get("responses", {}).items():
                if not code.startswith("2"):
                    continue
                description_ = response.get("description", "")
                if not description_ or description_ == "Successful Response":
                    error.append(f"{method_.upper()} {path}: {code} -> {description_!r}")
        assert error == [], error

    def test_every_path_parameter_is_described(self, schema) -> None:
        error = [
            f"{m.upper()} {p}: {param['name']}"
            for m, p, op in _operations(schema)
            for param in op.get("parameters", [])
            if not param.get("description")
        ]
        assert error == [], f"parameters zonder beschrijving: {error}"

    def test_no_response_schema_is_an_empty_object(self, schema) -> None:
        """The regression that provoked all of this: `-> dict` yields `{}`."""
        for method_, path, operation in _operations(schema):
            for code, response in operation.get("responses", {}).items():
                for mediatype, content in response.get("content", {}).items():
                    content_schema = content.get("schema", {})
                    assert content_schema != {}, f"{method_.upper()} {path} {code} {mediatype}: leeg schema"
                    assert content_schema != {"type": "object"}, (
                        f"{method_.upper()} {path} {code} {mediatype}: schema zonder velden"
                    )


class TestErrorContract:
    def test_problem_is_in_components(self, schema) -> None:
        problem = schema["components"]["schemas"][PROBLEM_SCHEMA_NAME]
        assert set(problem["properties"]) == {
            "type",
            "title",
            "status",
            "detail",
            "code",
            "indexCandidates",
        }
        assert set(problem["required"]) >= {"title", "status", "detail"}

    def test_every_error_is_problem_json_with_the_problem_schema(self, schema) -> None:
        seen = 0
        for method_, path, operation in _operations(schema):
            for code, response in operation.get("responses", {}).items():
                if not code.startswith(("4", "5")):
                    continue
                seen += 1
                content = response.get("content", {})
                assert list(content) == [PROBLEM_CONTENT_TYPE], f"{method_.upper()} {path} {code}: {list(content)}"
                assert content[PROBLEM_CONTENT_TYPE]["schema"] == {"$ref": PROBLEM_SCHEMA_REF}
                assert response.get("description"), f"{method_.upper()} {path} {code}: geen omschrijving"
        assert seen >= 60

    def test_fastapi_validation_schema_is_replaced(self, schema) -> None:
        """FastAPI's own 422 promises application/json with
        HTTPValidationError; the app sends problem+json."""
        import json

        assert "HTTPValidationError" not in json.dumps(schema)

    # The two halves of `plak login` a CLI reaches before it has any credential,
    # and the logout, which answers 204 for any token so it is no oracle.
    ANONYMOUS = frozenset(
        {
            "POST /-/api/v1/cli/device-authorizations",
            "POST /-/api/v1/cli/tokens",
            "DELETE /-/api/v1/cli/session",
        }
    )

    def test_every_endpoint_documents_the_401(self, schema) -> None:
        """No endpoint in this API is reachable anonymously, except the start of the CLI login."""
        without = {
            f"{m.upper()} {p}" for m, p, op in _operations(schema) if "401" not in op.get("responses", {})
        }
        assert without == self.ANONYMOUS, f"endpoints zonder 401: {without - self.ANONYMOUS}"


class TestFieldsAreDescribed:
    def test_every_field_of_every_model_has_a_description(self, schema) -> None:
        error = []
        for name, model in schema["components"]["schemas"].items():
            for field, definition in (model.get("properties") or {}).items():
                if not definition.get("description"):
                    error.append(f"{name}.{field}")
        assert error == [], f"velden zonder beschrijving: {error}"

    def test_wire_fields_are_lowercamelcase(self, schema) -> None:
        """The ApiModel base serialises camelCase (NL API Design Rules)."""
        error = []
        for name, model in schema["components"]["schemas"].items():
            for field in model.get("properties") or {}:
                if "_" in field or field[:1].isupper():
                    error.append(f"{name}.{field}")
        assert error == [], f"velden die geen lowerCamelCase zijn: {error}"


class TestDeployContract:
    """The two endpoints CI builds on, with their multipart contract."""

    def _deploy(self, schema) -> dict[str, Any]:
        return schema["paths"]["/-/api/v1/sites/{group_slug}/{site_slug}/deploys"]["post"]

    def test_multipart_fields_are_documented(self, schema) -> None:
        content = self._deploy(schema)["requestBody"]["content"]["multipart/form-data"]
        fields = content["schema"]["properties"]
        assert content["schema"]["required"] == ["file"]
        assert fields["file"]["format"] == "binary"
        for shape in (".html", ".zip", ".tar.gz", ".tgz"):
            assert shape in fields["file"]["description"]
        assert "preview" in fields
        assert fields["preview"]["description"]

    def test_example_call_and_limits_stand_in_the_hint(self, schema) -> None:
        hint = self._deploy(schema)["description"]
        assert "curl" in hint
        assert "PLAK_INGEST_MAX_BODY" in hint

    def test_response_is_version_id(self, schema) -> None:
        response = self._deploy(schema)["responses"]["201"]["content"]["application/json"]["schema"]
        model = schema["components"]["schemas"][response["$ref"].split("/")[-1]]
        assert list(model["properties"]) == ["versionId"]

    def test_preview_teardown_gives_204_without_body(self, schema) -> None:
        path = "/-/api/v1/sites/{group_slug}/{site_slug}/previews/{ref}"
        response = schema["paths"][path]["delete"]["responses"]["204"]
        assert "content" not in response
        assert response["description"]


class TestApiDescription:
    def test_info_is_filled_in(self, schema) -> None:
        info = schema["info"]
        assert info["title"] == "Plak API"
        assert info["description"].strip()
        assert info["contact"]["email"]
        assert info["version"].count(".") == 2

    def test_tags_have_a_description(self, schema) -> None:
        assert schema["tags"]
        for tag in schema["tags"]:
            assert tag["description"], tag["name"]


class TestSecurity:
    """Every endpoint states which authentication it accepts (Swagger: Authorize)."""

    DEPLOY = "/-/api/v1/sites/{group_slug}/{site_slug}/deploys"
    TEARDOWN = "/-/api/v1/sites/{group_slug}/{site_slug}/previews/{ref}"

    def test_both_schemas_are_in_components(self, schema) -> None:
        schemas = schema["components"]["securitySchemes"]
        assert schemas[SECURITY_SESSION] == {
            "type": "apiKey",
            "in": "cookie",
            "name": SESSION_COOKIE,
            "description": schemas[SECURITY_SESSION]["description"],
        }
        assert schemas[SECURITY_BEARER]["type"] == "http"
        assert schemas[SECURITY_BEARER]["scheme"] == "bearer"

    def test_every_operation_names_its_authentication(self, schema) -> None:
        for path, path_part in schema["paths"].items():
            for method_, operation in path_part.items():
                assert operation["security"], f"{method_} {path}"

    def test_only_the_deploy_cli_session_and_creation_endpoints_accept_a_token(self, schema) -> None:
        with_token = {
            (path, method_)
            for path, path_part in schema["paths"].items()
            for method_, operation in path_part.items()
            if any(SECURITY_BEARER in requirement for requirement in operation["security"])
        }
        assert with_token == {
            (self.DEPLOY, "post"),
            (self.TEARDOWN, "delete"),
            ("/-/api/v1/cli/session", "delete"),
            ("/-/api/v1/cli/whoami", "get"),
            ("/-/api/v1/groups", "post"),
            ("/-/api/v1/groups/{group_slug}/sites", "post"),
        }

    def test_the_documented_token_endpoints_are_exactly_the_ones_the_middleware_lets_through(
        self, schema
    ) -> None:
        """The docs and BearerOutsideDeploysMiddleware answer the same question
        from two places; over every operation they have to agree."""
        for path, path_part in schema["paths"].items():
            concrete = re.sub(r"\{[^}]+\}", "x", path)
            for method_, operation in path_part.items():
                documented = any(SECURITY_BEARER in requirement for requirement in operation["security"])
                assert accepts_bearer(method_.upper(), concrete) == documented, f"{method_} {path}"

    def test_the_cli_login_start_and_token_endpoint_need_no_authentication(self, schema) -> None:
        assert schema["paths"]["/-/api/v1/cli/device-authorizations"]["post"]["security"] == [{}]
        assert schema["paths"]["/-/api/v1/cli/tokens"]["post"]["security"] == [{}]

    def test_a_deploy_endpoint_accepts_besides_the_session(self, schema) -> None:
        requirements = schema["paths"][self.DEPLOY]["post"]["security"]
        assert {SECURITY_BEARER: []} in requirements
        assert {SECURITY_SESSION: []} in requirements


class TestInputExamples:
    """Every request body shows a body the API would accept."""

    def _request_models(self, schema) -> dict[str, dict[str, Any]]:
        models = {}
        for path_part in schema["paths"].values():
            for operation in path_part.values():
                content = operation.get("requestBody", {}).get("content", {}).get("application/json")
                if content:
                    # An optional body (the CLI login start) is anyOf [model, null].
                    body = content["schema"]
                    ref = body.get("$ref") or next(part["$ref"] for part in body["anyOf"] if "$ref" in part)
                    name = ref.split("/")[-1]
                    models[name] = schema["components"]["schemas"][name]
        return models

    def test_every_json_body_has_a_example(self, schema) -> None:
        models = self._request_models(schema)
        assert models
        for name, model in models.items():
            assert model.get("examples"), name

    def test_a_example_covers_every_required_field(self, schema) -> None:
        for name, model in self._request_models(schema).items():
            required = set(model.get("required", []))
            for example in model["examples"]:
                assert required <= set(example), f"{name}: {example}"

    def test_null_stays_in_the_example_stand(self, schema) -> None:
        # FastAPI runs the schema through exclude_none; without the repair in
        # api/docs.py this example would end up as {}, and that is a 422.
        examples = schema["components"]["schemas"]["PreviewAccessBody"]["examples"]
        assert {"access": None} in examples


class TestErrorExamples:
    """The example body belongs to the status code it sits under."""

    def _errors(self, schema, path: str, method_: str) -> dict[str, dict[str, Any]]:
        return {
            status: response["content"][PROBLEM_CONTENT_TYPE]["example"]
            for status, response in schema["paths"][path][method_]["responses"].items()
            if PROBLEM_CONTENT_TYPE in response.get("content", {})
        }

    def test_every_example_repeats_are_own_status_code(self, schema) -> None:
        for path, path_part in schema["paths"].items():
            for method_ in path_part:
                for status, example in self._errors(schema, path, method_).items():
                    assert example["status"] == int(status), f"{method_} {path} {status}"

    def test_the_code_comes_from_the_description_beside_it(self, schema) -> None:
        response = schema["paths"]["/-/api/v1/groups"]["post"]["responses"]["409"]
        assert response["content"][PROBLEM_CONTENT_TYPE]["example"]["code"] == "SLUG_EXISTS"
        assert "SLUG_EXISTS" in response["description"]

    def test_no_example_carries_a_extension_field_that_there_not_belongs(self, schema) -> None:
        # indexCandidates belongs only to a refused bundle, and that is a case
        # the description explains, not the standard example.
        for path, path_part in schema["paths"].items():
            for method_ in path_part:
                for status, example in self._errors(schema, path, method_).items():
                    assert FIELD_INDEX_CANDIDATES not in example, f"{method_} {path} {status}"

    def test_without_error_code_in_the_text_stays_code_gone(self) -> None:
        assert "code" not in error_example(429, "Het ratelimit-budget is op.")
        assert error_example(429, "Het ratelimit-budget is op.")["status"] == 429

    def test_a_word_without_lying_dash_is_no_error_code(self) -> None:
        assert "code" not in error_example(422, "Stuur het verzoek als `POST`.")

    def test_every_documented_code_exists_in_the_messages_catalogue(self, schema) -> None:
        # Every backticked SCREAMING_SNAKE token in a response description is
        # read by error_example() as a code; one that the catalogue does not
        # know cannot have been rendered in a real answer, so it is a typo.
        known_codes = {messages.code_of(key) for key in messages.NL if not messages.is_fragment(key)}
        for path, path_part in schema["paths"].items():
            for method_, operation in path_part.items():
                if not isinstance(operation, dict):
                    continue
                for status, response in operation.get("responses", {}).items():
                    description = response.get("description", "")
                    for code in _CODE_RE.findall(description):
                        assert code in known_codes, f"{method_} {path} {status}: `{code}`"


class TestSchemaEditsOnEdgeCases:
    """The edits on the generated schema, apart from the real app.

    An OpenAPI path item holds more than operations: `parameters`, `summary`
    and `$ref` sit at the same level. The skip branches for those never come
    past with the real schema, so they are driven directly here.
    """

    def test_security_stores_not_operations_about_and_lets_a_own_requirement_stand(self) -> None:
        schema = {
            "paths": {
                "/x": {
                    "parameters": [{"name": "q"}],
                    "get": {"security": [{"eigen": []}]},
                    "post": {},
                }
            }
        }
        _set_security(schema)
        part_ = schema["paths"]["/x"]
        assert part_["parameters"] == [{"name": "q"}]
        assert part_["get"]["security"] == [{"eigen": []}]
        assert part_["post"]["security"] == [{SECURITY_SESSION: []}]

    def test_path_parameters_let_a_existing_or_unknown_hint_with_rest(self) -> None:
        schema = {
            "paths": {
                "/x": {
                    "summary": "geen operatie",
                    "get": {
                        "parameters": [
                            {"name": "group_slug", "description": "eigen tekst"},
                            {"name": "onbekend"},
                            {"name": "site_slug"},
                        ]
                    },
                }
            }
        }
        _describe_path_parameters(schema)
        parameters = schema["paths"]["/x"]["get"]["parameters"]
        assert parameters[0]["description"] == "eigen tekst"
        assert "description" not in parameters[1]
        assert parameters[2]["description"] == PATH_PARAMETERS["site_slug"]

    def test_validation_errors_fix_stores_not_operations_about(self) -> None:
        schema = {"paths": {"/x": {"summary": "geen operatie", "get": {"responses": {}}}}}
        _fix_validation_errors(schema)
        assert schema["paths"]["/x"]["get"]["responses"] == {}

    def test_examples_restore_stores_a_model_about_that_not_in_the_schema_is(self) -> None:
        # A model without a route is absent from components; that must not be an error.
        schema: dict[str, Any] = {"components": {"schemas": {}}}
        _restore_examples(schema)
        assert schema["components"]["schemas"] == {}


class TestAssetOnAllowlistButMissing:
    """The allowlist and the disk can drift apart; then it is a 404, not a 500."""

    async def test_missing_file_gives_the_same_404_as_a_unknown_name(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("plak.api.docs.STATIC_DOCS_DIR", tmp_path)
        with pytest.raises(ApiError) as error:
            await docs_asset("docs.css")
        assert error.value.status == 404
        assert error.value.reason == "UNKNOWN_ASSET"


class TestVendoredAssets:
    """The Swagger UI files are downloaded bytes that nobody reads: 1.5 MB of
    minified JavaScript, served from the beheer origin under `script-src
    'self'`, so fully trusted script beside the session cookie. SHA256SUMS is
    the reviewed record of which bytes those are; this test is what makes a
    change to them visible without a network call.

    It already caught one: a repo-wide rename of `project` to `site` had
    silently edited a URI-scheme list inside the minified bundle.
    """

    def test_every_vendored_file_matches_the_pinned_sum(self):
        sums = (STATIC_DOCS_DIR / "SHA256SUMS").read_text().splitlines()
        assert sums, "SHA256SUMS is empty"
        for line in sums:
            expected, name = line.split(maxsplit=1)
            path = STATIC_DOCS_DIR / name.strip()
            assert path.is_file(), f"{name} is pinned but missing"
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            assert actual == expected, f"{name} does not match SHA256SUMS"

    def test_every_served_asset_is_pinned(self):
        """A new asset may not slip past the pinning by simply not being in
        the file."""
        pinned = {
            line.split(maxsplit=1)[1].strip()
            for line in (STATIC_DOCS_DIR / "SHA256SUMS").read_text().splitlines()
        }
        served = {name for name in _ASSETS if not (STATIC_DOCS_DIR / name).is_symlink()}
        vendored = {name for name in served if name.startswith("swagger-ui")}
        assert vendored <= pinned, f"not pinned: {vendored - pinned}"
