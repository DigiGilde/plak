"""The gates in .github/workflows, pinned down here rather than on GitHub.

They run there now, but a green run says the wiring held for that event;
it says nothing about the events that did not fire. Which job gates what,
which permission sits where and that every action is on a commit SHA is
checked here, on every commit.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"
ACTIONS = ROOT / ".github" / "actions"
LOCAL_TRIVY = "./.github/actions/trivy"
RELEASE_COMMANDS = SCRIPTS / "plak_release" / "commands.py"
# The parts the backend suite runs in; read from the backend's file so the
# matrix here is held to the same list, without importing from backend/.
PARTS_FILE = ROOT / "backend" / "tests" / "parts.py"


def _parts():
    spec = importlib.util.spec_from_file_location("parts", PARTS_FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


parts = _parts()


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def _action(name: str) -> dict:
    return yaml.safe_load((ACTIONS / name / "action.yml").read_text(encoding="utf-8"))


def _is_sha(reference: str) -> bool:
    ref = reference.split("@")[1]
    return len(ref) == 40 and all(c in "0123456789abcdef" for c in ref)


def _pinned(ci: dict) -> set[str]:
    """The third-party actions ci.yml and the local actions pin."""
    steps = [s for job in ci["jobs"].values() for s in job.get("steps", [])]
    for path in ACTIONS.glob("*/action.yml"):
        steps += yaml.safe_load(path.read_text(encoding="utf-8"))["runs"]["steps"]
    return {s["uses"] for s in steps if "uses" in s}


def _local_actions_exist(job: dict) -> None:
    for step in job.get("steps", []):
        uses = step.get("uses", "")
        if uses.startswith("./"):
            assert (ROOT / uses / "action.yml").is_file(), uses


@pytest.fixture(scope="module")
def ci() -> dict:
    return _load("ci.yml")


@pytest.fixture(scope="module")
def deploy() -> dict:
    return _load("deploy.yml")


@pytest.fixture(scope="module")
def codeql() -> dict:
    return _load("codeql.yml")


@pytest.fixture(scope="module")
def plugin() -> dict:
    return _load("plugin.yml")


@pytest.fixture(scope="module")
def changelog() -> dict:
    return _load("changelog.yml")


@pytest.fixture(scope="module")
def api() -> dict:
    return _load("api.yml")


@pytest.fixture(scope="module")
def release() -> dict:
    return _load("release.yml")


@pytest.fixture(scope="module")
def prepare_release() -> dict:
    return _load("prepare-release.yml")


class TestTheCheckGate:
    def test_production_waits_for_the_checks(self, deploy) -> None:
        """BIO2 8.31.02: significant changes are tested before they go to
        production. With only `needs: build` a push to main went straight
        to production, tested or not."""
        assert deploy["jobs"]["production"]["needs"] == ["ci", "build", "github-release"]

    def test_only_a_release_tag_deploys_to_production(self, deploy) -> None:
        """A push to `beta` still builds, scans and attests its image, but
        production follows a release tag alone. The ZAD_PROJECT_ID guard
        keeps it standing down where there is no project, instead of failing
        the action on an empty api-key."""
        assert deploy[True]["push"]["branches"] == ["main", "beta"]
        assert deploy[True]["push"]["tags"] == ["v[0-9][0-9][0-9][0-9].*"]
        condition = " ".join(deploy["jobs"]["production"]["if"].split())
        assert condition == (
            "github.event_name == 'push' "
            "&& startsWith(github.ref, 'refs/tags/v') "
            "&& vars.ZAD_PROJECT_ID != ''"
        )

    def test_a_branch_push_never_reaches_production(self, deploy) -> None:
        """Every condition on `production` has to hold, so a branch push
        (`refs/heads/...`) fails the tag prefix whatever branch it is. No
        other job in the file deploys to `productie`."""
        condition = deploy["jobs"]["production"]["if"]
        assert "refs/heads" not in condition
        assert "||" not in condition
        assert [
            name
            for name, job in deploy["jobs"].items()
            if "productie" in str(job.get("environment", ""))
        ] == ["production"]

    def test_production_rolls_out_one_tag_at_a_time(self, deploy) -> None:
        """Without it two tags pushed close together could roll out side by
        side, and the older one finish last."""
        assert deploy["jobs"]["production"]["concurrency"] == {
            "group": "production",
            "cancel-in-progress": False,
        }

    def test_the_merge_queue_gets_the_checks_and_nothing_else(self, deploy) -> None:
        """beta merges through a merge queue, which waits for the required
        checks on its own commit: without the trigger they never report and
        nothing merges. That commit is throwaway, so no image gets built for
        it, and production stays bound to a release tag."""
        assert "merge_group" in deploy[True]
        assert "github.event_name != 'merge_group'" in deploy["jobs"]["build"]["if"]
        assert "startsWith(github.ref, 'refs/tags/v')" in deploy["jobs"]["production"]["if"]

    def test_the_checks_are_called_rather_than_triggered(self, ci, deploy) -> None:
        """You cannot pass a standalone workflow as `needs`. Calling it is
        the only form in which the gate is technically enforceable, and it
        saves everything running twice per commit."""
        assert list(ci[True]) == ["workflow_call"]
        assert deploy["jobs"]["ci"]["uses"] == "./.github/workflows/ci.yml"

    def test_the_backend_parts_are_the_ones_the_suite_knows(self, ci) -> None:
        assert ci["jobs"]["backend-part"]["strategy"]["matrix"]["part"] == list(parts.PARTS)
        assert ci["jobs"]["backend-part"]["strategy"]["fail-fast"] is False

    def test_a_failed_backend_part_fails_the_test_check(self, ci) -> None:
        """A failed part skips a plain dependent, and a skipped required
        check counts as passed: the tests would go red and the merge
        button green. So `backend-tests` runs regardless and fails itself."""
        gate = ci["jobs"]["backend-tests"]
        assert gate["needs"] == "backend-part"
        assert gate["if"] == "${{ !cancelled() }}"
        [step] = gate["steps"]
        assert step["if"] == "needs.backend-part.result != 'success'"
        assert "exit 1" in step["run"]

    def test_coverage_is_judged_whatever_the_tests_concluded(self, ci) -> None:
        """Coverage is its own check: a part keeps its data when its tests
        fail, and `backend-coverage` runs then too, rather than being
        skipped into a pass."""
        upload = _step(ci["jobs"]["backend-part"], "Keep the coverage data")
        assert upload["if"] == "${{ !cancelled() }}"
        assert upload["with"]["if-no-files-found"] == "error"
        coverage = ci["jobs"]["backend-coverage"]
        assert coverage["needs"] == "backend-part"
        assert coverage["if"] == "${{ !cancelled() }}"
        names = [s.get("name") for s in coverage["steps"]]
        assert names.index("Require the data of every test part") < names.index("Coverage over all parts")

    def test_the_coverage_floor_holds_over_the_merged_parts(self, ci) -> None:
        run = _step(ci["jobs"]["backend-part"], "Backend tests with coverage (testcontainers)")["run"]
        assert "--cov-fail-under=0" in run
        report = _step(ci["jobs"]["backend-coverage"], "Coverage over all parts")["run"]
        assert "coverage combine" in report
        assert "coverage report" in report

    def test_the_scripts_have_a_floor_of_their_own(self, ci) -> None:
        """The CI tooling under scripts/ is a uv project of its own, outside
        every other suite's measurement. Its job runs its tests in that
        project, and the floor lives in its pyproject.toml."""
        job = ci["jobs"]["release-script"]
        uv = next(s for s in job["steps"] if s.get("uses") == "./.github/actions/setup-uv")
        assert uv["with"] == {"cache-dependency-glob": "scripts/uv.lock"}
        step = _step(job, "Release script tests with coverage")
        assert step["working-directory"] == "scripts"
        assert step["run"].splitlines() == ["uv run coverage run -m pytest -q", "uv run coverage report"]
        settings = tomllib.loads((SCRIPTS / "pyproject.toml").read_text(encoding="utf-8"))
        assert settings["tool"]["coverage"]["report"]["fail_under"] == 100
        assert settings["tool"]["coverage"]["run"] == {"branch": True, "source": ["."], "omit": ["tests/*"]}
        assert settings["tool"]["uv"] == {"package": False}

    def test_the_checks_cover_backend_cli_frontend_and_vulnerabilities(self, ci) -> None:
        assert set(ci["jobs"]) == {
            "backend-coverage",
            "backend-part",
            "backend-tests",
            "release-script",
            "cli",
            "cli-python",
            "cli-version",
            "cli-windows",
            "frontend",
            "vulnerabilities",
            "pre-commit",
            "secret-scan",
            "containers",
            "e2e",
        }

    def test_a_hung_e2e_run_does_not_hold_up_the_queue(self, ci, deploy) -> None:
        """deploy.yml queues its runs instead of cancelling them, so a hung
        Playwright install would block every later run for GitHub's default
        of 360 minutes. A normal e2e job takes two to eight minutes."""
        assert deploy["concurrency"]["cancel-in-progress"] is False
        assert ci["jobs"]["e2e"]["timeout-minutes"] == 15

    def test_the_cli_runs_on_every_python_it_supports(self, ci) -> None:
        """People install the CLI with whatever Python they have, so every
        minor from the floor in requires-python up to cli/.python-version,
        which `cli` and `cli-windows` run on, gets the suite."""
        floor = re.search(
            r'^requires-python = ">=3\.(\d+)"$', (ROOT / "cli" / "pyproject.toml").read_text(), re.M
        )
        assert floor
        newest = int((ROOT / "cli" / ".python-version").read_text().strip().split(".")[1])
        matrix = ci["jobs"]["cli-version"]["strategy"]["matrix"]["python"]

        assert ci["jobs"]["cli-version"]["strategy"]["fail-fast"] is False
        assert sorted([*matrix, f"3.{newest}"], key=lambda v: int(v.split(".")[1])) == [
            f"3.{minor}" for minor in range(int(floor[1]), newest + 1)
        ]
        install = _step(ci["jobs"]["cli-version"], "Install uv")
        assert install["with"]["python-version"] == "${{ matrix.python }}"

    def test_a_failed_python_version_fails_the_cli_check(self, ci) -> None:
        """The same trap as with the backend parts: a skipped required check
        counts as passed, so `cli-python` runs regardless and fails itself."""
        gate = ci["jobs"]["cli-python"]
        assert gate["needs"] == "cli-version"
        assert gate["if"] == "${{ !cancelled() }}"
        [step] = gate["steps"]
        assert step["if"] == "needs.cli-version.result != 'success'"
        assert "exit 1" in step["run"]


class TestTheScans:
    def test_the_image_is_scanned_before_anything_rolls_out(self, deploy) -> None:
        steps = deploy["jobs"]["build"]["steps"]
        scans = [s for s in steps if s.get("uses") == LOCAL_TRIVY]

        # One that reports everything, one that closes the gate, and the SBOM.
        assert len(scans) == 3
        gate = [s for s in scans if s.get("with", {}).get("exit-code") == "1"]
        assert len(gate) == 1
        assert gate[0]["with"]["severity"] == "CRITICAL,HIGH"
        assert gate[0]["with"]["trivyignores"] == ".trivyignore.yaml"

    def test_what_runs_is_scanned_every_day(self) -> None:
        """Dependabot raises no alerts for uv.lock or for the OS packages
        in the image, so a new advisory against what runs in production
        is seen here first."""
        triggers = _load("scan.yml")[True]
        assert set(triggers) == {"schedule", "workflow_dispatch"}
        [schedule] = triggers["schedule"]
        assert schedule["cron"].endswith(" * * *")

    def test_the_image_is_scanned_by_the_digest_that_was_pushed(self, deploy) -> None:
        """A tag can move between the push and the scan."""
        steps = deploy["jobs"]["build"]["steps"]
        for scan in (s for s in steps if s.get("uses") == LOCAL_TRIVY):
            assert scan["with"]["image-ref"] == "${{ steps.tag.outputs.name }}@${{ steps.push.outputs.digest }}"

    def test_the_build_job_does_not_keep_the_token_in_git(self, deploy) -> None:
        """It holds `packages: write` and runs the Containerfile."""
        checkout = deploy["jobs"]["build"]["steps"][0]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"]["persist-credentials"] is False

    def test_trivy_is_one_action_with_one_version(self, ci, deploy) -> None:
        """The version used to be a variable in two workflows; it is the
        input of the local action now, and nothing else sets one."""
        step = _action("trivy")["runs"]["steps"][0]
        assert step["uses"].startswith("aquasecurity/trivy-action@") and _is_sha(step["uses"])
        assert re.fullmatch(r"v\d+\.\d+\.\d+", step["with"]["version"])
        for workflow in (ci, deploy):
            assert "TRIVY_VERSION" not in workflow.get("env", {})
            for job in workflow["jobs"].values():
                for each in job.get("steps", []):
                    assert "version" not in each.get("with", {}) or "trivy" not in each.get("uses", "")

    def test_the_image_carries_provenance_and_sbom_attestations(self, deploy) -> None:
        """Both attest the digest that was pushed, never a tag, which can move."""
        provenance = deploy["jobs"]["provenance"]
        for permission in ("id-token", "attestations", "artifact-metadata"):
            assert provenance["permissions"][permission] == "write"
        # push-to-registry writes the attestation next to the image.
        assert provenance["permissions"]["packages"] == "write"

        attests = [
            s for s in provenance["steps"] if str(s.get("uses", "")).startswith("actions/attest@")
        ]
        assert len(attests) == 2
        for step in attests:
            assert step["with"]["subject-name"] == "${{ needs.build.outputs.name }}"
            assert step["with"]["subject-digest"] == "${{ needs.build.outputs.digest }}"
            assert step["with"]["push-to-registry"] is True
        assert [s["with"].get("sbom-path") for s in attests] == [None, "sbom.cdx.json"]

    def test_the_attestation_job_runs_no_project_code(self, deploy) -> None:
        """A job with `id-token: write` can mint a token for any audience it
        names, and that token carries this repository's identity. So it may
        not be the job that executes the Containerfile, which runs `npm ci`
        and `uv sync`."""
        assert deploy["jobs"]["build"]["permissions"] == {
            "contents": "read",
            "packages": "write",
        }

        # No checkout, no build, no scan: login, download, attest twice.
        steps = deploy["jobs"]["provenance"]["steps"]
        assert [s["uses"].split("@")[0] for s in steps] == [
            "docker/login-action",
            "actions/download-artifact",
            "actions/attest",
            "actions/attest",
        ]

    def test_only_a_pushed_image_gets_attested(self, deploy) -> None:
        """No `if:` of its own: a skipped or failed `build` skips this job as
        well, so nothing gets attested that was not built and pushed. The
        digest is handed over rather than re-resolved, so the two jobs
        cannot disagree about which image that was."""
        provenance = deploy["jobs"]["provenance"]
        assert provenance["needs"] == "build"
        assert "if" not in provenance
        assert deploy["jobs"]["build"]["outputs"]["digest"] == "${{ steps.push.outputs.digest }}"

        # The SBOM reaches the attestation as an artefact, under one name.
        upload = next(
            s
            for s in deploy["jobs"]["build"]["steps"]
            if str(s.get("uses", "")).startswith("actions/upload-artifact@")
        )
        download = next(
            s
            for s in provenance["steps"]
            if str(s.get("uses", "")).startswith("actions/download-artifact@")
        )
        assert upload["with"]["name"] == download["with"]["name"] == "sbom-${{ github.sha }}"

    def test_a_pull_request_is_gated_on_every_base_image(self, ci) -> None:
        """Without this the first trivy finding on a base image arrives at
        deploy time. The dev nginx image is gated too: it never leaves
        dev/compose.yml, but it runs on every developer's machine, and what
        it still carries is written down in .trivyignore.yaml with a date."""
        steps = ci["jobs"]["containers"]["steps"]
        scans = [s for s in steps if s.get("uses") == LOCAL_TRIVY]

        assert [s["with"]["exit-code"] for s in scans] == ["1"] * len(scans)
        assert {s["with"].get("scan-type", "image") for s in scans} == {"config", "image"}
        for step in scans:
            assert step["with"]["severity"] == "CRITICAL,HIGH"
            assert step["with"]["trivyignores"] == ".trivyignore.yaml"

    def test_the_base_image_scan_skips_only_what_the_image_removes(self, ci) -> None:
        """Skipping pip in the runtime base scan is only honest while the
        runtime stage uninstalls it; the other scans skip nothing."""
        steps = ci["jobs"]["containers"]["steps"]
        skipping = {s["name"]: s["with"]["skip-dirs"] for s in steps if "skip-dirs" in s.get("with", {})}

        assert skipping == {"Scan the runtime base image (trivy)": "usr/local/lib/python3.*/site-packages/pip"}
        runtime = (ROOT / "containers" / "plak" / "Containerfile").read_text().split(" AS runtime", 1)[1]
        assert "RUN python -m pip uninstall --yes --quiet pip" in runtime

    def test_every_third_party_action_is_pinned_to_a_sha(self) -> None:
        """A tag can be moved, a commit cannot. Over every workflow in the
        directory rather than a hand-kept list, so a new one is covered the
        moment it lands."""
        paths = sorted(WORKFLOWS.glob("*.yml"))
        assert [p.name for p in paths] == [
            "api.yml",
            "changelog.yml",
            "ci.yml",
            "codeql.yml",
            "deploy.yml",
            "plugin.yml",
            "prepare-release.yml",
            "release.yml",
            "rulesets.yml",
            "scan.yml",
        ]

        for path in paths:
            workflow = _load(path.name)
            for job in workflow["jobs"].values():
                _local_actions_exist(job)
                for step in job.get("steps", []):
                    uses = step.get("uses")
                    if not uses or uses.startswith("./"):
                        continue
                    assert _is_sha(uses), uses

    def test_every_third_party_action_in_a_local_action_is_pinned_to_a_sha(self) -> None:
        """The composite actions in .github/actions are workflow code too."""
        paths = sorted(ACTIONS.glob("*/action.yml"))
        assert paths
        for path in paths:
            steps = yaml.safe_load(path.read_text(encoding="utf-8"))["runs"]["steps"]
            for step in steps:
                uses = step.get("uses")
                if not uses or uses.startswith("./"):
                    continue
                assert _is_sha(uses), f"{path.parent.name}: {uses}"

    def test_uv_and_trivy_are_pinned_in_their_local_action_only(self) -> None:
        """A second pin next to the local action drifts from it: Dependabot
        bumps one, and a rebase brings back the other."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for job in _load(path.name).get("jobs", {}).values():
                for step in job.get("steps", []):
                    uses = step.get("uses", "")
                    assert not uses.startswith(("astral-sh/setup-uv@", "aquasecurity/")), f"{path.name}: {uses}"
        for path in sorted(ACTIONS.glob("*/action.yml")):
            if path.parent.name in ("setup-uv", "trivy"):
                continue
            for step in yaml.safe_load(path.read_text(encoding="utf-8"))["runs"]["steps"]:
                uses = step.get("uses", "")
                assert not uses.startswith(("astral-sh/setup-uv@", "aquasecurity/")), f"{path.parent.name}: {uses}"

    def test_every_input_given_to_a_local_action_is_one_it_declares(self) -> None:
        """GitHub only warns about an undeclared input and drops it, so a
        `skip-dirs` the action does not know scans what it was meant to skip."""
        callers = [(path.name, _load(path.name)) for path in sorted(WORKFLOWS.glob("*.yml"))]
        callers += [
            (path.parent.name, {"jobs": {"composite": yaml.safe_load(path.read_text(encoding="utf-8"))["runs"]}})
            for path in sorted(ACTIONS.glob("*/action.yml"))
        ]
        checked = 0
        for name, workflow in callers:
            for job in workflow.get("jobs", {}).values():
                for step in job.get("steps", []):
                    uses = step.get("uses", "")
                    if not uses.startswith("./.github/actions/"):
                        continue
                    declared = set(_action(uses.removeprefix("./.github/actions/")).get("inputs", {}))
                    assert set(step.get("with", {})) <= declared, f"{name}: {uses}"
                    checked += 1
        assert checked

    def test_the_trivy_action_hands_every_input_on(self) -> None:
        """An input the local action declares but does not pass on is the
        same silent drop, one level down."""
        action = _action("trivy")
        [step] = action["runs"]["steps"]
        assert {k: v for k, v in step["with"].items() if k != "version"} == {
            name: "${{ inputs." + name + " }}" for name in action["inputs"]
        }

    def test_setup_uv_passes_the_python_version_on_either_way(self) -> None:
        """The CLI's Python matrix sets it; with or without a cache, uv must
        get it."""
        steps = _action("setup-uv")["runs"]["steps"]
        assert len(steps) == 2
        for step in steps:
            assert step["with"]["python-version"] == "${{ inputs.python-version }}", step["name"]

    def test_every_push_trigger_names_the_default_branch(self) -> None:
        """A push trigger on a branch that does not exist is a workflow that
        never fires, and nothing reports that: the run list simply stays
        empty. `main` is the branch that does not exist yet, so every push
        trigger has to name `beta` as well, whatever else it lists."""
        for path in sorted(WORKFLOWS.glob("*.yml")):
            push = _load(path.name)[True].get("push")
            if push is None:
                continue
            assert "beta" in push["branches"], path.name


GUARDS = (
    "Check the tag format",
    "Check that the tagged commit is on beta",
    "Check that the tag is the newest release",
    "Check that the tagged commit has its release notes",
)


def _step(job: dict, name: str) -> dict:
    return next(s for s in job["steps"] if s.get("name") == name)


def _env(**extra: str) -> dict[str, str]:
    # Clear of the developer's git config, so a signing or hook setting
    # there cannot change what these repositories do.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Test",
        GIT_AUTHOR_EMAIL="test@example.nl",
        GIT_COMMITTER_NAME="Test",
        GIT_COMMITTER_EMAIL="test@example.nl",
    )
    env.update(extra)
    return env


def _git(repo: Path, *args: str) -> str:
    # git via PATH, like the justfile does; every argument comes from this file.
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=repo,
        env=_env(),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _run(step: dict, cwd: Path, **env: str) -> subprocess.CompletedProcess:
    """A `run:` step as the runner executes it on Linux: `bash -e`."""
    return subprocess.run(  # noqa: S603
        ["bash", "-e", "-c", step["run"]],  # noqa: S607
        cwd=cwd,
        env=_env(**env),
        capture_output=True,
        text=True,
    )


def _run_unnamed(step: dict, cwd: Path, **env: str) -> subprocess.CompletedProcess:
    """As _run, without the author and committer the other tests set: on
    the runner the step's own `git config` decides who commits."""
    clean = {k: v for k, v in _env(**env).items() if not k.startswith(("GIT_AUTHOR_", "GIT_COMMITTER_"))}
    return subprocess.run(  # noqa: S603
        ["bash", "-e", "-c", step["run"]],  # noqa: S607
        cwd=cwd,
        env=clean,
        capture_output=True,
        text=True,
    )


def _refused(result: subprocess.CompletedProcess) -> bool:
    return result.returncode != 0 and result.stdout.startswith("::error::")


class TestTheCoverageDataCheck:
    """`coverage combine` merges whatever data it finds, so the step before
    it has to refuse a missing part. Run as written in ci.yml, with a `uv`
    on PATH that hands `uv run python` to this interpreter."""

    @pytest.fixture
    def step(self, ci) -> dict:
        return _step(ci["jobs"]["backend-coverage"], "Require the data of every test part")

    @pytest.fixture
    def backend(self, tmp_path) -> Path:
        backend = tmp_path / "backend"
        (backend / "tests").mkdir(parents=True)
        (backend / "tests" / "parts.py").write_text(PARTS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        uv = bin_dir / "uv"
        uv.write_text(f'#!/bin/sh\nshift 2\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
        uv.chmod(0o755)
        return backend

    def _path(self, backend: Path) -> str:
        return f"{backend.parent / 'bin'}{os.pathsep}{os.environ['PATH']}"

    def test_the_data_of_every_part_passes(self, step, backend) -> None:
        for part in parts.PARTS:
            (backend / f".coverage.{part}").touch()
        result = _run(step, backend, PATH=self._path(backend))
        assert result.returncode == 0, result.stderr
        assert result.stderr == ""

    def test_a_part_without_data_is_refused(self, step, backend) -> None:
        missing, *present = parts.PARTS
        for part in present:
            (backend / f".coverage.{part}").touch()
        result = _run(step, backend, PATH=self._path(backend))
        assert result.returncode == 1
        assert result.stderr == f"No coverage data from backend test part {missing}.\n"

    def test_every_missing_part_is_named(self, step, backend) -> None:
        result = _run(step, backend, PATH=self._path(backend))
        assert result.returncode == 1
        assert result.stderr.splitlines() == [f"No coverage data from backend test part {p}." for p in parts.PARTS]


class TestTheReleaseGuards:
    """Production rolls out a release tag only after four checks, each of
    which fails the job rather than skipping it, so a refused tag shows red.
    The steps are run here as they are written in deploy.yml, against real
    repositories."""

    @pytest.fixture
    def guarded(self, deploy) -> dict:
        """The job that releases a tag, and with it the one production
        waits for."""
        return deploy["jobs"]["github-release"]

    @pytest.fixture
    def release_tag(self, deploy) -> str:
        return deploy["env"]["RELEASE_TAG"]

    @pytest.fixture
    def work(self, tmp_path) -> Path:
        """`origin` with beta at two commits, the baseline tag on the first."""
        origin = tmp_path / "origin.git"
        work = tmp_path / "work"
        _git(tmp_path, "init", "-q", "--bare", "-b", "beta", str(origin))
        _git(tmp_path, "init", "-q", "-b", "beta", str(work))
        _git(work, "remote", "add", "origin", str(origin))
        _git(work, "commit", "-q", "--allow-empty", "-m", "one")
        _git(work, "tag", "-a", "v2026.9.30", "-m", "Plak v2026.9.30")
        _git(work, "commit", "-q", "--allow-empty", "-m", "two")
        _git(work, "push", "-q", "origin", "beta", "--tags")
        return work

    @staticmethod
    def _tag(work: Path, tag: str, ref: str = "HEAD") -> None:
        _git(work, "tag", "-a", tag, ref, "-m", f"Plak {tag}")
        _git(work, "push", "-q", "origin", tag)

    @staticmethod
    def _checkout(tmp_path: Path) -> Path:
        """What actions/checkout leaves on the runner, at this moment."""
        runner = tmp_path / "runner"
        _git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(runner))
        return runner

    def test_the_guards_run_after_a_full_checkout_and_before_the_release(
        self, guarded
    ) -> None:
        """Ancestry and the newest tag need the whole history and every tag;
        a shallow checkout would refuse every release or pass the wrong one.
        The token stays out of .git/config: the repository is public, so the
        fetches need no credentials."""
        steps = guarded["steps"]
        assert steps[0]["uses"].startswith("actions/checkout@")
        assert steps[0]["with"] == {"fetch-depth": 0, "persist-credentials": False}
        assert [s.get("name") for s in steps[1:]] == [*GUARDS, "Publish the GitHub Release"]
        assert guarded["env"]["TAG"] == "${{ github.ref_name }}"
        for name in GUARDS:
            assert "${{" not in _step(guarded, name)["run"], name

    def test_production_checks_again_right_before_the_rollout(self, guarded, deploy) -> None:
        """A newer tag can arrive while production waits in its concurrency
        group; checked only at release time, an older tag could still roll
        out last. So production runs the same guards, after its own full
        checkout and immediately before the rollout."""
        production = deploy["jobs"]["production"]
        steps = production["steps"]
        assert steps[0]["uses"].startswith("actions/checkout@")
        assert steps[0]["with"] == {"fetch-depth": 0, "persist-credentials": False}
        assert [s.get("name") for s in steps[1:]] == [*GUARDS, "Roll out to ZAD"]
        assert production["env"] == {"TAG": "${{ github.ref_name }}"}
        for name in GUARDS:
            assert _step(production, name)["run"] == _step(guarded, name)["run"], name

    @pytest.mark.parametrize("tag", ["v2026.10.1", "v2026.9.30", "v2026.12.31.2"])
    def test_a_calver_tag_passes_the_format_check(
        self, guarded, release_tag, tmp_path, tag
    ) -> None:
        result = _run(
            _step(guarded, "Check the tag format"), tmp_path, TAG=tag, RELEASE_TAG=release_tag
        )
        assert result.returncode == 0, result.stdout

    @pytest.mark.parametrize(
        "tag",
        [
            "v2026.10",
            "v2026.01.1",
            "v2026.10.01",
            "v2026.0.1",
            "v26.10.1",
            "v2026.10.1.0",
            "v2026.10.1-rc1",
            "v2026.10.1,evil",
            "2026.10.1",
        ],
    )
    def test_any_other_tag_is_refused(self, guarded, release_tag, tmp_path, tag) -> None:
        result = _run(
            _step(guarded, "Check the tag format"), tmp_path, TAG=tag, RELEASE_TAG=release_tag
        )
        assert _refused(result), result.stdout

    def test_a_commit_on_beta_passes_the_ancestry_check(self, guarded, work, tmp_path) -> None:
        """Including one that reached beta after the checkout: the guard
        fetches beta itself."""
        runner = self._checkout(tmp_path)
        _git(work, "commit", "-q", "--allow-empty", "-m", "three")
        _git(work, "push", "-q", "origin", "beta")
        result = _run(
            _step(guarded, "Check that the tagged commit is on beta"),
            runner,
            TAG="v2026.10.1",
            GITHUB_SHA=_git(work, "rev-parse", "HEAD"),
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_commit_off_beta_is_refused(self, guarded, work, tmp_path) -> None:
        """A tag on a branch that never merged would ship unreviewed code."""
        _git(work, "switch", "-q", "-c", "side", "HEAD~1")
        _git(work, "commit", "-q", "--allow-empty", "-m", "unreviewed")
        _git(work, "push", "-q", "origin", "side")
        result = _run(
            _step(guarded, "Check that the tagged commit is on beta"),
            self._checkout(tmp_path),
            TAG="v2026.10.1",
            GITHUB_SHA=_git(work, "rev-parse", "HEAD"),
        )
        assert _refused(result), result.stdout

    def test_a_commit_dropped_from_beta_is_refused(self, guarded, work, tmp_path) -> None:
        """The runner's own origin/beta may be older than beta is now; the
        guard overwrites it rather than trusting it."""
        dropped = _git(work, "rev-parse", "HEAD")
        runner = self._checkout(tmp_path)
        _git(work, "reset", "-q", "--hard", "HEAD~1")
        _git(work, "push", "-q", "--force", "origin", "beta")
        result = _run(
            _step(guarded, "Check that the tagged commit is on beta"),
            runner,
            TAG="v2026.10.1",
            GITHUB_SHA=dropped,
        )
        assert _refused(result), result.stdout

    def test_the_newest_tag_passes(self, guarded, release_tag, work, tmp_path) -> None:
        """Ordered as numbers, not text: as text v2026.9.30 sorts after
        v2026.10.1. A tag that is not a release does not count."""
        self._tag(work, "v2026.10.1")
        self._tag(work, "v2026.12.1-rc1")
        result = _run(
            _step(guarded, "Check that the tag is the newest release"),
            self._checkout(tmp_path),
            TAG="v2026.10.1",
            RELEASE_TAG=release_tag,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_an_older_tag_pushed_again_is_refused(
        self, guarded, release_tag, work, tmp_path
    ) -> None:
        """Otherwise re-pushing an old tag rolls production back."""
        self._tag(work, "v2026.10.1")
        result = _run(
            _step(guarded, "Check that the tag is the newest release"),
            self._checkout(tmp_path),
            TAG="v2026.9.30",
            RELEASE_TAG=release_tag,
        )
        assert _refused(result), result.stdout

    def test_a_newer_tag_the_checkout_missed_still_refuses(
        self, guarded, release_tag, work, tmp_path
    ) -> None:
        """The guard fetches the tags itself, so one pushed after the
        checkout counts too."""
        self._tag(work, "v2026.10.1")
        runner = self._checkout(tmp_path)
        self._tag(work, "v2026.10.1.1")
        result = _run(
            _step(guarded, "Check that the tag is the newest release"),
            runner,
            TAG="v2026.10.1",
            RELEASE_TAG=release_tag,
        )
        assert _refused(result), result.stdout

    @staticmethod
    def _release_commit(work: Path, changelog: str) -> None:
        """A commit as the release step leaves it: the script that reads the
        notes with its package, and CHANGELOG.md."""
        scripts = work / "scripts"
        scripts.mkdir(exist_ok=True)
        shutil.copy(SCRIPTS / "release.py", scripts / "release.py")
        caches = shutil.ignore_patterns("__pycache__")
        shutil.copytree(SCRIPTS / "plak_release", scripts / "plak_release", ignore=caches, dirs_exist_ok=True)
        (work / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
        for name in ("cli/pyproject.toml", "plugin/.claude-plugin/plugin.json"):
            (work / name).parent.mkdir(parents=True, exist_ok=True)
            (work / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "Release")

    def test_a_tag_on_its_release_commit_passes(self, guarded, work, tmp_path) -> None:
        self._release_commit(work, "# Changelog\n\n## [2026.10.1]\n\n### Added\n\n- A thing.\n")
        self._tag(work, "v2026.10.1")
        result = _run(_step(guarded, "Check that the tagged commit has its release notes"), work, TAG="v2026.10.1")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_tag_without_its_section_is_refused(self, guarded, work, tmp_path) -> None:
        """A tag set by hand on an ordinary commit, whose changelog ends at
        the release before; a section that lands on beta later does not
        count."""
        self._release_commit(work, "# Changelog\n\n## [2026.9.30]\n\n### Added\n\n- A thing.\n")
        self._tag(work, "v2026.10.1")
        self._release_commit(
            work,
            "# Changelog\n\n## [2026.10.1]\n\n### Added\n\n- Another thing.\n\n"
            "## [2026.9.30]\n\n### Added\n\n- A thing.\n",
        )
        result = _run(_step(guarded, "Check that the tagged commit has its release notes"), work, TAG="v2026.10.1")
        assert _refused(result), result.stdout


class TestTheReleaseImage:
    """A release tag gets its version as its only image tag and as
    PLAK_VERSION in the image; any other push builds the commit tag."""

    @pytest.fixture
    def build(self, deploy) -> dict:
        return deploy["jobs"]["build"]

    def test_only_a_tag_push_sets_a_version(self, build) -> None:
        """The step is skipped on every other event, so its outputs are empty
        and the build gets no build-arg (PLAK_VERSION stays `dev`) and only
        the commit tag."""
        release = _step(build, "Determine the release version")
        assert release["if"] == (
            "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"
        )
        assert release["env"]["TAG"] == "${{ github.ref_name }}"
        assert "${{" not in release["run"]

        push = _step(build, "Build and push the image")
        assert push["with"]["build-args"] == "${{ steps.release.outputs.build-args }}"

    def test_everything_after_the_push_follows_the_one_image_it_pushed(self, build, deploy) -> None:
        """The release commit is a push to beta as well as a tag. Both builds
        once pushed the commit tag, the beta one without PLAK_VERSION, and
        production rolled out whichever finished last: v2026.10.4 and
        v2026.10.7 ran as `dev`, not as the image their release named. A
        release now pushes its version tag alone, and the scans, the SBOM,
        the attestations and production all take that one; the scans and the
        SBOM by the digest that push returned."""
        image = "${{ steps.release.outputs.image || steps.tag.outputs.image }}"
        assert _step(build, "Build and push the image")["with"]["tags"] == image
        assert build["outputs"]["image"] == image
        pushed = "${{ steps.tag.outputs.name }}@${{ steps.push.outputs.digest }}"
        for step in build["steps"]:
            if "image-ref" in step.get("with", {}):
                assert step["with"]["image-ref"] == pushed, step.get("name")
        assert _step(deploy["jobs"]["production"], "Roll out to ZAD")["with"]["image"] == (
            "${{ needs.build.outputs.image }}"
        )

    def test_a_release_tag_builds_only_its_version_tag(self, build, deploy, tmp_path) -> None:
        output = tmp_path / "output"
        output.touch()
        result = _run(
            _step(build, "Determine the release version"),
            tmp_path,
            TAG="v2026.10.1",
            NAME="ghcr.io/digigilde/plak",
            RELEASE_TAG=deploy["env"]["RELEASE_TAG"],
            GITHUB_OUTPUT=str(output),
        )
        assert result.returncode == 0, result.stdout
        assert output.read_text().splitlines() == [
            "build-args=PLAK_VERSION=2026.10.1",
            "image=ghcr.io/digigilde/plak:2026.10.1",
        ]

    def test_a_malformed_tag_builds_nothing(self, build, deploy, tmp_path) -> None:
        """A comma is legal in a git tag and a separator in the tag list:
        refused before it gets there."""
        output = tmp_path / "output"
        output.touch()
        result = _run(
            _step(build, "Determine the release version"),
            tmp_path,
            TAG="v2026.10.1,evil",
            NAME="ghcr.io/digigilde/plak",
            IMAGE="ghcr.io/digigilde/plak:abc123",
            RELEASE_TAG=deploy["env"]["RELEASE_TAG"],
            GITHUB_OUTPUT=str(output),
        )
        assert _refused(result), result.stdout
        assert output.read_text() == ""


class TestCodeQL:
    def test_it_is_its_own_workflow_with_a_weekly_run(self, codeql) -> None:
        """ci.yml is `workflow_call` only, and a called workflow carries no
        schedule of its own. CodeQL needs one: the queries and the
        advisories move while the code stands still."""
        triggers = codeql[True]
        assert set(triggers) == {"pull_request", "merge_group", "push", "schedule"}
        assert triggers["push"]["branches"] == ["beta"]
        assert len(triggers["schedule"]) == 1
        assert triggers["schedule"][0]["cron"].endswith(" * * 0")

    def test_the_write_permission_sits_on_the_job(self, codeql) -> None:
        """Same split as `provenance` in deploy.yml: the permission to write
        into the security tab is not handed to the whole file."""
        assert codeql["permissions"] == {"contents": "read"}
        assert codeql["jobs"]["analyse"]["permissions"] == {
            "contents": "read",
            "security-events": "write",
        }

    def test_all_three_languages_are_analysed_without_a_build(self, codeql) -> None:
        """The languages GitHub detects here, none of them compiled. A
        build step would mean running project code, and `build-mode: none`
        is what keeps this job away from it.

        These add three checks for branch protection: `CodeQL / Analyse
        (actions)`, `CodeQL / Analyse (javascript-typescript)` and
        `CodeQL / Analyse (python)`.
        """
        job = codeql["jobs"]["analyse"]
        assert job["strategy"]["matrix"]["language"] == [
            "actions",
            "javascript-typescript",
            "python",
        ]
        assert job["strategy"]["fail-fast"] is False

        init = next(s for s in job["steps"] if "codeql-action/init" in str(s.get("uses", "")))
        assert init["with"]["build-mode"] == "none"
        assert init["with"]["languages"] == "${{ matrix.language }}"

        analyze = next(
            s for s in job["steps"] if "codeql-action/analyze" in str(s.get("uses", ""))
        )
        assert analyze["with"]["category"] == "/language:${{ matrix.language }}"

    def test_init_and_analyze_are_the_same_version(self, codeql) -> None:
        """Two halves of one action; a mixed pair is a support matrix
        nobody tests."""
        refs = {
            s["uses"].split("@")[1]
            for s in codeql["jobs"]["analyse"]["steps"]
            if str(s.get("uses", "")).startswith("github/codeql-action/")
        }
        assert len(refs) == 1


class TestThePluginManifests:
    def test_it_runs_on_pull_requests_and_on_the_default_branch(self, plugin) -> None:
        """Validating `plugin/` has nothing to do with the production branch,
        so the push half follows the default branch, as codeql.yml does. It
        stood at `main` and therefore never fired; the pull request half has
        been running all along."""
        triggers = plugin[True]
        assert set(triggers) == {"pull_request", "push", "workflow_dispatch"}
        assert triggers["push"]["branches"] == ["beta"]

    def test_both_halves_watch_the_same_paths(self, plugin) -> None:
        """A path filter that differs per event makes a push and its own pull
        request disagree about whether the check applies."""
        triggers = plugin[True]
        assert triggers["pull_request"]["paths"] == triggers["push"]["paths"]
        assert set(triggers["push"]["paths"]) == {
            "plugin/**",
            ".claude-plugin/**",
            ".github/workflows/plugin.yml",
        }

    def test_a_pull_request_leaves_the_plugin_version_to_the_release(self, plugin) -> None:
        """The release sets it when plugin/ changed (test_release.py); a
        check that wanted it raised by hand would fight that."""
        assert set(plugin["jobs"]) == {"manifests"}


class TestTheChangelogCheck:
    """What the check decides is tested in test_release.py; here only how
    the workflow runs it."""

    def test_it_runs_on_every_pull_request_and_in_the_merge_queue(self, changelog) -> None:
        """`edited`, because a description that says `No changelog entry:`
        clears the warning and has to be read again when it changes."""
        triggers = changelog[True]
        assert set(triggers) == {"pull_request", "merge_group"}
        assert triggers["pull_request"]["types"] == ["opened", "synchronize", "reopened", "edited"]
        assert "paths" not in triggers["pull_request"]

    def test_only_the_job_may_write_to_pull_requests(self, changelog) -> None:
        assert changelog["permissions"] == {"contents": "read"}
        assert changelog["jobs"]["changelog"]["permissions"] == {"contents": "read", "pull-requests": "write"}

    def test_the_actions_are_the_ones_ci_already_pins(self, ci, changelog) -> None:
        pinned = _pinned(ci)
        _local_actions_exist(changelog["jobs"]["changelog"])
        for step in changelog["jobs"]["changelog"]["steps"]:
            if "uses" in step and not step["uses"].startswith("./"):
                assert step["uses"] in pinned, step["uses"]

    def test_the_check_sees_the_base_and_the_tags(self, changelog) -> None:
        checkout = changelog["jobs"]["changelog"]["steps"][0]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"]["fetch-depth"] == 0

    def test_the_merge_queue_runs_only_what_fails(self, changelog) -> None:
        step = next(s for s in changelog["jobs"]["changelog"]["steps"] if "release.py" in s.get("run", ""))
        assert step["env"]["BASE"] == (
            "${{ github.event_name == 'merge_group' && github.event.merge_group.base_sha "
            "|| format('origin/{0}', github.base_ref) }}"
        )
        assert "--hard-only" in step["run"]
        assert "${{" not in step["run"]

    def test_the_comment_cannot_fail_the_check(self, changelog) -> None:
        """A fork or Dependabot pull request has a read-only token."""
        step = next(s for s in changelog["jobs"]["changelog"]["steps"] if "comment" in s.get("name", ""))
        assert step["continue-on-error"] is True
        assert "github.event_name == 'pull_request'" in step["if"]
        assert step["uses"] == "./.github/actions/sticky-comment"
        assert step["with"]["token"] == "${{ github.token }}"

    def test_the_comment_marker_is_the_one_the_script_writes(self, changelog) -> None:
        """Another marker and every run posts a new comment instead of
        updating the one that is there."""
        script = RELEASE_COMMANDS.read_text(encoding="utf-8")
        step = next(s for s in changelog["jobs"]["changelog"]["steps"] if "comment" in s.get("name", ""))
        assert f'COMMENT_MARKER = "{step["with"]["marker"]}"\n' in script
        assert step["with"]["comment-file"] == "${{ runner.temp }}/changelog-comment.md"


class TestTheApiContractCheck:
    """What the check decides is tested in test_release.py; here only how
    the workflow runs it."""

    def test_it_runs_on_every_pull_request_and_in_the_merge_queue(self, api) -> None:
        assert set(api[True]) == {"pull_request", "merge_group"}
        assert api[True]["pull_request"] is None

    def test_only_the_job_may_write_to_pull_requests(self, api) -> None:
        assert api["permissions"] == {"contents": "read"}
        assert api["jobs"]["api-contract"]["permissions"] == {"contents": "read", "pull-requests": "write"}

    def test_the_actions_are_the_ones_ci_already_pins(self, ci, api) -> None:
        pinned = _pinned(ci)
        _local_actions_exist(api["jobs"]["api-contract"])
        for step in api["jobs"]["api-contract"]["steps"]:
            if "uses" in step and not step["uses"].startswith("./"):
                assert step["uses"] in pinned, step["uses"]

    def test_the_check_sees_the_tags(self, api) -> None:
        """The contract is the schema at the newest tag."""
        checkout = api["jobs"]["api-contract"]["steps"][0]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"]["fetch-depth"] == 0
        assert checkout["with"]["persist-credentials"] is False

    def test_oasdiff_comes_from_the_image_dependabot_bumps(self, api) -> None:
        """The digest pins the binary; the tag in front of it is what
        Dependabot reads. Run against the real Containerfile, so a FROM line
        the step no longer recognises fails here and not in a pull request."""
        step = next(s for s in api["jobs"]["api-contract"]["steps"] if s.get("name") == "Install oasdiff")
        assert step["uses"] == "./.github/actions/install-oasdiff"
        run = _action("install-oasdiff")["runs"]["steps"][0]["run"]
        reading = run.split("if [")[0]
        found = subprocess.run(  # noqa: S603
            ["bash", "-e", "-c", reading + 'printf "%s" "$image"'],  # noqa: S607
            cwd=WORKFLOWS.parents[1],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert re.fullmatch(r"docker\.io/tufin/oasdiff:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", found)
        assert run.index('if [ -z "$image" ]') < run.index("docker cp oasdiff:/usr/bin/oasdiff")

        dependabot = yaml.safe_load((WORKFLOWS.parent / "dependabot.yml").read_text(encoding="utf-8"))
        docker = next(u for u in dependabot["updates"] if u["package-ecosystem"] == "docker")
        assert "/.github/oasdiff" in docker["directories"]
        actions = next(u for u in dependabot["updates"] if u["package-ecosystem"] == "github-actions")
        assert "/.github/actions/*" in actions["directories"]

    def test_the_check_compares_the_schema_the_backend_prints(self, api) -> None:
        steps = api["jobs"]["api-contract"]["steps"]
        names = [s.get("name") for s in steps]
        printing = steps[names.index("Print the API schema")]
        checking = steps[names.index("Check the API contract")]
        order = [names.index(n) for n in ("Install oasdiff", "Print the API schema", "Check the API contract")]
        assert order == sorted(order)
        assert printing["working-directory"] == "backend"
        assert printing["run"] == 'uv run python -m plak.api.openapi_file > "$RUNNER_TEMP/openapi.json"'
        assert 'api-check --spec "$RUNNER_TEMP/openapi.json" --oasdiff "$OASDIFF"' in checking["run"]
        assert checking["env"]["OASDIFF"] == "${{ steps.oasdiff.outputs.path }}"
        assert steps[names.index("Install oasdiff")]["id"] == "oasdiff"
        assert "${{" not in checking["run"]

    def test_only_a_pull_request_gets_a_comment(self, api) -> None:
        checking = next(s for s in api["jobs"]["api-contract"]["steps"] if s.get("name") == "Check the API contract")
        assert 'if [ "$EVENT" = pull_request ]; then\n  args+=(--comment-file "$COMMENT_FILE")' in checking["run"]

    def test_the_comment_cannot_fail_the_check(self, api) -> None:
        """A fork or Dependabot pull request has a read-only token."""
        step = next(s for s in api["jobs"]["api-contract"]["steps"] if "comment" in s.get("name", ""))
        assert step["continue-on-error"] is True
        assert "github.event_name == 'pull_request'" in step["if"]
        assert step["uses"] == "./.github/actions/sticky-comment"
        assert step["with"]["token"] == "${{ github.token }}"

    def test_the_comment_marker_is_the_one_the_script_writes(self, api) -> None:
        script = RELEASE_COMMANDS.read_text(encoding="utf-8")
        step = next(s for s in api["jobs"]["api-contract"]["steps"] if "comment" in s.get("name", ""))
        assert f'API_COMMENT_MARKER = "{step["with"]["marker"]}"\n' in script
        assert step["with"]["comment-file"] == "${{ runner.temp }}/api-comment.md"


class TestTheStickyCommentAction:
    def test_nothing_from_the_caller_is_interpolated_into_the_script(self) -> None:
        """The marker, the file and the token reach the script through env."""
        step = _action("sticky-comment")["runs"]["steps"][0]
        assert "${{" not in step["run"]
        assert step["env"]["GH_TOKEN"] == "${{ inputs.token }}"
        assert step["env"]["MARKER"] == "${{ inputs.marker }}"
        assert step["env"]["COMMENT_FILE"] == "${{ inputs.comment-file }}"

    def test_it_updates_one_comment_and_removes_it_when_the_file_is_empty(self) -> None:
        run = _action("sticky-comment")["runs"]["steps"][0]["run"]
        assert 'startswith(env.MARKER)' in run
        for method in ("PATCH", "POST", "DELETE"):
            assert f"--method {method}" in run


def _origin(tmp_path: Path) -> tuple[Path, Path]:
    """A bare `origin` with beta at one commit, and a clone of it."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    _git(tmp_path, "init", "-q", "--bare", "-b", "beta", str(origin))
    _git(tmp_path, "init", "-q", "-b", "beta", str(work))
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "commit", "-q", "--allow-empty", "-m", "Merge something")
    _git(work, "push", "-q", "origin", "beta")
    return origin, work


def _gh(tmp_path: Path) -> tuple[str, Path]:
    """A PATH with a `gh` that answers the bot's user id and logs every
    other call, one NUL-separated argument list per line, with the token it
    was given in front."""
    stubs = tmp_path / "bin"
    stubs.mkdir()
    log = tmp_path / "gh.log"
    (stubs / "gh").write_text(
        "#!/bin/sh\n"
        '[ "$1" = api ] && { echo 123456; exit 0; }\n'
        f'{{ printf "%s\\0" "$GH_TOKEN" "$@"; echo; }} >> "{log}"\n',
        encoding="utf-8",
    )
    (stubs / "gh").chmod(0o755)
    return f"{stubs}:{os.environ['PATH']}", log


def _opening(tag: str, **env: str) -> dict[str, str]:
    """The environment of the step that pushes and opens, as `open` sets it:
    the button's mode unless told otherwise."""
    return {
        "GH_TOKEN": "app-token",
        "APP_SLUG": "digigilde-plak-release",
        "GITHUB_REPOSITORY": "DigiGilde/plak",
        "GITHUB_SHA": "0123abc",
        "TAG": tag,
        "EVENT": "workflow_dispatch",
        "NUMBER": "",
        "OLD_TAG": "",
        "OLD_SHA": "",
        **env,
    }


def _gh_calls(log: Path) -> list[list[str]]:
    """What `gh` was called with, other than for the user id: the token,
    then the arguments."""
    if not log.exists():
        return []
    return [call.split("\0") for call in log.read_text(encoding="utf-8").removesuffix("\0\n").split("\0\n")]


class TestTheReleaseWorkflow:
    """What decide answers is tested in test_release.py; here how the
    workflow acts on it, when the App token exists and what it pushes."""

    @pytest.fixture
    def job(self, release) -> dict:
        return release["jobs"]["tag"]

    def test_every_push_to_beta_and_nothing_else_runs_it(self, release) -> None:
        assert release[True] == {"push": {"branches": ["beta"]}}
        assert release["permissions"] == {"contents": "read"}
        assert release["concurrency"] == {"group": "release", "cancel-in-progress": False}

    def test_decide_reads_the_changelog_and_every_tag_without_a_token(self, release) -> None:
        decide = release["jobs"]["decide"]
        checkout, deciding = decide["steps"]
        assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
        assert deciding["run"] == "python3 scripts/release.py decide"
        assert decide["outputs"] == {
            "route": "${{ steps.decide.outputs.route }}",
            "tag": "${{ steps.decide.outputs.tag }}",
            "commit": "${{ steps.decide.outputs.commit }}",
        }
        assert "permissions" not in decide and "environment" not in decide

    def test_there_is_nothing_but_deciding_and_tagging(self, release) -> None:
        """No job writes or pushes a release any more: that is the release
        pull request, merged like any other."""
        assert set(release["jobs"]) == {"decide", "tag"}

    def test_only_a_release_route_tags(self, job) -> None:
        """A push whose newest section is tagged already never touches the
        App."""
        assert job["needs"] == "decide"
        assert job["if"] == "needs.decide.outputs.route == 'release'"
        assert job["env"] == {
            "TAG": "${{ needs.decide.outputs.tag }}",
            "COMMIT": "${{ needs.decide.outputs.commit }}",
        }

    def test_the_job_with_the_key_runs_no_dependencies(self, job) -> None:
        assert job["environment"] == "release"
        assert job["permissions"] == {"contents": "read"}
        assert [s["uses"].split("@")[0] for s in job["steps"] if "uses" in s] == [
            "actions/checkout",
            "actions/create-github-app-token",
        ]
        assert not any("uv " in s.get("run", "") or "docker " in s.get("run", "") for s in job["steps"])

    def test_the_tag_is_checked_before_the_token_exists(self, job) -> None:
        names = [s.get("name") or s["uses"].split("@")[0] for s in job["steps"]]
        assert names == [
            "actions/checkout",
            "Check the tag",
            "Get a token of the release App",
            "Tag and push",
        ]
        assert job["steps"][0]["with"] == {"fetch-depth": 0, "persist-credentials": False}
        assert _step(job, "Check the tag")["run"] == 'python3 scripts/release.py validate-tag "$TAG"'
        assert _step(job, "Get a token of the release App")["with"] == {
            "client-id": "${{ vars.RELEASE_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
            "permission-contents": "write",
        }
        pushing = _step(job, "Tag and push")
        assert pushing["env"] == {
            "GH_TOKEN": "${{ steps.app.outputs.token }}",
            "APP_SLUG": "${{ steps.app.outputs.app-slug }}",
        }
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step.get("name")

    def test_the_tag_goes_up_on_the_release_commit_and_nothing_else_does(self, job, tmp_path) -> None:
        """Run against a real origin, with `gh` stubbed to answer the bot's
        user id. The commit decide named is older than the checkout, and the
        checkout has a commit origin lacks: only the annotated tag goes up,
        on the named commit, and beta stays where it was."""
        origin, work = _origin(tmp_path)
        _git(work, "commit", "-q", "--allow-empty", "-m", "Release v2026.10.1")
        released = _git(work, "rev-parse", "HEAD")
        _git(work, "commit", "-q", "--allow-empty", "-m", "Merge something after")
        _git(work, "push", "-q", "origin", "beta")
        beta = _git(origin, "rev-parse", "beta")
        _git(work, "commit", "-q", "--allow-empty", "-m", "Not on beta")
        path, log = _gh(tmp_path)

        result = _run_unnamed(
            _step(job, "Tag and push"),
            work,
            PATH=path,
            GH_TOKEN="token",
            APP_SLUG="digigilde-plak-release",
            TAG="v2026.10.1",
            COMMIT=released,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _git(origin, "cat-file", "-t", "v2026.10.1") == "tag"
        assert _git(origin, "rev-parse", "v2026.10.1^{commit}") == released
        tagged = _git(origin, "for-each-ref", "--format=%(contents:subject)|%(taggername)|%(taggeremail)", "refs/tags")
        assert tagged == (
            "Plak v2026.10.1|digigilde-plak-release[bot]|"
            "<123456+digigilde-plak-release[bot]@users.noreply.github.com>"
        )
        assert _git(origin, "rev-parse", "beta") == beta
        assert _git(origin, "for-each-ref", "--format=%(refname)") == "refs/heads/beta\nrefs/tags/v2026.10.1"
        assert _gh_calls(log) == []

    def test_a_tag_that_is_there_already_is_not_moved(self, job, tmp_path) -> None:
        """A tag pushed by hand, or by a run that raced this one: the push is
        refused rather than moving it, and the job goes red."""
        origin, work = _origin(tmp_path)
        first = _git(work, "rev-parse", "HEAD")
        _git(work, "tag", "-a", "v2026.10.1", "-m", "Plak v2026.10.1")
        _git(work, "push", "-q", "origin", "v2026.10.1")
        _git(work, "tag", "-d", "v2026.10.1")
        _git(work, "commit", "-q", "--allow-empty", "-m", "Release v2026.10.1")
        _git(work, "push", "-q", "origin", "beta")
        path, _ = _gh(tmp_path)

        result = _run_unnamed(
            _step(job, "Tag and push"),
            work,
            PATH=path,
            GH_TOKEN="token",
            APP_SLUG="digigilde-plak-release",
            TAG="v2026.10.1",
            COMMIT=_git(work, "rev-parse", "HEAD"),
        )

        assert result.returncode != 0
        assert _git(origin, "rev-parse", "v2026.10.1^{commit}") == first

    def test_the_push_names_only_the_tag(self, job) -> None:
        run = _step(job, "Tag and push")["run"]
        assert run.rstrip().endswith('push origin "refs/tags/${TAG}"')
        assert "refs/heads" not in run and "--force" not in run and "--atomic" not in run


class TestThePrepareReleaseWorkflow:
    """What prepare and verify-staged write and refuse is tested in
    test_release.py; here when the workflow runs, when the App token exists
    and what it pushes and opens."""

    @pytest.fixture
    def prepare(self, prepare_release) -> dict:
        return prepare_release["jobs"]["prepare"]

    @pytest.fixture
    def job(self, prepare_release) -> dict:
        return prepare_release["jobs"]["open"]

    def test_the_button_opens_it_and_a_push_to_beta_refreshes_it(self, prepare_release) -> None:
        """No schedule, no pull request event: a release is made when someone
        decides to make one, and kept current by what lands on beta."""
        assert prepare_release[True] == {"workflow_dispatch": None, "push": {"branches": ["beta"]}}
        assert prepare_release["permissions"] == {"contents": "read"}
        assert prepare_release["concurrency"] == {"group": "prepare-release", "cancel-in-progress": False}
        assert set(prepare_release["jobs"]) == {"prepare", "open"}

    def test_only_beta_is_released(self, prepare, job) -> None:
        """The button offers every branch; pressed on another one, both jobs
        stand down rather than release that branch. `open` also stands down
        when there is nothing to push."""
        assert prepare["if"] == "github.ref == 'refs/heads/beta'"
        assert job["if"] == "github.ref == 'refs/heads/beta' && needs.prepare.outputs.changed == 'true'"
        assert job["needs"] == "prepare"

    def test_a_push_refreshes_only_an_open_release_pull_request(self, prepare) -> None:
        """The steps that run the dependencies and write the release are
        skipped when a push finds nothing to refresh."""
        names = [s.get("name") or s["uses"].split("@")[0] for s in prepare["steps"]]
        assert names == [
            "actions/checkout",
            "Is a release waiting for its tag",
            "Find the open release pull request",
            "Install uv",
            "Install oasdiff",
            "Print the API schema",
            "Pick the tag",
            "Write the release",
            "Compare with the open pull request",
            "Hand the release on as a patch",
            "actions/upload-artifact",
        ]
        assert _step(prepare, "Is a release waiting for its tag")["run"] == "python3 scripts/release.py pending"
        finding = _step(prepare, "Find the open release pull request")
        assert finding["env"] == {
            "GH_TOKEN": "${{ github.token }}",
            "EVENT": "${{ github.event_name }}",
            "PENDING": "${{ steps.pending.outputs.version }}",
        }
        assert "python3 scripts/release.py release-pr --repo" in finding["run"]
        for name in ("Install uv", "Install oasdiff", "Print the API schema", "Write the release"):
            assert _step(prepare, name)["if"] == "steps.find.outputs.mode != 'none'", name
        assert _step(prepare, "Pick the tag")["if"] == "steps.find.outputs.mode == 'refresh'"
        picking = _step(prepare, "Pick the tag")
        assert picking["run"] == 'python3 scripts/release.py refresh-tag --branch "release/${OLD_TAG}"'
        for step in prepare["steps"][-2:]:
            assert step["if"] == "steps.compare.outputs.changed == 'true'"

    def test_prepare_sees_every_tag_and_keeps_no_token(self, prepare) -> None:
        """The tags decide the next one."""
        checkout = prepare["steps"][0]
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"] == {"ref": "${{ github.sha }}", "fetch-depth": 0, "persist-credentials": False}

    def test_the_job_that_runs_the_dependencies_never_holds_the_key(self, prepare, job) -> None:
        """uv sync installs dependencies and the backend prints its schema,
        on a runner without the environment, the secret or the token."""
        assert "environment" not in prepare
        assert prepare["permissions"] == {"contents": "read", "pull-requests": "read"}
        assert "secrets." not in yaml.safe_dump(prepare)
        assert job["environment"] == "release"
        assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
        assert [s["uses"].split("@")[0] for s in job["steps"] if "uses" in s] == [
            "actions/checkout",
            "actions/download-artifact",
            "actions/create-github-app-token",
        ]
        assert not any("uv " in s.get("run", "") or "docker " in s.get("run", "") for s in job["steps"])

    def test_oasdiff_and_the_schema_come_as_in_the_api_check(self, prepare, api) -> None:
        """The release compares with the same oasdiff and the same schema
        the pull request check used."""
        check = api["jobs"]["api-contract"]
        assert _step(prepare, "Install oasdiff")["uses"] == _step(check, "Install oasdiff")["uses"]
        assert _step(prepare, "Install oasdiff")["id"] == "oasdiff"
        schema = "Print the API schema"
        assert _step(prepare, schema)["run"] == _step(check, schema)["run"]
        assert _step(prepare, schema).get("working-directory") == _step(check, schema).get("working-directory")
        # Under pipefail a grep without a match ends the step before its
        # message; here the message gets its turn.
        assert "| cut -d' ' -f2 || true)" in _action("install-oasdiff")["runs"]["steps"][0]["run"]
        writing = _step(prepare, "Write the release")
        assert writing["id"] == "prepare"
        assert writing["env"] == {"TAG": "${{ steps.tag.outputs.tag }}", "OASDIFF": "${{ steps.oasdiff.outputs.path }}"}
        assert (
            "python3 scripts/release.py prepare \\\n"
            '  --spec "$RUNNER_TEMP/openapi.json" --oasdiff "$OASDIFF" ${args[@]+"${args[@]}"}\n'
        ) in writing["run"]
        assert 'args+=(--tag "$TAG")' in writing["run"]
        # Nothing else reaches `open`: it looks up the pull request itself.
        assert prepare["outputs"] == {
            "tag": "${{ steps.prepare.outputs.tag }}",
            "changed": "${{ steps.compare.outputs.changed }}",
        }

    def test_the_patch_is_the_whole_staged_release_of_this_commit(self, prepare, job) -> None:
        handing = _step(prepare, "Hand the release on as a patch")
        assert 'git diff --cached --binary --no-renames > "$RUNNER_TEMP/release/release.patch"' in handing["run"]
        upload = next(s for s in prepare["steps"] if s.get("uses", "").startswith("actions/upload-artifact@"))
        download = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/download-artifact@"))
        assert upload["with"]["name"] == download["with"]["name"] == "release-${{ github.sha }}"
        assert upload["with"]["if-no-files-found"] == "error"

    def test_the_tag_and_the_patch_are_checked_before_the_token_exists(self, job) -> None:
        """Both come from the job that ran the dependencies. The patch goes
        into the index only, so the release.py that checks it is the one in
        the commit, whatever the patch holds."""
        names = [s.get("name") or s["uses"].split("@")[0] for s in job["steps"]]
        assert names == [
            "actions/checkout",
            "Find the open release pull request",
            "actions/download-artifact",
            "Check the tag",
            "Apply the release",
            "Check that it is the release and nothing else",
            "Get a token of the release App",
            "Push the branch and open the pull request",
        ]
        # Of what `prepare` computed only the tag comes in, and it is validated;
        # the pull request to touch is this job's own finding, made with the
        # workflow token before the App token exists.
        assert job["env"] == {"TAG": "${{ needs.prepare.outputs.tag }}"}
        assert "needs.prepare" not in yaml.safe_dump(job["steps"])
        finding = _step(job, "Find the open release pull request")
        assert finding["id"] == "find"
        assert finding["env"] == {"GH_TOKEN": "${{ github.token }}"}
        assert "python3 scripts/release.py release-pr --repo" in finding["run"]
        pushing = _step(job, "Push the branch and open the pull request")
        assert pushing["env"] == {
            "GH_TOKEN": "${{ steps.app.outputs.token }}",
            "APP_SLUG": "${{ steps.app.outputs.app-slug }}",
            "EVENT": "${{ github.event_name }}",
            "NUMBER": "${{ steps.find.outputs.number }}",
            "OLD_TAG": "${{ steps.find.outputs.old_tag }}",
            "OLD_SHA": "${{ steps.find.outputs.old_sha }}",
        }
        # verify-staged holds the tag against the existing ones, so it sees them all.
        assert job["steps"][0]["with"] == {"ref": "${{ github.sha }}", "fetch-depth": 0, "persist-credentials": False}
        assert _step(job, "Check the tag")["run"] == 'python3 scripts/release.py validate-tag "$TAG"'
        assert _step(job, "Apply the release")["run"] == (
            'git apply --cached --binary "$RUNNER_TEMP/release/release.patch"'
        )
        assert _step(job, "Check that it is the release and nothing else")["run"] == (
            'python3 scripts/release.py verify-staged --tag "$TAG"'
        )
        for step in job["steps"]:
            assert "${{" not in step.get("run", ""), step.get("name")

    def test_the_token_may_push_a_branch_and_open_a_pull_request(self, job) -> None:
        """And nothing else: a release reaches beta through the merge queue."""
        assert _step(job, "Get a token of the release App")["with"] == {
            "client-id": "${{ vars.RELEASE_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
            "permission-contents": "write",
            "permission-pull-requests": "write",
        }

    def test_the_pull_request_is_opened_with_the_app_token(self, job) -> None:
        """One opened with GITHUB_TOKEN would start no checks, and the merge
        queue would wait for them for ever."""
        opening = _step(job, "Push the branch and open the pull request")
        assert opening["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
        # The one force is the refresh of the branch the pull request already
        # has, with a lease on the commit that was read.
        assert " -f " not in opening["run"]
        assert opening["run"].count("--force") == 1
        assert '--force-with-lease="refs/heads/release/${TAG}:${OLD_SHA}"' in opening["run"]

    def test_the_release_goes_up_as_a_branch_and_a_pull_request_of_the_app(self, job, tmp_path) -> None:
        """Run against a real origin, with `gh` stubbed: the release commit
        lands on release/vX on top of beta, beta and the tags stay as they
        were, and the pull request into beta is opened with the App token."""
        origin, work = _origin(tmp_path)
        beta = _git(origin, "rev-parse", "beta")
        (work / "CHANGELOG.md").write_text("released\n", encoding="utf-8")
        _git(work, "add", "CHANGELOG.md")
        path, log = _gh(tmp_path)

        result = _run_unnamed(
            _step(job, "Push the branch and open the pull request"), work, PATH=path, **_opening("v2026.10.1")
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _git(origin, "log", "-1", "--format=%s|%an|%ae|%P", "release/v2026.10.1") == (
            "Release v2026.10.1|digigilde-plak-release[bot]|"
            f"123456+digigilde-plak-release[bot]@users.noreply.github.com|{beta}"
        )
        assert _git(origin, "show", "release/v2026.10.1:CHANGELOG.md") == "released"
        assert _git(origin, "rev-parse", "beta") == beta
        assert _git(origin, "tag", "-l") == ""
        [call] = _gh_calls(log)
        token, *args = call
        assert token == "app-token"
        body = args.pop(args.index("--body") + 1)
        assert args == [
            "pr",
            "create",
            "--repo",
            "DigiGilde/plak",
            "--base",
            "beta",
            "--head",
            "release/v2026.10.1",
            "--title",
            "Release v2026.10.1",
            "--body",
        ]
        paragraphs = body.split("\n\n")
        assert len(paragraphs) == 2
        assert all(p and "\n" not in p for p in paragraphs)
        assert "releases v2026.10.1" in paragraphs[0]
        assert "refreshed on every push to beta" in paragraphs[1]
        assert "edits made here are overwritten" in paragraphs[1]

    def test_a_release_branch_that_is_there_already_is_left_alone(self, job, tmp_path) -> None:
        """The button pressed twice for one tag, or pressed again after a run
        that pushed and then failed to open the pull request: nothing is
        forced, no second pull request is opened, and the message names the
        branch to delete."""
        origin, work = _origin(tmp_path)
        _git(work, "switch", "-q", "-c", "earlier")
        _git(work, "commit", "-q", "--allow-empty", "-m", "Release v2026.10.1")
        _git(work, "push", "-q", "origin", "HEAD:refs/heads/release/v2026.10.1")
        earlier = _git(work, "rev-parse", "HEAD")
        _git(work, "switch", "-q", "beta")
        (work / "CHANGELOG.md").write_text("released\n", encoding="utf-8")
        _git(work, "add", "CHANGELOG.md")
        path, log = _gh(tmp_path)

        result = _run_unnamed(
            _step(job, "Push the branch and open the pull request"), work, PATH=path, **_opening("v2026.10.1")
        )

        assert result.returncode != 0
        assert "The branch release/v2026.10.1 exists already. Delete it and run the workflow again." in result.stderr
        assert _git(origin, "rev-parse", "release/v2026.10.1") == earlier
        assert _git(work, "log", "-1", "--format=%s") == "Merge something"
        assert _gh_calls(log) == []

    @pytest.fixture
    def open_pull_request(self, tmp_path) -> tuple[Path, Path, str]:
        """An origin with beta, the branch release/v2026.10.1 of the open pull
        request one commit ahead of it, and a clone on beta with the fresh
        release staged. Returns origin, the clone and the branch's commit."""
        origin, work = _origin(tmp_path)
        _git(work, "switch", "-q", "-c", "release/v2026.10.1")
        (work / "CHANGELOG.md").write_text("released earlier\n", encoding="utf-8")
        _git(work, "add", "CHANGELOG.md")
        _git(work, "commit", "-q", "-m", "Release v2026.10.1")
        _git(work, "push", "-q", "origin", "HEAD:refs/heads/release/v2026.10.1")
        head = _git(work, "rev-parse", "HEAD")
        _git(work, "switch", "-q", "beta")
        (work / "CHANGELOG.md").write_text("released, with what landed since\n", encoding="utf-8")
        _git(work, "add", "CHANGELOG.md")
        return origin, work, head

    def test_a_refresh_moves_the_branch_of_the_pull_request_with_a_lease(self, job, open_pull_request, tmp_path):
        """Same tag: the branch is force-pushed on top of beta, but only over
        the commit `prepare` read, and the pull request hears about it."""
        origin, work, head = open_pull_request
        beta = _git(origin, "rev-parse", "beta")
        path, log = _gh(tmp_path)

        result = _run_unnamed(
            _step(job, "Push the branch and open the pull request"),
            work,
            PATH=path,
            **_opening("v2026.10.1", EVENT="push", NUMBER="12", OLD_TAG="v2026.10.1", OLD_SHA=head),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _git(origin, "log", "-1", "--format=%s|%P", "release/v2026.10.1") == f"Release v2026.10.1|{beta}"
        assert _git(origin, "show", "release/v2026.10.1:CHANGELOG.md") == "released, with what landed since"
        assert _git(origin, "rev-parse", "beta") == beta
        [call] = _gh_calls(log)
        token, *args = call
        assert token == "app-token"
        assert args[:5] == ["pr", "comment", "12", "--repo", "DigiGilde/plak"]
        assert "Refreshed from beta 0123abc" in args[-1]
        assert "edits made here are overwritten" in args[-1]

    def test_a_refresh_over_a_commit_it_did_not_read_is_refused(self, job, open_pull_request, tmp_path):
        """The lease: someone pushed to the branch in between, and the force
        would throw that away."""
        origin, work, head = open_pull_request
        beta = _git(origin, "rev-parse", "beta")
        path, log = _gh(tmp_path)

        result = _run_unnamed(
            _step(job, "Push the branch and open the pull request"),
            work,
            PATH=path,
            **_opening("v2026.10.1", EVENT="push", NUMBER="12", OLD_TAG="v2026.10.1", OLD_SHA=beta),
        )

        assert result.returncode != 0
        assert _git(origin, "rev-parse", "release/v2026.10.1") == head
        assert _gh_calls(log) == []

    @pytest.mark.parametrize(
        ("env", "reason"),
        [
            ({"NUMBER": "", "OLD_TAG": "", "OLD_SHA": ""}, "there is none; the button opens one"),
            ({"NUMBER": "12; rm -rf", "OLD_TAG": "v2026.10.1"}, "was not found properly"),
            ({"NUMBER": "12", "OLD_TAG": "v2026.10.1", "OLD_SHA": "HEAD"}, "was not found properly"),
            ({"NUMBER": "12", "OLD_TAG": "main"}, "was not found properly"),
        ],
        ids=["no-pull-request", "number", "sha", "tag"],
    )
    def test_a_push_without_a_proper_pull_request_of_its_own_pushes_nothing(
        self, job, open_pull_request, tmp_path, env, reason
    ) -> None:
        """A push never opens a pull request, and acts only on a finding of
        one shape: a number, a release tag and a full sha."""
        origin, work, head = open_pull_request
        beta = _git(origin, "rev-parse", "beta")
        path, log = _gh(tmp_path)

        result = _run_unnamed(
            _step(job, "Push the branch and open the pull request"),
            work,
            PATH=path,
            **_opening("v2026.10.1", EVENT="push", **{"OLD_SHA": head, **env}),
        )

        assert result.returncode != 0
        assert reason in result.stderr
        assert _git(origin, "rev-parse", "release/v2026.10.1") == head
        assert _git(origin, "rev-parse", "beta") == beta
        assert _git(origin, "for-each-ref", "--format=%(refname)", "refs/heads/").split() == [
            "refs/heads/beta",
            "refs/heads/release/v2026.10.1",
        ]
        assert _gh_calls(log) == []

    def test_what_prepare_says_about_the_pull_request_does_not_reach_the_token(self, job, open_pull_request, tmp_path):
        """A forged number, tag or sha in prepare's outputs: the step reads
        its own finding, so the forged values are not even in its
        environment, and nothing but the found pull request is touched."""
        origin, work, head = open_pull_request
        path, log = _gh(tmp_path)
        forged = {"MODE": "refresh", "NEEDS_NUMBER": "1", "NEEDS_OLD_TAG": "v2026.1.1", "NEEDS_OLD_SHA": "f" * 40}

        result = _run_unnamed(
            _step(job, "Push the branch and open the pull request"),
            work,
            PATH=path,
            **_opening("v2026.10.1", EVENT="push", NUMBER="12", OLD_TAG="v2026.10.1", OLD_SHA=head, **forged),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        [call] = _gh_calls(log)
        assert call[1:4] == ["pr", "comment", "12"]
        assert _git(origin, "rev-parse", "release/v2026.10.1") != head

    def test_a_stale_tag_gets_a_new_branch_and_pull_request_and_the_old_one_is_closed(
        self, job, open_pull_request, tmp_path
    ):
        origin, work, head = open_pull_request
        path, log = _gh(tmp_path)

        result = _run_unnamed(
            _step(job, "Push the branch and open the pull request"),
            work,
            PATH=path,
            **_opening("v2026.10.2", EVENT="push", NUMBER="12", OLD_TAG="v2026.10.1", OLD_SHA=head),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _git(origin, "log", "-1", "--format=%s", "release/v2026.10.2") == "Release v2026.10.2"
        # The old branch goes through the pull request's close, not through git here.
        assert _git(origin, "rev-parse", "release/v2026.10.1") == head
        created, closed = _gh_calls(log)
        assert created[1:3] == ["pr", "create"]
        assert created[created.index("--head") + 1] == "release/v2026.10.2"
        assert closed[1:6] == ["pr", "close", "12", "--repo", "DigiGilde/plak"]
        assert "--delete-branch" in closed
        assert "Replaced by the release pull request for v2026.10.2" in closed[-1]

    LISTING = json.dumps(
        [
            {
                "number": 7,
                "headRefName": "feature/x",
                "headRefOid": "a" * 40,
                "headRepositoryOwner": {"login": "DigiGilde"},
            },
            {
                "number": 12,
                "headRefName": "release/v2026.10.1",
                "headRefOid": "b" * 40,
                "headRepositoryOwner": {"login": "DigiGilde"},
            },
        ]
    )
    FOUND: ClassVar[dict[str, str]] = {"number": "12", "old_tag": "v2026.10.1", "old_sha": "b" * 40}

    @staticmethod
    def _listing_stub(tmp_path: Path, listing: str) -> str:
        """A PATH whose `gh` answers every call with `listing`."""
        stubs = tmp_path / "bin"
        stubs.mkdir()
        (stubs / "gh").write_text(f"#!/bin/sh\nprintf '%s' '{listing}'\n", encoding="utf-8")
        (stubs / "gh").chmod(0o755)
        return f"{stubs}{os.pathsep}{os.environ['PATH']}"

    @pytest.mark.parametrize(
        ("event", "pending", "listing", "expected"),
        [
            ("workflow_dispatch", "", "[]", {"mode": "open"}),
            ("push", "2026.10.1", LISTING, {"mode": "none"}),
            ("push", "", "[]", {"mode": "none", "number": "", "old_tag": "", "old_sha": ""}),
            ("push", "", LISTING, {"mode": "refresh", **FOUND}),
        ],
        ids=["button", "release-waiting-for-its-tag", "no-pull-request", "pull-request"],
    )
    def test_a_push_finds_the_pull_request_to_refresh(self, prepare, tmp_path, event, pending, listing, expected):
        """The button always opens; a push refreshes only an open release pull
        request, and not while a merged release still waits for its tag. Run
        from the repository, so the step finds scripts/release.py."""
        output = tmp_path / "output"
        output.touch()
        result = _run(
            _step(prepare, "Find the open release pull request"),
            ROOT,
            PATH=self._listing_stub(tmp_path, listing),
            EVENT=event,
            GITHUB_REPOSITORY="DigiGilde/plak",
            GITHUB_OUTPUT=str(output),
            GH_TOKEN="x",
            PENDING=pending,
        )
        assert result.returncode == 0, result.stderr
        assert dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()) == expected

    @pytest.mark.parametrize(
        ("listing", "expected"),
        [(LISTING, FOUND), ("[]", {"number": "", "old_tag": "", "old_sha": ""})],
        ids=["pull-request", "none"],
    )
    def test_open_finds_the_pull_request_itself(self, job, tmp_path, listing, expected):
        """The same lookup, from `open`, with the workflow token and before
        the App token: the only numbers the push step acts on."""
        output = tmp_path / "output"
        output.touch()
        result = _run(
            _step(job, "Find the open release pull request"),
            ROOT,
            PATH=self._listing_stub(tmp_path, listing),
            GITHUB_REPOSITORY="DigiGilde/plak",
            GITHUB_OUTPUT=str(output),
            GH_TOKEN="x",
        )
        assert result.returncode == 0, result.stderr
        assert dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines()) == expected
        names = [s.get("name") or s["uses"].split("@")[0] for s in job["steps"]]
        assert names.index("Find the open release pull request") < names.index("Get a token of the release App")

    def test_a_refresh_that_would_push_the_same_tree_is_skipped(self, prepare, tmp_path):
        """What the open pull request holds is compared tree for tree with
        what prepare wrote; only a difference goes on to `open`."""
        _, work = _origin(tmp_path)
        (work / "CHANGELOG.md").write_text("released\n", encoding="utf-8")
        _git(work, "add", "CHANGELOG.md")
        _git(work, "commit", "-q", "-m", "Release v2026.10.1")
        same = _git(work, "rev-parse", "HEAD")
        _git(work, "reset", "-q", "--soft", "HEAD~1")
        step = _step(prepare, "Compare with the open pull request")
        outcomes = {}
        for case, old in (("same", same), ("other", _git(work, "rev-parse", "beta")), ("open", same)):
            output = tmp_path / f"output-{case}"
            output.touch()
            mode = "open" if case == "open" else "refresh"
            result = _run(step, work, MODE=mode, OLD_SHA=old, GITHUB_OUTPUT=str(output))
            assert result.returncode == 0, result.stderr
            outcomes[case] = output.read_text(encoding="utf-8")
        assert outcomes == {"same": "changed=false\n", "other": "changed=true\n", "open": "changed=true\n"}


class TestTheGitHubRelease:
    @pytest.fixture
    def job(self, deploy) -> dict:
        return deploy["jobs"]["github-release"]

    def test_it_comes_before_production_and_after_the_tests_and_attestations(self, job, deploy) -> None:
        """What production runs has been released, and a release is tested
        and carries the attestations its notes tell you to verify. It needs
        no ZAD project: the release is the image, wherever it runs."""
        assert job["needs"] == ["ci", "build", "provenance"]
        assert " ".join(job["if"].split()) == "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"
        production = deploy["jobs"]["production"]
        assert "github-release" in production["needs"]
        assert job["permissions"] == {"contents": "write"}
        assert job["env"] == {
            "TAG": "${{ github.ref_name }}",
            "IMAGE": "${{ needs.build.outputs.name }}",
            "DIGEST": "${{ needs.build.outputs.digest }}",
        }

    def test_the_notes_come_from_the_tag_with_the_image(self, job) -> None:
        checkout = job["steps"][0]
        assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
        publish = _step(job, "Publish the GitHub Release")
        assert publish["env"] == {"GH_TOKEN": "${{ github.token }}"}
        assert (
            'python3 scripts/release.py notes --tag "$TAG" --image "$IMAGE" --digest "$DIGEST" > "$notes"'
            in publish["run"]
        )
        assert 'gh release create "$TAG" --verify-tag --title "$TAG" --notes-file "$notes"' in publish["run"]
        assert "${{" not in publish["run"]

    @pytest.mark.parametrize(("exists", "created"), [(True, False), (False, True)], ids=["rerun", "first-run"])
    def test_a_rerun_leaves_the_release_and_lets_production_go_on(self, job, tmp_path, exists, created) -> None:
        """Run with `gh` and the notes stubbed: a release that is there
        already is no reason to fail, or production would never follow."""
        stubs = tmp_path / "bin"
        stubs.mkdir()
        log = tmp_path / "gh.log"
        (stubs / "gh").write_text(
            f'#!/bin/sh\necho "$*" >> "{log}"\n[ "$1 $2" = "release view" ] && exit {0 if exists else 1}\nexit 0\n',
            encoding="utf-8",
        )
        (stubs / "gh").chmod(0o755)
        (stubs / "python3").write_text("#!/bin/sh\necho notes\n", encoding="utf-8")
        (stubs / "python3").chmod(0o755)

        result = _run(
            _step(job, "Publish the GitHub Release"),
            tmp_path,
            PATH=f"{stubs}:{os.environ['PATH']}",
            TAG="v2026.10.4",
            IMAGE="ghcr.io/digigilde/plak",
            DIGEST="sha256:" + "a" * 64,
            RUNNER_TEMP=str(tmp_path),
        )

        assert result.returncode == 0, result.stdout + result.stderr
        calls = log.read_text(encoding="utf-8").splitlines()
        assert calls[0] == "release view v2026.10.4"
        assert any(call.startswith("release create v2026.10.4") for call in calls) is created


def _ruleset(name: str) -> dict:
    return json.loads((ROOT / ".github" / "rulesets" / f"{name}.json").read_text(encoding="utf-8"))


def _rule(ruleset: dict, kind: str) -> dict:
    return next(rule for rule in ruleset["rules"] if rule["type"] == kind)


class TestTheRulesets:
    """The rulesets docs/releasing.md applies, kept in the repository. The
    App id is filled in when they are applied; 0 stands in for it here."""

    def test_the_app_bypasses_only_the_creation_of_a_release_tag(self) -> None:
        """A bypass covers its whole ruleset, so what nobody may do, the App
        included, sits in a ruleset without one: deleting or rewriting beta,
        moving or deleting a release tag. A release reaches beta as a pull
        request through the merge queue like any other, so the App has no
        bypass on beta at all."""
        app = [{"actor_id": 0, "actor_type": "Integration", "bypass_mode": "always"}]
        rulesets = {
            "beta": ([], {"pull_request", "required_status_checks", "merge_queue"}),
            "beta-history": ([], {"deletion", "non_fast_forward"}),
            "release-tags": (app, {"creation"}),
            "release-tags-fixed": ([], {"update", "deletion"}),
        }
        assert {p.stem for p in (ROOT / ".github" / "rulesets").glob("*.json")} == set(rulesets)
        for name, (bypass, rules) in rulesets.items():
            ruleset = _ruleset(name)
            assert ruleset["bypass_actors"] == bypass, name
            assert {rule["type"] for rule in ruleset["rules"]} == rules, name
            assert ruleset["enforcement"] == "active", name

    def test_beta_keeps_what_the_classic_protection_held(self) -> None:
        for name in ("beta", "beta-history"):
            ruleset = _ruleset(name)
            assert ruleset["target"] == "branch"
            assert ruleset["conditions"]["ref_name"]["include"] == ["refs/heads/beta"]
        beta = _ruleset("beta")
        assert _rule(beta, "pull_request")["parameters"]["required_review_thread_resolution"] is True
        assert _rule(beta, "merge_queue")["parameters"]["merge_method"] == "REBASE"

    def test_every_required_check_is_a_job_that_exists(self) -> None:
        """A required check that no job reports blocks every merge."""
        jobs = set()
        for path in WORKFLOWS.glob("*.yml"):
            prefix = "ci / " if path.name == "ci.yml" else ""
            for key, job in _load(path.name)["jobs"].items():
                name = job.get("name", key)
                languages = job.get("strategy", {}).get("matrix", {}).get("language", [])
                if "${{ matrix.language }}" in name:
                    jobs |= {prefix + name.replace("${{ matrix.language }}", lang) for lang in languages}
                else:
                    jobs.add(prefix + name)
        required = _rule(_ruleset("beta"), "required_status_checks")["parameters"]["required_status_checks"]
        checks = {check["context"] for check in required}
        assert checks <= jobs, checks - jobs
        assert {"ci / backend-tests", "ci / backend-coverage", "ci / release-script"} <= checks
        assert {"api-contract", "changelog"} <= checks

    def test_both_tag_rulesets_cover_the_release_tags(self) -> None:
        for name in ("release-tags", "release-tags-fixed"):
            tags = _ruleset(name)
            assert tags["target"] == "tag", name
            assert tags["conditions"]["ref_name"]["include"] == ["refs/tags/v*"], name

    def test_the_drift_check_runs_on_pull_requests_and_daily(self) -> None:
        """One workflow serves the pull requests, the merge queue and the
        daily run; ci.yml cannot, because a called workflow carries no
        schedule."""
        workflow = _load("rulesets.yml")
        assert workflow["permissions"] == {"contents": "read"}
        assert set(workflow[True]) == {"pull_request", "merge_group", "schedule", "workflow_dispatch"}
        assert workflow[True]["schedule"] == [{"cron": "43 5 * * *"}]
        step = _step(workflow["jobs"]["rulesets"], "Compare the live rulesets with the files")
        assert step["run"] == "python3 scripts/rulesets.py check"
        assert step["env"] == {"GH_TOKEN": "${{ github.token }}"}
        assert "rulesets" in {
            c["context"]
            for c in _rule(_ruleset("beta"), "required_status_checks")["parameters"]["required_status_checks"]
        }


class TestDependabot:
    def test_every_multi_ecosystem_group_is_a_name_dependabot_accepts(self) -> None:
        """Dependabot rejects a group name under three characters and then
        runs no updates at all; the only trace is a failed check on the
        commit. `ci` did exactly that."""
        dependabot = yaml.safe_load((WORKFLOWS.parent / "dependabot.yml").read_text(encoding="utf-8"))
        groups = dependabot["multi-ecosystem-groups"]
        assert all(len(name) >= 3 for name in groups)
        used = {u["multi-ecosystem-group"] for u in dependabot["updates"] if "multi-ecosystem-group" in u}
        assert used == set(groups)
