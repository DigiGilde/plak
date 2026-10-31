"""The ignore list for the vulnerability scans (.trivyignore.yaml).

An ignore list that is written wrongly mutes nothing, and you only notice that
once a real finding slips through. So the script reads it strictly, and this
test pins that down.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
IGNORE_FILE = ROOT / ".trivyignore.yaml"
SCRIPT = ROOT / "scripts" / "vulnerabilities.py"


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

    def test_an_entry_stops_counting_on_its_own_date(self, script, tmp_path: Path) -> None:
        """The date is the point at which the finding comes back by itself, so
        that an accepted risk does not quietly stay forever."""
        listed = tmp_path / "listed.yaml"
        listed.write_text(
            yaml.safe_dump(
                {
                    "vulnerabilities": [
                        {"id": "CVE-2026-0001", "expired_at": date(2026, 11, 1), "statement": "x"},
                        {"id": "GHSA-aaaa-bbbb-cccc", "expired_at": date(2026, 12, 1), "statement": "y"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        entries = script.read_ignore_list(listed)

        assert script.valid_ids(entries, date(2026, 10, 31)) == ["CVE-2026-0001", "GHSA-aaaa-bbbb-cccc"]
        assert script.valid_ids(entries, date(2026, 11, 1)) == ["GHSA-aaaa-bbbb-cccc"]
        assert script.valid_ids(entries, date(2026, 12, 1)) == []

    def test_an_empty_list_mutes_nothing(self, script, tmp_path: Path) -> None:
        """Once every accepted finding is fixed the file says `vulnerabilities: []`."""
        empty = tmp_path / "empty.yaml"
        empty.write_text("vulnerabilities: []\n", encoding="utf-8")

        assert script.read_ignore_list(empty) == []


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

    def test_a_via_without_an_advisory_url_is_no_finding(self, script) -> None:
        report = {"vulnerabilities": {"a": {"via": [{"url": "https://example.org/not-an-advisory"}, {}]}}}
        assert script.open_npm_advisories(report, set()) == []


def _ignore_file(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "ignore.yaml"
    path.write_text(yaml.safe_dump({"vulnerabilities": entries}), encoding="utf-8")
    return path


class TestRefusals:
    @pytest.mark.parametrize(
        ("entry", "reason"),
        [
            ({"id": "CVE-2026-0001", "expired_at": "2030-01-01x", "statement": "x"}, "has no date in expired_at"),
            ({"id": "CVE-2026-0001", "expired_at": date(2030, 1, 1), "statement": " "}, "has no statement"),
        ],
        ids=["no-date", "no-statement"],
    )
    def test_an_entry_without_a_date_or_a_statement_is_refused(self, script, tmp_path: Path, entry, reason) -> None:
        """A finding is accepted for a reason and for a while; an entry that
        says neither is not an ignore line."""
        with pytest.raises(ValueError, match=reason):
            script.read_ignore_list(_ignore_file(tmp_path, [entry]))


class TestMain:
    @pytest.fixture
    def listed(self, script, tmp_path: Path, monkeypatch) -> None:
        """An ignore list with one entry that still counts and one that ran out."""
        entries = [
            {"id": "GHSA-aaaa-bbbb-cccc", "expired_at": date(2999, 1, 1), "statement": "waiting on upstream"},
            {"id": "CVE-2020-0001", "expired_at": date(2020, 1, 1), "statement": "long gone"},
        ]
        monkeypatch.setattr(script, "IGNORE_FILE", _ignore_file(tmp_path, entries))

    def test_ids_prints_what_still_counts_one_per_line(self, script, listed, capsys) -> None:
        assert script.main(["ids"]) == 0
        assert capsys.readouterr().out == "GHSA-aaaa-bbbb-cccc\n"

    def test_ids_prints_nothing_at_all_for_an_empty_list(self, script, tmp_path: Path, monkeypatch, capsys) -> None:
        """The caller reads this in a bash loop; a blank line would become an
        empty --ignore-vuln."""
        monkeypatch.setattr(script, "IGNORE_FILE", _ignore_file(tmp_path, []))
        assert script.main(["ids"]) == 0
        assert capsys.readouterr().out == ""

    def test_npm_audit_passes_when_every_finding_is_on_the_list(self, script, listed, tmp_path: Path, capsys) -> None:
        report = tmp_path / "report.json"
        findings = {"vulnerabilities": {"a": {"via": [{"url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc"}]}}}
        report.write_text(json.dumps(findings), encoding="utf-8")
        assert script.main(["npm-audit", str(report)]) == 0
        assert capsys.readouterr().out == "npm audit: no vulnerabilities outside the ignore list.\n"

    def test_npm_audit_fails_and_names_what_is_not_on_the_list(self, script, listed, tmp_path: Path, capsys) -> None:
        report = tmp_path / "report.json"
        findings = {
            "vulnerabilities": {
                "a": {"via": [{"url": "https://github.com/advisories/GHSA-aaaa-bbbb-cccc"}]},
                "b": {"via": [{"url": "https://github.com/advisories/GHSA-dddd-eeee-ffff"}]},
            }
        }
        report.write_text(json.dumps(findings), encoding="utf-8")
        assert script.main(["npm-audit", str(report)]) == 1
        out, err = capsys.readouterr()
        assert out == ""
        assert err == "npm audit: https://github.com/advisories/GHSA-dddd-eeee-ffff\n"
