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


def changelog(unreleased: str = "", released: str = "") -> str:
    text = "# Changelog\n\nWhat changed, newest first.\n\n## [Unreleased]\n"
    if unreleased:
        text += "\n" + unreleased.strip("\n") + "\n"
    if released:
        text += "\n" + released.strip("\n") + "\n"
    return release.with_links(text)


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

    def release(self, tag: str, day: date, spec: str | None = None, diff=None) -> None:
        """The cycle the release workflow will run: promote, commit, tag."""
        spec, diff = spec or SPEC, diff or NO_CHANGES
        release.promote(self.handle, tag, day, spec, diff)
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
    repo.commit({"CHANGELOG.md": changelog(ENTRY)})
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
        assert release.changelog_links(["Unreleased", "2026.10.4.1", "2026.10.4", "2026.9.30"]) == [
            f"[Unreleased]: {self.BASE}/compare/v2026.10.4.1...HEAD",
            f"[2026.10.4.1]: {self.BASE}/compare/v2026.10.4...v2026.10.4.1",
            f"[2026.10.4]: {self.BASE}/compare/v2026.9.30...v2026.10.4",
            f"[2026.9.30]: {self.BASE}/releases/tag/v2026.9.30",
        ]

    def test_without_a_release_there_is_nothing_to_link(self):
        assert release.changelog_links(["Unreleased"]) == []
        text = "# Changelog\n\n## [Unreleased]\n\n[Unreleased]: https://example.org\n"
        assert release.with_links(text) == "# Changelog\n\n## [Unreleased]\n"

    def test_the_links_are_written_anew_and_nothing_else_moves(self):
        stale = changelog(ENTRY, "## [2026.9.1]\n\n- Old.").replace("v2026.9.1...HEAD", "v2026.1.1...HEAD")
        fresh = release.with_links(stale)
        assert fresh == changelog(ENTRY, "## [2026.9.1]\n\n- Old.")
        assert release.with_links(fresh) == fresh

    def test_the_links_are_no_part_of_the_last_section(self):
        """So the oldest release's notes and its freeze ignore them."""
        sections = release.split_sections(changelog("", "## [2026.9.1]\n\n- Old."))
        assert sections[-1].body == "- Old."

    @pytest.mark.parametrize(
        "edit",
        [
            lambda text: text[: text.index("\n[Unreleased]: ")] + "\n",
            lambda text: text.replace("/releases/tag/v2026.9.1", "/releases/tag/v2026.9.2"),
            lambda text: text + "[extra]: https://example.org\n",
        ],
        ids=["missing", "wrong", "extra"],
    )
    def test_links_that_do_not_follow_the_headings_fail_the_check(self, edit):
        text = edit(changelog(ENTRY, "## [2026.9.1]\n\n- Old."))
        assert release.changelog_problems(text) == [
            "CHANGELOG.md: the link references at the bottom are not the ones its headings need; "
            "`release.py links` writes them."
        ]

    def test_main_writes_them(self, repo, capsys):
        (repo.path / "CHANGELOG.md").write_text(
            changelog(ENTRY, "## [2026.9.1]\n\n- Old.").split("\n[Unreleased]: ")[0] + "\n", encoding="utf-8"
        )
        assert release.main(["links"]) == 0
        assert capsys.readouterr().out == "updated CHANGELOG.md\n"
        assert repo.read("CHANGELOG.md") == changelog(ENTRY, "## [2026.9.1]\n\n- Old.")


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
        """No entry, no release notes, no release, shipped path or not.
        The change reaches production with the next release that does have
        an entry."""
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

        touched = release.promote(repo.handle, TAG, DAY, SPEC, NO_CHANGES)

        assert repo.read("CHANGELOG.md") == (
            "# Changelog\n\nWhat changed, newest first.\n\n## [Unreleased]\n\n"
            f"## [2026.10.1]\n\n{ENTRY}\n\n{older}\n\n"
            "[Unreleased]: https://github.com/DigiGilde/plak/compare/v2026.10.1...HEAD\n"
            "[2026.10.1]: https://github.com/DigiGilde/plak/compare/v2026.9.1...v2026.10.1\n"
            "[2026.9.1]: https://github.com/DigiGilde/plak/releases/tag/v2026.9.1\n"
        )
        assert release.changelog_problems(repo.read("CHANGELOG.md")) == []
        staged = repo.git("diff", "--cached", "--name-only").split()
        assert sorted(staged) == sorted(touched)

    def test_the_first_release_sets_every_component_version(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        release.promote(repo.handle, TAG, DAY, SPEC, NO_CHANGES)

        assert _versions(repo) == ("2026.10.1", "2026.10.1")
        assert 'name = "plak"\nversion = "2026.10.1"' in repo.read("cli/uv.lock")
        assert 'softwareVersion: "2026.10.1"\nreleaseDate: "2026-10-01"' in repo.read("publiccode.yml")
        assert _image_versions(repo) == {"2026.10.1"}

    def test_a_backend_change_leaves_the_cli_and_plugin_versions(self, released):
        released.commit({"CHANGELOG.md": changelog(ENTRY), "backend/src/plak/app.py": "app = 2\n"})
        touched = release.promote(released.handle, TAG, DAY, SPEC, NO_CHANGES)

        assert _versions(released) == ("2026.9.1", "2026.9.1")
        assert sorted(touched) == [
            "CHANGELOG.md",
            "backend/openapi.json",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "frontend/package-lock.json",
            "frontend/package.json",
            "publiccode.yml",
        ]
        assert 'softwareVersion: "2026.10.1"' in released.read("publiccode.yml")

    def test_the_image_gets_every_release_also_when_only_the_cli_changed(self, released):
        """Every tag builds and ships a new image, whatever changed."""
        released.commit({"CHANGELOG.md": changelog(ENTRY), "cli/plak_cli/__init__.py": "x = 2\n"})
        release.promote(released.handle, TAG, DAY, SPEC, NO_CHANGES)
        assert _image_versions(released) == {"2026.10.1"}
        assert 'name = "plak"\nversion = "9.9.9"' in released.read("backend/uv.lock")

    def test_a_missing_image_package_file_stops_the_release(self, released):
        released.remove("frontend/package-lock.json")
        released.commit({"CHANGELOG.md": changelog(ENTRY)})
        with pytest.raises(release.ReleaseError, match=re.escape("frontend/package-lock.json is missing")):
            release.promote(released.handle, TAG, DAY, SPEC, NO_CHANGES)
        assert released.read("CHANGELOG.md") == changelog(ENTRY)

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
        release.promote(released.handle, TAG, DAY, SPEC, NO_CHANGES)
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
        release.promote(released.handle, TAG, DAY, SPEC, NO_CHANGES)
        assert _versions(released) == ("9.9.9", "9.9.9")

    def test_a_file_that_did_not_exist_at_the_last_tag_is_a_change(self, repo):
        section = "## [2026.9.1]\n\n- x"
        repo.remove("cli/uv.lock")
        repo.commit({"CHANGELOG.md": changelog("", section)})
        repo.git("tag", "v2026.9.1")
        repo.commit({"CHANGELOG.md": changelog(ENTRY, section), "cli/uv.lock": LOCK})
        release.promote(repo.handle, TAG, DAY, SPEC, NO_CHANGES)
        assert _versions(repo) == ("2026.10.1", "0.3.1")

    def test_a_shipped_change_without_an_entry_is_refused_and_writes_nothing(self, released):
        """Without entries there are no release notes, so nothing is made up
        from commit subjects either."""
        released.commit({"backend/src/plak/main.py": "app = 2\n"}, "Update urllib3 to 2.8.0")
        with pytest.raises(release.ReleaseError) as refused:
            release.promote(released.handle, TAG, DAY, SPEC, NO_CHANGES)
        assert refused.value.problems == [
            "Nothing to release: [Unreleased] has no entries, and they are the release notes."
        ]
        assert released.git("status", "--porcelain") == ""

    def test_without_a_tag_or_entries_there_is_nothing_to_release(self, repo):
        with pytest.raises(release.ReleaseError, match="Nothing to release"):
            release.promote(repo.handle, TAG, DAY, SPEC, NO_CHANGES)

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
            release.promote(released.handle, tag, DAY, SPEC, NO_CHANGES)

    def test_a_section_for_the_tag_already_in_the_changelog_is_refused(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY, "## [2026.10.1]\n\n- x")})
        with pytest.raises(release.ReleaseError, match=f"already has a section for {TAG}"):
            release.promote(repo.handle, TAG, DAY, SPEC, NO_CHANGES)

    def test_a_refusal_writes_nothing(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        repo.remove("publiccode.yml")
        with pytest.raises(release.ReleaseError, match=re.escape("publiccode.yml is missing")):
            release.promote(repo.handle, TAG, DAY, SPEC, NO_CHANGES)
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
        release.promote(repo.handle, TAG, DAY, SPEC, NO_CHANGES)

        assert sorted(os.listdir(repo.path / notes)) == ["2026.10.1.en.md", "2026.10.1.nl.md"]
        assert repo.read(notes + "2026.10.1.nl.md") == "Nieuw\n"
        status = repo.git("status", "--porcelain").splitlines()
        assert f"R  {notes}unreleased.nl.md -> {notes}2026.10.1.nl.md" in status

    def test_empty_notes_are_removed(self, repo):
        notes = release.NOTES_DIR
        repo.commit(
            {"CHANGELOG.md": changelog(ENTRY), notes + "unreleased.nl.md": "", notes + "unreleased.en.md": "\n"}
        )
        release.promote(repo.handle, TAG, DAY, SPEC, NO_CHANGES)
        assert not (repo.path / notes / "unreleased.nl.md").exists()
        assert f"D  {notes}unreleased.en.md" in repo.git("status", "--porcelain").splitlines()

    def test_a_one_language_note_is_refused_before_anything_is_written(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY), release.NOTES_DIR + "sub/unreleased.nl.md": "Nieuw\n"})
        with pytest.raises(release.ReleaseError, match=re.escape("sub/unreleased.nl.md has no sub/unreleased.en.md")):
            release.promote(repo.handle, TAG, DAY, SPEC, NO_CHANGES)
        assert repo.git("status", "--porcelain") == ""

    def test_main_lists_what_it_touched(self, repo, monkeypatch, tmp_path, capsys):
        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        monkeypatch.setattr(release, "today", lambda: DAY)
        (tmp_path / "openapi.json").write_text(SPEC, encoding="utf-8")
        args = ["promote", "--tag", TAG, "--spec", str(tmp_path / "openapi.json"), "--oasdiff", "unused"]
        assert release.main(args) == 0
        out = capsys.readouterr().out
        assert "updated CHANGELOG.md\n" in out
        assert "updated backend/openapi.json\n" in out

    def test_main_reports_a_refusal(self, repo, capsys):
        assert release.main(["promote", "--tag", "v2026.1", "--spec", "x", "--oasdiff", "x"]) == 1
        assert "error: x: cannot read the API schema." in capsys.readouterr().err

    def test_main_reports_a_refused_tag(self, repo, tmp_path, capsys):
        (tmp_path / "openapi.json").write_text(SPEC, encoding="utf-8")
        args = ["promote", "--tag", "v2026.1", "--spec", str(tmp_path / "openapi.json"), "--oasdiff", "x"]
        assert release.main(args) == 1
        assert "error: 'v2026.1' is not a CalVer tag" in capsys.readouterr().err


class TestReleaseNotes:
    def test_the_body_is_the_section_with_the_component_versions(self, released):
        released.commit({"CHANGELOG.md": changelog("### Fixed\n\n- A fix."), "cli/plak_cli/__init__.py": "x = 2\n"})
        released.release(TAG, DAY)

        assert release.release_notes(released.handle, TAG) == (
            "### Fixed\n\n- A fix.\n\nCLI 2026.10.1\n\nPlugin unchanged (2026.9.1)\n\nAPI 1.0.0\n"
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
        repo.commit({"CHANGELOG.md": changelog("", "## [2026.9.1]\n\n- x")})
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


class TestPromoteTheApi:
    def test_the_first_release_commits_the_schema_at_the_version_the_code_has(self, repo):
        def unused(base, head):
            raise AssertionError("nothing to compare with")

        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        release.promote(repo.handle, TAG, DAY, SPEC, unused)
        assert json.loads(repo.read("backend/openapi.json"))["info"]["version"] == "1.0.0"
        assert repo.read("backend/src/plak/main.py") == MAIN

    def test_a_hand_made_tag_without_a_schema_counts_as_the_first(self, repo):
        repo.commit({"CHANGELOG.md": changelog("", "## [2026.9.1]\n\n- x")})
        repo.git("tag", "v2026.9.1")
        repo.commit({"CHANGELOG.md": changelog(ENTRY, "## [2026.9.1]\n\n- x")})
        release.promote(repo.handle, TAG, DAY, SPEC, NO_CHANGES)
        assert json.loads(repo.read("backend/openapi.json"))["info"]["version"] == "1.0.0"

    def test_an_addition_raises_the_minor_in_the_schema_and_the_code(self, released):
        head = spec_with(paths=("/a", "/b"))
        seen = []

        def diff(base, revision):
            seen.append((base, revision))
            return [change(1)]

        released.commit({"CHANGELOG.md": changelog(ENTRY)})
        release.promote(released.handle, TAG, DAY, head, diff)

        assert seen == [(released.git("show", "v2026.9.1:backend/openapi.json"), head)]
        assert json.loads(released.read("backend/openapi.json")) == {
            **json.loads(head),
            "info": {**json.loads(head)["info"], "version": "1.1.0"},
        }
        assert 'API_VERSION = "1.1.0"' in released.read("backend/src/plak/main.py")

    def test_a_breaking_change_stops_the_release_before_it_writes(self, released):
        released.commit({"CHANGELOG.md": changelog(ENTRY)})
        with pytest.raises(release.ReleaseError, match="Breaking API change"):
            release.promote(released.handle, TAG, DAY, spec_with(paths=()), lambda b, h: [change(3)])
        assert released.git("status", "--porcelain") == ""

    def test_a_schema_that_is_not_json_stops_the_release(self, repo):
        repo.commit({"CHANGELOG.md": changelog(ENTRY)})
        with pytest.raises(release.ReleaseError, match="The API schema is not JSON"):
            release.promote(repo.handle, TAG, DAY, "{", NO_CHANGES)

    def test_a_committed_schema_that_is_not_json_stops_the_release(self, released):
        released.commit({"CHANGELOG.md": changelog(ENTRY)})
        released.git("tag", "-d", "v2026.9.1")
        released.git("checkout", "-q", "HEAD~1")
        released.commit({"backend/openapi.json": "{"})
        released.git("tag", "v2026.9.1")
        released.git("checkout", "-q", "-")
        with pytest.raises(release.ReleaseError, match=r"backend/openapi\.json at v2026\.9\.1 is not JSON"):
            release.promote(released.handle, TAG, DAY, SPEC, NO_CHANGES)


UNRELEASED_NOTES = {
    "frontend/src/content/releases/unreleased.nl.md": "Nieuw.\n",
    "frontend/src/content/releases/unreleased.en.md": "New.\n",
}


class TestVerifyStaged:
    """The publishing job applies a patch from a job that ran the
    dependencies, then pushes it past every review with the App. So what
    it commits has to be exactly what promote writes."""

    @pytest.fixture
    def promoted(self, released) -> Repo:
        """v2026.9.1 released, then a change with an entry and a note,
        promoted to TAG with an addition to the API."""
        released.commit({"CHANGELOG.md": changelog(ENTRY), "cli/plak_cli/__init__.py": "x = 2\n", **UNRELEASED_NOTES})
        release.promote(released.handle, TAG, DAY, spec_with(paths=("/a", "/b")), lambda b, h: [change(1)])
        return released

    def test_what_promote_writes_passes(self, promoted):
        assert release.verify_staged(promoted.handle, TAG) == []

    def test_the_patch_survives_the_hand_over_to_a_clean_checkout(self, promoted, tmp_path):
        """What release.yml does between its two jobs: the staged release as
        a patch, applied to a fresh clone of the same commit."""
        patch = tmp_path / "release.patch"
        patch.write_text(promoted.git("diff", "--cached", "--binary", "--no-renames"), encoding="utf-8")
        clean = Repo(tmp_path / "clean")
        subprocess.run(  # noqa: S603
            ["git", "clone", "-q", str(promoted.path), str(clean.path)],  # noqa: S607
            check=True,
            capture_output=True,
        )
        clean.git("apply", "--cached", "--binary", str(patch))
        assert release.verify_staged(clean.handle, TAG) == []
        assert "frontend/src/content/releases/2026.10.1.nl.md" in clean.git("diff", "--cached", "--name-only")

    def test_main_says_so(self, promoted, capsys):
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
            ("CHANGELOG.md", changelog("", "## [2026.10.1]\n\n- Something else."), "CHANGELOG.md is not"),
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
                "frontend/src/content/releases/2026.10.1.nl.md",
                "<script>x</script>\n",
                "frontend/src/content/releases/2026.10.1.nl.md is not a file a release writes.",
            ),
            (
                "frontend/src/content/releases/2026.9.2.en.md",
                "New.\n",
                "frontend/src/content/releases/2026.9.2.en.md is not a file a release writes.",
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
        ],
    )
    def test_anything_beyond_the_release_is_refused(self, promoted, path, text, problem):
        promoted.write({path: text})
        promoted.git("add", "--", path)
        problems = release.verify_staged(promoted.handle, TAG)
        assert len(problems) == 1 and problems[0].startswith(problem), problems

    def test_an_executable_or_a_symlink_is_refused(self, promoted):
        """Same text, other kind of file: what the release writes is plain."""
        promoted.git("update-index", "--chmod=+x", "publiccode.yml")
        (promoted.path / "CHANGELOG.md").unlink()
        (promoted.path / "CHANGELOG.md").symlink_to("README.md")
        promoted.git("add", "CHANGELOG.md")
        assert sorted(release.verify_staged(promoted.handle, TAG)) == [
            "CHANGELOG.md is not staged as a plain file.",
            "publiccode.yml is not staged as a plain file.",
        ]

    def test_the_schema_has_to_declare_the_version_api_version_gets(self, promoted):
        """API_VERSION follows the staged schema; one without that line
        staged has to match what the code has."""
        promoted.git("reset", "-q", "--", "backend/src/plak/main.py")
        assert release.verify_staged(promoted.handle, TAG) == [
            "backend/src/plak/main.py is not what the release writes for v2026.10.1."
        ]

    def test_a_schema_without_a_semver_version_is_refused(self, promoted):
        promoted.write({"backend/openapi.json": spec_with("one")})
        promoted.git("add", "backend/openapi.json")
        problems = release.verify_staged(promoted.handle, TAG)
        assert problems[0] == "backend/openapi.json: info.version 'one' is not MAJOR.MINOR.PATCH."

    def test_a_file_the_release_removes_has_to_be_an_unreleased_note(self, promoted):
        promoted.git("rm", "-q", "--cached", "README.md")
        assert release.verify_staged(promoted.handle, TAG) == ["README.md is not a file a release writes."]

    def test_a_version_file_that_disappears_is_refused(self, promoted):
        promoted.git("rm", "-q", "--cached", "publiccode.yml")
        assert release.verify_staged(promoted.handle, TAG) == [
            "publiccode.yml is not what the release writes for v2026.10.1."
        ]

    def test_api_version_moves_only_with_the_schema(self, promoted):
        """The schema unstaged, API_VERSION still raised: refused."""
        promoted.git("reset", "-q", "--", "backend/openapi.json")
        assert release.verify_staged(promoted.handle, TAG) == [
            "backend/src/plak/main.py is not what the release writes for v2026.10.1."
        ]

    def test_a_file_promote_could_not_have_written_is_refused(self, released):
        """HEAD's publiccode.yml has no version lines, so promote would have
        refused; a staged one is not its work."""
        released.commit({"publiccode.yml": "name: Plak\n"})
        released.write({"publiccode.yml": 'name: Plak\nsoftwareVersion: "2026.10.1"\n'})
        released.git("add", "publiccode.yml")
        assert release.verify_staged(released.handle, TAG) == [
            "publiccode.yml is not what the release writes for v2026.10.1."
        ]

    def test_the_tag_has_to_be_the_one_promoted(self, promoted):
        """Another tag gives another changelog section, and notes named
        after another version."""
        problems = release.verify_staged(promoted.handle, "v2026.10.2")
        assert "CHANGELOG.md is not what the release writes for v2026.10.2." in problems

    def test_nothing_staged_or_no_calver_tag_is_refused(self, released):
        assert release.verify_staged(released.handle, TAG) == ["Nothing is staged to release."]
        assert release.verify_staged(released.handle, "v2026.1")[0].startswith("'v2026.1' is not a CalVer tag")

    def test_main_fails_with_every_problem(self, promoted, capsys):
        promoted.write({"Makefile": "all:\n"})
        promoted.git("add", "Makefile")
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
        released.commit({"CHANGELOG.md": changelog(ENTRY)})
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
        """With the links following the edit, so only the freeze is left."""
        branch.commit({"CHANGELOG.md": release.with_links(edit(branch.read("CHANGELOG.md")))})
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
