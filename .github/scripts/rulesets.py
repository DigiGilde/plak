# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fails when the live GitHub rulesets drift from .github/rulesets/*.json.

    check [--repo OWNER/NAME]   print every difference, exit 1 on any drift

The files are the reviewed source, but they only reach GitHub through a
manual `gh api` call (docs/releasing.md). This is the check that notices a
file that was never applied, a ruleset edited in the UI and a ruleset that
has no file. Rulesets are matched by name. Only the keys a file carries are
compared: GitHub adds server fields (id, source, _links, timestamps), and it
returns `bypass_actors` only to callers with write access, so that key is
left out (the files hold a placeholder actor id anyway).

Needs `gh` and a token with read access to the repository metadata
(GH_TOKEN; the workflow `GITHUB_TOKEN` is enough).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

RULESETS_DIR = Path(__file__).resolve().parents[1] / "rulesets"

#: Dicts under these keys must match key for key: a live rule that no file
#: asks for is drift, not a server field.
STRICT_KEYS = frozenset({"rules"})
IGNORED_KEYS = frozenset({"bypass_actors"})


def normalise(ruleset: dict[str, Any]) -> dict[str, Any]:
    """The comparable form: no bypass actors, rules keyed by type, the
    required checks as plain strings."""
    result = {key: value for key, value in ruleset.items() if key not in IGNORED_KEYS}
    rules: dict[str, Any] = {}
    for rule in ruleset.get("rules", []):
        parameters = dict(rule.get("parameters", {}))
        if "required_status_checks" in parameters:
            parameters["required_status_checks"] = [_check(c) for c in parameters["required_status_checks"]]
        rules[rule["type"]] = parameters
    result["rules"] = rules
    return result


def _check(check: dict[str, Any]) -> str:
    if "integration_id" in check:
        return f"{check['context']} (integration {check['integration_id']})"
    return check["context"]


def differences(expected: Any, actual: Any, path: str = "", strict: bool = False) -> list[str]:
    """What in `actual` departs from `expected`. A dict only has to hold the
    keys of `expected` unless strict; lists compare as sets."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        found = [f"{_join(path, key)}: live has it, the file does not" for key in sorted(actual.keys() - expected.keys()) if strict]
        for key in expected:
            if key in actual:
                found += differences(expected[key], actual[key], _join(path, key), key in STRICT_KEYS)
            else:
                found.append(f"{_join(path, key)}: the file has it, live does not")
        return found
    if isinstance(expected, list) and isinstance(actual, list):
        want = {json.dumps(item, sort_keys=True): item for item in expected}
        have = {json.dumps(item, sort_keys=True): item for item in actual}
        found = [f"{path}: missing live: {want[key]}" for key in want if key not in have]
        return found + [f"{path}: extra live: {have[key]}" for key in have if key not in want]
    if expected != actual:
        return [f"{path}: the file says {expected!r}, live says {actual!r}"]
    return []


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def compare(files: dict[str, dict[str, Any]], live: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    """Differences per ruleset name; a name without any is in sync."""
    report: dict[str, list[str]] = {}
    for name, ruleset in files.items():
        if name not in live:
            report[name] = ["no live ruleset has this name"]
            continue
        found = differences(normalise(ruleset), normalise(live[name]))
        if found:
            report[name] = found
    for name in live.keys() - files.keys():
        report[name] = ["live ruleset without a file in .github/rulesets/"]
    return report


def read_files(directory: Path) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        ruleset = json.loads(path.read_text(encoding="utf-8"))
        files[ruleset["name"]] = ruleset
    return files


def gh_api(endpoint: str) -> Any:
    done = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"gh api {endpoint} failed: {done.stderr.strip()}")
    return json.loads(done.stdout)


def fetch_live(repo: str, api: Callable[[str], Any] = gh_api) -> dict[str, dict[str, Any]]:
    live: dict[str, dict[str, Any]] = {}
    for summary in api(f"repos/{repo}/rulesets?per_page=100"):
        ruleset = api(f"repos/{repo}/rulesets/{summary['id']}")
        live[ruleset["name"]] = ruleset
    return live


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="OWNER/NAME")
    parser.add_argument("--dir", type=Path, default=RULESETS_DIR)
    args = parser.parse_args(argv)
    if not args.repo:
        parser.error("--repo is required when GITHUB_REPOSITORY is not set")
    try:
        live = fetch_live(args.repo)
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 2
    report = compare(read_files(args.dir), live)
    for name, found in sorted(report.items()):
        print(f"Ruleset '{name}' differs:")
        for line in found:
            print(f"  - {line}")
    if report:
        print("Apply the file with the commands in docs/releasing.md, or bring the file in line with GitHub.")
        return 1
    print(f"{len(live)} live rulesets match .github/rulesets/.")
    return 0


if __name__ == "__main__":  # pragma: no cover - the script entry; main() is tested directly
    sys.exit(main())
