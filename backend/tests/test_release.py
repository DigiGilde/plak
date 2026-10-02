"""The release tooling (.github/scripts/release.py): CHANGELOG.md, CalVer
tags, component versions, the pull request check and the Claude Code hook.

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
ENTRY = "### Added\n\n- Something new."

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


def changelog(unreleased: str = "", released: str = "") -> str:
    text = "# Changelog\n\nWhat changed, newest first.\n\n## [Unreleased]\n"
    if unreleased:
        text += "\n" + unreleased.strip("\n") + "\n"
    if released:
        text += "\n" + released.strip("\n") + "\n"
    return text


def with_entry(repo: Repo, entry: str = ENTRY) -> str:
    """The repository's changelog with `entry` under [Unreleased], released
    sections untouched."""
    return repo.read("CHANGELOG.md").replace("## [Unreleased]\n", f"## [Unreleased]\n\n{entry}\n", 1)


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

    @property
    def handle(self):
        return release.Git(self.path)

    def release(self, tag: str, day: date) -> None:
        """The cycle the release workflow will run: promote, commit, tag."""
        release.promote(self.handle, tag, day)
        self.git("commit", "-q", "-m", f"Release {tag}")
        self.git("tag", tag)


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
            "backend/src/plak/main.py": "app = 1\n",
            "README.md": "readme\n",
        },
        "Start",
    )
    monkeypatch.chdir(repo.path)
    return repo


@pytest.fixture
def released(repo) -> Repo:
    """The same repository after its first release, v2026.9.1."""
    repo.commit({"CHANGELOG.md": changelog(ENTRY)})
    repo.release("v2026.9.1", date(2026, 9, 1))
    return repo


def _versions(repo: Repo) -> tuple[str, str]:
    return (
        release.pyproject_version(repo.read("cli/pyproject.toml")),
        json.loads(repo.read("plugin/.claude-plugin/plugin.json"))["version"],
    )


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


class TestChangelogStructure:
    def test_a_full_changelog_is_valid(self):
        text = changelog(
            "<!-- release: hold -->\n\n### Added\n\n- One line,\n  wrapped onto a second.\n\n"
            "<!-- a note to the next reader -->\n\n### Security\n\n- Another.",
            "## [2026.9.1]\n\nA paragraph before the list.\n\n### Fixed\n\n- Bump.\n\n## [2026.8.31.1]\n\n- Old.",
        )
        assert release.changelog_problems(text) == []

    def test_an_empty_unreleased_is_valid(self):
        assert release.changelog_problems(changelog()) == []

    def test_the_changelog_of_this_repository_is_valid(self):
        assert release.changelog_problems((ROOT / "CHANGELOG.md").read_text(encoding="utf-8")) == []

    @pytest.mark.parametrize(
        "text",
        ["# Changelog\n", "# Changelog\n\n## [2026.9.1]\n\n- x\n\n## [Unreleased]\n"],
        ids=["no-sections", "unreleased-not-first"],
    )
    def test_unreleased_has_to_come_first(self, text):
        assert release.changelog_problems(text) == ["CHANGELOG.md: the first section has to be '## [Unreleased]'."]

    @pytest.mark.parametrize(
        ("unreleased", "reason"),
        [
            ("### New\n\n- x", "'### New' is not one of: ### Added, ### Changed"),
            ("### Maintenance\n\n- x", "'### Maintenance' is not one of: ### Added, ### Changed"),
            ("### Added\n\n- x\n\n### Added\n\n- y", "'### Added' appears twice"),
            ("### Added\n\n### Fixed\n\n- y", "'### Added' has no entries."),
            ("- x", "'- x' sits under no heading"),
            ("### Added\n\n- x\n\nSome prose.", "'Some prose.' is not an entry"),
            ("### Added\n\n  indented, no entry above\n- x", "'  indented, no entry above' is not an entry"),
            ("### Added\n\n- x\n\n#### Deeper", "'#### Deeper' is not an entry"),
        ],
    )
    def test_a_malformed_unreleased_is_refused(self, unreleased, reason):
        [problem] = release.changelog_problems(changelog(unreleased))
        assert problem.startswith("CHANGELOG.md: [Unreleased]: ")
        assert reason in problem

    @pytest.mark.parametrize(
        ("heading", "reason"),
        [
            ("## 2026.9.1", "is not '## [YYYY.M.D]' or '## [YYYY.M.D.N]'"),
            ("## [2026.9.1] - 2026-09-01", "is not '## [YYYY.M.D]' or '## [YYYY.M.D.N]'"),
            ("## [2026.09.1]", "'## [2026.09.1]': 'v2026.09.1' has a leading zero"),
            ("## [2026.2.30]", "'## [2026.2.30]': 'v2026.2.30' does not name a real date"),
            ("## [1.0.0]", "'## [1.0.0]': 'v1.0.0' is not a CalVer tag"),
            ("## [Unreleased]", "'## [Unreleased]' appears twice"),
        ],
    )
    def test_a_malformed_released_heading_is_refused(self, heading, reason):
        [problem] = release.changelog_problems(changelog(ENTRY, f"{heading}\n\n- x"))
        assert reason in problem

    @pytest.mark.parametrize("heading", ["## [v2026.9.1] - 2026-09-01", "## [v2026.9.1]"])
    def test_the_old_heading_with_the_tag_and_a_date_is_refused(self, heading):
        """The version is the date already; the old form repeated it."""
        [problem] = release.changelog_problems(changelog(ENTRY, f"{heading}\n\n- x"))
        assert problem == (
            f"CHANGELOG.md: '{heading}' is the old form; write '## [2026.9.1]', "
            "the version without the v and without a date."
        )

    def test_a_version_twice_is_refused(self):
        section = "## [2026.9.1]\n\n- x\n"
        [problem] = release.changelog_problems(changelog(ENTRY, section + "\n" + section))
        assert "'## [2026.9.1]' appears twice" in problem

    def test_parse_raises_with_every_problem(self):
        with pytest.raises(release.ReleaseError) as refused:
            release.parse_changelog(changelog("### New\n\n- x\n\nprose"))
        assert len(refused.value.problems) == 2


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
            "frontend/src/content/releases/unreleased.nl.md",
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


class TestNotes:
    def test_pairs_in_both_languages_are_fine(self):
        notes = {"unreleased.nl.md": "Nieuw", "unreleased.en.md": "New", "2026.9.1.nl.md": "a", "2026.9.1.en.md": "b"}
        assert release.note_problems(notes | {"index.ts": "", "README.md": ""}) == []
        assert release.note_problems({"unreleased.nl.md": "", "unreleased.en.md": " \n"}) == []

    @pytest.mark.parametrize(
        ("notes", "reason"),
        [
            ({"unreleased.nl.md": "Nieuw"}, "unreleased.nl.md has no unreleased.en.md"),
            ({"unreleased.en.md": "New"}, "unreleased.en.md has no unreleased.nl.md"),
            ({"2026.9.1.nl.md": "Nieuw"}, "2026.9.1.nl.md has no 2026.9.1.en.md"),
            ({"unreleased.nl.md": "Nieuw", "unreleased.en.md": "\n"}, "one is empty and the other is not"),
        ],
    )
    def test_a_note_in_one_language_is_refused(self, notes, reason):
        [problem] = release.note_problems(notes)
        assert problem.startswith(release.NOTES_DIR)
        assert reason in problem

    def test_notes_with_content_are_named_after_the_version(self):
        notes = {"unreleased.nl.md": "Nieuw", "unreleased.en.md": "New"}
        assert release.note_operations(notes, "2026.10.1") == [
            ("unreleased.nl.md", "2026.10.1.nl.md"),
            ("unreleased.en.md", "2026.10.1.en.md"),
        ]

    def test_empty_notes_are_deleted(self):
        notes = {"unreleased.nl.md": "", "unreleased.en.md": "  \n"}
        assert release.note_operations(notes, "2026.10.1") == [("unreleased.nl.md", None), ("unreleased.en.md", None)]

    def test_no_notes_means_nothing_to_do(self):
        assert release.note_operations({}, "2026.10.1") == []
        assert release.note_operations({"2026.9.1.nl.md": "a", "2026.9.1.en.md": "b"}, "2026.10.1") == []

    def test_a_note_for_the_version_that_exists_already_is_refused(self):
        notes = {"unreleased.nl.md": "a", "unreleased.en.md": "b", "2026.10.1.nl.md": "c", "2026.10.1.en.md": "d"}
        with pytest.raises(release.ReleaseError, match=re.escape("2026.10.1.nl.md already exists")):
            release.note_operations(notes, "2026.10.1")

    def test_a_one_language_note_stops_the_release(self):
        with pytest.raises(release.ReleaseError, match=re.escape("has no unreleased.en.md")):
            release.note_operations({"unreleased.nl.md": "Nieuw"}, "2026.10.1")


class TestDecide:
    def test_an_empty_unreleased_without_tags_releases_nothing(self, repo):
        assert release.decide(repo.handle, DAY) == ("none", "")

    def test_entries_release_under_todays_tag(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        assert release.decide(repo.handle, DAY) == ("release", TAG)

    def test_nothing_since_the_last_tag_releases_nothing(self, released):
        assert release.decide(released.handle, DAY) == ("none", "")

    @pytest.mark.parametrize(
        "path",
        ["backend/src/plak/main.py", "backend/uv.lock", "cli/plak_cli/__init__.py", "docs/releasing.md"],
    )
    def test_a_change_without_an_entry_releases_nothing(self, released, path):
        """No entry, no release notes, no release: the push goes to staging
        only, shipped path or not. It reaches production with the next
        release that does have an entry."""
        released.commit({path: "changed\n"})
        assert release.decide(released.handle, DAY) == ("none", "")

    @pytest.mark.parametrize("unreleased", [release.HOLD + "\n\n" + ENTRY, release.HOLD])
    def test_the_hold_marker_holds(self, repo, unreleased):
        repo.commit({"CHANGELOG.md": changelog(unreleased)})
        assert release.decide(repo.handle, DAY) == ("hold", "")

    def test_a_second_release_the_same_day_gets_a_suffix(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        repo.release(TAG, DAY)
        repo.commit({"CHANGELOG.md": with_entry(repo)})
        assert release.decide(repo.handle, DAY) == ("release", "v2026.10.1.1")

    def test_a_malformed_changelog_is_refused(self, repo):
        repo.commit({"CHANGELOG.md": changelog("### New\n\n- x")})
        with pytest.raises(release.ReleaseError, match="'### New' is not one of"):
            release.decide(repo.handle, DAY)

    def test_main_prints_the_route_and_writes_the_step_output(self, repo, monkeypatch, tmp_path, capsys):
        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        output = tmp_path / "output"
        output.write_text("earlier=1\n")
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        monkeypatch.setattr(release, "today", lambda: DAY)

        assert release.main(["decide"]) == 0
        assert capsys.readouterr().out == f"route=release\ntag={TAG}\n"
        assert output.read_text() == f"earlier=1\nroute=release\ntag={TAG}\n"

    def test_main_without_a_step_output_only_prints(self, repo, capsys):
        assert release.main(["decide"]) == 0
        assert capsys.readouterr().out == "route=none\ntag=\n"

    def test_main_outside_a_repository_fails(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        assert release.main(["decide"]) == 1
        assert capsys.readouterr().err.startswith("error: git rev-parse --show-toplevel:")


class TestPromote:
    def test_unreleased_becomes_the_section_for_the_tag(self, repo):
        older = "## [2026.9.1]\n\n### Fixed\n\n- Old."
        repo.commit({"CHANGELOG.md": changelog(ENTRY, older)})

        touched = release.promote(repo.handle, TAG, DAY)

        assert repo.read("CHANGELOG.md") == (
            "# Changelog\n\nWhat changed, newest first.\n\n## [Unreleased]\n\n"
            f"## [2026.10.1]\n\n{ENTRY}\n\n{older}\n"
        )
        assert release.changelog_problems(repo.read("CHANGELOG.md")) == []
        staged = repo.git("diff", "--cached", "--name-only").split()
        assert sorted(staged) == sorted(touched)

    def test_the_first_release_sets_every_component_version(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        release.promote(repo.handle, TAG, DAY)

        assert _versions(repo) == ("2026.10.1", "2026.10.1")
        assert 'name = "plak"\nversion = "2026.10.1"' in repo.read("cli/uv.lock")
        assert 'softwareVersion: "2026.10.1"\nreleaseDate: "2026-10-01"' in repo.read("publiccode.yml")

    def test_a_backend_change_leaves_the_cli_and_plugin_versions(self, released):
        released.commit({"CHANGELOG.md": changelog(ENTRY), "backend/src/plak/main.py": "app = 2\n"})
        touched = release.promote(released.handle, TAG, DAY)

        assert _versions(released) == ("2026.9.1", "2026.9.1")
        assert sorted(touched) == ["CHANGELOG.md", "publiccode.yml"]
        assert 'softwareVersion: "2026.10.1"' in released.read("publiccode.yml")

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
        released.commit({"CHANGELOG.md": changelog(ENTRY), **files})
        release.promote(released.handle, TAG, DAY)
        assert _versions(released) == expected

    def test_a_version_line_changed_by_hand_is_no_change(self, released):
        """Only the line a release writes itself is left out of the
        comparison; everything around it still counts."""
        released.commit(
            {
                "CHANGELOG.md": changelog(ENTRY),
                "cli/pyproject.toml": release.set_pyproject_version(released.read("cli/pyproject.toml"), "9.9.9"),
                "cli/uv.lock": release.set_lock_version(released.read("cli/uv.lock"), "9.9.9"),
                "plugin/.claude-plugin/plugin.json": release.set_plugin_version(
                    released.read("plugin/.claude-plugin/plugin.json"), "9.9.9"
                ),
            }
        )
        release.promote(released.handle, TAG, DAY)
        assert _versions(released) == ("9.9.9", "9.9.9")

    def test_a_file_that_did_not_exist_at_the_last_tag_is_a_change(self, repo):
        section = "## [2026.9.1]\n\n- x"
        repo.remove("cli/uv.lock")
        repo.commit({"CHANGELOG.md": changelog("", section)})
        repo.git("tag", "v2026.9.1")
        repo.commit({"CHANGELOG.md": changelog(ENTRY, section), "cli/uv.lock": LOCK})
        release.promote(repo.handle, TAG, DAY)
        assert _versions(repo) == ("2026.10.1", "0.3.1")

    def test_a_shipped_change_without_an_entry_is_refused_and_writes_nothing(self, released):
        """Without entries there are no release notes, so nothing is made up
        from commit subjects either."""
        released.commit({"backend/src/plak/main.py": "app = 2\n"}, "Update urllib3 to 2.8.0")
        with pytest.raises(release.ReleaseError) as refused:
            release.promote(released.handle, TAG, DAY)
        assert refused.value.problems == [
            "Nothing to release: [Unreleased] has no entries, and they are the release notes."
        ]
        assert released.git("status", "--porcelain") == ""

    def test_without_a_tag_or_entries_there_is_nothing_to_release(self, repo):
        with pytest.raises(release.ReleaseError, match="Nothing to release"):
            release.promote(repo.handle, TAG, DAY)

    @pytest.mark.parametrize(
        ("unreleased", "tag", "reason"),
        [
            (release.HOLD + "\n\n" + ENTRY, TAG, "on hold"),
            (ENTRY, "v2026.10.01", "leading zero"),
            (ENTRY, "v2026.9.1", "already exists"),
            (ENTRY, "v2026.8.1", "not newer than v2026.9.1"),
        ],
        ids=["hold", "invalid-tag", "tag-exists", "older-tag"],
    )
    def test_refusals(self, released, unreleased, tag, reason):
        released.commit({"CHANGELOG.md": changelog(unreleased)})
        with pytest.raises(release.ReleaseError, match=reason):
            release.promote(released.handle, tag, DAY)

    def test_a_section_for_the_tag_already_in_the_changelog_is_refused(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY, "## [2026.10.1]\n\n- x")})
        with pytest.raises(release.ReleaseError, match=f"already has a section for {TAG}"):
            release.promote(repo.handle, TAG, DAY)

    def test_a_refusal_writes_nothing(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        repo.remove("publiccode.yml")
        with pytest.raises(release.ReleaseError, match=re.escape("publiccode.yml is missing")):
            release.promote(repo.handle, TAG, DAY)
        assert repo.git("status", "--porcelain") == ""

    def test_notes_with_content_are_renamed_to_the_version(self, repo):
        notes = release.NOTES_DIR
        repo.commit(
            {
                "CHANGELOG.md": changelog(ENTRY),
                notes + "unreleased.nl.md": "Nieuw\n",
                notes + "unreleased.en.md": "New\n",
            }
        )
        release.promote(repo.handle, TAG, DAY)

        assert sorted(os.listdir(repo.path / notes)) == ["2026.10.1.en.md", "2026.10.1.nl.md"]
        assert repo.read(notes + "2026.10.1.nl.md") == "Nieuw\n"
        status = repo.git("status", "--porcelain").splitlines()
        assert f"R  {notes}unreleased.nl.md -> {notes}2026.10.1.nl.md" in status

    def test_empty_notes_are_removed(self, repo):
        notes = release.NOTES_DIR
        repo.commit(
            {"CHANGELOG.md": changelog(ENTRY), notes + "unreleased.nl.md": "", notes + "unreleased.en.md": "\n"}
        )
        release.promote(repo.handle, TAG, DAY)
        assert not (repo.path / notes / "unreleased.nl.md").exists()
        assert f"D  {notes}unreleased.en.md" in repo.git("status", "--porcelain").splitlines()

    def test_a_one_language_note_is_refused_before_anything_is_written(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY), release.NOTES_DIR + "sub/unreleased.nl.md": "Nieuw\n"})
        with pytest.raises(release.ReleaseError, match=re.escape("sub/unreleased.nl.md has no sub/unreleased.en.md")):
            release.promote(repo.handle, TAG, DAY)
        assert repo.git("status", "--porcelain") == ""

    def test_main_lists_what_it_touched(self, repo, monkeypatch, capsys):
        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        monkeypatch.setattr(release, "today", lambda: DAY)
        assert release.main(["promote", "--tag", TAG]) == 0
        assert "updated CHANGELOG.md\n" in capsys.readouterr().out

    def test_main_reports_a_refusal(self, repo, capsys):
        assert release.main(["promote", "--tag", "v2026.1"]) == 1
        assert "error: 'v2026.1' is not a CalVer tag" in capsys.readouterr().err


class TestReleaseNotes:
    def test_the_body_is_the_section_with_the_component_versions(self, released):
        released.commit({"CHANGELOG.md": changelog("### Fixed\n\n- A fix."), "cli/plak_cli/__init__.py": "x = 2\n"})
        released.release(TAG, DAY)

        assert release.release_notes(released.handle, TAG) == (
            "### Fixed\n\n- A fix.\n\nCLI 2026.10.1\n\nPlugin unchanged (2026.9.1)\n"
        )

    def test_it_reads_the_tag_not_the_working_tree(self, released):
        released.commit({"CHANGELOG.md": changelog(ENTRY)})
        assert release.release_notes(released.handle, "v2026.9.1").startswith(ENTRY + "\n\nCLI 2026.9.1")

    @pytest.mark.parametrize("tag", ["v2026.9.2", "v2026.1"], ids=["no-such-tag", "invalid"])
    def test_a_tag_without_a_section_is_refused(self, released, tag):
        with pytest.raises(release.ReleaseError):
            release.release_notes(released.handle, tag)

    def test_an_empty_section_is_refused(self, repo):
        repo.commit({"CHANGELOG.md": changelog("", "## [2026.9.1]\n")})
        repo.git("tag", "v2026.9.1")
        with pytest.raises(release.ReleaseError, match=re.escape("No section for v2026.9.1")):
            release.release_notes(repo.handle, "v2026.9.1")

    def test_a_tag_without_the_manifests_is_refused(self, repo):
        repo.remove("plugin/.claude-plugin/plugin.json")
        repo.commit({"CHANGELOG.md": changelog("", "## [2026.9.1]\n\n- x")})
        repo.git("tag", "v2026.9.1")
        with pytest.raises(release.ReleaseError, match=re.escape("is missing at v2026.9.1")):
            release.release_notes(repo.handle, "v2026.9.1")

    def test_main_prints_the_notes(self, released, capsys):
        assert release.main(["notes", "--tag", "v2026.9.1"]) == 0
        assert capsys.readouterr().out.endswith("CLI 2026.9.1\n\nPlugin 2026.9.1\n")


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


class TestCheck:
    def test_a_shipped_change_with_an_entry_is_in_order(self, branch):
        branch.commit({"backend/src/plak/main.py": "app = 2\n", "CHANGELOG.md": with_entry(branch)})
        assert release.check(branch.handle, "beta") == release.CheckResult()

    def test_a_shipped_change_without_an_entry_warns(self, branch):
        branch.commit({"backend/src/plak/main.py": "app = 2\n"})
        result = release.check(branch.handle, "beta")
        assert result.errors == []
        assert result.warning == release.WARNING

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
        branch.commit({"backend/src/plak/main.py": "app = 2\n", "CHANGELOG.md": with_entry(branch)})
        branch.git("switch", "-q", "feature")
        branch.commit({"README.md": "better\n"})
        assert release.check(branch.handle, "beta").warning is None

    def test_the_pull_request_that_adds_the_changelog_does_not_warn(self, tmp_path):
        repo = Repo(tmp_path / "fresh")
        repo.path.mkdir()
        repo.git("init", "-q", "-b", "beta")
        repo.commit({"README.md": "x\n"})
        repo.git("switch", "-q", "-c", "feature")
        repo.commit({"CHANGELOG.md": changelog(ENTRY), "backend/src/plak/main.py": "app = 1\n"})
        assert release.check(repo.handle, "beta") == release.CheckResult()

    def test_a_malformed_unreleased_fails(self, branch):
        branch.commit({"CHANGELOG.md": with_entry(branch, "### Improved\n\n- x")})
        [error] = release.check(branch.handle, "beta").errors
        assert "'### Improved' is not one of" in error

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
        branch.commit({"CHANGELOG.md": edit(branch.read("CHANGELOG.md"))})
        [error] = release.check(branch.handle, "beta").errors
        assert error == (
            "CHANGELOG.md: [2026.9.1] was released as v2026.9.1 and is frozen; put the change under [Unreleased]."
        )

    def test_a_section_for_an_existing_tag_that_the_base_lacks_is_new(self, repo):
        """A version tagged by hand gets its section afterwards, in a pull
        request; the base has nothing there to freeze yet. Once that has
        landed, the section is frozen like any other."""
        repo.git("tag", "v2026.9.1")
        repo.git("switch", "-q", "-c", "feature")
        repo.commit({"CHANGELOG.md": changelog(ENTRY, "## [2026.9.1]\n\nWhat ran then.\n\n- x")})
        assert release.check(repo.handle, "beta").errors == []

        repo.git("switch", "-q", "beta")
        repo.git("merge", "-q", "--ff-only", "feature")
        repo.git("switch", "-q", "-c", "later")
        repo.commit({"CHANGELOG.md": changelog(ENTRY, "## [2026.9.1]\n\nWhat ran then.\n\n- y")})
        [error] = release.check(repo.handle, "beta").errors
        assert "[2026.9.1] was released as v2026.9.1 and is frozen" in error

    def test_a_section_without_a_tag_is_not_frozen(self, repo):
        repo.commit({"CHANGELOG.md": changelog("", "## [2026.9.1]\n\n- x")})
        repo.git("switch", "-q", "-c", "feature")
        repo.commit({"CHANGELOG.md": changelog("", "## [2026.9.1]\n\n- y")})
        assert release.check(repo.handle, "beta").errors == []

    @pytest.mark.parametrize(
        "files",
        [
            {"unreleased.nl.md": "Nieuw\n"},
            {"unreleased.en.md": "New\n"},
            {"2026.9.1.nl.md": "Nieuw\n"},
        ],
    )
    def test_a_note_in_one_language_fails(self, branch, files):
        notes = {release.NOTES_DIR + name: text for name, text in files.items()}
        branch.commit(notes | {"CHANGELOG.md": with_entry(branch)})
        [error] = release.check(branch.handle, "beta").errors
        assert "What's new note comes in Dutch and English" in error

    @pytest.mark.parametrize("path", ["frontend/src/App.vue", "cli/plak_cli/__init__.py"])
    def test_a_member_facing_change_without_a_note_gets_a_hint(self, branch, path):
        branch.commit({path: "changed\n", "CHANGELOG.md": with_entry(branch)})
        result = release.check(branch.handle, "beta")
        assert (result.errors, result.warning, result.hint) == ([], None, release.HINT)

    @pytest.mark.parametrize(
        "files",
        [
            {"frontend/src/App.vue": "x\n", "frontend/src/content/releases/unreleased.nl.md": "Nieuw\n",
             "frontend/src/content/releases/unreleased.en.md": "New\n"},
            {"frontend/src/App.test.ts": "x\n"},
            {"backend/src/plak/main.py": "app = 2\n"},
            {
                "frontend/src/content/releases/2026.9.1.nl.md": "a\n",
                "frontend/src/content/releases/2026.9.1.en.md": "b\n",
            },
        ],
        ids=["with-note", "test-only", "backend", "notes-only"],
    )
    def test_no_hint_otherwise(self, branch, files):
        branch.commit(files | {"CHANGELOG.md": with_entry(branch)})
        assert release.check(branch.handle, "beta").hint is None

    def test_hard_only_skips_the_warning_and_the_hint(self, branch):
        branch.commit({"frontend/src/App.vue": "x\n", "CHANGELOG.md": with_entry(branch, "### New\n\n- x")})
        result = release.check(branch.handle, "beta", hard_only=True)
        assert len(result.errors) == 1
        assert (result.warning, result.hint) == (None, None)


class TestCheckCommand:
    def test_errors_fail_with_annotations_and_a_summary(self, branch, monkeypatch, tmp_path, capsys):
        branch.commit({"CHANGELOG.md": with_entry(branch, "### New\n\n- x")})
        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

        assert release.main(["check", "--base", "beta"]) == 1
        assert capsys.readouterr().out.startswith("::error title=Changelog::CHANGELOG.md: [Unreleased]: '### New'")
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
        assert capsys.readouterr().out == "CHANGELOG.md and the What's new notes are in order.\n"
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
    """The feature branch with a shipped change and no entry, and an
    origin/beta to compare with."""
    branch.git("update-ref", "refs/remotes/origin/beta", "beta")
    branch.commit({"backend/src/plak/main.py": "app = 2\n"})
    return branch


class TestHook:
    def test_a_pull_request_without_an_entry_is_denied(self, pushed):
        decision = json.loads(release.hook(_event(pushed, "gh pr create --title x --body 'Fix it'")))
        assert decision["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
        assert "Shipped paths changed without an entry under [Unreleased]" in reason
        assert "changelog skill" in reason
        assert "No changelog entry: <reason>" in reason

    def test_a_pull_request_with_an_entry_passes(self, pushed):
        pushed.commit({"CHANGELOG.md": with_entry(pushed)})
        assert release.hook(_event(pushed, "gh pr create --fill")) is None

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
