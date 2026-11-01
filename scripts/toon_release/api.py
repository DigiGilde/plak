"""The API contract: the schema a release commits, its SemVer and oasdiff."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from toon_release.errors import ReleaseError

OPENAPI = "backend/openapi.json"
API_VERSION_FILE = "backend/src/toon/main.py"


API_VERSION_LINE = re.compile(r'^(API_VERSION = ")[^"\n]*(")$', re.MULTILINE)
API_VERSION_VALUE = re.compile(r'^API_VERSION = "([^"\n]*)"$', re.MULTILINE)
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
#: oasdiff's level for a breaking change (ERR); WARN is 2 and INFO 1.
BREAKING = 3


#: oasdiff's changelog of a base schema against a head schema, both as text.
Diff = Callable[[str, str], list[dict[str, Any]]]


def api_version(spec: dict[str, Any], source: str) -> tuple[int, int, int]:
    version = str(spec.get("info", {}).get("version", ""))
    match = SEMVER.match(version)
    if not match:
        raise ReleaseError(f"{source}: info.version '{version}' is not MAJOR.MINOR.PATCH.")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def _without_version(spec: dict[str, Any]) -> dict[str, Any]:
    info = {key: value for key, value in spec.get("info", {}).items() if key != "version"}
    return {**spec, "info": info}


def _where(change: dict[str, Any]) -> str:
    return " ".join(filter(None, (change.get("operation"), change.get("path")))) or change.get("section", "API")


def breaking_problems(changes: list[dict[str, Any]], major: int) -> list[str]:
    breaking = [change for change in changes if change.get("level") == BREAKING]
    if not breaking:
        return []
    return [
        *(f"Breaking API change: {_where(change)}: {change.get('text')}." for change in breaking),
        (
            f"A breaking change needs a new major: serve the API under /-/api/v{major + 1} and set "
            f"API_VERSION in {API_VERSION_FILE} to '{major + 1}.0.0' (docs/publishing.md)."
        ),
    ]


def next_api_version(base: dict[str, Any] | None, head: dict[str, Any], changes: list[dict[str, Any]]) -> str:
    """The version a release gives `head`, after `base` shipped. The major
    is set by hand with the path; minor and patch follow what changed."""
    major, minor, patch = api_version(head, API_VERSION_FILE)
    if base is None:
        return f"{major}.{minor}.{patch}"
    released = api_version(base, f"{OPENAPI} at the newest tag")
    if major > released[0]:
        return f"{major}.0.0"
    if major < released[0]:
        raise ReleaseError(f"API_VERSION in {API_VERSION_FILE} has major {major}, below the released {released[0]}.")
    if problems := breaking_problems(changes, major):
        raise ReleaseError(*problems)
    if _without_version(base) == _without_version(head):
        return "{}.{}.{}".format(*released)
    if changes:
        return f"{major}.{released[1] + 1}.0"
    return f"{major}.{released[1]}.{released[2] + 1}"


def set_api_version(text: str, version: str) -> str:
    edited, count = API_VERSION_LINE.subn(rf"\g<1>{version}\g<2>", text)
    if count != 1:
        raise ReleaseError(f"{API_VERSION_FILE}: expected one API_VERSION line, found {count}.")
    return edited


def render_spec(spec: dict[str, Any], version: str) -> str:
    """The schema as toon.api.openapi_file prints it, at `version`."""
    document = {**spec, "info": {**spec.get("info", {}), "version": version}}
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def parse_spec(text: str, source: str) -> dict[str, Any]:
    try:
        spec = json.loads(text)
    except ValueError:
        raise ReleaseError(f"{source} is not JSON.") from None
    if not isinstance(spec, dict):
        raise ReleaseError(f"{source} is not an OpenAPI document.")
    return spec


def oasdiff(binary: str) -> Diff:
    """Runs `oasdiff changelog` from `binary`. External references stay
    off: oasdiff would fetch them, and the schema has none."""

    def diff(base: str, head: str) -> list[dict[str, Any]]:
        with tempfile.TemporaryDirectory() as folder:
            files = [Path(folder) / "base.json", Path(folder) / "head.json"]
            for path, text in zip(files, (base, head), strict=True):
                path.write_text(text, encoding="utf-8")
            result = subprocess.run(  # noqa: S603 - the binary is the caller's --oasdiff, the files are ours
                [binary, "changelog", str(files[0]), str(files[1]), "--format", "json", "--allow-external-refs=false"],
                capture_output=True,
                text=True,
                check=False,
            )
        if result.returncode:
            raise ReleaseError(f"oasdiff: {result.stderr.strip() or f'exit {result.returncode}'}")
        try:
            changes = json.loads(result.stdout or "[]")
        except ValueError:
            raise ReleaseError("oasdiff: its output is not JSON.") from None
        return changes

    return diff
