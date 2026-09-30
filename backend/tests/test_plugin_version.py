"""The check that a change to the plak plugin comes with a new version
(.github/scripts/check_plugin_version.py, run by .github/workflows/plugin.yml).

Claude Code keeps an installed plugin until its manifest version changes, so a
change under plugin/ without one never reaches an existing install.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check_plugin_version.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_plugin_version", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load()


def _marketplace(version: object = "0.2.0", name: str = "plak") -> dict:
    return {"name": "plak", "plugins": [{"name": name, "source": "./plugin", "version": version}]}


class TestProblems:
    def test_a_raised_version_in_both_manifests_passes(self):
        assert check.problems(["plugin/skills/x/SKILL.md"], {"version": "0.1.0"}, {"version": "0.2.0"},
                              _marketplace("0.2.0")) == []

    @pytest.mark.parametrize("head", ["0.1.0", "0.0.9"])
    def test_a_plugin_change_without_a_higher_version_fails(self, head):
        [problem] = check.problems(["plugin/skills/x/SKILL.md"], {"version": "0.1.0"}, {"version": head},
                                   _marketplace(head))
        assert "raise the version above 0.1.0" in problem

    def test_versions_compare_as_numbers_not_as_text(self):
        assert check.problems(["plugin/x"], {"version": "0.9.0"}, {"version": "0.10.0"},
                              _marketplace("0.10.0")) == []

    @pytest.mark.parametrize(
        "changed",
        [["plugin/evals/x/prompt.md"], ["README.md", "backend/src/plak/main.py"], []],
        ids=["only-evals", "outside-plugin", "nothing"],
    )
    def test_a_change_outside_what_an_install_runs_needs_no_new_version(self, changed):
        assert check.problems(changed, {"version": "0.1.0"}, {"version": "0.1.0"}, _marketplace("0.1.0")) == []

    def test_the_marketplace_manifest_itself_needs_no_new_version(self):
        assert check.problems([".claude-plugin/marketplace.json"], {"version": "0.1.0"}, {"version": "0.1.0"},
                              _marketplace("0.1.0")) == []

    def test_manifests_that_disagree_fail_even_without_a_plugin_change(self):
        [problem] = check.problems(["README.md"], {"version": "0.2.0"}, {"version": "0.2.0"},
                                   _marketplace("0.1.0"))
        assert "differs from '0.2.0'" in problem

    @pytest.mark.parametrize("marketplace", [{"plugins": []}, {}, {"plugins": ["plak"]},
                                             {"plugins": [_marketplace()["plugins"][0]] * 2}])
    def test_a_marketplace_without_exactly_one_plak_entry_fails(self, marketplace):
        [problem] = check.problems([], {"version": "0.2.0"}, {"version": "0.2.0"}, marketplace)
        assert "exactly one entry" in problem

    @pytest.mark.parametrize("version", [None, "1.0", "v1.0.0", "1.0.0-beta", "01.0.0", 1])
    def test_a_version_that_is_not_major_minor_patch_fails(self, version):
        found = check.problems(["plugin/x"], {"version": "0.1.0"}, {"version": version}, _marketplace(version))
        assert found == [f"{check.PLUGIN_MANIFEST}: version {version!r} is not MAJOR.MINOR.PATCH."]

    @pytest.mark.parametrize("base", [None, {}, {"version": "oud"}])
    def test_without_a_usable_base_version_there_is_nothing_to_raise_above(self, base):
        assert check.problems(["plugin/x"], base, {"version": "0.1.0"}, _marketplace("0.1.0")) == []


def _git(repo: Path, *args: str) -> str:
    # git via PATH, like the justfile does; every argument comes from this file.
    command = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.nl", "-c", "commit.gpgsign=false"]
    return subprocess.run(  # noqa: S603
        [*command, *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _write_manifests(repo: Path, version: str, marketplace_version: str | None = None) -> None:
    (repo / "plugin" / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (repo / ".claude-plugin").mkdir(exist_ok=True)
    (repo / "plugin" / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "plak", "version": version}))
    (repo / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(_marketplace(marketplace_version or version))
    )


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A repository whose `base` branch holds the plugin at 0.1.0, checked
    out on a feature branch."""
    _git(tmp_path, "init", "-q", "-b", "base")
    _write_manifests(tmp_path, "0.1.0")
    (tmp_path / "plugin" / "SKILL.md").write_text("oud\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "switch", "-q", "-c", "feature")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _commit(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "change")


class TestMain:
    def test_a_skill_change_without_a_new_version_fails_with_an_annotation(self, repo, capsys):
        (repo / "plugin" / "SKILL.md").write_text("nieuw\n")
        _commit(repo)

        assert check.main(["check", "base"]) == 1
        assert capsys.readouterr().out.startswith("::error::plugin/ changed, so raise the version above 0.1.0")

    def test_a_skill_change_with_a_new_version_passes(self, repo, capsys):
        (repo / "plugin" / "SKILL.md").write_text("nieuw\n")
        _write_manifests(repo, "0.2.0")
        _commit(repo)

        assert check.main(["check", "base"]) == 0
        assert "Plugin version 0.2.0 is fine" in capsys.readouterr().out

    def test_an_eval_change_alone_passes(self, repo):
        (repo / "plugin" / "evals").mkdir()
        (repo / "plugin" / "evals" / "prompt.md").write_text("vraag\n")
        _commit(repo)

        assert check.main(["check", "base"]) == 0

    def test_only_the_commits_of_the_branch_count_not_what_landed_on_base(self, repo):
        """base...HEAD: a plugin change that landed on base since the branch
        started is base's own, and does not ask this branch for a version."""
        _git(repo, "switch", "-q", "base")
        (repo / "plugin" / "SKILL.md").write_text("elders\n")
        _write_manifests(repo, "0.2.0")
        _commit(repo)
        _git(repo, "switch", "-q", "feature")
        (repo / "README.md").write_text("docs\n")
        _commit(repo)

        assert check.main(["check", "base"]) == 0

    def test_a_base_without_the_plugin_has_nothing_to_raise_above(self, tmp_path, monkeypatch):
        _git(tmp_path, "init", "-q", "-b", "base")
        (tmp_path / "README.md").write_text("leeg\n")
        _commit(tmp_path)
        _git(tmp_path, "switch", "-q", "-c", "feature")
        _write_manifests(tmp_path, "0.1.0")
        _commit(tmp_path)
        monkeypatch.chdir(tmp_path)

        assert check.main(["check", "base"]) == 0

    def test_disagreeing_manifests_fail(self, repo, capsys):
        _write_manifests(repo, "0.2.0", marketplace_version="0.1.0")
        _commit(repo)

        assert check.main(["check", "base"]) == 1
        assert "differs from '0.2.0'" in capsys.readouterr().out

    def test_without_a_base_ref_it_says_how_to_call_it(self, capsys):
        assert check.main(["check"]) == 2
        assert capsys.readouterr().err.strip() == "Usage: check_plugin_version.py <base-ref>"
