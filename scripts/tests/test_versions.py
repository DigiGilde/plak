"""Shipped paths and the version lines a release writes (plak_release.versions)."""

from __future__ import annotations

import json
import re

import pytest
from release_support import BACKEND_LOCK, DAY, LOCK, PACKAGE, PACKAGE_LOCK, PLUGIN, PUBLICCODE, PYPROJECT, ROOT

from plak_release import errors, versions


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
        assert versions.is_shipped(path)

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
        assert not versions.is_shipped(path)

    def test_every_shipped_path_exists_in_this_repository(self):
        """A path that was moved away would silently stop counting."""
        missing = [path for path in versions.SHIPPED_PATHS + versions.NOT_SHIPPED if not (ROOT / path).exists()]
        assert missing == []


class TestVersionEdits:
    def test_only_the_project_version_changes_in_pyproject(self):
        edited = versions.set_pyproject_version(PYPROJECT, "2026.10.1")
        assert edited == PYPROJECT.replace('name = "plak"\nversion = "0.1.0"', 'name = "plak"\nversion = "2026.10.1"')
        assert versions.pyproject_version(edited) == "2026.10.1"

    @pytest.mark.parametrize(
        ("text", "reason"),
        [("[tool.x]\nversion = \"1\"\n", r"no \[project\] table"), ('[project]\nname = "plak"\n', "no version")],
    )
    def test_a_pyproject_without_a_project_version_is_refused(self, text, reason):
        with pytest.raises(errors.ReleaseError, match=reason):
            versions.set_pyproject_version(text, "2026.10.1")
        with pytest.raises(errors.ReleaseError, match=reason):
            versions.pyproject_version(text)

    def test_only_the_root_package_changes_in_the_lock(self):
        edited = versions.set_lock_version(LOCK, "2026.10.1")
        assert edited == LOCK.replace('"plak"\nversion = "0.1.0"', '"plak"\nversion = "2026.10.1"')
        assert 'name = "httpx"\nversion = "0.28.1"' in edited

    @pytest.mark.parametrize("text", ["version = 1\n", LOCK + LOCK[LOCK.index("[[package]]\nname = \"plak\"") :]])
    def test_a_lock_without_exactly_one_root_package_is_refused(self, text):
        with pytest.raises(errors.ReleaseError, match="expected one editable package 'plak'"):
            versions.set_lock_version(text, "2026.10.1")

    def test_the_plugin_manifest_keeps_its_formatting(self):
        edited = versions.set_plugin_version(PLUGIN, "2026.10.1")
        assert edited == PLUGIN.replace('"version": "0.3.1"', '"version": "2026.10.1"')

    @pytest.mark.parametrize("text", ['{\n  "name": "plak"\n}\n', '{\n  "a": {\n  "version": "1"\n}}\n'])
    def test_a_plugin_manifest_without_a_top_level_version_is_refused(self, text):
        with pytest.raises(errors.ReleaseError, match="no top-level"):
            versions.set_plugin_version(text, "2026.10.1")

    def test_a_pyproject_refusal_names_the_file(self):
        with pytest.raises(errors.ReleaseError, match=r"^backend/pyproject\.toml: no version"):
            versions.set_pyproject_version('[project]\nname = "plak-api"\n', "2026.10.1", "backend/pyproject.toml")

    def test_the_lock_edit_picks_the_named_root_package(self):
        edited = versions.set_lock_version(BACKEND_LOCK, "2026.10.1", "plak-api", "backend/uv.lock")
        assert edited == BACKEND_LOCK.replace('"plak-api"\nversion = "0.1.0"', '"plak-api"\nversion = "2026.10.1"')
        with pytest.raises(errors.ReleaseError, match=r"^backend/uv\.lock: expected one editable package 'plak-api'"):
            versions.set_lock_version(LOCK, "2026.10.1", "plak-api", "backend/uv.lock")

    def test_package_json_keeps_its_formatting(self):
        edited = versions.set_package_version(PACKAGE, "2026.10.1")
        assert edited == PACKAGE.replace('"version": "0.1.0"', '"version": "2026.10.1"')

    def test_package_lock_gets_the_version_twice_and_leaves_the_dependencies(self):
        edited = versions.set_package_version(PACKAGE_LOCK, "2026.10.1", "frontend/package-lock.json")
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
        with pytest.raises(errors.ReleaseError, match="^" + re.escape(f"frontend/package-lock.json: {reason}")):
            versions.set_package_version(text, "2026.10.1", "frontend/package-lock.json")

    def test_publiccode_gets_the_version_and_the_date(self):
        edited = versions.set_publiccode(PUBLICCODE, "2026.10.1", DAY)
        assert 'softwareVersion: "2026.10.1"\nreleaseDate: "2026-10-01"\n' in edited
        assert "license: EUPL-1.2" in edited

    def test_publiccode_without_the_lines_is_refused(self):
        with pytest.raises(errors.ReleaseError, match="no releaseDate line"):
            versions.set_publiccode('softwareVersion: "0.0.0"\n', "2026.10.1", DAY)

    def test_the_real_files_can_be_edited(self):
        """The edits are text edits on a format uv, Claude Code and
        publiccode own; this notices the day one of them changes shape."""
        pyproject = (ROOT / "cli/pyproject.toml").read_text(encoding="utf-8")
        lock = (ROOT / "cli/uv.lock").read_text(encoding="utf-8")
        plugin = (ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8")
        publiccode = (ROOT / "publiccode.yml").read_text(encoding="utf-8")

        assert versions.pyproject_version(versions.set_pyproject_version(pyproject, "2026.10.1")) == "2026.10.1"
        lock_diff = set(versions.set_lock_version(lock, "2026.10.1").splitlines()) ^ set(lock.splitlines())
        assert lock_diff == {'version = "2026.10.1"', f'version = "{versions.pyproject_version(pyproject)}"'}
        assert json.loads(versions.set_plugin_version(plugin, "2026.10.1"))["version"] == "2026.10.1"
        assert 'softwareVersion: "2026.10.1"' in versions.set_publiccode(publiccode, "2026.10.1", DAY)

        backend = (ROOT / "backend/pyproject.toml").read_text(encoding="utf-8")
        backend_lock = (ROOT / "backend/uv.lock").read_text(encoding="utf-8")
        package = (ROOT / "frontend/package.json").read_text(encoding="utf-8")
        package_lock = (ROOT / "frontend/package-lock.json").read_text(encoding="utf-8")

        edited = versions.set_pyproject_version(backend, "2026.10.1", "backend/pyproject.toml")
        assert versions.pyproject_version(edited, "backend/pyproject.toml") == "2026.10.1"
        lock_diff = set(versions.set_lock_version(backend_lock, "2026.10.1", "plak-api").splitlines())
        lock_diff ^= set(backend_lock.splitlines())
        assert lock_diff == {'version = "2026.10.1"', f'version = "{versions.pyproject_version(backend)}"'}
        assert json.loads(versions.set_package_version(package, "2026.10.1"))["version"] == "2026.10.1"
        edited = versions.set_package_version(package_lock, "2026.10.1", "frontend/package-lock.json")
        lock = json.loads(edited)
        assert (lock["version"], lock["packages"][""]["version"]) == ("2026.10.1", "2026.10.1")
        assert sum(a != b for a, b in zip(edited.splitlines(), package_lock.splitlines(), strict=True)) == 2

    def test_the_versions_the_real_files_carry_can_be_read_back(self):
        """What decide and check read is what prepare wrote."""
        for path, version_of in versions.VERSION_OF.items():
            found = version_of((ROOT / path).read_text(encoding="utf-8"))
            assert isinstance(found, str) and found, path
