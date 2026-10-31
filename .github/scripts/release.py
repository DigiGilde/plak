# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Releases from CHANGELOG.md: CalVer tags, component versions, the checks.

    prepare             turn the fragments in changelog.d/ into the next release, staged
    verify-staged --tag T   exit 1 unless the index holds only that release
    decide              what a push to beta does: tag the newest section, or nothing
    notes --tag T       the GitHub Release body for T
    validate-tag T      exit 1 unless T is a CalVer tag
    check --base REF    the pull request check on CHANGELOG.md, changelog.d/ and the notes
    api-check           the pull request check on the API contract
    hook                the Claude Code PreToolUse hook for `gh pr create`
    links               write the link references at the bottom of CHANGELOG.md

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
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

CHANGELOG = "CHANGELOG.md"
HEADINGS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")

TAG_RE = re.compile(r"^v[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}(\.[0-9]+)?$")
RELEASED_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\]$")
UNRELEASED_HEADING = "## [Unreleased]"
# The form before releases wrote headings themselves: `## [v2026.9.1] - 2026-09-01`.
TAGGED_HEADING = re.compile(r"^## \[v[^\]]*\]")
SECTION_TITLE = re.compile(r"^## \[([^\]]+)\]")
LINK_REFERENCE = re.compile(r"^\[[^\]]+\]: \S+$")
AMSTERDAM = ZoneInfo("Europe/Amsterdam")

#: One file per change, turned into the CHANGELOG section by a release.
FRAGMENTS = "changelog.d/"
FRAGMENTS_README = "README.md"
KINDS = {heading.lower(): heading for heading in HEADINGS}
SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
ENTRY_FRAGMENT = re.compile(rf"^(?P<slug>{SLUG})\.(?P<kind>{'|'.join(KINDS)})\.md$")
NOTE_FRAGMENT = re.compile(rf"^(?P<slug>{SLUG})\.note\.(?P<lang>nl|en)\.md$")
FRAGMENT_FORMS = f"<slug>.<kind>.md with kind one of {', '.join(KINDS)}, or <slug>.note.nl.md and <slug>.note.en.md"
PLAIN_FILE = "100644"
NOTHING_TO_RELEASE = f"Nothing to release: {FRAGMENTS} has no entries, and they are the release notes."

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
API_VERSION_LINE = re.compile(r'^(API_VERSION = ")[^"\n]*(")$', re.MULTILINE)
API_VERSION_VALUE = re.compile(r'^API_VERSION = "([^"\n]*)"$', re.MULTILINE)
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
#: oasdiff's level for a breaking change (ERR); WARN is 2 and INFO 1.
BREAKING = 3

NOTES_DIR = "frontend/src/content/releases/"
NOTE_NAME = re.compile(r"^(?P<key>[0-9]{4}\.[0-9]{1,2}\.[0-9]{1,2}(?:\.[0-9]+)?)\.(?P<lang>nl|en)\.md$")
MEMBER_FACING = ("frontend/src/", "cli/plak_cli/")

BASE_BRANCH = "origin/beta"
REPOSITORY = "DigiGilde/plak"
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
    between the brackets, so the version."""
    lines = text.splitlines()
    bottom = _links_start(lines)
    starts = [i for i, line in enumerate(lines[:bottom]) if line.startswith("## ")]
    sections = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else bottom
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


def tag_date(tag: str) -> date:
    return date(*tag_key(tag)[:3])


def changelog_problems(text: str) -> list[str]:
    problems = []
    seen: set[str] = set()
    for section in split_sections(text):
        match = RELEASED_HEADING.match(section.heading)
        if section.heading == UNRELEASED_HEADING:
            problems.append(
                f"{CHANGELOG}: [Unreleased] is gone; a change adds a fragment under {FRAGMENTS} (docs/releasing.md)."
            )
        elif section.title in seen:
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
    if not problems and text != with_links(text):
        problems.append(
            f"{CHANGELOG}: the link references at the bottom are not the ones its headings need; "
            "`release.py links` writes them."
        )
    return problems


def parse_changelog(text: str) -> list[Section]:
    problems = changelog_problems(text)
    if problems:
        raise ReleaseError(*problems)
    return split_sections(text)


def pending_version(sections: list[Section], tags: Iterable[str]) -> str | None:
    """The newest section's version while its tag does not exist: a release
    that is still a pull request, or was merged and not tagged yet."""
    if not sections:
        return None
    version = sections[0].title
    return None if f"v{version}" in set(tags) else version


def release_text(text: str, version: str, body: str) -> str:
    """The changelog with the section for `version`, holding `body`, above
    the newest section. The heading is the version, without the v: the tag
    already is the date."""
    lines = text.splitlines()
    sections = split_sections(text)
    at = sections[0].start if sections else _links_start(lines)
    before = lines[:at]
    if before and before[-1].strip():
        before.append("")
    section = [f"## [{version}]", "", *body.splitlines(), ""]
    return with_links("\n".join([*before, *section, *lines[at:]]))


def _links_start(lines: list[str]) -> int:
    """Where the link references at the bottom begin, blank lines included;
    len(lines) when there are none."""
    start = len(lines)
    while start and (not lines[start - 1].strip() or LINK_REFERENCE.match(lines[start - 1])):
        start -= 1
    return start


def changelog_links(versions: list[str]) -> list[str]:
    """The link references that make the headings links, newest first: a
    compare view with the previous version for each, the tag for the first."""
    if not versions:
        return []
    base = f"https://github.com/{REPOSITORY}"
    links = [f"[{newer}]: {base}/compare/v{older}...v{newer}" for newer, older in pairwise(versions)]
    return [*links, f"[{versions[-1]}]: {base}/releases/tag/v{versions[-1]}"]


def with_links(text: str) -> str:
    """The changelog with its link references written anew from its
    headings."""
    lines = text.splitlines()
    body = "\n".join(lines[: _links_start(lines)]).rstrip("\n")
    links = changelog_links([section.title for section in split_sections(text)])
    return body + "\n" + ("\n" + "\n".join(links) + "\n" if links else "")


# --- Fragments --------------------------------------------------------------


def _item_problems(name: str, text: str) -> list[str]:
    """An entry fragment holds list items and their continuation lines,
    nothing else, and at least one item."""
    problems = []
    items = 0
    in_item = False
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("- "):
            items += 1
            in_item = True
            continue
        if in_item and line[0] in " \t":
            continue
        problems.append(
            f"{FRAGMENTS}{name}: '{line}' is not an entry: every change is a list item starting with '- '."
        )
    if not items:
        problems.append(f"{FRAGMENTS}{name} has no entries.")
    return problems


def fragment_problems(files: dict[str, str], modes: dict[str, str] | None = None) -> list[str]:
    """Everything under changelog.d/ is an entry fragment, a What's new note
    in both languages, or the README, each a plain file. `files` maps a name
    under FRAGMENTS to its text, `modes` to its git mode."""
    problems = []
    notes: dict[str, set[str]] = {}
    for name, text in sorted(files.items()):
        if (modes or {}).get(name, PLAIN_FILE) != PLAIN_FILE:
            problems.append(f"{FRAGMENTS}{name} is not a plain file.")
        elif name == FRAGMENTS_README:
            continue
        elif ENTRY_FRAGMENT.match(name):
            problems.extend(_item_problems(name, text))
        elif match := NOTE_FRAGMENT.match(name):
            notes.setdefault(match["slug"], set()).add(match["lang"])
            if not text.strip():
                problems.append(f"{FRAGMENTS}{name} is empty.")
        else:
            problems.append(f"{FRAGMENTS}{name} is not {FRAGMENT_FORMS}.")
    for slug, languages in sorted(notes.items()):
        if len(languages) == 1:
            [lang] = languages
            other = "en" if lang == "nl" else "nl"
            problems.append(
                f"{FRAGMENTS}{slug}.note.{lang}.md has no {slug}.note.{other}.md: "
                "a What's new note comes in Dutch and English."
            )
    return problems


def release_fragment_problems(files: dict[str, str], modes: dict[str, str] | None = None) -> list[str]:
    """What keeps the fragments from becoming a release: a malformed one, or
    no entries at all. Notes alone are no release."""
    problems = fragment_problems(files, modes)
    if not problems and not any(ENTRY_FRAGMENT.match(name) for name in files):
        problems.append(NOTHING_TO_RELEASE)
    return problems


def fragment_paths(files: dict[str, str]) -> list[str]:
    """Every fragment as a path from the repository root, the README aside."""
    return [FRAGMENTS + name for name in sorted(files) if name != FRAGMENTS_README]


def assemble_section(files: dict[str, str]) -> str:
    """The section body the fragments make: a `### ` block per heading that
    has entries, in HEADINGS order; fragments by name, items in file order."""
    blocks = []
    for kind, heading in KINDS.items():
        items = [
            line
            for name in sorted(files)
            if (match := ENTRY_FRAGMENT.match(name)) and match["kind"] == kind
            for line in files[name].splitlines()
            if line.strip()
        ]
        if items:
            blocks.append("\n".join([f"### {heading}", "", *items]))
    return "\n\n".join(blocks)


def assemble_notes(files: dict[str, str]) -> dict[str, str]:
    """The What's new note per language, the note fragments by name with a
    blank line between them; empty without note fragments."""
    parts: dict[str, list[str]] = {}
    for name in sorted(files):
        if match := NOTE_FRAGMENT.match(name):
            parts.setdefault(match["lang"], []).append(files[name].strip())
    return {lang: "\n\n".join(texts) + "\n" for lang, texts in parts.items()}


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


# --- What's new notes -------------------------------------------------------


def note_problems(notes: dict[str, str]) -> list[str]:
    """Every note is named after its release and comes in Dutch and English,
    both with content or both without. `notes` maps a name under NOTES_DIR
    to its text."""
    pairs: dict[str, dict[str, str]] = {}
    problems = []
    for name, content in sorted(notes.items()):
        match = NOTE_NAME.match(name)
        if match:
            pairs.setdefault(match["key"], {})[match["lang"]] = content
        else:
            problems.append(f"{NOTES_DIR}{name} is not <version>.nl.md or <version>.en.md, the note of a release.")
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

    def tree(self, ref: str, directory: str) -> dict[str, str]:
        """Every file under `directory` at `ref`, by its name below it."""
        names = self.run("ls-tree", "-r", "-z", "--name-only", ref, "--", directory).split("\0")
        return {name[len(directory) :]: self.show(ref, name) or "" for name in names if name}

    def modes(self, ref: str, directory: str) -> dict[str, str]:
        """The git mode of every entry under `directory` at `ref`: 100644 for
        a plain file, 100755 executable, 120000 a symlink, 160000 a submodule."""
        entries = self.run("ls-tree", "-r", "-z", ref, "--", directory).split("\0")
        return {entry.split("\t", 1)[1][len(directory) :]: entry.split(" ", 1)[0] for entry in entries if entry}

    def changes(self, commit: str) -> list[tuple[str, str]]:
        """What `commit` did to its first parent: (status, path), with A, M
        or D as the status."""
        fields = self.run("diff", "--name-status", "--no-renames", "-z", f"{commit}^", commit).split("\0")
        return [(status, path) for status, path in zip(fields[::2], fields[1::2], strict=False) if path]

    def head(self) -> str:
        return self.run("rev-parse", "HEAD").strip()


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


def release_problems(git: Git, ref: str, version: str) -> list[str]:
    """What has to hold at `ref` for it to be the release of `version`: a
    tag that is valid and newer than every other, no fragment left, and
    the version in the files a release writes it into."""
    tag = f"v{version}"
    problems = []
    if error := tag_error(tag):
        problems.append(error)
    elif (newest := newest_tag(git.tags())) and tag_key(tag) <= tag_key(newest):
        problems.append(f"{tag} is not newer than {newest}.")
    problems.extend(f"{path} is still there: a release removes every fragment." for path in fragment_paths(git.tree(ref, FRAGMENTS)))
    for path, version_of in VERSION_OF.items():
        text = git.show(ref, path)
        try:
            found = version_of(text) if text is not None else None
        except (ReleaseError, ValueError):
            found = None
        if found != version:
            problems.append(f"{path} at {ref} does not carry version {version}.")
    return problems


def _is_release_commit(git: Git, commit: str) -> bool:
    """Whether `commit` touches nothing but what a release writes: its files,
    the notes, and fragments it removes."""
    for status, path in git.changes(commit):
        if path in RELEASE_PATHS or path.startswith(NOTES_DIR) or (path.startswith(FRAGMENTS) and status == "D"):
            continue
        return False
    return True


def release_head(git: Git, version: str) -> str:
    """The commit to tag for `version`: the one that added its section, and
    every commit after it on the first-parent line of HEAD that only edits
    the release (a reword in the pull request lands as a commit of its
    own), up to the first that changes anything else."""
    head = git.run("log", "-1", "--format=%H", f"-S## [{version}]", "--", CHANGELOG).strip()
    for commit in git.run("rev-list", "--reverse", "--first-parent", f"{head}..HEAD").split():
        if not _is_release_commit(git, commit):
            break
        head = commit
    return head


def decide(git: Git) -> tuple[str, str, str]:
    """('release', tag, commit) when the newest section of CHANGELOG.md has
    no tag yet, with the last commit of that release; ('none', '', '')
    otherwise. The commit is checked before it is named."""
    sections = split_sections(_read(git, CHANGELOG))
    version = pending_version(sections, git.tags())
    if version is None:
        return "none", "", ""
    commit = release_head(git, version)
    if problems := release_problems(git, commit, version):
        raise ReleaseError(*problems)
    return "release", f"v{version}", commit


def prepare(git: Git, day: date, spec: str, diff: Diff, tag: str | None = None) -> tuple[str, list[str]]:
    """Writes the release into the working tree and stages it: the section
    from the fragments at HEAD, the versions, the notes, the fragments
    removed. Returns the tag and the paths it touched. Refuses before it
    writes anything.

    `spec` is the API schema of HEAD, as plak.api.openapi_file prints it;
    `diff` compares it with the one the previous release committed."""
    tags = git.tags()
    if tag is None:
        tag = next_tag(day, tags)
    elif error := tag_error(tag):
        raise ReleaseError(error)
    text = _read(git, CHANGELOG)
    sections = parse_changelog(text)
    fragments = git.tree("HEAD", FRAGMENTS)
    if problems := release_fragment_problems(fragments, git.modes("HEAD", FRAGMENTS)):
        raise ReleaseError(*problems)
    if pending := pending_version(sections, tags):
        raise ReleaseError(f"v{pending} is waiting for its tag: merge or close that release first.")
    if tag in tags:
        raise ReleaseError(f"The tag {tag} already exists.")
    previous = newest_tag(tags)
    if previous and tag_key(tag) <= tag_key(previous):
        raise ReleaseError(f"{tag} is not newer than {previous}.")
    version = tag[1:]
    if any(section.title == version for section in sections):
        raise ReleaseError(f"{CHANGELOG} already has a section for {tag}.")

    changed = git.changed(previous) if previous else []
    writes = {CHANGELOG: release_text(text, version, assemble_section(fragments))}
    if previous is None or component_changed(git, previous, changed, CLI_PATHS):
        writes[CLI_PYPROJECT] = set_pyproject_version(_read(git, CLI_PYPROJECT), version)
        writes[CLI_LOCK] = set_lock_version(_read(git, CLI_LOCK), version)
    if previous is None or component_changed(git, previous, changed, PLUGIN_PATHS):
        writes[PLUGIN_MANIFEST] = set_plugin_version(_read(git, PLUGIN_MANIFEST), version)
    writes[PUBLICCODE] = set_publiccode(_read(git, PUBLICCODE), version, tag_date(tag))
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
    for lang, note in assemble_notes(fragments).items():
        path = f"{NOTES_DIR}{version}.{lang}.md"
        if git.show("HEAD", path) is not None:
            raise ReleaseError(f"{path} already exists.")
        writes[path] = note

    for path, content in writes.items():
        (git.root / path).parent.mkdir(parents=True, exist_ok=True)
        (git.root / path).write_text(content, encoding="utf-8")
    git.run("add", "--", *writes)
    removed = fragment_paths(fragments)
    git.run("rm", "-q", "--", *removed)
    return tag, [*writes, *removed]


def verify_staged(git: Git, tag: str) -> list[str]:
    """What the job that commits checks first: what is staged is what
    prepare writes for `tag` on HEAD, and nothing else. The job that wrote
    it ran the dependencies, so its patch is not trusted."""
    if error := tag_error(tag):
        return [error]
    if problems := _tag_problems(tag, git.tags()):
        return problems
    staged = _staged_modes(git)
    if not staged:
        return ["Nothing is staged to release."]
    fragments = git.tree("HEAD", FRAGMENTS)
    if problems := release_fragment_problems(fragments, git.modes("HEAD", FRAGMENTS)):
        return problems
    version = tag[1:]
    day = tag_date(tag)
    api = _staged_api_version(git, problems)
    writes: dict[str, Callable[[str], str]] = {
        CHANGELOG: lambda text: release_text(text, version, assemble_section(fragments)),
        CLI_PYPROJECT: lambda text: set_pyproject_version(text, version),
        CLI_LOCK: lambda text: set_lock_version(text, version),
        PLUGIN_MANIFEST: lambda text: set_plugin_version(text, version),
        PUBLICCODE: lambda text: set_publiccode(text, version, day),
        BACKEND_PYPROJECT: lambda text: set_pyproject_version(text, version, BACKEND_PYPROJECT),
        BACKEND_LOCK: lambda text: set_lock_version(text, version, "plak-api", BACKEND_LOCK),
        FRONTEND_PACKAGE: lambda text: set_package_version(text, version),
        FRONTEND_LOCK: lambda text: set_package_version(text, version, FRONTEND_LOCK),
        API_VERSION_FILE: lambda text: set_api_version(text, api or ""),
    }
    creates = {f"{NOTES_DIR}{version}.{lang}.md": note for lang, note in assemble_notes(fragments).items()}
    removes = set(fragment_paths(fragments))
    # A new schema version without a staged API_VERSION has to be the one
    # the code already has.
    if api and API_VERSION_FILE not in staged:
        staged[API_VERSION_FILE] = PLAIN_FILE
    for path, mode in staged.items():
        before, after = git.show("HEAD", path), git.show("", path)
        if mode not in (PLAIN_FILE, "000000"):
            problems.append(f"{path} is not staged as a plain file.")
        elif path == OPENAPI:
            continue
        elif path in writes:
            try:
                expected = writes[path](before) if before is not None else None
            except ReleaseError:
                expected = None
            if after is None or after != expected:
                problems.append(f"{path} is not what the release writes for {tag}.")
        elif path in creates:
            if before is not None or after != creates[path]:
                problems.append(f"{path} is not what the release writes for {tag}.")
        elif path in removes:
            if after is not None:
                problems.append(f"{path} is a fragment the release removes.")
        else:
            problems.append(f"{path} is not a file a release writes.")
    problems.extend(f"{path} is a fragment the release removes." for path in sorted(removes - staged.keys()))
    problems.extend(f"{path} is missing from the release." for path in (*ALWAYS_WRITTEN, *creates) if path not in staged)
    return problems


def _tag_problems(tag: str, tags: Iterable[str]) -> list[str]:
    """A tag the job that ran the dependencies chose: dated today or
    yesterday, and after every tag there is."""
    problems = []
    day, now = tag_date(tag), today()
    if not (now - timedelta(days=1) <= day <= now):
        problems.append(f"{tag} is not dated today or yesterday ({now.isoformat()}).")
    if (newest := newest_tag(tags)) and tag_key(tag) <= tag_key(newest):
        problems.append(f"{tag} is not newer than {newest}.")
    return problems


def _staged_modes(git: Git) -> dict[str, str]:
    """Every staged path with its new mode; 000000 for a removed one."""
    fields = git.run("diff", "--cached", "--raw", "--no-renames", "-z").split("\0")
    return {path: meta.split()[1] for meta, path in zip(fields[::2], fields[1::2], strict=False) if path}


def _staged_api_version(git: Git, problems: list[str]) -> str | None:
    """The API version the staged schema declares, which API_VERSION has to
    match. The schema itself is data the release does not ship."""
    text = git.show("", OPENAPI)
    if text is None or text == git.show("HEAD", OPENAPI):
        return None
    try:
        return "{}.{}.{}".format(*api_version(parse_spec(text, OPENAPI), OPENAPI))
    except ReleaseError as error:
        problems.extend(error.problems)
        return None


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


def release_notes(git: Git, tag: str, image: str | None = None, digest: str | None = None) -> str:
    """The body of the GitHub Release for `tag`, read from the tag itself.
    With the image's name and digest it ends with how to verify it."""
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
    if image or digest:
        lines.append(container_section(image or "", digest or "", version))
    return "\n\n".join(lines) + "\n"


IMAGE_NAME = re.compile(r"^[a-z0-9.-]+(?::[0-9]+)?(?:/[a-z0-9._-]+)+$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def container_section(image: str, digest: str, version: str) -> str:
    if not IMAGE_NAME.match(image) or not DIGEST.match(digest):
        raise ReleaseError(f"'{image}' and '{digest}' are not an image name and a sha256 digest.")
    reference = f"{image}@{digest}"
    return (
        "## Container image\n\n"
        f"- plak: `{image}:{version}`, digest `{digest}`\n\n"
        f"Verify where it was built: `gh attestation verify oci://{reference} -R {REPOSITORY}`\n"
        "Its SBOM (CycloneDX): the same command with `--predicate-type https://cyclonedx.org/bom`"
    )


@dataclass
class CheckResult:
    errors: list[str] = field(default_factory=list)
    warning: str | None = None
    hint: str | None = None


WARNING = (
    f"Shipped paths changed, but no fragment was added under {FRAGMENTS}. Add one for this change "
    "(docs/releasing.md), or put a line starting with 'No changelog entry:' and the reason in the "
    "pull request description."
)
HINT = (
    "frontend/src/ or cli/plak_cli/ changed without a What's new note. Is this visible to a member? "
    f"Then add one: {FRAGMENTS}<slug>.note.nl.md and <slug>.note.en.md."
)


def _fragments_touched(git: Git, changed: list[str], pattern: re.Pattern[str]) -> bool:
    """Whether `changed` holds a fragment of `pattern`'s form that HEAD still
    has, so one added or edited rather than removed."""
    return any(
        pattern.match(path[len(FRAGMENTS) :]) and git.show("HEAD", path) is not None
        for path in changed
        if path.startswith(FRAGMENTS)
    )


def added_sections(git: Git, base: str) -> list[str]:
    """The versions HEAD's CHANGELOG.md has and `base`'s does not, newest
    first: any at all makes this a release pull request."""
    before = {s.title for s in split_sections(git.show(base, CHANGELOG) or "")}
    return [s.title for s in split_sections(git.show("HEAD", CHANGELOG) or "") if s.title not in before]


def entry_missing(git: Git, base: str, body: str = "") -> bool:
    """The warning condition: shipped paths changed since the merge base with
    `base`, no entry fragment came with them, this is no release pull
    request, and `body` does not opt out."""
    changed = git.changed(git.merge_base(base))
    if not any(map(is_shipped, changed)) or added_sections(git, base):
        return False
    return not _fragments_touched(git, changed, ENTRY_FRAGMENT) and not OPT_OUT.search(body)


def check(git: Git, base: str, body: str = "", hard_only: bool = False) -> CheckResult:
    result = CheckResult()
    head = git.show("HEAD", CHANGELOG)
    if head is None:
        result.errors.append(f"{CHANGELOG} is missing.")
        return result
    result.errors.extend(changelog_problems(head))
    result.errors.extend(fragment_problems(git.tree("HEAD", FRAGMENTS), git.modes("HEAD", FRAGMENTS)))
    result.errors.extend(note_problems(git.tree("HEAD", NOTES_DIR)))

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
                f"describe the change in a fragment under {FRAGMENTS} instead."
            )
    # A pull request that adds a section is a release: one section, on top,
    # and the commit has to be releasable as it is.
    added = added_sections(git, base)
    if added and added != [next(iter(after))]:
        result.errors.append(
            f"{CHANGELOG}: a release adds one section, on top; this adds {', '.join(f'[{t}]' for t in added)}."
        )
    elif added and tag_error(f"v{added[0]}") is None:
        result.errors.extend(release_problems(git, "HEAD", added[0]))
    if hard_only:
        return result

    if entry_missing(git, base, body):
        result.warning = WARNING
    changed = git.changed(git.merge_base(base))
    visible = [p for p in changed if is_shipped(p) and _under(p, MEMBER_FACING) and not p.startswith(NOTES_DIR)]
    if visible and not _fragments_touched(git, changed, NOTE_FRAGMENT):
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
        print(f"{CHANGELOG}, {FRAGMENTS} and the What's new notes are in order.")

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


STALE_SCHEMA = (
    f"{OPENAPI} is not the schema this release ships: the API changed after the release was prepared; "
    "prepare it again."
)


def _change_line(change: dict[str, Any]) -> str:
    return f"- `{_where(change)}`: {change.get('text')}"


def _ships_api(git: Git, head: dict[str, Any], version: str) -> bool:
    """Whether HEAD commits the schema of its own code at `version`, in
    backend/openapi.json and in API_VERSION."""
    match = API_VERSION_VALUE.search(git.show("HEAD", API_VERSION_FILE) or "")
    return bool(match) and match.group(1) == version and git.show("HEAD", OPENAPI) == render_spec(head, version)


def api_check(git: Git, spec: str, diff: Diff) -> ApiCheckResult:
    """A pull request against the contract the newest release committed:
    a breaking change fails unless the major went up. Every change the
    schema shows goes into the comment, with the version it leads to. A
    release that is not tagged yet has to ship the schema of its own code."""
    result = ApiCheckResult()
    head = parse_spec(spec, "The API schema")
    tags = git.tags()
    tag = newest_tag(tags)
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
    # Only the release itself ships its schema: an API change queued behind
    # it, or checked between its merge and its tag, is the next release's.
    pending = pending_version(split_sections(git.show("HEAD", CHANGELOG) or ""), tags)
    if version and pending and release_head(git, pending) == git.head() and not _ships_api(git, head, version):
        result.errors.append(STALE_SCHEMA)
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
        f"Shipped paths changed without a changelog fragment. Write {FRAGMENTS}<slug>.<kind>.md (see the "
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
    commands.add_parser("decide", help="print route=release|none, tag= and commit=, also to $GITHUB_OUTPUT")
    preparing = commands.add_parser("prepare", help="stage the release the fragments in changelog.d/ make")
    preparing.add_argument("--tag", help="the tag to release under, instead of today's")
    commands.add_parser("verify-staged", help="exit 1 unless the index holds only the release for TAG").add_argument(
        "--tag", required=True
    )
    noting = commands.add_parser("notes", help="release notes")
    noting.add_argument("--tag", required=True)
    noting.add_argument("--image", help="the image name, for the container section")
    noting.add_argument("--digest", help="the image digest, for the container section")
    commands.add_parser("validate-tag", help="exit 1 unless TAG is a CalVer tag").add_argument("tag")
    checking = commands.add_parser("check", help="the pull request check")
    checking.add_argument("--base", required=True, help="the ref the pull request goes into")
    checking.add_argument("--body-file", type=Path, help="the pull request description")
    checking.add_argument("--comment-file", type=Path, help="write the sticky comment here, empty for none")
    checking.add_argument("--hard-only", action="store_true", help="only what fails the check")
    api_checking = commands.add_parser("api-check", help="the pull request check on the API contract")
    api_checking.add_argument("--comment-file", type=Path, help="write the sticky comment here, empty for none")
    for command in (preparing, api_checking):
        command.add_argument("--spec", type=Path, required=True, help="the schema `python -m plak.api.openapi_file` prints")
        command.add_argument("--oasdiff", required=True, help="the oasdiff binary")
    commands.add_parser("hook", help="Claude Code PreToolUse hook, reads the event on stdin")
    commands.add_parser("links", help="write the link references at the bottom of CHANGELOG.md")
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
            route, tag, commit = decide(git)
            output = f"route={route}\ntag={tag}\ncommit={commit}\n"
            print(output, end="")
            _append("GITHUB_OUTPUT", output)
            return 0
        if args.command == "prepare":
            tag, touched = prepare(git, today(), _read_spec(args.spec), oasdiff(args.oasdiff), args.tag)
            print(f"tag={tag}")
            for path in touched:
                print(f"updated {path}")
            _append("GITHUB_OUTPUT", f"tag={tag}\n")
            return 0
        if args.command == "notes":
            print(release_notes(git, args.tag, args.image, args.digest), end="")
            return 0
        if args.command == "links":
            path = git.root / CHANGELOG
            path.write_text(with_links(_read(git, CHANGELOG)), encoding="utf-8")
            print(f"updated {CHANGELOG}")
            return 0
        if args.command == "verify-staged":
            if problems := verify_staged(git, args.tag):
                raise ReleaseError(*problems)
            print(f"The index holds the release for {args.tag} and nothing else.")
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
