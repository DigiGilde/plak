"""The gates in .github/workflows, pinned down here rather than on GitHub.

They run there now, but a green run says the wiring held for that event;
it says nothing about the events that did not fire. Which job gates what,
which permission sits where and that every action is on a commit SHA is
checked here, on every commit.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


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


class TestTheCheckGate:
    def test_production_waits_for_the_checks(self, deploy) -> None:
        """BIO2 8.31.02: significant changes are tested before they go to
        production. With only `needs: bouw` a push to main went straight
        to production, tested or not."""
        assert deploy["jobs"]["productie"]["needs"] == ["ci", "bouw"]

    def test_a_push_to_beta_builds_an_image_without_deploying(self, deploy) -> None:
        """There is no production environment yet and no main branch, so a
        push to the default branch exists to produce a container package to
        roll out by hand. The checks run with it, because deploy.yml calls
        them; production stays bound to main and does not fire."""
        assert deploy[True]["push"]["branches"] == ["main", "beta"]
        assert deploy["jobs"]["productie"]["if"] == (
            "github.event_name == 'push' && github.ref == 'refs/heads/main'"
        )
        assert deploy["jobs"]["preview"]["if"].startswith(
            "github.event_name == 'pull_request'"
        )

    def test_the_checks_are_called_rather_than_triggered(self, ci, deploy) -> None:
        """You cannot pass a standalone workflow as `needs`. Calling it is
        the only form in which the gate is technically enforceable, and it
        saves everything running twice per commit."""
        assert list(ci[True]) == ["workflow_call"]
        assert deploy["jobs"]["ci"]["uses"] == "./.github/workflows/ci.yml"

    def test_the_checks_cover_backend_cli_frontend_and_vulnerabilities(self, ci) -> None:
        # These are also the names branch protection should be set to
        # later: `ci / backend`, `ci / cli`, `ci / frontend`,
        # `ci / vulnerabilities`, `ci / pre-commit`, `ci / secret-scan`,
        # `ci / containers`, `ci / e2e`.
        assert set(ci["jobs"]) == {
            "backend",
            "cli",
            "frontend",
            "vulnerabilities",
            "pre-commit",
            "secret-scan",
            "containers",
            "e2e",
        }


class TestTheScans:
    def test_the_image_is_scanned_before_anything_rolls_out(self, deploy) -> None:
        steps = deploy["jobs"]["bouw"]["steps"]
        scans = [s for s in steps if "trivy-action" in str(s.get("uses", ""))]

        # One that reports everything, one that closes the gate, and the SBOM.
        assert len(scans) == 3
        gate = [s for s in scans if s.get("with", {}).get("exit-code") == "1"]
        assert len(gate) == 1
        assert gate[0]["with"]["severity"] == "CRITICAL,HIGH"
        assert gate[0]["with"]["trivyignores"] == ".trivyignore.yaml"

    def test_the_image_carries_provenance_and_sbom_attestations(self, deploy) -> None:
        """Both attest the digest that was pushed, never a tag, which can move."""
        herkomst = deploy["jobs"]["herkomst"]
        for permission in ("id-token", "attestations", "artifact-metadata"):
            assert herkomst["permissions"][permission] == "write"
        # push-to-registry writes the attestation next to the image.
        assert herkomst["permissions"]["packages"] == "write"

        attests = [
            s for s in herkomst["steps"] if str(s.get("uses", "")).startswith("actions/attest@")
        ]
        assert len(attests) == 2
        for step in attests:
            assert step["with"]["subject-name"] == "${{ needs.bouw.outputs.naam }}"
            assert step["with"]["subject-digest"] == "${{ needs.bouw.outputs.digest }}"
            assert step["with"]["push-to-registry"] is True
        assert [s["with"].get("sbom-path") for s in attests] == [None, "sbom.cdx.json"]

    def test_the_attestation_job_runs_no_project_code(self, deploy) -> None:
        """A job with `id-token: write` can mint a token for any audience it
        names, and that token carries this repository's identity. So it may
        not be the job that executes the Containerfile, which runs `npm ci`
        and `uv sync`."""
        assert deploy["jobs"]["bouw"]["permissions"] == {
            "contents": "read",
            "packages": "write",
        }

        # No checkout, no build, no scan: login, download, attest twice.
        steps = deploy["jobs"]["herkomst"]["steps"]
        assert [s["uses"].split("@")[0] for s in steps] == [
            "docker/login-action",
            "actions/download-artifact",
            "actions/attest",
            "actions/attest",
        ]

    def test_only_a_pushed_image_gets_attested(self, deploy) -> None:
        """No `if:` of its own: a skipped or failed `bouw` skips this job as
        well, so nothing gets attested that was not built and pushed. The
        digest is handed over rather than re-resolved, so the two jobs
        cannot disagree about which image that was."""
        herkomst = deploy["jobs"]["herkomst"]
        assert herkomst["needs"] == "bouw"
        assert "if" not in herkomst
        assert deploy["jobs"]["bouw"]["outputs"]["digest"] == "${{ steps.push.outputs.digest }}"

        # The SBOM reaches the attestation as an artefact, under one name.
        upload = next(
            s
            for s in deploy["jobs"]["bouw"]["steps"]
            if str(s.get("uses", "")).startswith("actions/upload-artifact@")
        )
        download = next(
            s
            for s in herkomst["steps"]
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
        assert [p.name for p in paths] == ["ci.yml", "codeql.yml", "deploy.yml", "plugin.yml"]

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


class TestCodeQL:
    def test_it_is_its_own_workflow_with_a_weekly_run(self, codeql) -> None:
        """ci.yml is `workflow_call` only, and a called workflow carries no
        schedule of its own. CodeQL needs one: the queries and the
        advisories move while the code stands still."""
        triggers = codeql[True]
        assert set(triggers) == {"pull_request", "push", "schedule"}
        assert triggers["push"]["branches"] == ["beta"]
        assert len(triggers["schedule"]) == 1
        assert triggers["schedule"][0]["cron"].endswith(" * * 0")

    def test_the_write_permission_sits_on_the_job(self, codeql) -> None:
        """Same split as `herkomst` in deploy.yml: the permission to write
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
