"""The gates in .github/workflows, pinned down because they have never run.

The repo has no remote, so GitHub has never seen these files. Until that
changes, this is the only place where the wiring gets checked.
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
        bouw = deploy["jobs"]["bouw"]
        for permission in ("id-token", "attestations", "artifact-metadata"):
            assert bouw["permissions"][permission] == "write"

        steps = bouw["steps"]
        attests = [s for s in steps if str(s.get("uses", "")).startswith("actions/attest@")]
        assert len(attests) == 2
        for step in attests:
            assert step["with"]["subject-name"] == "${{ steps.tag.outputs.naam }}"
            assert step["with"]["subject-digest"] == "${{ steps.push.outputs.digest }}"
            assert step["with"]["push-to-registry"] is True
        assert [s["with"].get("sbom-path") for s in attests] == [None, "sbom.cdx.json"]

        # The SBOM has to exist before it can be attested.
        sbom = next(i for i, s in enumerate(steps) if s.get("with", {}).get("output") == "sbom.cdx.json")
        assert sbom < steps.index(attests[1])

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

    def test_every_third_party_action_is_pinned_to_a_sha(self, ci, deploy) -> None:
        """A tag can be moved, a commit cannot."""
        for workflow in (ci, deploy):
            for job in workflow["jobs"].values():
                for step in job.get("steps", []):
                    uses = step.get("uses")
                    if not uses or uses.startswith("./"):
                        continue
                    ref = uses.split("@")[1]
                    assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), uses
