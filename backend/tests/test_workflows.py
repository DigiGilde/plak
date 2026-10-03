"""The gates in .github/workflows, pinned down here rather than on GitHub.

They run there now, but a green run says the wiring held for that event;
it says nothing about the events that did not fire. Which job gates what,
which permission sits where and that every action is on a commit SHA is
checked here, on every commit.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import parts
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE_SCRIPT = ROOT / ".github" / "scripts" / "release.py"


def _load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


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


class TestTheCheckGate:
    def test_production_waits_for_the_checks(self, deploy) -> None:
        """BIO2 8.31.02: significant changes are tested before they go to
        production. With only `needs: build` a push to main went straight
        to production, tested or not."""
        assert deploy["jobs"]["production"]["needs"] == ["ci", "build"]

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
        assert ci["jobs"]["backend-tests"]["strategy"]["matrix"]["part"] == list(parts.PARTS)
        assert ci["jobs"]["backend-tests"]["strategy"]["fail-fast"] is False

    def test_a_failed_backend_part_fails_the_required_check(self, ci) -> None:
        """A failed part skips a plain dependent, and a skipped required
        check counts as passed: the tests would go red and the merge
        button green. So `backend-coverage` runs regardless and fails itself."""
        backend = ci["jobs"]["backend-coverage"]
        assert backend["needs"] == "backend-tests"
        assert backend["if"] == "${{ !cancelled() }}"
        first = backend["steps"][0]
        assert first["if"] == "needs.backend-tests.result != 'success'"
        assert "exit 1" in first["run"]

    def test_the_coverage_floor_holds_over_the_merged_parts(self, ci) -> None:
        run = ci["jobs"]["backend-tests"]["steps"][-2]["run"]
        assert "--cov-fail-under=0" in run
        report = ci["jobs"]["backend-coverage"]["steps"][-1]["run"]
        assert "coverage combine" in report
        assert "coverage report" in report

    def test_the_release_script_has_a_floor_of_its_own(self, ci) -> None:
        """release.py lies outside src/plak, which is all the merged backend
        coverage measures. So its tests run again in the required job,
        measured on that file alone, kept out of the data that gets
        combined."""
        [step] = [
            s for s in ci["jobs"]["backend-coverage"]["steps"] if s.get("name") == "Release script tests with coverage"
        ]
        assert step["working-directory"] == "backend"
        assert step["env"] == {"COVERAGE_FILE": "${{ runner.temp }}/release.coverage"}
        run, report = step["run"].splitlines()
        assert run.endswith("-m pytest -q tests/test_release.py")
        for line in (run, report):
            assert "--include='*/.github/scripts/release.py'" in line
        assert "--fail-under=100" in report

    def test_the_checks_cover_backend_cli_frontend_and_vulnerabilities(self, ci) -> None:
        # These are also the names branch protection should be set to
        # later: `ci / backend-coverage`, `ci / cli`, `ci / frontend`,
        # `ci / vulnerabilities`, `ci / pre-commit`, `ci / secret-scan`,
        # `ci / containers`, `ci / e2e`.
        assert set(ci["jobs"]) == {
            "backend-coverage",
            "backend-tests",
            "cli",
            "cli-windows",
            "frontend",
            "vulnerabilities",
            "pre-commit",
            "secret-scan",
            "containers",
            "e2e",
        }


class TestTheScans:
    def test_the_image_is_scanned_before_anything_rolls_out(self, deploy) -> None:
        steps = deploy["jobs"]["build"]["steps"]
        scans = [s for s in steps if "trivy-action" in str(s.get("uses", ""))]

        # One that reports everything, one that closes the gate, and the SBOM.
        assert len(scans) == 3
        gate = [s for s in scans if s.get("with", {}).get("exit-code") == "1"]
        assert len(gate) == 1
        assert gate[0]["with"]["severity"] == "CRITICAL,HIGH"
        assert gate[0]["with"]["trivyignores"] == ".trivyignore.yaml"

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
        scans = [s for s in steps if "trivy-action" in str(s.get("uses", ""))]

        assert [s["with"]["exit-code"] for s in scans] == ["1"] * len(scans)
        assert {s["with"].get("scan-type", "image") for s in scans} == {"config", "image"}
        for step in scans:
            assert step["with"]["severity"] == "CRITICAL,HIGH"
            assert step["with"]["trivyignores"] == ".trivyignore.yaml"

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
            "release.yml",
        ]

        for path in paths:
            workflow = _load(path.name)
            for job in workflow["jobs"].values():
                for step in job.get("steps", []):
                    uses = step.get("uses")
                    if not uses or uses.startswith("./"):
                        continue
                    ref = uses.split("@")[1]
                    assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), uses

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


class TestTheReleaseGuards:
    """Production rolls out a release tag only after four checks, each of
    which fails the job rather than skipping it, so a refused tag shows red.
    The steps are run here as they are written in deploy.yml, against real
    repositories."""

    @pytest.fixture
    def production(self, deploy) -> dict:
        return deploy["jobs"]["production"]

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

    def test_the_guards_run_after_a_full_checkout_and_before_the_rollout(
        self, production
    ) -> None:
        """Ancestry and the newest tag need the whole history and every tag;
        a shallow checkout would refuse every release or pass the wrong one.
        The token stays out of .git/config: this job holds the ZAD api-key,
        and the repository is public, so the fetches need no credentials."""
        steps = production["steps"]
        assert steps[0]["uses"].startswith("actions/checkout@")
        assert steps[0]["with"] == {"fetch-depth": 0, "persist-credentials": False}
        assert [s.get("name") for s in steps[1:]] == [*GUARDS, "Roll out to ZAD"]
        assert production["env"] == {"TAG": "${{ github.ref_name }}"}
        for name in GUARDS:
            assert "${{" not in _step(production, name)["run"], name

    @pytest.mark.parametrize("tag", ["v2026.10.1", "v2026.9.30", "v2026.12.31.2"])
    def test_a_calver_tag_passes_the_format_check(
        self, production, release_tag, tmp_path, tag
    ) -> None:
        result = _run(
            _step(production, "Check the tag format"), tmp_path, TAG=tag, RELEASE_TAG=release_tag
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
    def test_any_other_tag_is_refused(self, production, release_tag, tmp_path, tag) -> None:
        result = _run(
            _step(production, "Check the tag format"), tmp_path, TAG=tag, RELEASE_TAG=release_tag
        )
        assert _refused(result), result.stdout

    def test_a_commit_on_beta_passes_the_ancestry_check(self, production, work, tmp_path) -> None:
        """Including one that reached beta after the checkout: the guard
        fetches beta itself."""
        runner = self._checkout(tmp_path)
        _git(work, "commit", "-q", "--allow-empty", "-m", "three")
        _git(work, "push", "-q", "origin", "beta")
        result = _run(
            _step(production, "Check that the tagged commit is on beta"),
            runner,
            TAG="v2026.10.1",
            GITHUB_SHA=_git(work, "rev-parse", "HEAD"),
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_commit_off_beta_is_refused(self, production, work, tmp_path) -> None:
        """A tag on a branch that never merged would ship unreviewed code."""
        _git(work, "switch", "-q", "-c", "side", "HEAD~1")
        _git(work, "commit", "-q", "--allow-empty", "-m", "unreviewed")
        _git(work, "push", "-q", "origin", "side")
        result = _run(
            _step(production, "Check that the tagged commit is on beta"),
            self._checkout(tmp_path),
            TAG="v2026.10.1",
            GITHUB_SHA=_git(work, "rev-parse", "HEAD"),
        )
        assert _refused(result), result.stdout

    def test_a_commit_dropped_from_beta_is_refused(self, production, work, tmp_path) -> None:
        """The runner's own origin/beta may be older than beta is now; the
        guard overwrites it rather than trusting it."""
        dropped = _git(work, "rev-parse", "HEAD")
        runner = self._checkout(tmp_path)
        _git(work, "reset", "-q", "--hard", "HEAD~1")
        _git(work, "push", "-q", "--force", "origin", "beta")
        result = _run(
            _step(production, "Check that the tagged commit is on beta"),
            runner,
            TAG="v2026.10.1",
            GITHUB_SHA=dropped,
        )
        assert _refused(result), result.stdout

    def test_the_newest_tag_passes(self, production, release_tag, work, tmp_path) -> None:
        """Ordered as numbers, not text: as text v2026.9.30 sorts after
        v2026.10.1. A tag that is not a release does not count."""
        self._tag(work, "v2026.10.1")
        self._tag(work, "v2026.12.1-rc1")
        result = _run(
            _step(production, "Check that the tag is the newest release"),
            self._checkout(tmp_path),
            TAG="v2026.10.1",
            RELEASE_TAG=release_tag,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_an_older_tag_pushed_again_is_refused(
        self, production, release_tag, work, tmp_path
    ) -> None:
        """Otherwise re-pushing an old tag rolls production back."""
        self._tag(work, "v2026.10.1")
        result = _run(
            _step(production, "Check that the tag is the newest release"),
            self._checkout(tmp_path),
            TAG="v2026.9.30",
            RELEASE_TAG=release_tag,
        )
        assert _refused(result), result.stdout

    def test_a_newer_tag_the_checkout_missed_still_refuses(
        self, production, release_tag, work, tmp_path
    ) -> None:
        """The guard fetches the tags itself, so one pushed after the
        checkout counts too."""
        self._tag(work, "v2026.10.1")
        runner = self._checkout(tmp_path)
        self._tag(work, "v2026.10.1.1")
        result = _run(
            _step(production, "Check that the tag is the newest release"),
            runner,
            TAG="v2026.10.1",
            RELEASE_TAG=release_tag,
        )
        assert _refused(result), result.stdout

    @staticmethod
    def _release_commit(work: Path, changelog: str) -> None:
        """A commit as the release step leaves it: the script that reads the
        notes, and CHANGELOG.md."""
        scripts = work / ".github" / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "release.py").write_text(RELEASE_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
        (work / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
        for name in ("cli/pyproject.toml", "plugin/.claude-plugin/plugin.json"):
            (work / name).parent.mkdir(parents=True, exist_ok=True)
            (work / name).write_text((ROOT / name).read_text(encoding="utf-8"), encoding="utf-8")
        _git(work, "add", "-A")
        _git(work, "commit", "-q", "-m", "Release")

    def test_a_tag_on_its_release_commit_passes(self, production, work, tmp_path) -> None:
        self._release_commit(work, "# Changelog\n\n## [Unreleased]\n\n## [2026.10.1]\n\n### Added\n\n- A thing.\n")
        self._tag(work, "v2026.10.1")
        result = _run(_step(production, "Check that the tagged commit has its release notes"), work, TAG="v2026.10.1")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_a_tag_without_its_section_is_refused(self, production, work, tmp_path) -> None:
        """A tag set by hand on an ordinary commit, whose changelog only has
        [Unreleased]; a section that lands on beta later does not count."""
        self._release_commit(work, "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- A thing.\n")
        self._tag(work, "v2026.10.1")
        self._release_commit(work, "# Changelog\n\n## [Unreleased]\n\n## [2026.10.1]\n\n### Added\n\n- A thing.\n")
        result = _run(_step(production, "Check that the tagged commit has its release notes"), work, TAG="v2026.10.1")
        assert _refused(result), result.stdout


class TestTheReleaseImage:
    """A release tag gets its version as a second image tag and as
    PLAK_VERSION in the image; any other push builds as before."""

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
        assert push["with"]["tags"] == (
            "${{ steps.release.outputs.tags || steps.tag.outputs.image }}"
        )
        # Scans and attestation keep following the commit tag.
        assert build["outputs"]["image"] == "${{ steps.tag.outputs.image }}"

    def test_a_release_tag_gets_both_image_tags_and_the_version(
        self, build, deploy, tmp_path
    ) -> None:
        output = tmp_path / "output"
        output.touch()
        result = _run(
            _step(build, "Determine the release version"),
            tmp_path,
            TAG="v2026.10.1",
            NAME="ghcr.io/digigilde/plak",
            IMAGE="ghcr.io/digigilde/plak:abc123",
            RELEASE_TAG=deploy["env"]["RELEASE_TAG"],
            GITHUB_OUTPUT=str(output),
        )
        assert result.returncode == 0, result.stdout
        assert output.read_text().splitlines() == [
            "build-args=PLAK_VERSION=2026.10.1",
            "tags<<EOF",
            "ghcr.io/digigilde/plak:abc123",
            "ghcr.io/digigilde/plak:2026.10.1",
            "EOF",
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
        pinned = {s["uses"] for job in ci["jobs"].values() for s in job.get("steps", []) if "uses" in s}
        for step in changelog["jobs"]["changelog"]["steps"]:
            if "uses" in step:
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
        assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
        assert "${{" not in step["run"]

    def test_the_comment_marker_is_the_one_the_script_writes(self, changelog) -> None:
        """Another marker and every run posts a new comment instead of
        updating the one that is there."""
        script = (WORKFLOWS.parent / "scripts" / "release.py").read_text(encoding="utf-8")
        step = next(s for s in changelog["jobs"]["changelog"]["steps"] if "comment" in s.get("name", ""))
        assert f'COMMENT_MARKER = "{step["env"]["MARKER"]}"\n' in script


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
        pinned = {s["uses"] for job in ci["jobs"].values() for s in job.get("steps", []) if "uses" in s}
        for step in api["jobs"]["api-contract"]["steps"]:
            if "uses" in step:
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
        reading = step["run"].split("if [")[0]
        found = subprocess.run(  # noqa: S603
            ["bash", "-e", "-c", reading + 'printf "%s" "$image"'],  # noqa: S607
            cwd=WORKFLOWS.parents[1],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        assert re.fullmatch(r"docker\.io/tufin/oasdiff:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", found)
        assert step["run"].index('if [ -z "$image" ]') < step["run"].index("docker cp oasdiff:/usr/bin/oasdiff")

        dependabot = yaml.safe_load((WORKFLOWS.parent / "dependabot.yml").read_text(encoding="utf-8"))
        docker = next(u for u in dependabot["updates"] if u["package-ecosystem"] == "docker")
        assert "/.github/oasdiff" in docker["directories"]

    def test_the_check_compares_the_schema_the_backend_prints(self, api) -> None:
        steps = api["jobs"]["api-contract"]["steps"]
        names = [s.get("name") for s in steps]
        printing = steps[names.index("Print the API schema")]
        checking = steps[names.index("Check the API contract")]
        order = [names.index(n) for n in ("Install oasdiff", "Print the API schema", "Check the API contract")]
        assert order == sorted(order)
        assert printing["working-directory"] == "backend"
        assert printing["run"] == 'uv run python -m plak.api.openapi_file > "$RUNNER_TEMP/openapi.json"'
        assert 'api-check --spec "$RUNNER_TEMP/openapi.json" --oasdiff "$RUNNER_TEMP/oasdiff"' in checking["run"]
        assert "${{" not in checking["run"]

    def test_only_a_pull_request_gets_a_comment(self, api) -> None:
        checking = next(s for s in api["jobs"]["api-contract"]["steps"] if s.get("name") == "Check the API contract")
        assert 'if [ "$EVENT" = pull_request ]; then\n  args+=(--comment-file "$COMMENT_FILE")' in checking["run"]

    def test_the_comment_cannot_fail_the_check(self, api) -> None:
        """A fork or Dependabot pull request has a read-only token."""
        step = next(s for s in api["jobs"]["api-contract"]["steps"] if "comment" in s.get("name", ""))
        assert step["continue-on-error"] is True
        assert "github.event_name == 'pull_request'" in step["if"]
        assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
        assert "${{" not in step["run"]

    def test_the_comment_marker_is_the_one_the_script_writes(self, api) -> None:
        script = (WORKFLOWS.parent / "scripts" / "release.py").read_text(encoding="utf-8")
        step = next(s for s in api["jobs"]["api-contract"]["steps"] if "comment" in s.get("name", ""))
        assert f'API_COMMENT_MARKER = "{step["env"]["MARKER"]}"\n' in script


class TestTheReleaseWorkflow:
    """What a release writes is tested in test_release.py; here how the
    workflow decides, when the App token exists and how it pushes."""

    @pytest.fixture
    def job(self, release) -> dict:
        return release["jobs"]["publish"]

    @pytest.fixture
    def prepare(self, release) -> dict:
        return release["jobs"]["prepare"]

    def test_every_push_to_beta_and_nothing_else_runs_it(self, release) -> None:
        assert release[True] == {"push": {"branches": ["beta"]}}
        assert release["permissions"] == {"contents": "read"}
        assert release["concurrency"] == {"group": "release", "cancel-in-progress": False}

    def test_decide_reads_the_changelog_and_every_tag_without_a_token(self, release) -> None:
        decide = release["jobs"]["decide"]
        checkout, deciding = decide["steps"]
        assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
        assert deciding["run"] == "python3 .github/scripts/release.py decide"
        assert decide["outputs"] == {
            "route": "${{ steps.decide.outputs.route }}",
            "tag": "${{ steps.decide.outputs.tag }}",
        }
        assert "permissions" not in decide and "environment" not in decide

    def test_only_a_release_route_prepares_and_publishes(self, prepare, job) -> None:
        """A hold or a push without entries never touches the App."""
        assert prepare["needs"] == "decide"
        assert prepare["if"] == "needs.decide.outputs.route == 'release'"
        assert job["needs"] == ["decide", "prepare"]
        for each in (prepare, job):
            assert each["env"] == {"TAG": "${{ needs.decide.outputs.tag }}"}

    def test_the_job_that_runs_the_dependencies_never_holds_the_key(self, prepare, job) -> None:
        """uv sync installs dependencies and the backend prints its schema,
        on a runner without the environment, the secret or the token."""
        assert "environment" not in prepare and "permissions" not in prepare
        assert "secrets." not in yaml.safe_dump(prepare)
        assert job["environment"] == "release"
        assert job["permissions"] == {"contents": "read"}
        assert [s["uses"].split("@")[0] for s in job["steps"] if "uses" in s] == [
            "actions/checkout",
            "actions/download-artifact",
            "actions/create-github-app-token",
        ]
        assert not any("uv " in s.get("run", "") or "docker " in s.get("run", "") for s in job["steps"])

    def test_the_patch_is_checked_before_the_token_exists(self, job) -> None:
        names = [s.get("name") or s["uses"].split("@")[0] for s in job["steps"]]
        assert names == [
            "actions/checkout",
            "actions/download-artifact",
            "Apply the release",
            "Check that it is the release and nothing else",
            "Get a token of the release App",
            "Commit, tag and push",
        ]
        assert job["steps"][0]["with"] == {"ref": "${{ github.sha }}", "persist-credentials": False}
        assert _step(job, "Apply the release")["run"] == (
            'git apply --cached --binary "$RUNNER_TEMP/release/release.patch"'
        )
        assert _step(job, "Check that it is the release and nothing else")["run"] == (
            'python3 .github/scripts/release.py verify-staged --tag "$TAG"'
        )
        app = _step(job, "Get a token of the release App")
        assert app["with"] == {
            "client-id": "${{ vars.RELEASE_APP_CLIENT_ID }}",
            "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
            "permission-contents": "write",
        }

    def test_the_patch_is_the_whole_staged_release_of_this_commit(self, prepare, job) -> None:
        handing = _step(prepare, "Hand the release on as a patch")
        assert 'git diff --cached --binary --no-renames > "$RUNNER_TEMP/release/release.patch"' in handing["run"]
        upload = next(s for s in prepare["steps"] if s.get("uses", "").startswith("actions/upload-artifact@"))
        download = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/download-artifact@"))
        assert upload["with"]["name"] == download["with"]["name"] == "release-${{ github.sha }}"
        assert upload["with"]["if-no-files-found"] == "error"

    def test_the_tag_is_checked_before_it_is_used(self, prepare, job) -> None:
        names = [s.get("name") for s in prepare["steps"]]
        assert names.index("Check the tag") < names.index("Write the release")
        assert _step(prepare, "Check the tag")["run"] == 'python3 .github/scripts/release.py validate-tag "$TAG"'
        for step in (*prepare["steps"], *job["steps"]):
            assert "${{" not in step.get("run", ""), step.get("name")

    def test_oasdiff_and_the_schema_come_as_in_the_api_check(self, prepare, api) -> None:
        """The release compares with the same oasdiff and the same schema
        the pull request check used."""
        check = api["jobs"]["api-contract"]
        for name in ("Install oasdiff", "Print the API schema"):
            assert _step(prepare, name)["run"] == _step(check, name)["run"], name
        assert _step(prepare, "Write the release")["run"] == (
            'python3 .github/scripts/release.py promote --tag "$TAG" \\\n'
            '  --spec "$RUNNER_TEMP/openapi.json" --oasdiff "$RUNNER_TEMP/oasdiff"\n'
        )

    def test_the_commit_and_the_tag_go_up_together_as_the_app(self, job, tmp_path) -> None:
        """Run against a real origin, with `gh` stubbed to answer the bot's
        user id: one atomic push leaves the release commit on beta and the
        annotated tag on it."""
        origin, work = tmp_path / "origin.git", tmp_path / "work"
        _git(tmp_path, "init", "-q", "--bare", "-b", "beta", str(origin))
        _git(tmp_path, "init", "-q", "-b", "beta", str(work))
        _git(work, "remote", "add", "origin", str(origin))
        _git(work, "commit", "-q", "--allow-empty", "-m", "Merge something")
        _git(work, "push", "-q", "origin", "beta")
        (work / "CHANGELOG.md").write_text("released\n", encoding="utf-8")
        _git(work, "add", "CHANGELOG.md")
        stubs = tmp_path / "bin"
        stubs.mkdir()
        (stubs / "gh").write_text("#!/bin/sh\necho 123456\n", encoding="utf-8")
        (stubs / "gh").chmod(0o755)

        result = _run_unnamed(
            _step(job, "Commit, tag and push"),
            work,
            PATH=f"{stubs}:{os.environ['PATH']}",
            GH_TOKEN="token",
            APP_SLUG="digigilde-plak-release",
            TAG="v2026.10.1",
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert _git(origin, "log", "-1", "--format=%s|%an|%ae", "beta") == (
            "Release v2026.10.1|digigilde-plak-release[bot]|"
            "123456+digigilde-plak-release[bot]@users.noreply.github.com"
        )
        assert _git(origin, "cat-file", "-t", "v2026.10.1") == "tag"
        assert _git(origin, "rev-parse", "v2026.10.1^{commit}") == _git(origin, "rev-parse", "beta")
        assert _git(origin, "tag", "-l", "--format=%(contents:subject)", "v2026.10.1") == "Plak v2026.10.1"

    def test_a_beta_that_moved_on_takes_neither_the_commit_nor_the_tag(self, job, tmp_path) -> None:
        """Another merge landed while this one was writing: the push is
        refused as a whole, and the next run releases what collected."""
        origin, work, other = tmp_path / "origin.git", tmp_path / "work", tmp_path / "other"
        _git(tmp_path, "init", "-q", "--bare", "-b", "beta", str(origin))
        _git(tmp_path, "init", "-q", "-b", "beta", str(work))
        _git(work, "remote", "add", "origin", str(origin))
        _git(work, "commit", "-q", "--allow-empty", "-m", "Merge something")
        _git(work, "push", "-q", "origin", "beta")
        _git(tmp_path, "clone", "-q", str(origin), str(other))
        _git(other, "commit", "-q", "--allow-empty", "-m", "Merge something else")
        _git(other, "push", "-q", "origin", "beta")
        (work / "CHANGELOG.md").write_text("released\n", encoding="utf-8")
        _git(work, "add", "CHANGELOG.md")
        stubs = tmp_path / "bin"
        stubs.mkdir()
        (stubs / "gh").write_text("#!/bin/sh\necho 123456\n", encoding="utf-8")
        (stubs / "gh").chmod(0o755)

        result = _run_unnamed(
            _step(job, "Commit, tag and push"),
            work,
            PATH=f"{stubs}:{os.environ['PATH']}",
            GH_TOKEN="token",
            APP_SLUG="digigilde-plak-release",
            TAG="v2026.10.1",
        )

        assert result.returncode != 0
        assert _git(origin, "log", "-1", "--format=%s", "beta") == "Merge something else"
        assert _git(origin, "tag", "-l") == ""


class TestTheGitHubRelease:
    @pytest.fixture
    def job(self, deploy) -> dict:
        return deploy["jobs"]["github-release"]

    def test_it_follows_production_and_the_attestations(self, job) -> None:
        """The notes tell you to verify the attestations, and the release
        says the tag is what runs; both have to be true first."""
        assert job["needs"] == ["build", "provenance", "production"]
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
            'python3 .github/scripts/release.py notes --tag "$TAG" --image "$IMAGE" --digest "$DIGEST" > "$notes"'
            in publish["run"]
        )
        assert 'gh release create "$TAG" --verify-tag --title "$TAG" --notes-file "$notes"' in publish["run"]
        assert "${{" not in publish["run"]


def _ruleset(name: str) -> dict:
    return json.loads((ROOT / ".github" / "rulesets" / f"{name}.json").read_text(encoding="utf-8"))


def _rule(ruleset: dict, kind: str) -> dict:
    return next(rule for rule in ruleset["rules"] if rule["type"] == kind)


class TestTheRulesets:
    """The rulesets docs/releasing.md applies, kept in the repository. The
    App id is filled in when they are applied; 0 stands in for it here."""

    def test_the_app_bypasses_only_what_a_release_has_to_pass(self) -> None:
        """A bypass covers its whole ruleset, so what nobody may do, the App
        included, sits in a ruleset without one: deleting or rewriting beta,
        moving or deleting a release tag."""
        app = [{"actor_id": 0, "actor_type": "Integration", "bypass_mode": "always"}]
        rulesets = {
            "beta": (app, {"pull_request", "required_status_checks", "merge_queue"}),
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
        assert {"ci / backend-coverage", "api-contract", "changelog"} <= checks

    def test_both_tag_rulesets_cover_the_release_tags(self) -> None:
        for name in ("release-tags", "release-tags-fixed"):
            tags = _ruleset(name)
            assert tags["target"] == "tag", name
            assert tags["conditions"]["ref_name"]["include"] == ["refs/tags/v*"], name


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
