# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6,<7"]
# ///
"""Turns `.trivyignore.yaml` into filters for pip-audit and npm audit.

Trivy reads that file itself. pip-audit only has `--ignore-vuln` and npm audit
has no ignore mechanism at all, so both are wired up from here against the same
list, and there is one place where an accepted finding is written down.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

IGNORE_FILE = Path(__file__).resolve().parents[2] / ".trivyignore.yaml"

#: The identifier namespaces the scanners in this repo emit. Trivy accepts
#: anything here, so a typo would silently mute nothing at all.
ADVISORY_ID = re.compile(r"^(CVE-\d{4}-\d{4,}|GHSA(-[a-z0-9]{4}){3}|PYSEC-\d{4}-\d+)$")

GHSA_IN_URL = re.compile(r"(GHSA(-[a-z0-9]{4}){3})")


def read_ignore_list(path: Path) -> list[dict[str, Any]]:
    content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = list(content.get("vulnerabilities") or [])
    for entry in entries:
        if not ADVISORY_ID.match(str(entry.get("id", ""))):
            raise ValueError(f"{path.name}: {entry.get('id')!r} is not an advisory id")
        if not isinstance(entry.get("expired_at"), date):
            raise ValueError(f"{path.name}: {entry['id']} has no date in expired_at")
        if not str(entry.get("statement", "")).strip():
            raise ValueError(f"{path.name}: {entry['id']} has no statement")
    return entries


def valid_ids(entries: list[dict[str, Any]], today: date) -> list[str]:
    """The ids that still count today, in file order.

    `expired_at` is the first day an entry no longer counts, the way trivy
    reads the same field: it drops a rule once the date is before now.
    """
    return [entry["id"] for entry in entries if entry["expired_at"] > today]


def open_npm_advisories(report: dict[str, Any], ignored: set[str]) -> list[str]:
    """The advisories in an `npm audit --json` report that are not on the list.

    npm repeats a finding under every package it reaches: `via` holds either the
    name of the next package in the chain (a string, no identifier) or the
    advisory itself, so the same GHSA comes back once per affected package.
    """
    found: set[str] = set()
    for package in (report.get("vulnerabilities") or {}).values():
        for via in package.get("via") or []:
            if not isinstance(via, dict):
                continue
            match = GHSA_IN_URL.search(via.get("url") or "")
            if match:
                found.add(match.group(1))
    return sorted(found - ignored)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Filters based on .trivyignore.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ids", help="print the ignore ids that still count, one per line")
    npm = commands.add_parser("npm-audit", help="filter a report from npm audit --json")
    npm.add_argument("report", type=Path)
    args = parser.parse_args(argv)

    ids = valid_ids(read_ignore_list(IGNORE_FILE), datetime.now(UTC).date())

    if args.command == "ids":
        # Line by line, not through join: an empty list has to produce nothing
        # at all, because the caller reads this in a bash loop.
        for advisory in ids:
            print(advisory)
        return 0

    report = json.loads(args.report.read_text(encoding="utf-8"))
    remaining = open_npm_advisories(report, set(ids))
    if not remaining:
        print("npm audit: no vulnerabilities outside the ignore list.")
        return 0
    for advisory in remaining:
        print(f"npm audit: https://github.com/advisories/{advisory}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())