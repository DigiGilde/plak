"""What the tests of scripts/release.py share: the entry module, the files
a release touches, a repository class and the fixtures."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

from plak_release import changelog, commands, git, notes, versions

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release.py"


def _load():
    spec = importlib.util.spec_from_file_location("release", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["release"] = module
    spec.loader.exec_module(module)
    return module


release = _load()

DAY = date(2026, 10, 1)
TAG = "v2026.10.1"
FRAGMENT = {"changelog.d/something-new.added.md": "- Something new.\n"}
ENTRY = "### Added\n\n- Something new."
NOTE_FRAGMENTS = {"changelog.d/x.note.nl.md": "Nieuw.\n", "changelog.d/x.note.en.md": "New.\n"}
NOTES = notes.NOTES_DIR
BOTH_LANGUAGES = "a What's new note comes in Dutch and English."

PYPROJECT = (
    '[build-system]\nversion = "keep"\n\n'
    '[project]\nname = "plak"\nversion = "0.1.0"\ndependencies = ["httpx"]\n\n'
    '[tool.other]\nversion = "keep"\n'
)
LOCK = (
    "version = 1\n\n"
    '[[package]]\nname = "httpx"\nversion = "0.28.1"\nsource = { registry = "https://pypi.org/simple" }\n\n'
    '[[package]]\nname = "plak"\nversion = "0.1.0"\nsource = { editable = "." }\n'
    'dependencies = [\n    { name = "httpx" },\n]\n'
)
PLUGIN = (
    '{\n  "name": "plak",\n  "version": "0.3.1",\n'
    '  "author": {\n    "name": "Plak team"\n  },\n  "keywords": ["plak"]\n}\n'
)
PUBLICCODE = 'name: Plak\n\nsoftwareVersion: "0.0.0"\nreleaseDate: "2026-07-18"\n\nlegal:\n  license: EUPL-1.2\n'
BACKEND_PYPROJECT = '[project]\nname = "plak-api"\nversion = "0.1.0"\n\n[build-system]\nrequires = ["hatchling"]\n'
BACKEND_LOCK = (
    "version = 1\n\n"
    '[[package]]\nname = "plak-api"\nversion = "0.1.0"\nsource = { editable = "." }\n\n'
    '[[package]]\nname = "plak"\nversion = "9.9.9"\nsource = { editable = "." }\n'
)
MAIN = 'import x\n\nAPI_VERSION = "1.0.0"\n\napp = 1\n'


def spec_with(version: str = "1.0.0", paths: tuple[str, ...] = ("/a",), description: str = "") -> str:
    """An API schema as plak.api.openapi_file prints it, kept small."""
    info = {"title": "Plak API", "version": version, "description": description}
    return json.dumps({"openapi": "3.1.0", "info": info, "paths": {path: {} for path in paths}}, indent=2) + "\n"


SPEC = spec_with()


def NO_CHANGES(base: str, head: str) -> list:  # noqa: N802 - reads as the constant it stands for
    return []


PACKAGE = '{\n  "name": "plak-frontend",\n  "version": "0.1.0",\n  "private": true\n}\n'
PACKAGE_LOCK = (
    '{\n  "name": "plak-frontend",\n  "version": "0.1.0",\n  "lockfileVersion": 3,\n  "packages": {\n'
    '    "": {\n      "name": "plak-frontend",\n      "version": "0.1.0",\n'
    '      "dependencies": {\n        "vue": "^3.5.0"\n      }\n    },\n'
    '    "node_modules/vue": {\n      "version": "3.5.13"\n    }\n  }\n}\n'
)


def changelog_text(released: str = "", unreleased: str = changelog.UNRELEASED_POINTER) -> str:
    """A changelog with [Unreleased] over `unreleased` and `released` as its
    version sections, links included."""
    text = f"# Changelog\n\nWhat changed, newest first.\n\n## [Unreleased]\n\n{unreleased}\n"
    if released:
        text += "\n" + released.strip("\n") + "\n"
    return changelog.with_links(text)


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path

    def git(self, *args: str) -> str:
        # git via PATH, like the script; every argument comes from this file.
        return subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=self.path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def write(self, files: dict[str, str]) -> None:
        for name, text in files.items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    def read(self, name: str) -> str:
        return (self.path / name).read_text(encoding="utf-8")

    def commit(self, files: dict[str, str] | None = None, message: str = "Change something") -> None:
        self.write(files or {})
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def remove(self, name: str) -> None:
        self.git("rm", "-q", name)
        self.git("commit", "-q", "-m", f"Remove {name}")

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    @property
    def handle(self):
        return git.Git(self.path)

    def prepare(self, tag: str | None = None, day: date = DAY, spec: str | None = None, diff=None) -> str:
        """What a release pull request holds: the release prepared and
        committed, not tagged. Returns the tag."""
        tag, _ = commands.prepare(self.handle, day, spec or SPEC, diff or NO_CHANGES, tag)
        self.git("commit", "-q", "-m", f"Release {tag}")
        return tag

    def release(self, tag: str, day: date, spec: str | None = None, diff=None) -> None:
        """The whole cycle: the release prepared, merged and tagged."""
        self.git("tag", self.prepare(tag, day, spec, diff))


def _versions(repo: Repo) -> tuple[str, str]:
    return (
        versions.pyproject_version(repo.read("cli/pyproject.toml")),
        json.loads(repo.read("plugin/.claude-plugin/plugin.json"))["version"],
    )


def _image_versions(repo: Repo) -> set[str]:
    """Every version the image's packages carry: the backend project and
    its lock entry, package.json and both places in package-lock.json."""
    lock = repo.read("backend/uv.lock")
    package_lock = json.loads(repo.read("frontend/package-lock.json"))
    return {
        versions.pyproject_version(repo.read("backend/pyproject.toml"), "backend/pyproject.toml"),
        lock.split('name = "plak-api"\nversion = "')[1].split('"')[0],
        json.loads(repo.read("frontend/package.json"))["version"],
        package_lock["version"],
        package_lock["packages"][""]["version"],
    }


def change(level: int, text: str = "endpoint added", path: str = "/b", operation: str = "GET") -> dict:
    """One entry of `oasdiff changelog --format json`."""
    return {"id": "x", "text": text, "level": level, "operation": operation, "path": path, "section": "paths"}


def fake_oasdiff(tmp_path: Path, stdout: str = "[]", exit_code: int = 0, stderr: str = "") -> str:
    """A stand-in for the oasdiff binary: logs its arguments and both files,
    then answers as told."""
    script = tmp_path / "oasdiff"
    log = tmp_path / "oasdiff.log"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > "{log}"\n'
        f'cat "$2" >> "{log}"; echo "---" >> "{log}"; cat "$3" >> "{log}"\n'
        f"printf '%s' '{stdout}'\n"
        f"printf '%s' '{stderr}' >&2\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


RELEASED_SECTION = f"## [2026.9.1]\n\n{ENTRY}"


def _event(repo: Repo, command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo.path)})
