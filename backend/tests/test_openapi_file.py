"""The OpenAPI schema as the release commits it (plak.api.openapi_file).

The release writes it to backend/openapi.json and the API check compares a
pull request with it, so it has to come out the same wherever it is built.
"""

from __future__ import annotations

import json
import re

from plak.api import openapi_file
from plak.config import Settings
from plak.main import API_VERSION, create_app


def test_the_schema_does_not_depend_on_the_settings(tmp_path):
    """The placeholders are not a contract: another deployment's settings
    give the same schema."""
    settings = Settings(
        db_url="postgresql+asyncpg://other:other@db.internal:5432/other",
        content_root=tmp_path,
        oidc_issuer="https://login.example.org/realms/x",
        oidc_client_id="other-client",
        oidc_client_private_jwk="{}",
        oidc_required_acr="urn:acr:hoog",
        oidc_rp_logout=True,
        session_secret="another-session-secret-of-32-bytes!!",
        audit_pepper="another-audit-pepper-of-32-bytes!!!!",
        audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
        content_base_url="https://sites.example.org",
        environment="dev",
        base_url="https://admin.example.org",
    )
    assert openapi_file.schema() == create_app(settings).openapi()


def test_main_prints_the_schema_as_indented_json(capsys):
    openapi_file.main()
    out = capsys.readouterr().out
    assert out == openapi_file.render(openapi_file.schema())
    assert out.startswith('{\n  "openapi": "3.1.0",\n')
    assert json.loads(out)["info"]["version"] == API_VERSION


def test_render_keeps_dutch_text_readable():
    assert openapi_file.render({"a": "één"}) == '{\n  "a": "één"\n}\n'


def test_the_major_of_the_api_version_is_the_one_in_the_path():
    """The release raises minor and patch; the major moves by hand, together
    with the path. This holds the two to each other."""
    major = API_VERSION.split(".")[0]
    schema = openapi_file.schema()
    assert schema["servers"] == [{"url": f"/-/api/v{major}"}]
    versions = {match.group(1) for path in schema["paths"] if (match := re.match(r"^/-/api/v(\d+)/", path))}
    assert versions == {major}
    assert all(path.startswith("/-/api/v") for path in schema["paths"])
