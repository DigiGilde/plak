"""The ruleset drift check (.github/scripts/rulesets.py): what in the live
rulesets departs from .github/rulesets/*.json, and what it leaves alone."""

from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "rulesets.py"


def _load():
    spec = importlib.util.spec_from_file_location("rulesets", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["rulesets"] = module
    spec.loader.exec_module(module)
    return module


rulesets = _load()

FILE = {
    "name": "beta",
    "target": "branch",
    "enforcement": "active",
    "conditions": {"ref_name": {"include": ["refs/heads/beta"], "exclude": []}},
    "bypass_actors": [{"actor_id": 0, "actor_type": "Integration", "bypass_mode": "always"}],
    "rules": [
        {
            "type": "pull_request",
            "parameters": {"required_approving_review_count": 0, "allowed_merge_methods": ["merge", "squash"]},
        },
        {
            "type": "required_status_checks",
            "parameters": {"required_status_checks": [{"context": "ci / a"}, {"context": "ci / b"}]},
        },
    ],
}


def _live() -> dict:
    """What GitHub returns for FILE once it is applied: the same, in another
    order, plus server fields and real bypass actors."""
    live = copy.deepcopy(FILE)
    live["rules"].reverse()
    live["rules"][0]["parameters"]["required_status_checks"].reverse()
    live["rules"][1]["parameters"]["extra_server_default"] = True
    live.update(id=1, source="o/r", source_type="Repository", node_id="x", _links={}, current_user_can_bypass="never")
    live["bypass_actors"] = [{"actor_id": 99, "actor_type": "Integration", "bypass_mode": "always"}]
    return live


def _compare(file: dict, live: dict) -> dict:
    return rulesets.compare({file["name"]: file}, {live["name"]: live})


def _check_rule(ruleset: dict) -> dict:
    return next(rule for rule in ruleset["rules"] if rule["type"] == "required_status_checks")["parameters"]


class TestCompare:
    def test_an_applied_file_is_in_sync_whatever_the_server_adds(self) -> None:
        assert _compare(FILE, _live()) == {}

    def test_bypass_actors_are_ignored_even_when_live_has_none(self) -> None:
        live = _live()
        del live["bypass_actors"]
        assert _compare(FILE, live) == {}

    def test_a_missing_live_ruleset_is_drift(self) -> None:
        assert rulesets.compare({"beta": FILE}, {}) == {"beta": ["no live ruleset has this name"]}

    def test_a_live_ruleset_without_a_file_is_drift(self) -> None:
        other = {**_live(), "name": "by hand"}
        report = rulesets.compare({"beta": FILE}, {"beta": _live(), "by hand": other})
        assert report == {"by hand": ["live ruleset without a file in .github/rulesets/"]}

    def test_a_changed_rule_parameter_is_drift(self) -> None:
        live = _live()
        parameters = next(r for r in live["rules"] if r["type"] == "pull_request")["parameters"]
        parameters["required_approving_review_count"] = 1
        assert _compare(FILE, live) == {
            "beta": ["rules.pull_request.required_approving_review_count: the file says 0, live says 1"]
        }

    def test_a_changed_top_level_key_is_drift(self) -> None:
        live = _live()
        live["enforcement"] = "evaluate"
        assert _compare(FILE, live) == {"beta": ["enforcement: the file says 'active', live says 'evaluate'"]}

    def test_a_required_check_missing_live_is_drift(self) -> None:
        live = _live()
        _check_rule(live)["required_status_checks"].pop()
        report = _compare(FILE, live)["beta"]
        assert len(report) == 1
        assert report[0].startswith("rules.required_status_checks.required_status_checks: missing live:")
        assert "ci / " in report[0]

    def test_an_extra_required_check_is_drift(self) -> None:
        live = _live()
        _check_rule(live)["required_status_checks"].append({"context": "ci / hand-added"})
        report = _compare(FILE, live)["beta"]
        assert len(report) == 1
        assert report[0].endswith("extra live: ci / hand-added")

    def test_a_check_pinned_to_an_integration_differs_from_an_unpinned_one(self) -> None:
        live = _live()
        _check_rule(live)["required_status_checks"][0]["integration_id"] = 15368
        report = _compare(FILE, live)["beta"]
        assert any("(integration 15368)" in line for line in report)

    def test_a_rule_missing_live_is_drift(self) -> None:
        live = _live()
        live["rules"] = [r for r in live["rules"] if r["type"] != "pull_request"]
        assert _compare(FILE, live) == {"beta": ["rules.pull_request: the file has it, live does not"]}

    def test_a_rule_no_file_asks_for_is_drift(self) -> None:
        live = _live()
        live["rules"].append({"type": "deletion"})
        assert _compare(FILE, live) == {"beta": ["rules.deletion: live has it, the file does not"]}

    def test_a_list_parameter_that_differs_is_drift(self) -> None:
        live = _live()
        parameters = next(r for r in live["rules"] if r["type"] == "pull_request")["parameters"]
        parameters["allowed_merge_methods"] = ["merge", "rebase"]
        report = _compare(FILE, live)["beta"]
        assert "missing live: squash" in report[0]
        assert "extra live: rebase" in report[1]

    def test_a_key_the_file_has_and_a_rule_lacks_is_drift(self) -> None:
        live = _live()
        parameters = next(r for r in live["rules"] if r["type"] == "pull_request")["parameters"]
        del parameters["required_approving_review_count"]
        assert _compare(FILE, live) == {
            "beta": ["rules.pull_request.required_approving_review_count: the file has it, live does not"]
        }

    def test_a_rule_without_parameters_normalises_to_an_empty_dict(self) -> None:
        assert rulesets.normalise({"rules": [{"type": "deletion"}]})["rules"] == {"deletion": {}}


class TestFiles:
    def test_the_files_are_read_by_name(self, tmp_path: Path) -> None:
        (tmp_path / "x.json").write_text(json.dumps({"name": "alpha"}), encoding="utf-8")
        (tmp_path / "ignored.txt").write_text("not json", encoding="utf-8")
        assert rulesets.read_files(tmp_path) == {"alpha": {"name": "alpha"}}

    def test_the_real_files_are_all_found_by_name(self) -> None:
        assert set(rulesets.read_files(rulesets.RULESETS_DIR)) == {
            "beta",
            "beta history",
            "release tags",
            "release tags fixed",
        }


class TestLive:
    def test_every_ruleset_is_fetched_by_id_and_keyed_by_name(self) -> None:
        calls = []
        answers = {
            "repos/o/r/rulesets?per_page=100": [{"id": 1}, {"id": 2}],
            "repos/o/r/rulesets/1": {"name": "one"},
            "repos/o/r/rulesets/2": {"name": "two"},
        }

        def api(endpoint: str):
            calls.append(endpoint)
            return answers[endpoint]

        assert rulesets.fetch_live("o/r", api) == {"one": {"name": "one"}, "two": {"name": "two"}}
        assert calls[0] == "repos/o/r/rulesets?per_page=100"

    def test_gh_api_returns_the_parsed_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def run(cmd, **kwargs):
            assert cmd == ["gh", "api", "repos/o/r/rulesets"]
            return subprocess.CompletedProcess(cmd, 0, stdout='[{"id": 1}]', stderr="")

        monkeypatch.setattr(rulesets.subprocess, "run", run)
        assert rulesets.gh_api("repos/o/r/rulesets") == [{"id": 1}]

    def test_gh_api_failure_is_an_error_with_the_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            rulesets.subprocess,
            "run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="HTTP 403\n"),
        )
        with pytest.raises(RuntimeError, match="gh api repos/o/r/rulesets failed: HTTP 403"):
            rulesets.gh_api("repos/o/r/rulesets")


class TestMain:
    @pytest.fixture
    def directory(self, tmp_path: Path) -> Path:
        (tmp_path / "beta.json").write_text(json.dumps(FILE), encoding="utf-8")
        return tmp_path

    def _run(self, monkeypatch, directory: Path, live: dict, *extra: str) -> int:
        monkeypatch.setattr(rulesets, "fetch_live", lambda repo: live)
        return rulesets.main(["check", "--repo", "o/r", "--dir", str(directory), *extra])

    def test_no_drift_exits_zero(self, monkeypatch, directory: Path, capsys) -> None:
        assert self._run(monkeypatch, directory, {"beta": _live()}) == 0
        assert capsys.readouterr().out == "1 live rulesets match .github/rulesets/.\n"

    def test_drift_is_printed_per_ruleset_and_exits_one(self, monkeypatch, directory: Path, capsys) -> None:
        live = _live()
        live["enforcement"] = "disabled"
        assert self._run(monkeypatch, directory, {"beta": live, "by hand": {**live, "name": "by hand"}}) == 1
        out = capsys.readouterr().out
        assert "Ruleset 'beta' differs:\n  - enforcement: the file says 'active', live says 'disabled'\n" in out
        assert "Ruleset 'by hand' differs:\n  - live ruleset without a file" in out
        assert "docs/releasing.md" in out

    def test_a_failing_gh_exits_two_with_the_reason(self, monkeypatch, directory: Path, capsys) -> None:
        def broken(repo):
            raise RuntimeError("gh api failed: HTTP 401")

        monkeypatch.setattr(rulesets, "fetch_live", broken)
        assert rulesets.main(["check", "--repo", "o/r", "--dir", str(directory)]) == 2
        assert "HTTP 401" in capsys.readouterr().err

    def test_the_repo_comes_from_the_environment(self, monkeypatch, directory: Path) -> None:
        seen = []
        monkeypatch.setenv("GITHUB_REPOSITORY", "env/repo")
        monkeypatch.setattr(rulesets, "fetch_live", lambda repo: seen.append(repo) or {"beta": _live()})
        assert rulesets.main(["check", "--dir", str(directory)]) == 0
        assert seen == ["env/repo"]

    def test_without_a_repo_it_refuses(self, monkeypatch, directory: Path) -> None:
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        with pytest.raises(SystemExit) as exit_:
            rulesets.main(["check", "--dir", str(directory)])
        assert exit_.value.code == 2
