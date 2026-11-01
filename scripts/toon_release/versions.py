"""What a release ships and the version lines it writes into the components."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import date

from toon_release.api import API_VERSION_FILE, OPENAPI
from toon_release.changelog import CHANGELOG
from toon_release.errors import ReleaseError
from toon_release.git import Git

#: What ends up in what we ship: the image, the CLI, the plugin, the action.
#: An entry ending in "/" is a directory, anything else one file. Tests never
#: count (is_test_path), and NOT_SHIPPED carves out what sits inside.
SHIPPED_PATHS = (
    "backend/src/",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/alembic/",
    "backend/alembic.ini",
    "frontend/src/",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/index.html",
    "frontend/vite.config.ts",
    "frontend/public/",
    "containers/toon/",
    "cli/plak_cli/",
    "cli/pyproject.toml",
    "cli/uv.lock",
    "plugin/",
    "actions/publish/",
)
NOT_SHIPPED = ("plugin/evals/",)

CLI_PYPROJECT = "cli/pyproject.toml"
CLI_LOCK = "cli/uv.lock"
CLI_PATHS = ("cli/plak_cli/", CLI_PYPROJECT, CLI_LOCK)
PLUGIN_MANIFEST = "plugin/.claude-plugin/plugin.json"
PLUGIN_PATHS = ("plugin/",)
PUBLICCODE = "publiccode.yml"
BACKEND_PYPROJECT = "backend/pyproject.toml"
BACKEND_LOCK = "backend/uv.lock"
FRONTEND_PACKAGE = "frontend/package.json"
FRONTEND_LOCK = "frontend/package-lock.json"


#: What prepare writes; a commit that touches only these (and removes
#: fragments, or writes notes) is part of a release.
RELEASE_PATHS = frozenset(
    {
        CHANGELOG,
        CLI_PYPROJECT,
        CLI_LOCK,
        PLUGIN_MANIFEST,
        PUBLICCODE,
        BACKEND_PYPROJECT,
        BACKEND_LOCK,
        FRONTEND_PACKAGE,
        FRONTEND_LOCK,
        OPENAPI,
        API_VERSION_FILE,
    }
)
#: What every release stages. The CLI and plugin files only when they changed,
#: the schema and API_VERSION only when the API did.
ALWAYS_WRITTEN = (CHANGELOG, PUBLICCODE, BACKEND_PYPROJECT, BACKEND_LOCK, FRONTEND_PACKAGE, FRONTEND_LOCK)


MEMBER_FACING = ("frontend/src/", "cli/plak_cli/")


# The version lines a release writes itself. Left out when comparing a file
# with the previous release, so the bump of that release is not a change.
PYPROJECT_VERSION = re.compile(r'^version = "[^"\n]*"$', re.MULTILINE)


def _lock_version(name: str) -> re.Pattern[str]:
    return re.compile(
        rf'^(\[\[package\]\]\nname = "{re.escape(name)}"\nversion = ")[^"\n]*("\nsource = \{{ editable = "\." \}})$',
        re.MULTILINE,
    )


LOCK_VERSION = _lock_version("plak")
JSON_VERSION = re.compile(r'^(  "version": ")[^"\n]*(",?)$', re.MULTILINE)
# The root package of package-lock.json, under "packages": { "": { ... } }.
NPM_ROOT_VERSION = re.compile(r'^(    "": \{\n(?:      [^\n]*\n)*?      "version": ")[^"\n]*(",?)$', re.MULTILINE)
IGNORING_VERSION = {
    CLI_PYPROJECT: lambda text: PYPROJECT_VERSION.sub("", text),
    CLI_LOCK: lambda text: LOCK_VERSION.sub(r"\1\2", text),
    PLUGIN_MANIFEST: lambda text: JSON_VERSION.sub(r"\1\2", text),
}


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        any(part in ("tests", "test", "__tests__", "__snapshots__") for part in parts[:-1])
        or ".test." in name
        or ".spec." in name
        or name.startswith("test_")
        or name == "conftest.py"
    )


def _under(path: str, entries: Iterable[str]) -> bool:
    return any(path.startswith(entry) if entry.endswith("/") else path == entry for entry in entries)


def is_shipped(path: str) -> bool:
    return _under(path, SHIPPED_PATHS) and not _under(path, NOT_SHIPPED) and not is_test_path(path)


def _project_table(text: str, path: str) -> tuple[int, int]:
    table = re.search(r"^\[project\]$", text, re.MULTILINE)
    if not table:
        raise ReleaseError(f"{path}: no [project] table.")
    following = re.search(r"^\[", text[table.end() :], re.MULTILINE)
    return table.end(), table.end() + following.start() if following else len(text)


def set_pyproject_version(text: str, version: str, path: str = CLI_PYPROJECT) -> str:
    start, end = _project_table(text, path)
    body, count = PYPROJECT_VERSION.subn(f'version = "{version}"', text[start:end], count=1)
    if not count:
        raise ReleaseError(f"{path}: no version in [project].")
    return text[:start] + body + text[end:]


def pyproject_version(text: str, path: str = CLI_PYPROJECT) -> str:
    start, end = _project_table(text, path)
    match = PYPROJECT_VERSION.search(text, start, end)
    if not match:
        raise ReleaseError(f"{path}: no version in [project].")
    return match.group(0).split('"')[1]


def set_lock_version(text: str, version: str, name: str = "plak", path: str = CLI_LOCK) -> str:
    edited, count = _lock_version(name).subn(rf"\g<1>{version}\g<2>", text)
    if count != 1:
        raise ReleaseError(f"{path}: expected one editable package '{name}', found {count}.")
    return edited


def set_plugin_version(text: str, version: str) -> str:
    edited, count = JSON_VERSION.subn(rf"\g<1>{version}\g<2>", text)
    if count != 1 or json.loads(edited).get("version") != version:
        raise ReleaseError(f"{PLUGIN_MANIFEST}: no top-level \"version\" line to set.")
    return edited


def set_package_version(text: str, version: str, path: str = FRONTEND_PACKAGE) -> str:
    """package.json, or package-lock.json, which repeats the version in its
    root package."""
    edited, count = JSON_VERSION.subn(rf"\g<1>{version}\g<2>", text)
    data = json.loads(edited) if count == 1 else {}
    if data.get("version") != version:
        raise ReleaseError(f"{path}: no top-level \"version\" line to set.")
    if "packages" in data:
        edited, count = NPM_ROOT_VERSION.subn(rf"\g<1>{version}\g<2>", edited, count=1)
        if not count:
            raise ReleaseError(f'{path}: no "version" line in the root package to set.')
    return edited


def set_publiccode(text: str, version: str, day: date) -> str:
    for key, value in (("softwareVersion", version), ("releaseDate", day.isoformat())):
        text, count = re.subn(rf"^{key}: .*$", f'{key}: "{value}"', text, count=1, flags=re.MULTILINE)
        if not count:
            raise ReleaseError(f"{PUBLICCODE}: no {key} line.")
    return text


def _package_version(text: str) -> str | None:
    data = json.loads(text)
    return data.get("version") if isinstance(data, dict) else None


def _publiccode_version(text: str) -> str | None:
    match = re.search(r'^softwareVersion: "?([^"\n]*)"?$', text, re.MULTILINE)
    return match.group(1) if match else None


#: The version a release writes, read back from the files it writes it into.
VERSION_OF: dict[str, Callable[[str], str | None]] = {
    BACKEND_PYPROJECT: lambda text: pyproject_version(text, BACKEND_PYPROJECT),
    FRONTEND_PACKAGE: _package_version,
    PUBLICCODE: _publiccode_version,
}


def component_changed(git: Git, since: str, changed: list[str], paths: tuple[str, ...]) -> bool:
    """Whether a component changed since `since`, ignoring the version line
    the release writes into its own files."""
    for path in changed:
        if not _under(path, paths) or _under(path, NOT_SHIPPED) or is_test_path(path):
            continue
        ignoring = IGNORING_VERSION.get(path)
        if ignoring is None:
            return True
        before, after = git.show(since, path), git.show("HEAD", path)
        if before is None or after is None or ignoring(before) != ignoring(after):
            return True
    return False
