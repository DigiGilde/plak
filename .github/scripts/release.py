# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Releases from CHANGELOG.md: CalVer tags, component versions, the checks.

    decide              what a push to beta does: release, hold or none
    promote --tag T     turn [Unreleased] into the section for T, set versions
    notes --tag T       the GitHub Release body for T
    validate-tag T      exit 1 unless T is a CalVer tag
    check --base REF    the pull request check on CHANGELOG.md and the notes
    api-check           the pull request check on the API contract
    hook                the Claude Code PreToolUse hook for `gh pr create`

Run from anywhere inside the repository. docs/releasing.md has the model.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CHANGELOG = "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"
HOLD = "<!-- release: hold -->"
HEADINGS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")

TAG_RE = re.compile(r"^v[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}(\.[0-9]+)?$")
RELEASED_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\]$")
# The form before releases wrote headings themselves: `## [v2026.9.1] - 2026-09-01`.
TAGGED_HEADING = re.compile(r"^## \[v[^\]]*\]")
SECTION_TITLE = re.compile(r"^## \[([^\]]+)\]")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")

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
    "containers/plak/",
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
OPENAPI = "backend/openapi.json"
API_VERSION_FILE = "backend/src/plak/main.py"
API_VERSION_LINE = re.compile(r'^(API_VERSION = ")[^"\n]*(")$', re.MULTILINE)
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
#: oasdiff's level for a breaking change (ERR); WARN is 2 and INFO 1.
BREAKING = 3

NOTES_DIR = "frontend/src/content/releases/"
NOTE_NAME = re.compile(r"^(?P<key>.+)\.(?P<lang>nl|en)\.md$")
UNRELEASED_NOTES = ("unreleased.nl.md", "unreleased.en.md")
MEMBER_FACING = ("frontend/src/", "cli/plak_cli/")

BASE_BRANCH = "origin/beta"
COMMENT_MARKER = "<!-- plak-changelog-check -->"
API_COMMENT_MARKER = "<!-- plak-api-check -->"
OPT_OUT = re.compile(r"""(?:^|["'])[ \t]*No changelog entry:[ \t]*\S""", re.MULTILINE)
GH_PR_CREATE = re.compile(r"\bgh\s+pr\s+create\b")

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


class ReleaseError(Exception):
    """One or more reasons to refuse, each a sentence of its own."""

    def __init__(self, *problems: str) -> None:
        super().__init__("\n".join(problems))
        self.problems = list(problems)


# --- CHANGELOG.md -----------------------------------------------------------


@dataclass(frozen=True)
class Section:
    title: str
    heading: str
    body: str
    start: int
    end: int


def _trim(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    return "\n".join(lines)


def split_sections(text: str) -> list[Section]:
    """Every `## ` section, without judging it. The title is what sits
    between the brackets, so `Unreleased` or the tag."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("## ")]
    sections = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        heading = lines[start].rstrip()
        match = SECTION_TITLE.match(heading)
        title = match.group(1) if match else heading[3:].strip()
        sections.append(Section(title, heading, _trim(lines[start + 1 : end]), start, end))
    return sections


def tag_error(tag: str) -> str | None:
    if not TAG_RE.match(tag):
        return f"'{tag}' is not a CalVer tag: vYYYY.M.D, or vYYYY.M.D.N for another release that day."
    parts = tag[1:].split(".")
    if any(len(part) > 1 and part.startswith("0") for part in parts):
        return f"'{tag}' has a leading zero; write v2026.1.5, not v2026.01.05."
    if len(parts) == 4 and parts[3] == "0":
        return f"'{tag}' counts from .1: the first release of a day has no suffix."
    try:
        date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return f"'{tag}' does not name a real date."
    return None


def tag_key(tag: str) -> tuple[int, ...]:
    return tuple(int(part) for part in tag[1:].split("."))


def unreleased_problems(body: str) -> list[str]:
    problems = []
    entries: dict[str, int] = {}
    heading = None
    in_entry = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped == HOLD:
            continue
        if line.startswith("### "):
            heading = line[4:].strip()
            in_entry = False
            if heading not in HEADINGS:
                problems.append(f"'{line}' is not one of: {', '.join('### ' + h for h in HEADINGS)}.")
            elif heading in entries:
                problems.append(f"'{line}' appears twice in [Unreleased].")
            else:
                entries[heading] = 0
            continue
        if line.startswith("- "):
            if heading is None:
                problems.append(f"'{line}' sits under no heading; put it under one of the ### headings.")
            elif heading in entries:
                entries[heading] += 1
            in_entry = True
            continue
        if in_entry and line[0] in " \t":
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        problems.append(f"'{line}' is not an entry: every change is a list item starting with '- '.")
    problems.extend(f"'### {name}' has no entries." for name, count in entries.items() if count == 0)
    return problems


def changelog_problems(text: str) -> list[str]:
    sections = split_sections(text)
    if not sections or sections[0].heading != UNRELEASED:
        return [f"{CHANGELOG}: the first section has to be '{UNRELEASED}'."]
    problems = [f"{CHANGELOG}: [Unreleased]: {p}" for p in unreleased_problems(sections[0].body)]
    seen = {sections[0].title}
    for section in sections[1:]:
        match = RELEASED_HEADING.match(section.heading)
        if section.title in seen:
            problems.append(f"{CHANGELOG}: '{section.heading}' appears twice.")
        elif TAGGED_HEADING.match(section.heading):
            problems.append(
                f"{CHANGELOG}: '{section.heading}' is the old form; write '## [{section.title[1:]}]', "
                "the version without the v and without a date."
            )
        elif not match:
            problems.append(f"{CHANGELOG}: '{section.heading}' is not '## [YYYY.M.D]' or '## [YYYY.M.D.N]'.")
        elif error := tag_error("v" + match["version"]):
            problems.append(f"{CHANGELOG}: '{section.heading}': {error}")
        seen.add(section.title)
    return problems


def parse_changelog(text: str) -> list[Section]:
    problems = changelog_problems(text)
    if problems:
        raise ReleaseError(*problems)
    return split_sections(text)


def has_entries(body: str) -> bool:
    return any(line.startswith("- ") for line in body.splitlines())


def has_hold(body: str) -> bool:
    return any(line.strip() == HOLD for line in body.splitlines())


def promote_text(text: str, tag: str, body: str) -> str:
    """The changelog with a fresh, empty [Unreleased] above the section for
    `tag`, which holds `body`. The heading is the version, without the v:
    the tag already is the date."""
    lines = text.splitlines()
    unreleased = split_sections(text)[0]
    released = [UNRELEASED, "", f"## [{tag[1:]}]", "", *body.splitlines(), ""]
    rest = lines[unreleased.end :]
    return "\n".join([*lines[: unreleased.start], *released, *rest]).rstrip("\n") + "\n"


# --- Paths and versions -----------------------------------------------------


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


# --- What's new notes -------------------------------------------------------


def note_problems(notes: dict[str, str]) -> list[str]:
    """Every note comes in Dutch and English, both with content or both
    without. `notes` maps a name under NOTES_DIR to its text."""
    pairs: dict[str, dict[str, str]] = {}
    for name, content in notes.items():
        match = NOTE_NAME.match(name)
        if match:
            pairs.setdefault(match["key"], {})[match["lang"]] = content
    problems = []
    for key, languages in sorted(pairs.items()):
        if len(languages) == 1:
            [(lang, _)] = languages.items()
            other = "en" if lang == "nl" else "nl"
            problems.append(
                f"{NOTES_DIR}{key}.{lang}.md has no {key}.{other}.md: a What's new note comes in Dutch and English."
            )
        elif bool(languages["nl"].strip()) != bool(languages["en"].strip()):
            problems.append(f"{NOTES_DIR}{key}.nl.md and {key}.en.md: one is empty and the other is not.")
    return problems


def note_operations(notes: dict[str, str], version: str) -> list[tuple[str, str | None]]:
    """What promote does with the unreleased notes: (name, new name), where a
    new name of None deletes the file."""
    problems = note_problems(notes)
    if problems:
        raise ReleaseError(*problems)
    if UNRELEASED_NOTES[0] not in notes:
        return []
    if not notes[UNRELEASED_NOTES[0]].strip():
        return [(name, None) for name in UNRELEASED_NOTES]
    if f"{version}.nl.md" in notes:
        raise ReleaseError(f"{NOTES_DIR}{version}.nl.md already exists.")
    return [(name, name.replace("unreleased", version)) for name in UNRELEASED_NOTES]


# --- The API contract -------------------------------------------------------

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
    """The schema as plak.api.openapi_file prints it, at `version`."""
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
            result = subprocess.run(
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


# --- git --------------------------------------------------------------------


class Git:
    """The git calls, run in one repository; paths are relative to its root."""

    def __init__(self, cwd: Path) -> None:
        self.root = Path(self.run("rev-parse", "--show-toplevel", cwd=cwd).strip())

    def run(self, *args: str, cwd: Path | None = None) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise ReleaseError(f"git {' '.join(args)}: {result.stderr.strip()}")
        return result.stdout

    def tags(self) -> list[str]:
        return self.run("tag", "--list").split()

    def show(self, ref: str, path: str) -> str | None:
        try:
            return self.run("show", f"{ref}:{path}")
        except ReleaseError:
            return None

    def changed(self, since: str, until: str = "HEAD") -> list[str]:
        return [p for p in self.run("diff", "--name-only", "--no-renames", "-z", since, until).split("\0") if p]

    def merge_base(self, ref: str) -> str:
        return self.run("merge-base", ref, "HEAD").strip()

    def notes(self, ref: str) -> dict[str, str]:
        names = self.run("ls-tree", "-r", "-z", "--name-only", ref, "--", NOTES_DIR).split("\0")
        return {name[len(NOTES_DIR) :]: self.show(ref, name) or "" for name in names if name}


def newest_tag(tags: Iterable[str]) -> str | None:
    calver = [tag for tag in tags if tag_error(tag) is None]
    return max(calver, key=tag_key) if calver else None


def next_tag(day: date, tags: Iterable[str]) -> str:
    existing = set(tags)
    base = f"v{day.year}.{day.month}.{day.day}"
    tag, n = base, 1
    while tag in existing:
        tag, n = f"{base}.{n}", n + 1
    newest = newest_tag(existing)
    if newest and tag_key(tag) <= tag_key(newest):
        raise ReleaseError(f"The next tag {tag} is not newer than {newest}; is the date right?")
    return tag


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


def today() -> date:
    return datetime.now(AMSTERDAM).date()


# --- Subcommands ------------------------------------------------------------


def decide(git: Git, day: date) -> tuple[str, str]:
    """('hold', ''), ('release', tag) or ('none', ''). Only entries release:
    they are the release notes. Without them a push releases nothing."""
    unreleased = parse_changelog((git.root / CHANGELOG).read_text(encoding="utf-8"))[0]
    if has_hold(unreleased.body):
        return "hold", ""
    if has_entries(unreleased.body):
        return "release", next_tag(day, git.tags())
    return "none", ""


def promote(git: Git, tag: str, day: date, spec: str, diff: Diff) -> list[str]:
    """Writes the release into the working tree and stages it. Returns the
    paths it touched. Refuses before it writes anything.

    `spec` is the API schema of HEAD, as plak.api.openapi_file prints it;
    `diff` compares it with the one the previous release committed."""
    if error := tag_error(tag):
        raise ReleaseError(error)
    text = (git.root / CHANGELOG).read_text(encoding="utf-8")
    sections = parse_changelog(text)
    unreleased = sections[0]
    if has_hold(unreleased.body):
        raise ReleaseError(f"[Unreleased] is on hold; remove '{HOLD}' in a pull request to release it.")
    if not has_entries(unreleased.body):
        raise ReleaseError("Nothing to release: [Unreleased] has no entries, and they are the release notes.")
    if any(section.title == tag[1:] for section in sections):
        raise ReleaseError(f"{CHANGELOG} already has a section for {tag}.")
    tags = git.tags()
    if tag in tags:
        raise ReleaseError(f"The tag {tag} already exists.")
    previous = newest_tag(tags)
    if previous and tag_key(tag) <= tag_key(previous):
        raise ReleaseError(f"{tag} is not newer than {previous}.")

    changed = git.changed(previous) if previous else []
    version = tag[1:]
    writes = {CHANGELOG: promote_text(text, tag, unreleased.body)}
    if previous is None or component_changed(git, previous, changed, CLI_PATHS):
        writes[CLI_PYPROJECT] = set_pyproject_version(_read(git, CLI_PYPROJECT), version)
        writes[CLI_LOCK] = set_lock_version(_read(git, CLI_LOCK), version)
    if previous is None or component_changed(git, previous, changed, PLUGIN_PATHS):
        writes[PLUGIN_MANIFEST] = set_plugin_version(_read(git, PLUGIN_MANIFEST), version)
    writes[PUBLICCODE] = set_publiccode(_read(git, PUBLICCODE), version, day)
    # Every tag builds a new image, so its packages follow every release.
    writes[BACKEND_PYPROJECT] = set_pyproject_version(_read(git, BACKEND_PYPROJECT), version, BACKEND_PYPROJECT)
    writes[BACKEND_LOCK] = set_lock_version(_read(git, BACKEND_LOCK), version, "plak-api", BACKEND_LOCK)
    writes[FRONTEND_PACKAGE] = set_package_version(_read(git, FRONTEND_PACKAGE), version)
    writes[FRONTEND_LOCK] = set_package_version(_read(git, FRONTEND_LOCK), version, FRONTEND_LOCK)
    head = parse_spec(spec, "The API schema")
    base_text = git.show(previous, OPENAPI) if previous else None
    base = parse_spec(base_text, f"{OPENAPI} at {previous}") if base_text is not None else None
    api = next_api_version(base, head, diff(base_text, spec) if base_text is not None else [])
    writes[OPENAPI] = render_spec(head, api)
    code = _read(git, API_VERSION_FILE)
    if (edited := set_api_version(code, api)) != code:
        writes[API_VERSION_FILE] = edited
    operations = note_operations(_working_tree_notes(git.root), version)

    for path, content in writes.items():
        (git.root / path).write_text(content, encoding="utf-8")
    git.run("add", "--", *writes)
    touched = list(writes)
    for name, target in operations:
        if target is None:
            git.run("rm", "-q", "--", NOTES_DIR + name)
        else:
            git.run("mv", "--", NOTES_DIR + name, NOTES_DIR + target)
            touched.append(NOTES_DIR + target)
        touched.append(NOTES_DIR + name)
    return touched


def _read(git: Git, path: str) -> str:
    try:
        return (git.root / path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ReleaseError(f"{path} is missing.") from None


def _read_spec(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise ReleaseError(f"{path}: cannot read the API schema.") from None


def _working_tree_notes(root: Path) -> dict[str, str]:
    folder = root / NOTES_DIR
    if not folder.is_dir():
        return {}
    return {
        path.relative_to(folder).as_posix(): path.read_text(encoding="utf-8")
        for path in folder.rglob("*")
        if path.is_file()
    }


def release_notes(git: Git, tag: str) -> str:
    """The body of the GitHub Release for `tag`, read from the tag itself."""
    if error := tag_error(tag):
        raise ReleaseError(error)
    changelog = git.show(tag, CHANGELOG)
    section = next((s for s in split_sections(changelog or "") if s.title == tag[1:]), None)
    if section is None or not section.body:
        raise ReleaseError(f"No section for {tag} in {CHANGELOG} at {tag}.")
    pyproject, plugin = git.show(tag, CLI_PYPROJECT), git.show(tag, PLUGIN_MANIFEST)
    if pyproject is None or plugin is None:
        raise ReleaseError(f"{CLI_PYPROJECT} or {PLUGIN_MANIFEST} is missing at {tag}.")
    version = tag[1:]
    lines = [section.body]
    for name, current in (("CLI", pyproject_version(pyproject)), ("Plugin", json.loads(plugin)["version"])):
        lines.append(f"{name} {current}" if current == version else f"{name} unchanged ({current})")
    # Tags from before the release step wrote the schema have none.
    if (spec := git.show(tag, OPENAPI)) is not None:
        source = f"{OPENAPI} at {tag}"
        lines.append("API {}.{}.{}".format(*api_version(parse_spec(spec, source), source)))
    return "\n\n".join(lines) + "\n"


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)
    warning: str | None = None
    hint: str | None = None


WARNING = (
    "Shipped paths changed, but [Unreleased] in CHANGELOG.md did not. Add a line for this change "
    "(docs/releasing.md), or put a line starting with 'No changelog entry:' and the reason in the "
    "pull request description."
)
HINT = (
    "frontend/src/ or cli/plak_cli/ changed without a What's new note. Is this visible to a member? "
    f"Then add one: {NOTES_DIR}unreleased.nl.md and unreleased.en.md."
)


def entry_missing(git: Git, base: str, body: str = "") -> bool:
    """The warning condition: shipped paths changed since the merge base with
    `base`, [Unreleased] did not, and `body` does not opt out."""
    fork = git.merge_base(base)
    if not any(map(is_shipped, git.changed(fork))):
        return False
    before = _unreleased_body(git.show(fork, CHANGELOG))
    return before == _unreleased_body(git.show("HEAD", CHANGELOG)) and not OPT_OUT.search(body)


def _unreleased_body(text: str | None) -> str | None:
    return next((s.body for s in split_sections(text or "") if s.heading == UNRELEASED), None)


def check(git: Git, base: str, body: str = "", hard_only: bool = False) -> CheckResult:
    result = CheckResult()
    head = git.show("HEAD", CHANGELOG)
    if head is None:
        result.errors.append(f"{CHANGELOG} is missing.")
        return result
    result.errors.extend(changelog_problems(head))

    before = {s.title: s for s in split_sections(git.show(base, CHANGELOG) or "")}
    after = {s.title: s for s in split_sections(head)}
    # Frozen is a section the base already has and whose tag exists. One that
    # only this change adds is new, also when its tag is older than it.
    released = {tag[1:] for tag in git.tags() if tag_error(tag) is None}
    for version in sorted(before.keys() & released, key=lambda v: tag_key("v" + v)):
        old, new = before[version], after.get(version)
        if new is None or (old.heading, old.body) != (new.heading, new.body):
            result.errors.append(
                f"{CHANGELOG}: [{version}] was released as v{version} and is frozen; "
                "put the change under [Unreleased]."
            )
    result.errors.extend(note_problems(git.notes("HEAD")))
    if hard_only:
        return result

    if entry_missing(git, base, body):
        result.warning = WARNING
    changed = git.changed(git.merge_base(base))
    visible = [p for p in changed if is_shipped(p) and _under(p, MEMBER_FACING) and not p.startswith(NOTES_DIR)]
    if visible and not any(NOTES_DIR + name in changed for name in UNRELEASED_NOTES):
        result.hint = HINT
    return result


def _annotation(kind: str, title: str, message: str) -> str:
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::{kind} title={title}::{escaped}"


def _append(variable: str, text: str) -> None:
    path = os.environ.get(variable)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def report_check(result: CheckResult, comment_file: Path | None) -> int:
    for error in result.errors:
        print(_annotation("error", "Changelog", error))
    if result.warning:
        print(_annotation("warning", "Changelog", result.warning))
    if result.hint:
        print(_annotation("notice", "What's new", result.hint))
    if not (result.errors or result.warning or result.hint):
        print("CHANGELOG.md and the What's new notes are in order.")

    summary = [f"- **Error:** {e}" for e in result.errors]
    summary += [f"- **Warning:** {result.warning}"] if result.warning else []
    summary += [f"- **Hint:** {result.hint}"] if result.hint else []
    if summary:
        _append("GITHUB_STEP_SUMMARY", "## Changelog\n\n" + "\n".join(summary) + "\n")
    if comment_file is not None:
        comment = f"{COMMENT_MARKER}\n**Changelog:** {result.warning}\n" if result.warning else ""
        comment_file.write_text(comment, encoding="utf-8")
    return 1 if result.errors else 0


@dataclass
class ApiCheckResult:
    errors: list[str] = field(default_factory=list)
    notice: str | None = None
    comment: str = ""


def _change_line(change: dict[str, Any]) -> str:
    return f"- `{_where(change)}`: {change.get('text')}"


def api_check(git: Git, spec: str, diff: Diff) -> ApiCheckResult:
    """A pull request against the contract the newest release committed:
    a breaking change fails unless the major went up. Every change the
    schema shows goes into the comment, with the version it leads to."""
    result = ApiCheckResult()
    head = parse_spec(spec, "The API schema")
    tag = newest_tag(git.tags())
    base_text = git.show(tag, OPENAPI) if tag else None
    if base_text is None:
        result.notice = f"No {OPENAPI} at {tag or 'any tag'} yet: the next release writes it, and checks start there."
        return result
    base = parse_spec(base_text, f"{OPENAPI} at {tag}")
    changes = diff(base_text, spec)
    try:
        version = next_api_version(base, head, changes)
    except ReleaseError as error:
        result.errors.extend(error.problems)
        version = None
    if changes:
        outcome = f"The next release makes it API {version}." if version else "This cannot be released as it is."
        result.comment = (
            f"{API_COMMENT_MARKER}\n**API:** changes against the contract of {tag}.\n\n"
            + "\n".join(map(_change_line, changes))
            + f"\n\n{outcome}\n"
        )
    return result


def report_api_check(result: ApiCheckResult, comment_file: Path | None) -> int:
    for error in result.errors:
        print(_annotation("error", "API contract", error))
    if result.notice:
        print(_annotation("notice", "API contract", result.notice))
    if not (result.errors or result.notice):
        print("The API contract holds.")
    if result.errors:
        _append("GITHUB_STEP_SUMMARY", "## API contract\n\n" + "\n".join(f"- {e}" for e in result.errors) + "\n")
    if comment_file is not None:
        comment_file.write_text(result.comment, encoding="utf-8")
    return 1 if result.errors else 0


def _body_file(command: str, cwd: Path) -> str:
    try:
        words = shlex.split(command)
    except ValueError:
        return ""
    for n, word in enumerate(words):
        if word.startswith("--body-file="):
            name = word.split("=", 1)[1]
        elif word in ("--body-file", "-F") and n + 1 < len(words):
            name = words[n + 1]
        else:
            continue
        try:
            return (cwd / name).read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def hook(stdin: str) -> str | None:
    """The PreToolUse decision for a Bash call: a deny as JSON, or None to
    let it through. Anything unexpected lets it through; CI still checks."""
    try:
        event = json.loads(stdin)
        command = str(event["tool_input"]["command"])
        cwd = Path(event.get("cwd") or ".")
    except (ValueError, KeyError, TypeError):
        return None
    if not GH_PR_CREATE.search(command) or OPT_OUT.search(command):
        return None
    try:
        missing = entry_missing(Git(cwd), BASE_BRANCH, _body_file(command, cwd))
    except ReleaseError as error:
        print(f"changelog hook: skipped, {error}", file=sys.stderr)
        return None
    if not missing:
        return None
    reason = (
        "Shipped paths changed without an entry under [Unreleased] in CHANGELOG.md. Write one (see the "
        "changelog skill and docs/releasing.md), or add a line 'No changelog entry: <reason>' to the "
        "pull request body."
    )
    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Releases from CHANGELOG.md (docs/releasing.md)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("decide", help="print route=release|hold|none and tag=, also to $GITHUB_OUTPUT")
    promoting = commands.add_parser("promote", help="promote [Unreleased] to TAG and set versions")
    promoting.add_argument("--tag", required=True)
    commands.add_parser("notes", help="release notes").add_argument("--tag", required=True)
    commands.add_parser("validate-tag", help="exit 1 unless TAG is a CalVer tag").add_argument("tag")
    checking = commands.add_parser("check", help="the pull request check")
    checking.add_argument("--base", required=True, help="the ref the pull request goes into")
    checking.add_argument("--body-file", type=Path, help="the pull request description")
    checking.add_argument("--comment-file", type=Path, help="write the sticky comment here, empty for none")
    checking.add_argument("--hard-only", action="store_true", help="only what fails the check")
    api_checking = commands.add_parser("api-check", help="the pull request check on the API contract")
    api_checking.add_argument("--comment-file", type=Path, help="write the sticky comment here, empty for none")
    for command in (promoting, api_checking):
        command.add_argument("--spec", type=Path, required=True, help="the schema `python -m plak.api.openapi_file` prints")
        command.add_argument("--oasdiff", required=True, help="the oasdiff binary")
    commands.add_parser("hook", help="Claude Code PreToolUse hook, reads the event on stdin")
    args = parser.parse_args(argv)

    if args.command == "hook":
        decision = hook(sys.stdin.read())
        if decision:
            print(decision)
        return 0
    if args.command == "validate-tag":
        error = tag_error(args.tag)
        print(error or f"{args.tag} is a valid CalVer tag.", file=sys.stderr if error else sys.stdout)
        return 1 if error else 0

    try:
        git = Git(Path.cwd())
        if args.command == "decide":
            route, tag = decide(git, today())
            output = f"route={route}\ntag={tag}\n"
            print(output, end="")
            _append("GITHUB_OUTPUT", output)
            return 0
        if args.command == "promote":
            for path in promote(git, args.tag, today(), _read_spec(args.spec), oasdiff(args.oasdiff)):
                print(f"updated {path}")
            return 0
        if args.command == "notes":
            print(release_notes(git, args.tag), end="")
            return 0
        if args.command == "api-check":
            result = api_check(git, _read_spec(args.spec), oasdiff(args.oasdiff))
            return report_api_check(result, args.comment_file)
        body = args.body_file.read_text(encoding="utf-8") if args.body_file else ""
        return report_check(check(git, args.base, body, args.hard_only), args.comment_file)
    except ReleaseError as error:
        for problem in error.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover - the script entry; main() is tested directly
    raise SystemExit(main())
