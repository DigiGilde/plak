"""The entry scripts/release.py: argument parsing and running it as a plain script."""

from __future__ import annotations

import subprocess
import sys

from release_support import SCRIPT, release


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
