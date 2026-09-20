"""The ignore list for the vulnerability scans (.trivyignore.yaml).

An ignore list that is written wrongly mutes nothing, and you only notice that
once a real finding slips through. So the script reads it strictly, and this
test pins that down.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
IGNORE_FILE = ROOT / ".trivyignore.yaml"
SCRIPT = ROOT / ".github" / "scripts" / "vulnerabilities.py"


def _module():
    spec = importlib.util.spec_from_file_location("vulnerabilities", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["vulnerabilities"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _module()


class TestTheIgnoreList:
    def test_every_entry_has_an_id_a_date_and_a_reason(self, script) -> None:
        entries = script.read_ignore_list(IGNORE_FILE)

        assert entries
        for entry in entries:
            assert script.ADVISORY_ID.match(entry["id"]), entry["id"]
            assert isinstance(entry["expired_at"], date)
            assert entry["statement"].strip()

    def test_a_typo_in_an_id_is_refused_rather_than_silently_muting_nothing(
        self, script, tmp_path: Path
    ) -> None:
        bogus = tmp_path / "bogus.yaml"
        bogus.write_text(
            yaml.safe_dump(
                {"vulnerabilities": [{"id": "CVE-fout", "expired_at": date(2030, 1, 1), "statement": "x"}]}
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="advisory id"):
            script.read_ignore_list(bogus)

    def test_an_entry_stops_counting_on_its_own_date(self, script) -> None:
        """The date is the point at which the finding comes back by itself, so
        that an accepted risk does not quietly stay forever."""
        entries = script.read_ignore_list(IGNORE_FILE)
        last = max(entry["expired_at"] for entry in entries)

        assert script.valid_ids(entries, date(2026, 1, 1))
        assert script.valid_ids(entries, last) == []


class TestNpmAudit:
    def test_an_advisory_on_the_list_does_not_fail_the_scan(self, script) -> None:
        report = {
            "vulnerabilities": {
                "@vitest/mocker": {
                    "via": [
                        {"url": "https://github.com/advisories/GHSA-82fw-gwwq-j7x9"},
                    ]
                }
            }
        }

        assert script.open_npm_advisories(report, {"GHSA-82fw-gwwq-j7x9"}) == []

    def test_the_same_advisory_under_three_packages_counts_once(self, script) -> None:
        """npm repeats a finding under every package that reaches it; there `via`
        sometimes carries only a package name as a string."""
        report = {
            "vulnerabilities": {
                "a": {"via": [{"url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc"}]},
                "b": {"via": ["a"]},
                "c": {"via": [{"url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc"}]},
            }
        }

        assert script.open_npm_advisories(report, set()) == ["GHSA-aaaa-bbbb-cccc"]
