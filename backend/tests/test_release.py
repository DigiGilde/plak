"""The release tooling (.github/scripts/release.py): CHANGELOG.md, the
fragments under changelog.d/, CalVer tags, component versions, the pull
request check and the Claude Code hook.

The git side runs against real repositories in tmp_path, so what is pinned
down here is what git does, not what a mock says it does.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "release.py"


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
NOTES = release.NOTES_DIR
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


def changelog(released: str = "") -> str:
    """A changelog with `released` as its sections, links included."""
    text = "# Changelog\n\nWhat changed, newest first.\n"
    if released:
        text += "\n" + released.strip("\n") + "\n"
    return release.with_links(text)


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
        return release.Git(self.path)

    def prepare(self, tag: str | None = None, day: date = DAY, spec: str | None = None, diff=None) -> str:
        """What a release pull request holds: the release prepared and
        committed, not tagged. Returns the tag."""
        tag, _ = release.prepare(self.handle, day, spec or SPEC, diff or NO_CHANGES, tag)
        self.git("commit", "-q", "-m", f"Release {tag}")
        return tag

    def release(self, tag: str, day: date, spec: str | None = None, diff=None) -> None:
        """The whole cycle: the release prepared, merged and tagged."""
        self.git("tag", self.prepare(tag, day, spec, diff))


@pytest.fixture(autouse=True)
def _git_env(monkeypatch) -> None:
    """No global or system git config: a signing or hook setting on the
    machine must not decide whether these tests pass."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.nl")
    for variable in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Repo:
    """A repository on `beta` with the files a release touches, no tags yet."""
    repo = Repo(tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "beta")
    repo.commit(
        {
            "CHANGELOG.md": changelog(),
            "cli/pyproject.toml": PYPROJECT,
            "cli/uv.lock": LOCK,
            "cli/plak_cli/__init__.py": "x = 1\n",
            "plugin/.claude-plugin/plugin.json": PLUGIN,
            "plugin/skills/plak/SKILL.md": "skill\n",
            "publiccode.yml": PUBLICCODE,
            "backend/src/plak/main.py": MAIN,
            "backend/pyproject.toml": BACKEND_PYPROJECT,
            "backend/uv.lock": BACKEND_LOCK,
            "frontend/package.json": PACKAGE,
            "frontend/package-lock.json": PACKAGE_LOCK,
            "README.md": "readme\n",
        },
        "Start",
    )
    monkeypatch.chdir(repo.path)
    return repo


@pytest.fixture
def released(repo) -> Repo:
    """The same repository after its first release, v2026.9.1."""
    repo.commit(FRAGMENT)
    repo.release("v2026.9.1", date(2026, 9, 1))
    return repo


def _versions(repo: Repo) -> tuple[str, str]:
    return (
        release.pyproject_version(repo.read("cli/pyproject.toml")),
        json.loads(repo.read("plugin/.claude-plugin/plugin.json"))["version"],
    )


def _image_versions(repo: Repo) -> set[str]:
    """Every version the image's packages carry: the backend project and
    its lock entry, package.json and both places in package-lock.json."""
    lock = repo.read("backend/uv.lock")
    package_lock = json.loads(repo.read("frontend/package-lock.json"))
    return {
        release.pyproject_version(repo.read("backend/pyproject.toml"), "backend/pyproject.toml"),
        lock.split('name = "plak-api"\nversion = "')[1].split('"')[0],
        json.loads(repo.read("frontend/package.json"))["version"],
        package_lock["version"],
        package_lock["packages"][""]["version"],
    }


class TestTags:
    @pytest.mark.parametrize("tag", ["v2026.10.1", "v2026.1.5", "v2026.10.1.1", "v2026.12.31.12"])
    def test_calver_tags_pass(self, tag):
        assert release.tag_error(tag) is None

    @pytest.mark.parametrize(
        ("tag", "reason"),
        [
            ("2026.10.1", "not a CalVer tag"),
            ("v2026.10", "not a CalVer tag"),
            ("v26.10.1", "not a CalVer tag"),
            ("v2026.10.1-rc1", "not a CalVer tag"),
            ("v2026.01.5", "leading zero"),
            ("v2026.10.1.01", "leading zero"),
            ("v2026.10.1.0", "counts from .1"),
            ("v2026.2.30", "not name a real date"),
            ("v2026.13.1", "not name a real date"),
        ],
    )
    def test_anything_else_is_refused_with_the_reason(self, tag, reason):
        assert reason in release.tag_error(tag)

    def test_the_first_release_of_a_day_has_no_suffix(self):
        assert release.next_tag(DAY, []) == TAG

    def test_a_second_release_the_same_day_counts_from_one(self):
        assert release.next_tag(DAY, [TAG]) == "v2026.10.1.1"
        assert release.next_tag(DAY, [TAG, "v2026.10.1.1"]) == "v2026.10.1.2"

    def test_tags_outside_the_scheme_do_not_count(self):
        assert release.next_tag(DAY, ["v1.0.0", "latest", "v2027.01.01"]) == TAG

    def test_tags_compare_as_numbers_not_as_text(self):
        assert release.newest_tag(["v2026.10.1", "v2026.9.30", "v2026.10.1.2", "v2026.10.1.10"]) == "v2026.10.1.10"
        assert release.newest_tag(["latest"]) is None

    def test_a_date_behind_the_newest_tag_is_refused(self):
        """Forward only: a runner with a wrong clock must not cut a tag that
        sorts before the release that is already out."""
        with pytest.raises(release.ReleaseError, match=re.escape("not newer than v2026.10.2")):
            release.next_tag(DAY, ["v2026.10.2"])

    def test_the_tag_names_its_date(self):
        assert release.tag_date("v2026.10.1.3") == DAY

    def test_today_is_the_date_in_amsterdam_not_in_utc(self, monkeypatch):
        """Half past eleven at night UTC is already the next day here."""

        class LateEvening(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 30, 23, 30, tzinfo=UTC).astimezone(tz)

        monkeypatch.setattr(release, "datetime", LateEvening)
        assert release.today() == date(2026, 10, 1)

    def test_calver_versions_are_pep_440_and_sort_above_the_old_ones(self):
        assert Version("2026.10.1") > Version("0.3.1")
        assert Version("2026.10.1.1") > Version("2026.10.1")
        assert Version("2026.10.1") > Version("2026.9.30")


class TestChangelogLinks:
    """Keep a Changelog headings are links: `## [2026.10.1]` needs a
    `[2026.10.1]: <url>` at the bottom, or it shows its brackets."""

    BASE = "https://github.com/DigiGilde/plak"

    def test_each_version_compares_with_the_one_before_and_the_first_is_its_tag(self):
        assert release.changelog_links(["2026.10.4.1", "2026.10.4", "2026.9.30"]) == [
            f"[2026.10.4.1]: {self.BASE}/compare/v2026.10.4...v2026.10.4.1",
            f"[2026.10.4]: {self.BASE}/compare/v2026.9.30...v2026.10.4",
            f"[2026.9.30]: {self.BASE}/releases/tag/v2026.9.30",
        ]

    def test_without_a_release_there_is_nothing_to_link(self):
        assert release.changelog_links([]) == []
        assert release.with_links("# Changelog\n\n[stale]: https://example.org\n") == "# Changelog\n"

    def test_the_links_are_written_anew_and_nothing_else_moves(self):
        stale = changelog("## [2026.9.1]\n\n- Old.").replace("/releases/tag/v2026.9.1", "/releases/tag/v2026.1.1")
        fresh = release.with_links(stale)
        assert fresh == changelog("## [2026.9.1]\n\n- Old.")
        assert release.with_links(fresh) == fresh

    def test_the_links_are_no_part_of_the_last_section(self):
        """So the oldest release's notes and its freeze ignore them."""
        sections = release.split_sections(changelog("## [2026.9.1]\n\n- Old."))
        assert sections[-1].body == "- Old."

    @pytest.mark.parametrize(
        "edit",
        [
            lambda text: text[: text.index("\n[2026.9.1]: ")] + "\n",
            lambda text: text.replace("/releases/tag/v2026.9.1", "/releases/tag/v2026.9.2"),
            lambda text: text + "[Unreleased]: https://example.org\n",
        ],
        ids=["missing", "wrong", "extra"],
    )
    def test_links_that_do_not_follow_the_headings_fail_the_check(self, edit):
        text = edit(changelog("## [2026.9.1]\n\n- Old."))
        assert release.changelog_problems(text) == [
            "CHANGELOG.md: the link references at the bottom are not the ones its headings need; "
            "`release.py links` writes them."
        ]

    def test_main_writes_them(self, repo, capsys):
        (repo.path / "CHANGELOG.md").write_text(
            changelog("## [2026.9.1]\n\n- Old.").split("\n[2026.9.1]: ")[0] + "\n", encoding="utf-8"
        )
        assert release.main(["links"]) == 0
        assert capsys.readouterr().out == "updated CHANGELOG.md\n"
        assert repo.read("CHANGELOG.md") == changelog("## [2026.9.1]\n\n- Old.")


class TestChangelogStructure:
    def test_a_full_changelog_is_valid(self):
        text = changelog(
            "## [2026.10.1]\n\n### Added\n\n- One line,\n  wrapped onto a second.\n\n### Security\n\n- Another.\n\n"
            "## [2026.9.1]\n\nA paragraph before the list.\n\n### Fixed\n\n- Bump.\n\n## [2026.8.31.1]\n\n- Old."
        )
        assert release.changelog_problems(text) == []

    def test_a_changelog_without_a_release_yet_is_valid(self):
        assert release.changelog_problems(changelog()) == []

    def test_the_changelog_of_this_repository_is_valid(self):
        assert release.changelog_problems((ROOT / "CHANGELOG.md").read_text(encoding="utf-8")) == []

    @pytest.mark.parametrize(
        "sections",
        ["## [Unreleased]", "## [Unreleased]\n\n### Added\n\n- x\n\n## [2026.9.1]\n\n- y"],
        ids=["alone", "above-a-release"],
    )
    def test_an_unreleased_section_is_refused(self, sections):
        """Changes wait in changelog.d/, not in the changelog."""
        assert release.changelog_problems(changelog(sections)) == [
            "CHANGELOG.md: [Unreleased] is gone; a change adds a fragment under changelog.d/ (docs/releasing.md)."
        ]

    @pytest.mark.parametrize(
        ("heading", "reason"),
        [
            ("## 2026.9.1", "is not '## [YYYY.M.D]' or '## [YYYY.M.D.N]'"),
            ("## [2026.9.1] - 2026-09-01", "is not '## [YYYY.M.D]' or '## [YYYY.M.D.N]'"),
            ("## [2026.09.1]", "'## [2026.09.1]': 'v2026.09.1' has a leading zero"),
            ("## [2026.2.30]", "'## [2026.2.30]': 'v2026.2.30' does not name a real date"),
            ("## [1.0.0]", "'## [1.0.0]': 'v1.0.0' is not a CalVer tag"),
        ],
    )
    def test_a_malformed_heading_is_refused(self, heading, reason):
        [problem] = release.changelog_problems(changelog(f"{heading}\n\n- x"))
        assert reason in problem

    @pytest.mark.parametrize("heading", ["## [v2026.9.1] - 2026-09-01", "## [v2026.9.1]"])
    def test_the_old_heading_with_the_tag_and_a_date_is_refused(self, heading):
        """The version is the date already; the old form repeated it."""
        [problem] = release.changelog_problems(changelog(f"{heading}\n\n- x"))
        assert problem == (
            f"CHANGELOG.md: '{heading}' is the old form; write '## [2026.9.1]', "
            "the version without the v and without a date."
        )

    def test_a_version_twice_is_refused(self):
        section = "## [2026.9.1]\n\n- x\n"
        [problem] = release.changelog_problems(changelog(section + "\n" + section))
        assert "'## [2026.9.1]' appears twice" in problem

    def test_parse_raises_with_every_problem(self):
        with pytest.raises(release.ReleaseError) as refused:
            release.parse_changelog(changelog("## 2026.9.1\n\n- x\n\n## [1.0.0]\n\n- y"))
        assert len(refused.value.problems) == 2


class TestPendingVersion:
    """The newest section whose tag does not exist is a release on its way."""

    def test_the_newest_section_without_a_tag_is_pending(self):
        sections = release.split_sections(changelog("## [2026.10.1]\n\n- x\n\n## [2026.9.1]\n\n- y"))
        assert release.pending_version(sections, ["v2026.9.1"]) == "2026.10.1"

    def test_a_tagged_newest_section_is_not(self):
        sections = release.split_sections(changelog("## [2026.10.1]\n\n- x\n\n## [2026.9.1]\n\n- y"))
        assert release.pending_version(sections, ["v2026.9.1", "v2026.10.1"]) is None

    def test_without_sections_nothing_is_pending(self):
        assert release.pending_version([], []) is None


class TestReleaseText:
    def test_the_section_goes_above_the_newest_one(self):
        text = release.release_text(changelog("## [2026.9.1]\n\n- Old."), "2026.10.1", ENTRY)
        assert text == changelog(f"## [2026.10.1]\n\n{ENTRY}\n\n## [2026.9.1]\n\n- Old.")
        assert release.changelog_problems(text) == []

    def test_the_first_section_follows_the_intro_with_a_blank_line(self):
        assert release.release_text(changelog(), "2026.10.1", ENTRY) == changelog(f"## [2026.10.1]\n\n{ENTRY}")

    def test_an_empty_changelog_gets_the_section_alone(self):
        assert release.release_text("", "2026.10.1", "- x") == (
            "## [2026.10.1]\n\n- x\n\n[2026.10.1]: https://github.com/DigiGilde/plak/releases/tag/v2026.10.1\n"
        )


class TestFragments:
    FORMS = (
        "is not <slug>.<kind>.md with kind one of added, changed, deprecated, removed, fixed, security, "
        "or <slug>.note.nl.md and <slug>.note.en.md."
    )

    def test_entries_notes_and_the_readme_are_fine(self):
        files = {
            "README.md": "What goes here.\n",
            "site-id.added.md": "- One,\n  wrapped onto a second line.\n\n- Two.\n\t  tabbed continuation.\n",
            "a1.changed.md": "- x\n",
            "b.deprecated.md": "- x\n",
            "c.removed.md": "- x\n",
            "d.fixed.md": "- x\n",
            "e.security.md": "- x\n",
            "site-id.note.nl.md": "## Kop\n\nTekst.\n",
            "site-id.note.en.md": "## Heading\n\nText.\n",
        }
        assert release.fragment_problems(files) == []
        assert release.release_fragment_problems(files) == []

    def test_the_fragments_of_this_repository_are_valid(self):
        git = release.Git(ROOT)
        files, modes = git.tree("HEAD", release.FRAGMENTS), git.modes("HEAD", release.FRAGMENTS)
        assert release.fragment_problems(files, modes) == []

    @pytest.mark.parametrize(
        ("name", "mode"),
        [("x.added.md", "120000"), ("x.added.md", "100755"), ("x.added.md", "160000"), ("README.md", "100755")],
        ids=["symlink", "executable", "submodule", "readme-executable"],
    )
    def test_anything_but_a_plain_file_is_refused(self, name, mode):
        """A symlink's text is its target, so nothing else is said about it."""
        files = {name: "../../etc/passwd", "y.fixed.md": "- y\n"}
        assert release.fragment_problems(files, {name: mode, "y.fixed.md": "100644"}) == [
            f"changelog.d/{name} is not a plain file."
        ]

    @pytest.mark.parametrize(
        "name",
        [
            "Site-Id.added.md",
            "site_id.added.md",
            "-site.added.md",
            "site-.added.md",
            "site.improved.md",
            "site.note.md",
            "site.note.de.md",
            "site.added.txt",
            "site.added",
            "notes.md",
            ".gitkeep",
            "sub/site.added.md",
            "sub/README.md",
        ],
    )
    def test_a_name_in_neither_form_is_refused(self, name):
        assert release.fragment_problems({name: "- x\n"}) == [f"changelog.d/{name} {self.FORMS}"]

    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ("### Added\n\n- x\n", "'### Added' is not an entry: every change is a list item starting with '- '."),
            ("- x\n\nSome prose.\n", "'Some prose.' is not an entry"),
            ("  indented, no entry above\n- x\n", "'  indented, no entry above' is not an entry"),
            ("<!-- a comment -->\n- x\n", "'<!-- a comment -->' is not an entry"),
            ("- x\n#### Deeper\n", "'#### Deeper' is not an entry"),
        ],
    )
    def test_anything_but_list_items_is_refused(self, text, reason):
        [problem] = release.fragment_problems({"x.fixed.md": text})
        assert problem.startswith("changelog.d/x.fixed.md: ")
        assert reason in problem

    @pytest.mark.parametrize("text", ["", "  \n\n"])
    def test_an_entry_fragment_without_an_entry_is_refused(self, text):
        assert release.fragment_problems({"x.fixed.md": text}) == ["changelog.d/x.fixed.md has no entries."]

    @pytest.mark.parametrize(
        ("files", "problem"),
        [
            ({"x.note.nl.md": "Nieuw"}, "x.note.nl.md has no x.note.en.md: " + BOTH_LANGUAGES),
            ({"x.note.en.md": "New"}, "x.note.en.md has no x.note.nl.md: " + BOTH_LANGUAGES),
            ({"x.note.nl.md": " \n", "x.note.en.md": "New"}, "x.note.nl.md is empty."),
        ],
        ids=["no-english", "no-dutch", "empty"],
    )
    def test_a_note_comes_in_both_languages_with_content(self, files, problem):
        assert release.fragment_problems(files) == [f"changelog.d/{problem}"]

    @pytest.mark.parametrize(
        "files",
        [{}, {"README.md": "x"}, {"x.note.nl.md": "a", "x.note.en.md": "b"}],
        ids=["nothing", "readme", "notes-only"],
    )
    def test_without_entries_there_is_nothing_to_release(self, files):
        assert release.release_fragment_problems(files) == [
            "Nothing to release: changelog.d/ has no entries, and they are the release notes."
        ]

    def test_a_malformed_fragment_is_the_problem_not_the_emptiness(self):
        assert release.release_fragment_problems({"bad.md": "- x"}) == [f"changelog.d/bad.md {self.FORMS}"]

    def test_the_paths_leave_the_readme_out(self):
        assert release.fragment_paths({"b.added.md": "", "README.md": "", "a.note.nl.md": ""}) == [
            "changelog.d/a.note.nl.md",
            "changelog.d/b.added.md",
        ]

    def test_the_section_follows_the_headings_then_the_file_names(self):
        files = {
            "README.md": "ignored\n",
            "b.added.md": "- B.\n",
            "a.added.md": "- A,\n  continued.\n\n\n- A2.\n",
            "x.security.md": "- X.\n",
            "m.fixed.md": "- M.\n",
            "a.note.nl.md": "ignored here\n",
            "a.note.en.md": "ignored here\n",
        }
        assert release.assemble_section(files) == (
            "### Added\n\n- A,\n  continued.\n- A2.\n- B.\n\n### Fixed\n\n- M.\n\n### Security\n\n- X."
        )

    def test_the_notes_are_joined_by_name_with_a_blank_line_between(self):
        files = {
            "b.note.nl.md": "## B\n\nTekst.\n",
            "a.note.nl.md": "\n## A\n\nTekst A.",
            "a.note.en.md": "## A\n\nText A.\n\n",
            "b.note.en.md": "## B\n\nText.\n",
            "a.added.md": "- x\n",
        }
        assert release.assemble_notes(files) == {
            "nl": "## A\n\nTekst A.\n\n## B\n\nTekst.\n",
            "en": "## A\n\nText A.\n\n## B\n\nText.\n",
        }
        assert release.assemble_notes({"a.added.md": "- x\n"}) == {}


class TestShippedPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "backend/src/plak/main.py",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "backend/alembic/versions/0001_base.py",
            "backend/alembic.ini",
            "frontend/src/App.vue",
            "frontend/src/content/releases/2026.10.1.nl.md",
            "frontend/package.json",
            "frontend/package-lock.json",
            "frontend/index.html",
            "frontend/vite.config.ts",
            "frontend/public/favicon.svg",
            "containers/plak/Containerfile",
            "cli/plak_cli/__init__.py",
            "cli/pyproject.toml",
            "cli/uv.lock",
            "plugin/skills/plak-publish/SKILL.md",
            "plugin/.claude-plugin/plugin.json",
            "actions/publish/action.yml",
        ],
    )
    def test_what_ends_up_in_a_release_counts(self, path):
        assert release.is_shipped(path)

    @pytest.mark.parametrize(
        "path",
        [
            "plugin/evals/publish/prompt.md",
            "backend/tests/test_serving.py",
            "cli/tests/test_publish.py",
            "frontend/src/App.test.ts",
            "frontend/src/format.spec.ts",
            "frontend/src/components/__snapshots__/Card.snap",
            "frontend/tests/dutch-literals.test.ts",
            "backend/src/plak/test_helpers.py",
            "backend/src/plak/conftest.py",
            "containers/nginx-dev/Containerfile",
            "backend/pyproject.toml.orig",
            "README.md",
            "CHANGELOG.md",
            "changelog.d/site-id.added.md",
            "docs/releasing.md",
            ".github/workflows/ci.yml",
        ],
    )
    def test_tests_docs_and_tooling_do_not(self, path):
        assert not release.is_shipped(path)

    def test_every_shipped_path_exists_in_this_repository(self):
        """A path that was moved away would silently stop counting."""
        missing = [path for path in release.SHIPPED_PATHS + release.NOT_SHIPPED if not (ROOT / path).exists()]
        assert missing == []


class TestVersionEdits:
    def test_only_the_project_version_changes_in_pyproject(self):
        edited = release.set_pyproject_version(PYPROJECT, "2026.10.1")
        assert edited == PYPROJECT.replace('name = "plak"\nversion = "0.1.0"', 'name = "plak"\nversion = "2026.10.1"')
        assert release.pyproject_version(edited) == "2026.10.1"

    @pytest.mark.parametrize(
        ("text", "reason"),
        [("[tool.x]\nversion = \"1\"\n", r"no \[project\] table"), ('[project]\nname = "plak"\n', "no version")],
    )
    def test_a_pyproject_without_a_project_version_is_refused(self, text, reason):
        with pytest.raises(release.ReleaseError, match=reason):
            release.set_pyproject_version(text, "2026.10.1")
        with pytest.raises(release.ReleaseError, match=reason):
            release.pyproject_version(text)

    def test_only_the_root_package_changes_in_the_lock(self):
        edited = release.set_lock_version(LOCK, "2026.10.1")
        assert edited == LOCK.replace('"plak"\nversion = "0.1.0"', '"plak"\nversion = "2026.10.1"')
        assert 'name = "httpx"\nversion = "0.28.1"' in edited

    @pytest.mark.parametrize("text", ["version = 1\n", LOCK + LOCK[LOCK.index("[[package]]\nname = \"plak\"") :]])
    def test_a_lock_without_exactly_one_root_package_is_refused(self, text):
        with pytest.raises(release.ReleaseError, match="expected one editable package 'plak'"):
            release.set_lock_version(text, "2026.10.1")

    def test_the_plugin_manifest_keeps_its_formatting(self):
        edited = release.set_plugin_version(PLUGIN, "2026.10.1")
        assert edited == PLUGIN.replace('"version": "0.3.1"', '"version": "2026.10.1"')

    @pytest.mark.parametrize("text", ['{\n  "name": "plak"\n}\n', '{\n  "a": {\n  "version": "1"\n}}\n'])
    def test_a_plugin_manifest_without_a_top_level_version_is_refused(self, text):
        with pytest.raises(release.ReleaseError, match="no top-level"):
            release.set_plugin_version(text, "2026.10.1")

    def test_a_pyproject_refusal_names_the_file(self):
        with pytest.raises(release.ReleaseError, match=r"^backend/pyproject\.toml: no version"):
            release.set_pyproject_version('[project]\nname = "plak-api"\n', "2026.10.1", "backend/pyproject.toml")

    def test_the_lock_edit_picks_the_named_root_package(self):
        edited = release.set_lock_version(BACKEND_LOCK, "2026.10.1", "plak-api", "backend/uv.lock")
        assert edited == BACKEND_LOCK.replace('"plak-api"\nversion = "0.1.0"', '"plak-api"\nversion = "2026.10.1"')
        with pytest.raises(release.ReleaseError, match=r"^backend/uv\.lock: expected one editable package 'plak-api'"):
            release.set_lock_version(LOCK, "2026.10.1", "plak-api", "backend/uv.lock")

    def test_package_json_keeps_its_formatting(self):
        edited = release.set_package_version(PACKAGE, "2026.10.1")
        assert edited == PACKAGE.replace('"version": "0.1.0"', '"version": "2026.10.1"')

    def test_package_lock_gets_the_version_twice_and_leaves_the_dependencies(self):
        edited = release.set_package_version(PACKAGE_LOCK, "2026.10.1", "frontend/package-lock.json")
        assert edited == PACKAGE_LOCK.replace('"version": "0.1.0"', '"version": "2026.10.1"')
        assert '"node_modules/vue": {\n      "version": "3.5.13"' in edited

    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ('{\n  "name": "plak-frontend"\n}\n', 'no top-level "version"'),
            ('{\n  "a": {\n  "version": "1"\n}}\n', 'no top-level "version"'),
            (PACKAGE_LOCK.replace('      "version": "0.1.0",\n', ""), 'no "version" line in the root package'),
        ],
        ids=["no-version", "nested-version", "lock-without-root-version"],
    )
    def test_a_package_file_without_its_version_lines_is_refused(self, text, reason):
        with pytest.raises(release.ReleaseError, match="^" + re.escape(f"frontend/package-lock.json: {reason}")):
            release.set_package_version(text, "2026.10.1", "frontend/package-lock.json")

    def test_publiccode_gets_the_version_and_the_date(self):
        edited = release.set_publiccode(PUBLICCODE, "2026.10.1", DAY)
        assert 'softwareVersion: "2026.10.1"\nreleaseDate: "2026-10-01"\n' in edited
        assert "license: EUPL-1.2" in edited

    def test_publiccode_without_the_lines_is_refused(self):
        with pytest.raises(release.ReleaseError, match="no releaseDate line"):
            release.set_publiccode('softwareVersion: "0.0.0"\n', "2026.10.1", DAY)

    def test_the_real_files_can_be_edited(self):
        """The edits are text edits on a format uv, Claude Code and
        publiccode own; this notices the day one of them changes shape."""
        pyproject = (ROOT / "cli/pyproject.toml").read_text(encoding="utf-8")
        lock = (ROOT / "cli/uv.lock").read_text(encoding="utf-8")
        plugin = (ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8")
        publiccode = (ROOT / "publiccode.yml").read_text(encoding="utf-8")

        assert release.pyproject_version(release.set_pyproject_version(pyproject, "2026.10.1")) == "2026.10.1"
        lock_diff = set(release.set_lock_version(lock, "2026.10.1").splitlines()) ^ set(lock.splitlines())
        assert lock_diff == {'version = "2026.10.1"', f'version = "{release.pyproject_version(pyproject)}"'}
        assert json.loads(release.set_plugin_version(plugin, "2026.10.1"))["version"] == "2026.10.1"
        assert 'softwareVersion: "2026.10.1"' in release.set_publiccode(publiccode, "2026.10.1", DAY)

        backend = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
        backend_lock = (ROOT / "backend/uv.lock").read_text(encoding="utf-8")
        package = (ROOT / "frontend/package.json").read_text(encoding="utf-8")
        package_lock = (ROOT / "frontend/package-lock.json").read_text(encoding="utf-8")

        edited = release.set_pyproject_version(backend, "2026.10.1", "backend/pyproject.toml")
        assert release.pyproject_version(edited, "backend/pyproject.toml") == "2026.10.1"
        lock_diff = set(release.set_lock_version(backend_lock, "2026.10.1", "plak-api").splitlines())
        lock_diff ^= set(backend_lock.splitlines())
        assert lock_diff == {'version = "2026.10.1"', f'version = "{release.pyproject_version(backend)}"'}
        assert json.loads(release.set_package_version(package, "2026.10.1"))["version"] == "2026.10.1"
        edited = release.set_package_version(package_lock, "2026.10.1", "frontend/package-lock.json")
        lock = json.loads(edited)
        assert (lock["version"], lock["packages"][""]["version"]) == ("2026.10.1", "2026.10.1")
        assert sum(a != b for a, b in zip(edited.splitlines(), package_lock.splitlines(), strict=True)) == 2

    def test_the_versions_the_real_files_carry_can_be_read_back(self):
        """What decide and check read is what prepare wrote."""
        for path, version_of in release.VERSION_OF.items():
            found = version_of((ROOT / path).read_text(encoding="utf-8"))
            assert isinstance(found, str) and found, path


class TestNotes:
    def test_pairs_in_both_languages_are_fine(self):
        notes = {"2026.9.1.nl.md": "a", "2026.9.1.en.md": "b", "2026.10.4.1.nl.md": "", "2026.10.4.1.en.md": " \n"}
        assert release.note_problems(notes) == []

    @pytest.mark.parametrize(
        ("notes", "reason"),
        [
            ({"2026.9.1.nl.md": "Nieuw"}, "2026.9.1.nl.md has no 2026.9.1.en.md"),
            ({"2026.9.1.en.md": "New"}, "2026.9.1.en.md has no 2026.9.1.nl.md"),
            ({"2026.9.1.nl.md": "Nieuw", "2026.9.1.en.md": "\n"}, "one is empty and the other is not"),
        ],
    )
    def test_a_note_in_one_language_is_refused(self, notes, reason):
        [problem] = release.note_problems(notes)
        assert problem.startswith(release.NOTES_DIR)
        assert reason in problem

    @pytest.mark.parametrize(
        "name", ["unreleased.nl.md", "index.ts", "README.md", "v2026.9.1.nl.md", "2026.9.1.de.md", "sub/2026.9.1.nl.md"]
    )
    def test_anything_not_named_after_a_release_is_refused(self, name):
        """The same rule the frontend test holds the directory to."""
        assert release.note_problems({name: "x", "2026.9.1.nl.md": "a", "2026.9.1.en.md": "b"}) == [
            f"frontend/src/content/releases/{name} is not <version>.nl.md or <version>.en.md, the note of a release."
        ]


class TestReleaseProblems:
    """What a commit has to look like to be tagged as a release."""

    @pytest.fixture
    def pending(self, released) -> Repo:
        """A release prepared on top of v2026.9.1 and committed, not tagged."""
        released.commit(FRAGMENT)
        released.prepare(TAG)
        return released

    def test_a_prepared_release_passes(self, pending):
        assert release.release_problems(pending.handle, "HEAD", "2026.10.1") == []

    @pytest.mark.parametrize(
        ("files", "path"),
        [
            ({"backend/pyproject.toml": '[project]\nname = "plak-api"\n'}, "backend/pyproject.toml"),
            ({"backend/pyproject.toml": BACKEND_PYPROJECT}, "backend/pyproject.toml"),
            ({"frontend/package.json": "{"}, "frontend/package.json"),
            ({"frontend/package.json": "[]\n"}, "frontend/package.json"),
            ({"frontend/package.json": PACKAGE}, "frontend/package.json"),
            ({"publiccode.yml": "name: Plak\n"}, "publiccode.yml"),
            ({"publiccode.yml": "softwareVersion: 2026.9.1\n"}, "publiccode.yml"),
        ],
        ids=[
            "pyproject-without-version",
            "pyproject-old-version",
            "package-not-json",
            "package-not-an-object",
            "package-old-version",
            "publiccode-without-line",
            "publiccode-old-version",
        ],
    )
    def test_a_version_file_that_does_not_carry_the_version_fails(self, pending, files, path):
        pending.commit(files)
        assert release.release_problems(pending.handle, "HEAD", "2026.10.1") == [
            f"{path} at HEAD does not carry version 2026.10.1."
        ]

    def test_a_version_file_that_is_missing_fails(self, pending):
        pending.remove("publiccode.yml")
        assert release.release_problems(pending.handle, "HEAD", "2026.10.1") == [
            "publiccode.yml at HEAD does not carry version 2026.10.1."
        ]

    def test_a_fragment_left_behind_fails(self, pending):
        pending.commit({"changelog.d/late.added.md": "- Late.\n", "changelog.d/README.md": "Fine here.\n"})
        assert release.release_problems(pending.handle, "HEAD", "2026.10.1") == [
            "changelog.d/late.added.md is still there: a release removes every fragment."
        ]

    def test_a_version_behind_the_newest_tag_fails(self, released):
        problems = release.release_problems(released.handle, "HEAD", "2026.8.1")
        assert problems[0] == "v2026.8.1 is not newer than v2026.9.1."

    def test_a_version_that_is_no_calver_fails(self, released):
        problems = release.release_problems(released.handle, "HEAD", "Unreleased")
        assert problems[0].startswith("'vUnreleased' is not a CalVer tag")


class TestDecide:
    def test_without_a_section_there_is_nothing_to_tag(self, repo):
        assert release.decide(repo.handle) == ("none", "", "")

    def test_a_tagged_newest_section_releases_nothing(self, released):
        """Also what a rerun of the workflow sees after the tag went out."""
        assert release.decide(released.handle) == ("none", "", "")

    def test_a_change_without_a_release_releases_nothing(self, released):
        released.commit({"backend/src/plak/main.py": "app = 2\n", **FRAGMENT})
        assert release.decide(released.handle) == ("none", "", "")

    def test_an_untagged_newest_section_is_tagged_at_the_commit_that_added_it(self, released):
        released.commit(FRAGMENT)
        released.prepare(TAG)
        commit = released.head()
        released.commit({"README.md": "later\n"})
        assert release.decide(released.handle) == ("release", TAG, commit)

    def test_an_edit_in_the_pull_request_moves_the_tag_to_the_edit(self, released):
        """beta merges by rebase, so a reword made in the pull request lands
        as a commit of its own after the release commit; the tag has to
        carry it."""
        released.commit(FRAGMENT)
        released.prepare(TAG)
        released.commit({"CHANGELOG.md": released.read("CHANGELOG.md").replace("Something new", "Something newer")})
        released.commit({NOTES + "2026.10.1.nl.md": "Nieuw.\n", NOTES + "2026.10.1.en.md": "New.\n"})
        edit = released.head()
        released.commit({"README.md": "later\n"})
        assert release.decide(released.handle) == ("release", TAG, edit)

    def test_a_commit_that_adds_or_edits_a_fragment_ends_the_release(self, released):
        released.commit(FRAGMENT)
        released.prepare(TAG)
        commit = released.head()
        reworded = released.read("CHANGELOG.md") + "\n"
        released.commit({"changelog.d/late.added.md": "- Late.\n", "CHANGELOG.md": reworded})
        assert release.release_head(released.handle, "2026.10.1") == commit
        released.commit({"changelog.d/late.added.md": "- Later.\n"})
        assert release.release_head(released.handle, "2026.10.1") == commit

    def test_a_commit_that_removes_a_fragment_is_part_of_the_release(self, released):
        """A fragment that reached beta after the release was prepared, with
        the release rebased on top of it: taken out in the pull request, its
        line added to the section, and the tag carries that commit."""
        released.commit(FRAGMENT)
        released.prepare(TAG)
        release_commit = released.head()
        released.git("switch", "-q", "--detach", "HEAD^")
        released.commit({"changelog.d/late.added.md": "- Late.\n"})
        released.git("cherry-pick", release_commit)
        released.git("rm", "-q", "changelog.d/late.added.md")
        with_late = released.read("CHANGELOG.md").replace("- Something new.", "- Something new.\n- Late.")
        released.commit({"CHANGELOG.md": with_late})
        released.git("switch", "-q", "-C", "beta")
        assert release.decide(released.handle) == ("release", TAG, released.head())

    def test_a_fragment_added_after_the_release_is_the_next_releases(self, released):
        """It ends the release, which is tagged without it."""
        released.commit(FRAGMENT)
        released.prepare(TAG)
        commit = released.head()
        released.commit({"changelog.d/late.added.md": "- Late.\n"})
        assert release.decide(released.handle) == ("release", TAG, commit)

    def test_a_release_commit_that_is_not_releasable_fails(self, released):
        """The job goes red instead of tagging a commit that left a fragment
        behind or forgot a version."""
        released.commit({"CHANGELOG.md": changelog(f"## [2026.10.1]\n\n- Hand made.\n\n## [2026.9.1]\n\n{ENTRY}")})
        with pytest.raises(release.ReleaseError) as refused:
            release.decide(released.handle)
        assert refused.value.problems[0].startswith("backend/pyproject.toml at ")
        assert refused.value.problems[0].endswith(" does not carry version 2026.10.1.")

    def test_a_malformed_newest_heading_fails(self, released):
        released.commit({"CHANGELOG.md": changelog(f"## [Unreleased]\n\n- x\n\n## [2026.9.1]\n\n{ENTRY}")})
        with pytest.raises(release.ReleaseError, match="'vUnreleased' is not a CalVer tag"):
            release.decide(released.handle)

    def test_main_prints_the_route_and_writes_the_step_output(self, released, monkeypatch, tmp_path, capsys):
        released.commit(FRAGMENT)
        released.prepare(TAG)
        commit = released.head()
        output = tmp_path / "output"
        output.write_text("earlier=1\n")
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))

        assert release.main(["decide"]) == 0
        assert capsys.readouterr().out == f"route=release\ntag={TAG}\ncommit={commit}\n"
        assert output.read_text() == f"earlier=1\nroute=release\ntag={TAG}\ncommit={commit}\n"

    def test_main_without_a_step_output_only_prints(self, repo, capsys):
        assert release.main(["decide"]) == 0
        assert capsys.readouterr().out == "route=none\ntag=\ncommit=\n"

    def test_main_outside_a_repository_fails(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert release.main(["decide"]) == 1
        assert capsys.readouterr().err.startswith("error: git rev-parse --show-toplevel:")


class TestPrepare:
    def test_the_fragments_become_the_section_for_the_tag(self, repo):
        older = "## [2026.9.1]\n\n### Fixed\n\n- Old."
        repo.commit({"CHANGELOG.md": changelog(older), "changelog.d/README.md": "Read docs/releasing.md.\n"})
        repo.git("tag", "v2026.9.1")
        repo.commit({**FRAGMENT, "changelog.d/a-fix.fixed.md": "- Fixed it,\n  at last.\n"})

        tag, touched = release.prepare(repo.handle, DAY, SPEC, NO_CHANGES)

        assert tag == TAG
        assert repo.read("CHANGELOG.md") == (
            "# Changelog\n\nWhat changed, newest first.\n\n"
            f"## [2026.10.1]\n\n{ENTRY}\n\n### Fixed\n\n- Fixed it,\n  at last.\n\n{older}\n\n"
            "[2026.10.1]: https://github.com/DigiGilde/plak/compare/v2026.9.1...v2026.10.1\n"
            "[2026.9.1]: https://github.com/DigiGilde/plak/releases/tag/v2026.9.1\n"
        )
        assert release.changelog_problems(repo.read("CHANGELOG.md")) == []
        staged = repo.git("diff", "--cached", "--name-only").split()
        assert sorted(staged) == sorted(touched)
        assert sorted(os.listdir(repo.path / "changelog.d")) == ["README.md"]
        assert "D  changelog.d/something-new.added.md" in repo.git("status", "--porcelain").splitlines()

    def test_the_tag_is_todays_unless_given(self, released):
        released.commit(FRAGMENT)
        tag, _ = release.prepare(released.handle, DAY, SPEC, NO_CHANGES, "v2026.10.5")
        assert tag == "v2026.10.5"
        assert "## [2026.10.5]" in released.read("CHANGELOG.md")
        assert 'softwareVersion: "2026.10.5"\nreleaseDate: "2026-10-05"' in released.read("publiccode.yml")

    def test_the_first_release_sets_every_component_version(self, repo):
        repo.commit(FRAGMENT)
        release.prepare(repo.handle, DAY, SPEC, NO_CHANGES)

        assert _versions(repo) == ("2026.10.1", "2026.10.1")
        assert 'name = "plak"\nversion = "2026.10.1"' in repo.read("cli/uv.lock")
        assert 'softwareVersion: "2026.10.1"\nreleaseDate: "2026-10-01"' in repo.read("publiccode.yml")
        assert _image_versions(repo) == {"2026.10.1"}

    def test_a_backend_change_leaves_the_cli_and_plugin_versions(self, released):
        released.commit({**FRAGMENT, "backend/src/plak/app.py": "app = 2\n"})
        _, touched = release.prepare(released.handle, DAY, SPEC, NO_CHANGES)

        assert _versions(released) == ("2026.9.1", "2026.9.1")
        assert sorted(touched) == [
            "CHANGELOG.md",
            "backend/openapi.json",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "changelog.d/something-new.added.md",
            "frontend/package-lock.json",
            "frontend/package.json",
            "publiccode.yml",
        ]
        assert 'softwareVersion: "2026.10.1"' in released.read("publiccode.yml")

    def test_the_image_gets_every_release_also_when_only_the_cli_changed(self, released):
        """Every tag builds and ships a new image, whatever changed."""
        released.commit({**FRAGMENT, "cli/plak_cli/__init__.py": "x = 2\n"})
        release.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert _image_versions(released) == {"2026.10.1"}
        assert 'name = "plak"\nversion = "9.9.9"' in released.read("backend/uv.lock")

    def test_a_missing_image_package_file_stops_the_release(self, released):
        released.remove("frontend/package-lock.json")
        released.commit(FRAGMENT)
        with pytest.raises(release.ReleaseError, match=re.escape("frontend/package-lock.json is missing")):
            release.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert released.git("status", "--porcelain") == ""

    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            ({"cli/plak_cli/__init__.py": "x = 2\n"}, ("2026.10.1", "2026.9.1")),
            ({"cli/pyproject.toml": PYPROJECT.replace('["httpx"]', '["httpx", "keyring"]')}, ("2026.10.1", "2026.9.1")),
            ({"cli/uv.lock": LOCK.replace("0.28.1", "0.28.2")}, ("2026.10.1", "2026.9.1")),
            ({"plugin/skills/plak/SKILL.md": "better\n"}, ("2026.9.1", "2026.10.1")),
            ({"cli/tests/test_x.py": "def test(): pass\n"}, ("2026.9.1", "2026.9.1")),
            ({"plugin/evals/x/prompt.md": "zet dit online\n"}, ("2026.9.1", "2026.9.1")),
        ],
        ids=["cli-code", "cli-dependency", "cli-lock", "plugin-skill", "cli-test-only", "plugin-evals-only"],
    )
    def test_a_component_gets_a_new_version_only_when_it_changed(self, released, files, expected):
        released.commit({**FRAGMENT, **files})
        release.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert _versions(released) == expected

    def test_a_version_line_changed_by_hand_is_no_change(self, released):
        """Only the line a release writes itself is left out of the
        comparison; everything around it still counts."""
        released.commit(
            {
                **FRAGMENT,
                "cli/pyproject.toml": release.set_pyproject_version(released.read("cli/pyproject.toml"), "9.9.9"),
                "cli/uv.lock": release.set_lock_version(released.read("cli/uv.lock"), "9.9.9"),
                "plugin/.claude-plugin/plugin.json": release.set_plugin_version(
                    released.read("plugin/.claude-plugin/plugin.json"), "9.9.9"
                ),
            }
        )
        release.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert _versions(released) == ("9.9.9", "9.9.9")

    def test_a_file_that_did_not_exist_at_the_last_tag_is_a_change(self, repo):
        section = "## [2026.9.1]\n\n- x"
        repo.remove("cli/uv.lock")
        repo.commit({"CHANGELOG.md": changelog(section)})
        repo.git("tag", "v2026.9.1")
        repo.commit({**FRAGMENT, "cli/uv.lock": LOCK})
        release.prepare(repo.handle, DAY, SPEC, NO_CHANGES)
        assert _versions(repo) == ("2026.10.1", "0.3.1")

    def test_the_fragments_are_read_from_the_commit_not_the_working_tree(self, released):
        """A fragment that is not committed is not released, and an ignored
        file next to the fragments plays no part."""
        released.commit({".gitignore": ".DS_Store\nuncommitted.added.md\n"})
        released.write({"changelog.d/uncommitted.added.md": "- Not yet.\n", "changelog.d/.DS_Store": "junk"})
        with pytest.raises(release.ReleaseError, match="Nothing to release"):
            release.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        released.commit(FRAGMENT)
        _, touched = release.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert "changelog.d/something-new.added.md" in touched
        assert "Not yet." not in released.read("CHANGELOG.md")
        assert (released.path / "changelog.d" / ".DS_Store").exists()

    @pytest.mark.parametrize("mode", ["symlink", "executable"])
    def test_a_fragment_that_is_not_a_plain_file_is_refused(self, released, mode):
        released.commit(FRAGMENT)
        if mode == "symlink":
            os.symlink("../README.md", released.path / "changelog.d" / "link.fixed.md")
            released.commit()
            name = "link.fixed.md"
        else:
            released.git("update-index", "--chmod=+x", "changelog.d/something-new.added.md")
            released.git("commit", "-q", "-m", "Mode")
            name = "something-new.added.md"
        with pytest.raises(release.ReleaseError) as refused:
            release.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert refused.value.problems == [f"changelog.d/{name} is not a plain file."]

    def test_a_shipped_change_without_an_entry_is_refused_and_writes_nothing(self, released):
        """Without entries there are no release notes, so nothing is made up
        from commit subjects either."""
        released.commit({"backend/src/plak/main.py": "app = 2\n"}, "Update urllib3 to 2.8.0")
        with pytest.raises(release.ReleaseError) as refused:
            release.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert refused.value.problems == [
            "Nothing to release: changelog.d/ has no entries, and they are the release notes."
        ]
        assert released.git("status", "--porcelain") == ""

    def test_without_a_tag_or_entries_there_is_nothing_to_release(self, repo):
        with pytest.raises(release.ReleaseError, match="Nothing to release"):
            release.prepare(repo.handle, DAY, SPEC, NO_CHANGES)

    @pytest.mark.parametrize(
        ("files", "tag", "reason"),
        [
            (NOTE_FRAGMENTS, None, "Nothing to release"),
            ({"changelog.d/bad name.md": "- x\n"}, None, "changelog.d/bad name.md is not <slug>"),
            ({"changelog.d/sub/x.added.md": "- x\n"}, None, "changelog.d/sub/x.added.md is not <slug>"),
            ({"changelog.d/x.added.md": "Prose.\n"}, None, "'Prose.' is not an entry"),
            ({**FRAGMENT, "changelog.d/x.note.nl.md": "a\n"}, None, "x.note.nl.md has no x.note.en.md"),
            (FRAGMENT, "v2026.10.01", "leading zero"),
            (FRAGMENT, "v2026.9.1", "already exists"),
            (FRAGMENT, "v2026.8.1", "not newer than v2026.9.1"),
        ],
        ids=[
            "notes-only",
            "bad-name",
            "subdirectory",
            "bad-content",
            "unpaired-note",
            "invalid-tag",
            "tag-exists",
            "older-tag",
        ],
    )
    def test_refusals_write_nothing(self, released, files, tag, reason):
        released.commit(files)
        with pytest.raises(release.ReleaseError, match=re.escape(reason)):
            release.prepare(released.handle, DAY, SPEC, NO_CHANGES, tag)
        assert released.git("status", "--porcelain") == ""

    def test_a_release_waiting_for_its_tag_blocks_the_next(self, released):
        released.commit(FRAGMENT)
        released.prepare(TAG)
        released.commit({"changelog.d/next.added.md": "- Next.\n"})
        with pytest.raises(release.ReleaseError) as refused:
            release.prepare(released.handle, date(2026, 10, 2), SPEC, NO_CHANGES)
        assert refused.value.problems == ["v2026.10.1 is waiting for its tag: merge or close that release first."]
        assert released.git("status", "--porcelain") == ""

    def test_a_section_for_the_tag_already_in_the_changelog_is_refused(self, repo):
        """Even one further down, which no tag answers to yet."""
        repo.commit({"CHANGELOG.md": changelog("## [2026.9.1]\n\n- y\n\n## [2026.10.1]\n\n- x"), **FRAGMENT})
        repo.git("tag", "v2026.9.1")
        with pytest.raises(release.ReleaseError, match=f"already has a section for {TAG}"):
            release.prepare(repo.handle, DAY, SPEC, NO_CHANGES, TAG)

    def test_a_refusal_writes_nothing(self, repo):
        repo.commit(FRAGMENT)
        repo.remove("publiccode.yml")
        with pytest.raises(release.ReleaseError, match=re.escape("publiccode.yml is missing")):
            release.prepare(repo.handle, DAY, SPEC, NO_CHANGES)
        assert repo.git("status", "--porcelain") == ""

    def test_the_note_fragments_become_the_notes_of_the_version(self, repo):
        repo.commit(
            {
                **FRAGMENT,
                "changelog.d/b.note.nl.md": "## B\n\nTekst.\n",
                "changelog.d/a.note.nl.md": "## A\n\nTekst A.",
                "changelog.d/a.note.en.md": "## A\n\nText A.\n\n",
                "changelog.d/b.note.en.md": "## B\n\nText.\n",
            }
        )
        _, touched = release.prepare(repo.handle, DAY, SPEC, NO_CHANGES)

        assert sorted(os.listdir(repo.path / NOTES)) == ["2026.10.1.en.md", "2026.10.1.nl.md"]
        assert repo.read(NOTES + "2026.10.1.nl.md") == "## A\n\nTekst A.\n\n## B\n\nTekst.\n"
        assert repo.read(NOTES + "2026.10.1.en.md") == "## A\n\nText A.\n\n## B\n\nText.\n"
        status = repo.git("status", "--porcelain").splitlines()
        assert f"A  {NOTES}2026.10.1.nl.md" in status
        assert "D  changelog.d/a.note.nl.md" in status
        assert {NOTES + "2026.10.1.nl.md", NOTES + "2026.10.1.en.md", "changelog.d/b.note.en.md"} <= set(touched)
        assert not (repo.path / "changelog.d").exists()

    def test_without_note_fragments_there_are_no_notes(self, repo):
        repo.commit(FRAGMENT)
        _, touched = release.prepare(repo.handle, DAY, SPEC, NO_CHANGES)
        assert not (repo.path / NOTES).exists()
        assert not any(path.startswith(NOTES) for path in touched)

    def test_a_note_for_the_version_that_exists_already_is_refused(self, repo):
        repo.commit({**FRAGMENT, **NOTE_FRAGMENTS, NOTES + "2026.10.1.nl.md": "a\n", NOTES + "2026.10.1.en.md": "b\n"})
        with pytest.raises(release.ReleaseError, match=re.escape(f"{NOTES}2026.10.1.en.md already exists.")):
            release.prepare(repo.handle, DAY, SPEC, NO_CHANGES)
        assert repo.git("status", "--porcelain") == ""

    def test_main_prints_the_tag_first_and_then_what_it_touched(self, repo, monkeypatch, tmp_path, capsys):
        repo.commit(FRAGMENT)
        monkeypatch.setattr(release, "today", lambda: DAY)
        output = tmp_path / "output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        (tmp_path / "openapi.json").write_text(SPEC, encoding="utf-8")
        args = ["prepare", "--spec", str(tmp_path / "openapi.json"), "--oasdiff", "unused"]
        assert release.main(args) == 0
        out = capsys.readouterr().out.splitlines()
        assert out[0] == f"tag={TAG}"
        assert "updated CHANGELOG.md" in out
        assert "updated backend/openapi.json" in out
        assert "updated changelog.d/something-new.added.md" in out
        assert output.read_text() == f"tag={TAG}\n"

    def test_main_takes_the_tag(self, repo, tmp_path, capsys):
        repo.commit(FRAGMENT)
        (tmp_path / "openapi.json").write_text(SPEC, encoding="utf-8")
        args = ["prepare", "--tag", "v2026.10.5", "--spec", str(tmp_path / "openapi.json"), "--oasdiff", "unused"]
        assert release.main(args) == 0
        assert capsys.readouterr().out.startswith("tag=v2026.10.5\n")

    def test_main_reports_a_refusal(self, repo, capsys):
        assert release.main(["prepare", "--spec", "x", "--oasdiff", "x"]) == 1
        assert "error: x: cannot read the API schema." in capsys.readouterr().err

    def test_main_reports_a_refused_tag(self, repo, tmp_path, capsys):
        (tmp_path / "openapi.json").write_text(SPEC, encoding="utf-8")
        args = ["prepare", "--tag", "v2026.1", "--spec", str(tmp_path / "openapi.json"), "--oasdiff", "x"]
        assert release.main(args) == 1
        assert "error: 'v2026.1' is not a CalVer tag" in capsys.readouterr().err


class TestReleaseNotes:
    def test_the_body_is_the_section_with_the_component_versions(self, released):
        released.commit({"changelog.d/a-fix.fixed.md": "- A fix.\n", "cli/plak_cli/__init__.py": "x = 2\n"})
        released.release(TAG, DAY)

        assert release.release_notes(released.handle, TAG) == (
            "### Fixed\n\n- A fix.\n\nCLI 2026.10.1\n\nPlugin unchanged (2026.9.1)\n\nAPI 1.0.0\n"
        )

    def test_it_reads_the_tag_not_the_working_tree(self, released):
        released.commit({"CHANGELOG.md": changelog("## [2026.9.1]\n\n- Reworded.")})
        assert release.release_notes(released.handle, "v2026.9.1").startswith(ENTRY + "\n\nCLI 2026.9.1")

    @pytest.mark.parametrize("tag", ["v2026.9.2", "v2026.1"], ids=["no-such-tag", "invalid"])
    def test_a_tag_without_a_section_is_refused(self, released, tag):
        with pytest.raises(release.ReleaseError):
            release.release_notes(released.handle, tag)

    def test_an_empty_section_is_refused(self, repo):
        repo.commit({"CHANGELOG.md": changelog("## [2026.9.1]\n")})
        repo.git("tag", "v2026.9.1")
        with pytest.raises(release.ReleaseError, match=re.escape("No section for v2026.9.1")):
            release.release_notes(repo.handle, "v2026.9.1")

    def test_a_tag_without_the_manifests_is_refused(self, repo):
        repo.remove("plugin/.claude-plugin/plugin.json")
        repo.commit({"CHANGELOG.md": changelog("## [2026.9.1]\n\n- x")})
        repo.git("tag", "v2026.9.1")
        with pytest.raises(release.ReleaseError, match=re.escape("is missing at v2026.9.1")):
            release.release_notes(repo.handle, "v2026.9.1")

    def test_main_prints_the_notes(self, released, capsys):
        assert release.main(["notes", "--tag", "v2026.9.1"]) == 0
        assert capsys.readouterr().out.endswith("CLI 2026.9.1\n\nPlugin 2026.9.1\n\nAPI 1.0.0\n")

    def test_with_the_image_it_ends_with_how_to_verify_it(self, released):
        digest = "sha256:" + "a" * 64
        notes = release.release_notes(released.handle, "v2026.9.1", "ghcr.io/digigilde/plak", digest)
        assert notes.endswith(
            "API 1.0.0\n\n## Container image\n\n"
            f"- plak: `ghcr.io/digigilde/plak:2026.9.1`, digest `{digest}`\n\n"
            f"Verify where it was built: `gh attestation verify oci://ghcr.io/digigilde/plak@{digest} "
            "-R DigiGilde/plak`\n"
            "Its SBOM (CycloneDX): the same command with `--predicate-type https://cyclonedx.org/bom`\n"
        )

    @pytest.mark.parametrize(
        ("image", "digest"),
        [
            ("ghcr.io/digigilde/plak", None),
            (None, "sha256:" + "a" * 64),
            ("ghcr.io/DigiGilde/plak", "sha256:" + "a" * 64),
            ("ghcr.io/digigilde/plak`; rm", "sha256:" + "a" * 64),
            ("ghcr.io/digigilde/plak", "sha256:abc"),
        ],
        ids=["no-digest", "no-image", "upper-case", "markdown-break", "short-digest"],
    )
    def test_an_image_or_digest_that_is_not_one_is_refused(self, released, image, digest):
        """Both come from the build job; whatever else lands in the notes
        is refused rather than published."""
        with pytest.raises(release.ReleaseError, match="not an image name and a sha256 digest"):
            release.release_notes(released.handle, "v2026.9.1", image, digest)

    def test_main_prints_the_container_section(self, released, capsys):
        digest = "sha256:" + "b" * 64
        args = ["notes", "--tag", "v2026.9.1", "--image", "ghcr.io/digigilde/plak", "--digest", digest]
        assert release.main(args) == 0
        assert f"oci://ghcr.io/digigilde/plak@{digest}" in capsys.readouterr().out

    def test_a_tag_from_before_the_schema_has_no_api_line(self, repo):
        """v2026.9.30 was tagged by hand, before releases wrote the schema."""
        repo.commit({"CHANGELOG.md": changelog("## [2026.9.1]\n\n- x")})
        repo.git("tag", "v2026.9.1")
        assert release.release_notes(repo.handle, "v2026.9.1").endswith("Plugin unchanged (0.3.1)\n")


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


class TestApiVersion:
    def test_the_first_contract_keeps_the_version_the_code_has(self):
        assert release.next_api_version(None, json.loads(spec_with("1.4.2")), []) == "1.4.2"

    def test_an_unchanged_schema_keeps_the_released_version(self):
        """Minor and patch belong to the release: a hand edit in the code
        is not what decides them."""
        base, head = json.loads(spec_with("1.3.0")), json.loads(spec_with("1.9.9"))
        assert release.next_api_version(base, head, []) == "1.3.0"

    def test_a_change_oasdiff_does_not_list_is_a_patch(self):
        base, head = json.loads(spec_with("1.3.2")), json.loads(spec_with(description="Clearer."))
        assert release.next_api_version(base, head, []) == "1.3.3"

    @pytest.mark.parametrize("level", [1, 2], ids=["info", "warn"])
    def test_a_change_oasdiff_lists_as_compatible_is_a_minor(self, level):
        base, head = json.loads(spec_with("1.3.2")), json.loads(spec_with(paths=("/a", "/b")))
        assert release.next_api_version(base, head, [change(level)]) == "1.4.0"

    def test_a_breaking_change_without_a_new_major_is_refused(self):
        base, head = json.loads(spec_with("1.3.2")), json.loads(spec_with(paths=()))
        with pytest.raises(release.ReleaseError) as raised:
            release.next_api_version(base, head, [change(3, "api path removed", "/a", "POST"), change(1)])
        assert raised.value.problems == [
            "Breaking API change: POST /a: api path removed.",
            "A breaking change needs a new major: serve the API under /-/api/v2 and set API_VERSION in "
            "backend/src/plak/main.py to '2.0.0' (docs/publishing.md).",
        ]

    def test_a_change_without_an_operation_names_its_section(self):
        problems = release.breaking_problems([{"level": 3, "text": "t", "section": "components"}], 1)
        assert problems[0] == "Breaking API change: components: t."

    def test_a_new_major_starts_at_zero_and_may_break(self):
        base, head = json.loads(spec_with("1.3.2")), json.loads(spec_with("2.0.0", paths=()))
        assert release.next_api_version(base, head, [change(3)]) == "2.0.0"
        assert release.next_api_version(base, json.loads(spec_with("2.7.1")), []) == "2.0.0"

    def test_a_major_below_the_released_one_is_refused(self):
        with pytest.raises(release.ReleaseError, match="has major 1, below the released 2"):
            release.next_api_version(json.loads(spec_with("2.0.0")), json.loads(spec_with("1.0.0")), [])

    @pytest.mark.parametrize("version", ["1.0", "v1.0.0", "01.0.0", "1.0.0-beta", ""])
    def test_a_version_that_is_not_major_minor_patch_is_refused(self, version):
        with pytest.raises(release.ReleaseError, match=r"is not MAJOR\.MINOR\.PATCH"):
            release.next_api_version(None, json.loads(spec_with(version)), [])
        with pytest.raises(release.ReleaseError, match=r"backend/openapi\.json at the newest tag"):
            release.next_api_version(json.loads(spec_with(version)), json.loads(SPEC), [])

    def test_only_the_api_version_line_changes(self):
        assert release.set_api_version(MAIN, "1.4.0") == MAIN.replace('"1.0.0"', '"1.4.0"')

    @pytest.mark.parametrize("text", ["app = 1\n", MAIN + MAIN], ids=["none", "twice"])
    def test_a_main_without_exactly_one_api_version_is_refused(self, text):
        with pytest.raises(release.ReleaseError, match="expected one API_VERSION line"):
            release.set_api_version(text, "1.4.0")

    def test_the_real_api_version_line_can_be_edited(self):
        text = (ROOT / "backend/src/plak/main.py").read_text(encoding="utf-8")
        assert 'API_VERSION = "9.8.7"\n' in release.set_api_version(text, "9.8.7")

    def test_the_release_writes_the_schema_the_way_the_module_prints_it(self):
        """So the committed file and a fresh print only differ where the
        API differs."""
        from plak.api.openapi_file import render

        spec = json.loads(spec_with(description="Plak publiceert één site."))
        assert release.render_spec(spec, "1.0.0") == render(spec)
        assert json.loads(release.render_spec(spec, "1.2.0"))["info"]["version"] == "1.2.0"

    @pytest.mark.parametrize(("text", "reason"), [("{", "is not JSON"), ("[]", "is not an OpenAPI document")])
    def test_a_schema_that_is_not_a_json_object_is_refused(self, text, reason):
        with pytest.raises(release.ReleaseError, match=f"^The API schema {reason}"):
            release.parse_spec(text, "The API schema")


class TestOasdiff:
    def test_it_compares_the_two_schemas_without_fetching_references(self, tmp_path):
        binary = fake_oasdiff(tmp_path, json.dumps([change(1)]))
        assert release.oasdiff(binary)("base\n", "head\n") == [change(1)]
        log = (tmp_path / "oasdiff.log").read_text(encoding="utf-8").splitlines()
        assert log[0] == "changelog"
        assert log[3:6] == ["--format", "json", "--allow-external-refs=false"]
        assert log[6:] == ["base", "---", "head"]

    def test_no_output_is_no_changes(self, tmp_path):
        assert release.oasdiff(fake_oasdiff(tmp_path, ""))("a", "b") == []

    @pytest.mark.parametrize(
        ("exit_code", "stderr", "reason"),
        [(1, "failed to load base", "oasdiff: failed to load base"), (2, "", "oasdiff: exit 2")],
    )
    def test_a_failure_is_a_refusal(self, tmp_path, exit_code, stderr, reason):
        with pytest.raises(release.ReleaseError, match=f"^{reason}$"):
            release.oasdiff(fake_oasdiff(tmp_path, "", exit_code, stderr))("a", "b")

    def test_output_that_is_not_json_is_a_refusal(self, tmp_path):
        with pytest.raises(release.ReleaseError, match="its output is not JSON"):
            release.oasdiff(fake_oasdiff(tmp_path, "API changes:"))("a", "b")


class TestPrepareTheApi:
    def test_the_first_release_commits_the_schema_at_the_version_the_code_has(self, repo):
        def unused(base, head):
            raise AssertionError("nothing to compare with")

        repo.commit(FRAGMENT)
        release.prepare(repo.handle, DAY, SPEC, unused)
        assert json.loads(repo.read("backend/openapi.json"))["info"]["version"] == "1.0.0"
        assert repo.read("backend/src/plak/main.py") == MAIN

    def test_a_hand_made_tag_without_a_schema_counts_as_the_first(self, repo):
        repo.commit({"CHANGELOG.md": changelog("## [2026.9.1]\n\n- x")})
        repo.git("tag", "v2026.9.1")
        repo.commit(FRAGMENT)
        release.prepare(repo.handle, DAY, SPEC, NO_CHANGES)
        assert json.loads(repo.read("backend/openapi.json"))["info"]["version"] == "1.0.0"

    def test_an_addition_raises_the_minor_in_the_schema_and_the_code(self, released):
        head = spec_with(paths=("/a", "/b"))
        seen = []

        def diff(base, revision):
            seen.append((base, revision))
            return [change(1)]

        released.commit(FRAGMENT)
        release.prepare(released.handle, DAY, head, diff)

        assert seen == [(released.git("show", "v2026.9.1:backend/openapi.json"), head)]
        assert json.loads(released.read("backend/openapi.json")) == {
            **json.loads(head),
            "info": {**json.loads(head)["info"], "version": "1.1.0"},
        }
        assert 'API_VERSION = "1.1.0"' in released.read("backend/src/plak/main.py")

    def test_a_breaking_change_stops_the_release_before_it_writes(self, released):
        released.commit(FRAGMENT)
        with pytest.raises(release.ReleaseError, match="Breaking API change"):
            release.prepare(released.handle, DAY, spec_with(paths=()), lambda b, h: [change(3)])
        assert released.git("status", "--porcelain") == ""

    def test_a_schema_that_is_not_json_stops_the_release(self, repo):
        repo.commit(FRAGMENT)
        with pytest.raises(release.ReleaseError, match="The API schema is not JSON"):
            release.prepare(repo.handle, DAY, "{", NO_CHANGES)

    def test_a_committed_schema_that_is_not_json_stops_the_release(self, released):
        released.commit(FRAGMENT)
        released.git("tag", "-d", "v2026.9.1")
        released.git("checkout", "-q", "HEAD~1")
        released.commit({"backend/openapi.json": "{"})
        released.git("tag", "v2026.9.1")
        released.git("checkout", "-q", "-")
        with pytest.raises(release.ReleaseError, match=r"backend/openapi\.json at v2026\.9\.1 is not JSON"):
            release.prepare(released.handle, DAY, SPEC, NO_CHANGES)


class TestVerifyStaged:
    """The job that commits applies a patch from a job that ran the
    dependencies, then pushes it with the App. So what it commits has to
    be exactly what prepare writes."""

    @pytest.fixture(autouse=True)
    def _today(self, monkeypatch) -> None:
        monkeypatch.setattr(release, "today", lambda: DAY)

    @pytest.fixture
    def prepared(self, released) -> Repo:
        """v2026.9.1 released, then a change with an entry and a note,
        prepared as TAG with an addition to the API."""
        released.commit({**FRAGMENT, **NOTE_FRAGMENTS, "cli/plak_cli/__init__.py": "x = 2\n"})
        release.prepare(released.handle, DAY, spec_with(paths=("/a", "/b")), lambda b, h: [change(1)])
        return released

    def test_what_prepare_writes_passes(self, prepared):
        assert release.verify_staged(prepared.handle, TAG) == []

    def test_a_release_prepared_yesterday_still_passes(self, prepared, monkeypatch):
        """Midnight between the two jobs."""
        monkeypatch.setattr(release, "today", lambda: date(2026, 10, 2))
        assert release.verify_staged(prepared.handle, TAG) == []

    @pytest.mark.parametrize("now", [date(2026, 10, 3), date(2026, 9, 30)], ids=["two-days-later", "tomorrow"])
    def test_a_tag_dated_outside_today_and_yesterday_is_refused(self, prepared, monkeypatch, now):
        """The tag comes from the job that ran the dependencies; one dated
        at will is refused before anything else is looked at."""
        monkeypatch.setattr(release, "today", lambda: now)
        assert release.verify_staged(prepared.handle, TAG) == [
            f"{TAG} is not dated today or yesterday ({now.isoformat()})."
        ]

    def test_a_tag_behind_the_newest_one_is_refused(self, prepared, monkeypatch):
        monkeypatch.setattr(release, "today", lambda: date(2026, 8, 1))
        assert release.verify_staged(prepared.handle, "v2026.8.1") == ["v2026.8.1 is not newer than v2026.9.1."]

    @pytest.mark.parametrize(
        "path",
        ["CHANGELOG.md", "publiccode.yml", "backend/uv.lock", "frontend/package.json", f"{NOTES}2026.10.1.en.md"],
    )
    def test_a_file_the_release_writes_has_to_be_staged(self, prepared, path):
        """A patch that leaves a version or the notes behind is not the
        release either."""
        prepared.git("reset", "-q", "--", path)
        assert release.verify_staged(prepared.handle, TAG) == [f"{path} is missing from the release."]

    def test_the_cli_and_plugin_files_are_only_written_when_they_changed(self, released):
        released.commit({**FRAGMENT, "backend/src/plak/app.py": "app = 2\n"})
        release.prepare(released.handle, DAY, SPEC, NO_CHANGES)
        assert "cli/pyproject.toml" not in release._staged_modes(released.handle)
        assert release.verify_staged(released.handle, TAG) == []

    def test_a_fragment_that_is_not_a_plain_file_is_refused(self, released):
        released.commit(FRAGMENT)
        os.symlink("../README.md", released.path / "changelog.d" / "link.added.md")
        released.git("add", "-A")
        released.git("update-index", "--chmod=+x", "changelog.d/something-new.added.md")
        released.git("commit", "-q", "-m", "Mode")
        released.write({"Makefile": "all:\n"})
        released.git("add", "Makefile")
        assert release.verify_staged(released.handle, TAG) == [
            "changelog.d/link.added.md is not a plain file.",
            "changelog.d/something-new.added.md is not a plain file.",
        ]

    def test_the_patch_survives_the_hand_over_to_a_clean_checkout(self, prepared, tmp_path):
        """What the workflow does between its two jobs: the staged release as
        a patch, applied to a fresh clone of the same commit."""
        patch = tmp_path / "release.patch"
        patch.write_text(prepared.git("diff", "--cached", "--binary", "--no-renames"), encoding="utf-8")
        clean = Repo(tmp_path / "clean")
        subprocess.run(  # noqa: S603
            ["git", "clone", "-q", str(prepared.path), str(clean.path)],  # noqa: S607
            check=True,
            capture_output=True,
        )
        clean.git("apply", "--cached", "--binary", str(patch))
        assert release.verify_staged(clean.handle, TAG) == []
        staged = clean.git("diff", "--cached", "--name-only", "--no-renames").split()
        assert f"{NOTES}2026.10.1.nl.md" in staged
        assert "changelog.d/x.note.nl.md" in staged

    def test_main_says_so(self, prepared, capsys):
        assert release.main(["verify-staged", "--tag", TAG]) == 0
        assert capsys.readouterr().out == f"The index holds the release for {TAG} and nothing else.\n"

    @pytest.mark.parametrize(
        ("path", "text", "problem"),
        [
            (".github/workflows/x.yml", "on: push\n", ".github/workflows/x.yml is not a file a release writes."),
            ("backend/src/plak/app.py", "import os\n", "backend/src/plak/app.py is not a file a release writes."),
            (
                "backend/uv.lock",
                BACKEND_LOCK.replace("version = 1\n", 'version = 1\n\n[[package]]\nname = "evil"\n'),
                "backend/uv.lock is not what the release writes for v2026.10.1.",
            ),
            (
                "backend/src/plak/main.py",
                MAIN.replace("app = 1", "app = __import__('os')"),
                "backend/src/plak/main.py is not what the release writes for v2026.10.1.",
            ),
            ("CHANGELOG.md", changelog("## [2026.10.1]\n\n- Something else."), "CHANGELOG.md is not"),
            (
                ".github/scripts/release.py",
                "def verify_staged(*a): return []\n",
                ".github/scripts/release.py is not a file a release writes.",
            ),
            (
                "cli/pyproject.toml",
                release.set_pyproject_version(PYPROJECT, "9.9.9"),
                "cli/pyproject.toml is not what the release writes for v2026.10.1.",
            ),
            (
                "backend/pyproject.toml",
                BACKEND_PYPROJECT.replace('requires = ["hatchling"]', 'requires = ["hatchling"]\nversion = "x"'),
                "backend/pyproject.toml is not what the release writes for v2026.10.1.",
            ),
            (
                "backend/src/plak/main.py",
                MAIN.replace("1.0.0", "1.2.0"),
                "backend/src/plak/main.py is not what the release writes for v2026.10.1.",
            ),
            (
                f"{NOTES}2026.10.1.nl.md",
                "<script>x</script>\n",
                f"{NOTES}2026.10.1.nl.md is not what the release writes for v2026.10.1.",
            ),
            (f"{NOTES}2026.9.2.en.md", "New.\n", f"{NOTES}2026.9.2.en.md is not a file a release writes."),
            ("changelog.d/evil.added.md", "- Evil.\n", "changelog.d/evil.added.md is not a file a release writes."),
            (
                "changelog.d/something-new.added.md",
                "- Edited, not removed.\n",
                "changelog.d/something-new.added.md is a fragment the release removes.",
            ),
        ],
        ids=[
            "workflow",
            "code",
            "lock-dependency",
            "code-next-to-version",
            "changelog",
            "the-validator",
            "other-version",
            "other-version-line",
            "api-version-not-the-schemas",
            "note-text",
            "note-version",
            "new-fragment",
            "fragment-kept",
        ],
    )
    def test_anything_beyond_the_release_is_refused(self, prepared, path, text, problem):
        prepared.write({path: text})
        prepared.git("add", "--", path)
        problems = release.verify_staged(prepared.handle, TAG)
        assert len(problems) == 1 and problems[0].startswith(problem), problems

    def test_a_fragment_the_patch_leaves_in_place_is_refused(self, prepared):
        prepared.git("reset", "-q", "--", "changelog.d/x.note.nl.md")
        assert release.verify_staged(prepared.handle, TAG) == [
            "changelog.d/x.note.nl.md is a fragment the release removes."
        ]

    def test_a_note_written_over_an_existing_one_is_refused(self, released):
        """prepare refuses when the version's note exists, so a staged one
        is not its work, whatever its text."""
        existing = {NOTES + "2026.10.1.nl.md": "a\n", NOTES + "2026.10.1.en.md": "b\n"}
        released.commit({**FRAGMENT, **NOTE_FRAGMENTS, **existing})
        released.git("rm", "-q", "--cached", *FRAGMENT, *NOTE_FRAGMENTS)
        released.write({NOTES + "2026.10.1.en.md": "New.\n"})
        released.git("add", "--", NOTES + "2026.10.1.en.md")
        problems = release.verify_staged(released.handle, TAG)
        assert problems[0] == f"{NOTES}2026.10.1.en.md is not what the release writes for v2026.10.1."

    def test_an_executable_or_a_symlink_is_refused(self, prepared):
        """Same text, other kind of file: what the release writes is plain."""
        prepared.git("update-index", "--chmod=+x", "publiccode.yml")
        (prepared.path / "CHANGELOG.md").unlink()
        (prepared.path / "CHANGELOG.md").symlink_to("README.md")
        prepared.git("add", "CHANGELOG.md")
        assert sorted(release.verify_staged(prepared.handle, TAG)) == [
            "CHANGELOG.md is not staged as a plain file.",
            "publiccode.yml is not staged as a plain file.",
        ]

    def test_the_schema_has_to_declare_the_version_api_version_gets(self, prepared):
        """API_VERSION follows the staged schema; one without that line
        staged has to match what the code has."""
        prepared.git("reset", "-q", "--", "backend/src/plak/main.py")
        assert release.verify_staged(prepared.handle, TAG) == [
            "backend/src/plak/main.py is not what the release writes for v2026.10.1."
        ]

    def test_a_schema_without_a_semver_version_is_refused(self, prepared):
        prepared.write({"backend/openapi.json": spec_with("one")})
        prepared.git("add", "backend/openapi.json")
        problems = release.verify_staged(prepared.handle, TAG)
        assert problems[0] == "backend/openapi.json: info.version 'one' is not MAJOR.MINOR.PATCH."

    def test_a_file_the_release_removes_has_to_be_a_fragment(self, prepared):
        prepared.git("rm", "-q", "--cached", "README.md")
        assert release.verify_staged(prepared.handle, TAG) == ["README.md is not a file a release writes."]

    def test_a_version_file_that_disappears_is_refused(self, prepared):
        prepared.git("rm", "-q", "--cached", "publiccode.yml")
        assert release.verify_staged(prepared.handle, TAG) == [
            "publiccode.yml is not what the release writes for v2026.10.1."
        ]

    def test_api_version_moves_only_with_the_schema(self, prepared):
        """The schema unstaged, API_VERSION still raised: refused."""
        prepared.git("reset", "-q", "--", "backend/openapi.json")
        assert release.verify_staged(prepared.handle, TAG) == [
            "backend/src/plak/main.py is not what the release writes for v2026.10.1."
        ]

    def test_a_file_prepare_could_not_have_written_is_refused(self, released):
        """HEAD's publiccode.yml has no version lines, so prepare would have
        refused; a staged one is not its work."""
        released.commit({**FRAGMENT, "publiccode.yml": "name: Plak\n"})
        released.git("rm", "-q", "--cached", *FRAGMENT)
        released.write({"publiccode.yml": 'name: Plak\nsoftwareVersion: "2026.10.1"\n'})
        released.git("add", "publiccode.yml")
        problems = release.verify_staged(released.handle, TAG)
        assert problems[0] == "publiccode.yml is not what the release writes for v2026.10.1."

    def test_a_head_prepare_would_refuse_is_refused(self, released):
        """Without entries at HEAD there is no release to compare with."""
        released.write({"Makefile": "all:\n"})
        released.git("add", "Makefile")
        assert release.verify_staged(released.handle, TAG) == [
            "Nothing to release: changelog.d/ has no entries, and they are the release notes."
        ]
        released.commit({"changelog.d/x.improved.md": "- x\n"})
        released.write({"Makefile": "all: build\n"})
        released.git("add", "Makefile")
        [problem] = release.verify_staged(released.handle, TAG)
        assert problem.startswith("changelog.d/x.improved.md is not <slug>")

    def test_the_tag_has_to_be_the_one_prepared(self, prepared, monkeypatch):
        """Another tag gives another changelog section, and notes named
        after another version."""
        monkeypatch.setattr(release, "today", lambda: date(2026, 10, 2))
        problems = release.verify_staged(prepared.handle, "v2026.10.2")
        assert "CHANGELOG.md is not what the release writes for v2026.10.2." in problems
        assert f"{NOTES}2026.10.1.nl.md is not a file a release writes." in problems

    def test_nothing_staged_or_no_calver_tag_is_refused(self, released):
        assert release.verify_staged(released.handle, TAG) == ["Nothing is staged to release."]
        assert release.verify_staged(released.handle, "v2026.1")[0].startswith("'v2026.1' is not a CalVer tag")

    def test_main_fails_with_every_problem(self, prepared, capsys):
        prepared.write({"Makefile": "all:\n"})
        prepared.git("add", "Makefile")
        assert release.main(["verify-staged", "--tag", TAG]) == 1
        assert "error: Makefile is not a file a release writes." in capsys.readouterr().err


class TestApiCheck:
    def test_without_a_released_schema_there_is_nothing_to_hold_to(self, repo):
        result = release.api_check(repo.handle, SPEC, NO_CHANGES)
        assert (result.errors, result.comment) == ([], "")
        assert result.notice == (
            "No backend/openapi.json at any tag yet: the next release writes it, and checks start there."
        )

    def test_a_hand_made_tag_without_a_schema_says_so(self, repo):
        repo.git("tag", "v2026.9.1")
        assert "at v2026.9.1 yet" in release.api_check(repo.handle, SPEC, NO_CHANGES).notice

    def test_an_unchanged_contract_passes_without_a_comment(self, released):
        result = release.api_check(released.handle, SPEC, NO_CHANGES)
        assert (result.errors, result.notice, result.comment) == ([], None, "")

    def test_it_compares_with_the_newest_tag(self, released):
        released.commit(FRAGMENT)
        released.release(TAG, DAY, spec_with(paths=("/a", "/b")), lambda b, h: [change(1)])
        seen = []
        release.api_check(released.handle, SPEC, lambda base, head: seen.append(base) or [])
        assert json.loads(seen[0])["info"]["version"] == "1.1.0"

    def test_an_addition_passes_and_says_what_the_next_release_makes_of_it(self, released):
        result = release.api_check(released.handle, spec_with(paths=("/a", "/b")), lambda b, h: [change(1)])
        assert result.errors == []
        assert result.comment == (
            "<!-- plak-api-check -->\n**API:** changes against the contract of v2026.9.1.\n\n"
            "- `GET /b`: endpoint added\n\nThe next release makes it API 1.1.0.\n"
        )

    def test_a_breaking_change_without_a_new_major_fails(self, released):
        removed = [change(3, "api path removed", "/a")]
        result = release.api_check(released.handle, spec_with(paths=()), lambda b, h: removed)
        assert result.errors[0] == "Breaking API change: GET /a: api path removed."
        assert result.comment.endswith("- `GET /a`: api path removed\n\nThis cannot be released as it is.\n")

    def test_a_breaking_change_with_a_new_major_passes(self, released):
        result = release.api_check(released.handle, spec_with("2.0.0", paths=()), lambda b, h: [change(3)])
        assert result.errors == []
        assert result.comment.endswith("The next release makes it API 2.0.0.\n")

    @pytest.fixture
    def release_pr(self, released) -> Repo:
        """A release prepared with an addition to the API, committed and not
        tagged: the release pull request, or its commit in the merge queue."""
        released.commit(FRAGMENT)
        released.prepare(TAG, DAY, spec_with(paths=("/a", "/b")), lambda b, h: [change(1)])
        return released

    def test_a_release_that_ships_the_schema_of_its_code_passes(self, release_pr):
        result = release.api_check(release_pr.handle, spec_with("1.1.0", paths=("/a", "/b")), lambda b, h: [change(1)])
        assert result.errors == []

    def test_an_edit_in_the_release_pull_request_is_still_the_release(self, release_pr):
        release_pr.commit({"CHANGELOG.md": release_pr.read("CHANGELOG.md").replace("Something new", "Something newer")})
        spec = spec_with("1.1.0", paths=("/a", "/b", "/c"))
        result = release.api_check(release_pr.handle, spec, lambda b, h: [change(1), change(1, path="/c")])
        assert result.errors == [release.STALE_SCHEMA]

    def test_a_change_behind_the_release_is_the_next_releases(self, release_pr):
        """An API pull request in the queue after the release, or checked
        between its merge and its tag: not this release's schema to ship."""
        release_pr.commit({"backend/src/plak/app.py": "route = '/c'\n"})
        spec = spec_with("1.1.0", paths=("/a", "/b", "/c"))
        result = release.api_check(release_pr.handle, spec, lambda b, h: [change(1), change(1, path="/c")])
        assert result.errors == []
        assert "The next release makes it API 1.1.0." in result.comment

    def test_a_release_whose_api_changed_after_it_was_prepared_fails(self, release_pr):
        """An API pull request merged between preparing and merging the
        release: the queue rebases the release on top of it, so the schema
        the release commit carries is no longer the one its code prints."""
        spec = spec_with("1.1.0", paths=("/a", "/b", "/c"))
        result = release.api_check(release_pr.handle, spec, lambda b, h: [change(1), change(1, path="/c")])
        assert result.errors == [
            "backend/openapi.json is not the schema this release ships: the API changed after the release "
            "was prepared; prepare it again."
        ]

    @pytest.mark.parametrize("main", [MAIN, "app = 1\n"], ids=["old-version", "no-line"])
    def test_a_release_whose_code_does_not_carry_the_api_version_fails(self, release_pr, main):
        release_pr.commit({"backend/src/plak/main.py": main})
        result = release.api_check(release_pr.handle, spec_with("1.0.0", paths=("/a", "/b")), lambda b, h: [change(1)])
        assert result.errors == [release.STALE_SCHEMA]

    def test_a_release_with_a_breaking_change_reports_only_that(self, release_pr):
        result = release.api_check(release_pr.handle, spec_with("1.1.0", paths=()), lambda b, h: [change(3)])
        assert result.errors[0].startswith("Breaking API change")
        assert release.STALE_SCHEMA not in result.errors

    def test_main_runs_oasdiff_and_fails_on_a_breaking_change(self, released, tmp_path, monkeypatch, capsys):
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        (tmp_path / "head.json").write_text(spec_with(paths=()), encoding="utf-8")
        binary = fake_oasdiff(tmp_path, json.dumps([change(3, "api path removed", "/a")]))
        comment = tmp_path / "comment.md"
        args = ["api-check", "--spec", str(tmp_path / "head.json"), "--oasdiff", binary, "--comment-file", str(comment)]

        assert release.main(args) == 1
        out = capsys.readouterr().out
        assert "::error title=API contract::Breaking API change: GET /a: api path removed." in out
        assert summary.read_text(encoding="utf-8").startswith("## API contract\n\n- Breaking API change")
        assert comment.read_text(encoding="utf-8").startswith("<!-- plak-api-check -->\n")

    def test_main_passes_an_unchanged_contract_and_empties_the_comment(self, released, tmp_path, capsys):
        (tmp_path / "head.json").write_text(SPEC, encoding="utf-8")
        comment = tmp_path / "comment.md"
        comment.write_text("old", encoding="utf-8")
        args = ["api-check", "--spec", str(tmp_path / "head.json"), "--oasdiff", fake_oasdiff(tmp_path)]
        assert release.main([*args, "--comment-file", str(comment)]) == 0
        assert capsys.readouterr().out == "The API contract holds.\n"
        assert comment.read_text(encoding="utf-8") == ""

    def test_main_without_a_released_schema_gives_a_notice(self, repo, tmp_path, capsys):
        (tmp_path / "head.json").write_text(SPEC, encoding="utf-8")
        assert release.main(["api-check", "--spec", str(tmp_path / "head.json"), "--oasdiff", "unused"]) == 0
        assert capsys.readouterr().out.startswith("::notice title=API contract::No backend/openapi.json")


class TestValidateTag:
    def test_it_runs_as_a_plain_script_on_the_standard_library(self, tmp_path):
        """The workflow and the hook run the file itself, not this import."""
        # sys.executable and a path from this file, no shell.
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(SCRIPT), "validate-tag", "v2026.10.1"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode, result.stdout) == (0, "v2026.10.1 is a valid CalVer tag.\n")

    def test_a_valid_tag_passes(self, capsys):
        assert release.main(["validate-tag", "v2026.10.1.1"]) == 0
        assert capsys.readouterr().out == "v2026.10.1.1 is a valid CalVer tag.\n"

    def test_an_invalid_tag_fails_with_the_reason(self, capsys):
        assert release.main(["validate-tag", "v2026.10.1.0"]) == 1
        assert "counts from .1" in capsys.readouterr().err


@pytest.fixture
def branch(released) -> Repo:
    """A feature branch off beta, which has had one release."""
    released.git("switch", "-q", "-c", "feature")
    return released


RELEASED_SECTION = f"## [2026.9.1]\n\n{ENTRY}"


class TestCheck:
    def test_a_shipped_change_with_a_fragment_is_in_order(self, branch):
        branch.commit({"backend/src/plak/main.py": "app = 2\n", **FRAGMENT})
        assert release.check(branch.handle, "beta") == release.CheckResult()

    def test_a_shipped_change_without_a_fragment_warns(self, branch):
        branch.commit({"backend/src/plak/main.py": "app = 2\n"})
        result = release.check(branch.handle, "beta")
        assert result.errors == []
        assert result.warning == release.WARNING

    def test_a_fragment_removed_is_no_entry(self, released):
        """What counts is a fragment this branch adds or edits, not one it
        takes away."""
        released.commit(FRAGMENT)
        released.git("switch", "-q", "-c", "feature")
        released.git("rm", "-q", "changelog.d/something-new.added.md")
        released.commit({"backend/src/plak/main.py": "app = 2\n"})
        assert release.check(released.handle, "beta").warning == release.WARNING

    @pytest.mark.parametrize(
        "body",
        ["Bump a dependency.\n\nNo changelog entry: tooling only", "No changelog entry:  refactor, no effect"],
    )
    def test_the_description_can_say_why_there_is_no_entry(self, branch, body):
        branch.commit({"backend/src/plak/main.py": "app = 2\n"})
        assert release.check(branch.handle, "beta", body).warning is None

    @pytest.mark.parametrize("body", ["No changelog entry:", "No changelog entry:   ", "See: No changelog entry: x"])
    def test_an_opt_out_without_a_reason_or_mid_line_does_not_count(self, branch, body):
        branch.commit({"backend/src/plak/main.py": "app = 2\n"})
        assert release.check(branch.handle, "beta", body).warning == release.WARNING

    def test_a_change_outside_the_shipped_paths_does_not_warn(self, branch):
        branch.commit({"docs/x.md": "docs\n", "backend/tests/test_x.py": "def test(): pass\n"})
        assert release.check(branch.handle, "beta") == release.CheckResult()

    def test_only_the_branch_own_changes_count(self, branch):
        """A shipped change that landed on beta after the branch started is
        beta's, and does not ask this branch for an entry."""
        branch.git("switch", "-q", "beta")
        branch.commit({"backend/src/plak/main.py": "app = 2\n", **FRAGMENT})
        branch.git("switch", "-q", "feature")
        branch.commit({"README.md": "better\n"})
        assert release.check(branch.handle, "beta").warning is None

    def test_the_pull_request_that_adds_the_changelog_does_not_warn(self, tmp_path):
        repo = Repo(tmp_path / "fresh")
        repo.path.mkdir()
        repo.git("init", "-q", "-b", "beta")
        repo.commit({"README.md": "x\n"})
        repo.git("switch", "-q", "-c", "feature")
        repo.commit({"CHANGELOG.md": changelog(), "backend/src/plak/main.py": "app = 1\n", **FRAGMENT})
        assert release.check(repo.handle, "beta") == release.CheckResult()

    @pytest.mark.parametrize(
        ("files", "reason"),
        [
            ({"changelog.d/x.improved.md": "- x\n"}, "changelog.d/x.improved.md is not <slug>"),
            ({"changelog.d/x.added.md": "### Added\n\n- x\n"}, "'### Added' is not an entry"),
            ({"changelog.d/x.note.nl.md": "Nieuw\n"}, "x.note.nl.md has no x.note.en.md"),
        ],
        ids=["name", "content", "unpaired-note"],
    )
    def test_a_malformed_fragment_fails(self, branch, files, reason):
        branch.commit({"backend/src/plak/main.py": "app = 2\n", **files})
        [error] = release.check(branch.handle, "beta").errors
        assert reason in error

    def test_a_fragment_that_is_a_symlink_fails(self, branch):
        (branch.path / "changelog.d").mkdir()
        os.symlink("../README.md", branch.path / "changelog.d" / "link.added.md")
        branch.commit(FRAGMENT)
        assert release.check(branch.handle, "beta").errors == ["changelog.d/link.added.md is not a plain file."]

    def test_an_unreleased_section_fails(self, branch):
        branch.commit({"CHANGELOG.md": changelog(f"## [Unreleased]\n\n{ENTRY}\n\n{RELEASED_SECTION}")})
        [error] = release.check(branch.handle, "beta").errors
        assert error.startswith("CHANGELOG.md: [Unreleased] is gone")

    def test_a_missing_changelog_fails(self, branch):
        branch.remove("CHANGELOG.md")
        assert release.check(branch.handle, "beta").errors == ["CHANGELOG.md is missing."]

    @pytest.mark.parametrize(
        "edit",
        [
            lambda text: text.replace("- Something new.", "- Something newer."),
            lambda text: text.replace("## [2026.9.1]", "## [2026.9.2]"),
            lambda text: text[: text.index("## [2026.9.1]")],
        ],
        ids=["body", "renamed", "removed"],
    )
    def test_editing_a_released_section_fails(self, branch, edit):
        """With the links following the edit, so only the freeze is left."""
        branch.commit({"CHANGELOG.md": release.with_links(edit(branch.read("CHANGELOG.md")))})
        errors = release.check(branch.handle, "beta").errors
        assert errors[0] == (
            "CHANGELOG.md: [2026.9.1] was released as v2026.9.1 and is frozen; "
            "describe the change in a fragment under changelog.d/ instead."
        )

    def test_a_section_for_an_existing_tag_is_no_release(self, repo):
        """A version tagged by hand cannot get its section afterwards: the
        section that comes in is a release, and that tag is taken."""
        repo.git("tag", "v2026.9.1")
        repo.git("switch", "-q", "-c", "feature")
        repo.commit({"CHANGELOG.md": changelog("## [2026.9.1]\n\nWhat ran then.\n\n- x")})
        assert "v2026.9.1 is not newer than v2026.9.1." in release.check(repo.handle, "beta").errors

    def test_a_section_without_a_tag_is_not_frozen(self, released):
        """The release pull request may be edited until it merges."""
        released.commit({"changelog.d/other.fixed.md": "- Other thing.\n"})
        released.prepare(TAG)
        released.git("switch", "-q", "-c", "reword")
        released.commit({"CHANGELOG.md": released.read("CHANGELOG.md").replace("Other thing", "Other things")})
        assert release.check(released.handle, "beta").errors == []

    @pytest.fixture
    def release_pr(self, released) -> Repo:
        """The fragments on beta, the release prepared on its own branch."""
        released.commit({**FRAGMENT, **NOTE_FRAGMENTS, "frontend/src/App.vue": "x\n"})
        released.git("switch", "-q", "-c", f"release/{TAG}")
        released.prepare(TAG)
        return released

    def test_a_release_pull_request_passes_without_a_warning(self, release_pr):
        """It changes shipped paths and adds no fragment: that is the point."""
        assert release.check(release_pr.handle, "beta") == release.CheckResult()

    def test_a_release_pull_request_with_a_fragment_left_fails(self, release_pr):
        """A fragment that merged between preparing and merging: the release
        no longer says everything it ships."""
        release_pr.commit({"changelog.d/late.fixed.md": "- Late.\n"})
        assert release.check(release_pr.handle, "beta").errors == [
            "changelog.d/late.fixed.md is still there: a release removes every fragment."
        ]

    def test_a_release_pull_request_with_a_wrong_version_fails(self, release_pr):
        release_pr.commit({"publiccode.yml": PUBLICCODE})
        assert release.check(release_pr.handle, "beta").errors == [
            "publiccode.yml at HEAD does not carry version 2026.10.1."
        ]

    @pytest.mark.parametrize(
        ("sections", "added"),
        [
            (f"{RELEASED_SECTION}\n\n## [2026.8.1]\n\n- Old.", "[2026.8.1]"),
            (f"## [2026.10.2]\n\n- b\n\n## [2026.10.1]\n\n- a\n\n{RELEASED_SECTION}", "[2026.10.2], [2026.10.1]"),
        ],
        ids=["below", "two"],
    )
    def test_a_release_adds_exactly_one_section_on_top(self, branch, sections, added):
        branch.commit({"CHANGELOG.md": changelog(sections)})
        assert release.check(branch.handle, "beta").errors == [
            f"CHANGELOG.md: a release adds one section, on top; this adds {added}."
        ]

    def test_a_new_section_with_a_malformed_heading_is_reported_once(self, branch):
        branch.commit({"CHANGELOG.md": changelog(f"## [2026.10.01]\n\n- a\n\n{RELEASED_SECTION}")})
        [error] = release.check(branch.handle, "beta").errors
        assert "'v2026.10.01' has a leading zero" in error

    @pytest.mark.parametrize(
        ("files", "reason"),
        [
            ({"2026.9.1.nl.md": "Nieuw\n"}, "What's new note comes in Dutch and English"),
            ({"unreleased.nl.md": "Nieuw\n", "unreleased.en.md": "New\n"}, "is not <version>.nl.md or <version>.en.md"),
        ],
        ids=["one-language", "not-a-version"],
    )
    def test_a_note_that_is_not_a_pair_for_a_release_fails(self, branch, files, reason):
        branch.commit({NOTES + name: text for name, text in files.items()})
        errors = release.check(branch.handle, "beta").errors
        assert reason in errors[0]

    @pytest.mark.parametrize("path", ["frontend/src/App.vue", "cli/plak_cli/__init__.py"])
    def test_a_member_facing_change_without_a_note_gets_a_hint(self, branch, path):
        branch.commit({path: "changed\n", **FRAGMENT})
        result = release.check(branch.handle, "beta")
        assert (result.errors, result.warning, result.hint) == ([], None, release.HINT)

    def test_a_note_fragment_removed_is_no_note(self, released):
        released.commit(NOTE_FRAGMENTS)
        released.git("switch", "-q", "-c", "feature")
        released.git("rm", "-q", "changelog.d/x.note.nl.md", "changelog.d/x.note.en.md")
        released.commit({"frontend/src/App.vue": "x\n", **FRAGMENT})
        assert release.check(released.handle, "beta").hint == release.HINT

    @pytest.mark.parametrize(
        "files",
        [
            {"frontend/src/App.vue": "x\n", **NOTE_FRAGMENTS},
            {"frontend/src/App.test.ts": "x\n"},
            {"backend/src/plak/main.py": "app = 2\n"},
            {NOTES + "2026.9.1.nl.md": "a\n", NOTES + "2026.9.1.en.md": "b\n"},
        ],
        ids=["with-note", "test-only", "backend", "notes-only"],
    )
    def test_no_hint_otherwise(self, branch, files):
        branch.commit({**files, **FRAGMENT})
        assert release.check(branch.handle, "beta").hint is None

    def test_hard_only_skips_the_warning_and_the_hint(self, branch):
        branch.commit({"frontend/src/App.vue": "x\n", "changelog.d/x.improved.md": "- x\n"})
        result = release.check(branch.handle, "beta", hard_only=True)
        assert len(result.errors) == 1
        assert (result.warning, result.hint) == (None, None)


class TestCheckCommand:
    def test_errors_fail_with_annotations_and_a_summary(self, branch, monkeypatch, tmp_path, capsys):
        branch.commit({"changelog.d/x.improved.md": "- x\n"})
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        assert release.main(["check", "--base", "beta"]) == 1
        assert capsys.readouterr().out.startswith("::error title=Changelog::changelog.d/x.improved.md is not <slug>")
        assert summary.read_text().startswith("## Changelog\n\n- **Error:** ")

    def test_a_warning_passes_and_fills_the_comment(self, branch, monkeypatch, tmp_path, capsys):
        branch.commit({"frontend/src/App.vue": "x\n"})
        summary, comment = tmp_path / "summary.md", tmp_path / "comment.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        assert release.main(["check", "--base", "beta", "--comment-file", str(comment)]) == 0
        out = capsys.readouterr().out
        assert f"::warning title=Changelog::{release.WARNING}\n" in out
        assert f"::notice title=What's new::{release.HINT}\n" in out
        assert comment.read_text() == f"{release.COMMENT_MARKER}\n**Changelog:** {release.WARNING}\n"
        assert "- **Warning:** " in summary.read_text()
        assert "- **Hint:** " in summary.read_text()

    def test_the_description_file_clears_the_warning_and_empties_the_comment(self, branch, tmp_path, capsys):
        branch.commit({"backend/src/plak/main.py": "app = 2\n"})
        body, comment = tmp_path / "body.md", tmp_path / "comment.md"
        body.write_text("Chore.\n\nNo changelog entry: lockfile refresh\n")
        comment.write_text("stale")

        args = ["check", "--base", "beta", "--body-file", str(body), "--comment-file", str(comment)]
        assert release.main(args) == 0
        assert capsys.readouterr().out == "CHANGELOG.md, changelog.d/ and the What's new notes are in order.\n"
        assert comment.read_text() == ""

    def test_the_merge_queue_runs_only_the_hard_checks(self, branch, capsys):
        branch.commit({"frontend/src/App.vue": "x\n"})
        assert release.main(["check", "--base", "beta", "--hard-only"]) == 0
        assert "::warning" not in capsys.readouterr().out

    def test_an_unknown_base_fails(self, branch, capsys):
        assert release.main(["check", "--base", "nope"]) == 1
        assert "error: git merge-base nope HEAD" in capsys.readouterr().err

    def test_annotations_escape_what_would_end_them(self):
        assert release._annotation("error", "T", "50%\nnext\r") == "::error title=T::50%25%0Anext%0D"


def _event(repo: Repo, command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo.path)})


@pytest.fixture
def pushed(branch) -> Repo:
    """The feature branch with a shipped change and no fragment, and an
    origin/beta to compare with."""
    branch.git("update-ref", "refs/remotes/origin/beta", "beta")
    branch.commit({"backend/src/plak/main.py": "app = 2\n"})
    return branch


class TestHook:
    def test_a_pull_request_without_a_fragment_is_denied(self, pushed):
        decision = json.loads(release.hook(_event(pushed, "gh pr create --title x --body 'Fix it'")))
        assert decision["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Shipped paths changed without a changelog fragment" in reason
        assert "changelog.d/<slug>.<kind>.md" in reason
        assert "changelog skill" in reason
        assert "No changelog entry: <reason>" in reason

    def test_a_pull_request_with_a_fragment_passes(self, pushed):
        pushed.commit(FRAGMENT)
        assert release.hook(_event(pushed, "gh pr create --fill")) is None

    def test_a_release_pull_request_passes(self, released):
        """It changes shipped paths and adds no fragment: that is what a
        release does, as check() knows too."""
        released.commit(FRAGMENT)
        released.git("update-ref", "refs/remotes/origin/beta", "beta")
        released.git("switch", "-q", "-c", f"release/{TAG}")
        released.prepare(TAG)
        assert release.hook(_event(released, "gh pr create --fill")) is None

    @pytest.mark.parametrize(
        "command",
        [
            "gh pr create --title x --body \"No changelog entry: docs only\"",
            "gh pr create --title x --body-file - <<'EOF'\nSummary.\n\nNo changelog entry: tests only\nEOF",
        ],
        ids=["inline", "heredoc"],
    )
    def test_an_opt_out_in_the_command_passes(self, pushed, command):
        assert release.hook(_event(pushed, command)) is None

    @pytest.mark.parametrize("flag", ["--body-file {}", "--body-file={}", "-F {}"])
    def test_an_opt_out_in_the_body_file_passes(self, pushed, flag):
        (pushed.path / "body.md").write_text("Summary.\n\nNo changelog entry: CI only\n")
        assert release.hook(_event(pushed, "gh pr create --title x " + flag.format("body.md"))) is None

    @pytest.mark.parametrize(
        "tail",
        ["--body-file body.md", "--body-file missing.md", "--body-file", "--body 'unbalanced"],
        ids=["no-opt-out", "missing-file", "no-file-name", "unparseable"],
    )
    def test_a_body_that_does_not_opt_out_is_denied(self, pushed, tail):
        (pushed.path / "body.md").write_text("Summary only.\n")
        assert release.hook(_event(pushed, "gh pr create --title x " + tail)) is not None

    @pytest.mark.parametrize(
        "stdin",
        ["not json", "{}", '{"tool_input": null}', "[]", json.dumps({"tool_input": {"command": "gh pr list"}})],
    )
    def test_anything_but_a_pull_request_creation_passes(self, pushed, stdin):
        assert release.hook(stdin) is None

    def test_without_origin_beta_it_passes_and_says_why(self, branch, capsys):
        branch.commit({"backend/src/plak/main.py": "app = 2\n"})
        assert release.hook(_event(branch, "gh pr create --fill")) is None
        assert capsys.readouterr().err.startswith("changelog hook: skipped, git merge-base origin/beta HEAD")

    def test_main_prints_the_decision(self, pushed, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO(_event(pushed, "gh pr create --fill")))
        assert release.main(["hook"]) == 0
        assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_main_is_silent_when_it_allows(self, pushed, monkeypatch, capsys):
        monkeypatch.setattr(sys, "stdin", io.StringIO(_event(pushed, "git status")))
        assert release.main(["hook"]) == 0
        assert capsys.readouterr().out == ""

    def test_the_project_settings_run_it_before_gh_pr_create(self):
        settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        [entry] = settings["hooks"]["PreToolUse"]
        assert entry["matcher"] == "Bash"
        [handler] = entry["hooks"]
        assert handler["type"] == "command"
        assert handler["if"] == "Bash(gh pr create *)"
        assert '"${CLAUDE_PROJECT_DIR}/.github/scripts/release.py" hook' in handler["command"]
