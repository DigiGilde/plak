"""The OpenAPI schema as a file: `python -m plak.api.openapi_file` prints it.

A release commits it as backend/openapi.json, the contract that release
shipped, and the API check compares a pull request with the one at the
newest tag (docs/releasing.md).

Building the app connects to nothing, so placeholder settings do. The schema
does not depend on them; test_openapi_file.py holds it to that.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from plak.config import Settings
from plak.main import create_app


def schema() -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as content_root:
        settings = Settings(
            db_url="postgresql+asyncpg://plak@localhost/plak",
            content_root=Path(content_root),
            oidc_issuer="https://idp.example",
            oidc_client_id="plak",
            oidc_client_private_jwk="{}",
            oidc_required_acr="urn:acr:placeholder",
            session_secret="placeholder-session-secret-of-32-bytes",  # noqa: S106 - a placeholder, not a secret
            audit_pepper="placeholder-audit-pepper-of-32-bytes!",
            audit_ip_key="a2tra2tra2tra2tra2tra2tra2tra2tra2tra2tra2s=",
            content_base_url="https://plak.example",
            environment="dev",
            base_url="https://beheer.plak.example",
        )
        return create_app(settings).openapi()


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    sys.stdout.write(render(schema()))


if __name__ == "__main__":  # pragma: no cover - the module entry; main() is tested directly
    main()
